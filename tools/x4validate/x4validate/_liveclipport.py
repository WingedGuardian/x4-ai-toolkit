"""Bound live-client clip port. Recording authority stays with its caller.

No session creation, launch, input, pause or implicit capture/retry. Exported
receipts remain correlation records, not cryptographic visual attestations.
"""
from __future__ import annotations
from ._livetrees import journal_refusal
import copy
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
from ._liveactions import _write_new, _read_json
from ._liveautomation import AutomationClient
from ._livebound import BoundPipe, _decode
from ._liveclip import WindowClipRecorder, window_for_peer, run_bounded, need, MAX_BYTES
from ._liveevidence import ReadEvidence
from ._livepeerprofile import ProfilePeer
from ._livepersistence import _route, _file, _json
from ._livesession import SessionLink

SOURCES=("_liveclipport.py","_liveclip.py","_liveautomation.py","_livebound.py","_livepeerprofile.py",
         "_livesession.py","_liveevidence.py","_livereadpages.py","_liveactions.py","_livepersistence.py")
MAX_SAMPLES=2048
MAX_SAMPLE_BYTES=2*1024**2

def source_pins():
    root=Path(__file__).resolve().parent
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCES}

class ClipPort:
    """Construct via connect_clips; opaque tickets bind one task and local owner."""
    def __init__(self,client,profile,journals,*,ffmpeg,ffmpeg_sha256,synthetic=False,
                 window=window_for_peer,runner=run_bounded):
        need(isinstance(client,AutomationClient) and isinstance(client.bound,BoundPipe)
             and isinstance(client.bound.link,SessionLink) and isinstance(client.bound.link.peer,ProfilePeer),
             "actual bound client and ProfilePeer required")
        need(type(synthetic)is bool and (synthetic or client._native_connection is True),
             "production native connection required")
        need(type(journals)is dict and set(journals)=={"port","recorder"},"two exact existing task journals required")
        self.client=client;self.synthetic=synthetic;self.peer=client.bound.link.peer
        self.profile=_route(profile,directory=True);save=_route(self.profile/"save",directory=True)
        self.root=_route(journals["port"],directory=True);recordroot=_route(journals["recorder"],directory=True)
        need(self.root!=recordroot and not self.root.is_relative_to(recordroot) and not recordroot.is_relative_to(self.root),
             "distinct nonnested clip journals required")
        for path in (self.root,recordroot):
            why=journal_refusal(path,profile=self.profile)
            need(why is None,"clip journal/personal tree overlap: "+str(why))
        self.pins=source_pins()
        self.initial=copy.deepcopy(client.identity());self.build=client.bound.build
        need(self.profile.name==self.peer.profile_id and self.peer()==self.initial["peer"],
             "connected profile path mismatch")
        collector=ReadEvidence(client,synthetic=synthetic)
        handle=collector.collect("native_save_path")
        evidence=collector.receipt(handle);transcript=collector.transcript(handle)
        observed=_decode(evidence["capture"]["payload"])
        need(evidence["status"]=="OK" and type(observed)is dict and
             set(observed)=={"status","scope","stamp","save_folder"} and observed["status"]=="OBSERVED"
             and observed["scope"]=="NATIVE_SAVE_FOLDER_PATH" and
             observed["stamp"]==self._stamp() and type(observed["save_folder"])is str,
             "native profile path observation unavailable")
        need(_route(observed["save_folder"],directory=True)==save and client.identity()==self.initial,
             "native profile path/identity mismatch")
        need(source_pins()==self.pins,"clip sources changed during binding")
        self.records={};self.active=None;self.current=None;self.fault=None
        self._operation=threading.Lock()
        self.directory=[self.root.stat().st_dev,self.root.stat().st_ino]
        self.owner={"version":1,"identity":self.initial,"profile":str(self.profile),"build":self.build,
                    "source_pins":self.pins,"native_path":{"receipt":evidence,"transcript":transcript},
                    "tier":"SYNTHETIC" if synthetic else "LOCAL_TRUSTED_CAPTURE_OWNER",
                    "policy":"NO_AUTOMATIC_REOPEN_OR_REPLAY"}
        _write_new(self.root/"clip-port-owner.json",self.owner)
        self.recorder=WindowClipRecorder(recordroot,ffmpeg=ffmpeg,ffmpeg_sha256=ffmpeg_sha256,
            identity=self._ping,profile_peer=self._profile_peer,window=window,runner=runner,synthetic=synthetic)
    def _stamp(self):
        v=self.initial
        return {k:v[k] for k in ("provider","revision","session","world_token")}|{"generation":v["ui_generation"]}
    def _profile_peer(self):
        return {"peer":self.peer(),"profile_id":self.peer.profile_id}
    def _ping(self):
        # Supported read verb; no read-capture cache nonce consumed for each poll.
        # BoundPipe validates actual native stamp before/after, helper build and
        # monotonic envelope age. Host identity() alone does not observe a load.
        need(self.client.identity()==self.initial and self.client.bound.build==self.build,
             "clip client identity/build changed")
        reply=self.client.bound.ask("ping")
        envelope=copy.deepcopy(self.client.bound.last_envelope)
        need(reply.status=="OK" and type(reply.payload)is str and reply.payload.startswith("pong\t"),
             "bound ping source unavailable")
        need(self.client.identity()==self.initial and self._profile_peer()==
             {"peer":self.initial["peer"],"profile_id":self.profile.name},"clip peer/profile changed")
        if self.current is not None:
            row=self.current;raw=json.dumps(envelope,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
            need(len(raw)<=4096 and len(row["samples"])<MAX_SAMPLES and row["sample_bytes"]+len(raw)<=MAX_SAMPLE_BYTES,
                 "clip sampled identity evidence capacity")
            row["samples"].append(envelope);row["sample_bytes"]+=len(raw)
        return copy.deepcopy(self.initial)
    def _current(self):
        need(self.fault is None,"clip port quarantined: "+str(self.fault))
        _route(self.root,directory=True)
        need([self.root.stat().st_dev,self.root.stat().st_ino]==self.directory,"clip port directory replaced")
        need(_read_json(self.root/"clip-port-owner.json")==self.owner,"clip port owner journal changed")
        need(source_pins()==self.pins,"clip integration sources changed")
        need(self.client.identity()==self.initial and self.peer.profile_id==self.profile.name and
             self.peer()==self.initial["peer"],"clip host binding changed")
    def reserve(self,case_binding,*,duration=10,fps=15):
        need(self._operation.acquire(blocking=False),"clip port operation already active")
        try:return self._reserve(case_binding,duration=duration,fps=fps)
        finally:self._operation.release()
    def _reserve(self,case_binding,*,duration,fps):
        self._current()
        need(self.active is None and len(self.records)<128,"clip port active/quarantined/capacity")
        need(type(case_binding)is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",case_binding),
             "bounded declared case label required")
        row={"id":secrets.token_hex(16),"case_binding":case_binding,"stage":"RESERVING",
             "samples":[],"sample_bytes":0,"result":None}
        handle=object();self.active=handle;self.records[handle]=row;self.current=row
        try:
            row["native"]=self.recorder.prepare(row["id"],duration=duration,fps=fps)
            row["prepared"]={"version":1,"id":row["id"],"case_binding":case_binding,
                "attribution":"CALLER_DECLARED_LABEL_NOT_SCENE_PROOF","identity":self.initial,
                "profile":str(self.profile),"build":self.build,"duration":duration,"fps":fps,"source_pins":self.pins}
            _write_new(self.root/(row["id"]+"-prepared.json"),row["prepared"])
            row["stage"]="PREPARED"
            return handle
        except Exception as exc:
            self.fault=str(exc);row["stage"]="QUARANTINED";raise
        finally:self.current=None
    def execute(self,handle):
        need(self._operation.acquire(blocking=False),"clip port operation already active")
        try:return self._execute(handle)
        finally:self._operation.release()
    def _execute(self,handle):
        self._current()
        need(type(handle)is object and handle in self.records and self.active is handle,"opaque active clip ticket required")
        row=self.records[handle]
        need(row["stage"]=="PREPARED","clip port ticket consumed; never replay")
        need(_read_json(self.root/(row["id"]+"-prepared.json"))==row["prepared"],"clip port preparation changed")
        row["stage"]="EXECUTING";self.current=row
        try:
            _write_new(self.root/(row["id"]+"-executing.json"),{"version":1,"id":row["id"]})
            result=self.recorder.execute(row["native"])
            self._ping();self._current()
            need(result["identity"]==self.initial and result["profile"]==
                 {"peer":self.initial["peer"],"profile_id":self.profile.name} and result["id"]==row["id"]
                 and result["status"]=="DECODED_HOST_CLIP" and result["visual_case_pass"] is False,
                 "clip recorder receipt binding changed")
            value={"version":1,"status":"CLIP_ARTIFACT_CAPTURED_REVIEW_REQUIRED",
                   "tier":"SYNTHETIC" if self.synthetic else "LOCAL_TRUSTED_CAPTURE",
                   "identity":self.initial,"build":self.build,"profile_id":self.profile.name,
                   "case_binding":row["case_binding"],"case_attribution":"CALLER_DECLARED_NOT_SCENE_PROOF",
                   "source_pins":self.pins,"clip":result,"native_identity_samples":row["samples"],
                   "sample_count":len(row["samples"]),"sampling":"SAMPLED_NOT_CONTINUOUS",
                   "clock_join":"HOST_AND_NATIVE_ENVELOPE_CLOCKS_NOT_NUMERICALLY_JOINED",
                   "visual_case_pass":False,"world_lineage":"UNKNOWN","cryptographic_attestation":False}
            raw=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
            need(len(raw)<=4*1024**2,"clip campaign receipt byte capacity")
            path=self.root/(row["id"]+"-receipt.json");_write_new(path,value)
            row["result"]=copy.deepcopy(value);row["receipt_path"]=path
            row["receipt_sha256"]=_file(path,4*1024**2)["sha256"]
            row["stage"]="CAPTURED";self.active=None
            return handle
        except Exception as exc:
            self.fault=str(exc);row["stage"]="QUARANTINED";raise
        finally:self.current=None
    def receipt(self,handle):
        """Opaque same-owner access, with current bytes checked; no new capture."""
        need(self._operation.acquire(blocking=False),"clip port operation already active")
        try:return self._receipt(handle)
        finally:self._operation.release()
    def _receipt(self,handle):
        self._current()
        need(type(handle)is object and handle in self.records and self.records[handle]["stage"]=="CAPTURED","local completed clip ticket required")
        row=self.records[handle];result=row["result"];path=row["receipt_path"]
        need(_file(path,4*1024**2)["sha256"]==row["receipt_sha256"],"clip campaign receipt changed")
        need(_file(Path(result["clip"]["path"]),MAX_BYTES)==result["clip"]["file"],"captured clip changed")
        need(_file(self.recorder.binary,256*1024**2)==self.recorder.binary_row,"capture binary changed")
        value=copy.deepcopy(result)
        value["historical"]=True;value["fresh_current_native_identity"]=False
        return value
    def export(self,handle):
        """Serialization preserves correlation only; it cannot recreate a ticket."""
        value=self.receipt(handle)
        value["tier"]="EXPORTED_UNAUTHENTICATED"
        value["recorded_tier"]="SYNTHETIC" if self.synthetic else "LOCAL_TRUSTED_CAPTURE"
        return value

def connect_clips(client,profile,journals,*,ffmpeg,ffmpeg_sha256):
    """Bind an already-connected production client. Does read-only native queries.

    Neither connection nor reserve approves recording. The caller invokes
    execute only within its separately authorized capture/test scope.
    """
    return ClipPort(client,profile,journals,ffmpeg=ffmpeg,ffmpeg_sha256=ffmpeg_sha256)

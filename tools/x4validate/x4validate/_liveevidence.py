"""Bound read evidence, with exact outer strings and no file or game writes.

Local handles retain trusted connection provenance. Exported replay is structural
evidence, never cryptographic authentication against arbitrary same-user code.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import Path
from ._liveautomation import AutomationClient
from ._livebound import BoundPipe
from ._livepeerprofile import ProfilePeer
from ._livepipe import Reply
from ._livereadpages import query_capture
from ._livesession import SessionLink, SessionUnavailable, _peer, _token, _REV

LIMIT=4*1024*1024
SOURCES=("_liveevidence.py","_livebound.py","_liveautomation.py","_livereadpages.py","_livesession.py",
         "_livetrees.py")

def need(ok,reason):
    if not ok:raise SessionUnavailable(reason)

def _json(value):
    try:
        raw=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")
        need(len(raw)<=LIMIT,"evidence byte capacity")
        return raw
    except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError) as exc:
        raise SessionUnavailable("malformed evidence object") from exc

def source_pins():
    root=Path(__file__).resolve().parent
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCES}

def _identity(value):
    keys={"provider","revision","session","ui_generation","world_token","load_generation","world_lineage","peer"}
    need(isinstance(value,dict) and set(value)==keys,"incomplete host identity")
    need(value["provider"]=="x4-live-session-v1" and value["world_lineage"]=="UNKNOWN","identity scope")
    _token(value["revision"],_REV);_token(value["session"]);_token(value["ui_generation"])
    need(type(value["load_generation"]) is int and 1<=value["load_generation"]<=4096,"invalid host load generation")
    need(isinstance(value["world_token"],str) and 0<len(value["world_token"])<=128,"missing world token")
    _peer(value["peer"])
    return copy.deepcopy(value)

def _request(verb,args):
    need(isinstance(verb,str) and re.fullmatch("[a-z][a-z0-9_]{0,63}",verb),"invalid source read verb")
    need(not verb.startswith("action_") and verb not in
         ("bound","session_connect","ui_state","pause","unpause","read_capture","read_page"),"not a source read")
    need(isinstance(args,(tuple,list)) and len(args)<=16 and
         all(isinstance(x,str) and len(x.encode("utf-8"))<=4096 for x in args),"bounded string read arguments required")
    return dict(verb=verb,args=list(args))

class _ReplayLink(SessionLink):
    def __init__(self,identity):
        self.saved=_identity(identity)
    def identity(self):
        return copy.deepcopy(self.saved)
    # Reuses SessionLink.verify_reply, including exact native stamp equality.

class _ReplayPipe:
    def __init__(self,frames,identity,build):
        self.frames=frames;self.identity=identity;self.build=build;self.index=0;self.seq=0
    def ask(self,verb,*args):
        need(self.index<len(self.frames),"transcript omitted reply")
        f=self.frames[self.index]
        need(isinstance(f,dict) and set(f)=={"verb","args","before","after","reply"},"malformed trace frame")
        need(f["before"]==self.identity and f["after"]==self.identity,"frame host identity changed")
        need(isinstance(f["args"],list),"frame arguments absent")
        expected=(self.identity["session"],self.identity["ui_generation"],self.identity["revision"],
                  self.identity["world_token"],self.build,f["verb"],*f["args"])
        need(verb=="bound" and args==expected,"transcript request/order differs")
        r=f["reply"]
        need(isinstance(r,dict) and set(r)=={"seq","status","raw"} and type(r["seq"]) is int and
             r["seq"]>self.seq and r["status"]=="OK" and isinstance(r["raw"],str),"raw reply identity/status")
        self.index+=1;self.seq=r["seq"]
        return Reply(r["seq"],r["status"],r["raw"])

def replay(transcript):
    """Reparse exact envelopes/pages; exported metadata is not authenticated."""
    raw=_json(transcript)
    t=json.loads(raw)
    need(set(t)=={"version","scope","tier","request","identity","profile_id","build","source_pins","frames"},
         "evidence fields differ")
    need(type(t["version"]) is int and t["version"]==1 and t["scope"]=="BOUND_READ_TRANSCRIPT",
         "evidence version/scope")
    need(t["tier"] in ("MACHINE_OBSERVED","SYNTHETIC"),"unqualified evidence tier")
    need(isinstance(t["profile_id"],str) and re.fullmatch("[0-9]{1,32}",t["profile_id"]),"profile id absent")
    need(t["source_pins"]==source_pins(),"replay source pins changed; qualify intended reader explicitly")
    need(isinstance(t["request"],dict) and set(t["request"])=={"verb","args"},"source request absent")
    req=_request(t["request"]["verb"],t["request"]["args"])
    identity=_identity(t["identity"])
    frames=t["frames"]
    need(isinstance(frames,list) and 2<=len(frames)<=33,"read frame denominator")
    need(sum(len(f.get("reply",{}).get("raw","").encode("utf-8")) for f in frames
             if isinstance(f,dict) and isinstance(f.get("reply"),dict) and
             isinstance(f["reply"].get("raw"),str))<=33*65536,"raw frame byte capacity")
    first=frames[0]
    need(isinstance(first,dict) and first.get("verb")=="read_capture" and
         isinstance(first.get("args"),list) and len(first["args"])==len(req["args"])+2,
         "capture request missing")
    nonce=first["args"][0];_token(nonce)
    need(first["args"][1:]==[req["verb"],*req["args"]],"original request mismatch")
    pipe=_ReplayPipe(frames,identity,t["build"]);bound=BoundPipe(pipe,_ReplayLink(identity),t["build"])
    reply,capture=query_capture(bound,nonce,req["verb"],req["args"])
    need(pipe.index==len(frames),"transcript extra frames")
    need(t["source_pins"]==source_pins(),"reader source changed during replay")
    return dict(scope="RAW_BOUND_ENVELOPES_REPLAYED",provenance="EXPORTED_REPLAY_NOT_AUTHENTICATED",
        recorded_tier=t["tier"],identity=identity,profile_id=t["profile_id"],build=t["build"],
        request=req,capture=capture,status=reply.status,transport_complete=True,atomic=False,
        world_lineage="UNKNOWN",transcript_sha256=hashlib.sha256(raw).hexdigest(),
        first_envelope_age=json.loads(frames[0]["reply"]["raw"])["age"],last_envelope_age=bound.last_age,
        raw_frames=len(frames),source_pins=t["source_pins"])

class ReadEvidence:
    """One bounded collector. Synthetic fixtures must opt in and stay SYNTHETIC."""
    def __init__(self,client,*,synthetic=False):
        need(isinstance(client,AutomationClient) and isinstance(client.bound,BoundPipe),"actual client/bound path required")
        need(type(synthetic) is bool,"explicit synthetic switch")
        need(synthetic or (client._native_connection is True and isinstance(client.bound.link,SessionLink) and
             isinstance(client.bound.link.peer,ProfilePeer)),"qualified production connection required")
        self.client=client;self.synthetic=synthetic;self.handles={};self.total=0;self.busy=False

    def collect(self,verb,*args):
        need(not self.busy and len(self.handles)<16,"nested/capacity evidence collection")
        req=_request(verb,args);self.busy=True
        try:
            pins=source_pins();before=_identity(self.client.identity())
            peer=self.client.bound.link.peer
            profile=peer.profile_id
            need(_peer(peer())==before["peer"],"profile peer changed before capture")
            with self.client.bound.read_trace() as trace:
                reply=self.client.query(verb,*args)
            after=_identity(self.client.identity())
            need(before==after and _peer(peer())==before["peer"] and peer.profile_id==profile,
                 "profile/session changed at capture close")
            need(pins==source_pins(),"collector sources changed")
            t=dict(version=1,scope="BOUND_READ_TRANSCRIPT",tier="SYNTHETIC" if self.synthetic else "MACHINE_OBSERVED",
                request=req,identity=before,profile_id=profile,build=self.client.bound.build,source_pins=pins,frames=trace.snapshot())
            parsed=replay(t)
            need(parsed["capture"]==self.client.last_read and reply.status==parsed["status"] and
                 reply.payload==parsed["capture"]["payload"],"client/replay result disagreement")
            encoded=_json(t)
            need(self.total+len(encoded)<=8*LIMIT,"collector retained byte capacity")
            handle=object();self.handles[handle]=(encoded,copy.deepcopy(parsed));self.total+=len(encoded)
            return handle
        finally:
            self.busy=False

    def receipt(self,handle):
        need(handle in self.handles,"foreign/unsettled read handle")
        raw,value=self.handles[handle]
        out=copy.deepcopy(value)
        out["provenance"]="LOCAL_TRUSTED_COLLECTION" if not self.synthetic else "SYNTHETIC_COLLECTION"
        out["evidence_tier"]="SYNTHETIC" if self.synthetic else "MACHINE_OBSERVED"
        out["fresh_current_identity"]=False
        return out

    def transcript(self,handle):
        need(handle in self.handles,"foreign/unsettled read handle")
        return json.loads(self.handles[handle][0])

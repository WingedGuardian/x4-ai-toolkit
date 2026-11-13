"""Bounded native still-file observation. Never captures or controls the game.

Candidate directories are inferred from a qualified native save-folder path.
Only an actual new, stable, decoded image qualifies an observed output location.
This does not prove the image's contents, pose or causal origin.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import warnings
from dataclasses import dataclass
from ._liveactions import _write_new, _read_json
from ._livepersistence import _route, _file, _json, _finite, _hex
from ._livesession import _peer
from ._livecapacity import resolve as capacity_profile_for

DIRECTORY="x4_toolkit_capture"
# Logical JSON charge, not RSS. Refuse before capture when unreleased histories
# exhaust it; released frames remain in their original immutable journals.
FRAME_RAM_JSON_LIMIT=32*1024**2
FRAME_JOURNAL_LIMIT=16*1024**2
class CaptureUnavailable(ValueError):
    pass
def need(ok,why):
    if not ok:raise CaptureUnavailable(why)

@dataclass(frozen=True)
class _FrameJournalRef:
    path: str
    sha256: str
    size: int

    @classmethod
    def bind(cls,path,expected):
        raw=json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()+b'\n'
        need(len(raw)<=FRAME_JOURNAL_LIMIT,'frame journal exceeds bound')
        row=_file(path,FRAME_JOURNAL_LIMIT)
        need(row['size']==len(raw) and row['sha256']==hashlib.sha256(raw).hexdigest(),'frame journal differs from owned bytes')
        return cls(str(path),row['sha256'],row['size'])

    def load(self,expected_path,identity,request_id,key):
        need(self.path==str(expected_path),'frame reference substitution')
        p=Path(self.path);before=_file(p,FRAME_JOURNAL_LIMIT)
        need(before['size']==self.size and before['sha256']==self.sha256,'frame journal changed')
        with p.open('rb') as stream:raw=stream.read(FRAME_JOURNAL_LIMIT+1)
        need(len(raw)==self.size and hashlib.sha256(raw).hexdigest()==self.sha256
             and _file(p,FRAME_JOURNAL_LIMIT)==before,'frame journal changed while reading')
        value=json.loads(raw)
        actual_id=value.get('id') if key=='frame' else value.get('request',{}).get('id')
        need(value.get('identity')==identity and actual_id==request_id and key in value,
             'frame journal ownership differs')
        if key=='frame':need(value.get('stage')=='SETTLED','not a settled frame journal')
        return value[key]

def decode_image(raw, *, max_pixels=40_000_000, max_dimension=16384):
    """Pillow verifies structure then fully decodes; magic bytes choose format."""
    try:
        from PIL import Image, ImageFile, __version__
    except ImportError as exc:
        raise CaptureUnavailable("optional Pillow capture decoder is not installed") from exc
    need(ImageFile.LOAD_TRUNCATED_IMAGES is False,"truncated-image decoding must stay disabled")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw),formats=("PNG","JPEG","BMP","TGA")) as im:
                fmt,size,mode=im.format,im.size,im.mode
                need(len(size)==2 and all(type(x)is int and 0<x<=max_dimension for x in size)
                     and size[0]*size[1]<=max_pixels,"image dimensions outside bound")
                need(getattr(im,"n_frames",1)==1,"multiframe image refused")
                im.verify()
            with Image.open(io.BytesIO(raw),formats=(fmt,)) as im:
                need(im.size==size and im.mode==mode,"decoder metadata changed")
                im.load()
        return {"format":fmt,"width":size[0],"height":size[1],"mode":mode,
                "decoder":"Pillow","decoder_version":__version__,"fully_decoded":True}
    except CaptureUnavailable:raise
    except (OSError,ValueError,SyntaxError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise CaptureUnavailable("image decoding failed") from exc

def _candidate(path):
    """Canonical absent leaf is allowed; every existing ancestor is checked."""
    p=Path(path)
    need(p.is_absolute(),"absolute candidate required")
    existing=p
    while not existing.exists():
        need(not existing.is_symlink(),"dangling candidate link")
        parent=existing.parent;need(parent!=existing,"no existing candidate ancestor");existing=parent
    _route(existing,directory=True)
    need(os.path.normcase(str(p.resolve()))==os.path.normcase(str(p)),"noncanonical candidate route")
    if p.exists():_route(p,directory=True)
    return p

class CaptureObserver:
    """One task journal owner, one in-flight capture; opaque handles, no replay.

    identity/native_paths are trusted bound-link callbacks, not caller JSON.
    The journal must be outside the personal game tree. It receives exclusive
    evidence files only. Screenshot directories are never created or changed.
    """
    def __init__(self,journal,*,identity,native_paths,capture_receipt,clock=time.monotonic,
                 settle_seconds=2,timeout_seconds=30,max_members=None,
                 max_file_bytes=256*1024**2,max_total_bytes=8*1024**3,decoder=decode_image,
                 capacity_profile=None):
        self.capacity=capacity_profile_for(capacity_profile)
        if max_members is None:max_members=self.capacity['host_directory_members']
        self.journal=_route(journal,directory=True)
        need(all(callable(x) for x in (identity,native_paths,capture_receipt,clock,decoder)),"trusted callbacks required")
        need(_finite(settle_seconds) and 1<=settle_seconds<=10,"settlement interval")
        need(_finite(timeout_seconds) and settle_seconds<timeout_seconds<=120,"observation timeout")
        for v in (max_members,max_file_bytes,max_total_bytes):
            need(type(v)is int and v>0,"positive enumeration bounds")
        need(max_members<=self.capacity['host_directory_members'] and max_file_bytes<=256*1024**2
             and max_total_bytes<=self.capacity['capture_total_bytes'],"bounds exceed supported maximum")
        self.identity=identity;self.native_paths=native_paths;self.capture_receipt=capture_receipt;self.clock=clock;self.decoder=decoder
        self.settle_seconds=settle_seconds;self.timeout_seconds=timeout_seconds
        self.max_members=max_members;self.max_file_bytes=max_file_bytes;self.max_total_bytes=max_total_bytes
        self.records={};self.pending=None;self.last=None;self.retained_frame_json_bytes=0
        self.journal_identity=[self.journal.stat().st_dev,self.journal.stat().st_ino]
        self.initial=self._identity()
        self.native_savedir,self.native_roots=self._roots()
        if decoder is decode_image:
            try:
                import PIL
            except ImportError as exc:
                raise CaptureUnavailable("UNAVAILABLE: install the optional capture decoder") from exc
        self.owner={"version":1,"identity":self.initial,
                    "capacity_profile":self.capacity,
                    "policy":"EXCLUSIVE_NO_AUTOMATIC_REOPEN_OR_REPLAY"}
        _write_new(self.journal/"capture-owner.json",self.owner)
    def _now(self):
        n=self.clock()
        need(_finite(n) and n>=0 and (self.last is None or n>=self.last),"unknown/backward host clock")
        self.last=n;return n
    def _identity(self):
        v=_json(self.identity())
        need(type(v)is dict and set(v)=={"provider","revision","session","load_generation",
             "ui_generation","peer","world_token","world_lineage"},"qualified SessionLink identity required")
        need(v["provider"]=="x4-live-session-v1" and _hex(v["revision"],64) and _hex(v["session"],32)
             and _hex(v["ui_generation"],32) and type(v["load_generation"])is int and v["load_generation"]>0
             and type(v["world_token"])is str and 0<len(v["world_token"])<=128
             and v["world_lineage"]=="UNKNOWN","invalid current identity")
        _peer(v["peer"]);return v
    @staticmethod
    def _stamp(v):
        return {"provider":v["provider"],"revision":v["revision"],"session":v["session"],
                "generation":v["ui_generation"],"world_token":v["world_token"]}
    def _current(self):
        _route(self.journal,directory=True)
        need([self.journal.stat().st_dev,self.journal.stat().st_ino]==self.journal_identity,"journal replaced")
        if hasattr(self,'owner'):need(_read_json(self.journal/'capture-owner.json')==self.owner,'capture owner journal changed')
        need(self._identity()==self.initial,"public identity/process changed")
        self._now()
    def _roots(self):
        self._current()
        v=_json(self.native_paths())
        need(type(v)is dict and set(v)=={"status","scope","stamp","save_folder"} and
             v["status"]=="OBSERVED" and v["scope"]=="NATIVE_SAVE_FOLDER_PATH" and
             v["stamp"]==self._stamp(self.initial),"unqualified native folder observation")
        need(type(v["save_folder"])is str and len(v["save_folder"])<=4096,"native save-folder string required")
        savedir=_route(v["save_folder"],directory=True)
        need(savedir.name.casefold()=="save","native save-folder shape unqualified")
        profile=savedir.parent
        need(not self.journal.is_relative_to(profile.parent) and
             not profile.parent.is_relative_to(self.journal),"journal/personal-tree overlap")
        roots=tuple(_candidate(base/"screenshots"/DIRECTORY) for base in (profile,profile.parent))
        self._current()
        if hasattr(self,"native_savedir"):
            need(savedir==self.native_savedir and roots==self.native_roots,"native path changed")
        return savedir,roots
    def _frame(self,roots):
        rows={};directories={};total=0
        for root in roots:
            _candidate(root)
            if not root.exists():
                directories[str(root)]=None;continue
            info=root.stat();directories[str(root)]=[info.st_dev,info.st_ino]
            for p in root.iterdir():
                need(len(rows)<self.max_members,"capture directory membership limit")
                need(p.is_file(),"unexpected non-file capture member")
                row=_file(p,self.max_file_bytes);total+=row["size"]
                need(total<=self.max_total_bytes,"capture cache byte limit")
                rows[str(p)]=row
        return {"files":rows,"directories":directories}
    def prepare(self,request):
        """Reserve before native prepare/execute; caller retains the opaque handle."""
        self._current();need(self.pending is None,"prior capture unresolved/quarantined")
        need(len(self.records)<self.capacity['host_capture_records'],"capture record limit")
        r=_json(request)
        need(type(r)is dict and set(r)=={"id","digest","session","provider","binding","native_revision","view_binding"},
             "exact capture request identity required")
        need(_hex(r["id"],32) and _hex(r["digest"],64) and _hex(r["binding"],64) and _hex(r["native_revision"],64) and
             r["provider"]=="x4-screen-capture-v1" and r["session"]==self.initial["session"]
             and type(r["view_binding"])is str and 0<len(r["view_binding"])<=256,"invalid capture identity")
        savedir,roots=self._roots();frame=self._frame(roots);self._current()
        need(len(frame['files'])<self.max_members,"no directory capacity for another capture")
        need(sum(row['size'] for row in frame['files'].values())<self.max_total_bytes,
             "no retained-image byte capacity for another capture")
        charge=len(json.dumps(frame,sort_keys=True,separators=(',',':')).encode())
        need(self.retained_frame_json_bytes+charge<=FRAME_RAM_JSON_LIMIT,'unreleased frame metadata capacity')
        handle=object()
        rec={"request":r,"save":str(savedir),"roots":roots,"before":frame,"created":self._now(),
             "sample":None,"sample_age":None,"receipt":None,"quarantine":None,"native":None,
             "frame_charge":charge,"before_ref":None}
        path=self.journal/("capture-"+r["id"]+".json")
        entry={
            "version":1,"identity":self.initial,"request":r,"native_save_folder":str(savedir),
            "candidate_roots":[str(x) for x in roots],"root_status":"INFERRED_CANDIDATES_NOT_OBSERVED",
            "before":frame}
        need(len(json.dumps(entry,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())+1<=FRAME_JOURNAL_LIMIT,
             'frame journal exceeds bound')
        _write_new(path,entry)
        rec['before_ref']=_FrameJournalRef.bind(path,entry)
        self.records[handle]=rec;self.pending=handle
        self.retained_frame_json_bytes+=charge
        return handle
    def _before(self,handle):
        self._current();rec=self.records[handle]
        expected=self.journal/('capture-'+rec['request']['id']+'.json')
        loaded=rec['before_ref'].load(expected,self.initial,rec['request']['id'],'before')
        if rec['before'] is not None:need(loaded==rec['before'],'retained frame differs')
        self._current();return loaded
    def _archive_released(self,handle):
        """Dispatcher-only after qualified native release; never forget evidence."""
        rec=self.records[handle]
        need(rec['receipt'] is not None and self.pending is None and not rec['quarantine'],'capture not settled')
        self._before(handle)
        if rec['before'] is not None:
            rec['before']=None;self.retained_frame_json_bytes-=rec['frame_charge']
    def capacity_usage(self):
        """Actual current file frame and retained attempt counts, not future fit."""
        self._current();_,roots=self._roots();frame=self._frame(roots);self._current()
        return {'scope':'CURRENT_CAPTURE_OWNER_AND_FILE_FRAME','capacity_profile':_json(self.capacity),
                'records_used':len(self.records),'records_limit':self.capacity['host_capture_records'],
                'directory_members':len(frame['files']),'directory_limit':self.max_members,
                'image_bytes':sum(r['size'] for r in frame['files'].values()),'image_byte_limit':self.max_total_bytes,
                'retained_before_frame_json_bytes':self.retained_frame_json_bytes,
                'unreleased_frame_json_limit':FRAME_RAM_JSON_LIMIT,
                'logical_charge_is_process_rss':False,'pending':self.pending is not None,
                'file_frame':frame,'future_fit_verified':False}
    def _native(self,rec,value):
        v=_json(value);r=rec["request"]
        need(type(v)is dict and set(v)=={"version","id","session","digest","status","observations","reason"},
             "exact capture action receipt required")
        need(type(v["version"])is int and v["version"]==1 and v["id"]==r["id"] and v["session"]==r["session"] and v["digest"]==r["digest"]
             and v["status"]=="running" and type(v["reason"])is str,"native capture not observed")
        o=v["observations"]
        need(type(o)is dict and o.get("adapter_revision")==r["binding"] and o.get("revision")==r["native_revision"]
             and o.get("native_revision")==r["native_revision"] and o.get("directory")==DIRECTORY and
             o.get("bridge_phase") in ("execute","status") and o.get("bridge_generation")==self.initial["ui_generation"]
             and o.get("file_confirmed") is False and o.get("visual_pass") is False,
             "capture receipt source/correlation missing")
        for k in ("prepared_age","capture_age","age"):
            need(_finite(o.get(k)) and o[k]>=0,"unknown native capture chronology")
        need(o["prepared_age"]+4<=o["capture_age"]<=o["prepared_age"]+30 and o["age"]>=o["capture_age"],
             "invalid native capture chronology")
        need(type(o.get("bridge_call"))is str and 0<len(o["bridge_call"])<=128,"transport call missing")
        return v
    def observe(self,handle):
        need(handle in self.records,"opaque capture handle required")
        rec=self.records[handle]
        need(not rec["quarantine"],"capture quarantined: "+str(rec["quarantine"]))
        try:
            self._current()
            if rec["receipt"] is not None:
                self._before(handle)
                savedir,roots=self._roots()
                need(_file(Path(rec["receipt"]["path"]),self.max_file_bytes)==rec["receipt"]["file"],
                     "previously observed image changed")
                self._current()
                historical=_json(rec["receipt"]);historical["historical"]=True
                return historical
            native=self._native(rec,self.capture_receipt(_json(rec["request"])))
            if rec["native"] is None:rec["native"]=native
            else:
                # status transport/age may differ, captured identity/context may not.
                old=rec["native"]["observations"];new=native["observations"]
                need(all(new.get(k)==old.get(k) for k in ("revision","adapter_revision","native_revision","directory","prepared_age","capture_age")),
                     "captured event rebound")
            savedir,roots=self._roots()
            need(str(savedir)==rec["save"] and roots==rec["roots"],"native path changed")
            after=self._frame(roots)
            for name,row in rec["before"]["files"].items():
                need(after["files"].get(name)==row,"pre-existing capture file changed/removed")
            for name,identity in rec["before"]["directories"].items():
                if identity is not None:need(after["directories"].get(name)==identity,"capture directory replaced")
            added={n:r for n,r in after["files"].items() if n not in rec["before"]["files"]}
            need(len(added)<=1,"multiple new files; attribution ambiguous")
            now=self._now()
            if not added:
                need(now-rec["created"]<=self.timeout_seconds,"capture file timeout; no replay")
                return {"status":"PENDING","reason":"NO_NEW_FILE","file_observed":False}
            path,row=next(iter(added.items()))
            if rec["sample"]!={"path":path,"file":row}:
                rec["sample"]={"path":path,"file":row};rec["sample_age"]=now
                need(now-rec["created"]<=self.timeout_seconds,"capture did not settle before deadline")
                return {"status":"PENDING","reason":"FILE_NOT_YET_STABLE","file_observed":False}
            if now-rec["sample_age"]<self.settle_seconds:
                return {"status":"PENDING","reason":"SETTLEMENT_INTERVAL","file_observed":False}
            need(now-rec["created"]<=self.timeout_seconds,"capture file settlement timeout")
            p=Path(path);_route(p)
            with p.open("rb") as stream:
                raw=stream.read(self.max_file_bytes+1)
            need(len(raw)<=self.max_file_bytes and hashlib.sha256(raw).hexdigest()==row["sha256"],"image changed before decode")
            image=_json(self.decoder(raw))
            need(type(image)is dict and image.get("fully_decoded") is True and
                 image.get("format") in ("PNG","JPEG","BMP","TGA") and
                 all(type(image.get(k))is int and 0<image[k]<=16384 for k in ("width","height")) and
                 image["width"]*image["height"]<=40_000_000,"qualified bounded image decode required")
            need(_file(p,self.max_file_bytes)==row,"image changed during decode")
            need(self._frame(roots)==after,"capture membership changed during decode")
            self._current()
            output={"status":"NATIVE_CAPTURE_FILE_OBSERVED","file_observed":True,"visual_pass":False,
                "causal_origin_proven":False,"pose_qualified":False,"identity":self.initial,
                "request":rec["request"],"native_receipt":native,"path":path,"file":row,
                "image":image,"observed_output_directory":str(p.parent),"root_status":"OBSERVED_FOR_THIS_REQUEST",
                "root_discovery_scope":"TWO_INFERRED_SAVE_FOLDER_ANCESTRY_CANDIDATES",
                "host_settlement_seconds":now-rec["sample_age"]}
            if rec["receipt"] is not None:
                need(rec["receipt"]["file"]==row and rec["receipt"]["path"]==path,"observed image changed")
                return _json(rec["receipt"])
            _write_new(self.journal/("capture-"+rec["request"]["id"]+".observed.json"),output)
            rec["receipt"]=_json(output);self.pending=None
            return _json(output)
        except Exception as exc:
            rec["quarantine"]=str(exc);self.pending=handle
            raise

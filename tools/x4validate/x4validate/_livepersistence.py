"""Read-only save-slot verification with task-owned durable evidence.

No save/load dispatch or approval issuance. Opaque handles are local capabilities
for observations only; serialization cannot recreate them or authorize a write.
"""
from __future__ import annotations
from ._livetrees import journal_refusal, verbatim_or_device
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import zlib
from xml.parsers import expat
from ._liveactions import _write_new
from ._livesession import _peer, SessionUnavailable

class PersistenceUnavailable(ValueError):
    pass

#: The only slots a toolkit save may target: the game's ten standard manual slots
#: (ego_detailmonitorhelper/helper.lua `Helper.validSaveFilenames`, save_%03d for 1..10).
#: Shared with `_livesave` (`x4live save`), so the two save paths cannot drift. fullmatch only.
STANDARD_SAVE_SLOT = re.compile(r"save_00[1-9]|save_010")

def _require(ok, why):
    if not ok:
        raise PersistenceUnavailable(why)

def _json(v):
    try:
        raw=json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    except (TypeError,ValueError,RecursionError) as exc:
        raise PersistenceUnavailable("non-JSON observation") from exc
    _require(len(raw)<=1_048_576,"observation byte limit")
    return json.loads(raw)

def _hex(v,n):
    return isinstance(v,str) and re.fullmatch("[0-9a-f]{"+str(n)+"}",v) is not None

def _finite(v):
    return type(v) in (int,float) and abs(v)<=2**53-1 and math.isfinite(v)

def _route(path, *, directory=False):
    path=Path(path)
    _require(path.is_absolute(),"absolute path required")
    # A verbatim (//?/) or device (//./) spelling, with either slash, survives resolve()
    # unchanged, so the noncanonical check below cannot see it; it is never the plain
    # path, so refuse it.
    _require(not verbatim_or_device(path),
             "verbatim or device path refused; use the plain drive path")
    for p in (path,*path.parents):
        try:
            st=p.lstat()
        except FileNotFoundError:
            continue  # silent-ok: inspect remaining ancestors; strict resolve below refuses a missing leaf.
        _require(not stat.S_ISLNK(st.st_mode) and
            not (getattr(st,"st_file_attributes",0)&0x400),"linked/reparse route refused")
    resolved=path.resolve(strict=True)
    _require(os.path.normcase(str(resolved))==os.path.normcase(str(path)),"noncanonical route refused")
    if directory:
        _require(path.is_dir(),"existing directory required")
    return path

def _sig(st):
    return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]

def _file(path, max_bytes):
    _route(path)
    before=path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink==1,"nonregular/hardlinked file refused")
    _require(0<=before.st_size<=max_bytes,"file byte limit")
    h=hashlib.sha256()
    with path.open("rb") as stream:
        opened=_sig(os.fstat(stream.fileno()))
        # Windows path-stat and descriptor-stat expose different ctime semantics.
        _require(opened[:4]==_sig(before)[:4],"file changed before open")
        count=0
        while chunk:=stream.read(1024*1024):
            count+=len(chunk);_require(count<=max_bytes,"file grew past byte limit");h.update(chunk)
        _require(_sig(os.fstat(stream.fileno()))==opened,"file changed while reading")
    _route(path)
    _require(_sig(path.lstat())==_sig(before) and count==before.st_size,"file replaced while reading")
    return {"size":count,"sha256":h.hexdigest(),"stat":_sig(before)}

class SlotVerifier:
    """Trusted identity/catalogue callbacks must come from the qualified live link.

    The journal is outside the personal tree (every profile). Existing saves are only read. Each
    reservation consumes a unique journal filename even after process restart.
    """
    def __init__(self, profile, journal, *, identity, catalogue, profile_peer, clock=time.monotonic,
                 settle_seconds=2.0, max_members=4096, max_file_bytes=512*1024**2,
                 max_total_bytes=8*1024**3, max_expanded_bytes=2*1024**3):
        self.profile=_route(profile,directory=True)
        self.save=_route(self.profile/"save",directory=True)
        self.journal=_route(journal,directory=True)
        why=journal_refusal(self.journal,profile=self.profile)
        _require(why is None,"journal overlap: "+str(why))
        _require(callable(identity) and callable(catalogue) and callable(profile_peer) and callable(clock),"trusted observers required")
        _require(_finite(settle_seconds) and 1<=settle_seconds<=60,"invalid settlement interval")
        for value in (max_members,max_file_bytes,max_total_bytes,max_expanded_bytes):
            _require(type(value) is int and value>0,"positive bounds required")
        self.identity=identity;self.catalogue=catalogue;self.clock=clock
        self.profile_peer=profile_peer
        self.settle_seconds=settle_seconds;self.max_members=max_members
        self.max_file_bytes=max_file_bytes;self.max_total_bytes=max_total_bytes
        self.max_expanded_bytes=max_expanded_bytes
        self._records={};self._receipts={};self._last=None
        self._directories={str(p):_sig(p.stat())[:2] for p in (self.profile,self.save,self.journal)}

    def _now(self):
        n=self.clock()
        _require(_finite(n) and n>=0 and (self._last is None or n>=self._last),"invalid/backward clock")
        self._last=n;return n

    def _routes(self):
        for raw,ident in self._directories.items():
            p=_route(raw,directory=True)
            _require(_sig(p.stat())[:2]==ident,"directory identity changed")

    def _identity(self):
        v=_json(self.identity())
        keys={"provider","revision","session","load_generation","ui_generation","peer","world_token","world_lineage"}
        _require(isinstance(v,dict) and set(v)==keys,"qualified SessionLink identity required")
        _require(v["provider"]=="x4-live-session-v1" and _hex(v["revision"],64)
                 and _hex(v["session"],32) and _hex(v["ui_generation"],32)
                 and type(v["load_generation"]) is int and v["load_generation"]>0
                 and isinstance(v["world_token"],str) and 0<len(v["world_token"])<=128
                 and v["world_lineage"]=="UNKNOWN","malformed public identity")
        try: _peer(v["peer"])
        except SessionUnavailable as exc: raise PersistenceUnavailable("invalid peer") from exc
        binding=self.profile_peer()
        _require(isinstance(binding,dict) and set(binding)=={"peer","profile_id"} and
                 binding["peer"]==v["peer"] and isinstance(binding["profile_id"],str) and
                 re.fullmatch("[0-9]{1,20}",binding["profile_id"]) is not None and
                 self.profile.name==binding["profile_id"],"connected profile binding unavailable")
        return v

    @staticmethod
    def _stamp(identity):
        return {"provider":identity["provider"],"revision":identity["revision"],
                "session":identity["session"],"generation":identity["ui_generation"],
                "world_token":identity["world_token"]}

    def _native(self, identity):
        v=_json(self.catalogue())
        _require(isinstance(v,dict) and v.get("status")=="OBSERVED" and
                 v.get("scope")=="NATIVE_STANDARD_SAVE_CATALOGUE" and v.get("atomic") is False and
                 v.get("stamp")==self._stamp(identity) and type(v.get("saveable")) is bool,
                 "unqualified native catalogue")
        rows=v.get("rows");count=v.get("count")
        _require(type(count) is int and 0<=count<=64,"catalogue count unavailable")
        if type(rows) is dict and rows=={} and count==0:
            rows=[];v["rows"]=rows  # Actual empty Lua table through the qualified wire.
        _require(isinstance(rows,list) and len(rows)==count,"catalogue denominator")
        allowed={"online_save","quicksave"}|{f"autosave_{i:02}" for i in range(1,4)}|{f"save_{i:03}" for i in range(1,11)}
        names=set()
        for r in rows:
            _require(isinstance(r,dict) and r.get("filename") in allowed and r["filename"] not in names
                     and isinstance(r.get("name"),str) and _finite(r.get("rawtime")) and r["rawtime"]>=0,
                     "invalid/duplicate native row")
            names.add(r["filename"])
        return v

    def _frame(self):
        self._routes(); files={};dirs={};seen=set();total=0;members=0
        def walk(folder):
            nonlocal total,members
            _require(len(folder.relative_to(self.save).parts)<=32,"save directory depth limit")
            for p in sorted(folder.iterdir(),key=lambda p:p.name.casefold()):
                members+=1;_require(members<=self.max_members,"save member limit")
                rel=p.relative_to(self.save).as_posix();fold=rel.casefold()
                _require(fold not in seen,"case-aliased save members");seen.add(fold)
                _route(p);st=p.lstat()
                if stat.S_ISDIR(st.st_mode):
                    dirs[rel]=_sig(st)[:2];walk(p)
                else:
                    row=_file(p,self.max_file_bytes);total+=row["size"]
                    _require(total<=self.max_total_bytes,"save total byte limit");files[rel]=row
        walk(self.save);self._routes()
        return {"files":files,"directories":dirs}

    @staticmethod
    def _related(name,slot):
        # Unknown variants such as .bak, temp suffixes and nested copies refuse.
        return Path(name).name.casefold().startswith(slot.casefold())

    def _record(self,token):
        _require(type(token) is object and token in self._records,"foreign/serialized reservation")
        r=self._records[token]
        _require(not r.get("fault"),"reservation faulted")
        self._routes()
        _require(self._identity()==r["identity"],"public identity changed")
        _require(self._now()-r["started"]<=300,"reservation expired")
        _require(_file(r["journal"],self.max_file_bytes)["sha256"]==r["journal_sha256"],"reservation journal changed")
        _require(_file(r["claim"],self.max_file_bytes)["sha256"]==r["claim_sha256"],"slot claim changed")
        return r

    def reserve(self, request_id, slot):
        _require(_hex(request_id,32) and isinstance(slot,str) and
                 STANDARD_SAVE_SLOT.fullmatch(slot) is not None,"fixed slot/request required")
        _require(len(self._records)<64,"reservation limit")
        self._routes();started=self._now();identity=self._identity();native=self._native(identity)
        _require(native["saveable"],"native saving unavailable")
        label="x4live-"+request_id
        _require(all(v["filename"]!=slot and v["name"]!=label for v in native["rows"]),"native slot/label occupied")
        before=self._frame()
        _require(not any(self._related(n,slot) for n in (*before["files"],*before["directories"])),
                 "physical slot or unknown related member already exists")
        _require(self._identity()==identity,"identity changed during reservation")
        row={"version":1,"status":"OBSERVATION_RESERVED_NOT_AUTHORIZATION","request_id":request_id,"slot":slot,
             "label":label,"identity":identity,"profile":str(self.profile),"save_directory":str(self.save),
             "native":native,"before":before,"started_monotonic":started,"settle_seconds":self.settle_seconds}
        path=self.journal/(request_id+"-reservation.json")
        self._routes()
        claim=self.journal/(slot+"-claim.json")
        _write_new(claim,{"request_id":request_id,"slot":slot,"status":"TASK_JOURNAL_SLOT_RESERVED"})
        _write_new(path,row)
        token=object()
        self._records[token]={"row":row,"identity":identity,"slot":slot,"label":label,"before":before,
            "started":started,"journal":path,"journal_sha256":_file(path,self.max_file_bytes)["sha256"],
            "claim":claim,"claim_sha256":_file(claim,self.max_file_bytes)["sha256"],
            "polls":0,"stable":None}
        return token

    def preflight(self,token):
        r=self._record(token);native=self._native(r["identity"])
        _require(native["saveable"] and native["rows"]==r["row"]["native"]["rows"],"native preflight drift")
        _require(self._frame()==r["before"],"physical preflight drift")
        _require(self._identity()==r["identity"],"identity changed during preflight")
        return {"status":"EMPTY_SLOT_OBSERVED_NOT_AUTHORIZATION","request_id":r["row"]["request_id"],
                "slot":r["slot"],"guard_qualified":False,"approved":False,"exclusive_lock":False}

    def _parse(self,path,label,expected):
        parser=expat.ParserCreate();stack=[];found=[];games=[];expanded=0;root=None
        def start(name,attrs):
            nonlocal root
            stack.append(name)
            _require(len(stack)<=512,"XML depth limit")
            if len(stack)==1: root=name
            if stack==["savegame","info","save"]: found.append(dict(attrs))
            if stack==["savegame","info","game"]: games.append(dict(attrs))
        def forbidden(*_): raise PersistenceUnavailable("DTD/entity declarations refused")
        parser.StartElementHandler=start;parser.EndElementHandler=lambda _:stack.pop()
        parser.StartDoctypeDeclHandler=forbidden;parser.EntityDeclHandler=forbidden
        parser.ExternalEntityRefHandler=forbidden
        _route(path)
        with path.open("rb") as raw:
            opened=_sig(os.fstat(raw.fileno()))
            _require(opened[:4]==expected["stat"][:4],"save changed before parsing")
            stream=gzip.GzipFile(fileobj=raw,mode="rb") if path.name.endswith(".gz") else raw
            try:
                while chunk:=stream.read(1024*1024):
                    expanded+=len(chunk);_require(expanded<=self.max_expanded_bytes,"expanded byte limit")
                    parser.Parse(chunk,False)
                parser.Parse(b"",True)
            finally:
                if stream is not raw: stream.close()
            _require(_sig(os.fstat(raw.fileno()))==opened,"save changed during parsing")
        _require(root=="savegame" and len(found)==1 and found[0].get("name")==label and
                 len(games)==1 and games[0].get("id")=="X4","save XML identity mismatch")
        _require(_file(path,self.max_file_bytes)==expected,"save bytes changed during parsing")
        return {"expanded_bytes":expanded,"root":root,"save_header":found[0],"game_header":games[0],
                "parser":"EXPAT_COMPLETE_NO_DTD_ENTITIES"}

    def observe_settlement(self,token):
        r=self._record(token)
        _require(r["polls"]<64,"settlement poll limit");r["polls"]+=1
        try:
            frame=self._frame();base=r["before"]
            _require(frame["directories"]==base["directories"],"save directory membership changed")
            _require(all(frame["files"].get(n)==v for n,v in base["files"].items()),"preexisting save changed/removed")
            added=set(frame["files"])-set(base["files"])
            if not added:
                r["stable"]=None;return None
            _require(len(added)==1,"unexpected added save members")
            rel=next(iter(added))
            _require(rel in {r["slot"]+".xml",r["slot"]+".xml.gz"},"unexpected save representation")
            path=self.save/rel;file=frame["files"][rel]
            if file["size"]==0:
                r["stable"]=None;return None
            native=self._native(r["identity"])
            matches=[v for v in native["rows"] if v["filename"]==r["slot"]]
            if not matches or matches[0]["name"]!=r["label"]:
                r["stable"]=None;return None
            row=matches[0]
            _require(not any(row.get(k) for k in ("isonline","error","invalidgameid","invalidversion","invalidpatches")),
                     "invalid native completed save")
            previous=[v for v in native["rows"] if v["filename"]!=r["slot"]]
            _require(previous==r["row"]["native"]["rows"],"unrelated native catalogue drift")
            observed={"file":file,"native":row,"rel":rel}
            now=self._now()
            if r["stable"] is None or r["stable"][0]!=observed:
                r["stable"]=(observed,now);return None
            if now-r["stable"][1]<self.settle_seconds:
                return None
            parsed=self._parse(path,r["label"],file)
            _require(self._frame()==frame and self._identity()==r["identity"] and
                     self._native(r["identity"])["rows"]==native["rows"],"settlement end drift")
            self._routes()
            _require(self._identity()==r["identity"],"identity changed during final catalogue")
            receipt={"version":1,"status":"SETTLED_SAVE_OBSERVED_NOT_AUTHORIZATION",
                     "request_id":r["row"]["request_id"],"slot":r["slot"],"label":r["label"],
                     "path":str(path),"file":file,"parsed":parsed,"native":row,"identity":r["identity"],
                     "before_journal_sha256":r["journal_sha256"],"preserved_files":len(base["files"]),
                     "stable_seconds":now-r["stable"][1],"observed_monotonic":now,
                     "causal_engine_completion":False,"load_approved":False,"guard_qualified":False}
            out=self.journal/(r["row"]["request_id"]+"-settled.json")
            self._routes()
            _write_new(out,receipt)
            handle=object();self._receipts[handle]=(token,receipt,out,_file(out,self.max_file_bytes)["sha256"])
            r["fault"]="settlement finalized; use receipt"
            return handle
        except (OSError,ValueError,expat.ExpatError,EOFError,zlib.error) as exc:
            r["fault"]=str(exc)
            raise PersistenceUnavailable(str(exc)) from exc

    def receipt(self,handle,*,recheck=False):
        _require(type(handle) is object and handle in self._receipts,"foreign/serialized receipt")
        token,row,path,digest=self._receipts[handle]
        self._routes()
        _require(_file(path,self.max_file_bytes)["sha256"]==digest,"settlement journal changed")
        if recheck:
            r=self._records[token]
            _require(_file(r["journal"],self.max_file_bytes)["sha256"]==r["journal_sha256"] and
                     _file(r["claim"],self.max_file_bytes)["sha256"]==r["claim_sha256"],"reservation evidence changed")
            _require(self._identity()==row["identity"],"receipt session changed")
            _require(_file(Path(row["path"]),self.max_file_bytes)==row["file"],"receipt save changed")
            frame=self._frame()
            expected=copy.deepcopy(r["before"]);expected["files"][Path(row["path"]).name]=row["file"]
            _require(frame==expected,"receipt preservation frame changed")
            native=self._native(row["identity"])
            _require([v for v in native["rows"] if v["filename"]==row["slot"]]==[row["native"]],
                     "receipt native identity changed")
            _require([v for v in native["rows"] if v["filename"]!=row["slot"]]==r["row"]["native"]["rows"],
                     "receipt unrelated catalogue changed")
            self._routes()
            _require(self._identity()==row["identity"],"identity changed during receipt recheck")
        return copy.deepcopy(row)

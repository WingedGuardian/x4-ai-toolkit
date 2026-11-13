"""Bounded, exact-HWND host clips. No launch, input, focus or game mutation.

A decoded clip is an observation artifact, never proof of motion or visibility.
Callbacks come from the qualified live connection, not caller-supplied JSON.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from ._liveactions import _write_new
from ._livepersistence import _route, _file, _json, _hex, _finite
from ._livesession import _peer

MAX_BYTES = 512 * 1024**2
MAX_FRAMES = 900

class ClipUnavailable(ValueError):
    pass

def need(ok, reason):
    if not ok:
        raise ClipUnavailable(reason)

def window_for_peer(peer, expected=None, *, allow_minimized=False):
    """Read-only Win32 enumeration. Require exactly one eligible top-level HWND.

    Creation FILETIME and image are queried independently of the pipe. No
    foreground/restore/show calls; an obscured surface is NOT proven visible.
    Explicit allow_minimized is OS-window metadata only, never capture evidence.
    """
    need(os.name == "nt", "window capture is Windows-only")
    need(type(allow_minimized)is bool,"explicit minimized metadata mode must be boolean")
    peer = _peer(peer)
    from ctypes import wintypes as W
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u = ctypes.WinDLL("user32", use_last_error=True)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; k.OpenProcess.restype=W.HANDLE
    k.GetProcessTimes.argtypes=[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4; k.GetProcessTimes.restype=W.BOOL
    k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
    k.QueryFullProcessImageNameW.restype=W.BOOL
    k.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]; k.GetExitCodeProcess.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE]; k.CloseHandle.restype=W.BOOL
    u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]; u.GetWindowThreadProcessId.restype=W.DWORD
    u.IsWindowVisible.argtypes=[W.HWND]; u.IsWindowVisible.restype=W.BOOL
    u.IsIconic.argtypes=[W.HWND]; u.IsIconic.restype=W.BOOL
    u.GetClientRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]; u.GetClientRect.restype=W.BOOL
    callback=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    u.EnumWindows.argtypes=[callback,W.LPARAM]; u.EnumWindows.restype=W.BOOL
    process=k.OpenProcess(0x1000,False,peer["pid"])
    need(process,"cannot query window process")
    try:
        def check_process():
            stamps=[W.FILETIME() for _ in range(4)]
            need(k.GetProcessTimes(process,*[ctypes.byref(x) for x in stamps]),"process times unavailable")
            image=ctypes.create_unicode_buffer(32768); size=W.DWORD(len(image)); code=W.DWORD()
            need(k.QueryFullProcessImageNameW(process,0,image,ctypes.byref(size)),"process image unavailable")
            created=str((stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime)
            need(created==peer["creation_filetime"] and os.path.normcase(image.value)==os.path.normcase(peer["image"]),
                 "process incarnation changed")
            need(k.GetExitCodeProcess(process,ctypes.byref(code)) and code.value==259,"process exited")
        check_process()
        found=[]; failures=[]
        def visit(hwnd, unused):
            pid=W.DWORD()
            if not u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid)):
                failures.append("window PID query failed"); return True
            if pid.value==peer["pid"] and u.IsWindowVisible(hwnd):
                rect=W.RECT()
                minimized=bool(u.IsIconic(hwnd))
                if (minimized and not allow_minimized) or not u.GetClientRect(hwnd,ctypes.byref(rect)):
                    failures.append("game window minimized/unreadable"); return True
                width,height=rect.right-rect.left,rect.bottom-rect.top
                lower=-1 if minimized and allow_minimized else 0
                if not (lower<width<=7680 and lower<height<=4320):
                    failures.append("game window dimensions out of bound"); return True
                row={"hwnd":str(int(hwnd)),"pid":pid.value,"width":width,"height":height}
                if allow_minimized:row.update(minimized=minimized,scope="OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED")
                found.append(row)
            return True
        fn=callback(visit)
        need(u.EnumWindows(fn,0),"window enumeration failed")
        check_process()
        need(not failures and len(found)==1,"unique eligible game window unavailable")
        row=found[0]
        need(expected is None or row==expected,"game window identity/size changed")
        return row
    finally:
        k.CloseHandle(process)

def run_bounded(argv, *, output=None, cap=2*1024**2, timeout=60, check=lambda:None):
    """Drain both pipes with byte caps; terminate only this owned child on failure."""
    need(type(argv)is list and all(type(x)is str and "\x00" not in x for x in argv),"argument vector required")
    need(type(cap)is int and 0<cap<=MAX_BYTES and _finite(timeout) and 0<timeout<=120,"child limits")
    flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          shell=False,creationflags=flags)
    chunks=[]; errors=[]; stderr=bytearray(); total=[0]; lock=threading.Lock()
    def drain(stream, main):
        try:
            while data:=stream.read(65536):
                with lock:
                    if main:
                        total[0]+=len(data)
                        if total[0]>cap:
                            errors.append("child output byte limit"); return
                        if output is None: chunks.append(data)
                        else: output.write(data)
                    else:
                        if len(stderr)+len(data)>65536:
                            errors.append("child stderr byte limit"); return
                        stderr.extend(data)
        except (OSError,ValueError) as exc:
            with lock: errors.append(str(exc))
        finally:
            stream.close()
    threads=[threading.Thread(target=drain,args=(proc.stdout,True),daemon=True),
             threading.Thread(target=drain,args=(proc.stderr,False),daemon=True)]
    for t in threads:t.start()
    start=time.monotonic()
    try:
        while proc.poll() is None:
            check()
            need(time.monotonic()-start<=timeout,"owned child timeout")
            need(not errors,"owned child output failed: "+str(errors[:1]))
            time.sleep(0.1)
        for t in threads:t.join(2)
        need(all(not t.is_alive() for t in threads),"child pipe drain incomplete")
        need(not errors,"owned child output failed: "+str(errors[:1]))
        check()
        need(proc.returncode==0,"owned child failed: "+stderr.decode("utf-8","replace")[:1024])
        if output is not None: output.flush(); os.fsync(output.fileno())
        return b"".join(chunks)
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait(timeout=5)
        for t in threads:t.join(2)

def frame_evidence(raw, width, height, duration, fps):
    """Strict FFmpeg framehash (decoded frames) v2. No CFR count inference."""
    try: text=raw.decode("ascii")
    except UnicodeError as exc: raise ClipUnavailable("framehash is not ASCII") from exc
    need(len(raw)<=2*1024**2,"framehash output limit")
    lines=text.splitlines()
    need("#format: frame checksums" in lines and "#version: 2" in lines and "#hash: SHA256" in lines,
         "unsupported framehash format")
    tbs=[re.fullmatch(r"#tb 0: (\d+)/(\d+)",x) for x in lines if x.startswith("#tb")]
    dims=[re.fullmatch(r"#dimensions 0: (\d+)x(\d+)",x) for x in lines if x.startswith("#dimensions")]
    need(len(tbs)==1 and tbs[0] and len(dims)==1 and dims[0],"one video timebase/dimension required")
    num,den=map(int,tbs[0].groups())
    need(0<num<=10**9 and 0<den<=10**9 and tuple(map(int,dims[0].groups()))==(width,height),
         "decoded dimensions/timebase mismatch")
    frames=[]
    for line in lines:
        if not line or line.startswith("#"):continue
        fields=[x.strip() for x in line.split(",")]
        need(len(fields)==6 and fields[0]=="0" and all(re.fullmatch(r"-?\d{1,20}",x) for x in fields[1:5])
             and _hex(fields[5],64),"invalid decoded frame row")
        dts,pts,span,size=map(int,fields[1:5])
        need(dts==pts and pts>=0 and span>=0 and 0<size<=width*height*16,"invalid decoded frame timing/size")
        need(not frames or pts>frames[-1]["pts"],"nonincreasing decoded timestamps")
        need(len(frames)<MAX_FRAMES,"decoded frame count limit")
        frames.append({"pts":pts,"duration":span,"bytes":size,"sha256":fields[5]})
    need(len(frames)>=2,"fewer than two decoded frames")
    seconds=(frames[-1]["pts"]-frames[0]["pts"])*num/den
    gaps=[(b["pts"]-a["pts"])*num/den for a,b in zip(frames,frames[1:])]
    need(duration-1.0<=seconds<=duration+1.0,"decoded clip interval outside request tolerance")
    need(max(gaps)<=max(1.0,4/fps),"decoded clip has excessive sampling gap")
    return {"decoded_frames":len(frames),"time_base":[num,den],"frames":frames,
            "timestamp_span_seconds":seconds,"maximum_gap_seconds":max(gaps),
            "distinct_decoded_hashes":len({r["sha256"] for r in frames}),
            "motion_verified":False,"compositor_frame_timing_verified":False}

class WindowClipRecorder:
    """Exclusive task directory; opaque prepared handles dispatch once only.

    identity/profile_peer must be live observations from SessionLink/ProfilePeer.
    This class never grants launch or recording approval. Caller owns that scope.
    Injected window/runner are allowed only with explicit synthetic=True.
    """
    def __init__(self,journal,*,ffmpeg,ffmpeg_sha256,identity,profile_peer,
                 window=window_for_peer,runner=run_bounded,synthetic=False,clock=time.monotonic):
        need(type(synthetic)is bool and (synthetic or (window is window_for_peer and runner is run_bounded)),
             "custom backend requires synthetic evidence tier")
        self.journal=_route(journal,directory=True); self.binary=_route(ffmpeg)
        need(_hex(ffmpeg_sha256,64),"qualified FFmpeg binary hash required")
        self.binary_row=_file(self.binary,256*1024**2)
        need(self.binary_row["sha256"]==ffmpeg_sha256,"FFmpeg binary drift")
        need(all(callable(f) for f in (identity,profile_peer,window,runner,clock)),"trusted callbacks required")
        self.identity=identity;self.profile_peer=profile_peer;self.window=window;self.runner=runner;self.clock=clock
        self.synthetic=synthetic;self.records={};self.pending=None;self.last=None
        self.retained_bytes=0;self._operation=threading.Lock()
        self.directory=[self.journal.stat().st_dev,self.journal.stat().st_ino]
        self.initial,self.profile=self._current_identity()
        _write_new(self.journal/"clip-owner.json",{"version":1,"identity":self.initial,
                   "profile":self.profile,"binary":self.binary_row,"policy":"NO_REOPEN_NO_REPLAY",
                   "tier":"SYNTHETIC" if synthetic else "HOST_WINDOW_OBSERVATION"})
    def _now(self):
        value=self.clock()
        need(_finite(value) and value>=0 and (self.last is None or value>=self.last),"invalid/backward host clock")
        self.last=value;return value
    def _current_identity(self):
        v=_json(self.identity());binding=_json(self.profile_peer())
        need(type(v)is dict and set(v)=={"provider","revision","session","load_generation","ui_generation",
             "peer","world_token","world_lineage"},"qualified live identity required")
        need(v["provider"]=="x4-live-session-v1" and _hex(v["revision"],64) and _hex(v["session"],32)
             and _hex(v["ui_generation"],32) and type(v["load_generation"])is int and v["load_generation"]>0
             and type(v["world_token"])is str and 0<len(v["world_token"])<=128 and v["world_lineage"]=="UNKNOWN",
             "invalid live identity")
        _peer(v["peer"])
        need(type(binding)is dict and set(binding)=={"peer","profile_id"} and binding["peer"]==v["peer"]
             and type(binding["profile_id"])is str and re.fullmatch(r"\d{1,20}",binding["profile_id"]),
             "profile/process identity mismatch")
        return v,binding
    def _check(self,row=None):
        _route(self.journal,directory=True)
        need([self.journal.stat().st_dev,self.journal.stat().st_ino]==self.directory,"journal replaced")
        need(self._current_identity()==(self.initial,self.profile),"live session/profile changed")
        self._now()
        if row is not None:
            need(self.window(self.initial["peer"],row["window"])==row["window"],"window changed")
            prepared=self.journal/(row["id"]+"-prepared.json")
            file=_file(prepared,65536)
            with prepared.open("rb") as stream:
                raw=stream.read(65537)
            need(len(raw)<=65536 and hashlib.sha256(raw).hexdigest()==file["sha256"],"prepared journal unstable")
            need(json.loads(raw)==row["prepared"],
                 "prepared journal changed")
    def prepare(self,request_id,*,duration=10,fps=15):
        need(self._operation.acquire(blocking=False),"clip operation already active")
        try:
            return self._prepare(request_id,duration=duration,fps=fps)
        finally:
            self._operation.release()
    def _prepare(self,request_id,*,duration,fps):
        self._check()
        need(self.pending is None and len(self.records)<128,"clip pending/quarantined or capacity exhausted")
        need(self.retained_bytes+MAX_BYTES<=2*1024**3,"retained clip budget exhausted")
        need(_hex(request_id,32) and type(duration)is int and 5<=duration<=30
             and type(fps)is int and 5<=fps<=30 and duration*fps<=MAX_FRAMES,"clip request limits")
        win=_json(self.window(self.initial["peer"]))
        need(type(win)is dict and set(win)=={"hwnd","pid","width","height"} and
             type(win["hwnd"])is str and re.fullmatch(r"[1-9]\d{0,19}",win["hwnd"]) and int(win["hwnd"])<2**64
             and win["pid"]==self.initial["peer"]["pid"] and
             all(type(win[k])is int and 0<win[k]<=7680 for k in ("width","height")) and win["height"]<=4320,
             "invalid exact window observation")
        row={"id":request_id,"window":win,"duration":duration,"fps":fps,"stage":"PREPARED"}
        row["prepared"]={"version":1,"identity":self.initial,"profile":self.profile,"window":win,
                         "duration":duration,"fps":fps,"id":request_id,"binary":self.binary_row}
        _write_new(self.journal/(request_id+"-prepared.json"),row["prepared"])
        handle=object();self.records[handle]=row;self.pending=handle
        return handle
    def execute(self,handle):
        need(self._operation.acquire(blocking=False),"clip operation already active")
        try:
            return self._execute(handle)
        finally:
            self._operation.release()
    def _execute(self,handle):
        need(handle in self.records and self.pending is handle,"opaque current handle required")
        row=self.records[handle];need(row["stage"]=="PREPARED","clip already consumed")
        self._check(row)
        need(_file(self.binary,256*1024**2)==self.binary_row,"FFmpeg binary changed")
        row["stage"]="CONSUMED"
        path=self.journal/(row["id"]+".mkv")
        _write_new(self.journal/(row["id"]+"-consumed.json"),{"version":1,"id":row["id"],"time":self._now()})
        start=self._now()
        try:
            # stdout file descriptor avoids encoder-selected paths, overwrites or protocols.
            with path.open("xb") as out:
                argv=[str(self.binary),"-nostdin","-hide_banner","-loglevel","error","-f","gdigrab",
                      "-draw_mouse","0","-framerate",str(row["fps"]),"-i","hwnd="+row["window"]["hwnd"],
                      "-t",str(row["duration"]),"-an","-map","0:v:0","-fps_mode","passthrough",
                      "-enc_time_base","demux","-c:v","libx264","-preset","ultrafast","-crf","18",
                      "-threads","2","-f","matroska","pipe:1"]
                self.runner(argv,output=out,cap=MAX_BYTES,timeout=row["duration"]+15,check=lambda:self._check(row))
            file=_file(path,MAX_BYTES)
            need(file["size"]>0,"empty clip")
            raw=self.runner([str(self.binary),"-nostdin","-hide_banner","-loglevel","error","-xerror",
                 "-i",str(path),"-map","0:v:0","-an","-fps_mode","passthrough","-enc_time_base","demux","-f","framehash",
                 "-hash","sha256","pipe:1"],timeout=60,check=lambda:self._check(row))
            evidence=frame_evidence(raw,row["window"]["width"],row["window"]["height"],row["duration"],row["fps"])
            need(_file(path,MAX_BYTES)==file and _file(self.binary,256*1024**2)==self.binary_row,"clip/binary changed during decode")
            self._check(row)
            result={"version":1,"status":"DECODED_HOST_CLIP","tier":"SYNTHETIC" if self.synthetic else "HOST_WINDOW_OBSERVATION",
                    "identity":self.initial,"profile":self.profile,"window":row["window"],"id":row["id"],
                    "file":file,"path":str(path),"host_interval":[start,self._now()],"evidence":evidence,
                    "pixel_origin_verified":False,"unoccluded_verified":False,"visual_case_pass":False,
                    "sampling":"FFMPEG_GDIGRAB_CAPTURE_POLL_NOT_COMPOSITOR_CLOCK",
                    "window_checks":"SAMPLED_BEFORE_DURING_AFTER_NOT_ATOMIC"}
            _write_new(self.journal/(row["id"]+"-observed.json"),result)
            self.retained_bytes+=file["size"]
            row["stage"]="OBSERVED";self.pending=None
            return _json(result)
        except BaseException:
            row["stage"]="QUARANTINED"
            raise

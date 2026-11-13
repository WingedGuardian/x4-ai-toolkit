"""Bounded Windows process-tree PCM evidence. Never plays or launches anything."""
from __future__ import annotations
import array
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import threading
import time
import wave
from ._liveactions import _write_new, _read_json
from ._liveautomation import AutomationClient
from ._livebound import BoundPipe, _decode
from ._liveclip import run_bounded
from ._liveevidence import ReadEvidence, source_pins as read_pins
from ._livepeerprofile import ProfilePeer
from ._livepersistence import _route, _file
from ._livesession import SessionLink

MAX_BYTES=16*1024**2
MAX_PACKETS=8192
RATE=44100

class AudioUnavailable(ValueError):
    pass

def need(ok,why):
    if not ok: raise AudioUnavailable(why)

# The native helper's source lives in the toolkit checkout (tools/native_audio), not in
# the x4validate package; an installed wheel does not carry it.
NATIVE_SOURCE=Path(__file__).resolve().parent.parents[1]/"native_audio"/"process_loopback.cpp"

def source_pins():
    root=Path(__file__).resolve().parent
    names=("_liveaudio.py","_liveclip.py","_livepersistence.py","_liveactions.py")
    need(NATIVE_SOURCE.is_file(),
         "process_loopback.cpp not found beside this x4validate; audio capture needs the toolkit checkout "
         "(tools/native_audio), whose source the helper binary embeds and every stream header is checked against")
    return read_pins() | {n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names} | {
        "process_loopback.cpp":hashlib.sha256(NATIVE_SOURCE.read_bytes()).hexdigest()}

def _header(raw,*,peer,duration,native_source_sha256,synthetic):
    """Same fixed header for started observation and complete stream validation."""
    need(type(raw)is bytes and len(raw)>=120 and raw[:8]==b"X4AUD001","audio stream header missing")
    need(raw[8:72].decode("ascii",errors="replace")==native_source_sha256,"native source digest differs")
    fixture,pid,creation,rate,channels,bits,ms,freq,began=struct.unpack_from("<IIQIIIIQQ",raw,72)
    need(fixture==(1 if synthetic else 0),"synthetic/native stream tier differs")
    need(pid==peer["pid"] and str(creation)==peer["creation_filetime"],"captured process incarnation differs")
    need((rate,channels,bits,ms)==(RATE,2,16,duration*1000) and 0<freq<=10**12,"PCM/header contract differs")
    return ms,freq,began

class _StartedOutput:
    """Worker-owned file sink; retains only the fixed header in shared memory."""
    def __init__(self,stream,work):self.stream=stream;self.work=work
    def write(self,data):
        n=self.stream.write(data)
        need(n==len(data),"partial audio artifact write")
        with self.work["lock"]:
            h=self.work["header"]
            if len(h)<120:
                h.extend(data[:120-len(h)])
                if len(h)==120:self.work["ready"].set()
        return n
    def flush(self):return self.stream.flush()
    def fileno(self):return self.stream.fileno()

def parse_packets(raw,*,peer,duration,native_source_sha256,synthetic=False):
    """Decode helper packets; flags and no-packet windows remain explicit.

    No target-name or sound-name attribution comes from PCM. QPC packet times
    are native 100ns values; stream bounds are QPC ticks with their own frequency.
    They are deliberately not joined to MD/UI/envelope clocks.
    """
    need(type(raw)is bytes and len(raw)<=MAX_BYTES,"bounded binary audio stream required")
    need(type(synthetic)is bool and type(duration)is int and 1<=duration<=30,"capture limits")
    need(type(native_source_sha256)is str and re.fullmatch(r"[0-9a-f]{64}",native_source_sha256),
         "qualified native source digest required")
    ms,freq,began=_header(raw,peer=peer,duration=duration,native_source_sha256=native_source_sha256,synthetic=synthetic)
    rows=[];pcm=bytearray();offset=120;frames=0;end=None
    while offset<len(raw):
        kind=raw[offset:offset+1];offset+=1
        if kind==b"E":
            need(offset+20==len(raw),"terminal length/trailing data")
            n,total,ended=struct.unpack_from("<IQQ",raw,offset)
            need(n==len(rows) and total==frames and ended>=began,"terminal denominator/clock differs")
            # Bounds apply only to the helper's requested observation window.
            need(ms/1000<=(ended-began)/freq<=ms/1000+2,"native capture window outside bound")
            end=ended;break
        need(kind==b"P" and len(rows)<MAX_PACKETS and offset+24<=len(raw),"packet tag/count/header")
        n,flags,pos,qpc=struct.unpack_from("<IIQQ",raw,offset);offset+=24
        need(0<n<=RATE*2 and flags&~7==0 and frames+n<=RATE*(duration+2),"packet bounds/flags")
        size=n*4;need(offset+size<=len(raw),"truncated PCM packet")
        data=raw[offset:offset+size];offset+=size
        need(not(flags&2) or not any(data),"silent packet contains nonzero data")
        rows.append({"index":len(rows),"frames":n,"flags":flags,"silent":bool(flags&2),
            "data_discontinuity":bool(flags&1),"timestamp_error":bool(flags&4),
            "device_position":str(pos),"qpc_100ns":str(qpc),
            "native_timestamp_error_flag_absent":not bool(flags&4),"pcm_sha256":hashlib.sha256(data).hexdigest()})
        pcm.extend(data);frames+=n
    need(end is not None,"native terminal absent; partial stream is not settled evidence")
    values=array.array("h");values.frombytes(pcm)
    if sys.byteorder!="little": values.byteswap()
    nonzero=sum(v!=0 for v in values);peak=max((abs(v) for v in values),default=0)
    report={"status":"PCM_PACKETS_OBSERVED" if rows else "NO_PACKETS_OBSERVED",
        "scope":"TARGET_PROCESS_AND_DESCENDANTS","format":{"rate":RATE,"channels":2,"bits":16,"encoding":"PCM_SIGNED_LE"},
        "packet_count":len(rows),"frame_count":frames,"sample_count":len(values),"packets":rows,
        "native_window":{"qpc_frequency":str(freq),"qpc_begin":str(began),"qpc_end":str(end),
                         "requested_ms":ms,"closed":True},
        "packet_frames_seconds":frames/RATE,"nonzero_samples":nonzero,"absolute_peak":peak,
        "flagged_discontinuity_packets":sum(r["data_discontinuity"] for r in rows),
        "flagged_timestamp_error_packets":sum(r["timestamp_error"] for r in rows),
        "silent_flag_frames":sum(r["frames"] for r in rows if r["silent"]),
        "pcm_sha256":hashlib.sha256(pcm).hexdigest(),
        "continuous_audio_qualified":False,"audible_at_speakers_qualified":False,
        "named_sound_attribution_qualified":False,"no_packets_means_silence":False,
        "named_voice_or_audio_case_pass":False,"native_clock_alignment_qualified":False}
    return report,bytes(pcm)

class ProcessAudioRecorder:
    """Use connect_audio for production. Recording approval belongs to caller.

    No trusted callback/JSON label alone claims a named sound. Only an actual
    connected AutomationClient may produce a production local receipt.
    """
    def __init__(self,client,profile,journal,*,helper,helper_sha256,synthetic=False,runner=run_bounded):
        need(isinstance(client,AutomationClient) and isinstance(client.bound,BoundPipe)
             and isinstance(client.bound.link,SessionLink) and isinstance(client.bound.link.peer,ProfilePeer),
             "actual bound client/ProfilePeer required")
        need(type(synthetic)is bool and (synthetic or(client._native_connection is True and runner is run_bounded)),
             "production connection/backend required")
        need(callable(runner),"runner required")
        self.client=client;self.peer=client.bound.link.peer;self.synthetic=synthetic;self.runner=runner
        self.profile=_route(profile,directory=True);save=_route(self.profile/"save",directory=True)
        self.root=_route(journal,directory=True);self.binary=_route(helper)
        need(not self.root.is_relative_to(self.profile.parent) and not self.profile.parent.is_relative_to(self.root),
             "audio journal/personal tree overlap")
        self.binary_row=_file(self.binary,64*1024**2)
        need(type(helper_sha256)is str and re.fullmatch(r"[0-9a-f]{64}",helper_sha256)
             and self.binary_row["sha256"]==helper_sha256,"qualified helper hash differs")
        self.pins=source_pins();self.initial=copy.deepcopy(client.identity());self.build=client.bound.build
        need(self.profile.name==self.peer.profile_id and self.peer()==self.initial["peer"],"connected profile differs")
        collector=ReadEvidence(client,synthetic=synthetic)
        h=collector.collect("native_save_path");e=collector.receipt(h);v=_decode(e["capture"]["payload"])
        stamp={k:self.initial[k] for k in ("provider","revision","session","world_token")}|{"generation":self.initial["ui_generation"]}
        need(e["status"]=="OK" and type(v)is dict and set(v)=={"status","scope","stamp","save_folder"}
             and v["status"]=="OBSERVED" and v["scope"]=="NATIVE_SAVE_FOLDER_PATH" and v["stamp"]==stamp
             and type(v["save_folder"])is str and _route(v["save_folder"],directory=True)==save,
             "native profile path unavailable/different")
        need(client.identity()==self.initial and self.peer()==self.initial["peer"] and source_pins()==self.pins,
             "binding changed while observing path")
        self.records={};self.active=None;self.current=None;self.fault=None;self.retained=0
        self.lock=threading.Lock();st=self.root.stat();self.directory=(st.st_dev,st.st_ino)
        self.owner={"version":1,"identity":self.initial,"build":self.build,"profile":str(self.profile),
            "source_pins":self.pins,"helper":self.binary_row,"native_path":{"receipt":e,"transcript":collector.transcript(h)},
            "tier":"SYNTHETIC" if synthetic else "LOCAL_TRUSTED_PROCESS_TREE_CAPTURE",
            "policy":"NO_AUTOMATIC_REOPEN_OR_REPLAY"}
        _write_new(self.root/"audio-owner.json",self.owner)
    def _check(self,*,ping=False):
        need(self.fault is None,"audio recorder quarantined")
        _route(self.root,directory=True);st=self.root.stat()
        need((st.st_dev,st.st_ino)==self.directory,"audio directory replaced")
        need(_read_json(self.root/"audio-owner.json")==self.owner,"audio owner changed")
        need(source_pins()==self.pins and _file(self.binary,64*1024**2)==self.binary_row,"audio sources/binary changed")
        need(self.client.identity()==self.initial and self.peer.profile_id==self.profile.name
             and self.peer()==self.initial["peer"] and self.client.bound.build==self.build,"audio host binding changed")
        if ping:
            r=self.client.bound.ask("ping");envelope=copy.deepcopy(self.client.bound.last_envelope)
            need(r.status=="OK" and type(r.payload)is str and r.payload.startswith("pong\t"),"bound native identity unavailable")
            need(self.client.identity()==self.initial and self.peer()==self.initial["peer"],"peer drift after native read")
            if self.current is not None:
                row=self.current;raw=json.dumps(envelope,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
                need(len(raw)<=4096 and len(row["samples"])<2048 and row["sample_bytes"]+len(raw)<=2*1024**2,
                     "native identity sample capacity")
                row["samples"].append(envelope);row["sample_bytes"]+=len(raw)
    def prepare(self,label,*,duration=5):
        need(self.lock.acquire(False),"nested audio operation")
        try:
            self._check(ping=True)
            need(self.active is None and len(self.records)<16 and self.retained+MAX_BYTES<=256*1024**2,"audio capacity/active capture")
            need(type(label)is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",label),"bounded declared label required")
            need(type(duration)is int and 1<=duration<=30,"duration must be 1..30 integral seconds")
            row={"id":secrets.token_hex(16),"label":label,"duration":duration,"stage":"PREPARED","samples":[],"sample_bytes":0}
            prepared={"version":1,"id":row["id"],"label":label,"duration":duration,"identity":self.initial,
                      "build":self.build,"helper_sha256":self.binary_row["sha256"],"source_pins":self.pins}
            _write_new(self.root/(row["id"]+"-prepared.json"),prepared)
            row["prepared"]=prepared;h=object();self.records[h]=row;self.active=h;return h
        finally:self.lock.release()
    def execute(self,handle):
        need(self.lock.acquire(False),"nested audio operation")
        try:
            self._check()
            need(type(handle)is object and handle in self.records and self.active is handle,"own active audio ticket required")
            row=self.records[handle];need(row["stage"]=="PREPARED","audio ticket consumed")
            need(_read_json(self.root/(row["id"]+"-prepared.json"))==row["prepared"],"audio preparation changed")
            self.current=row
            self._check(ping=True)
            row["stage"]="CONSUMED";self.retained+=MAX_BYTES
            try:
                _write_new(self.root/(row["id"]+"-consumed.json"),{"id":row["id"],"identity":self.initial})
                path=self.root/(row["id"]+".x4pcm");peer=self.initial["peer"]
                argv=[str(self.binary),"--capture-pcm",str(peer["pid"]),peer["creation_filetime"],peer["image"],str(row["duration"]*1000)]
                # Immediate pre-spawn identity gate; child independently checks
                # PID + creation FILETIME + executable before and during capture.
                self._check(ping=True)
                with path.open("xb") as output:
                    self.runner(argv,output=output,cap=MAX_BYTES,timeout=row["duration"]+15,
                                check=lambda:self._check(ping=True))
                    output.flush();os.fsync(output.fileno())
                self._settle(row,path);self.active=None;return handle
            except Exception as exc:
                row["stage"]="QUARANTINED";self.fault=str(exc);raise
        finally:self.current=None;self.lock.release()
    def _settle(self,row,path):
        self._check(ping=True)
        file=_file(path,MAX_BYTES)
        with path.open("rb") as stream:raw=stream.read(MAX_BYTES+1)
        need(len(raw)<=MAX_BYTES,"audio stream grew beyond read cap")
        need(_file(path,MAX_BYTES)==file,"audio stream changed while decoding")
        if "started" in row:
            need(hashlib.sha256(raw[:120]).hexdigest()==row["started"]["header_sha256"],
                 "settled header differs from started observation")
        evidence,pcm=parse_packets(raw,peer=self.initial["peer"],duration=row["duration"],
            native_source_sha256=self.pins["process_loopback.cpp"],synthetic=self.synthetic)
        wav=self.root/(row["id"]+".wav")
        with wav.open("xb") as stream:
            with wave.open(stream,"wb") as writer:
                writer.setparams((2,2,RATE,0,"NONE","not compressed"));writer.writeframes(pcm)
            stream.flush();os.fsync(stream.fileno())
        self._check(ping=True)
        result={"version":1,"status":"AUDIO_ARTIFACT_CAPTURED_REVIEW_REQUIRED",
            "tier":"SYNTHETIC" if self.synthetic else "LOCAL_TRUSTED_PROCESS_TREE_CAPTURE",
            "identity":self.initial,"build":self.build,"profile_id":self.profile.name,
            "label":row["label"],"label_attribution":"CALLER_DECLARED_NOT_SOUND_PROOF",
            "scope":"TARGET_PROCESS_AND_DESCENDANTS","source_pins":self.pins,"helper":self.binary_row,
            "raw_path":str(path),"raw_file":file,"wav_path":str(wav),"wav_file":_file(wav,MAX_BYTES),
            "evidence":evidence,"native_identity_samples":row["samples"],"sample_count":len(row["samples"]),
            "sampling":"SAMPLED_NOT_CONTINUOUS","world_lineage":"UNKNOWN","runtime_case_pass":False,
            "historical":True,"fresh_current_native_identity":False,"cryptographic_attestation":False}
        if "started" in row:result["started_observation"]=copy.deepcopy(row["started"])
        receipt=self.root/(row["id"]+"-receipt.json");_write_new(receipt,result)
        row.update(stage="CAPTURED",result=copy.deepcopy(result),receipt_path=receipt,receipt_file=_file(receipt,8*1024**2))
    def _async_row(self,handle):
        need(type(handle)is object and handle in self.records and self.active is handle,"own active audio ticket required")
        row=self.records[handle]
        need(row["stage"]=="RECORDING_STARTED" and "work" in row,"started asynchronous capture required")
        return row,row["work"]
    def _quarantine(self,row,error):
        row["stage"]="QUARANTINED";self.fault=str(error)
        if "work" in row:
            row["work"]["cancel"].set()
            row["work"]["done"].wait(2)
    def begin(self,handle,*,minimum_remaining=1.0):
        """Start one owned recording; return only after a verified native start header.

        Worker never calls client, BoundPipe or ProfilePeer. Caller prepares any
        independently authorized playback only after this method returns.
        """
        need(self.lock.acquire(False),"nested audio operation")
        row=None
        try:
            self._check()
            need(type(handle)is object and handle in self.records and self.active is handle,"own active audio ticket required")
            row=self.records[handle];need(row["stage"]=="PREPARED","audio ticket consumed")
            need(type(minimum_remaining)in (int,float) and 0.25<=minimum_remaining<row["duration"],"bounded remaining window required")
            need(_read_json(self.root/(row["id"]+"-prepared.json"))==row["prepared"],"audio preparation changed")
            self.current=row;self._check(ping=True)
            row["stage"]="CONSUMED";self.retained+=MAX_BYTES
            try:
                _write_new(self.root/(row["id"]+"-consumed.json"),{"id":row["id"],"identity":self.initial})
                path=self.root/(row["id"]+".x4pcm");peer=self.initial["peer"]
                argv=[str(self.binary),"--capture-pcm",str(peer["pid"]),peer["creation_filetime"],peer["image"],str(row["duration"]*1000)]
                self._check(ping=True)
                work={"lock":threading.Lock(),"ready":threading.Event(),"done":threading.Event(),
                      "cancel":threading.Event(),"header":bytearray(),"error":None,"path":path,"spawned":time.monotonic()}
                row["work"]=work;runner=self.runner;duration=row["duration"]
                output=path.open("xb",buffering=0)
                def worker():
                    try:
                        with output:
                            runner(argv,output=_StartedOutput(output,work),cap=MAX_BYTES,timeout=duration+15,
                                   check=lambda:need(not work["cancel"].is_set(),"owned capture cancelled"))
                            output.flush();os.fsync(output.fileno())
                    except Exception as exc:work["error"]=exc
                    finally:work["done"].set();work["ready"].set()
                thread=threading.Thread(target=worker,daemon=True);work["thread"]=thread
                try:thread.start()
                except Exception:
                    output.close();work["done"].set();raise
                while not work["ready"].wait(0.05):
                    self._check(ping=True)
                    need(time.monotonic()-work["spawned"]<=15,"audio start header deadline")
                need(work["error"] is None,"audio worker failed before readiness: "+str(work["error"]))
                with work["lock"]:header=bytes(work["header"])
                ms,freq,began=_header(header,peer=peer,duration=duration,
                    native_source_sha256=self.pins["process_loopback.cpp"],synthetic=self.synthetic)
                self._check(ping=True)
                now=time.monotonic();remaining=work["spawned"]+duration-now
                need(now-work["spawned"]<=15 and remaining>=minimum_remaining,"late header/insufficient conservative window")
                need(not work["done"].is_set(),"audio worker terminal before begin returned")
                started={"version":1,"id":row["id"],"identity":self.initial,"header_sha256":hashlib.sha256(header).hexdigest(),
                    "native_window":{"requested_ms":ms,"qpc_frequency":str(freq),"qpc_begin":str(began)},
                    "host_observed_monotonic":now,"conservative_remaining_at_observation":remaining,
                    "remaining_scope":"DURATION_MINUS_HOST_START_ELAPSED_NOT_FUTURE_RESERVATION",
                    "header_scope":"NATIVE_START_RETURNED_NOT_PCM_OR_NAMED_SOUND_PROOF"}
                _write_new(self.root/(row["id"]+"-started.json"),started)
                row.update(stage="RECORDING_STARTED",started=started)
                # File publication can take time; do not return expired readiness.
                self._check(ping=True)
                need(not work["done"].is_set() and work["spawned"]+duration-time.monotonic()>=minimum_remaining,
                     "recording ended/window expired while publishing readiness")
                return copy.deepcopy(started)
            except Exception as exc:self._quarantine(row,exc);raise
        finally:self.current=None;self.lock.release()
    def status(self,handle):
        """Sample owned capture state; no start, playback or finalization occurs."""
        need(self.lock.acquire(False),"nested audio operation")
        try:
            row,work=self._async_row(handle);self.current=row
            try:
                self._check(ping=True)
                need(_read_json(self.root/(row["id"]+"-started.json"))==row["started"],"audio start journal changed")
                need(work["error"] is None,"audio worker failed: "+str(work["error"]))
                need(work["done"].is_set() or time.monotonic()-work["spawned"]<=row["duration"]+17,"audio worker deadline")
                return {"id":row["id"],"status":"AWAITING_FINISH" if work["done"].is_set() else "RECORDING",
                    "started_observation_historical":True,"settled":False,"named_sound_verified":False,
                    "conservative_remaining_seconds":max(0,work["spawned"]+row["duration"]-time.monotonic())}
            except Exception as exc:self._quarantine(row,exc);raise
        finally:self.current=None;self.lock.release()
    def finish(self,handle):
        """Wait for the one existing child and validate its full terminal/artifacts."""
        need(self.lock.acquire(False),"nested audio operation")
        try:
            row,work=self._async_row(handle);self.current=row
            try:
                self._check(ping=True)
                need(_read_json(self.root/(row["id"]+"-started.json"))==row["started"],"audio start journal changed")
                while not work["done"].wait(0.1):
                    self._check(ping=True)
                    need(time.monotonic()-work["spawned"]<=row["duration"]+17,"audio worker deadline")
                need(work["error"] is None,"audio worker failed: "+str(work["error"]))
                self._settle(row,work["path"]);self.active=None;return handle
            except Exception as exc:self._quarantine(row,exc);raise
        finally:self.current=None;self.lock.release()
    def cancel(self,handle):
        """Cancel only this recording. This never stops independent game playback."""
        need(self.lock.acquire(False),"nested audio operation")
        try:
            row,work=self._async_row(handle)
            self._quarantine(row,"owned recording cancelled; playback stop not performed")
            return {"id":row["id"],"status":"QUARANTINED","worker_finished":work["done"].is_set(),
                    "settled":False,"playback_stopped":False}
        finally:self.lock.release()
    def receipt(self,handle):
        need(self.lock.acquire(False),"nested audio operation")
        try:
            self._check()
            need(type(handle)is object and handle in self.records and self.records[handle]["stage"]=="CAPTURED",
                 "own settled audio handle required")
            row=self.records[handle];v=row["result"]
            need(_file(row["receipt_path"],8*1024**2)==row["receipt_file"]
                 and _file(Path(v["raw_path"]),MAX_BYTES)==v["raw_file"]
                 and _file(Path(v["wav_path"]),MAX_BYTES)==v["wav_file"],"audio artifacts changed")
            return copy.deepcopy(v)
        finally:self.lock.release()
    def export(self,handle):
        value=self.receipt(handle);value["recorded_tier"]=value["tier"];value["tier"]="EXPORTED_UNAUTHENTICATED";return value

def connect_audio(client,profile,journal,*,helper,helper_sha256):
    """Already-connected production client; separately approved recording only."""
    return ProcessAudioRecorder(client,profile,journal,helper=helper,helper_sha256=helper_sha256)

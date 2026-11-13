"""Actual bound router/collector; synthetic packets, never system audio."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import wave
import pytest
from test_liveclipport import setup
from x4validate import _liveaudio as A
from x4validate._livesession import SessionUnavailable

def stream(peer,source,*,duration=1,flags=0,data=b"\0\0\1\0",fixture=1,empty=False):
    raw=b"X4AUD001"+source.encode()+struct.pack("<IIQIIIIQQ",fixture,peer["pid"],int(peer["creation_filetime"]),
        44100,2,16,duration*1000,10000000,100)
    if not empty:raw+=b"P"+struct.pack("<IIQQ",len(data)//4,flags,0,100)+data
    return raw+b"E"+struct.pack("<IQQ",0 if empty else 1,0 if empty else len(data)//4,100+duration*10000000)

def build(setup):
    r,pipe,client,profile,roots,old,calls=setup
    source=A.source_pins()["process_loopback.cpp"];peer=client.identity()["peer"]
    def runner(argv,**kw):
        calls.append(argv);kw["check"]()
        kw["output"].write(stream(peer,source,duration=int(argv[-1])//1000))
        return b""
    kw={"helper":old["ffmpeg"],"helper_sha256":old["ffmpeg_sha256"],"synthetic":True,"runner":runner}
    return A.ProcessAudioRecorder(client,profile,roots["port"],**kw)

def test_actual_bound_route_synthetic_pcm(setup):
    a=build(setup);h=a.prepare("audio.loop",duration=1)
    assert not setup[-1]
    a.execute(h);r=a.receipt(h)
    assert r["tier"]=="SYNTHETIC" and r["evidence"]["frame_count"]==1
    assert r["evidence"]["nonzero_samples"]==1 and r["evidence"]["absolute_peak"]==1
    assert r["scope"]=="TARGET_PROCESS_AND_DESCENDANTS" and not r["runtime_case_pass"]
    assert r["sample_count"]>=4
    assert all(v["stamp"]["session"]==r["identity"]["session"] for v in r["native_identity_samples"])
    with wave.open(r["wav_path"]) as w:
        assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(2,2,44100,1)
        assert w.readframes(1)==b"\0\0\1\0"
    assert len(setup[-1])==1 and setup[-1][0][1]=="--capture-pcm"
    assert set(v[1][5] for v in setup[1].calls)=={"read_capture","read_page","ping"}
    exported=a.export(h);assert exported["tier"]=="EXPORTED_UNAUTHENTICATED"
    exported["evidence"]["frame_count"]=999
    assert a.receipt(h)["evidence"]["frame_count"]==1
    with pytest.raises(A.AudioUnavailable):a.execute(h)
    with pytest.raises(A.AudioUnavailable):a.receipt(r)

@pytest.mark.parametrize("empty,flags,data",[(True,0,b""),(False,2,b"\0"*4),(False,7,b"\0"*4),(False,1,b"\1\0\0\0")])
def test_empty_silent_and_discontinuity_explicit(setup,empty,flags,data):
    peer=setup[2].identity()["peer"];source=A.source_pins()["process_loopback.cpp"]
    r,pcm=A.parse_packets(stream(peer,source,empty=empty,flags=flags,data=data),
        peer=peer,duration=1,native_source_sha256=source,synthetic=True)
    assert r["packet_count"]==(0 if empty else 1)
    assert not r["continuous_audio_qualified"] and not r["no_packets_means_silence"]
    assert r["status"]==("NO_PACKETS_OBSERVED" if empty else "PCM_PACKETS_OBSERVED")
    assert not r["named_voice_or_audio_case_pass"]
    if not empty:
        assert r["flagged_discontinuity_packets"]==int(bool(flags&1))
        assert r["flagged_timestamp_error_packets"]==int(bool(flags&4))
        assert r["packets"][0]["native_timestamp_error_flag_absent"] is (not bool(flags&4))
        assert "timestamp_qualified" not in r["packets"][0]
        assert r["native_clock_alignment_qualified"] is False

@pytest.mark.parametrize("kind",["magic","source","tier","pid","creation","rate","channels","bits","duration","frequency",
    "no_end","trailing","count","frames","end_before","short_window","long_window","tag","unknown_flag","silent_nonzero",
    "truncated_pcm","frame_capacity"])
def test_packet_refusals(setup,kind):
    peer=setup[2].identity()["peer"];source=A.source_pins()["process_loopback.cpp"]
    b=bytearray(stream(peer,source))
    if kind=="magic":b[0]=1
    elif kind=="source":b[8]=ord("a") if b[8]!=ord("a") else ord("b")
    elif kind=="tier":struct.pack_into("<I",b,72,0)
    elif kind=="pid":struct.pack_into("<I",b,76,999)
    elif kind=="creation":struct.pack_into("<Q",b,80,999)
    elif kind in ("rate","channels","bits","duration"):
        struct.pack_into("<I",b,{"rate":88,"channels":92,"bits":96,"duration":100}[kind],999)
    elif kind=="frequency":struct.pack_into("<Q",b,104,0)
    elif kind=="no_end":b=b[:-21]
    elif kind=="trailing":b+=b"x"
    elif kind=="count":struct.pack_into("<I",b,len(b)-20,2)
    elif kind=="frames":struct.pack_into("<Q",b,len(b)-16,2)
    elif kind=="end_before":struct.pack_into("<Q",b,len(b)-8,99)
    elif kind=="short_window":struct.pack_into("<Q",b,len(b)-8,1000)
    elif kind=="long_window":struct.pack_into("<Q",b,len(b)-8,999999999)
    elif kind=="tag":b[120]=ord("Z")
    elif kind=="unknown_flag":struct.pack_into("<I",b,125,8)
    elif kind=="silent_nonzero":struct.pack_into("<I",b,125,2)
    elif kind=="truncated_pcm":del b[145]
    else:struct.pack_into("<I",b,121,44100*3)
    with pytest.raises(A.AudioUnavailable):
        A.parse_packets(bytes(b),peer=peer,duration=1,native_source_sha256=source,synthetic=True)

@pytest.mark.parametrize("kind",["load","ui","peer","profile","build","source","binary","owner","prepared"])
def test_drift_before_child_refuses(setup,kind,monkeypatch):
    a=build(setup);h=a.prepare("case",duration=1)
    if kind=="load":setup[0].execute("stamp.session=string.rep('e',32)")
    elif kind=="ui":setup[0].execute("stamp.generation=string.rep('e',32)")
    elif kind=="peer":setup[2].bound.link.value["peer"]["pid"]=999
    elif kind=="profile":setup[2].bound.link.peer.profile_id="999"
    elif kind=="build":setup[2].bound.build="eeeeeeee"
    elif kind=="source":monkeypatch.setattr(A,"source_pins",lambda:{"changed":"a"*64})
    elif kind=="binary":a.binary.write_bytes(b"changed")
    elif kind=="owner":(a.root/"audio-owner.json").write_text("{}")
    else:(a.root/(a.records[h]["id"]+"-prepared.json")).write_text("{}")
    with pytest.raises((A.AudioUnavailable,SessionUnavailable,ValueError)):a.execute(h)
    assert not setup[-1]

def test_mid_capture_drift_quarantines_partial_and_never_replays(setup):
    a=build(setup);h=a.prepare("case",duration=1)
    def runner(argv,**kw):
        setup[-1].append(argv);kw["output"].write(b"partial")
        setup[0].execute("stamp.session=string.rep('e',32)");kw["check"]()
    a.runner=runner
    with pytest.raises((A.AudioUnavailable,SessionUnavailable)):a.execute(h)
    assert a.fault and a.records[h]["stage"]=="QUARANTINED"
    assert (a.root/(a.records[h]["id"]+".x4pcm")).read_bytes()==b"partial"
    with pytest.raises(A.AudioUnavailable):a.execute(h)
    with pytest.raises(A.AudioUnavailable):a.prepare("again")
    assert len(setup[-1])==1

@pytest.mark.parametrize("kind",["raw","wav","receipt"])
def test_artifact_mutation_refuses(setup,kind):
    a=build(setup);h=a.prepare("case",duration=1);a.execute(h);row=a.records[h]
    path={"raw":Path(row["result"]["raw_path"]),"wav":Path(row["result"]["wav_path"]),
          "receipt":row["receipt_path"]}[kind];path.write_bytes(b"changed")
    with pytest.raises((A.AudioUnavailable,ValueError)):a.receipt(h)

@pytest.mark.parametrize("duration",[0,31,1.5,True])
def test_duration_refuses(setup,duration):
    a=build(setup)
    with pytest.raises(A.AudioUnavailable):a.prepare("case",duration=duration)
    assert not setup[-1]

def test_native_generation_retrieval_historical_only(setup):
    a=build(setup);h=a.prepare("case",duration=1);a.execute(h)
    setup[0].execute("stamp.session=string.rep('e',32)")
    assert a.receipt(h)["fresh_current_native_identity"] is False

def test_constructor_no_production_tier_from_synthetic_client(setup):
    old=setup[5]
    with pytest.raises(A.AudioUnavailable):
        A.connect_audio(setup[2],setup[3],setup[4]["port"],helper=old["ffmpeg"],helper_sha256=old["ffmpeg_sha256"])
    assert not (setup[4]["port"]/"audio-owner.json").exists()

def test_profile_journal_refuses_before_write(setup):
    old=setup[5]
    with pytest.raises(A.AudioUnavailable):
        A.ProcessAudioRecorder(setup[2],setup[3],setup[3]/"save",helper=old["ffmpeg"],
                              helper_sha256=old["ffmpeg_sha256"],synthetic=True)
    assert not (setup[3]/"save"/"audio-owner.json").exists()

def test_nested_execution_refuses(setup):
    a=build(setup);h=a.prepare("case",duration=1);old=a.runner
    def run(*args,**kw):
        with pytest.raises(A.AudioUnavailable):a.execute(h)
        with pytest.raises(A.AudioUnavailable):a.prepare("nested")
        return old(*args,**kw)
    a.runner=run;a.execute(h);assert len(setup[-1])==1

def test_actual_compiled_helper_synthetic_writer_only():
    path=os.environ.get("X4_TEST_AUDIO_HELPER")
    if not path:pytest.skip("explicit compiled helper required; no audio capture is invoked")
    expected=os.environ.get("X4_TEST_AUDIO_HELPER_SHA256")
    exe=Path(path);assert hashlib.sha256(exe.read_bytes()).hexdigest()==expected
    # These modes do not call COM, process enumeration, activation, or audio APIs.
    flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    r=subprocess.run([str(exe),"--synthetic-fixture"],capture_output=True,timeout=5,creationflags=flags)
    assert r.returncode==0 and not r.stderr
    source=A.source_pins()["process_loopback.cpp"]
    report,pcm=A.parse_packets(r.stdout,peer={"pid":123,"creation_filetime":"456"},duration=1,
                              native_source_sha256=source,synthetic=True)
    assert report["packet_count"]==2 and report["frame_count"]==5
    assert report["silent_flag_frames"]==3 and report["flagged_discontinuity_packets"]==1
    assert report["absolute_peak"]==32768 and len(pcm)==20
    with pytest.raises(A.AudioUnavailable):
        A.parse_packets(r.stdout,peer={"pid":123,"creation_filetime":"456"},duration=1,
                        native_source_sha256=source,synthetic=False)
    assert hashlib.sha256(exe.read_bytes()).hexdigest()==expected

def test_raw_growth_between_hash_and_read_is_bounded(setup,monkeypatch):
    monkeypatch.setattr(A,"MAX_BYTES",1024)
    a=build(setup);h=a.prepare("case",duration=1);original=A._file;grew=[]
    def grow(path,limit):
        result=original(path,limit)
        if str(path).endswith(".x4pcm") and not grew:
            grew.append(True)
            with Path(path).open("ab") as f:f.write(b"x"*2048)
        return result
    monkeypatch.setattr(A,"_file",grow)
    with pytest.raises(A.AudioUnavailable,match="grew beyond read cap"):a.execute(h)
    assert a.records[h]["stage"]=="QUARANTINED" and len(setup[-1])==1


@pytest.fixture
def asynchronous(setup):
    """Only byte/file plumbing runs in the worker; actual client stays on main."""
    a=build(setup);release=threading.Event();main=threading.get_ident()
    peer=copy.deepcopy(a.initial["peer"]);source=a.pins["process_loopback.cpp"]
    original=a._check;control={"mutate":lambda raw:raw,"terminal":False,"error":False}
    def check(**kw):
        assert threading.get_ident()==main
        return original(**kw)
    a._check=check
    def runner(argv,**kw):
        assert threading.get_ident()!=main
        setup[-1].append(argv)
        raw=control["mutate"](stream(peer,source,duration=int(argv[-1])//1000))
        kw["output"].write(raw[:57]);kw["output"].write(raw[57:120])
        if control["terminal"]:return b""
        while not release.wait(.01):kw["check"]()
        kw["check"]()
        if control["error"]:raise RuntimeError("synthetic worker failure")
        kw["output"].write(raw[120:]);return b""
    a.runner=runner
    try:yield a,release,control
    finally:
        release.set()
        for row in a.records.values():
            if "work" in row:
                row["work"]["cancel"].set()
                assert row["work"]["done"].wait(10)


def test_started_recording_allows_actual_same_client_action_then_settles(asynchronous,setup):
    from test_livebound import REQUEST
    a,release,_=asynchronous;h=a.prepare("fixed.named.window",duration=5)
    started=a.begin(h)
    assert a.records[h]["stage"]=="RECORDING_STARTED" and len(setup[-1])==1
    assert started["conservative_remaining_at_observation"]>=1
    assert a.status(h)["status"]=="RECORDING"
    client=setup[2]
    assert client.prepare(copy.deepcopy(REQUEST))["status"]=="prepared"
    assert client.execute(REQUEST["id"])["status"]=="running"
    assert client.status(REQUEST["id"])["status"]=="completed"
    assert list(setup[0].globals().calls.values())==["prepare","execute","status"]
    with pytest.raises(A.AudioUnavailable):a.receipt(h)
    release.set();assert a.records[h]["work"]["done"].wait(10)
    assert a.status(h)["status"]=="AWAITING_FINISH"
    assert a.finish(h) is h
    receipt=a.receipt(h)
    assert receipt["started_observation"]==started
    assert receipt["evidence"]["frame_count"]==1 and not receipt["runtime_case_pass"]
    for method in (a.begin,a.execute,a.finish):
        with pytest.raises(A.AudioUnavailable):method(h)
    assert len(setup[-1])==1 and a.retained==A.MAX_BYTES


@pytest.mark.parametrize("kind",["source","pid","tier","duration","frequency","short","terminal"])
def test_started_header_refusals_consume_and_quarantine(asynchronous,setup,kind):
    a,_,control=asynchronous
    def mutation(raw):
        b=bytearray(raw)
        if kind=="source":b[8]=ord("z")
        elif kind=="pid":struct.pack_into("<I",b,76,999)
        elif kind=="tier":struct.pack_into("<I",b,72,0)
        elif kind=="duration":struct.pack_into("<I",b,100,1000)
        elif kind=="frequency":struct.pack_into("<Q",b,104,0)
        elif kind=="short":return bytes(b[:119])
        return bytes(b)
    control["mutate"]=mutation;control["terminal"]=kind in ("short","terminal")
    h=a.prepare("case",duration=5)
    with pytest.raises(A.AudioUnavailable):a.begin(h)
    row=a.records[h]
    assert row["stage"]=="QUARANTINED" and a.retained==A.MAX_BYTES
    assert (a.root/(row["id"]+"-consumed.json")).exists()
    with pytest.raises(A.AudioUnavailable):a.begin(h)
    assert len(setup[-1])==1


@pytest.mark.parametrize("when",["header","publication"])
def test_expired_start_window_never_returns_ready(asynchronous,monkeypatch,when):
    a,_,_=asynchronous;h=a.prepare("case",duration=5)
    if when=="header":
        check=a._check
        def late(**kw):
            result=check(**kw);work=a.records[h].get("work")
            if work and work["ready"].is_set():work["spawned"]-=10
            return result
        a._check=late
    else:
        write=A._write_new
        def slow(path,value):
            write(path,value)
            if str(path).endswith("-started.json"):a.records[h]["work"]["spawned"]-=10
        monkeypatch.setattr(A,"_write_new",slow)
    with pytest.raises(A.AudioUnavailable,match="window|header"):a.begin(h)
    assert a.records[h]["stage"]=="QUARANTINED"


@pytest.mark.parametrize("kind",["journal","generation","worker","header","terminal"])
def test_async_settlement_drift_and_partial_refuse(asynchronous,setup,kind):
    a,release,control=asynchronous;h=a.prepare("case",duration=5);a.begin(h);row=a.records[h]
    if kind=="journal":(a.root/(row["id"]+"-started.json")).write_text("{}")
    elif kind=="generation":setup[0].execute("stamp.generation=string.rep('e',32)")
    elif kind=="worker":control["error"]=True
    release.set();assert row["work"]["done"].wait(10)
    if kind in ("header","terminal"):
        raw=bytearray(row["work"]["path"].read_bytes())
        if kind=="header":struct.pack_into("<Q",raw,112,101)
        else:raw=raw[:-21]
        row["work"]["path"].write_bytes(raw)
    with pytest.raises((A.AudioUnavailable,SessionUnavailable)):a.finish(h)
    assert row["stage"]=="QUARANTINED" and len(setup[-1])==1
    assert not (a.root/(row["id"]+"-receipt.json")).exists()


def test_async_cancel_only_owned_child_no_playback_stop(asynchronous,setup):
    a,_,_=asynchronous;h=a.prepare("case",duration=5);a.begin(h)
    with pytest.raises(A.AudioUnavailable):a.cancel(object())
    result=a.cancel(h)
    assert result["worker_finished"] and not result["playback_stopped"] and not result["settled"]
    assert len(setup[-1])==1 and len(setup[0].globals().calls)==0
    with pytest.raises(A.AudioUnavailable):a.begin(h)


@pytest.mark.parametrize("remaining",[True,0,float("nan"),float("inf"),5,6])
def test_async_remaining_bound_refuses_before_consumption(asynchronous,setup,remaining):
    a,_,_=asynchronous;h=a.prepare("case",duration=5)
    with pytest.raises(A.AudioUnavailable):a.begin(h,minimum_remaining=remaining)
    assert not setup[-1] and a.records[h]["stage"]=="PREPARED" and a.retained==0


def test_async_terminal_waiting_is_not_current_recording(asynchronous):
    a,release,_=asynchronous;h=a.prepare("case",duration=5);a.begin(h)
    release.set();work=a.records[h]["work"];assert work["done"].wait(10)
    work["spawned"]-=100
    result=a.status(h)
    assert result["status"]=="AWAITING_FINISH" and result["conservative_remaining_seconds"]==0
    assert not result["settled"] and result["started_observation_historical"]
    a.finish(h)


def test_actual_bounded_subprocess_drain_started_sink(setup):
    """Real pipes/file sink; child only emits synthetic bytes, no audio APIs."""
    a=build(setup);peer=a.initial["peer"];source=a.pins["process_loopback.cpp"]
    header=stream(peer,source,duration=5)[:120]
    code=("import sys,struct,time; o=sys.stdout.buffer; "
          "o.write(bytes.fromhex("+repr(header.hex())+")); "
          "o.write(b'P'+struct.pack('<IIQQ',16384,0,0,100)+b'\\0'*65536); o.flush(); "
          "time.sleep(0.7); o.write(b'E'+struct.pack('<IQQ',1,16384,50000100)); o.flush()")
    def runner(argv,**kw):
        setup[-1].append(argv)
        return A.run_bounded([sys.executable,"-B","-c",code],**kw)
    a.runner=runner;h=a.prepare("pipe.plumbing",duration=5)
    try:
        a.begin(h);a.finish(h)
        assert a.receipt(h)["evidence"]["frame_count"]==16384
        assert len(setup[-1])==1
    finally:
        row=a.records[h]
        if "work" in row:
            row["work"]["cancel"].set();assert row["work"]["done"].wait(10)


# Review r1-9: source_pins read tools/native_audio/process_loopback.cpp from the repo
# layout; outside a toolkit checkout (a wheel install) that raised FileNotFoundError.
def test_source_pins_refuse_plainly_without_the_native_source(tmp_path, monkeypatch):
    from x4validate import _liveaudio
    monkeypatch.setattr(_liveaudio, "NATIVE_SOURCE", tmp_path / "absent" / "process_loopback.cpp")
    with pytest.raises(_liveaudio.AudioUnavailable, match="process_loopback.cpp"):
        _liveaudio.source_pins()


def test_source_pins_include_the_native_source_in_a_checkout():
    # Twin: in this checkout the source is found and pinned.
    from x4validate import _liveaudio
    assert len(_liveaudio.source_pins()["process_loopback.cpp"]) == 64

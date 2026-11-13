"""Synthetic host clip controls. Never opens a window or records the desktop."""
import copy
import hashlib
import json
import sys
from pathlib import Path
import pytest
from x4validate import _liveclip as m

def framehash(count=51,step=1,tail=None):
    rows=["#format: frame checksums","#version: 2","#hash: SHA256","#tb 0: 1/10","#dimensions 0: 100x80"]
    for i in range(count):
        rows.append(f"0, {i*step}, {i*step}, 1, 24000, "+format(i%3,"064x"))
    if tail:rows.append(tail)
    return ("\n".join(rows)+"\n").encode()

@pytest.fixture
def setup(tmp_path):
    binary=tmp_path/"encoder.exe";binary.write_bytes(b"synthetic encoder")
    journal=tmp_path/"evidence";journal.mkdir()
    peer={"pid":123,"creation_filetime":"456","image":"C:/Synthetic/X4.exe"}
    identity={"provider":"x4-live-session-v1","revision":"a"*64,"session":"b"*32,
              "load_generation":1,"ui_generation":"c"*32,"peer":peer,"world_token":"world","world_lineage":"UNKNOWN"}
    profile={"peer":peer,"profile_id":"1234"}
    window={"hwnd":"1234567890123456789","pid":123,"width":100,"height":80}
    calls=[]
    def getwindow(p,expected=None):
        return copy.deepcopy(window)
    def runner(argv,**kw):
        calls.append(argv);kw["check"]()
        if kw.get("output") is not None:
            kw["output"].write(b"synthetic clip");return b""
        return framehash()
    recorder=m.WindowClipRecorder(journal,ffmpeg=binary,
        ffmpeg_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        identity=lambda:copy.deepcopy(identity),profile_peer=lambda:copy.deepcopy(profile),
        window=getwindow,runner=runner,synthetic=True)
    return recorder,identity,profile,window,calls,binary

def test_complete(setup):
    rec,identity,profile,window,calls,binary=setup
    result=rec.execute(rec.prepare("d"*32,duration=5,fps=10))
    assert result["status"]=="DECODED_HOST_CLIP" and result["tier"]=="SYNTHETIC"
    assert result["evidence"]["decoded_frames"]==51
    assert result["evidence"]["distinct_decoded_hashes"]==3
    assert not result["visual_case_pass"] and not result["evidence"]["motion_verified"]
    assert "hwnd="+window["hwnd"] in calls[0]
    assert "desktop" not in calls[0] and "title=" not in " ".join(calls[0])
    assert calls[0][-1]=="pipe:1" and "-fps_mode" in calls[0]
    assert Path(result["path"]).read_bytes()==b"synthetic clip"

@pytest.mark.parametrize("duration,fps",[(0,10),(31,10),(5,0),(5,31),(True,10),(5,True),(5.0,10)])
def test_bounds(setup,duration,fps):
    with pytest.raises(m.ClipUnavailable):
        setup[0].prepare("d"*32,duration=duration,fps=fps)

@pytest.mark.parametrize("hwnd",["0","-1","1;quit","18446744073709551616",True])
def test_bad_window(setup,hwnd):
    setup[3]["hwnd"]=hwnd
    with pytest.raises(m.ClipUnavailable):
        setup[0].prepare("d"*32,duration=5,fps=10)

@pytest.mark.parametrize("field",["session","ui_generation","peer","world_token"])
def test_identity_drift(setup,field):
    rec,identity,*_=setup;handle=rec.prepare("d"*32,duration=5,fps=10)
    identity[field]= {"pid":124,"creation_filetime":"456","image":"C:/Synthetic/X4.exe"} if field=="peer" else "e"*32
    with pytest.raises((m.ClipUnavailable,ValueError)):
        rec.execute(handle)
    assert setup[4]==[]

def test_profile_drift(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    setup[2]["profile_id"]="5678"
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert setup[4]==[]

@pytest.mark.parametrize("field,value",[("hwnd","777"),("width",101),("pid",321)])
def test_window_drift(setup,field,value):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    setup[3][field]=value
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert setup[4]==[]

def test_binary_drift(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    setup[5].write_bytes(b"changed")
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert setup[4]==[]

def test_prepared_tamper(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    (rec.journal/("d"*32+"-prepared.json")).write_text("{}")
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert setup[4]==[]

def test_no_duplicate_or_serialized_handles(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    with pytest.raises(m.ClipUnavailable):rec.prepare("e"*32,duration=5,fps=10)
    with pytest.raises(m.ClipUnavailable):rec.execute("d"*32)
    rec.execute(handle)
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert len(setup[4])==2
    with pytest.raises(FileExistsError):rec.prepare("d"*32,duration=5,fps=10)

def test_failed_child_stays_quarantined(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    def fail(*a,**kw):raise OSError("encoder failure")
    rec.runner=fail
    with pytest.raises(OSError):rec.execute(handle)
    with pytest.raises(m.ClipUnavailable):rec.prepare("e"*32)
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert rec.records[handle]["stage"]=="QUARANTINED"

def test_midflight_change(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    old=rec.runner
    def change(*a,**kw):
        setup[1]["session"]="e"*32
        return old(*a,**kw)
    rec.runner=change
    with pytest.raises(m.ClipUnavailable):rec.execute(handle)
    assert rec.records[handle]["stage"]=="QUARANTINED"

@pytest.mark.parametrize("raw",[
    b"",framehash(count=1),framehash(count=5),framehash(count=100),framehash(step=0),
    framehash().replace(b"1/10",b"0/10"),framehash().replace(b"100x80",b"99x80"),
    framehash().replace(b"#version: 2",b"#version: 1"),
    framehash().replace(b"0, 0, 0, 1,",b"1, 0, 0, 1,"),
    framehash().replace(b"0, 0, 0, 1,",b"0, -1, 0, 1,"),
    framehash().replace(b"24000",b"999999999"),
    framehash(tail="not a row"),framehash().replace(b"#tb 0: 1/10",b"#tb 0: 1/10\n#tb 0: 1/10")
])
def test_frame_refusals(raw):
    with pytest.raises(m.ClipUnavailable):m.frame_evidence(raw,100,80,5,10)

def test_pixel_changes_never_motion_pass():
    v=m.frame_evidence(framehash(),100,80,5,10)
    assert v["distinct_decoded_hashes"]==3 and v["motion_verified"] is False
    assert v["compositor_frame_timing_verified"] is False

def test_runner_actual_child_success():
    assert m.run_bounded([sys.executable,"-c","print('hello')"],cap=100,timeout=5).strip()==b"hello"

def test_runner_actual_child_nonzero():
    with pytest.raises(m.ClipUnavailable,match="child failed"):
        m.run_bounded([sys.executable,"-c","raise SystemExit(2)"],timeout=5)

def test_runner_actual_child_byte_cap():
    with pytest.raises(m.ClipUnavailable,match="byte limit"):
        m.run_bounded([sys.executable,"-c","print('x'*200000)"],cap=100,timeout=5)

def test_runner_actual_child_timeout():
    with pytest.raises(m.ClipUnavailable,match="timeout"):
        m.run_bounded([sys.executable,"-c","import time; time.sleep(3)"],timeout=.2)

def test_runner_actual_child_binding_refusal():
    def refuse():raise m.ClipUnavailable("drift")
    with pytest.raises(m.ClipUnavailable,match="drift"):
        m.run_bounded([sys.executable,"-c","import time; time.sleep(3)"],timeout=5,check=refuse)

def test_custom_backend_requires_synthetic(tmp_path):
    binary=tmp_path/"encoder";binary.write_bytes(b"x")
    with pytest.raises(m.ClipUnavailable,match="synthetic"):
        m.WindowClipRecorder(tmp_path,ffmpeg=binary,ffmpeg_sha256="a"*64,
          identity=lambda:None,profile_peer=lambda:None,window=lambda:None)

def test_no_constructor_reopen(setup):
    r=setup[0]
    with pytest.raises(FileExistsError):
        m.WindowClipRecorder(r.journal,ffmpeg=r.binary,ffmpeg_sha256=r.binary_row["sha256"],
          identity=r.identity,profile_peer=r.profile_peer,window=r.window,runner=r.runner,synthetic=True)

def test_actual_encoder_generated_pixels_only(setup,monkeypatch):
    import os
    binary=os.environ.get("X4_TEST_FFMPEG")
    if not binary:pytest.skip("explicit offline FFmpeg test binary not supplied")
    rec=setup[0]
    rec.binary=Path(binary)
    rec.binary_row=m._file(rec.binary,256*1024**2)
    def generated(argv,**kw):
        if kw.get("output") is not None:
            at=argv.index("-f")
            end=argv.index("-t")
            argv=argv[:at]+["-f","lavfi","-i","testsrc=size=100x80:rate=10:duration=5.1"]+argv[end:]
            # Bound fixture lasts5.1sec so first→last sample spans5sec.
            argv[argv.index("-t")+1]="5.1"
        return m.run_bounded(argv,**kw)
    rec.runner=generated
    value=rec.execute(rec.prepare("f"*32,duration=5,fps=10))
    assert value["tier"]=="SYNTHETIC"
    assert value["evidence"]["decoded_frames"]==51
    assert value["evidence"]["timestamp_span_seconds"]==5
    assert value["evidence"]["distinct_decoded_hashes"]>1
    assert value["visual_case_pass"] is False

def test_retained_budget(setup):
    setup[0].retained_bytes=2*1024**3
    with pytest.raises(m.ClipUnavailable,match="budget"):setup[0].prepare("d"*32)
    assert not setup[4]

def test_reentrant_dispatch_refuses(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    original=rec.runner
    def enter(*args,**kw):
        with pytest.raises(m.ClipUnavailable,match="already active"):
            rec.execute(handle)
        return original(*args,**kw)
    rec.runner=enter
    assert rec.execute(handle)["status"]=="DECODED_HOST_CLIP"
    assert len(setup[4])==2

def test_oversized_prepared_journal(setup):
    rec=setup[0];handle=rec.prepare("d"*32,duration=5,fps=10)
    (rec.journal/("d"*32+"-prepared.json")).write_bytes(b" "*65537)
    with pytest.raises(ValueError):rec.execute(handle)
    assert not setup[4]

@pytest.mark.skipif(sys.platform!="win32",reason="Win32 ctypes callback ABI control")
@pytest.mark.parametrize("change",["none","creation","image","exited","minimized","multiple","size","pid","hidden","enumeration",
                                   "minimized_allowed","zero_minimized_allowed","normal_metadata","zero_normal_metadata"])
def test_win32_binding_synthetic(monkeypatch,change):
    import ctypes
    from ctypes import wintypes as W
    class Fn:
        def __init__(self,fn):self.fn=fn
        def __call__(self,*args):return self.fn(*args)
    class DLL:pass
    k,u=DLL(),DLL();closed=[]
    k.OpenProcess=Fn(lambda *a:1)
    def times(handle,a,*unused):
        p=ctypes.cast(a,ctypes.POINTER(W.FILETIME))
        p.contents.dwLowDateTime=457 if change=="creation" else 456
        return 1
    k.GetProcessTimes=Fn(times)
    def image(handle,flags,buf,size):
        buf.value="C:/Other/X4.exe" if change=="image" else "C:/Synthetic/X4.exe"
        return 1
    k.QueryFullProcessImageNameW=Fn(image)
    def exitcode(handle,ptr):
        ctypes.cast(ptr,ctypes.POINTER(W.DWORD)).contents.value=0 if change=="exited" else 259
        return 1
    k.GetExitCodeProcess=Fn(exitcode);k.CloseHandle=Fn(lambda h:closed.append(h) or 1)
    def pid(hwnd,ptr):
        ctypes.cast(ptr,ctypes.POINTER(W.DWORD)).contents.value=124 if change=="pid" else 123
        return 1
    u.GetWindowThreadProcessId=Fn(pid)
    u.IsWindowVisible=Fn(lambda h:change!="hidden")
    u.IsIconic=Fn(lambda h:change in ("minimized","minimized_allowed","zero_minimized_allowed"))
    def rect(hwnd,ptr):
        p=ctypes.cast(ptr,ctypes.POINTER(W.RECT))
        p.contents.right=0 if change in ("size","zero_minimized_allowed","zero_normal_metadata") else 100;p.contents.bottom=80
        return 1
    u.GetClientRect=Fn(rect)
    def enum(fn,unused):
        fn(123,0)
        if change=="multiple":fn(456,0)
        return change!="enumeration"
    u.EnumWindows=Fn(enum)
    monkeypatch.setattr(ctypes,"WinDLL",lambda name,**kw:k if name=="kernel32" else u)
    peer={"pid":123,"creation_filetime":"456","image":"C:/Synthetic/X4.exe"}
    if change=="none":
        assert m.window_for_peer(peer)=={"hwnd":"123","pid":123,"width":100,"height":80}
    elif change in ("minimized_allowed","zero_minimized_allowed","normal_metadata"):
        result=m.window_for_peer(peer,allow_minimized=True)
        assert result=={"hwnd":"123","pid":123,"width":0 if change=="zero_minimized_allowed" else 100,"height":80,
                        "minimized":change!="normal_metadata","scope":"OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED"}
    elif change=="zero_normal_metadata":
        with pytest.raises(m.ClipUnavailable):m.window_for_peer(peer,allow_minimized=True)
    else:
        with pytest.raises(m.ClipUnavailable):m.window_for_peer(peer)
    assert closed==[1]

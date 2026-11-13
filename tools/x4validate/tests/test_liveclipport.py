"""Actual Lua router/BoundPipe/ReadEvidence/recorder; synthetic native/window/encoder."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from test_livebound import runtime,Pipe,STAMP
from test_liveevidence import Link,IDENTITY
from test_liveclip import framehash
from x4validate._liveautomation import AutomationClient
from x4validate._livebound import BoundPipe
from x4validate._liveclip import ClipUnavailable
from x4validate import _liveclipport as P
from x4validate._livesession import SessionUnavailable

@pytest.fixture
def setup(tmp_path):
    profile=tmp_path/"personal"/"9000000";(profile/"save").mkdir(parents=True)
    roots={}
    for name in ("port","recorder"):
        roots[name]=tmp_path/name;roots[name].mkdir()
    clientroot=tmp_path/"client";clientroot.mkdir()
    r=runtime()
    r.globals().path_payload=json.dumps({"status":"OBSERVED","scope":"NATIVE_SAVE_FOLDER_PATH",
        "stamp":STAMP,"save_folder":str(profile/"save")})
    r.execute("""
    router=routerFactory({session=session,wire=wire,registry=core,build='12345678',
      ui_authorize=function(proof,generation) return proof=='ui-proof' and generation==stamp.generation end,
      age=function() nativeage=nativeage+0.01;return nativeage end,
      read_verbs={native_save_path=true,ping=true},
      read=function(verb,args)
        if verb=='ping' then return ping_status or 'OK',ping_payload or 'pong\\t1\\t99' end
        return 'OK',path_payload
      end})
    """)
    pipe=Pipe(r);bound=BoundPipe(pipe,Link(),"12345678");client=AutomationClient(bound,clientroot)
    binary=tmp_path/"encoder.exe";binary.write_bytes(b"synthetic")
    win={"hwnd":"999","pid":7,"width":100,"height":80};calls=[]
    def window(peer,expected=None):return copy.deepcopy(win)
    def runner(argv,**kw):
        calls.append(argv);kw["check"]()
        if kw.get("output") is not None:kw["output"].write(b"synthetic clip");return b""
        return framehash()
    kwargs=dict(ffmpeg=binary,ffmpeg_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                synthetic=True,window=window,runner=runner)
    return r,pipe,client,profile,roots,kwargs,calls

def port(setup):
    r,pipe,client,profile,roots,kwargs,calls=setup
    return P.ClipPort(client,profile,roots,**kwargs)

def test_full_bound_path_and_opaque_adapter(setup):
    p=port(setup);h=p.reserve("visual.engine-loop",duration=5,fps=10)
    assert not setup[-1]
    assert p.execute(h) is h
    result=p.receipt(h)
    assert result["status"]=="CLIP_ARTIFACT_CAPTURED_REVIEW_REQUIRED"
    assert result["tier"]=="SYNTHETIC" and not result["visual_case_pass"]
    assert result["clip"]["evidence"]["decoded_frames"]==51
    assert result["sample_count"]==len(result["native_identity_samples"])>=4
    assert all(x["stamp"]==STAMP and x["build"]=="12345678" for x in result["native_identity_samples"])
    assert result["case_attribution"]=="CALLER_DECLARED_NOT_SCENE_PROOF"
    assert result["clock_join"].endswith("NOT_NUMERICALLY_JOINED")
    assert p.owner["native_path"]["receipt"]["evidence_tier"]=="SYNTHETIC"
    assert p.owner["native_path"]["transcript"]["request"]["verb"]=="native_save_path"
    assert {a[1][5] for a in setup[1].calls}=={"read_capture","read_page","ping"}
    out=p.export(h);assert out["tier"]=="EXPORTED_UNAUTHENTICATED" and out["recorded_tier"]=="SYNTHETIC"
    out["clip"]["file"]["sha256"]="f"*64
    assert p.receipt(h)["clip"]["file"]["sha256"]!="f"*64
    with pytest.raises(ClipUnavailable):p.execute(h)
    with pytest.raises(ClipUnavailable):p.receipt(result)

@pytest.mark.parametrize("kind",["foreign_path","bad_stamp","unavailable","extra","missing"])
def test_native_path_refusals(setup,kind):
    r=setup[0];v=json.loads(r.globals().path_payload)
    if kind=="foreign_path":v["save_folder"]=str(setup[4]["port"])
    elif kind=="bad_stamp":v["stamp"]["generation"]="e"*32
    elif kind=="unavailable":v["status"]="UNAVAILABLE"
    elif kind=="extra":v["new"]="unknown"
    else:del v["save_folder"]
    r.globals().path_payload=json.dumps(v)
    with pytest.raises((ClipUnavailable,SessionUnavailable)):port(setup)
    assert not (setup[4]["port"]/"clip-port-owner.json").exists()
    assert not setup[-1]

def test_requires_production_connect(setup):
    kwargs=dict(setup[5],synthetic=False)
    with pytest.raises(ClipUnavailable,match="native connection"):
        P.ClipPort(setup[2],setup[3],setup[4],**kwargs)
    assert not setup[-1]

def test_profile_mismatch(setup):
    setup[2].bound.link.peer.profile_id="999"
    with pytest.raises(ClipUnavailable):port(setup)
    assert not setup[-1]

def test_overlapping_journals(setup):
    roots=dict(setup[4],recorder=setup[4]["port"])
    with pytest.raises(ClipUnavailable):P.ClipPort(setup[2],setup[3],roots,**setup[5])

def test_personal_journal_refuses(setup):
    roots=dict(setup[4],port=setup[3]/"save")
    with pytest.raises(ClipUnavailable):P.ClipPort(setup[2],setup[3],roots,**setup[5])

@pytest.mark.parametrize("kind",["native_load","native_ui","native_missing","ping_status","ping_payload","profile","host","build"])
def test_drift_before_recording(setup,kind):
    p=port(setup);h=p.reserve("case",duration=5,fps=10);r=setup[0]
    if kind=="native_load":r.execute("stamp.session=string.rep('e',32)")
    elif kind=="native_ui":r.execute("stamp.generation=string.rep('e',32)")
    elif kind=="native_missing":r.execute("missing=true")
    elif kind=="ping_status":r.execute("ping_status='ERR'")
    elif kind=="ping_payload":r.execute("ping_payload='unknown'")
    elif kind=="profile":setup[2].bound.link.peer.profile_id="999"
    elif kind=="host":setup[2].bound.link.value["world_token"]="changed"
    else:setup[2].bound.build="ffffffff"
    with pytest.raises((ClipUnavailable,SessionUnavailable)):p.execute(h)
    assert not setup[-1]

def test_native_drift_during_child_quarantines(setup):
    p=port(setup);h=p.reserve("case",duration=5,fps=10)
    original=p.recorder.runner
    def drift(*a,**kw):
        setup[0].execute("stamp.session=string.rep('e',32)")
        return original(*a,**kw)
    p.recorder.runner=drift
    with pytest.raises((ClipUnavailable,SessionUnavailable)):p.execute(h)
    assert p.fault and p.records[h]["stage"]=="QUARANTINED"
    with pytest.raises(ClipUnavailable):p.reserve("again")

@pytest.mark.parametrize("kind",["receipt","clip","binary"])
def test_receipt_recheck_refusals(setup,kind):
    p=port(setup);h=p.reserve("case",duration=5,fps=10);p.execute(h)
    row=p.records[h]
    target={"receipt":row["receipt_path"],"clip":Path(row["result"]["clip"]["path"]),
            "binary":p.recorder.binary}[kind]
    target.write_bytes(b"changed")
    with pytest.raises((ClipUnavailable,ValueError)):p.receipt(h)

def test_source_drift(setup,monkeypatch):
    p=port(setup)
    monkeypatch.setattr(P,"source_pins",lambda:{"changed":"0"*64})
    with pytest.raises(ClipUnavailable,match="sources changed"):p.reserve("case")
    assert not setup[-1]

def test_sample_capacity_before_child(setup):
    p=port(setup);h=p.reserve("case",duration=5,fps=10)
    p.records[h]["samples"]=[{}]*P.MAX_SAMPLES
    with pytest.raises(ClipUnavailable,match="capacity"):p.execute(h)
    assert not setup[-1]

def test_owner_journal_drift(setup):
    p=port(setup);(p.root/"clip-port-owner.json").write_text("{}")
    with pytest.raises(ClipUnavailable):p.reserve("case")

def test_bad_case_label(setup):
    p=port(setup)
    with pytest.raises(ClipUnavailable):p.reserve("case;cmd")
    assert not setup[-1]

def test_production_facade_does_not_accept_synthetic_switch(setup):
    with pytest.raises(TypeError):
        P.connect_clips(setup[2],setup[3],setup[4],**setup[5])

def test_port_reentry_refuses_before_child(setup):
    p=port(setup);h=p.reserve("case",duration=5,fps=10)
    original=p.recorder.runner
    def nested(*a,**kw):
        with pytest.raises(ClipUnavailable,match="already active"):p.execute(h)
        with pytest.raises(ClipUnavailable,match="already active"):p.reserve("nested")
        with pytest.raises(ClipUnavailable,match="already active"):p.receipt(h)
        return original(*a,**kw)
    p.recorder.runner=nested;p.execute(h)
    assert len(setup[-1])==2

def test_receipt_does_not_claim_fresh_native_state(setup):
    p=port(setup);h=p.reserve("case",duration=5,fps=10);p.execute(h)
    setup[0].execute("stamp.session=string.rep('e',32)")
    value=p.receipt(h)
    assert value["historical"] is True and value["fresh_current_native_identity"] is False
    assert value["identity"]["session"]!= "e"*32

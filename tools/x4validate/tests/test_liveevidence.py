"""Actual Lua router/BoundPipe/client/pages; explicitly synthetic native data."""
import copy
import json
import pytest
from test_livebound import runtime,Pipe,STAMP
from x4validate._liveautomation import AutomationClient
from x4validate._livebound import BoundPipe
from x4validate._liveevidence import ReadEvidence,replay
from x4validate._livepeerprofile import ProfilePeer
from x4validate._livesession import SessionLink,SessionUnavailable

IDENTITY=dict(provider=STAMP["provider"],revision=STAMP["revision"],session=STAMP["session"],
    ui_generation=STAMP["generation"],world_token=STAMP["world_token"],load_generation=1,
    world_lineage="UNKNOWN",peer=dict(pid=7,creation_filetime="123",image="C:/synthetic/X4.exe"))

class Peer(ProfilePeer):
    def __init__(self):self.profile_id="9000000";self.value=copy.deepcopy(IDENTITY["peer"])
    def __call__(self):return copy.deepcopy(self.value)

class Link(SessionLink):
    def __init__(self):
        self.peer=Peer();self.value=copy.deepcopy(IDENTITY)
    def identity(self):return copy.deepcopy(self.value)

def setup(tmp_path,text="value",status="OK"):
    r=runtime();r.globals().read_payload=text;r.globals().read_status=status
    p=Pipe(r);b=BoundPipe(p,Link(),"12345678");c=AutomationClient(b,tmp_path)
    return r,p,b,c,ReadEvidence(c,synthetic=True)

@pytest.mark.parametrize("size",[0,1,999,1000,1001,32000])
def test_actual_chain_raw_frames_and_scope(tmp_path,size):
    r,p,b,c,e=setup(tmp_path,"x"*size)
    h=e.collect("probe","subject","field");value=e.receipt(h);t=e.transcript(h)
    assert value["evidence_tier"]=="SYNTHETIC" and value["world_lineage"]=="UNKNOWN"
    assert value["capture"]["payload"]=="x"*size and value["capture"]["transport_complete"]
    assert len(t["frames"])==1+max(1,(size+999)//1000)
    assert r.globals().read_calls==1 and value["atomic"] is False
    assert value["capture"]["started_age"]<=value["first_envelope_age"]<=value["last_envelope_age"]
    assert t["frames"][0]["args"][1:]==["probe","subject","field"]
    assert replay(t)["provenance"]=="EXPORTED_REPLAY_NOT_AUTHENTICATED"
    assert replay(t)["transcript_sha256"]==value["transcript_sha256"]
    t["frames"][0]["reply"]["raw"]="changed"
    assert e.transcript(h)["frames"][0]["reply"]["raw"]!="changed"
    value["capture"]["payload"]="caller edit"
    assert e.receipt(h)["capture"]["payload"]=="x"*size

@pytest.mark.parametrize("status",["ABSENT","ERR"])
def test_source_status_never_becomes_success(tmp_path,status):
    _,_,_,_,e=setup(tmp_path,"reason",status);h=e.collect("probe")
    assert e.receipt(h)["status"]==status and e.receipt(h)["transport_complete"]

def test_utf8_raw_outer_strings_preserved(tmp_path):
    _,p,_,_,e=setup(tmp_path,"艦船"*600)
    raw=[];original=p.ask
    def keep(*a):
        r=original(*a);raw.append(r.payload);return r
    p.ask=keep
    h=e.collect("probe")
    assert [f["reply"]["raw"] for f in e.transcript(h)["frames"]]==raw
    assert e.receipt(h)["capture"]["payload"]=="艦船"*600

@pytest.mark.parametrize("mode",["missing","extra","duplicate","order","raw_duplicate","outer_stamp",
    "outer_build","outer_age","source_age","source_status","host","profile","pins","request","nonce","sequence"])
def test_replay_counterexamples(tmp_path,mode):
    _,_,_,_,e=setup(tmp_path,"x"*1001);t=e.transcript(e.collect("probe","one"))
    f=t["frames"]
    if mode=="missing":f.pop()
    elif mode=="extra":f.append(copy.deepcopy(f[-1]))
    elif mode=="duplicate":f[2]=copy.deepcopy(f[1])
    elif mode=="order":f[1],f[2]=f[2],f[1]
    elif mode=="raw_duplicate":f[0]["reply"]["raw"]=f[0]["reply"]["raw"].replace('"version":1','"version":1,"version":1')
    elif mode in ("outer_stamp","outer_build","outer_age","source_age","source_status"):
        i=1 if mode=="outer_age" else 0
        row=json.loads(f[i]["reply"]["raw"])
        if mode=="outer_stamp":row["stamp"]["session"]="e"*32
        elif mode=="outer_build":row["build"]="f"*8
        elif mode=="outer_age":row["age"]=1
        else:
            inner=json.loads(row["payload"])
            if mode=="source_age":inner["finished_age"]=999
            else:inner["source_status"]="PASS"
            row["payload"]=json.dumps(inner)
        f[i]["reply"]["raw"]=json.dumps(row)
    elif mode=="host":f[1]["after"]["peer"]["pid"]=8
    elif mode=="profile":t["profile_id"]=""
    elif mode=="pins":t["source_pins"]["_livebound.py"]="0"*64
    elif mode=="request":t["request"]["args"]=["other"]
    elif mode=="nonce":f[1]["args"][0]="e"*32
    elif mode=="sequence":f[1]["reply"]["seq"]=f[0]["reply"]["seq"]
    with pytest.raises(SessionUnavailable):replay(t)

def test_missing_page_and_identity_drift_create_no_handle(tmp_path):
    _,p,b,c,e=setup(tmp_path,"x"*1001);original=p.ask
    def change(*a):
        out=original(*a)
        if len(p.calls)==2:b.link.value["peer"]["pid"]=8
        return out
    p.ask=change
    with pytest.raises(SessionUnavailable):e.collect("probe")
    assert not e.handles and b.failed

def test_end_profile_drift_creates_no_handle(tmp_path):
    _,p,b,c,e=setup(tmp_path);original=c.query
    def change(*a):
        result=original(*a);b.link.peer.profile_id="9000001";return result
    c.query=change
    with pytest.raises(SessionUnavailable):e.collect("probe")
    assert not e.handles

@pytest.mark.parametrize("verb",["action_execute","pause","read_page","session_connect"])
def test_nonread_rejected_before_pipe(tmp_path,verb):
    _,p,_,_,e=setup(tmp_path)
    with pytest.raises(SessionUnavailable):e.collect(verb)
    assert not p.calls

def test_trace_does_not_allow_action_even_if_query_is_replaced(tmp_path):
    _,p,b,c,e=setup(tmp_path)
    c.query=lambda *a:b.ask("action_execute","packet")
    with pytest.raises(SessionUnavailable):e.collect("probe")
    assert not p.calls and not e.handles and b.failed

def test_nested_trace_refuses(tmp_path):
    _,p,b,_,_=setup(tmp_path)
    with b.read_trace():
        with pytest.raises(SessionUnavailable):
            with b.read_trace():pass
    assert not p.calls

def test_frame_limit_before_extra_read(tmp_path):
    _,p,b,_,_=setup(tmp_path)
    with b.read_trace() as t:
        t.frames=[{}]*33
        with pytest.raises(SessionUnavailable):b.ask("read_capture","e"*32,"probe")
    assert not p.calls
    with pytest.raises(SessionUnavailable):t.snapshot()

def test_partial_trace_is_not_settled(tmp_path):
    _,_,b,_,_=setup(tmp_path)
    with pytest.raises(SessionUnavailable):
        with b.read_trace() as t:
            b.ask("read_capture","e"*32,"probe")
            raise SessionUnavailable("interrupted")
    with pytest.raises(SessionUnavailable):t.snapshot()

def test_bare_constructor_cannot_promote_to_observed(tmp_path):
    _,_,_,c,_=setup(tmp_path)
    with pytest.raises(SessionUnavailable):ReadEvidence(c)
    with pytest.raises(SessionUnavailable):ReadEvidence(c,synthetic="yes")

def test_unknown_handle_and_collector_capacity(tmp_path):
    _,_,_,_,e=setup(tmp_path)
    with pytest.raises(SessionUnavailable):e.receipt(object())
    e.handles={object():None for _ in range(16)}
    with pytest.raises(SessionUnavailable):e.collect("probe")

"""Actual Python client -> codec -> Lua bootstrap/pipe/menu, synthetic native UI."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest
import os
import subprocess
import sys
from x4validate._livestartmenu import StartClient,ConversationAuthority,SessionUnavailable,digest

ROOT=Path(__file__).resolve().parents[3]
BUILD="b"*64
PEER={"pid":123,"creation_filetime":"123456","image":"C:/test/X4.exe"}

class ActualLuaPipe:
    def __init__(self):
        spec=importlib.util.spec_from_file_location("start_fixture",Path(__file__).with_name("test_livestartmenu.py"))
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        self.rt=fixture.env()
        g=self.rt.globals()
        for key,file in [("menufactory","live_startmenu.lua"),("pipefactory","live_startpipe.lua"),
                         ("codecfactory","live_actionwire.lua"),("bootstrapfactory","live_startbootstrap.lua")]:
            setattr(g,key,self.rt.execute((ROOT/"mods/x4_toolkit_helper/ui"/file).read_text(encoding="utf-8")))
        self.rt.execute(r'''
g={Menus=menus,GetCellContent=deps.cell_id,IsButtonActive=deps.button_active,
IsValidWidgetElement=deps.valid_widget,ReadText=deps.read_text,OpenMenu=deps.open_menu,
getElapsedTime=deps.now}
H={currentTableRow={[20]=1}}
g.SelectRow=function(t,r) deps.select_row(t,r);H.currentTableRow[t]=currentrow end
C={IsStartmenu=deps.is_startmenu}
inputs,outputs,pending={},{},{}
H.addDelayedOneTimeCallbackOnUpdate=function(cb,block,when)
    assert(block==false and when>age);pending[#pending+1]=cb
end
pipes={
    Read_Pipe=function(name) assert(name=="x4live_startmenu");return true,table.remove(inputs,1) end,
    Write_Pipe=function(name,value) assert(name=="x4live_startmenu");outputs[#outputs+1]=value;return true end,
}
bootstrap=bootstrapfactory.new({modules={menu=menufactory,pipe=pipefactory,codec=codecfactory},
    build=string.rep("b",64),globals=g,C=C,Helper=H,pipes=pipes})
assert(bootstrap.start().status=="SCHEDULED")
function tick()
    age=age+0.2;local cb=table.remove(pending,1);assert(cb);cb()
end
''')
        self.packets=[];self.mutate=None;self.lose=None

    def exchange(self,packet):
        self.packets.append(packet)
        rt=self.rt;before=len(rt.globals().outputs)
        rt.globals().inputs[len(rt.globals().inputs)+1]=packet;rt.globals().tick()
        assert len(rt.globals().outputs)==before+1,"synthetic pipe produced no reply"
        raw=rt.globals().outputs[before+1]
        if self.lose and packet.split("|")[4]==self.lose:
            raise TimeoutError("lost after native handling")
        return self.mutate(raw) if self.mutate else raw

@pytest.fixture
def case(tmp_path):
    root=tmp_path/"client";root.mkdir();authroot=tmp_path/"authority";authroot.mkdir()
    state={"peer":copy.deepcopy(PEER),"now":10,"approved":True}
    def decide(context):
        if not state["approved"]:return False
        return {"source":"AGENT_RECORDED_CONVERSATION","reference":"synthetic conversation only",
                "scope":"NEW_WORLD_IN_APPROVED_RUNNING_DISPOSABLE_CAMPAIGN","context_sha256":digest(context)}
    authority=ConversationAuthority(authroot,decide,clock=lambda:state["now"])
    pipe=ActualLuaPipe()
    client=StartClient(pipe,root,lambda:copy.deepcopy(state["peer"]),"9000000",BUILD,authority,
                       desktop="explicit test desktop",focus="visible test window")
    return client,pipe,authority,state

def check_actual_native_chain_bound_no_fake_world(case):
    client,pipe,authority,state=case
    ident=client.bind()
    assert ident["world_session"] is False and ident["scope"]=="UI_ONLY_NO_WORLD"
    assert ident["peer"]==PEER and ident["profile_id"]=="9000000"
    r=client.prepare("start","argongamestart")
    out=client.execute(r["request_id"])
    assert out["kind"]=="START_AUTHORIZATION_CONSUMED"
    assert out["result"]["status"]=="DISPATCHED_UNVERIFIED"
    assert out["result"]["world_started"] is False
    assert list(pipe.rt.globals().calls.values())==["wrapper","start"]
    assert client.status(r["request_id"])["status"]=="DISPATCHED_UNVERIFIED"
    assert len(list(authority.root.glob("*issued.json")))==1
    assert len(list(authority.root.glob("*consumed.json")))==1
    with pytest.raises(SessionUnavailable):client.execute(r["request_id"])

def check_selection_without_start_or_authority(case):
    c,p,a,s=case;c.bind()
    r=c.prepare("select","other");out=c.execute(r["request_id"])
    assert out["status"]=="SELECTION_OBSERVED"
    assert not list(a.root.iterdir()) and list(p.rt.globals().calls.values())==["select"]

def check_unapproved_callback_cannot_dispatch(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");s["approved"]=False
    before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.execute(r["request_id"])
    assert len(p.packets)==before and not list(p.rt.globals().calls.values())

@pytest.mark.parametrize("field,value",[
    ("build","c"*64),("world_session",True),("scope","WORLD"),("ui_generation","d"*32),
    ("ui_instance","d"*32),("profile_id","wrong"),("process_binding","e"*64),("call_id","f"*32),
])
def check_each_reply_binding_mismatch_refuses(case,field,value):
    c,p,a,s=case;c.bind()
    def mutate(raw):
        row=json.loads(raw);row[field]=value;return json.dumps(row)
    p.mutate=mutate
    with pytest.raises(SessionUnavailable):c.observe()
    assert c.failed
    before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.observe()
    assert len(p.packets)==before

def check_wrong_loaded_build_refuses_before_any_menu_action(case):
    c,p,a,s=case;c.build="c"*64
    with pytest.raises(SessionUnavailable):c.bind()
    assert c.failed and not list(p.rt.globals().calls.values())

def check_changed_peer_before_write_refuses(case):
    c,p,a,s=case;c.bind();s["peer"]["pid"]=124;before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.observe()
    assert len(p.packets)==before

def check_peer_change_during_reply_refuses(case):
    c,p,a,s=case;c.bind()
    def mutate(raw):s["peer"]["pid"]=124;return raw
    p.mutate=mutate
    with pytest.raises(SessionUnavailable):c.observe()

def check_ui_allocation_tamper_refuses(case):
    c,p,a,s=case;c.bind()
    path=next(c.allocations.path.iterdir());row=json.loads(path.read_text(encoding="utf-8"));row["session"]="f"*32
    path.write_text(json.dumps(row))
    before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.observe()
    assert len(p.packets)==before

@pytest.mark.parametrize("lost",["prepare","execute","authorize"])
def check_lost_reply_never_resends_or_restarts(case,lost):
    c,p,a,s=case;c.bind();rid="1"*32
    if lost=="prepare":
        p.lose=lost
        with pytest.raises(SessionUnavailable):c.prepare("start","argongamestart",request_id=rid)
    else:
        c.prepare("start","argongamestart",request_id=rid);p.lose=lost
        with pytest.raises(SessionUnavailable):c.execute(rid)
    before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.execute(rid)
    assert len(p.packets)==before
    assert list(p.rt.globals().calls.values())==(["wrapper","start"] if lost=="authorize" else [])

def check_new_client_can_only_observe_consumed_start(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");p.lose="authorize"
    with pytest.raises(SessionUnavailable):c.execute(r["request_id"])
    p.lose=None
    other=StartClient(p,c.root,c.peer,c.profile,BUILD,a,desktop=c.desktop,focus=c.focus);other.bind()
    assert other.status(r["request_id"])["status"]=="DISPATCHED_UNVERIFIED"
    with pytest.raises(SessionUnavailable):other.execute(r["request_id"])
    assert list(p.rt.globals().calls.values())==["wrapper","start"]

def check_authority_is_opaque_exact_one_use(case):
    c,p,a,s=case
    context={"test":"bound observation only"}
    handle=a.issue(context)
    for invalid in (True,{},json.loads(next(a.root.glob("*issued.json")).read_text(encoding="utf-8")),object()):
        with pytest.raises(SessionUnavailable):a.consume(invalid,context)
    with pytest.raises(SessionUnavailable):a.consume(handle,{"test":"other"})
    assert a.consume(handle,context)["decision"]["source"]=="AGENT_RECORDED_CONVERSATION"
    with pytest.raises(SessionUnavailable):a.consume(handle,context)

@pytest.mark.parametrize("mutation",["expired","backward","tamper"])
def check_authority_freshness_and_record(case,mutation):
    c,p,a,s=case;context={"test":"x"};handle=a.issue(context)
    if mutation=="expired":s["now"]=41
    elif mutation=="backward":s["now"]=9
    else:next(a.root.glob("*issued.json")).write_text("{}")
    with pytest.raises(SessionUnavailable):a.consume(handle,context)

def check_request_journal_changed_refuses(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");rid=r["request_id"]
    path=c.requests/(rid+"-request.json");value=json.loads(path.read_text(encoding="utf-8"));value["option"]="other"
    path.write_text(json.dumps(value))
    with pytest.raises(SessionUnavailable):c.execute(rid)
    assert not list(p.rt.globals().calls.values())

def check_policy_callback_drift_refuses_before_execute(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");original=a.decide
    def drift(context):s["peer"]["pid"]=124;return original(context)
    a.decide=drift
    before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.execute(r["request_id"])
    assert len(p.packets)==before and not list(p.rt.globals().calls.values())

def check_no_untrusted_boolean_or_bad_policy_record(case):
    c,p,a,s=case
    for value in (True,{"approved":True},{"source":"USER_VERIFIED","reference":"x","scope":"any","context_sha256":"a"*64}):
        a.decide=lambda context:value
        with pytest.raises(SessionUnavailable):a.issue({"test":"x"})

def check_production_connect_requires_actual_dedicated_pipe(case):
    c,p,a,s=case
    with pytest.raises(SessionUnavailable):
        StartClient.connect(p,c.root,image="X4.exe",profile_id=c.profile,build=BUILD,
                            authority=a,desktop=c.desktop,focus=c.focus)

def check_request_changed_inside_policy_callback_refuses(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");original=a.decide
    def drift(context):
        path=c.requests/(r["request_id"]+"-request.json")
        row=json.loads(path.read_text(encoding="utf-8"));row["option"]="other";path.write_text(json.dumps(row))
        return original(context)
    a.decide=drift;before=len(p.packets)
    with pytest.raises(SessionUnavailable):c.execute(r["request_id"])
    assert len(p.packets)==before and not list(p.rt.globals().calls.values())

@pytest.mark.parametrize("level",["execute","authorize","status"])
def check_world_completion_overclaim_refuses(case,level):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart")
    if level=="status":c.execute(r["request_id"])
    def mutate(raw):
        row=json.loads(raw);result=row["result"]
        if level=="authorize" and result.get("kind")=="START_AUTHORIZATION_CONSUMED":
            result["result"]["world_started"]=True
        elif level in ("execute","status") and "request_id" in result:
            result["world_started"]=True
        return json.dumps(row)
    p.mutate=mutate
    with pytest.raises(SessionUnavailable):
        (c.status if level=="status" else c.execute)(r["request_id"])

def check_bootstrap_proof_superseded_or_reused_is_not_authority(case):
    c,p,a,s=case;c.bind()
    c._send("ui_state",{"challenge":"1"*32},bootstrap=True)
    c._send("ui_state",{"challenge":"2"*32},bootstrap=True)
    values={"proof":"1"*32,"generation":c.identity()["ui_generation"],
            "process_binding":digest({"peer":PEER,"profile_id":c.profile}),"profile_id":c.profile}
    assert c._send("ui_bind",values,bootstrap=True)["status"]=="UNKNOWN"
    values["proof"]="2"*32
    assert c._send("ui_bind",values,bootstrap=True)["kind"]=="UI_BOUND"
    assert c._send("ui_bind",values,bootstrap=True)["status"]=="UNKNOWN"
    assert not list(p.rt.globals().calls.values())

@pytest.mark.parametrize("field,value",[
    ("generation","f"*32),("process_binding","e"*64),("profile_id","9000001"),
])
def check_bound_ui_cannot_rebind_scope(case,field,value):
    c,p,a,s=case;c.bind();c._send("ui_state",{"challenge":"1"*32},bootstrap=True)
    args={"proof":"1"*32,"generation":c.identity()["ui_generation"],
          "process_binding":digest({"peer":PEER,"profile_id":c.profile}),"profile_id":c.profile}
    args[field]=value
    assert c._send("ui_bind",args,bootstrap=True)["status"]=="UNKNOWN"
    assert not list(p.rt.globals().calls.values())

def check_native_authorization_nonce_cannot_replay(case):
    c,p,a,s=case;c.bind();r=c.prepare("start","argongamestart");out=c.execute(r["request_id"])
    again=c._send("authorize",{"request_id":r["request_id"],"gamestart":"argongamestart",
        "capability":out["capability"],"context_sha256":out["context_sha256"]})
    assert again["status"]=="UNKNOWN"
    assert list(p.rt.globals().calls.values())==["wrapper","start"]

def check_packet_boolean_cannot_authorize_or_mint_host_capability(case):
    c,p,a,s=case;c.bind()
    before=len(list(a.root.iterdir()))
    result=c._send("authorize",{"approved":True})
    assert result["status"]=="UNKNOWN" and len(list(a.root.iterdir()))==before
    assert not list(p.rt.globals().calls.values())

def check_dedicated_native_bootstrap_has_no_player_dependency(case):
    c,p,a,s=case
    p.rt.execute("assert(g.GetPlayerID==nil and g.GetNPCBlackboard==nil and C.GetPlayerID==nil)")
    assert c.bind()["world_session"] is False
    assert c.observe()["status"]=="OBSERVED"

@pytest.mark.parametrize("failure",["none","partial","read_error","read_status","timeout","invalid_utf8","oversized"])
def check_actual_dedicated_transport_boundaries(case,failure):
    import x4validate._livestartmenu as module
    from types import SimpleNamespace
    calls=[]
    class NativeError(Exception):winerror=232
    class Files:
        @staticmethod
        def WriteFile(handle,data):
            calls.append("write");return 0,len(data)-(1 if failure=="partial" else 0)
        @staticmethod
        def ReadFile(handle,bound):
            calls.append("read");assert bound==4097
            if failure=="read_error":raise OSError("native read failure")
            if failure=="read_status":return 234,b"{}"
            if failure=="timeout":raise NativeError("no data")
            if failure=="invalid_utf8":return 0,b"\xff"
            if failure=="oversized":return 0,b"x"*4097
            return 0,b"{}"
    clock=[0]
    def tick():clock[0]+=10;return clock[0]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module,"_win32",lambda:(None,Files,SimpleNamespace(ERROR_NO_DATA=232,ERROR_PIPE_LISTENING=536)))
        patch.setattr(module.time,"monotonic",tick);patch.setattr(module.time,"sleep",lambda _:None)
        pipe=module.UIStartPipe();pipe._h=object();pipe._connected=True
        if failure=="none":assert pipe.exchange("packet")=="{}"
        else:
            with pytest.raises(SessionUnavailable):pipe.exchange("packet")
            count=len(calls)
            with pytest.raises(SessionUnavailable):pipe.exchange("packet")
            assert len(calls)==count
        if failure=="partial":assert calls==["write"]

@pytest.mark.parametrize("outcome",["connected","waiting"])
def check_ui_connection_refusal_does_not_infer_world_loading(case,outcome):
    import x4validate._livestartmenu as module
    from types import SimpleNamespace
    class NativeError(Exception):winerror=536
    def connect(*_):
        if outcome=="waiting":raise NativeError("listening")
    times=iter([0,100])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module,"_win32",lambda:(SimpleNamespace(ConnectNamedPipe=connect),None,
            SimpleNamespace(ERROR_PIPE_CONNECTED=535,ERROR_PIPE_LISTENING=536,ERROR_NO_DATA=232)))
        patch.setattr(module.time,"monotonic",lambda:next(times))
        pipe=module.UIStartPipe();pipe._h=object()
        if outcome=="connected":pipe.wait_for_game();assert pipe._connected
        else:
            with pytest.raises(SessionUnavailable,match="pre-world loading is not proved"):pipe.wait_for_game()

def check_immediate_presend_peer_drift_has_zero_exchanges(case):
    c,p,a,s=case;c.bind();before=len(p.packets)
    old=copy.deepcopy(s["peer"]);new=copy.deepcopy(old);new["pid"]+=1
    observed=iter([old,new])
    c.peer=lambda:next(observed)
    with pytest.raises(SessionUnavailable,match="before UI request"):c.observe()
    assert len(p.packets)==before and c.failed and c.ready is None

def _cases():
    result=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            multiple="," in marks[0].args[0]
            for i,arg in enumerate(marks[0].args[1]):
                result.append(pytest.param(name,list(arg) if multiple else [arg],id=f"{name}-{i}"))
        else:result.append(pytest.param(name,[],id=name))
    return result

@pytest.mark.parametrize("name,args",_cases())
def test_actual_host_lua_chain(name,args,tmp_path):
    p=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args),str(tmp_path)],
        capture_output=True,text=True,timeout=30,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip()=="CONTROL_PASSED",p.stdout+p.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](case.__wrapped__(Path(sys.argv[3])),*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

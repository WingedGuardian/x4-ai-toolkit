"""Actual persistence gate/native Lua + host observer/dispatch; synthetic temp saves only."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from x4validate._livepersistdispatch import PersistenceAuthority,PersistenceDispatcher,PersistenceUnavailable,sha,guard_reply
from x4validate._livesession import SessionLink,SessionJournal,SessionUnavailable
ROOT=Path(__file__).resolve().parents[3]
SID="1"*32;LID="2"*32

def setup(tmp):
    spec=importlib.util.spec_from_file_location("host_observer_fixture",Path(__file__).with_name("test_livepersistence_host.py"))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    fixture=m.case.__wrapped__(tmp);v,state,save,journal=fixture
    spec2=importlib.util.spec_from_file_location("native_fixture",Path(__file__).with_name("test_livepersistence.py"))
    f=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(f)
    rt=f.luajit.LuaRuntime(unpack_returned_tuples=True);rt.execute(f.SETUP)
    def to_lua(x):
        if isinstance(x,dict):return rt.table_from({k:to_lua(z) for k,z in x.items()})
        if isinstance(x,list):return rt.table_from([to_lua(z) for z in x])
        return x
    def from_lua(x):
        if hasattr(x,"items"):
            pairs=list(x.items())
            if pairs and all(type(k) is int for k,_ in pairs):return [from_lua(x[i]) for i in range(1,len(pairs)+1)]
            return {k:from_lua(z) for k,z in pairs}
        return x
    native=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_persistence.lua").read_text())
    gate=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_persistencegate.lua").read_text())
    rt.globals().nativefactory=native
    rt.execute("d.native=nativefactory")
    api=gate(rt.globals().d)
    class Port:
        def __init__(self):self.calls=[];self.after=None;self.mutate=None;self.lose=None
        def __call__(self,op,args):
            self.calls.append(op)
            out=from_lua(api.invoke(op,to_lua(args)))
            if self.after:self.after(op,args,out)
            if self.lose==op:raise OSError("synthetic lost response")
            result={"identity":copy.deepcopy(state["identity"]),"result":out}
            return self.mutate(result) if self.mutate else result
    port=Port()
    def native_save(slot,label):
        assert slot=="save_001" and label=="x4live-"+SID
        m.create(fixture,compressed=True)
        rt.globals().rows=to_lua(state["rows"])
    rt.globals().save_callback=native_save
    rt.execute('g.SaveGame=function(slot,label) note("save");save_callback(slot,label) end')
    rt.globals().age=state["time"]
    assert api.invoke("refresh",rt.table())["status"]=="REFRESH_REQUESTED"
    # Use the actual canonical session journal/link against a synthetic MD handshake.
    sj=SessionJournal(tmp/"session-journal")
    peer=copy.deepcopy(state["identity"]["peer"])
    def link(token="world",generation="d"*32):
        def exchange(req):
            return {"kind":"STATE" if req["mode"]=="probe" else "READY",
                "provider":"x4-live-session-v1","revision":"b"*64,"mode":req["mode"],
                "challenge":req["challenge"],"generation":req["generation"],"session":req["session"],
                "has_session":bool(req["session"]),"reason":"synthetic","age":10,"world_token":token}
        l=SessionLink(sj,lambda:copy.deepcopy(peer),exchange,"b"*64,generation)
        l.fresh(l.probe());return l
    old=link();state["identity"]=old.identity()
    rt.globals().stamp=to_lua(v._stamp(state["identity"]))
    assert api.invoke("refresh",rt.table())["status"]=="REFRESH_REQUESTED"
    v.catalogue=lambda:{"status":"OBSERVED","scope":"NATIVE_STANDARD_SAVE_CATALOGUE","atomic":False,
        "stamp":v._stamp(state["identity"]),"rows":copy.deepcopy(state["rows"]),
        "count":len(state["rows"]),"saveable":state["saveable"]}
    def decide(c):
        return {"source":"AGENT_RECORDED_CONVERSATION","reference":"SYNTHETIC TEST ONLY",
                "context_sha256":sha(c),"operation":c["operation"],
                "mode":"EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION"}
    (tmp/"authority").mkdir();(tmp/"dispatch").mkdir()
    authority=PersistenceAuthority(tmp/"authority",decide,clock=lambda:state["time"])
    dispatcher=PersistenceDispatcher(v,tmp/"dispatch",authority,port,clock=lambda:state["time"])
    return dict(v=v,state=state,save=save,rt=rt,api=api,port=port,a=authority,d=dispatcher,link=link,
                to_lua=to_lua,from_lua=from_lua,old=old)

def saved(c):
    ticket=c["d"].reserve_save(SID,"save_001")
    assert c["d"].execute(ticket)["status"]=="SAVE_REQUESTED"
    assert c["d"].settle(ticket) is None
    c["state"]["time"]+=2;c["rt"].globals().age=c["state"]["time"]
    receipt=c["d"].settle(ticket)
    assert receipt["causal_engine_completion"] is False
    return ticket

def load_requested(c):
    save=saved(c);ticket=c["d"].prepare_load(LID,save)
    assert c["d"].execute(ticket)["status"]=="LOAD_SCHEDULED"
    c["rt"].globals().callback()
    assert c["d"].acknowledge_load(ticket)["status"]=="LOAD_REQUESTED"
    return ticket

def check_full_actual_chain_scoped_not_global_lineage(tmp):
    c=setup(tmp);ticket=load_requested(c);new=c["link"]()
    c["state"]["identity"]=new.identity()
    result=c["d"].observe_loaded(ticket,new,lambda:c["v"]._stamp(new.identity()))
    assert result["status"]=="OWN_SAVE_LOAD_TRANSITION_OBSERVED"
    assert result["generic_world_lineage"]=="UNKNOWN" and not result["engine_loaded_file_digest_measured"]
    assert c["rt"].globals().nc("save")==1 and c["rt"].globals().nc("load")==1
    assert new.identity()["world_lineage"]=="UNKNOWN"
    with pytest.raises(PersistenceUnavailable):c["d"].observe_loaded(ticket,new,lambda:c["v"]._stamp(new.identity()))

@pytest.mark.parametrize("bad",[True,{"approved":True},"continue"])
def check_untrusted_or_generic_decision_never_dispatches(tmp,bad):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001");c["a"].decide=lambda _:bad
    with pytest.raises((PersistenceUnavailable,TypeError)):c["d"].execute(t)
    assert c["rt"].globals().nc("save")==0 and "execute_save" not in c["port"].calls

@pytest.mark.parametrize("mode",["CANONICAL_GUARD","CONTINUE","DEVELOPMENT","EXPLICIT_NATIVE_EXCEPTION"])
def check_unqualified_effect_scope_or_generic_mode_refuses(tmp,mode):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001");old=c["a"].decide
    c["a"].decide=lambda x:{**old(x),"mode":mode}
    with pytest.raises(PersistenceUnavailable):c["d"].execute(t)
    assert c["rt"].globals().nc("save")==0

def good_guard():
    return {"inert":False,"transactions":[],"decision":"advise",
        "result":{"api_version":1,"decision":"allow","evaluation":"evaluated","preservation":"not_required",
                  "reason":"synthetic only","validation":"not_run","warnings":[]}}

@pytest.mark.parametrize("change",["ask","deny","inert","unevaluated","preservation","warnings","bool_version","nested_transactions"])
def check_guard_reply_refusals(tmp,change):
    r=good_guard()
    if change in ("ask","deny"):r["result"]["decision"]=change
    elif change=="inert":r["inert"]=True
    elif change=="unevaluated":r["result"]["evaluation"]="unguarded"
    elif change=="preservation":r["result"]["preservation"]="required"
    elif change=="warnings":r["result"]["warnings"]=[True]
    elif change=="bool_version":r["result"]["api_version"]=True
    else:r["result"]["transactions"]=r.pop("transactions")
    with pytest.raises(PersistenceUnavailable):guard_reply(r)

def check_exact_guard_callback_route_and_advisory(tmp):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001");old=c["a"].decide;checked=[]
    c["a"].decide=lambda x:{**old(x),"mode":"CANONICAL_GUARD"}
    c["a"].effects=lambda x:{"status":"EXACT_NATIVE_EFFECTS_SOURCE_QUALIFIED","source":"SYNTHETIC ONLY",
        "action":{"api_version":1,"surface":"files","paths":[str(c["save"]/"save_001.xml.gz")],"operations":["write"]}}
    c["a"].check=lambda action:(checked.append(action) or good_guard())
    assert c["d"].execute(t)["status"]=="SAVE_REQUESTED" and len(checked)==1
    evidence=json.loads(next(c["a"].journal.glob("*-issued.json")).read_text())
    assert evidence["guard_qualified"] is True

@pytest.mark.parametrize("mutation",["peer","save_file","native_rows","journal"])
def check_drift_inside_policy_refuses_before_native(tmp,mutation):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001");old=c["a"].decide
    def drift(ctx):
        if mutation=="peer":c["state"]["identity"]["peer"]["pid"]+=1
        elif mutation=="save_file":(c["save"]/"save_001.xml").write_text("unexpected")
        elif mutation=="native_rows":c["state"]["rows"]=[{"filename":"save_002","name":"unexpected","rawtime":1}]
        else:next(c["v"].journal.glob("*-reservation.json")).write_text("{}")
        return old(ctx)
    c["a"].decide=drift
    with pytest.raises(PersistenceUnavailable):c["d"].execute(t)
    assert c["rt"].globals().nc("save")==0

@pytest.mark.parametrize("op",["execute_save","execute_load"])
def check_lost_reply_never_replays(tmp,op):
    c=setup(tmp)
    t=c["d"].reserve_save(SID,"save_001") if op=="execute_save" else c["d"].prepare_load(LID,saved(c))
    c["port"].lose=op
    with pytest.raises(OSError):c["d"].execute(t)
    count=len(c["port"].calls)
    with pytest.raises(PersistenceUnavailable):c["d"].execute(t)
    assert len(c["port"].calls)==count and c["d"].record(t)["state"]=="UNKNOWN"
    assert len(list(c["d"].root.glob("*-dispatch.json")))>=1

def check_scheduled_load_is_not_invoked_or_lineage(tmp):
    c=setup(tmp);t=c["d"].prepare_load(LID,saved(c));c["d"].execute(t)
    with pytest.raises(PersistenceUnavailable):c["d"].acknowledge_load(t)
    with pytest.raises(PersistenceUnavailable):c["d"].observe_loaded(t,c["old"],lambda:c["v"]._stamp(c["old"].identity()))
    assert c["rt"].globals().nc("load")==0

@pytest.mark.parametrize("change",["same_generation","other_world","skipped_load","file","frame","bad_native_stamp","expiry"])
def check_transition_refusals(tmp,change):
    c=setup(tmp);t=load_requested(c)
    if change=="same_generation":new=c["old"]
    else:
        if change=="skipped_load":c["link"]()
        new=c["link"]("different" if change=="other_world" else "world")
    c["state"]["identity"]=new.identity()
    if change=="file":(c["save"]/"save_001.xml.gz").write_bytes(b"drift")
    elif change=="frame":(c["save"]/"extra").write_bytes(b"drift")
    elif change=="expiry":c["state"]["time"]+=301
    stamp=lambda:c["v"]._stamp(new.identity())
    if change=="bad_native_stamp":stamp=lambda:{**c["v"]._stamp(new.identity()),"session":"f"*32}
    with pytest.raises((PersistenceUnavailable,SessionUnavailable)):c["d"].observe_loaded(t,new,stamp)

def check_old_receipt_must_be_rechecked_before_load(tmp):
    c=setup(tmp);save=saved(c);(c["save"]/"save_001.xml.gz").write_bytes(b"changed")
    with pytest.raises(PersistenceUnavailable):c["d"].prepare_load(LID,save)
    assert "prepare_load" not in c["port"].calls

def check_opaque_authority_and_single_use(tmp):
    c=setup(tmp);ticket=c["d"].reserve_save(SID,"save_001");r=c["d"].record(ticket)
    context=c["d"].context("SAVE",SID,r["source"],r["identity"])
    handle=c["a"].issue(context)
    with pytest.raises(PersistenceUnavailable):c["a"].consume({"capability":"x"},context)
    assert c["a"].consume(handle,context)["guard_qualified"] is False
    with pytest.raises(PersistenceUnavailable):c["a"].consume(handle,context)

@pytest.mark.parametrize("mutation",["status","ticket","completed","envelope"])
def check_port_false_claims_refused(tmp,mutation):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001")
    def corrupt(v):
        if mutation=="status":v["result"]["status"]="SAVE_COMPLETED"
        elif mutation=="ticket":v["result"]["ticket"]="f"*32
        elif mutation=="completed":v["result"]["completed"]=True
        else:v["identity"]["session"]="f"*32
        return v
    c["port"].mutate=corrupt
    with pytest.raises(PersistenceUnavailable):c["d"].execute(t)
    assert c["d"].record(t)["state"]=="UNKNOWN"

def check_gate_rejects_replay_and_wrong_authority_mode(tmp):
    c=setup(tmp);t=c["d"].reserve_save(SID,"save_001")
    args={"ticket":SID,"capability":"f"*32,"context_sha256":"e"*64,"mode":"CANONICAL_GUARD","guard_qualified":False}
    with pytest.raises(Exception):c["api"].invoke("execute_save",c["to_lua"](args))
    assert c["rt"].globals().nc("save")==0
    assert c["d"].execute(t)["status"]=="SAVE_REQUESTED"
    args["mode"]="EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION"
    with pytest.raises(Exception):c["api"].invoke("execute_save",c["to_lua"](args))
    assert c["rt"].globals().nc("save")==1

@pytest.mark.parametrize("mutation",["missing_profile","wrong_path","wrong_operation","bad_slot","bad_label","missing_inventory"])
def check_authority_requires_named_complete_operation(tmp,mutation):
    c=setup(tmp);ticket=c["d"].reserve_save(SID,"save_001");r=c["d"].record(ticket)
    context=c["d"].context("SAVE",SID,r["source"],r["identity"])
    if mutation=="missing_profile":context.pop("profile")
    elif mutation=="wrong_path":context["save_directory"]=str(c["save"].parent/"other")
    elif mutation=="wrong_operation":context["native_operation"]="LoadGame"
    elif mutation=="bad_slot":context["slot"]="quicksave"
    elif mutation=="bad_label":context["label"]="normal save"
    else:context.pop("inventory_sha256")
    with pytest.raises(PersistenceUnavailable):c["a"].issue(context)
    assert not list(c["a"].journal.iterdir())

def check_consumption_time_frame_drift_refuses_without_replay(tmp):
    c=setup(tmp);ticket=c["d"].reserve_save(SID,"save_001");consume=c["a"].consume
    def changed(handle,context):
        result=consume(handle,context)
        (c["save"]/"unrelated.xml").write_text("changed during consume")
        return result
    c["a"].consume=changed
    with pytest.raises(PersistenceUnavailable,match="during authority consumption"):c["d"].execute(ticket)
    assert c["d"].record(ticket)["state"]=="CONSUMED" and c["rt"].globals().nc("save")==0
    with pytest.raises(PersistenceUnavailable):c["d"].execute(ticket)
    assert "execute_save" not in c["port"].calls

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,arg in enumerate(marks[0].args[1]):out.append(pytest.param(name,[arg],id=name+"-"+str(i)))
        else:out.append(pytest.param(name,[],id=name))
    return out

@pytest.mark.parametrize("name,args",_cases())
def test_actual_persistence_chain(name,args,tmp_path):
    run=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args),str(tmp_path)],
        capture_output=True,text=True,timeout=30,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert run.returncode==0,run.stdout+run.stderr
    assert run.stdout.strip()=="CONTROL_PASSED",run.stdout+run.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](Path(sys.argv[3]),*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

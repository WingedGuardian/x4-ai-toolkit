"""Actual SDK/wire/AutomationClient/persistence provider; synthetic native I/O only."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from x4validate._liveautomation import AutomationClient
from x4validate._livepersistport import PersistencePort
from x4validate._livepersistdispatch import PersistenceUnavailable
from x4validate._liveactions import ActionRefused
from x4validate._livepipe import Reply
from x4validate import _liveautomationcli as cli
ROOT=Path(__file__).resolve().parents[3]

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def setup(tmp):
    base=load(Path(__file__).with_name("test_livepersistdispatch.py"),"dispatch_fixture")
    c=base.setup(tmp);rt=c["rt"]
    extension=load(ROOT/"scripts/live_persistence_extension.py","extension")
    sources={k:(ROOT/p).read_bytes() for k,p in extension.SOURCES.items()}
    outputs,receipt=extension.build(sources)
    rt.globals().core=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actions.lua").read_text())
    rt.globals().wirefactory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text())
    rt.execute("""
        registry=core();codec=wirefactory()
        X4ToolkitAutomationBundle={register_extension=function(x) descriptor=x end}
    """)
    rt.execute(next(iter(outputs.values())).decode())
    rt.execute("""
        ctx={ffi=require('ffi'),C=C,globals=g,Helper=H,clock=function()return age end,
            public_current=function()return cp(stamp)end,
            registry={register=function(spec)return registry.register(spec)end},
            encode=function(x)return codec.encode(x)end,on_dispose=function(fn) cleanup=fn end}
        extension_handle=descriptor.attach(ctx)
    """)
    class Bound:
        def __init__(self):self.link=c["old"];self.calls=[];self.lose=None;self.mutate=None;self.last_envelope=None
        def ask(self,verb,packet):
            self.calls.append(verb)
            q=rt.globals().codec.decode(packet)
            if verb=="action_prepare":result=rt.globals().registry.prepare(q)
            elif verb=="action_execute":result=rt.globals().registry.execute(q["id"],q["session"],q["digest"])
            else:result=rt.globals().registry.status(q["id"],q["session"],q["digest"])
            raw=rt.globals().codec.encode(result)
            if self.lose==verb:raise TimeoutError("synthetic lost wire reply")
            if self.mutate:raw=self.mutate(raw)
            return Reply(len(self.calls),"OK",raw)
    bound=Bound();(tmp/"automation").mkdir();client=AutomationClient(bound,tmp/"automation")
    (tmp/"port").mkdir()
    port=PersistencePort(client,tmp/"port",receipt["revision"],c["v"],c["a"])
    c["d"].port=port;c.update(port=port,client=client,bound=bound,revision=receipt["revision"],base=base)
    assert port.refresh()["status"]=="REFRESH_REQUESTED"
    c["v"].catalogue=port.catalogue
    return c

def check_full_wire_save_settle_load(tmp):
    c=setup(tmp)
    ticket=c["base"].load_requested(c)
    assert c["d"].record(ticket)["state"]=="LOAD_REQUESTED"
    assert c["rt"].globals().nc("save")==1 and c["rt"].globals().nc("load")==1
    assert list(c["port"].root.glob("*-native-grant.json"))
    assert list(c["client"].actions.iterdir())

@pytest.mark.parametrize("count",[0,1,10])
def check_complete_immutable_catalogue_pages(tmp,count):
    c=setup(tmp)
    rows=[{"filename":f"save_{i+1:03}","name":"Name"+str(i),"rawtime":i,
           "detail":"x"*350} for i in range(count)]
    c["rt"].globals().rows=c["to_lua"](rows)
    result=c["port"].catalogue()
    assert result["status"]=="OBSERVED" and result["count"]==count and result["rows"]==rows
    assert result["atomic"] is False

def check_oversized_row_refuses_without_partial_catalogue(tmp):
    c=setup(tmp);c["rt"].globals().rows=c["to_lua"]([{"filename":"save_001","name":"n","rawtime":1,"detail":"x"*4000}])
    with pytest.raises(PersistenceUnavailable):c["port"].catalogue()

def check_failed_catalogue_never_becomes_empty(tmp):
    c=setup(tmp);c["rt"].globals().ready=False
    result=c["port"].catalogue()
    assert result["status"]=="REFUSED" and "rows" not in result

def check_request_json_cannot_mint_authorization(tmp):
    c=setup(tmp)
    fake={"ticket":"1"*32,"capability":"f"*32,"context_sha256":"e"*64,
          "mode":"EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION","guard_qualified":False}
    before=len(c["bound"].calls)
    with pytest.raises(PersistenceUnavailable):c["port"]("execute_save",fake)
    assert len(c["bound"].calls)==before and c["rt"].globals().nc("save")==0

@pytest.mark.parametrize("phase",["prepare","execute","status"])
def check_generic_client_cannot_adopt_reserved_provider(tmp,phase):
    c=setup(tmp)
    req={"version":1,"id":"e"*32,"provider":"x4-persistence-v1","session":c["client"].identity()["session"],
         "action":"refresh","arguments":{},"binding":c["revision"]}
    if phase!="prepare":
        c["client"].prepare(req,_persistence_access=c["port"].access)
    before=len(c["bound"].calls)
    with pytest.raises(ActionRefused):
        if phase=="prepare":c["client"].prepare(req)
        else:getattr(c["client"],phase)(req["id"])
    assert len(c["bound"].calls)==before

@pytest.mark.parametrize("phase",["prepare","execute","status"])
def check_generic_cli_refuses_before_open(tmp,phase):
    c=setup(tmp)
    spec={"id":"f"*32,"provider":"x4-persistence-v1","action":"execute_save","arguments":{},"binding":c["revision"]}
    path=tmp/"request.json";path.write_text(json.dumps(spec))
    if phase!="prepare":(c["client"].pending/(spec["id"]+".json")).write_text(json.dumps(dict(spec,version=1,session="a"*32)))
    args=["--journal",str(c["client"].root),"--image","X4.exe","--profile-id","12345",
          "--build","b"*64,"--revision","c"*64,"--mode","resume",phase,str(path) if phase=="prepare" else spec["id"]]
    def forbidden(*a,**kw):raise AssertionError("pipe opened")
    assert cli.main(args,open_pipe=forbidden)==2

def check_retired_port_capability_refuses(tmp):
    c=setup(tmp);c["port"].close();before=len(c["bound"].calls)
    with pytest.raises(PersistenceUnavailable):c["port"].refresh()
    assert len(c["bound"].calls)==before

@pytest.mark.parametrize("field",["capture","index","count"])
def check_mixed_catalogue_pages_refuse(tmp,field):
    c=setup(tmp);c["rt"].globals().rows=c["to_lua"]([{"filename":"save_001","name":"n","rawtime":1}])
    def corrupt(raw):
        row=json.loads(raw);n=row.get("observations",{}).get("native_result",{})
        if n.get("status")=="CATALOGUE_PAGE":n[field]="f"*32 if field=="capture" else 9
        return json.dumps(row)
    c["bound"].mutate=corrupt
    with pytest.raises(PersistenceUnavailable):c["port"].catalogue()

def check_direct_provider_unknown_operation_and_duplicate_sdk(tmp):
    c=setup(tmp)
    c["rt"].execute("""
        local q={version=1,id=string.rep('e',32),session=stamp.session,provider='x4-persistence-v1',
          action='refresh',arguments={},binding=descriptor.revision,digest=string.rep('a',64)}
        assert(registry.prepare(q).status=='prepared')
        assert(registry.execute(q.id,q.session,q.digest).status=='completed')
        local before=nc('refresh')
        assert(registry.execute(q.id,q.session,q.digest).status=='completed')
        assert(nc('refresh')==before)
        q.id=string.rep('f',32);q.action='arbitrary'
        assert(not pcall(registry.prepare,q))
    """)

def check_actual_bundle_catalog_facade_and_cleanup(tmp):
    c=setup(tmp);rt=c["rt"]
    fixture=load(Path(__file__).with_name("test_livebundle.py"),"bundle_fixture")
    sources=fixture.synthetic()
    sources["bootstrap"]=b'''return function(d)
        return {session={current=function() return cp(stamp) end},encode=function(x)return codec.encode(x)end,
            register_provider=function(spec)return registry.register(spec)end,dispose=function()return true end}
        end'''
    payload,_=fixture.bundle.build(sources,"a"*64)
    rt.execute("X4ToolkitAutomationBundle=nil;registry=core()")
    rt.execute(payload.decode())
    extension=load(ROOT/"scripts/live_persistence_extension.py","extension_catalog")
    outputs,receipt=extension.build({k:(ROOT/v).read_bytes() for k,v in extension.SOURCES.items()})
    rt.execute(next(iter(outputs.values())).decode())
    rt.execute('''assembled=X4ToolkitAutomationBundle.new{ffi=require('ffi'),C=C,globals=g,Helper=H,
        clock=function()return age end,read=function()return 'OK','unused'end,read_verbs={probe=true}}
        ''')
    assert c["port"].refresh()["status"]=="REFRESH_REQUESTED"
    assert rt.eval("X4ToolkitAutomationBundle.dispose()") is True
    with pytest.raises(Exception):c["port"].refresh()

def check_extension_exact_sources_and_revision(tmp):
    extension=load(ROOT/"scripts/live_persistence_extension.py","extension_pins")
    sources={k:(ROOT/v).read_bytes() for k,v in extension.SOURCES.items()}
    _,before=extension.build(sources)
    for key in sources:
        changed=dict(sources);changed[key]+=b"\\n-- changed\\n"
        _,after=extension.build(changed)
        assert after["revision"]!=before["revision"]
    with pytest.raises(ValueError):extension.build({})

def check_file_drift_during_actual_sdk_prepare_stops_before_execute(tmp):
    c=setup(tmp);ticket=c["d"].reserve_save("1"*32,"save_001");original=c["client"].prepare
    def drift(request,**kwargs):
        result=original(request,**kwargs)
        if request["action"]=="execute_save":(c["save"]/"unexpected.xml").write_text("drift")
        return result
    c["client"].prepare=drift
    with pytest.raises(PersistenceUnavailable):c["d"].execute(ticket)
    assert c["rt"].globals().nc("save")==0 and c["d"].record(ticket)["state"]=="UNKNOWN"
    before=len(c["bound"].calls)
    with pytest.raises(PersistenceUnavailable):c["d"].execute(ticket)
    assert len(c["bound"].calls)==before

@pytest.mark.parametrize("mutation",["capture","pages_bool","count_bool","stamp"])
def check_catalogue_header_and_page_denominators_strict(tmp,mutation):
    c=setup(tmp);c["rt"].globals().rows=c["to_lua"]([{"filename":"save_001","name":"n","rawtime":1}])
    def corrupt(raw):
        row=json.loads(raw);value=row.get("observations",{}).get("native_result",{})
        if value.get("status")=="CATALOGUE_CAPTURED":
            if mutation=="capture":value["capture"]="f"*32
            elif mutation=="pages_bool":value["pages"]=True
            elif mutation=="stamp":value["header"]["stamp"]["world_token"]="other"
        elif value.get("status")=="CATALOGUE_PAGE" and mutation=="count_bool":value["count"]=True
        return json.dumps(row)
    c["bound"].mutate=corrupt
    with pytest.raises(PersistenceUnavailable):c["port"].catalogue()

@pytest.mark.parametrize("mutation",["none","unavailable","stamp","relative","wrong_root","profile_overlap"])
def check_production_constructor_binds_native_save_root(tmp,mutation):
    from x4validate._livepersistport import connect_persistence
    from x4validate._livepeerprofile import ProfilePeer
    c=setup(tmp)
    class SyntheticProfilePeer(ProfilePeer):
        def __init__(self):self.profile_id="12345"
        def __call__(self):return copy.deepcopy(c["state"]["identity"]["peer"])
    c["client"].bound.link.peer=SyntheticProfilePeer()
    paths={key:tmp/("new-"+key) for key in ("observer","port","dispatcher")}
    for folder in paths.values():folder.mkdir()
    if mutation=="profile_overlap":paths["port"]=c["save"]
    observation={"status":"OBSERVED","scope":"NATIVE_SAVE_FOLDER_PATH",
        "stamp":c["v"]._stamp(c["client"].identity()),"save_folder":str(c["save"])}
    if mutation=="unavailable":observation={"status":"UNAVAILABLE"}
    elif mutation=="stamp":observation["stamp"]["session"]="e"*32
    elif mutation=="relative":observation["save_folder"]="save"
    elif mutation=="wrong_root":observation["save_folder"]=str(tmp)
    c["client"].query=lambda verb:(Reply(1,"OK",json.dumps(observation)))
    if mutation=="none":
        dispatcher,port,verifier=connect_persistence(c["client"],c["v"].profile,paths,c["revision"],c["a"])
        assert verifier.save==c["save"] and dispatcher.port is port
        assert (paths["port"]/"native-path-binding.json").is_file()
    else:
        with pytest.raises(PersistenceUnavailable):connect_persistence(c["client"],c["v"].profile,paths,c["revision"],c["a"])
        assert not (paths["port"]/"native-path-binding.json").exists()

@pytest.mark.parametrize("mutation",["fault","status","missing_core","true_native_unknown"])
def check_sdk_outer_outcome_cannot_promote_retained_native_result(tmp,mutation):
    c=setup(tmp)
    def corrupt(raw):
        row=json.loads(raw);obs=row.get("observations",{})
        if "native_result" in obs:
            if mutation=="fault":
                row["status"]="unknown";obs["action_core"]["protocol_fault"]=True
            elif mutation=="status":row["status"]="unknown"
            elif mutation=="missing_core":obs.pop("action_core",None)
            else:
                row["status"]="unknown";obs["native_result"]={"status":"UNKNOWN","reason":"synthetic native uncertainty"}
        return json.dumps(row)
    c["bound"].mutate=corrupt
    if mutation=="true_native_unknown":
        assert c["port"].refresh()["status"]=="UNKNOWN"
    else:
        with pytest.raises(PersistenceUnavailable):c["port"].refresh()

@pytest.mark.parametrize("failure",["nil","throw","malformed","generation","clock","dispose"])
def check_lazy_provider_binding_and_sticky_loss(tmp,failure):
    from lupa.luajit21 import LuaRuntime
    rt=LuaRuntime(unpack_returned_tuples=True)
    rt.globals().factory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_persistenceprovider.lua").read_text())
    rt.globals().core=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actions.lua").read_text())
    rt.globals().wire=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text())
    rt.globals().failure=failure
    rt.execute("""
        registry=core(); codec=wire(); age=10; calls=0; reads=0; disposed=0
        available=false; throwing=false
        stamp={provider='x4-live-session-v1',revision=string.rep('b',64),
          session=string.rep('a',32),generation=string.rep('c',32),world_token='world'}
        deps={revision=string.rep('d',64),register=function(s) spec=s;return registry.register(s) end,
          current=function() reads=reads+1;if throwing then error('unavailable') end
            if available then return stamp end end,
          now=function()return age end,encode=codec.encode,
          gate={invoke=function()calls=calls+1;return {status='REFRESH_REQUESTED'} end,
                dispose=function()disposed=disposed+1;return true end}}
        api=factory(deps)
        assert(reads==0 and calls==0 and disposed==0)
        assert(spec.session()==nil and spec.session()==nil and calls==0)
        throwing=true;assert(spec.session()==nil);throwing=false
        available=true;stamp.world_token='';assert(spec.session()==nil)
        stamp.world_token='world'
        assert(spec.session()==stamp.session)
        q={version=1,id=string.rep('e',32),provider='x4-persistence-v1',session=stamp.session,
          binding=deps.revision,action='refresh',arguments={},digest=string.rep('f',64)}
        assert(registry.prepare(q).status=='prepared' and calls==0)
        assert(registry.execute(q.id,q.session,q.digest).status=='completed' and calls==1)
        if failure=='nil' then available=false
        elseif failure=='throw' then throwing=true
        elseif failure=='malformed' then stamp.world_token=''
        elseif failure=='generation' then stamp.generation=string.rep('f',32)
        elseif failure=='clock' then age=9
        elseif failure=='dispose' then assert(api.dispose()) end
        assert(spec.session()==nil)
        available=true;throwing=false;stamp.world_token='world'
        stamp.generation=string.rep('c',32);age=11
        assert(spec.session()==nil)
        assert(not pcall(spec.invoke,'prepare',q,function()error('unexpected callback')end))
        assert(calls==1)
        assert(api.dispose() and api.dispose() and disposed==1)
    """)

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            for i,arg in enumerate(marks[0].args[1]):out.append(pytest.param(name,[arg],id=name+"-"+str(i)))
        else:out.append(pytest.param(name,[],id=name))
    return out

@pytest.mark.parametrize("name,args",_cases())
def test_actual_provider_port(name,args,tmp_path):
    run=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args),str(tmp_path)],
        capture_output=True,text=True,timeout=45,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert run.returncode==0,run.stdout+run.stderr
    assert run.stdout.strip().endswith("CONTROL_PASSED"),run.stdout+run.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](Path(sys.argv[3]),*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

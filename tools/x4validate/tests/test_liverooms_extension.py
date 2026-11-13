"""Actual generated loader, native room adapter, SDK and wire; synthetic native/menu only."""
from pathlib import Path
import hashlib,importlib.util,json,os,subprocess,sys
from unittest.mock import patch
import pytest
from test_liverooms import build
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("room_extension",ROOT/"scripts/live_rooms_extension.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def runtime():
    r=build();files,receipt=g.prepare()
    r.globals().sdk=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actions.lua").read_text())
    r.execute("""
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
        registrations={};lua_conversions=0;property_reads=0;registry=sdk()
        g={Menus=menus,ConvertIDTo64Bit=function(v)
                if type(v)=='string' and v:match('^LUA:')then
                    local n=v:match('^LUA:(%d+)ULL$')
                    if wrongroundtrip then return u('999')end
                    if inexactroundtrip then return 9007199254740992 end
                    if saferoundtrip then return tonumber(n)end
                    return u(n)
                end
                return d.convert_id(v)
            end,getElapsedTime=d.now,
            ConvertStringToLuaID=function(value)
                lua_conversions=lua_conversions+1
                assert(type(value)=='string' and value:match('ULL$'))
                if nulllua then return nil end
                if zero_lua then return u('0')end
                return 'LUA:'..value
            end,
            GetComponentData=function(value,key)
                property_reads=property_reads+1
                assert(type(value)=='string' and value:match('^LUA:') and key=='islocked')
                return d.component_data(u(value:match('^LUA:(%d+)ULL$')),key)
            end}
    """)
    r.execute(files["ui/live_rooms_extension.lua"].decode())
    r.execute("""
        attached=descriptor.attach{globals=g,ffi=ffi,C=C,public_current=d.current,encode=wire.encode,
            on_dispose=function(fn)registrations[#registrations+1]=fn end,registry=registry}
        seq=0
        function request(action,args)
            seq=seq+1
            req={version=1,id=string.format('%032x',seq),digest=string.format('%064x',seq),
                session=stamp.session,provider='x4-rooms-v1',binding=descriptor.revision,action=action,arguments=args or {}}
        end
        function prep()return registry.prepare(req)end
        function run()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
        function targets_capture()
            request('targets');prep();result=run();capture_id=req.id
        end
        function transport_prepare()
            targets_capture();request('transport',{capture=capture_id,index=2});return prep()
        end
    """)
    return r,receipt

def check_factory_exact_sources_and_candidate_scope():
    files,receipt=g.prepare()
    assert set(files)=={"ui/live_rooms_extension.lua"} and not receipt["runtime_verified"]
    assert not receipt["menu_opening_supported"] and not receipt["arrival_verified"]
    for p,h in receipt["source_sha256"].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
    assert hashlib.sha256(files["ui/live_rooms_extension.lua"]).hexdigest()==receipt["output_sha256"]["ui/live_rooms_extension.lua"]

@pytest.mark.parametrize("target",["adapter","provider","self"])
def check_source_drift_is_refused(target):
    read=Path.read_bytes;n=0;selected=Path(g.__file__) if target=="self" else ROOT/g.PATHS[target]
    def changed(p):
        nonlocal n
        b=read(p)
        if p==selected:
            n+=1
            if n>1:return b+b"\n"
        return b
    with patch.object(Path,"read_bytes",changed),pytest.raises(AssertionError):g.prepare()

def check_constructor_does_not_query_or_transport():
    r,_=runtime()
    assert r.eval("counts+fills+transports+lua_conversions+property_reads")==0
    assert r.eval("#registrations")==2
    r.execute("request('targets');prep()")
    assert r.eval("counts+fills+transports")==0

def check_targets_location_and_immutable_pages_use_actual_wire():
    r,_=runtime();r.execute("targets_capture()")
    assert r.eval("result.status")=="completed"
    v=json.loads(r.eval("result.observations.payload_json"))
    assert v["count"]==2 and v["capture"]==r.eval("capture_id") and not v["transport_pages_complete"]
    assert "targets" not in v and r.eval("lua_conversions")==r.eval("property_reads")>0
    r.execute("request('page',{capture=capture_id,offset=0});prep();result=run()")
    p=json.loads(r.eval("result.observations.payload_json"))
    assert p["total"]==2 and p["next_offset"]==2 and p["end_of_retained"]
    assert p["rows"][1]["room"]=="20" and p["rows"][1]["locked"] is False
    assert len(r.eval("wire.encode({payload=wire.encode(result)})").encode())<=4096
    r.execute("request('location');prep();result=run()")
    assert json.loads(r.eval("result.observations.payload_json"))["location"]["player"]=="18446744073709551615"
    assert r.eval("transports")==0

def check_native_transport_is_once_only_and_never_arrival_complete():
    r,_=runtime();assert r.eval("transport_prepare().status")=="prepared"
    assert r.eval("transports")==0
    assert r.eval("run().status")=="running" and r.eval("transports")==1
    for _ in range(3):assert r.eval("status().status")=="running"
    r.execute("run()")
    assert r.eval("transports")==1 and r.eval("status().observations.arrival_verified") is False
    assert r.eval("status().observations.unresolved_transport") is True

def check_unknown_native_exception_is_not_replayed():
    r,_=runtime();r.execute("transport_prepare();transport_error=true")
    assert r.eval("run().status")=="unknown"
    r.execute("run();status()")
    assert r.eval("transports")==1 and r.eval("status().observations.action_attempted_or_uncertain") is True

@pytest.mark.parametrize("change",["stamp.generation=string.rep('f',32)","clock=26","targets[2].locked=true",
    "playerroom=u('99')","m.shown=false","targets[2].room='21'"])
def check_stale_transport_refuses_before_native_dispatch(change):
    r,_=runtime();r.execute("transport_prepare();"+change)
    assert r.eval("run().status") in ("refused","unknown")
    assert r.eval("transports")==0

def check_unresolved_transport_blocks_later_ticket():
    r,_=runtime();r.execute("transport_prepare();run();request('transport',{capture=capture_id,index=2});result=prep()")
    assert r.eval("result.status") not in ("prepared","completed") and r.eval("transports")==1

def check_successor_slot_observation_allows_new_preparation_but_not_arrival_claim():
    r,_=runtime();r.execute("""transport_prepare();run()
        playerroom=u('20');m.param[3]=u('200');m.param[4]='bridge'
        m.transportercomponent=u('200');m.transporterconnection='bridge';targets_capture()
        request('transport',{capture=capture_id,index=1});result=prep()""")
    assert r.eval("result.status")=="prepared"
    assert r.eval("result.observations.arrival_verified") is False and r.eval("transports")==1

@pytest.mark.parametrize("change",["m.shown=false","nulllua=true","zero_lua=true","lock_unknown=true","badcount=129"])
def check_missing_menu_or_native_state_is_unknown_not_empty(change):
    r,_=runtime();r.execute(change+";targets_capture()")
    assert r.eval("result.status")=="unknown" and r.eval("transports")==0

def check_unowned_and_superseded_capture_refuse():
    r,_=runtime();r.execute("request('transport',{capture=string.rep('9',32),index=2})")
    assert r.eval("prep().status") not in ("prepared","completed") and r.eval("transports")==0
    r.execute("targets_capture();old=capture_id;targets_capture();request('page',{capture=old,offset=0})")
    assert r.eval("prep().status") not in ("prepared","completed")

def check_target_pages_do_not_silently_truncate():
    r,_=runtime();r.execute("""for i=3,14 do targets[i]={component=tostring(i+200),connection='r'..i,
        room=tostring(i+20),locked=false,zone=false,name=string.rep('x',100)} end;targets_capture()""")
    assert r.eval("result.status")=="completed"
    offset=0;total=0
    while offset<14:
        r.globals().offset=offset;r.execute("request('page',{capture=capture_id,offset=offset});prep();result=run()")
        assert r.eval("result.status")=="completed"
        p=json.loads(r.eval("result.observations.payload_json"))
        assert p["offset"]==offset and p["next_offset"]>offset
        assert len(r.eval("wire.encode({payload=wire.encode(result)})").encode())<=4096
        total+=len(p["rows"]);offset=p["next_offset"]
    assert total==14 and p["end_of_retained"]

def check_retired_provider_cannot_dispatch():
    r,_=runtime();r.execute("transport_prepare();assert(attached.dispose()==true)")
    assert r.eval("run().status")=="unknown" and r.eval("transports")==0
    assert r.eval("attached.dispose()") is True

def check_empty_native_target_list_remains_explicit_zero():
    r,_=runtime();r.execute("targets={};targets_capture()")
    assert r.eval("result.status")=="completed"
    assert json.loads(r.eval("result.observations.payload_json"))["count"]==0
    r.execute("request('page',{capture=capture_id,offset=0});prep();result=run()")
    v=json.loads(r.eval("result.observations.payload_json"))
    assert v["total"]==v["offset"]==v["next_offset"]==0 and v["end_of_retained"]

def check_wrong_revision_or_extra_arguments_refuse_before_native_reads():
    for change in ("req.binding=string.rep('9',64)","req.arguments.extra=true"):
        r,_=runtime();r.execute("request('targets');"+change)
        assert r.eval("prep().status") not in ("prepared","completed")
        assert r.eval("counts+fills+transports")==0

@pytest.mark.parametrize("change",["wrongroundtrip=true","inexactroundtrip=true"])
def check_lua_id_roundtrip_mismatch_refuses_before_property_read(change):
    r,_=runtime();r.execute(change+";targets_capture()")
    assert r.eval("result.status")=="unknown" and r.eval("property_reads")==0
    assert r.eval("transports")==0

def check_safe_integer_roundtrip_is_exact_and_supported():
    r,_=runtime();r.execute("saferoundtrip=true;targets_capture()")
    assert r.eval("result.status")=="completed" and r.eval("property_reads")>0

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                out.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:out.append(pytest.param(name,[],id=name))
    return out
@pytest.mark.parametrize("name,args",_cases())
def test_actual_luajit_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

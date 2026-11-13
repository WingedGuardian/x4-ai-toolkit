"""Actual generated native-surface loader/SDK with synthetic MD; no engine claim."""
from pathlib import Path
import importlib.util
import hashlib
import json
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("surface_ext",ROOT/"scripts/live_surface_extension.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def make(profile="interactive",real=False):
    files,receipt=g.prepare(profile)
    r=LuaRuntime(unpack_returned_tuples=True)
    for key in ("actions","actionwire","automation","mdchannel"):
        r.globals()[key]=r.execute((ROOT/("mods/x4_toolkit_helper/ui/live_"+key+".lua")).read_text())
    r.globals().profile=profile;r.globals().real_channel=real
    r.globals().caprevision=receipt["capacity_profile"]["revision"]
    scope={"__file__":str(ROOT/g.PATHS["capacity"])}
    exec(compile((ROOT/g.PATHS["capacity"]).read_bytes(),scope["__file__"],"exec"),scope)
    r.globals().resolver=r.execute(scope["lua_source"]())
    r.globals().surface_factory=r.execute((ROOT/g.PATHS["provider"]).read_text())
    r.execute("""
        ffi=require('ffi');ship=ffi.cast('uint64_t',-1);sector=ffi.new('uint64_t',13)
        age=0;sends=0;reads=0;clears=0;disposals=0;queue={};hooks={}
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        wire=actionwire();registry=actions(profile,resolver)
        original_register=registry.register
        registry.register=function(s)provider_spec=s;return original_register(s)end
        function current()if unavailable then return nil end;return stamp end
        function resolve(token,kind)
            assert(token=='issued' and kind=='ship')
            if resolve_advance then age=11 end
            if resolve_drift then stamp.generation=string.rep('f',32)end
            if revoked then return nil end;return ship
        end
        function channel(cfg)
            assert(cfg.screen=='X4ToolkitSurface')
            if real_channel then
                return mdchannel{config=cfg,ffi=ffi,C={GetPlayerID=function()return ship end},
                    public_current=current,Helper={addDelayedOneTimeCallbackOnUpdate=function(fn)scheduled=fn end},
                    globals={RegisterEvent=function(name,fn)assert(name==cfg.event);drain=fn end,
                        GetNPCBlackboard=function(id,key)assert(id==ship and key==cfg.queue);reads=reads+1;return queue end,
                        SetNPCBlackboard=function(id,key,value)assert(id==ship and key==cfg.queue and value==nil);clears=clears+1;queue={}end,
                        AddUITriggeredEvent=function(screen,phase,body)
                            assert(screen==cfg.screen and cfg.controls[phase]);sends=sends+1;sent=body;sentphase=phase
                        end,
                        ConvertStringTo64Bit=function(text)if text==tostring(ship)then return ship end;assert(text==tostring(sector));return sector end,
                        ConvertIDTo64Bit=function(v)return v end,getElapsedTime=function()return age end}}
            end
            madechannel={public_current=current,now=function()return age end,
                arm_tick=function(fn)tick=fn end,subscribe=function(fn)drain=fn end,
                read=function()reads=reads+1;if advance_read then age=11 end;return queue end,
                clear=function()clears=clears+1;queue={}end,
                component_id=function(v)assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0);return tostring(v)end,
                send=function(phase,body)sends=sends+1;sent=body;sentphase=phase;if sendfail then error('send uncertain')end end,
                dispose=function()disposals=disposals+1;return not disposefail end}
            return madechannel
        end
        X4ToolkitAutomationBundle={register_extension=function(value)descriptor=value end}
    """)
    r.execute(files["ui/live_surface_extension.lua"].decode())
    r.execute("""
        attached=descriptor.attach{ffi=ffi,capacity_profile=profile,capacity_revision=caprevision,
            encode=function(v)local out=wire.encode(v);if encode_advance then age=11 end;return out end,
            registry=registry,resolve_issued=resolve,create_md_channel=channel,
            on_dispose=function(fn)hooks[#hooks+1]=fn end}
        req={version=1,id=string.rep('d',32),digest=string.rep('e',64),session=stamp.session,
            provider='x4-native-surface-v1',binding=descriptor.revision,action='surface_sample',
            arguments={ship='issued',face='xplus'}}
        function prepare()return registry.prepare(req)end
        function execute()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
        function row()
            local v={kind='SURFACE',revision=sent.revision,id=sent.id,digest=sent.digest,call=sent.call,phase=sentphase,
                session=sent.session,generation=sent.generation,world_token=sent.world_token,public_revision=sent.public_revision,
                age=3,status='observed',reason='synthetic native sample'}
            if sentphase=='capacity'then
                v.kind='SURFACE_CAPACITY';v.reason='READ_ONLY_CAPACITY_SAMPLE';v.md_used=3
                v.md_limit=profile=='interactive' and128 or512;v.md_queued=0;v.md_queue_limit=64
                v.capacity_profile=profile;v.capacity_revision=caprevision
            else
                v.target=ship;v.target_exists=true;v.sector=sector;v.physics_ready=true;v.sample_observed=true
                v.surface_found=true;v.face=req.arguments.face;v.origin={200,0,0};v.surface_position={50,0,0}
                v.surface_frame={1,0,0,0,1,0,0,0,1};v.host_pose={0,0,0,0,0,1,1,0,0,0,1,0}
                v.bbox_centre={0,0,0};v.dimensions={100,100,100}
            end
            return v
        end
        function deliver(v)queue={v};drain()end
        function start()prepare();execute()end
        function packet()
            local a={};for k,v in pairs(req.arguments)do
                a[#a+1]=k..'=s'..v:gsub('.',function(x)return string.format('%02x',x:byte())end)
            end;table.sort(a)
            return table.concat({'1',req.id,req.session,req.provider,req.action,req.binding,req.digest,table.concat(a,',')},'|')
        end
        router=automation{session={current=current},wire=wire,registry=registry,
            read=function()error('unexpected read')end,read_verbs={},age=function()return age end,
            build=string.rep('f',64),ui_authorize=function()return false end,capacity_profile=profile,capacity_resolver=resolver}
        function bound(verb)return router.bound{stamp.session,stamp.generation,stamp.revision,stamp.world_token,
            string.rep('f',64),verb,packet()}end
    """.replace("and128","and 128").replace("or512","or 512"))
    return r,receipt

@pytest.mark.parametrize("profile",["interactive","campaign_v1"])
def test_real_sdk_one_sample_is_not_containment_and_terminal_never_resends(profile):
    r,unused=make(profile)
    assert r.eval("sends+reads+clears")==0
    assert r.eval("prepare().status")=="prepared" and r.eval("sends")==0
    r.execute("execute()")
    assert r.eval("sends")==1 and r.eval("tostring(sent.target)")=="18446744073709551615ULL"
    r.execute("deliver(row())")
    out=r.eval("status()")
    assert out.status=="completed"
    assert out.observations.inside_hull=="UNKNOWN" and out.observations.collision_safe is False
    assert out.observations.runtime_case_pass is False
    assert out.observations.snapshot.surface_found is True
    assert out.observations.snapshot.origin_scope=="REQUEST_DERIVED_FROM_NATIVE_BBOX"
    r.execute("status();execute()")
    assert r.eval("sends")==1

@pytest.mark.parametrize("profile",["interactive","campaign_v1"])
def test_real_channel_router_codec_and_typed_uint64_roundtrip(profile):
    r,unused=make(profile,True)
    r.execute("stamp.world_token=string.rep('w',512)")
    assert json.loads(r.eval("bound('action_prepare')"))['status']=="OK"
    assert json.loads(r.eval("bound('action_execute')"))['status']=="OK"
    r.execute("deliver(row())")
    out=json.loads(r.eval("bound('action_status')"))
    assert out['status']=="OK" and json.loads(out['payload'])['status']=='completed'
    assert len(out['payload'])<4096
    assert r.eval("sends")==1

def test_null_native_hit_is_not_absence_or_case_pass():
    r,unused=make();r.execute("start();v=row();v.surface_found=false;v.surface_position={};v.surface_frame={};deliver(v)")
    out=r.eval("status()")
    assert out.status=="completed" and out.observations.snapshot.surface_found is False
    assert out.observations.inside_hull=="UNKNOWN" and out.observations.complete_collision_coverage is False

@pytest.mark.parametrize("change",[
    "v.extra=true","v.physics_ready=false","v.sample_observed=false","v.surface_found='true'",
    "v.target=sector","v.face='yplus'","v.surface_position={}",
    "v.surface_frame[1]=0","v.surface_frame[4]=1","v.host_pose[6]=0",
    "v.surface_position[1]=0/0","v.dimensions[1]=-1","v.dimensions[1]=100001",
    "v.age=-1","v.call=string.rep('f',32)","v.generation=string.rep('f',32)",
    "v.surface_position={1,2,3,bad=4}","v.status='done'",
])
def test_tampered_native_sample_never_becomes_completed(change):
    r,unused=make();r.execute("start();v=row();"+change+";deliver(v)")
    assert r.eval("status().status")!="completed"
    assert r.eval("sends")==1

@pytest.mark.parametrize("change",[
    "req.arguments.face='anything'","req.arguments.extra=true","req.arguments.ship='foreign'",
    "req.arguments.face=nil","req.binding=string.rep('f',64)","revoked=true",
])
def test_invalid_request_has_zero_native_queries(change):
    r,unused=make();r.execute(change)
    assert r.eval("prepare().status")=="refused"
    assert r.eval("sends")==0

@pytest.mark.parametrize("change",["unavailable=true","stamp.generation=string.rep('f',32)","age=-1"])
def test_identity_loss_stays_faulted_after_apparent_restore(change):
    r,unused=make();r.execute("prepare();"+change)
    r.eval("execute()")
    r.execute("unavailable=false;stamp.generation=string.rep('c',32);age=0")
    assert r.eval("provider_spec.session()") is None
    assert r.eval("sends")==0

@pytest.mark.parametrize("change",["resolve_advance=true","resolve_drift=true"])
def test_resolver_drift_cannot_query(change):
    r,unused=make();r.execute("prepare();"+change)
    r.eval("execute()")
    assert r.eval("sends")==0

def test_identity_drift_during_failed_resolve_is_sticky():
    r,unused=make();r.execute("resolve_drift=true;revoked=true")
    r.eval("prepare()")
    r.execute("resolve_drift=false;revoked=false;stamp.generation=string.rep('c',32)")
    assert r.eval("provider_spec.session()") is None and r.eval("sends")==0

@pytest.mark.parametrize("change",["advance_read=true","encode_advance=true"])
def test_delayed_reply_or_encoding_cannot_complete(change):
    r,unused=make();r.execute("start();"+change+";deliver(row())")
    assert r.eval("status().status")!="completed"

def test_timeout_and_late_reply_never_replay():
    r,unused=make();r.execute("start();age=11;tick()")
    assert r.eval("status().status")!="completed"
    r.execute("deliver(row());execute()")
    assert r.eval("sends")==1

def test_ambiguous_send_consumed_once():
    r,unused=make();r.execute("prepare();sendfail=true;execute();sendfail=false;execute()")
    assert r.eval("sends")==1 and r.eval("status().status")!="completed"

@pytest.mark.parametrize("profile",["interactive","campaign_v1"])
def test_capacity_has_no_native_sample_row_and_cached_status(profile):
    r,unused=make(profile);r.execute("req.action='capacity_observe';req.arguments={};start();deliver(row())")
    out=r.eval("status()")
    assert out.status=="completed" and out.observations.snapshot.md_used==3
    assert out.observations.snapshot.md_limit==(128 if profile=="interactive" else 512)
    r.execute("status();execute()");assert r.eval("sends")==1

def test_false_profile_is_not_implicit_interactive():
    with pytest.raises(ValueError):g.prepare(False)
    r,unused=make()
    with pytest.raises(Exception,match="fixed profile"):
        r.execute("""surface_factory{ffi=ffi,channel=madechannel,resolve_issued=resolve,encode=wire.encode,
            capacity_profile=false,capacity_resolver=resolver,revision=descriptor.revision,md_revision=string.rep('a',64)}""")


def test_foreign_pending_request_is_settled_when_another_request_observes_identity_loss():
    r,unused=make()
    r.execute('''
        callback_count=0
        provider_spec.invoke('prepare',req,function(value)first_prepared=value end)
        provider_spec.invoke('execute',req,function(value)first_result=value;callback_count=callback_count+1 end)
        assert(first_result==nil);unavailable=true
        other={};for k,v in pairs(req)do other[k]=v end;other.id=string.rep('9',32)
        provider_spec.invoke('prepare',other,function(value)other_result=value end)
    ''')
    assert r.eval('other_result.status')=='refused'
    assert r.eval('first_result.status')=='unknown' and r.eval('callback_count')==1
    r.execute("unavailable=false;stamp.generation=string.rep('c',32)")
    assert r.eval('provider_spec.session()') is None
    assert r.eval('sends')==1 and r.eval('callback_count')==1

def test_generated_md_has_real_query_and_no_gameplay_effects():
    files,receipt=g.prepare("campaign_v1");tree=E.fromstring(files["md/X4ToolkitSurface.xml"])
    queries=tree.findall(".//find_object_surface");assert len(queries)==6
    assert not tree.xpath(".//warp|.//destroy_object|.//create_ship|.//disable_collision_response")
    for query in queries:
        assert query.get("object")=="$Target" and query.get("space")=="$Target"
        assert dict(query.find("position").attrib)==dict(value="$OriginValue",object="$Target",space="$Target")
    guards=[node.get("value") for node in tree.findall(".//do_if")]
    assert any(value.startswith("$Target.exists and $Target.isclass.ship") and "$Target.isphysicsready" in value
        and "$Target.width le 100km" in value for value in guards)
    for cue in tree.xpath("//cue[conditions/event_ui_triggered]"):
        assert cue.find("actions")[0].tag=="debug_text"
    assert receipt["runtime_verified"] is False
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in receipt["source_sha256"].items())


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_exact_local_history_boundary_retains_preparations_without_native_queries(profile):
    r,unused=make(profile);limit=128 if profile=='interactive' else 512
    r.globals().expected_limit=limit
    r.execute('''
        for i=1,expected_limit do
            req.id=string.format('%032x',i)
            assert(prepare().status=='prepared')
        end
        req.id=string.format('%032x',expected_limit+1)
        overflow=prepare()
    ''')
    assert r.eval('overflow.status')=='refused' and r.eval('sends')==0


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_dense_actual_router_payload_preserves_numeric_sample(profile):
    r,unused=make(profile,True)
    r.execute("stamp.world_token=string.rep('w',512)")
    r.eval("bound('action_prepare')");r.eval("bound('action_execute')")
    r.execute('''
        v=row();v.age=9007199254740980;v.reason=string.rep('r',256)
        v.origin={9999999.00000012,-9999999.00000012,9999999.00000012}
        v.surface_position={9999998.00000012,-9999998.00000012,9999998.00000012}
        v.bbox_centre={9999997.00000012,-9999997.00000012,9999997.00000012}
        v.host_pose={9999996.00000012,-9999996.00000012,9999996.00000012,
            0.3333333333333333,0.6666666666666666,0.6666666666666666,
            0.6666666666666666,0.3333333333333333,-0.6666666666666666,
            -0.6666666666666666,0.6666666666666666,-0.3333333333333333}
        v.surface_frame={};for i=4,12 do v.surface_frame[i-3]=v.host_pose[i]end
        v.dimensions={99999.00000012,99999.00000012,99999.00000012}
        deliver(v)
    ''')
    outer=json.loads(r.eval("bound('action_status')"));out=json.loads(outer['payload'])
    assert out['status']=='completed' and len(outer['payload'])<=4096
    assert out['observations']['snapshot']['surface_position'][0]==9999998.00000012
    assert out['observations']['snapshot']['target']=='18446744073709551615ULL'


def test_fault_delivery_exception_cannot_strand_the_other_request():
    r,unused=make()
    r.execute('''
        callback_count=0
        provider_spec.invoke('prepare',req,function()end)
        provider_spec.invoke('execute',req,function()callback_count=callback_count+1;error('consumer unavailable')end)
        unavailable=true
        other={};for k,v in pairs(req)do other[k]=v end;other.id=string.rep('9',32)
        provider_spec.invoke('prepare',other,function(v)other_result=v end)
    ''')
    assert r.eval('other_result.status')=='refused' and r.eval('callback_count')==1

"""Actual generated loader/SDK/wire with synthetic MD packets, never engine proof."""
from pathlib import Path
import hashlib,importlib.util,json,os,subprocess,sys
from unittest.mock import patch
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('ship_placement_ext',ROOT/'scripts/live_placement_extension.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def make(profile='interactive',real_channel=False):
    files,receipt=g.prepare(profile);r=LuaRuntime(unpack_returned_tuples=True)
    for name in ('actions','actionwire','automation','mdchannel'):
        r.globals()[name]=r.execute((ROOT/f'mods/x4_toolkit_helper/ui/live_{name}.lua').read_text())
    r.globals().profile=profile;r.globals().capacity_revision=receipt['capacity_profile']['revision']
    r.globals().real_channel=real_channel
    capacity_scope={'__file__':str(ROOT/g.PATHS['capacity'])}
    exec(compile((ROOT/g.PATHS['capacity']).read_bytes(),capacity_scope['__file__'],'exec'),capacity_scope)
    r.globals().resolver=r.execute(capacity_scope['lua_source']())
    r.execute('''
        ffi=require('ffi');ship=ffi.cast('uint64_t',-1);sector=ffi.new('uint64_t',13)
        age=0;sends=0;reads=0;clears=0;disposed=0;nativecalls=0;hooks={};queue={}
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        wire=actionwire();registry=actions(profile,resolver)
        function resolve(token,kind)
            assert(token=='issued' and kind=='ship')
            if resolve_advance then age=11 end
            if resolve_drift then stamp.generation=string.rep('f',32)end
            if revoked then return nil end;return ship
        end
        function current()if unavailable then return nil end;return stamp end
        function channel(cfg)
            local primary=cfg.screen=='X4ToolkitShipPlacement'
            assert(primary or cfg.screen=='X4ToolkitPlacement')
            if primary and real_channel then
                return mdchannel{config=cfg,ffi=ffi,C={GetPlayerID=function()return ship end},public_current=current,
                    Helper={addDelayedOneTimeCallbackOnUpdate=function(fn)scheduled=fn end},
                    globals={RegisterEvent=function(name,fn)assert(name==cfg.event);drain=fn end,
                        GetNPCBlackboard=function(id,key)assert(id==ship and key==cfg.queue);reads=reads+1;return queue end,
                        SetNPCBlackboard=function(id,key,value)assert(id==ship and key==cfg.queue and value==nil);clears=clears+1;queue={}end,
                        AddUITriggeredEvent=function(screen,phase,body)
                            assert(screen==cfg.screen and cfg.controls[phase]);sends=sends+1;sent=body;sentphase=phase
                            if phase~='capacity'then assert(type(body.target)=='cdata' and body.target==ship)end
                        end,
                        ConvertStringTo64Bit=function(text)if text==tostring(ship)then return ship end;assert(text==tostring(sector));return sector end,
                        ConvertIDTo64Bit=function(v)return v end,getElapsedTime=function()return age end}}
            end
            return {public_current=current,now=function()return age end,
                arm_tick=function(f)if primary then tick=f end end,
                subscribe=function(f)if primary then drain=f end end,
                read=function()assert(primary);reads=reads+1;if advance_read then age=11 end;return queue end,
                clear=function()assert(primary);clears=clears+1;queue={} end,
                component_id=function(v)assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0);return tostring(v)end,
                send=function(phase,b)
                    assert(primary and cfg.controls[phase]);sends=sends+1;sent=b;sentphase=phase
                    if phase~='capacity'then assert(type(b.target)=='cdata' and b.target==ship)end
                    if sendfail then error('ambiguous event send')end
                end,
                dispose=function()disposed=disposed+1;return not disposefail end}
        end
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
    ''')
    r.execute(files['ui/live_placement_extension.lua'].decode())
    r.execute('''
        attached=descriptor.attach{ffi=ffi,capacity_profile=profile,capacity_revision=capacity_revision,
            encode=function(value)local out=wire.encode(value);if encode_advance then age=11 end;return out end,
            resolve_issued=resolve,registry=registry,
            create_md_channel=channel,on_dispose=function(fn)hooks[#hooks+1]=fn end}
        req={version=1,id=string.rep('d',32),digest=string.rep('e',64),session=stamp.session,
            provider='x4-diagnostic-shipplacement-v1',binding=descriptor.revision,action='diagnostic_place_ship',
            arguments={ship='issued',x_m=10,y_m=20,z_m=30,yaw_degrees=0,pitch_degrees=0,roll_degrees=0}}
        function row(status)
            local base={kind='SHIP_PLACEMENT',revision=sent.revision,id=sent.id,digest=sent.digest,call=sent.call,
                phase=sentphase,session=sent.session,generation=sent.generation,world_token=sent.world_token,
                public_revision=sent.public_revision,age=5,status=status,reason='synthetic native sample'}
            if sentphase=='capacity'then
                base.kind='SHIP_PLACEMENT_CAPACITY';base.status='observed';base.reason='READ_ONLY_CAPACITY_SAMPLE'
                base.md_used=3;base.md_limit=profile=='interactive' and 128 or 512;base.md_busy=false
                base.md_queued=0;base.md_queue_limit=64;base.capacity_profile=profile;base.capacity_revision=capacity_revision
            else
                base.target=ship;base.target_exists=true;base.sector=sector;base.pose_observed=true;base.eligible=true
                base.pose={0,0,0,0,0,1,1,0,0,0,1,0};base.desired={10,20,30,0,0,1,1,0,0,0,1,0}
                base.speed_mps=0;base.position_match=false;base.orientation_match=false
                base.dispatched=status=='dispatched';base.consumed=base.dispatched;base.busy=base.dispatched
            end
            return base
        end
        function deliver(v)queue={v};drain()end
        function prep()registry.prepare(req);deliver(row('prepared'))end
        function execute()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
        function dispatched()prep();execute();deliver(row('dispatched'))end
        function packet()
            local a={};for k,v in pairs(req.arguments)do
                a[#a+1]=k..'='..(type(v)=='number' and 'n'..string.format('%.17g',v) or
                    's'..v:gsub('.',function(x)return string.format('%02x',x:byte())end))
            end;table.sort(a)
            return table.concat({'1',req.id,req.session,req.provider,req.action,req.binding,req.digest,table.concat(a,',')},'|')
        end
        router=automation{session={current=current},wire=wire,registry=registry,read=function()error('unexpected read')end,
            read_verbs={},age=function()return age end,build=string.rep('f',64),ui_authorize=function()return false end,
            capacity_profile=profile,capacity_resolver=resolver}
        function bound(verb)
            return router.bound{stamp.session,stamp.generation,stamp.revision,stamp.world_token,string.rep('f',64),verb,packet()}
        end
    ''')
    return r,receipt

def check_generated_profiles_and_legacy_source_preservation():
    legacy_spec=importlib.util.spec_from_file_location('legacy_md',ROOT/g.PATHS['md'])
    legacy=importlib.util.module_from_spec(legacy_spec);legacy_spec.loader.exec_module(legacy)
    old,unused=legacy.prepare()
    for profile,limit in [('interactive',128),('campaign_v1',512)]:
        files,r=g.prepare(profile)
        assert set(files)=={'md/X4ToolkitPlacement.xml','md/X4ToolkitShipPlacement.xml','ui/live_placement_extension.lua'}
        assert files['md/X4ToolkitPlacement.xml']==old['md/X4ToolkitPlacement.xml']
        assert r['ship_md']['limits']['records']==limit and r['ship_md']['limits']['calls']==8*limit
        assert r['runtime_verified'] is False
        assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in r['source_sha256'].items())

def check_native_shape_consume_then_warp_and_status_only_release():
    files,_=g.prepare();root=E.fromstring(files['md/X4ToolkitShipPlacement.xml'])
    warp=root.xpath('//warp');assert len(warp)==1
    assert dict(warp[0].attrib)=={'object':'$Target','sector':'$Record.$sector'}
    assert dict(warp[0][0].attrib)=={'value':'$Record.$wanted_position'}
    assert dict(warp[0][1].attrib)=={'value':'$Record.$wanted_rotation'}
    assert root.xpath('//cue[conditions/event_ui_triggered]/@name')==['Prepare','Execute','Status','Capacity']
    for cue in root.xpath('//cue[conditions/event_ui_triggered]'):
        assert cue.find('actions')[0].tag=='debug_text'
    assert root.xpath('//cue[@name="State"]/actions/*')[0].tag=='debug_text'
    assert root.xpath('//cue[@name="Invalidate"]/actions/*')[0].get('text')=="'X4TKSHIPPLACE TRIGGER invalidated'"
    execute=E.tostring(root.xpath('//cue[@name="Execute"]')[0]).decode()
    assert execute.index('name="$Record.$consumed"')<execute.index('<warp')
    assert execute.index('name="md.X4ToolkitShipPlacement.State.$Busy"')<execute.index('<warp')
    for expected in ('$Target.isoperational','not $Target.dock','not $Target.assigneddock','not $Target.isinternallystored',
                     '$Sector == $Record.$sector','player.controlled == $Record.$controlled','le 100km',
                     '$Target.relativeposition.{$Sector}','$Target.relativerotation.{$Sector}',
                     '$Rotation.forward.distanceto.{$Record.$rotation.forward}',
                     '$R.$yaw_degrees * 1deg'):
        assert expected in execute
    assert 'name="md.X4ToolkitShipPlacement.State.$Busy" exact="null"' not in execute
    status=E.tostring(root.xpath('//cue[@name="Status"]')[0]).decode()
    assert 'player.age gt $Record.$dispatch_age' in status and '$PositionMatch and $OrientationMatch' in status
    capacity=root.xpath('//cue[@name="Capacity"]')[0]
    assert not capacity.xpath('.//warp|.//create_position')
    assert not [x for x in capacity.xpath('.//set_value|.//append_to_list') if '.$Records' in x.get('name','') or '.$Busy' in x.get('name','')]

@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_actual_sdk_typed_target_and_later_basis_match(profile):
    r,_=make(profile);assert r.eval('sends+reads+clears+nativecalls')==0
    r.execute('dispatched()');assert r.eval('sends')==2
    assert r.eval('tostring(sent.target)')=='18446744073709551615ULL'
    assert r.eval('status().status')=='running'
    r.execute("x=row('dispatched');x.age=6;x.pose=x.desired;x.position_match=true;x.orientation_match=true;x.busy=false;deliver(x)")
    out=r.eval('status()');assert out.status=='completed'
    assert out.observations.collision_safe is False and out.observations.flight_verified is False
    r.eval('execute()');assert r.eval('sends')==3
    # Complete outer transport remains within the real codec's string budget.
    r.globals().out=out
    r.execute("assert(#wire.encode({payload=wire.encode(out)})<4096)")

@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_capacity_cost_and_cached_terminal_status(profile):
    r,_=make(profile);r.execute("req.action='capacity_observe';req.arguments={}")
    assert r.eval('registry.prepare(req).status')=='prepared' and r.eval('sends')==0
    r.eval('execute()');assert r.eval('sends')==1
    r.execute("deliver(row('observed'))")
    out=r.eval('status()');assert out.status=='completed'
    assert out.observations.capacity.history_used==1 and out.observations.capacity.calls_used==1
    assert out.observations.snapshot.md_used==3 and out.observations.snapshot.queue_count_scope=='BEFORE_OWN_REPLY_APPEND'
    r.eval('status()');r.eval('execute()');assert r.eval('sends')==1

@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_real_channel_and_complete_router_wire_at_large_values(profile):
    r,_=make(profile,real_channel=True)
    r.execute("stamp.world_token=string.rep('w',512);req.arguments.x_m=9999999.00000012;"
        "req.arguments.y_m=-9999999.00000012;req.arguments.z_m=9999999.00000012;"
        "req.arguments.yaw_degrees=359.99999999999994;req.arguments.pitch_degrees=-359.99999999999994;"
        "req.arguments.roll_degrees=359.99999999999994")
    # This exercises the actual scalar request decoder, SDK, native channel,
    # receipt encoder and complete bound envelope, not a hand-built thin row.
    initial=json.loads(r.eval("bound('action_prepare')"));assert initial['status']=='OK'
    r.execute("x=row('prepared');x.age=9007199254740980;x.reason=string.rep('r',256);"
        "x.pose={9999998.00000012,-9999998.00000012,9999998.00000012,0.3333333333333333,0.6666666666666666,0.6666666666666666,"
        "0.6666666666666666,0.3333333333333333,-0.6666666666666666,-0.6666666666666666,0.6666666666666666,-0.3333333333333333};"
        "x.desired={req.arguments.x_m,req.arguments.y_m,req.arguments.z_m};for i=4,12 do x.desired[i]=x.pose[i]end;"
        "prepared_pose=x.pose;desired_pose=x.desired;deliver(x)")
    prepared=json.loads(r.eval("bound('action_execute')"));assert prepared['status']=='OK'
    r.execute("x=row('dispatched');x.age=9007199254740981;x.pose=prepared_pose;x.desired=desired_pose;deliver(x)")
    pending=json.loads(r.eval("bound('action_status')"));assert json.loads(pending['payload'])['status']=='running'
    r.execute("x=row('dispatched');x.age=9007199254740982;x.pose=desired_pose;x.desired=desired_pose;"
        "x.position_match=true;x.orientation_match=true;x.busy=false;x.reason=string.rep('r',256);deliver(x)")
    outer=json.loads(r.eval("bound('action_status')"));inner=json.loads(outer['payload'])
    assert outer['stamp']['world_token']=='w'*512 and inner['status']=='completed'
    assert len(outer['payload'].encode())<=4096
    snapshot=inner['observations']['snapshot']
    assert snapshot['target']=='18446744073709551615ULL' and snapshot['pose']==snapshot['desired']
    assert snapshot['call']==f'{3:032x}' and snapshot['phase']=='status'
    assert not {'id','digest','session','generation','world_token','public_revision'}&snapshot.keys()
    assert inner['id']=='d'*32 and inner['session']==outer['stamp']['session']
    assert r.eval('sends')==3 and r.eval('reads')==3

@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_capacity_complete_router_and_real_channel(profile):
    r,_=make(profile,real_channel=True);r.execute("stamp.world_token=string.rep('w',512);req.action='capacity_observe';req.arguments={}")
    assert json.loads(json.loads(r.eval("bound('action_prepare')"))['payload'])['status']=='prepared'
    r.eval("bound('action_execute')");r.execute("deliver(row('observed'))")
    outer=json.loads(r.eval("bound('action_status')"));out=json.loads(outer['payload'])
    assert out['status']=='completed' and out['observations']['snapshot']['md_used']==3
    assert out['observations']['capacity_observation_cost']==dict(sdk_records=1,lua_records=1,native_records=0,native_calls=1)
    assert len(outer['payload'].encode())<=4096 and r.eval('sends')==1

@pytest.mark.parametrize('change',[
    'req.arguments.x_m=10000001','req.arguments.yaw_degrees=361','req.arguments.pitch_degrees=false',
    'req.arguments.z_m=nil','req.arguments.eval="warp"','req.arguments.ship="foreign"',
    "req.action='capacity_observe'", "req.binding=string.rep('f',64)",
])
def check_invalid_arguments_never_send(change):
    r,_=make();r.execute(change)
    out=r.eval('registry.prepare(req)');assert out.status in ('unknown','refused') and r.eval('sends')==0

@pytest.mark.parametrize('change',[
    "x.call=string.rep('f',32)","x.digest=string.rep('f',64)","x.phase='execute'",
    "x.generation=string.rep('f',32)","x.revision=string.rep('f',64)",
    'x.extra=1','x.pose[12]=nil','x.pose[4]=2','x.target=sector',
    'x.desired[1]=11','x.dispatched=true','x.eligible=false','x.pose_observed=false',
])
def check_prepare_corruption_settles_unknown_without_action(change):
    r,_=make();r.execute('registry.prepare(req)');r.execute("x=row('prepared');"+change+';deliver(x)')
    assert r.eval('status().status')=='unknown' and r.eval('sends')==1

@pytest.mark.parametrize('change',[
    'x.pose[1]=500', 'x.pose[4]=0.5','x.eligible=false','x.speed_mps=2',
    'x.age=5','x.sector=ship','x.dispatched=false','x.consumed=false',
])
def check_false_achievement_never_completes(change):
    r,_=make();r.execute("dispatched();status();x=row('dispatched');x.age=6;x.pose=x.desired;"
        "x.pose={10,20,30,0,0,1,1,0,0,0,1,0};x.position_match=true;x.orientation_match=true;"+change+';deliver(x)')
    assert r.eval('status().status')=='unknown' and r.eval('sends')==3

def check_duplicate_batch_cannot_synchronously_execute():
    r,_=make();r.execute("registry.prepare(req);x=row('prepared');queue={x,x};drain()")
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1

@pytest.mark.parametrize('change',['age=11;tick()', "advance_read=true;deliver(row('prepared'))",
                                    "stamp.generation=string.rep('f',32);deliver(row('prepared'))"])
def check_timeout_or_world_drift_is_sticky(change):
    r,_=make();r.execute('registry.prepare(req);'+change)
    assert r.eval('status().status')=='unknown' and r.eval('sends')==1

def check_ambiguous_send_never_replays():
    r,_=make();r.execute('prep();sendfail=true')
    assert r.eval('execute().status')=='unknown'
    r.execute('sendfail=false');r.eval('execute()');r.eval('status()')
    assert r.eval('sends')==2

def check_prepared_argument_mutation_and_revoked_target_refuse():
    r,_=make();r.execute('prep();revoked=true')
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1
    r,_=make();r.execute('prep();req.arguments.x_m=99')
    with pytest.raises(Exception):r.eval('registry.prepare(req)')
    assert r.eval('sends')==1

def check_native_unavailable_target_is_not_pose_or_success():
    r,_=make();r.execute("registry.prepare(req);x=row('refused');x.target_exists=false;x.target=0;x.sector=0;"
        "x.pose_observed=false;x.pose={};x.eligible=false;deliver(x)")
    assert r.eval('status().status')=='refused'

def check_removed_target_after_dispatch_is_unknown_with_retained_effect_evidence():
    r,_=make();r.execute("dispatched();status();x=row('refused');x.age=6;x.target_exists=false;x.target=0;x.sector=0;"
        "x.pose_observed=false;x.pose={};x.eligible=false;x.dispatched=true;x.consumed=true;x.busy=true;deliver(x)")
    out=r.eval('status()')
    assert out.status=='unknown' and out.observations.snapshot.dispatched is True
    assert out.observations.snapshot.busy is True and out.observations.snapshot.target_exists is False
    before=r.eval('sends');r.eval('execute()');assert r.eval('sends')==before

def check_encoding_past_response_deadline_cannot_prepare():
    r,_=make();r.execute("registry.prepare(req);encode_advance=true;deliver(row('prepared'))")
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1

def check_final_resolver_expiration_prevents_execute_send():
    r,_=make();r.execute('prep();resolve_advance=true')
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1

def check_resolver_world_drift_remains_faulted_after_old_stamp_returns():
    r,_=make();r.execute('prep();resolve_drift=true')
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1
    r.execute("resolve_drift=false;stamp.generation=string.rep('c',32);tick()")
    r.eval('status()');r.eval('execute()')
    assert r.eval('sends')==1

def check_before_first_world_unavailable_can_bind_once_then_drift_is_sticky():
    r,_=make();r.execute('unavailable=true;tick();tick()');assert r.eval('sends+reads+clears')==0
    r.execute('unavailable=false;prep()');assert r.eval('sends')==1
    r.execute('unavailable=true;tick();unavailable=false')
    assert r.eval('execute().status')=='unknown' and r.eval('sends')==1

@pytest.mark.parametrize('change',['x.md_used=129','x.md_queued=64','x.md_limit=512',
    "x.capacity_revision=string.rep('f',64)","x.capacity_profile='campaign_v1'"])
def check_capacity_malformed_native_counts_never_complete(change):
    r,_=make();r.execute("req.action='capacity_observe';req.arguments={};registry.prepare(req);execute();x=row('observed');"+change+';deliver(x)')
    assert r.eval('status().status')=='unknown' and r.eval('sends')==1

@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_actual_lifetime_record_limit_never_evicts(profile):
    r,_=make(profile);limit=128 if profile=='interactive' else 512
    r.globals().limit=limit
    r.execute("req.action='capacity_observe';req.arguments={};for i=1,limit do "
        "req.id=string.format('%032x',i);assert(registry.prepare(req).status=='prepared')end;"
        "req.id=string.format('%032x',limit+1);assert(registry.prepare(req).status=='unknown')")
    assert r.eval('sends')==0
    # Last valid record remains executable; the refused extra record does not
    # replace an old one or silently free its lifetime capacity.
    r.execute("req.id=string.format('%032x',limit);execute();deliver(row('observed'))")
    out=r.eval('status()');assert out.status=='completed' and out.observations.capacity.history_used==limit

def check_disposal_failure_remains_failed_and_abandoned_drain_inert():
    r,_=make();r.execute('registry.prepare(req);disposefail=true')
    assert r.eval('attached.dispose()') is False and r.eval('attached.dispose()') is False
    r.execute("queue={row('prepared')};drain()")
    assert r.eval('reads+clears')==0

def check_profile_mismatch_refuses_before_channel_creation():
    r,_=make();r.execute('count=0')
    ok=r.eval("function()return pcall(descriptor.attach,{capacity_profile='other',create_md_channel=function()count=count+1 end})end")()
    assert ok[0] is False and r.eval('count')==0

@pytest.mark.parametrize('target',['ship_md','ship_provider','capacity'])
def check_factory_rechecks_consumed_sources(target):
    original=Path.read_bytes;selected=ROOT/g.PATHS[target];reads=0
    def changed(path):
        nonlocal reads
        raw=original(path)
        if path==selected:
            reads+=1
            if reads>1:return raw+b'\n'
        return raw
    with patch.object(Path,'read_bytes',changed),pytest.raises(AssertionError):g.prepare()

# MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0
# (KB 2026-11-10; ROR handover defect (b)). MDNUM re-encodes every boolean of a
# synthetic reply the way the engine delivers it.
MDNUM = """
function mdnum(v)
    if v==true then return 1 elseif v==false then return 0 end
    if type(v)=='table' and getmetatable(v)==nil then
        local o={};for k,x in pairs(v) do o[k]=mdnum(x) end;return o
    end
    return v
end
plain_deliver=deliver;function deliver(v) return plain_deliver(mdnum(v)) end
"""


def check_md_numeric_booleans_dispatch_and_basis_match():
    r,_=make();r.execute(MDNUM);r.execute('dispatched()')
    assert r.eval('status().status')=='running'
    r.execute("x=row('dispatched');x.age=6;x.pose=x.desired;x.position_match=true;x.orientation_match=true;x.busy=false;deliver(x)")
    assert r.eval('status().status')=='completed'


def check_md_numeric_busy_flag_in_capacity_sample():
    r,_=make();r.execute(MDNUM);r.execute("req.action='capacity_observe';req.arguments={}")
    r.eval('registry.prepare(req)');r.eval('execute()');r.execute("deliver(row('observed'))")
    out=r.eval('status()');assert out.status=='completed'
    assert out.observations.snapshot.md_busy is False

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if name.startswith('check_') and callable(fn):
            marks=[m for m in getattr(fn,'pytestmark',[]) if m.name=='parametrize']
            if marks:
                assert len(marks)==1
                for i,args in enumerate(marks[0].args[1]):out.append(pytest.param(name,[args],id=f'{name}-{i}'))
            else:out.append(pytest.param(name,[],id=name))
    return out

@pytest.mark.parametrize('name,args',_cases())
def test_control(name,args):
    result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=25,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='CONTROL_PASSED',result.stdout+result.stderr

if __name__=='__main__':
    assert sys.argv[1].startswith('check_');globals()[sys.argv[1]](*json.loads(sys.argv[2]));print('CONTROL_PASSED')

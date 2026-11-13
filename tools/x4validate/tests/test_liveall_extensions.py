"""All enabled real public loaders in the actual bundle; no game/native execution.

Only native boundaries are fake. No module factory or extension is substituted.
The additional descriptor is synthetic public test code, not private ROR content.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from lxml import etree as E

ROOT=Path(__file__).resolve().parents[3]
NAMES={'placement','roomentry','tradertext','rooms','topology','conversation','persistence','capture','orbit','surface'}
FIRST_ACTION={
    'x4-diagnostic-roomentry-v1':'diagnostic_enter_room',
    'x4-diagnostic-shipplacement-v1':'capacity_observe',
    'x4-native-surface-v1':'capacity_observe',
    'x4-diagnostic-placement-v1':'diagnostic_place_player','x4-trader-text-v1':'trader.observe',
    'x4-rooms-v1':'location','x4-topology-observer-v1':'capture','x4-conversation-observer-v1':'snapshot',
    'x4-comm-menu-v1':'inspect','x4-persistence-v1':'observe','x4-screen-capture-v1':'capture',
    'x4-orbit-camera-v1':'camera_observe'}
# Conversation.begin and Topology.begin subscribe lazily, not during attachment.
# No action is dispatched here, so their runtime subscriptions stay unqualified.
EVENTS={'X4ToolkitSession.Replies','X4ToolkitSession.Invalidated','X4ToolkitPlacement.Replies','X4ToolkitSurface.Replies',
    'X4ToolkitShipPlacement.Replies','X4ToolkitCapture.Replies','X4ToolkitOrbit.Replies','X4ToolkitRoomEntry.Replies'}


def sha(b):return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope='module',params=['interactive','campaign_v1'])
def packet(request):
    path=ROOT/'scripts/prepare_live_helper.py'
    spec=importlib.util.spec_from_file_location('all_ten_helper',path)
    p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
    assert set(p.EXTENSIONS)==NAMES,'exact public extension registry required'
    sources={k:(ROOT/v).read_bytes()for k,v in p.BUNDLE.SOURCES.items()}
    tracked={path,*(ROOT/v for v in p.BUNDLE.SOURCES.values()),*p.PINS,
             ROOT/'mods/x4_toolkit_helper/content.xml',ROOT/'mods/x4_toolkit_helper/ui.xml'}
    for name,(factory,_,_)in p.EXTENSIONS.items():
        mod=p._load(factory,{})
        tracked.add(ROOT/'scripts'/(factory+'.py'))
        tracked.update(ROOT/v for v in (mod.SOURCES if name=='persistence' else mod.PATHS).values())
    before={v:sha(v.read_bytes())for v in tracked}
    files,receipt=p.build((ROOT/'mods/x4_toolkit_helper/content.xml').read_bytes(),
        (ROOT/'mods/x4_toolkit_helper/ui.xml').read_bytes(),sources,extensions=tuple(p.EXTENSIONS),
        capacity_profile=request.param)
    assert {v:sha(v.read_bytes())for v in tracked}==before
    assert p.BUNDLE.verify(files['ui/live_automation_bundle.lua'])==receipt['bundle']['build']
    assert receipt['outputs']=={v:sha(b)for v,b in files.items()}
    assert set(receipt['extensions'])==NAMES
    assert receipt['bundle']['capacity']['profile']==request.param
    loaders=[node.get('name')for node in E.fromstring(files['ui.xml']).findall('environment/file')]
    wanted=['ui/live_automation_bundle.lua',*(v[1]for v in p.EXTENSIONS.values())]
    assert loaders[:len(wanted)]==wanted and loaders[len(wanted)]=='ui/live_query.lua'
    expected={}
    for r in receipt['extensions'].values():
        providers=r.get('providers',{r['provider']:r['revision']})
        assert not(set(expected)&set(providers));expected.update(providers)
    assert set(expected)==set(FIRST_ACTION) and len(expected)==12
    placement=receipt['extensions']['placement']
    assert placement['capacity_profile']['name']==request.param
    assert placement['ship_md']['limits']['records']==(128 if request.param=='interactive' else 512)
    assert placement['providers']=={key:placement['revision']for key in
        ('x4-diagnostic-placement-v1','x4-diagnostic-shipplacement-v1')}
    assert set(placement['output_sha256'])=={'ui/live_placement_extension.lua','md/X4ToolkitPlacement.xml','md/X4ToolkitShipPlacement.xml'}
    yield dict(files=files,receipt=receipt,loaders=wanted,expected=expected,pins=before)
    assert {v:sha(v.read_bytes())for v in tracked}==before,'consumed source changed during qualification'


def runtime(packet,*,ninth=None):
    lj=pytest.importorskip('lupa.luajit21',reason='actual LuaJIT FFI required')
    r=lj.LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        ffi=require('ffi');ffi.cdef('typedef uint64_t UniverseID;')
        native_calls=0;clock_calls=0;events={};scheduled={};fixture_disposed=0
        local function forbidden() native_calls=native_calls+1;error('unexpected native game call') end
        local function clock() clock_calls=clock_calls+1;return 50 end
        held={ffi.cast('uint64_t (*)(void)',function() native_calls=native_calls+1;return 0 end),
              ffi.cast('double (*)(void)',function() native_calls=native_calls+1;return 0 end)}
        C=setmetatable({GetPlayerID=held[1],GetCurrentGameTime=held[2]},
            {__index=function(t,k) rawset(t,k,forbidden);return forbidden end})
        globals={Menus={},getElapsedTime=clock}
        for _,name in ipairs({'GetCellContent','OpenMenu','ConvertIDTo64Bit','IsValidWidgetElement',
            'IsButtonActive','IsValidComponent','GetComponentData','ConvertStringToLuaID','SetOrderParam',
            'GetOrderParams','IsDockingPossible','GetPlayerMoney','GetOwnLicences','HasLicence','ConvertStringTo64Bit',
            'GetActiveGuidanceMissionComponent','GetAutoPilotTarget','GetPlayerActivity',
            'GetUIRelation','GetWareData','GetMacroData','GetLocalMousePosition',
            'GetSaveList','IsSavingPossible','OnlineIsOnlineModeActive','SaveGame','LoadGame'}) do globals[name]=forbidden end
        local function register(name,fn)
            assert(type(name)=='string' and type(fn)=='function','native event contract')
            events[#events+1]={name=name,callback=fn}
        end
        deps={ffi=ffi,C=C,globals=globals,Helper={convertComponentIDs=forbidden},
            native={RegisterEvent=register,GetNPCBlackboard=forbidden,SetNPCBlackboard=forbidden,
                AddUITriggeredEvent=forbidden,ConvertStringTo64Bit=forbidden,getElapsedTime=clock},
            resolve_issued=forbidden,clock=clock,read=forbidden,read_verbs={probe=true}}
        deps.Helper.addDelayedOneTimeCallbackOnUpdate=function(fn,one,when)
            assert(type(fn)=='function' and one==false and type(when)=='number','native scheduler contract')
            scheduled[#scheduled+1]=fn -- stored, NEVER fired while a live owner exists
        end
        function late_callbacks()
            local before=native_calls
            for _,event in ipairs(events) do event.callback() end
            for _,fn in ipairs(scheduled) do fn() end
            assert(native_calls==before,'retired callbacks performed a native call')
        end
        function fixture_descriptor(mode)
            return {id='public-synthetic-fixture',revision=string.rep('b',64),
                providers={['public-synthetic-fixture-v1']=string.rep('c',64)},attach=function(ctx)
                    local retired=false
                    local function close()
                        if retired then return mode~='bad_cleanup' end
                        retired=true;fixture_disposed=fixture_disposed+1
                        return mode~='bad_cleanup'
                    end
                    ctx.on_dispose(close)
                    register('SyntheticFixture.Replies',function() if not retired then forbidden() end end)
                    ctx.registry.register{id='public-synthetic-fixture-v1',revision=string.rep('c',64),
                        actions={observe=true},session=ctx.public_current,
                        invoke=function() forbidden() end,dispose=close}
                    if mode=='attach_error' then error('controlled ninth attach failure') end
                    if mode=='bad_handle' then return {} end
                    return {dispose=close}
                end}
        end
    """)
    for path in packet['loaders']:r.execute(packet['files'][path].decode())
    assert r.globals().native_calls==0 and r.globals().clock_calls==0
    if ninth is not None:r.globals().X4ToolkitAutomationBundle.register_extension(r.globals().fixture_descriptor(ninth))
    return r


def registered(r,provider,action,revision,index):
    # Deliberately unready public session: this proves registry identity, not
    # native action readiness. No prepared SDK request is ever executed.
    req=r.table_from(dict(version=1,id=format(index,'032x'),digest='a'*64,session='b'*32,
        provider=provider,action=action,binding=revision,arguments=r.table()))
    result=r.globals().instance.registry.prepare(req)
    assert result['status']=='unknown'
    assert result['observations']['action_core']['provider_revision']==revision
    assert r.globals().native_calls==0


def test_actual_ten_loaders_constructor_and_exact_provider_revisions(packet):
    r=runtime(packet);r.execute('instance=X4ToolkitAutomationBundle.new(deps)')
    assert r.globals().native_calls==0 and r.globals().clock_calls==0
    assert [e['name']for e in r.globals().events.values()]==list(dict.fromkeys(e['name']for e in r.globals().events.values()))
    assert {e['name']for e in r.globals().events.values()}==EVENTS
    for i,(provider,rev)in enumerate(packet['expected'].items(),1):registered(r,provider,FIRST_ACTION[provider],rev,i)
    registered(r,'x4-native-v1','camera.view',packet['receipt']['bundle']['build'],len(packet['expected'])+1)
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    r.globals().late_callbacks();assert r.globals().native_calls==0
    print('ALL_TEN_SOURCE_IDENTITY='+json.dumps(dict(bundle=packet['receipt']['bundle']['build'],
        generators=packet['receipt']['generators'],providers=packet['expected']),sort_keys=True))


def test_actual_ten_plus_a_synthetic_descriptor_and_retired_callbacks(packet):
    r=runtime(packet,ninth='normal');r.execute('instance=X4ToolkitAutomationBundle.new(deps)')
    assert r.globals().native_calls==0 and r.globals().clock_calls==0
    for i,(provider,rev)in enumerate(packet['expected'].items(),1):registered(r,provider,FIRST_ACTION[provider],rev,i)
    registered(r,'public-synthetic-fixture-v1','observe','c'*64,len(packet['expected'])+1)
    assert {e['name']for e in r.globals().events.values()}==EVENTS|{'SyntheticFixture.Replies'}
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    r.globals().late_callbacks();assert r.globals().fixture_disposed==1
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps)')[0] is False


def test_same_vm_reconstruction_uses_real_shared_ffi_and_retires_old_owner(packet):
    r=runtime(packet,ninth='normal');r.execute('old=X4ToolkitAutomationBundle.new(deps);old_events={} for i,e in ipairs(events)do old_events[i]=e end')
    r.execute('instance=X4ToolkitAutomationBundle.new(deps)')
    assert r.globals().fixture_disposed==1 and r.globals().native_calls==0
    r.execute('for _,e in ipairs(old_events)do e.callback()end')
    assert r.globals().native_calls==0
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    r.globals().late_callbacks();assert r.globals().fixture_disposed==2


@pytest.mark.parametrize('failure',['attach_error','bad_handle'])
def test_ninth_attach_failure_retires_all_actual_preceding_loaders(packet,failure):
    r=runtime(packet,ninth=failure)
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps)')[0] is False
    assert r.globals().fixture_disposed==1
    before=len(list(r.globals().events.values()));r.globals().late_callbacks()
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps)')[0] is False
    assert len(list(r.globals().events.values()))==before and r.globals().native_calls==0


def test_ninth_cleanup_failure_is_sticky_with_all_actual_loaders(packet):
    r=runtime(packet,ninth='bad_cleanup');r.execute('instance=X4ToolkitAutomationBundle.new(deps)')
    assert r.eval('pcall(X4ToolkitAutomationBundle.dispose)')[0] is False
    before=len(list(r.globals().events.values()));r.globals().late_callbacks()
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps)')[0] is False
    assert len(list(r.globals().events.values()))==before and r.globals().native_calls==0

"""Actual Lua SDK/wrapper controls; native stubs, plus real camera/menu/order adapters."""
import ast
from pathlib import Path

import pytest

luajit = pytest.importorskip("lupa.luajit21")
UI = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui"


def source(name):
    return (UI / ("live_" + name + ".lua")).read_text(encoding="utf-8")


def constant(test, name):
    """Reuse only the literal owned native-stub fixture, not its test runner."""
    tree = ast.parse(Path(__file__).with_name(test).read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))


def make_runtime():
    rt = luajit.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("""
        REV=string.rep('a',64); NONCE=string.rep('b',32); GEN=string.rep('c',32)
        time=10; writes=0; reads=0; prepares=0; native_status=nil; queue_rows={}
        stamp={provider='x4-live-session-v1',revision=string.rep('d',64),
            session=NONCE,generation=GEN,world_token=NONCE}
        function pre(...)
            prepares=prepares+1;last_prepare={...}
            return {status='PREPARED',ticket='private-'..prepares,ship='42',action_attempted=false}
        end
        function dispatch(ticket)
            writes=writes+1;last_ticket=ticket
            if throw_write then error('native outcome unavailable') end
            if change_write then stamp.generation=string.rep('e',32) end
            return {status=native_status or 'DISPATCHED_UNVERIFIED',action_attempted=true,order_id='123'}
        end
        menu_adapter={prepare=pre,prepare_open=pre,execute=dispatch,
            observe=function() reads=reads+1;return {status='OBSERVED',container='7'} end}
        order_adapter={prepare=pre,prepare_cancel=pre,execute=dispatch,execute_cancel=dispatch,
            observe=function(token) reads=reads+1;return {status='OBSERVED',ship='42',
                count=#queue_rows,rows=queue_rows} end}
        camera_adapter={prepare=pre,execute=dispatch,restore_cockpit=dispatch,
            observe=function() reads=reads+1;return {status=camera_status or 'PENDING'} end}
        transaction_adapter={capture=function(token)
            reads=reads+1;return {status='complete',enumeration_complete=true,atomic=false,rows={}} end}
        options_adapter={prepare_open=pre,prepare_submenu=pre,prepare_select=pre,execute=dispatch,
            observe=function(page) reads=reads+1;last_page=page;return {status='OBSERVED'} end}
        navigation_adapter={prepare=pre,execute=dispatch,
            observe=function(ship,target) reads=reads+1;return {status='OBSERVED',docked_at_requested_container=true} end}
        deps={revision=REV,public_current=function() return stamp end,
            resolve_issued=function(token,kind) assert(token=='station' and kind=='station');return '7' end,
            now=function() return time end, menu=menu_adapter,orders=order_adapter,
            camera=camera_adapter,transactions=transaction_adapter,options=options_adapter,navigation=navigation_adapter,timecontrol=navigation_adapter}
        function req(action,args,serial)
            return {version=1,id=string.rep(serial or '1',32),session=NONCE,provider='x4-native-v1',
                action=action,arguments=args,binding=REV,digest=string.rep(serial or '1',64)}
        end
        function setup()
            adapter=factory(deps);registry=core();registry.register(adapter.spec)
        end
        function prepare(action,args,serial)
            request=req(action,args,serial);return registry.prepare(request)
        end
        function execute()
            return registry.execute(request.id,request.session,request.digest)
        end
        function status()
            return registry.status(request.id,request.session,request.digest)
        end
    """)
    rt.globals().factory = rt.execute(source("nativeproviders"))
    rt.globals().core = rt.execute(source("actions"))
    rt.globals().camera_factory = rt.execute(source("camera"))
    rt.execute("setup()")
    return rt

@pytest.fixture
def runtime():
    return make_runtime()


@pytest.mark.parametrize("action,args", [
    ("menu.open", "{mode='purchase',station='station'}"),
    ("menu.open", "{mode='upgrade',station='station',ship='ship'}"),
    ("menu.select", "{mode='purchase',station='station',field='ship',option='macro'}"),
    ("menu.add", "{mode='purchase',station='station'}"),
    ("menu.confirm", "{mode='purchase',station='station'}"),
    ("order.submit", "{kind='dock',ship='ship',target='target',policy='require_empty'}"),
    ("order.submit", "{kind='attack',ship='ship',target='target',policy='require_empty'}"),
    ("order.submit", "{kind='move',ship='ship',target='target',policy='require_empty',x=-1,y=2,z=3}"),
    ("camera.view", "{target='target'}"),
    ("options.open", "{}"),
    ("time.start", "{ship='ship'}"), ("time.stop", "{ship='ship'}"),
    ("navigation.guidance", "{ship='ship',target='target'}"),
    ("navigation.target", "{ship='ship',target='target'}"),
    ("navigation.autopilot_start", "{ship='ship',target='target'}"),
    ("navigation.dock_request", "{ship='ship',target='target'}"),
    ("navigation.quick_dock", "{ship='ship',target='target'}"),
    ("options.submenu", "{page='main',id='simple_menu_extension_options',submenu='simple_menu_extension_options'}"),
    ("options.select", "{page='example_options',row=2,col=3,widget=30,option='1',text='High',value_type='number',value='5'}"),
])
def test_closed_actions_prepare_without_mutation(runtime, action, args):
    r = runtime.execute(f"return prepare('{action}',{args})")
    assert r.status == "prepared"
    assert r.observations.native_action_attempted is False
    assert runtime.eval("writes") == 0
    assert runtime.eval("status().status") == "prepared"
    assert runtime.eval("prepares") == 1


@pytest.mark.parametrize('mode',['start','stop'])
def test_actual_time_control_through_registry_does_not_claim_factor(mode):
    from test_livetimecontrol import make
    rt=make("active=true;activity='seta'" if mode=='stop' else '')
    rt.globals().provider_factory=rt.execute(source('nativeproviders'))
    rt.globals().action_core=rt.execute(source('actions'))
    rt.globals().mode=mode
    rt.execute('''
        local function noop()end
        local dummy={prepare=noop,prepare_open=noop,prepare_submenu=noop,prepare_select=noop,
            execute=noop,observe=noop,prepare_cancel=noop,execute_cancel=noop,restore_cockpit=noop,capture=noop}
        provider=provider_factory{revision=string.rep('f',64),public_current=function()return stamp end,
            resolve_issued=deps.resolve_issued,now=function()return time end,menu=dummy,orders=dummy,
            camera=dummy,transactions=dummy,options=dummy,navigation=dummy,timecontrol=api}
        registry=action_core();registry.register(provider.spec)
        request={version=1,id=string.rep('1',32),session=stamp.session,provider='x4-native-v1',
            action='time.'..mode,arguments={ship='ship'},binding=string.rep('f',64),digest=string.rep('2',64)}
    ''')
    assert rt.eval('registry.prepare(request).status')=='prepared'
    assert rt.eval('writes')==0
    assert rt.eval('registry.execute(request.id,request.session,request.digest).status')=='running'
    out=rt.eval('registry.status(request.id,request.session,request.digest)')
    assert out.status=='running' and out.observations.completion_qualified is False
    assert out.observations.scope=='NATIVE_SETA_STATE_ONLY'
    rt.eval('registry.execute(request.id,request.session,request.digest)')
    assert rt.eval('writes')==1


@pytest.mark.parametrize("action",["guidance","target","autopilot_start","dock_request","quick_dock"])
def test_actual_player_navigation_through_registry_never_claims_arrival(action):
    from test_liveplayernavigation import make
    rt=make("guidance=200ULL")
    rt.globals().provider_factory=rt.execute(source("nativeproviders"))
    rt.globals().action_core=rt.execute(source("actions"))
    rt.globals().navigation_action="navigation."+action
    rt.execute("""
        local function noop()end
        local dummy={prepare=noop,prepare_open=noop,prepare_submenu=noop,prepare_select=noop,
            execute=noop,observe=noop,prepare_cancel=noop,execute_cancel=noop,restore_cockpit=noop,capture=noop}
        provider=provider_factory{revision=string.rep('f',64),public_current=function()return stamp end,
            resolve_issued=deps.resolve_issued,now=function()return time end,menu=dummy,orders=dummy,
            camera=dummy,transactions=dummy,options=dummy,navigation=api,timecontrol=dummy}
        registry=action_core();registry.register(provider.spec)
        request={version=1,id=string.rep('1',32),session=stamp.session,provider='x4-native-v1',
            action=navigation_action,arguments={ship='ship',target='target'},
            binding=string.rep('f',64),digest=string.rep('2',64)}
    """)
    assert rt.eval("registry.prepare(request).status")=="prepared"
    assert rt.eval("registry.execute(request.id,request.session,request.digest).status")=="running"
    assert rt.eval("writes")==1
    out=rt.eval("registry.status(request.id,request.session,request.digest)")
    assert out.status=="running" and out.observations.arrival_verified is False
    assert out.observations.completion_qualified is False
    rt.eval("registry.execute(request.id,request.session,request.digest)")
    assert rt.eval("writes")==1


@pytest.mark.parametrize("action,args", [
    ("menu.open", "{mode='upgrade',station='station'}"),
    ("menu.open", "{mode='purchase',station='station',ship='ship'}"),
    ("menu.select", "{mode='purchase',station='station',field='arbitrary',option='x'}"),
    ("menu.add", "{mode='purchase',station='station',option='x'}"),
    ("menu.confirm", "{mode='purchase',station='station',eval='x'}"),
    ("order.submit", "{kind='dock',ship='ship',target='target',policy='append'}"),
    ("order.submit", "{kind='dock',ship='ship',target='target',policy='require_empty',x=1}"),
    ("order.submit", "{kind='move',ship='ship',target='target',policy='require_empty',x=0/0,y=0,z=0}"),
    ("order.cancel", "{submission=string.rep('f',32),order_id='123'}"),
    ("camera.restore", "{view=string.rep('f',32)}"),
    ("camera.view", "{target='target',extra=true}"),
    ("time.start", "{ship='ship',factor=10}"), ("time.stop", "{}"),
    ("options.open", "{submenu='load'}"),
    ("options.submenu", "{page='load',id='x',submenu='x'}"),
    ("options.select", "{page='example_options',row=2.5,col=3,widget=30,option='1',text='High',value_type='number',value='5'}"),
    ("options.select", "{page='example_options',row=2,col=0,widget=30,option='1',text='High',value_type='number',value='5'}"),
    ("options.select", "{page='example_options',row=2,col=3,widget=30,option='1',text='High',value_type='UNKNOWN',value='5'}"),
    ("options.select", "{page='example_options',row=2,col=3,widget=30,option='1',text='High',value_type='number',value=5}"),
])
def test_invalid_requests_never_dispatch(runtime, action, args):
    if "0/0" in args:
        with pytest.raises(luajit.LuaError, match="finite value required"):
            runtime.execute(f"return prepare('{action}',{args})")
    else:
        r = runtime.execute(f"return prepare('{action}',{args})")
        assert r.status in ("refused", "unknown")
    assert runtime.eval("writes") == 0


def test_wrong_binding_refused(runtime):
    r = runtime.execute("request=req('camera.view',{target='target'});request.binding=string.rep('f',64);return registry.prepare(request)")
    assert r.status == "refused"
    assert runtime.eval("prepares") == 0


@pytest.mark.parametrize("native", ["DISPATCHED_UNVERIFIED", "ORDER_OBSERVED", "PARTIAL_CREATED_NOT_ENABLED"])
def test_native_submission_not_completion_or_replay(runtime, native):
    runtime.execute(f"native_status='{native}';prepare('order.submit',{{kind='dock',ship='ship',target='target',policy='require_empty'}})")
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("execute().status") == "running"
    observed = runtime.eval("status()")
    assert observed.status == "running"
    assert observed.observations.arrival_verified is False
    assert observed.observations.completion_qualified is False
    assert runtime.eval("writes") == 1


def test_postwrite_exception_and_identity_change_remain_uncertain(runtime):
    runtime.execute("prepare('menu.confirm',{mode='purchase',station='station'});throw_write=true")
    r = runtime.eval("execute()")
    assert r.status == "unknown"
    assert r.observations.wrapper_dispatch_attempted is True
    runtime.eval("execute()")
    assert runtime.eval("writes") == 1


def test_current_stamp_change_prevents_mutation(runtime):
    runtime.execute("prepare('camera.view',{target='target'});stamp.generation=string.rep('f',32)")
    assert runtime.eval("execute().status") == "unknown"
    assert runtime.eval("writes") == 0


def test_dispose_permanently_refuses(runtime):
    runtime.execute("prepare('camera.view',{target='target'});adapter.dispose()")
    assert runtime.eval("execute().status") == "unknown"
    assert runtime.eval("writes") == 0


def test_cancellation_requires_owned_id_then_later_observation(runtime):
    runtime.execute("native_status='ORDER_OBSERVED';prepare('order.submit',{kind='dock',ship='ship',target='target',policy='require_empty'});execute()")
    runtime.execute("prepare('order.cancel',{submission=string.rep('1',32),order_id='123'},'2');native_status='ORDER_REMOVAL_OBSERVED'")
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("status().status") == "running"
    runtime.execute("time=11")
    r = runtime.eval("status()")
    assert r.status == "completed"
    assert r.observations.scope == "OWNED_ORDER_REMOVAL_AT_OBSERVATION"
    assert r.observations.arrival_verified is False


@pytest.mark.parametrize("mutation", ["queue_rows={{id='123'}}", "order_adapter=nil;deps.orders=nil"])
def test_no_unearned_cancellation_from_raw_queue(runtime, mutation):
    # No owned request is enough even when the station/ship queue is empty.
    runtime.execute(mutation)
    r = runtime.execute("return prepare('order.cancel',{submission=string.rep('f',32),order_id='123'})")
    assert r.status == "unknown"
    assert runtime.eval("writes") == 0


def test_camera_restore_owned_verified_only_and_unverified_result(runtime):
    runtime.execute("native_status='REQUESTED';prepare('camera.view',{target='target'});execute();camera_status='TARGET_VERIFIED'")
    assert runtime.eval("status().status") == "completed"
    runtime.execute("prepare('camera.restore',{view=string.rep('1',32)},'2');native_status='RESTORE_REQUESTED_UNVERIFIED'")
    r = runtime.eval("execute()")
    assert r.status == "unknown"
    assert r.observations.completion_qualified is False
    assert runtime.eval("status().status") == "unknown"
    assert runtime.eval("writes") == 2


def test_separate_observers_never_register_actions(runtime):
    runtime.execute("r=adapter.observe('transactions','station')")
    assert runtime.eval("r.enumeration_complete") is True
    assert runtime.eval("r.atomic") is False
    assert runtime.eval("writes") == 0
    assert runtime.eval("adapter.spec.actions.transactions") is None
    okay, _ = runtime.execute("return pcall(adapter.observe,'eval','x')")
    assert not okay


def test_actual_camera_later_target_and_restoration(runtime):
    runtime.execute("""
        external=2
        deps.camera=real_camera{
            epoch=function() return stamp.generation end,now=function() return time end,
            resolve_issued=function(t) if t=='target' then return 42 end end,is_valid=function() return true end,
            native={GetPlayerID=function() return 1 end,GetPlayerControlledShipID=function() return 2 end,
                GetContextByClass=function() return 7 end,IsExternalViewDisabled=function() return false end,
                IsPlayerCameraTargetViewPossible=function() return true end,
                SetSofttarget=function() writes=writes+1;return true end,
                SetPlayerCameraTargetView=function() writes=writes+1 end,
                GetExternalViewRefObject=function() return external end,
                SetPlayerCameraCockpitView=function() writes=writes+1 end}}
        setup();prepare('camera.view',{target='target'})
    """.replace("real_camera", "camera_factory"))
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("status().status") == "running"
    runtime.execute("time=11;external=42")
    assert runtime.eval("status().status") == "completed"
    runtime.execute("prepare('camera.restore',{view=string.rep('1',32)},'2')")
    assert runtime.eval("execute().status") == "unknown"
    assert runtime.eval("writes") == 3
    assert runtime.execute("return prepare('camera.view',{target='target'},'3').status") == "prepared"


def test_actual_order_submission_and_owned_cancel(runtime):
    runtime.execute(constant("test_liveorders.py", "SETUP"))
    runtime.globals().order_factory = runtime.execute(source("orders"))
    runtime.execute("""
        deps.orders=order_factory(d);setup()
        prepare('order.submit',{kind='dock',ship='ship',target='target',policy='require_empty'})
    """)
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("status().status") == "running"
    runtime.execute("prepare('order.cancel',{submission=string.rep('1',32),order_id='18446744073709551615'},'2')")
    assert runtime.eval("execute().status") == "running"
    runtime.execute("time=11")
    r = runtime.eval("status()")
    assert r.status == "completed"
    assert runtime.eval("#orders") == 0


def actual_menu(runtime, fixture):
    runtime.execute("ffi=require('ffi')")
    runtime.globals().decl = constant('test_livemenu.py', 'DECL')
    runtime.execute('ffi.cdef(decl)')
    runtime.execute(constant('test_livemenu.py', 'SETUP'))
    runtime.execute(constant('test_livemenu.py', fixture))
    runtime.globals().menu_factory = runtime.execute(source('menu'))
    runtime.execute('''
        local md=deps
        md.widget_state,md.set_dropdown_current=menu_factory.native_reader(ffi,C,g)
        native_menu=menu_factory.new(md)
        deps={revision=REV,public_current=function() return stamp end,now=function() return time end,
            resolve_issued=function(token,kind)
                if token=='station' and kind=='station' then return station end
                if token=='ship' and kind=='ship' then return subject end
            end,
            menu=native_menu,orders=order_adapter,camera=camera_adapter,transactions=transaction_adapter,
            options=options_adapter,navigation=navigation_adapter,timecontrol=navigation_adapter}
        setup()
    ''')


def test_actual_menu_view_close_through_sdk_requires_owned_open_and_later_read(runtime):
    actual_menu(runtime, 'VIEW_SETUP')
    assert runtime.eval("prepare('menu.view',{ship='ship'}).status") == 'prepared'
    assert runtime.eval('execute().status') == 'running'
    runtime.execute('show_view()')
    assert runtime.eval("prepare('menu.close',{opening=string.rep('1',32)},'2').status") == 'prepared'
    assert runtime.eval('execute().status') == 'running'
    runtime.execute('time=11;age=11')
    r = runtime.eval('status()')
    assert r.status == 'completed' and r.observations.scope == 'OWNED_MENU_CLOSED_READBACK'
    assert r.observations.economic_outcome_verified is False and r.observations.rendered_pixels_verified is False
    runtime.eval('execute()')
    assert runtime.eval('#calls') == 2


@pytest.mark.parametrize('action', ['menu.repair_tab', 'menu.repair_select'])
def test_actual_repair_wrapper_is_dispatch_only_and_preserves_loadout(runtime, action):
    actual_menu(runtime, 'REPAIR_SETUP')
    if action.endswith('select'):
        runtime.execute("menu.upgradetypeMode='repair'")
    runtime.globals().repair_action = action
    assert runtime.eval("prepare(repair_action,{station='station',ship='ship'}).status") == 'prepared'
    assert runtime.eval('execute().status') == 'running'
    r = runtime.eval('status()')
    assert r.status == 'running' and r.observations.completion_qualified is False
    assert r.observations.economic_outcome_verified is False
    runtime.eval('execute()')
    assert runtime.eval('#calls') == 2
    assert runtime.eval('menu.upgradeplan.engine[1].macro') == 'engine_macro'


@pytest.mark.parametrize('action,args', [
    ('menu.view', "{ship='ship',station='station'}"),
    ('menu.view', '{}'), ('menu.close', "{opening='private-1'}"),
    ('menu.close', "{opening=string.rep('f',32)}"),
    ('menu.repair_tab', "{station='station'}"),
    ('menu.repair_select', "{station='station',ship='ship',all=true}"),
])
def test_new_menu_closed_vocabulary_and_foreign_opening_refuse(runtime, action, args):
    r = runtime.execute(f"return prepare('{action}',{args})")
    assert r.status in ('unknown', 'refused')
    assert runtime.eval('writes') == 0 and runtime.eval('prepares') == 0


def test_menu_close_cannot_adopt_opening_with_failed_outer_readback(runtime):
    runtime.execute('''
        menu_adapter.execute=function(ticket)
            writes=writes+1;time=0/0
            return {status='DISPATCHED_UNVERIFIED',action_attempted=true}
        end
        setup();prepare('menu.view',{ship='ship'})
    ''')
    assert runtime.eval('execute().status') == 'unknown'
    runtime.execute('time=10')
    assert runtime.eval("prepare('menu.close',{opening=string.rep('1',32)},'2').status") == 'unknown'
    assert runtime.eval('writes') == 1 and runtime.eval('prepares') == 1


def test_actual_menu_selection_uses_current_native_model(runtime):
    runtime.execute("ffi=require('ffi')")
    runtime.globals().decl = constant("test_livemenu.py", "DECL")
    runtime.execute("ffi.cdef(decl)")
    runtime.execute(constant("test_livemenu.py", "SETUP"))
    runtime.globals().menu_factory = runtime.execute(source("menu"))
    runtime.execute("""
        local md=deps
        md.widget_state,md.set_dropdown_current=menu_factory.native_reader(ffi,C,g)
        native_menu=menu_factory.new(md)
    """)
    # Recreate the wrapper's outer deps after the existing native fixture supplied its own.
    runtime.execute("""
        deps={revision=REV,public_current=function() return stamp end,now=function() return time end,
            resolve_issued=function() return station end,menu=native_menu,orders=order_adapter,
            camera=camera_adapter,transactions=transaction_adapter,options=options_adapter,navigation=navigation_adapter,timecontrol=navigation_adapter}
        setup();prepare('menu.select',{mode='purchase',station='station',field='ship',option='ship_test_macro'})
    """)
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("status().status") == "running"


@pytest.mark.parametrize("mutation", [
    "queue_rows={{id='123'}}", "order_adapter.observe=function() return {status='OBSERVED',ship='43',rows={},count=0} end",
    "order_adapter.observe=function() return {status='OBSERVED',ship='42',rows={},count=1} end",
])
def test_owned_cancel_does_not_complete_on_bad_later_read(runtime, mutation):
    runtime.execute(mutation + ";setup()")
    runtime.execute("native_status='ORDER_OBSERVED';prepare('order.submit',{kind='dock',ship='ship',target='target',policy='require_empty'});execute()")
    runtime.execute("prepare('order.cancel',{submission=string.rep('1',32),order_id='123'},'2');native_status='ORDER_REMOVAL_OBSERVED';execute();time=11")
    assert runtime.eval("status().status") == "unknown"


def test_wrong_order_identity_cannot_cancel_owned_submission(runtime):
    runtime.execute("native_status='ORDER_OBSERVED';prepare('order.submit',{kind='dock',ship='ship',target='target',policy='require_empty'});execute()")
    r = runtime.execute("return prepare('order.cancel',{submission=string.rep('1',32),order_id='124'},'2')")
    assert r.status == "unknown"
    assert runtime.eval("writes") == 1


def test_public_stamp_change_after_native_call_is_unknown(runtime):
    runtime.execute("prepare('menu.add',{mode='purchase',station='station'});change_write=true")
    assert runtime.eval("execute().status") == "unknown"
    assert runtime.eval("writes") == 1
    runtime.eval("execute()")
    assert runtime.eval("writes") == 1


def test_native_preparation_refusal_retains_false_attempt(runtime):
    runtime.execute("camera_adapter.prepare=function() return {status='REFUSED',action_attempted=false} end;setup()")
    r = runtime.execute("return prepare('camera.view',{target='target'})")
    assert r.status == "refused"
    assert r.observations.native_action_attempted is False
    assert runtime.eval("writes") == 0


def test_unavailable_identity_never_calls_observer(runtime):
    runtime.execute("stamp=nil")
    okay, _ = runtime.execute("return pcall(adapter.observe,'transactions','station')")
    assert not okay
    assert runtime.eval("reads") == 0


def test_changed_request_rejected_even_without_sdk(runtime):
    runtime.execute("prepare('camera.view',{target='target'});request.arguments.target='other'")
    runtime.execute("adapter.spec.invoke('execute',request,function(r) answer=r end)")
    assert runtime.eval("answer.status") == "unknown"
    assert runtime.eval("writes") == 0


def test_camera_restore_before_verified_view_is_unqualified(runtime):
    runtime.execute("native_status='REQUESTED';prepare('camera.view',{target='target'});execute()")
    r = runtime.execute("return prepare('camera.restore',{view=string.rep('1',32)},'2')")
    assert r.status == "unknown"
    assert runtime.eval("writes") == 1


def test_restoration_no_second_attempt(runtime):
    runtime.execute("native_status='REQUESTED';prepare('camera.view',{target='target'});execute();camera_status='TARGET_VERIFIED';status()")
    runtime.execute("prepare('camera.restore',{view=string.rep('1',32)},'2');native_status='RESTORE_REQUESTED_UNVERIFIED';execute();execute()")
    assert runtime.eval("writes") == 2
    r = runtime.execute("return prepare('camera.restore',{view=string.rep('1',32)},'3')")
    assert r.status == "unknown"
    assert runtime.eval("writes") == 2


def test_clock_reversal_during_dispatch_cannot_later_complete(runtime):
    runtime.execute("camera_adapter.execute=function() writes=writes+1;time=9;return {status='REQUESTED',action_attempted=true} end;setup()")
    runtime.execute("prepare('camera.view',{target='target'})")
    assert runtime.eval("execute().status") == "unknown"
    runtime.execute("time=11;camera_status='TARGET_VERIFIED'")
    assert runtime.eval("status().status") == "unknown"
    assert runtime.eval("writes") == 1


def test_backwards_observation_clock_cannot_complete(runtime):
    runtime.execute("native_status='REQUESTED';prepare('camera.view',{target='target'});execute();time=9;camera_status='TARGET_VERIFIED'")
    assert runtime.eval("status().status") == "unknown"
    assert runtime.eval("reads") == 0


def test_missing_nonboolean_attempt_facts_remain_unknown(runtime):
    runtime.execute("camera_adapter.execute=function() return {status='REQUESTED',action_attempted=true,attempts={softtarget=false,target_view=1}} end;setup()")
    runtime.execute("prepare('camera.view',{target='target'})")
    r = runtime.eval("execute()")
    assert r.status == "running"
    assert r.observations.softtarget_attempted is False
    assert r.observations.target_view_attempted == "UNKNOWN"
    assert r.observations.cockpit_attempted == "UNKNOWN"


@pytest.mark.parametrize("action,args,page", [
    ("options.open", "{}", "main"),
    ("options.submenu", "{page='main',id='simple_menu_extension_options',submenu='simple_menu_extension_options'}", "simple_menu_extension_options"),
    ("options.select", "{page='example_options',row=2,col=3,widget=30,option='1',text='High',value_type='number',value='5'}", "example_options"),
])
def test_options_never_complete_from_dispatch_or_status(runtime, action, args, page):
    runtime.execute(f"prepare('{action}',{args});execute();execute()")
    out = runtime.eval("status()")
    assert out.status == "running" and out.observations.completion_qualified is False
    assert out.observations.setting_effect_verified is False
    assert runtime.eval("last_page") == page and runtime.eval("writes") == 1


def actual_options(runtime):
    tree = ast.parse(Path(__file__).with_name("test_liveoptions.py").read_text(encoding="utf-8"))
    build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build")
    call = next(n for n in ast.walk(build) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "execute"
                and n.args and isinstance(n.args[0], ast.BinOp))
    runtime.execute(ast.literal_eval(call.args[0].left))
    runtime.globals().options_factory = runtime.execute(source("options"))
    runtime.execute("deps.options=options_factory(d);setup()")


def test_actual_options_native_index_and_unverified_effect(runtime):
    actual_options(runtime)
    runtime.execute("prepare('options.select',{page='simple_menu_extension_options',row=2,col=3,widget=30,option='1',text='High',value_type='number',value='5'})")
    assert runtime.eval("execute().status") == "running"
    assert runtime.eval("last_option") == "1" and runtime.eval("writes") == 2
    runtime.eval("execute()")
    out = runtime.eval("status()")
    assert out.status == "running" and out.observations.setting_effect_verified is False
    assert runtime.eval("writes") == 2


def test_actual_options_open_discloses_pause(runtime):
    actual_options(runtime)
    runtime.execute("m.shown=false;prepare('options.open',{})")
    out = runtime.eval("execute()")
    assert out.status == "running" and out.observations.native_pause_possible is True


def test_actual_options_changed_mapping_refused_without_write(runtime):
    actual_options(runtime)
    runtime.execute("prepare('options.select',{page='simple_menu_extension_options',row=2,col=3,widget=30,option='1',text='High',value_type='number',value='5'});cell.properties.options[1].value=4")
    assert runtime.eval("execute().status") == "refused"
    assert runtime.eval("writes") == 0

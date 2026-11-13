"""Actual SDK/provider with synthetic native adapter outcomes."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

UI=Path(__file__).resolve().parents[3]/'mods/x4_toolkit_helper/ui'


def runtime(action='open'):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        REV=string.rep('a',64);stamp={provider='x4-live-session-v1',revision=REV,
            session=string.rep('b',32),generation=string.rep('c',32),world_token='world'}
        writes=0;reads=0;prepares=0;outcome={status='DISPATCHED_UNVERIFIED',action_attempted=true}
        adapter={observe=function() reads=reads+1;return {status='OBSERVED'} end,
            prepare=function(context,kind,actor) prepares=prepares+1;assert(context=='context' and kind=='ENTITY' and actor=='43');return {status='PREPARED',ticket='private'} end,
            execute=function(ticket) assert(ticket=='private');writes=writes+1;if throws then error('dispatch unknown') end;return outcome end,
            dispose=function() return true end}
        req={version=1,id=string.rep('d',32),session=stamp.session,provider='x4-comm-menu-v1',action='open',
            arguments={context='context',kind='ENTITY',actor='43'},binding=REV,digest=string.rep('e',64)}
        function prep() return registry.prepare(req) end
        function run() return registry.execute(req.id,req.session,req.digest) end
        function status() return registry.status(req.id,req.session,req.digest) end
    """)
    r.globals().factory=r.execute((UI/'live_commmenu_provider.lua').read_text())
    r.globals().core=r.execute((UI/'live_actions.lua').read_text())
    r.execute("""
        context_adapter={observe=function() reads=reads+1;return {status='OBSERVED',rows={}} end,
            prepare=function(context,role,actor) prepares=prepares+1;assert(context=='context' and role=='info_shiptrader' and actor=='43');return {status='PREPARED',ticket='context-private'} end,
            execute=function(ticket) assert(ticket=='context-private');writes=writes+1;if throws then error('context dispatch unknown') end;return outcome end,
            dispose=function() return true end}
        navigation_adapter={observe=function(context,side)assert(context=='context' and side=='left');reads=reads+1;return {status='OBSERVED'}end,
            prepare=function(context,side,step)assert(context=='context' and side=='left');prepares=prepares+1;last_step=step;return {status='PREPARED',ticket='nav-private'}end,
            execute=function(ticket)assert(ticket=='nav-private');writes=writes+1;return outcome end,dispose=function()return true end}
        api=factory{adapter=adapter,context_adapter=context_adapter,navigation_adapter=navigation_adapter,revision=REV,public_current=function() return stamp end}
        registry=core();registry.register(api.spec)
    """)
    if action=='inspect':r.execute("req.action='inspect';req.arguments={}")
    if action=='context_inspect':r.execute("req.action='context_inspect';req.arguments={}")
    if action=='context_open':r.execute("req.action='context_open';req.arguments={context='context',role='info_shiptrader',actor='43'}")
    if action.startswith('map_'):
        r.globals().action=action;r.execute("req.action=action;req.arguments={context='context',side='left'}")
    return r


def test_inspect_is_completed_observation_not_opening():
    r=runtime('inspect');assert r.eval('prep().status')=='prepared'
    out=r.eval('run()');assert out.status=='completed' and r.eval('writes')==0 and r.eval('reads')==1
    assert out.observations.conversation_open_verified is False


def test_open_dispatch_and_reconciliation_never_reclick():
    r=runtime();assert r.eval('prep().status')=='prepared' and r.eval('writes')==0
    assert r.eval('run().status')=='running'
    out=r.eval('status()');assert out.status=='running' and out.observations.wrapper_attempted is True
    assert out.observations.conversation_open_verified is False and r.eval('writes')==1


@pytest.mark.parametrize('outcome,state,attempted',[
    ("{status='REFUSED',action_attempted=false}",'refused',False),
    ("{status='UNCERTAIN',action_attempted=true}",'unknown',True),
    ("{status='DISPATCHED_UNVERIFIED'}",'running','UNKNOWN'),
    ("{status='DISPATCHED_UNVERIFIED',action_attempted='maybe'}",'running','UNKNOWN'),
])
def test_attempt_facts_preserve_unknown(outcome,state,attempted):
    r=runtime();r.execute('prep();outcome='+outcome)
    out=r.eval('run()');assert out.status==state and out.observations.wrapper_attempted==attempted


def test_callback_exception_retains_unknown_attempt_not_false():
    r=runtime();r.execute('prep();throws=true');out=r.eval('run()')
    assert out.status=='unknown' and out.observations.wrapper_attempted=='UNKNOWN'
    r.execute('run();status()');assert r.eval('writes')==1


@pytest.mark.parametrize('change',["req.arguments.extra='x'","req.arguments.actor=nil","req.binding=string.rep('f',64)"])
def test_bad_request_refuses_before_native_prepare(change):
    r=runtime();r.execute(change)
    assert r.eval('prep().status')=='refused' and r.eval('prepares+writes')==0


def test_changed_session_after_prepare_cannot_dispatch():
    r=runtime();r.execute("prep();stamp.session=string.rep('f',32)")
    assert r.eval('run().status')=='unknown' and r.eval('writes')==0


def test_context_inspection_and_dispatch_keep_separate_attempt_scope():
    r=runtime('context_inspect');assert r.eval('prep().status')=='prepared'
    out=r.eval('run()');assert out.status=='completed' and r.eval('writes')==0
    assert out.observations.context_handler_attempted is False and out.observations.wrapper_attempted is False
    r=runtime('context_open');assert r.eval('prep().status')=='prepared'
    out=r.eval('run()');assert out.status=='running' and out.observations.context_handler_attempted is True
    assert out.observations.wrapper_attempted is False and out.observations.context_open_verified is False
    r.execute('run();status()');assert r.eval('writes')==1


def test_context_callback_failure_keeps_unknown_handler_attempt():
    r=runtime('context_open');r.execute('prep();throws=true');out=r.eval('run()')
    assert out.status=='unknown' and out.observations.context_handler_attempted=='UNKNOWN'
    assert out.observations.wrapper_attempted is False


def test_context_open_cannot_substitute_comm_kind_for_role():
    r=runtime('context_open');r.execute("req.arguments.role=nil;req.arguments.kind='ENTITY'")
    assert r.eval('prep().status')=='refused' and r.eval('prepares+writes')==0


@pytest.mark.parametrize('action',['map_open','map_panel','map_objectinfo'])
def test_map_navigation_dispatch_is_not_completion(action):
    r=runtime(action);assert r.eval('prep().status')=='prepared'
    assert r.eval('last_step')==action[4:]
    out=r.eval('run()')
    assert out.status=='running' and out.observations.map_handler_attempted is True
    assert out.observations.info_open_verified is False
    r.execute('run();status()');assert r.eval('writes')==1


def test_map_inspect_is_only_observation():
    r=runtime('map_inspect');assert r.eval('prep().status')=='prepared'
    out=r.eval('run()');assert out.status=='completed'
    assert out.observations.info_open_verified is False and r.eval('writes')==0


@pytest.mark.parametrize('change',["req.arguments.side='other'","req.arguments.extra='x'","req.arguments.context=nil","req.arguments.side='right'"])
def test_bad_map_open_arguments_refuse(change):
    r=runtime('map_open');r.execute(change)
    assert r.eval('prep().status')=='refused' and r.eval('prepares+writes')==0


def test_context_inspect_accepts_explicit_left_without_changing_comm_arguments():
    r=runtime('context_inspect');r.execute("req.arguments={side='left'}")
    assert r.eval('prep().status')=='prepared' and r.eval('run().status')=='completed'
    r=runtime();r.execute("req.arguments.side='left'")
    assert r.eval('prep().status')=='refused'

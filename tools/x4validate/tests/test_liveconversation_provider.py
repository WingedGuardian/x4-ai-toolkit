"""Real SDK plus real observation provider; synthetic observer outcomes."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

UI=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui"


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        REV=string.rep('a',64);SESSION=string.rep('b',32);id=string.rep('c',32);sends=0;polls=0;disposed=0
        stamp={provider='x4-live-session-v1',revision=string.rep('d',64),session=SESSION,
            generation=string.rep('e',32),world_token='world'}
        value={status='PENDING',request=id}
        observer={begin=function(x) sends=sends+1;if senderror then error('uncertain send') end;
                return {status='PENDING',request=x} end,
            poll=function() polls=polls+1;return value end,dispose=function() disposed=disposed+1;return true end}
        req={version=1,id=id,session=SESSION,provider='x4-conversation-observer-v1',action='snapshot',
            arguments={},binding=REV,digest=string.rep('f',64)}
        function prep() return registry.prepare(req) end
        function run() return registry.execute(req.id,req.session,req.digest) end
        function status() return registry.status(req.id,req.session,req.digest) end
    """)
    r.globals().factory=r.execute((UI/'live_conversation_provider.lua').read_text())
    r.globals().core=r.execute((UI/'live_actions.lua').read_text())
    r.execute("api=factory{revision=REV,public_current=function() return stamp end,observer=observer};registry=core();registry.register(api.spec)")
    return r


def test_prepare_status_and_running_never_repeat_snapshot():
    r=runtime();assert r.eval("prep().status")=="prepared"
    assert r.eval("status().status")=="prepared" and r.eval("sends")==0
    assert r.eval("run().status")=="running"
    assert r.eval("status().status")=="running" and r.eval("sends")==1


@pytest.mark.parametrize('state',['INACTIVE_OBSERVED','ACTOR_OBSERVED_SECTION_UNKNOWN','ACTOR_AND_HISTORICAL_SECTION_OBSERVED'])
def test_completed_means_only_snapshot_captured(state):
    r=runtime();r.execute("prep();run();value={status='"+state+"',request=id,rendered_route='BLOCKED',runtime_verified=false}")
    result=r.eval("status()")
    assert result.status=='completed'
    assert result.observations.proof_scope=='CONVERSATION_SNAPSHOT_ONLY'
    assert result.observations.gameplay_action_attempted is False and result.observations.runtime_verified is False
    assert result.observations.action_core.provider_revision==r.eval('REV')


@pytest.mark.parametrize('state',['UNAVAILABLE','UNQUALIFIED','PASS','PENDING_BAD'])
def test_unknown_or_failed_observer_never_completes(state):
    r=runtime();r.execute("prep();run();value={status='"+state+"',request=id}")
    assert r.eval("status().status")=='unknown' and r.eval('sends')==1


@pytest.mark.parametrize('change',["req.arguments={actor='42'}","req.action='select'","req.binding=string.rep('1',64)"])
def test_invalid_action_context_is_refused_before_snapshot(change):
    r=runtime();r.execute(change)
    if "req.action=" in change:
        ok,why=r.execute("return pcall(prep)")
        assert ok is False and 'unregistered provider/action' in str(why)
    else:
        assert r.eval("prep().status")=='refused'
    assert r.eval('sends')==0


def test_send_failure_never_retries_and_is_unknown():
    r=runtime();r.execute("prep();senderror=true")
    assert r.eval("run().status")=='unknown'
    r.execute("senderror=false;run();status()")
    assert r.eval('sends')==1


def test_wrong_reply_correlation_or_scope_does_not_complete():
    for changed in ("request=string.rep('2',32)","rendered_route='PASS'","runtime_verified=true"):
        r=runtime();r.execute("prep();run();value={status='INACTIVE_OBSERVED',request=id,rendered_route='BLOCKED',runtime_verified=false};value."+changed)
        assert r.eval("status().status")=='unknown'


def test_changed_public_generation_cannot_send():
    r=runtime();r.execute("prep();stamp.generation=string.rep('1',32)")
    assert r.eval("run().status")=='unknown' and r.eval('sends')==0


def test_dispose_retires_provider_and_is_idempotent():
    r=runtime();assert r.eval('api.dispose()') is True
    assert r.eval('api.dispose()') is True and r.eval('disposed')==1
    assert r.eval('api.session()') is None

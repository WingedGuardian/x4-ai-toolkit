"""Actual Lua lifecycle controls with synthetic providers, not native proof."""
from pathlib import Path

import pytest
from lupa import LuaRuntime

SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_actions.lua"

SETUP = """
registry = factory()
calls = {}
session = 'world-token'
request = {version=1,id=string.rep('a',32),session=session,provider='fixture',
    action='start',arguments={case='one'},binding='prepared-token',digest=string.rep('d',64)}
provider = {id='fixture',revision=string.rep('b',64),actions={start=true},
    session=function() return session end,
    invoke=function(phase, req, done)
        calls[#calls+1] = phase
        if phase == 'prepare' then done({status='prepared',reason='',observations={}})
        elseif phase == 'execute' then done({status='running',reason='',observations={}})
        else done({status='completed',reason='',observations={measured=false}}) end
    end}
registry.register(provider)
function execute() return registry.execute(request.id,request.session,request.digest) end
function status() return registry.status(request.id,request.session,request.digest) end
"""


def runtime(extra=""):
    rt = LuaRuntime(unpack_returned_tuples=True)
    rt.globals().factory = rt.execute(SOURCE.read_text(encoding="utf-8"))
    rt.execute(SETUP + extra)
    return rt


def test_full_lifecycle_does_not_equate_dispatch_and_completion():
    r = runtime()
    assert r.eval("registry.prepare(request).status") == "prepared"
    assert r.eval("execute().status") == "running"
    assert r.eval("status().status") == "completed"
    assert r.eval("#calls") == 3
    assert r.eval("execute().status") == "completed"
    assert r.eval("#calls") == 3


def test_repeated_preparation_and_execution_never_repeat_adapter_dispatch():
    r = runtime()
    r.execute("registry.prepare(request); registry.prepare(request); execute(); execute()")
    assert r.eval("#calls") == 2


def test_duplicate_id_changed_request_refuses_without_adapter_call():
    r = runtime()
    r.execute("registry.prepare(request); request.arguments.case='changed'")
    assert r.eval("pcall(registry.prepare,request)")[0] is False
    assert r.eval("#calls") == 1


def test_session_change_blocks_prepared_action():
    r = runtime()
    r.execute("registry.prepare(request); session='new-world'")
    assert r.eval("execute().status") == "unknown"
    assert r.eval("#calls") == 1


def test_throwing_adapter_consumes_execution_and_never_retries():
    r = runtime()
    # Registered function object is immutable; its behavior is changed through its
    # external flag, not by replacing a registration after the fact.
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "calls[#calls+1] = phase",
        "calls[#calls+1] = phase; if phase=='execute' then error('after write') end"))
    r.execute("registry.prepare(request)")
    assert r.eval("execute().status") == "unknown"
    assert r.eval("execute().status") == "unknown"
    assert r.eval("#calls") == 2
    assert r.eval("status().status") == "completed"
    assert r.eval("execute().status") == "completed"
    assert r.eval("#calls") == 3


def test_async_execution_lost_ack_can_be_reconciled_by_read_only_status():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "elseif phase == 'execute' then done({status='running',reason='',observations={}})",
        "elseif phase == 'execute' then missing_ack=done"))
    r.execute("registry.prepare(request)")
    assert r.eval("execute().status") == "running"
    assert r.eval("status().status") == "completed"
    assert r.eval("#calls") == 3


def test_callback_for_old_session_cannot_complete_current_request():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "elseif phase == 'execute' then done({status='running',reason='',observations={}})",
        "elseif phase == 'execute' then callback=done"))
    r.execute("registry.prepare(request); execute(); session='new-world'; "
              "callback({status='completed',reason='',observations={}})")
    assert r.eval("status().status") == "unknown"


@pytest.mark.parametrize("body", [
    "nil", "{status='PASS',reason='',observations={}}",
    "{status='completed',reason='',observations={}}",
    "{status='prepared',reason='',observations=function() end}",
])
def test_invalid_prepare_outcome_never_enables_execute(body):
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "done({status='prepared',reason='',observations={}})", f"done({body})"))
    assert r.eval("registry.prepare(request).status") == "unknown"
    assert r.eval("execute().status") == "unknown"
    assert r.eval("#calls") == 1


def test_mutating_provider_cannot_rebind_stored_request():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace("calls[#calls+1] = phase",
        "calls[#calls+1] = phase; req.arguments.case='changed'"))
    r.execute("registry.prepare(request)")
    assert r.eval("registry.prepare(request).status") == "prepared"
    assert r.eval("request.arguments.case") == "one"


def test_double_callback_is_not_a_success():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "done({status='prepared',reason='',observations={}})",
        "done({status='prepared',reason='',observations={}}); "
        "done({status='prepared',reason='',observations={}})"))
    assert r.eval("registry.prepare(request).status") == "unknown"


@pytest.mark.parametrize("change", [
    "request.id='bad'", "request.digest='bad'", "request.session='bad token'",
    "request.action='notregistered'", "request.provider='missing'",
    "request.arguments={payload=string.rep('x',1400)}",
    "request.arguments={value=0/0}", "request.arguments={fn=function()end}",
    "request.arguments.self=request.arguments", "request.extra='ignored'",
])
def test_invalid_requests_never_call_provider(change):
    r = runtime(change)
    assert r.eval("pcall(registry.prepare,request)")[0] is False
    assert r.eval("#calls") == 0


def test_duplicate_provider_registration_refused():
    r = runtime()
    assert r.eval("pcall(registry.register,provider)")[0] is False


def test_request_capacity_never_evicts_old_execution_records():
    r = runtime()
    r.execute("for i=1,256 do request.id=string.format('%032x',i); "
              "registry.prepare(request) end; request.id=string.rep('f',32)")
    assert r.eval("pcall(registry.prepare,request)")[0] is False
    assert r.eval("#calls") == 256


def test_lost_status_reply_can_be_superseded_without_redispatch():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else callbacks=callbacks or {}; callbacks[#callbacks+1]=done end"))
    r.execute("registry.prepare(request); execute(); status(); status()")
    assert r.eval("#calls") == 4
    r.execute("callbacks[2]({status='completed',reason='',observations={}}); "
              "callbacks[1]({status='running',reason='',observations={}})")
    assert r.eval("status().status") == "completed"
    assert r.eval("status().observations.action_core.superseded_status_replies") == 1
    assert r.eval("#calls") == 4


def test_duplicate_execute_callback_is_sticky_not_reconciled_into_success():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "elseif phase == 'execute' then done({status='running',reason='',observations={}})",
        "elseif phase == 'execute' then "
        "done({status='running',reason='',observations={}}); "
        "done({status='running',reason='',observations={}})"))
    r.execute("registry.prepare(request); execute()")
    assert r.eval("status().status") == "unknown"
    assert r.eval("status().observations.action_core.protocol_fault") is True
    assert r.eval("#calls") == 2


def test_revision_is_from_immutable_registration_not_mutable_provider_or_observations():
    r = runtime("provider.revision=string.rep('f',64)")
    assert r.eval("registry.prepare(request).observations.action_core.provider_revision") == "b" * 64
    r.execute("execute()")
    assert r.eval("status().observations.action_core.provider_revision") == "b" * 64


def test_lost_prepare_ack_recovers_readiness_without_repeating_prepare():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "if phase == 'prepare' then done({status='prepared',reason='',observations={}})",
        "if phase == 'prepare' then oldprepare=done").replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else done({status='prepared',reason='',observations={}}) end"))
    assert r.eval("registry.prepare(request).status") == "preparing"
    assert r.eval("execute().status") == "preparing"
    assert r.eval("status().status") == "prepared"
    r.execute("oldprepare({status='refused',reason='stale',observations={}})")
    assert r.eval("execute().status") == "running"
    assert r.eval("registry.prepare(request).observations.action_core.superseded_prepare_replies") == 1
    assert list(r.globals().calls.values()) == ["prepare", "status", "execute"]


def test_pending_readiness_blocks_dispatch_and_old_status_cannot_unblock_it():
    # The prepare ack is LOST (never delivered), so both status() calls are genuine readiness checks; an ack that
    # did arrive is reported as-is (test_async_prepare_ack_is_observable_by_status_and_executes).
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "if phase == 'prepare' then done({status='prepared',reason='',observations={}})",
        "if phase == 'prepare' then lost=done").replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else reads=reads or {}; reads[#reads+1]=done end"))
    r.execute("registry.prepare(request); status(); status()")
    assert r.eval("execute().status") == "preparing"
    r.execute("reads[1]({status='prepared',reason='',observations={}})")
    assert r.eval("execute().status") == "preparing"
    assert list(r.globals().calls.values()) == ["prepare", "status", "status"]
    r.execute("reads[2]({status='prepared',reason='',observations={}})")
    assert r.eval("execute().status") == "running"
    assert list(r.globals().calls.values()) == ["prepare", "status", "status", "execute"]


def test_unknown_preparation_requires_fresh_readiness_without_protocol_fault():
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "if phase == 'prepare' then done({status='prepared',reason='',observations={}})",
        "if phase == 'prepare' then done({status='unknown',reason='ack timeout',observations={}})").replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else done({status='prepared',reason='',observations={}}) end"))
    assert r.eval("registry.prepare(request).status") == "unknown"
    assert r.eval("execute().observations.action_core.protocol_fault") is False
    assert r.eval("#calls") == 1
    assert r.eval("status().status") == "prepared"
    assert r.eval("execute().status") == "running"
    assert list(r.globals().calls.values()) == ["prepare", "status", "execute"]


@pytest.mark.parametrize("outcome", ["completed", "running", "failed"])
def test_prepare_status_cannot_invent_execution(outcome):
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "if phase == 'prepare' then done({status='prepared',reason='',observations={}})",
        "if phase == 'prepare' then missing=done").replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else done({status='" + outcome + "',reason='',observations={}}) end"))
    r.execute("registry.prepare(request)")
    assert r.eval("status().status") == "unknown"
    assert r.eval("execute().observations.action_core.protocol_fault") is True
    assert list(r.globals().calls.values()) == ["prepare", "status"]


def _async_runtime():
    """Prepare AND status answered later (an MD-backed provider replies on a later frame), as in game."""
    r = LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute(SETUP.replace(
        "if phase == 'prepare' then done({status='prepared',reason='',observations={}})",
        "if phase == 'prepare' then pack=done").replace(
        "else done({status='completed',reason='',observations={measured=false}}) end",
        "else reads=reads or {}; reads[#reads+1]=done end"))
    return r


def test_async_prepare_ack_is_observable_by_status_and_executes():
    """run-12: the ack arrives after prepare returned 'preparing'; status() must report it, not discard it."""
    r = _async_runtime()
    assert r.eval("registry.prepare(request).status") == "preparing"
    r.execute("pack({status='prepared',reason='',observations={}})")
    assert r.eval("status().status") == "prepared"
    assert list(r.globals().calls.values()) == ["prepare"]          # nothing re-invoked
    assert r.eval("execute().status") == "running"


def test_async_readiness_reply_is_observable_by_the_next_status():
    r = _async_runtime()
    assert r.eval("registry.prepare(request).status") == "preparing"
    assert r.eval("status().status") == "preparing"                 # lost ack: readiness asked once
    r.execute("reads[1]({status='prepared',reason='',observations={}})")
    assert r.eval("status().status") == "prepared"                  # previously reset to preparing forever
    assert list(r.globals().calls.values()) == ["prepare", "status"]
    assert r.eval("execute().status") == "running"

"""Actual public session Lua with stub transport; not native MD qualification."""
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa.luajit21", reason="actual Lua controls require dev dependency")
SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_session.lua"


@pytest.fixture
def bridge():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
        REV=string.rep('a',64); UI=string.rep('b',32); NONCE=string.rep('c',32)
        state={time=1,sends=0,reads=0,clears=0,state_reads=0}
        callbacks={};sent={};replies={};results={}
        md={provider='x4-live-session-v1',revision=REV,session='',generation='',world_token='',age=10}
        env={revision=REV,
            now=function() return state.time end,
            send=function(r) state.sends=state.sends+1;sent[#sent+1]=r
                if state.throw_send then error('send uncertain') end end,
            subscribe=function(reply,invalidated) callbacks[#callbacks+1]={reply,invalidated} end,
            read=function() state.reads=state.reads+1;return replies end,
            clear=function() state.clears=state.clears+1
                if state.clear_false then return false end
                replies={} end,
            read_state=function() state.state_reads=state.state_reads+1
                if state.throw_state then error('state unavailable') end
                return md end}
        function request(mode,challenge)
            return {mode=mode,session=mode=='probe' and '' or NONCE,generation=UI,
                    challenge=string.rep(challenge or 'd',32)}
        end
        function done(r) results[#results+1]=r end
        function response(req,kind)
            return {kind=kind,provider='x4-live-session-v1',revision=REV,mode=req.mode,
                challenge=req.challenge,generation=req.generation,session=md.session,
                has_session=md.session~='',reason='',age=10,world_token=md.world_token}
        end
        function queue_reply(kind)
            replies={response(sent[#sent],kind)}
        end
        function set_ready()
            md.session=NONCE;md.generation=UI;md.world_token='observed-only'
        end
    """)
    factory = lua.execute(SOURCE.read_text(encoding="utf-8"))
    api = factory(lua.globals().env)
    lua.globals().api = api
    return lua, api, factory


def ready(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');callbacks[1][1]()")
    assert lua.globals().results[1].kind == "READY"
    assert api.current().session == "c" * 32


def test_pending_not_ready_then_actual_current_read(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done)")
    assert api.status().pending
    assert api.current()[0] is None
    lua.execute("set_ready();queue_reply('READY');callbacks[1][1]()")
    before = lua.globals().state.state_reads
    first = api.current()
    api.current()
    assert first.session == "c" * 32
    assert lua.globals().state.state_reads == before + 2


def test_probe_observes_old_ui_generation_without_ready(bridge):
    lua, api, _ = bridge
    lua.execute("set_ready();md.generation=string.rep('f',32);api.connect(request('probe'),done);"
                "queue_reply('STATE');callbacks[1][1]()")
    assert lua.globals().results[1].kind == "STATE"
    assert api.current()[0] is None


@pytest.mark.parametrize("code", [
    "r.extra=true", "r.challenge='bad'", "r.generation='bad'", "r.mode='execute'",
    "r.session=''", "r.session=123", "r.generation=3", "r.challenge=nil",
    "setmetatable(r,{})",
])
def test_invalid_connect_never_sends(bridge, code):
    lua, _, _ = bridge
    lua.execute("r=request('fresh');" + code)
    with pytest.raises(lupa.LuaError):
        lua.execute("api.connect(r,done)")
    assert lua.globals().state.sends == 0


def test_md_number_boolean_is_accepted(bridge):
    # Run-03 (2026-11-10): MD has no boolean datatype, so an MD true/false crosses the blackboard as 1/0
    # (vanilla ego_detailmonitorhelper/helper.lua:8824 tests a blackboard flag with ~= 0). The boolean-only
    # test faulted the first live reply. Exactly 1/0 is accepted and normalized; 2 and 'true' still refuse.
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');replies[1].has_session=1;callbacks[1][1]()")
    assert lua.globals().results[1].kind == "READY"
    assert lua.globals().results[1].has_session is True
    assert api.current().session == "c" * 32


def test_md_number_false_probe_observes(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('probe'),done);queue_reply('STATE');replies[1].has_session=0;callbacks[1][1]()")
    assert lua.globals().results[1].kind == "STATE"
    assert lua.globals().results[1].has_session is False


@pytest.mark.parametrize("code", [
    "replies[1].extra=true", "replies[1].revision=string.rep('f',64)",
    "replies[1].provider='ror-fixtures-v1'", "replies[1].age=0/0",
    "replies[1].age=math.huge", "replies[1].has_session=2", "replies[1].has_session='true'",
    "replies[1].has_session=0",  # MD number form, but contradicts the present session
    "replies[1].generation=string.rep('f',32)", "replies[1].mode='resume'",
    "replies[1].session=string.rep('f',32)", "replies[1].world_token='other'",
    "replies[1].kind='COMPLETED'", "replies[1].reason=false",
])
def test_bad_reply_never_ready(bridge, code):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');" + code + ";callbacks[1][1]()")
    assert api.current()[0] is None
    assert lua.globals().results[1].kind == "UNAVAILABLE"


def test_wrong_challenge_ignored_until_timeout(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');"
                "replies[1].challenge=string.rep('f',32);callbacks[1][1]()")
    assert api.status().pending
    assert api.status().ignored == 1
    lua.execute("state.time=11.01;api.tick()")
    assert lua.globals().results[1].reason == "HANDSHAKE_TIMEOUT_NO_FALLBACK"
    assert lua.globals().state.sends == 1


def test_late_timeout_reply_cannot_restore_ready_explicit_resume_can(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');"
                "state.time=12;api.tick();callbacks[1][1]()")
    assert api.current()[0] is None
    lua.execute("api.connect(request('resume','e'),done);queue_reply('READY');callbacks[1][1]()")
    assert api.current().session == "c" * 32
    assert lua.globals().state.sends == 2


def test_duplicate_batch_faults_before_any_ready_delivery(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');"
                "replies[2]=replies[1];callbacks[1][1]()")
    assert lua.globals().results[1].kind == "UNAVAILABLE"
    assert api.current()[0] is None


def test_duplicate_delivered_reply_faults(bridge):
    ready(bridge)
    lua, api, _ = bridge
    lua.execute("queue_reply('READY');callbacks[1][1]()")
    assert api.current()[0] is None
    assert "duplicate" in api.status().fault


@pytest.mark.parametrize("code", [
    "md.session='';md.generation=''", "md.generation=string.rep('f',32)",
    "md.revision=string.rep('f',64)", "md.world_token='changed'", "md=nil",
    "md.age=math.huge", "md.extra=true", "state.throw_state=true",
])
def test_current_never_uses_cached_ready(bridge, code):
    ready(bridge)
    lua, api, _ = bridge
    lua.execute(code)
    assert api.current()[0] is None
    assert api.status().fault


def test_load_invalidation_retires_pending_and_ready(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);callbacks[1][2]()")
    assert "INVALIDATED" in lua.globals().results[1].reason
    assert api.current()[0] is None
    with pytest.raises(lupa.LuaError):
        lua.execute("api.connect(request('probe','f'),done)")


def test_one_owner_dispose_old_callback_does_not_drain_new_queue(bridge):
    lua, api, factory = bridge
    with pytest.raises(lupa.LuaError, match="active owner"):
        factory(lua.globals().env)
    api.dispose()
    second = factory(lua.globals().env)
    lua.globals().second = second
    lua.execute("second.connect(request('fresh'),done);set_ready();queue_reply('READY')")
    before = lua.globals().state.reads
    lua.execute("callbacks[1][1]();callbacks[1][2]()")
    assert lua.globals().state.reads == before
    lua.execute("callbacks[2][1]()")
    assert second.current().session == "c" * 32


@pytest.mark.parametrize("code", [
    "replies[65]=replies[1]", "replies.other=replies[1]", "replies[3]=replies[1]",
    "setmetatable(replies,{})", "state.clear_false=true",
])
def test_queue_bad_or_not_cleared_refuses(bridge, code):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();queue_reply('READY');" + code + ";callbacks[1][1]()")
    assert api.current()[0] is None
    assert lua.globals().results[1].kind == "UNAVAILABLE"


def test_send_throw_uncertain_no_replay(bridge):
    lua, api, _ = bridge
    lua.execute("state.throw_send=true;api.connect(request('fresh'),done)")
    assert lua.globals().state.sends == 1
    assert "UNCERTAIN" in lua.globals().results[1].reason
    assert api.current()[0] is None


def test_challenge_reuse_and_ui_generation_change_refuse(bridge):
    ready(bridge)
    lua, _, _ = bridge
    with pytest.raises(lupa.LuaError, match="reused"):
        lua.execute("api.connect(request('resume'),done)")
    with pytest.raises(lupa.LuaError, match="generation changed"):
        lua.execute("r=request('resume','e');r.generation=string.rep('e',32);api.connect(r,done)")
    assert lua.globals().state.sends == 1


@pytest.mark.parametrize("time", [-1, float("nan"), float("inf"), 0.5])
def test_bad_clock_never_ready(bridge, time):
    ready(bridge)
    lua, api, _ = bridge
    lua.globals().state.time = time
    assert api.current()[0] is None


def test_result_mutation_does_not_change_ready(bridge):
    ready(bridge)
    lua, api, _ = bridge
    lua.globals().results[1].session = "f" * 32
    result = api.current()
    result.session = "e" * 32
    assert api.current().session == "c" * 32


def test_status_also_revalidates_native_stamp(bridge):
    ready(bridge)
    lua, api, _ = bridge
    lua.execute("md.session='';md.generation=''")
    assert not api.status().ready
    assert api.status().fault


def test_matching_empty_ready_world_token_is_impossible_producer_state(bridge):
    lua, api, _ = bridge
    lua.execute("api.connect(request('fresh'),done);set_ready();md.world_token='';"
                "queue_reply('READY');callbacks[1][1]()")
    assert api.current()[0] is None
    assert lua.globals().results[1].kind == "UNAVAILABLE"

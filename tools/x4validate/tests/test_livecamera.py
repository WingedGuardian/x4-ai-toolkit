"""Execute the real isolated Lua adapter; stubs record native calls, not game physics."""
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa.luajit21", reason="actual Lua behavioral tests require dev dependency")
SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_camera.lua"


@pytest.fixture
def camera():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
        state = { epoch='load1', time=10, issued=42, valid=true, disabled=false,
                  possible=true, player=1, controlled=2, sector=7, targetsector=7,
                  external=9, target_result=true, writes=0 }
        calls = {}
        local function record(name, a, b)
            state.writes = state.writes + 1
            calls[#calls+1] = {name, a, b}
        end
        deps = {
            epoch=function() return state.epoch end,
            now=function() return state.time end,
            resolve_issued=function(token) if token=='issued' then return state.issued end end,
            is_valid=function(id) return state.valid end,
            native={
                GetPlayerID=function() return state.player end,
                GetPlayerControlledShipID=function() return state.controlled end,
                GetContextByClass=function(id, class, parents)
                    assert(class=='sector')
                    if id==state.player then assert(parents==false); return state.sector end
                    assert(parents==true); return state.targetsector
                end,
                IsExternalViewDisabled=function() return state.disabled end,
                IsPlayerCameraTargetViewPossible=function(id, force)
                    assert(id==42 and force==true); return state.possible
                end,
                SetSofttarget=function(id, context)
                    record('target', id, context)
                    if state.throw_target then error('target error') end
                    if state.reload_on_target then state.epoch='load2' end
                    return state.target_result
                end,
                SetPlayerCameraTargetView=function(id, force)
                    record('camera', id, force)
                    if state.throw_camera then error('camera error') end
                end,
                GetExternalViewRefObject=function()
                    if state.throw_observe then error('read error') end
                    return state.external
                end,
                SetPlayerCameraCockpitView=function(force)
                    record('cockpit', force)
                    if state.throw_restore then error('restore error') end
                end,
            }
        }
    """)
    factory = lua.execute(SOURCE.read_text(encoding="utf-8"))
    return factory(lua.globals().deps), lua.globals().state, lua.globals().calls, lua


def prepared(camera):
    api, state, calls, lua = camera
    r = api.prepare("issued")
    assert r.status == "PREPARED" and state.writes == 0
    return r.ticket


def requested(camera):
    ticket = prepared(camera)
    assert camera[0].execute(ticket).status == "REQUESTED"
    return ticket


def verified(camera):
    ticket = requested(camera)
    camera[1].time = 11
    camera[1].external = 42
    assert camera[0].observe(ticket).status == "TARGET_VERIFIED"
    return ticket


def test_request_is_not_completion_and_exact_native_arguments(camera):
    api, state, calls, _ = camera
    ticket = requested(camera)
    assert state.writes == 2
    assert tuple(calls[1][n] for n in (1, 2, 3)) == ("target", 42, "")
    assert tuple(calls[2][n] for n in (1, 2, 3)) == ("camera", 42, True)
    state.external = 42
    assert api.observe(ticket).status == "PENDING"
    state.time = 10.01
    assert api.observe(ticket).status == "TARGET_VERIFIED"


@pytest.mark.parametrize("field,value", [
    ("issued", None), ("valid", False), ("valid", "true"),
    ("disabled", True), ("disabled", None), ("possible", False),
    ("possible", 1), ("player", 0), ("controlled", 42),
    ("sector", 0), ("targetsector", 0), ("targetsector", 8),
    ("time", float("nan")), ("time", float("inf")), ("epoch", ""),
])
def test_preparation_refuses_without_writes(camera, field, value):
    api, state, _, _ = camera
    state[field] = value
    assert api.prepare("issued").status == "REFUSED"
    assert state.writes == 0


@pytest.mark.parametrize("token", ["unissued", "", None, 42])
def test_only_issued_tokens_are_accepted(camera, token):
    assert camera[0].prepare(token).status == "REFUSED"
    assert camera[1].writes == 0


@pytest.mark.parametrize("field,value", [
    ("epoch", "load2"), ("issued", 43), ("valid", False),
    ("disabled", True), ("possible", False), ("targetsector", 8),
    ("controlled", 42), ("time", 9), ("time", 15.001),
])
def test_execution_revalidates_before_write(camera, field, value):
    ticket = prepared(camera)
    camera[1][field] = value
    assert camera[0].execute(ticket).status == "REFUSED"
    assert camera[1].writes == 0


def test_tickets_cannot_be_forged_mutated_or_replayed(camera):
    api, state, _, lua = camera
    ticket = prepared(camera)
    ticket.id = 123
    assert api.execute(lua.table()).status == "REFUSED"
    assert api.prepare("issued").status == "REFUSED"
    assert api.execute(ticket).status == "REQUESTED"
    assert api.execute(ticket).status == "REFUSED"
    assert state.writes == 2


def test_native_target_refusal_prevents_camera_call(camera):
    ticket = prepared(camera)
    camera[1].target_result = False
    r = camera[0].execute(ticket)
    assert r.status == "REFUSED" and r.action_attempted
    assert r.attempts.softtarget and not r.attempts.target_view
    assert camera[1].writes == 1


@pytest.mark.parametrize("reply", [None, 0, 1, "false", "true"])
def test_nonboolean_native_target_reply_is_uncertain(camera, reply):
    ticket = prepared(camera)
    camera[1].target_result = reply
    r = camera[0].execute(ticket)
    assert r.status == "UNCERTAIN" and r.action_attempted
    assert r.attempts.softtarget and not r.attempts.target_view
    assert camera[0].execute(ticket).status == "REFUSED"
    assert camera[1].writes == 1


@pytest.mark.parametrize("field,writes", [("throw_target", 1), ("throw_camera", 2), ("reload_on_target", 1)])
def test_partial_write_failure_is_uncertain_and_never_retried(camera, field, writes):
    ticket = prepared(camera)
    camera[1][field] = True
    r = camera[0].execute(ticket)
    assert r.status == "UNCERTAIN" and r.action_attempted
    assert r.attempts.softtarget and r.attempts.target_view == (writes == 2)
    assert camera[0].execute(ticket).status == "REFUSED"
    assert camera[0].observe(ticket).status == "REFUSED"
    assert camera[1].writes == writes


def test_delayed_match_and_timeout_boundary(camera):
    ticket = requested(camera)
    camera[1].time = 14
    assert camera[0].observe(ticket).status == "PENDING"
    camera[1].time = 15
    camera[1].external = 42
    assert camera[0].observe(ticket).status == "TARGET_VERIFIED"


def test_late_match_is_not_promoted_to_completion(camera):
    ticket = requested(camera)
    camera[1].time = 15.001
    camera[1].external = 42
    assert camera[0].observe(ticket).status == "UNVERIFIED_TIMEOUT"
    assert camera[0].restore_cockpit(ticket).status == "REFUSED"


@pytest.mark.parametrize("field,value", [("epoch", "load2"), ("valid", False),
    ("issued", None), ("time", 9), ("time", float("inf")), ("throw_observe", True)])
def test_observation_refuses_stale_or_unreadable_evidence(camera, field, value):
    ticket = requested(camera)
    camera[1][field] = value
    r = camera[0].observe(ticket)
    assert r.status == "UNCERTAIN" and r.action_attempted
    assert r.attempts.softtarget and r.attempts.target_view and not r.attempts.cockpit
    assert camera[1].writes == 2


@pytest.mark.parametrize("external", [None, False, True, "UNKNOWN", -1, 0.5, float("nan"), float("inf")])
def test_invalid_external_reference_is_unavailable_not_a_valid_mismatch(camera, external):
    ticket = requested(camera)
    camera[1].time = 11
    camera[1].external = external
    r = camera[0].observe(ticket)
    assert r.status == "UNCERTAIN" and r.action_attempted
    assert r.attempts.target_view and camera[1].writes == 2


def test_valid_different_external_reference_remains_pending_with_attempt_facts(camera):
    ticket = requested(camera)
    camera[1].time = 11
    camera[1].external = 99
    r = camera[0].observe(ticket)
    assert r.status == "PENDING" and r.action_attempted
    assert r.attempts.softtarget and r.attempts.target_view


def test_changed_camera_relinquishes_restoration(camera):
    ticket = verified(camera)
    camera[1].external = 99
    assert camera[0].observe(ticket).status == "DIVERGED"
    assert camera[0].restore_cockpit(ticket).status == "REFUSED"
    assert camera[1].writes == 2


def test_cockpit_request_is_guarded_once_and_not_a_completion_claim(camera):
    ticket = verified(camera)
    api, state, calls, _ = camera
    r = api.restore_cockpit(ticket)
    assert r.status == "RESTORE_REQUESTED_UNVERIFIED" and r.action_attempted
    assert r.attempts.softtarget and r.attempts.target_view and r.attempts.cockpit
    assert calls[3][1] == "cockpit" and calls[3][2] is True
    assert api.restore_cockpit(ticket).status == "REFUSED"
    assert state.writes == 3


@pytest.mark.parametrize("field,value", [("epoch", "load2"), ("external", 99),
    ("valid", False), ("possible", False), ("time", 9)])
def test_restoration_rechecks_ownership_and_context(camera, field, value):
    ticket = verified(camera)
    camera[1][field] = value
    assert camera[0].restore_cockpit(ticket).status == "REFUSED"
    assert camera[1].writes == 2


def test_cockpit_exception_has_unknown_outcome_and_no_retry(camera):
    ticket = verified(camera)
    camera[1].throw_restore = True
    assert camera[0].restore_cockpit(ticket).status == "UNCERTAIN"
    assert camera[0].restore_cockpit(ticket).status == "REFUSED"
    assert camera[1].writes == 3

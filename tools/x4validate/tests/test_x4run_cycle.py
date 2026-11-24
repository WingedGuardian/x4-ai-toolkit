r"""x4run cycle: start, wait for ready, run the caller's command, ALWAYS close; stop; forget.

Everything goes through `FakeSystem`; nothing here starts Steam or X4, and a close, a terminate
and the caller's command are only ever calls made to the scripted fake runner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _x4run as X

GAME_ROOT = Path("steamroot/steamapps/common/X4 Foundations")
STEAM = make_proc(900, name="steam.exe", path="S:/steam/steam.exe")
GAME = make_proc(1000)
CMD = ["python", "tests/e2e_driver.py", "--flag"]
_UNSET = object()


def _approval() -> X.Approval:
    return X.Approval(approved_by="user", mode="menu-only", target="main menu",
                      desktop="secondary desktop", recorded_at="2026-11-24T10:00:00Z")


def _world(tmp_path, **command_kw):
    """Steam running; a launch makes the game appear; a window-close ends it; CMD is scripted."""
    fake = FakeSystem([STEAM])
    fake.launch_spawns(GAME, after=5)
    fake.close_exits_after(3)
    fake.script_command(CMD, **command_kw)
    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    return fake, store


def _cycle(fake, store, probe=lambda: True, command=CMD, approval=_UNSET, **kw):
    return X.run_cycle(GAME_ROOT, _approval() if approval is _UNSET else approval, store,
                       command, ready_probe=probe, run=fake.run, sleep=fake.sleep,
                       clock=fake.clock, **kw)


def _kinds(fake) -> list[str]:
    """What each call the fake saw was, in order: read / launch / close / terminate / command."""
    return [fake._classify(c.argv) for c in fake.calls]


def _launches(fake):
    return [k for k in _kinds(fake) if k == "launch"]


def _assert_game_closed(fake, store, rep):
    assert rep.close is not None and rep.close.closed
    assert len(fake.close_calls) == 1
    assert not store.path.exists()
    assert fake.table == [STEAM]  # the game is gone, Steam is untouched


# ---------------------------------------------------------------------------- the happy path


def test_a_successful_cycle_starts_waits_runs_and_closes(tmp_path):
    fake, store = _world(tmp_path)
    rep = _cycle(fake, store)
    assert (rep.started, rep.ready, rep.command_rc) == (True, True, 0)
    _assert_game_closed(fake, store, rep)
    kinds = _kinds(fake)
    assert kinds.index("launch") < kinds.index("command") < kinds.index("close")
    assert kinds.count("command") == 1 and len(_launches(fake)) == 1
    assert rep.notes == ()


def test_every_call_including_the_callers_command_is_hidden_and_never_a_shell(tmp_path):
    fake, store = _world(tmp_path)
    _cycle(fake, store, command_timeout_s=123.0)
    assert fake.calls and all(c.creationflags == X.NO_WINDOW for c in fake.calls)
    if sys.platform == "win32":
        assert X.NO_WINDOW == 0x08000000
    (cmd,) = fake.command_calls
    assert cmd.argv == tuple(CMD)  # the argv list, untouched
    assert cmd.creationflags == X.NO_WINDOW
    assert cmd.timeout == 123.0
    assert dict(cmd.kw).get("shell", False) is False


@pytest.mark.parametrize("bad, err", [
    ("python run.py", TypeError), (None, TypeError), (["python", 3], TypeError),
    (("python", "x"), TypeError), ([], ValueError), ([""], ValueError),
])
def test_a_command_that_is_not_an_argv_list_is_refused_before_anything_is_started(tmp_path, bad, err):
    fake, store = _world(tmp_path)
    with pytest.raises(err):
        _cycle(fake, store, command=bad)
    assert fake.calls == [] and not store.path.exists()


def test_the_ready_probe_is_polled_about_every_five_seconds(tmp_path):
    fake, store = _world(tmp_path)
    times: list[float] = []

    def probe():
        times.append(fake.now)
        return fake.now - times[0] >= 20

    rep = _cycle(fake, store, probe=probe)
    assert rep.ready and rep.command_rc == 0
    assert [round(b - a, 6) for a, b in zip(times, times[1:])] == [5.0, 5.0, 5.0, 5.0]


# ------------------------------------------------------------- the game is ALWAYS closed


def test_the_game_is_closed_when_the_command_fails(tmp_path):
    fake, store = _world(tmp_path, rc=3)
    rep = _cycle(fake, store)
    assert (rep.started, rep.ready, rep.command_rc) == (True, True, 3)
    _assert_game_closed(fake, store, rep)


def test_the_game_is_closed_when_the_command_times_out(tmp_path):
    fake, store = _world(tmp_path, timeout=True)
    rep = _cycle(fake, store)
    assert (rep.started, rep.ready, rep.command_rc) == (True, True, None)
    assert any("timed out" in n for n in rep.notes)
    _assert_game_closed(fake, store, rep)


def test_the_game_is_closed_when_the_command_cannot_be_started(tmp_path):
    fake, store = _world(tmp_path, exc=FileNotFoundError("no such program"))
    rep = _cycle(fake, store)
    assert rep.command_rc is None and rep.ready
    assert any("no such program" in n for n in rep.notes)
    _assert_game_closed(fake, store, rep)


def test_the_game_is_closed_when_the_probe_never_becomes_ready(tmp_path):
    fake, store = _world(tmp_path)
    times: list[float] = []

    def probe():
        times.append(fake.now)
        return False

    rep = _cycle(fake, store, probe=probe, ready_timeout_s=30.0)
    assert (rep.started, rep.ready, rep.command_rc) == (True, False, None)
    assert fake.command_calls == []  # never run against a game that is not ready
    assert any("not ready" in n for n in rep.notes)
    assert times[-1] - times[0] >= 30.0  # it really waited the whole time
    _assert_game_closed(fake, store, rep)


def test_a_probe_that_raises_counts_as_not_ready_and_is_recorded(tmp_path):
    fake, store = _world(tmp_path)

    def probe():
        raise RuntimeError("x4live exploded")

    rep = _cycle(fake, store, probe=probe, ready_timeout_s=20.0)
    assert (rep.ready, rep.command_rc) == (False, None)
    assert fake.command_calls == []
    assert any("x4live exploded" in n for n in rep.notes)
    _assert_game_closed(fake, store, rep)


def test_a_probe_that_raises_once_and_then_succeeds_is_ready(tmp_path):
    fake, store = _world(tmp_path)
    seen = []

    def probe():
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("not up yet")
        return True

    rep = _cycle(fake, store, probe=probe)
    assert rep.ready and rep.command_rc == 0
    assert any("not up yet" in n for n in rep.notes)
    _assert_game_closed(fake, store, rep)


def test_a_keyboard_interrupt_still_closes(tmp_path):
    fake, store = _world(tmp_path, exc=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt) as exc:
        _cycle(fake, store)
    assert len(fake.close_calls) == 1
    assert fake.table == [STEAM] and not store.path.exists()
    # the close outcome is not lost with the report: it rides on the exception
    assert any("closed" in n for n in getattr(exc.value, "__notes__", []))


def test_a_keyboard_interrupt_in_the_probe_still_closes(tmp_path):
    fake, store = _world(tmp_path)

    def probe():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, store, probe=probe)
    assert len(fake.close_calls) == 1 and fake.command_calls == []
    assert not store.path.exists()


def test_a_game_that_will_not_close_is_reported_as_not_closed_plainly(tmp_path):
    fake, store = _world(tmp_path)
    fake.on_close = FakeSystem._default_hook  # the close request is ignored forever...
    fake.on_terminate = FakeSystem._default_hook  # ...and so is the terminate
    rep = _cycle(fake, store, grace_s=10.0, kill_wait_s=10.0)
    assert rep.close is not None and not rep.close.closed
    assert rep.close.method == "unclosed"
    assert store.path.exists()  # kept: the game is still unaccounted for
    assert any("NOT" in n and "closed" in n and "X4.exe" in n for n in rep.notes)


def test_close_keywords_are_passed_through(tmp_path):
    fake, store = _world(tmp_path)
    fake.on_close = FakeSystem._default_hook
    rep = _cycle(fake, store, grace_s=7.0, kill_wait_s=4.0, hold_s=0.0)
    assert any("7 s" in s for s in rep.close.steps)


# --------------------------------------------------------------------------- refused starts


def test_a_refused_start_closes_nothing(tmp_path):
    fake, store = _world(tmp_path)
    fake.table = []  # Steam is not running: start refuses
    rep = _cycle(fake, store)
    assert (rep.started, rep.ready, rep.command_rc, rep.close) == (False, False, None, None)
    assert any("Steam is not running" in n for n in rep.notes)
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert fake.command_calls == [] and _launches(fake) == []
    assert not store.path.exists()
    assert not any("run record" in n and "kept" in n.lower() for n in rep.notes)


def test_a_refusal_that_kept_a_run_record_says_so(tmp_path):
    fake, store = _world(tmp_path)
    fake.launch_spawns(GAME, make_proc(2000, started="2026-11-24T10:00:01.0000000Z"), after=5)
    rep = _cycle(fake, store)
    assert (rep.started, rep.close) == (False, None)
    assert store.path.exists()  # kept by start: a game may be starting
    assert any("run record" in n and str(store.path) in n for n in rep.notes)
    assert fake.close_calls == [] and fake.terminate_calls == []  # never touches an unrecorded game
    assert fake.command_calls == []


def test_an_unfinished_run_propagates_and_changes_nothing(tmp_path):
    fake, store = _world(tmp_path)
    X.start(GAME_ROOT, _approval(), store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    fake.table = [STEAM]  # the earlier game is gone but its record is still there
    before = store.path.read_bytes()
    n_calls = len(fake.calls)
    with pytest.raises(X.UnfinishedRun):
        _cycle(fake, store)
    assert store.path.read_bytes() == before  # the earlier run's record is untouched
    assert fake.close_calls == [] and fake.terminate_calls == [] and fake.command_calls == []
    assert len(_launches(fake)) == 1  # no second launch
    assert all(fake._classify(c.argv) == "read" for c in fake.calls[n_calls:])


@pytest.mark.parametrize("bad", [None, X.Approval("u", "headless", "t", "d", "r")])
def test_an_unapproved_launch_propagates_before_any_call(tmp_path, bad):
    fake, store = _world(tmp_path)
    with pytest.raises(X.UnapprovedLaunch):
        _cycle(fake, store, approval=bad)
    assert fake.calls == [] and not store.path.exists()


@pytest.mark.parametrize("exc", [
    X.UnapprovedLaunch("a"), X.UnfinishedRun("b"), X.InvalidState("c"), X.CorruptState("d"),
])
def test_only_a_start_refusal_is_caught(tmp_path, monkeypatch, exc):
    """The four non-refusals must come out of `run_cycle` unchanged (the same object)."""
    fake, store = _world(tmp_path)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(X, "start", boom)
    with pytest.raises(type(exc)) as got:
        _cycle(fake, store)
    assert got.value is exc
    assert fake.calls == []


# ------------------------------------------------------------------------------------ stop


def test_stop_closes_the_recorded_process_from_the_state_file(tmp_path):
    """Review Focus 6: the driver died mid-run. The next run refuses; stop still closes the game."""
    fake, store = _world(tmp_path)
    X.start(GAME_ROOT, _approval(), store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert fake.table == [STEAM, GAME] and store.path.exists()
    # the driver process is gone; a fresh store object over the same folder is all that is left
    fresh = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    nxt = _cycle(fake, fresh)  # the game is still up, so the next run refuses at the preflight
    assert (nxt.started, nxt.close) == (False, None)
    assert any("already running" in n for n in nxt.notes)
    assert any("x4run stop" in n and str(store.path) in n for n in nxt.notes)
    assert len(_launches(fake)) == 1 and fake.close_calls == [] and fake.command_calls == []
    rep = X.stop(fresh, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert rep is not None and rep.closed and rep.method == "window-close"
    assert [c.argv[-1] for c in fake.close_calls] == [str(GAME.pid)]  # the recorded pid only
    assert fake.table == [STEAM] and not store.path.exists()
    assert fake.terminate_calls == []


def test_stop_never_touches_an_unrecorded_x4(tmp_path):
    fake, store = _world(tmp_path)
    X.start(GAME_ROOT, _approval(), store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    other = make_proc(2000, started="2026-11-24T11:00:00.0000000Z")
    fake.table.append(other)
    rep = X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert rep.closed and [c.argv[-1] for c in fake.close_calls] == ["1000"]
    assert other in fake.table and fake.terminate_calls == []


def test_stop_with_no_state_file_reports_nothing_to_stop(tmp_path):
    fake, store = _world(tmp_path)
    assert X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock) is None
    assert fake.calls == []  # not even a read


def test_stop_on_an_empty_record_says_what_to_do_instead_of_raising_the_bare_error(tmp_path):
    """The crash window between the O_EXCL create and the first write leaves an EMPTY file."""
    fake, store = _world(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_bytes(b"")
    with pytest.raises(X.StopCannotRead) as exc:
        X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    msg = str(exc.value)
    assert str(store.path) in msg and "empty" in msg
    assert "X4.exe" in msg and "by hand" in msg and "x4run forget" in msg
    assert store.path.exists() and store.path.read_bytes() == b""  # left in place
    assert fake.calls == []  # nothing was read, closed or terminated on a guess


def test_stop_on_a_corrupt_record_names_the_file_and_what_was_found(tmp_path):
    fake, store = _world(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_bytes(b'{"run_id": "r1", "phase"')  # a torn write
    with pytest.raises(X.StopCannotRead) as exc:
        X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    msg = str(exc.value)
    assert str(store.path) in msg and "empty" not in msg
    assert "24 bytes" in msg and "x4run forget" in msg and "by hand" in msg
    assert store.path.read_bytes() == b'{"run_id": "r1", "phase"'
    assert fake.calls == []
    assert isinstance(exc.value, RuntimeError)


def test_stop_on_a_starting_record_with_no_process_closes_nothing_and_says_so(tmp_path):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)  # a game IS running, but it was never recorded
    state = X.RunState(run_id="r1", approval=_approval(), phase="starting", process=None,
                       started_at="2026-11-24T10:00:00+00:00")
    store.begin(state)
    rep = X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert rep is not None and not rep.closed and rep.method == "unclosed"
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert GAME in fake.table and store.path.exists()  # kept, and the game untouched
    assert rep.remaining == (GAME,)  # reported, never touched
    assert any("no process was recorded" in s and "KEPT" in s for s in rep.steps)


def test_stop_when_the_recorded_game_has_already_gone_clears_the_record(tmp_path):
    fake, store = _world(tmp_path)
    X.start(GAME_ROOT, _approval(), store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    fake.table = [STEAM]  # the game quit by itself
    rep = X.stop(store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert (rep.closed, rep.method) == (True, "already-gone")
    assert fake.close_calls == [] and not store.path.exists()


# ---------------------------------------------------------------------------------- forget


def _record(tmp_path, fake, content=None):
    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    store.folder.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(content if content is not None else b'{"x": 1}\n')
    return store


def _forget(fake, store):
    return X.forget(store, run=fake.run, sleep=fake.sleep)


def test_forget_clears_the_record_after_a_good_read_shows_no_x4(tmp_path):
    fake = FakeSystem([STEAM])  # Steam is not X4.exe
    store = _record(tmp_path, fake)
    msg = _forget(fake, store)
    assert not store.path.exists()
    assert str(store.path) in msg and "removed" in msg
    assert len(fake.calls) == 1 and "Name = 'X4.exe'" in " ".join(fake.calls[0].argv)
    assert fake.close_calls == [] and fake.terminate_calls == []


def test_forget_reads_before_it_clears(tmp_path):
    fake = FakeSystem([STEAM])
    store = _record(tmp_path, fake)
    existed_at_read: list[bool] = []
    fake.on_read = lambda f, n: existed_at_read.append(store.path.exists())
    _forget(fake, store)
    assert existed_at_read == [True]  # the file was still there when the read was made
    assert not store.path.exists()


def test_forget_refuses_while_an_x4_is_running(tmp_path):
    fake = FakeSystem([STEAM, GAME])
    store = _record(tmp_path, fake)
    with pytest.raises(X.ForgetRefused) as exc:
        _forget(fake, store)
    assert "1000" in str(exc.value) and str(store.path) in str(exc.value)
    assert store.path.read_bytes() == b'{"x": 1}\n'  # untouched
    assert fake.close_calls == [] and fake.terminate_calls == []  # forget never closes anything
    assert GAME in fake.table


def test_forget_refuses_on_a_failed_read_the_third_state(tmp_path):
    fake = FakeSystem([STEAM, GAME])  # a game IS running but the read cannot say so
    fake.fail_reads(10)
    store = _record(tmp_path, fake)
    with pytest.raises(X.ForgetRefused) as exc:
        _forget(fake, store)
    assert "could not be read" in str(exc.value)
    assert store.path.exists()


@pytest.mark.parametrize("content", [b"", b"\x00\xff garbage", b'{"run_id": "r1", "phase"'])
def test_forget_works_on_an_empty_or_corrupt_record_without_parsing_it(tmp_path, content):
    fake = FakeSystem([STEAM])
    store = _record(tmp_path, fake, content)
    msg = _forget(fake, store)
    assert not store.path.exists()
    assert str(store.path) in msg and f"{len(content)} bytes" in msg


def test_forget_with_no_record_says_there_is_nothing_to_forget(tmp_path):
    fake = FakeSystem([STEAM, GAME])
    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    msg = _forget(fake, store)
    assert "nothing to forget" in msg
    assert fake.calls == []  # no read was needed


def test_forget_reports_a_record_it_could_not_remove(tmp_path):
    fake = FakeSystem([STEAM])
    store = _record(tmp_path, fake)

    def deny():
        raise PermissionError("locked")
    store.clear = deny
    with pytest.raises(X.ForgetRefused, match="could not be removed"):
        _forget(fake, store)
    assert store.path.exists()


def test_forget_refuses_when_the_read_times_out(tmp_path):
    fake = FakeSystem([STEAM])
    fake.timeout_reads(3)
    store = _record(tmp_path, fake)
    with pytest.raises(X.ForgetRefused):
        _forget(fake, store)
    assert store.path.exists()
    assert isinstance(X.ForgetRefused("x"), RuntimeError)

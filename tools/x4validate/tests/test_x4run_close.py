r"""x4run close: the ladder that only ever touches the RECORDED process.

Everything goes through `FakeSystem`; nothing here closes, terminates or could close a real
process. A close or terminate is only ever a call made to the scripted fake runner, and the
fake's virtual clock makes every wait instantaneous.
"""
from __future__ import annotations

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _x4run as X

REC = make_proc(1000)
OTHER = make_proc(2000, started="2026-11-24T10:05:00.0000000Z")  # an unrelated second X4
IMPOSTOR = make_proc(1000, started="2026-11-24T12:00:00.0000000Z")  # same pid, a new process


def _approval() -> X.Approval:
    return X.Approval(approved_by="user", mode="menu-only", target="main menu",
                      desktop="secondary desktop", recorded_at="2026-11-24T10:00:00Z")


def _state(process=REC, phase="running") -> X.RunState:
    return X.RunState(run_id="r1", approval=_approval(), phase=phase, process=process,
                      started_at="2026-11-24T10:00:00+00:00")


def _world(tmp_path, *procs, state=None):
    """A fake world holding `procs`, a store that holds the run record, and that record."""
    fake = FakeSystem(list(procs))
    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    state = state or _state()
    store.begin(state)
    return fake, store, state


def _close(fake, store, state, **kw):
    return X.close(state, store, run=fake.run, sleep=fake.sleep, clock=fake.clock, **kw)


def _reads(fake):
    return [c for c in fake.calls if "Get-CimInstance" in " ".join(c.argv)]


def _record_time(fake, attr):
    """Make the `attr` hook ('on_close' / 'on_terminate') note the virtual time of each call."""
    times: list[float] = []
    inner = getattr(fake, attr)

    def hook(argv):
        times.append(fake.now)
        return inner(argv)
    setattr(fake, attr, hook)
    return times


# ------------------------------------------------------------------------------ already gone


def test_already_gone_makes_no_call(tmp_path):
    fake, store, state = _world(tmp_path)  # nothing is running
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "already-gone")
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert len(fake.calls) == len(_reads(fake)) > 0  # only reads were made
    assert not store.path.exists()  # accounted for: the record is cleared
    assert rep.remaining == ()


def test_a_reused_pid_is_never_closed_or_terminated(tmp_path):
    """Review Focus 1: the recorded PID is alive again with another start time."""
    fake, store, state = _world(tmp_path, IMPOSTOR)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "already-gone")
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert rep.remaining == (IMPOSTOR,)  # reported, never touched
    assert not store.path.exists()


@pytest.mark.parametrize("field, value", [
    ("name", "Other.exe"), ("path", "G:/elsewhere/X4.exe"),
    ("started", "2026-11-24T10:00:00.0000001Z"),
])
def test_a_process_that_differs_in_any_one_field_is_not_the_recorded_one(tmp_path, field, value):
    other = make_proc(**{"pid": 1000, field: value})
    fake, store, state = _world(tmp_path, other)
    # name differs -> the read (filtered by X4.exe) does not even return it; both are "gone"
    rep = _close(fake, store, state)
    assert rep.method == "already-gone"
    assert fake.close_calls == [] and fake.terminate_calls == []


def test_one_missing_read_is_not_gone(tmp_path):
    """The first read lies (the process is hidden once): it is still closed, not 'already gone'."""
    fake, store, state = _world(tmp_path, REC)
    full = list(fake.table)

    def hide_first(f, n):
        if n == 0:
            f.table = []
        elif n == 1:
            f.table = full
    fake.on_read = hide_first
    fake.close_exits_after(3)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "window-close")
    assert len(fake.close_calls) == 1


def test_a_pid_reused_between_the_first_read_and_the_close_request_is_not_closed(tmp_path):
    """The start time is re-checked by a second read right before the close request."""
    fake, store, state = _world(tmp_path, REC)

    def swap_on_second_read(f, n):
        if n == 1:
            f.table = [IMPOSTOR]
    fake.on_read = swap_on_second_read
    rep = _close(fake, store, state)
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert rep.method == "already-gone"
    assert rep.remaining == (IMPOSTOR,)


# ------------------------------------------------------------------------------ window close


def test_window_close_that_works_never_terminates(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(5)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "window-close")
    assert len(fake.close_calls) == 1
    assert fake.terminate_calls == []
    assert fake.close_calls[0].argv[-1] == "1000"  # the recorded pid, as its own argument
    assert rep.remaining == ()
    assert not store.path.exists()


def test_the_close_request_is_re_checked_by_a_read_right_before_it(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(5)
    _close(fake, store, state)
    i = fake.calls.index(fake.close_calls[0])
    assert i >= 2 and all("Get-CimInstance" in " ".join(c.argv) for c in fake.calls[i - 2:i])


def test_watching_stops_as_soon_as_the_exit_is_confirmed(tmp_path):
    """ExitWatch is not latched: no read is made after it returned True (but the final one)."""
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(5)
    _close(fake, store, state)
    i = fake.calls.index(fake.close_calls[0])
    after = [c for c in fake.calls[i + 1:]]
    # exits at 5; polls at 2, 4 (present), 6, 8, 10 (three misses) = 5 watch reads, + 1 final read
    assert len(after) == 6 and all(c in _reads(fake) for c in after)


def test_the_phase_is_set_to_closing_before_any_call(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    seen = []

    def hook(argv):
        seen.append(store.read().phase)
        return 0, ""
    fake.on_close = hook
    fake.on_read = lambda f, n: seen.append(store.read().phase) if n == 0 else None
    _close(fake, store, state, grace_s=2, kill_wait_s=2)
    assert seen[0] == "closing" and set(seen) == {"closing"}


# ----------------------------------------------------------------------------- the escalation


def test_an_ignored_window_close_escalates_only_after_the_grace_period(tmp_path):
    """Review Focus 5 (the 2026-11-03 quit freeze): the window close is ignored forever."""
    fake, store, state = _world(tmp_path, REC, OTHER)
    fake.terminate_removes()  # only the terminate gets rid of it
    close_t = _record_time(fake, "on_close")
    kill_t = _record_time(fake, "on_terminate")
    rep = _close(fake, store, state, grace_s=60, kill_wait_s=30)
    assert len(close_t) == 1 and len(kill_t) == 1
    assert kill_t[0] - close_t[0] >= 60  # never before the grace period has elapsed
    assert kill_t[0] - close_t[0] < 60 + 10  # ... and not long after it
    assert (rep.closed, rep.method) == (True, "terminate")
    assert fake.table == [OTHER]  # only the recorded process is gone
    assert not store.path.exists()


def test_no_terminate_call_is_made_while_the_grace_period_runs(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    kill_t = _record_time(fake, "on_terminate")
    seen_reads_before_kill: list[float] = []
    inner = fake.on_read

    def watch(f, n):
        if not kill_t:
            seen_reads_before_kill.append(f.now)
        if inner:
            inner(f, n)
    fake.on_read = watch
    _close(fake, store, state, grace_s=40, kill_wait_s=4)
    assert len(kill_t) == 1 and kill_t[0] >= 40
    # the window was watched throughout, up to the end of the grace period
    assert max(seen_reads_before_kill) >= 40


def test_hold_s_extends_the_grace_period(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    close_t = _record_time(fake, "on_close")
    kill_t = _record_time(fake, "on_terminate")
    _close(fake, store, state, grace_s=20, kill_wait_s=4, hold_s=100)
    assert kill_t[0] - close_t[0] >= 120


def test_a_window_close_that_lands_inside_the_held_period_is_never_terminated(tmp_path):
    # a crash dump takes 90 s to write: the exit lands at +90, past grace_s=20 but inside hold
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(90)
    rep = _close(fake, store, state, grace_s=20, kill_wait_s=4, hold_s=100)
    assert (rep.closed, rep.method) == (True, "window-close")
    assert fake.terminate_calls == []
    # ... and without the hold the same exit is too late
    fake, store, state = _world(tmp_path / "b", REC)
    fake.close_exits_after(90)
    _close(fake, store, state, grace_s=20, kill_wait_s=4, hold_s=0)
    assert len(fake.terminate_calls) == 1


def test_terminate_targets_only_the_recorded_pid_after_a_start_time_check(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    fake.terminate_removes()
    _close(fake, store, state, grace_s=10, kill_wait_s=10)
    assert len(fake.terminate_calls) == 1
    kill = fake.terminate_calls[0]
    assert kill.argv[-1] == "1000"
    assert "2000" not in " ".join(kill.argv)
    i = fake.calls.index(kill)
    assert "Get-CimInstance" in " ".join(fake.calls[i - 1].argv)  # the re-check read, just before


def test_a_pid_reused_just_before_the_terminate_is_not_terminated(tmp_path):
    """The start time is re-checked once more before the terminate: the grace period ends with
    the recorded process replaced by another one with the same PID."""
    fake, store, state = _world(tmp_path, REC)
    fake.at(30, lambda f: setattr(f, "table", [IMPOSTOR]))
    rep = _close(fake, store, state, grace_s=30, kill_wait_s=10)
    assert fake.terminate_calls == []
    assert rep.closed is True and rep.remaining == (IMPOSTOR,)
    assert fake.table == [IMPOSTOR]


def test_a_second_unrelated_X4_is_left_alone_and_listed_in_remaining(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    fake.close_exits_after(3)
    rep = _close(fake, store, state)
    assert rep.closed is True
    assert rep.remaining == (OTHER,)
    assert fake.terminate_calls == []
    for c in fake.close_calls:
        assert "2000" not in " ".join(c.argv)
    assert fake.table == [OTHER]


# ------------------------------------------------------------------------------ failed reads


def test_a_failed_read_during_the_wait_is_not_gone(tmp_path):
    """Every read after the first two fails and the table is empty: that is NOT 'exited'."""
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(1)    # the game IS gone after the request ...
    fake.good_reads(2)           # (the initial read and the re-check)
    fake.fail_reads(10_000)      # ... but nothing can be read any more: unknowable from here
    rep = _close(fake, store, state, grace_s=20, kill_wait_s=10)
    assert rep.closed is False and rep.method == "unclosed"
    assert store.path.exists()
    assert fake.terminate_calls == []  # never terminated on a read it could not make
    # two earlier reads DID succeed and saw the process: that stale list is never reported
    assert rep.remaining is None


def test_a_failed_read_before_the_close_request_makes_no_call(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.fail_reads(10_000)
    rep = _close(fake, store, state, grace_s=20, kill_wait_s=10)
    assert rep.closed is False and rep.method == "unclosed"
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert store.path.exists()
    assert any("could not" in s for s in rep.steps)
    assert rep.remaining is None  # nothing was ever read successfully


def test_transient_read_failures_delay_the_exit_but_never_fake_it(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(1)
    fake.good_reads(2)           # initial + re-check
    for _ in range(4):           # then a failed read between every good one
        fake.fail_reads(3)       # (one whole read = three attempts)
        fake.good_reads(1)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "window-close")
    assert fake.terminate_calls == []


# ------------------------------------------------------------------------------- unclosed


def test_unclosed_keeps_the_state_file_and_says_so(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    # neither the close request nor the terminate does anything: the process survives
    rep = _close(fake, store, state, grace_s=10, kill_wait_s=10)
    assert (rep.closed, rep.method) == (False, "unclosed")
    assert store.path.exists()
    assert store.read().phase == "closing"
    assert store.read().process == REC
    assert set(rep.remaining) == {REC, OTHER}
    assert any("KEPT" in s for s in rep.steps)
    assert len(fake.terminate_calls) == 1  # asked once, not retried


def test_a_failing_close_request_still_waits_before_anything_else(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.on_close = lambda argv: (3, "")  # no main window: the request was not delivered
    close_t = _record_time(fake, "on_close")
    kill_t = _record_time(fake, "on_terminate")
    rep = _close(fake, store, state, grace_s=20, kill_wait_s=4)
    assert kill_t[0] - close_t[0] >= 20
    assert any("window-close request" in s and "3" in s for s in rep.steps)


def test_a_terminate_call_that_raises_is_reported_not_raised(tmp_path):
    fake, store, state = _world(tmp_path, REC)

    def boom(argv):
        raise OSError("no powershell")
    fake.on_terminate = boom
    rep = _close(fake, store, state, grace_s=4, kill_wait_s=4)
    assert rep.closed is False and store.path.exists()
    assert any("no powershell" in s for s in rep.steps)


def test_a_recorded_process_still_present_on_the_final_read_is_not_closed(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(1)
    # reads: 0 initial, 1 re-check, 2-4 the three confirming misses, 5 the final read, where
    # the same process is back (impossible for one process, but a contradiction is never "closed")
    fake.on_read = lambda f, n: setattr(f, "table", [REC]) if n == 5 else None
    rep = _close(fake, store, state)
    assert rep.closed is False
    assert rep.remaining == (REC,)
    assert store.path.exists()


# ----------------------------------------------------------------------- no process recorded


def test_a_starting_record_with_no_process_is_never_closed(tmp_path):
    """Ruling 10: nothing recorded means nothing to close: no close, no terminate, record kept."""
    state = _state(process=None, phase="starting")
    fake, store, state = _world(tmp_path, REC, OTHER, state=state)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (False, "unclosed")
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert len(fake.calls) == 1  # one read of all X4.exe
    assert set(rep.remaining) == {REC, OTHER}
    assert store.path.exists()
    assert any("no process was recorded" in s for s in rep.steps)


def test_a_starting_record_whose_read_fails_still_makes_no_call(tmp_path):
    state = _state(process=None, phase="starting")
    fake, store, state = _world(tmp_path, REC, state=state)
    fake.fail_reads(10)
    rep = _close(fake, store, state)
    assert rep.closed is False
    assert rep.remaining is None  # a failed read is UNKNOWN, never "no X4 running"
    assert fake.close_calls == [] and fake.terminate_calls == []
    assert store.path.exists()
    assert any("unknown" in s for s in rep.steps)


# ------------------------------------------------------------------------ hidden, bounded


def test_every_call_is_hidden(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    fake.terminate_removes()
    _close(fake, store, state, grace_s=6, kill_wait_s=6)
    assert fake.close_calls and fake.terminate_calls  # the whole ladder ran
    assert fake.calls
    for c in fake.calls:
        assert c.creationflags == X.NO_WINDOW == 0x08000000
        assert c.creationflags & 0x00000008 == 0  # never DETACHED_PROCESS
        assert isinstance(c.argv, tuple) and c.argv[0] == "powershell.exe"
        assert not any(k == "shell" and v for k, v in c.kw)


def test_the_close_and_terminate_scripts_are_separate_and_take_a_plain_pid():
    assert "CloseMainWindow" in X._CLOSE_SCRIPT and "Stop-Process" not in X._CLOSE_SCRIPT
    assert "Stop-Process" in X._TERMINATE_SCRIPT and "CloseMainWindow" not in X._TERMINATE_SCRIPT
    for script in (X._CLOSE_SCRIPT, X._TERMINATE_SCRIPT):
        assert "[int]$ProcId" in script          # the pid is a typed parameter ...
        assert not any(ch.isdigit() for ch in script.replace("exit 3", ""))  # ... never a pid in the text
        assert "-Force" not in script and "taskkill" not in script


@pytest.mark.parametrize("bad", [0, -4])
def test_a_non_positive_recorded_pid_is_never_passed_to_any_call(tmp_path, bad):
    proc = X.ProcessInfo(pid=bad, name="X4.exe", path="G:/games/X4/X4.exe",
                         started="2026-11-24T10:00:00.0000000Z")
    fake, store, state = _world(tmp_path, proc, state=_state(process=proc))
    rep = _close(fake, store, state)
    assert rep.closed is False
    assert fake.calls == []
    assert rep.remaining is None  # no read was made: unknown, not empty
    assert store.path.exists()


def test_an_invalid_record_is_refused_before_any_call(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    state.approval = X.Approval("user", "headless", "t", "d", "r")  # a mode `read` would reject
    with pytest.raises(X.InvalidState):
        _close(fake, store, state)
    assert fake.calls == []


@pytest.mark.parametrize("kw", [{"grace_s": -1}, {"kill_wait_s": -1}, {"hold_s": -1},
                                {"grace_s": float("nan")}])
def test_negative_waits_are_refused_before_any_call(tmp_path, kw):
    fake, store, state = _world(tmp_path, REC)
    with pytest.raises(ValueError):
        _close(fake, store, state, **kw)
    assert fake.calls == [] and store.read().phase == "running"


def test_the_fake_refuses_a_script_that_is_both_a_close_and_a_terminate():
    fake = FakeSystem([REC])
    with pytest.raises(AssertionError, match="exactly one kind"):
        fake.run(["powershell.exe", "-Command", "$p.CloseMainWindow(); Stop-Process -Id 1"],
                 creationflags=X.NO_WINDOW)


# ------------------------------------------------- remaining: UNKNOWN is not NONE (ruling 11)


def test_a_failed_final_read_after_a_confirmed_exit_leaves_remaining_unknown(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)  # an unrelated X4 IS running
    fake.close_exits_after(1)
    fake.good_reads(5)   # initial, re-check, three confirming absent reads ...
    fake.fail_reads(3)   # ... then the final read fails (one read = three attempts)
    rep = _close(fake, store, state)
    assert (rep.closed, rep.method) == (True, "window-close")  # the exit was confirmed
    assert rep.remaining is None  # never () while OTHER is running
    assert any("final" in s and "read failed" in s and "unknown" in s for s in rep.steps)
    assert not store.path.exists()  # the record logic is unchanged: it depends on the watch


def test_a_successful_empty_final_read_is_none_remaining_not_unknown(tmp_path):
    fake, store, state = _world(tmp_path, REC)
    fake.close_exits_after(1)
    rep = _close(fake, store, state)
    assert rep.closed is True and rep.remaining == ()  # a read succeeded and saw none


def test_a_keep_path_with_an_earlier_good_read_but_a_failing_last_read_is_unknown(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    fake.good_reads(8)       # initial + re-check + the watch: the process is still there
    fake.fail_reads(10_000)  # then the kill-wait and the final read cannot be read
    rep = _close(fake, store, state, grace_s=4, kill_wait_s=6)
    assert rep.closed is False and rep.method == "unclosed"
    assert rep.remaining is None  # not the stale (REC, OTHER) of the last good read
    assert store.path.exists()


def test_a_keep_path_whose_last_read_succeeded_reports_that_read(tmp_path):
    fake, store, state = _world(tmp_path, REC, OTHER)
    rep = _close(fake, store, state, grace_s=4, kill_wait_s=4)
    assert rep.closed is False
    assert set(rep.remaining) == {REC, OTHER}


# ------------------------------------------------------------ a terminate call that fails


@pytest.mark.parametrize("how, expect", [("exit", "exited 1"), ("oserror", "powershell is gone"),
                                         ("timeout", "timed out")])
def test_a_failed_terminate_call_is_recorded_even_if_the_process_then_ends(tmp_path, how, expect):
    import subprocess

    fake, store, state = _world(tmp_path, REC)

    def hook(argv):
        if how == "exit":
            return 1, ""
        if how == "oserror":
            raise OSError("powershell is gone")
        raise subprocess.TimeoutExpired(argv, 30)
    fake.on_terminate = hook
    kill_t = _record_time(fake, "on_terminate")
    fake.at(11, lambda f: setattr(f, "table", []))  # the process ends by itself, after the grace
    rep = _close(fake, store, state, grace_s=10, kill_wait_s=10)
    assert len(kill_t) == 1  # a terminate request WAS issued
    assert (rep.closed, rep.method) == (True, "terminate")
    failed = [s for s in rep.steps if "terminate request for pid 1000 failed" in s]
    assert len(failed) == 1 and expect in failed[0]
    assert any("although the terminate call failed" in s for s in rep.steps)

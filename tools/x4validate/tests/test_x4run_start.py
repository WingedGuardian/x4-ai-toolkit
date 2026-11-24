r"""x4run start: preflight, one hidden Steam launch call, poll for the new process, never retry.

Everything goes through `FakeSystem`; nothing here starts, or could start, Steam or X4. The
launch is only ever a call made to the scripted fake runner.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _paths
from x4validate import _x4run as X

GAME_ROOT = Path("steamroot/steamapps/common/X4 Foundations")
STEAM = make_proc(900, name="steam.exe", path="S:/steam/steam.exe")


def _fake(*procs) -> FakeSystem:
    """A fake world with Steam running (the start's preflight requires it) plus `procs`."""
    return FakeSystem([STEAM, *procs])


def _approval(**kw) -> X.Approval:
    base = dict(approved_by="user", mode="menu-only", target="main menu",
                desktop="secondary desktop", recorded_at="2026-11-24T10:00:00Z")
    base.update(kw)
    return X.Approval(**base)


def _store(tmp_path, fake) -> X.StateStore:
    return X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)


def _start(fake, tmp_path, approval="default", store=None, **kw):
    store = store or _store(tmp_path, fake)
    approval = _approval() if approval == "default" else approval
    return X.start(GAME_ROOT, approval, store, run=fake.run, sleep=fake.sleep,
                   clock=fake.clock, **kw)


def _launches(fake):
    return [c for c in fake.calls if Path(c.argv[0]).name.lower() == "steam.exe"]


def _reads(fake):
    return [c for c in fake.calls if "Get-CimInstance" in " ".join(c.argv)]


# ------------------------------------------------------------------------------ preflight


def test_missing_approval_refuses_before_any_call(tmp_path):
    fake = _fake()
    store = _store(tmp_path, fake)
    for bad in (None, _approval(approved_by=" "), _approval(mode="headless")):
        with pytest.raises(X.UnapprovedLaunch):
            _start(fake, tmp_path, approval=bad, store=store)
    assert fake.calls == []  # not even a process read
    assert not store.path.exists()


def test_a_running_X4_refuses_and_starts_nothing(tmp_path):
    fake = _fake(make_proc(4242))
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused, match="4242") as exc:
        _start(fake, tmp_path, store=store)
    assert "nothing was started" in str(exc.value)
    assert _launches(fake) == []
    assert len(fake.calls) == len(_reads(fake)) == 1  # one read, nothing else
    assert not store.path.exists()  # and no state file claiming a run


def test_a_failed_process_read_refuses_the_start(tmp_path):
    # A game IS running but the read cannot say so: the failed read must not pass as "none".
    fake = _fake(make_proc())
    fake.fail_reads(10)
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused, match="could not be read") as exc:
        _start(fake, tmp_path, store=store)
    assert "nothing was started" in str(exc.value)
    assert _launches(fake) == []
    assert not store.path.exists()


def test_a_pre_existing_state_file_refuses(tmp_path):
    fake = _fake()
    store = _store(tmp_path, fake)
    first = _start_ok(fake, tmp_path, store)
    before = store.path.read_bytes()
    fake.table[:] = [STEAM]  # even with the game gone, the record must be dealt with first
    n_launches = len(_launches(fake))
    with pytest.raises(X.UnfinishedRun):
        _start(fake, tmp_path, store=store)
    assert len(_launches(fake)) == n_launches  # no second launch
    assert store.path.read_bytes() == before  # the earlier record is untouched
    assert store.read().run_id == first.run_id


def _start_ok(fake, tmp_path, store=None):
    fake.launch_spawns(make_proc(2000))
    return _start(fake, tmp_path, store=store)


# ---------------------------------------------------------------------------- the launch


def test_steam_exe_is_beside_the_steam_root():
    assert X.steam_exe(GAME_ROOT) == Path("steamroot/steam.exe")
    assert X.steam_exe(Path("S/steamapps/common/X4")) == Path("S/steam.exe")


def test_the_launch_is_steam_applaunch_with_the_app_id_and_hidden(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000))
    _start(fake, tmp_path)
    launches = _launches(fake)
    assert len(launches) == 1
    assert launches[0].argv == (str(X.steam_exe(GAME_ROOT)), "-applaunch", _paths.STEAM_APPID)
    assert _paths.STEAM_APPID == "392160"
    assert launches[0].timeout is not None and launches[0].timeout <= 60  # it only asks Steam
    # every call the fake saw -- launch and reads -- is hidden, and never DETACHED_PROCESS
    assert fake.calls
    for c in fake.calls:
        assert c.creationflags == X.NO_WINDOW == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert c.creationflags != 0x8


def test_the_state_file_exists_before_the_launch_call(tmp_path):
    fake = _fake()
    store = _store(tmp_path, fake)
    seen = {}

    def hook(argv):
        seen["state"] = store.read()
        return 0, ""

    fake.on_launch = hook
    with pytest.raises(X.StartRefused):
        _start(fake, tmp_path, store=store, timeout_s=10)
    st = seen["state"]
    assert st.phase == "starting" and st.process is None
    assert st.approval == _approval()


def test_the_new_process_is_recorded_with_pid_path_and_start_time(tmp_path):
    proc = make_proc(2000, path="G:/games/X4/X4.exe", started="2026-11-24T10:01:02.5000000Z")
    fake = _fake()
    fake.launch_spawns(proc, after=7)
    store = _store(tmp_path, fake)
    state = _start(fake, tmp_path, store=store)
    assert state.phase == "running"
    assert state.process == proc  # pid, name, path AND start time
    assert state.approval == _approval()
    assert store.read() == state  # on disk, not only returned
    assert len(_launches(fake)) == 1
    assert 7 <= fake.clock() < 7 + 2 + 2  # it polled in ~2 s steps, no longer than needed


def test_two_new_processes_refuse_naming_both_and_keep_the_record(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000), make_proc(2001, started="2026-11-24T10:00:01.0000000Z"))
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused) as exc:
        _start(fake, tmp_path, store=store)
    msg = str(exc.value)
    assert "2000" in msg and "2001" in msg
    # the record names no game, so `stop` cannot close it: the way out is by hand, then forget
    assert "by hand" in msg and "x4run forget" in msg
    assert "`x4run stop` cannot close it" in msg and "recover with `x4run stop`" not in msg
    st = store.read()  # a game IS running: the record stays, recording no process
    assert st is not None and st.phase == "starting" and st.process is None
    assert len(_launches(fake)) == 1


# --------------------------------------------------------------- failure: nothing started


def test_no_game_within_the_timeout_clears_state_and_does_not_retry(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000), after=500)  # appears long after the deadline
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused) as exc:
        _start(fake, tmp_path, store=store, timeout_s=30)
    msg = str(exc.value)
    assert "no X4.exe appeared within 30" in msg and "Steam was asked" in msg
    assert "launch call timed out" not in msg  # that warning is only for a timed-out call
    assert not store.path.exists()
    assert len(_launches(fake)) == 1  # exactly one launch attempt
    assert 30 <= fake.clock() < 30 + 2 + 2  # it waited out the deadline, not far beyond it
    assert fake.table == [STEAM]


@pytest.mark.parametrize("failure", ["oserror", "nonzero"])
def test_a_failed_launch_call_clears_state_and_is_not_retried(tmp_path, failure):
    fake = _fake()

    def hook(argv):
        if failure == "oserror":
            raise FileNotFoundError("no steam.exe here")
        return 3, ""

    fake.on_launch = hook
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused, match="launch call") as exc:
        _start(fake, tmp_path, store=store)
    assert "not retried" in str(exc.value)
    assert not store.path.exists()
    assert len(_launches(fake)) == 1


def test_a_read_failure_during_the_poll_is_not_no_game(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000), after=1)
    inner = fake.on_launch

    def hook(argv):
        fake.fail_reads(3)  # the first poll read fails in full; the game is already there
        return inner(argv)

    fake.on_launch = hook
    state = _start(fake, tmp_path)
    assert state.phase == "running" and state.process.pid == 2000
    assert len(_launches(fake)) == 1


def test_reads_that_fail_all_the_way_keep_the_record_and_say_so(tmp_path):
    fake = _fake()

    def hook(argv):
        fake.fail_reads(1000)  # every poll read fails, to the deadline
        return 0, ""

    fake.on_launch = hook
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused) as exc:
        _start(fake, tmp_path, store=store, timeout_s=20)
    msg = str(exc.value)
    assert "a game may be running unseen" in msg and "KEPT" in msg
    assert "Check for an X4.exe by hand" in msg
    assert "x4run stop" not in msg  # `stop` is not promised to find a game it never recorded
    st = store.read()  # a failed read is the third state: the record is NOT cleared
    assert st is not None and st.phase == "starting" and st.process is None
    assert len(_launches(fake)) == 1


def test_reads_that_failed_earlier_but_end_with_a_good_empty_read_clear_the_record(tmp_path):
    fake = _fake()

    def hook(argv):
        fake.fail_reads(6)  # the first two poll reads fail in full; every later one succeeds
        return 0, ""

    fake.on_launch = hook
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused) as exc:
        _start(fake, tmp_path, store=store, timeout_s=20)
    msg = str(exc.value)
    assert "no X4.exe appeared within 20" in msg and "earlier process read(s) failed" in msg
    assert "KEPT" not in msg
    assert not store.path.exists()  # the LAST read succeeded and saw no game: nothing started
    assert len(_launches(fake)) == 1


# ------------------------------------------------------------- failure: a game may exist


def test_an_interrupted_poll_leaves_the_record_in_place(tmp_path):
    fake = _fake()
    store = _store(tmp_path, fake)

    def interrupted_sleep(n):
        if _launches(fake):
            raise KeyboardInterrupt
        fake.sleep(n)

    with pytest.raises(KeyboardInterrupt):
        X.start(GAME_ROOT, _approval(), store, run=fake.run, sleep=interrupted_sleep,
                clock=fake.clock)
    st = store.read()  # Steam was asked: a game may be starting, so the record stays
    assert st is not None and st.phase == "starting" and st.process is None
    assert len(_launches(fake)) == 1


def test_a_failed_record_update_names_the_running_game(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000), after=1)

    def bad_replace(src, dst):
        raise OSError("disk full")

    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock,
                         replace=bad_replace)
    with pytest.raises(X.StartRefused, match="2000") as exc:
        _start(fake, tmp_path, store=store)
    assert "could not be recorded" in str(exc.value)
    assert "x4run forget" in str(exc.value) and "recover with `x4run stop`" not in str(exc.value)
    assert store.read().process is None  # the record still exists and still says "starting"


# ------------------------------------------------------- a launch call that times out


def _timing_out_launch(fake, spawn=None, after=5.0):
    """A launch call that times out; optionally the game still appears `after` s later."""
    def hook(argv):
        if spawn is not None:
            fake.at(fake.now + after, lambda f: f.table.append(spawn))
        raise subprocess.TimeoutExpired(argv, 30)

    fake.on_launch = hook


def test_a_timed_out_launch_call_still_polls_and_records_a_game_that_appears(tmp_path):
    proc = make_proc(2000)
    fake = _fake()
    _timing_out_launch(fake, proc)
    store = _store(tmp_path, fake)
    state = _start(fake, tmp_path, store=store)
    assert state.phase == "running" and state.process == proc
    assert store.read() == state
    assert len(_launches(fake)) == 1  # never a second launch call


def test_a_timed_out_launch_call_with_no_game_clears_the_record_and_warns(tmp_path):
    fake = _fake()
    _timing_out_launch(fake)
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused) as exc:
        _start(fake, tmp_path, store=store, timeout_s=30)
    msg = str(exc.value)
    assert "launch call timed out" in msg and "may still start the game" in msg
    assert "check for an X4.exe before starting again" in msg
    assert not store.path.exists()
    assert len(_launches(fake)) == 1
    assert fake.clock() >= 30  # it waited out the deadline before giving up


# ------------------------------------------------------- Steam must already be running


def test_a_missing_steam_refuses_and_starts_nothing(tmp_path):
    fake = FakeSystem()  # no steam.exe in the table
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused, match="Steam is not running") as exc:
        _start(fake, tmp_path, store=store)
    assert "does not start the Steam client" in str(exc.value)
    assert "nothing was started" in str(exc.value)
    assert _launches(fake) == []
    assert not store.path.exists()


def test_a_failed_steam_read_refuses_the_start(tmp_path):
    fake = _fake()
    fake.good_reads(1)  # the X4.exe read answers; the steam.exe read then fails in full
    fake.fail_reads(10)
    store = _store(tmp_path, fake)
    with pytest.raises(X.StartRefused, match="whether Steam is running is unknown") as exc:
        _start(fake, tmp_path, store=store)
    assert "nothing was started" in str(exc.value)
    assert _launches(fake) == []
    assert not store.path.exists()


def test_the_steam_read_asks_for_steam_only(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000))
    _start(fake, tmp_path)
    scripts = [c.argv[-1] for c in _reads(fake)]
    assert "Name = 'X4.exe'" in scripts[0] and "Name = 'steam.exe'" in scripts[1]


def test_the_launch_call_does_not_capture_output(tmp_path):
    fake = _fake()
    fake.launch_spawns(make_proc(2000))
    _start(fake, tmp_path)
    (call,) = _launches(fake)
    kw = dict(call.kw)
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    for banned in ("capture_output", "text", "encoding", "universal_newlines", "stdin", "input"):
        assert banned not in kw
    assert call.timeout == X.LAUNCH_CALL_TIMEOUT_S == 30
    assert call.creationflags == X.NO_WINDOW


def test_a_game_root_without_a_steam_root_refuses_before_any_record(tmp_path):
    fake = _fake()
    store = _store(tmp_path, fake)
    for shallow in (Path("X4"), Path("common/X4")):
        with pytest.raises(X.StartRefused, match="cannot find the Steam folder"):
            X.start(shallow, _approval(), store, run=fake.run, sleep=fake.sleep, clock=fake.clock)
    assert fake.calls == []  # not even a read
    assert not store.path.exists() and not store.folder.exists()

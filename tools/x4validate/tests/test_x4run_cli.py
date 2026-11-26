r"""x4run command line: argument handling, exit codes and wording.

`main()` is driven with the injected scripted fake (`FakeSystem`) for every side effect, so
nothing here starts Steam or X4, and the real process runner is never reached: the default
seams are replaced in every test that gets past argument parsing.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from _x4run_fake import FAKE_GAME_ROOT, FakeSystem, make_proc

from x4validate import _x4run as X
from x4validate import _x4runcli as cli

GAME_ROOT = FAKE_GAME_ROOT
STEAM = make_proc(900, name="steam.exe", path="S:/steam/steam.exe")
GAME = make_proc(1000)
CMD = ["python", "tests/e2e_driver.py", "--flag"]
APPROVAL = ["--approved-by", "user (chat 2026-11-24)", "--mode", "menu-only",
            "--target", "main menu", "--desktop", "main"]


@pytest.fixture(autouse=True)
def _machine_state_in_tmp(tmp_path, monkeypatch):
    """No test here may reach the REAL machine-wide state folder: any `main()` that builds its
    own store gets one under tmp_path."""
    monkeypatch.setenv(X.STATE_DIR_ENV, str(tmp_path / "machine-state"))


def _world(tmp_path, *, steam=True, **command_kw):
    """Steam running; a launch makes the game appear; a window-close ends it; CMD is scripted."""
    fake = FakeSystem([STEAM] if steam else [])
    fake.launch_spawns(GAME, after=5)
    fake.close_exits_after(3)
    fake.script_command(CMD, **command_kw)
    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    return fake, store


def _main(fake, store, argv, *, game_root=GAME_ROOT, probe=None):
    return cli.main(argv, run=fake.run, sleep=fake.sleep, clock=fake.clock,
                    game_root=game_root, store=store, ready_probe=probe)


def _run_argv(*extra, approval=APPROVAL, command=CMD):
    return ["run", *approval, *extra, "--", *command]


def _record(store, *, phase="running", process=GAME, age_s=3600.0):
    """A run record whose FILE is `age_s` old (forget refuses a young one)."""
    store.begin(X.RunState(
        run_id="r1",
        approval=X.Approval("user", "menu-only", "main menu", "secondary", "2026-11-24T10:00:00Z"),
        phase=phase, process=process, started_at="2026-11-24T10:00:01+00:00"))
    old = time.time() - age_s
    os.utime(store.path, (old, old))


def _assert_all_hidden(fake):
    """Compared with the module's own flag, so it holds on a host without CREATE_NO_WINDOW;
    the literal Windows value is asserted once, in test_x4run_process.py (Windows only)."""
    assert fake.calls, "the test expected at least one call"
    assert X.NO_WINDOW == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    bad = [c.argv for c in fake.calls if c.creationflags != X.NO_WINDOW]
    assert bad == [], bad


# ----------------------------------------------------------------------------- the five named tests


def test_run_without_approval_exits_2_before_any_call(tmp_path, capsys):
    fake, store = _world(tmp_path)
    for missing in ("--approved-by", "--mode", "--target", "--desktop"):
        argv = list(APPROVAL)
        i = argv.index(missing)
        del argv[i:i + 2]
        with pytest.raises(SystemExit) as ei:
            _main(fake, store, ["run", *argv, "--", *CMD], probe=lambda: True)
        assert ei.value.code == 2, missing
    # a BLANK approval gets past argparse and must be refused by the module, still before any call
    assert _main(fake, store, _run_argv(approval=["--approved-by", "  ", "--mode", "menu-only",
                                                  "--target", "t", "--desktop", "d"]),
                 probe=lambda: True) == 2
    assert fake.calls == [], [c.argv for c in fake.calls]
    assert not store.path.exists()
    assert "approv" in capsys.readouterr().err.lower()


def test_a_blank_approval_is_refused_as_an_approval_problem_not_as_the_probe_stub(tmp_path, capsys):
    """The CLI checks the approval FIRST: with no --no-ready-check and no probe, a blank approval
    must be reported as what it is, not hidden behind the later 'not wired yet' refusal."""
    fake, store = _world(tmp_path)
    blank = ["--approved-by", " ", "--mode", "menu-only", "--target", "t", "--desktop", "d"]
    assert _main(fake, store, _run_argv(approval=blank)) == 2
    err = capsys.readouterr().err
    assert "approval" in err and "not wired yet" not in err
    assert fake.calls == []


def test_status_with_no_state_and_no_game(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, ["status"]) == 0
    out = capsys.readouterr().out
    assert "no run recorded" in out
    assert "X4.exe: none running" in out
    assert [fake._classify(c.argv) for c in fake.calls] == ["read"]
    _assert_all_hidden(fake)


def test_stop_with_nothing_recorded_exits_0_and_says_so(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, ["stop"]) == 0
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "X4.exe: none running; nothing to stop" in out
    assert [fake._classify(c.argv) for c in fake.calls] == ["read"]   # one live read, nothing else


def test_unclosed_exits_1(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.on_close = lambda argv: (0, "")   # a window-close request that is ignored forever
    fake.on_terminate = lambda argv: (0, "")
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    out = capsys.readouterr()
    assert rc == 1
    assert "NOT confirmed closed" in out.out + out.err
    assert store.path.exists()          # the record is kept for recovery


def test_help_names_every_flag(capsys):
    parser = cli.build_parser()
    subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(subs.choices) == {"status", "run", "stop", "forget"}
    seen = 0
    for name, sp in subs.choices.items():
        with pytest.raises(SystemExit) as ei:
            cli.main([name, "--help"])
        assert ei.value.code == 0
        text = capsys.readouterr().out
        for act in sp._actions:
            if isinstance(act, argparse._HelpAction):
                continue
            assert act.help and act.help.strip(), f"{name}: {act.option_strings or act.dest} has no help"
            for opt in act.option_strings or [act.dest]:
                assert opt in text, f"{name}: {opt} missing from --help"
            seen += 1
    assert seen >= 10
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    top = capsys.readouterr().out
    assert "run against the real game once (menu only, no save loaded)" in top    # the epilog banner
    assert "treat every result as unverified" in top
    assert "until step 2" not in top                        # the first real run IS step 2
    for name in subs.choices:
        assert name in top


# ----------------------------------------------------------------------------- run: the probe stub


def test_the_stub_probe_refuses_run_and_starts_nothing_without_no_ready_check(tmp_path, capsys):
    fake, store = _world(tmp_path)
    rc = _main(fake, store, _run_argv())            # no probe injected: the real (stub) one
    err = capsys.readouterr().err
    assert rc == 2
    assert "not wired yet: step 3" in err
    assert fake.calls == [] and not store.path.exists()


def test_no_ready_check_lets_run_go_ahead_and_the_game_is_closed(tmp_path, capsys):
    fake, store = _world(tmp_path)
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    out = capsys.readouterr().out
    assert rc == 0
    kinds = [fake._classify(c.argv) for c in fake.calls]
    assert kinds.count("launch") == 1 and kinds.count("close") == 1 and kinds.count("command") == 1
    assert not store.path.exists() and fake.table == [STEAM]
    assert "started: yes" in out and "closed: yes" in out
    _assert_all_hidden(fake)


def test_run_says_plainly_how_little_it_has_been_run(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _main(fake, store, _run_argv("--no-ready-check"))
    out = capsys.readouterr().out
    assert "once (menu only, no save loaded): treat every result as unverified" in out


def test_an_injected_probe_is_used_instead_of_the_stub(tmp_path):
    fake, store = _world(tmp_path)
    polled = []
    assert _main(fake, store, _run_argv(), probe=lambda: polled.append(1) or True) == 0
    assert polled


# ----------------------------------------------------------------------------- run: the approval record


def test_the_approval_record_reaches_the_state_file_with_recorded_at_filled(tmp_path):
    fake, store = _world(tmp_path)
    seen = {}

    def spy_probe():
        seen["state"] = store.read()
        return True

    assert _main(fake, store, _run_argv(), probe=spy_probe) == 0
    ap = seen["state"].approval
    assert (ap.approved_by, ap.mode, ap.target, ap.desktop) == (
        "user (chat 2026-11-24)", "menu-only", "main menu", "main")
    parsed = datetime.fromisoformat(ap.recorded_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0
    assert seen["state"].phase == "running" and seen["state"].process == GAME


def test_an_unknown_mode_is_an_argument_error(tmp_path):
    fake, store = _world(tmp_path)
    argv = _run_argv(approval=["--approved-by", "u", "--mode", "yolo", "--target", "t", "--desktop", "d"])
    with pytest.raises(SystemExit) as ei:
        _main(fake, store, argv, probe=lambda: True)
    assert ei.value.code == 2 and fake.calls == []


def test_run_without_a_command_is_refused_before_any_call(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, ["run", *APPROVAL, "--no-ready-check"]) == 2
    assert fake.calls == []
    assert "command" in capsys.readouterr().err.lower()


def test_the_game_root_must_resolve(tmp_path, capsys, monkeypatch):
    fake, store = _world(tmp_path)
    monkeypatch.setattr(cli._paths, "game_root", lambda: None)
    rc = cli.main(_run_argv("--no-ready-check"), run=fake.run, sleep=fake.sleep, clock=fake.clock,
                  store=store)
    assert rc == 2 and fake.calls == []
    assert "game root" in capsys.readouterr().err


# ----------------------------------------------------------------------------- run: refusals and exit codes


def test_a_refused_start_exits_2_with_the_reason_and_no_traceback(tmp_path, capsys):
    fake, store = _world(tmp_path, steam=False)        # Steam not running
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    cap = capsys.readouterr()
    assert rc == 2
    assert "Steam is not running" in cap.out + cap.err
    assert not store.path.exists()
    assert "Traceback" not in cap.out + cap.err
    assert [fake._classify(c.argv) for c in fake.calls].count("launch") == 0


def test_a_second_x4_refuses_the_run_with_exit_2(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(make_proc(77))
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    cap = capsys.readouterr()
    assert "already running" in cap.out + cap.err
    assert not store.path.exists()                     # nothing started, no record: safe to retry
    assert [fake._classify(c.argv) for c in fake.calls].count("launch") == 0


def test_an_unfinished_run_exits_2_and_names_the_recovery(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    cap = capsys.readouterr()
    assert "record already exists" in cap.err and "x4run stop" in cap.err
    assert "Traceback" not in cap.err
    assert {fake._classify(c.argv) for c in fake.calls} <= {"read"}   # nothing launched, nothing closed


@pytest.mark.parametrize("exc, text", [
    (X.UnapprovedLaunch("no approval recorded"), "no approval recorded"),
    (X.UnfinishedRun("a run record already exists"), "a run record already exists"),
    (X.InvalidState("cannot be read back"), "cannot be read back"),
    (X.CorruptState("record unreadable"), "record unreadable"),
    (X.StopCannotRead("cannot be read"), "cannot be read"),
    (X.ForgetRefused("is running"), "is running"),
    (X.StateFolderError("no toolkit root"), "no toolkit root"),
    (X.ProcessReadError("read failed"), "read failed"),
    (X.StartRefused("start refused", launch_attempted=False), "start refused"),
    (ValueError("bad duration"), "bad duration"),
    (TypeError("bad type"), "bad type"),
])
def test_every_refusal_type_maps_to_exit_2_with_a_readable_message(tmp_path, capsys, monkeypatch, exc, text):
    fake, store = _world(tmp_path)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(cli, "run_cycle", boom)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    cap = capsys.readouterr()
    assert text in cap.err
    assert "Traceback" not in cap.out + cap.err


def test_a_bad_duration_is_a_refusal_before_any_call(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check", "--ready-timeout", "nan")) == 2
    assert fake.calls == []
    assert "ready_timeout_s" in capsys.readouterr().err


def test_a_commands_nonzero_rc_is_reported_but_is_not_a_driver_error(tmp_path, capsys):
    fake, store = _world(tmp_path, rc=7)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    out = capsys.readouterr().out
    assert "command exit code: 7" in out
    assert "closed: yes" in out


def test_a_game_not_ready_in_time_is_exit_1_with_the_game_closed(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--ready-timeout", "10"), probe=lambda: False) == 1
    out = capsys.readouterr().out
    assert "not ready" in out and "closed: yes" in out
    assert fake.command_calls == []


def test_the_command_that_timed_out_is_exit_1(tmp_path, capsys):
    fake, store = _world(tmp_path, timeout=True)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    assert "timed out" in capsys.readouterr().out


# ----------------------------------------------------------------------------- run: the close report


def test_an_unknown_remaining_list_prints_unknown_not_none(tmp_path, capsys):
    fake, store = _world(tmp_path)
    rep = X.CloseReport(True, "window-close", ("t+3s the recorded process is gone",), None)
    cyc = X.CycleReport(True, True, 0, rep, (), launch_attempted=True)
    out = cli._format_cycle(cyc)
    assert "remaining: unknown (the last process read failed)" in out
    assert "other X4 processes could not be checked" in out
    assert "remaining: none" not in out


def test_a_known_empty_remaining_list_prints_none(tmp_path):
    rep = X.CloseReport(True, "window-close", ("s",), ())
    out = cli._format_cycle(X.CycleReport(True, True, 0, rep, (), launch_attempted=True))
    assert "remaining: none" in out and "unknown" not in out


def test_a_known_remaining_list_names_each_process(tmp_path):
    rep = X.CloseReport(False, "unclosed", ("s",), (make_proc(5), make_proc(6)))
    out = cli._format_cycle(X.CycleReport(True, True, 0, rep, (), launch_attempted=True))
    assert "pid 5" in out and "pid 6" in out and "closed: NO" in out


def test_the_unclosed_note_reaches_the_output(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.on_close = lambda argv: (0, "")
    fake.on_terminate = lambda argv: (0, "")
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    cap = capsys.readouterr()
    assert "the game was NOT confirmed closed" in cap.out + cap.err
    assert "x4run stop" in cap.out + cap.err


class _FailingCloseStore(X.StateStore):
    """`close`'s first record is REJECTED (`InvalidState`): the close itself raises, before any
    call. (An OSError there no longer stops the close: see the Minor-4 tests below.)"""

    def update(self, state):
        if state.phase == "closing":
            raise X.InvalidState("disk says no")
        super().update(state)


def _failing_close_world(tmp_path):
    fake, _ = _world(tmp_path)
    store = _FailingCloseStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    return fake, store


def test_an_exception_with_the_not_confirmed_closed_note_exits_1_and_prints_it(tmp_path, capsys):
    fake, store = _failing_close_world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert "disk says no" in text
    assert "NOT confirmed closed" in text
    assert "x4run forget" in text                    # the note's recovery steps reach the user
    assert "Traceback" not in text


def test_a_close_error_that_is_a_refusal_type_is_still_exit_1_when_the_game_may_be_running(tmp_path, capsys, monkeypatch):
    fake, store = _world(tmp_path)

    def boom(*a, **k):
        exc = X.InvalidState("record cannot be read back")
        exc.add_note("x4run: the game was NOT confirmed closed: `close` failed")
        raise exc
    monkeypatch.setattr(cli, "run_cycle", boom)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    assert "NOT confirmed closed" in capsys.readouterr().err


def test_an_interrupt_with_a_failed_close_prints_the_close_error_and_exits_1(tmp_path, capsys):
    fake, store = _failing_close_world(tmp_path)

    def interrupt(f):
        raise KeyboardInterrupt

    fake.script_command(CMD, action=interrupt)
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    cap = capsys.readouterr()
    assert rc == 1                                   # not 130
    text = cap.out + cap.err
    assert "disk says no" in text and "NOT confirmed closed" in text
    assert "Traceback" not in text


def test_an_interrupt_with_a_confirmed_close_says_the_game_was_closed_and_exits_1(tmp_path, capsys):
    fake, store = _world(tmp_path)

    def interrupt(f):
        raise KeyboardInterrupt

    fake.script_command(CMD, action=interrupt)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    cap = capsys.readouterr()
    assert "interrupted" in (cap.out + cap.err).lower()
    assert "closed afterwards" in cap.out + cap.err
    assert fake.table == [STEAM] and not store.path.exists()


def test_a_start_that_keeps_a_starting_record_says_forget_not_stop(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.on_launch = lambda argv: (0, "")             # Steam accepts the call; no game ever appears
    # every read after the two preflight reads fails: whether a game exists is unknown, so the
    # run record is KEPT in phase starting

    def on_read(f, n):
        if n >= 2:
            f.fail_reads(1)
    fake.on_read = on_read
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 1                                    # a launch call was made: a game may be running unseen
    assert store.path.exists() and store.read().phase == "starting"
    assert "check for a running X4.exe by hand, then `x4run forget`" in text
    assert "never `x4run stop`" in text or "`x4run stop` cannot" in text


# ----------------------------------------------------------------------------- status


def test_status_prints_the_recorded_run_and_the_live_list(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    _record(store)
    assert _main(fake, store, ["status"]) == 0
    out = capsys.readouterr().out
    for want in ("phase: running", "pid 1000", "user", "menu-only", "main menu", "secondary",
                 "2026-11-24T10:00:00Z", "started_at: 2026-11-24T10:00:01+00:00", "pid 1000"):
        assert want in out, want
    assert "X4.exe: 1 running" in out


def test_status_with_a_failed_read_prints_unknown_never_none(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.fail_reads(5)
    rc = _main(fake, store, ["status"])
    out = capsys.readouterr()
    assert "unknown: process read failed" in out.out + out.err
    assert "none running" not in out.out + out.err
    assert rc == 2                                    # a non-answer is not a success


def test_status_with_a_corrupt_record_says_so_and_still_lists_the_processes(tmp_path, capsys):
    fake, store = _world(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_text("not json", encoding="utf-8")
    rc = _main(fake, store, ["status"])
    cap = capsys.readouterr()
    assert rc == 2
    assert "unreadable" in cap.out + cap.err
    assert "X4.exe: none running" in cap.out


def test_a_state_folder_that_cannot_be_located_refuses_with_exit_2_and_no_traceback(capsys, monkeypatch):
    """The machine-wide folder cannot be decided (Windows with no %LOCALAPPDATA%, no override):
    a refusal, never a traceback, and no process is read."""
    monkeypatch.setattr(cli, "state_folder", lambda: X.state_folder(environ={}, platform="win32"))

    def no_call(*a, **k):
        raise AssertionError("no process may be read or started")
    assert cli.main(["status"], run=no_call) == 2
    cap = capsys.readouterr()
    assert "LOCALAPPDATA" in cap.err and "Traceback" not in cap.out + cap.err


def test_the_cold_matrix_case_reaches_the_real_unconfigured_refusal(capsys, monkeypatch):
    """verify-cold.sh's x4run case (Minor 1): with no game root configured, `run` refuses with
    exit 2 BECAUSE the game root cannot be resolved, before any process call. (`status` was
    the wrong case: it needs no configuration and exits 0 on a host that can read processes,
    see test_status_with_no_state_and_no_game.)"""
    monkeypatch.setattr(cli._paths, "game_root", lambda: None)

    def no_call(*a, **k):
        raise AssertionError("no process may be read or started")
    argv = ["run", "--approved-by", "cold", "--mode", "menu-only", "--target", "x",
            "--desktop", "main", "--no-ready-check", "--", "x"]
    assert cli.main(argv, run=no_call) == 2
    cap = capsys.readouterr()
    assert "game root cannot be resolved" in cap.err and "Traceback" not in cap.out + cap.err


def test_status_changes_nothing(tmp_path):
    fake, store = _world(tmp_path)
    _record(store)
    before = store.path.read_bytes()
    _main(fake, store, ["status"])
    assert store.path.read_bytes() == before
    assert {fake._classify(c.argv) for c in fake.calls} == {"read"}


# ----------------------------------------------------------------------------- stop


def test_stop_closes_the_recorded_game_and_exits_0(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    _record(store)
    assert _main(fake, store, ["stop"]) == 0
    assert "closed: yes" in capsys.readouterr().out
    assert fake.table == [STEAM] and not store.path.exists()
    _assert_all_hidden(fake)


def test_stop_of_a_starting_record_exits_1_and_says_forget_not_stop(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store, phase="starting", process=None)
    assert _main(fake, store, ["stop"]) == 1
    text = capsys.readouterr().out
    assert "check for a running X4.exe by hand, then `x4run forget`" in text
    assert store.path.exists()


def test_stop_that_cannot_read_the_record_exits_2_with_the_hand_steps(tmp_path, capsys):
    fake, store = _world(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_text("", encoding="utf-8")
    assert _main(fake, store, ["stop"]) == 2
    err = capsys.readouterr().err
    assert "cannot be read" in err and "x4run forget" in err and "Traceback" not in err


def test_stop_of_an_unclosable_game_exits_1_and_keeps_the_record(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    fake.on_close = lambda argv: (0, "")
    fake.on_terminate = lambda argv: (0, "")
    _record(store)
    assert _main(fake, store, ["stop"]) == 1
    assert "closed: NO" in capsys.readouterr().out
    assert store.path.exists()


# ----------------------------------------------------------------------------- forget


def test_forget_removes_the_record_after_a_clean_read(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store)
    assert _main(fake, store, ["forget"]) == 0
    assert "removed the run record" in capsys.readouterr().out
    assert not store.path.exists()


def test_forget_with_no_record_says_so_and_exits_0(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, ["forget"]) == 0
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "X4.exe: none running; nothing to forget" in out
    assert [fake._classify(c.argv) for c in fake.calls] == ["read"]


def test_forget_refused_while_x4_runs_exits_2(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    _record(store)
    assert _main(fake, store, ["forget"]) == 2
    err = capsys.readouterr().err
    assert "is running" in err and "Traceback" not in err
    assert store.path.exists()


def test_forget_refused_on_a_failed_read_exits_2(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store)
    fake.fail_reads(5)
    assert _main(fake, store, ["forget"]) == 2
    assert "unknown" in capsys.readouterr().err
    assert store.path.exists()


# ----------------------------------------------------------------------------- readiness is not a measurement


def test_no_ready_check_does_not_print_a_plain_ready_yes(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    out = capsys.readouterr().out
    assert "ready: yes (NOT checked: --no-ready-check)" in out
    assert "readiness probe was skipped" in out
    assert "ready: yes\n" not in out


def test_a_real_probe_prints_a_plain_ready_yes(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv(), probe=lambda: True) == 0
    out = capsys.readouterr().out
    assert "ready: yes\n" in out
    assert "NOT checked" not in out and "skipped" not in out


# ----------------------------------------------------------------------------- a start that left a record exits 1


class _UnrecordableStore(X.StateStore):
    """The game started, but the record of its process cannot be written."""

    def update(self, state):
        if state.phase == "running":
            raise OSError("disk says no")
        super().update(state)


def _two_games(tmp_path):
    fake, store = _world(tmp_path)
    fake.launch_spawns(GAME, make_proc(2000, started="2026-11-24T10:00:01.0000000Z"), after=5)
    return fake, store


def _unrecordable(tmp_path):
    fake, _ = _world(tmp_path)
    return fake, _UnrecordableStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)


@pytest.mark.parametrize("make, text", [
    (_two_games, "appeared at once"),
    (_unrecordable, "could not be recorded"),
])
def test_a_start_refusal_that_kept_a_record_exits_1_and_says_forget(tmp_path, capsys, make, text):
    fake, store = make(tmp_path)
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert rc == 1, out                               # not 2: a game may be running
    assert text in out and "by hand" in out and "x4run forget" in out
    assert "recover with `x4run stop`" not in out     # stop cannot close a record that names no game
    assert store.path.exists()
    assert fake.close_calls == [] and fake.terminate_calls == [] and fake.command_calls == []
    assert [fake._classify(c.argv) for c in fake.calls].count("launch") == 1


def test_a_refusal_that_left_no_record_still_exits_2(tmp_path):
    fake, store = _world(tmp_path)
    fake.table.append(make_proc(77))                  # an X4 at preflight: refused before any launch
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    assert not store.path.exists()


def test_a_closed_game_with_other_x4_still_running_is_exit_0(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    other = make_proc(77, path="G:/other/X4.exe")
    fake.table.append(other)
    _record(store)
    assert _main(fake, store, ["stop"]) == 0
    assert "closed: yes" in capsys.readouterr().out
    assert other in fake.table                        # never the driver's to touch


def test_the_run_help_says_what_exit_1_and_2_mean(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "2 refused before any launch call, nothing started" in text
    assert "1 a launch call was made and no game was recorded" in text
    assert "an X4.exe left (or not checkable) after the game closed" in text
    # Ruling 22/23 and I10: what the user must know is in the help itself
    assert "Do not start X4 yourself while a run is starting" in text
    assert "the driver cannot choose a desktop" in text
    assert "cuts short a crash dump in progress" in text and ">= 600" in text
    assert "last-command.out" in text


# ----------------------------------------------------------------------------- no tracebacks


def test_status_with_an_unreadable_record_file_is_exit_2_not_a_traceback(tmp_path, capsys):
    fake, store = _world(tmp_path)
    store.path.mkdir(parents=True)                    # reading a directory raises an OSError
    assert _main(fake, store, ["status"]) == 2
    cap = capsys.readouterr()
    assert "unreadable" in cap.out and "Traceback" not in cap.out + cap.err
    assert "X4.exe: none running" in cap.out


def test_stop_with_an_unreadable_record_file_is_exit_1_with_the_error(tmp_path, capsys):
    fake, store = _world(tmp_path)
    store.path.mkdir(parents=True)
    assert _main(fake, store, ["stop"]) == 1
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err and "x4run" in cap.err


def test_an_interrupt_during_stop_keeps_the_record_and_says_what_to_do(tmp_path, capsys):
    fake, store = _world(tmp_path)
    fake.table.append(GAME)
    _record(store)

    def interrupt(argv):
        raise KeyboardInterrupt
    fake.on_close = interrupt
    assert _main(fake, store, ["stop"]) == 1          # not 130
    cap = capsys.readouterr()
    assert "interrupted" in cap.err and "record was kept" in cap.err and "x4run stop" in cap.err
    assert "Traceback" not in cap.out + cap.err
    assert store.path.exists()


def test_an_interrupt_during_forget_is_exit_1_not_a_traceback(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store)

    def interrupt(f, n):
        raise KeyboardInterrupt
    fake.on_read = interrupt
    assert _main(fake, store, ["forget"]) == 1
    cap = capsys.readouterr()
    assert "interrupted" in cap.err and "Traceback" not in cap.out + cap.err
    assert store.path.exists()


# ----------------------------------------------------------------------------- hidden, always


def test_every_call_the_cli_makes_is_hidden(tmp_path):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    assert any(fake._classify(c.argv) == "command" for c in fake.calls)
    _assert_all_hidden(fake)


def test_the_cli_never_names_a_real_launcher_or_killer():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    for banned in ("taskkill", "Stop-Process", "Popen", "os.system", "-applaunch"):
        assert banned not in src, banned


def test_the_entry_point_is_registered_and_refuses_unconfigured():
    import tomllib
    pkg = Path(__file__).resolve().parent.parent
    scripts = tomllib.loads((pkg / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["x4run"] == "x4validate._x4runcli:main"
    assert getattr(cli.main, "_refuses_unconfigured", False)


# ----------------------------------------------------------------------------- fix wave: exit-code truth (Ruling 18)


def test_a_start_refused_after_the_launch_call_is_exit_1_even_if_no_record_remains(tmp_path, capsys):
    """No game by the deadline (a good empty last read): the record is cleared, but a launch call
    WAS made, so this is not "nothing started, safe to retry"."""
    fake, store = _world(tmp_path)
    fake.on_launch = lambda argv: (0, "")            # Steam accepts; no game appears in time
    rc = _main(fake, store, _run_argv("--no-ready-check"))
    cap = capsys.readouterr()
    assert rc == 1
    assert not store.path.exists()                   # the old heuristic would have said 2 here
    assert "Steam may still start the game" in cap.err


def test_a_nonzero_steam_exit_is_unknown_and_a_game_that_appears_is_run_and_closed(tmp_path, capsys):
    fake, store = _world(tmp_path)
    inner = fake.on_launch

    def hook(argv):
        inner(argv)                                  # the game still appears 5 s later
        return 7, ""
    fake.on_launch = hook
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    assert "closed: yes" in capsys.readouterr().out
    assert len(fake.command_calls) == 1


def test_a_steam_exe_that_cannot_be_started_is_exit_2(tmp_path, capsys):
    fake, store = _world(tmp_path)

    def hook(argv):
        raise FileNotFoundError("no steam.exe")
    fake.on_launch = hook
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    assert "could not be made" in capsys.readouterr().err
    assert not store.path.exists()


def test_a_refusal_with_launch_attempted_is_exit_1_through_the_failure_path(tmp_path, capsys, monkeypatch):
    fake, store = _world(tmp_path)

    def boom(*a, **k):
        raise X.StartRefused("start refused late", launch_attempted=True)
    monkeypatch.setattr(cli, "run_cycle", boom)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1


def test_a_preflight_refusal_with_an_earlier_record_present_is_still_exit_2_with_the_hint(tmp_path, capsys):
    """Not attempted: exit 2 although a (stale, phase starting) record exists; the hint stays."""
    fake, store = _world(tmp_path)
    _record(store, phase="starting", process=None)
    fake.table.append(make_proc(77))                 # an X4 at preflight: refused before any launch
    assert _main(fake, store, _run_argv("--no-ready-check")) == 2
    err = capsys.readouterr().err
    assert "already running" in err and "x4run forget" in err


# ----------------------------------------------------------------------------- fix wave: the command's output (Ruling 17)


def test_the_command_output_tails_and_paths_appear_in_the_cli_output(tmp_path, capsys):
    fake, store = _world(tmp_path, out=b"MEASURED-STDOUT\n", err=b"MEASURED-STDERR\n")
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    out = capsys.readouterr().out
    assert f"command stdout: {store.folder / 'last-command.out'}" in out
    assert f"command stderr: {store.folder / 'last-command.err'}" in out
    assert "MEASURED-STDOUT" in out and "MEASURED-STDERR" in out


# ----------------------------------------------------------------------------- fix wave: forget vs a live start (Ruling 19)


def test_forget_of_a_young_record_is_refused_with_exit_2_and_no_read(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _record(store, age_s=5)
    before = store.path.read_bytes()
    assert _main(fake, store, ["forget"]) == 2
    err = capsys.readouterr().err
    assert "still starting" in err and "Wait" in err
    assert store.path.read_bytes() == before and fake.calls == []


# ----------------------------------------------------------------------------- fix wave: one machine-wide record (Ruling 20)


def test_main_builds_its_store_in_the_machine_wide_folder(tmp_path, monkeypatch, capsys):
    fake = FakeSystem([STEAM])
    monkeypatch.setenv(X.STATE_DIR_ENV, str(tmp_path / "one-per-machine"))
    assert cli.main(["status"], run=fake.run, sleep=fake.sleep, clock=fake.clock) == 0
    assert "no run recorded" in capsys.readouterr().out
    # a record written there is seen by `status` from any checkout: nothing is per-checkout
    _record(X.StateStore(tmp_path / "one-per-machine"))
    assert cli.main(["status"], run=fake.run, sleep=fake.sleep, clock=fake.clock) == 0
    assert f"run record: {tmp_path / 'one-per-machine' / 'state.json'}" in capsys.readouterr().out


@pytest.mark.parametrize("verb", ["stop", "forget"])
def test_stop_or_forget_with_no_record_but_a_running_x4_exits_1_and_names_it(tmp_path, capsys, verb):
    fake, store = _world(tmp_path)
    fake.table.append(make_proc(4321))
    assert _main(fake, store, [verb]) == 1
    out = capsys.readouterr().out
    assert "nothing recorded here; X4.exe is running (pid 4321)" in out
    assert "close it by hand, or use the checkout/host that started it" in out
    assert fake.close_calls == [] and fake.terminate_calls == []   # never touched
    assert [fake._classify(c.argv) for c in fake.calls] == ["read"]


@pytest.mark.parametrize("verb", ["stop", "forget"])
def test_stop_or_forget_with_no_record_and_a_failed_read_says_unknown(tmp_path, capsys, verb):
    fake, store = _world(tmp_path)
    fake.fail_reads(5)
    assert _main(fake, store, [verb]) == 2
    out = capsys.readouterr().out
    assert "unknown" in out and "none running" not in out


# ----------------------------------------------------------------------------- fix wave: leftovers after a run (Ruling 21)


def test_a_run_whose_game_closed_but_another_x4_appeared_exits_1(tmp_path, capsys):
    fake, store = _world(tmp_path)
    late = make_proc(5555, path="G:/other/X4.exe", started="2026-11-24T10:05:00.0000000Z")
    fake.script_command(CMD, action=lambda f: f.table.append(late))   # appears during the run
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    out = capsys.readouterr().out
    assert "closed: yes" in out and "pid 5555" in out
    assert "appeared during the run" in out
    assert late in fake.table                        # reported, never touched


def test_a_run_whose_final_read_failed_exits_1(tmp_path, capsys, monkeypatch):
    fake, store = _world(tmp_path)
    rep = X.CloseReport(True, "window-close", ("s",), None)
    monkeypatch.setattr(cli, "run_cycle", lambda *a, **k: X.CycleReport(
        True, True, 0, rep, (), launch_attempted=True))
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    assert "whether an X4.exe is left is unknown" in capsys.readouterr().out


def test_a_run_with_nothing_left_still_exits_0(tmp_path, monkeypatch):
    fake, store = _world(tmp_path)
    rep = X.CloseReport(True, "window-close", ("s",), ())
    monkeypatch.setattr(cli, "run_cycle", lambda *a, **k: X.CycleReport(
        True, True, 0, rep, (), launch_attempted=True))
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0


# ----------------------------------------------------------------------------- fix wave: --desktop (Ruling 23)


@pytest.mark.parametrize("desktop", ["secondary", "Main", "main ", "private"])
def test_a_desktop_other_than_main_is_refused_before_any_call(tmp_path, capsys, desktop):
    fake, store = _world(tmp_path)
    approval = ["--approved-by", "u", "--mode", "menu-only", "--target", "t", "--desktop", desktop]
    assert _main(fake, store, _run_argv("--no-ready-check", approval=approval)) == 2
    err = capsys.readouterr().err
    assert ("the driver cannot choose a desktop: the game appears on the current desktop and "
            "takes focus") in err
    assert fake.calls == [] and not store.path.exists()


# ----------------------------------------------------------------------------- fix wave: Minors 4, 5, 9


def test_the_run_report_prints_the_approval_record(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    out = capsys.readouterr().out
    assert ("approval: by user (chat 2026-11-24); mode menu-only; target main menu; "
            "desktop main; recorded_at ") in out


def test_a_nul_in_the_command_is_refused_before_any_call(tmp_path, capsys):
    fake, store = _world(tmp_path)
    assert _main(fake, store, _run_argv("--no-ready-check", command=["python", "a\0b"])) == 2
    assert "NUL" in capsys.readouterr().err
    assert fake.calls == [] and not store.path.exists()


def test_a_refusal_type_raised_after_the_start_is_exit_1_by_its_close_note(tmp_path, capsys):
    """A ValueError from the command call itself (here scripted) comes AFTER the game started:
    run_cycle closes the game and notes it, and the CLI must not call that a refusal."""
    fake, store = _world(tmp_path, exc=ValueError("embedded null byte"))
    assert _main(fake, store, _run_argv("--no-ready-check")) == 1
    err = capsys.readouterr().err
    assert "embedded null byte" in err and "x4run: the game was closed afterwards" in err
    assert fake.table == [STEAM]                     # the game was closed first


def test_a_failed_closing_write_still_closes_the_game_from_the_cli(tmp_path, capsys):
    fake, _ = _world(tmp_path)

    class _Store(X.StateStore):
        def update(self, state):
            if state.phase == "closing":
                raise OSError("disk says no")
            super().update(state)
    store = _Store(tmp_path / "state", sleep=fake.sleep, clock=fake.clock)
    assert _main(fake, store, _run_argv("--no-ready-check")) == 0
    out = capsys.readouterr().out
    assert "closed: yes" in out and "could not be set to phase closing (disk says no)" in out
    assert fake.table == [STEAM] and not store.path.exists()

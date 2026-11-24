r"""x4run command line: argument handling, exit codes and wording.

`main()` is driven with the injected scripted fake (`FakeSystem`) for every side effect, so
nothing here starts Steam or X4, and the real process runner is never reached: the default
seams are replaced in every test that gets past argument parsing.
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _x4run as X
from x4validate import _x4runcli as cli

GAME_ROOT = Path("steamroot/steamapps/common/X4 Foundations")
STEAM = make_proc(900, name="steam.exe", path="S:/steam/steam.exe")
GAME = make_proc(1000)
CMD = ["python", "tests/e2e_driver.py", "--flag"]
APPROVAL = ["--approved-by", "user (chat 2026-11-24)", "--mode", "menu-only",
            "--target", "main menu", "--desktop", "secondary"]


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


def _record(store, *, phase="running", process=GAME):
    store.begin(X.RunState(
        run_id="r1",
        approval=X.Approval("user", "menu-only", "main menu", "secondary", "2026-11-24T10:00:00Z"),
        phase=phase, process=process, started_at="2026-11-24T10:00:01+00:00"))


def _assert_all_hidden(fake):
    assert fake.calls, "the test expected at least one call"
    bad = [c.argv for c in fake.calls if c.creationflags != subprocess.CREATE_NO_WINDOW]
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
    assert "nothing recorded" in capsys.readouterr().out
    assert fake.calls == []


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
    assert "NOT been run against the real game" in top      # the epilog banner
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


def test_run_says_plainly_it_has_not_been_run_against_the_real_game(tmp_path, capsys):
    fake, store = _world(tmp_path)
    _main(fake, store, _run_argv("--no-ready-check"))
    out = capsys.readouterr().out
    assert "NOT been run against the real game" in out


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
        "user (chat 2026-11-24)", "menu-only", "main menu", "secondary")
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
    (X.StartRefused("start refused"), "start refused"),
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
    cyc = X.CycleReport(True, True, 0, rep, ())
    out = cli._format_cycle(cyc)
    assert "remaining: unknown (the last process read failed)" in out
    assert "other X4 processes could not be checked" in out
    assert "remaining: none" not in out


def test_a_known_empty_remaining_list_prints_none(tmp_path):
    rep = X.CloseReport(True, "window-close", ("s",), ())
    out = cli._format_cycle(X.CycleReport(True, True, 0, rep, ()))
    assert "remaining: none" in out and "unknown" not in out


def test_a_known_remaining_list_names_each_process(tmp_path):
    rep = X.CloseReport(False, "unclosed", ("s",), (make_proc(5), make_proc(6)))
    out = cli._format_cycle(X.CycleReport(True, True, 0, rep, ()))
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
    """`close` cannot write its first record: the close itself fails with an OSError."""

    def update(self, state):
        if state.phase == "closing":
            raise OSError("disk says no")
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


def test_an_unconfigured_toolkit_refuses_with_exit_2_and_no_traceback(capsys, monkeypatch):
    """The cold matrix (scripts/verify-cold.sh) runs `x4run status` with nothing configured: the
    state folder cannot be located, which is a refusal, never a traceback."""
    monkeypatch.setattr(cli._paths, "toolkit_root", lambda: None)
    assert cli.main(["status"]) == 2
    cap = capsys.readouterr()
    assert "toolkit root" in cap.err and "Traceback" not in cap.out + cap.err


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
    assert "nothing to forget" in capsys.readouterr().out


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
    assert "2 refused, nothing started and no run record left" in text
    assert "a start that left a run record" in text


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

r"""`x4run` -- start X4 through Steam, run a command, close X4 again (the command line).

THE DRIVER HAS NEVER BEEN RUN AGAINST THE REAL GAME: treat every result as unverified. It is
built and tested offline: every test drives `main()` with a scripted fake in place of the
process runner. The first real `x4run run` is build step 2 and needs its own approval.

This module is the command line ONLY: argument handling, the approval record's assembly, the
mapping of outcomes to exit codes and the wording. The logic (preflight, start, the close
ladder, the run record) is `x4validate._x4run`.

EXIT CODES. 0 success. 2 a REFUSAL before any launch call (`StartRefused.launch_attempted`
False): an incomplete approval, a `--desktop` other than `main`, a refused preflight, a run
record in the way, an unreadable record, a refused `forget`, a bad argument, the stubbed
readiness probe; nothing was started, so it is safe to retry once the cause is fixed.
1 a launch call was made and no game was recorded (a game may still appear or be running: see
the output), the game is NOT confirmed closed, an X4.exe is still running (or could not be
checked) after a `run` whose game closed, or any other failure (including a run whose game
never became ready, or whose command timed out or could not be started: the game is still
closed first). A nonzero exit code of the CALLER'S command is reported, never an error of the
driver. `stop` exits 0 once the recorded game closed even if other X4.exe run; `stop` and
`forget` with no record exit 1 if an X4.exe is running and 2 if that could not be read.
`status` exits 0, or 2 if the process read failed or the record could not be read.

Every flag has `--help` text; the whole parser is built by `build_parser`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import _paths
from . import __version__
from ._x4run import (
    Approval,
    CloseReport,
    CorruptState,
    CycleReport,
    ForgetRefused,
    InvalidState,
    ProcessReadError,
    StartRefused,
    StateFolderError,
    StateStore,
    StopCannotRead,
    UnapprovedLaunch,
    UnfinishedRun,
    APPROVAL_MODES,
    COMMAND_TAIL_BYTES,
    FORGET_MIN_AGE_S,
    forget,
    read_processes,
    require_approval,
    run_cycle,
    state_folder,
    stop,
)

#: Printed on every `run` and in the top-level `--help`.
BANNER = ("x4run: this driver has never been run against the real game: treat every result as "
          "unverified (built and tested offline, build step 1 of 3).")

#: The exception types that are REFUSALS (exit 2) unless they carry the unclosed note.
REFUSALS = (UnapprovedLaunch, UnfinishedRun, InvalidState, CorruptState, StopCannotRead,
            ForgetRefused, StateFolderError, ProcessReadError, StartRefused, ValueError, TypeError)

#: The phrase `_x4run` attaches (as an exception note) when a game may still be running.
_UNCLOSED = "NOT confirmed closed"

#: How every close note `_x4run.run_cycle` attaches to an exception begins. Such an exception
#: was raised AFTER a game was started, so it is never a refusal (exit 1), whatever its type.
_CLOSE_NOTE = "x4run: the game was"

#: The only `--desktop` value accepted, and why.
DESKTOP_MAIN = "main"
_DESKTOP_REFUSAL = ("the driver cannot choose a desktop: the game appears on the current "
                    "desktop and takes focus")

#: The hand step for a run record that names no process (phase starting).
_STARTING_HINT = ("the run record is in phase starting and names no process: check for a running "
                  "X4.exe by hand, then `x4run forget` (`x4run stop` cannot close a record that "
                  "names no process: it can only report it)")


#: What the stubbed readiness probe says. The real probe (`x4live` against the running game) is step 3.
NOT_WIRED = "not wired yet: step 3"


class NotWired(RuntimeError):
    """The real readiness probe does not exist yet (build step 3)."""


def stub_ready_probe() -> bool:
    """The real readiness probe, NOT WIRED in this step. `_cmd_run` refuses before it could be
    called (a probe that raised AFTER the launch would start the game only to close it again)."""
    raise NotWired(NOT_WIRED)


# ----------------------------------------------------------------------------- the parser


def _seconds(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="x4run",
        description="Start X4 through Steam, run a command, close X4 again, and verify nothing "
                    "is left. A launch needs the user's approval of that specific run.",
        epilog=BANNER + " See docs/x4run.md for what is measured and what is not.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "status", help="the recorded run (if any) and the live X4.exe list; read-only",
        description="Print the recorded run (phase, pid, the approval, when it started) or "
                    "'no run recorded', and the live X4.exe processes. A failed process read "
                    "prints 'unknown: process read failed', never 'none'. Changes nothing. Exit 0, "
                    "or 2 when the process read failed or the run record could not be read.")

    pr = sub.add_parser(
        "run", help="start X4, wait for it, run COMMAND, close X4 (needs the approval flags)",
        description="Start X4 through Steam, wait until it is ready, run COMMAND, then ALWAYS "
                    "close the game and verify it is gone. Every approval flag is required: "
                    "the driver records the user's approval but cannot grant it. " + BANNER,
        epilog="exit: 0 done; 2 refused before any launch call, nothing started (safe to retry); "
               "1 a launch call was made and no game was recorded (a game may still appear or be "
               "running: see the output), a game not confirmed closed, an X4.exe left (or not "
               "checkable) after the game closed, or any other failure. Do not start X4 yourself "
               "while a run is starting (up to about 3.5 min): the driver cannot tell your game "
               "from its own and would close it. The game appears on the current desktop and "
               "takes focus. The command's output goes to last-command.out/.err in the state "
               "folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pr.add_argument("--approved-by", required=True, metavar="WHO",
                    help="who approved this specific launch, and where (e.g. 'user, chat "
                         "2026-11-24'); recorded verbatim, never verified")
    pr.add_argument("--mode", required=True, choices=APPROVAL_MODES,
                    help="the approved launch mode")
    pr.add_argument("--target", required=True, metavar="WHAT",
                    help="the approved save or new game to reach (e.g. 'main menu' for menu-only)")
    pr.add_argument("--desktop", required=True, metavar="WHERE",
                    help="the desktop the user approved; only 'main' is accepted: "
                         + _DESKTOP_REFUSAL)
    pr.add_argument("--no-ready-check", action="store_true",
                    help="treat the game as ready the moment its process appears: the real "
                         "readiness probe is not wired yet (step 3), so without this flag `run` "
                         "refuses and starts nothing")
    pr.add_argument("--ready-timeout", type=_seconds, default=300.0, metavar="SECONDS",
                    help="how long to wait for the game to become ready (default 300)")
    pr.add_argument("--command-timeout", type=_seconds, default=3600.0, metavar="SECONDS",
                    help="how long COMMAND may run before it is killed (default 3600)")
    pr.add_argument("--hold", type=_seconds, default=0.0, metavar="SECONDS",
                    help="extra seconds the close waits before it may terminate the game "
                         "(default 0: the terminate comes 60 s after the window-close request, "
                         "which cuts short a crash dump in progress; use >= 600 when a dump "
                         "writer such as procdump is attached)")
    pr.add_argument("command", nargs="*", metavar="COMMAND",
                    help="the command to run once the game is ready, after `--`: an argv list, "
                         "never run through a shell")

    ps = sub.add_parser(
        "stop", help="close the recorded game (the recovery path after a driver crash)",
        description="Close the game named by the run record: a window-close request, then, "
                    "only if that same process is still there, a terminate request. Only the "
                    "recorded process is ever touched. With no record it reads the X4.exe list: "
                    "none running exits 0, any running exits 1 (close it by hand), a failed read "
                    "exits 2.")
    ps.add_argument("--hold", type=_seconds, default=0.0, metavar="SECONDS",
                    help="extra seconds to wait before the terminate request (default 0: the "
                         "terminate comes 60 s after the window-close request, which cuts short "
                         "a crash dump in progress; use >= 600 when a dump writer such as "
                         "procdump is attached)")

    sub.add_parser(
        "forget", help="remove the run record, only after a read shows no X4.exe running",
        description="Remove the run record without parsing it (it works on an empty or damaged "
                    f"one), but only if it was written at least {FORGET_MIN_AGE_S:g} s ago (a "
                    "younger one may belong to a run that is still starting) and ONE successful "
                    "process read "
                    "shows no X4.exe running. A young record, a failed read or a running X4.exe "
                    "refuses (exit 2) and keeps the record. With no record it reads the X4.exe "
                    "list like `stop`.")
    return p


# ----------------------------------------------------------------------------- wording


def _fmt_remaining(rem) -> str:
    if rem is None:
        return "unknown (the last process read failed)"
    if not rem:
        return "none"
    return ", ".join(f"pid {p.pid} ({p.path})" for p in rem)


def _format_close(rep: CloseReport) -> list[str]:
    lines = [f"closed: {'yes' if rep.closed else 'NO'} (method: {rep.method})"]
    lines += [f"  step: {s}" for s in rep.steps]
    lines.append(f"remaining: {_fmt_remaining(rep.remaining)}")
    if rep.closed and rep.remaining is None:
        lines.append("note: the recorded game is gone, but other X4 processes could not be "
                     "checked (the last process read failed)")
    return lines


def _format_cycle(rep: CycleReport, *, ready_checked: bool = True) -> str:
    """`ready_checked` False: readiness was ASSUMED (--no-ready-check), never measured."""
    ready = ("NO" if not rep.ready else
             "yes" if ready_checked else "yes (NOT checked: --no-ready-check)")
    lines = [f"started: {'yes' if rep.started else 'NO'}", f"ready: {ready}"]
    if rep.started and not ready_checked:
        lines.append("note: the readiness probe was skipped (--no-ready-check): the game was "
                     "assumed ready once its process appeared")
    if rep.command_rc is None:
        lines.append("command exit code: none (the command did not finish; see notes)")
    else:
        lines.append(f"command exit code: {rep.command_rc}")
    lines += [f"note: {n}" for n in rep.notes]
    if rep.command_out_path is not None:
        for label, path, tail in (("stdout", rep.command_out_path, rep.command_out_tail),
                                  ("stderr", rep.command_err_path, rep.command_err_tail)):
            lines.append(f"command {label}: {path}")
            if tail:
                lines.append(f"--- command {label}, last {COMMAND_TAIL_BYTES} bytes at most ---")
                lines.append(tail.rstrip("\n"))
                lines.append(f"--- end of command {label} ---")
            else:
                lines.append(f"  (command {label} is empty)")
    if rep.close is not None:
        lines += _format_close(rep.close)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the commands


class _Io:
    def __init__(self, out) -> None:
        self._out = out

    def say(self, text: str = "") -> None:
        print(text, file=self._out if self._out is not None else sys.stdout)

    def err(self, text: str = "") -> None:
        print(text, file=self._out if self._out is not None else sys.stderr)


def _starting_record(store: StateStore) -> bool:
    """True if a run record exists, can be read, and names no process (phase starting)."""
    try:
        state = store.read()
    except (CorruptState, OSError):
        return False  # silent-ok: only selects an extra hint; both callers treat False as "not confirmed closed" / no extra hint, the safe default
    return state is not None and state.process is None


def _failure(exc: BaseException, io: _Io) -> int:
    """One place that turns an exception into a message and an exit code. Never a traceback.

    An exception that carries a close note, or a `StartRefused` whose launch call was issued,
    is exit 1 even though its type is a refusal: a game was (or may have been) started.
    """
    notes = list(getattr(exc, "__notes__", ()))
    unclosed = (any(_UNCLOSED in n or n.startswith(_CLOSE_NOTE) for n in notes)
                or getattr(exc, "launch_attempted", False) is True)
    if isinstance(exc, KeyboardInterrupt):
        io.err("x4run: interrupted")
    elif isinstance(exc, REFUSALS):
        io.err(f"x4run: refused: {exc}")
    else:
        io.err(f"x4run: failed: {type(exc).__name__}: {exc}")
    for n in notes:
        io.err(f"  {n}")
    if unclosed:
        return 1
    if isinstance(exc, KeyboardInterrupt) or not isinstance(exc, REFUSALS):
        io.err("x4run: run `x4run status` to see whether a run record or an X4.exe remains")
        return 1
    return 2


def _cmd_status(store: StateStore, run, sleep, io: _Io) -> int:
    rc = 0
    try:
        state = store.read()
    except (CorruptState, OSError) as exc:
        io.say(f"run record: unreadable: {exc}")
        rc = 2
    else:
        if state is None:
            io.say("run record: no run recorded")
        else:
            ap = state.approval
            io.say(f"run record: {store.path}")
            io.say(f"  run_id: {state.run_id}")
            io.say(f"  phase: {state.phase}")
            if state.process is None:
                io.say("  process: none recorded (the game had not been seen yet)")
            else:
                pr = state.process
                io.say(f"  process: pid {pr.pid} ({pr.name}) {pr.path}, started {pr.started}")
            io.say(f"  started_at: {state.started_at}")
            io.say(f"  approved by: {ap.approved_by}")
            io.say(f"  mode: {ap.mode}; target: {ap.target}; desktop: {ap.desktop}")
            io.say(f"  approval recorded_at: {ap.recorded_at}")
    try:
        procs = read_processes(run, name="X4.exe", sleep=sleep)
    except ProcessReadError as exc:
        io.say(f"X4.exe: unknown: process read failed ({exc})")
        return 2
    if not procs:
        io.say("X4.exe: none running")
    else:
        io.say(f"X4.exe: {len(procs)} running")
        for p in procs:
            io.say(f"  pid {p.pid} {p.path}, started {p.started}")
    return rc


def _cmd_run(args, command, *, run, sleep, clock, game_root, store, ready_probe, io: _Io) -> int:
    io.say(BANNER)
    approval = Approval(approved_by=args.approved_by, mode=args.mode, target=args.target,
                        desktop=args.desktop,
                        recorded_at=datetime.now(timezone.utc).isoformat())
    try:
        require_approval(approval)      # before ANY process read or call
    except UnapprovedLaunch as exc:
        return _failure(exc, io)
    if args.desktop != DESKTOP_MAIN:
        io.err(f"x4run: refused: --desktop {args.desktop!r}: {_DESKTOP_REFUSAL}. Only "
               f"--desktop {DESKTOP_MAIN} is accepted; nothing was started")
        return 2
    if command is None or not command or args.command:
        io.err("x4run: refused: no command to run: put it after `--`, e.g. "
               "`x4run run ... -- python my_check.py`; nothing was started")
        return 2
    probe = ready_probe if ready_probe is not None else stub_ready_probe
    if args.no_ready_check:
        probe = lambda: True  # noqa: E731 - the documented escape from the unwired probe
    elif probe is stub_ready_probe:
        io.err(f"x4run: refused: the readiness probe is {NOT_WIRED}; nothing was started. "
               "Pass --no-ready-check to treat the game as ready once its process appears.")
        return 2
    root = game_root if game_root is not None else _paths.game_root()
    if root is None:
        io.err("x4run: refused: the game root cannot be resolved (set X4_GAME or configure "
               "the toolkit); nothing was started")
        return 2
    io.say(f"approval: by {approval.approved_by}; mode {approval.mode}; target "
           f"{approval.target}; desktop {approval.desktop}; recorded_at {approval.recorded_at}")
    try:
        report = run_cycle(root, approval, store, list(command), ready_probe=probe,
                           run=run, sleep=sleep, clock=clock,
                           ready_timeout_s=args.ready_timeout,
                           command_timeout_s=args.command_timeout, hold_s=args.hold)
    except (Exception, KeyboardInterrupt) as exc:  # every outcome becomes a message and a code
        return _failure(exc, io)
    text = _format_cycle(report, ready_checked=not args.no_ready_check)
    if not report.started:
        io.err(text)
        if store.path.exists() and _starting_record(store) and "x4run forget" not in text:
            io.err(f"x4run: {_STARTING_HINT}")
        # The exit code comes from what `start` DID, never from whether a file exists: once the
        # launch call was issued a game may still appear or be running, so it is not the
        # "nothing started, safe to retry" refusal.
        return 1 if report.launch_attempted else 2
    io.say(text)
    if report.close is None or not report.close.closed:
        return 1
    rem = report.close.remaining
    if rem is None or rem:
        # The preflight PROVED no X4.exe ran when this run began, so any X4.exe now appeared
        # during it (a relaunch, a second instance, a user start), or could not be checked.
        why = ("the last process read failed, so whether an X4.exe is left is unknown" if rem is None
               else "X4.exe still running after the recorded game closed: "
                    + ", ".join(f"pid {p.pid}" for p in rem)
                    + " (none ran when this run began, so it appeared during the run)")
        io.say(f"x4run: exit 1: {why}; check by hand")
        return 1
    if not report.ready or report.command_rc is None:
        return 1
    return 0


def _cmd_stop(args, *, run, sleep, clock, store, io: _Io) -> int:
    try:
        rep = stop(store, run=run, sleep=sleep, clock=clock, hold_s=args.hold)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        code = _failure(exc, io)
        if isinstance(exc, KeyboardInterrupt):
            io.err("x4run: the run record was kept: check for a running X4.exe by hand, then "
                   "run `x4run stop` again (or `x4run forget` if the record names no game)")
        return code
    if rep is None:
        return _no_record(store, run, sleep, io, "stop")
    io.say("\n".join(_format_close(rep)))
    if rep.closed:
        return 0
    if _starting_record(store):
        io.say(f"x4run: {_STARTING_HINT}")
    else:
        io.say("x4run: the game was NOT confirmed closed: the run record was kept; check for a "
               "running X4.exe by hand, then run `x4run stop` again")
    return 1


def _no_record(store: StateStore, run, sleep, io: _Io, verb: str) -> int:
    """`stop`/`forget` found no record HERE: one live read says whether an X4.exe runs anyway."""
    io.say(f"nothing recorded: there is no run record at {store.path}")
    try:
        procs = read_processes(run, name="X4.exe", sleep=sleep)
    except ProcessReadError as exc:
        io.say(f"X4.exe: unknown: process read failed ({exc})")
        return 2
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        io.say(f"x4run: nothing recorded here; X4.exe is running (pid {pids}): close it by hand, "
               f"or use the checkout/host that started it")
        return 1
    io.say(f"X4.exe: none running; nothing to {verb}")
    return 0


def _cmd_forget(*, run, sleep, store, io: _Io) -> int:
    try:
        if not store.path.exists():
            return _no_record(store, run, sleep, io, "forget")
        io.say(forget(store, run=run, sleep=sleep))
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        return _failure(exc, io)
    return 0


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None, *, run=subprocess.run, sleep=time.sleep,
         clock=time.monotonic, game_root=None, store=None, ready_probe=None, out=None) -> int:
    """The `x4run` entry point. The keyword seams are for tests; the defaults are the real ones."""
    raw = list(sys.argv[1:] if argv is None else argv)
    # The command is everything after the first `--`, split off BEFORE argparse sees it: an
    # `nargs="*"` positional would be matched empty ahead of the options that follow it.
    command = None
    if "--" in raw:
        cut = raw.index("--")
        raw, command = raw[:cut], raw[cut + 1:]
    args = build_parser().parse_args(raw)
    io = _Io(out)
    if store is None:
        try:
            store = StateStore(state_folder())
        except StateFolderError as exc:
            return _failure(exc, io)
    if args.cmd == "status":
        return _cmd_status(store, run, sleep, io)
    if args.cmd == "run":
        return _cmd_run(args, command, run=run, sleep=sleep, clock=clock, game_root=game_root,
                        store=store, ready_probe=ready_probe, io=io)
    if args.cmd == "stop":
        return _cmd_stop(args, run=run, sleep=sleep, clock=clock, store=store, io=io)
    return _cmd_forget(run=run, sleep=sleep, store=store, io=io)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

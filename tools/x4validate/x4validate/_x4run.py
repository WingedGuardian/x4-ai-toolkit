r"""x4run: start X4 through Steam, track the process, close it again -- the driver's logic.

EVERY SIDE EFFECT IS INJECTED (`run`, `sleep`, `clock`), so the whole driver is exercised
through a scripted fake and nothing in this module's tests can start Steam or X4. The real
callables are only the DEFAULTS of those parameters.

HIDDEN, ALWAYS. Every child process this module starts passes `creationflags=NO_WINDOW`
(`CREATE_NO_WINDOW`), never `DETACHED_PROCESS`: a visible console is a user-ruled defect, and
a detached child is one that nothing can cleanly wait for.

THE THIRD STATE. A process read that FAILS is neither "no X4 is running" nor "the game
exited". `read_processes` raises `ProcessReadError` for every way the read can fail to give
an answer (non-zero exit, timeout, no PowerShell, malformed output); an EMPTY LIST is a real
answer and only ever means "the query ran and found none". `_livedump.game_pids` returns
None for the same split; here it is an exception, so a caller cannot forget to look.

Sections: process, exit, state, start, close, cycle (below).
"""
from __future__ import annotations

import inspect
import json
import math
import ntpath
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from . import _paths

#: Flag every child process is started with. 0 where the platform has no such flag, so the
#: module still imports on a non-Windows host (the logic is tested there through the fake).
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ====================================================================== section: process

#: Seconds one PowerShell process read may take before it counts as a failed read.
READ_TIMEOUT_S = 15

#: Seconds to wait between two attempts at a failed read.
READ_RETRY_PAUSE_S = 1.0

#: A process image name this module will put inside the PowerShell filter. Conservative on
#: purpose: the name is interpolated into a script, so anything outside it is refused.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.\-]+")


class ProcessReadError(RuntimeError):
    """The process list could not be read. A THIRD STATE: not 'none running', not 'exited'.

    Callers must treat it as 'I do not know' and refuse to act on it.
    """


@dataclass(frozen=True)
class ProcessInfo:
    """One running process as identified for closing: all four fields must match.

    `started` is the process start time as a UTC ISO-8601 string, compared EXACTLY. PID alone
    is not an identity (Windows reuses PIDs); PID plus start time is.
    """

    pid: int
    name: str
    path: str
    started: str


def _ps_script(name: str) -> str:
    # UTF-8 out (no BOM) so a non-ASCII install path survives the pipe; ErrorAction Stop so a
    # failure inside the pipeline is a non-zero exit, never a short list. Each row is built
    # explicitly: CreationDate is a local datetime, converted to UTC and printed round-trip.
    return (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false;"
        f"$r=@(Get-CimInstance Win32_Process -Filter \"Name = '{name}'\" | ForEach-Object {{"
        "[pscustomobject]@{ProcessId=[int]$_.ProcessId;Name=$_.Name;"
        "ExecutablePath=$_.ExecutablePath;"
        "Started=$_.CreationDate.ToUniversalTime().ToString('o')}});"
        "if($r.Count -gt 0){ConvertTo-Json -InputObject $r -Compress}"
    )


def _parse(text: str) -> list[ProcessInfo]:
    """Parse one read's stdout. Empty output is `[]`; anything unparseable raises."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProcessReadError(f"process read gave malformed JSON: {exc}") from exc
    if isinstance(data, dict):  # PowerShell serialises a single object without a list
        data = [data]
    if not isinstance(data, list):
        raise ProcessReadError(f"process read gave {type(data).__name__}, not a list")
    out: list[ProcessInfo] = []
    for row in data:
        if not isinstance(row, dict):
            raise ProcessReadError(f"process row is {type(row).__name__}, not an object")
        pid, nm, path, started = (row.get(k) for k in ("ProcessId", "Name", "ExecutablePath", "Started"))
        if (type(pid) is not int or not isinstance(nm, str) or not isinstance(path, str)
                or not isinstance(started, str) or not (nm and path and started)):
            # A missing path or start time (e.g. an unreadable process) cannot be matched
            # later, so it is not guessed at as "" -- the read is refused.
            raise ProcessReadError(f"process row has a missing or mistyped field: {row!r}")
        out.append(ProcessInfo(pid=pid, name=nm, path=path, started=started))
    return out


def read_processes(run=subprocess.run, *, name: str = "X4.exe", sleep=time.sleep,
                   attempts: int = 3) -> list[ProcessInfo]:
    """Every running process called `name`, read by ONE hidden PowerShell call.

    A transient failure (non-zero exit, timeout, OS error, malformed output) is retried, up to
    `attempts` calls in all with `sleep` between them; the last failure then raises
    `ProcessReadError`. `[]` means the read SUCCEEDED and found none -- never a failed read.
    """
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"unsafe process name: {name!r}")
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ps_script(name)]
    last = "no attempt made"
    for attempt in range(attempts):
        if attempt:
            sleep(READ_RETRY_PAUSE_S)
        try:
            res = run(argv, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=READ_TIMEOUT_S, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            last = f"timed out after {READ_TIMEOUT_S} s"
            continue
        except OSError as exc:
            last = f"could not start PowerShell: {exc}"
            continue
        except UnicodeError as exc:
            last = f"process read output could not be decoded: {exc}"
            continue
        if res.returncode != 0:
            last = f"PowerShell exited {res.returncode}: {(res.stderr or '').strip()[:200]}"
            continue
        if res.stdout is None:
            # A dead reader thread leaves no stdout at all. That is NOT empty output (which
            # is the real answer "none running"): it is a failed read.
            last = "process read produced no stdout"
            continue
        try:
            return _parse(res.stdout)
        except ProcessReadError as exc:
            last = str(exc)
    raise ProcessReadError(f"could not read {name} processes after {attempts} attempt(s): {last}")


# ========================================================================= section: exit

#: Consecutive absent reads that end the watch. One miss is not an exit: one read can be
#: transiently wrong (the cause is unmeasured), so three in a row are required.
EXIT_MISSES = 3


class ExitWatch:
    """Has the RECORDED process gone? True only after `misses` consecutive absent reads.

    Feed it each `read_processes` result through `observe`. "Present" means an entry equal to
    `recorded` on pid, name, path AND started: a reused PID with a different start time is a
    different process, so it counts as a miss. A failed read (`None`) is the third state: it
    neither counts as a miss nor resets the count, so it can never end the watch and never
    erase the evidence gathered so far.

    Not latched: stop observing once it returns True. A later present read resets the count
    to zero and returns False again, while a failed read after True leaves it True.
    """

    def __init__(self, recorded: ProcessInfo, misses: int = EXIT_MISSES) -> None:
        if misses < 1:
            raise ValueError("misses must be at least 1")
        self.recorded = recorded
        self.misses = misses
        self.missed = 0

    def observe(self, procs: list[ProcessInfo] | None) -> bool:
        """Record one read; True once the recorded process has been absent `misses` reads running."""
        if procs is None:
            return self.missed >= self.misses
        if any(p == self.recorded for p in procs):  # frozen dataclass: all four fields
            self.missed = 0
        else:
            self.missed += 1
        return self.missed >= self.misses


# ======================================================================= section: state

#: The launch modes a user can approve. One is named in every approval, so an approval for a
#: menu-only run can never be mistaken for one that loads a save.
APPROVAL_MODES = ("menu-only", "load-save", "new-game")

#: The phases a recorded run moves through.
PHASES = ("starting", "running", "closing")

#: The state file's name inside `state_folder()`.
STATE_FILE = "state.json"

#: Seconds `update`/`clear` keep retrying a `PermissionError` before giving the real error up.
#: A just-written file may be locked for a moment by another process (a virus scanner is one
#: suspected cause; unmeasured), so one refusal is not taken as final.
REPLACE_RETRY_S = 10.0

#: Seconds between two attempts of that retry.
REPLACE_PAUSE_S = 0.5


class UnapprovedLaunch(ValueError):
    """A launch was requested without a complete, valid approval record."""


class UnfinishedRun(RuntimeError):
    """A state file already exists: a game may still be running from an earlier run."""


class CorruptState(RuntimeError):
    """The state file exists but cannot be read as a run record. Never treated as 'no run'."""


class InvalidState(ValueError):
    """A `RunState` that `read` would reject (unknown phase or mode, mistyped field). Never written."""


class StateFolderError(RuntimeError):
    """The machine-wide state folder cannot be located (see `state_folder`)."""


@dataclass(frozen=True)
class Approval:
    """The user's approval of ONE next launch, as given in chat and recorded verbatim."""

    approved_by: str
    mode: str
    target: str
    desktop: str
    recorded_at: str


@dataclass
class RunState:
    """What the driver knows about the run in flight; present on disk exactly while one is."""

    run_id: str
    approval: Approval
    phase: str
    process: ProcessInfo | None
    started_at: str


def require_approval(a: Approval | None) -> Approval:
    """`a` itself when every field is non-blank and `mode` is an approved one, else raise."""
    if a is None:
        raise UnapprovedLaunch("no approval recorded: the user must approve this specific launch first")
    for field in ("approved_by", "mode", "target", "desktop", "recorded_at"):
        v = getattr(a, field)
        if not isinstance(v, str) or not v.strip():
            raise UnapprovedLaunch(f"approval field {field!r} is missing or blank")
    if a.mode not in APPROVAL_MODES:
        raise UnapprovedLaunch(f"approval mode {a.mode!r} is not one of {', '.join(APPROVAL_MODES)}")
    return a


#: Environment variable that overrides the state folder (tests, or a deliberate relocation).
STATE_DIR_ENV = "X4RUN_STATE_DIR"


def state_folder(*, environ=None, platform: str | None = None, home=None) -> Path:
    """The ONE machine-wide (per-user) folder holding the run record, whichever checkout runs.

    In order: `$X4RUN_STATE_DIR` if set; on Windows `%LOCALAPPDATA%/x4-toolkit/x4run`; elsewhere
    `~/.local/state/x4-toolkit/x4run`. Per machine, not per checkout, so a second `run` from ANY
    checkout or worktree sees the first one's record and refuses. Never a cwd-relative or
    per-checkout path: anything that cannot be decided (an unset or relative location) raises
    `StateFolderError`. `environ`, `platform` and `home` are seams for tests.
    """
    # env-ok: a per-user OS location (%LOCALAPPDATA%) or the explicit $X4RUN_STATE_DIR override, not toolkit configuration, so the config-file layer of `_paths` does not apply
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    override = env.get(STATE_DIR_ENV, "")
    if override.strip():
        p = Path(override)
        if not p.is_absolute():
            raise StateFolderError(f"${STATE_DIR_ENV} is not an absolute path: {override!r}")
        return p
    if plat == "win32":
        base = env.get("LOCALAPPDATA", "")
        if not base.strip() or not Path(base).is_absolute():
            raise StateFolderError(
                "cannot locate the x4run state folder: %LOCALAPPDATA% is not set to an absolute "
                f"path (set ${STATE_DIR_ENV} to choose one)")
        return Path(base) / "x4-toolkit" / "x4run"
    try:
        h = Path.home() if home is None else Path(home())
    except (RuntimeError, KeyError, OSError) as exc:
        raise StateFolderError(
            f"cannot locate the x4run state folder: no home folder ({exc}); set ${STATE_DIR_ENV}") from exc
    if not h.is_absolute():
        raise StateFolderError(
            f"cannot locate the x4run state folder: the home folder {str(h)!r} is not absolute; "
            f"set ${STATE_DIR_ENV}")
    return h / ".local" / "state" / "x4-toolkit" / "x4run"


def _str(d: dict, key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str):
        raise ValueError(f"{where}.{key} is missing or not text")
    return v


def _process_from(d: object) -> ProcessInfo | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("process is not an object")
    pid = d.get("pid")
    if type(pid) is not int:  # bool is an int subclass: not a PID
        raise ValueError("process.pid is missing or not an integer")
    return ProcessInfo(pid=pid, name=_str(d, "name", "process"), path=_str(d, "path", "process"),
                       started=_str(d, "started", "process"))


def _state_from(data: object) -> RunState:
    if not isinstance(data, dict):
        raise ValueError(f"top level is {type(data).__name__}, not an object")
    ap = data.get("approval")
    if not isinstance(ap, dict):
        raise ValueError("approval is missing or not an object")
    approval = Approval(**{k: _str(ap, k, "approval") for k in
                           ("approved_by", "mode", "target", "desktop", "recorded_at")})
    if approval.mode not in APPROVAL_MODES:
        raise ValueError(f"approval.mode {approval.mode!r} is not a known mode")
    phase = _str(data, "phase", "state")
    if phase not in PHASES:
        raise ValueError(f"phase {phase!r} is not one of {', '.join(PHASES)}")
    return RunState(run_id=_str(data, "run_id", "state"), approval=approval, phase=phase,
                    process=_process_from(data.get("process")),
                    started_at=_str(data, "started_at", "state"))


def _encode(state: RunState) -> bytes:
    """The bytes to write, or `InvalidState`. Called BEFORE anything on disk is touched.

    Encode FIRST: a failed encode must not leave a truncated file behind. And the text is
    parsed back through the SAME validator `read` uses, so no record can be written that
    `read` would then reject: replacing the last readable record (which may hold a live game's
    process) with an unreadable one is the failure this file exists to prevent.
    """
    try:
        text = json.dumps(asdict(state), indent=2, ensure_ascii=False)
        _state_from(json.loads(text))
    except (ValueError, TypeError) as exc:
        raise InvalidState(f"refusing to write a run record that could not be read back: {exc}") from exc
    return text.encode("utf-8") + b"\n"


class StateStore:
    """The recoverable run record: ONE file that exists exactly while a game may be running.

    `begin` creates it exclusively (`O_EXCL`), so a second start cannot overwrite the record of
    a run that may still be alive; `update` replaces it atomically; `read` raises
    `CorruptState` on anything it cannot parse, because a record that cannot be read is a game
    that cannot be accounted for; `clear` removes it once the run is accounted for.
    `sleep`, `clock` and `replace` are injectable so the transient-lock retry is testable.
    """

    def __init__(self, folder: Path, *, sleep=time.sleep, clock=time.monotonic,
                 replace=os.replace) -> None:
        self.folder = Path(folder)
        self.path = self.folder / STATE_FILE
        self._sleep, self._clock, self._replace = sleep, clock, replace

    def begin(self, state: RunState) -> None:
        data = _encode(state)
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            raise UnfinishedRun(
                f"a run record already exists at {self.path}: a game may still be running "
                f"from an earlier run. `x4run stop` closes a game the record names; a record "
                f"that names none (a start that never recorded its game) is cleared with "
                f"`x4run forget` after checking for a running X4.exe by hand. Never delete "
                f"the file by hand.") from None
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
        except BaseException:
            # We created it, so a half-written record is ours to remove; left behind it
            # would block every later start as a bogus 'unfinished run'.
            try:
                self.path.unlink()
            except OSError:
                pass  # silent-ok: best-effort cleanup of our own half-written file; the original error is re-raised below
            raise

    def _retrying(self, op) -> None:
        deadline = self._clock() + REPLACE_RETRY_S
        while True:
            try:
                op()
                return
            except PermissionError:
                if self._clock() >= deadline:
                    raise
                self._sleep(REPLACE_PAUSE_S)

    def update(self, state: RunState) -> None:
        if not self.path.exists():
            raise FileNotFoundError(
                f"no run record at {self.path}: `begin` must create it before `update`")
        data = _encode(state)
        tmp = self.folder / f"{STATE_FILE}.{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            self._retrying(lambda: self._replace(tmp, self.path))
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass  # silent-ok: best-effort cleanup of our own temp file; the original error is re-raised below
            raise

    def read(self) -> RunState | None:
        """The recorded run, or None when there is no file. Anything unreadable raises."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None  # silent-ok: an absent record is the defined "no run recorded" answer; unreadable ones raise below
        try:
            return _state_from(json.loads(raw.decode("utf-8")))
        except (ValueError, TypeError) as exc:  # UnicodeError and JSONDecodeError are ValueErrors
            raise CorruptState(f"run record {self.path} is unreadable ({exc}); it was left in "
                               f"place: inspect it. No game can be read from it, so check for a "
                               f"running X4.exe by hand, then clear it with `x4run forget`") from exc

    def clear(self) -> None:
        def unlink() -> None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # silent-ok: clearing an already-absent record is the goal state; any other error propagates
        self._retrying(unlink)


# ====================================================================== section: start

#: Seconds the Steam launch call may take. It only ASKS the running Steam client to start the
#: game and is expected to return quickly when Steam is already running (unmeasured); a
#: timeout means UNKNOWN, not failed: see the poll in `start`.
LAUNCH_CALL_TIMEOUT_S = 30

#: Seconds between two reads while waiting for the game process to appear.
POLL_INTERVAL_S = 2.0

#: Seconds `start` waits for the game process after Steam was asked (a cold start is slow).
START_TIMEOUT_S = 180.0

_GAME_EXE = "X4.exe"
_STEAM_EXE = "steam.exe"


class StartRefused(RuntimeError):
    """A start was refused or did not produce a recorded game. The message says exactly what
    was and was not done (what was launched, whether a run record exists).

    `launch_attempted` (keyword-only, required) is True once the Steam launch call was issued:
    from then on a game may appear or be running although none was recorded. It is False only
    for a refusal before that call (or a call that could not be started at all), where nothing
    can have been started.
    """

    def __init__(self, message: str, *, launch_attempted: bool) -> None:
        super().__init__(message)
        self.launch_attempted = bool(launch_attempted)


def steam_exe(game_root: Path) -> Path:
    """`steam.exe` beside the Steam root of `game_root` (`<Steam>/steamapps/common/<game>`).

    Does NOT check that the file exists: a missing Steam makes the launch call fail, and
    `start` reports that failure (and refuses) rather than guessing another location.
    """
    return Path(game_root).parents[2] / "steam.exe"


def _clear_note(store: "StateStore") -> str:
    """Clear the run record; say in one clause what became of it (a failed clear is named)."""
    try:
        store.clear()
    except OSError as exc:
        return (f"the run record at {store.path} could NOT be removed ({exc}); check for a "
                f"running X4.exe by hand, then clear it with `x4run forget`")
    return "the run record was removed"


#: Recovery for a run record that names NO game (phase starting, or unreadable): `stop` has
#: nothing to close and can only report it, so the by-hand check and `forget` are the way out.
_NO_GAME_RECOVERY = ("check for a running X4.exe by hand, then `x4run forget` (the record names "
                     "no game, so `x4run stop` cannot close it)")


def _recovery_hint(store: "StateStore") -> str:
    """What to do about the record at `store.path`: `stop` if it names a game, else `forget`."""
    try:
        state = store.read()
    except (CorruptState, OSError):
        state = None
    if state is not None and state.process is not None:
        return "check for a running X4.exe by hand, then recover with `x4run stop`"
    return _NO_GAME_RECOVERY


def _norm_exe(path) -> str:
    """A Windows path compared by value: separators unified, `.`/`..` collapsed, case folded."""
    return ntpath.normpath(str(path).replace("/", "\\")).casefold()


def start(game_root: Path, approval: Approval | None, store: StateStore, *,
          run=subprocess.run, sleep=time.sleep, clock=time.monotonic,
          timeout_s: float = START_TIMEOUT_S) -> RunState:
    """Start X4 through Steam ONCE, wait for its process, record it. Never retries.

    Order: approval (before any call at all) -> the launch argv (a game root too shallow to
    have a Steam root is refused here) -> one `X4.exe` read -> one `steam.exe` read (Steam must
    ALREADY be running: this driver never starts the Steam client) -> run record ("starting",
    no process) -> ONE hidden `steam.exe -applaunch <appid>` call -> poll for a new `X4.exe`
    -> record it ("running"). The record exists from before the launch call until the game
    is accounted for, so a crash anywhere after the call leaves something `x4run stop` can
    recover from. It is removed only where NOTHING is known to have started: the launch call
    could not be started at all (`OSError`), or the LAST poll read succeeded and saw no game
    by the deadline (a last read that FAILED keeps it: a game may be running unseen). A
    launch call that TIMES OUT or exits NON-ZERO is unknown, not failed (what the forwarder's
    exit codes mean is unmeasured): it still polls and records a game that appears.
    Any other exit after `begin` (an interrupt, an unexpected error, a failed update) leaves
    it in place, because a game may be starting.

    Only `<game_root>/X4.exe` is ever recorded (compared case-insensitively, separators
    normalised): a new X4.exe anywhere else, or more than one, is refused with the record
    kept. The driver cannot tell a game the USER starts from that same folder during the
    poll from its own.

    Every `StartRefused` says whether the launch call was issued (`launch_attempted`).
    """
    approval = require_approval(approval)  # before ANY call: an unapproved launch does nothing

    def before_launch(msg: str) -> StartRefused:
        return StartRefused(msg, launch_attempted=False)

    def after_launch(msg: str) -> StartRefused:
        return StartRefused(msg, launch_attempted=True)

    # The launch argv is computed BEFORE any read or record, so a game root without a Steam
    # root above it is a clean refusal, not an IndexError after a record was written.
    try:
        argv = [str(steam_exe(game_root)), "-applaunch", _paths.STEAM_APPID]
    except IndexError:
        raise before_launch(
            f"cannot find the Steam folder above the game root {str(game_root)!r} (the game "
            f"root must sit three folders below it). Refused: nothing was started and no run "
            f"record was written.") from None
    own_exe = _norm_exe(Path(game_root) / _GAME_EXE)

    # A failed read refuses the start (the third state is never "no game"). In particular a
    # game process whose path/start time the driver cannot read (an elevated X4 against a
    # non-elevated driver) makes every read fail, which refuses here: the safe direction.
    try:
        running = read_processes(run, name=_GAME_EXE, sleep=sleep)
    except ProcessReadError as exc:
        raise before_launch(
            f"the running processes could not be read, so whether {_GAME_EXE} is already "
            f"running is unknown: {exc}. Refused: nothing was started and no run record was written.") from exc
    if running:
        pids = ", ".join(str(p.pid) for p in running)
        raise before_launch(
            f"{_GAME_EXE} is already running (pid {pids}). Refused: nothing was started and no "
            f"run record was written.")

    # The launch call below runs `steam.exe -applaunch`: with no Steam running, that process
    # would BE the Steam client, which a timeout would then kill. So Steam must already be up.
    try:
        steam = read_processes(run, name=_STEAM_EXE, sleep=sleep)
    except ProcessReadError as exc:
        raise before_launch(
            f"the running processes could not be read, so whether Steam is running is unknown: "
            f"{exc}. Refused: nothing was started and no run record was written.") from exc
    if not steam:
        raise before_launch(
            "Steam is not running: start Steam first; the driver does not start the Steam "
            "client. Refused: nothing was started and no run record was written.")

    state = RunState(run_id=uuid.uuid4().hex, approval=approval, phase="starting", process=None,
                     started_at=datetime.now(timezone.utc).isoformat())
    store.begin(state)  # UnfinishedRun (a record exists) propagates unwrapped

    # Steps below deliberately have no blanket cleanup handler: every path that started
    # NOTHING clears the record explicitly, and every other exit (KeyboardInterrupt included)
    # keeps it, since a game may be starting.
    launch_unknown = ""  # why the launch call's outcome is unknown, if it is
    try:
        # No captured pipes: a grandchild (Steam's helpers, the game) that inherited a pipe
        # would keep this call blocked until the timeout. Output goes nowhere.
        res = run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  timeout=LAUNCH_CALL_TIMEOUT_S, creationflags=NO_WINDOW)
        if res.returncode != 0:
            # UNKNOWN, not failed: what a non-zero exit of the forwarder means is unmeasured,
            # and Steam may still start the game. Poll exactly as after a timeout.
            launch_unknown = (f"the launch call exited {res.returncode} (what that means is "
                              f"unmeasured)")
    except subprocess.TimeoutExpired:
        # UNKNOWN, not failed: Steam may well have started the game. Fall through to the
        # normal poll (never a second launch call) so a game that does appear is recorded.
        launch_unknown = f"the launch call timed out after {LAUNCH_CALL_TIMEOUT_S} s"
    except OSError as exc:
        # The ONLY definite failure: steam.exe could not be started, so the call never ran
        # and nothing can have been started.
        raise before_launch(
            f"the Steam launch call could not be made ({exc}); it is not retried. Nothing was "
            f"started, no game was recorded and {_clear_note(store)}.") from exc

    # The poll gets its OWN full `timeout_s`, counted from when the launch call returned (or
    # timed out), so a slow launch call does not eat into the time the game needs to appear.
    deadline = clock() + timeout_s
    failed_reads = 0
    while True:
        sleep(POLL_INTERVAL_S)
        try:
            new = read_processes(run, name=_GAME_EXE, sleep=sleep)
            last_read_ok = True
        except ProcessReadError:
            new = []  # a failed read is not "no game": keep polling until the deadline
            last_read_ok = False
            failed_reads += 1
        if new:
            break
        if clock() >= deadline:
            if not last_read_ok:
                # The last word was a failed read: whether a game exists is UNKNOWN, so the
                # record is KEPT (clearing it could orphan a running game).
                raise after_launch(
                    f"Steam was asked to start the game, but the last {_GAME_EXE} process read "
                    f"failed ({failed_reads} read(s) failed in all) before the {timeout_s:g} s "
                    f"deadline: a game may be running unseen. The run record was KEPT (phase "
                    f"starting, no process). Check for an {_GAME_EXE} by hand"
                    + (f"; {launch_unknown}" if launch_unknown else "")
                    + ". The launch call was made once and is not retried.")
            note = (f" ({failed_reads} earlier process read(s) failed)" if failed_reads else "")
            why = (f"{launch_unknown[0].upper()}{launch_unknown[1:]}, so Steam may still start "
                   f"the game: check for an {_GAME_EXE} before starting again. "
                   if launch_unknown else
                   f"Steam may still start the game late: check for an {_GAME_EXE} before "
                   f"starting again. ")
            raise after_launch(
                f"Steam was asked to start the game but no {_GAME_EXE} appeared within "
                f"{timeout_s:g} s{note}. {why}The launch call was made once and is not retried; "
                f"no game was recorded and {_clear_note(store)}.")

    listed = "; ".join(f"pid {p.pid} at {p.path}" for p in new)
    if len(new) > 1:
        raise after_launch(
            f"{len(new)} {_GAME_EXE} processes appeared at once ({listed}); which one this "
            f"start launched cannot be told, so none was recorded. The run record was kept "
            f"(phase starting, no process): {_NO_GAME_RECOVERY}")
    if _norm_exe(new[0].path) != own_exe:
        raise after_launch(
            f"an {_GAME_EXE} appeared that is not this game root's ({listed}; expected "
            f"{Path(game_root) / _GAME_EXE}), so it is not recognised as the one this start "
            f"launched and was not recorded. The run record was kept (phase starting, no "
            f"process): {_NO_GAME_RECOVERY}")

    state.process, state.phase = new[0], "running"
    try:
        store.update(state)
    except (OSError, InvalidState) as exc:
        raise after_launch(
            f"{_GAME_EXE} started (pid {new[0].pid}) but could not be recorded: {exc}. The run "
            f"record keeps phase starting with no process; close the game yourself. "
            f"{_NO_GAME_RECOVERY}") from exc
    return state


# ====================================================================== section: close

#: Seconds the window-close request is given to end the game before it is terminated. The
#: 2026-11-03 quit freeze showed a close request can be ignored for a long time: this is a
#: default, not a measurement, and `hold_s` extends it while a crash dump is written. The
#: driver does not detect a dump: with the default hold of 0 the terminate comes 60 s after
#: the window-close request and cuts short any dump still being written.
CLOSE_GRACE_S = 60.0

#: Seconds to watch for the exit after the terminate request.
KILL_WAIT_S = 30.0

#: Seconds one close or terminate call may take.
CLOSE_CALL_TIMEOUT_S = 30

# The pid is passed as its OWN argv element after a constant script, but PowerShell `-Command`
# joins the arguments that follow into one command string, so `str(pid)` IS appended to the
# command text. The safeguard is therefore the value, not the channel: `close` accepts a pid
# only if it is an `int` (not a bool) and >= 1, checked before any call, so only digits can
# be appended, and `[int]$ProcId` binds them. (The binding was checked once, in review, with
# a harmless read-only stand-in of this wrapper shape: `-Command "& {param([int]$ProcId) ...}"
# 123` bound 123 to $ProcId. The close script has run against the real game once (2026-11-25, at
# the main menu); the terminate script has NEVER been run.)
# Exit 3: no main window to ask. The close and terminate scripts are separate and never both
# in one call.
_CLOSE_SCRIPT = (
    "& { param([int]$ProcId) $ErrorActionPreference='Stop'; "
    "$p = Get-Process -Id $ProcId; if (-not $p.CloseMainWindow()) { exit 3 } }"
)
_TERMINATE_SCRIPT = (
    "& { param([int]$ProcId) $ErrorActionPreference='Stop'; Stop-Process -Id $ProcId }"
)


@dataclass(frozen=True)
class CloseReport:
    """What `close` did. `closed` is True only when the recorded process is confirmed gone.

    `method`: "already-gone" (it was not running; nothing was called), "window-close" (it
    ended within the grace period), "terminate" (it ended after the terminate request) or
    "unclosed". `steps` says in order what was done.

    `remaining` is every X4.exe seen by the LAST read, unrelated ones too (they are only ever
    reported). `None` means UNKNOWN: the last read failed, or no read ever succeeded. `()` means
    a read that SUCCEEDED saw none. It is never filled from an earlier read.
    """

    closed: bool
    method: str
    steps: tuple[str, ...]
    remaining: tuple[ProcessInfo, ...] | None


class _Reader:
    """Reads all X4.exe; `None` for a failed read (the third state).

    `latest` is the answer of the MOST RECENT read (`None` if it failed or none was made): it
    is never an older read's result, so it can feed `CloseReport.remaining` honestly.
    """

    def __init__(self, run, sleep) -> None:
        self._run, self._sleep = run, sleep
        self.latest: list[ProcessInfo] | None = None
        self.error = ""

    def __call__(self) -> list[ProcessInfo] | None:
        try:
            procs = read_processes(self._run, name=_GAME_EXE, sleep=self._sleep)
        except ProcessReadError as exc:
            self.error = str(exc)
            self.latest = None
            return None
        self.latest = procs
        return procs


def _ask(run, script: str, pid: int) -> str | None:
    """One hidden PowerShell call; None if it exited 0, else a short account of what went wrong."""
    argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, str(pid)]
    try:
        res = run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=CLOSE_CALL_TIMEOUT_S, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return f"the call timed out after {CLOSE_CALL_TIMEOUT_S} s"
    except (OSError, UnicodeError) as exc:
        return f"the call could not be made: {exc}"
    if res.returncode != 0:
        return f"the call exited {res.returncode}: {(res.stderr or '').strip()[:200]}"
    return None


def _wait_for_exit(watch: ExitWatch, read, sleep, clock, deadline: float) -> bool:
    """Poll until `watch` confirms the exit (True) or `deadline` has passed (False).

    A read is always made AFTER the last sleep, so when this returns False the clock is at or
    past `deadline`. It stops reading the moment `observe` returns True: ExitWatch is not latched.
    """
    while True:
        sleep(max(0.0, min(POLL_INTERVAL_S, deadline - clock())))
        if watch.observe(read()):
            return True
        if clock() >= deadline:
            return False


def _check_seconds(label: str, value) -> None:
    """Raise `ValueError` naming `label` unless `value` is a finite number of seconds >= 0.

    The ONE rule for every duration this module accepts (`close` and `run_cycle` both call it):
    a bool is not a number of seconds, and NaN, infinity and negatives are refused.
    """
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite number of seconds >= 0, got {value!r}")


def close(state: RunState, store: StateStore, *, run=subprocess.run, sleep=time.sleep,
          clock=time.monotonic, grace_s: float = CLOSE_GRACE_S, kill_wait_s: float = KILL_WAIT_S,
          hold_s: float = 0.0) -> CloseReport:
    """Close the RECORDED process, gently first; touch nothing else.

    A failed read, close call or terminate call is reported in the result, never raised; a
    record the store rejects (`InvalidState`) still raises, before any call.

    Order: phase "closing" (a record the store would reject raises `InvalidState` before any
    call; a write that fails with `OSError` is noted and the close goes ahead) -> read all X4.exe, twice running (the second read is the start-time re-check right
    before the request) -> a hidden window-close request for that pid -> watch up to
    `grace_s + hold_s` -> only if it is still there, one more read, and a hidden terminate
    request for that pid if it still matches on all four fields -> watch up to `kill_wait_s`
    -> a last read, whose X4.exe processes are `remaining`.

    Only a process equal to the recorded one on pid, name, path AND start time is ever asked
    to close or terminated; a read that fails is never "gone" (it makes no call at all if it
    comes before the request). The terminate is never made before `grace_s + hold_s` has
    elapsed. `closed` needs `ExitWatch` to confirm the exit; the run record is cleared then
    and KEPT otherwise. A record with no process (phase "starting") is never closed: it is
    reported and kept. The remaining gap is the few milliseconds between the last start-time
    read and the terminate itself.
    """
    for label, value in (("grace_s", grace_s), ("kill_wait_s", kill_wait_s), ("hold_s", hold_s)):
        _check_seconds(label, value)
    t0 = clock()
    steps: list[str] = []

    def note(msg: str) -> None:
        steps.append(f"t+{clock() - t0:.0f}s {msg}")

    try:
        store.update(replace(state, phase="closing"))  # InvalidState propagates, before any call
        note("run record set to phase closing")
    except OSError as exc:
        # The identity is in memory, so the close goes ahead: leaving the game running because
        # one record write failed is the worse outcome. The record keeps its previous phase.
        note(f"the run record could not be set to phase closing ({exc}); it keeps its previous "
             f"phase, and the close goes ahead with the identity held in memory")
    read = _Reader(run, sleep)
    rec = state.process

    def rem(which: str = "last") -> tuple[ProcessInfo, ...] | None:
        """`remaining` from the most recent read; None, with a step saying why, if it failed."""
        if read.latest is None:
            note(f"the {which} {_GAME_EXE} read failed or none was made, so `remaining` is "
                 f"unknown (not empty): {read.error or 'no read was made'}")
            return None
        return tuple(read.latest)

    def report(closed: bool, method: str, remaining) -> CloseReport:
        return CloseReport(closed, method, tuple(steps), remaining)

    if rec is None:
        read()
        note("no process was recorded (the run was still starting): nothing was closed or "
             "terminated; the run record was KEPT")
        return report(False, "unclosed", rem())
    if type(rec.pid) is not int or rec.pid < 1:
        note(f"the recorded pid {rec.pid!r} is not a positive integer: nothing was closed or "
             f"terminated; the run record was KEPT")
        return report(False, "unclosed", rem())

    def finish_gone(method: str, remaining) -> CloseReport:
        note(f"the recorded process (pid {rec.pid}) is gone; {_clear_note(store)}")
        return report(True, method, remaining)

    def keep(why: str, remaining) -> CloseReport:
        note(f"{why}; the run record was KEPT (phase closing)")
        return report(False, "unclosed", remaining)

    # --- 1. find it: present on two reads running (the second is the start-time re-check)
    watch = ExitWatch(rec)
    deadline = clock() + max(kill_wait_s, POLL_INTERVAL_S * (EXIT_MISSES + 1))
    present_reads = 0
    while True:
        procs = read()
        exited = watch.observe(procs)
        if procs is not None and rec in procs:
            present_reads += 1
            if present_reads >= 2:
                break
            continue  # re-read at once: the request is made only on a fresh match
        present_reads = 0
        if exited:
            note("the recorded process is not running (confirmed by repeated reads); no close "
                 "or terminate call was made")
            return finish_gone("already-gone", rem())
        if clock() >= deadline:
            return keep("could not establish whether the recorded process is running "
                        f"(last read error: {read.error or 'none'}); no close or terminate "
                        "call was made", rem())
        sleep(POLL_INTERVAL_S)

    # --- 2. ask the window to close, then give it the grace period (plus any hold)
    problem = _ask(run, _CLOSE_SCRIPT, rec.pid)
    note(f"window-close request for pid {rec.pid} " + (f"failed: {problem}" if problem else "sent"))
    watch = ExitWatch(rec)
    terminate_problem = None
    if _wait_for_exit(watch, read, sleep, clock, clock() + grace_s + hold_s):
        note(f"the recorded process ended within the grace period ({grace_s + hold_s:g} s)")
        method = "window-close"
    else:
        note(f"still running after {grace_s + hold_s:g} s (grace {grace_s:g} s + hold {hold_s:g} s)")
        # --- 3. terminate, only the recorded process, only after one more start-time check
        verdict = ""
        recheck_deadline = clock() + max(kill_wait_s, POLL_INTERVAL_S * (EXIT_MISSES + 1))
        while True:
            procs = read()
            if watch.observe(procs):
                verdict = "exited"
            elif procs is not None:
                verdict = "present" if rec in procs else "changed"
            elif clock() >= recheck_deadline:
                verdict = "unreadable"
            if verdict:
                break
            sleep(POLL_INTERVAL_S)
        if verdict == "unreadable":
            return keep("the start-time re-check before the terminate could not be read, so "
                        "nothing was terminated", rem())
        method = "window-close"
        if verdict == "present":
            terminate_problem = _ask(run, _TERMINATE_SCRIPT, rec.pid)
            note(f"terminate request for pid {rec.pid} "
                 + (f"failed: {terminate_problem}" if terminate_problem else "sent"))
            method = "terminate"
        elif verdict == "changed":
            note("the recorded process no longer matches on a re-read: nothing was terminated")
        if verdict != "exited" and not _wait_for_exit(watch, read, sleep, clock, clock() + kill_wait_s):
            read()
            return keep(f"the recorded process did not end within {kill_wait_s:g} s",
                        rem())

    if terminate_problem:
        note(f"the recorded process ended although the terminate call failed ({terminate_problem}); "
             f"the method stays 'terminate' because a terminate request was issued")

    # --- 4. a last read: closed only if it agrees (a contradiction is never reported closed)
    final = read()
    if final is None:
        return finish_gone(method, rem("final"))
    if rec in final:
        return keep("the recorded process is present again on the final read", rem("final"))
    return finish_gone(method, rem("final"))


# ====================================================================== section: cycle

#: Seconds between two `ready_probe` calls while waiting for the game to become ready.
READY_POLL_S = 5.0


class StopCannotRead(RuntimeError):
    """`stop` found a run record it cannot read. The message says what to do by hand."""


class ForgetRefused(RuntimeError):
    """`forget` was refused: a game may be running, or the record could not be removed."""


@dataclass
class CycleReport:
    """What `run_cycle` did. `close` is None only when no game was ever recorded as started.

    `started` False means `start` refused (the reason is in `notes`); `launch_attempted` then
    says whether the Steam launch call had been issued (a game may appear or be running
    although none was recorded). `command_rc` is None when the command was not run, timed out,
    or could not be started (`notes` says which). `notes` carries everything a person must
    read, in particular a game that is NOT confirmed closed.

    The command's stdout and stderr go to two files in the state folder (`command_out_path`,
    `command_err_path`, None when they were never opened); `command_out_tail` and
    `command_err_tail` are the last `COMMAND_TAIL_BYTES` of each, decoded as UTF-8 with
    replacement characters.
    """

    started: bool
    ready: bool
    command_rc: int | None
    close: CloseReport | None
    notes: tuple[str, ...]
    launch_attempted: bool
    command_out_path: Path | None = None
    command_err_path: Path | None = None
    command_out_tail: str = ""
    command_err_tail: str = ""


#: The files in the state folder that receive the caller's command output (truncated per run).
COMMAND_OUT_FILE = "last-command.out"
COMMAND_ERR_FILE = "last-command.err"

#: How much of the end of each output file the report carries.
COMMAND_TAIL_BYTES = 4096


def _tail(path: Path) -> str:
    """The last COMMAND_TAIL_BYTES of `path`, decoded; a read failure is said, never empty."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - COMMAND_TAIL_BYTES))
            data = fh.read()
    except OSError as exc:
        return f"<could not read {path}: {exc}>"
    return data.decode("utf-8", errors="replace")


def _unclosed_note(rep: CloseReport) -> str:
    if rep.remaining is None:
        seen = "which X4.exe processes remain is UNKNOWN (the last read failed)"
    elif rep.remaining:
        seen = "X4.exe still running: " + ", ".join(f"pid {p.pid}" for p in rep.remaining)
    else:
        seen = "no X4.exe was seen on the last read"
    return (f"the game was NOT confirmed closed ({rep.method}; {seen}). The run record was "
            f"kept: check for a running X4.exe by hand, then recover with `x4run stop`")


def _wait_ready(probe, sleep, clock, timeout_s: float, notes: list[str]) -> bool:
    """Poll `probe` about every READY_POLL_S until it is true or `timeout_s` has passed.

    A probe that raises counts as not ready and is recorded (first message, and how many).
    """
    deadline = clock() + timeout_s
    errors: list[str] = []
    ready = False
    while True:
        try:
            ready = bool(probe())
        except Exception as exc:  # noqa: BLE001 - any probe failure is "not ready", recorded
            errors.append(f"{type(exc).__name__}: {exc}")
            ready = False
        if ready or clock() >= deadline:
            break
        sleep(max(0.0, min(READY_POLL_S, deadline - clock())))
    if errors:
        notes.append(f"the ready probe raised {len(errors)} time(s); first: {errors[0]}"
                     + (f"; last: {errors[-1]}" if len(errors) > 1 else ""))
    if not ready:
        notes.append(f"the game was not ready within {timeout_s:g} s; the command was not run")
    return ready


def _check_command(command) -> list[str]:
    if (not isinstance(command, list) or any(not isinstance(a, str) for a in command)):
        raise TypeError("command must be an argv LIST of strings (it is never run through a shell)")
    if not command or not command[0].strip():
        raise ValueError("command is empty: it needs a program to run")
    if any("\0" in a for a in command):
        # `subprocess.run` would refuse it only AFTER the game was started.
        raise ValueError("command contains a NUL character, which no argv can carry")
    return command


def _check_cycle_args(close_kw: dict, ready_timeout_s, command_timeout_s) -> None:
    """Everything `run_cycle` could only discover AFTER the game started, refused up front.

    `close_kw` is bound against `close`'s own signature (an unknown or misspelled keyword is a
    `TypeError`), and every duration is checked by the rule `close` uses. A bad value found
    only inside the final `close` would leave a launched game running.
    """
    inspect.signature(close).bind(None, None, **close_kw)  # TypeError on a bad keyword
    for label, value in (("ready_timeout_s", ready_timeout_s),
                         ("command_timeout_s", command_timeout_s), *close_kw.items()):
        if label in ("grace_s", "kill_wait_s", "hold_s", "ready_timeout_s", "command_timeout_s"):
            _check_seconds(label, value)


def run_cycle(game_root: Path, approval: Approval | None, store: StateStore, command: list[str],
              *, ready_probe, run=subprocess.run, sleep=time.sleep, clock=time.monotonic,
              ready_timeout_s: float = 300.0, command_timeout_s: float = 3600.0,
              **close_kw) -> CycleReport:
    """Start the game, wait until it is ready, run `command`, ALWAYS close the game.

    `command` is an argv list, started hidden (`creationflags=NO_WINDOW`), never through a
    shell, with `command_timeout_s`; `subprocess.run` kills a timed-out command itself. It is
    run only if `ready_probe()` became true within `ready_timeout_s`. Once `start` has returned
    a recorded game, `close` is called in a `finally`: whatever happened in between (the probe
    never ready or raising, the command failing, timing out, not starting, an interrupt), the
    game is closed or reported as not closed. On an exception the close outcome is attached to
    it as a note (the report cannot be returned then).

    The command gets `stdin=DEVNULL` and its stdout/stderr redirected to `last-command.out`
    and `last-command.err` in the state folder (truncated once this run owns the record,
    closed in a `finally`): a hidden console child's own output is otherwise lost. The report
    names both files and carries the end of each.

    A `StartRefused` returns a report with `started=False` and nothing to close. The other
    start failures (`UnapprovedLaunch`, `UnfinishedRun`, `InvalidState`, `CorruptState`)
    propagate unchanged: the caller must see them.
    """
    _check_command(command)  # before anything is started
    _check_cycle_args(close_kw, ready_timeout_s, command_timeout_s)
    try:
        state = start(game_root, approval, store, run=run, sleep=sleep, clock=clock)
    except StartRefused as exc:
        notes = [str(exc)]
        if store.path.exists():
            notes.append(f"the refusal kept a run record at {store.path}: a game may be running; "
                         f"{_recovery_hint(store)}")
        return CycleReport(False, False, None, None, tuple(notes),
                           launch_attempted=exc.launch_attempted)

    notes: list[str] = []
    ready = False
    rc: int | None = None
    close_report: CloseReport | None = None
    pending: BaseException | None = None
    out_path = store.folder / COMMAND_OUT_FILE
    err_path = store.folder / COMMAND_ERR_FILE
    opened = False
    out_fh = err_fh = None
    try:
        try:
            # Truncated here, at the start of THIS run (it owns the record now), so files left
            # by an earlier run can never pass for this run's output.
            out_fh = open(out_path, "wb")
            err_fh = open(err_path, "wb")
            opened = True
        except OSError as exc:
            notes.append(f"the command's output files in {store.folder} could not be opened "
                         f"({exc}); the command will not be run")
        ready = _wait_ready(ready_probe, sleep, clock, ready_timeout_s, notes)
        if ready and opened:
            try:
                res = run(command, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh,
                          timeout=command_timeout_s, creationflags=NO_WINDOW, shell=False)
                rc = res.returncode
            except subprocess.TimeoutExpired:
                notes.append(f"the command timed out after {command_timeout_s:g} s and was killed")
            except OSError as exc:
                notes.append(f"the command could not be run: {exc}")
    except BaseException as exc:  # an interrupt or an unexpected error: re-raised after the close
        pending = exc
        raise
    finally:
        for fh in (out_fh, err_fh):
            if fh is not None:
                try:
                    fh.close()
                except OSError as exc:
                    notes.append(f"closing a command output file failed: {exc}")
        try:
            close_report = close(state, store, run=run, sleep=sleep, clock=clock, **close_kw)
        except BaseException as close_exc:
            # The game was started and nothing is known to have closed it: say so on the error
            # the caller will see (the in-flight exception stays chained as its context).
            close_exc.add_note(
                f"x4run: the game was NOT confirmed closed: `close` failed "
                f"({type(close_exc).__name__}: {close_exc}) and no close was confirmed. A game "
                f"may still be running. The run record is at {store.path}: recover with "
                f"`x4run stop`; if that cannot read the record, check for a running X4.exe by "
                f"hand and then use `x4run forget`."
                + (f" The error being handled when the close failed: {pending!r}." if pending is not None else ""))
            raise
        if pending is not None and hasattr(pending, "add_note"):
            pending.add_note("x4run: the game was closed afterwards" if close_report.closed
                             else "x4run: " + _unclosed_note(close_report))
    if not close_report.closed:
        notes.append(_unclosed_note(close_report))
    if opened:
        return CycleReport(True, ready, rc, close_report, tuple(notes), launch_attempted=True,
                           command_out_path=out_path, command_err_path=err_path,
                           command_out_tail=_tail(out_path), command_err_tail=_tail(err_path))
    return CycleReport(True, ready, rc, close_report, tuple(notes), launch_attempted=True)


def _size_text(store: StateStore) -> str:
    try:
        n = store.path.stat().st_size
    except OSError:
        return "a file whose size could not be read"
    return "an empty file (0 bytes)" if n == 0 else f"{n} bytes that do not form a run record"


def stop(store: StateStore, *, run=subprocess.run, sleep=time.sleep, clock=time.monotonic,
         **close_kw) -> CloseReport | None:
    """Close the game named by the run record (the recovery path for a driver that died).

    None when there is no record. A record in phase "starting" has no process, so `close`
    makes no call and keeps it: the returned report is "unclosed" and says so. A record that
    cannot be read raises `StopCannotRead`, which says what to do by hand.
    """
    try:
        state = store.read()
    except CorruptState as exc:
        raise StopCannotRead(
            f"the run record {store.path} cannot be read: it is {_size_text(store)}. The "
            f"driver died between creating it and writing it, or it was damaged. Nothing was "
            f"closed. Check for a running X4.exe by hand (Task Manager or "
            f"`Get-Process X4`); `x4run forget` clears the record, and does so only after a "
            f"read shows no X4.exe running.") from exc
    if state is None:
        return None
    return close(state, store, run=run, sleep=sleep, clock=clock, **close_kw)


#: Seconds a run record must be old (by FILE MTIME) before `forget` removes it: the longest a
#: live start can take to record its game (launch call + poll) plus a margin. A younger record
#: may belong to a driver that is still starting, whose game is not running YET.
FORGET_MIN_AGE_S = LAUNCH_CALL_TIMEOUT_S + START_TIMEOUT_S + 60


def forget(store: StateStore, *, run=subprocess.run, sleep=time.sleep, now=time.time) -> str:
    """Remove the run record, but only if it is old enough and ONE successful read shows no
    X4.exe running.

    Never parses the record, so it works on an empty or corrupt one. A record whose file was
    written less than `FORGET_MIN_AGE_S` ago (`now()` minus its mtime) is refused without a
    process read: a live start in another shell may still be recording its game, and "no
    X4.exe yet" is exactly what such a start looks like. A failed read (the third state) or
    any running X4.exe refuses and leaves the file. Returns what was removed.
    """
    try:
        mtime = store.path.stat().st_mtime
    except FileNotFoundError:
        return f"nothing to forget: there is no run record at {store.path}"
    except OSError as exc:
        raise ForgetRefused(
            f"the age of the run record {store.path} could not be read ({exc}); it was left "
            f"in place.") from exc
    age = now() - mtime
    if age < FORGET_MIN_AGE_S:
        wait = math.ceil(FORGET_MIN_AGE_S - age)
        raise ForgetRefused(
            f"the run record {store.path} was written {max(age, 0):.0f} s ago: a run that is "
            f"still starting may be recording its game right now (a start can take up to "
            f"{FORGET_MIN_AGE_S:g} s: the {LAUNCH_CALL_TIMEOUT_S} s launch call, the "
            f"{START_TIMEOUT_S:g} s wait for the game and a 60 s margin). Wait {wait} s and "
            f"try again. The record was left in place.")
    try:
        running = read_processes(run, name=_GAME_EXE, sleep=sleep)
    except ProcessReadError as exc:
        raise ForgetRefused(
            f"the running processes could not be read, so whether {_GAME_EXE} is running is "
            f"unknown: {exc}. The run record {store.path} was left in place.") from exc
    if running:
        pids = ", ".join(str(p.pid) for p in running)
        raise ForgetRefused(
            f"{_GAME_EXE} is running (pid {pids}): close it (`x4run stop` closes the recorded "
            f"one) before forgetting. The run record {store.path} was left in place.")
    what = _size_text(store).replace(" that do not form a run record", "")
    try:
        store.clear()
    except OSError as exc:
        raise ForgetRefused(f"the run record {store.path} could not be removed: {exc}") from exc
    return (f"removed the run record {store.path} ({what}) after a read showed no "
            f"{_GAME_EXE} running")

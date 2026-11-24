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

Sections: process, exit, state (below). Later sections are added to this same file.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
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
    text = text.lstrip("﻿").strip()
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

#: Consecutive absent reads that end the watch. One miss is not an exit: a single read can
#: land between a process list refresh and the game's own relaunch, so three are required.
EXIT_MISSES = 3


class ExitWatch:
    """Has the RECORDED process gone? True only after `misses` consecutive absent reads.

    Feed it each `read_processes` result through `observe`. "Present" means an entry equal to
    `recorded` on pid, name, path AND started: a reused PID with a different start time is a
    different process, so it counts as a miss. A failed read (`None`) is the third state: it
    neither counts as a miss nor resets the count, so it can never end the watch and never
    erase the evidence gathered so far.
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

#: Seconds `update`/`clear` keep retrying a `PermissionError` (a just-written file is briefly
#: locked by scanners on this machine) before giving the real error up.
REPLACE_RETRY_S = 10.0

#: Seconds between two attempts of that retry.
REPLACE_PAUSE_S = 0.5


class UnapprovedLaunch(ValueError):
    """A launch was requested without a complete, valid approval record."""


class UnfinishedRun(RuntimeError):
    """A state file already exists: a game may still be running from an earlier run."""


class CorruptState(RuntimeError):
    """The state file exists but cannot be read as a run record. Never treated as 'no run'."""


class StateFolderError(RuntimeError):
    """The state folder cannot be located (the toolkit root is unknown)."""


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


def state_folder() -> Path:
    """`<X4_TOOLKIT>/.x4run`, the toolkit root resolved through `_paths`; never a guessed cwd path."""
    root = _paths.toolkit_root()
    if root is None:
        raise StateFolderError(
            "cannot locate the toolkit root for the x4run state folder: set X4_TOOLKIT "
            "(or run from an installed toolkit)")
    return Path(root) / ".x4run"


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
    # Encode FIRST: a failed encode must not leave a truncated file behind.
    return json.dumps(asdict(state), indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


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
                f"from an earlier run. Recover with `x4run stop` (never delete the file by hand).") from None
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
                pass
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
                pass
            raise

    def read(self) -> RunState | None:
        """The recorded run, or None when there is no file. Anything unreadable raises."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return _state_from(json.loads(raw.decode("utf-8")))
        except (ValueError, TypeError) as exc:  # UnicodeError and JSONDecodeError are ValueErrors
            raise CorruptState(f"run record {self.path} is unreadable ({exc}); it was left in "
                               f"place: inspect it, then recover with `x4run stop`") from exc

    def clear(self) -> None:
        def unlink() -> None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._retrying(unlink)

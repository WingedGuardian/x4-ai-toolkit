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

Sections: process (below). Later sections are added to this same file.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass

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

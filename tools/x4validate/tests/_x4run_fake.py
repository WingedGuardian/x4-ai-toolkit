r"""A scripted stand-in for the whole outside world of the x4run driver.

`FakeSystem` owns a PROCESS TABLE, a VIRTUAL CLOCK and a CALL LOG, and nothing here (or in a
test built on it) can start Steam or X4: `fake.run` answers by looking at the argv and never
executes anything.

  fake.run(argv, **kw)   drop-in for `subprocess.run`; logs the call, then answers
  fake.sleep(n)          advances the virtual clock by n (no real waiting)
  fake.clock()           reads the virtual clock (a drop-in for `time.monotonic`)
  fake.calls             every call seen: `Call(argv, creationflags, timeout, kw)` (kw: the other keywords, sorted pairs), in order
  fake.table             the process table (a list of `ProcessInfo`); mutate it freely

HOW A CALL IS CLASSIFIED. By argv, in this order: a first element ending `steam.exe` is a
Steam launch; a PowerShell script mentioning `CloseMainWindow` is a window-close request, one
mentioning `Stop-Process` (or a `taskkill` argv) is a terminate request; a script mentioning
`Get-CimInstance` is a process read. Anything else raises, so a test never silently talks to
a world the fake does not model.

SCRIPTING. Reads: `fail_reads(n)`, `malformed_reads(n)`, `timeout_reads(n)` queue n bad
answers (`good_reads(n)` queues n normal ones, to place a bad one later), consumed one per read before the table answers again. Launch, close and terminate
each call a settable hook (`on_launch`, `on_close`, `on_terminate`) with the argv and return
a `(returncode, stdout)` pair; the default records the call and changes nothing. A hook may
raise (`OSError`, `subprocess.TimeoutExpired`) to model a call that fails. `launch_spawns(*procs,
after=s)` scripts a launch after which new game processes appear `s` virtual seconds later. Time-driven
changes use `at(t, fn)`: `fn(fake)` runs once when the virtual clock reaches `t` (checked on
every `sleep` and every `run`), e.g. a game process that appears five seconds after launch.

LATER TASKS MAY EDIT THIS FILE (Steam launch, window-close and terminate answers are the
obvious extensions); keep the classification above and the `Call` shape stable.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from x4validate._x4run import ProcessInfo


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    creationflags: int
    timeout: float | None = None
    #: every other keyword the caller passed, as sorted (name, value) pairs
    kw: tuple = ()


def make_proc(pid: int = 1000, *, name: str = "X4.exe", path: str = "G:/games/X4/X4.exe",
              started: str = "2026-11-24T10:00:00.0000000Z") -> ProcessInfo:
    """A `ProcessInfo` with sensible defaults, so a test names only what it varies."""
    return ProcessInfo(pid=pid, name=name, path=path, started=started)


def _row(p: ProcessInfo) -> dict:
    return {"ProcessId": p.pid, "Name": p.name, "ExecutablePath": p.path, "Started": p.started}


class FakeSystem:
    def __init__(self, table: list[ProcessInfo] | None = None, *, start: float = 0.0):
        self.table: list[ProcessInfo] = list(table or [])
        self.calls: list[Call] = []
        self.now = float(start)
        self._read_script: list[str] = []
        self._events: list[tuple[float, object]] = []
        self.on_launch = self._default_hook
        self.on_close = self._default_hook
        self.on_terminate = self._default_hook

    # ---------------------------------------------------------------- virtual clock
    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)
        self._fire_events()

    def at(self, t: float, fn) -> None:
        """Run `fn(self)` once when the virtual clock first reaches `t`."""
        self._events.append((float(t), fn))
        self._fire_events()

    def _fire_events(self) -> None:
        due = [e for e in self._events if e[0] <= self.now]
        self._events = [e for e in self._events if e[0] > self.now]
        for _, fn in sorted(due, key=lambda e: e[0]):
            fn(self)

    # ---------------------------------------------------------------- scripting
    def fail_reads(self, n: int = 1) -> None:
        """The next n process reads exit non-zero."""
        self._read_script += ["fail"] * n

    def good_reads(self, n: int = 1) -> None:
        """The next n process reads answer normally (use before `fail_reads` to fail a LATER read)."""
        self._read_script += ["table"] * n

    def malformed_reads(self, n: int = 1) -> None:
        """The next n process reads exit 0 with output that is not valid JSON."""
        self._read_script += ["malformed"] * n

    def timeout_reads(self, n: int = 1) -> None:
        """The next n process reads time out."""
        self._read_script += ["timeout"] * n

    def launch_spawns(self, *procs: ProcessInfo, after: float = 5.0) -> None:
        """A Steam launch call makes `procs` appear in the table `after` virtual seconds later.

        Never calling this models Steam that accepts the call but never starts a game. A
        scripted `on_launch` set afterwards replaces it (wrap the old one to combine).
        """
        def hook(argv):
            self.at(self.now + after, lambda f: f.table.extend(procs))
            return 0, ""
        self.on_launch = hook

    @staticmethod
    def _default_hook(argv):
        return 0, ""

    # ---------------------------------------------------------------- the runner
    def run(self, argv, **kw):
        argv = tuple(str(a) for a in argv)
        extra = tuple(sorted((k, v) for k, v in kw.items() if k not in ("creationflags", "timeout")))
        self.calls.append(Call(argv, int(kw.get("creationflags", 0)), kw.get("timeout"), extra))
        self._fire_events()
        kind = self._classify(argv)
        if kind == "read":
            return self._answer_read(argv, kw)
        hook = {"launch": self.on_launch, "close": self.on_close,
                "terminate": self.on_terminate}[kind]
        rc, out = hook(argv)
        return subprocess.CompletedProcess(argv, rc, out, "")

    @staticmethod
    def _classify(argv: tuple[str, ...]) -> str:
        exe = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if argv else ""
        text = " ".join(argv)
        if exe == "steam.exe":
            return "launch"
        if exe == "taskkill" or exe == "taskkill.exe" or "Stop-Process" in text:
            return "terminate"
        if "CloseMainWindow" in text:
            return "close"
        if "Get-CimInstance" in text:
            return "read"
        raise AssertionError(f"FakeSystem does not model this call: {argv!r}")

    def _answer_read(self, argv, kw):
        step = self._read_script.pop(0) if self._read_script else "table"
        if step == "timeout":
            raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 0)
        if step == "fail":
            return subprocess.CompletedProcess(argv, 1, "", "scripted read failure")
        if step == "malformed":
            return subprocess.CompletedProcess(argv, 0, '[{"ProcessId": 12', "")
        # A read is filtered by the image name in its script, like the real query.
        m = re.search(r"Name = '([^']+)'", " ".join(argv))
        want = m.group(1).lower() if m else None
        rows = [_row(p) for p in self.table if want is None or p.name.lower() == want]
        # PowerShell's real shapes: nothing -> no output, one object -> an object, many -> list.
        if not rows:
            out = ""
        elif len(rows) == 1:
            out = json.dumps(rows[0])
        else:
            out = json.dumps(rows)
        return subprocess.CompletedProcess(argv, 0, out, "")

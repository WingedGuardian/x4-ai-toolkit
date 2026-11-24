r"""A scripted stand-in for the whole outside world of the x4run driver.

`FakeSystem` owns a PROCESS TABLE, a VIRTUAL CLOCK and a CALL LOG, and nothing here (or in a
test built on it) can start Steam or X4: `fake.run` answers by looking at the argv and never
executes anything.

  fake.run(argv, **kw)   drop-in for `subprocess.run`; logs the call, then answers
  fake.sleep(n)          advances the virtual clock by n (no real waiting)
  fake.clock()           reads the virtual clock (a drop-in for `time.monotonic`)
  fake.calls             every call seen: `Call(argv, creationflags, timeout, kw)` (kw: the other keywords, sorted pairs), in order
  fake.table             the process table (a list of `ProcessInfo`); mutate it freely

HOW A CALL IS CLASSIFIED. By argv, independent of any order: a first element ending
`steam.exe` is a Steam launch; a PowerShell script mentioning `CloseMainWindow` is a
window-close request, one mentioning `Stop-Process` (or a `taskkill` argv) is a terminate
request; a script mentioning `Get-CimInstance` is a process read. A call that is two of these
at once (a script with both `CloseMainWindow` and `Stop-Process`), or none, raises, so a test
never silently talks to a world the fake does not model. THE CALLER'S COMMAND is recognised
first, by EXACT argv equality with one registered through `script_command`; it is logged like
any other call (`fake.command_calls`) and answered as scripted.

SCRIPTING. Reads: `fail_reads(n)`, `malformed_reads(n)`, `timeout_reads(n)` queue n bad
answers (`good_reads(n)` queues n normal ones, to place a bad one later), consumed one per read before the table answers again. Launch, close and terminate
each call a settable hook (`on_launch`, `on_close`, `on_terminate`) with the argv and return
a `(returncode, stdout)` pair; the default records the call and changes nothing (so a
window-close request is IGNORED FOREVER unless scripted otherwise). A hook may
raise (`OSError`, `subprocess.TimeoutExpired`) to model a call that fails. `launch_spawns(*procs,
after=s)` scripts a launch after which new game processes appear `s` virtual seconds later.
`close_exits_after(s)` makes a window-close request remove that process `s` virtual seconds
later; `terminate_removes()` makes a terminate request remove it at once. Both take the pid
from the request's LAST argv element, which must be a plain integer (the module under test
passes the pid as its own argument, never inside the script text), and remove the process
with that pid from the table. `fake.close_calls` / `fake.terminate_calls` list those calls.
`script_command(argv, rc=0, timeout=False, exc=None, action=None)` registers the caller's
command: it answers `rc`, or raises `TimeoutExpired` (`timeout=True`) or `exc`; `action(fake)`, if
given, runs first (e.g. to let the game exit while the command runs). `on_read`, if set, is `fn(fake, n)` run before the n-th process read (0-based) is answered,
so a table can change between two reads. Time-driven
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
        self.on_read = None
        self._reads = 0
        self._commands: dict[tuple[str, ...], dict] = {}

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

    @staticmethod
    def _target_pid(argv) -> int:
        last = argv[-1] if argv else ""
        assert last.isdigit(), f"a close/terminate request must end with the pid as its own argument: {argv!r}"
        return int(last)

    def _remove_pid(self, pid: int) -> None:
        self.table = [p for p in self.table if p.pid != pid]

    def close_exits_after(self, seconds: float) -> None:
        """A window-close request makes the targeted process exit `seconds` virtual seconds later."""
        def hook(argv):
            pid = self._target_pid(argv)
            self.at(self.now + seconds, lambda f: f._remove_pid(pid))
            return 0, ""
        self.on_close = hook

    def terminate_removes(self) -> None:
        """A terminate request removes the targeted process at once."""
        def hook(argv):
            self._remove_pid(self._target_pid(argv))
            return 0, ""
        self.on_terminate = hook

    def script_command(self, argv, *, rc: int = 0, timeout: bool = False, exc=None,
                       action=None) -> None:
        """Register the caller's command (matched by exact argv) and how it ends."""
        self._commands[tuple(str(a) for a in argv)] = dict(rc=rc, timeout=timeout, exc=exc,
                                                          action=action)

    @property
    def command_calls(self) -> list[Call]:
        return [c for c in self.calls if c.argv in self._commands]

    @property
    def close_calls(self) -> list[Call]:
        return [c for c in self.calls if self._classify(c.argv) == "close"]

    @property
    def terminate_calls(self) -> list[Call]:
        return [c for c in self.calls if self._classify(c.argv) == "terminate"]

    # ---------------------------------------------------------------- the runner
    def run(self, argv, **kw):
        argv = tuple(str(a) for a in argv)
        extra = tuple(sorted((k, v) for k, v in kw.items() if k not in ("creationflags", "timeout")))
        self.calls.append(Call(argv, int(kw.get("creationflags", 0)), kw.get("timeout"), extra))
        self._fire_events()
        if argv in self._commands:
            spec = self._commands[argv]
            if spec["action"] is not None:
                spec["action"](self)
            if spec["timeout"]:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 0)
            if spec["exc"] is not None:
                raise spec["exc"]
            return subprocess.CompletedProcess(argv, spec["rc"], "", "")
        kind = self._classify(argv)
        if kind == "read":
            return self._answer_read(argv, kw)
        hook = {"launch": self.on_launch, "close": self.on_close,
                "terminate": self.on_terminate}[kind]
        rc, out = hook(argv)
        return subprocess.CompletedProcess(argv, rc, out, "")

    def _classify(self, argv: tuple[str, ...]) -> str:
        if argv in self._commands:
            return "command"
        exe = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if argv else ""
        text = " ".join(argv)
        kinds = set()
        if exe == "steam.exe":
            kinds.add("launch")
        if exe in ("taskkill", "taskkill.exe") or "Stop-Process" in text:
            kinds.add("terminate")
        if "CloseMainWindow" in text:
            kinds.add("close")
        if "Get-CimInstance" in text:
            kinds.add("read")
        if len(kinds) != 1:
            raise AssertionError(f"FakeSystem cannot classify this call as exactly one kind "
                                 f"({sorted(kinds) or 'none'}): {argv!r}")
        return kinds.pop()

    def _answer_read(self, argv, kw):
        n, self._reads = self._reads, self._reads + 1
        if self.on_read is not None:
            self.on_read(self, n)
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

r"""Locate a REAL bash on Windows, never the WSL stub -- by asking the GUARDS' resolver.

`shutil.which("bash")` on Windows returns `C:\Windows\System32\bash.exe` -- the WSL
launcher -- whenever Git Bash is not on PATH, which is the normal state in PowerShell
and in CI. That stub either fails outright or runs a Linux bash in a filesystem where
`C:/Users/...` does not exist, so every path-shaped assertion silently changes meaning.

MEASURED 2026-09-01 from PowerShell on this machine:
    shutil.which("bash")     -> C:\Windows\system32\bash.EXE
    shutil.which("bash.exe") -> C:\Windows\system32\bash.exe
The second is the important one: `scripts/fuzz-guard.py` carried
`which("bash.exe") or which("bash")` with a comment saying it avoided the WSL stub.
It did not -- the stub IS named bash.exe. A defence that names the right threat and
does not stop it is worse than none, because it stops anyone looking again.

ONE RESOLVER (B1, install red-team 2026-10-04). This module used to carry its own list of
Git for Windows locations while the guards' `x4guard.resolve_bash()` asked only PATH -- so on
a stock machine the tools found Git Bash and the guards (and the OpenCode renderer, and
x4doctor's rows) did not. The algorithm now lives in ONE place, `x4guard.resolve_bash()`,
because a deployed guard copy has no scripts/ to import from; this module loads the toolkit's
own guard copy and asks it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOLKIT = Path(__file__).resolve().parent.parent

#: Where this toolkit's x4guard.py lives: the agent/ source in a checkout, else whichever
#: guard copy an install carries (all are byte-identical generated copies).
_GUARD_COPIES = (
    _TOOLKIT / "agent" / "guards" / "claude-hooks" / "x4guard.py",
    _TOOLKIT / ".claude" / "hooks" / "x4guard.py",
    _TOOLKIT / ".codex" / "hooks" / "x4guard.py",
    _TOOLKIT / ".opencode" / "hooks" / "x4guard.py",
    _TOOLKIT / "tools" / "x4_harness" / "x4guard.py",
)

_MOD = None


def _resolver():
    """The toolkit's x4guard module (loaded once), or None when no guard copy exists."""
    global _MOD
    if _MOD is None:
        for p in _GUARD_COPIES:
            if p.is_file():
                spec = importlib.util.spec_from_file_location("x4guard_for_gitbash", p)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                _MOD = m
                break
    return _MOD


def _is_stub(p) -> bool:
    m = _resolver()
    return bool(m and m.is_stub_bash(p))


def find_bash() -> str | None:
    """A usable bash, or None. Never a WSL/Store stub on Windows."""
    m = _resolver()
    if m is None:
        return None
    return m.resolve_bash()[0]


def why_not() -> str:
    """Why find_bash() returned None (the exact `setx X4_BASH` line on Windows)."""
    m = _resolver()
    if m is None:
        return "no x4guard.py in this toolkit to resolve bash with (re-run the installer)"
    return m.resolve_bash()[1] or ""

"""Is a toolkit root this toolkit's own SOURCE CHECKOUT, or an INSTALL? One answer, shared.

Used by `x4lock.py` (a source checkout is never locked: its .claude/ etc. are generated) and
`x4doctor.py` (parity runs the generator only in a source checkout), and mirrored by the test
suite's `tests/_layout.installed_layout`, which loads this file.

A CHECKOUT is a `.git` AT the root (a directory, or the FILE a worktree uses) whose HEAD tracks
`agent/`. Anything else is an install. `agent/` alone is NOT a checkout: the release zip carries
it, and the README's default install extracts that zip and installs IN PLACE (BLIND-SPOTS F269,
MEASURED 2026-10-11). Why not just "has a .git": the `in-game` method installs INTO the game
root, which can be a git repo of its own -- one whose HEAD never tracks agent/. And the `.git`
must be AT the root: asked of git alone, a tree nested inside a toolkit checkout would borrow
the parent's HEAD. Git unable to answer (no git on PATH, no commit) proves nothing, so: install.
"""
from __future__ import annotations

import functools
import subprocess
from pathlib import Path


def is_source_checkout(root) -> bool:
    """True only for this toolkit's own git checkout (see the module docstring)."""
    root = Path(root)
    if not (root / ".git").exists():
        return False
    return _head_tracks_agent(str(root))


@functools.lru_cache(maxsize=None)
def _head_tracks_agent(root: str) -> bool:
    """Does the git repo AT `root` have `agent/` in its HEAD tree? False when git cannot answer."""
    try:
        r = subprocess.run(["git", "-C", root, "cat-file", "-e", "HEAD:agent"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0

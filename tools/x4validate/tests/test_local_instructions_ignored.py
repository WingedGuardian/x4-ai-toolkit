"""`CLAUDE.local.md` is a user's private, per-machine instruction file (machine paths, personal
rules); it must never reach a commit. Asked of git with `check-ignore --no-index`, never
re-implemented: `.gitignore` alone decides, and a negation that wins prints nothing (rc 1)."""
from __future__ import annotations

import subprocess

from _layout import ROOT, require_repo


def _ignored(path: str) -> bool:
    r = subprocess.run(("git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode in (0, 1), f"git check-ignore failed: {r.stderr.strip()}"
    return r.returncode == 0


def test_claude_local_md_is_ignored():
    require_repo(".gitignore", ".git", why="a checkout's .gitignore decides")
    assert _ignored("CLAUDE.local.md")


def test_the_check_can_say_no():
    """Control: a tracked name is NOT ignored, so a green above is not 'everything is ignored'."""
    require_repo(".gitignore", ".git", why="a checkout's .gitignore decides")
    assert not _ignored("CLAUDE.md")

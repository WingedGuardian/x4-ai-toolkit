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
the parent's HEAD.

`checkout_status` answers in THREE states, because "git failed" is not "git said no":
  * "checkout" -- git read the repo at the root, and its HEAD tracks agent/;
  * "install"  -- no `.git` at the root, or git read the repo and its HEAD has no agent/;
  * "unknown"  -- a `.git` is there but git could not answer (no git on PATH, no commit yet, a
                  corrupt repo); the reason says which.
`is_source_checkout` folds "unknown" into False (nothing proves the repo is the toolkit's).
What each consumer does with "unknown" is its own decision, stated where it reads it.

The question is asked of the repo AT the root and nothing else (v4.0.0 delta review, M-a,
MEASURED): git runs with every repo-LOCAL environment variable removed (`git rev-parse
--local-env-vars`: an exported GIT_DIR made `git -C <root>` answer for some OTHER repo, and a
real checkout read as an install) and with `safe.directory=<root>` (a checkout owned by another
account was refused as "dubious ownership" and read the same way). The command only reads the
HEAD tree, so trusting the root for this one call runs none of its hooks.
"""
from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

#: `git rev-parse --local-env-vars` (git 2.48): the variables that point git at a repo other
#: than the one `-C` names, or rewrite how it reads one. Removed from the environment of every
#: call here, whatever the caller exported.
GIT_LOCAL_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def clean_git_env(env=None) -> dict:
    """A copy of `env` (default: os.environ) with every GIT_LOCAL_ENV variable removed."""
    src = os.environ if env is None else env
    drop = {k.upper() for k in GIT_LOCAL_ENV}
    return {k: v for k, v in src.items() if k.upper() not in drop}


def git_argv(root, *args: str) -> list[str]:
    """`git -C <root> -c safe.directory=<root> <args>`: the repo AT root, whoever owns it."""
    r = Path(root).resolve()
    return ["git", "-c", "safe.directory=" + r.as_posix(), "-C", str(r), *args]


def is_source_checkout(root) -> bool:
    """True only for this toolkit's own git checkout (see the module docstring). "unknown"
    -- git could not answer -- is False: nothing proves the repo is the toolkit's."""
    return checkout_status(root)[0] == "checkout"


def checkout_status(root) -> tuple[str, str]:
    """("checkout" | "install" | "unknown", reason) -- see the module docstring."""
    root = Path(root)
    if not (root / ".git").exists():
        return "install", "no .git at the root"
    return _git_answer(str(root))


@functools.lru_cache(maxsize=None)
def _git_answer(root: str) -> tuple[str, str]:
    """Ask the repo AT `root` whether its HEAD tree has `agent/` (`git ls-tree HEAD agent`:
    rc 0 with a row = yes, rc 0 empty = no, anything else = git could not answer)."""
    try:
        r = subprocess.run(git_argv(root, "ls-tree", "HEAD", "--", "agent"),
                           capture_output=True, text=True, timeout=60, env=clean_git_env())
    except (OSError, subprocess.SubprocessError) as e:
        return "unknown", "git could not be run (%s: %s)" % (type(e).__name__, e)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return "unknown", "git could not read the repo at %s (rc %s: %s)" % (
            root, r.returncode, err[0] if err else "no message")
    if r.stdout.strip():
        return "checkout", "the repo at the root tracks agent/ in HEAD"
    return "install", "the repo at the root is not this toolkit's (its HEAD has no agent/)"


#: kept under its old name: callers clear its cache when they stub git out
_head_tracks_agent = _git_answer

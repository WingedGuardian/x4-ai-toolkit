"""Which LAYOUT is this suite running in: the git checkout, or an INSTALLED toolkit?

R2-B1 (second install red-team, 2026-10-04): SETUP_PROMPT.txt tells the user the installed
toolkit's suite "should pass", and in an installed tree `uv run pytest -q` died in collection
(FileNotFoundError on agent/rules/codex-rules.yaml), because an install is runtime-only by
design: the `agent/` source the generator reads, the root `docs/`, and the git history are not
copied (README, "An installed toolkit is runtime-only").

A test that needs repo-only content calls `require_repo(...)`. It SKIPS -- counted, with a
reason naming what is absent -- only when BOTH hold:
  * the tree is an installed layout: the root is not this toolkit's own git checkout (see
    `installed_layout`; an in-place install of the release zip HAS `agent/`), and
  * the content the test needs is actually absent.
In a checkout a missing file is NOT skipped: the test then fails, as it should, because a
checkout that lost `agent/rules/codex-rules.yaml` is broken, not "installed".
"""
from __future__ import annotations

import functools
import subprocess
from pathlib import Path

import pytest

#: The toolkit root (tests/ -> x4validate/ -> tools/ -> root).
ROOT = Path(__file__).resolve().parents[3]

#: The reason prefix every such skip carries, so a reader of `-rs` can count them.
REASON = "REPO-ONLY (installed toolkit, not a checkout)"


def installed_layout(root: Path = ROOT) -> bool:
    """True for an installed toolkit: the root is not this toolkit's own git checkout -- a `.git`
    (a directory, or the FILE a worktree uses) whose HEAD tracks `agent/`.

    FX-H2 / I-1 (F269, MEASURED 2026-10-10): `agent/` alone is NOT a checkout. The README's
    default install extracts the release zip (`git archive`: agent/ present, no .git) and runs
    the installer IN PLACE; keyed on agent/, that read as a checkout, and the suite SETUP_PROMPT
    has the user run failed or errored in every "configured but not present yet" case.

    v4.0.0 delta review: keyed on `agent/` alone, a CHECKOUT that lost `agent/` read as
    "installed" and every repo-only test SKIPPED instead of failing. So a root with `.git`
    (a directory, or the FILE a worktree uses) whose HEAD tracks `agent/` is a broken
    checkout, not an install.

    Why not simply "no `.git`": the `in-game` install method copies the toolkit INTO the game
    root (install.sh: `TOOLKIT="$GAME"`), and a game root can itself be a git repo -- this
    machine's is. Neither installer copies `.git` (the copy sets name `.gitignore` and
    `.gitattributes` only), so a `.git` at an install root is always SOMEONE ELSE's repo;
    asking it whether its HEAD tracks `agent/` tells the two apart."""
    root = Path(root)
    if not (root / ".git").exists():
        return True
    return not _head_tracks_agent(str(root))


@functools.lru_cache(maxsize=None)
def _head_tracks_agent(root: str) -> bool:
    """Does the git repo AT `root` have `agent/` in its HEAD tree? False when git cannot answer
    (no git on PATH, no commit yet): then nothing proves this is the toolkit's checkout."""
    try:
        r = subprocess.run(["git", "-C", root, "cat-file", "-e", "HEAD:agent"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def own_checkout(root: Path = ROOT) -> bool:
    """Is `root` the top of its own git work tree? (`rev-parse --show-toplevel == root`, the
    precedent in test_cli_reference.) False in a release-zip extract, an install, or a tree
    nested inside SOMEONE ELSE's repo, where git would answer for the wrong tree."""
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == Path(root).resolve()


#: Directories never searched for shipped rule files (bulk or generated, never shipped rules).
_RULES_PRUNE = {".git", ".venv", ".test-sandbox", "__pycache__", "node_modules", "reference"}


def shipped_rules_repo(tmp: Path, root: Path = ROOT) -> Path:
    """A scratch git repo holding ONLY this tree's `.gitignore` / `.gitattributes` files, at
    their relative paths, so a test can ask git what the SHIPPED RULES say -- `check-ignore
    --no-index`, `check-attr` -- without this tree's own Git metadata (FX-H2 / I-1: a release
    zip has none). The copy is refused when no root `.gitignore` is found: an empty rule set
    would answer "not ignored" for everything."""
    import os
    import shutil
    repo = Path(tmp) / "shipped-rules"
    repo.mkdir(parents=True)
    root = Path(root)
    copied = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _RULES_PRUNE]
        for f in filenames:
            if f in (".gitignore", ".gitattributes"):
                src = Path(dirpath) / f
                dst = repo / src.relative_to(root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                copied.append(dst)
    if not (repo / ".gitignore").is_file():
        raise AssertionError("no root .gitignore under %s: the shipped rules cannot be asked" % root)
    r = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("git init failed: %s" % r.stderr)
    return repo


def reference_unpacked() -> bool:
    """A reference tree is configured AND unpacked (it holds libraries/wares.xml).

    R2-B1: three markers asked only `_paths.reference() is None`. An install configures
    X4_REFERENCE before anything is unpacked -- SETUP_PROMPT has the suite run BEFORE the
    unpack step -- so on every fresh install those tests ran against a folder that did not
    exist yet, and failed. Configured is not unpacked."""
    from x4validate import _paths
    p = _paths.reference()
    return p is not None and (Path(p) / "libraries" / "wares.xml").is_file()


#: The reason every needs-a-reference skip carries.
NEEDS_REFERENCE = ("needs an UNPACKED reference tree (none configured, or not unpacked yet: "
                   "bash bin/unpack-reference.sh)")


def reference_skip_reason(root: Path = ROOT) -> str | None:
    """Why a needs-a-reference test must SKIP here, or None when it must RUN.

    v4.0.0 delta review: the markers skipped on `not reference_unpacked()`, so a CHECKOUT whose
    configured reference had lost `libraries/wares.xml` skipped too -- a broken dev reference
    read as "not unpacked yet". Configured-but-not-unpacked is the fresh-INSTALL state
    (SETUP_PROMPT runs the suite before the unpack), so only an installed layout skips on it.
      * nothing configured           -> skip, any layout (CI, a cold clone); the derived
                                        <X4_TOOLKIT>/reference default with nothing unpacked
                                        there counts as nothing configured
      * configured and unpacked      -> run
      * configured, NOT unpacked     -> skip in an installed layout; RUN (and fail) in a checkout
    """
    from x4validate import _paths
    p = _paths.reference()
    if p is None:
        return NEEDS_REFERENCE + ": no reference tree is configured"
    if (Path(p) / "libraries" / "wares.xml").is_file():
        return None
    if not _paths.value("X4_REFERENCE"):
        # Only the `<X4_TOOLKIT>/reference` DEFAULT answered (`_paths.reference` derives it
        # when nothing names a reference), and nothing is unpacked there: that is a machine
        # with no reference, not a configured reference that broke.
        return NEEDS_REFERENCE + (": no reference tree is configured (only the "
                                  "<X4_TOOLKIT>/reference default, and it is not unpacked)")
    if installed_layout(root):
        return NEEDS_REFERENCE + ": configured but not unpacked yet (installed toolkit)"
    return None


def needs_reference_mark():
    """The `skipif` marker for a test that needs an unpacked reference (see
    `reference_skip_reason`). Evaluated once, at collection."""
    why = reference_skip_reason()
    return pytest.mark.skipif(why is not None, reason=why or "")


def require_repo(*rel: str, why: str = "", module_level: bool = False, root: Path = ROOT) -> None:
    """Skip when this is an installed layout and any of `rel` (paths relative to the toolkit
    root, e.g. "agent/rules/codex-rules.yaml" or ".git") is absent; otherwise return.

    `module_level=True` for a call at import time (the whole module is counted as skipped)."""
    if not installed_layout(root):
        return
    missing = [r for r in rel if not (Path(root) / r).exists()]
    if not missing:
        return
    reason = "%s: needs %s, which an install does not ship%s" % (
        REASON, ", ".join(missing), (" -- " + why) if why else "")
    pytest.skip(reason, allow_module_level=module_level)

"""Where an agent may write its notes, and what it is told when a file is LOCKED (lane N).

THE INCIDENT (2026-10-03, a live Codex run in a game root, MEASURED by the orchestrator):
the agent could not save its investigation notes anywhere.

  * `<game>/X4-NOTES.md` -- the file the instructions TELL it to use -- was hard-denied as a
    game-installation file by protect-files.sh.
  * `<game>/KNOWLEDGEBASE.md` was allowed by the guard, but the WRITE failed: x4lock had set
    the read-only attribute, and nothing told the agent so, or how to unlock it.

So, each through all three front doors -- the Claude hook, `x4guard check` (any agent) and
the Codex adapter:

  N1  exactly `<project root>/X4-NOTES.md` and `<project root>/X4-NOTES.pre-4.0.md` are
      allowed; the same NAME anywhere else in the game tree stays denied.
  N2  a write to an x4lock-LOCKED file is ADVISED (never asked or denied) with the exact
      unlock -> edit -> relock commands.
  N3  the game-install deny routes the agent: notes, game facts, mod files.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from codex_testlib import copy_shared_runtime, make_sandbox, native, run_adapter

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
from _layout import common_hook_dir
COMMON_HOOKS = common_hook_dir(REPO)
CLAUDE_HOOK = COMMON_HOOKS / "protect-files.sh"
X4GUARD = COMMON_HOOKS / "x4guard.py"
X4LOCK = REPO / "scripts" / "x4lock.py"
sys.path.insert(0, str(REPO / "scripts"))
from gitbash import find_bash
BASH = find_bash()

pytestmark = pytest.mark.skipif(not BASH, reason="no bash -- the guards cannot run, NOTHING here was checked")


@pytest.fixture
def sandbox(tmp_path):
    tmp, tk, env = make_sandbox(tmp_path)
    env["X4_GUARD_TIMEOUT_S"] = "120"     # verdicts, not speed (see test_x4guard_check.py)
    return tmp, tk, tmp / "X4 Foundations", env


def hook(env, path) -> tuple[str, str]:
    """(decision, text) from the Claude hook itself: allow / advise / ask / deny."""
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(path), "content": ""}})
    r = subprocess.run([BASH, str(CLAUDE_HOOK)], input=payload.encode("utf-8"), capture_output=True,
                       env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode("utf-8").strip()
    if not out:
        return "allow", ""
    hso = json.loads(out)["hookSpecificOutput"]
    if "permissionDecision" in hso:
        return hso["permissionDecision"], hso["permissionDecisionReason"]
    return "advise", hso["additionalContext"]


def check(env, path) -> tuple[str, str]:
    """(decision, text) from `x4guard check --kind write` -- the front door the Codex adapter uses."""
    r = subprocess.run([sys.executable, str(X4GUARD), "check", "--kind", "write", "--path", str(path)],
                       capture_output=True, env=env, timeout=180)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert not v["inert"], v
    return v["decision"], (v.get("reason") or v.get("context") or "")


def both(env, path) -> str:
    """The Claude hook and x4guard must agree; return the shared decision."""
    a, b = hook(env, path)[0], check(env, path)[0]
    assert a == b, f"Claude hook says {a}, x4guard says {b} for {path}"
    return a


def _lock(p: Path) -> None:
    os.chmod(p, os.stat(p).st_mode & ~stat.S_IWRITE)


def _unlock(p: Path) -> None:
    os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE)


# ------------------------------------------------------------------------- N1 -------- #

@pytest.mark.parametrize("name", ["X4-NOTES.md", "X4-NOTES.pre-4.0.md"])
def test_N1_notes_at_the_game_root_are_allowed(sandbox, name):
    _, _, game, env = sandbox
    assert both(env, game / name) == "allow"


@pytest.mark.parametrize("rel", [
    "sub/X4-NOTES.md",                 # the NAME clause alone is not enough: same name, deeper
    "X4-NOTES.md.bak",                 # the ROOT clause alone is not enough: right place, other name
    "X4-NOTES.pre-4.0.md.bak",
    "libraries/X4-NOTES.pre-4.0.md",
    "X4-NOTES.md/../sub/X4-NOTES.md",  # raw text STARTS like the allowed file; resolves deeper
])
def test_N1_TWIN_the_same_name_elsewhere_in_the_game_tree_is_denied(sandbox, rel):
    _, _, game, env = sandbox
    assert both(env, game / rel) == "deny"


def test_N1_TWIN_a_deployed_mod_folder_is_not_the_project_root(sandbox):
    """extensions/ keeps its own rule (a deny here: the mods root is separate)."""
    _, _, game, env = sandbox
    (game / "extensions" / "x").mkdir()
    assert both(env, game / "extensions" / "x" / "X4-NOTES.md") == "deny"


def test_N1_a_dot_dot_spelling_that_RESOLVES_to_the_root_is_the_root(sandbox):
    """The decision is made on the resolved path, never the raw text (AUDIT-2026-09-24 HK-3)."""
    _, _, game, env = sandbox
    assert both(env, game / "sub" / ".." / "X4-NOTES.md") == "allow"


def test_N1_the_toolkit_root_anchors_when_the_game_root_is_not_configured(sandbox):
    """In-game layout with X4_GAME unset: the toolkit IS the game folder and only the
    `X4 Foundations/` name backstop protects it. The toolkit root is then the project root."""
    tmp, _, game, env = sandbox
    copy_shared_runtime(game)
    env = dict(env, X4_TOOLKIT=str(game), X4_REFERENCE=str(game / "reference"))
    env.pop("X4_GAME")
    assert both(env, game / "X4-NOTES.md") == "allow"
    assert both(env, game / "sub" / "X4-NOTES.md") == "deny"      # twin: the backstop still bites


def test_N1_codex_apply_patch_add_file_of_the_notes(sandbox):
    tmp, _, game, env = sandbox
    for rel, want in (("X4-NOTES.md", "allow"), ("sub/X4-NOTES.md", "deny")):
        patch = f"*** Begin Patch\n*** Add File: {rel}\n+notes\n*** End Patch"
        assert run_adapter(env, native("apply_patch_add", game, command=patch))[0] == want, rel


# ------------------------------------------------------------------------- N3 -------- #

def _routes(reason: str) -> None:
    assert reason.startswith("BLOCKED"), reason
    for needle in ("X4-NOTES.md", "KNOWLEDGEBASE.md", "x4lock.py", "unlock", "mod folder"):
        assert needle in reason, (needle, reason)


def test_N3_the_game_install_deny_routes_the_agent(sandbox):
    _, _, game, env = sandbox
    for get in (hook, check):
        d, reason = get(env, game / "libraries" / "wares.xml")
        assert d == "deny"
        _routes(reason)
        assert str(game / "X4-NOTES.md").replace("\\", "/") in reason.replace("\\", "/")


def test_N3_preferences_route_to_the_TOOLKIT_root_where_they_are_read(sandbox):
    """v4.0 release review (IN-ARC 264828c): the deny routed preferences to
    `$X4_GAME/KNOWLEDGEBASE.local.md`, but session-canary.sh, preferences.py, the OpenCode plugin
    and upgrade.py read the TOOLKIT root -- in the separate layout an agent wrote a file nothing
    reads. X4-NOTES.md is listed as the legacy file, after the preferences route."""
    _, tk, game, env = sandbox
    for get in (hook, check):
        d, reason = get(env, game / "libraries" / "wares.xml")
        assert d == "deny"
        r = reason.replace("\\", "/")
        want = str(tk / "KNOWLEDGEBASE.local.md").replace("\\", "/")
        assert want in r, (want, reason)
        assert str(game / "KNOWLEDGEBASE.local.md").replace("\\", "/") not in r, reason
        assert r.index("KNOWLEDGEBASE.local.md") < r.index("X4-NOTES.md"), reason


def test_N3_local_knowledgebase_is_writable_only_where_it_is_read(sandbox):
    _, tk, game, env = sandbox
    assert both(env, tk / "KNOWLEDGEBASE.local.md") == "allow"
    assert both(env, game / "KNOWLEDGEBASE.local.md") == "deny"          # separate layout: unread there
    # TWIN: the in-game layout (the toolkit IS the game folder) keeps it writable at that root.
    copy_shared_runtime(game)
    env = dict(env, X4_TOOLKIT=str(game), X4_REFERENCE=str(game / "reference"))
    env.pop("X4_GAME")
    assert both(env, game / "KNOWLEDGEBASE.local.md") == "allow"
    assert both(env, game / "sub" / "KNOWLEDGEBASE.local.md") == "deny"


def test_N3_the_name_backstop_deny_routes_too(sandbox):
    """The second deny site (X4_GAME unset, `X4 Foundations/` in the path)."""
    tmp, _, _, env = sandbox
    env = dict(env)
    env.pop("X4_GAME")
    other = tmp / "elsewhere" / "X4 Foundations" / "libraries" / "wares.xml"
    d, reason = hook(env, other)
    assert d == "deny"
    _routes(reason)


# ------------------------------------------------------------------------- N2 -------- #

def _x4lock(env, *args):
    return subprocess.run([sys.executable, str(X4LOCK), *args], capture_output=True, env=env, timeout=120)


def test_N2_x4lock_protected_answers_from_its_own_manifest(sandbox):
    _, _, game, env = sandbox
    kb, other = game / "KNOWLEDGEBASE.md", game / "notes.txt"
    kb.write_text("kb\n", encoding="utf-8")
    other.write_text("x\n", encoding="utf-8")
    assert _x4lock(env, "protected", str(kb)).returncode == 0
    assert _x4lock(env, "protected", str(other)).returncode == 1           # twin: not in the manifest
    assert _x4lock(env, "protected", str(game / "absent.md")).returncode == 1


def test_N2_a_LOCKED_manifest_file_is_advised_with_the_exact_commands(sandbox):
    _, _, game, env = sandbox
    kb = game / "KNOWLEDGEBASE.md"
    kb.write_text("kb\n", encoding="utf-8")
    _lock(kb)
    try:
        for get in (hook, check):
            d, text = get(env, kb)
            assert d == "advise", (get.__name__, d, text)
            assert "x4lock.py" in text and f'unlock "{kb}"' in text and " lock" in text, text
            assert text.index("unlock") < text.index(" lock"), "unlock -> edit -> relock, in that order"
    finally:
        _unlock(kb)


def test_N2_TWIN_the_same_file_UNLOCKED_is_a_plain_allow(sandbox):
    _, _, game, env = sandbox
    kb = game / "KNOWLEDGEBASE.md"
    kb.write_text("kb\n", encoding="utf-8")
    assert hook(env, kb) == ("allow", "")
    assert check(env, kb)[0] == "allow"


def test_N2_TWIN_a_read_only_file_x4lock_does_NOT_manage_is_not_called_locked(sandbox):
    """Read-only alone is not x4lock's lock: X4-NOTES.md is never in the manifest."""
    _, _, game, env = sandbox
    notes = game / "X4-NOTES.md"
    notes.write_text("n\n", encoding="utf-8")
    _lock(notes)
    try:
        assert hook(env, notes) == ("allow", "")
    finally:
        _unlock(notes)


def test_N2_a_locked_file_inside_a_DENIED_area_stays_denied(sandbox):
    """The advisory never softens a verdict: a deny still wins."""
    _, tk, _, env = sandbox
    ref = tk / "reference" / "libraries" / "wares.xml"
    _lock(ref)
    try:
        env = dict(env, X4_PROTECTED=str(ref))
        assert hook(env, ref)[0] == "deny"
    finally:
        _unlock(ref)


def test_N2_the_advisory_reaches_codex_as_context(sandbox):
    _, _, game, env = sandbox
    kb = game / "KNOWLEDGEBASE.md"
    kb.write_text("kb\n", encoding="utf-8")
    _lock(kb)
    try:
        patch = "*** Begin Patch\n*** Update File: KNOWLEDGEBASE.md\n@@\n-kb\n+kb2\n*** End Patch"
        d, ctx = run_adapter(env, native("apply_patch_update", game, command=patch))
        assert d == "advise" and "x4lock.py" in ctx and "unlock" in ctx, (d, ctx)
    finally:
        _unlock(kb)

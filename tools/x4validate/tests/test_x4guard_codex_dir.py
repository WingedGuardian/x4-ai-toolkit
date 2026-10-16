"""x4guard copied under .codex/hooks finds its roots exactly as under .claude/hooks (lane B Task 6).

Kept in its own file (not test_x4guard_check.py) so it merges cleanly with lane E's edits there.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _layout import require_repo  # noqa: E402  (R2-B1: repo-only content skips, counted)
from codex_testlib import copy_shared_runtime

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
GUARDS = REPO / "agent" / "guards" / "claude-hooks"
require_repo("agent/guards/claude-hooks", module_level=True,
             why="every test here copies the guard SOURCE tree")


@pytest.fixture
def env_without_toolkit(tmp_path):
    copy_shared_runtime(tmp_path / "root")
    env = {k: v for k, v in os.environ.items() if k != "X4_TOOLKIT"}
    env.update(X4_CONFIG="/nonexistent", X4_REFERENCE=str(tmp_path / "ref"), X4_GAME=str(tmp_path / "game"))
    env.pop("X4_BASH", None)
    return env


def check(env, script):
    r = subprocess.run([sys.executable, str(script), "check", "--kind", "shell", "--shell", "bash",
                        "--command", "echo hi"], capture_output=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_runs_when_deployed_under_codex_hooks(tmp_path, env_without_toolkit):
    codex_hooks = tmp_path / "root" / ".codex" / "hooks"
    shutil.copytree(GUARDS, codex_hooks)
    v = check(env_without_toolkit, codex_hooks / "x4guard.py")
    assert not v["inert"] and v["decision"] == "allow", v["reason"]


def test_TWIN_claude_hooks_still_runs(tmp_path, env_without_toolkit):
    claude_hooks = tmp_path / "root" / ".claude" / "hooks"
    shutil.copytree(GUARDS, claude_hooks)
    assert not check(env_without_toolkit, claude_hooks / "x4guard.py")["inert"]


def test_runs_when_deployed_under_opencode_hooks(tmp_path, env_without_toolkit):
    oc_hooks = tmp_path / "root" / ".opencode" / "hooks"
    shutil.copytree(GUARDS, oc_hooks)
    v = check(env_without_toolkit, oc_hooks / "x4guard.py")
    assert not v["inert"] and v["decision"] == "allow", v["reason"]


@pytest.mark.parametrize("agent_dir", [".codex", ".opencode", ".claude"])
def test_a_write_into_reference_is_DENIED_from_every_hooks_dir(tmp_path, env_without_toolkit, agent_dir):
    """R7-12 (v4.0.0 review): the tests above prove only an ALLOW of `echo hi`, which guards that
    found no roots at all would also give. A write into the configured reference/ must be a real
    deny (not inert) from each deployed location -- the roots were found and a rule fired."""
    hooks = tmp_path / "root" / agent_dir / "hooks"
    shutil.copytree(GUARDS, hooks)
    ref = Path(env_without_toolkit["X4_REFERENCE"])
    (ref / "libraries").mkdir(parents=True)
    r = subprocess.run([sys.executable, str(hooks / "x4guard.py"), "check", "--kind", "write",
                        "--path", str(ref / "libraries" / "wares.xml")],
                       capture_output=True, env=env_without_toolkit, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v["decision"] == "deny" and not v["inert"], v


@pytest.mark.parametrize("where", [("x", "hooks"), (".codex", "guards"), (".codexx", "hooks"),
                                   (".opencode", "guards"), (".opencodex", "hooks")])
def test_TWIN_other_dir_without_toolkit_still_inert(tmp_path, env_without_toolkit, where):
    lone = tmp_path.joinpath(*where)
    shutil.copytree(GUARDS, lone)
    v = check(env_without_toolkit, lone / "x4guard.py")
    assert v["inert"] and v["decision"] == "deny"

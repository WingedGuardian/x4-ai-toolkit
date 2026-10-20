"""Real rendered session entrypoints; no running coding host is implied."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from _layout import common_hook_dir, require_native_codex
from codex_testlib import REPO, make_sandbox

sys.path.insert(0, str(REPO / "scripts"))
from gitbash import find_bash  # noqa: E402


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_rendered_session_hook_injects_only_explicit_preferences(tmp_path, agent):
    _, root, env = make_sandbox(tmp_path)
    # Each case copies only ITS carrier, from where this install has it (release review T4):
    # the session canary is a common guard (any carrier); the Codex entry needs .codex/hooks.
    if agent == "codex":
        require_native_codex(REPO)
    source = REPO / ".codex/hooks" if agent == "codex" else common_hook_dir(REPO)
    target = ".codex" if agent == "codex" else ".claude"
    shutil.copytree(source, root / target / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "KNOWLEDGEBASE.local.md").write_text("## My preferences\nKeep the local evidence.\n## Other\nUnclassified note.")
    env.update(X4_PYTHON=sys.executable, X4_CODEX_SHELL="bash")
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(root), "session_id": "s"})
    if agent == "claude":
        bash = find_bash()
        if not bash:
            pytest.skip("Bash unavailable; rendered session hook not exercised")
        # The notes belong to this hook's install, independent of tool lookup roots.
        other = tmp_path / "other-toolkit"
        other.mkdir()
        (other / "KNOWLEDGEBASE.local.md").write_text("## My preferences\nWRONG PROJECT PREFERENCES")
        env["X4_TOOLKIT"] = str(other)
        command = [str(bash), str(root / ".claude/hooks/session-canary.sh")]
    else:
        command = [sys.executable, "-B", str(root / ".codex/hooks/codex_adapter.py"), "session_start"]
    completed = subprocess.run(command, input=payload, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "Keep the local evidence." in completed.stdout
    assert "Unclassified note." not in completed.stdout and "WRONG PROJECT" not in completed.stdout


def test_K7_guards_off_banner_precedes_injected_preferences(tmp_path):
    """K7: up to 4 KiB of preferences were printed BEFORE the GUARDS OFF banner, and the
    harness preview keeps only the head of SessionStart output (#38)."""
    bash = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path(shutil.which("bash") or "")
    if not bash.is_file():
        pytest.skip("Bash unavailable; rendered session hook not exercised")
    _, root, env = make_sandbox(tmp_path)
    shutil.copytree(common_hook_dir(REPO), root / ".claude/hooks", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "KNOWLEDGEBASE.local.md").write_text("## My preferences\n" + "Keep the local evidence.\n" * 150)
    env.update(X4_PYTHON=sys.executable, X4_GUARD="off")
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(root), "session_id": "s"})
    completed = subprocess.run([str(bash), str(root / ".claude/hooks/session-canary.sh")], input=payload, env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "Keep the local evidence." in completed.stdout
    assert completed.stdout.lstrip().startswith("[x4 guards] *** GUARDS OFF"), completed.stdout[:300]


@pytest.mark.parametrize("status", [-2, -1, 0, 1, 2])
@pytest.mark.parametrize("long_note", [False, True])
def test_complete_session_context_shares_one_output_cap(tmp_path, status, long_note):
    bash = find_bash()
    assert bash, "the session-context control requires Bash"
    _, root, env = make_sandbox(tmp_path)
    hooks = root / ".claude/hooks"
    shutil.copytree(common_hook_dir(REPO), hooks, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "KNOWLEDGEBASE.local.md").write_text(
        "## My preferences\n" + "Keep the local evidence.\n" * 150, encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    if status != -1:
        (root / "scripts/x4canary.py").write_text(
            "import sys\nprint('NOT CHECKED: fixture' if " + str(status) + " == 0 else 'detail ' * 6000)\n"
            "raise SystemExit(" + str(status) + ")\n", encoding="utf-8")
    if long_note:
        (hooks.parent / "housekeep-session.txt").write_text("[x4 disk] " + "detail " * 6000, encoding="utf-8")
    env.update(X4_TOOLKIT=str(root), X4_PYTHON=sys.executable, X4_GUARD="off",
               X4_HOOK_MAX_CHARS="10000", X4_HOUSEKEEP_SWEEP="off",
               X4_CONFIG=str(tmp_path / "absent.env"), HOME=str(tmp_path / "home"))
    if status == -2:
        env["X4_PYTHON"] = "no_such_session_python"
    completed = subprocess.run([bash, str(hooks / "session-canary.sh")], input="{}",
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=40)
    assert completed.returncode == 0, completed.stderr
    assert len(completed.stdout.rstrip("\n")) <= 10000, len(completed.stdout)
    assert completed.stdout.lstrip().startswith("[x4 guards] *** GUARDS OFF")
    preference = "PREFERENCES: NOT INJECTED" if status == -2 else "Keep the local evidence."
    assert preference in completed.stdout
    priority = ("NOT RUN:" if status < 0 else "NOT CHECKED: fixture" if status == 0
                else "Recover it BEFORE" if status == 1 else "COULD NOT CHECK")
    assert completed.stdout.index(priority) < completed.stdout.index(preference)
    assert completed.stdout.index(priority) < 2000
    assert ("TRUNCATED" in completed.stdout) == (long_note or status > 0)

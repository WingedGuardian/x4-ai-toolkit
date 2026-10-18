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

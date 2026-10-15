"""Real rendered session entrypoints; no running coding host is implied."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from codex_testlib import REPO, make_sandbox


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_rendered_session_hook_injects_only_explicit_preferences(tmp_path, agent):
    _, root, env = make_sandbox(tmp_path)
    for target in (".claude", ".codex"):
        shutil.copytree(REPO / target / "hooks", root / target / "hooks",
                        ignore=shutil.ignore_patterns("__pycache__"))
    (root / "KNOWLEDGEBASE.local.md").write_text("## My preferences\nKeep the local evidence.\n## Other\nUnclassified note.")
    env.update(X4_PYTHON=sys.executable, X4_CODEX_SHELL="bash")
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(root), "session_id": "s"})
    if agent == "claude":
        bash = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path(shutil.which("bash") or "")
        if not bash.is_file():
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

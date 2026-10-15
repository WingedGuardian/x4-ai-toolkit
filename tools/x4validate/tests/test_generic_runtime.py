import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from codex_testlib import REPO


def test_generic_runtime_works_without_any_named_agent_folder(tmp_path):
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    saves = tmp_path / "profile/save"
    saves.mkdir(parents=True)
    save = saves / "progress.xml"
    save.write_bytes(b"fixture save")
    env = dict(os.environ, X4_TOOLKIT=str(root), X4_PROFILE=str(saves.parent), X4_SAVES=str(saves),
               X4_CONFIG="/nonexistent", X4_GAME=str(tmp_path / "game"), X4_REFERENCE=str(tmp_path / "reference"))
    for name in ("X4_GUARD", "X4_GUARD_CHECK", "X4_BASH"):
        env.pop(name, None)
    def run(command, data=None, *args):
        result = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), command, *args],
                                input=json.dumps(data).encode() if data is not None else None,
                                env=env, capture_output=True, timeout=45)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert run("capabilities")["host_enforcement"].startswith("unmeasured")
    action = {"api_version": 1, "host": "future-coder", "tool": "edit", "surface": "files",
              "cwd": str(root), "session_id": "s", "paths": [str(save)], "operations": ["write"]}
    preview = run("check", action, "--preview")
    assert preview["result"]["preservation"] == "required" and not preview["transactions"]
    checked = run("check", action)
    assert checked["result"]["preservation"] == "verified", checked
    assert len(run("save-list")["transactions"]) == 1
    save.write_bytes(b"changed")
    restored = run("save-restore", None, checked["transactions"][0], "--to", str(tmp_path / "recovered"))
    assert Path(restored["published"][0]).read_bytes() == b"fixture save"
    assert save.read_bytes() == b"changed"
    unknown = run("check", dict(action, tool="future-code-mode", surface="nested", paths=[], operations=[]))
    assert unknown["result"]["decision"] == "allow" and unknown["result"]["evaluation"] == "unguarded"
    assert run("check", dict(action, operations=[]))["result"]["decision"] == "deny"
    assert not any((root / folder).exists() for folder in (".claude", ".codex", ".opencode", ".agents"))

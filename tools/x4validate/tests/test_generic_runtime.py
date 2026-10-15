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
    restored = run("save-restore", None, checked["transactions"][0], "--in-place")
    assert restored["pre_restore_transactions"]
    assert restored["published"] == [str(save)]
    assert save.read_bytes() == b"fixture save"
    # Both the deciding policy and the restore journal retain a verified copy of
    # the state replaced in-place; the old transaction is not rewritten.
    transactions = run("save-list")["transactions"]
    assert len(transactions) >= 2
    import hashlib
    assert any(member.get("sha256") == hashlib.sha256(b"changed").hexdigest()
               for transaction in transactions for group in transaction["groups"] for member in group["members"])
    unknown = run("check", dict(action, tool="future-code-mode", surface="nested", paths=[], operations=[]))
    assert unknown["result"]["decision"] == "allow" and unknown["result"]["evaluation"] == "unguarded"
    assert run("check", dict(action, operations=[]))["result"]["decision"] == "deny"
    note = root / "notes.txt"
    note.write_bytes(b"unclassified personal notes")
    candidate = root / "candidate.json"
    candidate.write_text(json.dumps({"v": 1, "files": {"notes.txt": "a" * 64}}))
    plan = run("upgrade-preview", None, "--candidate", str(candidate))
    assert plan["mode"] == "read-only preview" and plan["collisions"] == 1
    assert plan["files"][0]["action"] == "preserve_collision"
    assert note.read_bytes() == b"unclassified personal notes"
    assert not any((root / folder).exists() for folder in (".claude", ".codex", ".opencode", ".agents"))


def test_generic_upgrade_staging_preserves_install_and_fails_nonzero_if_reused(tmp_path):
    import hashlib
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    installed, source, bundle = tmp_path / "installed", tmp_path / "source", tmp_path / "review"
    installed.mkdir()
    source.mkdir()
    (installed / "AGENTS.md").write_bytes(b"private instructions")
    (source / "AGENTS.md").write_bytes(b"candidate instructions")
    inventory = tmp_path / "candidate.json"
    inventory.write_text(json.dumps({"v": 1, "files": {"AGENTS.md": hashlib.sha256(b"candidate instructions").hexdigest()}}))
    args = [sys.executable, "-B", str(root / "tools/agent-harness.py"), "upgrade-stage", "--installed-root", str(installed),
            "--source", str(source), "--candidate", str(inventory), "--destination", str(bundle)]
    first = subprocess.run(args, capture_output=True, timeout=45)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["complete"] and not result["application_authorized"] and result["collisions"] == 1
    assert (installed / "AGENTS.md").read_bytes() == b"private instructions"
    assert (bundle / "rollback/AGENTS.md").read_bytes() == b"private instructions"
    assert subprocess.run(args, capture_output=True, timeout=45).returncode == 2


def test_bulk_in_place_restore_binds_human_scope_command_count_and_host(tmp_path):
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    saves = tmp_path / "profile/save"
    saves.mkdir(parents=True)
    members = [saves / (str(index) + ".xml") for index in range(3)]
    for member in members:
        member.write_bytes(b"original")
    sys.path.insert(0, str(root / "tools"))
    try:
        from modding_harness.preservation import SaveStore
        from modding_harness import approval
        store = SaveStore(root, reserve=0)
        transaction = store.backup([[member] for member in members])["id"]
    finally:
        sys.path.pop(0)
    for member in members:
        member.write_bytes(b"current")
    env = dict(os.environ, X4_TOOLKIT=str(root), X4_PROFILE=str(saves.parent), X4_SAVES=str(saves),
               X4_CONFIG="/nonexistent", X4_GAME=str(tmp_path / "game"), X4_REFERENCE=str(tmp_path / "reference"))
    for name in ("X4_GUARD", "X4_GUARD_CHECK", "X4_BASH"):
        env.pop(name, None)
    transcript = root / "human.jsonl"
    args = ["save-restore", transaction, "--in-place", "--host", "claude", "--session-id", "s",
            "--transcript", str(transcript)]
    def run(arguments):
        completed = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), *arguments],
                                   capture_output=True, env=env, timeout=45)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    refused = run(args)
    assert refused["decision"] == "deny", refused
    grant = next(line for line in refused["reason"].splitlines() if line.startswith(approval.KEY + " "))
    scope = json.loads(grant[len(approval.KEY) + 1:])
    assert scope["count"] == 3 and scope["command_sha256"]
    def human(text):
        transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                        "message": {"role": "user", "content": text}}))
    # Correct paths alone do not authorize a helper with a changed command or count.
    human(approval.example("s", "write", members))
    assert run(args)["decision"] == "deny"
    human(grant)
    # Single-save helpers follow the normal one/two-save rule; use changed bulk
    # argument order to retain three saves while changing the bound command.
    changed = [*args[:2], "--host", "claude", "--in-place", *args[5:]]
    assert run(changed)["decision"] == "deny"
    assert run(["save-restore", transaction, "--in-place", "--host", "future-agent", "--session-id", "s",
                "--transcript", str(transcript)])["decision"] == "deny"
    restored = run(args)
    assert len(restored["published"]) == 3 and restored["pre_restore_transactions"]
    assert all(member.read_bytes() == b"original" for member in members)

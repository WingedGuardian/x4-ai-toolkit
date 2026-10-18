import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_testlib import REPO


@pytest.mark.parametrize("environment_root", ["unset", "different-install"])
@pytest.mark.parametrize("surface", ["other", "mcp", "nested"])
def test_unknown_tools_use_own_runtime_without_game_configuration(tmp_path, environment_root, surface):
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    foreign = tmp_path / "another-install"
    foreign.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("X4_")}
    if environment_root != "unset":
        environment["X4_TOOLKIT"] = str(foreign)
    action = {"api_version": 1, "host": "future-coder", "tool": "unknown_tool", "surface": surface,
              "cwd": str(root), "session_id": "unknown-fixture", "tool_call_id": "one"}
    result = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), "check"],
                            input=json.dumps(action).encode(), capture_output=True, env=environment, timeout=20)
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)
    assert reply["result"]["decision"] == "allow" and reply["result"]["evaluation"] == "unguarded", reply
    assert any("WARN AND ALLOW" in warning for warning in reply["result"]["warnings"])
    assert (root / ".modding-agent-harness").is_dir()
    assert not list(foreign.iterdir())
    assert not any((root / folder).exists() for folder in (".claude", ".codex", ".opencode", ".agents"))


@pytest.mark.parametrize('brackets', [False, True])
def test_generic_runtime_works_without_any_named_agent_folder(tmp_path, brackets):
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    saves = tmp_path / ('profile [one]' if brackets else 'profile') / 'save'
    saves.mkdir(parents=True)
    save = saves / ('progress[1].xml' if brackets else 'progress.xml')
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
    # Execute the exact public new-host examples through the installed-only entry.
    blocks = re.findall(r"```json\n(.*?)\n```", (REPO / "ADAPTING.md").read_text(encoding="utf-8"), re.S)
    examples = [json.loads(block) for block in blocks if '"api_version":1' in block]
    assert len(examples) == 2
    for example in examples:
        example["cwd"] = str(root)
        reply = run("check", example)
        assert reply["result"]["api_version"] == 1
        assert reply["result"]["decision"] == "allow" and reply["result"]["evaluation"] == "evaluated", reply
        assert reply["result"]["preservation"] == "not_required" and not reply["transactions"]
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
    choices = tmp_path / 'choices.json'
    choices.write_text(json.dumps({'AGENTS.md': 'candidate'}))
    review_sha = hashlib.sha256((bundle / 'complete.json').read_bytes()).hexdigest()
    applying = [sys.executable, '-B', str(root / 'tools/agent-harness.py'), 'upgrade-apply',
                '--installed-root', str(installed), '--bundle', str(bundle), '--review-sha256', review_sha,
                '--choices', str(choices), '--quiescent']
    applied = subprocess.run(applying, capture_output=True, timeout=45)
    assert applied.returncode == 0, applied.stdout
    receipt = json.loads(applied.stdout)
    assert receipt['host_review_required'] and (installed / 'AGENTS.md').read_bytes() == b'candidate instructions'
    rolled = subprocess.run([sys.executable, '-B', str(root / 'tools/agent-harness.py'), 'upgrade-rollback',
        receipt['operation'], '--installed-root', str(installed), '--quiescent'], capture_output=True, timeout=45)
    assert rolled.returncode == 0, rolled.stdout
    assert (installed / 'AGENTS.md').read_bytes() == b'private instructions'


@pytest.mark.parametrize("in_place", [False, True])
def test_neutral_restore_carries_one_deadline_through_every_copy(tmp_path, monkeypatch, capsys, in_place):
    import importlib.util
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    saves = tmp_path / "profile/save"
    saves.mkdir(parents=True)
    member = saves / "A.xml"
    member.write_bytes(b"original")
    # Isolate the rendered CLI's imports from other fixture roots in this process.
    monkeypatch.syspath_prepend(str(root / "tools/x4_harness"))
    for name in ("save_policy", "x4guard"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location("deadline_generic_cli", root / "tools/agent-harness.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    roots = {"toolkit": str(root), "profile": str(saves.parent), "saves": str(saves)}
    monkeypatch.setattr(cli.save_policy, "read_roots", lambda deadline: roots)
    monkeypatch.setattr(cli, "judge", lambda *args: {"decision": "allow", "inert": False, "reason": None, "context": None})
    profile = cli.save_policy.resolve(root, roots, [])[0]
    store = cli.save_policy.core(root, "workspace").save_store(root, profile)
    transaction = store.backup([[member]])["id"]
    member.write_bytes(b"current")
    durable = cli.save_policy.core(root, "durable")
    real, seen = durable.copy_verified, []
    def observe(source, target, deadline=None):
        seen.append(deadline)
        return real(source, target, deadline)
    monkeypatch.setattr(durable, "copy_verified", observe)
    args = ["save-restore", transaction, *(('--in-place',) if in_place else ('--to', str(tmp_path / 'out')))]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"]
    assert seen and all(deadline is not None for deadline in seen)
    assert len(set(seen)) == 1, "pre-preservation and restore must share one total budget"


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


def test_K6_a_shell_action_without_cwd_never_borrows_the_entry_process_directory(tmp_path):
    """K6: the generic entry passed execution_cwd_known=True when provenance omitted it, so a
    relative mutation in a cwd-less shell action was resolved against agent-harness.py's OWN
    directory. Run from inside the save folder, `rm progress.xml` became a save delete there."""
    root = tmp_path / "toolkit"
    for rel in ("tools/x4_harness", "tools/modding_harness"):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    saves = tmp_path / "profile/save"
    saves.mkdir(parents=True)
    (saves / "progress.xml").write_bytes(b"fixture save")
    env = dict(os.environ, X4_TOOLKIT=str(root), X4_PROFILE=str(saves.parent), X4_SAVES=str(saves),
               X4_CONFIG="/nonexistent", X4_GAME=str(tmp_path / "game"), X4_REFERENCE=str(tmp_path / "reference"))
    for name in ("X4_GUARD", "X4_GUARD_CHECK", "X4_BASH"):
        env.pop(name, None)

    def check(action):
        result = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), "check"],
                                input=json.dumps(action).encode(), capture_output=True, env=env,
                                timeout=60, cwd=str(saves))
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    shell = {"api_version": 1, "host": "future-coder", "tool": "shell", "surface": "shell", "shell": "bash",
             "session_id": "s", "command": "rm progress.xml"}
    reply = check(shell)
    assert reply["result"]["decision"] == "deny", reply
    assert "absolute" in reply["result"]["reason"], reply
    assert not reply["transactions"] and (saves / "progress.xml").exists()
    # Twins: the same action WITH its cwd is judged there; a cwd-less harmless command passes.
    reply = check(dict(shell, cwd=str(saves)))
    assert reply["result"]["preservation"] == "verified" and reply["transactions"], reply
    reply = check(dict(shell, command="echo hello"))
    assert reply["result"]["decision"] == "allow", reply

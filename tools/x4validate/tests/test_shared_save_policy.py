import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("profile_save_policy", REPO / "agent/guards/claude-hooks/save_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


@pytest.fixture
def install(tmp_path):
    import shutil
    root = tmp_path / "toolkit"
    shutil.copytree(REPO / "tools/modding_harness", root / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    profile = tmp_path / "profile"
    saves = profile / "save"
    saves.mkdir(parents=True)
    files = [saves / (str(i) + ext) for i, ext in enumerate((".xml.gz", ".xml", ".xml.gz"))]
    for file in files:
        file.write_bytes(file.name.encode())
    return root, {"profile": str(profile), "saves": str(saves)}, files


def run(install, files=None, **options):
    root, roots, saves = install
    files = saves[:1] if files is None else files
    evidence = [{"path": str(p), "operation": "write"} for p in files]
    contracts = policy.core(root, "contracts")
    action = contracts.Action("claude", "Write", "files", session_id="s")
    decisions = []
    def backend(save_only):
        decisions.append(save_only)
        return contracts.Result(evaluation="evaluated")
    return policy.enforce(root, roots, evidence, action, backend, **options), decisions


def test_one_or_two_saves_preserved_as_one_batch_compressed_and_plain(install):
    outcome, decisions = run(install, install[2][:2])
    assert decisions == [True]
    assert outcome.result.preservation == "verified" and len(outcome.transactions) == 1
    store = policy.core(install[0], "workspace").save_store(install[0], policy.resolve(install[0], install[1], [])[0])
    assert len(store.load(outcome.transactions[0])["groups"]) == 2


def test_bulk_is_not_split_into_individual_allow_calls(install):
    outcome, _decisions = run(install, install[2])
    assert outcome.result.decision == "deny" and "SAVE_ACCESS_APPROVAL" in outcome.result.reason
    assert not outcome.transactions


def test_copy_to_existing_save_directory_preserves_resulting_filename(install, tmp_path):
    root, roots, files = install
    source = tmp_path / files[0].name
    source.write_bytes(b"incoming replacement")
    result = invoke_hook(install, "Bash", {"command": 'cp -- "' + str(source) + '" "' + roots["saves"] + '/"'})
    assert result["hookSpecificOutput"].get("permissionDecision") is None, result
    assert "backup verified" in result["hookSpecificOutput"]["additionalContext"]
    records = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert len(records) == 1
    manifest = json.loads(records[0].read_text())
    assert [Path(m["path"]).name for g in manifest["groups"] for m in g["members"]] == [files[0].name]
    assert files[0].read_bytes() == files[0].name.encode()


@pytest.mark.parametrize("syntax", ["plain", "target-option", "long-target", "move"])
def test_directory_copy_batches_two_and_bulk_needs_one_command_bound_grant(install, tmp_path, syntax):
    root, roots, files = install
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    for file in files:
        (incoming / file.name).write_bytes(b"replacement")
    sources = ['"' + str(incoming / file.name) + '"' for file in files]
    dest = '"' + roots["saves"] + '"'
    def command(count):
        args = ' '.join(sources[:count])
        if syntax == "target-option":
            return 'cp -t ' + dest + ' -- ' + args
        if syntax == "long-target":
            return 'cp --target-directory=' + dest + ' -- ' + args
        return ('mv' if syntax == "move" else 'cp') + ' -- ' + args + ' ' + dest
    first = invoke_hook(install, "Bash", {"command": command(2)})
    assert first["hookSpecificOutput"].get("permissionDecision") is None, first
    records = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert len(records) == 1 and len(json.loads(records[0].read_text())["groups"]) == 2
    bulk = invoke_hook(install, "Bash", {"command": command(3)})
    assert bulk["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = bulk["hookSpecificOutput"]["permissionDecisionReason"]
    assert "SAVE_ACCESS_APPROVAL" in reason and "command_sha256" in reason
    assert len(list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))) == 1


@pytest.mark.parametrize("shape", ["recursive", "symlink", "backup-suffix", "glob", "unknown", "directory"])
def test_unqualified_directory_copy_never_authorizes_a_save_mutation(install, tmp_path, shape):
    root, roots, files = install
    source = tmp_path / files[0].name
    source.write_bytes(b"replacement")
    operand, option = '"' + str(source) + '"', ''
    if shape == "recursive":
        option = '-r '
        source.unlink()
        source.mkdir()
    elif shape == "symlink":
        option = '-s '
    elif shape == "backup-suffix":
        option = '--backup '
    elif shape == "glob":
        operand = '"' + str(tmp_path) + '/"*.xml.gz'
    elif shape == "unknown":
        operand = '"$UNKNOWN_SOURCE"'
    else:
        source.unlink()
        source.mkdir()
    command = 'cp ' + option + operand + ' "' + roots["saves"] + '"'
    result = invoke_hook(install, "Bash", {"command": command})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny", result
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_directory_resolution_recheck_detects_a_source_changed_to_a_directory(install, tmp_path):
    root, roots, files = install
    source = tmp_path / files[0].name
    source.write_bytes(b"replacement")
    evidence = [{"path": roots["saves"], "operation": "write", "scope": "copy-destination",
                 "copy_sources": [{"path": str(source), "unresolved": False}]}]
    _profile, plan, _affected, save_only = policy.resolve(root, roots, evidence)
    assert save_only and plan.helper and plan.recheck() == plan.mutations
    source.unlink()
    source.mkdir()
    assert plan.recheck() != plan.mutations


def test_other_profile_file_retains_backend_confirmation_and_preview_has_no_backup(install):
    root, roots, saves = install
    config = Path(roots["profile"]) / "config.xml"
    outcome, decisions = run(install, [saves[0], config], preview=True)
    assert decisions == [False] and outcome.result.preservation == "required"
    assert not (root / ".modding-agent-harness/backups/saves").exists()


def test_local_restriction_precedes_preservation(install):
    contracts = policy.core(install[0], "contracts")
    outcome, _decisions = run(install, local_restrictions=(lambda action: contracts.Result("deny", "evaluated", reason="local policy"),))
    assert outcome.result.reason == "local policy" and not outcome.transactions


def test_whole_folder_and_store_writes_refuse(install):
    root, roots, saves = install
    contracts = policy.core(root, "contracts")
    action = contracts.Action("claude", "Bash", "shell")
    for path, op in ((Path(roots["saves"]), "delete"),
                     (root / ".modding-agent-harness/backups/saves/blob", "write")):
        outcome = policy.enforce(root, roots, [{"path": str(path), "operation": op}], action,
                                lambda save_only: contracts.Result(evaluation="evaluated"))
        assert outcome.result.decision == "deny" and not outcome.transactions


def test_wildcard_has_exact_command_bound_grant_and_membership_recheck(install):
    root, roots, files = install
    contracts = policy.core(root, "contracts")
    approval = policy.core(root, "approval")
    action = contracts.Action("claude", "Bash", "shell", command="change all", session_id="s")
    text = approval.example("s", "write", files, action.command, True, len(files))
    transcript = root / "human.jsonl"
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                     "message": {"role": "user", "content": text}}))
    evidence = [{"path": str(Path(roots["saves"]) / "*.xml*"), "operation": "write"}]
    outcome = policy.enforce(root, roots, evidence, action, lambda save_only: contracts.Result(evaluation="evaluated"),
                             transcript=str(transcript))
    assert outcome.result.preservation == "verified"
    (Path(roots["saves"]) / "new.xml").write_bytes(b"new")
    outcome = policy.enforce(root, roots, evidence, action, lambda save_only: contracts.Result(evaluation="evaluated"),
                             transcript=str(transcript))
    assert outcome.result.decision == "deny" and not outcome.transactions


def invoke_hook(install, tool, ti, preview=False):
    root, roots, _files = install
    hooks = root / ".claude/hooks"
    shutil.copytree(REPO / "agent/guards/claude-hooks", hooks, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("fixture_x4guard", hooks / "x4guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, why = guard.resolve_bash()
    assert bash, why
    env = dict(os.environ, X4_TOOLKIT=str(root), X4_PROFILE=roots["profile"], X4_SAVES=roots["saves"],
               X4_GAME=str(root.parent / "game"), X4_REFERENCE=str(root / "reference"),
               X4_PYTHON=sys.executable)
    env.pop("X4_GUARD", None)
    env.pop("X4_GUARD_CHECK", None)
    if preview:
        env["X4_GUARD_CHECK"] = "1"
    payload = {"tool_name": tool, "tool_input": ti, "cwd": str(root), "session_id": "s"}
    result = subprocess.run([bash, str(hooks / ("protect-bash.sh" if tool in ("Bash", "PowerShell") else "protect-files.sh"))],
                            input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=40)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


@pytest.mark.parametrize("verb", ["Copy-Item", "Move-Item"])
def test_powershell_file_to_save_directory_preserves_destination_first(install, tmp_path, verb):
    root, roots, files = install
    source = tmp_path / files[0].name
    source.write_bytes(b"incoming")
    command = verb + " -LiteralPath '" + str(source) + "' -Destination '" + roots["saves"] + "'"
    result = invoke_hook(install, "PowerShell", {"command": command})
    assert result["hookSpecificOutput"].get("permissionDecision") is None, result
    assert "backup verified" in result["hookSpecificOutput"]["additionalContext"]
    assert len(list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))) == 1


def test_real_file_hook_preserves_before_permission_and_preview_is_readonly(install):
    root, _roots, files = install
    result = invoke_hook(install, "Write", {"file_path": str(files[0]), "content": "changed"}, preview=True)
    assert "PREVIEW ONLY" in result["hookSpecificOutput"]["additionalContext"]
    assert not (root / ".modding-agent-harness/backups").exists()
    assert not (root / ".modding-agent-harness/coverage.sqlite3").exists()
    result = invoke_hook(install, "Write", {"file_path": str(files[0]), "content": "changed"})
    assert result["hookSpecificOutput"].get("permissionDecision") is None, result
    assert "backup verified" in result["hookSpecificOutput"]["additionalContext"]
    assert list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert files[0].read_bytes() == files[0].name.encode()


def test_native_file_hook_treats_bracketed_save_paths_as_literal(install, tmp_path):
    root, roots, _files = install
    profile = tmp_path / 'profile [one]'
    folder = profile / 'save'
    folder.mkdir(parents=True)
    target = folder / 'Save[1].xml.gz'
    target.write_bytes(b'opaque save fixture')
    roots.update(profile=str(profile), saves=str(folder))
    result = invoke_hook((root, roots, [target]), 'Write', {'file_path': str(target), 'content': 'changed'})
    assert result['hookSpecificOutput'].get('permissionDecision') is None, result
    assert 'backup verified' in result['hookSpecificOutput']['additionalContext']
    records = list((root / '.modding-agent-harness/backups/saves/transactions').glob('*.json'))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert [Path(m['path']).name for g in record['groups'] for m in g['members']] == [target.name]
    assert target.read_bytes() == b'opaque save fixture'


def test_real_shell_hook_batches_saves_and_retains_other_denials(install):
    root, roots, files = install
    result = invoke_hook(install, "Bash", {"command": 'rm -- "' + str(files[0]) + '"'})
    assert result["hookSpecificOutput"].get("permissionDecision") is None, result
    assert "backup verified" in result["hookSpecificOutput"]["additionalContext"]
    result = invoke_hook(install, "Bash", {"command": "rm -- " + " ".join('"' + str(f) + '"' for f in files)})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny", result
    assert "SAVE_ACCESS_APPROVAL" in result["hookSpecificOutput"]["permissionDecisionReason"]
    result = invoke_hook(install, "Bash", {"command": 'rm -rf "' + roots["saves"] + '"'})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert all(f.exists() for f in files)

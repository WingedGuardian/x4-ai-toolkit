import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _layout import require_repo


REPO = Path(__file__).resolve().parents[3]
require_repo("agent/guards/claude-hooks/save_policy.py", module_level=True,
             why="this module loads the canonical save policy source")
spec = importlib.util.spec_from_file_location("profile_save_policy", REPO / "agent/guards/claude-hooks/save_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_neutral_private_state_is_ignored_but_runtime_sources_are_trackable(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    shutil.copy2(REPO / ".gitignore", tmp_path / ".gitignore")
    names = [".modding-agent-harness/coverage.sqlite3", ".modding-agent-harness/backups/saves/private.ess",
             ".modding-agent-harness/upgrades/private/plan.json", "tools/modding_harness/upgrade.py"]
    result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "-z", "--stdin"],
                            input=b"\0".join(name.encode() for name in names), capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split(b"\0")[:-1] == [name.encode() for name in names[:-1]]


@pytest.mark.parametrize("count,mixed", [(1, "none"), (3, "none"), (1, "profile"),
                                        (3, "profile"), (3, "compound")])
def test_mixed_asks_never_bypass_save_preservation_or_bulk_approval(install, count, mixed):
    root, roots, files = install
    other = Path(roots["profile"]) / "content.xml"
    other.write_bytes(b"keep profile")
    command = "rm -- " + " ".join("'" + p.as_posix() + "'" for p in files[:count])
    if mixed == "profile":
        command += " '" + other.as_posix() + "'"
    elif mixed == "compound":
        command += "; cd '" + Path(roots["profile"]).as_posix() + "' && git clean -fdx"
    result = invoke_hook(install, "Bash", {"command": command})
    hso = result["hookSpecificOutput"]
    records = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    if count > 2:
        assert hso["permissionDecision"] == "deny", hso
        assert "SAVE_ACCESS_APPROVAL" in hso["permissionDecisionReason"]
        assert not records
    else:
        assert len(records) == 1
        manifest = json.loads(records[0].read_text(encoding="utf-8"))
        assert len(manifest["groups"]) == count
        if mixed != "none":
            assert hso["permissionDecision"] == "ask", hso
            assert "backup verified" in hso["permissionDecisionReason"], hso
        else:
            assert hso.get("permissionDecision") is None, hso
            assert "backup verified" in hso["additionalContext"]
    assert other.read_bytes() == b"keep profile"
    assert all(p.read_bytes() == p.name.encode() for p in files)


@pytest.mark.parametrize("preview", [False, True])
def test_batch_ask_keeps_transaction_and_preservation_notice(install, monkeypatch, preview):
    root, roots, files = install
    monkeypatch.setattr(policy, "read_roots", lambda deadline: dict(roots, toolkit=str(root)))
    calls = [("write", None, None, str(files[0]), "save"),
             ("write", None, None, str(Path(roots["profile"]) / "content.xml"), "profile")]
    payload = {"tool_name": "fixture", "session_id": "s", "tool_input": {}}
    result = policy.batch(payload, calls, lambda qualified: {
        "decision": "ask", "inert": False, "reason": "Other profile change", "context": None},
        None, "claude", preview=preview, execution_cwd_known=True)
    assert result["decision"] == "ask" and not result["inert"], result
    assert result["result"]["preservation"] == ("required" if preview else "verified")
    assert len(result["transactions"]) == (0 if preview else 1), result
    assert ("backup verified" in result["reason"]) is not preview
    assert files[0].read_bytes() == files[0].name.encode()


@pytest.mark.parametrize("count", [1, 3])
def test_opaque_program_naming_saves_cannot_silently_bypass_preservation(install, count):
    root, roots, files = install
    command = "./unknown-tool --wipe " + " ".join("'" + p.as_posix() + "'" for p in files[:count])
    result = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    transactions = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    if count == 1:
        assert result["permissionDecision"] == "ask", result
        assert "opaque program" in result["permissionDecisionReason"]
        assert "backup verified" in result["permissionDecisionReason"]
        assert len(transactions) == 1
    else:
        assert result["permissionDecision"] == "deny", result
        assert "SAVE_ACCESS_APPROVAL" in result["permissionDecisionReason"]
        assert not transactions
    assert all(p.read_bytes() == p.name.encode() for p in files)


@pytest.mark.parametrize("program", ["awk", "gawk"])
def test_opaque_embedded_save_program_refuses_unqualified_scope(install, program):
    root, roots, files = install
    command = program + " 'BEGIN{system(\"rm " + Path(roots['saves']).as_posix() + "/0*.xml.gz\")}'"
    result = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny", result
    assert "unresolved protected operand" in result["permissionDecisionReason"]
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


@pytest.mark.parametrize("prefix", ["--wipe=", "-o"])
@pytest.mark.parametrize("quoted", [False, True])
def test_opaque_glued_save_options_refuse_without_guessing_membership(install, prefix, quoted):
    root, roots, files = install
    target = files[0].as_posix()
    command = "./unknown-tool " + prefix + ("'" + target + "'" if quoted else target)
    result = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny", result
    assert "unresolved protected operand" in result["permissionDecisionReason"]
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert files[0].read_bytes() == files[0].name.encode()


def test_opaque_unrelated_glued_options_still_allow(install):
    root, roots, files = install
    # Only analyze this synthetic workspace: the command is never executed. A POSIX
    # pytest sandbox lives under /tmp, where output is independently refused.
    cwd = "C:/x4-fixture-workspace" if os.name == "nt" else "/x4-fixture-workspace"
    result = invoke_hook(install, "Bash", {"command": "./unknown-tool --output=unrelated.xml.gz -v"},
                         extra={"cwd": cwd})
    assert not result.get("hookSpecificOutput", {}).get("permissionDecision"), result
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_opaque_unrelated_glued_output_keeps_shared_tmp_refusal(install):
    root, roots, files = install
    result = invoke_hook(install, "Bash", {"command": "./unknown-tool --output=unrelated.xml.gz -v"},
                         extra={"cwd": "/tmp/x4-fixture-shared"})
    hso = result["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny", result
    assert "WRITING MEASUREMENT OUTPUT TO /tmp" in hso["permissionDecisionReason"], result
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


@pytest.mark.parametrize("spelling", ["$X4_SAVES", "${X4_SAVES}", "${X4_PROFILE}/save"])
@pytest.mark.parametrize("quoted", [False, True])
def test_opaque_glued_save_root_variables_cannot_hide_scope(install, spelling, quoted):
    root, roots, files = install
    target = spelling + "/" + files[0].name
    command = "./unknown-tool --wipe=" + ('"' + target + '"' if quoted else target)
    result = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny", result
    assert "unresolved protected operand" in result["permissionDecisionReason"]
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


@pytest.mark.parametrize("name", ["X4_SAVES", "X4_PROFILE"])
@pytest.mark.parametrize("assignment_first", [False, True])
def test_opaque_glued_root_variable_assignments_cannot_erase_possible_scope(install, name, assignment_first):
    root, roots, files = install
    ordinary = (root / "ordinary").as_posix()
    suffix = "/save" if name == "X4_PROFILE" else ""
    assignment = name + "='" + ordinary + "'"
    call = "./unknown-tool --out=\"$" + name + suffix + "/x.xml.gz\""
    command = assignment + "; " + call if assignment_first else call + "; " + assignment
    result = invoke_hook(install, "Bash", {"command": command})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny", result
    assert "unresolved protected operand" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_opaque_glued_non_save_profile_variable_keeps_accepted_scope(install):
    result = invoke_hook(install, "Bash", {"command": './unknown-tool --out="$X4_PROFILE/config.xml"'})
    assert not result.get("hookSpecificOutput", {}).get("permissionDecision"), result


def test_opaque_unrelated_operand_and_quoted_save_prose_are_outside_scope(install):
    root, roots, files = install
    unrelated = invoke_hook(install, "Bash", {"command": "./unknown-tool --wipe 'unrelated.xml.gz'"})
    assert not unrelated.get("hookSpecificOutput", {}).get("permissionDecision"), unrelated
    prose = invoke_hook(install, "Bash", {"command": "printf '%s' '" + files[0].as_posix() + "'"})
    assert not prose.get("hookSpecificOutput", {}).get("permissionDecision"), prose
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_opaque_relative_save_operand_uses_the_command_directory(install):
    root, roots, files = install
    command = "cd '" + Path(roots['saves']).as_posix() + "'; ./unknown-tool '" + files[0].name + "'"
    result = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert result["permissionDecision"] == "ask", result
    assert "backup verified" in result["permissionDecisionReason"]
    assert len(list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))) == 1


@pytest.mark.parametrize("prefix", ["head -c 3", "tail -n 1", "du -h", "gzip -dc", "zcat"])
def test_opaque_save_rule_exempts_known_read_only_program_forms(install, prefix):
    root, roots, files = install
    result = invoke_hook(install, "Bash", {"command": prefix + " '" + files[0].as_posix() + "'"})
    assert not result.get("hookSpecificOutput", {}).get("permissionDecision"), result
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_opaque_save_rule_exempts_proven_literal_python_prose(install):
    root, roots, files = install
    command = 'python -c "print(\'' + files[0].as_posix() + '\')"'
    result = invoke_hook(install, "Bash", {"command": command})
    assert not result.get("hookSpecificOutput", {}).get("permissionDecision"), result
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


@pytest.mark.parametrize("options", ["", "-S -c", "-Sc", "--suffix --stdout", "--suffix=--stdout", "-- -c"])
def test_opaque_gzip_without_stdout_is_still_possible_save_mutation(install, options):
    root, roots, files = install
    result = invoke_hook(install, "Bash", {"command": "gzip " + options + " '" + files[0].as_posix() + "'"})
    assert result["hookSpecificOutput"]["permissionDecision"] == "ask", result
    assert "backup verified" in result["hookSpecificOutput"]["permissionDecisionReason"]


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
    assert_scope_refusal(bulk["hookSpecificOutput"])            # T2
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
    assert_scope_refusal(result["hookSpecificOutput"])          # T2: the policy's deny, not a crash
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
        assert_scope_refusal({"permissionDecisionReason": outcome.result.reason})   # T2


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
    assert_scope_refusal({"permissionDecisionReason": outcome.result.reason})   # T2


def invoke_hook(install, tool, ti, preview=False, omit_cwd=False, extra=None):
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
               X4_CONFIG=str(root / "owned-missing.env"), X4_BACKUPS=str(root / "backups"),
               X4_MODS=str(root / "dev"), X4_DOCUMENTS=str(root.parent / "documents"),
               X4_EXTENSIONS=str(root.parent / "game/extensions"),
               X4_PYTHON=sys.executable)
    env.pop("X4_GUARD", None)
    env.pop("X4_GUARD_CHECK", None)
    if preview:
        env["X4_GUARD_CHECK"] = "1"
    payload = {"tool_name": tool, "tool_input": ti, "cwd": str(root), "session_id": "s"}
    payload.update(extra or {})
    if omit_cwd:
        payload.pop("cwd")
    result = subprocess.run([bash, str(hooks / ("protect-bash.sh" if tool in ("Bash", "PowerShell") else "protect-files.sh"))],
                            input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=40)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem-provider physical path preservation")
@pytest.mark.parametrize("prefix", ["", "FileSystem::", "filesystem::", "Microsoft.PowerShell.Core\\FileSystem::",
                                   "\\\\?\\", "\\\\.\\", "FileSystem::\\\\?\\"])
def test_real_provider_save_request_preserves_exact_existing_bytes_before_permission(install, prefix):
    root, _roots, files = install
    original = files[0].read_bytes()
    command = "Remove-Item -LiteralPath '" + prefix + str(files[0]) + "'"
    result = invoke_hook(install, "PowerShell", {"command": command})
    hso = result["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None, result
    assert "backup verified" in hso["additionalContext"], result
    store = root / ".modding-agent-harness/backups/saves"
    records = list((store / "transactions").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    members = [m for group in record["groups"] for m in group["members"]]
    assert len(members) == 1 and os.path.samefile(members[0]["path"], files[0])
    if "\\\\?\\" in prefix:
        assert members[0]["path"].startswith("\\\\?\\"), members
    assert (store / "blobs" / members[0]["sha256"]).read_bytes() == original
    assert files[0].read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows extended drive spelling")
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_missing_save_under_native_drive_namespace_does_not_require_a_backup(install, prefix):
    root, roots, _files = install
    target = Path(roots["saves"]) / "Missing.xml.gz"
    result = invoke_hook(install, "PowerShell", {"command": "Remove-Item -LiteralPath '" + prefix + str(target) + "'"})
    # C1 (v4.0 release review): an absent delete target is no longer a "save" that lifts the
    # profile ask -- it keeps it. Still no backup, no deny, and the `?` is not a wildcard.
    assert result["hookSpecificOutput"].get("permissionDecision") == "ask", result
    assert_scope_refusal(result["hookSpecificOutput"], "X4 PROFILE")
    assert not target.exists()
    assert not list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive mounts are Windows-specific")
def test_native_file_hook_resolves_msys_save_path_without_cwd(install):
    root, roots, files = install
    path = files[0].as_posix()
    path = "/" + path[0].lower() + path[2:]
    result = invoke_hook(install, "Write", {"file_path": path}, omit_cwd=True)
    assert result["hookSpecificOutput"].get("permissionDecision") is None, result
    assert "backup verified" in result["hookSpecificOutput"]["additionalContext"]
    records = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert [Path(m["path"]) for g in record["groups"] for m in g["members"]] == [files[0]]
    assert files[0].read_bytes() == files[0].name.encode()


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
    # K3: a preview preserves nothing, so it asks rather than advising an unbacked change.
    assert result["hookSpecificOutput"]["permissionDecision"] == "ask", result
    assert "NOT BACKED UP" in result["hookSpecificOutput"]["permissionDecisionReason"]
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
    assert_scope_refusal(result["hookSpecificOutput"])          # T2
    assert all(f.exists() for f in files)


ENGINE_FAILURES = ("policy engine failed", "X4 save dispatcher", "GUARD INERT", "did not return a complete decision",
                   "preservation/policy failed")


def assert_scope_refusal(hso, *needles):
    """A deny or ask that is the POLICY's answer, never an engine failure wearing its verdict."""
    reason = hso.get("permissionDecisionReason", "")
    assert not any(failure in reason for failure in ENGINE_FAILURES), reason
    for needle in needles:
        assert needle in reason, reason


def _with_content(install):
    content = Path(install[1]["profile"]) / "content.xml"
    content.write_text("<content/>")
    return content


@pytest.mark.parametrize("tail", [
    ' $X',                                  # an unresolved operand naming no root
    ' "$(cat list)"',                        # a substitution operand
    '; $(echo rm) "{content}"',              # a substituted command name
    '; python -c "import os; os.remove(\'{content}\')"',   # an interpreter no row models
    '; rm "{content}"',                      # a resolved non-save profile file
    '; bash -c "$CMD"',                      # a carrier whose program is not text held here
    '; eval "$X"',
    '; bash ./cleanup.sh',                   # a script file
    # I1: a carrier that also runs startup code its text never shows.
    "; BASH_ENV=./e.sh bash -c 'true'",
    '; env BASH_ENV=./e.sh bash -c true',
    '; bash --rcfile ./e.sh -i -c true',
    '; bash -lc true',
])
def test_K1_one_backed_up_save_never_lifts_the_ask_for_what_the_backup_cannot_cover(install, tail):
    """K1 (v4.0 release review): the profile ask was lifted for the WHOLE command once its
    resolved rows were one save. MEASURED before the fix: each of the first four advised."""
    root, _roots, files = install
    content = _with_content(install)
    command = 'rm "' + str(files[0]) + '"' + tail.replace("{content}", str(content).replace("\\", "/"))
    hso = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert hso.get("permissionDecision") == "ask", hso
    assert_scope_refusal(hso, "X4 PROFILE")
    assert content.exists() and files[0].exists()


def test_K1_TWIN_one_and_two_backed_up_saves_still_proceed_and_three_need_typed_approval(install):
    root, _roots, files = install
    _with_content(install)
    for count in (1, 2):
        command = "rm -- " + " ".join('"' + str(f) + '"' for f in files[:count])
        hso = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
        assert hso.get("permissionDecision") is None, (count, hso)
        assert "backup verified" in hso["additionalContext"], (count, hso)
    # A carrier whose program IS literal text is walked like the top level (cmd //c, bash -c).
    hso = invoke_hook(install, "Bash", {"command": "bash -c 'rm -- \"" + str(files[1]).replace(chr(92), "/") + "\"'"})[
        "hookSpecificOutput"]
    assert hso.get("permissionDecision") is None and "backup verified" in hso["additionalContext"], hso
    hso = invoke_hook(install, "Bash", {"command": "rm -- " + " ".join('"' + str(f) + '"' for f in files)})[
        "hookSpecificOutput"]
    assert hso["permissionDecision"] in ("ask", "deny"), hso
    assert_scope_refusal(hso, "SAVE_ACCESS_APPROVAL")


@pytest.mark.parametrize("operands", [
    "SV/s{1,2}.xml.gz",                        # 2 saves, brace list
    "SV/s{1,2,3}.xml.gz",                      # 3 saves, brace list
    "SV/s{1..3}.xml.gz",                       # a sequence
    'SV/{s1,s2}.xml.gz',                       # the brace spans the name
    '"SV/s1.xml.gz" SV/s{2,3}.xml.gz',         # mixed: one literal (backed up), two braced
    'SV/s1.xml.gz{,}',                         # one save named twice
])
def test_C1_brace_expanded_save_operands_keep_the_profile_ask(install, operands):
    """C1 (v4.0 release review), MEASURED before the fix: `rm <saves>/save_00{0,1,2}.xml.gz`
    ADVISED with 0 backups (v3.3.1 asked) -- the literal `{...}` word reached the save bridge as
    ONE save that did not exist; the mixed form printed "Save backup verified" while two were
    unbacked. A word bash expands into several paths must never lift the profile ask."""
    root, roots, _files = install
    saves = Path(roots["saves"])
    named = [saves / ("s%d.xml.gz" % n) for n in (1, 2, 3)]
    for f in named:
        f.write_bytes(f.name.encode())
    command = "rm -- " + operands.replace("SV", str(saves).replace(chr(92), "/"))
    hso = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert hso.get("permissionDecision") in ("ask", "deny"), (command, hso)
    assert_scope_refusal(hso)
    assert "backup verified" not in hso.get("permissionDecisionReason", ""), hso
    assert all(f.exists() for f in named)


@pytest.mark.parametrize("spelling", ["'{saves}/x{a,b}.xml.gz'", '"{saves}/x{a,b}.xml.gz"',
                                      "{saves}/x'{a,b}'.xml.gz", "{saves}/x" + chr(92) + "{a,b" + chr(92) + "}.xml.gz"])
def test_C1_TWIN_a_quoted_brace_is_a_literal_name_and_is_backed_up(install, spelling):
    """A QUOTED or escaped `{a,b}` is one literal filename: when that file exists it is one save,
    backed up and advised exactly like any other single save."""
    root, roots, _files = install
    target = Path(roots["saves"]) / "x{a,b}.xml.gz"
    target.write_bytes(b"literal")
    command = "rm -- " + spelling.replace("{saves}", str(roots["saves"]).replace(chr(92), "/"))
    hso = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None, (command, hso)
    assert "backup verified" in hso["additionalContext"], hso
    assert target.exists()


@pytest.mark.parametrize("command", ["rm -- SV/x{a,b}.xml.gz", "printf '' | tee SV/x{a,b}.xml.gz"])
def test_C1_an_UNQUOTED_brace_is_unresolved_even_when_its_literal_name_exists(install, command):
    """The clause twin of the existence check: here the literal `x{a,b}.xml.gz` EXISTS (so an
    existence test passes), but bash runs on xa/xb -- and a tee WRITE has no existence test at
    all. Only hook_facts marking the unquoted brace word unresolved keeps these asks."""
    root, roots, _files = install
    saves = Path(roots["saves"])
    for name in ("x{a,b}.xml.gz", "xa.xml.gz", "xb.xml.gz"):
        (saves / name).write_bytes(b"s")
    hso = invoke_hook(install, "Bash", {"command": command.replace("SV", str(saves).replace(chr(92), "/"))}
                      )["hookSpecificOutput"]
    assert hso.get("permissionDecision") in ("ask", "deny"), hso
    assert_scope_refusal(hso)


def test_C1_an_absent_delete_target_keeps_the_ask_and_an_existing_one_TWIN_is_backed_up(install):
    root, roots, files = install
    sv = str(roots["saves"]).replace(chr(92), "/")
    hso = invoke_hook(install, "Bash", {"command": 'rm -- "' + sv + '/absent.xml.gz"'})["hookSpecificOutput"]
    assert hso.get("permissionDecision") == "ask", hso
    hso = invoke_hook(install, "Bash", {"command": 'rm -- "' + str(files[0]).replace(chr(92), "/") + '"'}
                      )["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None and "backup verified" in hso["additionalContext"], hso


def _count_analyser_runs(hooks, marker):
    """Make the copied hook_facts.py record every run as a SCRIPT (an import does not count)."""
    source = hooks / "hook_facts.py"
    text = source.read_text(encoding="utf-8")
    tail = 'if __name__ == "__main__":\n    raise SystemExit(main())'
    assert text.rstrip().endswith(tail)
    text = text.rstrip()[:-len(tail)] + ('if __name__ == "__main__":\n    open(' + repr(str(marker)) +
                                         ', "a").write("run ")\n    raise SystemExit(main())\n')
    source.write_bytes(text.encode("utf-8"))


@pytest.mark.parametrize("tool,command", [("Bash", "ls -la"), ("Bash", 'rm "{save}"'),
                                          ("PowerShell", "Get-ChildItem .")])
def test_K5_the_native_hook_analyses_the_command_once(install, tool, command):
    """K5: the save bridge ran hook_facts for its evidence and the Bash backend ran it AGAIN in
    a second process (PowerShell translated twice) -- MEASURED 0.99 s vs 0.47 s before the
    bridge. The backend now takes the bridge's bytes (--facts-stdin)."""
    if tool == "PowerShell" and not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("no PowerShell to translate with")
    root, _roots, files = install
    invoke_hook(install, "Bash", {"command": "true"})          # installs the hook copy
    marker = root / "analyser-runs.txt"
    _count_analyser_runs(root / ".claude/hooks", marker)
    hooks = root / ".claude/hooks"
    command = command.replace("{save}", str(files[0]).replace("\\", "/"))
    spec = importlib.util.spec_from_file_location("fixture_x4guard2", hooks / "x4guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, _why = guard.resolve_bash()
    env = dict(os.environ, X4_TOOLKIT=str(root), X4_PROFILE=_roots["profile"], X4_SAVES=_roots["saves"],
               X4_GAME=str(root.parent / "game"), X4_REFERENCE=str(root / "reference"),
               X4_CONFIG=str(root / "owned-missing.env"), X4_MODS=str(root / "dev"),
               X4_DOCUMENTS=str(root.parent / "documents"), X4_PYTHON=sys.executable)
    env.pop("X4_GUARD", None)
    env.pop("X4_GUARD_CHECK", None)
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(root),
                          "session_id": "s"}).encode()
    native = subprocess.run([bash, str(hooks / "protect-bash.sh")], input=payload, capture_output=True,
                            env=env, timeout=60)
    assert native.returncode == 0, native.stderr
    assert not marker.exists(), "the backend re-ran the analyser: " + marker.read_text()
    # Twin: the backend alone (no precomputed facts) still analyses -- the marker can go red.
    alone = subprocess.run([bash, str(hooks / "protect-bash.sh"), "--harness-policy-backend"], input=payload,
                           capture_output=True, env=env, timeout=60)
    assert alone.returncode == 0 and marker.read_text() == "run ", alone.stderr


def test_K4_a_missing_harness_names_the_fix_and_only_profile_changes_ask(install, tmp_path):
    """K4: with X4_TOOLKIT naming a folder without tools/modding_harness EVERY Bash/PowerShell/
    Edit/Write call was denied "X4 save dispatcher failed: ValueError", and X4_GUARD=off did not
    help. The Bash rules need no harness; only save preservation does."""
    root, roots, files = install
    invoke_hook(install, "Bash", {"command": "true"})            # installs the hook copy
    (root / "tools/modding_harness").rename(tmp_path / "harness-moved-away")
    other = root / "notes.txt"
    hso = invoke_hook(install, "Bash", {"command": "ls -la"})["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None and "tools/modding_harness" in hso["additionalContext"], hso
    hso = invoke_hook(install, "Write", {"file_path": str(other), "content": "x"})["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None and "X4_TOOLKIT" in hso["additionalContext"], hso
    for tool, ti in (("Bash", {"command": 'rm -- "' + str(files[0]).replace("\\", "/") + '"'}),
                     ("Write", {"file_path": str(files[0]), "content": "x"})):
        hso = invoke_hook(install, tool, ti)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask", (tool, hso)
        assert "tools/modding_harness" in hso["permissionDecisionReason"], hso
        assert "X4_TOOLKIT" in hso["permissionDecisionReason"], hso
    assert files[0].read_bytes() == files[0].name.encode()
    # Twin: a hard block needs no harness and still denies with its own reason.
    hso = invoke_hook(install, "Bash", {"command": 'rm -rf "' + str(root.parent / "game").replace("\\", "/") + '"'}
                      )["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny", hso
    assert_scope_refusal(hso)


def test_K8_the_typed_approval_line_comes_first_and_the_message_is_bounded(install):
    """K8: the approval refusal listed every path unbounded, after the explanation; above the
    hook-output cap (#38) the harness files it and Claude sees only the head."""
    root, roots, files = install
    saves = Path(roots["saves"])
    many = [saves / ("campaign-" + "x" * 60 + "-%03d.xml.gz" % i) for i in range(150)]
    for path in many:
        path.write_bytes(b"save")
    hso = invoke_hook(install, "Bash", {"command": "rm -- " + " ".join(
        '"' + str(p).replace("\\", "/") + '"' for p in many)})["hookSpecificOutput"]
    assert hso["permissionDecision"] in ("ask", "deny"), hso
    reason = hso["permissionDecisionReason"]
    assert_scope_refusal(hso)
    assert reason.startswith("SAVE APPROVAL REQUIRED"), reason[:200]
    assert reason.split("\n")[1].startswith("SAVE_ACCESS_APPROVAL "), reason[:300]
    assert len(reason) <= 10000, len(reason)
    assert all(p.exists() for p in many)
    # Twin: a grant line that fits is delivered whole and parses.
    hso = invoke_hook(install, "Bash", {"command": "rm -- " + " ".join(
        '"' + str(f).replace("\\", "/") + '"' for f in files)})["hookSpecificOutput"]
    line = hso["permissionDecisionReason"].split("\n")[1]
    grant = json.loads(line[len("SAVE_ACCESS_APPROVAL "):])
    assert grant["op"] == "delete" and len(grant["paths"]) == 3


def test_H1_TWIN_the_native_hook_still_honours_a_typed_approval_in_the_host_transcript(install):
    """The CLI refuses a caller-named transcript (H1); the NATIVE hook reads the one the host
    names in its own hook input, so a typed bulk approval there still authorizes the batch."""
    root, _roots, files = install
    approval = policy.core(root, "approval")
    command = "rm -- " + " ".join('"' + str(f).replace("\\", "/") + '"' for f in files)
    refused = invoke_hook(install, "Bash", {"command": command})["hookSpecificOutput"]
    grant = next(line for line in refused["permissionDecisionReason"].splitlines()
                 if line.startswith(approval.KEY + " "))
    transcript = root.parent / "host-transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"}, "sessionId": "s",
                                      "message": {"role": "user", "content": grant}}))
    hso = invoke_hook(install, "Bash", {"command": command},
                      extra={"transcript_path": str(transcript)})["hookSpecificOutput"]
    assert hso.get("permissionDecision") is None, hso
    assert "backup verified" in hso["additionalContext"]


# ------------------------------------------------------------------ LINUX GAP (POSIX semantics)
# CI ubuntu failed 8 hook tests: native() used the host's os.path.isabs while hook_facts treats
# `\tmp\...` and `C:/...` as absolute everywhere, so PowerShell/cmd-spelled paths raised "no
# absolute physical path" (deny), and `<save>\a.xml.gz` from cmd was never a save. Each
# evidence row now records the DIALECT that spelled it; on POSIX only a Windows dialect's
# backslash separates. These run the POSIX branch on any host (it is injectable).
BSL = chr(92)


@pytest.mark.parametrize("path,dialect,want", [
    (BSL + "tmp" + BSL + "s" + BSL + "save" + BSL + "a.xml.gz", "windows", "/tmp/s/save/a.xml.gz"),
    ("/tmp/s/save" + BSL + "a.xml.gz", "windows", "/tmp/s/save/a.xml.gz"),
    ("/tmp/s/save" + BSL + "a.xml.gz", "bash", "/tmp/s/save" + BSL + "a.xml.gz"),   # a filename char
    ("/tmp/s/save/a.xml.gz", "bash", "/tmp/s/save/a.xml.gz"),
])
def test_LINUX_native_text_on_posix_converts_only_a_windows_dialect(path, dialect, want):
    assert policy.native_text(path, dialect, posix=True) == want


@pytest.mark.parametrize("path,dialect,error", [
    ("C:/work/x/build", "windows", "ForeignPath"),
    ("C:" + BSL + "work", "windows", "ForeignPath"),
    (BSL + BSL + "server" + BSL + "share", "windows", "ForeignPath"),
    (BSL + "tmp" + BSL + "x", "bash", "ValueError"),          # Bash: a relative filename
    ("relative/a.xml.gz", "bash", "ValueError"),
])
def test_LINUX_native_text_on_posix_never_guesses_a_windows_anchor(path, dialect, error):
    with pytest.raises(policy.ForeignPath if error == "ForeignPath" else ValueError) as caught:
        policy.native_text(path, dialect, posix=True)
    assert (error == "ForeignPath") == isinstance(caught.value, policy.ForeignPath)


def test_LINUX_TWIN_native_text_on_windows_is_unchanged(tmp_path):
    if os.name != "nt":
        pytest.skip("the Windows branch needs a Windows host (MSYS conversion)")
    assert policy.native_text("C:/work/a.xml", "windows", posix=False) == "C:/work/a.xml"
    with pytest.raises(ValueError):
        policy.native_text(BSL + "tmp" + BSL + "x", "windows", posix=False)


@pytest.fixture
def posix_facts(monkeypatch):
    sys.path.insert(0, str(REPO / "agent/guards/claude-hooks"))
    try:
        import hook_facts
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(hook_facts, "POSIX_HOST", True)
    return hook_facts


def _posix_rows(hook_facts, tool, command):
    t = "/tmp/psfe_x"
    roots = {"game": t + "/X4 Foundations", "reference": t + "/reference", "toolkit": t + "/tk",
             "profile": t + "/Documents/Egosoft/X4/123", "mods": t + "/tk/dev", "documents": t + "/Documents",
             "saves": t + "/Documents/Egosoft/X4/123/save", "config": ""}
    evidence = []
    facts = hook_facts.facts({"tool_name": tool, "tool_input": {"command": command}, "cwd": "/tmp/w"},
                             roots, evidence=evidence)
    assert not facts.get("powershell_error") and not facts.get("carrier_untranslated"), facts
    out = []
    for row in evidence:
        try:
            out.append((row["dialect"], policy.native_text(row["path"], row["dialect"], posix=True)))
        except policy.ForeignPath:
            out.append((row["dialect"], "FOREIGN"))
    return out


SAVE_W = BSL.join(["", "tmp", "psfe_x", "Documents", "Egosoft", "X4", "123", "save"])
WANT_SAVE = "/tmp/psfe_x/Documents/Egosoft/X4/123/save/a.xml.gz"


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="no PowerShell to translate with")
@pytest.mark.parametrize("command,want", [
    ("$p=@{LiteralPath='" + SAVE_W + BSL + "a.xml.gz'}; Remove-Item @p", [("windows", WANT_SAVE)]),
    ("$items = Get-ChildItem '" + SAVE_W + "'; $items | Remove-Item",
     [("windows", "/tmp/psfe_x/Documents/Egosoft/X4/123/save/*")]),
])
def test_LINUX_powershell_paths_are_windows_dialect_and_resolve_on_posix(posix_facts, command, want):
    assert _posix_rows(posix_facts, "PowerShell", command) == want


@pytest.mark.parametrize("tool,command,want", [
    ("Bash", 'cmd //c del /q "%X4_SAVES%' + BSL + 'a.xml.gz"', [("windows", WANT_SAVE)]),
    ("Bash", 'cmd //c "cd /d C:' + BSL + 'work' + BSL + 'x && rd /s /q build"', [("windows", "FOREIGN")]),
    # Twins: Bash text keeps its backslash as a filename character on POSIX.
    ("Bash", "rm '/tmp/psfe_x/Documents/Egosoft/X4/123/save" + BSL + "a.xml.gz'",
     [("bash", "/tmp/psfe_x/Documents/Egosoft/X4/123/save" + BSL + "a.xml.gz")]),
    ("Bash", "cd /tmp/w && rm 'a" + BSL + "b'", [("bash", "/tmp/w/a" + BSL + "b")]),
])
def test_LINUX_cmd_carrier_paths_are_windows_dialect_and_bash_paths_are_not(posix_facts, tool, command, want):
    assert _posix_rows(posix_facts, tool, command) == want

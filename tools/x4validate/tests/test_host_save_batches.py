"""Actual rendered adapters, preserving one request as a single authorization scope."""
import importlib.util
import json
import sys
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codex_testlib import REPO, make_sandbox, native, run_adapter
from test_opencode_adapter import call as open_call, run as open_run


@pytest.mark.parametrize('shape', ['bash-syntax', 'guards-off-syntax', 'powershell-syntax'])
def test_claude_check_protocol_distinguishes_failed_evaluation_from_a_policy_verdict(tmp_path, shape):
    tmp, root, env = make_sandbox(tmp_path)
    if shape == 'powershell-syntax' and not (shutil.which('pwsh') or shutil.which('powershell')):
        pytest.skip('PowerShell unavailable; native malformed-command check not qualified')
    command = "Write-Output 'unterminated" if shape == 'powershell-syntax' else "echo 'unterminated"
    payload = {'tool_name': 'PowerShell' if shape == 'powershell-syntax' else 'Bash',
               'tool_input': {'command': command}, 'cwd': str(root)}
    env.pop('X4_GUARD_CHECK', None)
    if shape == 'guards-off-syntax':
        env['X4_GUARD'] = 'off'
    else:
        env.pop('X4_GUARD', None)
    def run(values):
        return subprocess.run([shutil.which('bash'), str(REPO / '.claude/hooks/protect-bash.sh')],
                  input=json.dumps(payload).encode(), capture_output=True, env=values, timeout=35)
    native_result = run(env)
    assert native_result.returncode == 0
    assert json.loads(native_result.stdout)['hookSpecificOutput']['permissionDecision'] in ('deny', 'ask')
    checked = run(dict(env, X4_GUARD_CHECK='1'))
    assert checked.returncode == 2, checked.stdout
    assert not list((root / '.modding-agent-harness/backups/saves/transactions').glob('*.json'))


def test_claude_check_protocol_keeps_an_evaluated_protection_denial_as_a_verdict(tmp_path):
    _tmp, root, env = make_sandbox(tmp_path)
    env.update(X4_GUARD_CHECK='1')
    env.pop('X4_GUARD', None)
    payload = {'tool_name': 'Bash', 'tool_input': {'command': 'rm -rf "' + (root / 'reference').as_posix() + '"'}, 'cwd': str(root)}
    result = subprocess.run([shutil.which('bash'), str(REPO / '.claude/hooks/protect-bash.sh')],
              input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=35)
    assert result.returncode == 0, result.stderr
    hso = json.loads(result.stdout)['hookSpecificOutput']
    assert hso['permissionDecision'] == 'deny'
    # T2: an evaluated protection denial, not an engine failure that also says deny.
    assert not any(f in hso['permissionDecisionReason'] for f in (
        'policy engine failed', 'X4 save dispatcher', 'GUARD INERT', 'preservation/policy failed')), hso
    assert 'reference' in hso['permissionDecisionReason'], hso


@pytest.mark.parametrize('checking', [False, True])
def test_nonzero_worker_output_never_becomes_an_allow_and_preserves_check_failure(tmp_path, checking):
    _tmp, root, env = make_sandbox(tmp_path)
    worker = tmp_path / 'worker'
    worker.mkdir()
    (worker / 'save_policy.py').write_text("print('X4SAVE {}')\nraise SystemExit(3)\n")
    if checking:
        env['X4_GUARD_CHECK'] = '1'
    else:
        env.pop('X4_GUARD_CHECK', None)
    result = subprocess.run([shutil.which('bash'), '-c',
        '. "$3"; PY="$1"; HOOK_DIR="$2"; INPUT="{}"; x4_save_dispatch shell', '--',
        sys.executable, str(worker), str(REPO / '.claude/hooks/_x4-env.sh')],
        capture_output=True, env=env, timeout=20)
    assert result.returncode == (2 if checking else 0), result.stdout
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('defect', ['python-status', 'shell-propagation'])
def test_check_protocol_regressions_are_killed_in_an_owned_rendered_copy(tmp_path, defect):
    _tmp, root, env = make_sandbox(tmp_path)
    tree = root / '.claude/hooks'
    shutil.copytree(REPO / '.claude/hooks', tree, ignore=shutil.ignore_patterns('__pycache__'))
    env.update(X4_GUARD_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('X4_GUARD', None)
    payload = {'tool_name':'Bash', 'tool_input':{'command':"echo 'unterminated"}, 'cwd':str(root)}
    def run():
        return subprocess.run([shutil.which('bash'), str(tree / 'protect-bash.sh')],
                  input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=35)
    assert run().returncode == 2
    if defect == 'python-status':
        path = tree / 'save_policy.py'
        before = '    return 2 if failed and os.environ.get("X4_GUARD_CHECK") == "1" else 0'
        after = '    return 0'
    else:
        path = tree / '_x4-env.sh'
        before = '       x4_guard_check_inert\n       return 0 ;;'
        after = '       return 0 ;;'
    text = path.read_text()
    assert text.count(before) == 1
    path.write_text(text.replace(before, after))
    mutated = run()
    assert mutated.returncode == 0, mutated.stderr
    assert json.loads(mutated.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'


def prepare(tmp_path):
    tmp, root, env = make_sandbox(tmp_path)
    cfg = root / ".modding-agent-harness/config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"save_reserve_bytes": 0}))
    files = [Path(env["X4_SAVES"]) / (str(i) + ".xml.gz") for i in range(3)]
    for file in files:
        file.write_bytes(file.name.encode())
    return tmp, root, env, files


def patch(files):
    return "*** Begin Patch\n" + "".join("*** Update File: " + file.as_posix() +
        "\n@@\n-old\n+new\n" for file in files) + "*** End Patch"


def transactions(root):
    return list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_codex_batches_two_preserves_then_three_refuses_and_typed_grant_succeeds(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = native("apply_patch_multi", root, command=patch(files[:2]))
    assert run_adapter(env, payload)[0] in ("allow", "advise")
    records = transactions(root)
    assert len(records) == 1 and len(json.loads(records[0].read_text())["groups"]) == 2
    payload["tool_input"]["command"] = patch(files)
    assert run_adapter(env, payload)[0] == "deny"
    assert len(transactions(root)) == 1
    # Fixture provenance matches the qualified reader contract. This does not
    # claim a current running Codex TUI was intercepted.
    spec = importlib.util.spec_from_file_location("host_save_profile", REPO / "agent/guards/claude-hooks/save_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    line = policy.core(root, "approval").example("human-session", "write", files)
    transcript = tmp / "human.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"originator": "codex-tui", "id": "human-session"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": line}]}}}]))
    payload.update(session_id="human-session", transcript_path=str(transcript))
    assert run_adapter(env, payload)[0] in ("allow", "advise")
    assert len(transactions(root)) == 2
    assert all(file.read_bytes() == file.name.encode() for file in files)


def test_opencode_single_save_preserves_and_bulk_has_no_invented_human_reader(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = open_call(root, "edit", {"filePath": str(files[0]), "oldString": "old", "newString": "new"})
    result = open_run(env, payload)
    assert result["decision"] in ("allow", "advise"), result
    assert len(transactions(root)) == 1
    payload = open_call(root, "apply_patch", {"patchText": patch(files)})
    result = open_run(env, payload)
    assert result["decision"] == "deny" and "SAVE_ACCESS_APPROVAL" in result["reason"], result
    assert len(transactions(root)) == 1


def test_a_backend_denial_never_preserves_or_gets_overruled(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = native("apply_patch_multi", root, command=patch([files[0], root / "reference/libraries/wares.xml"]))
    assert run_adapter(env, payload)[0] == "deny"
    assert not transactions(root)


@pytest.mark.parametrize('host', ['codex', 'opencode'])
def test_named_adapter_file_paths_are_literal_with_bracketed_parents_and_names(tmp_path, host):
    tmp, root, env, _files = prepare(tmp_path)
    profile = tmp / 'profile [one]'
    folder = profile / 'save'
    folder.mkdir(parents=True)
    file = folder / 'Save[1].xml.gz'
    file.write_bytes(b'opaque literal path fixture')
    env.update(X4_PROFILE=str(profile), X4_SAVES=str(folder))
    if host == 'codex':
        payload = native('apply_patch_multi', root, command=patch([file]))
        assert run_adapter(env, payload)[0] in ('allow', 'advise')
    else:
        payload = open_call(root, 'edit', {'filePath': str(file), 'oldString': 'old', 'newString': 'new'})
        result = open_run(env, payload)
        assert result['decision'] in ('allow', 'advise'), result
    records = transactions(root)
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert [Path(m['path']).name for g in record['groups'] for m in g['members']] == [file.name]
    assert file.read_bytes() == b'opaque literal path fixture'

"""Judge command data only: Codex yielding execution is not Claude foreground Bash."""
import json
import subprocess
import sys

import pytest

from codex_testlib import REPO, make_sandbox, native, run_adapter


@pytest.mark.parametrize('command', ['bash build-effective.sh', 'uv run x4effective build'])
def test_codex_long_job_does_not_require_claude_background_flag(tmp_path, command):
    tmp, tk, env = make_sandbox(tmp_path)
    payload = native('bash_powershell', tk, command=command)
    payload['tool_input']['workdir'] = str(tk)
    decision, reason = run_adapter(env, payload, shell='bash')
    assert decision in ('allow', 'advise'), reason


@pytest.mark.parametrize('host,want', [('codex', 'allow'), ('claude', 'deny'), ('generic', 'deny')])
def test_neutral_entry_preserves_host_scheduling_contract(tmp_path, host, want):
    tmp, tk, env = make_sandbox(tmp_path)
    action = dict(api_version=1, host=host, tool='shell', surface='shell', shell='bash',
                  cwd=str(tk), command='bash build-effective.sh', session_id='scheduling-test')
    result = subprocess.run([sys.executable, str(REPO/'tools/agent-harness.py'), 'check'],
                            input=json.dumps(action).encode(), capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert not data.get('inert'), data
    assert data['result']['decision'] == want, data
    if want == 'deny':
        assert 'LONG JOB IN THE FOREGROUND' in data['result']['reason']


def test_codex_long_job_still_checks_destructive_command(tmp_path):
    tmp, tk, env = make_sandbox(tmp_path)
    command = f"bash build-effective.sh; rm -rf '{tk / 'reference'}'"
    payload = native('bash_powershell', tk, command=command)
    payload['tool_input']['workdir'] = str(tk)
    decision, reason = run_adapter(env, payload, shell='bash')
    assert decision == 'deny', reason
    assert 'reference' in reason.lower()
    assert 'LONG JOB IN THE FOREGROUND' not in reason

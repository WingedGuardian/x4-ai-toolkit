"""Native missing-cwd classification of shell options versus real writes.

Uses the existing real operand extractor and mocks only downstream enforcement.
Every command is data: no shell, script, network request or mutation executes.
PREPARED: expected old red is four option-only refusals, with eight safety twins.
"""
import sys

import pytest

from test_codex_execution_cwd import run_case

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows native execution-cwd contract; not qualified elsewhere')


@pytest.mark.parametrize('command,refused', [
    pytest.param('bash -o pipefail -n script.sh', False, id='syntax-option-file'),
    pytest.param("bash -o pipefail -n <(echo 'rm -rf C:/OwnedGame')", False, id='syntax-option-visible-text'),
    pytest.param('bash -o pipefail -n <(touch notes.txt)', True, id='process-substitution-actually-writes'),
    pytest.param('bash -o pipefail -n <(printf x > notes.txt)', True, id='process-substitution-redirect-writes'),
    pytest.param('bash -o pipefail -n script.sh > notes.txt', True, id='outer-redirection-writes'),
    pytest.param('curl -o notes.txt https://example.invalid/owned', True, id='actual-output-option-writes'),
    pytest.param('bash -o pipefail -n <(curl -o notes.txt https://example.invalid/owned)', True,
                 id='process-substitution-output-option-writes'),
    pytest.param("grep -o 'pattern' file.txt", False, id='search-option-stays-read-only'),
    pytest.param('env bash -o pipefail -n script.sh', False, id='wrapped-shell-option-stays-read-only'),
    pytest.param('curl -o pipefail https://example.invalid/owned', True, id='option-word-can-be-real-output-file'),
    pytest.param("bash -o pipefail -c 'curl -o notes.txt https://example.invalid/owned'", True,
                 id='nested-command-output-option-writes'),
    pytest.param('env -u bash bash -o pipefail -n script.sh', False, id='wrapper-value-named-bash'),
    pytest.param('env -u OWNED_UNUSED bash -o pipefail -n script.sh', False, id='wrapper-value-ordinary'),
    pytest.param('strace -o notes.txt bash -o pipefail -n script.sh', True, id='wrapper-output-is-still-a-write'),
    pytest.param('bash -eO extglob -o pipefail -n script.sh', False, id='clustered-shopt-before-option'),
    pytest.param('bash -eo pipefail -o errexit -n script.sh', False, id='clustered-option-before-option'),
    pytest.param('bash -o pipefail -- script.sh > notes.txt', True, id='option-terminator-preserves-outer-write'),
    pytest.param('bash "-x" -o pipefail -n script.sh', False, id='quoted-option-before-option'),
    pytest.param('A=$(echo bash) env -u bash bash -o pipefail -n script.sh', False, id='assignment-and-wrapper-value-named-bash'),
])
def test_shell_options_do_not_invent_mutations(tmp_path, command, refused):
    result, downstream_calls = run_case(tmp_path, 'bash', command)
    assert (result['decision'] == 'deny') is refused, (command, result)
    assert downstream_calls == (0 if refused else 1)
    if refused:
        assert 'execution workdir' in result['reason']


def test_syntax_only_visible_text_keeps_original_quote_grammar(tmp_path):
    # Same quote grammar as scripts/test-hooks.sh #225; the path is an owned
    # parser stand-in, not the live harness sandbox or a real game operation.
    command = 'bash -o pipefail -n <(echo rm -rf "C:/OwnedGame")'
    result, downstream_calls = run_case(tmp_path, 'bash', command)
    assert result['decision'] == 'allow', result
    assert downstream_calls == 1

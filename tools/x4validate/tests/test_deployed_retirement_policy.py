"""Typed deployed-mod removal confirms; writes and protected targets remain blocked."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from test_x4guard_check import sandbox, check
REPO = Path(__file__).resolve().parents[3]

@pytest.mark.parametrize('leaf', ['probe', 'probe/libraries/jobs.xml'])
@pytest.mark.parametrize('kind,expected', [('write', 'deny'), ('delete', 'ask')])
def test_deployed_removal_preserves_operation(sandbox, leaf, kind, expected):
    tmp, tk, env = sandbox
    target = tmp / 'X4 Foundations/extensions' / leaf
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix:
        target.write_text('<diff/>', encoding='utf-8')
    else:
        target.mkdir(exist_ok=True)
    rc, v, err = check(env, '--kind', kind, '--path', str(target))
    assert rc == 0 and (not v['inert']), (v, err)
    assert v['decision'] == expected, v

@pytest.mark.parametrize('kind,expected', [('write', 'deny'), ('delete', 'ask')])
def test_neutral_action_preserves_deployed_removal(sandbox, kind, expected):
    tmp, tk, env = sandbox
    target = tmp / 'X4 Foundations/extensions/probe'
    target.mkdir()
    action = dict(api_version=1, host='codex', tool='delete' if kind == 'delete' else 'edit', surface='files', cwd=str(tk), paths=[str(target)], operations=[kind])
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert not v.get('inert') and v['result']['evaluation'] == 'evaluated', v
    assert v['result']['decision'] == expected, v

@pytest.mark.parametrize('target', ['reference', 'game', 'extensions', 'basefile', 'packed'])
def test_deletion_keeps_protected_blocks(sandbox, target):
    tmp, tk, env = sandbox
    paths = {'reference': tk / 'reference/libraries/wares.xml', 'game': tmp / 'X4 Foundations', 'extensions': tmp / 'X4 Foundations/extensions', 'basefile': tmp / 'X4 Foundations/libraries/wares.xml', 'packed': tmp / 'X4 Foundations/extensions/probe/ext_01.cat'}
    rc, v, err = check(env, '--kind', 'delete', '--path', str(paths[target]))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'deny'), (v, err)

def test_native_write_cannot_forge_removal_metadata(sandbox):
    tmp, tk, env = sandbox
    payload = {'tool_name': 'Write', 'operation': 'delete', 'tool_input': {'file_path': str(tmp / 'X4 Foundations/extensions/probe/libraries/jobs.xml'), 'content': '<diff/>', 'operation': 'delete', 'tool_name': 'DeleteFile'}}
    script = REPO / '.claude/hooks/protect-files.sh'
    spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location('retirement_guard', REPO / '.claude/hooks/x4guard.py')
    guard = __import__('importlib.util', fromlist=['util']).module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, reason = guard.resolve_bash()
    assert bash, reason
    r = subprocess.run([bash, str(script), '--harness-policy-backend'], input=json.dumps(payload).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)['hookSpecificOutput']
    assert v['permissionDecision'] == 'deny', v

def test_mixed_batch_cannot_borrow_delete_semantics(sandbox):
    tmp, tk, env = sandbox
    target = tmp / 'X4 Foundations/extensions/probe'
    target.mkdir()
    write = target / 'libraries/jobs.xml'
    action = dict(api_version=1, host='codex', tool='patch', surface='files', cwd=str(tk), paths=[str(target), str(write)], operations=['delete', 'write'])
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v['result']['evaluation'] == 'evaluated' and v['result']['decision'] == 'deny', v

@pytest.mark.parametrize('ops,expected', [(['move', 'write'], 'deny'), (['delete', 'write'], 'ask'), (['delete', 'delete'], 'ask')])
def test_explicit_move_and_both_endpoints(sandbox, ops, expected):
    tmp, tk, env = sandbox
    source = tmp / 'X4 Foundations/extensions/probe'
    source.mkdir()
    destination = tmp / 'mods/retired'
    action = dict(api_version=1, host='codex', tool='move', surface='files', cwd=str(tk), paths=[str(source), str(destination)], operations=ops)
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v['result']['evaluation'] == 'evaluated' and v['result']['decision'] == expected, v
    assert source.is_dir() and (not destination.exists())

def test_move_destination_is_still_a_write(sandbox):
    tmp, tk, env = sandbox
    source = tmp / 'mods/source'
    source.mkdir()
    destination = tmp / 'X4 Foundations/extensions/probe'
    action = dict(api_version=1, host='codex', tool='move', surface='files', cwd=str(tk), paths=[str(source), str(destination)], operations=['delete', 'write'])
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v['result']['evaluation'] == 'evaluated' and v['result']['decision'] == 'deny', v

@pytest.mark.parametrize('leaf,expected', [('config.xml', 'ask'), ('nested/content.xml', 'ask')])
def test_profile_deletion_still_confirms(sandbox, leaf, expected):
    tmp, tk, env = sandbox
    rc, v, err = check(env, '--kind', 'delete', '--path', str(tmp / 'profile' / leaf))
    assert rc == 0 and (not v['inert']) and (v['decision'] == expected), (v, err)

def test_write_only_batch_cannot_inherit_delete_name(sandbox):
    tmp, tk, env = sandbox
    script = REPO / '.claude/hooks/protect-files.sh'
    spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location('retirement_guard_batch', REPO / '.claude/hooks/x4guard.py')
    guard = __import__('importlib.util', fromlist=['util']).module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, reason = guard.resolve_bash()
    assert bash, reason
    path = str(tmp / 'X4 Foundations/extensions/probe/libraries/jobs.xml')
    payload = {'tool_name': 'DeleteFile', 'tool_input': {'file_path': path, 'content': ''}}
    wire = ('1\x00' + path + '\x00' + json.dumps(payload) + '\x00').encode()
    r = subprocess.run([bash, str(script), '--harness-policy-backend', '--batch'], input=wire, capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    fields = r.stdout.decode().split('\x00')
    assert fields[:3] == ['X4FILE1', '1', 'deny'], fields

def test_deployed_delete_python_reader_fallback(sandbox):
    tmp, tk, env = sandbox
    # JQ is the name the hook reads. Without jq the branch that tells a delete from a write is the
    # Python reader; removing that reader turns this ask into the deployed-copy deny.
    env = dict(env, JQ=str(tmp / 'missing-jq'))
    rc, v, err = check(env, '--kind', 'delete', '--path', str(tmp / 'X4 Foundations/extensions/probe'))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'ask'), (v, err)
    assert 'REMOVING DEPLOYED MOD CONTENT' in v['reason'], v

def test_TWIN_python_reader_fallback_still_denies_a_deployed_write(sandbox):
    tmp, tk, env = sandbox
    env = dict(env, JQ=str(tmp / 'missing-jq'))
    rc, v, err = check(env, '--kind', 'write', '--path', str(tmp / 'X4 Foundations/extensions/probe/libraries/jobs.xml'))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'deny'), (v, err)

@pytest.mark.parametrize('leaf', ['reference/libraries/wares.xml', 'X4 Foundations'])
def test_delete_alias_keeps_actual_target_policy(sandbox, leaf):
    tmp, tk, env = sandbox
    actual = tk / 'reference/libraries/wares.xml' if leaf.startswith('reference') else tmp / 'X4 Foundations'
    alias = tmp / 'mods/alias'
    try:
        alias.symlink_to(actual, target_is_directory=actual.is_dir())
    except OSError as exc:
        pytest.skip('file/directory symlink privilege unavailable: ' + str(exc))
    rc, v, err = check(env, '--kind', 'delete', '--path', str(alias))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'deny'), (v, err)

def test_single_location_source_whitelist_is_unchanged(sandbox):
    tmp, tk, env = sandbox
    env = dict(env, X4_MODS=str(tmp / 'X4 Foundations/extensions'))
    rc, v, err = check(env, '--kind', 'write', '--path', str(tmp / 'X4 Foundations/extensions/probe/libraries/jobs.xml'))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'allow'), (v, err)

@pytest.mark.parametrize('field,value', [('content', '{}'), ('edit', {'old_string': 'a', 'new_string': 'b'}), ('command', 'patch-text')])
def test_delete_payload_preserves_existing_content_fields(field, value):
    spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location('retirement_guard_content', REPO / '.claude/hooks/x4guard.py')
    guard = __import__('importlib.util', fromlist=['util']).module_from_spec(spec)
    spec.loader.exec_module(guard)
    payload = guard.guard_payload('delete', None, None, '/project/.claude/settings.json', **{field: value}) if field != 'command' else guard.guard_payload('delete', None, value, '/project/.claude/settings.json', patch=True)
    assert payload['tool_name'] == 'DeleteFile'
    expected = {'content': 'content', 'edit': 'x4_edit', 'command': 'x4_written'}[field]
    assert payload['tool_input'][expected] == value
    if field == 'command':
        assert payload['tool_input']['x4_patch'] is True

def test_settings_deletion_retains_earlier_protection(sandbox):
    tmp, tk, env = sandbox
    rc, v, err = check(env, '--kind', 'delete', '--path', str(tk / '.claude/settings.json'))
    assert rc == 0 and (not v['inert']) and (v['decision'] == 'deny'), (v, err)

@pytest.fixture
def link_dir(request):
    # A junction needs no privilege; a POSIX symlink is the same shape. The link only is removed.
    def make(link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
            if r.returncode != 0:
                pytest.skip('directory junction unavailable: ' + repr(r.stderr))
        else:
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                pytest.skip('directory symlink unavailable: ' + str(exc))
        request.addfinalizer(lambda: os.rmdir(link) if os.name == 'nt' else os.unlink(link))
        return link
    return make

def _delete(env, path):
    rc, v, err = check(env, '--kind', 'delete', '--path', str(path))
    assert rc == 0 and (not v['inert']), (v, err)
    return v

def _installed(root):
    (root / 'probe/libraries').mkdir(parents=True)
    (root / 'probe/libraries/jobs.xml').write_text('<diff/>', encoding='utf-8')
    (root / 'ego_dlc_boron/libraries').mkdir(parents=True)
    return root

def test_delete_extensions_root_outside_game_denies(sandbox):
    tmp, tk, env = sandbox
    outside = _installed(tmp / 'ext-outside')
    env = dict(env, X4_EXTENSIONS=str(outside))
    assert _delete(env, outside)['decision'] == 'deny'
    assert _delete(env, outside / 'ego_dlc_boron')['decision'] == 'deny'

def test_TWIN_mod_in_extensions_root_outside_game_still_asks(sandbox):
    tmp, tk, env = sandbox
    outside = _installed(tmp / 'ext-outside')
    env = dict(env, X4_EXTENSIONS=str(outside))
    assert _delete(env, outside / 'probe')['decision'] == 'ask'
    assert _delete(env, outside / 'probe/libraries/jobs.xml')['decision'] == 'ask'

def test_delete_real_target_of_junctioned_extensions_denies(sandbox, link_dir):
    tmp, tk, env = sandbox
    real = _installed(tmp / 'real-ext')
    game = tmp / 'linked-game'
    linked = link_dir(game / 'extensions', real)
    env = dict(env, X4_GAME=str(game), X4_EXTENSIONS=str(linked))
    assert _delete(env, real)['decision'] == 'deny'
    assert _delete(env, real / 'ego_dlc_boron')['decision'] == 'deny'

def test_TWIN_mod_through_junctioned_extensions_still_asks(sandbox, link_dir):
    tmp, tk, env = sandbox
    real = _installed(tmp / 'real-ext')
    game = tmp / 'linked-game'
    linked = link_dir(game / 'extensions', real)
    env = dict(env, X4_GAME=str(game), X4_EXTENSIONS=str(linked))
    assert _delete(env, real / 'probe')['decision'] == 'ask'

@pytest.mark.parametrize('link', ['mods/alias', 'alias'])
def test_delete_junction_alias_of_extensions_denies(sandbox, link_dir, link):
    tmp, tk, env = sandbox
    extensions = tmp / 'X4 Foundations/extensions'
    assert _delete(env, link_dir(tmp / link, extensions))['decision'] == 'deny'

def test_delete_extensions_through_a_junction_to_the_game_denies(sandbox, link_dir):
    tmp, tk, env = sandbox
    game = tmp / 'X4 Foundations'
    jgame = link_dir(game / 'extensions/jgame', game)
    assert _delete(env, jgame / 'extensions')['decision'] == 'deny'
    assert _delete(env, jgame)['decision'] == 'deny'

def test_delete_junction_alias_of_a_dlc_folder_denies(sandbox, link_dir):
    tmp, tk, env = sandbox
    _installed(tmp / 'X4 Foundations/extensions')
    alias = link_dir(tmp / 'mods/alias', tmp / 'X4 Foundations/extensions/ego_dlc_boron')
    assert _delete(env, alias)['decision'] == 'deny'

def test_TWIN_junction_alias_of_a_mod_folder_still_asks(sandbox, link_dir):
    tmp, tk, env = sandbox
    _installed(tmp / 'X4 Foundations/extensions')
    alias = link_dir(tmp / 'mods/alias', tmp / 'X4 Foundations/extensions/probe')
    assert _delete(env, alias)['decision'] == 'ask'

@pytest.mark.parametrize('leaf', ['ego_dlc_boron', 'EGO_DLC_Boron', 'Ego_Dlc_terran', 'ego_dlc_mini_01', 'ego_dlc_boron/libraries/wares.xml', 'ego_dlc_boron/'])
def test_delete_dlc_folder_denies(sandbox, leaf):
    tmp, tk, env = sandbox
    _installed(tmp / 'X4 Foundations/extensions')
    assert _delete(env, str(tmp / 'X4 Foundations/extensions') + '/' + leaf)['decision'] == 'deny'

@pytest.mark.parametrize('leaf', ['ego_dlcfoo', 'my_ego_dlc_boron', 'boron', 'probe/ego_dlc_boron', 'probe/ego_dlc_boron/jobs.xml'])
def test_TWIN_a_mod_merely_named_like_a_dlc_still_asks(sandbox, leaf):
    tmp, tk, env = sandbox
    assert _delete(env, tmp / 'X4 Foundations/extensions' / leaf)['decision'] == 'ask'

@pytest.mark.parametrize('leaf,expected', [('ego_dlc_boron', 'deny'), ('probe', 'ask')])
def test_single_location_keeps_dlc_and_mod_policy(sandbox, leaf, expected):
    tmp, tk, env = sandbox
    env = {k: v for k, v in env.items() if k != 'X4_MODS'}
    assert _delete(env, tmp / 'X4 Foundations/extensions' / leaf)['decision'] == expected

@pytest.mark.parametrize('leaf,expected', [('ego_dlc_boron', 'deny'), ('probe', 'ask')])
def test_neutral_action_keeps_dlc_and_mod_policy(sandbox, leaf, expected):
    tmp, tk, env = sandbox
    target = tmp / 'X4 Foundations/extensions' / leaf
    action = dict(api_version=1, host='codex', tool='delete', surface='files', cwd=str(tk), paths=[str(target)], operations=['delete'])
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v['result']['evaluation'] == 'evaluated' and v['result']['decision'] == expected, v

@pytest.mark.skipif(os.name != 'nt', reason='a Windows-form path resolves through links only under Git Bash')
def test_delete_with_an_unresolvable_extensions_root_is_not_a_retirement_ask(sandbox, link_dir):
    tmp, tk, env = sandbox
    linked = link_dir(tmp / 'jext', _installed(tmp / 'real-ext'))
    # Only a linked path goes through realpath, so a failing one fails only the ROOT. A function
    # in the same shell, because Git Bash puts its own /usr/bin ahead of any stub on PATH.
    spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location('retirement_guard_root', REPO / '.claude/hooks/x4guard.py')
    guard = __import__('importlib.util', fromlist=['util']).module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, reason = guard.resolve_bash()
    assert bash, reason
    payload = {'tool_name': 'DeleteFile', 'tool_input': {'file_path': str(tmp / 'elsewhere/notes.xml'), 'content': ''}}
    r = subprocess.run([bash, '-c', 'realpath() { return 1; }; . "$0" --harness-policy-backend', str(REPO / '.claude/hooks/protect-files.sh')], input=json.dumps(payload).encode(), capture_output=True, env=dict(env, X4_EXTENSIONS=str(linked)), cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)['hookSpecificOutput']
    assert v['permissionDecision'] == 'deny', v

# 4.1.1 (user ruling): with X4_MODS pointed AT the extensions folder (the single-location layout
# spelled out), the mod-root whitelist returned before the deployed-delete block, so a typed
# delete of the whole extensions folder or of a DLC was not denied. Both deny; a mod keeps its ask.
@pytest.mark.parametrize('leaf', ['', 'ego_dlc_boron', 'EGO_DLC_Boron', 'ego_dlc_boron/libraries', 'ego_dlc_boron/content.xml'])
def test_mods_root_at_extensions_still_denies_extensions_and_dlc_deletes(sandbox, leaf):
    tmp, tk, env = sandbox
    ext = _installed(tmp / 'X4 Foundations/extensions')
    env = dict(env, X4_MODS=str(ext))
    target = str(ext) + ('/' + leaf if leaf else '')
    assert _delete(env, target)['decision'] == 'deny', target

@pytest.mark.parametrize('leaf', ['probe', 'probe/libraries/jobs.xml', 'probe/ego_dlc_boron'])
def test_TWIN_mods_root_at_extensions_keeps_the_mod_delete_ask(sandbox, leaf):
    tmp, tk, env = sandbox
    ext = _installed(tmp / 'X4 Foundations/extensions')
    env = dict(env, X4_MODS=str(ext))
    assert _delete(env, ext / leaf)['decision'] == 'ask', leaf

def test_TWIN_mods_root_at_extensions_keeps_ordinary_writes_allowed(sandbox):
    """The whitelist still holds for an edit: only a typed delete of the root or a DLC leaves it."""
    tmp, tk, env = sandbox
    ext = _installed(tmp / 'X4 Foundations/extensions')
    env = dict(env, X4_MODS=str(ext))
    rc, v, err = check(env, '--kind', 'write', '--path', str(ext / 'probe/libraries/jobs.xml'))
    assert rc == 0 and (not v['inert']) and v['decision'] == 'allow', (v, err)

def _file_guard_alone(env, tk, path):
    """protect-files.sh by itself, as the file-tool channel runs it (no shell guard to mask it)."""
    spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location('retirement_guard_alone', REPO / '.claude/hooks/x4guard.py')
    guard = __import__('importlib.util', fromlist=['util']).module_from_spec(spec)
    spec.loader.exec_module(guard)
    bash, reason = guard.resolve_bash()
    assert bash, reason
    payload = {'tool_name': 'DeleteFile', 'tool_input': {'file_path': str(path)}}
    r = subprocess.run([bash, str(REPO / '.claude/hooks/protect-files.sh'), '--harness-policy-backend'], input=json.dumps(payload).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode().strip()
    return json.loads(out)['hookSpecificOutput'].get('permissionDecision', 'advise') if out else 'allow'

@pytest.mark.parametrize('leaf,expected', [('', 'deny'), ('ego_dlc_boron', 'deny'), ('ego_dlc_boron/content.xml', 'deny'), ('probe', 'ask'), ('probe/libraries/jobs.xml', 'ask')])
def test_mods_root_at_extensions_file_guard_alone(sandbox, leaf, expected):
    tmp, tk, env = sandbox
    ext = _installed(tmp / 'X4 Foundations/extensions')
    env = dict(env, X4_MODS=str(ext))
    target = str(ext) + ('/' + leaf if leaf else '')
    assert _file_guard_alone(env, tk, target) == expected, target

@pytest.mark.parametrize('leaf,expected', [('', 'deny'), ('ego_dlc_boron', 'deny'), ('probe', 'ask')])
def test_mods_root_at_extensions_neutral_delete(sandbox, leaf, expected):
    tmp, tk, env = sandbox
    ext = _installed(tmp / 'X4 Foundations/extensions')
    env = dict(env, X4_MODS=str(ext))
    target = str(ext) + ('/' + leaf if leaf else '')
    action = dict(api_version=1, host='codex', tool='delete', surface='files', cwd=str(tk), paths=[target], operations=['delete'])
    r = subprocess.run([sys.executable, str(REPO / 'tools/agent-harness.py'), 'check'], input=json.dumps(action).encode(), capture_output=True, env=env, cwd=tk, timeout=120)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)
    assert v['result']['evaluation'] == 'evaluated' and v['result']['decision'] == expected, (target, v)

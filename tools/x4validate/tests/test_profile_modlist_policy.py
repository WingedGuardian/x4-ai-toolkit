"""Mod-list creation/editing is ordinary advisory XML, independent of profile selection."""
import json
from pathlib import Path
import pytest
from test_file_guard_batch import load_guard
from test_generic_file_batch import candidate
from codex_testlib import REPO

@pytest.fixture
def guard(monkeypatch):
    # Shell guards intentionally refuse writes under shared /tmp. Use the same
    # project-owned sandbox convention as test-hooks.sh on every host.
    import shutil
    import tempfile
    base = REPO / '.test-sandbox'
    base.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='profile-modlist-', dir=base))
    roots = {
        'X4_TOOLKIT': REPO, 'X4_GAME': root / 'game',
        'X4_REFERENCE': root / 'reference', 'X4_MODS': root / 'dev',
        'X4_PROFILE': root / 'profile', 'X4_EXTENSIONS': root / 'game/extensions',
        'X4_DOCUMENTS': root / 'Documents', 'X4_SAVES': root / 'profile/save',
        'X4_CONFIG': root / 'no-config',
    }
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('X4_GUARD', raising=False)
    try:
        yield load_guard(), roots
    finally:
        assert root.resolve().is_relative_to(base.resolve())
        shutil.rmtree(root)

@pytest.mark.parametrize("existing",[False,True])
def test_secondary_profile_modlist_is_advisory(guard,tmp_path,existing):
    g,roots=guard
    path=tmp_path/"Documents/Egosoft/X4/12345678/content.xml"
    if existing:
        path.parent.mkdir(parents=True);path.write_text("<content/>",encoding="utf-8")
    reply=g.verdict_for("write",None,None,str(path),save_qualified=False)
    assert reply["decision"]=="advise" and not reply["inert"],reply

@pytest.mark.parametrize("relative",["config.xml","save/content.xml","nested/content.xml","content.xml/child.xml"])
def test_other_secondary_profile_files_still_confirm(guard,tmp_path,relative):
    g,roots=guard
    path=tmp_path/"Documents/Egosoft/X4/12345678"/relative
    reply=g.verdict_for("write",None,None,str(path),save_qualified=False)
    assert reply["decision"]=="ask" and not reply["inert"],reply

def test_whole_action_new_modlist_plus_dev_files(candidate):
    root,env,check=candidate
    target=Path(env["X4_DOCUMENTS"])/"Egosoft/X4/12345678/content.xml"
    reply,_=check([root/"dev/probe/aiscripts/trace.xml",root/"dev/probe/md/trace.xml",target])
    assert reply["result"]["decision"]=="allow" and reply["result"]["evaluation"]=="evaluated" and not reply["inert"],reply
    assert not target.exists() and not (root/"dev").exists(),"check cannot execute writes"

def test_modlist_cannot_lift_hard_block_in_same_action(candidate):
    root,env,check=candidate
    target=Path(env["X4_DOCUMENTS"])/"Egosoft/X4/12345678/content.xml"
    reply,_=check([target,Path(env["X4_REFERENCE"])/"content.xml"])
    assert reply["result"]["decision"]=="deny" and not reply["inert"],reply

@pytest.mark.parametrize("name",["content.xml","CONTENT.XML"])
def test_configured_profile_modlist_shell_write_is_advisory(guard,name):
    g,roots=guard
    target=roots["X4_PROFILE"]/name
    command="echo fixture > '"+str(target)+"'"
    reply=g.verdict_for("shell","bash",command,None,save_qualified=False)
    assert reply["decision"] in ("allow","advise") and not reply["inert"],reply

def test_modlist_shell_write_cannot_cover_config_write(guard):
    g,roots=guard
    base=str(roots["X4_PROFILE"])
    command="echo fixture > '"+base+"/content.xml'; echo fixture > '"+base+"/config.xml'"
    reply=g.verdict_for("shell","bash",command,None,save_qualified=False)
    assert reply["decision"]=="ask" and not reply["inert"],reply

def test_modlist_shell_delete_keeps_general_profile_confirmation(guard):
    g,roots=guard
    command="r"+"m '"+str(roots["X4_PROFILE"]/"content.xml")+"'"
    reply=g.verdict_for("shell","bash",command,None,save_qualified=False)
    assert reply["decision"]=="ask" and not reply["inert"],reply

def test_numeric_profile_rule_survives_unset_active_profile(guard,tmp_path,monkeypatch):
    g,roots=guard
    monkeypatch.delenv("X4_PROFILE")
    target=tmp_path/"Documents/Egosoft/X4/12345678/content.xml"
    reply=g.verdict_for("write",None,None,str(target),save_qualified=False)
    assert reply["decision"]=="advise" and not reply["inert"],reply

@pytest.mark.parametrize("root_name",["X4_REFERENCE","X4_GAME"])
def test_modlist_basename_never_lifts_game_or_reference_block(guard,root_name):
    g,roots=guard
    reply=g.verdict_for("write",None,None,str(roots[root_name]/"content.xml"),save_qualified=False)
    assert reply["decision"]=="deny" and not reply["inert"],reply

def test_existing_modlist_preserves_save_transaction(candidate):
    root,env,check=candidate
    target=Path(env["X4_SAVES"])/"content.xml"
    target.parent.mkdir(parents=True);target.write_bytes(b"<save>unique</save>")
    reply,_=check([target])
    assert reply["result"]["preservation"]=="verified" and reply["transactions"],reply
    assert target.read_bytes()==b"<save>unique</save>"

def test_guarded_new_profile_transaction_scratch_only(candidate):
    root,env,check=candidate
    current=Path(env["X4_PROFILE"])/"content.xml"
    current.parent.mkdir(parents=True);current.write_bytes(b"<content existing='preserve'/>")
    target=Path(env["X4_DOCUMENTS"])/"Egosoft/X4/12345678/content.xml"
    files={root/"dev/probe/aiscripts/trace.xml":b"<diff/>",root/"dev/probe/md/trace.xml":b"<diff/>",target:b"<content sync='false'/>"}
    reply,_=check(list(files))
    assert reply["result"]["decision"]=="allow" and not reply["inert"],reply
    for path,data in files.items():
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("xb") as stream:stream.write(data)
    assert current.read_bytes()==b"<content existing='preserve'/>"

def test_mutation_snapshot_carries_modlist_native_path_dependency(tmp_path):
    import importlib.util
    import shutil
    import subprocess
    import sys
    from _layout import require_repo
    require_repo('scripts/verify-hook-tests.py', 'agent/guards/claude-hooks/test_hook_facts.py', why="the maintainers' mutation gate")
    spec = importlib.util.spec_from_file_location('modlist_mutation_gate', REPO / 'scripts/verify-hook-tests.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    work = tmp_path / 'mutation-snapshot'
    work.mkdir()
    for name in gate.FILES:
        shutil.copy2(gate.HOOKS / name, work / name)
    # Exercise the consumer, not an assertion about a filename in the manifest.
    result = subprocess.run([sys.executable, '-B', 'test_hook_facts.py',
        'TestProfileModlistWritePolicy.test_modlist_write_is_still_a_write_without_another_confirmation'],
        cwd=work, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')

def test_rejected_copy_options_cannot_borrow_redirect_exemption(guard, tmp_path):
    g, roots = guard
    source = tmp_path / 'source.xml'
    source.write_bytes(b'<content/>')
    target = roots['X4_PROFILE'] / 'content.xml'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'<content/>')
    command = "cp --backup=simple --suffix=.bak '" + str(source) + "' '" + str(target) + "' > '" + str(target) + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

@pytest.mark.parametrize('channel', ['write', 'shell'])
def test_hardlinked_modlist_does_not_waive_profile_confirmation(guard, channel):
    import os
    g, roots = guard
    config = roots['X4_PROFILE'] / 'config.xml'
    config.parent.mkdir(parents=True)
    config.write_bytes(b'<config/>')
    target = config.with_name('content.xml')
    os.link(config, target)
    assert target.stat().st_nlink > 1
    reply = (g.verdict_for('write', None, None, str(target), save_qualified=False)
             if channel == 'write' else g.verdict_for('shell', 'bash',
                 "echo fixture > '" + str(target) + "'", None, save_qualified=False))
    assert reply['decision'] == 'ask' and not reply['inert'], reply
    assert config.read_bytes() == b'<config/>'

@pytest.mark.parametrize('first', ['directory_copy', 'mkdir'])
def test_prior_destination_mutation_keeps_confirmation(guard, tmp_path, first):
    g, roots = guard
    target = roots['X4_PROFILE'] / 'content.xml'
    source = tmp_path / 'source-directory'
    source.mkdir()
    config = source / 'config.xml'
    config.write_bytes(b'<config/>')
    if first == 'directory_copy':
        command = "cp -r '" + str(source) + "' '" + str(target) + "'; echo fixture > '" + str(target) + "'"
    else:
        command = "mkdir -p '" + str(target) + "'; cp '" + str(config) + "' '" + str(target) + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

def test_recursive_directory_copy_cannot_claim_modlist_file(guard, tmp_path):
    g, roots = guard
    source = tmp_path / 'source-directory'
    source.mkdir()
    (source / 'config.xml').write_bytes(b'<config/>')
    command = "cp -r '" + str(source) + "' '" + str(roots['X4_PROFILE'] / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

def test_known_file_copy_to_new_modlist_is_advisory(guard, tmp_path):
    g, roots = guard
    source = tmp_path / 'candidate.xml'
    source.write_bytes(b'<content/>')
    command = "cp '" + str(source) + "' '" + str(roots['X4_PROFILE'] / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] in ('allow', 'advise') and not reply['inert'], reply

@pytest.mark.parametrize('verb', ['Set-Content', 'Add-Content'])
def test_powershell_modlist_writes_use_same_policy(guard, verb):
    g, roots = guard
    target = roots['X4_PROFILE'] / 'content.xml'
    command = verb + " -LiteralPath '" + str(target) + "' -Value '<content/>'"
    reply = g.verdict_for('shell', 'powershell', command, None, save_qualified=False)
    assert reply['decision'] in ('allow', 'advise') and not reply['inert'], reply

def test_modlist_parent_junction_cannot_hide_reference(guard, tmp_path, request):
    import os
    from test_file_guard_batch import _make_link
    g, roots = guard
    target = roots['X4_REFERENCE']
    target.mkdir(parents=True)
    link = tmp_path / 'Documents/Egosoft/X4/12345678'
    link.parent.mkdir(parents=True)
    _make_link('junction' if os.name == 'nt' else 'symlink', link, target, request)
    reply = g.verdict_for('write', None, None, str(link / 'content.xml'), save_qualified=False)
    assert reply['decision'] == 'deny' and not reply['inert'], reply

def test_modlist_source_fact_does_not_hide_the_write(guard):
    import importlib.util
    from codex_testlib import REPO
    from _layout import claude_hooks_source
    spec = importlib.util.spec_from_file_location('modlist_facts', claude_hooks_source(why='the parser under test') / 'hook_facts.py')
    facts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(facts)
    _, roots = guard
    target = str(roots['X4_PROFILE'] / 'content.xml')
    normalized = {k[3:].lower(): str(v) for k, v in roots.items()}
    result = facts.facts({'tool_input': {'command': "echo fixture > '" + target + "'"}}, normalized)
    assert result['writes_profile'] is True and result['writes_profile_other'] is False

def test_shell_copy_destination_named_content_xml_is_not_a_modlist(guard):
    g, roots = guard
    target = roots['X4_PROFILE'] / 'content.xml'
    target.mkdir(parents=True)
    origin = target.parent / 'fixture.xml'
    origin.write_bytes(b'<x/>')
    command = "cp '" + str(origin) + "' '" + str(target) + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

def test_shell_file_link_to_config_keeps_confirmation(guard, request):
    import os
    g, roots = guard
    target = roots['X4_PROFILE'] / 'config.xml'
    target.parent.mkdir(parents=True)
    target.write_bytes(b"<config/>")
    link = target.with_name('content.xml')
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows file-symlink privilege unavailable; Ubuntu CI required')
        raise
    request.addfinalizer(link.unlink)
    command = "echo fixture > '" + str(link) + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

def test_unconfigured_profile_container_is_not_a_modlist(guard, tmp_path, monkeypatch):
    g, roots = guard
    monkeypatch.delenv('X4_PROFILE')
    container = roots['X4_DOCUMENTS'] / 'Egosoft/X4'
    container.mkdir(parents=True)
    command = "echo fixture > '" + str(container / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply

def test_shell_profile_parent_link_cannot_gain_modlist_exemption(guard, tmp_path, monkeypatch, request):
    import os
    from test_file_guard_batch import _make_link
    g, roots = guard
    # Inside the fixture's sandbox, not tmp_path: since 4.1.1 the guard also judges where a link leads, and on
    # Linux tmp_path is under /tmp, which the shell guard refuses outright -- the verdict this test is not about.
    actual = roots['X4_DOCUMENTS'].parent / 'actual-profile'
    actual.mkdir()
    link = roots['X4_PROFILE']
    _make_link('junction' if os.name == 'nt' else 'symlink', link, actual, request)
    # Resolve the filename through a parent link into an ordinary configuration
    # directory, not an independently verified root mod-list.
    monkeypatch.setenv('X4_PROFILE', str(roots['X4_DOCUMENTS'] / 'Egosoft/X4'))
    linked_numeric = roots['X4_DOCUMENTS'] / 'Egosoft/X4/12345678'
    linked_numeric.parent.mkdir(parents=True)
    _make_link('junction' if os.name == 'nt' else 'symlink', linked_numeric, actual, request)
    command = "echo fixture > '" + str(linked_numeric / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply


@pytest.mark.parametrize('source', ['other/save/s2.xml.gz', 'other/config.xml', 'plain.xml'])
def test_a_move_onto_the_modlist_keeps_its_confirmation(guard, source):
    """A move also removes its source: a save moved this way would lose its only copy with no backup and no question."""
    g, roots = guard
    profile = roots['X4_PROFILE']
    origin = profile.parent / source
    origin.parent.mkdir(parents=True, exist_ok=True)
    origin.write_bytes(b'<x/>')
    command = "mv '" + str(origin) + "' '" + str(profile / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] == 'ask' and not reply['inert'], reply


def test_TWIN_a_copy_of_a_real_file_onto_the_modlist_is_advisory(guard):
    g, roots = guard
    profile = roots['X4_PROFILE']
    profile.mkdir(parents=True, exist_ok=True)
    origin = profile.parent / 'plain.xml'
    origin.write_bytes(b'<x/>')
    command = "cp '" + str(origin) + "' '" + str(profile / 'content.xml') + "'"
    reply = g.verdict_for('shell', 'bash', command, None, save_qualified=False)
    assert reply['decision'] != 'ask' and not reply['inert'], reply

"""Literal dollar filenames are paths; expandable dollars remain unknown scope.

Commands below are parsed only. No command under examination is executed.
"""
import importlib.util
import os
from pathlib import Path
import shutil

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def parser():
    spec = importlib.util.spec_from_file_location('literal_dollar_facts',
        REPO/'agent/guards/claude-hooks/hook_facts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def examine(parser, tmp_path, shell, command, *, allow_untranslated=False, operation='delete'):
    if shell == 'PowerShell' and not (shutil.which('pwsh') or shutil.which('powershell')):
        pytest.skip('PowerShell parser unavailable')
    roots = {key:str(tmp_path/key) for key in
             ('game', 'reference', 'toolkit', 'mods', 'profile', 'saves', 'documents')}
    rows = []
    facts = parser.facts(dict(tool_name=shell,tool_input=dict(command=command),cwd=''),
                         roots,evidence=rows,execution_context=True)
    assert not facts.get('powershell_error'), facts
    if not allow_untranslated:
        assert not facts.get('carrier_untranslated'), facts
    return [row for row in rows if row['operation']==operation]


@pytest.mark.parametrize('shell', ['Bash', 'PowerShell'])
@pytest.mark.parametrize('quote,unresolved', [("'", False), ('"', True)])
def test_absolute_dollar_filename_keeps_quote_provenance(parser,tmp_path,shell,quote,unresolved):
    path=(tmp_path/'$Recycle.Bin'/'$Rsample').as_posix()
    verb='rm -rf -- ' if shell=='Bash' else 'Remove-Item -Recurse -Force -LiteralPath '
    rows=examine(parser,tmp_path,shell,verb+quote+path+quote,allow_untranslated=unresolved)
    assert len(rows)==1, rows
    assert rows[0]['unresolved'] is unresolved, rows
    if not unresolved:
        assert os.path.normcase(rows[0]['path'].replace('\\','/'))==os.path.normcase(path)


def test_escaped_bash_dollars_are_literal(parser,tmp_path):
    path=(tmp_path/'$Recycle.Bin'/'$Rsample').as_posix()
    rows=examine(parser,tmp_path,'Bash','rm -rf -- '+path.replace('$','\\$'))
    assert len(rows)==1 and not rows[0]['unresolved'], rows
    assert rows[0]['path'].replace('\\','/')==path


def test_same_text_with_mixed_quotes_never_borrows_literal_proof(parser,tmp_path):
    path=(tmp_path/'$Recycle.Bin'/'$Rsample').as_posix()
    rows=examine(parser,tmp_path,'Bash',f'rm -- \'{path}\' "{path}"')
    assert len(rows)==2 and all(row['unresolved'] for row in rows), rows


def test_literal_proof_does_not_leak_between_commands(parser,tmp_path):
    path=(tmp_path/'$Recycle.Bin'/'$Rsample').as_posix()
    rows=examine(parser,tmp_path,'Bash',f'rm -- \'{path}\'; rm -- "{path}"')
    assert [row['unresolved'] for row in rows]==[False,True], rows


def test_powershell_literal_assignment_retains_literal_dollars(parser,tmp_path):
    path=(tmp_path/'$Recycle.Bin'/'$Rsample').as_posix()
    rows=examine(parser,tmp_path,'PowerShell',f"$p='{path}'; Remove-Item -LiteralPath $p")
    assert len(rows)==1 and not rows[0]['unresolved'], rows


def test_literal_dollar_pattern_does_not_gain_exact_target_qualification(parser,tmp_path):
    path=(tmp_path/'$Recycle.Bin'/'$R[abc]').as_posix()
    rows=examine(parser,tmp_path,'Bash',f"rm -- '{path}'")
    assert len(rows)==1 and rows[0]['unresolved'], rows


def test_a_quoted_trailing_dollar_is_not_the_next_quotes_sigil(parser,tmp_path):
    parent=(tmp_path/'$Recycle.Bin'/'folder$').as_posix()
    rows=examine(parser,tmp_path,'Bash',f"rm -- '{parent}'\"/item\"")
    assert len(rows)==1 and not rows[0]['unresolved'], rows
    assert rows[0]['path'].replace('\\','/')==parent+'/item'
    assert parser.words("$'\\x72\\x6d' -f file")==['rm','-f','file']


@pytest.mark.parametrize('shell', ['Bash', 'PowerShell'])
def test_literal_dollars_survive_copy_source_and_destination(parser,tmp_path,shell):
    source=(tmp_path/'$input'/'file.bin').as_posix()
    destination=(tmp_path/'$output').as_posix()
    command=(f"cp -- '{source}' '{destination}'" if shell=='Bash' else
             f"Copy-Item -LiteralPath '{source}' -Destination '{destination}'")
    rows=examine(parser,tmp_path,shell,command,operation='write')
    assert len(rows)==1 and not rows[0]['unresolved'], rows
    assert rows[0]['path'].replace('\\','/')==destination
    assert len(rows[0]['copy_sources'])==1
    assert not rows[0]['copy_sources'][0]['unresolved'], rows
    assert rows[0]['copy_sources'][0]['path'].replace('\\','/')==source

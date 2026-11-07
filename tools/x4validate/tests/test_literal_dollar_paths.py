"""Literal dollar filenames are paths; expandable dollars remain unknown scope.

Ported for 4.1.1 from session/literal-dollar-v2 (6cbeb825, 51129cde, ed667b4a), re-applied by
hand over the rounds 4-7 parser; the twins at the end are new with the port.
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


# ---------------------------------------------------------------------------------------
# A command handed to ANOTHER shell. Its text no longer shows what the OUTER layer did:
# in `bash -c "rm -rf 'REF$X'"` the outer double quotes expand $X BEFORE the inner shell
# sees its single quotes, so the target may be reference/ itself. The first version of
# this change (1158deb, reverted by a052f8cd) re-read the inner text and called that `$`
# literal, which lost the hard denies below. Each shape is one test so a regression
# names its shape. `{R}`, `{G}`, `{S}` are the reference, game and save roots.
WRAPPED_STRICT = [
    ('rm_targets_reference', "bash -c \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "sh -c \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "env bash -c \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "timeout 5 bash -c \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "nohup bash -c \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "bash -c \"bash -c \\\"rm -rf '{R}$X'\\\"\""),
    ('rm_targets_reference', "eval \"rm -rf '{R}$X'\""),
    ('rm_targets_reference', "bash <<EOF\nrm -rf '{R}$X'\nEOF"),
    ('rm_targets_reference', "cmd /c \"rmdir /s /q {R}$X\""),
    ('rm_targets_reference', "pwsh -c \"Remove-Item -Recurse '{R}$X'\""),
    ('writes_reference', "bash -c \"echo hi > '{R}$X/libraries/wares.xml'\""),
    ('writes_reference', "bash -c \"cp a '{R}$X/wares.xml'\""),
    ('writes_reference', "bash -c \"sed -i s/a/b/ '{R}$X/libraries/wares.xml'\""),
    ('writes_reference', "bash -c \"echo hi > '{R}$1/libraries/wares.xml'\" _ \"\""),
    ('rm_saves', "bash -c \"rm -rf '{S}$X'\""),
    ('rm_in_x4_dir', "bash -c \"rm -rf '{G}$X'\""),
    ('rm_in_x4_dir', "sh -c \"rm -rf '{G}$X'\""),
    ('rm_in_x4_dir', "eval \"rm -rf '{G}$X'\""),
    ('rm_in_x4_dir', "bash <<EOF\nrm -rf '{G}$X'\nEOF"),
]


def wrapped_facts(parser, tmp_path, shell, command):
    if ((shell == 'PowerShell' or 'pwsh' in command)
            and not (shutil.which('pwsh') or shutil.which('powershell'))):
        pytest.skip('PowerShell parser unavailable')
    base = tmp_path.as_posix()
    roots = {'game': base + '/game', 'reference': base + '/reference', 'toolkit': base + '/toolkit',
             'mods': base + '/dev', 'profile': base + '/profile', 'saves': base + '/profile/save',
             'documents': base + '/docs'}
    command = (command.replace('{R}', roots['reference']).replace('{G}', roots['game'])
               .replace('{S}', roots['saves']))
    return parser.facts(dict(tool_name=shell, tool_input=dict(command=command), cwd=roots['toolkit']),
                        roots, evidence=[])


@pytest.mark.parametrize('flag,command', WRAPPED_STRICT, ids=[c for _f, c in WRAPPED_STRICT])
def test_a_wrapped_shell_dollar_stays_an_unresolved_expansion(parser, tmp_path, flag, command):
    facts = wrapped_facts(parser, tmp_path, 'Bash', command)
    assert facts.get(flag), (flag, command, facts)


# The same shapes with the `$` really literal in EVERY layer: the outer shell hands the
# inner one a `$` it did not expand (escaped in double quotes, or single-quoted), and the
# inner single quotes keep it. These may -- and do -- name the sibling path `<root>$X`.
WRAPPED_LITERAL = [
    ('rm_targets_reference', "bash -c \"rm -rf '{R}\\$X'\""),
    ('rm_targets_reference', "bash -c 'rm -rf '\"'\"'{R}$X'\"'\"''"),
    ('rm_targets_reference', "bash <<'EOF'\nrm -rf '{R}$X'\nEOF"),
]


@pytest.mark.parametrize('flag,command', WRAPPED_LITERAL, ids=[c for _f, c in WRAPPED_LITERAL])
def test_a_dollar_literal_in_every_layer_names_the_sibling(parser, tmp_path, flag, command):
    facts = wrapped_facts(parser, tmp_path, 'Bash', command)
    assert not facts.get(flag), (flag, command, facts)


# Carriers whose quoting this parser cannot follow through every layer stay strict even
# when the `$` happens to be literal: a here-string or a pipe into a shell, a heredoc
# reaching the shell through a filter, a PowerShell-tool command handing text to bash.
@pytest.mark.parametrize('shell,command', [
    ('Bash', "echo \"rm -rf '{R}\\$X'\" | bash"),
    ('Bash', "bash <<< \"rm -rf '{R}$X'\""),
    # In double quotes a backslash before ' is KEPT, so the inner shell sees \' -- a
    # literal quote character -- and expands $X: not the single-quoted form it resembles.
    ('Bash', "bash -c \"rm -rf \\'{R}\\$X\\'\""),
    ('Bash', "cat <<'EOF' | sed \"s/'//g\" | bash\nrm -rf '{R}$X'\nEOF"),
    ('Bash', "bash <<'EOF' < /dev/null\nrm -rf '{R}$X'\nEOF"),
    ('Bash', "C=\"rm -rf '{R}\\$X'\"; bash -c \"$C\""),
    ('PowerShell', "bash -c \"rm -rf '{R}$X'\""),
])
def test_carriers_without_followed_quoting_stay_strict(parser, tmp_path, shell, command):
    if shell == 'PowerShell' and not (shutil.which('pwsh') or shutil.which('powershell')):
        pytest.skip('PowerShell parser unavailable')
    facts = wrapped_facts(parser, tmp_path, shell, command)
    assert facts.get('rm_targets_reference'), (command, facts)


# Review of 6cbeb825 (three bypasses, each MEASURED). One test per shape; every one of
# them failed on 6cbeb825.
# (1) A function or alias defined in the same command re-parses the "literal" word:
#     `rm(){ eval command rm "$@"; }; rm -rf '<ref>$X'` removed <ref> in Git Bash.
# (2) A NUL ends an ANSI-C word: `$'<ref>\x00$X'` IS <ref>. The decoder kept going, which
#     also let `$'<ref>\x00junk'` through on the code before this change.
# (3) `sudo -s` / `-i` runs the command through a shell's -c, escaping everything but `$`.
BYPASSES = [
    ('rm_targets_reference', "rm(){ eval command rm \"$@\"; }; rm -rf '{R}$X'"),
    ('rm_targets_reference', "function rm { eval command rm \"$@\"; }; rm -rf '{R}$X'"),
    ('rm_targets_reference', "rm(){ sh -c \"/bin/rm $*\"; }; rm -rf '{R}$X'"),
    ('rm_targets_reference', "shopt -s expand_aliases; alias rm='eval rm'; rm -rf '{R}$X'"),
    ('rm_targets_reference', "mv(){ eval command rm -rf \"$@\"; }; mv '{R}$X' /tmp/x"),
    ('rm_saves', "rm(){ eval command rm \"$@\"; }; rm -rf '{S}$X'"),
    ('rm_in_x4_dir', "rm(){ eval command rm \"$@\"; }; rm -rf '{G}$X'"),
    ('writes_reference', "cp(){ eval command cp \"$@\"; }; cp a '{R}$X/libraries/wares.xml'"),
    ('rm_targets_reference', "rm -rf $'{R}\\x00$X'"),
    ('rm_targets_reference', "rm -rf $'{R}\\0$X'"),
    ('rm_targets_reference', "rm -rf $'{R}\\u0000$X'"),
    ('rm_targets_reference', "rm -rf $'{R}\\c@$X'"),
    ('rm_targets_reference', "rm -rf $'{R}\\x00junk'"),
    ('rm_targets_reference', "bash -c 'rm -rf $'\"'\"'{R}\\x00$X'\"'\"''"),
    ('rm_targets_reference', "mv $'{R}\\x00$X' /tmp/x"),
    ('writes_reference', "echo x > $'{R}\\x00$X/libraries/wares.xml'"),
    ('rm_targets_reference', "sudo -s rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -i rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo --shell rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo --login rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -is rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -E -s rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -u root -s rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -su root rm -rf '{R}$X'"),
    ('rm_targets_reference', "sudo -s bash -c \"rm -rf '{R}\\$X'\""),
    ('rm_saves', "sudo -s rm -rf '{S}$X'"),
    # A value-taking flag ending a CLUSTER takes the next word: before this change verb()
    # read `root` as the command, so even the plain path reached no delete rule.
    ('rm_targets_reference', "sudo -su root rm -rf '{R}'"),
    ('rm_targets_reference', "sudo -Eu root rm -rf '{R}'"),
]


@pytest.mark.parametrize('flag,command', BYPASSES, ids=[c for _f, c in BYPASSES])
def test_a_command_that_can_reparse_the_word_keeps_it_unresolved(parser, tmp_path, flag, command):
    facts = wrapped_facts(parser, tmp_path, 'Bash', command)
    assert facts.get(flag), (flag, command, facts)


# Re-review of 51129cde (each MEASURED in Git Bash). A definition BUILT at run time evades
# any text pattern, so the rule is broad: an eval/source/`.` anywhere, a command name that
# is not a plain word, BASH_ALIASES/BASH_CMDS, or a shell loading startup files or aliases
# means no `$` in the whole command is literal.
REVIEW_2 = [
    "eval $'rm\\x28\\x29 { eval command rm \"$@\"; }'; rm -rf '{R}$X'",
    "eval $'\\x66unction rm { eval command rm \"$@\"; }'; rm -rf '{R}$X'",
    "eval rm\\(\\) '{ eval command rm \"$@\"; }'; rm -rf '{R}$X'",
    "eval \"rm\"'()' '{ eval command rm \"$@\"; }'; rm -rf '{R}$X'",
    "R=eval; $R $'rm\\x28\\x29 { eval command rm \"$@\"; }'; rm -rf '{R}$X'",
    "bash -c $'eval $\\'rm\\\\x28\\\\x29 { eval command rm \"$@\"; }\\'; rm -rf \\'{R}$X\\''",
    "bash <<'EOF'\neval $'rm\\x28\\x29 { eval command rm \"$@\"; }'; rm -rf '{R}$X'\nEOF",
    "BASH_ALIASES[rm]='eval rm'; rm -rf '{R}$X'",
    "bash -O expand_aliases -c \"BASH_ALIASES[rm]='eval rm'\"; rm -rf '{R}$X'",
    "bash -i -c \"rm -rf '{R}\\$X'\"",
    "bash -l -c \"rm -rf '{R}\\$X'\"",
    "bash --rcfile /tmp/rc -c \"rm -rf '{R}\\$X'\"",
    "rm -rf $'{R}\\uD800junk'",
    "rm -rf $'{R}\\U0010FFFFjunk'",
]


@pytest.mark.parametrize('command', REVIEW_2)
def test_a_run_time_definition_or_rc_shell_keeps_every_dollar_unresolved(parser, tmp_path, command):
    facts = wrapped_facts(parser, tmp_path, 'Bash', command)
    assert facts.get('rm_targets_reference'), (command, facts)


def test_sudo_h_takes_no_separate_value_in_a_cluster(parser, tmp_path):
    facts = wrapped_facts(parser, tmp_path, 'Bash', "sudo -Eh rm -rf '{R}'")
    assert facts.get('rm_targets_reference'), facts


def test_ansi_c_unicode_escapes_follow_measured_git_bash(parser):
    # MEASURED: printf '[%s]' $'a\uD800b' -> [a\uD800b]; $'a/b' -> [a/b]
    assert parser._ansi_c_decode('a\\uD800b') == 'a\\uD800b'
    assert parser._ansi_c_decode('a\\U0010FFFFb') == 'a\\U0010FFFFb'
    assert parser._ansi_c_decode('a\\U00000041b') == 'aAb'
    assert parser._ansi_c_decode('a\\x4142b') == 'aA42b'
    assert parser._ansi_c_decode('a\\c?b') == 'a' + chr(127) + 'b'


def test_ansi_c_decoding_stops_at_the_first_nul(parser):
    for body in ('ab\\x00cd', 'ab\\0cd', 'ab\\000cd', 'ab\\400cd', 'ab\\u0000cd',
                 'ab\\U00000000cd', 'ab\\c@cd', 'ab' + chr(0) + 'cd'):
        assert parser._ansi_c_decode(body) == 'ab', body
    assert parser._ansi_c_decode('a\\u002fb') == 'a/b'
    assert parser._ansi_c_decode('\\cA') == chr(1)


# ---------------------------------------------------------------------------------------
# 4.1.1 port (hand re-application of 6cbeb825/51129cde/ed667b4a over the rounds 4-7 parser).
# A `$` literal at the OUTER layer may still be expanded by an INNER one: a shell's -c text
# with its own double quotes or bare word, positional parameters filled in, ssh/watch/wsl and
# the other carriers that join words for a shell, su/script -c, zsh (.zshenv), eval, a
# here-string, a pipe, startup-steering variables, and PowerShell's own eval (iex), which can
# build a definition at run time. Each stays an unresolved expansion, as on a933b5c0.
EVERY_LAYER_TWINS = [
    "sh -c 'rm -rf \"{R}$x\"'",
    "bash -c 'rm -rf \"{R}$x\"'",
    "sh -c 'rm -rf {R}$x'",
    "sh -c 'rm -rf \"$1\"' _ '{R}$x'",
    "bash -c 'rm -rf \"$0\"' '{R}$x'",
    "ssh localhost rm -rf '{R}$x'",
    "ssh localhost \"rm -rf '{R}\\$x'\"",
    "sshpass -p x ssh localhost rm -rf '{R}$x'",
    "watch rm -rf '{R}$x'",
    "wsl rm -rf '{R}$x'",
    "su -c \"rm -rf '{R}\\$x'\"",
    "script -c \"rm -rf '{R}\\$x'\" /dev/null",
    "parallel rm -rf ::: '{R}$x'",
    "tmux new-session -d \"rm -rf '{R}\\$x'\"",
    "mintty -e bash -c \"rm -rf '{R}\\$x'\"",
    "zsh -c \"rm -rf '{R}\\$x'\"",
    "xargs sh -c 'rm -rf \"{R}$x\"'",
    "find . -exec sh -c 'rm -rf \"{R}$x\"' \\;",
    "eval 'rm -rf {R}$x'",
    "X='{R}$x'; rm -rf $X",
    "BASH_ENV=/tmp/x bash -c \"rm -rf '{R}\\$x'\"",
    "rm -rf '{R}'$x",
    "rm -rf '{R}$x'*",
]


@pytest.mark.parametrize('command', EVERY_LAYER_TWINS)
def test_an_inner_layer_that_may_expand_keeps_the_dollar_unresolved(parser, tmp_path, command):
    facts = wrapped_facts(parser, tmp_path, 'Bash', command)
    assert facts.get('rm_targets_reference'), (command, facts)


@pytest.mark.parametrize('command', [
    "Invoke-Expression \"Remove-Item '{R}$x'\"",
    "Invoke-Expression 'Remove-Item \"{R}$x\"'",
    "iex \"Remove-Item '{R}`$x'\"",
    "$c = \"Remove-Item '{R}`$x'\"; iex $c",
    "powershell -c \"Remove-Item '{R}`$x'\"",
    "bash -c \"rm -rf '{R}`$x'\"",
    "Remove-Item -LiteralPath \"{R}$x\" -Recurse",
])
def test_a_powershell_layer_that_may_expand_keeps_the_dollar_unresolved(parser, tmp_path, command):
    # iex beside a literal is strict even where the `$` is literal in every layer (the second
    # iex row): like bash's eval, it can build a definition at run time. Either verdict below
    # is the a933b5c0 one -- the deny, or the untranslated refusal.
    facts = wrapped_facts(parser, tmp_path, 'PowerShell', command)
    assert facts.get('rm_targets_reference') or facts.get('carrier_untranslated'), (command, facts)


# ...and wrappers that EXEC their argv (no shell between), an ANSI-C `\x24`, and PowerShell's
# own literal spellings: literal in every layer, so the sibling `<reference>$x` is named.
@pytest.mark.parametrize('shell,command', [
    ('Bash', "nohup rm -rf '{R}$x'"),
    ('Bash', "env rm -rf '{R}$x'"),
    ('Bash', "timeout 5 rm -rf '{R}$x'"),
    ('Bash', "xargs rm -rf '{R}$x'"),
    ('Bash', "rm -rf $'{R}\\x24x'"),
    ('PowerShell', "Remove-Item -LiteralPath '{R}$x' -Recurse"),
    ('PowerShell', "Remove-Item -LiteralPath \"{R}`$x\" -Recurse"),
])
def test_a_wrapper_that_execs_its_argv_keeps_the_literal(parser, tmp_path, shell, command):
    facts = wrapped_facts(parser, tmp_path, shell, command)
    assert not facts.get('rm_targets_reference'), (command, facts)

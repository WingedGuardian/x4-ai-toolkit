"""x4save vars: read MD cue variables out of a save without a hand-rolled script.

Why it exists: answering "what is this mod's setting in my save?" used to mean writing a
gzip-streaming script against a save PATH, which the save-protection guard (rightly)
treats as an opaque program touching a save. `x4save vars <pattern> <save name>` is the
supported route. Layout MEASURED on a real 9.0 save: <md> > <script name> > nested
<cue name> > <vars> > <value name type value>. An integer whose `value` attribute is
absent is 0; a value with no `type` is null; a table is stored by reference id.

The production change each test would catch is named in its docstring.
"""
from __future__ import annotations

import gzip

import pytest

from x4validate import _savecli

_DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<savegame><info><save name="#T"/></info>
<universe><component class="ship"><vars><value name="$shipvar" type="integer" value="7"/></vars></component></universe>
<md>
<script id="1" name="kuertee_aait">
<cue id="2" name="kAAIT2" state="complete">
<vars></vars>
<cue id="3" name="kAAIT2_Config" state="complete">
<vars>
<value name="$byShipId" type="table" value="1433"/>
<value name="$debugChance_idCode"/>
<value name="$xssm_isAvoidHighRisk" type="integer" value="1"/>
<value name="$xssm_isAvoidHighRisk_allFactions" type="integer"/>
<value name="$label" type="string" value="xssm_note"/>
<value name="$items" type="list" value="57502"/>
<value name="$grp" type="group" value="125265"/>
<value name="$delay" type="time"/>
<value name="$obj" type="component"/>
</vars>
</cue>
</cue>
</script>
<value name="$mdloose" type="integer" value="3"/>
</md>
<aidirector><entity id="[0x14718]"><script id="1" name="build.buildstorage" label="loop">
<vars><value name="$buildmodule" type="component" value="[0x14714]"/></vars>
</script></entity></aidirector>
</savegame>
"""


def _save(tmp_path, data: bytes = _DOC):
    p = tmp_path / "save_001.xml.gz"
    p.write_bytes(gzip.compress(data))
    return p


def _rows(scan):
    return [(r.where, r.name, r.type, r.value) for r in scan.rows]


def test_nested_cue_path_and_absent_integer_reads_zero(tmp_path):
    """Fails if the cue stack is not tracked, or an absent integer value is not 0."""
    scan = _savecli.extract_vars(_save(tmp_path), "allfactions")
    assert _rows(scan) == [("kuertee_aait/kAAIT2/kAAIT2_Config",
                            "$xssm_isAvoidHighRisk_allFactions", "integer", "0 (absent)")]


def test_table_null_and_string_values_render_honestly(tmp_path):
    """Fails if a table ref is shown as a bare number, or a typeless value as empty."""
    got = {r.name: r.value for r in _savecli.extract_vars(_save(tmp_path), r"^\$").rows}
    assert got["$byShipId"] == "table#1433"
    assert got["$debugChance_idCode"] == "null"
    assert got["$label"] == "xssm_note"


def test_pattern_matches_names_case_insensitively_not_values(tmp_path):
    """Fails if the regex is applied to values, or is case-sensitive."""
    names = [r.name for r in _savecli.extract_vars(_save(tmp_path), "XSSM").rows]
    assert names == ["$xssm_isAvoidHighRisk", "$xssm_isAvoidHighRisk_allFactions"]


def test_anonymous_values_are_not_reported(tmp_path):
    """Fails if a <value> with no name attribute is reported as a variable."""
    doc = b'<savegame><md><script name="s"><cue name="c"><vars><value type="integer" value="5"/></vars></cue></script></md></savegame>'
    assert _savecli.extract_vars(_save(tmp_path, doc), "").rows == []


def test_list_and_group_are_shown_as_references(tmp_path):
    """Review item 3: fails if a list/group id prints as a bare number (real saves store
    both by reference, e.g. type="list" value="57502")."""
    got = {r.name: r.value for r in _savecli.extract_vars(_save(tmp_path), "items|grp").rows}
    assert got == {"$items": "list#57502", "$grp": "group#125265"}


def test_absent_values_are_labelled_by_type(tmp_path):
    """Review item 4: fails if a numeric type other than integer reads as plain '(absent)'
    or a non-numeric type reads as zero."""
    got = {r.name: r.value for r in _savecli.extract_vars(_save(tmp_path), "delay|obj").rows}
    assert got == {"$delay": "0 (absent, inferred)", "$obj": "(absent)"}


def test_aiscript_variables_are_labelled_with_script_and_entity(tmp_path):
    """Review item 1: fails if an AI-director script variable is printed as an MD cue path."""
    rows = {r.name: r.where for r in _savecli.extract_vars(_save(tmp_path), "buildmodule").rows}
    assert rows == {"$buildmodule": "(aiscript build.buildstorage on entity [0x14718])"}


def test_md_value_outside_a_named_cue_is_labelled(tmp_path):
    """Fails if an MD value with no named cue/script is reported as outside MD."""
    rows = {r.name: r.where for r in _savecli.extract_vars(_save(tmp_path), "mdloose").rows}
    assert rows == {"$mdloose": "(md, no named cue)"}


def test_anchored_pattern_matches_the_name_without_its_dollar(tmp_path):
    """Review item 2a: fails if '^xssm' matches nothing because stored names start with '$'."""
    names = [r.name for r in _savecli.extract_vars(_save(tmp_path), "^xssm_").rows]
    assert names == ["$xssm_isAvoidHighRisk", "$xssm_isAvoidHighRisk_allFactions"]


def test_streaming_callback_keeps_no_rows_but_counts(tmp_path):
    """Review item 10: fails if the streaming path still accumulates every row in memory."""
    seen = []
    scan = _savecli.extract_vars(_save(tmp_path), "isAvoid", on_row=seen.append)
    assert [r.value for r in seen] == ["1", "0 (absent)"]
    assert scan.rows == [] and scan.count == 2


def test_values_outside_md_cues_are_reported_with_their_container(tmp_path):
    """Fails if a value outside <md> is silently dropped (a narrowing step must say so)."""
    rows = {r.name: r.where for r in _savecli.extract_vars(_save(tmp_path), "shipvar").rows}
    assert rows == {"$shipvar": "(outside MD cues: component)"}


def test_scanned_byte_count_is_the_decompressed_length(tmp_path):
    """Fails if the footer's denominator is not the real amount read."""
    scan = _savecli.extract_vars(_save(tmp_path), "nomatch_xyz")
    assert scan.rows == [] and scan.scanned_bytes == len(_DOC)


@pytest.mark.parametrize("chunk", [1, 7, 997, 1 << 22])
def test_chunk_boundaries_do_not_change_the_answer(tmp_path, monkeypatch, chunk):
    """Fails if parsing depends on where a read chunk ends."""
    monkeypatch.setattr(_savecli, "_CHUNK", chunk)
    scan = _savecli.extract_vars(_save(tmp_path), "isAvoid")
    assert [r.value for r in scan.rows] == ["1", "0 (absent)"]


@pytest.mark.parametrize("body", [_DOC[:-40], b'<savegame><md><value name="$x" type="integer" value="\xff"/></md></savegame>'])
def test_malformed_or_truncated_save_is_unreadable(tmp_path, body):
    """Fails if a truncated save returns a partial answer instead of refusing."""
    with pytest.raises(_savecli.SaveUnreadable):
        _savecli.extract_vars(_save(tmp_path, body), "x")


def test_cli_prints_rows_and_footer_rc0(tmp_path, capsys):
    """Fails if `vars` is not wired as a subcommand or the footer omits the denominator."""
    rc = _savecli.main(["vars", "allfactions", str(_save(tmp_path))])
    out = capsys.readouterr().out
    assert rc == 0
    assert "$xssm_isAvoidHighRisk_allFactions" in out and "0 (absent)" in out
    assert f"1 match(es) over {len(_DOC):,} decompressed bytes" in out


def test_cli_no_match_says_absence_of_key_rc1(tmp_path, capsys):
    """Twin: fails if zero matches reads like success, or hides the scanned denominator."""
    rc = _savecli.main(["vars", "nomatch_xyz", str(_save(tmp_path))])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"NO MATCH over {len(_DOC):,} decompressed bytes" in out
    assert "no named variable matched" in out
    assert "table, list and group contents are not searched" in out


def test_cli_bad_regex_is_a_refusal_rc2(tmp_path, capsys):
    """Fails if an invalid pattern crashes with a traceback instead of refusing."""
    rc = _savecli.main(["vars", "(", str(_save(tmp_path))])
    assert rc == 2
    assert "pattern" in capsys.readouterr().err


def test_sibling_cue_does_not_leak_into_the_next_cues_path(tmp_path):
    """Fails if closed elements are not popped: a later sibling cue would inherit the
    earlier cue's name in its path."""
    doc = (b'<savegame><md><script name="s"><cue name="first"><vars>'
           b'<value name="$a" type="integer" value="1"/></vars></cue>'
           b'<cue name="second"><vars><value name="$b" type="integer" value="2"/></vars></cue>'
           b'</script></md></savegame>')
    rows = {r.name: r.where for r in _savecli.extract_vars(_save(tmp_path, doc), "").rows}
    assert rows == {"$a": "s/first", "$b": "s/second"}


class _ClosedPipe:
    """A stdout whose reader went away (`x4save vars ... | head`): the first write raises. POSIX raises
    BrokenPipeError (EPIPE); Windows raises OSError EINVAL, which is what was MEASURED on this machine."""
    def __init__(self, exc):
        self.exc = exc

    def write(self, _s):
        raise self.exc

    def flush(self):
        pass

    def fileno(self):
        raise OSError("no fd")


@pytest.mark.parametrize("exc", [BrokenPipeError(32, "Broken pipe"), OSError(22, "Invalid argument")])
def test_a_closed_pipe_is_a_quiet_exit_not_a_broken_save(tmp_path, monkeypatch, capsys, exc):
    """Fails if `| head` closing stdout is reported as SaveUnreadable 'decompression/XML parse failed (Broken
    pipe)' with rc 2 -- the save is fine, the reader just stopped."""
    monkeypatch.setattr("sys.stdout", _ClosedPipe(exc))
    rc = _savecli.main(["vars", "isAvoid", str(_save(tmp_path))])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Broken pipe" not in err and "decompression" not in err and "error:" not in err


def test_another_write_error_is_not_mistaken_for_a_closed_pipe(tmp_path, monkeypatch):
    """Twin: a full disk (ENOSPC) while redirecting to a file is a real failure, never a quiet rc 0 (it surfaces)."""
    monkeypatch.setattr("sys.stdout", _ClosedPipe(OSError(28, "No space left on device")))
    with pytest.raises(OSError, match="No space"):
        _savecli.main(["vars", "isAvoid", str(_save(tmp_path))])


def test_extract_vars_lets_a_broken_pipe_through_unconverted(tmp_path):
    """Twin: a real parse failure is still SaveUnreadable, but a BrokenPipeError from on_row is not one."""
    def closed(_row):
        raise BrokenPipeError(32, "Broken pipe")
    with pytest.raises(BrokenPipeError):
        _savecli.extract_vars(_save(tmp_path), "isAvoid", on_row=closed)
    with pytest.raises(_savecli.SaveUnreadable):
        _savecli.extract_vars(_save(tmp_path, _DOC[:-40]), "isAvoid", on_row=lambda _r: None)


def test_a_save_truncated_after_rows_printed_says_incomplete_on_stderr(tmp_path, capsys):
    """Fails if rows are printed and then only the generic code-2 error follows: the reader must be told the
    list above is partial."""
    rc = _savecli.main(["vars", "isAvoid", str(_save(tmp_path, _DOC[:-40]))])
    cap = capsys.readouterr()
    assert rc == 2 and "$xssm_isAvoidHighRisk" in cap.out
    assert "INCOMPLETE" in cap.err and "2 row(s)" in cap.err


def test_a_unreadable_save_with_no_rows_printed_has_no_incomplete_line(tmp_path, capsys):
    """Twin: nothing was printed, so there is no partial list to disclaim."""
    rc = _savecli.main(["vars", "nomatch_xyz", str(_save(tmp_path, _DOC[:-40]))])
    assert rc == 2 and "INCOMPLETE" not in capsys.readouterr().err

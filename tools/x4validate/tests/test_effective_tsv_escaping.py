"""x4effective TSV output cannot be mis-split by a cell that holds a tab, a newline or a backslash.

MEASURED 2026-11-14 on the live store before the fix: 4 of 80,533 `texts` rows contain a TAB and 52 a
NEWLINE (attribute values: 0 of 841,625). `x4effective sql "SELECT page, t, text FROM texts WHERE <tab or
newline>"` returned those 52 rows (the 4 tab rows also hold a newline) as 173 output lines -- 117
one-field continuation lines and 4 four-field lines -- so any consumer splitting rows on newline and cells on tab read garbage with exit 0.

The cure is the PostgreSQL COPY text convention, lossless and reversible: `\\` -> `\\\\`, TAB -> `\\t`,
LF -> `\\n`, CR -> `\\r`, NULL -> `\\N`. `--json` gives the exact values with no escaping to undo.
`find` prints the same TSV shape with free text (the resolved in-game name) and is cured the same way.
"""
from __future__ import annotations

import json
import re

import pytest

from x4validate import _effective, _effectivecli, _merge

TAB_TEXT = "Left\tRight"
NL_TEXT = "Line one\nLine two"


def _world(tmp_path):
    ref = tmp_path / "reference"
    (ref / "t").mkdir(parents=True)
    (ref / "t" / "0001-l044.xml").write_bytes(
        b'<language id="44"><page id="20101">'
        b'<t id="1">Left&#9;Right</t>'
        b'<t id="2">Line one&#10;Line two</t>'
        b'<t id="3">plain</t>'
        b'<t id="4">Tab&#9;Name</t></page></language>')
    m = ref / "assets" / "props" / "macros"
    m.mkdir(parents=True)
    (m / "weapon_tab_macro.xml").write_bytes(
        b'<macros><macro name="weapon_tab_macro" class="weapon"><properties>'
        b'<identification name="{20101,4}"/></properties></macro></macros>')
    return ref, tmp_path / "extensions"


@pytest.fixture
def db(tmp_path, monkeypatch):
    ref, exts = _world(tmp_path)
    exts.mkdir()
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    path = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), path, dirs=[exts], kinds=("macro",))
    return path


def _unescape(cell: str):
    if cell == "\\N":
        return None
    return re.sub(r"\\(.)", lambda m: {"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}[m.group(1)], cell)


def _sql(db, capsys, query, *extra):
    rc = _effectivecli.main(["--db", str(db), "sql", query, *extra])
    return rc, capsys.readouterr().out


def test_the_store_really_holds_the_tab_and_the_newline(db, capsys):
    """Control: the fixture expresses the defect, or the tests below prove nothing."""
    import sqlite3
    texts = dict(((p, t), v) for p, t, v in sqlite3.connect(db).execute("SELECT page, t, text FROM texts"))
    assert texts[("20101", "1")] == TAB_TEXT and texts[("20101", "2")] == NL_TEXT


@pytest.mark.parametrize("tid,raw", [("1", TAB_TEXT), ("2", NL_TEXT)])
def test_a_text_cell_with_a_separator_stays_ONE_row_of_THREE_cells(db, capsys, tid, raw):
    rc, out = _sql(db, capsys, f"SELECT page, t, text FROM texts WHERE t='{tid}'")
    assert rc == 0
    lines = out.split("\n")
    assert lines[-1] == "" and len(lines) == 2, out          # one row, newline-terminated
    cells = lines[0].split("\t")
    assert len(cells) == 3, cells
    assert _unescape(cells[2]) == raw                        # lossless


def test_the_plain_twin_is_printed_unchanged(db, capsys):
    """A cell with nothing to escape must not change shape: existing readers keep working."""
    rc, out = _sql(db, capsys, "SELECT page, t, text FROM texts WHERE t='3'")
    assert rc == 0 and out == "20101\t3\tplain\n"


def test_a_backslash_is_escaped_so_the_escaping_is_reversible(db, capsys):
    """Without this a literal `\\t` (backslash, t) and a real TAB print identically."""
    rc, out = _sql(db, capsys, "SELECT 'a\\tb', 'a' || char(9) || 'b'")
    assert rc == 0
    c1, c2 = out.rstrip("\n").split("\t")
    assert c1 == "a\\\\tb" and c2 == "a\\tb"
    assert _unescape(c1) == "a\\tb" and _unescape(c2) == "a\tb"


def test_null_and_empty_string_are_told_apart(db, capsys):
    rc, out = _sql(db, capsys, "SELECT NULL, ''")
    assert rc == 0 and out == "\\N\t\n"


def test_a_carriage_return_is_escaped(db, capsys):
    rc, out = _sql(db, capsys, "SELECT 'x' || char(13) || 'y'")
    assert rc == 0 and out == "x\\ry\n"


def test_json_gives_the_exact_values_and_the_column_names(db, capsys):
    rc, out = _sql(db, capsys, "SELECT page, t, text AS body FROM texts WHERE t IN ('1','2') ORDER BY t",
                   "--json")
    assert rc == 0
    doc = json.loads(out)
    assert doc["schema"] == "x4effective.sql/1"
    assert doc["columns"] == ["page", "t", "body"]
    assert doc["rows"] == [["20101", "1", TAB_TEXT], ["20101", "2", NL_TEXT]]


def test_json_keeps_null_as_null(db, capsys):
    rc, out = _sql(db, capsys, "SELECT NULL AS a, '' AS b", "--json")
    assert rc == 0 and json.loads(out)["rows"] == [[None, ""]]


def test_the_write_refusal_is_unchanged_under_json(db, capsys):
    rc, _ = _sql(db, capsys, "DELETE FROM entities", "--json")
    assert rc == 2


def test_the_help_documents_the_escaping(capsys):
    with pytest.raises(SystemExit):
        _effectivecli.main(["sql", "--help"])
    out = capsys.readouterr().out
    assert "\\t" in out and "\\n" in out and "\\N" in out and "--json" in out


def test_find_escapes_a_tab_in_the_resolved_name(db, capsys):
    """The same shape in a second command: `find` prints name<TAB>class<TAB>origin<TAB>shown."""
    assert _effectivecli.main(["--db", str(db), "find", "tab"]) == 0
    rows = [ln for ln in capsys.readouterr().out.split("\n") if ln.strip()]
    assert len(rows) == 1
    cells = rows[0].split("\t")
    assert cells[0] == "weapon_tab_macro" and len(cells) == 4, cells
    assert _unescape(cells[3]) == "Tab\tName"

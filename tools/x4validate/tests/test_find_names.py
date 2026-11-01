"""x4effective find: in-game names resolved from the merged English text, never a silent zero.

The grammar cases are the corpus's own (MEASURED 2026-11-01 over the merged t-files): a leading `(...)`
comment that is not displayed, `\\(`/`\\)` literal parentheses, nested `{page,id}` references, and a
reference to text that does not exist."""
from __future__ import annotations

import sqlite3

import pytest

from x4validate import _effective, _effectivecli, _merge, _text


def test_strip_comments_follows_the_measured_grammar():
    assert _text.strip_comments("M0(M stands for military)") == "M0"
    assert _text.strip_comments("(XEN XL Omega Turret Mk1){20202,603}") == "{20202,603}"
    assert _text.strip_comments(r"(Wyvern \(Gas\) Vanguard){1,2}") == "{1,2}"
    assert _text.strip_comments(r"Storage \(ZSm\) module") == "Storage (ZSm) module"
    assert _text.strip_comments("a (outer (inner) still) b") == "a  b"


def test_resolver_expands_nested_refs_and_counts_what_it_cannot():
    r = _text.Resolver({("1", "1"): "(label){1,2} {1,3}", ("1", "2"): "Omega", ("1", "3"): "Turret",
                        ("1", "4"): "{1,5}", ("1", "5"): "{1,4}"})
    assert r.ref("1", "1") == ("Omega Turret", "ok")
    assert r.ref("9", "9") == (None, "dangling")
    assert r.ref("1", "4")[1] == "cycle"
    assert r.value("{1,2} Mk2") == ("Omega Mk2", "ok")
    assert r.value("Enter") == ("Enter", "literal")
    assert r.value("{9,9}") == ("{9,9}", "dangling") and r.problems["dangling"] == 1


def _world(tmp_path):
    ref = tmp_path / "reference"
    (ref / "t").mkdir(parents=True)
    (ref / "t" / "0001-l044.xml").write_bytes(
        b'<language id="44"><page id="20101">'
        b'<t id="1">M0(M stands for "military")</t>'
        b'<t id="2">(Omega){20101,3} Turret</t><t id="3">XEN XL Omega</t>'
        b'<t id="4">Old Name</t></page></language>')
    m = ref / "assets" / "units" / "size_xl" / "macros"
    m.mkdir(parents=True)
    for name, ident in (("ship_xen_xl_mothership_01_b_macro", "{20101,1}"),
                        ("turret_xen_xl_battleship_01_mk1_macro", "{20101,2}"),
                        ("ship_renamed_macro", "{20101,4}"),
                        ("ship_dangling_macro", "{77,77}")):
        (m / f"{name}.xml").write_bytes(
            f'<macros><macro name="{name}" class="ship_xl"><properties>'
            f'<identification name="{ident}"/></properties></macro></macros>'.encode())
    exts = tmp_path / "extensions"
    mod = exts / "renamer"
    (mod / "t").mkdir(parents=True)
    (mod / "content.xml").write_bytes(b'<content id="renamer" name="R" version="1"/>')
    # A mod's t-file diff must reach the name: the store holds the MERGED text. Node replace, the form
    # real mods use (a `/text()` selector is 0 of 22,457 corpus selectors and our merge does not apply it).
    (mod / "t" / "0001-l044.xml").write_bytes(
        b"<diff><replace sel=\"//page[@id='20101']/t[@id='4']\"><t id=\"4\">Shiny New Name</t></replace></diff>")
    # And a mod that puts English in the NEUTRAL file, which l044 must override for the same id.
    (mod / "t" / "0001.xml").write_bytes(
        b'<language><page id="20101"><t id="1">Neutral M0 that l044 overrides</t>'
        b'<t id="9">only neutral</t></page></language>')
    return ref, exts


@pytest.fixture
def db(tmp_path, monkeypatch):
    ref, exts = _world(tmp_path)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    path = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), path, dirs=[exts], kinds=("macro",))
    return path


def test_the_store_holds_merged_english_over_neutral(db):
    texts = _text.load(sqlite3.connect(db))
    assert texts[("20101", "4")] == "Shiny New Name"                   # the mod's t-diff applied
    assert texts[("20101", "1")].startswith("M0")                      # l044 beat the neutral file
    assert texts[("20101", "9")] == "only neutral"                     # neutral-only text kept


@pytest.mark.parametrize("query,expect", [
    ("m0", ["ship_xen_xl_mothership_01_b_macro"]),
    ("omega", ["turret_xen_xl_battleship_01_mk1_macro"]),
    ("shiny", ["ship_renamed_macro"]),
])
def test_find_matches_what_the_player_reads(db, capsys, query, expect):
    assert _effectivecli.main(["--db", str(db), "find", query]) == 0
    out, err = capsys.readouterr()
    assert [ln.split("\t")[0] for ln in out.splitlines() if ln.strip()] == expect
    assert "among 4 named macro entit(ies)" in err


def test_the_comment_is_not_the_name(db, capsys):
    """'military' is in M0's author comment, which the game never shows."""
    assert _effectivecli.main(["--db", str(db), "find", "military"]) == 1
    assert "0 match(es)" in capsys.readouterr().err


def test_an_unresolvable_name_is_counted_and_still_findable_by_its_raw_text(db, capsys):
    assert _effectivecli.main(["--db", str(db), "find", "{77,77}"]) == 0
    out, err = capsys.readouterr()
    assert "ship_dangling_macro" in out and "[dangling reference]" in out
    assert "3 fully resolved, 1 with an unresolvable reference" in err


def test_a_store_without_the_text_table_says_rebuild_never_zero(db, capsys):
    con = sqlite3.connect(db)
    con.execute("DROP TABLE texts")
    con.commit()
    con.close()
    assert _effectivecli.main(["--db", str(db), "find", "m0"]) == 2
    assert "rebuild" in capsys.readouterr().err

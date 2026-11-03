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


def test_a_match_on_the_internal_id_still_shows_the_in_game_name(db, capsys):
    """MEASURED on the live store: `find mothership` matched the macro id and printed the id again,
    where the player's name ("M0") is the whole point of the command."""
    assert _effectivecli.main(["--db", str(db), "find", "mothership"]) == 0
    rows = [ln.split("\t") for ln in capsys.readouterr()[0].splitlines() if ln.strip()]
    assert rows == [["ship_xen_xl_mothership_01_b_macro", "ship_xl", "base", "M0"]]


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


# --------------------------------------------------------------------------- release review 4.1.0: T13

def _insert_unnamed(db, kind, n=3):
    con = sqlite3.connect(db)
    for i in range(n):
        con.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)",
                    (9000 + i, kind, f"thing_{i}", "k", "libraries/ships.xml", "base", None))
    con.commit()
    con.close()


def _duplicate_entity(db, name, vpath):
    """A second entity with the same (kind, name), as the live store has 34 macros with."""
    con = sqlite3.connect(db)
    eid, kind, klass, origin = con.execute("SELECT id, kind, klass, origin FROM entities WHERE name=?", (name,)).fetchone()
    con.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", (8000, kind, name, klass, vpath, origin, None))
    con.execute("INSERT INTO attrs SELECT 8000, prop, value, value_num, origin, chain FROM attrs WHERE entity_id=?", (eid,))
    con.commit()
    con.close()


def test_a_kind_with_no_names_points_at_the_kinds_that_have_them(db, capsys):
    """`find X --kind ship` over a kind that carries no name property searched nothing and said "0 match(es)
    among 0 named", exit 1: an absence, for what was a non-answer."""
    _insert_unnamed(db, "ship")
    assert _effectivecli.main(["--db", str(db), "find", "omega", "--kind", "ship"]) == 3
    err = capsys.readouterr().err
    assert "0 of 3 ship entit(ies) carry a name" in err
    assert "--kind macro" in err and "macro (4)" in err


def test_a_class_filter_that_matches_nothing_is_the_same_non_answer(db, capsys):
    """Twin of the kind clause."""
    assert _effectivecli.main(["--db", str(db), "find", "omega", "--class", "no_such_class"]) == 3
    err = capsys.readouterr().err
    assert "0 of 0 macro class no_such_class entit(ies)" in err


def test_a_real_zero_over_named_entities_stays_an_absence_rc_1(db, capsys):
    """Twin: names were searched and none matched. That IS an answer, with its denominator."""
    assert _effectivecli.main(["--db", str(db), "find", "zzzz-nothing"]) == 1
    err = capsys.readouterr().err
    assert "0 match(es)" in err and "among 4 named" in err and "carry a name" not in err


def test_a_kind_where_no_kind_has_names_says_that(db, capsys):
    con = sqlite3.connect(db)
    con.execute("DELETE FROM attrs")
    con.commit()
    con.close()
    assert _effectivecli.main(["--db", str(db), "find", "omega"]) == 3
    assert "no kind in this store carries a name" in capsys.readouterr().err


def test_entities_sharing_an_id_are_each_listed_and_counted(db, capsys):
    """They were keyed by name, so a second entity vanished from the rows and from the denominator."""
    _duplicate_entity(db, "ship_renamed_macro", "assets/other/ship_renamed_macro.xml")
    assert _effectivecli.main(["--db", str(db), "find", "shiny"]) == 0
    out, err = capsys.readouterr()
    rows = [ln.split("\t") for ln in out.splitlines() if ln.strip()]
    assert [r[0] for r in rows] == ["ship_renamed_macro", "ship_renamed_macro"]
    assert "assets/other/ship_renamed_macro.xml" in out
    assert all("shares this id" in r[-1] for r in rows)
    assert "2 match(es)" in err and "among 5 named" in err and "1 id(s) are on more than one entity" in err


def test_a_unique_id_carries_no_duplicate_noise(db, capsys):
    """Twin; the exact row shape is pinned by test_a_match_on_the_internal_id_still_shows_the_in_game_name."""
    assert _effectivecli.main(["--db", str(db), "find", "shiny"]) == 0
    out, err = capsys.readouterr()
    assert "shares this id" not in out and "more than one entity" not in err

"""Issue #11 follow-up: an op that lands, at its own load position, on content ANOTHER mod
added (an `<add>`, or the siblings a multi-element `<replace>` inserted).

User decisions (2026-10-18): a later REPLACE or REMOVE on added content FAILS x4compat
(HARD, named as added by that mod; a partial overwrite of k>1 inserted elements says so;
the engine's resolution after an earlier insert is UNMEASURED). A plain ADD into added
content is the compatibility-patch pattern: REPORTED (advisory), never failing -- but two
mods adding the same key under it are the normal duplicate HARD + SOFT, as under a base
parent."""

from __future__ import annotations

import contextlib
import io
import json

from x4validate import _compat, _merge

VPATH = "md/m.xml"
BASE = ('<mdscript name="M"><cues><cue name="E"><actions>'
        '<set_value name="$MissionCount" exact="1"/><set_value name="$other" exact="5"/>'
        '</actions></cue></cues></mdscript>')
SEL1 = "(//cue[@name='E']//set_value[@name='$MissionCount'])[1]"
THREE = ('<set_value name="reset" exact="0"/><set_value name="$MissionCount" exact="0"/>'
         '<do_if value="true"><set_value name="$MissionCount" exact="10"/></do_if>')


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path, mods, base=BASE):
    ref = tmp_path / "reference"
    _w(ref / VPATH, base)
    ext = tmp_path / "extensions"
    for folder, diff in mods.items():
        _w(ext / folder / "content.xml",
           f'<content id="{folder}" name="{folder}" version="1"></content>')
        _w(ext / folder / VPATH, diff)
    return ext, _merge.Config(reference=ref)


def _rep(sel, payload):
    return f'<diff><replace sel="{sel}">{payload}</replace></diff>'


def _exit(ext, cfg, *extra):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _compat.main(["check", *extra, "--ext-dir", str(ext),
                           "--reference", str(cfg.reference)])
    return rc, buf.getvalue()


# --- gap 1: a replace/remove on added content -------------------------------------

def test_a_partial_overwrite_of_inserted_siblings_fails_and_says_so(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_mod": _rep(SEL1, THREE),
        "b_mod": _rep(SEL1, '<set_value name="$MissionCount" exact="7"/>')})
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert len(hard) == 1, hard
    row = hard[0]
    assert row.mods == ["a_mod", "b_mod"] and row.winner == "b_mod"
    assert row in rep.hard
    assert "added by 'a_mod'" in row.detail, row.detail
    assert "partial: 1 of 3 elements a_mod inserted; the others survive" in row.detail
    assert "UNMEASURED" in row.detail
    assert "supply" not in row.detail
    rc, out = _exit(ext, cfg)
    assert rc == 1
    assert "node/key : /mdscript/cues/cue/actions/set_value[2]" in out, out
    assert "~" not in out, out          # no synthetic identity reaches the reader


def test_TWIN_two_one_element_replaces_are_the_plain_later_wins_row(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_mod": _rep(SEL1, '<set_value name="$MissionCount" exact="3"/>'),
        "b_mod": _rep(SEL1, '<set_value name="$MissionCount" exact="7"/>')})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod"
    assert "loads last and wins" in hard[0].detail
    for word in ("added by", "partial", "UNMEASURED", "supply"):
        assert word not in hard[0].detail, hard[0].detail


def test_replacing_an_attribute_of_an_added_node_fails_naming_the_adder(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_mod": '<diff><add sel="//cues"><cue name="New" x="1"/></add></diff>',
        "b_mod": "<diff><replace sel=\"//cue[@name='New']/@x\">2</replace></diff>"})
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod"
    assert "added by 'a_mod'" in hard[0].detail and "partial" not in hard[0].detail
    assert hard[0].target_path == "/mdscript/cues/cue[2]/@x"
    assert not rep.added_into
    rc, out = _exit(ext, cfg, "--json")
    assert rc == 1
    row = json.loads(out[out.index("{"):])["collisions"][0]
    assert row["target_path"] == "/mdscript/cues/cue[2]/@x"


def test_removing_an_added_node_fails_naming_the_adder(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_mod": '<diff><add sel="//cues"><cue name="New"/></add></diff>',
        "b_mod": "<diff><remove sel=\"//cue[@name='New']\"/></diff>"})
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod", hard
    assert "added by 'a_mod'" in hard[0].detail
    assert _exit(ext, cfg)[0] == 1


# --- gap 2: an add into added content ---------------------------------------------

ADDED_PARENT = '<diff><add sel="//cues"><cue name="New"><cues/></cue></add></diff>'
INTO = "<diff><add sel=\"//cue[@name='New']/cues\">{}</add></diff>"


def test_two_mods_adding_the_same_key_under_an_added_node_are_the_duplicate_row(tmp_path):
    ext, cfg = _world(tmp_path, {"a_mod": ADDED_PARENT,
                                 "b_mod": INTO.format('<cue name="Dup"/>'),
                                 "c_mod": INTO.format('<cue name="Dup"/>')})
    rep = _compat.analyze(ext, config=cfg)
    hard, soft = rep.by_kind("HARD"), rep.by_kind("SOFT")
    assert [(c.mods, c.winner) for c in hard] == [(["b_mod", "c_mod"], "c_mod")], hard
    assert "cue#Dup" in hard[0].detail and "duplicate" in hard[0].detail
    assert "loads last and wins" not in hard[0].detail
    assert [c.mods for c in soft] == [["b_mod", "c_mod"]]
    assert "added by 'a_mod'" in soft[0].detail


def test_two_mods_adding_different_children_under_an_added_node_do_not_fail(tmp_path):
    ext, cfg = _world(tmp_path, {"a_mod": ADDED_PARENT,
                                 "b_mod": INTO.format('<cue name="B"/>'),
                                 "c_mod": INTO.format('<cue name="C"/>')})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard, rep.hard
    assert [c.mods for c in rep.by_kind("SOFT")] == [["b_mod", "c_mod"]]
    assert sorted((m.mod, m.added_by) for m in rep.added_into) == [
        ("b_mod", "a_mod"), ("c_mod", "a_mod")]
    assert _exit(ext, cfg)[0] == 0


def test_a_lone_add_into_added_content_is_reported_not_failed(tmp_path):
    ext, cfg = _world(tmp_path, {"a_mod": ADDED_PARENT,
                                 "b_mod": INTO.format('<cue name="B"/>')})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.collisions, rep.collisions
    assert [(m.mod, m.added_by, m.target_path) for m in rep.added_into] == [
        ("b_mod", "a_mod", "/mdscript/cues/cue[2]/cues")]
    rc, out = _exit(ext, cfg)
    assert rc == 0
    assert "ADDS INTO CONTENT ANOTHER MOD ADDED" in out and "a_mod" in out, out
    rc, out = _exit(ext, cfg, "--json")
    payload = json.loads(out[out.index("{"):])
    assert payload["added_into"][0]["added_by"] == "a_mod"


def test_TWIN_a_lone_add_into_a_BASE_parent_is_not_reported(tmp_path):
    ext, cfg = _world(tmp_path, {"a_mod": '<diff><add sel="//cues"><cue name="X"/></add></diff>',
                                 "b_mod": "<diff><add sel=\"//cue[@name='E']/actions\">"
                                          '<set_value name="$b"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.collisions and not rep.added_into


def test_a_candidate_run_keeps_only_the_advisories_it_takes_part_in(tmp_path):
    ext, cfg = _world(tmp_path, {"a_mod": ADDED_PARENT,
                                 "b_mod": INTO.format('<cue name="B"/>'),
                                 "z_mod": '<diff><add sel="//cues"><cue name="Z"/></add></diff>'})
    assert _compat.analyze(ext, candidate=ext / "z_mod", config=cfg).added_into == []
    kept = _compat.analyze(ext, candidate=ext / "a_mod", config=cfg).added_into
    assert [(m.mod, m.added_by) for m in kept] == [("b_mod", "a_mod")]


# --- gap 3: an UNEVALUATED guard keeps its LOAD-POSITION target -------------------

def test_an_unevaluable_guard_keeps_the_target_at_its_load_position(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_mod": _rep(SEL1, THREE),
        "b_mod": (f'<diff><replace sel="{SEL1}" if="not valid ((">'
                  '<set_value name="$MissionCount" exact="7"/></replace></diff>')})
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert len(hard) == 1, hard
    assert hard[0].target_tree == "final", hard[0]
    assert hard[0].target_path == "/mdscript/cues/cue/actions/set_value[2]"
    assert not hard[0].winner_qualified and "guard UNEVALUATED" in hard[0].detail


def test_a_last_loading_ADD_is_never_said_to_win(tmp_path):
    """An add replaces nothing, so 'loads last and wins' is wrong for it (gap 1)."""
    ext, cfg = _world(tmp_path, {
        "a_mod": "<diff><replace sel=\"//cue[@name='E']/actions\"><actions/></replace></diff>",
        "b_mod": "<diff><add sel=\"//cue[@name='E']/actions\"><set_value name=\"$b\"/></add></diff>"})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1, hard
    assert "loads last and wins" not in hard[0].detail, hard[0].detail
    assert "only inserts children" in hard[0].detail

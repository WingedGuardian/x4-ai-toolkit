"""F387: a remove-then-re-add x4compat did not see -- a NESTED keyed node, and a node in a
non-library file (macro files, md) -- now continues the removed identity, so the re-adder joins
the node's row exactly as F385 made it for a top-level library entry.

The user's binding rules are F385's and are unchanged here: the row is ADVISORY
(`readded_after_removal`, "CONFIRM INTENT", exit 0) iff the re-adder declares a dependency on
the remover and on every other applied mod on the node, HARD otherwise.

Identity keys (the 4.3 plan's defaults): id/name at any depth in libraries/index/t;
`connection@name` and `macro@name` in macro/component files; `cue`/`library`/`label` names in
md/aiscripts. A node with no key has no identity to revive (twin below).
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _compat, _compat_state, _merge


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _mod(ext, folder, files, deps=()):
    dep_xml = "".join(f'<dependency id="{d}"/>' for d in deps)
    _w(ext / folder / "content.xml",
       f'<content id="{folder}" name="{folder}" version="1">{dep_xml}</content>')
    for vpath, text in files.items():
        _w(ext / folder / vpath, text)


def _world(tmp_path, vpath, base, rm, readd, deps=("a_rm",)):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / vpath, base)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {vpath: f"<diff>{rm}</diff>"})
    _mod(ext, "b_readd", {vpath: f"<diff>{readd}</diff>"}, deps=deps)
    rep = _compat.analyze(ext, config=_merge.Config(reference=ref, include_packed_dlc=False))
    assert not [s for s in rep.skipped if "disagreed" in s.why], rep.skipped
    return rep


def _readds(rep):
    return [(r.mod, r.removed_by) for r in rep.readded_after_removal]


def _hard_readds(rep):
    return [c for c in rep.by_kind("HARD") if "adds it again" in c.detail]


WARES = "libraries/wares.xml"
NESTED_BASE = ('<wares><ware id="ore"><price average="100"/>'
               '<production method="default" name="p1" time="10"/></ware></wares>')
NESTED_RM = "<remove sel=\"//ware[@id='ore']/production[@name='p1']\"/>"
NESTED_ADD = ("<add sel=\"//ware[@id='ore']\">"
              '<production method="default" name="p1" time="20"/></add>')


def test_a_NESTED_keyed_node_removed_and_re_added_is_a_confirm_intent_row(tmp_path):
    rep = _world(tmp_path, WARES, NESTED_BASE, NESTED_RM, NESTED_ADD)
    assert _readds(rep) == [("b_readd", "a_rm")], rep.collisions


def test_the_identical_signal_finds_a_NESTED_node_by_its_removal_path(tmp_path):
    same = NESTED_ADD.replace('time="20"', 'time="10"')
    rep = _world(tmp_path, WARES, NESTED_BASE, NESTED_RM, same)
    assert [r.signals["identical_to_pre_removal"] for r in rep.readded_after_removal] == ["yes"]
    rep = _world(tmp_path / "differs", WARES, NESTED_BASE, NESTED_RM, NESTED_ADD)
    assert [r.signals["identical_to_pre_removal"] for r in rep.readded_after_removal] == ["no"]


def test_a_keyed_DESCENDANT_of_a_removed_node_continues_its_identity(tmp_path):
    """x_edit changes the production, a_rm removes the whole ware, b_readd adds the ware back
    WITH the production: the production is the same node again, so x_edit's change is one
    that went with the removal (named on the production's row)."""
    ref = tmp_path / "reference"
    _w(ref / WARES, NESTED_BASE)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {WARES: "<diff><remove sel=\"//ware[@id='ore']\"/></diff>"},
         deps=("a_edit",))
    _mod(ext, "a_edit", {WARES: "<diff><replace sel=\"//ware[@id='ore']/production[@name='p1']"
                                "/@time\">99</replace></diff>"})
    _mod(ext, "b_readd", {WARES: '<diff><add sel="/wares"><ware id="ore"><price average="1"/>'
                                 '<production method="default" name="p1" time="5"/></ware>'
                                 '</add></diff>'})
    rep = _compat.analyze(ext, config=_merge.Config(reference=ref, include_packed_dlc=False))
    rows = [c for c in rep.collisions if "production" in (c.target_path or c.target)
            and "a_edit" in c.mods and "b_readd" in c.mods]
    assert len(rows) == 1 and rows[0].winner == "b_readd", rep.collisions


def test_TWIN_nested_without_the_dependency_is_HARD(tmp_path):
    rep = _world(tmp_path, WARES, NESTED_BASE, NESTED_RM, NESTED_ADD, deps=())
    assert _readds(rep) == [] and len(_hard_readds(rep)) == 1, rep.collisions
    assert _hard_readds(rep)[0].winner == "b_readd"


def test_TWIN_nested_a_DIFFERENT_key_is_no_re_add(tmp_path):
    other = NESTED_ADD.replace('name="p1"', 'name="p2"')
    rep = _world(tmp_path, WARES, NESTED_BASE, NESTED_RM, other)
    assert _readds(rep) == [] and _hard_readds(rep) == [], rep.collisions


def test_TWIN_nested_an_UNKEYED_node_has_no_identity_to_revive(tmp_path):
    rm = "<remove sel=\"//ware[@id='ore']/price\"/>"
    add = "<add sel=\"//ware[@id='ore']\"><price average=\"5\"/></add>"
    rep = _world(tmp_path, WARES, NESTED_BASE, rm, add)
    assert _readds(rep) == [] and _hard_readds(rep) == [], rep.collisions


MACRO = "assets/units/size_s/macros/ship_y_macro.xml"
MACRO_BASE = ('<macros><macro name="m1" class="ship_s"><connections>'
              '<connection name="c1" ref="a"/></connections></macro></macros>')


def test_a_MACRO_removed_and_re_added_in_a_macro_file(tmp_path):
    rm = "<remove sel=\"/macros/macro[@name='m1']\"/>"
    add = '<add sel="/macros"><macro name="m1" class="ship_s"/></add>'
    rep = _world(tmp_path, MACRO, MACRO_BASE, rm, add)
    assert _readds(rep) == [("b_readd", "a_rm")], rep.collisions


def test_a_CONNECTION_removed_and_re_added_in_a_macro_file(tmp_path):
    rm = "<remove sel=\"//connection[@name='c1']\"/>"
    add = '<add sel="//connections"><connection name="c1" ref="b"/></add>'
    rep = _world(tmp_path, MACRO, MACRO_BASE, rm, add, deps=())
    assert len(_hard_readds(rep)) == 1, rep.collisions


def test_TWIN_macro_file_an_UNKEYED_tag_is_no_re_add(tmp_path):
    base = MACRO_BASE.replace("<connections>", '<properties><hull max="1"/></properties><connections>')
    rm = "<remove sel=\"//macro[@name='m1']/properties\"/>"
    add = "<add sel=\"//macro[@name='m1']\"><properties/></add>"
    rep = _world(tmp_path, MACRO, base, rm, add)
    assert _readds(rep) == [] and _hard_readds(rep) == [], rep.collisions


MD = "md/thing.xml"
MD_BASE = '<mdscript name="Thing"><cues><cue name="c1"/><cue name="c2"/></cues></mdscript>'


def test_a_CUE_removed_and_re_added_in_md(tmp_path):
    rm = "<remove sel=\"//cue[@name='c1']\"/>"
    add = '<add sel="//cues"><cue name="c1"/></add>'
    rep = _world(tmp_path, MD, MD_BASE, rm, add)
    assert _readds(rep) == [("b_readd", "a_rm")], rep.collisions


# --- user decisions 2026-11-18 ---------------------------------------------------------------

COMP = "assets/units/size_xl/ship_z.xml"
COMP_BASE = ('<components><component name="ship_z" class="ship_xl"><connections>'
             '<connection name="c1" tags="a"/><connection name="c2" tags="a"/>'
             '</connections></component></components>')


def test_DECISION1_a_REPLACE_of_connections_stays_one_SUBTREE_row(tmp_path):
    """A <replace> of a parent does NOT continue its children's identities: the earlier
    edits inside it are wiped (one SUBTREE row), never one HARD row per connection."""
    edit = ("<replace sel=\"//connection[@name='c1']/@tags\">x</replace>"
            "<replace sel=\"//connection[@name='c2']/@tags\">x</replace>")
    wipe = ('<replace sel="//connections"><connections><connection name="c1" tags="b"/>'
            '<connection name="c2" tags="b"/></connections></replace>')
    rep = _world(tmp_path, COMP, COMP_BASE, edit, wipe, deps=())
    assert len(rep.by_kind("SUBTREE")) == 1, rep.collisions
    assert [c for c in rep.by_kind("HARD") if "connection" in (c.target_path or "")] == []
    assert rep.readded_after_removal == []


def test_TWIN_DECISION1_a_REMOVE_then_another_mods_re_add_is_confirm_intent(tmp_path):
    rm = "<remove sel=\"//connection[@name='c1']\"/>"
    add = '<add sel="//connections"><connection name="c1" tags="b"/></add>'
    rep = _world(tmp_path, COMP, COMP_BASE, rm, add)
    assert _readds(rep) == [("b_readd", "a_rm")], rep.collisions


RM_C1 = "<remove sel=\"//connection[@name='c1']\"/>"
REPLACE_WITH_C1 = ('<replace sel="//connections"><connections><connection name="c1" tags="b"/>'
                   '<connection name="c2" tags="a"/></connections></replace>')


def test_DECISION1_remove_then_a_REPLACE_of_the_parent_is_no_revival(tmp_path):
    """User ruling 2026-11-18: revival is <remove>-then-re-add ONLY. An earlier mod removing
    c1 and a later one REPLACING the parent with a copy holding c1 is the replace's one
    SUBTREE row -- never a CONFIRM INTENT row (declared) nor a HARD re-add (undeclared)."""
    for sub, deps in (("declared", ("a_rm",)), ("undeclared", ())):
        rep = _world(tmp_path / sub, COMP, COMP_BASE, RM_C1, REPLACE_WITH_C1, deps=deps)
        assert rep.readded_after_removal == [], (sub, rep.collisions)
        assert _hard_readds(rep) == [], (sub, rep.collisions)
        assert len(rep.by_kind("SUBTREE")) == 1, (sub, rep.collisions)


def test_TWIN_DECISION1_remove_then_ADD_with_no_dependency_is_still_a_HARD_re_add(tmp_path):
    add = '<add sel="//connections"><connection name="c1" tags="b"/></add>'
    rep = _world(tmp_path, COMP, COMP_BASE, RM_C1, add, deps=())
    assert rep.readded_after_removal == [] and len(_hard_readds(rep)) == 1, rep.collisions


def test_TWIN_DECISION1_a_REPLACE_then_a_later_ADD_of_the_same_key_is_no_revival(tmp_path):
    """The replace consumed the removed slot without reviving it: a third mod adding c1 after
    the replace adds a second c1, it does not revive the first mod's removed one."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / COMP, COMP_BASE)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {COMP: f"<diff>{RM_C1}</diff>"})
    _mod(ext, "b_readd", {COMP: f"<diff>{REPLACE_WITH_C1}</diff>"}, deps=("a_rm",))
    _mod(ext, "c_add", {COMP: '<diff><add sel="//connections"><connection name="c1"/></add></diff>'},
         deps=("a_rm", "b_readd"))
    rep = _compat.analyze(ext, config=_merge.Config(reference=ref, include_packed_dlc=False))
    assert rep.readded_after_removal == [] and _hard_readds(rep) == [], rep.collisions


TEXTREF_BASE = ('<wares><ware id="ore"><price average="100"/>'
                '<production method="default" name="{20206,101}" time="10"/></ware></wares>')


def test_DECISION2_a_nested_TEXT_REFERENCE_name_is_no_identity(tmp_path):
    rm = "<remove sel=\"//ware[@id='ore']/production[@name='{20206,101}']\"/>"
    add = ("<add sel=\"//ware[@id='ore']\">"
           '<production method="default" name="{20206,101}" time="20"/></add>')
    rep = _world(tmp_path, WARES, TEXTREF_BASE, rm, add)
    assert _readds(rep) == [] and _hard_readds(rep) == [], rep.collisions


def test_TWIN_DECISION2_a_plain_nested_name_still_is(tmp_path):
    rep = _world(tmp_path, WARES, NESTED_BASE, NESTED_RM, NESTED_ADD)
    assert _readds(rep) == [("b_readd", "a_rm")]
    el = _merge.parse_bytes(b'<wares><ware id="w" name="{1,2}"/></wares>')[0]
    assert _compat_state.identity_key(WARES, el) == ("ware", "id=w"), "top level: F385 key"
    top = _merge.parse_bytes(b'<wares><thing name="{1,2}"/></wares>')[0]
    assert _compat_state.identity_key(WARES, top) == ("thing", "name={1,2}"), \
        "a TOP-LEVEL name-only entry keeps F385's key, text reference or not"


ORE_BASE = '<wares><ware id="ore"><price average="100"/></ware></wares>'
RM_ORE = "<remove sel=\"//ware[@id='ore']\"/>"


def _file_world(tmp_path, b_text, deps=("a_rm",)):
    ref = tmp_path / "reference"
    _w(ref / WARES, ORE_BASE)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {WARES: f"<diff>{RM_ORE}</diff>"})
    _mod(ext, "b_readd", {WARES: b_text}, deps=deps)
    rep = _compat.analyze(ext, config=_merge.Config(reference=ref, include_packed_dlc=False))
    assert not [s for s in rep.skipped if "disagreed" in s.why], rep.skipped
    return rep


def test_DECISION3_a_COMPLETE_file_re_defining_a_removed_entry_is_confirm_intent(tmp_path):
    rep = _file_world(tmp_path, '<wares><ware id="ore"><price average="7"/></ware></wares>')
    got = [(r.mod, r.removed_by, r.sel, r.signals["guard"], r.signals["identical_to_pre_removal"])
           for r in rep.readded_after_removal]
    assert got == [("b_readd", "a_rm", "(complete file)", "none", "no")], rep.collisions


def test_TWIN_DECISION3_complete_file_without_the_dependency_is_HARD(tmp_path):
    rep = _file_world(tmp_path, '<wares><ware id="ore"><price average="7"/></ware></wares>',
                      deps=())
    assert rep.readded_after_removal == [] and len(_hard_readds(rep)) == 1, rep.collisions


def test_TWIN_DECISION3_complete_file_identical_entry_reads_yes(tmp_path):
    rep = _file_world(tmp_path, f"{ORE_BASE}")
    assert [r.signals["identical_to_pre_removal"] for r in rep.readded_after_removal] == ["yes"]


def test_TWIN_DECISION3_complete_file_with_OTHER_entries_only_is_no_re_add(tmp_path):
    rep = _file_world(tmp_path, '<wares><ware id="ice"><price average="7"/></ware></wares>')
    assert rep.readded_after_removal == [] and _hard_readds(rep) == [], rep.collisions


def test_identity_key_per_file_class():
    el = _merge.parse_bytes(b'<x id="1" name="n"/>')
    assert _compat_state.identity_key("libraries/wares.xml", el) == ("x", "id=1")
    conn = _merge.parse_bytes(b'<connection name="c"/>')
    assert _compat_state.identity_key(MACRO, conn) == ("connection", "name=c")
    assert _compat_state.identity_key(MACRO, el) is None, "only connection/macro in assets"
    cue = _merge.parse_bytes(b'<cue name="c"/>')
    assert _compat_state.identity_key("aiscripts/a.xml", cue) == ("cue", "name=c")
    assert _compat_state.identity_key("md/a.xml", conn) is None
    assert _compat_state.identity_key("readme/a.xml", cue) is None

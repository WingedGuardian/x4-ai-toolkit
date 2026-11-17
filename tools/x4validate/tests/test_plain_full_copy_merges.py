"""Issue #23: a MOD's complete macro/component file at a PLAIN base-game path MERGES.

MEASURED (debug.txt 2026-11-17, game 9.00, one launch, two load passes): `xspvro` ships
complete `<macros>` files at four base-game ship macro paths and a complete `<components>`
file at `assets/units/size_s/ship_xen_s_fighter_02.xml`, each over a base file (plus a VRO
`<diff>`) with the same root. Two later mods' ops naming those macros/components were
skipped with "Multiple matching nodes ... Skipping node" (23 distinct ops in 54 lines over
the two passes, x4validate predicted 0), and the engine logged `LoadoutDB::AddLoadout: Loadout with ID
'<macro>.default' already exists` -- the document holds BOTH same-named nodes. The plain
sibling of issue #17 (F358), reusing its append ("merge") step.

Only that class changes: `assets/units/**/macros/*_macro.xml` with root `macros`, and a
component file under `assets/units/` with root `components`, each a MOD layer over a document the GAME
supplies with the same root. Each twin below pins a shape that must NOT change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from x4validate import _check, _compat, _compat_state, _debugcli, _debuglog, _effectivecli, _merge

REL = "assets/units/size_xl/macros/ship_x_macro.xml"
COMP = "assets/units/size_s/ship_x.xml"
SEL = "/macros/macro[@name='ship_x_macro']/properties"


def _macro(value: int, name: str = "ship_x_macro") -> str:
    return (f'<macros><macro name="{name}" class="ship_xl"><properties>'
            f'<hull max="{value}"/></properties></macro></macros>')


def _comp(value: int) -> str:
    return (f'<components><component name="ship_x" class="ship_s">'
            f'<connections><connection name="c{value}"/></connections></component></components>')


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _manifest(d: Path, mid: str) -> None:
    _w(d / "content.xml", f'<content id="{mid}" name="{mid}" version="1"/>')


def _world(tmp_path: Path, rel: str = REL, base: str | None = None,
           copy_text: str | None = None, patch_ops: str | None = None):
    """reference ships *base* at *rel*; a_copy ships *copy_text* there (complete);
    b_patch ships a plain-path <diff> of *patch_ops*. Load order a, b."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    if base is not None:
        _w(ref / rel, base)
    ext = tmp_path / "extensions"
    a, b = ext / "a_copy", ext / "b_patch"
    _manifest(a, "a_copy")
    if copy_text is not None:
        _w(a / rel, copy_text)
    _manifest(b, "b_patch")
    if patch_ops is not None:
        _w(b / rel, f"<diff>{patch_ops}</diff>")
    return ref, a, b


def _cfg(ref: Path, *overlays: Path) -> _merge.Config:
    return _merge.Config(reference=ref, include_packed_dlc=False, overlays=tuple(overlays))


def _sel_errors(rep):
    return [f for f in rep.errors if f.category == "sel"]


# --- the merge model -----------------------------------------------------------------

def test_a_plain_path_full_copy_of_a_base_macro_file_MERGES(tmp_path):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    res = _merge.build_effective(REL, _cfg(ref, a))
    assert [h.get("max") for h in res.tree.iter("hull")] == ["1", "2"], res.sources
    assert res.sources == ["base", "a_copy:merge"], res.sources
    assert len(res.unmeasured) == 1, res.unmeasured
    note = res.unmeasured[0]
    assert "plain" in note and "MERGED" in note and "issue #23" in note, note
    assert "copy order" in note and "Skipping file" in note, note


def test_TWIN_a_weapon_macro_file_at_a_plain_path_keeps_the_replacement_and_names_the_refusal(tmp_path):
    """The measured boundary: xspvro's bullet/turret macro files were REFUSED (Skipping
    file, 5 of 6 load passes), never merged -- so outside assets/units/ nothing changes,
    and the note says what the engine was seen doing instead."""
    rel = "assets/fx/weaponfx/macros/bullet_x_macro.xml"
    ref, a, _b = _world(tmp_path, rel=rel, base=_macro(1), copy_text=_macro(2))
    res = _merge.build_effective(rel, _cfg(ref, a))
    assert res.sources == ["base", "a_copy:full"], res.sources
    assert len(res.unmeasured) == 1 and "Skipping file" in res.unmeasured[0], res.unmeasured


def test_a_plain_path_full_copy_of_a_base_component_file_MERGES(tmp_path):
    ref, a, _b = _world(tmp_path, rel=COMP, base=_comp(1), copy_text=_comp(2))
    res = _merge.build_effective(COMP, _cfg(ref, a))
    assert [c.get("name") for c in res.tree.iter("connection")] == ["c1", "c2"], res.sources
    assert res.sources == ["base", "a_copy:merge"], res.sources


@pytest.mark.parametrize("vpath, tag, merges", [
    (REL, "macros", True),
    (COMP, "components", True),
    ("assets/units/size_xl/ship_x_macro.xml", "macros", False),      # parent is not macros/
    ("assets/units/size_xl/macros/ship_x.xml", "macros", False),     # name is not *_macro.xml
    (REL, "components", False),                                       # macro path, other root
    (COMP, "macros", False),                                          # component path, macros root
    ("libraries/ship_x.xml", "components", False),                    # components outside assets/
    # Outside assets/units/: the engine REFUSED this shape for 3 weapon macro files (#23).
    ("assets/fx/weaponfx/macros/bullet_x_macro.xml", "macros", False),
    ("assets/props/weaponsystems/heavy/macros/turret_x_macro.xml", "macros", False),
    ("assets/props/weaponsystems/heavy/turret_x.xml", "components", False),
    (COMP, "wares", False),                                           # neither class root
    (REL, "wares", False),
    (f"extensions/ego_dlc_x/{REL}", "macros", False),                 # a DLC's nested path (F358)
    (f"extensions/ego_dlc_x/{COMP}", "components", False),
])
def test_merges_plain_copy_one_twin_per_clause(vpath, tag, merges):
    assert _merge.merges_plain_copy(vpath, tag) is merges


def test_TWIN_apply_overlay_merges_only_with_the_flag(tmp_path):
    tree = _merge.parse_bytes(_macro(1).encode())
    out, mode = _merge.apply_overlay(tree, _merge.parse_bytes(_macro(2).encode()), REL, "m")
    assert mode == "full" and len(out.findall("macro")) == 1
    tree = _merge.parse_bytes(_macro(1).encode())
    out, mode = _merge.apply_overlay(tree, _merge.parse_bytes(_macro(2).encode()), REL, "m",
                                     plain_on_game=True)
    assert mode == "merge" and len(out.findall("macro")) == 2


def test_TWIN_apply_overlay_with_no_tree_is_the_document():
    out, mode = _merge.apply_overlay(None, _merge.parse_bytes(_macro(2).encode()), REL, "m",
                                     plain_on_game=True)
    assert mode == "full" and len(out.findall("macro")) == 1


def test_TWIN_apply_overlay_root_mismatch_still_replaces():
    """Clause `oroot.tag == tree.tag`, with the class clause TRUE (a macro copy at a macro
    path), so the root clause is the only one that can refuse."""
    tree = _merge.parse_bytes(_comp(1).encode())             # existing root <components>
    assert _merge.merges_plain_copy(REL, "macros")
    out, mode = _merge.apply_overlay(tree, _merge.parse_bytes(_macro(2).encode()), REL, "m",
                                     plain_on_game=True)
    assert mode == "full" and out.tag == "macros"


def test_TWIN_a_non_class_plain_full_copy_still_replaces_and_says_so(tmp_path):
    rel = "assets/fx/weaponfx/bullet_x.xml"
    ref, a, _b = _world(tmp_path, rel=rel, base=_macro(1), copy_text=_macro(2))
    res = _merge.build_effective(rel, _cfg(ref, a))
    assert [h.get("max") for h in res.tree.iter("hull")] == ["2"], res.sources
    assert res.sources == ["base", "a_copy:full"], res.sources
    assert len(res.unmeasured) == 1 and "plain path" in res.unmeasured[0]
    assert "UNMEASURED" in res.unmeasured[0]


def test_TWIN_a_DLC_layer_full_copy_over_base_is_not_merged(tmp_path):
    """Clause `odir not in game_dirs`: a DLC's own complete file is game content."""
    ref = tmp_path / "reference"
    _w(ref / REL, _macro(1))
    _w(ref / "extensions" / "ego_dlc_x" / REL, _macro(2))     # an unpacked DLC layer
    out = _merge.build_effective(REL, _merge.Config(reference=ref, include_packed_dlc=False))
    assert [h.get("max") for h in out.tree.iter("hull")] == ["2"], out.sources
    assert out.sources == ["base", "ego_dlc_x:full"], out.sources


def test_TWIN_a_full_copy_over_a_MOD_supplied_document_is_not_merged(tmp_path):
    """Clause `from_game`: no game file at the path; the first mod's file IS the
    document and a second mod's complete copy keeps the old (replacement) model."""
    ref, a, _b = _world(tmp_path, base=None, copy_text=_macro(1))
    z = tmp_path / "extensions" / "z_second"
    _w(z / REL, _macro(2))
    res = _merge.build_effective(REL, _cfg(ref, a, z))
    assert [h.get("max") for h in res.tree.iter("hull")] == ["2"], res.sources
    assert res.sources == ["a_copy:full", "z_second:full"], res.sources


def test_TWIN_a_library_full_copy_keeps_its_keyed_union(tmp_path):
    rel = "libraries/ships.xml"
    base = '<ships><ship id="a" v="1"/></ships>'
    ref, a, _b = _world(tmp_path, rel=rel, base=base, copy_text='<ships><ship id="a" v="2"/></ships>')
    res = _merge.build_effective(rel, _cfg(ref, a))
    assert res.sources == ["base", "a_copy:union"], res.sources
    assert [s.get("v") for s in res.tree.iter("ship")] == ["2"]


def test_a_nested_patch_onto_a_merging_mod_is_not_applied(tmp_path):
    """MEASURED in the same log: the patching overlay ships
    `extensions/xspvro/<carrier macro>` and the engine never opened it (FileIO census of the
    log: no nested patch opened for any of the 5 merged files). A merging mod does not OWN
    the base document, so it is no owner for a nested patch (BLIND-SPOTS F382)."""
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    _w(b / "extensions" / "a_copy" / REL,
       f'<diff><replace sel="{SEL}/hull/@max">9</replace></diff>')
    res = _merge.build_effective(REL, _cfg(ref, a, b))
    assert [h.get("max") for h in res.tree.iter("hull")] == ["1", "2"], res.sources
    assert res.sources == ["base", "a_copy:merge"], res.sources


# --- x4validate Tier B -------------------------------------------------------------------

def test_x4validate_flags_a_later_unindexed_selector_as_multiple_match(tmp_path):
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                       patch_ops=f'<add sel="{SEL}"><x/></add>')
    rep = _check.validate(b, _cfg(ref, a))
    sel = _sel_errors(rep)
    assert len(sel) == 1 and "matched 2 nodes" in sel[0].message, rep.findings
    assert sel[0].sel == SEL


def test_x4validate_flags_a_later_component_selector_as_multiple_match(tmp_path):
    csel = "/components/component[@name='ship_x']"
    ref, a, b = _world(tmp_path, rel=COMP, base=_comp(1), copy_text=_comp(2),
                       patch_ops=f'<add sel="{csel}"><x/></add>')
    rep = _check.validate(b, _cfg(ref, a))
    sel = _sel_errors(rep)
    assert len(sel) == 1 and "matched 2 nodes" in sel[0].message, rep.findings


def test_TWIN_x4validate_passes_the_selector_without_the_copy(tmp_path):
    ref, _a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                        patch_ops=f'<add sel="{SEL}"><x/></add>')
    assert _sel_errors(_check.validate(b, _cfg(ref))) == []


def test_TWIN_x4validate_passes_first_indexed_and_a_true_X2_guard(tmp_path):
    """The shapes the patching mods now ship: `<remove sel="X[2]" if="X[2]"/>` first, then
    `[1]`-anchored selectors. Under the merge model the guard is TRUE and the anchor matches
    one node; under the replacement model the guard would read false."""
    ops = ('<remove sel="/macros/macro[@name=\'ship_x_macro\'][2]" '
           'if="/macros/macro[@name=\'ship_x_macro\'][2]"/>'
           '<add sel="/macros/macro[@name=\'ship_x_macro\'][1]/properties"><x/></add>')
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2), patch_ops=ops)
    rep = _check.validate(b, _cfg(ref, a))
    assert _sel_errors(rep) == [], rep.findings
    guarded_off = [f for f in rep.findings if f.category == "sel" and "if= is false" in f.message]
    assert guarded_off == [], rep.findings


def test_x4validate_tells_the_copying_mod_its_plain_file_merges(tmp_path):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    rep = _check.validate(a, _cfg(ref))
    merge = [f for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and merge[0].severity == "info", rep.findings
    msg = merge[0].message
    assert "MERGES" in msg and "ship_x_macro" in msg and "issue #23" in msg, msg
    assert "Skipping file" in msg, msg


def test_TWIN_x4validate_a_class_copy_over_a_game_document_with_another_root_is_no_merge(tmp_path):
    """`_check`'s game-root clause: the copy is in the class, the game's root is not equal."""
    ref, a, _b = _world(tmp_path, base=_comp(1), copy_text=_macro(2))
    merge = [f.message for f in _check.validate(a, _cfg(ref)).findings if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0] and "MERGES it" not in merge[0], merge


def test_TWIN_x4validate_still_discloses_a_non_class_plain_copy_as_unmeasured(tmp_path):
    rel = "assets/fx/weaponfx/bullet_x.xml"
    ref, a, _b = _world(tmp_path, rel=rel, base=_macro(1), copy_text=_macro(2))
    merge = [f for f in _check.validate(a, _cfg(ref)).findings if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0].message, merge
    assert "MERGES it" not in merge[0].message


def test_crosscheck_predicts_the_engine_skip(tmp_path):
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                       patch_ops=f'<add sel="{SEL}"><x/></add>')
    rep = _check.validate(b, _cfg(ref, a))
    log = ("[=ERROR=] 0.00 Multiple matching nodes for path '" + SEL + "' in patch file "
           "'extensions\\b_patch\\assets\\units\\size_xl\\macros\\ship_x_macro'. "
           "Skipping node.\n")
    observed = _debugcli.observed_ops(_debuglog.parse_log_text(log), "b_patch")
    cmp = _debugcli.compare_ops(observed, _debugcli.predicted_ops(rep))
    assert cmp.clean, cmp


# --- the other doors: x4compat's two instruments and x4effective dump ---------------------

def test_walk_positions_agrees_with_the_plain_merge(tmp_path):
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                       patch_ops=f'<add sel="{SEL}[1]"><x/></add>')
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    assert _compat._walk_positions(REL, ["a_copy", "b_patch"],
                                   {"a_copy": a, "b_patch": b}, cfg, lambda *x: None)


def test_trace_merges_like_the_plain_door(tmp_path):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    want = _merge.build_effective(REL, _cfg(ref, a)).tree
    _rec, _paths, qualified = _compat_state.trace(
        REL, _merge.parse_bytes(_macro(1).encode()),
        [("a_copy", _merge.parse_bytes(_macro(2).encode()))], want, plain_on_game=True)
    assert qualified
    _rec, _paths, qualified = _compat_state.trace(
        REL, _merge.parse_bytes(_macro(1).encode()),
        [("a_copy", _merge.parse_bytes(_macro(2).encode()))], want)
    assert not qualified, "TWIN: without the flag the instrument replaces and disagrees"


def test_x4compat_keeps_collision_analysis_and_reports_no_full_override(tmp_path):
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                       patch_ops=f'<replace sel="{SEL}[1]/hull/@max">5</replace>')
    z = tmp_path / "extensions" / "c_copy"
    _manifest(z, "c_copy")
    _w(z / REL, _macro(3))
    rep = _compat.analyze(tmp_path / "extensions",
                          config=_merge.Config(reference=ref, include_packed_dlc=False))
    assert rep.by_kind("FULL-OVERRIDE") == [], rep.collisions
    assert not [s for s in rep.skipped if "disagreed" in s.reason], rep.skipped
    hard = [c for c in rep.by_kind("HARD") if "merged document" in c.detail]
    assert [c.mods for c in hard] == [["a_copy", "c_copy"]], rep.collisions
    assert "plain" in hard[0].detail and hard[0].live_value_owner() is None


def test_dump_prints_the_merge_note(tmp_path, monkeypatch, capsys):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *x, **k: [a])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", REL,
                             "--reference", str(ref), "--chain"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "a_copy:merge" in out and out.count('name="ship_x_macro"') == 2, out
    assert "<!-- UNMEASURED: a_copy: a complete copy of a base-game" in out, out


# --- which document the engine LOADS for the name: the effective index (F383) -----------
#
# MEASURED (same log, 10 of 10 `assets/units/` complete copies): when the LAST effective
# index entry for the file's name points at `extensions\<copier>\<rel>`, the engine loaded the
# copier's file plus nested `extensions/<copier>/` patches and opened NO plain-path diff (5
# files); when it points at the base path, the plain path was loaded and the copy merged (5).

IDX_BASE = r"assets\units\size_xl\macros\ship_x_macro"
IDX_OWN = r"extensions\a_copy\assets\units\size_xl\macros\ship_x_macro"


def _index_world(tmp_path, copier_ops=None, patcher_ops=None, base_entries=(IDX_BASE,)):
    ref, a, b = _world(tmp_path, base=_macro(1), copy_text=_macro(2),
                       patch_ops=f'<add sel="{SEL}"><x/></add>')
    entries = "".join(f'<entry name="ship_x_macro" value="{v}"/>' for v in base_entries)
    _w(ref / "index" / "macros.xml", f"<index>{entries}</index>")
    if copier_ops is not None:
        _w(a / "index" / "macros.xml", f"<diff>{copier_ops}</diff>")
    if patcher_ops is not None:
        _w(b / "index" / "macros.xml", f"<diff>{patcher_ops}</diff>")
    return ref, a, b


_REPOINT_OWN = (f'<replace sel="/index/entry[@name=\'ship_x_macro\']/@value">{IDX_OWN}</replace>')
_ADD_OWN = f'<add sel="/index"><entry name="ship_x_macro" value="{IDX_OWN}"/></add>'
_REPOINT_BASE = (f'<replace sel="/index/entry[@name=\'ship_x_macro\'][last()]/@value">'
                 f'{IDX_BASE}</replace>')


def _not_applied(rep):
    return [f for f in rep.findings if f.category == "load-path"]


def test_a_plain_diff_on_a_name_the_index_sends_to_the_copier_is_NOT_APPLIED(tmp_path):
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    rep = _check.validate(b, _cfg(ref, a))
    na = _not_applied(rep)
    assert len(na) == 1 and na[0].severity == "error", rep.findings
    assert "a_copy" in na[0].message and "never opens" in na[0].message, na[0].message
    assert _sel_errors(rep) == [], "its ops are not evaluated: the engine never opens the file"


def test_TWIN_a_DUPLICATE_entry_the_LAST_one_decides(tmp_path):
    """xspvro's measured shape: an <add> beside the base entry, no remove. Last wins."""
    ref, a, b = _index_world(tmp_path, copier_ops=_ADD_OWN)
    assert len(_not_applied(_check.validate(b, _cfg(ref, a)))) == 1


def test_TWIN_the_base_entry_LAST_means_the_plain_path_loads_and_merges(tmp_path):
    ref, a, b = _index_world(tmp_path, copier_ops=None, base_entries=(IDX_OWN, IDX_BASE))
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [], rep.findings
    assert len(_sel_errors(rep)) == 1 and "matched 2 nodes" in _sel_errors(rep)[0].message


def test_TWIN_the_patching_mod_repointing_the_entry_back_is_counted(tmp_path):
    """The patching overlay's measured shape: its OWN index re-points the name at the base
    path, so the mod under test's index belongs in the decision (it is not in overlays)."""
    ref, a, b = _index_world(tmp_path, copier_ops=_ADD_OWN, patcher_ops=_REPOINT_BASE)
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [], rep.findings
    assert len(_sel_errors(rep)) == 1


def test_TWIN_no_index_change_keeps_the_merge(tmp_path):
    ref, a, b = _index_world(tmp_path)
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [] and len(_sel_errors(rep)) == 1, rep.findings


def test_TWIN_an_index_entry_at_a_mod_that_ships_no_complete_copy_is_no_finding(tmp_path):
    """Clause `the owner ships a COMPLETE file there`: a diff at the path owns nothing."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    _w(a / REL, f'<diff><replace sel="{SEL}/hull/@max">3</replace></diff>')
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [], rep.findings


def test_TWIN_an_index_entry_at_another_path_is_no_finding(tmp_path):
    other = r"extensions\a_copy\assets\units\size_xl\macros\some_other_macro"
    ops = f'<replace sel="/index/entry[@name=\'ship_x_macro\']/@value">{other}</replace>'
    ref, a, b = _index_world(tmp_path, copier_ops=ops)
    assert _not_applied(_check.validate(b, _cfg(ref, a))) == []


def test_index_loads_copy_reads_the_last_entry():
    idx = _merge.parse_bytes(
        f'<index><entry name="ship_x_macro" value="{IDX_BASE}"/>'
        f'<entry name="ship_x_macro" value="{IDX_OWN}"/></index>'.encode())
    assert _merge.index_loaded_path(idx, "ship_x_macro") == \
        "extensions/a_copy/assets/units/size_xl/macros/ship_x_macro"
    assert _merge.index_loaded_path(idx, "nope") is None


def test_x4validate_tells_the_copying_mod_the_engine_loads_its_own_copy(tmp_path):
    ref, a, _b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    merge = [f.message for f in _check.validate(a, _cfg(ref)).findings if f.category == "merge"]
    assert len(merge) == 1 and "its OWN document" in merge[0] and "MERGES it" not in merge[0], merge


def test_a_component_name_the_index_sends_to_the_copier_is_NOT_APPLIED(tmp_path):
    """The measured fighter_01 shape: `index/components.xml`, name = the file's stem."""
    csel = "/components/component[@name='ship_x']"
    ref, a, b = _world(tmp_path, rel=COMP, base=_comp(1), copy_text=_comp(2),
                       patch_ops=f'<add sel="{csel}"><x/></add>')
    _w(ref / "index" / "components.xml",
       '<index><entry name="ship_x" value="assets/units/size_s/ship_x"/></index>')
    _w(a / "index" / "components.xml",
       '<diff><add sel="/index"><entry name="ship_x" '
       'value="extensions/a_copy/assets/units/size_s/ship_x"/></add></diff>')
    rep = _check.validate(b, _cfg(ref, a))
    assert len(_not_applied(rep)) == 1 and _sel_errors(rep) == [], rep.findings


def test_TWIN_a_NEW_file_indexed_at_its_own_path_is_the_ordinary_case(tmp_path):
    """Clause `the game supplies the document`: adding a macro at a plain path and indexing
    it at `extensions/<mod>/...` is how mods add content -- no INFO, no finding."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    (ref / REL).unlink()
    merge = [f for f in _check.validate(a, _cfg(ref)).findings if f.category == "merge"]
    assert merge == [], merge


def test_TWIN_a_partial_index_gives_no_verdict(tmp_path):
    """Clause `built.skipped`: one unreadable index layer and the answer is open, so no
    'never opens' claim is made from an index the engine reads differently."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    bad = tmp_path / "extensions" / "c_bad"
    _manifest(bad, "c_bad")
    _w(bad / "index" / "macros.xml", "<diff><add sel='/index'>")
    rep = _check.validate(b, _cfg(ref, a, bad))
    assert _not_applied(rep) == [] and _sel_errors(rep) == [], rep.findings
    assert [s for s in rep.skipped if "which document the engine loads" in s.why and s.degraded], rep.skipped


# --- review round (ae3617a0..HEAD): the converse door, partial index, case, packed ------

def _nested_patch_world(tmp_path, copier_ops=None):
    """The copy merges or not by the index; b ships a NESTED patch of a_copy's file."""
    ref, a, b = _index_world(tmp_path, copier_ops=copier_ops)
    (b / REL).unlink()
    _w(b / "extensions" / "a_copy" / REL,
       f'<diff><replace sel="{SEL}/hull/@max">9</replace></diff>')
    return ref, a, b


def test_a_nested_patch_on_a_MERGING_copier_is_NOT_APPLIED(tmp_path):
    """Review I1. MEASURED once (F382): the patching overlay's nested patch of the copier's
    merged carrier file was never opened; the index entry was the base path."""
    ref, a, b = _nested_patch_world(tmp_path)
    rep = _check.validate(b, _cfg(ref, a))
    na = _not_applied(rep)
    assert len(na) == 1 and na[0].severity == "error", rep.findings
    assert "nested patch" in na[0].message and "MERGES" in na[0].message, na[0].message
    assert _sel_errors(rep) == [], rep.findings


def test_TWIN_a_nested_patch_on_a_copier_the_index_points_at_is_evaluated(tmp_path):
    """The own-document group: there the engine DID open the nested patch (5 of 5)."""
    ref, a, b = _nested_patch_world(tmp_path, copier_ops=_REPOINT_OWN)
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [] and _sel_errors(rep) == [], rep.findings


def test_TWIN_a_nested_patch_on_a_NEW_copier_file_is_evaluated(tmp_path):
    """No game document: the copier's file IS the document (the ordinary nested patch)."""
    ref, a, b = _nested_patch_world(tmp_path)
    (ref / REL).unlink()
    rep = _check.validate(b, _cfg(ref, a))
    assert _not_applied(rep) == [] and _sel_errors(rep) == [], rep.findings


def test_TWIN_a_nested_patch_on_a_copier_that_ships_a_DIFF_is_not_this_rule(tmp_path):
    ref, a, b = _nested_patch_world(tmp_path)
    _w(a / REL, f'<diff><replace sel="{SEL}/hull/@max">3</replace></diff>')
    assert _not_applied(_check.validate(b, _cfg(ref, a))) == []


def test_a_partial_index_is_a_degraded_skip_in_sel_only(tmp_path):
    """Review I2 (probe P8): --sel-only with an unreadable LATER index layer must not turn a
    non-answer into a confident 'matched 2 nodes'."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    bad = tmp_path / "extensions" / "c_bad"
    _manifest(bad, "c_bad")
    _w(bad / "index" / "macros.xml", "<diff><add sel='/index'>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(a,),
                        final_overlays=(a, bad))
    rep = _check.Report()
    _check.check_sel_resolution(b, cfg, rep)
    assert _sel_errors(rep) == [] and _not_applied(rep) == [], rep.findings
    skips = [s for s in rep.skipped if "which document the engine loads" in s.why and s.degraded]
    assert len(skips) == 1 and "NOT evaluated" in skips[0].why, rep.skipped


def test_TWIN_index_entry_names_match_ignoring_case():
    """The engine resolves macro/component names ignoring case (V9)."""
    idx = _merge.parse_bytes(
        f'<index><entry name="SHIP_X_MACRO" value="{IDX_OWN}"/></index>'.encode())
    assert _merge.index_loaded_path(idx, "ship_x_macro") == \
        "extensions/a_copy/assets/units/size_xl/macros/ship_x_macro"
    assert _merge.index_entries(idx, "Ship_X_Macro") == [IDX_OWN]


def test_a_SECOND_complete_copy_is_not_called_measured(tmp_path):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    z = tmp_path / "extensions" / "z_copy"
    _manifest(z, "z_copy")
    _w(z / REL, _macro(3))
    merge = [f.message for f in _check.validate(z, _cfg(ref, a)).findings
             if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0] and "a_copy" in merge[0], merge
    assert "(MEASURED, issue #23)" not in merge[0], merge


def _write_cat(mod_dir, members):
    import hashlib
    mod_dir.mkdir(parents=True, exist_ok=True)
    lines, blob = [], bytearray()
    for vpath, data in members:
        lines.append(f"{vpath} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    (mod_dir / "ext_01.cat").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (mod_dir / "ext_01.dat").write_bytes(bytes(blob))


def test_a_PACKED_copier_is_read_the_same_way(tmp_path):
    """xspvro is packed: the copy and its index diff live in ext_01.cat."""
    ref, a, b = _index_world(tmp_path)
    (a / REL).unlink()
    _write_cat(a, [(REL, _macro(2).encode()),
                   ("index/macros.xml", f"<diff>{_REPOINT_OWN}</diff>".encode())])
    rep = _check.validate(b, _cfg(ref, a))
    assert len(_not_applied(rep)) == 1 and _sel_errors(rep) == [], rep.findings
    merged = _merge.build_effective(REL, _cfg(ref, a))
    assert merged.sources == ["base", "a_copy:merge"], merged.sources


@pytest.mark.parametrize("vpath", ["Assets/Units/size_xl/macros/ship_x_macro.xml",
                                   "ASSETS/UNITS/size_s/ship_x.xml"])
def test_TWIN_the_class_and_its_index_ignore_path_case(vpath):
    tag = "macros" if "/macros/" in vpath else "components"
    assert _merge.merges_plain_copy(vpath, tag)
    assert _merge.plain_copy_index(vpath) is not None


def _bad_index_layer(tmp_path):
    bad = tmp_path / "extensions" / "c_bad"
    _manifest(bad, "c_bad")
    _w(bad / "index" / "macros.xml", "<diff><add sel='/index'>")
    return bad


def test_TWIN_a_partial_index_skips_a_NESTED_patch_too(tmp_path):
    ref, a, b = _nested_patch_world(tmp_path)
    rep = _check.validate(b, _cfg(ref, a, _bad_index_layer(tmp_path)))
    assert _not_applied(rep) == [], rep.findings
    assert [s for s in rep.skipped if "which document the engine loads" in s.why and s.degraded], rep.skipped


def test_TWIN_a_partial_index_without_any_copier_changes_nothing(tmp_path):
    """Clause `a copier exists`: with no complete copy at the path the index cannot redirect
    anything, so an unreadable index layer must not cost this file its verdict."""
    ref, _a, b = _index_world(tmp_path)
    rep = _check.validate(b, _cfg(ref, _bad_index_layer(tmp_path)))
    assert not [s for s in rep.skipped if "which document the engine loads" in s.why], rep.skipped
    assert _sel_errors(rep) == [], rep.findings


def test_the_index_is_rebuilt_for_every_run(tmp_path):
    """Review minor 7: a long-lived process must not answer from a previous run's index."""
    ref, a, b = _index_world(tmp_path)
    assert _not_applied(_check.validate(b, _cfg(ref, a))) == []
    _w(a / "index" / "macros.xml", f"<diff>{_REPOINT_OWN}</diff>")
    assert len(_not_applied(_check.validate(b, _cfg(ref, a)))) == 1


def test_TWIN_the_index_names_a_mod_whose_file_there_is_a_DIFF(tmp_path):
    """Clause `<owner> ships a COMPLETE file`, with ANOTHER mod's complete copy present (so
    the copier gate is open): an entry at a mod that only patches the path owns nothing."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    _w(a / REL, f'<diff><replace sel="{SEL}/hull/@max">3</replace></diff>')
    c = tmp_path / "extensions" / "c_copy"
    _manifest(c, "c_copy")
    _w(c / REL, _macro(3))
    assert _not_applied(_check.validate(b, _cfg(ref, a, c))) == []


# --- re-review: the copier may load AFTER the patcher; unmeasured nested shapes ----------

def _later_cfg(ref, *final):
    """Tier B as the patching mod sees it when every copier loads AFTER it."""
    return _merge.Config(reference=ref, include_packed_dlc=False, overlays=(),
                         final_overlays=tuple(final))


def test_a_copier_that_loads_AFTER_the_patcher_still_redirects_it(tmp_path):
    """Re-review N1 (probe P5), the measured shape: the base-mod diff loads BEFORE the copier
    and the engine never opened it. The patch-time tree cannot show the copier."""
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    rep = _check.validate(b, _later_cfg(ref, a))
    assert len(_not_applied(rep)) == 1 and _sel_errors(rep) == [], rep.findings


def test_TWIN_a_later_copier_the_index_does_not_point_at_changes_nothing(tmp_path):
    ref, a, b = _index_world(tmp_path)
    rep = _check.validate(b, _later_cfg(ref, a))
    assert _not_applied(rep) == [] and _sel_errors(rep) == [], rep.findings


def test_a_partial_index_skips_with_the_copier_only_loading_later(tmp_path):
    ref, a, b = _index_world(tmp_path, copier_ops=_REPOINT_OWN)
    rep = _check.validate(b, _later_cfg(ref, a, _bad_index_layer(tmp_path)))
    assert _not_applied(rep) == [] and _sel_errors(rep) == [], rep.findings
    assert [s for s in rep.skipped if "which document the engine loads" in s.why and s.degraded]


def test_the_FIRST_copier_is_told_a_LATER_second_copy_is_unmeasured(tmp_path):
    ref, a, _b = _world(tmp_path, base=_macro(1), copy_text=_macro(2))
    z = tmp_path / "extensions" / "z_copy"
    _manifest(z, "z_copy")
    _w(z / REL, _macro(3))
    merge = [f.message for f in _check.validate(a, _later_cfg(ref, z)).findings
             if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0] and "z_copy" in merge[0], merge


def test_a_nested_copy_by_a_THIRD_mod_is_unmeasured_not_a_merge_claim(tmp_path):
    """Re-review N3 (probe P9): the owner ships nothing; a third mod's complete NESTED copy is
    the nested door's document. No measurement covers it, so: a WARN, never 'MERGES'."""
    ref, a, b = _nested_patch_world(tmp_path)
    (a / REL).unlink()
    c = tmp_path / "extensions" / "c_nestedcopy"
    _manifest(c, "c_nestedcopy")
    _w(c / "extensions" / "a_copy" / REL, _macro(5))
    rep = _check.validate(b, _cfg(ref, a, c))
    lp = _not_applied(rep)
    assert [f.severity for f in lp] == ["warn"], rep.findings
    assert "UNMEASURED" in lp[0].message and "MERGES" not in lp[0].message, lp[0].message


def test_a_nested_patch_on_a_copy_with_ANOTHER_root_is_unmeasured(tmp_path):
    """Re-review N4 (probe P10): the owner's copy has a different root from the game's file,
    so the plain door REPLACES rather than merges; the nested rule must not claim a merge."""
    ref, a, b = _nested_patch_world(tmp_path)
    _w(a / REL, _comp(2))
    _w(b / "extensions" / "a_copy" / REL, '<diff><add sel="/components"><x/></add></diff>')
    rep = _check.validate(b, _cfg(ref, a))
    lp = _not_applied(rep)
    assert [f.severity for f in lp] == ["warn"], rep.findings
    assert "UNMEASURED" in lp[0].message and "MERGES" not in lp[0].message, lp[0].message

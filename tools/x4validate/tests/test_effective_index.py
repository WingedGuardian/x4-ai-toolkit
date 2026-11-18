"""F383/F384 open parts: ONE index resolver (`_merge.EffectiveIndex`) and the opt-in
`build_effective(..., index_roots=...)` that loads the document the effective index names.

MEASURED (BLIND-SPOTS F383, one log, 10 of 10 `assets/units/` complete copies): when the LAST
effective index entry for a file's name points at `extensions/<copier>/<rel>`, the engine loads
the copier's file plus its nested patches and opens NO plain-path file (5 files); when it points
at the base path, the copy merges at the plain path and no nested patch of it is opened (5).
That the LAST of several entries decides is INFERRED from 3 of those 10.

Every behavioural clause has a twin; `test_every_reader_agrees_on_the_loaded_document` is the
agreement test the hand mutant (last -> first entry) must turn red.
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _check, _merge, _resolve
from x4validate._provenance import Recorder

REL = "assets/units/size_xl/macros/ship_x_macro.xml"
NESTED = f"extensions/a_copy/{REL}"
SEL = "/macros/macro[@name='ship_x_macro']/properties"
IDX_BASE = r"assets\units\size_xl\macros\ship_x_macro"
IDX_OWN = r"extensions\a_copy\assets\units\size_xl\macros\ship_x_macro"
ADD_OWN = f'<add sel="/index"><entry name="ship_x_macro" value="{IDX_OWN}"/></add>'


def _macro(value: int, comp: str = "ship_x") -> str:
    return (f'<macros><macro name="ship_x_macro" class="ship_xl"><component ref="{comp}"/>'
            f'<properties><hull max="{value}"/></properties></macro></macros>')


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path: Path, copier_index: str | None = ADD_OWN, copy: str | None = None,
           base_entries=(IDX_BASE,)):
    """reference: base macro (hull 1) + index; a_copy: complete copy (hull 2) + an index diff;
    b_patch: a plain-path diff (hull 7); c_nest: a nested patch of a_copy's file (hull 9)."""
    ref = tmp_path / "reference"
    _w(ref / REL, _macro(1))
    _w(ref / "index" / "macros.xml",
       "<index>" + "".join(f'<entry name="ship_x_macro" value="{v}"/>' for v in base_entries)
       + "</index>")
    ext = tmp_path / "extensions"
    a, b, c = ext / "a_copy", ext / "b_patch", ext / "c_nest"
    for d in (a, b, c):
        _w(d / "content.xml", f'<content id="{d.name}" name="{d.name}" version="1"/>')
    _w(a / REL, copy if copy is not None else _macro(2, comp="ship_copy"))
    if copier_index is not None:
        _w(a / "index" / "macros.xml", f"<diff>{copier_index}</diff>")
    _w(b / REL, f'<diff><replace sel="{SEL}/hull/@max">7</replace></diff>')
    _w(c / NESTED, f'<diff><replace sel="{SEL}/hull/@max">9</replace></diff>')
    return ref, a, b, c


def _cfg(ref: Path, *overlays: Path) -> _merge.Config:
    return _merge.Config(reference=ref, include_packed_dlc=False, overlays=tuple(overlays))


def _hulls(tree) -> list[str]:
    return tree.xpath("//macro[@name='ship_x_macro']/properties/hull/@max")


# --- the resolver ----------------------------------------------------------------------------

def test_choice_reads_the_LAST_entry_as_the_copiers_own_document(tmp_path):
    ref, a, b, c = _world(tmp_path)
    got = _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c))
    assert got == _merge.OwnCopy("a_copy", IDX_OWN, 2), got


def test_TWIN_choice_base_entry_last_is_PLAIN(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=(IDX_OWN, IDX_BASE))
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_honours_a_REMOVE_and_a_value_REPLACE(tmp_path):
    rm = "<remove sel=\"/index/entry[@name='ship_x_macro'][last()]\"/>"
    ref, a, b, c = _world(tmp_path, copier_index=ADD_OWN)
    _w(b / "index" / "macros.xml", f"<diff>{rm}</diff>")
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN, "removed"
    _merge.EffectiveIndex.clear()
    rp = f'<replace sel="/index/entry[@name=\'ship_x_macro\']/@value">{IDX_OWN}</replace>'
    ref, a, b, c = _world(tmp_path / "second", copier_index=rp)
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == \
        _merge.OwnCopy("a_copy", IDX_OWN, 1), "re-pointed"


def test_TWIN_choice_a_copier_that_ships_a_DIFF_owns_nothing(tmp_path):
    ref, a, b, c = _world(tmp_path, copy=f'<diff><replace sel="{SEL}/hull/@max">3</replace></diff>')
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_a_file_the_game_does_not_supply_is_PLAIN(tmp_path):
    ref, a, b, c = _world(tmp_path)
    (ref / REL).unlink()
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_a_partial_index_is_no_verdict(tmp_path):
    ref, a, b, c = _world(tmp_path)
    _w(b / "index" / "macros.xml", "<diff><add sel='/index'>")
    got = _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c))
    assert isinstance(got, _merge.IndexPartial) and got.skipped, got


def test_the_index_is_built_once_per_tuple_of_roots(tmp_path):
    ref, a, b, c = _world(tmp_path)
    one = _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), (a, b, c))
    assert _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), [a, b, c]) is one
    assert _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), (a, b)) is not one


def test_every_reader_agrees_on_the_loaded_document(tmp_path):
    """build_index, index_loads_copy, EngineDocs, EntityDefs and the opt-in merge all name the
    copier's own document. The hand mutant (last -> first entry) turns this red."""
    ref, a, b, c = _world(tmp_path)
    cfg = _cfg(ref, a, b, c)
    roots = [a, b, c]
    root, value = _resolve.build_index(cfg, [], "index/macros.xml")["ship_x_macro"]
    assert (root.name, value.replace("\\", "/")) == ("a_copy", REL[:-4]), (root, value)
    assert _check.index_loads_copy(REL, None, cfg) == ("a_copy", IDX_OWN, 2)
    docs = _check.EngineDocs(cfg, None, _check.Report())
    assert docs.node("macro", "ship_x_macro") is None, "own document: build_index's file is it"
    defs = _check.EntityDefs(_cfg(ref), roots)
    assert [v.lower() for v in defs._registered["ship_x_macro"]] == [NESTED.lower()]
    res = _merge.build_effective(REL, cfg, index_roots=roots)
    assert res.tree.xpath("//component/@ref") == ["ship_copy"], res.sources
    assert _merge.EffectiveIndex.choice(REL, cfg, roots).owner == "a_copy"


def test_TWIN_agreement_with_the_base_entry_last(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=(IDX_OWN, IDX_BASE))
    cfg = _cfg(ref, a, b, c)
    assert _check.index_loads_copy(REL, None, cfg) is None
    defs = _check.EntityDefs(_cfg(ref), [a, b, c])
    assert [v.lower() for v in defs._registered["ship_x_macro"]] == [REL.lower()]
    res = _merge.build_effective(REL, cfg, index_roots=[a, b, c])
    assert sorted(_hulls(res.tree)) == ["1", "2"] and res.never_opened == [], res.sources


# --- build_effective(index_roots=...) : the plain door --------------------------------------

def test_without_index_roots_the_merge_is_unchanged(tmp_path):
    """Opt-in: no index_roots, no index. The copy merges and the plain diff is evaluated."""
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(REL, _cfg(ref, a, b, c))
    assert res.never_opened == [] and "a_copy:merge" in res.sources, res.sources


def test_index_roots_load_the_copiers_document_and_never_open_the_plain_diff(tmp_path):
    ref, a, b, c = _world(tmp_path)
    rec = Recorder()
    res = _merge.build_effective(REL, _cfg(ref, a, b, c), recorder=rec, index_roots=[a, b, c])
    assert res.tree.xpath("//component/@ref") == ["ship_copy"], "the copier's document"
    assert _hulls(res.tree) == ["9"], "the nested patch applies, the plain diff (7) does not"
    assert res.never_opened == ["b_patch:diff(unopened:index)"], res.never_opened
    assert res.sources[0] == f"index:{IDX_OWN}" and "b_patch:diff(unopened:index)" in res.sources
    assert any("F383" in u and "a_copy" in u for u in res.unmeasured), res.unmeasured
    comp = res.tree.xpath("//component")[0]
    assert rec.winner(comp).source == "a_copy", "the copier's values are never 'base'"


def test_TWIN_index_roots_out_of_the_class_change_nothing(tmp_path):
    ref, a, b, c = _world(tmp_path)
    other = "assets/fx/weaponfx/macros/bullet_x_macro.xml"
    _w(ref / other, _macro(1))
    _w(a / other, _macro(2))
    plain = _merge.build_effective(other, _cfg(ref, a, b))
    res = _merge.build_effective(other, _cfg(ref, a, b), index_roots=[a, b, c])
    assert res.sources == plain.sources and res.never_opened == []


def test_TWIN_index_roots_with_no_complete_copy_among_the_layers(tmp_path):
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(REL, _cfg(ref, b), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["7"] and res.never_opened == [], res.sources


def test_TWIN_index_roots_with_the_copier_not_merged_here(tmp_path):
    """The index names a_copy, but only b and a second copier are merged: no document to
    build, so the plain model stands."""
    ref, a, b, c = _world(tmp_path)
    d = tmp_path / "extensions" / "d_copy"
    _w(d / REL, _macro(4))
    res = _merge.build_effective(REL, _cfg(ref, b, d), index_roots=[a, b, c, d])
    assert res.never_opened == [] and "d_copy:merge" in res.sources, res.sources


def test_TWIN_index_roots_a_partial_index_is_a_skip_on_the_plain_model(tmp_path):
    ref, a, b, c = _world(tmp_path)
    _w(b / "index" / "macros.xml", "<diff><add sel='/index'>")
    res = _merge.build_effective(REL, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.never_opened == [] and "a_copy:merge" in res.sources
    assert any("F383" in s and "could not be built completely" in s for s in res.skipped)


# --- build_effective(index_roots=...) : the nested door -------------------------------------

def test_a_nested_patch_on_a_copy_that_merges_at_the_base_path_builds_no_tree(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.tree is None and res.never_opened == ["c_nest:diff(unopened:index)"], res.sources
    assert any("never opens a nested patch" in u for u in res.unmeasured), res.unmeasured


def test_TWIN_nested_without_index_roots_still_applies_the_patch(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c))
    assert _hulls(res.tree) == ["9"] and res.never_opened == []


def test_TWIN_nested_with_the_own_entry_last_applies_the_patch(tmp_path):
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["9"] and res.never_opened == []


def test_TWIN_nested_a_copy_with_another_root_keeps_the_nested_model(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None,
                          copy='<components><component name="ship_x_macro"/></components>')
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.never_opened == [] and res.tree is not None


def test_TWIN_nested_no_entry_for_the_name_keeps_the_nested_model(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=())
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["9"] and res.never_opened == []

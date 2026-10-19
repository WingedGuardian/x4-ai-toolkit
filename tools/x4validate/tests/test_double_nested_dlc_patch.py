"""A DOUBLY-NESTED patch: `<C>/extensions/<B>/extensions/<DLC>/<rel>` (F305).

Mod B ships a file at a DLC vpath, `extensions/<DLC>/<rel>`. Mod C patches B's copy of
it with the ordinary cross-mod nesting, `extensions/<B>/<that vpath>`, which spells
`extensions/<B>/extensions/<DLC>/<rel>`. One logical document, the DLC vpath.

ENGINE EVIDENCE (MEASURED 2026-10-19, a fresh engine log of the live modlist, 171 active
mods): 11 such files exist in active mods. The engine OPENED 10 of them -- every one whose
middle mod B ships the file as a FULL (new) file -- each right after B's own file and in
load order (`bullet_xen_weapon_xl_beam_01_mk1_macro.xml`: B, then ebi_m0_vro, then a
personal overlay mod). It did NOT open the 11th, the only one whose B ships the file
as a `<diff>` over the DLC's own file. That the opened files' ops APPLY is INFERRED from
the same mechanism measured for single nesting (F286: engine log + a save).

`_merge.build_effective` already modelled both halves (10 of 10 applied, the diff-owner
one not). What dropped them was the TOUCH MAP: `_effective.build_touch_map` (and the
`_modfiles.logical_vpath` it shares with x4validate's entity-definition lookup) kept the
doubly-nested file under its literal key, so the merge of the DLC vpath was never handed
C. The store recorded B's values with origin B for all 10 (gates/cross_tool: 4 of 2,430
dependent_overwrites rows disagreed).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from lxml import etree

from x4validate import _compat, _effective, _effectivecli, _merge, _modfiles

DLC = "ego_dlc_x"
NEW = f"extensions/{DLC}/assets/fx/macros/bullet_new_macro.xml"     # B ships it FULL
OLD = f"extensions/{DLC}/assets/fx/macros/bullet_dlc_macro.xml"     # the DLC ships it


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _macro(name: str, dmg: str) -> str:
    return (f'<macros><macro name="{name}" class="bullet"><properties>'
            f'<damage value="{dmg}"/></properties></macro></macros>')


def _set(name: str, dmg: str) -> str:
    return (f"<diff><replace sel=\"/macros/macro[@name='{name}']/properties/damage/@value\">"
            f"{dmg}</replace></diff>")


def _mod(ext: Path, folder: str, files: dict[str, str], deps=()) -> Path:
    dep = "".join(f'<dependency id="{d}"/>' for d in deps)
    _w(ext / folder / "content.xml",
       f'<content id="{folder}" name="{folder}" version="1">{dep}</content>')
    for vp, text in files.items():
        _w(ext / folder / vp, text)
    return ext / folder


def _world(tmp_path, patchers=("c_mid", "d_late"), owner=True):
    """reference: base wares + a DLC shipping bullet_dlc_macro (damage 1).
    b_owner: a NEW full file at the DLC vpath (damage 100) and a DIFF of the DLC's own
    file (damage 200). Each patcher: a doubly-nested diff of BOTH of b_owner's files."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / OLD, _macro("bullet_dlc_macro", "1"))
    ext = tmp_path / "extensions"
    if owner:
        _mod(ext, "b_owner", {NEW: _macro("bullet_new_macro", "100"),
                              OLD: _set("bullet_dlc_macro", "200")})
    values = {"a_early": "333", "c_mid": "444", "d_late": "555"}
    for p in patchers:
        _mod(ext, p, {f"extensions/b_owner/{NEW}": _set("bullet_new_macro", values[p]),
                      f"extensions/b_owner/{OLD}": _set("bullet_dlc_macro", "777")},
             deps=("b_owner",) if owner else ())
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    return cfg, ext


def _store(tmp_path, monkeypatch, cfg, ext):
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    db = tmp_path / "eff.sqlite"
    _effective.build(cfg, db, dirs=[ext], kinds=("macro",))
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT e.name, e.vpath, a.value, a.origin FROM attrs a JOIN entities e "
        "ON a.entity_id = e.id WHERE a.prop = 'damage.value'").fetchall()
    con.close()
    return {r[0]: r[1:] for r in rows}, rows


def _full(cfg, ext, order):
    return _merge.replace(cfg, overlays=tuple(ext / f for f in order))


# --- the merge: already right; pinned so the store fix cannot be "fixed" there -----

def test_merge_applies_a_doubly_nested_patch_on_a_FULL_owner_in_load_order(tmp_path):
    cfg, ext = _world(tmp_path)
    res = _merge.build_effective(NEW, _full(cfg, ext, ["b_owner", "c_mid", "d_late"]))
    assert res.tree.xpath("//damage/@value") == ["555"], res.sources
    assert res.sources == ["b_owner:full", "c_mid:diff(nested:b_owner)",
                           "d_late:diff(nested:b_owner)"], res.sources


def test_merge_does_NOT_apply_a_doubly_nested_patch_on_a_DIFF_owner(tmp_path):
    """Twin, MEASURED 1 of 1 live: B ships the DLC vpath as a <diff>, and the engine
    never opened the nested patch of it."""
    cfg, ext = _world(tmp_path)
    res = _merge.build_effective(OLD, _full(cfg, ext, ["b_owner", "c_mid", "d_late"]))
    assert res.tree.xpath("//damage/@value") == ["200"], res.sources
    assert not any("nested" in s for s in res.sources), res.sources


def test_merge_applies_an_EARLIER_loading_doubly_nested_patcher_right_after_the_owner(tmp_path):
    """C loads BEFORE B. The single-nesting rule (F286, MEASURED) applies it right after
    B's file; at depth 2 this is INFERRED -- no live instance (0 of 11)."""
    cfg, ext = _world(tmp_path, patchers=("a_early", "d_late"))
    res = _merge.build_effective(NEW, _full(cfg, ext, ["a_early", "b_owner", "d_late"]))
    assert res.sources == ["b_owner:full", "a_early:diff(nested:b_owner)",
                           "d_late:diff(nested:b_owner)"], res.sources
    assert res.tree.xpath("//damage/@value") == ["555"]


# --- the touch map: the defect ------------------------------------------------------

def test_the_touch_map_files_a_doubly_nested_DLC_patch_under_the_DLC_vpath(tmp_path):
    cfg, ext = _world(tmp_path)
    ordered = [({"folder": f}, ext / f) for f in ("b_owner", "c_mid", "d_late")]
    touch = _effective.build_touch_map(ordered)
    assert [f for f, _ in touch[NEW.lower()]] == ["b_owner", "c_mid", "d_late"], touch
    assert f"extensions/b_owner/{NEW}".lower() not in touch


def test_touchers_are_handed_to_the_merge_ONCE_each(tmp_path):
    """A mod shipping BOTH the bare DLC patch and the doubly-nested form (ebi_m0_vro does,
    live) touches the DLC vpath twice. Handing its root to the merge twice applied its bare
    diff twice -- why the rewrite was refused in the first place."""
    touch = {OLD.lower(): [("b_owner", OLD), ("c_mid", OLD),
                           ("c_mid", f"extensions/b_owner/{OLD}")]}
    f2p = {"b_owner": Path("b"), "c_mid": Path("c")}
    assert _effective.touchers_for(OLD, touch, f2p) == [Path("b"), Path("c")]


def test_the_store_records_the_doubly_nested_patcher_as_the_origin(tmp_path, monkeypatch):
    """THE DEFECT (gates/cross_tool, 4 of 2,430 rows): the store kept B's value and
    origin. RED before the fix: ('100', 'b_owner')."""
    cfg, ext = _world(tmp_path)
    got, rows = _store(tmp_path, monkeypatch, cfg, ext)
    assert got["bullet_new_macro"] == (NEW, "555", "d_late"), rows
    # Twin: the diff-owner file is NOT patched by the nested form (engine: never opened).
    assert got["bullet_dlc_macro"] == (OLD, "200", "b_owner"), rows
    # And no phantom document at the literal doubly-nested path.
    assert all(not v.lower().startswith("extensions/b_owner/") for _n, v, _x, _o in rows), rows


def test_the_store_applies_a_bare_and_nested_shipper_exactly_once(tmp_path, monkeypatch):
    """c_mid ships the bare DLC patch AND the doubly-nested one (the ebi_m0_vro shape)
    with an <add>: a double application would leave two <extra/> nodes."""
    cfg, ext = _world(tmp_path, patchers=("c_mid",))
    add = "<diff><add sel=\"/macros/macro/properties\"><extra n='1'/></add></diff>"
    _w(ext / "c_mid" / OLD, add)
    _w(ext / "c_mid" / f"extensions/b_owner/{OLD}", add)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    mods = _effective.active_mods([ext], [], cfg)
    ordered = _effective.ordered_overlays(mods)
    touch = _effective.build_touch_map(ordered)
    ov = _effective.touchers_for(OLD, touch, {m["folder"]: p for m, p in ordered})
    tree, _rec, _dropped = _effective._merge_one(OLD, cfg, ov)
    assert len(tree.findall(".//extra")) == 1, etree.tostring(tree)


def test_the_store_with_the_middle_mod_ABSENT_holds_no_phantom(tmp_path, monkeypatch):
    """Twin: B not installed -> the engine never loads C's file; nothing is stored for
    B's file, and nothing at the literal path."""
    cfg, ext = _world(tmp_path, owner=False)
    got, rows = _store(tmp_path, monkeypatch, cfg, ext)
    assert "bullet_new_macro" not in got, rows
    assert got["bullet_dlc_macro"] == (OLD, "1", "base"), rows


# --- the shared vpath helpers -------------------------------------------------------

@pytest.mark.parametrize("vpath,owners,want", [
    # depth 2 under a DLC: the DLC vpath
    (f"extensions/b/{NEW}", {"b"}, NEW),
    (f"Extensions/B/Extensions/EGO_DLC_X/a.xml", {"b"}, "Extensions/EGO_DLC_X/a.xml"),
    # single nesting, unchanged
    ("extensions/b/libraries/w.xml", {"b"}, "libraries/w.xml"),
    # twin: the inner folder is a MOD -- unmeasured (0 of 11 live), keeps its literal key
    ("extensions/b/extensions/d/libraries/w.xml", {"b", "d"}, "extensions/b/extensions/d/libraries/w.xml"),
    # twin: the inner folder is neither a DLC nor a mod
    ("extensions/b/extensions/ghost/libraries/w.xml", {"b"}, "extensions/b/extensions/ghost/libraries/w.xml"),
    # twin: the middle folder is not an owner
    (f"extensions/ghost/{NEW}", {"b"}, f"extensions/ghost/{NEW}"),
    # twin: nothing after the DLC folder
    ("extensions/b/extensions/ego_dlc_x", {"b"}, "extensions/b/extensions/ego_dlc_x"),
])
def test_logical_vpath(vpath, owners, want):
    assert _modfiles.logical_vpath(vpath, owners) == want


def test_effectivecli_dump_form_maps_a_doubly_nested_DLC_path_and_refuses_a_mod_inner():
    mods, dlc = {"b", "d"}, {DLC}
    assert _effectivecli.logical_vpath(f"extensions/b/{NEW}", mods, dlc) == NEW
    assert _effectivecli.logical_vpath(
        "extensions/b/extensions/d/libraries/w.xml", mods, dlc) is None
    assert _effectivecli.logical_vpath(
        "extensions/b/extensions/ego_dlc_unknown/a.xml", mods, dlc) is None


# --- cross-tool agreement on the fixture --------------------------------------------

def test_x4compat_and_the_store_name_the_same_live_owner(tmp_path, monkeypatch):
    cfg, ext = _world(tmp_path)
    rep = _compat.analyze(ext, config=cfg)
    rows = [c for c in list(rep.collisions) + list(rep.dependent_overwrites)
            if "bullet_new_macro" in c.vpath]
    assert rows, [(c.kind, c.vpath) for c in rep.collisions]
    owners = {c.winner for c in rows}
    assert owners == {"d_late"}, [(c.vpath, c.winner) for c in rows]
    # x4compat's sequential pass (`_compat_state.trace`) agreed with the merge: a drift
    # would cost the file its analysis as "instrument disagreed".
    assert not [s for s in rep.skipped if "instrument disagreed" in s.why], rep.skipped
    got, _rows = _store(tmp_path, monkeypatch, cfg, ext)
    assert got["bullet_new_macro"][2] in owners


# --- Tier B: x4validate on the doubly-nested candidate ------------------------------

from x4validate import _check, _registry  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    """Game root with b_owner (+ an unrelated zzz_other) installed; reference has the DLC."""
    ext = tmp_path / "game" / "extensions"
    ref = tmp_path / "ref"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / "index" / "macros.xml", "<index/>")
    _w(ref / "index" / "components.xml", "<index/>")
    _w(ref / "t" / "0001-l044.xml", "<language/>")
    _w(ref / OLD, _macro("bullet_dlc_macro", "1"))
    pc = tmp_path / "content.xml"
    _w(pc, "<content/>")
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", pc)
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_registry, "PROFILE_EXTENSIONS", None)
    monkeypatch.setattr(_registry, "WORKSHOP_CONTENT", None)
    monkeypatch.setattr(_registry, "_reference_dlc_dirs", lambda config=None: [])
    return ext, tmp_path, _merge.Config(reference=ref, include_packed_dlc=False)


def _owner(ext: Path, folder: str = "b_owner") -> None:
    _mod(ext, folder, {NEW: _macro("bullet_new_macro", "100"),
                       OLD: _set("bullet_dlc_macro", "200")})


def _candidate(tmp: Path, files: dict[str, str]) -> Path:
    cand = tmp / "dev" / "cand"
    _w(cand / "content.xml", '<content id="cand" version="1" name="c"/>')
    for vp, text in files.items():
        _w(cand / vp, text)
    return cand


def _tierb(cand, cfg, vpath):
    rep = _check.validate(cand, cfg, tier="b", sel_only=True)
    return [f for f in rep.findings if f.vpath == vpath]


NESTED_NEW = f"extensions/b_owner/{NEW}"
NESTED_OLD = f"extensions/b_owner/{OLD}"
_BAD = ("<diff><replace sel=\"/macros/macro[@name='nope']/properties/damage/@value\">"
        "1</replace></diff>")


def test_tierb_evaluates_a_doubly_nested_patch_on_a_FULL_owner(install):
    ext, tmp, cfg = install
    _owner(ext)
    got = _tierb(_candidate(tmp, {NESTED_NEW: _set("bullet_new_macro", "9")}), cfg, NESTED_NEW)
    assert not [f for f in got if f.severity == "error"], got
    # Twin: the ops really were evaluated -- a selector that matches nothing is caught.
    got = _tierb(_candidate(tmp, {NESTED_NEW: _BAD}), cfg, NESTED_NEW)
    assert any(f.severity == "error" and f.category != "path" for f in got), got


def test_tierb_names_a_DIFF_owner_instead_of_a_path_mismatch(install):
    """The engine never opened the nested patch of a mod's <diff> (MEASURED 1 of 1 live,
    depth 2). Still an ERROR -- the patch can never apply -- but the diagnosis is the
    owner's diff, not a misspelt path."""
    ext, tmp, cfg = install
    _owner(ext)
    got = _tierb(_candidate(tmp, {NESTED_OLD: _set("bullet_dlc_macro", "9")}), cfg, NESTED_OLD)
    assert len(got) == 1, got
    f = got[0]
    assert f.severity == "error", f
    assert "path mismatch" not in f.message and "<diff>" in f.message, f.message
    assert "b_owner" in f.message, f.message


def test_tierb_middle_mod_NOT_installed_is_an_inactive_no_op(install):
    _ext, tmp, cfg = install
    got = _tierb(_candidate(tmp, {NESTED_NEW: _set("bullet_new_macro", "9")}), cfg, NESTED_NEW)
    assert [(f.severity, f.category) for f in got] == [("info", "inactive")], got


def test_tierb_middle_mod_loading_LATER_is_evaluated_against_its_file(install):
    """The candidate (`cand`) loads before `zz_owner`: the F286 rule (MEASURED at depth 1,
    INFERRED at depth 2) evaluates it against the owner's file, INFO naming the order."""
    ext, tmp, cfg = install
    _owner(ext, "zz_owner")
    vp = f"extensions/zz_owner/{NEW}"
    got = _tierb(_candidate(tmp, {vp: _set("bullet_new_macro", "9")}), cfg, vp)
    assert [(f.severity, f.category) for f in got] == [("info", "load-order")], got

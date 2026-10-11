r"""Same-named WILDCARD icon declarations COEXIST in the effective icons.xml (issue #9).

`<icon name="upgrade_*" texture="ui\upgrades\*.tga"/>` is not an id-keyed registry
entry: it is a texture-folder expansion, and several layers legitimately declare the
SAME wildcard name against DIFFERENT folders. `_union_children` dedupes direct
children by (tag, id|name) with later-wins, so each DLC's `upgrade_*` REPLACED the
previous layer's -- including base's own.

MEASURED 2026-10-11 on the real reference (base + 8 DLC, no mods): 38 wildcard
declarations in, 17 survived in the effective libraries/icons.xml (21 dropped:
upgrade_*, ship_*, module_*, `*`).

The fix is deliberately narrow: `<icon>` children whose name contains `*`, in
owner-relative `libraries/icons.xml`, are APPENDED and never enter the dedupe index.
Plain icon names, other libraries/ files and index/ keep last-wins -- there is no
evidence either way for those (UNMEASURED), so their behaviour is pinned here as a
control rather than changed.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from x4validate import _check, _merge
from x4validate._provenance import Origin, Recorder

ICONS = "libraries/icons.xml"


def _icons(*pairs, root="icons", tag="icon"):
    r = etree.Element(root)
    for name, tex in pairs:
        etree.SubElement(r, tag, name=name, texture=tex)
    return r


def _decls(tree, name):
    return [(e.get("name"), e.get("texture")) for e in tree if e.get("name") == name]


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ref_with_dlc(tmp_path):
    """base + two DLC, each a COMPLETE <icons> declaring `upgrade_*` (own folder)."""
    ref = tmp_path / "reference"
    _w(ref / ICONS, '<icons><icon name="upgrade_*" texture="ui\\base\\*.tga"/>'
                    '<icon name="plain" texture="ui\\base\\plain.tga"/></icons>')
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    for dlc in ("ego_dlc_boron", "ego_dlc_split"):
        _w(ref / "extensions" / dlc / ICONS,
           f'<icons><icon name="upgrade_*" texture="ui\\{dlc}\\*.tga"/></icons>')
    return ref


# --------------------------------------------------------------------------- #
# The defect
# --------------------------------------------------------------------------- #

def test_base_and_each_DLC_upgrade_wildcard_all_survive(tmp_path):
    cfg = _merge.Config(reference=_ref_with_dlc(tmp_path), include_packed_dlc=False)
    r = _merge.build_effective(ICONS, cfg)
    assert _decls(r.tree, "upgrade_*") == [
        ("upgrade_*", "ui\\base\\*.tga"),
        ("upgrade_*", "ui\\ego_dlc_boron\\*.tga"),
        ("upgrade_*", "ui\\ego_dlc_split\\*.tga"),
    ], r.sources


def test_two_wildcard_layers_give_two_declarations_each_with_its_own_texture():
    tree = _icons(("upgrade_*", "base\\*.tga"))
    out, mode = _merge.apply_overlay(tree, _icons(("upgrade_*", "dlc\\*.tga")), ICONS, "dlc")
    assert mode == "union"
    assert _decls(out, "upgrade_*") == [("upgrade_*", "base\\*.tga"),
                                        ("upgrade_*", "dlc\\*.tga")]


def test_a_mod_diff_add_then_a_later_complete_file_both_kept(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / ICONS, "<icons/>")
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    a, b = tmp_path / "ext" / "a_mod", tmp_path / "ext" / "b_mod"
    _w(a / ICONS, '<diff><add sel="/icons"><icon name="upgrade_*" '
                  'texture="a\\*.tga"/></add></diff>')
    _w(b / ICONS, '<icons><icon name="upgrade_*" texture="b\\*.tga"/></icons>')
    cfg = _merge.Config(reference=ref, overlays=(a, b), include_packed_dlc=False)
    r = _merge.build_effective(ICONS, cfg)
    assert _decls(r.tree, "upgrade_*") == [("upgrade_*", "a\\*.tga"),
                                           ("upgrade_*", "b\\*.tga")], r.sources


def test_two_wildcards_with_one_name_INSIDE_one_overlay_file_both_kept():
    tree = _icons()
    ov = _icons(("upgrade_*", "x\\*.tga"), ("upgrade_*", "y\\*.tga"))
    out, _ = _merge.apply_overlay(tree, ov, ICONS, "m")
    assert _decls(out, "upgrade_*") == [("upgrade_*", "x\\*.tga"),
                                        ("upgrade_*", "y\\*.tga")]


def test_an_owner_NESTED_icons_vpath_also_keeps_both():
    """The _build_owned door hands apply_overlay `extensions/<owner>/libraries/...`."""
    tree = _icons(("upgrade_*", "owner\\*.tga"))
    out, mode = _merge.apply_overlay(tree, _icons(("upgrade_*", "m\\*.tga")),
                                     "extensions/some_mod/Libraries/Icons.xml", "m")
    assert mode == "union" and len(_decls(out, "upgrade_*")) == 2


def test_the_appended_wildcard_has_a_union_add_provenance_chain():
    tree, rec = _icons(("upgrade_*", "base\\*.tga")), Recorder()
    out, _ = _merge.apply_overlay(tree, _icons(("upgrade_*", "dlc\\*.tga")), ICONS,
                                  "dlc", recorder=rec)
    base_el, new_el = [e for e in out if e.get("name") == "upgrade_*"]
    assert rec.elem_chain(new_el) == [Origin("dlc", "union-add")]
    assert rec.elem_chain(base_el) == [Origin("base", "base")]


# --------------------------------------------------------------------------- #
# Controls: one twin per clause of the predicate (tag, '*', file) + index/
# --------------------------------------------------------------------------- #

def test_control_a_PLAIN_icon_name_is_still_replaced_later_wins():
    tree, rec = _icons(("plain", "base.tga")), Recorder()
    out, _ = _merge.apply_overlay(tree, _icons(("plain", "mod.tga")), ICONS, "m",
                                  recorder=rec)
    assert _decls(out, "plain") == [("plain", "mod.tga")]
    assert rec.elem_chain(out[0])[-1] == Origin("m", "union-replace")


def test_control_a_wildcard_name_on_a_NON_icon_tag_in_icons_xml_still_replaced():
    tree = _icons(("upgrade_*", "a"), tag="other")
    out, _ = _merge.apply_overlay(tree, _icons(("upgrade_*", "b"), tag="other"), ICONS, "m")
    assert _decls(out, "upgrade_*") == [("upgrade_*", "b")]


def test_control_a_wildcard_icon_in_a_DIFFERENT_libraries_file_still_replaced():
    tree = _icons(("upgrade_*", "a"))
    out, mode = _merge.apply_overlay(tree, _icons(("upgrade_*", "b")),
                                     "libraries/other.xml", "m")
    assert mode == "union" and _decls(out, "upgrade_*") == [("upgrade_*", "b")]


def test_control_a_wildcard_entry_in_index_macros_still_replaced():
    tree = _icons(("upgrade_*", "a"), root="index", tag="entry")
    out, mode = _merge.apply_overlay(
        tree, _icons(("upgrade_*", "b"), root="index", tag="entry"), "index/macros.xml", "m")
    assert mode == "union" and _decls(out, "upgrade_*") == [("upgrade_*", "b")]


# --------------------------------------------------------------------------- #
# Downstream: a diff selecting a wildcard BY NAME now matches several nodes
# --------------------------------------------------------------------------- #

def test_a_diff_selecting_a_now_duplicated_wildcard_is_reported_ambiguous(tmp_path):
    ref = _ref_with_dlc(tmp_path)
    mod = tmp_path / "ext" / "patcher"
    _w(mod / "content.xml", '<content id="patcher" name="patcher" version="1"/>')
    _w(mod / ICONS, '<diff><replace sel="/icons/icon[@name=\'upgrade_*\']/@texture">'
                    'x\\*.tga</replace></diff>')
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    rep = _check.Report()
    _check.check_sel_resolution(mod, cfg, rep)
    errs = [f for f in rep.findings if f.category == "sel" and f.severity == "error"]
    assert len(errs) == 1, rep.findings
    assert "matched 3 nodes" in errs[0].message and "SKIPS ambiguous" in errs[0].message
    # and the effective tree is unchanged by it (the engine skips the op)
    r = _merge.build_effective(ICONS, _merge.Config(reference=ref, overlays=(mod,),
                                                    include_packed_dlc=False))
    assert [t for _, t in _decls(r.tree, "upgrade_*")] == [
        "ui\\base\\*.tga", "ui\\ego_dlc_boron\\*.tga", "ui\\ego_dlc_split\\*.tga"]


# --------------------------------------------------------------------------- #
# x4effective: one icon entity PER declaration, and `show` tells them apart
# --------------------------------------------------------------------------- #

def _icon_store(tmp_path, monkeypatch):
    import sqlite3
    from x4validate import _effective
    ref = _ref_with_dlc(tmp_path)
    ext = tmp_path / "extensions"
    mod = ext / "icon_mod"
    _w(mod / "content.xml", '<content id="icon_mod" name="icon_mod" version="1"/>')
    _w(mod / ICONS, '<diff><add sel="/icons"><icon name="upgrade_*" '
                    'texture="ui\\icon_mod\\*.tga"/></add></diff>')
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref, include_packed_dlc=False), db,
                     dirs=[ext], kinds=("icon",))
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return db, con


def test_the_effective_store_holds_one_icon_entity_per_wildcard_declaration(
        tmp_path, monkeypatch):
    _, con = _icon_store(tmp_path, monkeypatch)
    rows = con.execute("SELECT klass, origin FROM entities WHERE kind='icon' AND "
                       "name='upgrade_*' ORDER BY id").fetchall()
    assert [(r["klass"], r["origin"]) for r in rows] == [
        ("ui\\base\\*.tga", "base"),
        ("ui\\ego_dlc_boron\\*.tga", "ego_dlc_boron"),
        ("ui\\ego_dlc_split\\*.tga", "ego_dlc_split"),
        ("ui\\icon_mod\\*.tga", "icon_mod"),
    ]
    # the plain icon is still exactly one entity
    assert con.execute("SELECT count(*) FROM entities WHERE kind='icon' AND "
                       "name='plain'").fetchone()[0] == 1


def test_x4effective_show_names_each_same_named_row_by_class_and_origin(
        tmp_path, monkeypatch, capsys):
    """All four rows share one vpath, so a NOTE listing `id=N  libraries/icons.xml`
    per row gives the reader no way to tell them apart."""
    from x4validate import _effectivecli
    db, _ = _icon_store(tmp_path, monkeypatch)
    rc = _effectivecli.main(["--db", str(db), "show", "icon", "upgrade_*"])
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "NOTE: 4 entities share (icon, 'upgrade_*')" in err
    for tex, origin in (("ui\\base\\*.tga", "base"),
                        ("ui\\ego_dlc_boron\\*.tga", "ego_dlc_boron"),
                        ("ui\\ego_dlc_split\\*.tga", "ego_dlc_split"),
                        ("ui\\icon_mod\\*.tga", "icon_mod")):
        line = [ln for ln in err.splitlines() if tex in ln]
        assert len(line) == 1 and origin in line[0], (tex, err)
    assert "ui\\base\\*.tga" in out   # the lowest id is the one shown

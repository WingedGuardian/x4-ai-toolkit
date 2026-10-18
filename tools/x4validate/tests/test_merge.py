"""Diff-application and effective-tree assembly."""

import os
from pathlib import Path

import pytest
from lxml import etree

from x4validate import _merge, _resolve
from x4validate._provenance import Recorder


def _wares():
    return etree.fromstring(b"""<wares>
  <ware id="ore" price_average="120"><price min="80" average="120" max="160"/></ware>
  <ware id="gone"/>
</wares>""")


def _diff(body: bytes):
    return etree.fromstring(b"<diff>" + body + b"</diff>")


def test_add_append():
    tree = _wares()
    _merge.apply_diff(tree, _diff(b'<add sel="//wares"><ware id="new"/></add>'))
    assert tree.xpath("//ware[@id='new']")
    assert tree.xpath("//wares/ware[last()][@id='new']")  # appended last


def test_add_prepend():
    tree = _wares()
    _merge.apply_diff(tree, _diff(b'<add sel="//wares" pos="prepend"><ware id="first"/></add>'))
    assert tree.xpath("//wares/ware[1][@id='first']")


def test_add_before_after():
    tree = _wares()
    _merge.apply_diff(tree, _diff(
        b'<add sel="//ware[@id=\'ore\']" pos="before"><ware id="b4"/></add>'))
    ids = tree.xpath("//ware/@id")
    assert ids.index("b4") < ids.index("ore")


def test_replace_attribute():
    tree = _wares()
    _merge.apply_diff(tree, _diff(
        b'<replace sel="//ware[@id=\'ore\']/@price_average">500</replace>'))
    assert tree.xpath("//ware[@id='ore']/@price_average") == ["500"]


def test_replace_element():
    tree = _wares()
    _merge.apply_diff(tree, _diff(
        b'<replace sel="//ware[@id=\'ore\']/price"><price min="1" average="2" max="3"/></replace>'))
    assert tree.xpath("//ware[@id='ore']/price/@average") == ["2"]


def test_remove():
    tree = _wares()
    _merge.apply_diff(tree, _diff(b'<remove sel="//ware[@id=\'gone\']"/>'))
    assert not tree.xpath("//ware[@id='gone']")


def test_if_false_skips():
    tree = _wares()
    applied = _merge.apply_diff(tree, _diff(
        b'<add sel="//wares" if="//ware[@id=\'absent\']"><ware id="cond"/></add>'))
    assert not tree.xpath("//ware[@id='cond']")
    assert applied[0].skipped_if


def test_if_true_applies():
    tree = _wares()
    _merge.apply_diff(tree, _diff(
        b'<add sel="//wares" if="//ware[@id=\'ore\']"><ware id="cond"/></add>'))
    assert tree.xpath("//ware[@id='cond']")


def test_unmatched_sel_records_failure_without_crashing():
    tree = _wares()
    applied = _merge.apply_diff(tree, _diff(b'<replace sel="//ware[@id=\'nope\']/@x">1</replace>'))
    assert applied[0].ok is False and "nothing" in applied[0].detail


# --- Effective-tree assembly through the real pipeline (tmp reference) --------


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_build_effective_dlc_diff_and_mod(tmp_path):
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml",
           '<wares><ware id="ore" price_average="120"/></wares>')
    # A DLC adds a ware via diff:
    _write(ref / "extensions/ego_dlc_x/libraries/wares.xml",
           '<diff><add sel="//wares"><ware id="boronfuel" price_average="9"/></add></diff>')
    cfg = _merge.Config(reference=ref)

    base_plus_dlc = _merge.build_effective("libraries/wares.xml", cfg)
    # A sel that ONLY matches post-DLC state must resolve under Tier A:
    assert base_plus_dlc.tree.xpath("//ware[@id='boronfuel']")
    assert "ego_dlc_x:diff" in base_plus_dlc.sources

    # A mod overlay applied on top:
    mod = tmp_path / "mymod"
    _write(mod / "libraries/wares.xml",
           '<diff><replace sel="//ware[@id=\'ore\']/@price_average">999</replace></diff>')
    with_mod = _merge.build_effective("libraries/wares.xml", cfg, extra_overlays=[mod])
    assert with_mod.tree.xpath("//ware[@id='ore']/@price_average") == ["999"]


def test_full_file_override_asset(tmp_path):
    # Asset files (outside libraries/index/t) keep full-override semantics.
    ref = tmp_path / "reference"
    _write(ref / "assets/props/x/macros/bar_macro.xml", '<macros><macro name="a"/></macros>')
    _write(ref / "extensions/ego_dlc_x/assets/props/x/macros/bar_macro.xml",
           '<macros><macro name="b"/></macros>')  # full file, not a diff
    cfg = _merge.Config(reference=ref)
    merged = _merge.build_effective("assets/props/x/macros/bar_macro.xml", cfg)
    assert merged.tree.xpath("//macro/@name") == ["b"]  # DLC fully overrode base


def test_full_file_registry_union(tmp_path):
    # Shared-registry files (libraries/, index/, t/) UNION entries — base must NOT be
    # clobbered by a DLC's full file. This is the clobber-bug regression test.
    ref = tmp_path / "reference"
    _write(ref / "libraries/ships.xml", '<ships><ship id="base_ship"/></ships>')
    _write(ref / "extensions/ego_dlc_x/libraries/ships.xml",
           '<ships><ship id="dlc_ship"/></ships>')  # full file, not a diff
    cfg = _merge.Config(reference=ref)
    merged = _merge.build_effective("libraries/ships.xml", cfg)
    assert set(merged.tree.xpath("//ship/@id")) == {"base_ship", "dlc_ship"}
    assert "ego_dlc_x:union" in merged.sources


def test_full_file_registry_union_same_id_overrides(tmp_path):
    # Same id across base + overlay -> later-overlay-wins (replaced, not duplicated).
    ref = tmp_path / "reference"
    _write(ref / "libraries/ships.xml", '<ships><ship id="dup" hull="1"/></ships>')
    _write(ref / "extensions/ego_dlc_x/libraries/ships.xml",
           '<ships><ship id="dup" hull="2"/></ships>')
    cfg = _merge.Config(reference=ref)
    merged = _merge.build_effective("libraries/ships.xml", cfg)
    assert merged.tree.xpath("//ship[@id='dup']/@hull") == ["2"]
    assert len(merged.tree.xpath("//ship[@id='dup']")) == 1


def test_textfile_synthetic_language_base(tmp_path):
    # t/0001.xml has no single base file in reference (real base t-files are
    # 0001-lNNN.xml); build_effective synthesizes a <language> root so a mod's
    # <add sel="/language"> resolves instead of "no base game file".
    cfg = _merge.Config(reference=tmp_path / "reference")
    merged = _merge.build_effective("t/0001.xml", cfg)
    assert merged.tree is not None and merged.tree.tag == "language"


def test_strip_mod_index_prefix():
    # Mod index values are written game-root-relative (extensions/<mod>/...) but
    # resolve relative to the mod root here -> the prefix must be stripped, else
    # the path doubles into a spurious 'file missing'.
    assert _resolve._strip_mod_index_prefix(
        r"extensions\mymod\assets\props\x_macro", "mymod") == "assets/props/x_macro"
    # Case-insensitive, like the engine's folder lookup.
    assert _resolve._strip_mod_index_prefix(
        r"extensions\MyMod\assets\props\x_macro", "mymod") == "assets/props/x_macro"
    # No prefix -> unchanged (mod-relative style).
    assert _resolve._strip_mod_index_prefix(
        r"assets\props\x_macro", "mymod") == r"assets\props\x_macro"
    # ANOTHER folder's prefix is NOT stripped (AUDIT-2026-09-24 MG-4): it points into
    # that folder, and stripping it resolved the path inside the registering mod.
    assert _resolve._strip_mod_index_prefix(
        r"extensions\othermod\assets\props\x_macro", "mymod") == \
        r"extensions\othermod\assets\props\x_macro"


# --- RFC 5261 attribute-add ---------------------------------------------------
# Regression: <add type="@attr"> fell through to the append-children branch and,
# with no element children, reported "1 target(s)" while mutating nothing. Found
# while repairing higher_dimensional_space, where a whole-node <replace> would have
# baked in another mod's @radius.

def _apply(base_xml: str, diff_xml: str):
    tree = _merge.parse_bytes(base_xml.encode())
    ops = _merge.apply_diff(tree, _merge.parse_bytes(diff_xml.encode()))
    return tree, ops


def test_add_type_attr_sets_the_attribute():
    tree, ops = _apply(
        "<r><safepos x='1' radius='21km'/></r>",
        '<diff><add sel="//safepos" type="@value">$Position</add></diff>')
    assert [o.ok for o in ops] == [True]
    sp = tree.find("safepos")
    assert sp.get("value") == "$Position"
    assert sp.get("radius") == "21km", "sibling attributes must survive"


def test_add_type_attr_overwrites_existing_value():
    tree, _ = _apply(
        "<r><safepos value='old'/></r>",
        '<diff><add sel="//safepos" type="@value">new</add></diff>')
    assert tree.find("safepos").get("value") == "new"


def test_add_with_unsupported_type_is_reported_not_silently_ok():
    _, ops = _apply(
        "<r><safepos/></r>",
        '<diff><add sel="//safepos" type="namespace">urn:x</add></diff>')
    assert [o.ok for o in ops] == [False]
    assert "unsupported add type" in ops[0].detail


def test_plain_add_still_appends_children():
    tree, ops = _apply(
        "<r><a/></r>", '<diff><add sel="//a"><b/></add></diff>')
    assert [o.ok for o in ops] == [True]
    assert tree.find("a/b") is not None


# --------------------------------------------------------------------------
# F19 phase 2: the engine only loads a bare-path <diff> when the GAME supplies
# the file — over another mod's file it never opens it. build_effective must
# refuse such a diff (sources records ':diff(inert)') or the effective tree
# carries values the engine never sees (measured: 14 lied attribute values
# before this landed). Full non-diff files keep overriding cross-mod — the
# engine's VFS honours those (cpsdo_vro clobbering cpsdo_zb_modpack, live).
# --------------------------------------------------------------------------


def _f19p2_world(tmp_path):
    """A reference tree with one real base file, plus a supplier mod that owns
    an asset file the base game does not have."""
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml", '<wares><ware id="ore"/></wares>')
    supplier = tmp_path / "supplier_mod"
    _write(supplier / "assets/units/size_s/macros/ship_x_macro.xml",
           '<macros><macro name="ship_x"><properties hull="100"/></macro></macros>')
    return _merge.Config(reference=ref), supplier


def test_bare_diff_over_mod_only_file_is_refused(tmp_path):
    from x4validate._provenance import Recorder
    cfg, supplier = _f19p2_world(tmp_path)
    patcher = tmp_path / "patcher_mod"
    _write(patcher / "assets/units/size_s/macros/ship_x_macro.xml",
           '<diff><replace sel="//properties/@hull">9999</replace></diff>')

    rec = Recorder()
    res = _merge.build_effective("assets/units/size_s/macros/ship_x_macro.xml", cfg,
                                 extra_overlays=[supplier, patcher], recorder=rec)
    # Supplier's file, UNMODIFIED — the engine never opens the patcher's diff.
    assert res.tree.xpath("//properties/@hull") == ["100"]
    assert "patcher_mod:diff(inert)" in res.sources
    assert not res.base_from_game
    # No provenance entry for a file that contributed nothing.
    assert all(o.source != "patcher_mod" for o in rec.file_chain)


def test_bare_diff_over_reference_base_still_applies(tmp_path):
    cfg, supplier = _f19p2_world(tmp_path)
    patcher = tmp_path / "patcher_mod"
    _write(patcher / "libraries/wares.xml",
           '<diff><add sel="//wares"><ware id="newware"/></add></diff>')
    res = _merge.build_effective("libraries/wares.xml", cfg,
                                 extra_overlays=[supplier, patcher])
    assert res.tree.xpath("//ware[@id='newware']")
    assert "patcher_mod:diff" in res.sources


def test_bare_diff_over_dlc_supplied_base_still_applies(tmp_path):
    # reference/ lacks the file; a DLC layer supplies it -> from_game via the loop.
    cfg, supplier = _f19p2_world(tmp_path)
    _write(cfg.reference / "extensions/ego_dlc_x/assets/props/y/macros/gun_macro.xml",
           '<macros><macro name="gun"><properties damage="5"/></macro></macros>')
    patcher = tmp_path / "patcher_mod"
    _write(patcher / "assets/props/y/macros/gun_macro.xml",
           '<diff><replace sel="//properties/@damage">7</replace></diff>')
    res = _merge.build_effective("assets/props/y/macros/gun_macro.xml", cfg,
                                 extra_overlays=[patcher])
    assert res.tree.xpath("//properties/@damage") == ["7"]
    assert res.base_from_game


def test_t_diff_with_only_mod_suppliers_still_applies(tmp_path):
    # The 33-file class, phase-2 edition: the ENGINE supplies the language tree,
    # so a t/ diff is never inert no matter who else ships the path.
    cfg, supplier = _f19p2_world(tmp_path)
    _write(supplier / "t/0001.xml", '<language id="44"><page id="1"/></language>')
    patcher = tmp_path / "patcher_mod"
    _write(patcher / "t/0001.xml",
           '<diff><add sel="/language"><page id="99"/></add></diff>')
    res = _merge.build_effective("t/0001.xml", cfg,
                                 extra_overlays=[supplier, patcher])
    assert res.tree.xpath("//page[@id='99']")
    assert "patcher_mod:diff" in res.sources


def test_diff_mod_loading_before_supplier_is_still_refused(tmp_path):
    # Order-independence: the engine's decision does not depend on load order
    # (the file is not base-game content, full stop), and neither may ours —
    # from_game is final before ANY mod overlay is processed.
    cfg, supplier = _f19p2_world(tmp_path)
    patcher = tmp_path / "patcher_mod"
    _write(patcher / "assets/units/size_s/macros/ship_x_macro.xml",
           '<diff><replace sel="//properties/@hull">9999</replace></diff>')
    res = _merge.build_effective("assets/units/size_s/macros/ship_x_macro.xml", cfg,
                                 extra_overlays=[patcher, supplier])  # patcher FIRST
    assert res.tree.xpath("//properties/@hull") == ["100"]
    assert "patcher_mod:diff(inert)" in res.sources


def test_mod_full_file_over_mod_only_base_still_overrides(tmp_path):
    # The cpsdo_vro idiom: a full (non-diff) file at a bare path DOES override
    # another mod's file — the engine's VFS chain honours it. Refusing diffs
    # must not leak into refusing full files.
    cfg, supplier = _f19p2_world(tmp_path)
    clobberer = tmp_path / "clobber_mod"
    _write(clobberer / "assets/units/size_s/macros/ship_x_macro.xml",
           '<macros><macro name="ship_x"><properties hull="777"/></macro></macros>')
    res = _merge.build_effective("assets/units/size_s/macros/ship_x_macro.xml", cfg,
                                 extra_overlays=[supplier, clobberer])
    assert res.tree.xpath("//properties/@hull") == ["777"]
    assert "clobber_mod:full" in res.sources


# --- <replace> targeting the document ROOT -----------------------------------
# Regression guard for the defect that silently dropped 858 installed-mod ops
# (VRO alone ships 848 `<replace sel="//macros">` whole-file overrides). The op
# was discarded because a root has no parent to swap it through, yet apply_diff
# still reported applied=True. The engine applies these — it logs every other
# patch failure and never one of these.

def _macros(hull="100"):
    return etree.fromstring(
        f'<macros><macro name="m"><properties hull="{hull}"/></macro></macros>'.encode())


def test_replace_document_root_applies():
    tree = _macros()
    ops = _merge.apply_diff(tree, _diff(
        b'<replace sel="//macros">'
        b'<macros><macro name="m"><properties hull="999"/></macro></macros>'
        b'</replace>'))
    assert tree.xpath("//properties/@hull") == ["999"]
    assert tree.tag == "macros"          # still a well-formed document
    assert [o.ok for o in ops] == [True]


def test_replace_document_root_absolute_and_components_selectors():
    for sel, root in ((b"/macros", b"macros"), (b"//components", b"components"),
                      (b"/components", b"components")):
        tree = etree.fromstring(b"<" + root + b'><c hull="1"/></' + root + b">")
        _merge.apply_diff(tree, _diff(
            b'<replace sel="' + sel + b'"><' + root + b'><c hull="2"/></' + root + b"></replace>"))
        assert tree.xpath("//c/@hull") == ["2"], sel


def test_replace_document_root_multi_element_payload_reports_not_applied():
    tree = _macros()
    ops = _merge.apply_diff(tree, _diff(
        b'<replace sel="//macros"><macros/><macros/></replace>'))
    assert [o.ok for o in ops] == [False]
    assert "one payload element" in ops[0].detail
    assert tree.xpath("//properties/@hull") == ["100"]   # unchanged


def test_replace_document_root_records_provenance():
    tree = _macros()
    rec = Recorder()
    _merge.apply_diff(tree, _diff(
        b'<replace sel="//macros">'
        b'<macros><macro name="m"><properties hull="999"/></macro></macros>'
        b'</replace>'), recorder=rec, source="vro")
    # A root replace is a whole-file override: every node's origin is the mod.
    assert rec.default_origin.source == "vro"
    assert [o.source for o in rec.file_chain] == ["vro"]


def test_remove_document_root_reports_not_applied():
    tree = _macros()
    ops = _merge.apply_diff(tree, _diff(b'<remove sel="//macros"/>'))
    assert [o.ok for o in ops] == [False]
    assert "document root" in ops[0].detail


def test_apply_diff_never_reports_ok_for_a_noop():
    """The contract that makes the whole class visible: if the tree did not
    change, the op must not be reported as applied."""
    cases = [
        b'<replace sel="//macros"><macros/><macros/></replace>',   # multi-root payload
        b'<remove sel="//macros"/>',                                # root removal
        b'<add sel="//macros" type="@"/>',                          # unnamed attribute
        b'<add sel="//macros" pos="after"><macro/></add>',          # sibling of a root
    ]
    for body in cases:
        tree = _macros()
        before = etree.tostring(tree)
        ops = _merge.apply_diff(tree, _diff(body))
        unchanged = etree.tostring(tree) == before
        if unchanged:
            assert all(not o.ok for o in ops), f"silent no-op reported as applied: {body}"
            assert all(o.detail for o in ops)


def test_ambiguous_sel_applies_nothing_and_is_reported():
    """RFC 5261: sel must select exactly ONE node, and X4 enforces it — it logs
    "Multiple matching nodes ... Skipping node" and applies NOTHING.

    Modelling that faithfully matters: applying to every match instead would
    produce an effective tree the game never has. Added after a mutation probe
    showed this guard could be deleted with the whole unit suite still green —
    only the oracle gate covered it, and that needs a captured engine log.
    """
    tree = etree.fromstring(
        b'<wares><ware id="a" price="1"/><ware id="b" price="1"/></wares>')
    ops = _merge.apply_diff(tree, _diff(b'<replace sel="//ware/@price">999</replace>'))
    assert tree.xpath("//ware/@price") == ["1", "1"], "an ambiguous op must change nothing"
    assert [o.ok for o in ops] == [False]
    assert ops[0].ambiguous is True
    assert "2 nodes" in ops[0].detail


def test_ambiguous_remove_also_applies_nothing():
    """Same rule for <remove> — the destructive direction, where applying to
    every match would delete content the engine keeps."""
    tree = etree.fromstring(b'<wares><ware id="a"/><ware id="b"/></wares>')
    ops = _merge.apply_diff(tree, _diff(b'<remove sel="//ware"/>'))
    assert len(tree.findall("ware")) == 2, "an ambiguous remove must delete nothing"
    assert [o.ok for o in ops] == [False]


def _ware_mod(tmp_path, name, sel, value, attr="transport"):
    d = tmp_path / name
    (d / "libraries").mkdir(parents=True, exist_ok=True)
    (d / "content.xml").write_text(
        f'<content id="{name}" name="{name}" version="1"/>', encoding="utf-8")
    (d / "libraries" / "wares.xml").write_text(
        f'<diff><replace sel="//ware[@id=\'{sel}\']/@{attr}">{value}</replace></diff>',
        encoding="utf-8")
    return d


def _staged_reference(tmp_path):
    """A tiny base tree, so the load-order invariants RUN on a machine with no game.

    They used to bail with a bare `return` when `res.tree` was None -- which pytest
    counts as a PASS, and `X4_MAX_SKIPS` (added expressly to catch tests going silently
    dormant) is structurally blind to, because nothing was skipped. Both ran green in
    the CI step named "Run the suite with NO game installed" while asserting nothing.

    Three wares is enough: the invariants are about ORDER, not about content.
    """
    ref = tmp_path / "_ref"
    (ref / "libraries").mkdir(parents=True, exist_ok=True)
    (ref / "libraries" / "wares.xml").write_text(
        '<wares>'
        '<ware id="ore" transport="solid" volume="10"/>'
        '<ware id="silicon" transport="solid" volume="20"/>'
        '<ware id="ice" transport="solid" volume="30"/>'
        '</wares>', encoding="utf-8")
    return _merge.Config(reference=ref, include_packed_dlc=False)


def test_merge_depends_on_load_order_and_nothing_else(tmp_path):
    """Non-overlapping overlays must give the SAME result in any order.

    If a permutation changes the outcome, something depends on enumeration order
    rather than semantics — and every collision "winner" we report would be a
    coin flip dressed as an answer.
    """
    import itertools
    mods = [_ware_mod(tmp_path, "ord_a", "ore", "liquid"),
            _ware_mod(tmp_path, "ord_b", "silicon", "liquid"),
            _ware_mod(tmp_path, "ord_c", "ice", "liquid")]
    cfg = _staged_reference(tmp_path)
    seen = set()
    for perm in itertools.permutations(mods):
        res = _merge.build_effective("libraries/wares.xml", cfg,
                                     extra_overlays=list(perm))
        assert res.tree is not None, "the staged reference did not build"
        seen.add(tuple(sorted(
            (w.get("id"), w.get("transport")) for w in res.tree.iter("ware")
            if w.get("id") in ("ore", "silicon", "ice"))))
    assert len(seen) == 1, "result depends on overlay order for non-overlapping mods"


def test_overlapping_overlays_last_one_wins(tmp_path):
    """And where they DO overlap, the winner must be the last overlay — the whole
    load-order model rests on this being true and predictable."""
    x = _ware_mod(tmp_path, "ord_x", "ore", "111", attr="volume")
    y = _ware_mod(tmp_path, "ord_y", "ore", "222", attr="volume")
    cfg = _staged_reference(tmp_path)
    for overlays, expected in (([x, y], "222"), ([y, x], "111")):
        res = _merge.build_effective("libraries/wares.xml", cfg,
                                     extra_overlays=overlays)
        assert res.tree is not None, "the staged reference did not build"
        got = [w.get("volume") for w in res.tree.iter("ware") if w.get("id") == "ore"]
        assert got == [expected], f"expected last overlay to win, got {got}"


def test_replace_attribute_with_element_payload_is_rejected():
    """An attribute can only hold text.

    Given element children the old code set the attribute to `op.text or ""`,
    silently blanking it and throwing the payload away — while reporting the op
    applied. Found by the diff fuzzer on the case where the attribute was already
    empty, so "applied" changed nothing whatsoever.
    """
    tree = etree.fromstring(b'<wares><ware id="a" transport="container"/></wares>')
    ops = _merge.apply_diff(tree, _diff(
        b'<replace sel="//ware/@transport"><ware id="new"/></replace>'))
    assert tree.xpath("//ware/@transport") == ["container"], "must not blank the attribute"
    assert [o.ok for o in ops] == [False]
    assert "element" in ops[0].detail


def test_replace_attribute_with_text_still_works():
    """The valid form must be untouched by that guard."""
    tree = etree.fromstring(b'<wares><ware id="a" transport="container"/></wares>')
    ops = _merge.apply_diff(tree, _diff(b'<replace sel="//ware/@transport">liquid</replace>'))
    assert tree.xpath("//ware/@transport") == ["liquid"]
    assert [o.ok for o in ops] == [True]


# --- nested cross-mod patches through the PLAIN door --------------------------
#
# The defect (2026-08-11): build_effective handled nesting only when the
# REQUESTED vpath was itself nested (-> _build_owned). Asking for the OWNER's
# plain vpath consulted only `odir/<vpath>` per overlay, so a later mod's file at
# `odir/extensions/<owner>/<vpath>` was invisible. Two doors to one logical file
# gave two different answers: Tier B (nested door) said a real mod's 27 bullet
# overrides resolve; the effective store (plain door) attributed every value to
# the owner. The engine has ONE document (F19, engine-proven), so the plain door
# was wrong. Found because two of our own tools disagreed — which is exactly what
# gates/cross_tool.py exists to catch, one class wider now.

def _nested_pair(tmp_path, patch_xml: str):
    ext = tmp_path / "extensions"
    owner = ext / "mod_owner"
    patch = ext / "mod_patch"
    (owner / "assets/test/macros").mkdir(parents=True)
    (patch / "extensions/mod_owner/assets/test/macros").mkdir(parents=True)
    (owner / "content.xml").write_text(
        '<content id="mod_owner" version="100" name="o" enabled="1"/>', encoding="utf-8")
    (patch / "content.xml").write_text(
        '<content id="mod_patch" version="100" name="p" enabled="1">'
        '<dependency id="mod_owner" optional="false"/></content>', encoding="utf-8")
    (owner / "assets/test/macros/widget_macro.xml").write_text(
        '<macros><macro name="widget_macro" class="bullet"><properties>'
        '<damage value="100" shield="100"/></properties></macro></macros>',
        encoding="utf-8")
    (patch / "extensions/mod_owner/assets/test/macros/widget_macro.xml").write_text(
        patch_xml, encoding="utf-8")
    return [owner, patch]


def test_nested_root_replace_applies_through_the_plain_door(tmp_path):
    overlays = _nested_pair(tmp_path, (
        '<diff><replace sel="//macros"><macros>'
        '<macro name="widget_macro" class="bullet"><properties>'
        '<damage value="999" shield="999"/></properties></macro>'
        '</macros></replace></diff>'))
    res = _merge.build_effective("assets/test/macros/widget_macro.xml",
                                 _merge.Config(overlays=overlays))
    dmg = res.tree.find(".//damage")
    assert dmg is not None and dmg.get("value") == "999"
    assert any("nested:mod_owner" in s for s in res.sources), res.sources


def test_nested_attr_replace_applies_through_the_plain_door(tmp_path):
    overlays = _nested_pair(tmp_path, (
        "<diff><replace sel=\"//macro[@name='widget_macro']"
        "/properties/damage/@value\">777</replace></diff>"))
    res = _merge.build_effective("assets/test/macros/widget_macro.xml",
                                 _merge.Config(overlays=overlays))
    assert res.tree.find(".//damage").get("value") == "777"


def test_nested_remove_deletes_through_the_plain_door(tmp_path):
    """A real mod ships exactly this: <remove> of a whole macro it replaces
    elsewhere. The old code resurrected the removed ship in the effective tree."""
    overlays = _nested_pair(tmp_path, (
        "<diff><remove sel=\"//macros/macro[@name='widget_macro']\"/></diff>"))
    res = _merge.build_effective("assets/test/macros/widget_macro.xml",
                                 _merge.Config(overlays=overlays))
    assert res.tree is not None
    assert res.tree.find(".//macro") is None, "the removed macro must stay removed"


def test_both_doors_agree(tmp_path):
    """The invariant the defect violated: the plain vpath and the nested vpath
    are the same logical document and must merge to the same tree."""
    overlays = _nested_pair(tmp_path, (
        '<diff><replace sel="//macros"><macros>'
        '<macro name="widget_macro" class="bullet"><properties>'
        '<damage value="999" shield="999"/></properties></macro>'
        '</macros></replace></diff>'))
    cfg = _merge.Config(overlays=overlays)
    plain = _merge.build_effective("assets/test/macros/widget_macro.xml", cfg)
    nested = _merge.build_effective(
        "extensions/mod_owner/assets/test/macros/widget_macro.xml", cfg)
    pv = plain.tree.find(".//damage").get("value")
    nv = nested.tree.find(".//damage").get("value")
    assert pv == nv == "999"


def test_bare_diff_over_another_mods_file_stays_inert(tmp_path):
    """The rule this fix must NOT loosen: a BARE-path diff over another mod's
    file is never opened by the engine. Only the nested form applies."""
    overlays = _nested_pair(tmp_path, "<diff/>")
    bare = overlays[1] / "assets/test/macros/widget_macro.xml"
    bare.parent.mkdir(parents=True, exist_ok=True)
    bare.write_text("<diff><replace sel=\"//macro[@name='widget_macro']"
                    "/properties/damage/@value\">666</replace></diff>",
                    encoding="utf-8")
    res = _merge.build_effective("assets/test/macros/widget_macro.xml",
                                 _merge.Config(overlays=overlays))
    assert res.tree.find(".//damage").get("value") == "100"
    assert any("diff(inert)" in s for s in res.sources), res.sources


# --- a nested patch from a mod that loads BEFORE the owner ----------------------
#
# MEASURED 2026-10-12 (engine log + a save, no launch): `<aaa>/extensions/<zzz>/<rel>`
# where aaa loads EARLIER than zzz is APPLIED by the engine, right after zzz's own
# file and before later mods (live: kuertee_additional_agent_actions ->
# kuertee_emergent_missions md/kuertee_emergent_missions_hunt.xml). The nested door
# (`_build_owned`) already did that; the plain door filled `owner_folders` only when
# its loop reached zzz, so aaa's patch was silently DROPPED -- in neither `sources`
# nor `skipped`.

_VP = "assets/test/macros/widget_macro.xml"
_SEL = "//macro[@name='widget_macro']/properties/damage/@value"


def _owned_world(tmp_path, patchers: dict[str, str]):
    """zzz owns _VP (a full file, damage 100); each patcher ships a nested diff."""
    ext = tmp_path / "extensions"
    zzz = ext / "zzz"
    (zzz / "assets/test/macros").mkdir(parents=True)
    (zzz / _VP).write_text(
        '<macros><macro name="widget_macro" class="bullet"><properties>'
        '<damage value="100"/></properties></macro></macros>', encoding="utf-8")
    dirs = {"zzz": zzz}
    for name, body in patchers.items():
        f = ext / name / "extensions" / "zzz" / _VP
        f.parent.mkdir(parents=True)
        f.write_text(f"<diff>{body}</diff>", encoding="utf-8")
        dirs[name] = ext / name
    return dirs


def _cfg(tmp_path, dirs, order):
    return _merge.Config(reference=tmp_path / "ref", include_packed_dlc=False,
                         overlays=tuple(dirs[n] for n in order))


def _both_doors(tmp_path, dirs, order):
    cfg = _cfg(tmp_path, dirs, order)
    return (_merge.build_effective(_VP, cfg),
            _merge.build_effective(f"extensions/zzz/{_VP}", cfg))


def test_a_nested_patch_from_a_mod_loading_BEFORE_the_owner_applies(tmp_path):
    dirs = _owned_world(tmp_path, {"aaa": f'<replace sel="{_SEL}">555</replace>'})
    plain, _nested = _both_doors(tmp_path, dirs, ["aaa", "zzz"])
    assert plain.tree.find(".//damage").get("value") == "555", plain.sources
    assert plain.sources == ["zzz:full", "aaa:diff(nested:zzz)"], plain.sources


def test_an_early_and_a_late_nested_add_each_apply_exactly_once(tmp_path):
    """Twin: the early patch is applied at the owner, never again when the loop later
    passes the patcher; a LATER patcher keeps its own single application."""
    add = "<add sel=\"//macro[@name='widget_macro']/properties\"><{t}/></add>"
    dirs = _owned_world(tmp_path, {"aaa": add.format(t="early"),
                                   "bbb": add.format(t="late")})
    plain, nested = _both_doors(tmp_path, dirs, ["aaa", "zzz", "bbb"])
    for res in (plain, nested):
        assert len(res.tree.findall(".//early")) == 1, res.sources
        assert len(res.tree.findall(".//late")) == 1, res.sources
    assert plain.sources == ["zzz:full", "aaa:diff(nested:zzz)", "bbb:diff(nested:zzz)"]


@pytest.mark.parametrize("order", [["aaa", "zzz"], ["zzz", "aaa"],
                                   ["aaa", "zzz", "bbb"], ["bbb", "aaa", "zzz"]])
def test_plain_and_nested_doors_give_identical_trees(tmp_path, order):
    dirs = _owned_world(tmp_path, {
        "aaa": f'<replace sel="{_SEL}">555</replace>'
               "<add sel=\"//macro[@name='widget_macro']/properties\"><a/></add>",
        "bbb": "<add sel=\"//macro[@name='widget_macro']/properties\"><b/></add>"})
    dirs = {k: v for k, v in dirs.items() if k in order}
    plain, nested = _both_doors(tmp_path, dirs, order)
    assert etree.tostring(plain.tree) == etree.tostring(nested.tree), (
        plain.sources, nested.sources)


def test_a_nested_patch_whose_owner_is_not_active_applies_nothing(tmp_path):
    """Twin: no owner in the overlay set, no document -- the patch has nothing to
    patch, through either door."""
    dirs = _owned_world(tmp_path, {"aaa": f'<replace sel="{_SEL}">555</replace>'})
    plain, nested = _both_doors(tmp_path, dirs, ["aaa"])
    assert plain.tree is None and nested.tree is None
    assert not any("nested" in s for s in plain.sources), plain.sources


def test_an_early_and_a_late_patch_on_one_attribute_the_LATER_wins(tmp_path):
    dirs = _owned_world(tmp_path, {"aaa": f'<replace sel="{_SEL}">111</replace>',
                                   "bbb": f'<replace sel="{_SEL}">222</replace>'})
    plain, nested = _both_doors(tmp_path, dirs, ["aaa", "zzz", "bbb"])
    assert plain.tree.find(".//damage").get("value") == "222"
    assert nested.tree.find(".//damage").get("value") == "222"


# --- Engine semantics: a MALFORMED overlay is contained ------------------------
# Researched 2026-08-23 (KB 2026-08-23b). The engine logs
#   "Error loading from XML merge/patch file '<TARGET>'. ... Skipping file."
# naming the merge TARGET, not the failing patch -- so the message reads as though
# the whole document were discarded. It is not: the PATCH is skipped and the base
# survives. Evidence (~90%): after logging the failure the engine went on to open
# exactly that macro's own <component ref> and its con_storage_01 target, i.e. it
# walked the merged document's reference list -- which would not exist had the
# document been discarded. Corroborated by X4_Customizer's Source_Reader, which
# skips the offending patch and continues with the next extension.
#
# These pin the property so a future refactor cannot quietly switch to the
# discard reading, which would silently drop real content for every mod that
# ships one bad file (MEASURED: 12 malformed docs across 114 active mods).

def test_malformed_overlay_is_skipped_and_the_BASE_still_wins(tmp_path):
    cfg, supplier = _f19p2_world(tmp_path)
    broken = tmp_path / "broken_mod"
    # Truncated exactly like amphitrite_vro's raider macro: unclosed tags. The op
    # would REMOVE the only base ware, so if the discard reading were right -- or
    # if the op leaked through -- 'ore' would be gone and this test goes red.
    _write(broken / "libraries/wares.xml",
           '<diff><remove sel="//ware[@id=&apos;ore&apos;]"/>')
    res = _merge.build_effective("libraries/wares.xml", cfg,
                                 extra_overlays=[supplier, broken])
    assert res.tree.xpath("//wares"), "base document must survive a malformed overlay"
    assert res.tree.xpath("//ware[@id='ore']"), \
        "base content must be intact -- the malformed overlay contributes NOTHING"


def test_malformed_overlay_does_not_block_a_LATER_good_overlay(tmp_path):
    """The load-last repair overlay must still apply.

    This is the load-bearing half: a personal fix layered after someone else's
    broken patch has to reach the document. If a parse failure poisoned the whole
    merge chain, the repair would be inert and the correct fix would instead be to
    edit or remove the upstream file.
    """
    cfg, supplier = _f19p2_world(tmp_path)
    broken = tmp_path / "broken_mod"
    _write(broken / "libraries/wares.xml",
           '<diff><add sel="//wares"><ware id="never"/></add>')   # unclosed <diff>
    fixer = tmp_path / "zzz_fixer"
    _write(fixer / "libraries/wares.xml",
           '<diff><add sel="//wares"><ware id="repaired"/></add></diff>')
    res = _merge.build_effective("libraries/wares.xml", cfg,
                                 extra_overlays=[supplier, broken, fixer])
    assert res.tree.xpath("//ware[@id='repaired']"), "later overlay must still apply"
    assert not res.tree.xpath("//ware[@id='never']"), "malformed overlay must contribute nothing"


def test_a_malformed_overlay_is_REPORTED_not_silently_absent(tmp_path):
    """Absence and non-answer must stay distinguishable (BLIND-SPOTS, passim)."""
    skipped: list[str] = []
    broken = tmp_path / "broken_mod"
    _write(broken / "libraries/wares.xml", '<diff><add sel="//wares"><ware id="x"/></add>')
    root = _merge.overlay_root(broken, "libraries/wares.xml", skipped)
    assert root is None
    assert skipped and "malformed" in skipped[0].lower(), \
        "a skipped overlay must say WHY, or it is indistinguishable from 'no such file'"


def test_apply_diff_rejects_a_string_where_the_recorder_goes():
    """The third positional is `recorder`, not `source` -- and the guess fails deep.

    Reported by a parallel session 2026-08-27:

        _merge.apply_diff(tree, diff_root, "myfile.xml")
          -> AttributeError: 'str' object has no attribute 'elem_replaced'
             at _merge.py:417 in _do_replace

    Four frames from the call, naming an internal protocol method rather than the
    bad argument, and only for diffs that happen to contain a <replace> -- a diff
    of pure <add> ops would sail through and record nothing. The signature is fine;
    the failure mode is not.
    """
    import pytest
    tree = etree.fromstring("<wares><ware id='ore'/></wares>")
    diff = etree.fromstring("<diff><add sel='//wares'><ware id='x'/></add></diff>")
    with pytest.raises(TypeError) as exc:
        _merge.apply_diff(tree, diff, "myfile.xml")
    msg = str(exc.value)
    assert "recorder" in msg, "the message must name the parameter that was misused"
    assert "source" in msg, "and point at the keyword the caller actually wanted"


def test_apply_diff_still_accepts_a_real_recorder_and_None():
    """Falsification twin: the guard must not reject the two legitimate forms."""
    tree = etree.fromstring("<wares><ware id='ore'/></wares>")
    diff = etree.fromstring("<diff><add sel='//wares'><ware id='x'/></add></diff>")
    assert _merge.apply_diff(tree, diff, None, "f.xml")[0].ok
    tree2 = etree.fromstring("<wares><ware id='ore'/></wares>")
    assert _merge.apply_diff(tree2, diff, Recorder(), "f.xml")[0].ok


def test_remove_document_root_leaves_NO_provenance_ROW():
    """M12. `_do_remove` called `recorder.node_removed()` BEFORE the
    `parent is None` check, so the op was correctly reported as NOT applied and
    the recorder was handed a removal row for it anyway -- the two channels of one
    op disagreeing, inside the module whose whole job is to make an unapplied op
    say why. No installed mod does this, so it was latent rather than a wrong
    number on disk; `test_remove_document_root_reports_not_applied` above pins the
    verdict, and this pins the record that goes with it.
    """
    tree = _macros()
    rec = Recorder()
    ops = _merge.apply_diff(tree, _diff(b'<remove sel="//macros"/>'), recorder=rec)
    assert [o.ok for o in ops] == [False]
    assert rec.removed == [], (
        "an op reported as NOT applied must not leave a removal record: %r" % (rec.removed,))


def test_a_NORMAL_remove_still_records_its_provenance():
    """The control for the test above. Moving the recorder call past a guard is
    one edit away from moving it past the removal entirely, and a green from a
    recorder that records NOTHING would look identical."""
    tree = _macros()
    rec = Recorder()
    ops = _merge.apply_diff(
        tree, _diff(b'<remove sel="//macro[@name=\'m\']/properties"/>'), recorder=rec)
    assert [o.ok for o in ops] == [True]
    assert len(rec.removed) == 1, "a real removal must still be recorded"
    assert "properties" in rec.removed[0][0], rec.removed[0]


def test_overlay_root_routes_an_UNREADABLE_archive_to_skipped_not_a_traceback(tmp_path, monkeypatch):
    """M7. `overlay_root` caught only `etree.XMLSyntaxError`, so bad XML inside a
    good archive was routed to `skipped` while a bad ARCHIVE still escaped and
    killed the run -- which is the exact failure the channel was built to stop,
    surviving in the branch nobody reproduced. `_cat.read_member` raises OSError
    for a truncated member and ValueError for a malformed catalog line, and
    `_scan.iter_mod_xml_bytes` has caught that pair from the same call all along:
    two readers of one source disagreeing about what it can raise.
    """
    odir = tmp_path / "somemod"
    odir.mkdir()

    for exc in (OSError("truncated .dat member"), ValueError("bad catalog line")):
        skipped: list[str] = []
        monkeypatch.setattr(_merge._cat, "read_path",
                            lambda d, v, _e=exc: (_ for _ in ()).throw(_e))
        got = _merge.overlay_root(odir, "libraries/wares.xml", skipped)
        assert got is None, "an unreadable overlay must be None, never a partial tree"
        assert len(skipped) == 1, (
            "%s escaped instead of being recorded: %r" % (type(exc).__name__, skipped))
        assert "somemod/libraries/wares.xml" in skipped[0] and str(exc) in skipped[0]


def test_overlay_root_still_DISTINGUISHES_absent_from_unreadable(tmp_path, monkeypatch):
    """The falsification twin: a blanket `except Exception: return None` would pass
    the test above only if it also appended -- but a file that simply is not there
    must stay a silent None with an EMPTY skipped list, or every mod that does not
    ship a vpath is reported as broken."""
    odir = tmp_path / "somemod"
    odir.mkdir()
    skipped: list[str] = []
    monkeypatch.setattr(_merge._cat, "read_path", lambda d, v: None)
    assert _merge.overlay_root(odir, "libraries/wares.xml", skipped) is None
    assert skipped == [], "a merely ABSENT file must not be reported as unreadable"


def test_is_configured_reference_accepts_a_DIFFERENT_SPELLING_of_one_tree(tmp_path, monkeypatch):
    """M11. `dlc_dirs()` gated the packed-DLC supplement on `self.reference !=
    REFERENCE` -- an identity test on raw `Path` objects. A caller passing the same
    directory spelled differently read as a DIFFERENT tree, and the DLC were
    silently dropped with nothing saying they had been left out.

    The spellings here are the ones pathlib does NOT normalise on construction.
    A first draft used a trailing separator and a `.` segment; MEASURED, pathlib
    collapses both at construction, so those assertions passed with or without the
    fix and the mutant that put `ref == REFERENCE` back SURVIVED them. `..` and a
    relative path survive construction and are exactly what `os.path.relpath` or a
    hand-built `<root>/../<root>` produces.
    """
    ref = tmp_path / "reference"
    (ref / "extensions").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    monkeypatch.setattr(_merge, "REFERENCE", ref)

    dotdot = tmp_path / "other" / ".." / "reference"
    assert dotdot != ref, "precondition: pathlib must NOT have normalised this away"
    assert _merge.is_configured_reference(dotdot), "a `..` segment is the same tree"

    monkeypatch.chdir(tmp_path)
    rel = Path("reference")
    assert rel != ref, "precondition: a relative path is a distinct Path object"
    assert _merge.is_configured_reference(rel), "a relative spelling is the same tree"

    assert _merge.is_configured_reference(ref)
    assert not _merge.is_configured_reference(tmp_path / "other"),         "a genuinely different tree must still be refused"
    assert not _merge.is_configured_reference(None)

    monkeypatch.setattr(_merge, "REFERENCE", None)
    assert not _merge.is_configured_reference(ref),         "an UNCONFIGURED toolkit supplements nothing -- absence is not a match"


def test_overlay_root_with_NO_channel_RAISES_on_an_unreadable_archive(tmp_path, monkeypatch):
    """The first cut of the unreadable-archive handler returned None whether or not
    the caller passed a `skipped` list. FIVE callers pass none (`_compat` x2,
    `_diff` x2, `_stats`), and for them a corrupt archive went from a loud crash to
    a silent "absent": x4stats printed "candidate introduces no wares" rc 0, and
    `_diff.read_merged` built a baseline from the good layers while its `unreadable`
    list stayed EMPTY. A fix for the narrowing-step shape that introduced the same
    shape one door over. MEASURED by the 2026-09-05 delta review, pre-fix vs fixed.

    No channel -> the exception propagates, exactly as before the handler existed.
    """
    odir = tmp_path / "somemod"
    odir.mkdir()
    for exc in (OSError("truncated .dat member"), ValueError("bad catalog line")):
        monkeypatch.setattr(_merge._cat, "read_path",
                            lambda d, v, _e=exc: (_ for _ in ()).throw(_e))
        with pytest.raises(type(exc)):
            _merge.overlay_root(odir, "libraries/wares.xml")          # NO skipped list


def test_overlay_root_with_NO_channel_still_returns_None_for_a_merely_ABSENT_file(tmp_path, monkeypatch):
    """The twin: re-raising must be scoped to the failure, not to the absence of a
    channel. A vpath the mod does not ship is None for every caller, as always."""
    odir = tmp_path / "somemod"
    odir.mkdir()
    monkeypatch.setattr(_merge._cat, "read_path", lambda d, v: None)
    assert _merge.overlay_root(odir, "libraries/wares.xml") is None

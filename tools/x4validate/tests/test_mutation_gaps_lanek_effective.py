"""Mutation-campaign gaps in `_effective`, lane K (2026-09-26). Each test names the surviving
mutant it was written to kill and was verified to FAIL with it applied and PASS without."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from x4validate import _effective, _merge
from x4validate._provenance import BASE, Recorder


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _touch(tmp_path, mods: dict[str, list[str]]):
    ordered = []
    for folder, vpaths in mods.items():
        root = tmp_path / folder
        for v in vpaths:
            _w(root / v, "<x/>")
        ordered.append(({"folder": folder}, root))
    return _effective.build_touch_map(ordered)


def test_a_mod_nesting_under_its_OWN_folder_is_not_rewritten(tmp_path):
    """E1: `extensions/<self>/<rel>` is not a patch on another mod; it keeps its key."""
    t = _touch(tmp_path, {"a": ["extensions/a/libraries/w.xml"], "b": ["libraries/o.xml"]})
    assert "extensions/a/libraries/w.xml" in t
    assert "libraries/w.xml" not in t


def test_a_DOUBLE_nested_patch_on_a_MOD_inner_is_not_rewritten(tmp_path):
    """E2: `extensions/<modB>/extensions/<modD>/<rel>` patches modB's PATCH of modD;
    unmeasured, so it is not transitively rewritten to `extensions/<modD>/<rel>`.
    (Under a DLC the same shape IS rewritten since F305 -- see
    tests/test_double_nested_dlc_patch.py.)"""
    lit = "extensions/b/extensions/d/libraries/w.xml"
    t = _touch(tmp_path, {"a": [lit], "b": ["libraries/o.xml"], "d": ["libraries/w.xml"]})
    assert lit in t
    assert [f for f, _ in t["libraries/w.xml"]] == ["d"], t
    assert "extensions/d/libraries/w.xml" not in t


def test_a_DOUBLE_nested_patch_under_a_DLC_is_filed_under_the_DLC_vpath(tmp_path):
    """F305: modB ships a file at a DLC vpath; the patch of modB's copy belongs to that
    DLC document (the engine opens it right after modB's file, MEASURED 2026-10-19)."""
    lit = "extensions/b/extensions/ego_dlc_x/libraries/w.xml"
    t = _touch(tmp_path, {"a": [lit], "b": ["extensions/ego_dlc_x/libraries/w.xml"]})
    assert lit not in t
    assert [f for f, _ in t["extensions/ego_dlc_x/libraries/w.xml"]] == ["a", "b"], t


def test_nesting_under_a_folder_that_is_NOT_an_installed_mod_is_not_rewritten(tmp_path):
    """E3: only an INSTALLED mod folder is an owner; anything else keeps its literal key."""
    t = _touch(tmp_path, {"a": ["extensions/ghost/libraries/w.xml"]})
    assert "extensions/ghost/libraries/w.xml" in t
    assert "libraries/w.xml" not in t


def _nest(n: int) -> etree._Element:
    return etree.fromstring("<properties>" + "<a>" * n + '<leaf v="1"/>' + "</a>" * n
                            + "</properties>")


def test_prop_depth_guard_boundary_is_exact():
    """E7: nesting of MAX_PROP_DEPTH-1 is walked in full; MAX_PROP_DEPTH is truncated."""
    n = _effective.MAX_PROP_DEPTH
    try:
        props = _nest(n - 1)
        _effective.truncated_props.clear()
        rows = _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props)
        assert _effective.truncated_props == []
        assert [r[0] for r in rows] == [".".join(["a"] * (n - 1) + ["leaf", "v"])]

        props = _nest(n)
        _effective.truncated_props.clear()
        rows = _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props)
        assert len(_effective.truncated_props) == 1
        assert rows == []
    finally:
        _effective.truncated_props.clear()


def test_active_mods_excludes_a_manifest_disabled_mod(tmp_path):
    """E10: active = installed AND enabled; a manifest enabled="0" mod is out."""
    ext = tmp_path / "extensions"
    _w(ext / "on/content.xml", '<content id="on" name="on" version="1"/>')
    _w(ext / "off/content.xml", '<content id="off" name="off" version="1" enabled="0"/>')
    assert [m["folder"] for m in _effective.active_mods([ext])] == ["on"]


def test_base_has_resolves_a_DLC_root_file_through_owner(tmp_path):
    """E11: a vpath relative to a DLC's ROOT (no separator) resolves via *owner*; the
    suffix fallback cannot, because it deliberately refuses bare names."""
    ref = tmp_path / "reference"
    _w(ref / "libraries/wares.xml", "<wares/>")
    _w(ref / "extensions/ego_dlc_x/rootfile.xml", "<x/>")
    cfg = _merge.Config(reference=ref)
    _effective.base_vpaths.cache_clear()
    try:
        assert _effective.base_has(cfg, "rootfile.xml", owner="ego_dlc_x") is True
        assert _effective.base_has(cfg, "rootfile.xml") is False
    finally:
        _effective.base_vpaths.cache_clear()

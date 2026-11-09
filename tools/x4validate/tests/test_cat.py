"""Tests for _cat.py — CAT/DAT mod-archive reader."""

from __future__ import annotations

import hashlib

import pytest

from x4validate import _cat, _merge


def _write_cat(mod_dir, cat_name, members):
    """Write a .cat/.dat pair. *members* = list of (vpath, bytes).

    Mirrors the real format: .cat lines "path size mtime md5", .dat = concatenated
    payloads in the same order.
    """
    mod_dir.mkdir(parents=True, exist_ok=True)
    cat = mod_dir / cat_name
    dat = cat.with_suffix(".dat")
    lines = []
    blob = bytearray()
    for vpath, data in members:
        md5 = hashlib.md5(data).hexdigest()
        lines.append(f"{vpath} {len(data)} 1700000000 {md5}")
        blob += data
    cat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dat.write_bytes(bytes(blob))


def test_reads_members_with_correct_offsets(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [
        ("md/first.xml", b"<a>first</a>"),
        ("md/second.xml", b"<b>second-longer-payload</b>"),
    ])
    vfs = _cat.build_mod_vfs(mod)
    assert set(vfs) == {"md/first.xml", "md/second.xml"}
    assert _cat.read_member(vfs["md/first.xml"]) == b"<a>first</a>"
    assert _cat.read_member(vfs["md/second.xml"]) == b"<b>second-longer-payload</b>"


def test_path_with_spaces_parses(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [("assets/my ship/macro.xml", b"<macro/>")])
    vfs = _cat.build_mod_vfs(mod)
    assert "assets/my ship/macro.xml" in vfs


def test_xml_only_skips_non_xml_but_offsets_stay_correct(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [
        ("assets/geo.xmf", b"BINARYGEOMETRYBLOB"),   # skipped from index
        ("md/after.xml", b"<ok/>"),                  # must still read correctly
    ])
    vfs = _cat.build_mod_vfs(mod)  # xml_only=True default
    assert set(vfs) == {"md/after.xml"}
    # The skipped .xmf's bytes still shift the offset; a wrong impl returns garbage here.
    assert _cat.read_member(vfs["md/after.xml"]) == b"<ok/>"


def test_later_catalog_overrides_earlier(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [("libraries/x.xml", b"<old/>")])
    _write_cat(mod, "ext_02.cat", [("libraries/x.xml", b"<new/>")])
    assert _cat.read_path(mod, "libraries/x.xml") == b"<new/>"


def test_subst_and_ext_both_read(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "subst_01.cat", [("libraries/base_override.xml", b"<sub/>")])
    _write_cat(mod, "ext_01.cat", [("md/added.xml", b"<add/>")])
    vfs = _cat.build_mod_vfs(mod)
    assert set(vfs) == {"libraries/base_override.xml", "md/added.xml"}


def test_sig_and_version_cats_skipped(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [("md/real.xml", b"<real/>")])
    _write_cat(mod, "ext_01_sig.cat", [("md/sig.xml", b"<sig/>")])
    _write_cat(mod, "ext_v900.cat", [("md/versioned.xml", b"<ver/>")])
    vfs = _cat.build_mod_vfs(mod)
    assert set(vfs) == {"md/real.xml"}


def test_md5_mismatch_raises(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [("md/x.xml", b"<x/>")])
    # Corrupt the .dat so the stored md5 no longer matches.
    (mod / "ext_01.dat").write_bytes(b"<TAMPERED/>")
    member = _cat.build_mod_vfs(mod)["md/x.xml"]
    with pytest.raises(OSError, match="MD5 mismatch"):
        _cat.read_member(member)


def test_is_packed(tmp_path):
    loose = tmp_path / "loose"
    (loose / "md").mkdir(parents=True)
    (loose / "md" / "x.xml").write_text("<x/>")
    assert not _cat.is_packed(loose)
    packed = tmp_path / "packed"
    _write_cat(packed, "ext_01.cat", [("md/x.xml", b"<x/>")])
    assert _cat.is_packed(packed)


def test_merge_reads_packed_overlay(tmp_path):
    """build_effective must apply a packed mod's diff, not just loose files."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text(
        '<wares><ware id="ore"><price average="100"/></ware></wares>')
    mod = tmp_path / "packedmod"
    _write_cat(mod, "ext_01.cat", [(
        "libraries/wares.xml",
        b'<diff><replace sel="//ware[@id=\'ore\']/price/@average">777</replace></diff>',
    )])
    cfg = _merge.Config(reference=ref)
    res = _merge.build_effective("libraries/wares.xml", cfg, extra_overlays=[mod])
    assert res.tree.xpath("//ware[@id='ore']/price/@average") == ["777"]


def test_loose_overrides_packed_in_same_mod(tmp_path):
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [("md/x.xml", b"<packed/>")])
    loose = mod / "md" / "x.xml"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_text("<loose/>")
    root = _merge.overlay_root(mod, "md/x.xml")
    assert root.tag == "loose"


def test_read_path_reads_non_xml_members(tmp_path):
    """A packed non-XML member used to read as None (absent): the lookup went through
    the XML-only index. MEASURED 2026-11-08 on a packed .lua in sn_mod_support_apis."""
    mod = tmp_path / "mymod"
    _write_cat(mod, "ext_01.cat", [
        ("assets/geo.xmf", b"BINARYGEOMETRYBLOB"),
        ("ui/Menu.LUA", b"return {}"),
        ("md/after.xml", b"<ok/>"),
    ])
    assert _cat.read_path(mod, "assets/geo.xmf") == b"BINARYGEOMETRYBLOB"
    assert _cat.read_path(mod, "ui/menu.lua") == b"return {}"   # case-insensitive too
    assert _cat.read_path(mod, "md/after.xml") == b"<ok/>"
    assert _cat.read_path(mod, "ui/missing.lua") is None
    # The XML index every XML reader shares is unchanged: still XML-only.
    assert set(_cat.mod_vfs(mod, packed_only=True)) == {"md/after.xml"}

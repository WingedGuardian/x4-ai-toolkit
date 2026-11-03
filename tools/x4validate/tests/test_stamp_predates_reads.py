"""A mod edited DURING a build must not leave an artifact reading FRESH.

The stamp used to be computed AFTER the reads, so a store holding the old values carried the new world's
digest and read FRESH forever (MEASURED, release review 4.1.0, finding T1). The contract: the content axis
describes the world at or BEFORE the first read, so an edit during the build reads STALE, the safe direction.
Each test has a control (no edit -> FRESH), so a verdict that is always STALE cannot pass.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from x4validate import _effective, _freshness, _merge, _xref


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg))
    ref = tmp_path / "reference"
    (ref / "t").mkdir(parents=True)
    (ref / "t" / "0001-l044.xml").write_bytes(b'<language id="44"><page id="1"><t id="1">x</t></page></language>')
    (ref / "libraries").mkdir()
    (ref / "libraries" / "wares.xml").write_bytes(b'<wares><ware id="w1" name="n"><price average="100"/></ware></wares>')
    (ref / "md").mkdir()
    (ref / "md" / "base.xml").write_bytes(b'<mdscript name="base"><cues/></mdscript>')
    exts = tmp_path / "extensions"
    mod = exts / "m1"
    (mod / "libraries").mkdir(parents=True)
    (mod / "md").mkdir()
    (mod / "content.xml").write_bytes(b'<content id="m1" name="M" version="1"/>')
    patch = mod / "libraries" / "wares.xml"
    patch.write_bytes(b"<diff><replace sel=\"//ware[@id='w1']/price/@average\">111</replace></diff>")
    (mod / "md" / "mine.xml").write_bytes(b'<mdscript name="mine"><cues/></mdscript>')
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_effective, "_ext_root", lambda config: exts)
    _effective.base_vpaths.cache_clear()
    yield ref, exts, patch
    _effective.base_vpaths.cache_clear()


def _edit(patch):
    patch.write_bytes(b"<diff><replace sel=\"//ware[@id='w1']/price/@average\">999999</replace></diff>")


def _store_verdict(db, cfg, exts):
    con = sqlite3.connect(db)
    stored = _freshness.read_sqlite(con)
    con.close()
    return _freshness.compare(stored, _freshness.fingerprint(cfg, exts), engine_dependent=True)


def test_effective_store_built_with_no_edit_is_fresh_the_control(world, tmp_path):
    ref, exts, _patch = world
    cfg = _merge.Config(reference=ref)
    db = tmp_path / "s.sqlite"
    _effective.build(cfg, db, dirs=[exts], kinds=("ware",))
    assert _store_verdict(db, cfg, exts).fresh


def test_effective_store_edited_after_the_merge_reads_stale(world, tmp_path):
    ref, exts, patch = world
    cfg = _merge.Config(reference=ref)
    db = tmp_path / "s.sqlite"
    fired = []

    def progress(line):
        if line.startswith("texts:") and not fired:   # the merge is over; the stamp has not been taken
            fired.append(1)
            _edit(patch)
    _effective.build(cfg, db, dirs=[exts], kinds=("ware",), progress=progress)
    assert fired, "the edit never fired, so this test measured nothing"
    v = _store_verdict(db, cfg, exts)
    assert not v.fresh, "the store holds the old value and claims the new world"
    assert "content changed" in " ".join(v.reasons)


def test_effective_fingerprint_is_taken_before_the_first_mod_read(world, tmp_path, monkeypatch):
    """Twin of the edit test for the earlier half of the window: an edit between the enumeration and the
    merge is invisible to it, so the ORDER is pinned directly."""
    ref, exts, _patch = world
    order = []
    real_fp, real_mods = _freshness.fingerprint, _effective.active_mods
    monkeypatch.setattr(_freshness, "fingerprint", lambda *a, **k: (order.append("fingerprint"), real_fp(*a, **k))[1])
    monkeypatch.setattr(_effective, "active_mods", lambda *a, **k: (order.append("mods"), real_mods(*a, **k))[1])
    _effective.build(_merge.Config(reference=ref), tmp_path / "s.sqlite", dirs=[exts], kinds=("ware",))
    assert order[:2] == ["fingerprint", "mods"], order


def test_effective_build_refuses_a_mutated_tree_before_hashing_it(world, tmp_path, monkeypatch):
    """The engine axis is now hashed at the start too, so a gate active THEN must refuse THEN."""
    ref, exts, _patch = world
    monkeypatch.setattr(_effective._mutation, "active", lambda: "marker")
    monkeypatch.setattr(_effective, "active_mods", lambda *a, **k: pytest.fail("read mods while mutating"))
    with pytest.raises(_effective._mutation.TreeMutating):
        _effective.build(_merge.Config(reference=ref), tmp_path / "s.sqlite", dirs=[exts], kinds=("ware",))


def _build_xref(ref, exts, out):
    return _xref.main(["build", "--reference", str(ref), "--ext-dir", str(exts), "--out", str(out)])


def test_xref_index_built_with_no_edit_is_fresh_the_control(world, tmp_path):
    ref, exts, _patch = world
    out = tmp_path / "x.tsv"
    assert _build_xref(ref, exts, out) == 0
    assert _xref.check_index(out)[3].fresh


def test_xref_index_edited_during_the_walk_reads_stale(world, tmp_path, monkeypatch):
    ref, exts, patch = world
    out = tmp_path / "x.tsv"
    real = _xref.build_index

    def walk_then_edit(*a, **k):
        rows = real(*a, **k)
        _edit(patch)
        return rows
    monkeypatch.setattr(_xref, "build_index", walk_then_edit)
    assert _build_xref(ref, exts, out) == 0
    v = _xref.check_index(out)[3]
    assert not v.fresh, "the index predates the edit and claims the new world"
    assert "content changed" in " ".join(v.reasons)


def test_an_xref_index_ignores_an_engine_move(world, tmp_path, monkeypatch):
    """x4xref reads files as written and merges nothing, so its engine axis is not compared. Pinned because the
    changelog said otherwise (review T11) and the release notes depend on it. The control is the effective
    store's compare, which must go stale on the same move."""
    ref, exts, _patch = world
    out = tmp_path / "x.tsv"
    assert _build_xref(ref, exts, out) == 0
    real = _freshness.fingerprint

    def moved_engine(*a, **k):
        return dict(real(*a, **k), engine="moved")
    monkeypatch.setattr(_freshness, "fingerprint", moved_engine)
    assert _xref.check_index(out)[3].fresh
    stored = _freshness.read_sidecar(out)
    assert not _freshness.compare(stored, moved_engine(_merge.Config(reference=ref), [exts]), engine_dependent=True).fresh

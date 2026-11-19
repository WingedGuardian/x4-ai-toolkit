"""gates/cross_tool.py over the effective store's INPUT SNAPSHOT (toolkit 4.3 item 6):
a STALE store still runs, an unprovable snapshot refuses and x4compat never runs.
"""
from __future__ import annotations

import sqlite3

from test_inputs_snapshot import _build, _w
from x4validate import _effective, _inputs, _merge, _registry


# --------------------------------------------------------------------------- cross_tool

def _gate(monkeypatch, db, ref):
    import types

    from conftest import import_gate
    ct = import_gate("cross_tool", module_level=False)
    monkeypatch.setattr(ct, "failures", [])
    monkeypatch.setattr(ct, "cannot", [])
    monkeypatch.setattr(ct, "DB_OVERRIDE", db)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    seen = []

    def analyze(ext_dir, config=None, **k):
        seen.append((ext_dir, _registry.GAME_EXTENSIONS))
        return types.SimpleNamespace(collisions=[])
    monkeypatch.setattr(ct._compat, "analyze", analyze)
    return ct, seen


def test_cross_tool_runs_on_a_STALE_store_over_the_snapshot_without_a_hold(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    _w(exts / "aaa" / "libraries" / "wares.xml", b"<diff/>")      # the store is now STALE
    con = sqlite3.connect(db)
    assert not _effective.store_freshness(con, _merge.Config(reference=ref)).fresh
    con.close()
    ct, seen = _gate(monkeypatch, db, ref)
    ct.check_cross_tool_agreement()
    assert seen == [(None, _inputs.snapshot_path(db) / "r0")], seen   # all roots, the copy
    assert not any("snapshot" in c for c in ct.cannot), ct.cannot
    assert _registry.GAME_EXTENSIONS == exts                         # restored afterwards


def test_TWIN_cross_tool_refuses_when_the_snapshot_was_edited(tmp_path, monkeypatch, capsys):
    db, ref, exts = _build(tmp_path, monkeypatch)
    _w(_inputs.snapshot_path(db) / "r0" / "aaa" / "libraries" / "wares.xml", b"<diff/>")
    ct, seen = _gate(monkeypatch, db, ref)
    ct.check_cross_tool_agreement()
    assert seen == [] and any("input snapshot could not be proven" in c for c in ct.cannot)
    assert "aaa/libraries/wares.xml" in capsys.readouterr().err

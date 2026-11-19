"""x4fresh's view of the input snapshot (toolkit 4.3 item 6): a store stale only because
mods moved is labelled as still backing cross_tool; orphaned snapshot temps are listed.
"""
from __future__ import annotations

from test_inputs_snapshot import _build, _set_meta, _w
from x4validate import _effective, _merge


# --------------------------------------------------------------------------- x4fresh

def _fresh_status(monkeypatch, db, ref):
    from x4validate import _freshcli
    monkeypatch.setattr(_freshcli._effective, "effective_db", lambda: db)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    real = _effective.store_freshness
    monkeypatch.setattr(_freshcli._effective, "store_freshness",
                        lambda con, config=None: real(con, _merge.Config(reference=ref)))
    s = _freshcli._status_effective()
    return s, _freshcli.render([s])


def test_x4fresh_says_a_snapshot_backed_stale_store_still_runs_cross_tool(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    _w(exts / "aaa" / "libraries" / "wares.xml", b"<diff/>")
    s, text = _fresh_status(monkeypatch, db, ref)
    assert s.state == "STALE" and "effective  STALE (snapshot-backed: cross_tool runs)" in text, text


def test_TWIN_x4fresh_gives_no_label_when_the_snapshot_cannot_back_it(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    _w(exts / "aaa" / "libraries" / "wares.xml", b"<diff/>")
    _set_meta(db, "input_snapshot", None)
    s, text = _fresh_status(monkeypatch, db, ref)
    assert s.state == "STALE" and s.label == "" and "snapshot-backed" not in text


def test_x4fresh_lists_an_orphaned_snapshot_temp_folder(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    (tmp_path / "eff.sqlite.inputs.4242.tmp").mkdir()
    (tmp_path / "eff.sqlite.inputs.old.4243.tmp").mkdir()
    s, text = _fresh_status(monkeypatch, db, ref)
    assert sorted(p.name for p in s.orphans) == ["eff.sqlite.inputs.4242.tmp",
                                                 "eff.sqlite.inputs.old.4243.tmp"], s.orphans

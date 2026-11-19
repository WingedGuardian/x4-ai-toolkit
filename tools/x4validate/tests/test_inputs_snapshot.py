"""Toolkit 4.3 item 6: every effective-store build takes an INPUT SNAPSHOT, the store is
built from it, and `gates/cross_tool.py` runs x4compat against the same snapshot after
`_inputs.prove` shows it is the one the store was built from (user decision 2026-11-18:
cross_tool must never need a modlist hold).

Each guard clause has a falsification twin: the same setup with only that clause's
condition flipped must flip the verdict.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from x4validate import _cat, _compat, _effective, _freshness, _inputs, _merge, _registry

PKG = Path(_inputs.__file__).resolve().parent
GATES = PKG.parent / "gates"


# --------------------------------------------------------------------------- fixture

def _w(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


def _pack(mod: Path, members: list[tuple[str, bytes]], name: str = "ext_01") -> None:
    """A real catalog pair: .cat lines `<path> <size> <mtime> <md5>`, .dat in line order."""
    lines, blob = [], b""
    for path, data in members:
        lines.append(f"{path} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    _w(mod / f"{name}.cat", "\n".join(lines) + "\n")
    _w(mod / f"{name}.dat", blob)


WARES = b'<wares><ware id="ore"><price min="1" average="2" max="3"/></ware></wares>'
ICONS = ('<icons><icon name="ship_*" texture="extensions\\{m}\\ui\\ships\\*.tga" '
         'width="64" height="64"/></icons>')


def _world(tmp_path: Path, monkeypatch):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", WARES)
    _w(ref / "extensions" / "ego_dlc_split" / "libraries" / "wares.xml",
       b'<wares><ware id="spice"><price min="5" average="9" max="12"/></ware></wares>')
    exts = tmp_path / "extensions"
    a = exts / "aaa"
    _w(a / "content.xml", b'<content id="aaa" name="A" version="1"/>')
    _w(a / "libraries" / "wares.xml",
       b'<diff><replace sel="//ware[@id=\'ore\']/price/@average">20</replace></diff>')
    _w(a / "ui" / "ships" / "ship_a1.gz", b"TEXTURE-A1" * 50)
    _w(a / "ui" / "ships" / "ship_a2.gz", b"TEXTURE-A2" * 50)
    _w(a / "libraries" / "icons.xml", ICONS.format(m="aaa"))
    _w(a / "ui" / "addons" / "x.lua", b"return 1\n")
    _w(a / "README.txt", b"docs")
    b = exts / "bbb"
    _w(b / "content.xml", b'<content id="bbb" name="B" version="1"/>')
    _pack(b, [("libraries/wares.xml",
               b'<diff><replace sel="//ware[@id=\'ore\']/price/@max">30</replace></diff>'),
              ("ui/ships/ship_b1.gz", b"PACKED-TEXTURE" * 40),
              ("ui/ships/ship_b2.gz", b"PACKED-TEXTURE-2" * 40),
              ("md/bbb.xml", b'<mdscript name="bbb"><cues/></mdscript>')])
    _w(b / "libraries" / "icons.xml", ICONS.format(m="bbb"))
    _w(exts / "ego_dlc_test" / "content.xml", b'<content id="ego_dlc_test" version="1"/>')
    profile = _w(tmp_path / "profile" / "content.xml",
                 b'<content><extension id="aaa" enabled="true"/>'
                 b'<extension id="bbb" enabled="true"/></content>')
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", exts)
    monkeypatch.setattr(_registry, "PROFILE_EXTENSIONS", None)
    monkeypatch.setattr(_registry, "WORKSHOP_CONTENT", None)
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", profile)
    # `_ext_root` stamps a content axis only for the CONFIGURED reference.
    monkeypatch.setattr(_effective._paths, "reference", lambda: ref)
    monkeypatch.setattr(_merge.Config, "dlc_dirs",
                        lambda self: [ref / "extensions" / "ego_dlc_split"])
    return ref, exts, profile


def _build(tmp_path, monkeypatch, name="eff.sqlite", progress=None):
    ref, exts, profile = _world(tmp_path, monkeypatch) if not (tmp_path / "reference").is_dir() \
        else (tmp_path / "reference", tmp_path / "extensions", tmp_path / "profile" / "content.xml")
    db = tmp_path / name
    _effective.build(_merge.Config(reference=ref), db, kinds=("ware",),
                     progress=progress or (lambda s: None))
    return db, ref, exts


def _prove(db, ref):
    return _inputs.prove(db, config=_merge.Config(reference=ref))


def _analysis(snap_or_none, ref) -> str:
    """x4compat's full answer, with the snapshot's root spelled as the live one."""
    cfg = _merge.Config(reference=ref)
    if snap_or_none is None:
        rep = _compat.analyze(None, config=cfg)
    else:
        with _inputs.world(snap_or_none):
            rep = _compat.analyze(None, config=cfg)
    text = _compat.render(rep, show_soft=True) + json.dumps(
        [dataclasses.asdict(c) for c in rep.collisions], sort_keys=True, default=str)
    if snap_or_none is not None:
        for d, rec in zip(snap_or_none.dirs(), snap_or_none.manifest["roots"]):
            for a, b in ((str(d), rec["live"]), (d.as_posix(), Path(rec["live"]).as_posix())):
                text = text.replace(a, b)
    return text


# --------------------------------------------------------------------------- the store side

def test_every_build_writes_a_snapshot_and_records_its_manifest(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    snap = _inputs.load(db)
    assert snap is not None, "no snapshot beside the store"
    con = sqlite3.connect(db)
    meta = dict(con.execute("select key, value from meta"))
    con.close()
    assert meta["input_snapshot"] == snap.manifest_sha
    receipt = json.loads((snap.path / _inputs.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["disposition"] == _inputs.DISPOSITION
    assert receipt["manifest_sha256"] == snap.manifest_sha
    assert not list(tmp_path.glob("*.tmp")), "a temp snapshot was left behind"


def test_the_store_is_stamped_with_the_LIVE_vector(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    stored = _freshness.read_sqlite(con)
    con.close()
    live = _freshness.fingerprint(_merge.Config(reference=ref), [exts])
    assert stored["content"] == live["content"] and stored["detail"] == live["detail"]


def test_the_store_is_built_FROM_the_snapshot(tmp_path, monkeypatch):
    """A live edit made after the snapshot was taken must not reach the store."""
    ref, exts, _ = _world(tmp_path, monkeypatch)
    real_take = _inputs.take

    def take_then_edit(*a, **k):
        snap = real_take(*a, **k)
        _w(exts / "aaa" / "libraries" / "wares.xml",
           b'<diff><replace sel="//ware[@id=\'ore\']/price/@average">99</replace></diff>')
        return snap
    monkeypatch.setattr(_inputs, "take", take_then_edit)
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, kinds=("ware",))
    con = sqlite3.connect(db)
    vals = {v for (v,) in con.execute(
        "select a.value from attrs a join entities e on e.id=a.entity_id "
        "where e.name='ore' and a.prop='price.average'")}
    con.close()
    assert vals == {"20"}, vals


# --------------------------------------------------------------------------- prove()

def test_a_mod_edited_after_the_build_does_not_refuse_and_the_verdict_is_unchanged(
        tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap, why = _prove(db, ref)
    assert why == []
    before = _analysis(snap, ref)
    _w(exts / "aaa" / "libraries" / "wares.xml",
       b'<diff><replace sel="//ware[@id=\'ore\']/price/@min">7</replace></diff>')
    _w(exts / "ccc" / "content.xml", b'<content id="ccc" version="1"/>')
    snap, why = _prove(db, ref)
    assert why == [], why
    assert _analysis(snap, ref) == before


def test_TWIN_editing_the_snapshot_copy_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    copy = _inputs.snapshot_path(db) / "r0" / "aaa" / "libraries" / "wares.xml"
    copy.write_bytes(copy.read_bytes() + b"\n")
    _, why = _prove(db, ref)
    assert any("aaa/libraries/wares.xml" in w for w in why), why


def test_TWIN_a_file_added_to_the_snapshot_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    _w(_inputs.snapshot_path(db) / "r0" / "aaa" / "extra.xml", b"<x/>")
    _, why = _prove(db, ref)
    assert any("not in the manifest" in w for w in why), why


def _set_meta(db, key, value):
    con = sqlite3.connect(db)
    if value is None:
        con.execute("delete from meta where key=?", (key,))
    else:
        con.execute("insert or replace into meta values(?,?)", (key, value))
    con.commit()
    con.close()


def test_one_detail_triple_differing_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    detail = json.loads(con.execute("select value from meta where key='fingerprint_detail'")
                        .fetchone()[0])
    con.close()
    rec = next(r for r in detail if r["folder"] == "aaa")
    rec["entries"][0][1] += 1                     # one mtime, one file
    _set_meta(db, "fingerprint_detail", json.dumps(detail, separators=(",", ":")))
    _, why = _prove(db, ref)
    assert any("per-file detail differs for aaa" in w for w in why), why


def test_TWIN_identical_detail_passes(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    detail = con.execute("select value from meta where key='fingerprint_detail'").fetchone()[0]
    con.close()
    _set_meta(db, "fingerprint_detail", json.dumps(json.loads(detail), separators=(",", ":")))
    assert _prove(db, ref)[1] == []


def test_a_store_with_no_input_snapshot_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    _set_meta(db, "input_snapshot", None)
    _, why = _prove(db, ref)
    assert why and "no input snapshot" in why[0], why


def test_TWIN_a_store_with_its_snapshot_proceeds(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    snap, why = _prove(db, ref)
    assert why == [] and snap is not None and snap.path == _inputs.snapshot_path(db)


def test_a_snapshot_from_ANOTHER_build_refuses(tmp_path, monkeypatch):
    """The store records which manifest it was built from; a snapshot that is intact and
    self-consistent but belongs to another build (a swap that raced, a copied folder) must
    not pass. Killed hand mutant M1 (manifest-sha compare removed)."""
    db, ref, _ = _build(tmp_path, monkeypatch)
    _set_meta(db, "input_snapshot", "f" * 64)
    _, why = _prove(db, ref)
    assert any("not the one this store was built from" in w for w in why), why


def test_the_stores_content_digest_must_be_the_snapshots(tmp_path, monkeypatch):
    """Killed hand mutant M10 (content-digest compare removed)."""
    db, ref, _ = _build(tmp_path, monkeypatch)
    _set_meta(db, "fingerprint_content", "0" * 16)
    _, why = _prove(db, ref)
    assert any("content digest is not the snapshot's" in w for w in why), why


def test_a_missing_snapshot_folder_refuses(tmp_path, monkeypatch):
    import shutil
    db, ref, _ = _build(tmp_path, monkeypatch)
    shutil.rmtree(_inputs.snapshot_path(db))
    snap, why = _prove(db, ref)
    assert snap is None and why


def test_an_engine_edit_after_the_build_still_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_freshness, "hash_engine", lambda *a, **k: "0" * 16)
    _, why = _prove(db, ref)
    assert any("engine changed" in w for w in why), why


def test_a_reference_move_after_the_build_still_refuses(tmp_path, monkeypatch):
    db, ref, _ = _build(tmp_path, monkeypatch)
    _w(ref / "aiscripts" / "new.xml", b"<aiscript/>")
    _, why = _prove(db, ref)
    assert any("reference tree moved" in w for w in why), why


def test_a_live_DLC_change_after_the_build_refuses(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    _w(exts / "ego_dlc_test" / "content.xml", b'<content id="ego_dlc_test" version="2"/>')
    _, why = _prove(db, ref)
    assert any("DLC ego_dlc_test changed" in w for w in why), why


# --------------------------------------------------------------------------- take()

def test_a_mid_snapshot_edit_is_retried_then_refused_naming_the_file(tmp_path, monkeypatch):
    ref, exts, _ = _world(tmp_path, monkeypatch)
    calls = []

    def edit(attempt):
        calls.append(attempt)
        p = exts / "aaa" / "libraries" / "wares.xml"
        _w(p, p.read_bytes() + b" ")
    monkeypatch.setattr(_inputs, "_AFTER_COPY", edit)
    db = tmp_path / "eff.sqlite"
    with pytest.raises(_inputs.SnapshotRefused) as exc:
        _effective.build(_merge.Config(reference=ref), db, kinds=("ware",))
    assert "aaa/libraries/wares.xml" in str(exc.value)
    assert calls == list(range(1 + _inputs.RETRIES))
    assert not db.exists() and not _inputs.snapshot_path(db).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_TWIN_a_still_tree_takes_one_pass(tmp_path, monkeypatch):
    ref, exts, _ = _world(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(_inputs, "_AFTER_COPY", calls.append)
    _effective.build(_merge.Config(reference=ref), tmp_path / "eff.sqlite", kinds=("ware",))
    assert calls == [0]


def test_one_edit_during_the_first_attempt_is_absorbed_by_a_retake(tmp_path, monkeypatch):
    ref, exts, _ = _world(tmp_path, monkeypatch)
    calls = []

    def edit_once(attempt):
        calls.append(attempt)
        if attempt == 0:
            _w(exts / "aaa" / "ui" / "ships" / "ship_a3.gz", b"new")   # a NAME stub moved
    monkeypatch.setattr(_inputs, "_AFTER_COPY", edit_once)
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, kinds=("ware",))
    assert calls == [0, 1]
    assert (_inputs.snapshot_path(db) / "r0" / "aaa" / "ui" / "ships" / "ship_a3.gz").is_file()


# --------------------------------------------------------------------------- copy fidelity

def test_a_packed_XML_member_read_from_the_snapshot_is_byte_identical(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap_mod = _inputs.snapshot_path(db) / "r0" / "bbb"
    for vp in ("libraries/wares.xml", "md/bbb.xml"):
        assert _cat.read_path(snap_mod, vp) == _cat.read_path(exts / "bbb", vp)
    assert (snap_mod / "ext_01.dat").stat().st_size < (exts / "bbb" / "ext_01.dat").stat().st_size


def test_TWIN_reading_a_zeroed_non_XML_member_fails_md5_loudly(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap_mod = _inputs.snapshot_path(db) / "r0" / "bbb"
    assert _cat.read_path(exts / "bbb", "ui/ships/ship_b1.gz")      # live: readable
    with pytest.raises(OSError, match="MD5 mismatch"):
        _cat.read_path(snap_mod, "ui/ships/ship_b1.gz")


def test_loose_copies_are_exact_and_other_files_are_name_stubs(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    s = _inputs.snapshot_path(db) / "r0" / "aaa"
    assert (s / "ui" / "addons" / "x.lua").read_bytes() == b"return 1\n"
    assert (s / "ui" / "ships" / "ship_a1.gz").stat().st_size == 0
    assert (s / "README.txt").stat().st_size == 0
    assert int((s / "libraries" / "wares.xml").stat().st_mtime) == \
        int((exts / "aaa" / "libraries" / "wares.xml").stat().st_mtime)
    dlc = _inputs.snapshot_path(db) / "r0" / "ego_dlc_test"
    assert [p.name for p in dlc.iterdir()] == ["content.xml"]


def _icon_ids(folder_to_path):
    out = {}
    for m in ("aaa", "bbb"):
        ids, why = _compat._wildcard_icon_ids("ship_*", f"extensions\\{m}\\ui\\ships\\*.tga",
                                              folder_to_path)
        out[m] = (sorted(ids) if ids is not None else None, why)
    return out


def test_wildcard_icon_ids_are_identical_live_and_from_the_snapshot(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap = _inputs.snapshot_path(db) / "r0"
    live = _icon_ids({"aaa": exts / "aaa", "bbb": exts / "bbb"})
    assert live["aaa"][0] and live["bbb"][0], live              # the fixture expands
    assert _icon_ids({"aaa": snap / "aaa", "bbb": snap / "bbb"}) == live


def test_TWIN_deleting_a_stub_changes_the_icon_ids(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap = _inputs.snapshot_path(db) / "r0"
    live = _icon_ids({"aaa": exts / "aaa", "bbb": exts / "bbb"})
    (snap / "aaa" / "ui" / "ships" / "ship_a2.gz").unlink()
    assert _icon_ids({"aaa": snap / "aaa", "bbb": snap / "bbb"}) != live


def test_parity_analyze_live_vs_from_a_snapshot_of_a_still_fixture(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    live = _analysis(None, ref)
    assert "bbb" in live and "aaa" in live                     # the analysis saw both mods
    snap = _inputs.load(db)
    assert snap is not None                                     # else this compares live to live
    assert _analysis(snap, ref) == live


def test_world_restores_the_roots_and_points_them_at_the_snapshot(tmp_path, monkeypatch):
    db, ref, exts = _build(tmp_path, monkeypatch)
    snap = _inputs.load(db)
    before = {n: getattr(_registry, n) for n in _inputs.ROOT_CONSTANTS}
    with _inputs.world(snap):
        assert _registry.GAME_EXTENSIONS == snap.path / "r0"
        assert _registry.PROFILE_CONTENT == snap.path / "p" / "content.xml"
        assert _registry.PROFILE_EXTENSIONS is None
    assert {n: getattr(_registry, n) for n in _inputs.ROOT_CONSTANTS} == before


# --------------------------------------------------------------------------- disposal

def test_a_previous_snapshot_whose_receipt_and_hashes_match_is_deleted(tmp_path, monkeypatch):
    lines: list[str] = []
    recycled: list[Path] = []
    monkeypatch.setattr(_inputs, "_recycle", lambda p: recycled.append(p))
    db, ref, _ = _build(tmp_path, monkeypatch)
    _build(tmp_path, monkeypatch, progress=lines.append)
    assert any(x.startswith("deleted the previous input snapshot") for x in lines), lines
    assert recycled == [] and not list(tmp_path.glob("*.tmp"))


def test_TWIN_a_hand_edited_previous_snapshot_goes_to_the_recycle_bin(tmp_path, monkeypatch):
    import shutil
    lines: list[str] = []
    recycled: list[Path] = []

    def fake_bin(p):
        recycled.append(p)
        shutil.move(str(p), str(tmp_path / "bin"))
    monkeypatch.setattr(_inputs, "_recycle", fake_bin)
    db, ref, _ = _build(tmp_path, monkeypatch)
    f = _inputs.snapshot_path(db) / "r0" / "aaa" / "content.xml"
    f.write_bytes(f.read_bytes() + b"<!-- mine -->")
    _build(tmp_path, monkeypatch, progress=lines.append)
    assert len(recycled) == 1 and any("Recycle Bin" in x for x in lines), lines
    assert (tmp_path / "bin" / "r0" / "aaa" / "content.xml").read_bytes().endswith(b"<!-- mine -->")


def test_a_failed_recycle_leaves_the_folder_in_place_never_deletes_it(tmp_path, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(_inputs, "_recycle", lambda p: "no bin here")
    db, ref, _ = _build(tmp_path, monkeypatch)
    (_inputs.snapshot_path(db) / _inputs.RECEIPT).unlink()
    _build(tmp_path, monkeypatch, progress=lines.append)
    left = list(tmp_path.glob("eff.sqlite.inputs.old.*.tmp"))
    assert len(left) == 1 and any("left the previous input snapshot in place" in x for x in lines)


# --------------------------------------------------------------------------- the root constants

def _module_level_copies(tree: ast.Module) -> list[str]:
    names = set(_inputs.ROOT_CONSTANTS)
    bad = []

    def refs(node) -> bool:
        return any(isinstance(n, ast.Attribute) and n.attr in names
                   and isinstance(n.value, ast.Name) and n.value.id.endswith("_registry")
                   for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("_registry"):
            bad += [f"from-import of {a.name}" for a in node.names if a.name in names]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
            bad += [f"default argument in {getattr(node, 'name', 'lambda')}"
                    for d in defaults if refs(d)]
    for stmt in tree.body:                        # module level, and class bodies
        bodies = [stmt] + (list(stmt.body) if isinstance(stmt, ast.ClassDef) else [])
        for s in bodies:
            if isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and s.value is not None \
                    and refs(s.value):
                bad.append(f"module/class-level assignment at line {s.lineno}")
    return bad


def test_no_module_keeps_an_import_time_copy_of_the_root_constants():
    files = [p for p in sorted(PKG.glob("*.py")) + sorted(GATES.glob("*.py"))
             if p.name != "_registry.py"]
    assert len(files) > 50                          # the population is the package + gates
    offenders = {p.name: b for p in files
                 if (b := _module_level_copies(ast.parse(p.read_text(encoding="utf-8"))))}
    assert not offenders, offenders


def test_TWIN_the_root_constant_scan_finds_each_copy_shape():
    src = ("from x4validate._registry import GAME_EXTENSIONS\n"
           "from x4validate import _registry\n"
           "EXT = _registry.PROFILE_CONTENT\n"
           "def f(x=_registry.WORKSHOP_CONTENT):\n    return x\n"
           "class C:\n    root = _registry.PROFILE_EXTENSIONS\n"
           "def ok():\n    return _registry.GAME_EXTENSIONS\n")
    assert len(_module_level_copies(ast.parse(src))) == 4


def test_inputs_is_an_engine_source():
    assert "_inputs.py" in _freshness.ENGINE_SOURCES


# --------------------------------------------------------------------------- a leftover temp

def _take(tmp_path, monkeypatch, lines):
    ref, exts, _ = (_world(tmp_path, monkeypatch) if not (tmp_path / "reference").is_dir()
                    else (tmp_path / "reference", tmp_path / "extensions", None))
    stamp = {"engine": "e", "reference": "r", "detail": []}
    return _inputs.take(tmp_path / "eff.sqlite", [exts], lambda: dict(stamp),
                        progress=lines.append)


def _leftover(tmp_path) -> Path:
    import os
    return tmp_path / f"eff.sqlite{_inputs.SUFFIX}.{os.getpid()}.tmp"


def test_a_leftover_temp_proven_by_its_receipt_is_deleted(tmp_path, monkeypatch):
    """C5a: an existing `<db>.inputs.<pid>.tmp` at the start of take() is not this run's;
    it is deleted outright only when its receipt and every hash prove it rebuildable."""
    recycled: list[Path] = []
    monkeypatch.setattr(_inputs, "_recycle", lambda p: recycled.append(p) or "planted")
    lines: list[str] = []
    first = _take(tmp_path, monkeypatch, lines)
    assert first.path == _leftover(tmp_path) and (first.path / _inputs.RECEIPT).is_file()
    lines.clear()
    _take(tmp_path, monkeypatch, lines)
    assert recycled == [] and any("deleted" in x and "receipt" in x for x in lines), lines


def test_TWIN_an_unproven_leftover_temp_goes_to_the_recycle_bin(tmp_path, monkeypatch):
    import shutil
    recycled: list[Path] = []

    def fake_bin(p):
        recycled.append(p)
        shutil.move(str(p), str(tmp_path / "bin"))
    monkeypatch.setattr(_inputs, "_recycle", fake_bin)
    _world(tmp_path, monkeypatch)
    _w(_leftover(tmp_path) / "mine.txt", b"not a snapshot")
    lines: list[str] = []
    _take(tmp_path, monkeypatch, lines)
    assert recycled == [_leftover(tmp_path)] and (tmp_path / "bin" / "mine.txt").is_file()
    assert any("Recycle Bin" in x for x in lines), lines


def test_TWIN_an_unproven_leftover_temp_that_cannot_be_recycled_refuses_never_deletes(
        tmp_path, monkeypatch):
    monkeypatch.setattr(_inputs, "_recycle", lambda p: "no bin here")
    _world(tmp_path, monkeypatch)
    _w(_leftover(tmp_path) / "mine.txt", b"not a snapshot")
    with pytest.raises(_inputs.SnapshotRefused, match="leftover"):
        _take(tmp_path, monkeypatch, [])
    assert (_leftover(tmp_path) / "mine.txt").read_bytes() == b"not a snapshot"


def test_a_failed_snapshot_install_leaves_a_new_store_that_fails_CLOSED(tmp_path, monkeypatch):
    """C5c (the `_effective.build` ORDER comment): the store is installed before its
    snapshot, so a failing `_inputs.install` leaves the NEW store beside the OLD snapshot;
    `prove` must refuse that pair, never accept it."""
    db, ref, exts = _build(tmp_path, monkeypatch)
    (exts / "aaa" / "libraries" / "wares.xml").write_bytes(
        b'<diff><replace sel="//ware[@id=\'ore\']/price/@average">21</replace></diff>')

    def broken(*a, **k):
        raise OSError("planted install failure")
    monkeypatch.setattr(_inputs, "install", broken)
    with pytest.raises(OSError, match="planted"):
        _build(tmp_path, monkeypatch)
    _, why = _prove(db, ref)
    assert any("not the one this store was built from" in w for w in why), why
    assert not list(tmp_path.glob("*.inputs.*.tmp")), "the unfinished temp is discarded"


# --------------------------------------------------------------------------- the CLI (C1)

def _cli_build(tmp_path, ref, capsys):
    from x4validate import _effectivecli
    rc = _effectivecli.main(["--db", str(tmp_path / "eff.sqlite"), "build",
                             "--reference", str(ref), "--kinds", "ware"])
    return rc, capsys.readouterr().err


def test_x4effective_build_prints_a_snapshot_refusal_and_exits_2(tmp_path, monkeypatch, capsys):
    """`_inputs.SnapshotRefused` (a RuntimeError) reached the CLI uncaught: a traceback and
    exit 1, which cross_tool counted as a FAILED build. It is a refusal: one line, exit 2."""
    ref, exts, _ = _world(tmp_path, monkeypatch)

    def edit(attempt):
        p = exts / "aaa" / "libraries" / "wares.xml"
        _w(p, p.read_bytes() + b" ")
    monkeypatch.setattr(_inputs, "_AFTER_COPY", edit)
    rc, err = _cli_build(tmp_path, ref, capsys)
    assert rc == 2, err
    assert "x4effective build: REFUSED" in err and "kept moving" in err, err
    assert "Traceback" not in err


def test_TWIN_a_real_build_error_is_still_not_a_refusal(tmp_path, monkeypatch, capsys):
    from x4validate import _effectivecli
    ref, exts, _ = _world(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("a genuine build defect")
    monkeypatch.setattr(_effectivecli, "build", boom)
    with pytest.raises(RuntimeError, match="genuine"):   # uncaught: the process exits 1
        _cli_build(tmp_path, ref, capsys)

"""Issue #10, release-review finding: DLC documents must not satisfy the reference check.

`require_base_documents` counted `base_vpaths(config, "*.xml")`, which INCLUDES DLC --
and for the CONFIGURED reference `Config.dlc_dirs()` adds every game DLC the reference
lacks unpacked, read from the install's catalogs. So an existing-but-EMPTY reference on a
machine with a real install enumerated the packed DLC (n>0) and every builder passed.
MEASURED by the release reviewer: empty `libraries/` reference + X4_GAME at a fake install
with one packed `ego_dlc_test` -> not refused (n=1). The first round of tests pinned
`include_packed_dlc=False` / a game root that does not exist, which hid it.

The world here is the configured one (`_merge.REFERENCE` and `_merge.GAME_ROOT`
monkeypatched to tmp_path), so the packed supplement is genuinely live. Every X4_* is
dropped; X4_CONFIG names an empty file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from x4validate import _effective, _effectivecli, _merge, _xref

BASEX = Path(__file__).resolve().parents[2] / "basex"


def _write_cat(mod_dir, cat_name, members):
    mod_dir.mkdir(parents=True, exist_ok=True)
    lines, blob = [], bytearray()
    for vpath, data in members:
        lines.append(f"{vpath} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    (mod_dir / cat_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (mod_dir / cat_name).with_suffix(".dat").write_bytes(bytes(blob))


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, BASEX / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg))
    game = tmp_path / "game"
    _write_cat(game / "extensions" / "ego_dlc_test", "ext_01.cat",
               [("libraries/wares.xml", b"<diff><add sel='/wares'/></diff>")])
    monkeypatch.setattr(_merge, "GAME_ROOT", game)
    ext = tmp_path / "extensions"
    ext.mkdir()
    monkeypatch.setattr(_effective, "active_mods", lambda *a, **k: [])
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_xref._registry, "default_installed_dirs", lambda: [ext])

    def configure(shape: str) -> Path:
        ref = tmp_path / f"{shape}-ref"
        if shape == "empty":
            (ref / "libraries").mkdir(parents=True)
        elif shape == "dlc-only":               # an unpacked DLC, but no base game
            (ref / "extensions" / "ego_dlc_x" / "libraries").mkdir(parents=True)
            (ref / "extensions" / "ego_dlc_x" / "libraries" / "wares.xml").write_text(
                "<diff/>", encoding="utf-8")
        elif shape == "good":
            (ref / "libraries").mkdir(parents=True)
            (ref / "libraries" / "wares.xml").write_text(
                "<wares><ware id='ore'/></wares>", encoding="utf-8")
            (ref / "md").mkdir()
            (ref / "md" / "s.xml").write_text(
                '<mdscript name="S"><cues><cue name="C"/></cues></mdscript>', encoding="utf-8")
        monkeypatch.setattr(_merge, "REFERENCE", ref)    # CONFIGURED: supplement is live
        _effective.base_vpaths.cache_clear()
        return ref

    yield tmp_path, ext, configure
    _effective.base_vpaths.cache_clear()


def test_the_fixture_really_supplies_packed_dlc(world):
    """Control: without this the refusals below could pass over a world with no DLC."""
    _tmp, _ext, configure = world
    ref = configure("empty")
    cfg = _merge.Config(reference=ref)
    assert "ego_dlc_test" in cfg.packed_dlc_names()
    assert any(v.startswith("extensions/ego_dlc_test/")
               for v in _effective.base_vpaths(cfg, "*.xml"))


@pytest.mark.parametrize("shape", ["empty", "dlc-only"])
def test_helper_refuses_when_only_DLC_documents_exist(world, shape):
    _tmp, _ext, configure = world
    ref = configure(shape)
    with pytest.raises(_effective.EmptyReference, match="no base-game"):
        _effective.require_base_documents(_merge.Config(reference=ref), "build")


def test_TWIN_helper_passes_with_one_base_document_and_reports_both(world):
    _tmp, _ext, configure = world
    ref = configure("good")
    n_base, n_dlc = _effective.require_base_documents(_merge.Config(reference=ref), "build")
    assert n_base == 2 and n_dlc == 1


def test_TWIN_missing_reference_still_refuses_with_packed_dlc(world):
    """The other clause, under the same live supplement."""
    _tmp, _ext, configure = world
    ref = configure("missing")
    with pytest.raises(_effective.EmptyReference, match="does not exist"):
        _effective.require_base_documents(_merge.Config(reference=ref), "build")


@pytest.mark.parametrize("shape,rc", [("empty", 2), ("good", 0)])
def test_x4effective_build(world, capsys, shape, rc):
    tmp, _ext, configure = world
    ref = configure(shape)
    db = tmp / "eff.sqlite"
    got = _effectivecli.main(["--db", str(db), "build", "--reference", str(ref),
                              "--kinds", "ware"])
    assert got == rc, capsys.readouterr().err
    assert db.exists() == (rc == 0)


@pytest.mark.parametrize("shape,rc", [("empty", 2), ("good", 0)])
def test_x4xref_build(world, capsys, shape, rc):
    tmp, ext, configure = world
    ref = configure(shape)
    got = _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                      "--out", str(tmp / "x.tsv")])
    assert got == rc, capsys.readouterr().err


@pytest.mark.parametrize("shape,rc", [("empty", 2), ("good", 0)])
def test_basex_build_effective_py(world, capsys, monkeypatch, shape, rc):
    tmp, _ext, configure = world
    ref = configure(shape)
    mod = _load("build-effective.py", "bxeff_packed_dlc")
    monkeypatch.setattr(mod, "installed_in_load_order", lambda config: [])
    got = mod.main(["--out", str(tmp / "_eff" / "tree"), "--reference", str(ref)])
    assert got == rc, capsys.readouterr()


@pytest.mark.parametrize("shape,rc", [("empty", 2), ("dlc-only", 2), ("good", 0)])
def test_reference_check_py_used_by_both_shell_scripts(world, capsys, shape, rc):
    _tmp, _ext, configure = world
    ref = configure(shape)
    mod = _load("reference_check.py", "refcheck_packed_dlc")
    got = mod.main(["--reference", str(ref)])
    out = capsys.readouterr()
    assert got == rc, out
    if rc == 0:
        assert "base" in out.out and "DLC" in out.out

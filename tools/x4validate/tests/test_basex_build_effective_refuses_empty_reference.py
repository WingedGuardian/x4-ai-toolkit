"""The issue #10 shape in `tools/basex/build-effective.py`: refuse a missing/empty reference.

MEASURED on bf2e9dc with every X4_* pinned to scratch: `--reference <missing>` and
`--reference <empty dir>` both printed `TOTAL 0 documents` and exited 0 -- after
`shutil.rmtree(out)` had already wiped the previous tree. The only signal was a `!!`
line ("source(s) that contributed NOTHING: reference"); `build-effective.sh` treats
rc 0 as success and goes on to index the empty tree into BaseX x4eff.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from x4validate import _effective

BASEX = Path(__file__).resolve().parents[2] / "basex"


@pytest.fixture
def builder(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg))
    spec = importlib.util.spec_from_file_location("basex_build_effective_h7",
                                                  BASEX / "build-effective.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "installed_in_load_order", lambda config: [])
    _effective.base_vpaths.cache_clear()
    yield mod
    _effective.base_vpaths.cache_clear()


def _previous_build(tmp_path):
    tree = tmp_path / "_eff" / "tree"
    (tree / "libraries").mkdir(parents=True)
    (tree / "libraries" / "wares.xml").write_bytes(b"<wares><ware id='kept'/></wares>")
    return tree


@pytest.mark.parametrize("shape", ["missing", "empty"])
def test_build_effective_refuses_and_keeps_the_previous_tree(builder, tmp_path, capsys, shape):
    ref = tmp_path / f"{shape}-ref"
    if shape == "empty":
        ref.mkdir()
    tree = _previous_build(tmp_path)
    rc = builder.main(["--out", str(tree), "--reference", str(ref)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert (tree / "libraries" / "wares.xml").read_bytes() == b"<wares><ware id='kept'/></wares>"
    assert not (tree.parent / "effective-manifest.json").exists(), \
        "a refused build must not write a manifest build-effective.sh would index against"
    assert str(ref) in err


def test_TWIN_build_effective_over_a_minimal_reference_still_builds(builder, tmp_path, capsys):
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(b"<wares><ware id='ore'/></wares>")
    tree = tmp_path / "_eff" / "tree"
    rc = builder.main(["--out", str(tree), "--reference", str(ref)])
    assert rc == 0, capsys.readouterr()
    assert (tree / "libraries" / "wares.xml").is_file()


def test_existing_build_recycle_refusal_stops_before_rebuild(builder, tmp_path, monkeypatch, capsys):
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries/wares.xml").write_bytes(b"<wares/>")
    tree = _previous_build(tmp_path)
    def refuse(paths):
        raise OSError("declined")
    monkeypatch.setattr(builder, "recycle_or_raise", refuse)
    assert builder.main(["--out", str(tree), "--reference", str(ref)]) == 2
    assert "declined" in capsys.readouterr().err
    assert (tree / "libraries/wares.xml").read_bytes() == b"<wares><ware id='kept'/></wares>"

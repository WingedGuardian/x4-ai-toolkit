"""The issue #10 shape in `x4xref build`: a missing or empty reference must REFUSE.

MEASURED on bf2e9dc with every X4_* pinned to scratch: `x4xref build --reference
<missing>` and `--reference <empty dir>` both printed `indexed 0 rows` and exited 0,
overwriting the TSV and stamping it. An xref index exists to make NEGATIVES admissible
("nobody calls X"); an index of nothing certifies every negative. The extensions dir
already refuses (rc 2) -- the reference, the larger half of the population, did not.
"""
from __future__ import annotations

import os

import pytest

from x4validate import _effective, _xref


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg))
    ext = tmp_path / "extensions"
    ext.mkdir()
    monkeypatch.setattr(_xref._registry, "default_installed_dirs", lambda: [ext])
    _effective.base_vpaths.cache_clear()
    yield tmp_path, ext
    _effective.base_vpaths.cache_clear()


def _build(ref, ext, out):
    return _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                       "--out", str(out)])


def test_xref_build_over_a_MISSING_reference_refuses_and_keeps_the_old_index(hermetic, capsys):
    tmp, ext = hermetic
    out = tmp / "md_xref.tsv"
    out.write_bytes(b"SENTINEL: a previously built index")
    missing = tmp / "missing-ref"
    rc = _build(missing, ext, out)
    err = capsys.readouterr().err
    assert rc == 2, err
    assert out.read_bytes() == b"SENTINEL: a previously built index"
    assert str(missing) in err and "does not exist" in err
    assert "X4_REFERENCE" in err


def test_xref_build_over_an_EMPTY_reference_refuses(hermetic, capsys):
    tmp, ext = hermetic
    empty = tmp / "empty-ref"
    (empty / "md").mkdir(parents=True)
    out = tmp / "md_xref.tsv"
    rc = _build(empty, ext, out)
    err = capsys.readouterr().err
    assert rc == 2, err
    assert not out.exists()
    assert str(empty) in err


def test_TWIN_xref_build_over_a_minimal_reference_still_builds(hermetic, capsys):
    tmp, ext = hermetic
    ref = tmp / "reference"
    (ref / "md").mkdir(parents=True)
    (ref / "md" / "ok.xml").write_text(
        '<mdscript name="S"><cues><cue name="C"/></cues></mdscript>', encoding="utf-8")
    out = tmp / "md_xref.tsv"
    assert _build(ref, ext, out) == 0, capsys.readouterr().err
    assert out.is_file()

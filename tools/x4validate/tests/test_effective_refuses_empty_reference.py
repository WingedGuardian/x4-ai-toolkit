"""Issue #10: `x4effective build` over a reference tree that holds nothing must REFUSE.

MEASURED on master bf2e9dc (empty X4_CONFIG, X4_REFERENCE naming a directory that does
not exist): every category printed 0, the last line was `wrote 0 entities to <db>`, and
the exit code was 0 -- an empty store that claims to model the game, written over
whatever store was there before. The same happened with a reference directory that
EXISTS but is empty. Base enumeration (`_effective.base_vpaths`) is an rglob, and an
rglob over a missing or empty root returns nothing without complaint, so nothing in the
build could tell "the game has no wares" from "I looked in the wrong place".

The refusal must come BEFORE the store is touched: a rebuild over a wrong path used to
REPLACE a good store with an empty one.

Every X4_* variable is removed and X4_CONFIG names an empty file in tmp_path, so nothing
here can read the real game, profile or reference.
"""
from __future__ import annotations

import os

import pytest

from x4validate import _effective, _effectivecli, _merge


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg))
    # No installed mods, no profile: the build sees base+DLC only.
    monkeypatch.setattr(_effective, "active_mods", lambda *a, **k: [])
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    _effective.base_vpaths.cache_clear()
    yield tmp_path
    _effective.base_vpaths.cache_clear()


def _good_store(path):
    """A pre-existing store, as bytes we can compare after a refused build."""
    path.write_bytes(b"SENTINEL: a previously built store that must survive")
    return path.read_bytes(), path.stat().st_mtime_ns


# ---- the CLI: missing reference ---------------------------------------------------

def test_cli_build_over_a_MISSING_reference_refuses_rc2(hermetic, capsys):
    missing = hermetic / "missing-ref"
    db = hermetic / "eff.sqlite"
    rc = _effectivecli.main(["--db", str(db), "build", "--reference", str(missing)])
    err = capsys.readouterr().err
    assert rc == 2, f"expected a configuration refusal (rc 2), got rc {rc}; stderr:\n{err}"
    assert not db.exists(), "a refused build must not write a store"
    assert str(missing) in err, "the refusal must name the root it looked in"
    assert "does not exist" in err
    assert "X4_REFERENCE" in err and "--reference" in err, "must say how to configure it"


def test_cli_refused_build_leaves_an_EXISTING_store_untouched(hermetic, capsys):
    db = hermetic / "eff.sqlite"
    before, mtime = _good_store(db)
    rc = _effectivecli.main(["--db", str(db), "build", "--reference",
                             str(hermetic / "missing-ref")])
    assert rc == 2
    assert db.read_bytes() == before and db.stat().st_mtime_ns == mtime
    assert not list(hermetic.glob("*.tmp")), "no temp store may be left behind"


# ---- the library: existing-but-empty reference -------------------------------------

def test_build_over_an_EXISTING_but_EMPTY_reference_refuses(hermetic):
    empty = hermetic / "empty-ref"
    (empty / "libraries").mkdir(parents=True)        # a directory, but no documents
    db = hermetic / "eff.sqlite"
    before, mtime = _good_store(db)
    with pytest.raises(_effective.EmptyReference) as exc:
        _effective.build(_merge.Config(reference=empty, include_packed_dlc=False), db,
                         dirs=[])
    msg = str(exc.value)
    assert str(empty) in msg and "no base-game XML" in msg
    assert db.read_bytes() == before and db.stat().st_mtime_ns == mtime
    # rc 2 at the CLI because it is a ValueError, the class the CLI already maps to 2.
    assert isinstance(exc.value, ValueError)


def test_cli_build_over_an_EMPTY_reference_refuses_rc2(hermetic, capsys):
    empty = hermetic / "empty-ref"
    empty.mkdir()
    db = hermetic / "eff.sqlite"
    rc = _effectivecli.main(["--db", str(db), "build", "--reference", str(empty)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert not db.exists()
    assert str(empty) in err


# ---- the falsification twin: a real (minimal) reference still builds --------------

def test_TWIN_a_minimal_valid_reference_still_builds(hermetic):
    ref = hermetic / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(
        b'<wares><ware id="ore" group="minerals"><price min="1" average="2" max="3"/></ware></wares>')
    db = hermetic / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref, include_packed_dlc=False), db,
                     dirs=[], kinds=("ware",))
    import sqlite3
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM entities WHERE kind='ware'").fetchone()[0] == 1
    finally:
        con.close()


def test_TWIN_cli_minimal_valid_reference_rc0(hermetic, capsys):
    ref = hermetic / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(b'<wares><ware id="ore"/></wares>')
    db = hermetic / "eff.sqlite"
    rc = _effectivecli.main(["--db", str(db), "build", "--reference", str(ref),
                             "--kinds", "ware"])
    assert rc == 0, capsys.readouterr().err
    assert db.is_file()

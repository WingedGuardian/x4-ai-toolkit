"""The x4xref index must go STALE when the code that decides its rows changes.

`check_index` compares with ``engine_dependent=False``, and none of `_xref.py`,
`_scan.py` (nor, before this, any index-shaping module) is consulted on the engine
axis, so an edit to `_walk`, `_SKIP_TAGS`, the script enumeration or the scan walk
left every existing index FRESH, certifying negatives the current code would not
produce. Same shape as F21/F53 for the effective store, which the engine axis closed.

The fix stamps an xref-specific digest of the BYTES of `_freshness.XREF_SOURCES`.
That population is derived by TRACING a real index build (as FR-2 does for the
store), and it must carry no CLI (F69): a help-text edit must not invalidate an
index, which is why the index-building code lives in `_xrefindex.py`, not in the
CLI module `_xref.py`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from x4validate import _freshness, _merge, _registry, _xref

PKG = Path(_freshness.__file__).resolve().parent
#: Run during a build but decide no row: the fingerprint itself, the mutation refusal,
#: and path configuration (the same exclusions FR-2 makes for the store).
_NOT_BYTES = {"_freshness.py", "_mutation.py", "_paths.py"}


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def hermetic(monkeypatch):
    monkeypatch.setattr(_registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", None)
    monkeypatch.setattr(_registry, "default_installed_dirs", lambda: [])


def _world(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "reference"
    _w(ref / "md" / "b.xml", '<mdscript name="B"><cues><cue name="BaseCue"/></cues></mdscript>')
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / "extensions" / "ego_dlc_test" / "md" / "d.xml",
       '<mdscript name="D"><cues><cue name="DlcCue"/></cues></mdscript>')
    ext = tmp_path / "ext"
    _w(ext / "moda" / "content.xml", '<content id="moda" version="1"/>')
    _w(ext / "moda" / "extensions" / "ownerb" / "md" / "p.xml",
       '<diff><add sel="//cues"><cue name="NestedCue"/></add></diff>')
    # A PACKED member too, so the catalog reader (`_cat.read_member`) is on the traced
    # path (review M-2): without it a catalog-only module could go unhashed unnoticed.
    data = b'<mdscript name="K"><cues><cue name="PackedCue"/></cues></mdscript>'
    (ext / "moda" / "ext_01.cat").write_text(
        f"md/k.xml {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}\n", encoding="utf-8")
    (ext / "moda" / "ext_01.dat").write_bytes(data)
    return ref, ext


def test_every_module_that_shapes_the_index_is_hashed_and_vice_versa(tmp_path, hermetic):
    ref, ext = _world(tmp_path)
    ran: set[str] = set()
    pkg = str(PKG)

    def prof(frame, event, arg):
        if event == "call" and frame.f_code.co_filename.startswith(pkg):
            ran.add(os.path.basename(frame.f_code.co_filename))

    sys.setprofile(prof)
    try:
        rows = _xref.build_index(ref, ext, [])   # dlc_dirs=None: Config decides the DLC set
    finally:
        sys.setprofile(None)
    assert {r.name for r in rows if r.kind == "cuedef"} >= {
        "BaseCue", "NestedCue", "PackedCue"}  # the trace saw loose AND packed reads
    ran -= _NOT_BYTES
    assert ran == set(_freshness.XREF_SOURCES), (
        f"traced but not hashed: {sorted(ran - set(_freshness.XREF_SOURCES))}; "
        f"hashed but not traced: {sorted(set(_freshness.XREF_SOURCES) - ran)}")


def _imports(name: str) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse((PKG / name).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def test_no_xref_source_carries_a_cli():
    """F69: a CLI-text edit in a hashed module would mark the index stale for nothing."""
    assert len(_freshness.XREF_SOURCES) >= 4
    for name in _freshness.XREF_SOURCES:
        assert (PKG / name).is_file(), name
        assert "argparse" not in _imports(name), name
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"], name


@pytest.mark.parametrize("name", list(_freshness.XREF_SOURCES))
def test_the_digest_moves_when_any_source_changes(tmp_path, name):
    copy = tmp_path / "pkg"
    copy.mkdir()
    for n in _freshness.XREF_SOURCES:
        shutil.copyfile(PKG / n, copy / n)
    before = _freshness.hash_engine(copy, _freshness.XREF_SOURCES)
    assert before == _freshness.hash_engine(PKG, _freshness.XREF_SOURCES)  # twin: a copy is equal
    (copy / name).write_bytes((copy / name).read_bytes() + b"\n# edited\n")
    assert _freshness.hash_engine(copy, _freshness.XREF_SOURCES) != before


@pytest.fixture
def built(tmp_path, hermetic, monkeypatch):
    ref, ext = _world(tmp_path)
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    tsv = tmp_path / "xref.tsv"
    assert _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                       "--out", str(tsv)]) == 0
    return tsv


def test_a_build_stamps_the_index_code_digest(built):
    assert _freshness.read_sidecar(built)["index_code"] == _freshness.hash_engine(
        None, _freshness.XREF_SOURCES)


def _restamp(tsv: Path, **change) -> None:
    side = _freshness._sidecar(tsv)
    data = json.loads(side.read_text(encoding="utf-8"))
    for k, v in change.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    side.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("value", ["0123456789abcdef", None])
def test_an_index_from_other_index_code_cannot_certify_a_negative(built, capsys, value):
    _restamp(built, index_code=value)
    capsys.readouterr()
    rc = _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(built)])
    cap = capsys.readouterr()
    assert rc == 2
    assert "STALE" in cap.err and "index code changed" in cap.err
    assert "a real negative" not in cap.out


def test_twin_matching_index_code_still_certifies(built, capsys):
    capsys.readouterr()
    rc = _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(built)])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert "STALE" not in cap.err and "a real negative" in cap.out


def test_index_code_mismatch_is_reported_by_check_index(built):
    """`x4fresh status` judges the index through `check_index`, so it must see it too."""
    _restamp(built, index_code="0123456789abcdef")
    _rows, _signed, _excl, verdict = _xref.check_index(built)
    assert not verdict.fresh
    assert any("index code changed" in r for r in verdict.reasons)


#: Imported (at module level, transitively) by `_xrefindex` but deciding no row, so NOT
#: hashed. Each needs its reason; an entry that is no longer imported fails below.
_IMPORTED_NOT_HASHED = {
    "_paths.py": "path configuration; WHICH roots are read is the content axis's job",
    "_mutation.py": "the mutation-gate refusal (imported by _paths); decides no row",
    "_provenance.py": "merge provenance Recorder (imported by _merge); never runs on an "
                      "index build, which the trace test above proves",
}


def _top_level_deps(name: str) -> set[str]:
    out: set[str] = set()
    for n in ast.parse((PKG / name).read_text(encoding="utf-8")).body:
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if (n.level == 1 and not mod) or mod == "x4validate":
                out |= {a.name + ".py" for a in n.names}
            elif mod.startswith("x4validate.") or (n.level == 1 and mod):
                out.add(mod.split(".")[-1] + ".py")
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[-1] + ".py" for a in n.names
                    if a.name.startswith("x4validate.")}
    return {d for d in out if (PKG / d).is_file()}


def test_every_module_the_index_build_imports_is_hashed_or_excluded_with_a_reason():
    """Review M-2: the trace sees only what the fixture exercises. Every x4validate
    module `_xrefindex` imports at module level, transitively, is either hashed or named
    in `_IMPORTED_NOT_HASHED` with its reason. (Function-level imports load only when
    called, which is what the trace covers.)"""
    seen: set[str] = set()
    todo = ["_xrefindex.py"]
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo.extend(sorted(_top_level_deps(m) - seen))
    assert "_merge.py" in seen and "_cat.py" in seen, seen      # the walk works
    unaccounted = seen - set(_freshness.XREF_SOURCES) - set(_IMPORTED_NOT_HASHED)
    assert not unaccounted, f"imported by the index build but neither hashed nor excluded: {sorted(unaccounted)}"
    stale = set(_IMPORTED_NOT_HASHED) - seen
    assert not stale, f"excluded but no longer imported: {sorted(stale)}"
    assert not set(_IMPORTED_NOT_HASHED) & set(_freshness.XREF_SOURCES)

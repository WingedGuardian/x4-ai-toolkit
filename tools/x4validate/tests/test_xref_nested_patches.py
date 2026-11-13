"""x4xref must index a mod's EXTENSION-NESTED script patches.

A mod patches another mod's MD or aiscript at ``extensions/<owner>/md/...`` (CLAUDE.md
gotcha #6). The index enumerated scripts with ``_scan.under("md/", "aiscripts/")``, which
matches only path STARTS, so every nested patch was never read -- and a cue defined only
there was then certified "does not appear under ANY kind -- a real negative".

MEASURED by the reporter on a real install: 20 of 24 named overlay script documents were
omitted, 0 unreadable (so nothing in the exclusions channel said a word).

The base walk must stay direct-only: the reference root's own ``extensions/`` subtree IS the
DLC, which is indexed separately under ``dlc:<name>``, so widening the base walk would count
every DLC script twice. The ``base_and_dlc_once`` tests pin that.

All fixtures are synthetic; no real mod names.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from x4validate import _freshness, _merge, _registry, _scan, _xref


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _write_cat(mod_dir: Path, cat_name: str, members: list[tuple[str, bytes]]) -> None:
    mod_dir.mkdir(parents=True, exist_ok=True)
    lines, blob = [], bytearray()
    for vpath, data in members:
        lines.append(f"{vpath} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    (mod_dir / cat_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (mod_dir / cat_name).with_suffix(".dat").write_bytes(bytes(blob))


def _md(script: str, cue: str) -> str:
    return f'<mdscript name="{script}"><cues><cue name="{cue}"><actions/></cue></cues></mdscript>'


def _mod(ext: Path, folder: str, files: dict[str, str]) -> Path:
    root = ext / folder
    _w(root / "content.xml", f'<content id="{folder}" name="{folder}" version="1"/>')
    for vpath, text in files.items():
        _w(root / vpath, text)
    return root


def _ref(tmp_path: Path) -> Path:
    ref = tmp_path / "reference"
    _w(ref / "md" / "base.xml", _md("BaseScript", "BaseCue"))
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    return ref


@pytest.fixture
def hermetic(monkeypatch):
    monkeypatch.setattr(_registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", None)
    monkeypatch.setattr(_registry, "default_installed_dirs", lambda: [])


def _index(tmp_path: Path, ext: Path, unreadable=None, dlc_dirs=None):
    return _xref.build_index(_ref(tmp_path), ext, unreadable,
                             dlc_dirs=[] if dlc_dirs is None else dlc_dirs)


def _cuedefs(rows):
    return {(r.source, r.file, r.name) for r in rows if r.kind == "cuedef"}


# --- the predicate ------------------------------------------------------------

@pytest.mark.parametrize("vpath", [
    "md/a.xml",
    "aiscripts/a.xml",
    "extensions/owner/md/a.xml",
    "extensions/owner/aiscripts/a.xml",
    "extensions/one/extensions/two/md/a.xml",      # the prefix REPEATS
    "Extensions/Owner/MD/a.xml",                   # case-insensitive, as the engine is
])
def test_nested_predicate_accepts_script_paths(vpath):
    assert _scan.under_nested("md/", "aiscripts/")(vpath)


@pytest.mark.parametrize("vpath", [
    "libraries/wares.xml",
    "extensions/owner/libraries/wares.xml",        # nested, but not a script dir
    "extensions/md/a.xml",                         # `md` is the OWNER here, no script dir
    "extensions/owner/sub/md/a.xml",               # an intermediate that is not extensions/
    "other/owner/md/a.xml",                        # a non-extensions wrapper
    "extensions/owner/mdx/a.xml",                  # prefix of a dir name is not the dir
    "extensions//md/a.xml",                        # an empty owner segment
])
def test_nested_predicate_rejects_non_script_paths(vpath):
    assert not _scan.under_nested("md/", "aiscripts/")(vpath)


def test_nested_predicate_still_narrows_the_walk(tmp_path):
    """It must keep `.prefixes`, so `iter_mod_xml` rglobs only md/, aiscripts/ and
    extensions/ instead of the whole mod (the x4xref-over-100-mods cost)."""
    pred = _scan.under_nested("md/", "aiscripts/")
    heads = {p.strip("/").split("/")[0] for p in pred.prefixes}
    assert heads == {"md", "aiscripts", "extensions"}


# --- mod enumeration ----------------------------------------------------------

def test_loose_direct_script_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"md/own.xml": _md("Own", "DirectCue")})
    assert ("moda", "md/own.xml", "DirectCue") in _cuedefs(_index(tmp_path, ext))


def test_packed_direct_script_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    mod = _mod(ext, "moda", {})
    _write_cat(mod, "ext_01.cat", [("aiscripts/own.xml",
               b'<aiscript name="own"><attention min="unknown"><actions>'
               b'<signal_cue cue="X"/></actions></attention></aiscript>')])
    rows = _index(tmp_path, ext)
    assert any(r.source == "moda" and r.file == "aiscripts/own.xml" and r.kind == "signal"
               for r in rows)


def test_one_level_nested_loose_patch_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"extensions/ownerb/md/patch.xml":
                       '<diff><add sel="//cues"><cue name="NestedCue"/></add></diff>'})
    assert ("moda", "extensions/ownerb/md/patch.xml", "NestedCue") in _cuedefs(
        _index(tmp_path, ext))


def test_two_level_nested_loose_patch_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"extensions/ownerb/extensions/ownerc/aiscripts/p.xml":
                       '<diff><add sel="//actions"><cue name="DeepCue"/></add></diff>'})
    assert ("moda", "extensions/ownerb/extensions/ownerc/aiscripts/p.xml", "DeepCue") in \
        _cuedefs(_index(tmp_path, ext))


def test_nested_packed_patch_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    mod = _mod(ext, "moda", {})
    _write_cat(mod, "ext_01.cat", [("extensions/ownerb/md/packed.xml",
               b'<diff><add sel="//cues"><cue name="PackedNestedCue"/></add></diff>')])
    assert ("moda", "extensions/ownerb/md/packed.xml", "PackedNestedCue") in _cuedefs(
        _index(tmp_path, ext))


def test_nested_non_script_files_are_not_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {
        "extensions/ownerb/libraries/wares.xml": '<wares><cue name="NotAScriptCue"/></wares>',
        "extensions/ownerb/sub/md/x.xml": _md("S", "WrongWrapperCue"),
        "extensions/ownerb/content.xml": '<content id="x"/>',
    })
    rows = _index(tmp_path, ext)
    assert not [r for r in rows if r.source == "moda"], rows


def test_malformed_nested_script_is_counted_unreadable(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"extensions/ownerb/md/broken.xml": "<diff><add sel='//cues'>"})
    unreadable: list = []
    _index(tmp_path, ext, unreadable)
    assert [u.vpath for u in unreadable] == ["extensions/ownerb/md/broken.xml"]


def test_malformed_nested_packed_script_is_counted_unreadable(tmp_path, hermetic):
    ext = tmp_path / "ext"
    mod = _mod(ext, "moda", {})
    _write_cat(mod, "ext_01.cat", [("extensions/ownerb/aiscripts/bad.xml", b"<diff><")])
    unreadable: list = []
    _index(tmp_path, ext, unreadable)
    assert [(u.vpath, u.packed) for u in unreadable] == [
        ("extensions/ownerb/aiscripts/bad.xml", True)]


# --- base and DLC: each script counted ONCE ----------------------------------

def test_base_and_dlc_are_each_counted_once(tmp_path, hermetic):
    """The reference root's `extensions/` IS the DLC. Base must not descend into it."""
    ref = _ref(tmp_path)
    dlc = ref / "extensions" / "ego_dlc_test"
    _w(dlc / "md" / "dlc.xml", _md("DlcScript", "DlcCue"))
    rows = _xref.build_index(ref, tmp_path / "noext", dlc_dirs=[dlc])
    defs = Counter((r.source, r.name) for r in rows if r.kind == "cuedef")
    assert defs == Counter({("base", "BaseCue"): 1, ("dlc:ego_dlc_test", "DlcCue"): 1})
    assert not [r for r in rows if r.source == "base" and r.file.startswith("extensions/")]


def test_a_dlc_nested_patch_is_indexed_under_the_dlc_once(tmp_path, hermetic):
    """A DLC patching another DLC sits at <dlc>/extensions/<other>/md: indexed under the
    patching DLC, and NOT a second time under base."""
    ref = _ref(tmp_path)
    dlc = ref / "extensions" / "ego_dlc_test"
    _w(dlc / "extensions" / "ego_dlc_other" / "md" / "p.xml",
       '<diff><add sel="//cues"><cue name="DlcPatchCue"/></add></diff>')
    rows = _xref.build_index(ref, tmp_path / "noext", dlc_dirs=[dlc])
    hits = [(r.source, r.file) for r in rows if r.name == "DlcPatchCue"]
    assert hits == [("dlc:ego_dlc_test", "extensions/ego_dlc_other/md/p.xml")]


# --- a positive witness per kind, through the CLI ----------------------------

_WITNESS = """<diff><add sel="//cues">
  <cue name="WitnessCue">
    <conditions><event_object_destroyed object="player.entity"/></conditions>
    <actions>
      <witness_unique_action/>
      <signal_cue cue="md.Other.WitnessTarget"/>
    </actions>
  </cue>
</add></diff>"""


@pytest.fixture
def nested_index(tmp_path, hermetic, monkeypatch):
    ref = _ref(tmp_path)
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"extensions/ownerb/md/witness.xml": _WITNESS})
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    tsv = tmp_path / "xref.tsv"
    assert _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                       "--out", str(tsv)]) == 0
    return tsv


@pytest.mark.parametrize("argv,expect", [
    (["cue", "WitnessCue"], "defined (1)"),                       # cuedef
    (["cue", "WitnessTarget"], "signal_cue (1)"),                 # signal
    (["who-listens", "event_object_destroyed"], "1 occurrence"),  # event
    (["who-calls", "witness_unique_action"], "1 occurrence"),     # action
])
def test_every_kind_is_found_in_a_nested_patch(nested_index, capsys, argv, expect):
    rc = _xref.main([*argv, "--tsv", str(nested_index)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert expect in out
    # the OWNER of the patched file is visible in the row's path
    assert "moda:extensions/ownerb/md/witness.xml" in out


# --- an index built BEFORE this coverage existed cannot certify a negative -----

def test_fresh_index_records_its_script_scope(nested_index):
    assert _freshness.read_sidecar(nested_index)["script_scope"] == _xref.SCRIPT_SCOPE


def test_an_index_without_the_nested_scope_cannot_certify_a_negative(nested_index, capsys):
    """An index from the narrower enumeration reads FRESH on the content axis (no mod
    changed) and `_xref.py` is not an ENGINE_SOURCE, so nothing else would mark it."""
    side = _freshness._sidecar(nested_index)
    import json
    data = json.loads(side.read_text(encoding="utf-8"))
    del data["script_scope"]
    side.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    rc = _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(nested_index)])
    cap = capsys.readouterr()
    assert rc == 2
    assert "STALE" in cap.err and "script scope" in cap.err
    assert "a real negative" not in cap.out


def test_twin_an_index_with_the_scope_still_certifies(nested_index, capsys):
    capsys.readouterr()
    rc = _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(nested_index)])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert "STALE" not in cap.err
    assert "a real negative" in cap.out
    # the negative states WHICH scripts it covers
    assert "extensions/<owner>/" in cap.out


def test_an_old_scope_index_does_not_claim_nested_coverage(nested_index, capsys):
    """The negative's scope words describe the INDEX, not the tool reading it: an index
    built by the narrower enumeration must not be described as covering nested patches."""
    import json
    side = _freshness._sidecar(nested_index)
    data = json.loads(side.read_text(encoding="utf-8"))
    del data["script_scope"]
    side.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(nested_index)])
    out = capsys.readouterr().out
    assert "extensions/<owner>/" not in out
    assert "script scope unrecorded or different" in out


# --- the narrowed walk finds a differently-cased loose folder on EVERY OS ------

def test_scan_roots_return_the_on_disk_spelling(tmp_path):
    """`_scan_roots` built `mod_dir / "extensions"` literally. Windows answers that
    case-insensitively; a POSIX filesystem does not, so a loose `Extensions/` or `MD/`
    folder was never walked there although the predicate is case-insensitive. The
    roots must be the folders ON DISK, which this checks on any OS: a literal join
    keeps the lowercase spelling even where it resolves."""
    mod = tmp_path / "moda"
    for d in ("Extensions/Owner/MD", "MD", "AIScripts", "libraries"):
        (mod / d).mkdir(parents=True)
    roots = _scan._scan_roots(mod, _scan.under_nested("md/", "aiscripts/"))
    assert sorted(r.name for r in roots) == ["AIScripts", "Extensions", "MD"]


def test_twin_scan_roots_lowercase_and_absent(tmp_path):
    mod = tmp_path / "moda"
    (mod / "md").mkdir(parents=True)
    (mod / "libraries").mkdir()
    roots = _scan._scan_roots(mod, _scan.under("md/", "aiscripts/"))
    assert [r.name for r in roots] == ["md"]
    assert _scan._scan_roots(tmp_path / "missing", _scan.under("md/")) == []


def test_mixed_case_nested_patch_is_indexed(tmp_path, hermetic):
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"Extensions/OwnerB/MD/patch.xml":
                       '<diff><add sel="//cues"><cue name="CasedCue"/></add></diff>'})
    assert ("moda", "Extensions/OwnerB/MD/patch.xml", "CasedCue") in _cuedefs(
        _index(tmp_path, ext))

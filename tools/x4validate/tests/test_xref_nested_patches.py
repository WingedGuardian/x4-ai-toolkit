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


# --- a folder that cannot be LISTED is a non-answer, not an empty mod (review M-4, delta 5 M-3) --
#
# Reported as an unscanned FOLDER naming the mod, never as a file that would not parse.
# Only the LOOSE listing failed, so the catalogs are still read. A subfolder that cannot be
# listed is reported the same way instead of reading as empty.

def _deny_top_listing(monkeypatch, mod):
    real = _scan._list_dir

    def deny(path):
        if path == mod:
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr(_scan, "_list_dir", deny)


def _packed_mod(tmp_path):
    mod = tmp_path / "moda"
    (mod / "md").mkdir(parents=True)
    (mod / "md" / "x.xml").write_text(_md("X", "LooseCue"), encoding="utf-8")
    _write_cat(mod, "ext_01.cat", [("md/k.xml", _md("K", "PackedCue").encode())])
    return mod


def test_an_unlistable_mod_folder_is_an_unscanned_folder_naming_the_mod(tmp_path, monkeypatch):
    mod = _packed_mod(tmp_path)
    _deny_top_listing(monkeypatch, mod)
    unreadable: list = []
    got = [v for v, _ in _scan.iter_mod_xml(mod, _scan.under_nested("md/", "aiscripts/"),
                                            unreadable)]
    assert got == ["md/k.xml"]                       # the catalogs are still read
    assert len(unreadable) == 1
    u = unreadable[0]
    assert u.folder and "moda" in str(u) and "NOT SCANNED" in str(u)
    assert "(loose)" not in str(u)


def test_an_unlistable_mod_folder_without_a_channel_still_reads_its_catalogs(tmp_path, monkeypatch):
    mod = _packed_mod(tmp_path)
    _deny_top_listing(monkeypatch, mod)
    assert [v for v, _ in _scan.iter_mod_xml(mod, _scan.under("md/", "aiscripts/"))] == ["md/k.xml"]


def test_an_unlistable_subfolder_is_reported_not_read_as_empty(tmp_path, monkeypatch):
    mod = tmp_path / "moda"
    (mod / "md" / "sub").mkdir(parents=True)
    (mod / "md" / "sub" / "x.xml").write_text(_md("X", "C"), encoding="utf-8")
    (mod / "md" / "ok.xml").write_text(_md("O", "D"), encoding="utf-8")
    import os
    real = os.scandir
    blocked = str(mod / "md" / "sub")

    def deny(path="."):
        if os.fspath(path) == blocked:
            raise PermissionError(13, "Access is denied", blocked)
        return real(path)

    monkeypatch.setattr(os, "scandir", deny)
    unreadable: list = []
    got = [v for v, _ in _scan.iter_mod_xml(mod, _scan.under("md/"), unreadable)]
    assert got == ["md/ok.xml"]
    assert [(u.vpath, u.folder) for u in unreadable] == [("md/sub/", True)]


def test_twin_an_absent_mod_folder_records_nothing(tmp_path):
    unreadable: list = []
    assert list(_scan.iter_mod_xml(tmp_path / "missing", _scan.under("md/"), unreadable)) == []
    assert unreadable == []


def test_an_unlistable_mod_reaches_the_xref_negative_as_a_folder(tmp_path, hermetic, monkeypatch, capsys):
    """...and the negative says a FOLDER was not scanned, not that a file would not parse."""
    ref = _ref(tmp_path)
    ext = tmp_path / "ext"
    mod = _mod(ext, "moda", {"md/x.xml": _md("X", "C")})
    _mod(ext, "modb", {"md/y.xml": _md("Y", "D")})
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    _deny_top_listing(monkeypatch, mod)
    tsv = tmp_path / "xref.tsv"
    assert _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                       "--out", str(tsv)]) == 0
    capsys.readouterr()
    _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(tsv)])
    out = capsys.readouterr().out
    assert "1 folder(s) that could not be listed" in out
    assert "would not parse" not in out


def test_twin_a_parse_failure_is_still_a_file(tmp_path, hermetic, monkeypatch, capsys):
    ref = _ref(tmp_path)
    ext = tmp_path / "ext"
    _mod(ext, "moda", {"md/x.xml": "<mdscript"})
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_merge, "REFERENCE", ref)
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    tsv = tmp_path / "xref.tsv"
    assert _xref.main(["build", "--reference", str(ref), "--ext-dir", str(ext),
                       "--out", str(tsv)]) == 0
    capsys.readouterr()
    _xref.main(["cue", "NoSuchCueAnywhere", "--tsv", str(tsv)])
    out = capsys.readouterr().out
    assert "1 file(s) that would not parse" in out and "folder" not in out


# --- delta review 6: absence at the walk root is silent; the mod folder is named ----

def test_a_missing_mod_folder_with_no_predicate_records_nothing(tmp_path):
    """No prefix predicate walks the mod folder itself. A folder that does not exist is
    an absence (as on the prefix path), not 'folder ./ NOT SCANNED'."""
    for missing in (tmp_path / "missing", tmp_path / "afile.xml"):
        if missing.suffix:
            missing.write_text("<x/>", encoding="utf-8")   # NotADirectoryError twin
        unreadable: list = []
        assert list(_scan.iter_mod_xml(missing, None, unreadable)) == []
        assert list(_scan.iter_mod_xml_bytes(missing, None, unreadable)) == []
        assert unreadable == [], [str(u) for u in unreadable]


def test_an_unlistable_mod_folder_with_no_predicate_is_named_as_the_mod_folder(tmp_path, monkeypatch):
    import os
    mod = tmp_path / "moda"
    (mod / "md").mkdir(parents=True)
    real = os.scandir

    def deny(path="."):
        if os.fspath(path) == os.fspath(mod):
            raise PermissionError(13, "Access is denied", os.fspath(mod))
        return real(path)

    monkeypatch.setattr(os, "scandir", deny)
    unreadable: list = []
    # The loose WALK of the mod folder (no prefix predicate). The catalog read that
    # follows in iter_mod_xml lists the same folder and raises, as documented.
    list(_scan._walk_xml(mod, mod, unreadable))
    assert [str(u).split(" (")[0] for u in unreadable] == ["mod folder 'moda'"]


def test_check_readability_words_a_folder_as_a_folder(tmp_path, monkeypatch):
    from x4validate import _check
    mod = tmp_path / "moda"
    (mod / "md" / "sub").mkdir(parents=True)
    (mod / "content.xml").write_text('<content id="moda"/>', encoding="utf-8")
    import os
    real = os.scandir
    blocked = os.fspath(mod / "md" / "sub")

    def deny(path="."):
        if os.fspath(path) == blocked:
            raise PermissionError(13, "Access is denied", blocked)
        return real(path)

    monkeypatch.setattr(os, "scandir", deny)
    report = _check.Report()
    _check.check_readability(mod, _merge.Config(reference=tmp_path / "ref"), report)
    texts = [f.message for f in report.findings] + [s.why for s in report.skipped]
    assert any("could not be listed" in t and "not checked" in t for t in texts), texts
    assert not any("will not parse" in t for t in texts), texts

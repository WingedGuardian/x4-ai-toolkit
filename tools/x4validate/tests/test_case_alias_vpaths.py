"""Issue #6: one logical file, two spellings.

X4 reads virtual paths case-insensitively (MEASURED: debug.txt I/O paths are lowercase), so
`md/CaseProbe.xml` in one mod and `md/caseprobe.xml` in another are ONE file to the engine.

* `_modfiles.overlay_vpaths` kept mod-only paths in a set of ORIGINAL spellings, so the
  x4eff build merged and wrote the same document twice -- on Windows the second write
  overwrote the first and the counted total exceeded the files on disk by one, which
  coverage (correctly) refused. Fixed: one contested vpath per case-folded path.
* `_merge.overlay_root` looked a loose file up with `Path.is_file()`, case-INsensitive on
  Windows and case-SENSITIVE on Linux, so there a differently-spelled loose overlay would be
  silently dropped (INFERRED from the lookup; the Linux-only tests below measure it where CI
  runs on Linux). Fixed: on a case-sensitive filesystem the loose lookup falls back to a
  case-folded match, and REFUSES a folder holding two spellings of one file -- which one the
  engine reads is not measured, so neither is picked.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from x4validate import _effective, _merge, _modfiles


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fs_is_case_sensitive(tmp: Path) -> bool:
    probe = tmp / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return not (tmp / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


@pytest.fixture
def two_spellings(monkeypatch, tmp_path):
    """Base has no such file; mods A and B each ship it, spelled differently."""
    base = tmp_path / "base"
    _w(base / "libraries" / "wares.xml", "<wares/>")
    a, b = tmp_path / "ext" / "a_mod", tmp_path / "ext" / "b_mod"
    _w(a / "libraries" / "CaseProbe.xml", "<root><from_a/></root>")
    # B ships the file whole too: a plain-path diff would be INERT (the file is a_mod's,
    # not base's -- gotcha #6), so it would not reach the merge chain either way.
    _w(b / "libraries" / "caseprobe.xml", "<root><from_b/></root>")
    monkeypatch.setattr(_effective, "base_vpaths",
                        lambda config, pattern: {"libraries/wares.xml": "libraries/wares.xml"})
    monkeypatch.setattr(_modfiles._cat, "mod_vfs", lambda d, packed_only=True: {})
    cfg = _merge.Config(reference=base, overlays=(a, b), include_packed_dlc=False)
    return cfg, a, b


def test_6_two_spellings_are_ONE_contested_vpath(two_spellings):
    cfg, a, b = two_spellings
    contested, _untouched, _base = _modfiles.overlay_vpaths(cfg, [a, b], [])
    folded = [v for v in contested if v.lower() == "libraries/caseprobe.xml"]
    assert folded == ["libraries/CaseProbe.xml"], contested   # first in load order


def test_6_TWIN_the_spelling_follows_LOAD_ORDER_not_the_alphabet(two_spellings):
    cfg, a, b = two_spellings
    contested, _u, _b = _modfiles.overlay_vpaths(cfg, [b, a], [])
    assert [v for v in contested if v.lower() == "libraries/caseprobe.xml"] == \
        ["libraries/caseprobe.xml"]


def test_6_TWIN_a_BASE_spelling_still_wins(monkeypatch, tmp_path):
    a = tmp_path / "a_mod"
    _w(a / "LIBRARIES" / "WARES.xml", "<diff/>")
    monkeypatch.setattr(_effective, "base_vpaths",
                        lambda config, pattern: {"libraries/wares.xml": "libraries/wares.xml"})
    monkeypatch.setattr(_modfiles._cat, "mod_vfs", lambda d, packed_only=True: {})
    contested, untouched, _ = _modfiles.overlay_vpaths(None, [a], [])
    assert contested == {"libraries/wares.xml"} and untouched == set()


def test_6_TWIN_loose_and_PACKED_spellings_in_one_overlay_are_one_vpath(monkeypatch, tmp_path):
    a = tmp_path / "a_mod"
    _w(a / "md" / "Probe.xml", "<diff/>")
    monkeypatch.setattr(_effective, "base_vpaths", lambda config, pattern: {})
    monkeypatch.setattr(_modfiles._cat, "mod_vfs", lambda d, packed_only=True: {"md/probe.xml": None})
    contested, _u, _b = _modfiles.overlay_vpaths(None, [a], [])
    assert len([v for v in contested if v.lower() == "md/probe.xml"]) == 1, contested


def test_6_the_one_document_has_BOTH_mods_as_sources(two_spellings):
    cfg, a, b = two_spellings
    contested, _u, _b = _modfiles.overlay_vpaths(cfg, [a, b], [])
    (v,) = [v for v in contested if v.lower() == "libraries/caseprobe.xml"]
    res = _merge.build_effective(v, cfg)
    names = [s.split(":")[0] for s in res.sources]
    assert "a_mod" in names and "b_mod" in names, res.sources
    assert res.tree.find("from_b") is not None   # the later-loading spelling's content


# --- overlay_root on a case-SENSITIVE filesystem -------------------------------------

def test_6_overlay_root_folds_case_on_a_case_sensitive_fs(tmp_path):
    if not _fs_is_case_sensitive(tmp_path):
        pytest.skip("filesystem is case-insensitive here; the fallback is a Linux-path test")
    odir = tmp_path / "mod"
    _w(odir / "md" / "CaseProbe.xml", "<mdscript name='x'/>")
    root = _merge.overlay_root(odir, "md/caseprobe.xml", [])
    assert root is not None and root.tag == "mdscript"


def test_6_overlay_root_REFUSES_two_spellings_in_one_folder(tmp_path):
    if not _fs_is_case_sensitive(tmp_path):
        pytest.skip("two spellings cannot coexist on a case-insensitive filesystem")
    odir = tmp_path / "mod"
    _w(odir / "md" / "CaseProbe.xml", "<a/>")
    _w(odir / "md" / "caseprobe.xml", "<b/>")
    for asked in ("md/caseprobe.xml", "md/CaseProbe.xml", "md/CASEPROBE.xml"):
        skipped: list[str] = []
        assert _merge.overlay_root(odir, asked, skipped) is None
        assert len(skipped) == 1 and "differ only by case" in skipped[0], skipped
    with pytest.raises(ValueError):
        _merge.overlay_root(odir, "md/caseprobe.xml")


# The same two branches, reached on ANY OS by standing in for the filesystem's answer.

def test_6_fallback_logic_with_a_simulated_case_sensitive_fs(monkeypatch, tmp_path):
    odir = tmp_path / "mod"
    _w(odir / "md" / "CaseProbe.xml", "<mdscript/>")
    monkeypatch.setattr(_merge, "_loose_case_sensitive", lambda: True)
    _merge._folded_loose.cache_clear()
    real_is_file = Path.is_file
    # A case-sensitive is_file: true only for the exact on-disk spelling.
    monkeypatch.setattr(Path, "is_file", lambda self: real_is_file(self) and self.name in
                        os.listdir(self.parent))
    try:
        assert _merge.overlay_root(odir, "md/caseprobe.xml", []) is not None
        assert _merge.overlay_root(odir, "md/absent.xml", []) is None
    finally:
        _merge._folded_loose.cache_clear()


def test_6_ambiguity_logic_with_a_simulated_case_sensitive_fs(monkeypatch, tmp_path):
    odir = tmp_path / "mod"
    _w(odir / "md" / "CaseProbe.xml", "<mdscript/>")
    monkeypatch.setattr(_merge, "_loose_case_sensitive", lambda: True)
    monkeypatch.setattr(_merge, "_folded_loose", lambda s: {
        "md/caseprobe.xml": ("md/CaseProbe.xml", "md/caseprobe.xml")})
    skipped: list[str] = []
    assert _merge.overlay_root(odir, "md/CaseProbe.xml", skipped) is None
    assert len(skipped) == 1 and "differ only by case" in skipped[0]


def test_6_TWIN_windows_never_consults_the_folded_index(monkeypatch, tmp_path):
    """Windows behaviour is unchanged: the case-insensitive filesystem already answers,
    so the folded index is never built there."""
    odir = tmp_path / "mod"
    _w(odir / "md" / "CaseProbe.xml", "<mdscript/>")
    monkeypatch.setattr(_merge, "_loose_case_sensitive", lambda: False)

    def boom(s):
        raise AssertionError("folded index consulted on a case-insensitive filesystem")
    monkeypatch.setattr(_merge, "_folded_loose", boom)
    assert _merge.overlay_root(odir, "md/absent.xml", []) is None


def test_6_the_gate_is_the_OS():
    assert _merge._loose_case_sensitive() is (os.name != "nt")


# --- M-b (v4.0.0 delta review): x4compat routes the case refusal into its channel ------
#
# `overlay_root` RAISES the two-spellings refusal when no `skipped` list is passed (NO
# CHANNEL, NO SWALLOW). x4compat passes none at two sites and caught only LxmlError, so on
# Linux the refusal escaped as a traceback. Both sites have a channel; route it there.

def _compat_mods(tmp_path, b_body="<diff><replace sel=\"/r/a/b/@v\">3</replace></diff>"):
    ref = tmp_path / "ref"
    _w(ref / "libraries" / "x.xml", "<r><a><b v='1'/></a></r>")
    ext = tmp_path / "ext"
    for folder, body in (("a_first", "<diff><replace sel=\"/r/a/b/@v\">2</replace></diff>"),
                         ("b_later", b_body)):
        _w(ext / folder / "content.xml", f'<content id="{folder}" version="1" name="{folder}"/>')
        _w(ext / folder / "libraries" / "x.xml", body)
    paths = {"a_first": ext / "a_first", "b_later": ext / "b_later"}
    return _merge.Config(reference=ref, include_packed_dlc=False), paths


def _two_spellings_in(monkeypatch, folder_name):
    """Simulate a case-sensitive filesystem where `folder_name` holds X.xml AND x.xml."""
    monkeypatch.setattr(_merge, "_loose_case_sensitive", lambda: True)
    monkeypatch.setattr(_merge, "_folded_loose", lambda s: (
        {"libraries/x.xml": ("libraries/X.xml", "libraries/x.xml")}
        if Path(s).name == folder_name else {}))


def test_Mb_compat_routes_a_case_ambiguous_overlay_to_report_skip(monkeypatch, tmp_path):
    from x4validate import _compat
    cfg, paths = _compat_mods(tmp_path)
    _two_spellings_in(monkeypatch, "b_later")
    with pytest.raises(ValueError):                       # control: the raise is real
        _merge.overlay_root(paths["b_later"], "libraries/x.xml")
    report = _compat.CompatReport()
    _compat._analyze_vpath("libraries/x.xml", ["a_first", "b_later"], paths,
                           {"a_first": 0, "b_later": 1}, cfg, report=report)
    hits = [s for s in report.skipped if "b_later" in s.what]
    assert len(hits) == 1 and "differ only by case" in hits[0].why, report.skipped


def test_Mb_TWIN_an_unreadable_overlay_OSError_is_routed_too(monkeypatch, tmp_path):
    """Clause twin: the handler takes OSError as well (a mod folder moved between is_file and
    the read, a truncated .dat member) -- the same refusal family, the same escape."""
    from x4validate import _compat
    cfg, paths = _compat_mods(tmp_path)
    real = _merge.parse_file

    def flaky(p, *a, **k):
        if "b_later" in str(p):
            raise PermissionError(13, "simulated", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(_merge, "parse_file", flaky)
    report = _compat.CompatReport()
    _compat._analyze_vpath("libraries/x.xml", ["a_first", "b_later"], paths,
                           {"a_first": 0, "b_later": 1}, cfg, report=report)
    hits = [s for s in report.skipped if "b_later" in s.what]
    assert len(hits) == 1 and "could not be read" in hits[0].why, report.skipped


def test_Mb_TWIN_no_ambiguity_means_no_skip(monkeypatch, tmp_path):
    from x4validate import _compat
    cfg, paths = _compat_mods(tmp_path)
    _two_spellings_in(monkeypatch, "nobody")
    report = _compat.CompatReport()
    _compat._analyze_vpath("libraries/x.xml", ["a_first", "b_later"], paths,
                           {"a_first": 0, "b_later": 1}, cfg, report=report)
    assert report.skipped == [], report.skipped


def test_Mb_the_no_base_REASON_names_a_case_ambiguous_owner_file(monkeypatch, tmp_path):
    """The second site: `_no_base_reason` explains a nested path whose owner ships the base.
    A case-ambiguous owner file must give a reason, not escape through it."""
    from x4validate import _compat
    cfg, paths = _compat_mods(tmp_path)
    _two_spellings_in(monkeypatch, "a_first")
    why = _compat._no_base_reason("extensions/a_first/libraries/x.xml", paths, cfg)
    assert "differ only by case" in why, why

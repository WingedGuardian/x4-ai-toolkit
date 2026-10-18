r"""The load-order oracle gate, and the engine rule it holds `compute_load_order` to.

Two halves:

* The GATE's own logic (`parse_sequences`, `score`, the refusal paths) -- pinned so the
  instrument can go red and can refuse. A gate that could only print PASS would be
  decoration (CLAUDE.md #26).
* LO-1 (AUDIT-2026-09-24): the ENGINE's order, MEASURED on a real 125-mod install --
  372 file classes / 5,134 ordered pairs across two launches two weeks apart, 0
  inverted by this rule, 685 inverted by the pre-fix code:
    - folders walked in case-insensitive UPPERCASE order (`_` sorts AFTER letters,
      a space before `_`);
    - repeated passes; in each pass every mod whose INSTALLED dependencies are already
      loaded -- including ones loaded earlier in the SAME pass -- loads.
  Those cases were xfail(strict) until the fix landed; they are now plain regression
  tests of the fixed rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import load_order_oracle as g  # noqa: E402

from x4validate import _loadorder  # noqa: E402

BS = "\\"


def _line(folder: str, rel: str) -> str:
    return ("[FileIO ] 0.00 File I/O: Failed to verify the file signature for file "
            f"'.{BS}extensions{BS}{folder}{BS}{rel.replace('/', BS)}' (error: 14)\n")


# --------------------------------------------------------------------------- parse
def test_parse_keeps_first_check_order_per_class():
    lines = [_line("b_mod", "libraries/wares.xml"), _line("a_mod", "libraries/wares.xml"),
             _line("b_mod", "libraries/wares.xml"),        # a repeat check is not a new position
             _line("a_mod", "t/0001.xml"), "unrelated line\n"]
    seqs = g.parse_sequences(lines)
    assert seqs == {"libraries/wares.xml": ["b_mod", "a_mod"], "t/0001.xml": ["a_mod"]}


def test_parse_lowercases_and_keeps_nested_remainder_separate():
    lines = [_line("Ship AI Core", "libraries/wares.xml"),
             _line("patcher", "extensions/vro/libraries/wares.xml")]
    seqs = g.parse_sequences(lines)
    assert seqs["libraries/wares.xml"] == ["ship ai core"]
    assert seqs["extensions/vro/libraries/wares.xml"] == ["patcher"]


# --------------------------------------------------------------------------- score
def test_score_agreeing_order_has_no_inversions():
    s = g.score(["a", "b", "c"], {"x.xml": ["a", "b", "c"]})
    assert (s.classes, s.pairs, s.inversions) == (1, 3, 0)


def test_score_counts_every_inverted_pair_exactly():
    s = g.score(["c", "b", "a"], {"x.xml": ["a", "b", "c"]})
    assert (s.pairs, s.inversions) == (3, 3)
    assert s.bad[0][0] == "x.xml"


def test_score_ignores_single_mod_classes_and_tracks_unknown_folders():
    s = g.score(["a", "b"], {"one.xml": ["a"], "two.xml": ["a", "ghost", "b"]})
    assert s.classes == 1 and s.pairs == 1          # 'ghost' is not compared...
    assert s.unknown_folders == {"ghost"}           # ...but it is counted, never dropped


def test_score_with_nothing_comparable_has_zero_classes():
    """main() turns this into rc 2 -- an empty comparison is never a PASS."""
    assert g.score(["a"], {"x.xml": ["a"]}).classes == 0


def test_main_refuses_without_a_log(monkeypatch):
    monkeypatch.setattr(g, "_log_path", lambda: None)
    assert g.main() == 2


# --------------------------------------------------------------------------- LO-1
def _mods(tmp_path: Path, spec: dict[str, list[str]]) -> list[dict]:
    """spec: {folder: [dependency ids]}; each mod's id is its folder name lowercased."""
    out = []
    for folder, deps in spec.items():
        d = tmp_path / folder
        d.mkdir()
        dep_xml = "".join(f'<dependency id="{x}"/>' for x in deps)
        (d / "content.xml").write_text(
            f'<content id="{folder.lower()}" version="100">{dep_xml}</content>',
            encoding="utf-8")
        out.append({"folder": folder, "path": str(d)})
    return out


def _order(tmp_path, spec):
    return _loadorder.compute_load_order(_mods(tmp_path, spec))


def test_LO1_folder_names_compare_case_insensitively(tmp_path):
    assert _order(tmp_path, {"Zeta": [], "alpha": []}) == ["alpha", "Zeta"]


def test_LO1_underscore_sorts_after_letters(tmp_path):
    assert _order(tmp_path, {"s_combat": [], "station": []}) == ["station", "s_combat"]


def test_LO1_space_sorts_before_underscore(tmp_path):
    """Both rules agree here -- the control that the case above is not an artefact."""
    assert _order(tmp_path, {"ship_v": [], "ship ai": []}) == ["ship ai", "ship_v"]


def test_LO1_unmet_dependency_waits_for_the_next_pass(tmp_path):
    # pass 1: a skipped (c not loaded yet), b, c, d  -- pass 2: a
    assert _order(tmp_path, {"a": ["c"], "b": [], "c": [], "d": []}) == ["b", "c", "d", "a"]


def test_LO1_dependency_loaded_earlier_in_the_same_pass_counts(tmp_path):
    """Refutes the 'defer every dependent to the next pass' variant (33/72 on the log):
    x's dependency a loaded earlier in THIS pass, so x loads in this pass too."""
    assert _order(tmp_path, {"a": [], "b": [], "x": ["a"]}) == ["a", "b", "x"]


def test_LO1_uninstalled_dependency_does_not_hold_a_mod_back(tmp_path):
    """34 of 125 real mods declare dependencies that are not mods here (mostly DLC ids);
    the MEASURED order places them as if those were satisfied. Missing REQUIRED
    dependencies are an open question for the probe (LO-3), not asserted here."""
    assert _order(tmp_path, {"a": ["ego_dlc_boron"], "b": []}) == ["a", "b"]


# ------------------------------------------------ release review 2026-09-26: modlist match
# The docs said the gate refuses (rc 2) when the log describes a different modlist; the
# code only checked that AFTER finding inversions, so a mismatched log with 0 inversions
# PASSED. And a mod WE exclude that the engine LOGS -- our active rule disagreeing with
# the engine -- landed among the unknown folders (rc 2, or PASS), never as a finding.
import os  # noqa: E402


def _world(tmp_path, monkeypatch, *, active, excluded=(), log_lines, files=None,
           log_age=+1000, profile_age=None, dropped=(), dlc=(), newer=()):
    """active/excluded: installed game-root folders; only *active* are in the active set.
    files: {folder: [relative paths it ships]} (each mod always ships content.xml).
    log_age: log mtime relative to the manifests (+ newer, - older).
    profile_age: profile content.xml mtime relative to the LOG (None = no profile).
    dlc: the DLC folder names the configuration knows (pinned; never the real install).
    newer: folders whose manifest is then set NEWER than the log."""
    monkeypatch.setattr(g, "_dlc_folders", lambda: frozenset(dlc), raising=False)
    files = files or {}
    mods = {}
    for f in [*active, *excluded]:
        d = tmp_path / "ext" / f
        d.mkdir(parents=True)
        (d / "content.xml").write_text(f'<content id="{f}" version="1"/>', encoding="utf-8")
        for rel in files.get(f, []):
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("<x/>", encoding="utf-8")
        mods[f] = {"folder": f, "path": str(d), "root_rank": 0, "id": f}
    t_manifest = max(os.stat(m["path"] + "/content.xml").st_mtime for m in mods.values())
    log = tmp_path / "debug.txt"
    log.write_text("".join(log_lines), encoding="utf-8")
    t_log = t_manifest + log_age
    os.utime(log, (t_log, t_log))
    for f in newer:
        os.utime(mods[f]["path"] + "/content.xml", (t_log + 1000, t_log + 1000))
    prof = None
    if profile_age is not None:
        prof = tmp_path / "content.xml"
        prof.write_text("<content/>", encoding="utf-8")
        os.utime(prof, (t_log + profile_age, t_log + profile_age))

    def fake_mods(scope, *a, **k):
        from x4validate import _registry
        if scope == "active":
            return _registry.ModList([mods[f] for f in active], dropped)
        return _registry.ModList(list(mods.values()))
    monkeypatch.setattr(g, "_log_path", lambda: log)
    monkeypatch.setattr(g, "_profile_content", lambda: prof)
    monkeypatch.setattr(g._registry, "mods", fake_mods)


_AGREE = [_line("a", "x.xml"), _line("b", "x.xml")]


def _rc(capsys):
    got = capsys.readouterr()
    return got.out + got.err


def test_a_current_log_with_no_inversions_passes(tmp_path, monkeypatch, capsys):
    """The CONTROL every refusal below is a twin of."""
    _world(tmp_path, monkeypatch, active=["a", "b"], log_lines=_AGREE,
           files={"a": ["x.xml"], "b": ["x.xml"]})
    assert g.main() == 0, _rc(capsys)


def test_a_logged_folder_that_is_not_installed_refuses_even_with_0_inversions(
        tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, active=["a", "b"],
           log_lines=[*_AGREE, _line("removed_probe", "x.xml")])
    assert g.main() == 2
    assert "removed_probe" in _rc(capsys)


def test_a_manifest_newer_than_the_log_refuses_even_with_0_inversions(
        tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, active=["a", "b"], log_lines=_AGREE, log_age=-1000)
    assert g.main() == 2
    assert "manifest" in _rc(capsys)


def test_a_profile_content_xml_newer_than_the_log_refuses(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, active=["a", "b"], log_lines=_AGREE, profile_age=+1000)
    assert g.main() == 2
    assert "profile" in _rc(capsys)


def test_a_profile_content_xml_older_than_the_log_does_not_refuse(tmp_path, monkeypatch,
                                                                   capsys):
    _world(tmp_path, monkeypatch, active=["a", "b"], log_lines=_AGREE, profile_age=-1000)
    assert g.main() == 0, _rc(capsys)


def test_an_active_mod_missing_from_a_class_it_ships_refuses(tmp_path, monkeypatch, capsys):
    """c ships x.xml, the engine logged x.xml for a and b but never c: the log was not
    written by a launch that had c -- evidence, not mere absence."""
    _world(tmp_path, monkeypatch, active=["a", "b", "c"], log_lines=_AGREE,
           files={"c": ["x.xml"]})
    assert g.main() == 2
    assert "c" in _rc(capsys)


def test_an_active_mod_that_ships_no_logged_class_is_only_a_note(tmp_path, monkeypatch,
                                                                  capsys):
    """Twin: c ships only y.xml, which nothing logged -- no evidence it was left out."""
    _world(tmp_path, monkeypatch, active=["a", "b", "c"], log_lines=_AGREE,
           files={"c": ["y.xml"]})
    assert g.main() == 0, _rc(capsys)


def test_a_mod_we_EXCLUDE_that_the_engine_LOGGED_is_a_finding(tmp_path, monkeypatch, capsys):
    """Our active rule leaves e out; the engine signature-checked its files at a launch
    the log shows is current. That is our rule disagreeing with the engine: rc 1."""
    _world(tmp_path, monkeypatch, active=["a", "b"], excluded=["e"],
           log_lines=[*_AGREE, _line("e", "x.xml")],
           dropped=["e: NOT LOADED by the engine -- required dependency 'z' is not installed"])
    assert g.main() == 1
    out = _rc(capsys)
    assert "e" in out and "NOT LOADED" in out


def test_an_excluded_mod_in_a_STALE_log_is_not_convicted(tmp_path, monkeypatch, capsys):
    """Twin: the same log, older than a manifest -- the exclusion may postdate it."""
    _world(tmp_path, monkeypatch, active=["a", "b"], excluded=["e"],
           log_lines=[*_AGREE, _line("e", "x.xml")], log_age=-1000)
    assert g.main() == 2


# FX-H9 (2026-10-12): a DLC folder in a sequence is not a removed mod. MEASURED on a
# fresh 2026-10-12 log: one signature line names `.\extensions\ego_dlc_terran\...`
# (a texture, error 13), DLC are never in `_registry.mods(...)`, so the gate listed
# ego_dlc_terran as "not installed now" and refused a log of the current modlist.
def test_a_DLC_folder_in_a_sequence_is_not_a_removed_folder(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, active=["a", "b"], files={"a": ["x.xml"], "b": ["x.xml"]},
           log_lines=[*_AGREE, _line("ego_dlc_terran", "t.gz"),
                      _line("ego_dlc_terran", "x.xml")],
           dlc=["ego_dlc_terran"])
    assert g.main() == 0, _rc(capsys)


def test_a_DLC_folder_does_not_change_the_pairs_compared(tmp_path, monkeypatch, capsys):
    """The DLC sits BETWEEN a and b in the class; the score is a's and b's pair alone."""
    _world(tmp_path, monkeypatch, active=["a", "b"], files={"a": ["x.xml"], "b": ["x.xml"]},
           log_lines=[_line("a", "x.xml"), _line("ego_dlc_terran", "x.xml"),
                      _line("b", "x.xml")],
           dlc=["ego_dlc_terran"])
    assert g.main() == 0
    out = _rc(capsys)
    assert "ordered pairs: 1 " in out and "inverted by compute_load_order: 0" in out, out
    assert "not in the active set" in out and "+0 logged" in out, out


def test_a_removed_MOD_folder_still_refuses_beside_a_DLC(tmp_path, monkeypatch, capsys):
    """Twin: the DLC exemption must not swallow a genuinely removed mod."""
    _world(tmp_path, monkeypatch, active=["a", "b"], files={"a": ["x.xml"], "b": ["x.xml"]},
           log_lines=[*_AGREE, _line("ego_dlc_terran", "t.gz"), _line("removed_probe", "x.xml")],
           dlc=["ego_dlc_terran"])
    assert g.main() == 2
    out = _rc(capsys)
    assert "1 logged folder(s) are not installed now: removed_probe" in out, out


def test_an_ego_dlc_NAMED_folder_the_config_does_not_know_still_refuses(
        tmp_path, monkeypatch, capsys):
    """Twin: the exemption is the CONFIGURED DLC set, not a name prefix. A folder that is
    neither a known DLC nor installed is gone, whatever it is called."""
    _world(tmp_path, monkeypatch, active=["a", "b"], files={"a": ["x.xml"], "b": ["x.xml"]},
           log_lines=[*_AGREE, _line("ego_dlc_terran", "t.gz")], dlc=["ego_dlc_split"])
    assert g.main() == 2
    assert "not installed now: ego_dlc_terran" in _rc(capsys)


def test_an_unconfigured_reference_exempts_no_DLC_and_says_so(monkeypatch, capsys):
    """The refusal path, run against a REAL wrong configuration (no reference): the DLC
    set is empty, so a DLC line counts as not installed and the gate refuses."""
    from x4validate import _merge, _paths
    monkeypatch.setattr(_merge, "REFERENCE", None)
    with pytest.raises(_paths.Unconfigured):
        _merge.Config()
    assert g._dlc_folders() == frozenset()
    assert "no DLC set to exempt" in _rc(capsys)


def test_P1_an_EXCLUDED_logged_mod_edited_after_the_log_refuses_not_convicts(
        tmp_path, monkeypatch, capsys):
    """Release review P finding 1 (pre-arc twin): e is installed, logged, excluded by our
    active rule -- and its manifest changed AFTER the log (e.g. it was disabled since).
    The exclusion may postdate the launch, so this is CANNOT JUDGE (rc 2), not a finding.
    Twin: test_a_mod_we_EXCLUDE_that_the_engine_LOGGED_is_a_finding (manifest older, rc 1)."""
    _world(tmp_path, monkeypatch, active=["a", "b"], excluded=["e"],
           log_lines=[*_AGREE, _line("e", "x.xml")], newer=["e"],
           dropped=["e: NOT LOADED by the engine -- disabled by its manifest"])
    assert g.main() == 2
    assert "a manifest changed after the log was written (e)" in _rc(capsys)


def test_F285_a_content_edit_after_the_log_does_not_refuse_the_load_order(
        tmp_path, monkeypatch, capsys):
    """Deliberately NOT asked here: the order comes from manifests and dependencies, so a
    diff edited after the launch cannot change it. Twin of the oracle.py refusal."""
    _world(tmp_path, monkeypatch, active=["a", "b"], log_lines=_AGREE,
           files={"a": ["x.xml"], "b": ["x.xml"]})
    t = os.stat(tmp_path / "debug.txt").st_mtime + 1000
    os.utime(tmp_path / "ext" / "a" / "x.xml", (t, t))
    assert g.main() == 0, _rc(capsys)

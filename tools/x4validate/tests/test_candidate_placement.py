"""Tier B, x4compat and x4stats must place the mod under test IDENTICALLY.

AUDIT-2026-09-24 final review item 3: three hand-rolled placements disagreed.
  * Tier B matched the installed copy CASE-SENSITIVELY and ordered it by the
    INSTALLED manifest's dependencies (not the version under test);
  * x4compat placed a dev copy under its OWN folder name even when an installed
    copy with the same id sat elsewhere in the order;
  * x4stats appended the candidate under its own folder name.

The one rule (`_loadorder.place_candidate`): position key = the INSTALLED copy's
folder name when the candidate matches an installed mod by folder or manifest id
(case-insensitive), else the candidate's own folder name; dependencies = the
CANDIDATE's manifest; the installed same-id copy is excluded from the tree.

Each case asserts the EXPECTED set of mods loading before the candidate for each
caller, so the three agreeing on a wrong answer cannot pass. Hermetic: tmp_path only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from x4validate import _check, _compat, _loadorder, _merge, _registry, _stats


def _mod(parent: Path, folder: str, mod_id: str, deps: tuple[str, ...] = ()) -> Path:
    d = parent / folder
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<dependency id="{i}"/>' for i in deps)
    (d / "content.xml").write_text(
        f'<content id="{mod_id}" version="100" name="{folder}">{body}</content>',
        encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    ext = tmp_path / "game" / "extensions"
    ext.mkdir(parents=True)
    dev = tmp_path / "dev"
    dev.mkdir()
    ref = tmp_path / "reference"
    (ref / "extensions").mkdir(parents=True)
    prof = tmp_path / "profile_content.xml"
    prof.write_text("<content/>", encoding="utf-8")
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", prof)
    monkeypatch.setattr(_registry, "default_installed_dirs", lambda: [ext])
    monkeypatch.setattr(_registry, "_reference_dlc_dirs", lambda config=None: [])
    return ext, dev, _merge.Config(reference=ref)


def _before_tier_b(cand: Path) -> list[str]:
    return [p.name for p in _check.tier_b_trees(cand).patch_time]


def _before_compat(ext: Path, cand: Path, cfg) -> list[str]:
    rep = _compat.analyze(ext, candidate=cand, config=cfg)
    key = _loadorder.place_candidate(_registry.mods("active", [ext]), cand).entry["folder"]
    return rep.load_order[:rep.load_order.index(key)]


def _before_stats(ext: Path, cand: Path, cfg, monkeypatch) -> list[str]:
    seen: list[list[str]] = []

    class _R:
        tree = None

    def fake(vpath, config, extra_overlays=(), **_kw):   # 4.3: x4stats also passes index_roots
        seen.append([Path(p).name for p in extra_overlays])
        return _R()
    monkeypatch.setattr(_stats._merge, "build_effective", fake)
    _stats.effective_wares(ext, cfg, exclude=cand, scope="active", patch_time=True)
    return seen[0]


def _all_three(ext, cand, cfg, monkeypatch) -> tuple[list[str], list[str], list[str]]:
    return (_before_tier_b(cand), _before_compat(ext, cand, cfg),
            _before_stats(ext, cand, cfg, monkeypatch))


def test_dev_folder_name_differs_from_installed_same_id(env, monkeypatch):
    ext, dev, cfg = env
    _mod(ext, "AAA", "aaa")
    _mod(ext, "ModX", "modx")
    _mod(ext, "MODX_B", "modx_b")      # sorts AFTER "ModX", BEFORE "modx_dev"
    cand = _mod(dev, "modx_dev", "modx")
    tb, cp, st = _all_three(ext, cand, cfg, monkeypatch)
    # placed at the INSTALLED copy's key "ModX": MODX_B loads after it
    assert tb == ["AAA"], tb
    assert cp == ["AAA"], cp
    assert st == ["AAA"], st


def test_case_difference_matches_the_installed_copy(env, monkeypatch):
    ext, dev, cfg = env
    _mod(ext, "AAA", "aaa")
    _mod(ext, "ModY", "ModY")
    _mod(ext, "ZZZ", "zzz")
    cand = _mod(dev, "mody", "MODY")
    tb, cp, st = _all_three(ext, cand, cfg, monkeypatch)
    # the installed "ModY" is the same mod: excluded, never merged as a third party
    assert tb == ["AAA"], tb
    assert cp == ["AAA"], cp
    assert st == ["AAA"], st


def test_a_dependency_only_in_the_dev_manifest_moves_the_candidate(env, monkeypatch):
    ext, dev, cfg = env
    _mod(ext, "Alpha", "alpha")                       # installed: no dependencies
    _mod(ext, "beta", "beta")
    _mod(ext, "zeta", "zeta")
    cand = _mod(dev, "alpha_dev", "alpha", deps=("zeta",))  # the version under test
    tb, cp, st = _all_three(ext, cand, cfg, monkeypatch)
    # key "Alpha", but it must wait for zeta: second pass, after beta and zeta
    assert tb == ["beta", "zeta"], tb
    assert cp == ["beta", "zeta"], cp
    assert st == ["beta", "zeta"], st


def test_not_installed_uses_its_own_folder_name(env, monkeypatch):
    ext, dev, cfg = env
    _mod(ext, "AAA", "aaa")
    _mod(ext, "ZZZ", "zzz")
    cand = _mod(dev, "NEW", "new_mod")
    tb, cp, st = _all_three(ext, cand, cfg, monkeypatch)
    assert tb == cp == st == ["AAA"], (tb, cp, st)


def test_place_candidate_reports_the_excluded_installed_copy(env):
    ext, dev, _cfg = env
    inst = _mod(ext, "ModX", "modx")
    _mod(ext, "Other", "other")
    cand = _mod(dev, "modx_dev", "MODX")
    placed = _loadorder.place_candidate(_registry.mods("active", [ext]), cand)
    assert placed.entry["folder"] == "ModX"
    assert placed.entry["path"] == str(cand)
    assert [m["path"] for m in placed.excluded] == [str(inst)]
    assert sorted(m["folder"] for m in placed.mods) == ["ModX", "Other"]
    assert sum(1 for m in placed.mods if m["folder"] == "ModX") == 1

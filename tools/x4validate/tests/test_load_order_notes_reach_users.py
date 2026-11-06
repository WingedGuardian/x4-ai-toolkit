"""Every caller that ORDERS the mod set must disclose what the order had to assume.

v3.3.0 release review (lane roots), finding 3: `compute_load_order` records a duplicate
manifest id, a repeated folder, a dependency cycle and an unmeasured cross-root pass --
but only through its opt-in `dropped=` list, and only x4compat passed one. Tier B,
x4effective build, BaseX build-effective, x4stats, x4similar and x4effective dump
ordered the same set and said nothing.

THE MECHANISM (one): given a `_registry.ModList`, `compute_load_order` also appends its
records to the list's own ``.notes``; `place_candidate` shares that channel; and
`_registry.dropped_note` renders ``.notes`` beside the exclusions. Each caller renders
the channel AFTER ordering. Driven here with one duplicate id -- two folders both
claiming ``shared`` -- which every one of them must name.

Hermetic: tmp_path install, profile and reference only.
"""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

from x4validate import _check, _compat, _effective, _merge, _registry

ROOT = Path(__file__).resolve().parents[1]
BASEX = ROOT.parent / "basex"
CLASH = "claimed by both"


def _mod(parent: Path, folder: str, mod_id: str | None = None) -> Path:
    d = parent / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "content.xml").write_text(
        f'<content id="{mod_id or folder}" version="100" name="{folder}"/>',
        encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    ext = tmp_path / "game" / "extensions"
    ext.mkdir(parents=True)
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(b"<wares/>")
    prof = tmp_path / "profile_content.xml"
    prof.write_text("<content/>", encoding="utf-8")
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", prof)
    monkeypatch.setattr(_registry, "GAME_EXTENSIONS", ext)
    monkeypatch.setattr(_registry, "PROFILE_EXTENSIONS", None)
    monkeypatch.setattr(_registry, "WORKSHOP_CONTENT", None)
    monkeypatch.setattr(_registry, "default_installed_dirs", lambda: [ext])
    monkeypatch.setattr(_registry, "_reference_dlc_dirs", lambda config=None: [])
    _mod(ext, "b_one", "shared")
    _mod(ext, "d_two", "shared")
    return ext, tmp_path, _merge.Config(reference=ref, include_packed_dlc=False)


@pytest.fixture
def clean_env(env):
    """TWIN: the same install with the clash removed."""
    ext, tmp, cfg = env
    _mod(ext, "d_two")
    return ext, tmp, cfg


def test_tier_b_names_the_load_order_record(env):
    _ext, tmp, _cfg = env
    t = _check.tier_b_trees(_mod(tmp / "dev", "cand"), _check.Report())
    assert any(CLASH in n for n in t.notes), t.notes


def test_TWIN_tier_b_quiet_without_a_record(clean_env):
    _ext, tmp, _cfg = clean_env
    t = _check.tier_b_trees(_mod(tmp / "dev", "cand"), _check.Report())
    assert not any(CLASH in n for n in t.notes), t.notes


def test_x4effective_build_names_the_load_order_record(env):
    ext, tmp, cfg = env
    said: list[str] = []
    _effective.build(cfg, tmp / "eff.sqlite", dirs=[ext], kinds=("ware",),
                     progress=said.append)
    assert any(CLASH in s for s in said), said


def test_TWIN_x4effective_build_quiet_without_a_record(clean_env):
    ext, tmp, cfg = clean_env
    said: list[str] = []
    _effective.build(cfg, tmp / "eff.sqlite", dirs=[ext], kinds=("ware",),
                     progress=said.append)
    assert not any(CLASH in s for s in said), said


def _basex():
    spec = importlib.util.spec_from_file_location("basex_build_effective_notes",
                                                  BASEX / "build-effective.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_basex_build_effective_names_the_load_order_record(env, capsys):
    _ext, _tmp, cfg = env
    _basex().installed_in_load_order(cfg)
    assert CLASH in capsys.readouterr().err


def test_TWIN_basex_build_effective_quiet_without_a_record(clean_env, capsys):
    _ext, _tmp, cfg = clean_env
    _basex().installed_in_load_order(cfg)
    assert CLASH not in capsys.readouterr().err


def test_x4stats_wares_names_the_load_order_record(env, capsys):
    from x4validate import _stats
    ext, tmp, cfg = env
    _stats.main(["wares", str(_mod(tmp / "dev", "cand")), "--ext-dir", str(ext),
                 "--reference", str(cfg.reference)])
    got = capsys.readouterr()
    assert CLASH in got.out + got.err, got


def test_TWIN_x4stats_quiet_without_a_record(clean_env, capsys):
    from x4validate import _stats
    ext, tmp, cfg = clean_env
    _stats.main(["wares", str(_mod(tmp / "dev", "cand")), "--ext-dir", str(ext),
                 "--reference", str(cfg.reference)])
    got = capsys.readouterr()
    assert CLASH not in got.out + got.err, got


def test_x4similar_names_the_load_order_record(env, monkeypatch, capsys):
    from x4validate import _similarity
    ext, _tmp, cfg = env
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    _similarity.main(["--reference", str(cfg.reference), "--ext-dir", str(ext)])
    got = capsys.readouterr()
    assert CLASH in got.out + got.err, got


def test_x4effective_dump_names_the_load_order_record(env, monkeypatch, capsys):
    from lxml import etree
    from x4validate import _effectivecli as C
    _ext, _tmp, cfg = env
    monkeypatch.setattr(C._merge, "build_effective", lambda *a, **k: types.SimpleNamespace(
        tree=etree.fromstring("<wares/>"), sources=["base"], skipped=[], unmeasured=[]))
    C._cmd_dump(types.SimpleNamespace(reference=str(cfg.reference),
                                      vpath="libraries/wares.xml", chain=False))
    got = capsys.readouterr()
    assert CLASH in got.out + got.err, got


def test_x4compat_names_the_load_order_record(env):
    # the one caller that already did; kept as the control
    ext, _tmp, cfg = env
    rep = _compat.analyze(ext, config=cfg)
    assert CLASH in _compat.render(rep)


def _case_only(ext: Path) -> None:
    d = ext / "aaa_needy"
    d.mkdir()
    (d / "content.xml").write_text(
        '<content id="aaa_needy" version="1" name="n"><dependency id="B_ONE_X"/></content>',
        encoding="utf-8")
    _mod(ext, "b_one_x", "b_one_x")


def test_tier_b_names_a_registry_level_note(env):
    ext, tmp, _cfg = env
    _case_only(ext)
    t = _check.tier_b_trees(_mod(tmp / "dev", "cand"), _check.Report())
    assert any("only ignoring case" in n for n in t.notes), t.notes

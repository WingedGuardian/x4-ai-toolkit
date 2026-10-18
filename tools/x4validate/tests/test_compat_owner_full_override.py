"""A mod shipping a FULL (non-diff) file over another mod's own file is a FULL-OVERRIDE.

Regression (release review V1, in-arc e5a0794): the owner of a nested path supplies the
owner-aware BASE, so `_analyze_vpath` skipped it outright -- which also dropped it from the
overriders, so `len(overriders) >= 2` never fired. MEASURED live: v3.3.1 reported 2 rows
"'cpsdo_vro' clobbers the rest" (full files at
extensions/cpsdo_zb_modpack/assets/fx/weaponfx/macros/...); master reported none, silently.
"""

from __future__ import annotations

from x4validate import _compat, _merge

REL = "assets/fx/macros/bullet_x_macro.xml"
FULL = '<macros><macro name="bullet_x_macro" class="bullet"><properties v="{}"/></macro></macros>'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path, over_text, owner_text=FULL.format(1)):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    ext = tmp_path / "extensions"
    _w(ext / "a_owner" / "content.xml", '<content id="a_owner" name="a" version="1"/>')
    _w(ext / "a_owner" / REL, owner_text)
    _w(ext / "b_over" / "content.xml",
       '<content id="b_over" name="b" version="1"><dependency id="a_owner"/></content>')
    _w(ext / "b_over" / "extensions" / "a_owner" / REL, over_text)
    return ext, _merge.Config(reference=ref)


def test_a_full_file_over_another_mods_own_file_is_a_FULL_OVERRIDE(tmp_path):
    ext, cfg = _world(tmp_path, FULL.format(2))
    rep = _compat.analyze(ext, config=cfg)
    fo = rep.by_kind("FULL-OVERRIDE")
    assert [(c.mods, c.winner) for c in fo] == [(["a_owner", "b_over"], "b_over")], \
        rep.collisions
    assert "'b_over' clobbers the rest" in fo[0].detail
    assert fo[0] in rep.hard


def test_TWIN_a_DIFF_over_another_mods_own_file_is_no_full_override(tmp_path):
    ext, cfg = _world(tmp_path, '<diff><replace sel="//properties/@v">2</replace></diff>')
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("FULL-OVERRIDE") == [], rep.collisions


def test_TWIN_an_owner_whose_file_is_a_DIFF_is_not_an_overrider(tmp_path):
    """The owner's own diff (patching base content) is not a full file: one full file
    alone at the path clobbers nothing another mod shipped as a full file."""
    ref_text = FULL.format(0)
    ext, cfg = _world(tmp_path, FULL.format(2),
                      owner_text='<diff><replace sel="//properties/@v">1</replace></diff>')
    _w(cfg.reference / REL, ref_text)
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("FULL-OVERRIDE") == [], rep.collisions

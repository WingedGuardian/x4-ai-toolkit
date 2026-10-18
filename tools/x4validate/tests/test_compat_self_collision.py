"""The adder named on a HARD row must load BEFORE the op that lands on its content
(MEASURED live 2026-10-18: 10 rows named the WINNER as the adder of a node it re-created by
a later full replace -- "zzz_moona_mk4_tuning over zzz_moona_mk4_tuning"). A mod's own ops
never form a collision row with itself."""

from __future__ import annotations

import contextlib
import io
import json

from x4validate import _compat, _merge

VPATH = "md/m.xml"
BASE = '<mdscript name="M"><cues><cue name="E" x="1"/></cues></mdscript>'
ADD_NEW = '<diff><add sel="//cues"><cue name="New" x="1"/></add></diff>'
REPLACE_NEW_X = "<diff><replace sel=\"//cue[@name='New']/@x\">2</replace></diff>"


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _manifest(mid, deps=()):
    body = "".join(
        f'<dependency id="{d}"' + (' optional="true"' if opt else "") + "/>"
        for d, opt in deps)
    return f'<content id="{mid}" name="{mid}" version="1">{body}</content>'


def _world(tmp_path, mods, base=BASE):
    """*mods*: folder -> (diff text, manifest id, [(dep id, optional)])."""
    ref = tmp_path / "reference"
    _w(ref / VPATH, base)
    ext = tmp_path / "extensions"
    for folder, (diff, mid, deps) in mods.items():
        _w(ext / folder / "content.xml", _manifest(mid, deps))
        _w(ext / folder / VPATH, diff)
    return ext, _merge.Config(reference=ref)


def _run(ext, cfg, *extra):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _compat.main(["check", *extra, "--ext-dir", str(ext),
                           "--reference", str(cfg.reference)])
    return rc, buf.getvalue()


# --- B: the adder must load BEFORE the op that lands on its content -----------------

def test_B_a_later_full_replace_is_not_named_as_the_adder_of_an_earlier_edit(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_edit": ("<diff><replace sel=\"//cue[@name='E']/@x\">2</replace></diff>", "a", []),
        "b_full": ("<diff><replace sel=\"//cue[@name='E']\"><cue name=\"E\" x=\"3\"/>"
                   "</replace></diff>", "b", [])})
    rep = _compat.analyze(ext, config=cfg)
    for c in rep.collisions:
        assert "added by" not in c.detail, c
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_full", rep.collisions


def test_B_one_mod_adding_then_replacing_in_its_own_diff_is_no_collision(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_self": ('<diff><add sel="//cues"><cue name="New" x="1"/></add>'
                   "<replace sel=\"//cue[@name='New']/@x\">2</replace></diff>", "a", []),
        "b_other": ('<diff><add sel="//cues"><cue name="Other"/></add></diff>', "b", [])})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard, rep.hard
    for c in rep.collisions:
        assert c.mods != ["a_self", "a_self"] and "added by 'a_self'" not in c.detail, c


def test_B_TWIN_two_different_mods_still_collide_on_an_added_node(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "a", []),
        "b_patch": (REPLACE_NEW_X, "b", [])})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1 and "added by 'a_adder'" in hard[0].detail

"""Release review V4/V5: what the x4compat TEXT says about rows it builds.

V4: an internal node identity of a node another mod ADDED (``.../cue~a_mod:0:3``) means
nothing outside one run, yet 25 live rows printed one as `node/key`. No synthetic identity
may reach the text output -- not as node/key, not inside a note.
V5: when the sequential instrument disagrees with the merge, the whole file yields no
rows of ANY kind; the NOT ANALYSED reason must say so, not only "node collisions".
"""

from __future__ import annotations

import contextlib
import io

from x4validate import _compat, _compat_state, _merge

VPATH = "md/m.xml"
BASE = '<mdscript name="M"><cues><cue name="E" x="1"/></cues></mdscript>'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path, mods):
    ref = tmp_path / "reference"
    _w(ref / VPATH, BASE)
    ext = tmp_path / "extensions"
    for folder, diff in mods.items():
        _w(ext / folder / "content.xml", f'<content id="{folder}" name="{folder}" version="1"/>')
        _w(ext / folder / VPATH, diff)
    return ext, _merge.Config(reference=ref)


def _text(ext, cfg):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _compat.main(["check", "--soft", "--ext-dir", str(ext),
                           "--reference", str(cfg.reference)])
    return rc, buf.getvalue()


ADD = '<diff><add sel="//cues"><cue name="New" x="1"><cues/></cue></add></diff>'


def test_V4_a_REMOVED_added_node_prints_its_removal_path_not_an_identity(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": ADD,
        "b_rm": "<diff><remove sel=\"//cue[@name='New']\"/></diff>",
        "c_late": "<diff><replace sel=\"//cue[@name='New']/@x\">3</replace></diff>"})
    rep = _compat.analyze(ext, config=cfg)
    assert any("~" in c.target for c in rep.collisions), rep.collisions   # the hazard exists
    rc, out = _text(ext, cfg)
    assert rc == 1, out
    assert "~" not in out, out


def test_V4_a_SUBTREE_note_names_paths_not_identities(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": ADD,
        "b_edit": "<diff><replace sel=\"//cue[@name='New']/@x\">2</replace></diff>",
        "c_wipe": "<diff><replace sel=\"//cue[@name='New']\"><cue name=\"New\"/></replace></diff>"})
    rep = _compat.analyze(ext, config=cfg)
    sub = rep.by_kind("SUBTREE")
    assert sub, rep.collisions
    rc, out = _text(ext, cfg)
    assert "=== SUBTREE" in out
    assert "~" not in out, out


def test_V5_an_instrument_disagreement_says_EVERY_row_kind_is_lost(tmp_path, monkeypatch):
    ext, cfg = _world(tmp_path, {
        "a_mod": "<diff><replace sel=\"//cue[@name='E']/@x\">2</replace></diff>",
        "b_mod": "<diff><replace sel=\"//cue[@name='E']/@x\">3</replace></diff>"})
    real = _compat_state.trace

    def disagree(*a, **k):
        traced, final_paths, _ok = real(*a, **k)
        return traced, final_paths, False

    monkeypatch.setattr(_compat._compat_state, "trace", disagree)
    rep = _compat.analyze(ext, config=cfg)
    assert rep.collisions == []
    why = [s.why for s in rep.skipped if s.what == VPATH]
    assert len(why) == 1 and rep.degraded, rep.skipped
    for word in ("ANY kind", "FULL-OVERRIDE", "UNION-KEY", "not only its node collisions"):
        assert word in why[0], why[0]

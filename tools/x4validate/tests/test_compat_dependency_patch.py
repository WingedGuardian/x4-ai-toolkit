"""Issue #11 dependency rule (user decision 2026-10-18) and the self-collision defect.

A: a replace/remove landing on content another mod ADDED is an INTENDED PATCH -- advisory,
   grouped per (overwriter, adder) pair -- when the overwriting mod DECLARES a dependency
   (required or optional, direct only) on the adder, matched by the adder's manifest `id`
   (not its folder name). Without the declaration it stays a failing HARD row.
B: the mod named as having ADDED a node must load BEFORE the mod whose op lands on it. A
   later full replace that re-creates a node an earlier mod edited in the BASE is not "the
   target was added by <the later mod>" (MEASURED live: 10 rows naming the winner as its own
   adder).
"""

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


# --- A: the dependency rule ---------------------------------------------------------

def test_A_overwrite_of_a_DECLARED_dependency_is_advisory_and_grouped(tmp_path):
    # The adder's folder and id differ on purpose: the match is by manifest id.
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("adder_id", False)])})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard, rep.hard
    assert [(d.mod, d.added_by, d.dependency) for d in rep.dependent_overwrites] == [
        ("b_patch", "a_adder", "required")]
    assert rep.dependent_overwrites[0].target_path == "/mdscript/cues/cue[2]/@x"
    rc, out = _run(ext, cfg)
    assert rc == 0, out
    assert "OVERWRITES OF A DECLARED DEPENDENCY'S ADDED CONTENT" in out, out
    assert "b_patch over a_adder" in out and "1 op target" in out, out
    assert "DIRECT" in out and "transitive" in out
    assert "~" not in out, out
    rc, out = _run(ext, cfg, "--json")
    payload = json.loads(out[out.index("{"):])
    row = payload["dependent_overwrites"][0]
    assert (row["mod"], row["added_by"], row["dependency"]) == ("b_patch", "a_adder", "required")
    assert row["detail"] and row["target"]
    assert payload["collisions"] == []


def test_A_an_OPTIONAL_dependency_counts_too(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("adder_id", True)])})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard, rep.hard
    assert [d.dependency for d in rep.dependent_overwrites] == ["optional"]


def test_A_TWIN_no_declared_dependency_stays_a_failing_HARD_row(tmp_path):
    """Issue #11's own shape: ice_mission_commander over kuertee_more_generic_missions."""
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.hard) == 1 and "added by 'a_adder'" in rep.hard[0].detail
    assert rep.dependent_overwrites == []
    assert _run(ext, cfg)[0] == 1


def test_A_TWIN_a_dependency_naming_the_FOLDER_not_the_id_does_not_count(tmp_path):
    # Optional: a REQUIRED dependency on an id nobody has would keep b_patch from loading.
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("a_adder", True)])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.hard) == 1 and rep.dependent_overwrites == []


def test_A_TWIN_a_dependency_on_a_THIRD_mod_does_not_count(tmp_path):
    """Direct dependency on the ADDER only -- a dependency on something else is not it."""
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("c_id", False)]),
        "c_other": ("<diff/>", "c_id", [])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.hard) == 1 and rep.dependent_overwrites == []


def test_A_TWIN_a_TRANSITIVE_dependency_does_not_count(tmp_path):
    """b depends on c, c depends on the adder: b declares nothing about the adder."""
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("c_id", False)]),
        "c_middle": ("<diff/>", "c_id", [("adder_id", False)])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.hard) == 1 and rep.dependent_overwrites == []


def test_A_a_remove_of_a_declared_dependencys_addition_is_advisory(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": ("<diff><remove sel=\"//cue[@name='New']\"/></diff>", "patch_id",
                    [("adder_id", False)])})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard, rep.hard
    assert [(d.mod, d.added_by) for d in rep.dependent_overwrites] == [("b_patch", "a_adder")]


def test_A_TWIN_two_overwriters_of_one_added_node_still_fail(tmp_path):
    """Two mods replacing the same added node contend with EACH OTHER: a declared
    dependency on the adder does not make that intended."""
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "b_id", [("adder_id", False)]),
        "c_patch": (REPLACE_NEW_X.replace(">2<", ">3<"), "c_id", [("adder_id", False)])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.hard) == 1, rep.hard
    assert rep.hard[0].mods == ["a_adder", "b_patch", "c_patch"]
    assert rep.dependent_overwrites == []


def test_A_candidate_mode_keeps_only_the_advisories_it_takes_part_in(tmp_path):
    ext, cfg = _world(tmp_path, {
        "a_adder": (ADD_NEW, "adder_id", []),
        "b_patch": (REPLACE_NEW_X, "patch_id", [("adder_id", False)]),
        "z_other": ('<diff><add sel="//cues"><cue name="Z"/></add></diff>', "z_id", [])})
    assert _compat.analyze(ext, candidate=ext / "z_other", config=cfg).dependent_overwrites == []
    kept = _compat.analyze(ext, candidate=ext / "a_adder", config=cfg).dependent_overwrites
    assert [(d.mod, d.added_by) for d in kept] == [("b_patch", "a_adder")]


def test_A_the_text_groups_many_ops_into_ONE_line_per_pair(tmp_path):
    adds = "".join(f'<cue name="N{i}" x="1"/>' for i in range(5))
    reps = "".join(f"<replace sel=\"//cue[@name='N{i}']/@x\">2</replace>" for i in range(5))
    ext, cfg = _world(tmp_path, {
        "a_adder": (f'<diff><add sel="//cues">{adds}</add></diff>', "adder_id", []),
        "b_patch": (f"<diff>{reps}</diff>", "patch_id", [("adder_id", False)])})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.dependent_overwrites) == 5 and not rep.hard
    rc, out = _run(ext, cfg)
    assert rc == 0
    assert out.count("b_patch over a_adder") == 1, out
    assert "5 op targets" in out, out

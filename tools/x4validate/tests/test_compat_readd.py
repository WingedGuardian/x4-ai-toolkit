"""A node one mod REMOVES and a later mod ADDS again, under the same parent.

The engine applies patches in load order, so the node exists with the re-adder's
definition. `_compat_state.Nodes` gives the re-added node the removed node's identity
(its key is the same), so both mods' ops land on ONE row -- but the re-adder's
`<add sel="/wares">` targets the PARENT, and the revived identity was never recorded as
something that op created. The re-adder was therefore absent from the row, and the
remover was named its live owner while the store (x4effective) named the re-adder.

MEASURED 2026-11-17 on the live install (gates/cross_tool.py, 1 of 130 HARD rows): a
mod adds into a base ware, a second removes the ware, a later overlay adds the ware
again under `/wares`; x4compat said the remover wins. These pin every order around it.
"""
from __future__ import annotations

from x4validate import _compat, _merge


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _mod(ext, folder, files, deps=(), optional=()):
    root = ext / folder
    dep_xml = "".join(f'<dependency id="{d}"/>' for d in deps) + "".join(
        f'<dependency id="{d}" optional="true"/>' for d in optional)
    _w(root / "content.xml",
       f'<content id="{folder}" name="{folder}" version="1">{dep_xml}</content>')
    for vpath, text in files.items():
        _w(root / vpath, text)
    return root


def _setup_ref(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml",
       '<wares><ware id="ore"><price average="100"/></ware>'
       '<ware id="ice"><price average="50"/></ware></wares>')
    return _merge.Config(reference=ref)

_ADD_INTO_ORE = ('<diff><add sel="//ware[@id=\'ore\']"><owner faction="argon"/></add>'
                 '</diff>')
_RM_ORE = '<diff><remove sel="//ware[@id=\'ore\']"/></diff>'
_READD_ORE = ('<diff><add sel="/wares"><ware id="ore"><price average="55"/></ware>'
              '</add></diff>')
_EDIT_ORE = '<diff><replace sel="//ware[@id=\'ore\']/price/@average">9</replace></diff>'
_REPLACE_ORE = ('<diff><replace sel="//ware[@id=\'ore\']"><ware id="ore">'
                '<price average="7"/></ware></replace></diff>')
_VP = "libraries/wares.xml"


def _live(cfg, ext, folders):
    return _merge.build_effective(_VP, _merge.replace(
        cfg, overlays=tuple(ext / f for f in folders))).tree


def _ore_rows(rep):
    return [c for c in rep.by_kind("HARD") if c.target == "/wares/ware[1]"]


def test_the_live_shape_add_into_then_remove_then_RE_ADD_is_won_by_the_re_adder(tmp_path):
    """The measured case: x adds into ore, y removes ore, z adds ore again."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "x_into", {_VP: _ADD_INTO_ORE})
    _mod(ext, "y_rm", {_VP: _RM_ORE}, deps=["x_into"])
    _mod(ext, "z_readd", {_VP: _READD_ORE}, deps=["y_rm"])
    live = _live(cfg, ext, ("x_into", "y_rm", "z_readd"))
    ore = live.findall("ware[@id='ore']")
    assert len(ore) == 1 and ore[0].find("price").get("average") == "55"   # control
    assert ore[0].find("owner") is None                                   # control
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["x_into", "y_rm", "z_readd"], rep.load_order
    rows = _ore_rows(rep)
    assert len(rows) == 1, rep.collisions
    row = rows[0]
    assert row.mods == ["x_into", "y_rm", "z_readd"], row
    assert row.live_value_owner() == "z_readd" and not row.removed_by, row
    assert row.target_tree == "final", row
    assert "'y_rm' removes this node" in row.detail and "'z_readd' adds it again" in row.detail, \
        row.detail
    assert "change(s) of 'x_into' went with the removal" in row.detail, row.detail


def test_remove_then_RE_ADD_by_another_mod_is_a_row_the_re_adder_wins(tmp_path):
    """Two mods alone: the removal is NOT live, and that is a contest -- the remover's
    intent is undone by the later definition. Before the fix this file had no row."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE})
    assert _live(cfg, ext, ("a_rm", "b_readd")).find("ware[@id='ore']/price").get(
        "average") == "55"                                                # control
    rows = _ore_rows(_compat.analyze(ext, config=cfg))
    assert [(r.mods, r.live_value_owner(), r.removed_by) for r in rows] == [
        (["a_rm", "b_readd"], "b_readd", "")], rows
    assert "adds it again" in rows[0].detail and "went with" not in rows[0].detail, rows[0]


def test_an_add_of_a_DIFFERENT_id_does_not_revive_the_removed_node(tmp_path):
    """Twin for the identity clause: only the same KEY continues the removed node."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_add", {_VP: _READD_ORE.replace('id="ore"', 'id="ore2"')})
    rep = _compat.analyze(ext, config=cfg)
    assert _ore_rows(rep) == [], rep.collisions


def test_REVERSE_order_add_then_a_later_remove_is_the_removal(tmp_path):
    """b adds a node the base lacks, c removes it later: gone, the remover is named."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "b_add", {_VP: _READD_ORE.replace('id="ore"', 'id="zware"')})
    _mod(ext, "c_rm", {_VP: '<diff><remove sel="//ware[@id=\'zware\']"/></diff>'})
    assert _live(cfg, ext, ("b_add", "c_rm")).find("ware[@id='zware']") is None  # control
    rows = [c for c in _compat.analyze(ext, config=cfg).by_kind("HARD")
            if set(c.mods) == {"b_add", "c_rm"}]
    assert len(rows) == 1, rows
    assert rows[0].live_value_owner() == "c_rm" and rows[0].removed_by == "c_rm", rows[0]


def test_REVERSE_order_add_into_then_a_later_remove_is_still_the_removal(tmp_path):
    """The live shape WITHOUT the re-add: the remover stays the live owner."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "x_into", {_VP: _ADD_INTO_ORE})
    _mod(ext, "y_rm", {_VP: _RM_ORE}, deps=["x_into"])
    rows = _ore_rows(_compat.analyze(ext, config=cfg))
    assert len(rows) == 1 and rows[0].mods == ["x_into", "y_rm"], rows
    assert rows[0].live_value_owner() == "y_rm" and rows[0].removed_by == "y_rm", rows[0]


def test_SAME_mod_remove_and_re_add_alone_is_no_row(tmp_path):
    """One mod redefining a node is no collision between mods."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_self", {_VP: _RM_ORE.replace("</diff>", "") + _READD_ORE.replace("<diff>", "")})
    _mod(ext, "z_other", {_VP: '<diff><replace sel="//ware[@id=\'ice\']/price/@average">1'
                               '</replace></diff>'})
    assert _ore_rows(_compat.analyze(ext, config=cfg)) == []


def test_SAME_mod_remove_and_re_add_after_another_edit_is_the_wipe(tmp_path):
    """An earlier mod edits INSIDE ore; a later one removes and re-adds it in one file:
    the earlier edit is wiped (SUBTREE), and nobody claims the re-added node over it."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_edit", {_VP: _EDIT_ORE})
    _mod(ext, "b_self", {_VP: _RM_ORE.replace("</diff>", "") + _READD_ORE.replace("<diff>", "")})
    assert _live(cfg, ext, ("a_edit", "b_self")).find("ware[@id='ore']/price").get(
        "average") == "55"                                                # control
    rep = _compat.analyze(ext, config=cfg)
    assert _ore_rows(rep) == [], rep.collisions
    assert [(c.mods, c.wiped_by) for c in rep.by_kind("SUBTREE")] == [
        (["a_edit", "b_self"], "b_self")], rep.collisions


def test_an_edit_AFTER_the_re_add_wins_over_the_re_adder(tmp_path):
    """remove, re-add, later edit: the last writer at the node's own position wins,
    and the re-adder is named as the mod that ADDED the target the edit lands on."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE})
    _mod(ext, "c_replace", {_VP: '<diff><replace sel="//ware[@id=\'ore\']"><ware id="ore">'
                                 '<price average="7"/></ware></replace></diff>'})
    rows = _ore_rows(_compat.analyze(ext, config=cfg))
    assert len(rows) == 1 and rows[0].mods == ["a_rm", "b_readd", "c_replace"], rows
    assert rows[0].live_value_owner() == "c_replace" and not rows[0].removed_by, rows[0]
    assert "added by 'b_readd'" in rows[0].detail, rows[0].detail
    assert "re-supplied by 'b_readd'" in rows[0].detail, rows[0].detail


# --- user decision 2026-11-17 (amended): a re-add of a DECLARED dependency's removal ------
#
# A re-adder that declares a direct dependency (required or optional, by manifest id -- the
# issue #11 rule's reading) on every other mod on the row is ADVISORY (exit 0), but it is NOT
# folded into `dependent_overwrites`: a dependency proves the re-adder patches the remover,
# not that re-adding is meant, so it is listed on its own (`readded_after_removal`) for the
# user to confirm. Without the declaration, or with it the other way round, it stays HARD.


def _readd_world(tmp_path, b_deps=(), b_optional=(), a_deps=()):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE}, deps=a_deps)
    _mod(ext, "b_readd", {_VP: _READD_ORE}, deps=b_deps, optional=b_optional)
    return cfg, ext


def _run(ext, cfg, *extra):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _compat.main(["check", *extra, "--ext-dir", str(ext),
                           "--reference", str(cfg.reference)])
    return rc, buf.getvalue()


def test_a_re_add_by_a_mod_DECLARING_the_remover_is_its_own_advisory(tmp_path):
    cfg, ext = _readd_world(tmp_path, b_deps=["a_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["a_rm", "b_readd"], rep.load_order
    assert _ore_rows(rep) == [], rep.collisions
    assert rep.dependent_overwrites == [], rep.dependent_overwrites
    got = [(r.mod, r.removed_by, r.others, r.dependency, r.winner, r.target_path, r.guard)
           for r in rep.readded_after_removal]
    assert got == [("b_readd", "a_rm", "a_rm", "required", "b_readd", "/wares/ware[2]", "")], got
    assert ("removed by 'a_rm', added back by 'b_readd' ('b_readd' declares a dependency "
            "on 'a_rm')") in rep.readded_after_removal[0].detail, rep.readded_after_removal
    rc, out = _run(ext, cfg)
    assert rc == 0, out
    assert "RE-ADDED AFTER A DECLARED DEPENDENCY REMOVED IT -- CONFIRM INTENT" in out, out
    assert "b_readd re-adds what a_rm removed (required dependency): 1 node in 1 file" in out
    assert "OVERWRITES OF A DECLARED DEPENDENCY" not in out, out


def test_the_re_add_advisory_is_in_the_JSON(tmp_path):
    import json
    cfg, ext = _readd_world(tmp_path, b_deps=["a_rm"])
    rc, out = _run(ext, cfg, "--json")
    data = json.loads(out)
    assert rc == 0 and data["dependent_overwrites"] == [], data
    assert [(r["mod"], r["removed_by"], r["line"] > 0) for r in data["readded_after_removal"]] \
        == [("b_readd", "a_rm", True)], data["readded_after_removal"]


def test_TWIN_a_re_add_WITHOUT_a_declared_dependency_stays_HARD(tmp_path):
    cfg, ext = _readd_world(tmp_path)
    rep = _compat.analyze(ext, config=cfg)
    assert len(_ore_rows(rep)) == 1 and rep.readded_after_removal == [], rep
    rc, _out = _run(ext, cfg)
    assert rc == 1


def test_TWIN_a_dependency_the_OTHER_way_round_is_no_excused_re_add(tmp_path):
    """The remover declares the re-adder. A dependency also ORDERS the two, so b now loads
    first and there is no re-add at all: b's add lands beside the base node, and a's
    remove then matches two nodes, which the merge model skips (both stay). Nothing is
    excused as a re-add. (No row at all is reported here today: an add duplicating a
    BASE key is not compared -- a separate, pre-existing gap, not this rule's.) The
    re-adder declaring some OTHER mod is the next test."""
    cfg, ext = _readd_world(tmp_path, a_deps=["b_readd"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["b_readd", "a_rm"], rep.load_order
    live = _live(cfg, ext, ("b_readd", "a_rm"))
    assert len(live.findall("ware[@id='ore']")) == 2                      # control
    assert rep.readded_after_removal == [] and rep.dependent_overwrites == []


def test_TWIN_a_re_adder_declaring_some_OTHER_mod_stays_HARD(tmp_path):
    """d declares c (unrelated), not the remover a: the row stays HARD."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "c_other", {_VP: '<diff><replace sel="//ware[@id=\'ice\']/price/@average">1'
                               '</replace></diff>'})
    _mod(ext, "d_readd", {_VP: _READD_ORE}, deps=["c_other"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order.index("a_rm") < rep.load_order.index("d_readd"), rep.load_order
    assert [r.mods for r in _ore_rows(rep)] == [["a_rm", "d_readd"]], rep.collisions
    assert rep.readded_after_removal == [] and rep.dependent_overwrites == []
    # It declares NOT EVEN the remover, so no "declaring X would make it advisory" hint.
    assert "would make this an advisory" not in _ore_rows(rep)[0].detail


def test_TWIN_an_OPTIONAL_dependency_counts_as_for_an_overwrite(tmp_path):
    cfg, ext = _readd_world(tmp_path, b_optional=["a_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert _ore_rows(rep) == [], rep.collisions
    assert [(r.mod, r.dependency) for r in rep.readded_after_removal] == [
        ("b_readd", "optional")], rep.readded_after_removal


def test_TWIN_a_third_mod_in_the_row_must_be_declared_too(tmp_path):
    """The live shape: x adds into ore, y removes it, z adds it back declaring y only.
    As for an overwrite (every adder must be declared), every other mod on the row must
    be: z declares y but not x, so the row stays HARD; declaring both excuses it."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "x_into", {_VP: _ADD_INTO_ORE})
    _mod(ext, "y_rm", {_VP: _RM_ORE}, deps=["x_into"])
    _mod(ext, "z_readd", {_VP: _READD_ORE}, deps=["y_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert len(_ore_rows(rep)) == 1 and rep.readded_after_removal == [], rep
    _mod(ext, "z_readd", {_VP: _READD_ORE}, deps=["y_rm", "x_into"])
    rep = _compat.analyze(ext, config=cfg)
    assert _ore_rows(rep) == [], rep.collisions
    assert [(r.mod, r.removed_by, r.others) for r in rep.readded_after_removal] == [
        ("z_readd", "y_rm", "x_into, y_rm")], rep.readded_after_removal
    assert rep.dependent_overwrites == [], rep.dependent_overwrites


def test_TWIN_an_edit_after_the_re_add_is_not_excused_by_the_re_adders_dependency(tmp_path):
    """Only the re-adder's own win is excused: c replaces the re-added node, so the row
    stays HARD (c wins) and no re-add is listed, though b declares a."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE}, deps=["a_rm"])
    _mod(ext, "c_replace", {_VP: _REPLACE_ORE}, deps=["b_readd"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.readded_after_removal == [], rep.readded_after_removal
    hard = _ore_rows(rep)
    dep = [d for d in rep.dependent_overwrites if d.target == "/wares/ware[1]"]
    # c declares b (the re-adder, whose content it overwrites) but not a: still HARD.
    assert [r.live_value_owner() for r in hard] == ["c_replace"] and dep == [], (hard, dep)


def test_a_GUARDED_re_add_carries_its_guard(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE.replace('<add sel="/wares">',
                                                  '<add sel="/wares" if="not(//ware[@id=\'ore\'])">')},
         deps=["a_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert [r.guard for r in rep.readded_after_removal] == [
        "if=not(//ware[@id='ore'])"], rep.readded_after_removal


def test_TWIN_a_replacer_after_the_re_add_declaring_EVERYONE_is_still_no_re_add(tmp_path):
    """c replaces the re-added node and declares both a and b: c is not a re-adder, so
    nothing is listed as a re-add, whatever c declares."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE}, deps=["a_rm"])
    _mod(ext, "c_replace", {_VP: _REPLACE_ORE}, deps=["b_readd", "a_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.readded_after_removal == [], rep.readded_after_removal
    assert [r.live_value_owner() for r in _ore_rows(rep)] == ["c_replace"], rep.collisions


# --- review 2026-11-17: the row's detail names what happened, mod by mod --------------


def _four(tmp_path, *mods):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    prev = None
    for folder, text in mods:
        _mod(ext, folder, {_VP: text}, deps=[prev] if prev else ())
        prev = folder
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == [f for f, _t in mods], rep.load_order
    return cfg, ext, rep


def test_remove_readd_remove_readd_names_both_removers_and_the_lost_re_add(tmp_path):
    """a removes, b re-adds, d removes again, e re-adds: e wins; b's node went with d's
    removal (it is LOST, never "the target was added by b"), and a, a remover, is not."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_readd", _READD_ORE),
                          ("d_rm", _RM_ORE), ("e_readd", _READD_ORE))
    rows = _ore_rows(rep)
    assert len(rows) == 1 and rows[0].live_value_owner() == "e_readd", rows
    d = rows[0].detail
    assert "'a_rm', 'd_rm' remove this node, and 'e_readd' adds it again" in d, d
    assert "the earlier change(s) of 'b_readd' went with the removal" in d, d
    assert "added by" not in d and "UNMEASURED" not in d, d


def test_an_op_that_matched_nothing_before_the_re_adder_is_named_not_lost(tmp_path):
    """a removes, b's replace then matches nothing, c re-adds: b changed nothing, so it
    is named as matching nothing, never as a change that went with the removal."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_rep", _REPLACE_ORE),
                          ("c_readd", _READD_ORE))
    rows = _ore_rows(rep)
    assert len(rows) == 1 and rows[0].live_value_owner() == "c_readd", rows
    d = rows[0].detail
    assert "op(s) from 'b_rep' match nothing (the node is removed when they load)" in d, d
    assert "went with" not in d, d


def test_a_re_adder_with_a_SECOND_op_on_the_node_is_still_the_re_adder(tmp_path):
    """b re-adds ore and, in the same file, edits it again: b re-supplied the node, so
    the row says b adds it again -- not that another mod re-supplied it."""
    edit = ('<replace sel="//ware[@id=\'ore\']"><ware id="ore"><price average="3"/>'
            '</ware></replace>')
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE),
                          ("b_readd", _READD_ORE.replace("</diff>", edit + "</diff>")))
    # b declares a (`_four` chains the dependencies), so the row is the re-add advisory.
    rows = rep.readded_after_removal
    assert _ore_rows(rep) == [] and [r.winner for r in rows] == ["b_readd"], rep
    assert "'b_readd' adds it again" in rows[0].detail, rows[0].detail
    assert "another mod" not in rows[0].detail, rows[0].detail


def test_a_miss_BEFORE_a_second_removal_is_still_not_lost(tmp_path):
    """a removes, b's replace matches nothing, c re-adds, d removes, e re-adds: b changed
    nothing, so it is not listed with c as a change that went with the removal."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_rep", _REPLACE_ORE),
                          ("c_readd", _READD_ORE), ("d_rm", _RM_ORE), ("e_readd", _READD_ORE))
    d = _ore_rows(rep)[0].detail
    assert "the earlier change(s) of 'c_readd' went with the removal" in d, d
    assert "op(s) from 'b_rep' match nothing" in d, d


def test_a_re_add_then_REMOVE_in_the_winners_own_file_is_the_removal(tmp_path):
    """b re-adds ore and removes it again in the same file: the node is gone at the end,
    so the removal is live and b is named its remover -- never a re-add."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE),
                          ("b_both", _READD_ORE.replace("</diff>", "") + _RM_ORE.replace(
                              "<diff>", "")))
    rows = _ore_rows(rep)
    assert len(rows) == 1 and rows[0].removed_by == "b_both", rows
    assert rep.readded_after_removal == [], rep.readded_after_removal


def test_a_winner_that_REMOVES_then_re_adds_is_named_among_the_removers(tmp_path):
    """a removes, b re-adds, c removes b's node and re-adds its own in one file: both
    removals are named, and b's node went with c's removal."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_readd", _READD_ORE),
                          ("c_self", _RM_ORE.replace("</diff>", "") + _READD_ORE.replace(
                              "<diff>", "")))
    rows = _ore_rows(rep)
    assert len(rows) == 1 and rows[0].live_value_owner() == "c_self", rows
    d = rows[0].detail
    assert "'a_rm', 'c_self' remove this node, and 'c_self' adds it again" in d, d
    assert "the earlier change(s) of 'b_readd' went with the removal" in d, d


def test_two_re_adds_with_a_removal_BETWEEN_are_no_duplicate(tmp_path):
    """a removes, b re-adds, d removes, e re-adds: b's and e's ore never coexist, so no
    "2 mods add ware#ore under the same parent (duplicate)" row and no UNION-KEY row."""
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_readd", _READD_ORE),
                          ("d_rm", _RM_ORE), ("e_readd", _READD_ORE))
    assert [c for c in rep.collisions if "duplicate" in c.detail] == [], rep.collisions
    assert rep.by_kind("UNION-KEY") == [], rep.collisions


def test_TWIN_two_adds_of_one_key_with_an_UNRELATED_removal_between_are_a_duplicate(tmp_path):
    zadd = _READD_ORE.replace('id="ore"', 'id="zware"')
    cfg, ext, rep = _four(tmp_path, ("b_add", zadd), ("d_rm", _RM_ORE), ("e_add", zadd))
    dups = [c for c in rep.by_kind("HARD") if "duplicate" in c.detail]
    assert [c.mods for c in dups] == [["b_add", "e_add"]], rep.collisions


def test_TWIN_coexisting_duplicates_one_removed_LATER_are_still_a_duplicate(tmp_path):
    """b and e both add zware (they coexist: a real duplicate), then f removes the FIRST
    copy only. b's node is gone at the end, so its slot is unknown -- the duplicate row
    must stay, never be read as one slot filled in turn."""
    zadd = _READD_ORE.replace('id="ore"', 'id="zware"')
    cfg, ext, rep = _four(tmp_path, ("b_add", zadd), ("e_add", zadd),
                          ("f_rm", '<diff><remove sel="(//ware[@id=\'zware\'])[1]"/></diff>'))
    live = _live(cfg, ext, ("b_add", "e_add", "f_rm"))
    assert len(live.findall("ware[@id='zware']")) == 1                    # control
    dups = [c for c in rep.by_kind("HARD") if "duplicate" in c.detail]
    assert [c.mods for c in dups] == [["b_add", "e_add"]], rep.collisions


# --- review round 2 (2026-11-17) -------------------------------------------------------

_G_RM_ZZ = "<diff><remove sel=\"//ware[@id='zz']\" if=\"not(\"/></diff>"
_G_REP_ZZ = "<diff><replace sel=\"//ware[@id='zz']\" if=\"not(\"><ware id=\"zz\"/></replace></diff>"
_NEW_ZZ = _READD_ORE.replace('id="ore"', 'id="zz"')


def test_an_adder_with_every_remove_AFTER_it_is_no_re_adder(tmp_path):
    """a adds zz; b's remove and c's replace load later and match nothing (unevaluable
    guards). No removal came before a, so the row must not read "remove this node, and
    'a_new' adds it again" with no remover named."""
    cfg, ext, rep = _four(tmp_path, ("a_new", _NEW_ZZ), ("b_rmg", _G_RM_ZZ), ("c_repg", _G_REP_ZZ))
    rows = [c for c in rep.by_kind("HARD") if c.mods == ["a_new", "b_rmg", "c_repg"]]
    assert len(rows) == 1, rep.collisions
    d = rows[0].detail
    assert "adds it again" not in d and "— remove" not in d, d
    assert "'a_new' is the last mod whose op on this node applies" in d, d
    assert rep.readded_after_removal == []


def test_a_HARD_re_add_names_the_declaration_that_would_make_it_advisory(tmp_path):
    """z re-adds ore and declares y (the remover) but not x, whose change went with the
    removal: the HARD row says that declaring x would make it the advisory."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "x_into", {_VP: _ADD_INTO_ORE})
    _mod(ext, "y_rm", {_VP: _RM_ORE}, deps=["x_into"])
    _mod(ext, "z_readd", {_VP: _READD_ORE}, deps=["y_rm"])
    d = _ore_rows(_compat.analyze(ext, config=cfg))[0].detail
    assert ("'z_readd' declares a dependency on 'y_rm' but not on 'x_into': declaring it "
            "would make this an advisory re-add") in d, d


def test_TWIN_a_HARD_re_add_declaring_NOT_EVEN_the_remover_gets_no_hint(tmp_path):
    cfg, ext = _readd_world(tmp_path)
    d = _ore_rows(_compat.analyze(ext, config=cfg))[0].detail
    assert "would make this an advisory" not in d, d


def test_one_REQUIRED_and_one_OPTIONAL_dependency_label_the_row_optional(tmp_path):
    """The weakest declaration names the row, as for an overwrite."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "x_into", {_VP: _ADD_INTO_ORE})
    _mod(ext, "y_rm", {_VP: _RM_ORE}, deps=["x_into"])
    _mod(ext, "z_readd", {_VP: _READD_ORE}, deps=["y_rm"], optional=["x_into"])
    rep = _compat.analyze(ext, config=cfg)
    assert [r.dependency for r in rep.readded_after_removal] == ["optional"], rep


def test_TWIN_a_failed_op_AFTER_the_re_adder_is_a_later_miss_not_a_removed_one(tmp_path):
    """a removes, b re-adds, c's replace (unevaluable guard) fails after b: c is a LATER op
    matching nothing, never "match nothing (the node is removed when they load)"."""
    g = "<diff><replace sel=\"//ware[@id='ore']\" if=\"not(\"><ware id=\"ore\"/></replace></diff>"
    cfg, ext, rep = _four(tmp_path, ("a_rm", _RM_ORE), ("b_readd", _READD_ORE), ("c_g", g))
    d = _ore_rows(rep)[0].detail
    assert "later op(s) from 'c_g' match nothing" in d, d
    assert "'c_g' match nothing (the node is removed" not in d, d


# --- user decision 2026-11-17: the four signals, and the only two that warn ------------


def _mod_v(ext, folder, files, version="1", date=None, deps=()):
    """*deps*: [(id, version-or-None)]."""
    root = ext / folder
    dep_xml = "".join(f'<dependency id="{d}"' + (f' version="{v}"' if v is not None else "")
                      + "/>" for d, v in deps)
    date_xml = f' date="{date}"' if date is not None else ""
    _w(root / "content.xml", f'<content id="{folder}" name="{folder}" version="{version}"'
                             f'{date_xml}>{dep_xml}</content>')
    for vpath, text in files.items():
        _w(root / vpath, text)


def _sig(tmp_path, a_version="800", a_date="2026-03-24", b_date="2026-11-17", dep_version=None,
         b_text=_READD_ORE):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod_v(ext, "a_rm", {_VP: _RM_ORE}, version=a_version, date=a_date)
    _mod_v(ext, "b_readd", {_VP: b_text}, version="18", date=b_date,
           deps=[("a_rm", dep_version)])
    rep = _compat.analyze(ext, config=cfg)
    assert [r.mod for r in rep.readded_after_removal] == ["b_readd"], rep
    return cfg, ext, rep.readded_after_removal[0]


def test_the_live_shape_shows_every_signal_and_warns_on_none(tmp_path):
    """Re-adder dated after the remover, no dependency version stated: no warning."""
    cfg, ext, r = _sig(tmp_path)
    one = r.signals["removers"][0]
    assert (one["declared_version"], one["installed_version"], one["version"]) == (
        "", "800", "unknown (no dependency version stated)"), one
    assert (one["readder_date"], one["remover_date"], one["date"]) == (
        "2026-11-17", "2026-03-24", "same or newer"), one
    assert r.signals["identical_to_pre_removal"] == "no" and r.signals["guard"] == "none"
    assert r.carry_forward_warning == [], r.carry_forward_warning
    rc, out = _run(ext, cfg)
    assert rc == 0, out
    assert "dependency version declared none / installed 800 (unknown" in out, out
    assert "identical to the node before the removal: no; guard: none" in out, out
    assert "WARN" not in out, out


def test_an_OLDER_declared_dependency_version_is_SHOWN_and_never_warns(tmp_path):
    """User decision 2026-11-17 (14:4x): X4 reads a dependency version as a minimum, and 66
    of 108 stated versions are older than installed -- shown, never a warning."""
    cfg, ext, r = _sig(tmp_path, dep_version="700")
    assert r.signals["removers"][0]["version"] == "older"
    assert r.carry_forward_warning == [], r
    rc, out = _run(ext, cfg)
    assert rc == 0 and "WARN" not in out, out
    assert "dependency version declared 700 / installed 800 (older)" in out, out


def test_TWIN_an_EQUAL_or_NEWER_declared_version_does_not_warn(tmp_path):
    for v in ("800", "900"):
        sub = tmp_path / v
        sub.mkdir()
        _c, _e, r = _sig(sub, dep_version=v)
        assert r.signals["removers"][0]["version"] == "same or newer", (v, r.signals)
        assert r.carry_forward_warning == [], (v, r.carry_forward_warning)


def test_TWIN_an_UNCOMPARABLE_version_is_unknown_never_a_warning(tmp_path):
    _c, _e, r = _sig(tmp_path, dep_version="v7-beta")
    assert r.signals["removers"][0]["version"].startswith("unknown (cannot compare"), r.signals
    assert r.carry_forward_warning == []


def test_an_OLDER_re_adder_date_warns(tmp_path):
    _c, _e, r = _sig(tmp_path, b_date="2026-01-01")
    assert r.signals["removers"][0]["date"] == "older"
    assert r.carry_forward_warning == [
        "'b_readd' content.xml date 2026-01-01 is older than 'a_rm''s 2026-03-24"], r


def test_TWIN_a_SAME_or_NEWER_re_adder_date_does_not_warn(tmp_path):
    _c, _e, r = _sig(tmp_path, b_date="2026-03-24")
    assert r.signals["removers"][0]["date"] == "same or newer" and not r.carry_forward_warning


def test_TWIN_an_UNPARSEABLE_or_MISSING_date_is_unknown_and_disclosed(tmp_path):
    _c, _e, r = _sig(tmp_path, b_date="24.03.2026")
    d = r.signals["removers"][0]["date"]
    assert d == "unknown ('b_readd' date '24.03.2026' is not YYYY-MM-DD)", d
    assert r.carry_forward_warning == []
    sub = tmp_path / "nodate"
    sub.mkdir()
    _c, _e, r = _sig(sub, a_date=None)
    assert r.signals["removers"][0]["date"] == "unknown ('a_rm' has no date)", r.signals
    assert r.carry_forward_warning == []


def test_an_IDENTICAL_and_GUARDED_re_add_is_shown_never_warned(tmp_path):
    same = ('<diff><add sel="/wares" if="not(//ware[@id=\'ore\'])"><ware id="ore">'
            '<price average="100"/></ware></add></diff>')
    _c, _e, r = _sig(tmp_path, b_text=same)
    assert r.signals["identical_to_pre_removal"] == "yes", r.signals
    assert r.signals["guard"] == "if=not(//ware[@id='ore'])", r.signals
    assert r.carry_forward_warning == []


def test_the_signals_and_warning_are_in_the_JSON(tmp_path):
    import json
    cfg, ext, _r = _sig(tmp_path, dep_version="700", b_date="2026-01-01")
    rc, out = _run(ext, cfg, "--json")
    row = json.loads(out)["readded_after_removal"][0]
    assert rc == 0 and set(row["signals"]) == {"removers", "identical_to_pre_removal", "guard"}
    assert row["signals"]["removers"][0]["version"] == "older"
    # The date warns; the older declared version is shown only.
    assert row["carry_forward_warning"] == [
        "'b_readd' content.xml date 2026-01-01 is older than 'a_rm''s 2026-03-24"], row


def test_TWIN_a_trailing_zero_in_a_DOTTED_version_is_the_same_version(tmp_path):
    _c, _e, r = _sig(tmp_path, a_version="8.0", dep_version="8.0.0")
    assert r.signals["removers"][0]["version"] == "same or newer" and not r.carry_forward_warning


def test_a_DOTTED_against_an_UNDOTTED_version_is_unknown(tmp_path):
    """Review round 3: 1.0.2 vs 102 (or 800.0 vs 800) is no comparison -- unknown."""
    for a, d in (("102", "1.0.2"), ("800", "800.0"), ("1.0.2", "102")):
        sub = tmp_path / f"{a}-{d}"
        sub.mkdir()
        _c, _e, r = _sig(sub, a_version=a, dep_version=d)
        assert r.signals["removers"][0]["version"].startswith("unknown (cannot compare"), (a, d)
        assert r.carry_forward_warning == [], (a, d)


def test_a_version_with_TEXT_after_it_reads_unknown_and_never_crashes(tmp_path):
    """"1.0beta" must not reach int(): a prefix match would, and crash analyze."""
    for a, d in (("1.0", "1.0beta"), ("1.0beta", "0.9")):
        sub = tmp_path / f"{a}-{d}"
        sub.mkdir()
        _c, _e, r = _sig(sub, a_version=a, dep_version=d)
        assert r.signals["removers"][0]["version"].startswith("unknown"), (a, d, r.signals)


def test_dates_that_are_not_EXACTLY_YYYY_MM_DD_read_unknown(tmp_path):
    """Both "2026-07-8" and "25 December 2024" occur live; a trailing suffix must not
    pass either."""
    for b in ("2026-07-8", "25 December 2024", "2026-07-08 12:00"):
        sub = tmp_path / b.replace(" ", "_").replace(":", "")
        sub.mkdir()
        _c, _e, r = _sig(sub, b_date=b)
        assert r.signals["removers"][0]["date"].startswith("unknown ('b_readd' date"), (b, r.signals)
        assert r.carry_forward_warning == [], b


# --- review round 3 (2026-11-17) --------------------------------------------------------


def test_TWIN_the_hint_needs_EVERY_remover_declared(tmp_path):
    """Two removers, the re-adder declares only one: no "declaring X would make it
    advisory" hint -- it is not one declaration away."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_readd", {_VP: _READD_ORE}, deps=["a_rm"])
    _mod(ext, "c_rm", {_VP: _RM_ORE}, deps=["b_readd"])
    _mod(ext, "d_readd", {_VP: _READD_ORE}, deps=["a_rm", "c_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["a_rm", "b_readd", "c_rm", "d_readd"], rep.load_order
    d = _ore_rows(rep)[0].detail
    assert "would make this an advisory" in d and "but not on 'b_readd'" in d, d
    _mod(ext, "d_readd", {_VP: _READD_ORE}, deps=["a_rm"])
    _mod(ext, "c_rm", {_VP: _RM_ORE}, deps=["b_readd"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order.index("c_rm") < rep.load_order.index("d_readd"), rep.load_order
    d = _ore_rows(rep)[0].detail
    assert "declares a dependency on" not in d, d


def test_the_hint_never_asks_to_declare_a_mod_whose_op_MATCHED_NOTHING(tmp_path):
    """a removes, b's replace matches nothing, c re-adds declaring a: b changed nothing,
    so c is not told that declaring b makes it advisory; it is told the rule still counts
    b, so the row stays HARD."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {_VP: _RM_ORE})
    _mod(ext, "b_rep", {_VP: _REPLACE_ORE}, deps=["a_rm"])
    _mod(ext, "c_readd", {_VP: _READD_ORE}, deps=["a_rm"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["a_rm", "b_rep", "c_readd"], rep.load_order
    d = _ore_rows(rep)[0].detail
    assert "would make this an advisory" not in d, d
    assert ("'b_rep' matched nothing here, yet the rule counts every mod on the node, so the "
            "row stays HARD unless that is declared too") in d, d


def _custom(tmp_path, base, rm_text, readd_text, extra=()):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", base)
    cfg = _merge.Config(reference=ref)
    ext = tmp_path / "extensions"
    prev = None
    for folder, text in [("a_rm", rm_text), *extra, ("z_readd", readd_text)]:
        deps = ["a_rm", *(f for f, _t in extra)] if folder == "z_readd" else (
            [prev] if prev else [])
        _mod(ext, folder, {_VP: text}, deps=deps)
        prev = folder
    rep = _compat.analyze(ext, config=cfg)
    assert [r.mod for r in rep.readded_after_removal] == ["z_readd"], rep
    return rep.readded_after_removal[0]


_BASE2 = '<wares><ware id="ore" transport="container"><price average="100"/></ware></wares>'


def test_identical_ignores_ATTRIBUTE_ORDER(tmp_path):
    r = _custom(tmp_path, _BASE2, _RM_ORE,
                '<diff><add sel="/wares"><ware transport="container" id="ore">'
                '<price average="100"/></ware></add></diff>')
    assert r.signals["identical_to_pre_removal"] == "yes", r.signals


def test_identical_ignores_WHITESPACE_only_text(tmp_path):
    r = _custom(tmp_path, _BASE2, _RM_ORE,
                '<diff><add sel="/wares">\n  <ware id="ore" transport="container">\n'
                '    <price average="100"/>\n  </ware>\n</add></diff>')
    assert r.signals["identical_to_pre_removal"] == "yes", r.signals


def test_TWIN_a_real_difference_is_not_identical(tmp_path):
    r = _custom(tmp_path, _BASE2, _RM_ORE,
                '<diff><add sel="/wares"><ware id="ore" transport="container">'
                '<price average="101"/></ware></add></diff>')
    assert r.signals["identical_to_pre_removal"] == "no", r.signals


def test_identical_compares_with_the_node_before_the_LAST_removal(tmp_path):
    """a removes, b re-adds a NEW definition, c removes, z re-adds b's definition: z is
    identical to what c removed (b's), not to the base node a removed."""
    mine = ('<diff><add sel="/wares"><ware id="ore" transport="container">'
            '<price average="7"/></ware></add></diff>')
    r = _custom(tmp_path, _BASE2, _RM_ORE, mine,
                extra=[("b_readd", mine), ("c_rm", _RM_ORE)])
    assert r.removed_by == "a_rm, c_rm", r
    assert r.signals["identical_to_pre_removal"] == "yes", r.signals


def test_TWO_nodes_with_the_key_before_the_removal_read_unknown(tmp_path):
    """b adds a second ore beside the base one, a removes only the first: before the
    removal the key is held twice, so there is no single node to compare -- unknown."""
    base = _BASE2
    dup = ('<diff><add sel="/wares"><ware id="ore" transport="container">'
           '<price average="100"/></ware></add></diff>')
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", base)
    cfg = _merge.Config(reference=ref)
    ext = tmp_path / "extensions"
    _mod(ext, "b_dup", {_VP: dup})
    _mod(ext, "c_rm", {_VP: '<diff><remove sel="(//ware[@id=\'ore\'])[1]"/></diff>'},
         deps=["b_dup"])
    _mod(ext, "z_readd", {_VP: dup}, deps=["b_dup", "c_rm"])
    rep = _compat.analyze(ext, config=cfg)
    got = [r.signals["identical_to_pre_removal"] for r in rep.readded_after_removal]
    assert got and all(g.startswith("unknown (no single node") for g in got), (
        got, rep.collisions)


def test_a_re_adding_add_with_TWO_children_of_the_key_reads_unknown(tmp_path):
    """The add puts ore in twice: which child is "the re-added element" is not one answer."""
    two = ('<diff><add sel="/wares"><ware id="ore" transport="container"><price average="100"/>'
           '</ware><ware id="ore" transport="container"><price average="1"/></ware></add></diff>')
    r = _custom(tmp_path, _BASE2, _RM_ORE, two)
    assert r.signals["identical_to_pre_removal"] == (
        "unknown (the re-adding <add> holds no single element with this key)"), r.signals

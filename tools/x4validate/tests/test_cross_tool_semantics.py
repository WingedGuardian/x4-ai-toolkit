"""Per-kind collision semantics in gates/cross_tool.py.

The gate itself only exercises these against the CURRENT modlist, so its "0
disagreements" is a fact about today's 115 mods, not a guarantee about the logic.
These tests pin the logic itself.

Background: BLIND-SPOTS F25/F26, KB 2026-08-13d, CLAUDE.md gotcha #18.
`Collision.winner` means a different thing per kind, and asserting one blanket
rule produced 6 false HARD disagreements + 6 false SUBTREE alarms — both of them
the CHECKER's bug, not x4compat's.
"""

import sys
from pathlib import Path

from conftest import import_gate  # noqa: E402

# Module-scope import of a gate exits the whole pytest session on a machine
# with no X4 install -- see tests/conftest.py. Skip, do not abort.
cross_tool = import_gate("cross_tool")


# --- SUBTREE targets are NODE-scoped, not file-scoped -------------------------

def test_document_root_replace_is_a_whole_file_wipe():
    """`/macros` is the document-root override idiom (gotcha #10) — 140 of 148
    SUBTREE rows on the measured install. The victim must be gone entirely."""
    assert cross_tool._subtree_scope("/macros") == ("file", None)


def test_whole_macro_element_replace_is_also_file_wide():
    assert cross_tool._subtree_scope("/macros/macro") == ("file", None)


def test_node_scoped_wipe_maps_to_a_property_prefix():
    """A wipe of ONE node must NOT assert file-wide absence: the victim keeps its
    other attributes in the same document. This is the exact shape that produced
    6 false alarms."""
    mode, prop = cross_tool._subtree_scope("/macros/macro/properties/explosiondamage")
    assert (mode, prop) == ("node", "explosiondamage")


def test_deeper_node_scope_keeps_the_dotted_grammar():
    mode, prop = cross_tool._subtree_scope("/macros/macro/properties/hull/max")
    assert (mode, prop) == ("node", "hull.max")


def test_bare_properties_is_a_whole_entity_wipe():
    assert cross_tool._subtree_scope("/macros/macro/properties") == ("file", None)


def test_predicated_target_is_unmapped_not_guessed():
    """A predicate cannot be mapped onto a flattened prop key. Report it as
    unmapped so it is COUNTED, never silently treated as clean."""
    mode, prop = cross_tool._subtree_scope(
        "/macros/macro/connections/connection[@ref='con_cockpit_01']")
    assert mode == "unmapped" and prop is None


def test_unknown_anchor_is_unmapped():
    assert cross_tool._subtree_scope("/components/component/source")[0] == "unmapped"


# --- nested mod-on-mod paths --------------------------------------------------

def test_nested_patch_path_also_offers_the_owners_logical_path():
    """`build_touch_map` rewrites extensions/<owner>/<rel> to <rel>. A lookup that
    only tries the literal spelling finds nothing — that is how 6 of 148 rows went
    unresolvable and nearly became a bogus x4compat finding."""
    literal, stripped = cross_tool._vpath_forms(
        "extensions/some_mod/assets/units/size_l/macros/x_macro.xml")
    assert literal == "extensions/some_mod/assets/units/size_l/macros/x_macro.xml"
    assert stripped == "assets/units/size_l/macros/x_macro.xml"


def test_plain_path_is_unchanged_by_stripping():
    literal, stripped = cross_tool._vpath_forms("libraries/wares.xml")
    assert literal == stripped == "libraries/wares.xml"


def test_dlc_paths_are_not_treated_as_mod_nesting():
    """Unpacked ego_dlc_* content genuinely lives under extensions/ — stripping it
    would invent a vpath the game does not have."""
    _, stripped = cross_tool._vpath_forms(
        "extensions/ego_dlc_terran/libraries/modulegroups.xml")
    assert stripped.startswith("extensions/ego_dlc_terran/")


# --- Collision.live_value_owner(): the one definition of "whose value is live" ---

from x4validate import _compat  # noqa: E402


def _c(kind, winner="w_mod", wiped_by=""):
    return _compat.Collision("v.xml", kind, "t", ["a_mod", "w_mod"], winner,
                             wiped_by=wiped_by)


def test_kinds_that_really_do_have_a_live_winner_report_it():
    for kind in ("FULL-OVERRIDE", "HARD", "UNION-KEY"):
        assert _c(kind).live_value_owner() == "w_mod", kind


def test_subtree_refuses_to_name_a_live_owner():
    """`winner` was the WIPER; a later mod can re-supply what was wiped
    (MEASURED 3 of 148 on the live install), so there is no answer to give."""
    c = _c("SUBTREE", winner="", wiped_by="w_mod")
    assert c.live_value_owner() is None
    assert c.wiped_by == "w_mod", "the wiper is still reported, under its own name"


def test_name_clash_refuses_too():
    assert _c("NAME-CLASH", winner="").live_value_owner() is None


def test_soft_has_no_winner_to_claim():
    assert _c("SOFT", winner="").live_value_owner() is None


def test_an_empty_winner_never_renders_as_a_mod_name():
    """Guards the failure this replaced: '' must not read as an answer."""
    assert _c("HARD", winner="").live_value_owner() is None


# --- a HARD row decided by an earlier REMOVAL (AUDIT-2026-09-24 AN-5) ----------

def test_a_removal_owned_row_is_checked_against_the_stores_REMOVALS():
    """The remover owns no surviving value, so the gate must look for it among the
    store's `removed` sources -- never among attribute origins, where it is absent
    by construction and would read as a false disagreement."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE removed(vpath TEXT, node_path TEXT, source TEXT, "
                "op_line INTEGER)")
    con.execute("INSERT INTO removed VALUES('libraries/wares.xml', '/wares/ware[1]', "
                "'a_mod', 1)")
    assert cross_tool._removal_sources(con, "libraries/wares.xml") == {"a_mod"}
    assert cross_tool._removal_sources(con, "libraries/jobs.xml") == set()


def test_a_removal_owned_HARD_row_goes_red_and_green_against_store_removals(
        tmp_path, monkeypatch):
    """The merged check (lane E's per-node HARD scope + AN-5): a row whose live state
    is a REMOVAL is compared against the store's `removed` sources -- agreeing when
    the remover recorded the removal, failing when the store names someone else."""
    import sqlite3
    import types
    from test_audit0924_gates import _cross_tool_with
    from x4validate import _compat
    vp = "libraries/wares.xml"
    db = tmp_path / "e.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    con.execute("create table removed (vpath, node_path, source, op_line)")
    con.execute("insert into entities values (1,'ware','ice','k',?, 'base')", (vp,))
    con.execute("insert into attrs values (1,'price.max','1','base')")
    con.execute("insert into removed values (?, '/wares/ware[1]', 'a_mod', 1)", (vp,))
    con.commit()
    con.close()

    def row(remover):
        return _compat.Collision(vpath=vp, kind="HARD", target="/wares/ware[1]",
                                 mods=["a_mod", "b_mod"], winner=remover,
                                 removed_by=remover)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [row("a_mod")])
    assert not ct.failures, ct.failures
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [row("b_mod")])
    assert ct.failures, "the store records a_mod's removal; b_mod was claimed"


def _removal_store(tmp_path, removed_rows):
    import sqlite3
    vp = "libraries/wares.xml"
    db = tmp_path / "e.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    con.execute("create table removed (vpath, node_path, source, op_line)")
    con.execute("insert into entities values (1,'ware','ice','k',?, 'base')", (vp,))
    con.execute("insert into attrs values (1,'price.max','1','base')")
    for node, src in removed_rows:
        con.execute("insert into removed values (?, ?, ?, 1)", (vp, node, src))
    con.commit()
    con.close()
    return db


def _removal_row(vpath, target, remover):
    from x4validate import _compat
    return _compat.Collision(vpath=vpath, kind="HARD", target=target,
                             mods=[remover, "b_mod"], winner=remover, removed_by=remover)


def test_a_remover_of_a_DIFFERENT_node_in_the_file_does_not_agree(tmp_path, monkeypatch):
    """Review of AN-5: file-level `removed` sources confirmed any claim by any mod that
    removed ANYTHING in wares.xml. The removal must be of the collided node."""
    from test_audit0924_gates import _cross_tool_with
    db = _removal_store(tmp_path, [("/wares/ware[7]", "a_mod")])
    ct = _cross_tool_with(monkeypatch, tmp_path, db,
                          [_removal_row("libraries/wares.xml", "/wares/ware[1]", "a_mod")])
    assert ct.failures, "a_mod removed ware[7], not ware[1]; the check agreed"


def test_a_removal_of_an_ANCESTOR_of_the_collided_node_agrees(tmp_path, monkeypatch):
    from test_audit0924_gates import _cross_tool_with
    db = _removal_store(tmp_path, [("/wares/ware[1]", "a_mod")])
    ct = _cross_tool_with(monkeypatch, tmp_path, db,
                          [_removal_row("libraries/wares.xml", "/wares/ware[1]/price/@max",
                                        "a_mod")])
    assert not ct.failures, ct.failures


def test_a_removal_row_in_an_UNTRACKED_file_is_explained_not_a_disagreement(
        tmp_path, monkeypatch, capsys):
    """The store never merges md/ (or aiscripts/, t/, index/), so it can hold no
    removal there: that is the explained UNTRACKED_FILE bucket, never a failure."""
    from test_audit0924_gates import _cross_tool_with
    db = _removal_store(tmp_path, [])
    ct = _cross_tool_with(monkeypatch, tmp_path, db,
                          [_removal_row("md/some_script.xml", "/mdscript/cues/cue[1]",
                                        "a_mod")])
    assert not ct.failures, ct.failures
    assert "explained" in capsys.readouterr().out


# --- release review 2026-09-26: a FINDING outranks the coverage-floor REFUSAL -------

def test_a_HARD_disagreement_is_a_FAILURE_even_over_the_coverage_floor(
        tmp_path, monkeypatch, capsys):
    """The floor branch used to `not_run(...); continue` BEFORE the disagreement was
    noted, so a real HARD disagreement was filed as CANNOT (rc 2) whenever more than
    10% of rows were unmapped. The disagreement must be recorded as a FAIL; the floor
    refusal is still recorded beside it."""
    from lxml import etree
    from test_audit0924_gates import _WARE_TREE, _ware_store, _cross_tool_with
    from x4validate import _compat
    vp, db = _ware_store(tmp_path)
    tree = etree.fromstring(_WARE_TREE)
    # modA owns production[default]; production[2] (alt) is modB's -> a DISAGREEMENT.
    bad = _compat.Collision(vpath=vp, kind="HARD", target="/wares/ware[1]/production[2]",
                            mods=["modA", "modB"], winner="modA")
    lost = [_compat.Collision(vpath=vp, kind="HARD", target=f"/wares/ware[{9 + i}]",
                              mods=["modA", "modB"], winner="modB") for i in range(2)]
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [bad, *lost], tree)
    assert any(x.startswith("HARD:") for x in ct.failures), (ct.failures, ct.cannot)
    assert any(x.startswith("HARD:") for x in ct.cannot), ct.cannot
    assert "2 unmapped by the checker" in capsys.readouterr().out


# --- release review 2026-09-26: an ENTITY_REMOVED row is CHECKED, not explained away ---

_FOUR_WARES = (b'<wares><ware id="a"><price max="1"/></ware><ware id="b"><price max="1"/>'
               b'</ware><ware id="c"><price max="1"/></ware><ware id="d"><price max="1"/>'
               b'</ware></wares>')


def _entity_removed_store(tmp_path, removals):
    """wares a and b survive the merge; c and d are gone. *removals* are store
    `removed` rows (node_path AT THE INSTANT of removal, source), in application
    order -- as `_merge._path_of` writes them."""
    import sqlite3
    vp = "libraries/wares.xml"
    db = tmp_path / "e.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    con.execute("create table removed (vpath, node_path, source, op_line)")
    for i, name in enumerate(("a", "b"), 1):
        con.execute("insert into entities values (?,'ware',?,'k',?, 'base')", (i, name, vp))
        con.execute("insert into attrs values (?,'price.max','1','base')", (i,))
    for n, (node, src) in enumerate(removals):
        con.execute("insert into removed values (?, ?, ?, ?)", (vp, node, src, n + 1))
    con.commit()
    con.close()
    return vp, db


def _hard(vp, target, owner):
    from x4validate import _compat
    return _compat.Collision(vpath=vp, kind="HARD", target=target,
                             mods=["other_mod", owner], winner=owner)


def test_an_ENTITY_REMOVED_row_whose_owner_did_not_remove_it_is_a_FAILURE(
        tmp_path, monkeypatch):
    """compat names mod_b the live owner of ware d; the store says mod_a removed it.
    It used to count as 'explained' and leave the denominator unchecked."""
    from lxml import etree
    from test_audit0924_gates import _cross_tool_with
    # mod_a removes c (ware[3]) and then d -- which is ware[3] again by then.
    vp, db = _entity_removed_store(tmp_path, [("/wares/ware[3]", "mod_a"),
                                              ("/wares/ware[3]", "mod_a")])
    tree = etree.fromstring(_FOUR_WARES)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [_hard(vp, "/wares/ware[4]", "mod_b")],
                          tree)
    assert any(x.startswith("HARD:") for x in ct.failures), (ct.failures, ct.cannot)


def test_an_ENTITY_REMOVED_row_agrees_through_SHIFTED_removal_paths(tmp_path, monkeypatch):
    """Twin: the owner DID remove it. The store records d's removal as ware[3] (c was
    already gone), so an exact-path lookup of compat's base target ware[4] finds no
    record -- the replay must map it back to d."""
    from lxml import etree
    from test_audit0924_gates import _cross_tool_with
    vp, db = _entity_removed_store(tmp_path, [("/wares/ware[3]", "mod_a"),
                                              ("/wares/ware[3]", "mod_a")])
    tree = etree.fromstring(_FOUR_WARES)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [_hard(vp, "/wares/ware[4]", "mod_a")],
                          tree)
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot)


def test_a_removal_at_the_SAME_instant_path_but_a_different_base_node_does_not_agree(
        tmp_path, monkeypatch):
    """Twin the other way: mod_x's record reads ware[3] -- the exact string of c's base
    target -- but by then c was gone and ware[3] was d. It removed d, not c."""
    from lxml import etree
    from test_audit0924_gates import _cross_tool_with
    vp, db = _entity_removed_store(tmp_path, [("/wares/ware[3]", "mod_a"),
                                              ("/wares/ware[3]", "mod_x")])
    tree = etree.fromstring(_FOUR_WARES)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [_hard(vp, "/wares/ware[3]", "mod_x")],
                          tree)
    assert any(x.startswith("HARD:") for x in ct.failures), (ct.failures, ct.cannot)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [_hard(vp, "/wares/ware[3]", "mod_a")],
                          tree)
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot)


def test_the_replay_skips_DLC_removals_already_in_the_base_tree(tmp_path, monkeypatch):
    """The checker's tree is base + DLC, so a DLC's removal is already applied there;
    replaying it again would shift every later record by one."""
    from lxml import etree
    from test_audit0924_gates import _cross_tool_with
    vp, db = _entity_removed_store(tmp_path, [("/wares/ware[1]", "ego_dlc_x"),
                                              ("/wares/ware[4]", "mod_a")])
    tree = etree.fromstring(_FOUR_WARES)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [_hard(vp, "/wares/ware[4]", "mod_a")],
                          tree)
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot)


def test_sql_hardening_survives_an_UNUSABLE_store(monkeypatch, capsys):
    """v4.0.0 review R5-4: `_effective._connect` reports a missing/incompatible store with
    ValueError now; the gate caught only SystemExit and died with a traceback."""
    from x4validate import _effective
    monkeypatch.setattr(cross_tool, "run", lambda *a, **k: (1, "refused"))

    def boom(db):
        raise ValueError("no store at x -- run `x4effective build` first")
    monkeypatch.setattr(_effective, "_connect", boom)
    # the store path must RESOLVE, or _env.effective_db() refuses first and _connect is
    # never reached (the first draft of this test passed for exactly that reason)
    monkeypatch.setattr(cross_tool._env, "effective_db", lambda: Path("effective.sqlite"))
    monkeypatch.setattr(cross_tool, "failures", [])
    monkeypatch.setattr(cross_tool, "cannot", [])
    cross_tool.check_sql_hardening()
    # v4.0.0 delta review: the first fix only PRINTED "check NOT RUN", so a gate whose
    # read-only check never ran still exited 0. It must be counted as NOT RUN (exit 2).
    assert any("read-only" in c for c in cross_tool.cannot), cross_tool.cannot
    capsys.readouterr()


def test_an_UNUSABLE_store_makes_the_gate_exit_2_not_0(monkeypatch, capsys):
    """End to end through main(): every other section passes, the sql-hardening
    connection cannot be opened -> the verdict is 2 (could not run), never 0."""
    from x4validate import _effective
    for name in ("check_sensitivity", "check_cross_tool_agreement",
                 "check_builder_idempotence", "check_path_edges"):
        monkeypatch.setattr(cross_tool, name, lambda *a, **k: None)
    monkeypatch.setattr(cross_tool, "run", lambda *a, **k: (1, "refused"))
    monkeypatch.setattr(cross_tool, "failures", [])
    monkeypatch.setattr(cross_tool, "cannot", [])

    def boom(db):
        raise ValueError("incompatible store")
    monkeypatch.setattr(_effective, "_connect", boom)
    monkeypatch.setattr(cross_tool._env, "effective_db", lambda: Path("effective.sqlite"))
    assert cross_tool.main() == 2
    capsys.readouterr()


def test_a_USABLE_store_lets_the_sql_hardening_section_pass(monkeypatch, capsys, tmp_path):
    """Twin: a real read-only store -> the section counts nothing as NOT RUN, so the
    exit-2 above is caused by the unusable store and nothing else."""
    import sqlite3 as _sq
    from x4validate import _effective
    db = tmp_path / "e.sqlite"
    c = _sq.connect(db)
    c.execute("CREATE TABLE entities(origin)")
    c.execute("CREATE TABLE attrs(a)")
    c.commit()
    c.close()
    monkeypatch.setattr(cross_tool, "run", lambda *a, **k: (1, "refused"))
    monkeypatch.setattr(cross_tool, "failures", [])
    monkeypatch.setattr(cross_tool, "cannot", [])
    monkeypatch.setattr(_effective, "_connect",
                        lambda p: _sq.connect(f"file:{p}?mode=ro", uri=True))
    monkeypatch.setattr(cross_tool._env, "effective_db", lambda: db)
    cross_tool.check_sql_hardening()
    assert not cross_tool.cannot and not cross_tool.failures, (cross_tool.cannot,
                                                                cross_tool.failures)
    capsys.readouterr()


# --- FX-H8: a NESTED vpath's base tree is the OWNER's file (as x4compat builds it) ---
#
# MEASURED 2026-10-12 on the live modlist: 9 of 50 mappable HARD rows, all at
# `extensions/cpsdo_zb_modpack/assets/props/surfaceelements/macros/`, were UNRESOLVED
# because the gate built the base tree with a bare `_merge.Config()`, whose overlays
# never contain the owner mod -- so the tree was None. x4compat builds the same file
# owner-aware; the gate now asks x4compat's own helper for that tree.

_OWNED_REL = "assets/props/surfaceelements/macros/shield_t_macro.xml"
_OWNED_BASE = ('<macros><macro name="shield_t_macro" class="shieldgenerator"><properties>'
               '<recharge max="1" rate="1" delay="1"/></properties></macro></macros>')


def _owned_world(tmp_path, monkeypatch, store_origin, owner="owner_mod"):
    """owner_mod ships <rel>; a_patch and z_patch both replace its recharge/@rate via
    the nested path extensions/<owner>/<rel> (z_patch loads last and wins). The store
    records the live recharge.rate with origin *store_origin*."""
    import sqlite3

    from x4validate import _merge
    ref = tmp_path / "reference"
    ref.mkdir()
    ext = tmp_path / "extensions"
    mods = {"owner_mod": {_OWNED_REL: _OWNED_BASE}}
    for p in ("a_patch", "z_patch"):
        mods[p] = {f"extensions/{owner}/{_OWNED_REL}":
                   f'<diff><replace sel="/macros/macro/properties/recharge/@rate">{p}'
                   '</replace></diff>'}
    for folder, files in mods.items():
        root = ext / folder
        root.mkdir(parents=True)
        (root / "content.xml").write_text(
            f'<content id="{folder}" name="{folder}" version="1"/>', encoding="utf-8")
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
    db = tmp_path / "e.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    con.execute("create table removed (vpath, node_path, source, op_line)")
    con.execute("insert into entities values (1,'macro','shield_t_macro','shieldgenerator',?,"
                "'owner_mod')", (_OWNED_REL,))
    con.execute("insert into attrs values (1,'recharge.rate','x',?)", (store_origin,))
    con.commit()
    con.close()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    ct = import_gate("cross_tool", module_level=False)
    monkeypatch.setattr(ct, "failures", [])
    monkeypatch.setattr(ct, "cannot", [])
    monkeypatch.setattr(ct._env, "effective_db", lambda: db)
    monkeypatch.setattr(ct._env, "extensions", lambda: ext)
    monkeypatch.setattr(ct._env, "stale_store_refusal", lambda db, who: None)
    monkeypatch.setattr(ct._merge, "Config", lambda *a, **k: cfg)
    return ct


def _run_with(ct, monkeypatch, collisions):
    import types
    monkeypatch.setattr(ct._compat, "analyze",
                        lambda *a, **k: types.SimpleNamespace(collisions=collisions))
    ct.check_cross_tool_agreement()
    return ct


def _real_hard(ct):
    rows = [c for c in ct._compat.analyze(ct._env.extensions(), config=ct._merge.Config())
            .collisions if c.kind == "HARD"]
    assert len(rows) == 1 and rows[0].winner == "z_patch", rows
    assert rows[0].vpath.lower().startswith("extensions/owner_mod/"), rows[0].vpath
    return rows


def test_FXH8_a_NESTED_HARD_row_maps_through_the_owners_base_tree(
        tmp_path, monkeypatch, capsys):
    """RED on the bare-Config tree_for: the row was UNRESOLVED (1 of 1 unmapped, the
    floor tripped, CANNOT). GREEN: it maps to recharge.rate and agrees."""
    ct = _owned_world(tmp_path, monkeypatch, store_origin="z_patch")
    ct.check_cross_tool_agreement()                 # the REAL analyze, end to end
    out = capsys.readouterr().out
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot, out)
    assert "checked 1 of 1 mappable" in out and "0 unmapped by the checker" in out, out


def test_FXH8_TWIN_the_LOSING_mod_as_store_origin_is_a_FAILURE(tmp_path, monkeypatch, capsys):
    """Same owned fixture; the store says a_patch (the loser) owns the live value. Now
    that the row maps, that is a DISAGREEMENT -- a FAIL, never CANNOT or ok."""
    ct = _owned_world(tmp_path, monkeypatch, store_origin="a_patch")
    ct.check_cross_tool_agreement()
    out = capsys.readouterr().out
    assert any(x.startswith("HARD:") for x in ct.failures), (ct.failures, ct.cannot, out)
    assert not any(x.startswith("HARD:") for x in ct.cannot), (ct.cannot, out)


def test_sequential_identity_maps_via_its_qualified_final_path(tmp_path, monkeypatch, capsys):
    ct = _owned_world(tmp_path, monkeypatch, store_origin="z_patch")
    row = _real_hard(ct)[0]
    row.target = "/generated/identity~a_patch:0:1/@rate"
    row.target_path = "/macros/macro/properties/recharge/@rate"
    row.target_tree = "final"
    _run_with(ct, monkeypatch, [row])
    out = capsys.readouterr().out
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot, out)
    assert "checked 1 of 1 mappable" in out


def _subtree_row(victim, target, target_path):
    return _compat.Collision(_OWNED_REL, "SUBTREE", target, [victim, "w_mod"], "",
                             "wipe", wiped_by="w_mod", target_path=target_path,
                             target_tree="final")


def test_a_SUBTREE_on_a_sequential_identity_is_scoped_by_its_final_path(
        tmp_path, monkeypatch, capsys):
    """Issue #11 follow-up: a SUBTREE target can be a synthetic identity (a node another
    mod added); its scope comes from `target_path`. RED: the identity was scoped as-is,
    so the row was 'unmapped' and the check could not run (CANNOT)."""
    ct = _owned_world(tmp_path, monkeypatch, store_origin="z_patch")
    row = _subtree_row("a_patch", "/macros/macro~x_mod:0:1", "/macros/macro")
    _run_with(ct, monkeypatch, [row])
    out = capsys.readouterr().out
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot, out)
    assert "1/1 clean - 0 unmapped" in out, out


def test_TWIN_a_SUBTREE_scoped_by_its_final_path_still_finds_a_surviving_victim(
        tmp_path, monkeypatch, capsys):
    """The same row whose victim still owns recharge.rate in the store must FAIL -- the
    final path is used to check, not to excuse."""
    ct = _owned_world(tmp_path, monkeypatch, store_origin="a_patch")
    row = _subtree_row("a_patch", "/macros/macro~x_mod:0:1", "/macros/macro")
    _run_with(ct, monkeypatch, [row])
    out = capsys.readouterr().out
    assert any(x.startswith("SUBTREE:") for x in ct.failures), (ct.failures, ct.cannot, out)
    assert "~" not in out, out


def test_FXH8_TWIN_an_INACTIVE_owner_and_a_0_node_target_stay_UNRESOLVED(
        tmp_path, monkeypatch, capsys):
    """The fallback survives: an owner that is not an active mod has no base tree, and
    a target matching 0 nodes in the owned tree resolves to nothing -- both UNRESOLVED,
    so the coverage floor can still trip (CANNOT)."""
    from x4validate import _compat
    ct = _owned_world(tmp_path, monkeypatch, store_origin="z_patch")
    real = _real_hard(ct)[0]
    ghost = _compat.Collision(vpath=f"extensions/ghost_mod/{_OWNED_REL}", kind="HARD",
                              target=real.target, mods=["a_patch", "z_patch"],
                              winner="z_patch")
    nothere = _compat.Collision(vpath=real.vpath, kind="HARD",
                                target="/macros/macro/properties/nothere/@rate",
                                mods=["a_patch", "z_patch"], winner="z_patch")
    _run_with(ct, monkeypatch, [real, ghost, nothere])
    out = capsys.readouterr().out
    assert f"HARD not compared x2 [CHECKER]: {ct.UNRESOLVED}" in out, out
    assert "checked 1 of 3 mappable" in out, out
    assert any(x.startswith("HARD:") for x in ct.cannot), (ct.cannot, out)
    assert not ct.failures, ct.failures

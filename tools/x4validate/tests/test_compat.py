"""Tests for _compat.py — cross-mod collision detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from x4validate import _compat, _merge


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _mod(ext, folder, files, mod_id=None, deps=()):
    """Create a loose mod folder with a content.xml + given {vpath: text}."""
    root = ext / folder
    dep_xml = "".join(f'<dependency id="{d}"/>' for d in deps)
    _w(root / "content.xml",
       f'<content id="{mod_id or folder}" name="{folder}" version="1">{dep_xml}</content>')
    for vpath, text in files.items():
        _w(root / vpath, text)
    return root


def _setup_ref(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml",
       '<wares><ware id="ore"><price average="100"/></ware>'
       '<ware id="ice"><price average="50"/></ware></wares>')
    _w(ref / "aiscripts" / "order.fight.xml",
       '<aiscript name="order.fight"><actions><label name="start"/></actions></aiscript>')
    _w(ref / "assets" / "props" / "mat.xml", "<materials><m id='a'/></materials>")
    return _merge.Config(reference=ref)


def test_hard_two_mods_replace_same_node(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">200</replace></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">300</replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert len(hard) == 1
    assert hard[0].mods == ["a_mod", "b_mod"]
    assert hard[0].winner == "b_mod"  # loads last alphabetically


def test_sequential_replacement_only_contests_the_actual_new_contribution(tmp_path):
    cfg = _setup_ref(tmp_path)
    vpath = "md/missions.xml"
    _w(cfg.reference / vpath,
       '<root><actions><set_value name="$MissionCount" exact="1"/></actions></root>')
    ext = tmp_path / "extensions"
    _mod(ext, "a_feature", {vpath: '''<diff>
      <replace sel="(//set_value[@name='$MissionCount'])[1]">
        <set_value name="reset" exact="0"/>
        <set_value name="$MissionCount" exact="0"/>
        <do_if value="true"><set_value name="$MissionCount" exact="10"/></do_if>
      </replace></diff>'''})
    _mod(ext, "b_patch", {vpath: '''<diff>
      <replace sel="(//set_value[@name='$MissionCount'])[1]">
        <set_value name="$MissionCount" exact="7"/>
      </replace></diff>'''})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.skipped
    hard = rep.by_kind("HARD")
    assert len(hard) == 1, hard
    row = hard[0]
    assert row.mods == ["a_feature", "b_patch"] and row.winner == "b_patch"
    assert row.target_tree == "final" and row.target_path == "/root/actions/set_value[2]"
    assert not rep.by_kind("SUBTREE")
    live = _merge.build_effective(vpath, _merge.replace(
        cfg, overlays=(ext / "a_feature", ext / "b_patch"))).tree
    assert live.xpath(row.target_path + "/@exact") == ["7"]
    assert live.xpath("//do_if/set_value/@exact") == ["10"]


def test_sequential_instrument_disagreement_is_a_degraded_nonanswer(tmp_path, monkeypatch):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    for name in ("a", "b"):
        _mod(ext, name, {"libraries/wares.xml":
             '<diff><replace sel="//price/@average">2</replace></diff>'})
    monkeypatch.setattr(_compat._compat_state, "trace", lambda *a: ({}, {}, False))
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.collisions and any(s.degraded for s in rep.skipped)


def test_selector_invalidated_by_an_earlier_rename_is_not_a_live_winner(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    vpath = "assets/props/mat.xml"
    _mod(ext, "a", {vpath: '<diff><replace sel="//m[@id=\'a\']"><m id="renamed" value="1"/></replace></diff>'})
    _mod(ext, "b", {vpath: '<diff><replace sel="//m[@id=\'a\']"><m id="wrong" value="2"/></replace></diff>'})
    live = _merge.build_effective(vpath, _merge.replace(cfg, overlays=(ext / "a", ext / "b"))).tree
    assert live.xpath("//m/@id") == ["renamed"]
    report = _compat.analyze(ext, config=cfg)
    assert not report.by_kind("HARD"), report.collisions
    assert any("b" in item and "sel=" in item for item in report.unapplied)


def test_soft_two_mods_add_under_same_parent(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"aiscripts/order.fight.xml":
         '<diff><add sel="//actions"><label name="a_extra"/></add></diff>'})
    _mod(ext, "b_mod", {"aiscripts/order.fight.xml":
         '<diff><add sel="//actions"><label name="b_extra"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.hard
    assert len(rep.by_kind("SOFT")) == 1


def test_dup_add_same_key_escalates_to_hard(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    # Both add a label with the SAME name under the same parent -> duplicate.
    _mod(ext, "a_mod", {"aiscripts/order.fight.xml":
         '<diff><add sel="//actions"><label name="dup"/></add></diff>'})
    _mod(ext, "b_mod", {"aiscripts/order.fight.xml":
         '<diff><add sel="//actions"><label name="dup"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert len(rep.by_kind("HARD")) == 1
    assert "duplicate" in rep.by_kind("HARD")[0].detail


def test_union_key_two_mods_define_same_ware_id(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    # Full-file (non-diff) libraries/wares.xml from two mods, both defining ware 'newore'.
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<wares><ware id="newore"><price average="1"/></ware></wares>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<wares><ware id="newore"><price average="2"/></ware></wares>'})
    rep = _compat.analyze(ext, config=cfg)
    uk = rep.by_kind("UNION-KEY")
    assert len(uk) == 1
    assert "newore" in uk[0].target
    assert uk[0].winner == "b_mod"


def test_union_different_ids_no_collision(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<wares><ware id="ware_a"><price average="1"/></ware></wares>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<wares><ware id="ware_b"><price average="2"/></ware></wares>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.collisions  # distinct ids coexist in a union dir


def test_full_override_two_mods_same_asset(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"assets/props/mat.xml": "<materials><m id='x'/></materials>"})
    _mod(ext, "b_mod", {"assets/props/mat.xml": "<materials><m id='y'/></materials>"})
    rep = _compat.analyze(ext, config=cfg)
    fo = rep.by_kind("FULL-OVERRIDE")
    assert len(fo) == 1
    assert fo[0].winner == "b_mod"


def test_ui_xml_not_flagged_as_override(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"ui.xml": "<wm><add><ui/></add></wm>"})
    _mod(ext, "b_mod", {"ui.xml": "<wm><add><ui/></add></wm>"})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.by_kind("FULL-OVERRIDE")  # per-extension manifest, never overrides


def test_load_order_dependency_forces_earlier(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    # z_mod depends on a_mod's id -> but also they collide; dependency doesn't change
    # who-wins here (dep loads FIRST, so the dependent z_mod still wins as it loads later).
    _mod(ext, "z_first_alpha", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">1</replace></diff>'},
         mod_id="ZID")
    _mod(ext, "a_dependent", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">2</replace></diff>'},
         deps=["ZID"])
    rep = _compat.analyze(ext, config=cfg)
    order = rep.load_order
    # dependency ZID(z_first_alpha) must come before its dependent a_dependent,
    # overriding the alphabetical (a before z) default.
    assert order.index("z_first_alpha") < order.index("a_dependent")
    assert rep.by_kind("HARD")[0].winner == "a_dependent"


def test_candidate_mode_only_reports_candidate_collisions(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">1</replace></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ice\']/price/@average">1</replace></diff>'})
    # candidate collides only with a_mod (both touch 'ore')
    cand = _mod(ext, "cand_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">9</replace></diff>'})
    rep = _compat.analyze(ext, candidate=cand, config=cfg)
    assert all("cand_mod" in c.mods for c in rep.collisions)
    assert any(set(c.mods) == {"a_mod", "cand_mod"} for c in rep.by_kind("HARD"))
    assert not any("b_mod" in c.mods for c in rep.collisions)


# --- cross-mod NESTED patches (audit 2026-08-01, finding F10) ------------------
#
# A mod that exists purely to patch ANOTHER mod ships extensions/<target>/<rel>
# while the target itself ships <rel>. Keyed on the raw vpath those never meet,
# so x4compat reported "0 shared files examined … no collisions", exit 0 — blind
# to the most collision-prone construct in X4 modding.

def test_nested_patch_collides_with_its_target(tmp_path, case_insensitive_fs):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "target_mod", {"md/Thing.xml": '<mdscript name="Thing"><cues/></mdscript>'})
    _mod(ext, "zzz_patcher", {"extensions/target_mod/md/thing.xml":
         '<diff><add sel="//cues"><cue name="Extra"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert rep.files_examined >= 1, "the patch and its target must share a file"


def test_nested_patch_matches_target_case_insensitively(tmp_path, case_insensitive_fs):
    """The live case differed in BOTH prefix and case: the overlay shipped
    .../md/morerooms.xml while moreroomsforships ships md/MoreRooms.xml."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "Target_Mod", {"md/MixedCase.xml": '<mdscript name="M"><cues/></mdscript>'})
    _mod(ext, "zzz_patcher", {"extensions/target_mod/md/mixedcase.xml":
         '<diff><add sel="//cues"><cue name="Extra"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert rep.files_examined >= 1


def test_two_nested_patches_on_one_target_still_collide(tmp_path, case_insensitive_fs):
    """The alias must not lose the ordinary patcher-vs-patcher HARD case.

    The owner must be reachable through the config, exactly as it is in a real
    run: `build_effective` resolves extensions/<owner>/<rel> by finding <owner>
    among the overlay dirs. Without that the base tree is None and _analyze_vpath
    reports nothing at all — see the note on that branch.
    """
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    target = _mod(ext, "target_mod", {"md/Thing.xml":
                  '<mdscript name="Thing"><cues><cue name="C"/></cues></mdscript>'})
    for p in ("y_patch", "z_patch"):
        _mod(ext, p, {"extensions/target_mod/md/thing.xml":
             '<diff><replace sel="//cue/@name">' + p + '</replace></diff>'})
    cfg = _merge.replace(cfg, overlays=[target])
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert hard and hard[0].winner == "z_patch"


def test_unrelated_mods_are_not_aliased_together(tmp_path):
    """Both sides asserted: the alias must not invent shared files."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"md/OwnA.xml": "<mdscript name='A'/>"})
    _mod(ext, "b_mod", {"md/OwnB.xml": "<mdscript name='B'/>"})
    rep = _compat.analyze(ext, config=cfg)
    assert rep.files_examined == 0


# --- F17: the owner mod must be found without the caller staging it ------------

def test_nested_patch_collides_without_a_manually_staged_owner(tmp_path, case_insensitive_fs):
    """The F17 regression test.

    `test_two_nested_patches_on_one_target_still_collide` above only passes
    because it hand-stages the owner via `_merge.replace(cfg, overlays=[target])`.
    A real x4compat run cannot do that: `analyze()` is handed one Config for the
    whole sweep and the owner differs per vpath. So `_build_owned` never found the
    owner, the base tree was None, and every mod on the file was dropped by a bare
    `continue` — measured at 140 of 523 examined files on the live 101-mod install,
    silently discarding 144 <diff> mods.

    Same fixture, WITHOUT the staging line. It must still find the collision.
    """
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "target_mod", {"md/Thing.xml":
         '<mdscript name="Thing"><cues><cue name="C"/></cues></mdscript>'})
    for p in ("y_patch", "z_patch"):
        _mod(ext, p, {"extensions/target_mod/md/thing.xml":
             '<diff><replace sel="//cue/@name">' + p + '</replace></diff>'})

    rep = _compat.analyze(ext, config=cfg)          # <- no overlays= staging

    hard = rep.by_kind("HARD")
    assert hard, f"nested collision missed; skipped={[s.why for s in rep.skipped]}"
    assert hard[0].winner == "z_patch"
    assert not rep.degraded, "a resolvable file must not be reported as degraded"


def test_owner_overlay_does_not_apply_the_other_patchers(tmp_path, case_insensitive_fs):
    """The base must stay UNPATCHED, or one mod's <remove> hides another's target.

    y_patch removes the very cue z_patch replaces. Both must still be seen to
    resolve against the same pristine owner tree and collide; if the fix folded
    every patcher into the base, z_patch's sel would match nothing and the
    collision would vanish — a false clean.

    Both ops target the cue ELEMENT deliberately. An earlier draft had z_patch
    target `.../@name`, which does not collide here — `_canonical` keys an
    attribute as `<elem path>/@name`, a different string from the element's own
    path, so an element-vs-its-own-attribute overlap is invisible to the detector.
    That is a real gap (logged as F18) but a separate one; pulling it into this
    test would have hidden the thing this test exists to prove.
    """
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "target_mod", {"md/Thing.xml":
         '<mdscript name="Thing"><cues><cue name="C"/></cues></mdscript>'})
    _mod(ext, "y_patch", {"extensions/target_mod/md/thing.xml":
         '<diff><remove sel="//cue[@name=\'C\']"/></diff>'})
    _mod(ext, "z_patch", {"extensions/target_mod/md/thing.xml":
         '<diff><replace sel="//cue[@name=\'C\']"><cue name="zz"/></replace></diff>'})

    rep = _compat.analyze(ext, config=cfg)

    hard = rep.by_kind("HARD")
    assert hard, "a remove-vs-replace on one node is the canonical HARD case"
    assert set(hard[0].mods) == {"y_patch", "z_patch"}


# --- F17: a file that yields no comparison must SAY so -------------------------

def test_missing_owner_is_a_degraded_skip_not_silence(tmp_path):
    """No owner installed => no base => no comparison. That must not read as OK."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    for p in ("y_patch", "z_patch"):
        _mod(ext, p, {"extensions/ghost_mod/md/thing.xml":
             '<diff><replace sel="//cue[@name=\'C\']/@name">' + p + '</replace></diff>'})

    rep = _compat.analyze(ext, config=cfg)

    assert not rep.collisions
    assert rep.degraded, "silently contributing nothing is the F4 failure mode"
    assert "ghost_mod" in rep.degraded[0].why
    assert "not installed" in rep.degraded[0].why


def test_no_base_reason_is_computed_not_guessed(tmp_path):
    """An owner that ships its own <diff> is a DIFFERENT cause from a missing one.

    A draft of this fix hard-coded "the owning mod is not installed" for every
    nested path. A wrong reason is worse than none — it is what stops the next
    person checking (the F1 lesson).
    """
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "target_mod", {"md/thing.xml":
         '<diff><replace sel="//x/@y">1</replace></diff>'})   # a patch, not a base
    _mod(ext, "z_patch", {"extensions/target_mod/md/thing.xml":
         '<diff><replace sel="//cue/@name">z</replace></diff>'})

    rep = _compat.analyze(ext, config=cfg)

    assert rep.degraded
    why = rep.degraded[0].why
    assert "<diff> itself" in why, why
    assert "not installed" not in why, "target_mod IS installed — do not guess"


def test_clean_run_reports_no_skips(tmp_path):
    """The channel must stay quiet when everything really was compared."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">200</replace></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">300</replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD")
    assert rep.skipped == []


def test_cli_exits_3_when_degraded_without_hard_collisions(tmp_path, capsys):
    """Exit-code contract, mirrored from x4validate: 1 findings > 3 degraded > 0."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    for p in ("y_patch", "z_patch"):
        _mod(ext, p, {"extensions/ghost_mod/md/thing.xml":
             '<diff><replace sel="//cue/@name">' + p + '</replace></diff>'})

    code = _compat.main(["check", "--ext-dir", str(ext),
                         "--reference", str(cfg.reference)])

    assert code == 3, "a run that compared nothing must not exit 0"
    out = capsys.readouterr().out
    assert "NOT ANALYSED" in out
    assert "DEGRADED" in out


def test_json_output_includes_unresolvable_ops(tmp_path, capsys):
    """Finding 6: a malformed sel= makes a mod's op contribute NOTHING to collision
    detection (`unresolvable`, the text render's own NOT CHECKED disclosure). The
    --json payload must carry the same fact, not report a clean-looking run that
    silently checked less than it claims."""
    import json
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    # A file only ONE mod touches is never examined at all (nothing to collide
    # with), so a second, ordinary mod on the same file is needed to reach the
    # sel-resolution pass that records `unresolvable`.
    _mod(ext, "a_bad", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=">bad</replace></diff>'})
    _mod(ext, "z_other", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ice\']/price/@average">7</replace></diff>'})
    code = _compat.main(["check", "--ext-dir", str(ext),
                         "--reference", str(cfg.reference), "--json"])
    assert code in (0, 1, 3), code
    payload = json.loads(capsys.readouterr().out)
    assert payload["unresolvable"], payload
    assert any("a_bad" in u for u in payload["unresolvable"]), payload["unresolvable"]


# --------------------------------------------------------------------------
# F12 (2026-08-02): registry uniqueness is per-DOCUMENT, and identity is @id.
# Both engine-confirmed duplicates (WareDB shield_xen_xl_standard_02_mk1,
# GroupDB yak_destroyer_l) shaped these rules; mod-vs-base duplication was
# measured (476 tolerated instances incl. VRO/SVE) and deliberately DROPPED.
# --------------------------------------------------------------------------

def test_added_child_keys_prefer_id_over_name():
    """A ware's name= is a localized {page,t} TEXT REFERENCE, not identity.
    Keying by name hid the engine-confirmed shield duplicate and compared
    unrelated jobs by display name."""
    from lxml import etree
    op = etree.fromstring('<add sel="/wares">'
                          '<ware id="shield_x" name="{20204,1}"/></add>')
    assert _compat._added_child_keys(op) == ["ware#shield_x"]


def test_docwide_duplicate_add_different_anchors_is_union_key(tmp_path):
    """The shield_xen_xl_standard_02_mk1 shape: two mods add the same ware id
    anchored at DIFFERENT siblings — same-anchor HARD never compares them, and
    before 2026-08-02 nothing else did either."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ore\']" pos="after">'
         '<ware id="dupware" name="{20204,9}"/></add></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ice\']" pos="after">'
         '<ware id="dupware" name="{20204,9}"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    uk = [c for c in rep.by_kind("UNION-KEY") if c.target == "ware#dupware"]
    assert len(uk) == 1 and set(uk[0].mods) == {"a_mod", "b_mod"}


def test_same_anchor_duplicate_stays_hard_only(tmp_path):
    """One fact, one finding: a same-anchor duplicate is already HARD, so the
    document-wide pass must not report it a second time as UNION-KEY."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    body = ('<diff><add sel="/wares"><ware id="dupware"/></add></diff>')
    _mod(ext, "a_mod", {"libraries/wares.xml": body})
    _mod(ext, "b_mod", {"libraries/wares.xml": body})
    rep = _compat.analyze(ext, config=cfg)
    assert [c for c in rep.by_kind("HARD") if "dupware" in c.detail]
    assert not [c for c in rep.by_kind("UNION-KEY") if "dupware" in c.target]


def _docwide_guard_world(tmp_path, guard):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><add sel="/wares"><ware id="upgrade_*"/></add>'
         f'<add sel="//ware[@id=\'ore\']" pos="after" if="{guard}">'
         '<ware id="guarded_ware"/></add></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ice\']" pos="after"><ware id="upgrade_*"/></add>'
         f'<add sel="/wares" if="{guard}">'
         '<ware id="guarded_ware"/></add></diff>'})
    return _compat.analyze(ext, config=cfg)


def test_wildcard_adds_do_not_join_the_docwide_pass_and_guarded_ones_follow_their_guard(
        tmp_path):
    """icon#upgrade_* style wildcard entries are the generic-icon idiom (32 of
    37 measured document-wide duplicates, zero engine complaints).

    CHANGED for issue #8 (user-approved fold-in): an if=-guarded add used to be
    excluded outright as "a designed conditional", so two mods whose guards were both
    TRUE -- here `not(//ware[@id='g'])`, true at both positions -- adding the same
    ware were a false negative: both adds apply and the engine sees the duplicate. A
    guarded add now joins exactly when its guard is not FALSE at its load position,
    and the row says the answer depends on this load order. The FALSE twin is below."""
    rep = _docwide_guard_world(tmp_path, "not(//ware[@id='g'])")
    assert not [c for c in rep.by_kind("UNION-KEY") if "upgrade" in c.target]
    uk = [c for c in rep.by_kind("UNION-KEY") if c.target == "ware#guarded_ware"]
    assert len(uk) == 1 and uk[0].mods == ["a_mod", "b_mod"], rep.collisions
    assert "TRUE at its load position" in uk[0].detail


def test_TWIN_guarded_adds_whose_guard_is_FALSE_stay_out_of_the_docwide_pass(tmp_path):
    rep = _docwide_guard_world(tmp_path, "//ware[@id='g']")
    assert not [c for c in rep.by_kind("UNION-KEY")
                if "upgrade" in c.target or "guarded" in c.target], rep.collisions
    assert sorted(g.mod for g in rep.guarded_off) == ["a_mod", "b_mod"]


# --------------------------------------------------------------------------
# F18 (2026-08-02): a later mod replace/removing an ELEMENT wipes an earlier
# mod's change INSIDE it — _canonical gave the two unrelated ids. Order-aware:
# 184 measured pair-hits -> 165 real clobbers; the filter correctly cleared
# ebi_m0_vro, whose ws_ dependency on VRO orders it after the wipe.
# --------------------------------------------------------------------------

def test_later_element_replace_wiping_earlier_inner_edit_is_subtree(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">7</replace></diff>'})
    _mod(ext, "z_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']">'
         '<ware id="ore"><price average="1"/></ware></replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    st = rep.by_kind("SUBTREE")
    assert len(st) == 1
    assert st[0].mods == ["a_mod", "z_mod"]
    # The wiper goes in its OWN field, and `winner` is deliberately empty: for a
    # SUBTREE the load-order winner is the mod that WIPED, not the owner of the
    # final value, and a later mod can re-supply what was wiped (MEASURED: 3 of
    # 148 on the live install). Reporting the wiper as `winner` is what made an
    # inbound report conclude x4compat was wrong. Same precedent as NAME-CLASH.
    assert st[0].wiped_by == "z_mod", "the wiper must still be reported"
    assert st[0].winner == "", "a SUBTREE has no live-value winner"
    assert st[0].live_value_owner() is None, "and must refuse to name one"
    assert st[0] in rep.hard, "SUBTREE gates hard-ish by user decision"
    assert "advisory" in st[0].detail, "the load-order caveat must ride every row"


def test_element_replace_loading_before_the_inner_edit_is_not_subtree(tmp_path):
    """A wipe that loads FIRST merely changes what the victim's sel sees —
    the Tier B sel check covers that; nothing is destroyed."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']">'
         '<ware id="ore"><price average="1"/></ware></replace></diff>'})
    _mod(ext, "z_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">7</replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.by_kind("SUBTREE")


def test_add_on_the_ancestor_does_not_wipe(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">7</replace></diff>'})
    _mod(ext, "z_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ore\']"><production time="1"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not rep.by_kind("SUBTREE")


def test_textref_keys_never_join_the_docwide_pass(tmp_path):
    """A {page,text} key is localized display text (a <production>'s name=),
    not identity — keying on it matched production stages of UNRELATED wares."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ore\']"><production name="{20206,601}"/></add></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><add sel="//ware[@id=\'ice\']"><production name="{20206,601}"/></add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert not [c for c in rep.by_kind("UNION-KEY") if "20206" in c.target]


def test_union_key_row_survives_when_it_names_mods_the_hard_row_missed(tmp_path):
    """Subset-fold semantics: a same-anchor HARD duplicate between a and b must
    not swallow the fact that FULL-FILE shipper c also defines the key — the
    real-world case is icon#upgrade_*, where the full-file shippers are
    entirely different mods from the same-anchor adders."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    body = '<diff><add sel="/wares"><ware id="dupware"/></add></diff>'
    _mod(ext, "a_mod", {"libraries/wares.xml": body})
    _mod(ext, "b_mod", {"libraries/wares.xml": body})
    _mod(ext, "c_mod", {"libraries/wares.xml":
         '<wares><ware id="dupware"/></wares>'})   # full union file
    rep = _compat.analyze(ext, config=cfg)
    assert [c for c in rep.by_kind("HARD") if "dupware" in c.detail]
    uk = [c for c in rep.by_kind("UNION-KEY") if c.target == "ware#dupware"]
    assert len(uk) == 1 and "c_mod" in uk[0].mods


def test_by_kind_order_is_stable():
    """Two identical runs must emit collisions in the same order.

    An upstream `for k in <set>` leaked hash-randomized iteration order into the
    report, so two identical runs produced the same 419 collisions in different
    orders. Content was never wrong — but a baseline diff became noise, which is
    how a real change hides.
    """
    import random
    from x4validate._compat import Collision, CompatReport

    rows = [Collision(vpath=f"libraries/f{i % 3}.xml", kind="UNION-KEY",
                      target=f"entry#e{i}", mods=[f"mod_{i%2}", f"mod_{(i+1)%2}"],
                      winner=f"mod_{i%2}")
            for i in range(50)]

    first = None
    for _ in range(5):
        shuffled = rows[:]
        random.shuffle(shuffled)              # simulate a different discovery order
        rep = CompatReport(collisions=shuffled)
        order = [(c.vpath, c.target, tuple(c.mods)) for c in rep.by_kind("UNION-KEY")]
        if first is None:
            first = order
        assert order == first, "by_kind order depends on insertion order"


# --------------------------------------------------------------------------
# NAME-CLASH: the same macro NAME defined by two mods in DIFFERENT files.
#
# Structurally invisible to the per-vpath scan (the two mods never share a
# path), yet X4 resolves macros by NAME via index/macros.xml, so only one
# definition is ever loaded and the other is dead content the author cannot
# tell is dead. Found 2026-08-09 re-deriving the missile roster:
# `missile_flagship_light_mk1_macro` is defined by two mods at different paths
# and the effective index points at only one of them.
# --------------------------------------------------------------------------

_MACROS = ('<macros><macro name="ship_probe_macro" class="ship_s">'
           "<properties/></macro></macros>")


def _clashes(report):
    return [c for c in report.collisions if c.kind == "NAME-CLASH"]


def test_same_macro_name_in_different_files_is_reported(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "amod", {"assets/units/size_s/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "bmod", {"assets/units/other/macros/ship_probe_macro.xml": _MACROS})
    rep = _compat.analyze(ext, config=cfg)
    cl = _clashes(rep)
    assert len(cl) == 1, [c.target for c in cl]
    assert cl[0].target == "ship_probe_macro"
    assert sorted(cl[0].mods) == ["amod", "bmod"]
    # index/macros.xml decides, NOT load order -- so no winner may be claimed.
    assert cl[0].winner == ""
    assert "index/macros.xml" in cl[0].detail


def test_same_macro_name_in_the_SAME_file_is_not_a_name_clash(tmp_path):
    """That is a plain file-level collision; reporting it twice is noise."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    same = "assets/units/size_s/macros/ship_probe_macro.xml"
    _mod(ext, "amod", {same: _MACROS})
    _mod(ext, "bmod", {same: _MACROS})
    assert _clashes(_compat.analyze(ext, config=cfg)) == []


def test_nested_cross_mod_patch_is_the_same_logical_file(tmp_path):
    """`extensions/<owner>/<rel>` is a patch INTO <owner>'s file, not a rival
    definition. Measured: without this, 35 of 57 reported rows were this case."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    rel = "assets/units/size_s/macros/ship_probe_macro.xml"
    _mod(ext, "amod", {rel: _MACROS})
    _mod(ext, "bmod", {f"extensions/amod/{rel}": _MACROS})
    assert _clashes(_compat.analyze(ext, config=cfg)) == []


def test_a_macro_defined_inside_a_diff_still_counts(tmp_path):
    """`<replace sel="//macros">` with a fresh payload is the standard whole-file
    override idiom. Skipping all <diff> roots missed the very case this check was
    built for, because that is exactly how the overhaul mod ships its macros."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "amod", {"assets/units/size_s/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "bmod", {"assets/units/other/macros/ship_probe_macro.xml":
                       f'<diff><replace sel="//macros">{_MACROS}</replace></diff>'})
    cl = _clashes(_compat.analyze(ext, config=cfg))
    assert len(cl) == 1 and cl[0].target == "ship_probe_macro"


def test_a_diff_that_defines_no_macro_is_not_a_definition(tmp_path):
    """A bare attribute tweak names no macro and must not count as defining one."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "amod", {"assets/units/size_s/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "bmod", {"assets/units/other/macros/ship_probe_macro.xml":
                       '<diff><replace sel="//macro/@class">ship_m</replace></diff>'})
    assert _clashes(_compat.analyze(ext, config=cfg)) == []


# --- the precondition guard itself ------------------------------------------

def test_case_probe_detects_this_filesystem(tmp_path):
    """The probe must return a real answer, and both branches must be reachable.

    Five tests above stage `md/Thing.xml` and patch `md/thing.xml`. That is one
    file under X4's Windows VFS and two on a case-sensitive filesystem, so on
    Linux they were failing for a reason that says nothing about the code. They
    now declare the precondition instead — but a guard nobody can make flip is
    not a guard, so both outcomes are exercised here.

    Asserting `is True` was wrong, and contradicted the paragraph above it: on
    Linux the honest answer is False, and demanding True made this guard the only
    thing in the file that FAILED there rather than skipped. What actually needs
    pinning is that the probe reports the truth about whatever filesystem it is
    handed — so it is checked against an INDEPENDENT mechanism. The probe asks
    "does the other casing exist?"; `os.path.samefile` asks "are these the same
    file?" — different syscalls, so agreement is evidence rather than tautology.
    """
    import os.path

    from conftest import fs_is_case_insensitive

    probe = fs_is_case_insensitive(tmp_path)
    assert isinstance(probe, bool), "the probe must give a real answer, not None"

    original = tmp_path / "SameFileProbe.tmp"
    original.write_text("x", encoding="utf-8")
    other_casing = tmp_path / "samefileprobe.tmp"
    try:
        independently_same = os.path.samefile(original, other_casing)
    except (FileNotFoundError, OSError):
        independently_same = False

    assert probe == independently_same, (
        f"the case probe says folds={probe} but os.path.samefile says "
        f"{independently_same}; one of them is lying about this filesystem, and "
        f"five nested-patch tests decide whether to run based on the first")


def test_case_probe_reports_false_on_a_case_sensitive_filesystem(tmp_path, monkeypatch):
    """Simulate the Linux answer — the branch this machine can never take."""
    from pathlib import Path

    import conftest
    real_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists",
        lambda self: False if self.name == "caseprobe.tmp" else real_exists(self))
    assert conftest.fs_is_case_insensitive(tmp_path) is False


# --- live_value_owner must REFUSE for the kinds it cannot know (survivor) -----
#
# Narrowing the guard to `("SOFT",)` survived the whole suite, and it is exactly the
# conflation CLAUDE.md #18 exists to prevent: for SUBTREE, `winner` is the mod that did
# the WIPING -- not the owner of the final value, because a later mod can re-supply it
# (MEASURED: 3 of 148 SUBTREE rows, 2.0%) -- and for NAME-CLASH nothing in load order
# decides at all, index/macros.xml does. An inbound report once called x4compat "wrong"
# by reading `winner` as the live owner; x4compat was right, the reading was not.


def _collision(kind, winner="modB"):
    from x4validate._compat import Collision
    return Collision(vpath="libraries/wares.xml", kind=kind, target="t",
                     mods=["modA", "modB"], winner=winner)


def test_live_value_owner_is_NONE_for_the_kinds_it_cannot_know():
    for kind in ("SUBTREE", "NAME-CLASH", "SOFT"):
        assert _collision(kind).live_value_owner() is None, (
            f"{kind} named a live owner it has no way to know")


def test_live_value_owner_DOES_name_one_when_it_can():
    """The twin. A method that returned None for everything would pass the test
    above while destroying the answer for the three kinds that do have one."""
    for kind in ("FULL-OVERRIDE", "HARD", "UNION-KEY"):
        assert _collision(kind).live_value_owner() == "modB", kind


# --- AN-5 twins: a REMOVE decides the node only when it loads FIRST -----------
#
# tests/test_audit0924_analysis.py pins the defect (an earlier remove, a later
# replace named the winner). These pin the other clause of the new branch: a remove
# that loads LAST is the ordinary "last loader wins", and the detail must say what
# happened rather than "loads last and wins" when the removal decided it.


def test_a_remove_that_loads_LAST_is_the_ordinary_last_loader(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">200</replace></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><remove sel="//ware[@id=\'ore\']/price/@average"/></diff>'})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod"
    assert "loads last and wins" in hard[0].detail


def test_an_earlier_remove_is_NAMED_as_what_is_live(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"libraries/wares.xml":
         '<diff><remove sel="//ware[@id=\'ore\']/price/@average"/></diff>'})
    _mod(ext, "b_mod", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">300</replace></diff>'})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1 and hard[0].live_value_owner() == "a_mod"
    assert "match nothing" in hard[0].detail and "'b_mod'" in hard[0].detail


# --- AN-10: a selector that only a LATER mod's add can satisfy -----------------
#
# Every mod's sel= resolves against the UNPATCHED base, so a patch on a node that
# another mod ADDS matched nothing there and contributed no target: x4compat said
# "No collisions" while the engine -- which applies the patch BEFORE the later mod
# adds the node -- skips it. It must be disclosed, never silent.

_ADDS_ZWARE = ('<diff><add sel="/wares"><ware id="zware"><price average="100"/></ware>'
               '</add></diff>')
_PATCHES_ZWARE = ('<diff><replace sel="//ware[@id=\'zware\']/price/@average">5</replace>'
                  '</diff>')


def test_a_patch_on_a_node_a_LATER_mod_adds_is_disclosed(tmp_path, capsys):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    _mod(ext, "z_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    rep = _compat.analyze(ext, config=cfg)
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "z_adder")]
    out = _compat.render(rep)
    assert "a_patcher" in out and "z_adder" in out
    assert not any(ln.startswith("No HARD") and "see" not in ln for ln in out.splitlines()), out


def test_a_patch_on_a_node_an_EARLIER_mod_adds_is_not_a_miss(tmp_path):
    """The twin: the engine has the node by then, so nothing is skipped."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    _mod(ext, "z_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    assert _compat.analyze(ext, config=cfg).order_misses == []


def test_a_patch_on_a_node_its_OWN_mod_adds_is_not_a_miss(tmp_path):
    """The other twin: a mod patching what it adds itself depends on no one."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_self", {"libraries/wares.xml":
         _ADDS_ZWARE.replace("</diff>", "") + _PATCHES_ZWARE.replace("<diff>", "")})
    _mod(ext, "z_other", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ore\']/price/@average">1</replace></diff>'})
    assert _compat.analyze(ext, config=cfg).order_misses == []


_REMOVES_ZWARE = '<diff><remove sel="//ware[@id=\'zware\']"/></diff>'


def test_an_added_then_REMOVED_node_still_names_the_order_miss(tmp_path):
    """Finding 3: `_order_misses` filtered pending ops against the FINISHED tree
    first. A node a later mod adds and a still-later one removes again is absent
    from that finished tree, so the op read as a dead selector and the order miss
    was dropped silently -- even though the op matched for real, briefly, between
    the add and the remove, exactly as the engine would apply it. Load order is
    forced with dependencies -- the engine-measured order is not alphabetical."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    _mod(ext, "z_adder", {"libraries/wares.xml": _ADDS_ZWARE}, deps=["a_patcher"])
    _mod(ext, "zz_remover", {"libraries/wares.xml": _REMOVES_ZWARE}, deps=["z_adder"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.load_order == ["a_patcher", "z_adder", "zz_remover"], rep.load_order
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "z_adder")], \
        rep.order_misses


def test_the_rebuild_FALLBACK_still_shares_the_masking_limit(tmp_path, monkeypatch):
    """Disclosed, not fixed: `_order_misses_rebuild`'s own initial filter (one tree
    of every mod, i.e. the finished tree) has the identical shape and was not
    touched by finding 3's fix, which is scoped to the fast pass only -- rebinding
    the bisection to a non-monotonic predicate (a match can go away again) is a
    materially different, riskier change to a fallback path this finding did not
    name. Forced onto the fallback the same way
    test_a_drifted_incremental_pass_falls_back_to_the_rebuild does, so this is a
    PROVEN gap, not an assumed one."""
    import types
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    _mod(ext, "z_adder", {"libraries/wares.xml": _ADDS_ZWARE}, deps=["a_patcher"])
    # An unrelated edit so the drifted (no-op apply_overlay) tree and the real
    # merged tree diverge regardless of zware's fate -- in THIS fixture alone,
    # zware ends up absent both when never added (drifted) and when added then
    # removed (real), which would make the self-check pass by coincidence.
    _mod(ext, "y_other", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'ice\']/price/@average">42</replace></diff>'},
         deps=["z_adder"])
    _mod(ext, "zz_remover", {"libraries/wares.xml": _REMOVES_ZWARE}, deps=["y_other"])
    used = []
    real_rebuild = _compat._order_misses_rebuild
    monkeypatch.setattr(_compat, "_order_misses_rebuild",
                        lambda *a, **k: used.append(1) or real_rebuild(*a, **k))
    drifted = types.SimpleNamespace(**{k: getattr(_merge, k) for k in dir(_merge)
                                       if not k.startswith("__")})
    drifted.apply_overlay = lambda tree, *a, **k: (tree, "diff")
    monkeypatch.setattr(_compat, "_merge", drifted)
    rep = _compat.analyze(ext, config=cfg)
    assert used, "the drifted pass was trusted: the self-check never fired"
    assert rep.order_misses == [], rep.order_misses  # the fallback's known, disclosed gap


def test_the_adder_is_NAMED_among_several_later_mods(tmp_path):
    """Exercises the bisection: the adder is neither the first nor the last later mod,
    and the others on the file touch something else."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    other = '<diff><replace sel="//ware[@id=\'ice\']/price/@average">{v}</replace></diff>'
    _mod(ext, "a_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    _mod(ext, "b_other", {"libraries/wares.xml": other.format(v=1)})
    _mod(ext, "c_other", {"libraries/wares.xml": other.format(v=2)})
    _mod(ext, "d_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    _mod(ext, "e_other", {"libraries/wares.xml": other.format(v=3)})
    rep = _compat.analyze(ext, config=cfg)
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "d_adder")]
    # candidate mode keeps it only when the candidate is one of the two mods
    assert _compat.analyze(ext, candidate=ext / "d_adder", config=cfg).order_misses
    assert not _compat.analyze(ext, candidate=ext / "c_other", config=cfg).order_misses


# --- AN-10 perf: the incremental pass is CHECKED against `_merge`, not trusted ----

def _an10_world(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    other = '<diff><replace sel="//ware[@id=\'ice\']/price/@average">{v}</replace></diff>'
    _mod(ext, "a_patcher", {"libraries/wares.xml": _PATCHES_ZWARE})
    _mod(ext, "b_other", {"libraries/wares.xml": other.format(v=1)})
    _mod(ext, "d_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    return cfg, ext


def test_a_drifted_incremental_pass_falls_back_to_the_rebuild(tmp_path, monkeypatch):
    """If the mirrored per-overlay loop ever disagrees with build_effective, the
    self-check must notice and the rebuild form must answer -- same finding."""
    import types
    cfg, ext = _an10_world(tmp_path)
    used = []
    real_rebuild = _compat._order_misses_rebuild
    monkeypatch.setattr(_compat, "_order_misses_rebuild",
                        lambda *a, **k: used.append(1) or real_rebuild(*a, **k))
    # A _merge whose apply_overlay silently does nothing -- ONLY as _compat sees it.
    drifted = types.SimpleNamespace(**{k: getattr(_merge, k) for k in dir(_merge)
                                       if not k.startswith("__")})
    drifted.apply_overlay = lambda tree, *a, **k: (tree, "diff")
    monkeypatch.setattr(_compat, "_merge", drifted)
    rep = _compat.analyze(ext, config=cfg)
    assert used, "the drifted pass was trusted: the self-check never fired"
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "d_adder")]


def test_the_incremental_pass_needs_no_fallback_when_it_agrees(tmp_path, monkeypatch):
    """The twin: on an ordinary file the fast path answers by itself."""
    cfg, ext = _an10_world(tmp_path)
    monkeypatch.setattr(_compat, "_order_misses_rebuild",
                        lambda *a, **k: pytest.fail("fell back on an agreeing pass"))
    rep = _compat.analyze(ext, config=cfg)
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "d_adder")]


# --- review of AN-5: the removal is live only while NOTHING re-supplies the node ---

_RM_ORE = '<diff><remove sel="//ware[@id=\'ore\']"/></diff>'
_READD_ORE = ('<diff><add sel="/wares"><ware id="ore"><price average="55"/></ware>'
              '</add></diff>')
_REPLACE_ORE = ('<diff><replace sel="//ware[@id=\'ore\']"><ware id="ore">'
                '<price average="7"/></ware></replace></diff>')
_EDIT_IN_ORE = '<diff><replace sel="//ware[@id=\'ore\']/price/@average">9</replace></diff>'


def test_a_node_RE_ADDED_after_its_removal_is_won_by_the_later_writer(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {"libraries/wares.xml": _RM_ORE})
    _mod(ext, "b_readd", {"libraries/wares.xml": _READD_ORE})
    _mod(ext, "c_replace", {"libraries/wares.xml": _REPLACE_ORE})
    live = _merge.build_effective("libraries/wares.xml", _merge.replace(
        cfg, overlays=(ext / "a_rm", ext / "b_readd", ext / "c_replace"))).tree
    assert live.find("ware[@id='ore']/price").get("average") == "7"   # control
    hard = [c for c in _compat.analyze(ext, config=cfg).by_kind("HARD")
            if set(c.mods) == {"a_rm", "c_replace"}]
    assert len(hard) == 1, hard
    assert hard[0].live_value_owner() == "c_replace" and not hard[0].removed_by, hard[0]


def test_an_edit_INSIDE_a_node_an_earlier_mod_removed_is_disclosed(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {"libraries/wares.xml": _RM_ORE})
    _mod(ext, "b_edit", {"libraries/wares.xml": _EDIT_IN_ORE})
    rep = _compat.analyze(ext, config=cfg)
    assert [(m.mod, m.removed_by) for m in rep.removed_first] == [("b_edit", "a_rm")]
    assert "b_edit" in _compat.render(rep) and "a_rm" in _compat.render(rep)


def test_an_edit_inside_a_removed_node_that_was_RE_ADDED_is_not_disclosed(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {"libraries/wares.xml": _RM_ORE})
    _mod(ext, "b_readd", {"libraries/wares.xml": _READD_ORE})
    _mod(ext, "c_edit", {"libraries/wares.xml": _EDIT_IN_ORE})
    assert _compat.analyze(ext, config=cfg).removed_first == []


def test_a_GUARDED_order_miss_is_marked_as_intentional(tmp_path):
    """`if=` / `silent` ops are the optional-compat idiom: disclosed as guarded, not
    read as a mistake -- while the unguarded twin stays unmarked."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml":
         '<diff><replace sel="//ware[@id=\'zware\']/price/@average" silent="true">5'
         '</replace><replace sel="//ware[@id=\'zware\']/@id" if="//ware[@id=\'zware\']">'
         'zz</replace><remove sel="//ware[@id=\'zware\']/price/@min"/></diff>'})
    _mod(ext, "z_adder", {"libraries/wares.xml":
         '<diff><add sel="/wares"><ware id="zware"><price min="1" average="100"/></ware>'
         '</add></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    got = sorted((m.sel.rsplit("/", 1)[-1], bool(m.guard)) for m in rep.order_misses)
    assert got == [("@average", True), ("@id", True), ("@min", False)], got
    out = _compat.render(rep)
    assert out.count("guarded") >= 2, out


# --- _resolve_candidate: a bare NAME always means the installed copy (finding 2) ------

def test_bare_name_resolves_to_the_INSTALLED_copy_even_when_cwd_has_a_same_named_dir(
        tmp_path, monkeypatch):
    """AN-1's own docstring promises 'a bare NAME means the copy in the extensions
    dir'. p.exists() used to be tried BEFORE that lookup, so a bare name that also
    happened to exist relative to cwd silently resolved to that OTHER copy instead."""
    ext = tmp_path / "extensions"
    _mod(ext, "m_cand", {"libraries/wares.xml": "<diff/>"})
    staging = tmp_path / "staging"
    _mod(staging, "m_cand", {"libraries/wares.xml": "<diff/>"})
    monkeypatch.chdir(staging)
    resolved = _compat._resolve_candidate("m_cand", ext)
    assert resolved.resolve() == (ext / "m_cand").resolve(), resolved


def test_an_explicit_relative_path_STILL_wins_over_the_installed_copy(tmp_path, monkeypatch):
    """Twin: naming the staged copy explicitly (a real path, not a bare name) must
    still mean that copy -- the fix must not make every candidate mean 'installed'."""
    ext = tmp_path / "extensions"
    _mod(ext, "m_cand", {"libraries/wares.xml": "<diff/>"})
    staging = tmp_path / "staging"
    _mod(staging, "m_cand", {"libraries/wares.xml": "<diff/>"})
    monkeypatch.chdir(staging)
    resolved = _compat._resolve_candidate("./m_cand", ext)
    assert resolved.resolve() == (staging / "m_cand").resolve(), resolved


def test_bare_name_lookup_is_still_case_insensitive(tmp_path):
    ext = tmp_path / "extensions"
    _mod(ext, "m_cand", {"libraries/wares.xml": "<diff/>"})
    resolved = _compat._resolve_candidate("M_CAND", ext)
    assert resolved.resolve() == (ext / "m_cand").resolve(), resolved


# --- issue #4: a duplicate-add row names the KEY's providers, not every co-adder ------

def _wares_ref(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    return _merge.Config(reference=ref)


def _adds(ext, folder, *keys):
    body = "".join(f'<ware id="{k}"/>' for k in keys)
    return _mod(ext, folder, {"libraries/wares.xml":
                f'<diff><add sel="/wares">{body}</add></diff>'})


def _issue4(tmp_path):
    cfg = _wares_ref(tmp_path)
    ext = tmp_path / "extensions"
    _adds(ext, "a_duplicate", "test_shared")
    _adds(ext, "b_duplicate", "test_shared")
    _adds(ext, "c_unrelated", "test_unique")
    return cfg, ext


def test_4_duplicate_row_names_only_the_keys_providers(tmp_path):
    cfg, ext = _issue4(tmp_path)
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert len(hard) == 1, hard
    assert hard[0].mods == ["a_duplicate", "b_duplicate"]
    assert hard[0].winner == "b_duplicate"
    assert "ware#test_shared" in hard[0].detail
    assert "survivor not engine-measured" in hard[0].detail


def test_4_the_parent_SOFT_row_still_names_every_adder(tmp_path):
    cfg, ext = _issue4(tmp_path)
    soft = _compat.analyze(ext, config=cfg).by_kind("SOFT")
    assert [c.mods for c in soft] == [["a_duplicate", "b_duplicate", "c_unrelated"]]


def test_4_a_candidate_focus_on_the_UNRELATED_adder_keeps_no_HARD_row(tmp_path):
    cfg, ext = _issue4(tmp_path)
    rep = _compat.analyze(ext, candidate=ext / "c_unrelated", config=cfg)
    assert rep.by_kind("HARD") == []
    assert len(rep.by_kind("SOFT")) == 1


def test_4_TWIN_a_candidate_focus_on_a_PROVIDER_keeps_the_HARD_row(tmp_path):
    cfg, ext = _issue4(tmp_path)
    rep = _compat.analyze(ext, candidate=ext / "a_duplicate", config=cfg)
    assert [c.mods for c in rep.by_kind("HARD")] == [["a_duplicate", "b_duplicate"]]


def test_4_every_duplicated_key_gets_its_own_row(tmp_path):
    cfg = _wares_ref(tmp_path)
    ext = tmp_path / "extensions"
    _adds(ext, "a_mod", "k1", "k2")
    _adds(ext, "b_mod", "k1")
    _adds(ext, "c_mod", "k2")
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    rows = sorted((c.detail.split(" add ")[1].split()[0], c.mods, c.winner) for c in hard)
    assert rows == [("ware#k1", ["a_mod", "b_mod"], "b_mod"),
                    ("ware#k2", ["a_mod", "c_mod"], "c_mod")], [c.detail for c in hard]


def test_4_TWIN_no_union_key_row_repeats_a_hard_duplicate(tmp_path):
    cfg, ext = _issue4(tmp_path)
    rep = _compat.analyze(ext, config=cfg)
    assert [c for c in rep.by_kind("UNION-KEY") if "test_shared" in c.target] == []


def test_4_TWIN_a_definer_the_hard_row_does_not_name_still_gets_a_union_key_row(tmp_path):
    """hard_dup_keys must hold the key's PROVIDERS, not every co-adder. e_both adds an
    unrelated ware under /wares (so it IS a co-adder there) and defines ware#test_shared
    at a DIFFERENT anchor. The per-document UNION-KEY row naming it must survive: folding
    it into the /wares HARD row, which does not name e_both, would lose its definition."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", '<wares><ware id="base_w"/></wares>')
    cfg = _merge.Config(reference=ref)
    ext = tmp_path / "extensions"
    _adds(ext, "a_duplicate", "test_shared")
    _adds(ext, "b_duplicate", "test_shared")
    _mod(ext, "e_both", {"libraries/wares.xml":
         '<diff><add sel="/wares"><ware id="e_other"/></add>'
         """<add sel="//ware[@id='base_w']" pos="after"><ware id="test_shared"/></add></diff>"""})
    rep = _compat.analyze(ext, config=cfg)
    hard = [c.mods for c in rep.by_kind("HARD") if "test_shared" in c.detail]
    assert hard == [["a_duplicate", "b_duplicate"]], rep.collisions
    uk = [c for c in rep.by_kind("UNION-KEY") if "test_shared" in c.target]
    assert [c.mods for c in uk] == [["a_duplicate", "b_duplicate", "e_both"]], rep.collisions


def test_4_TWIN_one_mod_adding_a_key_twice_is_not_a_cross_mod_duplicate(tmp_path):
    cfg = _wares_ref(tmp_path)
    ext = tmp_path / "extensions"
    _adds(ext, "a_mod", "same", "same")
    _adds(ext, "b_mod", "other")
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == [] and len(rep.by_kind("SOFT")) == 1


# --- F276: a duplicated WILDCARD icon is compared by its EXPANSION ------------------------
# The engine expands <icon name="ship_*" texture="extensions\m\dir\*.tga"> into one ID per
# matching texture file (name's '*' -> the text the texture '*' matched) and logs a
# duplicate only where two mods' EXPANSIONS overlap (READ, 2026-10-08 engine log:
# 'ship_ship_arg_xl_battleship_lc4_02_macro' for wildcard 'ship_*' with texture path
# 'extensions\vro\...\ships/ship_arg_xl_battleship_lc4_02_macro.tga'). Every vanilla and
# DLC wildcard declares `*.tga` while shipping `.gz` files, so `.gz` is an accepted alias.

_TEX = "assets/fx/gui/textures/ships"


def _icons_cfg(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "icons.xml", "<icons/>")
    return _merge.Config(reference=ref)


def _icon_mod(ext, folder, textures=(), name="ship_*", texture=None, packed=False,
              extra_icons=""):
    """A mod adding one wildcard icon under /icons, shipping *textures* (file names) in
    _TEX -- loose by default, or inside an ext_01.cat when *packed*."""
    texture = texture if texture is not None else (
        "extensions\\" + folder + "\\" + _TEX.replace("/", "\\") + "\\*.tga")
    icons = (f'<diff><add sel="/icons"><icon name="{name}" texture="{texture}" '
             f'height="256" width="256"/>{extra_icons}</add></diff>')
    if not packed:
        root = _mod(ext, folder, {"libraries/icons.xml": icons})
        for t in textures:
            p = root / _TEX / t
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"tex")
        return root
    import hashlib
    root = ext / folder
    _w(root / "content.xml", f'<content id="{folder}" name="{folder}" version="1"/>')
    members = [("libraries/icons.xml", icons.encode("utf-8"))]
    members += [(f"{_TEX}/{t}", b"tex") for t in textures]
    lines, blob = [], bytearray()
    for vpath, data in members:
        lines.append(f"{vpath} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    (root / "ext_01.cat").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "ext_01.dat").write_bytes(bytes(blob))
    return root


def _wild_hard(rep):
    return sorted((c.detail, c.mods, c.winner) for c in rep.by_kind("HARD"))


def test_F276_overlapping_expansions_are_one_HARD_row_per_shared_ID(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga", "ship_a_only_macro.tga"])
    _icon_mod(ext, "b_mod", ["ship_x_macro.tga", "ship_b_only_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert [(c.mods, c.winner) for c in hard] == [(["a_mod", "b_mod"], "b_mod")], hard
    assert "'ship_ship_x_macro'" in hard[0].detail and "icon#ship_*" in hard[0].detail
    assert len(rep.by_kind("SOFT")) == 1 and rep.skipped == []


def test_F276_TWIN_disjoint_expansions_are_SOFT_only(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_a_macro.tga"])
    _icon_mod(ext, "b_mod", ["ship_b_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == [], rep.by_kind("HARD")
    soft = rep.by_kind("SOFT")
    assert [c.mods for c in soft] == [["a_mod", "b_mod"]]
    assert "NOT CHECKED" not in soft[0].detail and rep.skipped == []


def test_F276_providers_are_the_mods_whose_expansion_holds_the_ID(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", ["ship_y_macro.tga"])
    _icon_mod(ext, "c_mod", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert [(c.mods, c.winner) for c in rep.by_kind("HARD")] == [(["a_mod", "c_mod"], "c_mod")]
    assert [c.mods for c in rep.by_kind("SOFT")] == [["a_mod", "b_mod", "c_mod"]]


def test_F276_packed_textures_are_enumerated_against_loose_ones(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_packed", ["ship_x_macro.tga"], packed=True)
    _icon_mod(ext, "b_loose", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert [c.mods for c in rep.by_kind("HARD")] == [["a_packed", "b_loose"]], rep.skipped
    assert rep.skipped == []


def test_F276_TWIN_packed_disjoint_is_SOFT_only(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_packed", ["ship_a_macro.tga"], packed=True)
    _icon_mod(ext, "b_packed", ["ship_b_macro.tga"], packed=True)
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == [] and rep.skipped == []


def test_F276_a_gz_texture_answers_a_tga_pattern(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.gz"])
    _icon_mod(ext, "b_mod", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert [c.mods for c in rep.by_kind("HARD")] == [["a_mod", "b_mod"]]


def test_F276_TWIN_an_unrelated_extension_does_not_answer_a_tga_pattern(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.png", "ship_a_macro.tga"])
    _icon_mod(ext, "b_mod", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == []


def test_F276_the_ID_substitutes_the_matched_text_not_the_file_stem(tmp_path):
    """loading_* over screen_*.tga: screen_a.tga is ID loading_a, which collides with a
    plain *.tga pattern's a.tga -- and NOT with a file literally named loading_a."""
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["screen_a.tga"], name="loading_*",
              texture="extensions\\a_mod\\" + _TEX.replace("/", "\\") + "\\screen_*.tga")
    _icon_mod(ext, "b_mod", ["a.tga"], name="loading_*")
    rep = _compat.analyze(ext, config=cfg)
    hard = rep.by_kind("HARD")
    assert [c.mods for c in hard] == [["a_mod", "b_mod"]] and "'loading_a'" in hard[0].detail


def _unchecked(rep, folder):
    return [s for s in rep.skipped if s.what.startswith(folder) and "icon#ship_*" in s.what]


def test_F276_a_missing_texture_directory_is_announced_never_HARD(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", [])                     # declares the path, ships nothing
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == []
    assert len(_unchecked(rep, "b_mod")) == 1 and not _unchecked(rep, "a_mod"), rep.skipped
    assert not rep.degraded
    soft = rep.by_kind("SOFT")
    assert len(soft) == 1 and "NOT CHECKED" in soft[0].detail and "b_mod" in soft[0].detail


def test_F276_a_bare_texture_path_is_announced(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", ["ship_x_macro.tga"],
              texture=_TEX.replace("/", "\\") + "\\*.tga")
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == []
    assert len(_unchecked(rep, "b_mod")) == 1 and "bare" in _unchecked(rep, "b_mod")[0].why


def test_F276_a_texture_owner_that_is_not_active_is_announced(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", [], texture="extensions\\gone\\" + _TEX.replace("/", "\\")
              + "\\*.tga")
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == [] and len(_unchecked(rep, "b_mod")) == 1


def test_F276_an_unreadable_catalog_is_announced(tmp_path, monkeypatch):
    from x4validate import _cat
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_packed", ["ship_x_macro.tga"], packed=True)
    real = _cat.mod_vfs

    def boom(mod_dir, xml_only=True, packed_only=False):
        if not xml_only and Path(mod_dir).name == "b_packed":
            raise OSError("simulated unreadable catalog")
        return real(mod_dir, xml_only, packed_only)

    monkeypatch.setattr(_cat, "mod_vfs", boom)
    rep = _compat.analyze(ext, config=cfg)
    assert rep.by_kind("HARD") == []
    un = _unchecked(rep, "b_packed")
    assert len(un) == 1 and "simulated unreadable catalog" in un[0].why, rep.skipped


def test_F276_TWIN_an_enumerable_overlap_still_reports_beside_an_unchecked_provider(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", [])
    _icon_mod(ext, "c_mod", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert [c.mods for c in rep.by_kind("HARD")] == [["a_mod", "c_mod"]]
    assert len(_unchecked(rep, "b_mod")) == 1


def test_F276_TWIN_a_wildcard_key_that_is_not_an_icon_stays_a_key_level_HARD(tmp_path):
    cfg = _wares_ref(tmp_path)
    ext = tmp_path / "extensions"
    _adds(ext, "a_mod", "upgrade_*")
    _adds(ext, "b_mod", "upgrade_*")
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert [c.mods for c in hard] == [["a_mod", "b_mod"]] and "ware#upgrade_*" in hard[0].detail


def test_F276_TWIN_a_plain_icon_key_stays_a_key_level_HARD(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    for f in ("a_mod", "b_mod"):
        _mod(ext, f, {"libraries/icons.xml":
             '<diff><add sel="/icons"><icon name="ship_plain" texture="x.tga"/></add></diff>'})
    hard = _compat.analyze(ext, config=cfg).by_kind("HARD")
    assert [c.mods for c in hard] == [["a_mod", "b_mod"]] and "icon#ship_plain" in hard[0].detail


def test_F276_a_candidate_focus_skips_unchecked_rows_it_is_not_part_of(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _icon_mod(ext, "b_mod", [])
    _mod(ext, "c_cand", {"libraries/icons.xml":
         '<diff><add sel="/icons"><icon name="ship_c" texture="c.tga"/></add></diff>'})
    rep = _compat.analyze(ext, candidate=ext / "c_cand", config=cfg)
    assert not [s for s in rep.skipped if "icon#ship_*" in s.what], rep.skipped
    rep2 = _compat.analyze(ext, candidate=ext / "b_mod", config=cfg)
    assert len(_unchecked(rep2, "b_mod")) == 1, rep2.skipped


# F276 (2): the same rule for FULL-FILE shippers -- the UNION-KEY pass.

def _full_icon_mod(ext, folder, textures=(), name="ship_*", texture=None, extra=""):
    texture = texture if texture is not None else (
        "extensions\\" + folder + "\\" + _TEX.replace("/", "\\") + "\\*.tga")
    root = _mod(ext, folder, {"libraries/icons.xml":
                f'<icons><icon name="{name}" texture="{texture}"/>{extra}</icons>'})
    for t in textures:
        p = root / _TEX / t
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"tex")
    return root


def _uk(rep):
    return sorted((c.target, c.mods, c.winner) for c in rep.by_kind("UNION-KEY"))


def test_F276_full_file_overlap_is_a_UNION_KEY_row_per_shared_ID(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _full_icon_mod(ext, "a_mod", ["ship_x_macro.tga", "ship_a_macro.tga"])
    _full_icon_mod(ext, "b_mod", ["ship_x_macro.gz"])
    _full_icon_mod(ext, "c_mod", ["ship_c_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert _uk(rep) == [("icon#ship_ship_x_macro", ["a_mod", "b_mod"], "b_mod")], rep.collisions
    row = rep.by_kind("UNION-KEY")[0]
    assert "icon#ship_*" in row.detail and rep.skipped == []


def test_F276_TWIN_full_file_disjoint_expansions_have_no_row(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _full_icon_mod(ext, "a_mod", ["ship_a_macro.tga"])
    _full_icon_mod(ext, "b_mod", ["ship_b_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert _uk(rep) == [] and rep.skipped == []


def test_F276_full_file_un_enumerable_set_is_announced(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _full_icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _full_icon_mod(ext, "b_mod", [], texture=_TEX.replace("/", "\\") + "\\*.tga")   # bare
    rep = _compat.analyze(ext, config=cfg)
    assert _uk(rep) == []
    un = _unchecked(rep, "b_mod")
    assert len(un) == 1 and "bare" in un[0].why and not rep.degraded, rep.skipped
    assert not _unchecked(rep, "a_mod")


def test_F276_TWIN_full_file_overlap_still_reports_beside_an_unchecked_shipper(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _full_icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _full_icon_mod(ext, "b_mod", [])
    _full_icon_mod(ext, "c_mod", ["ship_x_macro.tga"])
    rep = _compat.analyze(ext, config=cfg)
    assert _uk(rep) == [("icon#ship_ship_x_macro", ["a_mod", "c_mod"], "c_mod")]
    assert len(_unchecked(rep, "b_mod")) == 1


def test_F276_TWIN_full_file_plain_and_non_icon_keys_keep_their_UNION_KEY_row(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    for f in ("a_mod", "b_mod"):
        _mod(ext, f, {"libraries/icons.xml":
             '<icons><icon name="ship_plain" texture="x.tga"/>'
             '<other name="thing_*"/></icons>'})
    rep = _compat.analyze(ext, config=cfg)
    assert [t for t, _, _ in _uk(rep)] == ["icon#ship_plain", "other#thing_*"], rep.collisions


def test_F276_full_file_candidate_focus_announces_only_its_own_rows(tmp_path):
    cfg = _icons_cfg(tmp_path)
    ext = tmp_path / "extensions"
    _full_icon_mod(ext, "a_mod", ["ship_x_macro.tga"])
    _full_icon_mod(ext, "b_mod", [])
    _mod(ext, "c_cand", {"libraries/icons.xml":
         '<icons><icon name="ship_c" texture="c.tga"/></icons>'})
    rep = _compat.analyze(ext, candidate=ext / "c_cand", config=cfg)
    assert not [s for s in rep.skipped if "icon#ship_*" in s.what], rep.skipped
    rep2 = _compat.analyze(ext, candidate=ext / "b_mod", config=cfg)
    assert len(_unchecked(rep2, "b_mod")) == 1, rep2.skipped


# --- issue #8: an op whose `if=` guard is FALSE at its load position does nothing ----
#
# `_merge.apply_diff` evaluates `if=` against the tree as it stands when the op applies
# (base+DLC, every earlier mod, earlier ops of the same file) and skips the op when it is
# false. x4compat resolved every op's sel= against the base alone and never looked at the
# guard, so a guarded-off op was reported as colliding -- and could be named the winner.

_ORE_AVG = "//ware[@id='ore']/price/@average"


def _rep(tmp_path, *mods, candidate=None):
    """*mods* = (folder, diff-body) pairs on libraries/wares.xml."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    for folder, body in mods:
        _mod(ext, folder, {"libraries/wares.xml": f"<diff>{body}</diff>"})
    return _compat.analyze(ext, candidate=(ext / candidate) if candidate else None,
                           config=cfg)


def _set(v, guard=None, sel=_ORE_AVG):
    g = f' if="{guard}"' if guard is not None else ""
    return f'<replace sel="{sel}"{g}>{v}</replace>'


def _off(rep):
    return [(g.mod, g.guard) for g in rep.guarded_off]


def test_8_issue_fixture_a_guarded_off_op_is_not_a_HARD_collision(tmp_path):
    rep = _rep(tmp_path, ("aaa_setter", _set(100)), ("bbb_guarded", _set(999, "false()")))
    assert rep.by_kind("HARD") == [], rep.collisions
    assert _off(rep) == [("bbb_guarded", "if=false()")]
    assert rep.guarded_off[0].shared_with == ["aaa_setter"]


def test_8_TWIN_the_unguarded_control_is_HARD_won_by_ccc(tmp_path):
    rep = _rep(tmp_path, ("aaa_setter", _set(100)), ("ccc_unguarded", _set(999)))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "ccc_unguarded", rep.collisions
    assert rep.guarded_off == []


def test_8_a_guard_false_on_the_EARLIER_mod_removes_it_too(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1, "false()")), ("b_mod", _set(2)))
    assert rep.by_kind("HARD") == [], rep.collisions
    assert _off(rep) == [("a_mod", "if=false()")]


def test_8_a_TRUE_guard_keeps_the_row_and_says_it_depends_on_load_order(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "true()")))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod", rep.collisions
    assert "b_mod's op guarded `if=true()`: TRUE at its load position" in hard[0].detail
    assert "depends on this run's load order" in hard[0].detail
    assert rep.guarded_off == []


def test_8_the_guard_is_read_at_the_LOAD_POSITION_not_against_the_base(tmp_path):
    """True against the base (average=100), false once a_mod (loading first) set 200."""
    rep = _rep(tmp_path, ("a_mod", _set(200)),
               ("b_mod", _set(2, "//ware[@id='ore']/price[@average='100']")))
    assert rep.by_kind("HARD") == [], rep.collisions
    assert [g.mod for g in rep.guarded_off] == ["b_mod"]


def test_8_TWIN_the_load_position_guard_TRUE_only_after_the_earlier_mod(tmp_path):
    """False against the base, true once a_mod set 200: participates."""
    rep = _rep(tmp_path, ("a_mod", _set(200)),
               ("b_mod", _set(2, "//ware[@id='ore']/price[@average='200']")))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod", rep.collisions
    assert "TRUE at its load position" in hard[0].detail


def test_8_an_earlier_op_in_the_SAME_file_is_seen_by_the_guard(tmp_path):
    """b_mod's own first op sets @average=300; its guarded second op (on the SAME node)
    requires average=100, which the base has -- false once its own first op ran."""
    rep = _rep(tmp_path, ("a_mod", _set(1, sel="//ware[@id='ore']/price/@min")),
               ("b_mod", _set(300)
                + _set(2, "//ware[@id='ore']/price[@average='100']",
                       sel="//ware[@id='ore']/price/@min")))
    assert rep.by_kind("HARD") == [], rep.collisions
    assert [g.mod for g in rep.guarded_off] == ["b_mod"]


_ADD_DUP = '<add sel="/wares"{g}><ware id="dupware"/></add>'


def test_8_a_guarded_off_duplicate_add_is_neither_HARD_nor_SOFT(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _ADD_DUP.format(g="")),
               ("b_mod", _ADD_DUP.format(g=' if="false()"')))
    assert [c.kind for c in rep.collisions] == [], rep.collisions
    assert _off(rep) == [("b_mod", "if=false()")]


def test_8_TWIN_a_TRUE_guarded_duplicate_add_stays_HARD_and_SOFT(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _ADD_DUP.format(g="")),
               ("b_mod", _ADD_DUP.format(g=' if="true()"')))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and "dupware" in hard[0].detail, rep.collisions
    assert "TRUE at its load position" in hard[0].detail
    assert len(rep.by_kind("SOFT")) == 1


_WIPE = '<replace sel="//ware[@id=\'ore\']/price"{g}><price average="5"/></replace>'


def test_8_a_guarded_off_WIPER_is_no_SUBTREE(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _WIPE.format(g=' if="false()"')))
    assert rep.by_kind("SUBTREE") == [], rep.collisions
    assert _off(rep) == [("b_mod", "if=false()")]
    assert rep.guarded_off[0].shared_with == ["a_mod"]


def test_8_TWIN_a_TRUE_guarded_WIPER_is_SUBTREE(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _WIPE.format(g=' if="true()"')))
    st = rep.by_kind("SUBTREE")
    assert len(st) == 1 and st[0].wiped_by == "b_mod", rep.collisions
    assert "TRUE at its load position" in st[0].detail


def test_8_a_guarded_off_VICTIM_is_no_SUBTREE(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1, "false()")), ("b_mod", _WIPE.format(g="")))
    assert rep.by_kind("SUBTREE") == [], rep.collisions
    assert _off(rep) == [("a_mod", "if=false()")]


def test_8_TWIN_a_TRUE_guarded_VICTIM_is_SUBTREE(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1, "true()")), ("b_mod", _WIPE.format(g="")))
    assert len(rep.by_kind("SUBTREE")) == 1, rep.collisions


_RM_PRICE = '<remove sel="//ware[@id=\'ore\']/price"{g}/>'
_REPL_PRICE = '<replace sel="//ware[@id=\'ore\']/price"{g}><price average="2"/></replace>'


def test_8_AN5_a_guarded_off_REMOVE_does_not_end_the_contest(tmp_path):
    """The remove never happens, so no row claims the node was removed."""
    rep = _rep(tmp_path, ("a_mod", _RM_PRICE.format(g=' if="false()"')),
               ("b_mod", _REPL_PRICE.format(g="")))
    assert rep.by_kind("HARD") == [], rep.collisions
    assert _off(rep) == [("a_mod", "if=false()")]


def test_8_AN5_TWIN_a_TRUE_guarded_REMOVE_is_what_is_live(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _RM_PRICE.format(g=' if="true()"')),
               ("b_mod", _REPL_PRICE.format(g="")))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].removed_by == "a_mod", rep.collisions


def test_8_AN5_a_guarded_off_LATER_writer_is_never_the_winner(tmp_path):
    """a removes ore, b re-adds it, c replaces it, d's replace is guarded off: the live
    writer is c. Before the fix d matched at its position and was named winner."""
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_rm", {"libraries/wares.xml": _RM_ORE})
    _mod(ext, "b_readd", {"libraries/wares.xml": _READD_ORE})
    _mod(ext, "c_replace", {"libraries/wares.xml": _REPLACE_ORE})
    _mod(ext, "d_guarded", {"libraries/wares.xml": _REPLACE_ORE.replace(
        "<replace sel", '<replace if="false()" sel')})
    hard = [c for c in _compat.analyze(ext, config=cfg).by_kind("HARD")
            if "a_rm" in c.mods]
    assert len(hard) == 1, hard
    assert hard[0].winner == "c_replace" and "d_guarded" not in hard[0].mods, hard[0]


def test_8_an_UNEVALUABLE_guard_keeps_the_row_labelled_and_announced(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "//[")))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].winner == "b_mod", rep.collisions
    assert "guard UNEVALUATED" in hard[0].detail
    assert "engine behaviour UNMEASURED" in hard[0].detail
    assert hard[0].winner_qualified is False and hard[0].live_value_owner() is None
    assert [s for s in rep.skipped if "b_mod" in s.what and "if=" in s.why], rep.skipped
    assert rep.guarded_off == []


def test_8_TWIN_an_evaluable_guard_is_not_announced_as_unevaluated(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "true()")))
    assert rep.skipped == [] and "UNEVALUATED" not in rep.by_kind("HARD")[0].detail


def _nested_world(tmp_path, guard):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "owner", {"md/thing.xml": '<mdscript name="t"><cues><cue name="c" x="0"/>'
                                         '</cues></mdscript>'})
    rel = "extensions/owner/md/thing.xml"
    _mod(ext, "p_one", {rel: '<diff><replace sel="//cue/@x">1</replace></diff>'})
    _mod(ext, "q_two", {rel: f'<diff><replace sel="//cue/@x" if="{guard}">2</replace></diff>'})
    return _compat.analyze(ext, config=cfg)


def test_8_a_NESTED_vpath_evaluates_guards_through_the_rebuild(tmp_path):
    rep = _nested_world(tmp_path, "false()")
    assert [c for c in rep.by_kind("HARD") if "q_two" in c.mods] == [], rep.collisions
    assert _off(rep) == [("q_two", "if=false()")]


def test_8_TWIN_a_NESTED_vpath_TRUE_guard_still_collides(tmp_path):
    hard = [c for c in _nested_world(tmp_path, "true()").by_kind("HARD")
            if "q_two" in c.mods]
    assert len(hard) == 1 and hard[0].winner == "q_two", hard


def _three_guards(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    for f, body in (("a_mod", _set(200)),
                    ("b_mod", _set(2, "//ware[@id='ore']/price[@average='100']")),
                    ("c_mod", _set(3, "//ware[@id='ore']/price[@average='200']"))):
        _mod(ext, f, {"libraries/wares.xml": f"<diff>{body}</diff>"})
    return cfg, ext


def test_8_a_drifted_walk_falls_back_and_gives_the_same_answer(tmp_path, monkeypatch):
    """The incremental pass is CHECKED: a walk whose tree drifts (apply_diff returning
    the right verdicts without mutating) fails the self-check, its verdicts are
    discarded, and the per-position rebuild answers -- with the same answer."""
    import copy
    import types
    cfg, ext = _three_guards(tmp_path)
    built = []
    real_rebuild = _compat._verdicts_at
    monkeypatch.setattr(_compat, "_verdicts_at",
                        lambda *a, **k: built.append(1) or real_rebuild(*a, **k))
    drifted = types.SimpleNamespace(**{k: getattr(_merge, k) for k in dir(_merge)
                                       if not k.startswith("__")})
    drifted.apply_diff = lambda tree, root, *a, **k: _merge.apply_diff(
        copy.deepcopy(tree), root, *a, **k)
    monkeypatch.setattr(_compat, "_merge", drifted)
    rep = _compat.analyze(ext, config=cfg)
    assert built, "the drifted walk was trusted: the self-check never fired"
    assert [g.mod for g in rep.guarded_off] == ["b_mod"]
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and hard[0].mods == ["a_mod", "c_mod"], rep.collisions


def test_8_TWIN_an_agreeing_walk_needs_no_rebuild(tmp_path, monkeypatch):
    cfg, ext = _three_guards(tmp_path)
    monkeypatch.setattr(_compat, "_verdicts_at",
                        lambda *a, **k: pytest.fail("rebuilt on an agreeing walk"))
    rep = _compat.analyze(ext, config=cfg)
    assert [g.mod for g in rep.guarded_off] == ["b_mod"]
    assert [c.mods for c in rep.by_kind("HARD")] == [["a_mod", "c_mod"]]


def test_8_guarded_off_is_rendered_counted_and_in_JSON(tmp_path, capsys):
    import json
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "aaa_setter", {"libraries/wares.xml": f"<diff>{_set(100)}</diff>"})
    _mod(ext, "bbb_guarded", {"libraries/wares.xml":
                              f"<diff>{_set(999, 'false()')}</diff>"})
    args = ["check", "--ext-dir", str(ext), "--reference", str(cfg.reference)]
    assert _compat.main(args) == 0
    out = capsys.readouterr().out
    assert "GUARDED OFF" in out and "bbb_guarded" in out, out
    assert "1 op(s) guarded off" in out, out
    assert _compat.main(args + ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [g["mod"] for g in payload["guarded_off"]] == ["bbb_guarded"], payload


def test_8_TWIN_the_unguarded_control_exits_1(tmp_path, capsys):
    import json
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "aaa_setter", {"libraries/wares.xml": f"<diff>{_set(100)}</diff>"})
    _mod(ext, "ccc_unguarded", {"libraries/wares.xml": f"<diff>{_set(999)}</diff>"})
    args = ["check", "--ext-dir", str(ext), "--reference", str(cfg.reference), "--json"]
    assert _compat.main(args) == 1
    assert json.loads(capsys.readouterr().out)["guarded_off"] == []


def test_8_a_candidate_run_keeps_only_guarded_off_ops_it_is_part_of(tmp_path):
    ice = "//ware[@id='ice']/price/@average"
    mods = (("a_mod", _set(1)), ("b_mod", _set(2, "false()")),
            ("c_mod", _set(5, "false()", sel=ice)), ("d_mod", _set(6, sel=ice)))
    assert [g.mod for g in _rep(tmp_path / "1", *mods, candidate="a_mod").guarded_off] \
        == ["b_mod"]
    assert [g.mod for g in _rep(tmp_path / "2", *mods, candidate="d_mod").guarded_off] \
        == ["c_mod"]


_DOCWIDE_A = '<add sel="/wares"><ware id="kware"/></add>'
_DOCWIDE_B = '<add sel="//ware[@id=\'ore\']" pos="after"{g}><ware id="kware"/></add>'


def test_8_UNION_KEY_a_TRUE_guarded_add_joins_the_docwide_pass(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _DOCWIDE_A),
               ("b_mod", _DOCWIDE_B.format(g=' if="true()"')))
    uk = [c for c in rep.by_kind("UNION-KEY") if c.target == "ware#kware"]
    assert len(uk) == 1 and uk[0].mods == ["a_mod", "b_mod"], rep.collisions
    assert "TRUE at its load position" in uk[0].detail


def test_8_UNION_KEY_TWIN_a_FALSE_guarded_add_stays_out(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _DOCWIDE_A),
               ("b_mod", _DOCWIDE_B.format(g=' if="false()"')))
    assert not [c for c in rep.by_kind("UNION-KEY") if "kware" in c.target], rep.collisions
    assert _off(rep) == [("b_mod", "if=false()")]
    assert rep.guarded_off[0].shared_with == ["a_mod"]


def test_8_UNION_KEY_TWIN_an_unevaluable_guarded_add_joins_labelled(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _DOCWIDE_A), ("b_mod", _DOCWIDE_B.format(g=' if="//["')))
    uk = [c for c in rep.by_kind("UNION-KEY") if c.target == "ware#kware"]
    assert len(uk) == 1 and "guard UNEVALUATED" in uk[0].detail, rep.collisions


def _name_clash_world(tmp_path, guard):
    cfg = _setup_ref(tmp_path)
    _w(cfg.reference / "assets/y/macros/m2_macro.xml", '<macros><macro name="m2"/></macros>')
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"assets/x/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "b_mod", {"assets/y/macros/m2_macro.xml":
                        f'<diff><replace sel="//macros" if="{guard}">{_MACROS}</replace></diff>'})
    return _compat.analyze(ext, config=cfg)


def test_8_NAME_CLASH_a_guarded_off_definition_defines_nothing(tmp_path):
    rep = _name_clash_world(tmp_path, "false()")
    assert _clashes(rep) == [], rep.collisions
    assert _off(rep) == [("b_mod", "if=false()")]


def test_8_NAME_CLASH_TWIN_a_TRUE_guarded_definition_still_clashes(tmp_path):
    cl = _clashes(_name_clash_world(tmp_path, "true()"))
    assert len(cl) == 1 and cl[0].target == "ship_probe_macro", cl
    assert "TRUE at its load position" in cl[0].detail


def test_8_NAME_CLASH_the_guard_is_evaluated_against_the_file(tmp_path):
    """A guard testing the patched file's content: true and false variants."""
    assert len(_clashes(_name_clash_world(tmp_path / "t", "//macro[@name='m2']"))) == 1
    assert _clashes(_name_clash_world(tmp_path / "f", "//macro[@name='zz']")) == []


# --- issue #8: one falsification twin per clause of the conditions the fix added -------

def test_8_shared_with_an_ADD_compares_its_anchor_by_equality_only(tmp_path):
    """Every op in the file sits inside /wares; a guarded-off add anchored there must
    not be reported as meeting a mod that merely edits inside a ware."""
    rep = _rep(tmp_path, ("a_mod", _set(1)),
               ("b_mod", '<add sel="/wares" if="false()"><ware id="new"/></add>'))
    assert [(g.mod, g.shared_with) for g in rep.guarded_off] == [("b_mod", [])]


def test_8_TWIN_shared_with_an_ADD_on_the_same_anchor_is_named(tmp_path):
    rep = _rep(tmp_path, ("a_mod", '<add sel="/wares"><ware id="x1"/></add>'),
               ("b_mod", '<add sel="/wares" if="false()"><ware id="x2"/></add>'))
    assert [(g.mod, g.shared_with) for g in rep.guarded_off] == [("b_mod", ["a_mod"])]


def test_8_shared_with_a_guarded_off_VICTIM_names_the_wiper_around_it(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1, "false()")), ("b_mod", _WIPE.format(g="")))
    assert [(g.mod, g.shared_with) for g in rep.guarded_off] == [("a_mod", ["b_mod"])]


def test_8_a_candidate_run_keeps_the_guarded_off_op_OF_the_candidate(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "false()")),
               candidate="b_mod")
    assert [g.mod for g in rep.guarded_off] == ["b_mod"]


def test_8_a_guarded_off_op_with_an_INVALID_sel_is_not_NOT_CHECKED(tmp_path):
    """The engine skips a false-guarded op before it ever reads sel=."""
    rep = _rep(tmp_path, ("a_mod", _set(1)),
               ("b_mod", _set(2, "false()", sel="//ware[@id=")))
    assert rep.unresolvable == [] and [g.mod for g in rep.guarded_off] == ["b_mod"]


def test_8_TWIN_a_TRUE_guarded_op_with_an_INVALID_sel_is_NOT_CHECKED(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)),
               ("b_mod", _set(2, "true()", sel="//ware[@id=")))
    assert len(rep.unresolvable) == 1 and rep.guarded_off == []


def _drifted(monkeypatch, **overrides):
    import types
    drifted = types.SimpleNamespace(**{k: getattr(_merge, k) for k in dir(_merge)
                                       if not k.startswith("__")})
    for k, v in overrides.items():
        setattr(drifted, k, v)
    monkeypatch.setattr(_compat, "_merge", drifted)


def test_8_UNPAIRED_verdicts_make_the_guard_UNEVALUATED_never_false(tmp_path, monkeypatch):
    """apply_diff returning one verdict too few cannot be paired with the ops -- in the
    walk or in the rebuild -- so the guard is UNEVALUATED: kept, labelled, announced."""
    _drifted(monkeypatch,
             apply_diff=lambda tree, root, *a, **k: _merge.apply_diff(tree, root, *a, **k)[:-1])
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "false()")))
    hard = rep.by_kind("HARD")
    assert len(hard) == 1 and "guard UNEVALUATED" in hard[0].detail, rep.collisions
    assert "could not be paired" in hard[0].detail
    assert rep.guarded_off == [] and [s for s in rep.skipped if "b_mod" in s.what]


def test_8_a_walk_that_read_a_DIFFERENT_op_list_is_not_trusted(tmp_path, monkeypatch):
    """The pass parses its own copy of each file; if its op count differs from the copy
    the rows are built from, its verdicts are discarded for the rebuild's."""
    from lxml import etree
    real = _merge.overlay_root

    def walk_sees_an_extra_op(odir, vpath, *rest):
        root = real(odir, vpath, *rest)
        if rest and root is not None and root.tag == "diff":      # the walk's call
            # matches nothing, so the walk's TREE stays right and its self-check
            # passes: only the op INDICES are off by one
            root.insert(0, etree.fromstring('<remove sel="//nothing"/>'))
        return root

    built = []
    real_rebuild = _compat._verdicts_at
    monkeypatch.setattr(_compat, "_verdicts_at",
                        lambda *a, **k: built.append(1) or real_rebuild(*a, **k))
    _drifted(monkeypatch, overlay_root=walk_sees_an_extra_op)
    rep = _rep(tmp_path, ("a_mod", _set(1)), ("b_mod", _set(2, "false()")))
    assert built, "verdicts from a mismatched op list were trusted"
    assert rep.by_kind("HARD") == [] and [g.mod for g in rep.guarded_off] == ["b_mod"]


def test_8_a_UNION_file_in_the_walk_is_still_unioned_not_diffed(tmp_path, monkeypatch):
    """Only a <diff> takes the apply_diff route; a full union file must still go through
    apply_overlay, or the self-check fails and every guard pays for a rebuild."""
    monkeypatch.setattr(_compat, "_verdicts_at",
                        lambda *a, **k: pytest.fail("rebuilt: the walk mishandled a union"))
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_full", {"libraries/wares.xml": '<wares><ware id="u1"/></wares>'})
    _mod(ext, "b_mod", {"libraries/wares.xml": f"<diff>{_set(1)}</diff>"})
    _mod(ext, "c_mod", {"libraries/wares.xml":
                        f"<diff>{_set(2, 'not(//ware[@id=' + chr(39) + 'u1' + chr(39) + '])')}"
                        "</diff>"})
    rep = _compat.analyze(ext, config=cfg)
    assert [g.mod for g in rep.guarded_off] == ["c_mod"], rep.collisions


def test_8_one_guarded_off_op_is_announced_ONCE_across_both_passes(tmp_path):
    """A guarded macro definition in a file two mods patch is met by the per-file pass
    AND the NAME-CLASH pass; it is one op, so one record."""
    cfg = _setup_ref(tmp_path)
    _w(cfg.reference / "assets/y/macros/m2_macro.xml", '<macros><macro name="m2"/></macros>')
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"assets/x/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "b_mod", {"assets/y/macros/m2_macro.xml":
                        f'<diff><replace sel="//macros" if="false()">{_MACROS}</replace></diff>'})
    _mod(ext, "c_mod", {"assets/y/macros/m2_macro.xml":
                        '<diff><replace sel="//macro/@name">m3</replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert [g.mod for g in rep.guarded_off] == ["b_mod"], rep.guarded_off


def test_8_NAME_CLASH_a_mod_defining_the_name_ELSEWHERE_too_is_not_narrowed(tmp_path):
    cfg = _setup_ref(tmp_path)
    _w(cfg.reference / "assets/y/macros/m2_macro.xml", '<macros><macro name="m2"/></macros>')
    ext = tmp_path / "extensions"
    _mod(ext, "a_mod", {"assets/x/macros/ship_probe_macro.xml": _MACROS})
    _mod(ext, "b_mod", {"assets/y/macros/m2_macro.xml":
                        f'<diff><replace sel="//macros" if="false()">{_MACROS}</replace></diff>',
                        "assets/z/macros/ship_probe_macro.xml": _MACROS})
    rep = _compat.analyze(ext, config=cfg)
    assert len(_clashes(rep)) == 1 and rep.guarded_off == [], rep.guarded_off


def test_8_NAME_CLASH_two_live_definers_in_ONE_logical_file_are_no_clash(tmp_path):
    """Before the guard, three definers over two files; after it, two in one file."""
    cfg = _setup_ref(tmp_path)
    _w(cfg.reference / "assets/y/macros/m2_macro.xml", '<macros><macro name="m2"/></macros>')
    ext = tmp_path / "extensions"
    same = "assets/x/macros/ship_probe_macro.xml"
    _mod(ext, "a_mod", {same: _MACROS})
    _mod(ext, "b_mod", {same: _MACROS})
    _mod(ext, "c_mod", {"assets/y/macros/m2_macro.xml":
                        f'<diff><replace sel="//macros" if="false()">{_MACROS}</replace></diff>'})
    rep = _compat.analyze(ext, config=cfg)
    assert _clashes(rep) == [] and [g.mod for g in rep.guarded_off] == ["c_mod"]


def test_8_NAME_CLASH_an_UNEVALUABLE_guard_keeps_the_clash_labelled(tmp_path):
    rep = _name_clash_world(tmp_path, "//[")
    cl = _clashes(rep)
    assert len(cl) == 1 and "guard UNEVALUATED" in cl[0].detail, cl
    assert [s for s in rep.skipped if "b_mod" in s.what], rep.skipped


def test_8_the_guard_pass_and_the_AN10_pass_share_ONE_finished_tree(tmp_path, monkeypatch):
    """Both passes self-check against the merged tree over every mod on the file; it is
    the costliest thing either builds (MEASURED, libraries/wares.xml on the live install),
    so it is built once per file -- and both answers still come out."""
    full = []

    def counting(vpath, config, extra_overlays=None, *a, **k):
        if extra_overlays is not None and len(extra_overlays) == 2:
            full.append(vpath)
        return _merge.build_effective(vpath, config, extra_overlays, *a, **k)

    _drifted(monkeypatch, build_effective=counting)
    rep = _rep(tmp_path, ("a_patcher", _PATCHES_ZWARE[6:-7] + _set(1, "true()")),
               ("z_adder", _ADDS_ZWARE[6:-7] + _set(2)))
    assert [(m.mod, m.added_by) for m in rep.order_misses] == [("a_patcher", "z_adder")]
    assert len(rep.by_kind("HARD")) == 1
    assert full == ["libraries/wares.xml"], full


# --- release review of #8: an op that is BOTH guarded off and an order miss ----------
#
# A guard can be false precisely because its target is not there yet, so the op stays in
# both lists -- but it is ONE op: the render says so, the summary does not count it as
# two, the order-miss text does not blame load order alone, and its `shared_with` names
# the later mod that adds the target (or says None = unknown, never an empty list).

_GUARD_ZWARE = ('<replace sel="//ware[@id=\'zware\']/price/@average" '
                'if="//ware[@id=\'zware\']">5</replace>')


def _both_world(tmp_path, body=_GUARD_ZWARE):
    return _rep(tmp_path, ("a_patcher", body), ("z_adder", _ADDS_ZWARE[6:-7]))


def test_8R_a_guarded_off_order_miss_is_ONE_op_in_both_lists(tmp_path):
    rep = _both_world(tmp_path)
    assert [(m.mod, m.added_by, m.guard_false) for m in rep.order_misses] == \
        [("a_patcher", "z_adder", True)]
    assert [(g.mod, g.shared_with, g.also_order_miss) for g in rep.guarded_off] == \
        [("a_patcher", ["z_adder"], True)]
    out = _compat.render(rep)
    assert "its sel= and its guard both fail at its load position" in out, out
    assert "also listed under GUARDED OFF" in out, out
    assert "1 op(s) guarded off (if= false; 1 of them also an order miss)" in out, out
    assert "load order is" in out and "not necessarily the only reason" in out, out


def test_8R_TWIN_a_TRUE_or_silent_guarded_order_miss_is_not_marked(tmp_path):
    rep = _both_world(tmp_path, _GUARD_ZWARE.replace('if="//ware[@id=\'zware\']"',
                                                    'silent="true"'))
    assert [(m.mod, m.guard_false) for m in rep.order_misses] == [("a_patcher", False)]
    assert rep.guarded_off == []
    out = _compat.render(rep)
    assert "both fail" not in out and "also listed under GUARDED OFF" not in out, out
    assert "not necessarily the only reason" not in out, out
    assert "0 op(s) guarded off (if= false)," in out, out


def test_8R_a_guarded_off_op_whose_target_NOBODY_supplies_is_unknown_not_empty(tmp_path):
    rep = _rep(tmp_path, ("a_mod", _set(1)),
               ("b_mod", _set(2, "false()", sel="//ware[@id='nope']/@x")))
    assert [(g.mod, g.shared_with, g.also_order_miss) for g in rep.guarded_off] == \
        [("b_mod", None, False)]
    assert "would meet: unknown" in _compat.render(rep)


def test_8R_TWIN_a_guarded_off_op_on_a_BASE_node_alone_is_an_empty_list(tmp_path):
    """Its target exists in the base and no other mod touches it: genuinely nobody."""
    rep = _rep(tmp_path, ("a_mod", _set(1)),
               ("b_mod", _set(2, "false()", sel="//ware[@id='ice']/price/@average")))
    assert [(g.mod, g.shared_with) for g in rep.guarded_off] == [("b_mod", [])]


def test_8R_JSON_carries_guard_false_and_also_order_miss(tmp_path, capsys):
    import json
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml": f"<diff>{_GUARD_ZWARE}</diff>"})
    _mod(ext, "z_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    _compat.main(["check", "--ext-dir", str(ext), "--reference", str(cfg.reference),
                  "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [m["guard_false"] for m in payload["order_misses"]] == [True]
    assert [(g["shared_with"], g["also_order_miss"]) for g in payload["guarded_off"]] == \
        [(["z_adder"], True)]


def test_8R_a_candidate_run_on_the_ADDER_keeps_the_shared_guarded_off_op(tmp_path):
    cfg = _setup_ref(tmp_path)
    ext = tmp_path / "extensions"
    _mod(ext, "a_patcher", {"libraries/wares.xml": f"<diff>{_GUARD_ZWARE}</diff>"})
    _mod(ext, "z_adder", {"libraries/wares.xml": _ADDS_ZWARE})
    rep = _compat.analyze(ext, candidate=ext / "z_adder", config=cfg)
    assert [g.mod for g in rep.guarded_off] == ["a_patcher"]


def test_8R_an_absent_target_ADD_whose_child_key_another_mod_defines_is_named(tmp_path):
    """No base node, but the document-wide key pass knows who else defines the key: that
    is known, not 'unknown'."""
    rep = _rep(tmp_path, ("a_mod", _DOCWIDE_A),
               ("b_mod", '<add sel="//ware[@id=\'gone\']" pos="after" if="false()">'
                         '<ware id="kware"/></add>'))
    assert [(g.mod, g.shared_with) for g in rep.guarded_off] == [("b_mod", ["a_mod"])]

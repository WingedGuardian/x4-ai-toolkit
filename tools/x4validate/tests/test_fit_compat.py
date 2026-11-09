"""check_fitting_compatibility: the three fitting questions the connection check never asked (BLIND-SPOTS F363).

Every finding here is INFO: the documented tag rule rejects 0 of 1421 vanilla preset leaves (MEASURED 2026-11-08),
so it is safe as a screen. Engine enforcement is MEASURED for weapons in one save (mismatched preset weapons were not
installed, BLIND-SPOTS F363 RUNTIME EVIDENCE) and UNMEASURED for other kinds -- a hit is a suspect to test.
One falsification twin per clause: each test flips exactly one input and asserts the finding appears or vanishes."""

from pathlib import Path

from x4validate import _check, _merge


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_TURRET = "turret_arg_m_gatling_01_mk1_macro"


def _world(tmp_path, *, slot_tags="engine ror_xl_a", eng_tags="engine component ror_xl_a",
           group_name="group_back", preset_group="group_back", macro_conn="con_cockpit", eng_registered=True,
           leaf_kind="engine", leaf_port="con_engine_01", group_kind="turrets", eng_second_attach=False,
           ship_class="ship_xl", turret_tags="turret component medium", turret_registered=True, extra_group_slot=None):
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", "<index>"
       + ('<entry name="eng_macro" value="assets/p/eng_macro"/>' if eng_registered else "")
       + (f'<entry name="{_TURRET}" value="assets/p/tur_macro"/>' if turret_registered else "") + "</index>")
    _w(ref / "index/components.xml",
       '<index><entry name="ship_x" value="assets/u/ship_x"/><entry name="eng" value="assets/p/eng"/>'
       '<entry name="tur" value="assets/p/tur"/></index>')
    _w(ref / "assets/u/ship_x.xml",
       '<components><component name="ship_x"><connections>'
       f'<connection name="con_engine_01" tags="{slot_tags}"/>'
       f'<connection name="con_turret_01" group="{group_name}" tags="turret combat medium standard hittable"/>'
       + (f'<connection name="con_extra_01" group="{group_name}" tags="{extra_group_slot}"/>' if extra_group_slot else "")
       + '<connection name="con_cockpit" tags="cockpit"/>'
       '</connections></component></components>')
    _w(ref / "assets/p/eng.xml",
       f'<components><component name="eng" class="engine"><connections><connection name="EngineConnection01" tags="{eng_tags}"/>'
       + ('<connection name="EngineConnection02" tags="engine component other_mount"/>' if eng_second_attach else "")
       + '</connections></component></components>')
    _w(ref / "assets/p/eng_macro.xml",
       '<macros><macro name="eng_macro" class="engine"><component ref="eng"/></macro></macros>')
    _w(ref / "assets/p/tur.xml",
       f'<components><component name="tur" class="turret"><connections><connection name="TurretConnection01" '
       f'tags="{turret_tags}"/></connections></component></components>')
    _w(ref / "assets/p/tur_macro.xml",
       f'<macros><macro name="{_TURRET}" class="turret"><component ref="tur"/></macro></macros>')
    mod = tmp_path / "mod"
    _w(mod / "assets/u/ship_new_macro.xml",
       f'<macros><macro name="ship_new_macro" class="{ship_class}"><component ref="ship_x"/>'
       '<properties><loadouts><loadout id="default"><macros>'
       f'<{leaf_kind} macro="eng_macro" path="../{leaf_port}"/>'
       '</macros><groups>'
       f'<{group_kind} macro="{_TURRET}" path=".." group="{preset_group}" exact="1"/>'
       '</groups></loadout></loadouts></properties>'
       f'<connections><connection ref="{macro_conn}"><macro ref="bridge_x_macro" connection="con_cockpit"/></connection></connections>'
       '</macro></macros>')
    return mod, _merge.Config(reference=ref)


def _run(mod, cfg):
    rep = _check.Report()
    _check.check_fitting_compatibility(mod, cfg, rep)
    return rep


def test_clean_world_has_no_fit_findings(tmp_path):
    rep = _run(*_world(tmp_path))
    assert [f for f in rep.findings if f.category == "fit"] == []


def test_leaf_missing_slot_tag_is_info(tmp_path):
    rep = _run(*_world(tmp_path, slot_tags="engine ror_xl_b"))
    hits = [f for f in rep.findings if f.category == "fit"]
    assert len(hits) == 1 and hits[0].severity == "info"
    m = hits[0].message
    assert "1 of 1 preset leaves fail" in m and "con_engine_01" in m and "eng_macro" in m and "ror_xl_a" in m
    assert "group names" not in m and "connection refs" not in m


def test_group_case_only_match_is_info_and_exact_match_is_silent(tmp_path):
    rep = _run(*_world(tmp_path, group_name="Group_Back"))
    hits = [f for f in rep.findings if f.category == "fit"]
    assert len(hits) == 1 and hits[0].severity == "info"
    assert "1 of 1 group names match the component only by case" in hits[0].message and "Group_Back" in hits[0].message
    assert not [f for f in _run(*_world(tmp_path)).findings if f.category == "fit"]


def test_macro_connection_ref_absent_is_info(tmp_path):
    rep = _run(*_world(tmp_path, macro_conn="con_nowhere"))
    hits = [f for f in rep.findings if f.category == "fit"]
    assert len(hits) == 1 and hits[0].severity == "info"
    assert "1 of 1 macro connection refs name no component connection" in hits[0].message and "con_nowhere" in hits[0].message


def test_one_finding_per_ship_macro_not_per_item(tmp_path):
    """The flood guard: three distinct problems on one hull are ONE line, and a second clean hull adds none."""
    rep = _run(*_world(tmp_path, slot_tags="engine ror_xl_b", group_name="Group_Back", macro_conn="con_nowhere"))
    fit = [f for f in rep.findings if f.category == "fit"]
    assert len(fit) == 1 and fit[0].message.startswith("ship macro 'ship_new_macro': ")
    assert all(s in fit[0].message for s in ("preset leaves fail", "only by case", "connection refs name no"))


def test_unresolvable_equipment_macro_is_a_skip_not_a_finding(tmp_path):
    rep = _run(*_world(tmp_path, eng_registered=False))
    assert not [f for f in rep.findings if f.category == "fit" and "eng_macro" in f.message]
    assert any("eng_macro" in s.why for s in rep.skipped)


def test_unreadable_component_is_a_skip_not_a_finding(tmp_path):
    mod, cfg = _world(tmp_path, slot_tags="engine ror_xl_b")
    (cfg.reference / "assets/u/ship_x.xml").write_text("<components><component name='ship_x'><broken", encoding="utf-8")
    rep = _run(mod, cfg)
    assert [f for f in rep.findings if f.category == "fit"] == []
    assert any("ship_x" in s.why for s in rep.skipped)


def test_nothing_here_is_ever_an_error(tmp_path):
    rep = _run(*_world(tmp_path, slot_tags="engine ror_xl_b", group_name="Group_Back", macro_conn="con_nowhere"))
    fit = [f for f in rep.findings if f.category == "fit"]
    assert fit and {f.severity for f in fit} == {"info"}


# ---- v4.1.1 review: hull-level skips, the denominator note, diff fragments, the library branch, filter twins ----

_MISFIT = "engine other_slot"  # a slot that lacks the equipment's attachment tag: the leaf fails the rule


def _fit(rep):
    return [f for f in rep.findings if f.category == "fit"]


def _hull_skips(rep, name):
    return [s for s in rep.skipped if s.what == "fitting checks" and f"'{name}'" in s.why]


def _note(rep):
    notes = [n for n in rep.notes if n.startswith("fitting checks:")]
    assert len(notes) == 1, rep.notes
    return notes[0]


def _add_hull(mod, name, *, comp=None, cls="ship_s", loadouts=1):
    comp_xml = f'<component ref="{comp}"/>' if comp else ""
    los = "".join(f'<loadout id="l{i}"><macros><engine macro="eng_macro" path="../con_engine_01"/></macros></loadout>'
                  for i in range(loadouts))
    _w(mod / f"assets/u/{name}.xml",
       f'<macros><macro name="{name}" class="{cls}">{comp_xml}<properties><loadouts>{los}</loadouts></properties>'
       '<connections><connection ref="con_nowhere"/></connections></macro></macros>')


def test_hull_with_unregistered_component_is_a_skip_not_silent(tmp_path):
    mod, cfg = _world(tmp_path)
    _add_hull(mod, "ship_orphan_macro", comp="ship_unregistered")
    rep = _run(mod, cfg)
    hits = _hull_skips(rep, "ship_orphan_macro")
    assert len(hits) == 1 and "not registered" in hits[0].why and not hits[0].degraded
    assert not [f for f in _fit(rep) if "ship_orphan_macro" in f.message]


def test_hull_whose_component_file_is_missing_is_a_skip(tmp_path):
    mod, cfg = _world(tmp_path)
    _w(cfg.reference / "index/components.xml",
       '<index><entry name="ship_x" value="assets/u/ship_x"/><entry name="eng" value="assets/p/eng"/>'
       '<entry name="ship_gone" value="assets/u/ship_gone"/></index>')
    _add_hull(mod, "ship_gone_macro", comp="ship_gone")
    hits = _hull_skips(_run(mod, cfg), "ship_gone_macro")
    assert len(hits) == 1 and "file is missing" in hits[0].why


def test_hull_without_component_ref_is_a_skip(tmp_path):
    mod, cfg = _world(tmp_path)
    _add_hull(mod, "ship_bare_macro", comp=None)
    hits = _hull_skips(_run(mod, cfg), "ship_bare_macro")
    assert len(hits) == 1 and "no <component ref>" in hits[0].why


def test_hull_skip_is_recorded_once_per_macro(tmp_path):
    """Two loadouts plus a connection ref on one unresolvable hull: one cause, one line."""
    mod, cfg = _world(tmp_path)
    _add_hull(mod, "ship_orphan_macro", comp="ship_unregistered", loadouts=2)
    assert len(_hull_skips(_run(mod, cfg), "ship_orphan_macro")) == 1


def test_clean_world_records_no_hull_skip_and_states_its_denominator(tmp_path):
    """Twin for every hull-skip test: a resolvable hull is judged, counted, and not skipped."""
    rep = _run(*_world(tmp_path))
    assert [s for s in rep.skipped if s.what == "fitting checks"] == []
    assert "1 macro(s) judged, 0 skipped" in _note(rep) and "NOT CHECKED" not in _note(rep)


def test_denominator_counts_skipped_hulls(tmp_path):
    mod, cfg = _world(tmp_path)
    _add_hull(mod, "ship_orphan_macro", comp="ship_unregistered")
    _add_hull(mod, "ship_bare_macro", comp=None)
    note = _note(_run(mod, cfg))
    assert "1 macro(s) judged, 2 skipped" in note and "(2 skip(s), see NOT CHECKED)" in note


def test_non_ship_class_macro_is_not_judged(tmp_path):
    """Twin for the class startswith('ship_') filter: same dangling ref, non-ship class -> no finding, no count."""
    rep = _run(*_world(tmp_path, macro_conn="con_nowhere", ship_class="station"))
    assert _fit(rep) == [] and "0 macro(s) examined" in _note(rep) and "judged" not in _note(rep).split(";")[0]
    assert _fit(_run(*_world(tmp_path / "twin", macro_conn="con_nowhere", ship_class="ship_s")))


def test_diff_fragment_leaves_are_a_skip_not_silent(tmp_path):
    mod, cfg = _world(tmp_path, slot_tags=_MISFIT)
    _w(mod / "assets/u/ship_new_patch.xml",
       "<diff><add sel=\"//macro[@name='ship_new_macro']/properties/loadouts/loadout[@id='default']/macros\">"
       '<engine macro="eng_macro" path="../con_engine_02"/></add></diff>')
    rep = _run(mod, cfg)
    hits = [s for s in rep.skipped if s.what == "fitting checks" and "diff fragment not judged" in s.why]
    assert len(hits) == 1 and "ship_new_patch.xml" in hits[0].why and "1 loadout" in hits[0].why
    assert "1 diff file(s)" in _note(rep)


def test_complete_macro_added_by_a_diff_is_judged_not_skipped(tmp_path):
    """Twin: a whole <macro> inside an <add> has its macro ancestor, so it is judged like a plain file."""
    mod, cfg = _world(tmp_path, slot_tags=_MISFIT)
    (mod / "assets/u/ship_new_macro.xml").unlink()
    _w(mod / "assets/u/ship_new_patch.xml",
       '<diff><add sel="/macros"><macro name="ship_diff_macro" class="ship_s"><component ref="ship_x"/>'
       '<properties><loadouts><loadout id="d"><macros><engine macro="eng_macro" path="../con_engine_01"/>'
       '</macros></loadout></loadouts></properties></macro></add></diff>')
    rep = _run(mod, cfg)
    assert [f for f in _fit(rep) if "ship_diff_macro" in f.message]
    assert not [s for s in rep.skipped if "diff fragment" in s.why]


def _library(tmp_path, cls):
    mod, cfg = _world(tmp_path, slot_tags=_MISFIT)
    (mod / "assets/u/ship_new_macro.xml").unlink()
    _w(cfg.reference / "index/macros.xml",
       '<index><entry name="eng_macro" value="assets/p/eng_macro"/><entry name="lib_macro" value="assets/u/lib_macro"/></index>')
    _w(cfg.reference / "assets/u/lib_macro.xml",
       f'<macros><macro name="lib_macro" class="{cls}"><component ref="ship_x"/></macro></macros>')
    _w(mod / "libraries/loadouts.xml",
       '<loadouts><loadout id="lib" macro="lib_macro"><macros><engine macro="eng_macro" path="../con_engine_01"/>'
       '</macros></loadout></loadouts>')
    return _fit(_run(mod, cfg))


def test_library_loadout_on_a_ship_is_judged_and_labelled_ship(tmp_path):
    hits = _library(tmp_path, "ship_m")
    assert len(hits) == 1 and hits[0].message.startswith("ship macro 'lib_macro': ")


def test_library_loadout_on_a_non_ship_is_labelled_by_its_class(tmp_path):
    """Twin: the //loadout[@macro] branch judges any macro, so it must not call a station a ship."""
    hits = _library(tmp_path, "station")
    assert len(hits) == 1 and hits[0].message.startswith("macro 'lib_macro' (class 'station'): ")


def test_leaf_kind_outside_the_fit_kinds_is_not_judged(tmp_path):
    """Twin for _FIT_LEAF_KINDS: the same misfit leaf as <software> is not a fitting leaf."""
    assert _fit(_run(*_world(tmp_path, slot_tags=_MISFIT, leaf_kind="software"))) == []
    assert _fit(_run(*_world(tmp_path / "twin", slot_tags=_MISFIT, leaf_kind="engine")))


def test_group_kind_outside_the_fit_kinds_is_not_judged(tmp_path):
    """Twin for _FIT_GROUP_KINDS: a case-only group name under <drones> is not counted."""
    assert _fit(_run(*_world(tmp_path, group_name="Group_Back", group_kind="drones"))) == []
    assert _fit(_run(*_world(tmp_path / "twin", group_name="Group_Back", group_kind="turrets")))


def test_equipment_with_two_attachments_is_a_skip_not_a_verdict(tmp_path):
    """Twin for len(att) == 1: two component-tagged attachments are ambiguous, so the leaf is skipped."""
    rep = _run(*_world(tmp_path, slot_tags=_MISFIT, eng_second_attach=True))
    assert _fit(rep) == [] and any("eng_macro" in s.why for s in rep.skipped)


def test_leaf_on_a_missing_port_is_not_a_tag_verdict(tmp_path):
    """Twin for `port not in ports`: existence is check_connections' finding, never a tag finding here."""
    assert _fit(_run(*_world(tmp_path, slot_tags=_MISFIT, leaf_port="con_engine_99"))) == []
    assert _fit(_run(*_world(tmp_path / "twin", slot_tags=_MISFIT, leaf_port="con_engine_01")))


# ---- v4.1.1 review round 2: the tag rule on <groups> entries, whitespace in group names, attachment edge cases ----
# A group entry places its macro into the slots of one connection group. Vanilla groups MIX kinds (a carrier's
# group_back_mid_mid holds engines and shields), so "every slot of the group accepts it" is the wrong reading: it
# rejects 945 of 946 resolvable vanilla entries. The judged reading is "NO slot of the group accepts it", which
# rejects 1 of 946 (MEASURED 2026-11-09, base + 8 DLC).

_BIG_TURRET = "turret component large"  # needs a `large` slot; the fixture's group slot is medium


def test_group_entry_fitting_no_slot_of_its_group_is_info(tmp_path):
    hits = _fit(_run(*_world(tmp_path, turret_tags=_BIG_TURRET)))
    assert len(hits) == 1 and hits[0].severity == "info"
    m = hits[0].message
    assert "1 of 1 group entries fit no slot of their group" in m and _TURRET in m and "group_back" in m
    assert "preset leaves fail" not in m


def test_group_entry_needs_one_fitting_slot_not_all(tmp_path):
    """Twin for the any-slot reading: a second group slot that does fit silences it; one that does not, does not."""
    assert _fit(_run(*_world(tmp_path, turret_tags=_BIG_TURRET,
                             extra_group_slot="turret combat large standard hittable"))) == []
    assert _fit(_run(*_world(tmp_path / "twin", turret_tags=_BIG_TURRET, extra_group_slot="shield medium")))


def test_fitting_group_entry_is_judged_and_silent(tmp_path):
    rep = _run(*_world(tmp_path))
    assert _fit(rep) == [] and "0 group entries not tag-judged" in _note(rep)


def test_group_absent_from_the_component_is_counted_not_judged(tmp_path):
    rep = _run(*_world(tmp_path, turret_tags=_BIG_TURRET, preset_group="group_nowhere"))
    assert _fit(rep) == []
    assert "1 group entries not tag-judged" in _note(rep)


def test_group_entry_with_unresolvable_equipment_is_a_skip(tmp_path):
    rep = _run(*_world(tmp_path, turret_tags=_BIG_TURRET, turret_registered=False))
    assert _fit(rep) == [] and any(_TURRET in s.why for s in rep.skipped)


def test_component_group_with_trailing_space_still_resolves(tmp_path):
    """Twin for the strip in connection_tags_of: the corpus writes `group="group_back "`."""
    rep = _run(*_world(tmp_path, turret_tags=_BIG_TURRET, group_name="group_back "))
    assert len(_fit(rep)) == 1 and "0 group entries not tag-judged" in _note(rep)


def test_preset_group_with_trailing_space_still_resolves(tmp_path):
    """Twin for the preset-side strip in judge_loadout."""
    rep = _run(*_world(tmp_path, turret_tags=_BIG_TURRET, preset_group="group_back "))
    assert len(_fit(rep)) == 1 and "0 group entries not tag-judged" in _note(rep)


def test_equipment_with_no_component_tagged_attachment_is_a_skip(tmp_path):
    """Twin for `"component" in tags`: zero attachments is unresolvable, never a verdict; with one it is judged."""
    rep = _run(*_world(tmp_path, slot_tags=_MISFIT, eng_tags="engine ror_xl_a"))
    assert _fit(rep) == [] and any("eng_macro" in s.why for s in rep.skipped)
    assert _fit(_run(*_world(tmp_path / "twin", slot_tags=_MISFIT, eng_tags="engine component ror_xl_a")))


def _frag_skips(rep):
    return [s for s in rep.skipped if s.what == "fitting checks" and "diff fragment not judged" in s.why]


def test_diff_adding_a_connection_ref_to_an_existing_macro_is_a_skip(tmp_path):
    """v4.1.1 review: a diff op adding <connection ref> into an existing ship macro was silently not judged."""
    mod, cfg = _world(tmp_path)
    _w(mod / "assets/u/ship_new_patch.xml",
       "<diff><add sel=\"//macro[@name='ship_new_macro']/connections\">"
       '<connection ref="con_nowhere"><macro ref="bridge_x_macro" connection="con_cockpit"/></connection></add></diff>')
    rep = _run(mod, cfg)
    hits = _frag_skips(rep)
    assert len(hits) == 1 and "1 macro connection ref(s)" in hits[0].why and "loadout leaf" not in hits[0].why
    assert "1 diff file(s)" in _note(rep)


def test_diff_replacing_a_connection_ref_attribute_is_a_skip(tmp_path):
    mod, cfg = _world(tmp_path)
    _w(mod / "assets/u/ship_new_patch.xml",
       "<diff><replace sel=\"//macro[@name='ship_new_macro']/connections/connection[@ref='con_cockpit']/@ref\">"
       "con_nowhere</replace></diff>")
    hits = _frag_skips(_run(mod, cfg))
    assert len(hits) == 1 and "1 macro connection ref(s)" in hits[0].why


def test_connection_ref_inside_a_whole_macro_added_by_a_diff_is_judged_not_skipped(tmp_path):
    """Twin: the <connection ref> has its <macro> ancestor, so it is judged like a plain file."""
    mod, cfg = _world(tmp_path)
    (mod / "assets/u/ship_new_macro.xml").unlink()
    _w(mod / "assets/u/ship_new_patch.xml",
       '<diff><add sel="/macros"><macro name="ship_diff_macro" class="ship_s"><component ref="ship_x"/>'
       '<connections><connection ref="con_nowhere"/></connections></macro></add></diff>')
    rep = _run(mod, cfg)
    assert [f for f in _fit(rep) if "ship_diff_macro" in f.message and "connection refs" in f.message]
    assert _frag_skips(rep) == []


def test_component_connection_added_by_a_diff_is_not_a_macro_ref_fragment(tmp_path):
    """Twin: a component's <connection name=> carries no ref, so a component diff is not miscounted."""
    mod, cfg = _world(tmp_path)
    _w(mod / "assets/u/ship_x_patch.xml",
       "<diff><add sel=\"//component[@name='ship_x']/connections\">"
       '<connection name="con_new_01" tags="engine"/></add></diff>')
    assert _frag_skips(_run(mod, cfg)) == []


def test_diff_adding_a_ref_attribute_to_a_connection_is_a_skip_and_elsewhere_is_not(tmp_path):
    """One twin per clause of the type="@ref" branch: on a connection it counts; on another element it does not."""
    mod, cfg = _world(tmp_path)
    _w(mod / "assets/u/ship_new_patch.xml",
       "<diff><add sel=\"//macro[@name='ship_new_macro']/connections/connection[1]\" type=\"@ref\">con_nowhere</add></diff>")
    assert len(_frag_skips(_run(mod, cfg))) == 1
    mod, cfg = _world(tmp_path / "twin")
    _w(mod / "assets/u/ship_new_patch.xml",
       "<diff><add sel=\"//macro[@name='ship_new_macro']/component\" type=\"@ref\">ship_x</add></diff>")
    assert _frag_skips(_run(mod, cfg)) == []

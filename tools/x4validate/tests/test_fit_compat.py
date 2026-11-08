"""check_fitting_compatibility: the three fitting questions the connection check never asked (BLIND-SPOTS F363).

Every finding here is INFO: the documented tag rule rejects 0 of 1421 vanilla preset leaves (MEASURED 2026-11-08),
so it is safe as a screen, but whether the ENGINE rejects a mismatch is unmeasured -- a hit is a suspect to test.
One falsification twin per clause: each test flips exactly one input and asserts the finding appears or vanishes."""

from pathlib import Path

from x4validate import _check, _merge


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path, *, slot_tags="engine ror_xl_a", eng_tags="engine component ror_xl_a",
           group_name="group_back", preset_group="group_back", macro_conn="con_cockpit", eng_registered=True,
           leaf_kind="engine", leaf_port="con_engine_01", group_kind="turrets", eng_second_attach=False,
           ship_class="ship_xl"):
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", '<index><entry name="eng_macro" value="assets/p/eng_macro"/></index>'
       if eng_registered else "<index/>")
    _w(ref / "index/components.xml",
       '<index><entry name="ship_x" value="assets/u/ship_x"/><entry name="eng" value="assets/p/eng"/></index>')
    _w(ref / "assets/u/ship_x.xml",
       '<components><component name="ship_x"><connections>'
       f'<connection name="con_engine_01" tags="{slot_tags}"/>'
       f'<connection name="con_turret_01" group="{group_name}" tags="turret combat medium standard hittable"/>'
       '<connection name="con_cockpit" tags="cockpit"/>'
       '</connections></component></components>')
    _w(ref / "assets/p/eng.xml",
       f'<components><component name="eng" class="engine"><connections><connection name="EngineConnection01" tags="{eng_tags}"/>'
       + ('<connection name="EngineConnection02" tags="engine component other_mount"/>' if eng_second_attach else "")
       + '</connections></component></components>')
    _w(ref / "assets/p/eng_macro.xml",
       '<macros><macro name="eng_macro" class="engine"><component ref="eng"/></macro></macros>')
    mod = tmp_path / "mod"
    _w(mod / "assets/u/ship_new_macro.xml",
       f'<macros><macro name="ship_new_macro" class="{ship_class}"><component ref="ship_x"/>'
       '<properties><loadouts><loadout id="default"><macros>'
       f'<{leaf_kind} macro="eng_macro" path="../{leaf_port}"/>'
       '</macros><groups>'
       f'<{group_kind} macro="turret_arg_m_gatling_01_mk1_macro" path=".." group="{preset_group}" exact="1"/>'
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
    assert "1 macro(s) judged, 0 skipped" in _note(rep)


def test_denominator_counts_skipped_hulls(tmp_path):
    mod, cfg = _world(tmp_path)
    _add_hull(mod, "ship_orphan_macro", comp="ship_unregistered")
    _add_hull(mod, "ship_bare_macro", comp=None)
    assert "1 macro(s) judged, 2 skipped" in _note(_run(mod, cfg))


def test_non_ship_class_macro_is_not_judged(tmp_path):
    """Twin for the class startswith('ship_') filter: same dangling ref, non-ship class -> no finding, no count."""
    rep = _run(*_world(tmp_path, macro_conn="con_nowhere", ship_class="station"))
    assert _fit(rep) == [] and "0 macro(s) judged, 0 skipped" in _note(rep)
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

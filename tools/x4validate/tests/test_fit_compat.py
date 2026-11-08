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
           group_name="group_back", preset_group="group_back", macro_conn="con_cockpit", eng_registered=True):
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
       '</connections></component></components>')
    _w(ref / "assets/p/eng_macro.xml",
       '<macros><macro name="eng_macro" class="engine"><component ref="eng"/></macro></macros>')
    mod = tmp_path / "mod"
    _w(mod / "assets/u/ship_new_macro.xml",
       '<macros><macro name="ship_new_macro" class="ship_xl"><component ref="ship_x"/>'
       '<properties><loadouts><loadout id="default"><macros>'
       '<engine macro="eng_macro" path="../con_engine_01"/>'
       '</macros><groups>'
       f'<turrets macro="turret_arg_m_gatling_01_mk1_macro" path=".." group="{preset_group}" exact="1"/>'
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
    hits = [f for f in rep.findings if f.category == "fit" and "ror_xl_a" in f.message]
    assert len(hits) == 1 and hits[0].severity == "info"
    assert "con_engine_01" in hits[0].message and "eng_macro" in hits[0].message


def test_group_case_only_match_is_info_and_exact_match_is_silent(tmp_path):
    rep = _run(*_world(tmp_path, group_name="Group_Back"))
    hits = [f for f in rep.findings if f.category == "fit" and "case" in f.message]
    assert len(hits) == 1 and hits[0].severity == "info" and "Group_Back" in hits[0].message
    assert not [f for f in _run(*_world(tmp_path)).findings if "case" in f.message]


def test_macro_connection_ref_absent_is_info(tmp_path):
    rep = _run(*_world(tmp_path, macro_conn="con_nowhere"))
    hits = [f for f in rep.findings if f.category == "fit" and "con_nowhere" in f.message]
    assert len(hits) == 1 and hits[0].severity == "info"


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
    assert len(fit) == 3 and {f.severity for f in fit} == {"info"}

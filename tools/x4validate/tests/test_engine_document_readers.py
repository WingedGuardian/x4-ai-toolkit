"""Issue #23 residual: the index-file readers must read the document the ENGINE uses.

`_resolve.build_index` collects `<entry>` elements and ignores `<replace>`/`<remove>` ops, and
`_resolve.read_indexed` returns ONE physical file. When a copier's complete ship macro merges at
the plain path (F382) and the effective index sends the name there (F383), the engine's document
is the MERGED plain-path one, whose first node of the name is the game's; `read_indexed` read the
copier's file instead. MEASURED shape (read-only, the installed modlist): a later overlay's index
`<replace>` re-points the carrier macro back at the base path, so the engine used the base macro's
component, while x4validate's connection check reported the loadout against the copier's component
(1 false error on a mod whose new-game log has 0 errors).
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _check, _merge

REL = "assets/units/size_xl/macros/ship_x_macro.xml"
IDX_BASE = r"assets\units\size_xl\macros\ship_x_macro"
IDX_OWN = r"extensions\a_copy\assets\units\size_xl\macros\ship_x_macro"


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _manifest(d: Path, mid: str) -> None:
    _w(d / "content.xml", f'<content id="{mid}" name="{mid}" version="1"/>')


def _macro(component: str) -> str:
    return (f'<macros><macro name="ship_x_macro" class="ship_xl"><component ref="{component}"/>'
            '<properties/></macro></macros>')


def _component(name: str, conn: str) -> str:
    return (f'<components><component name="{name}" class="ship_xl"><connections>'
            f'<connection name="{conn}" tags="engine"/></connections></component></components>')


def _world(tmp_path: Path, repoint_back: bool = True, copier: bool = True):
    """Base macro -> component ship_x (connection c_a). a_copy ships a COMPLETE copy of the
    macro naming ship_other (connection c_b) and ADDS an index entry at its own path, as the
    measured copier does; c_repoint (later) re-points the entry back at the base path with a
    <replace>, as the measured overlay does. d_lo ships a loadout for the macro on c_a."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / REL, _macro("ship_x"))
    _w(ref / "assets/units/size_xl/ship_x.xml", _component("ship_x", "c_a"))
    _w(ref / "index" / "macros.xml", f'<index><entry name="ship_x_macro" value="{IDX_BASE}"/></index>')
    _w(ref / "index" / "components.xml",
       r'<index><entry name="ship_x" value="assets\units\size_xl\ship_x"/></index>')
    ext = tmp_path / "extensions"
    a, c, d = ext / "a_copy", ext / "c_repoint", ext / "d_lo"
    roots = []
    if copier:
        _manifest(a, "a_copy")
        _w(a / REL, _macro("ship_other"))
        _w(a / "assets/units/size_xl/ship_other.xml", _component("ship_other", "c_b"))
        _w(a / "index" / "macros.xml",
           f'<diff><add sel="/index"><entry name="ship_x_macro" value="{IDX_OWN}"/></add></diff>')
        _w(a / "index" / "components.xml",
           r'<diff><add sel="/index"><entry name="ship_other" '
           r'value="extensions\a_copy\assets\units\size_xl\ship_other"/></add></diff>')
        roots.append(a)
    if repoint_back:
        _manifest(c, "c_repoint")
        _w(c / "index" / "macros.xml",
           "<diff><replace sel=\"/index/entry[@name='ship_x_macro'][last()]/@value\">"
           f"{IDX_BASE}</replace></diff>")
        roots.append(c)
    _manifest(d, "d_lo")
    _w(d / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="ship_x_macro"><macros>'
       '<engine macro="engine_x_macro" path="../c_a"/></macros></loadout></loadouts>')
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=tuple(roots))
    return d, cfg


def _connection_errors(rep):
    return [f for f in rep.errors if f.category == "connection"]


def test_the_loadout_is_judged_against_the_component_the_engine_uses(tmp_path):
    d, cfg = _world(tmp_path)
    rep = _check.validate(d, cfg)
    assert _connection_errors(rep) == [], rep.findings


def test_the_reader_discloses_that_the_copy_choice_is_inferred(tmp_path):
    d, cfg = _world(tmp_path)
    rep = _check.validate(d, cfg)
    notes = [n for n in rep.notes if "copy [1]" in n and "ship_x_macro" in n]
    assert len(notes) == 1 and "UNMEASURED" in notes[0], rep.notes


def test_TWIN_the_index_left_at_the_copier_reads_the_copier_s_own_document(tmp_path):
    """F383 own-document: the engine loads the copier's file, so ship_other IS right."""
    d, cfg = _world(tmp_path, repoint_back=False)
    errs = _connection_errors(_check.validate(d, cfg))
    assert len(errs) == 1 and "ship_other" in errs[0].message, errs


def test_TWIN_no_copier_reads_the_base_macro_as_before(tmp_path):
    d, cfg = _world(tmp_path, copier=False, repoint_back=False)
    assert _connection_errors(_check.validate(d, cfg)) == []


def test_TWIN_a_loadout_wrong_for_the_engine_s_component_is_still_an_error(tmp_path):
    """The merged reader must not excuse a real miss: c_b is on the copier's component only."""
    d, cfg = _world(tmp_path)
    _w(d / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="ship_x_macro"><macros>'
       '<engine macro="engine_x_macro" path="../c_b"/></macros></loadout></loadouts>')
    errs = _connection_errors(_check.validate(d, cfg))
    assert len(errs) == 1 and "'ship_x'" in errs[0].message, errs


COMP = "assets/units/size_xl/ship_x.xml"


def test_a_MERGED_component_gives_the_engine_s_first_component_s_connections(tmp_path):
    """The component half (the measured fighter_02 shape): a copier's complete component file
    over the game's, the index re-pointed back at the base path."""
    d, cfg = _world(tmp_path, copier=False, repoint_back=False)
    ref = cfg.reference
    a = tmp_path / "extensions" / "a_comp"
    c = tmp_path / "extensions" / "c_back"
    _manifest(a, "a_comp")
    _manifest(c, "c_back")
    _w(a / COMP, _component("ship_x", "c_b"))
    _w(a / "index" / "components.xml",
       '<diff><add sel="/index"><entry name="ship_x" '
       'value="extensions/a_comp/assets/units/size_xl/ship_x"/></add></diff>')
    _w(c / "index" / "components.xml",
       "<diff><replace sel=\"/index/entry[@name='ship_x'][last()]/@value\">"
       "assets/units/size_xl/ship_x</replace></diff>")
    _w(d / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="ship_x_macro"><macros>'
       '<engine macro="engine_x_macro" path="../c_b"/></macros></loadout></loadouts>')
    cfg = _merge.replace(cfg, overlays=(a, c))
    errs = _connection_errors(_check.validate(d, cfg))
    assert len(errs) == 1 and "'c_b'" in errs[0].message and "'ship_x'" in errs[0].message, errs
    assert ref.is_dir()


def test_file_existence_follows_the_engine_s_component_too(tmp_path):
    """check_file_existence: the copier's macro names a component nobody registers; the engine's
    document (the base macro) names ship_x, which exists. No 'not registered' error."""
    d, cfg = _world(tmp_path)
    a = tmp_path / "extensions" / "a_copy"
    (a / "index" / "components.xml").unlink()
    _w(d / "libraries" / "wares.xml",
       '<diff><add sel="/wares"><ware id="w_x"><component ref="ship_x_macro"/></ware></add></diff>')
    rep = _check.validate(d, cfg)
    assert [f for f in rep.errors if f.category == "file"] == [], rep.findings


def test_TWIN_file_existence_with_the_index_left_at_the_copier_reports_its_component(tmp_path):
    d, cfg = _world(tmp_path, repoint_back=False)
    a = tmp_path / "extensions" / "a_copy"
    (a / "index" / "components.xml").unlink()
    _w(d / "libraries" / "wares.xml",
       '<diff><add sel="/wares"><ware id="w_x"><component ref="ship_x_macro"/></ware></add></diff>')
    errs = [f for f in _check.validate(d, cfg).errors if f.category == "file"]
    assert len(errs) == 1 and "ship_other" in errs[0].message, errs


def test_an_unreadable_index_is_a_degraded_skip_and_no_verdict(tmp_path):
    """A partial runtime index: which document the engine loads is unknown, so no connection
    verdict either way, and the run says so (review minor)."""
    d, cfg = _world(tmp_path)
    bad = tmp_path / "extensions" / "e_bad"
    _manifest(bad, "e_bad")
    _w(bad / "index" / "macros.xml", "<diff><add sel='/index'>")
    cfg = _merge.replace(cfg, overlays=tuple(cfg.overlays) + (bad,))
    rep = _check.validate(d, cfg)
    assert _connection_errors(rep) == [], rep.findings
    assert [s for s in rep.skipped if s.degraded and "e_bad" in s.why], rep.skipped
    # EngineDocs' own skip: the copied names are left unjudged, not judged on a partial index.
    assert [s for s in rep.skipped
            if s.degraded and s.what == "engine document (index/macros.xml)"], rep.skipped


def test_index_names_ignore_case_here_too(tmp_path):
    """A loadout naming the macro in another case still reaches the engine's component (a
    case-sensitive lookup finds no component at all and judges nothing)."""
    d, cfg = _world(tmp_path)
    _w(d / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="SHIP_X_MACRO"><macros>'
       '<engine macro="engine_x_macro" path="../c_b"/></macros></loadout></loadouts>')
    errs = _connection_errors(_check.validate(d, cfg))
    assert len(errs) == 1 and "'ship_x'" in errs[0].message, errs


import pytest  # noqa: E402


@pytest.mark.parametrize("where", ["outside_class", "no_game_file"])
def test_a_later_index_replace_to_a_second_copier_is_followed_everywhere(tmp_path, where):
    """A later index <replace> points the name at a SECOND copier's own path. Since build_index
    reads the EFFECTIVE index (review I-3) the reader follows it in and outside the class: an
    `extensions/<X>/` value is X's file however the entry got there. (This twin pinned the old,
    op-blind reader until the general fix.)"""
    rel = ("assets/fx/weaponfx/macros/ship_x_macro.xml" if where == "outside_class" else REL)
    d, cfg = _world(tmp_path, repoint_back=False)
    ref, a = cfg.reference, cfg.overlays[0]
    if where == "outside_class":
        (a / REL).unlink()
        _w(a / rel, _macro("ship_other"))
        _w(ref / rel, _macro("ship_x"))
        (ref / REL).unlink()
        _w(a / "index" / "macros.xml",
           '<diff><add sel="/index"><entry name="ship_x_macro" '
           'value="extensions/a_copy/assets/fx/weaponfx/macros/ship_x_macro"/></add></diff>')
    else:
        (ref / REL).unlink()
    c2 = tmp_path / "extensions" / "c2_copy"
    _manifest(c2, "c2_copy")
    _w(c2 / rel, _macro("ship_x"))
    _w(c2 / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='ship_x_macro'][last()]/@value\">"
       f"extensions/c2_copy/{rel[:-4]}</replace></diff>")
    cfg = _merge.replace(cfg, overlays=(a, c2))
    assert _connection_errors(_check.validate(d, cfg)) == []


def test_TWIN_with_no_copy_merged_the_stored_file_is_read_as_before(tmp_path):
    """Clause `a copy merged`: with only a DIFF re-pointing the component, the plain-path
    document is base+diff, but check_connections reads components AS STORED (its documented
    scope limit) -- this rule does not widen that."""
    d, cfg = _world(tmp_path, copier=False, repoint_back=False)
    ref = cfg.reference
    _w(ref / "assets/units/size_xl/ship_y.xml", _component("ship_y", "c_z"))
    _w(ref / "index" / "components.xml",
       '<index><entry name="ship_x" value="assets/units/size_xl/ship_x"/>'
       '<entry name="ship_y" value="assets/units/size_xl/ship_y"/></index>')
    p = tmp_path / "extensions" / "p_diff"
    _manifest(p, "p_diff")
    _w(p / REL, "<diff><replace sel=\"/macros/macro[@name='ship_x_macro']/component/@ref\">"
       "ship_y</replace></diff>")
    cfg = _merge.replace(cfg, overlays=(p,))
    assert _connection_errors(_check.validate(d, cfg)) == []


# --- review of the residual: build_index from the EFFECTIVE index (I-3), load position (I-2),
#     a silent fallback (I-1), the fitting path (I-4), one matched node -------------------

from x4validate import _resolve  # noqa: E402


def _base_index_world(tmp_path):
    """A plain reference with two macros in the index; return (ref, ext)."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / REL, _macro("ship_x"))
    _w(ref / "assets/units/size_xl/ship_x.xml", _component("ship_x", "c_a"))
    _w(ref / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_q"/></macro></macros>')
    _w(ref / "index" / "macros.xml",
       f'<index><entry name="ship_x_macro" value="{IDX_BASE}"/>'
       r'<entry name="bullet_q_macro" value="assets\fx\weaponfx\macros\bullet_q_macro"/></index>')
    _w(ref / "index" / "components.xml",
       r'<index><entry name="ship_x" value="assets\units\size_xl\ship_x"/></index>')
    return ref, tmp_path / "extensions"


def test_build_index_applies_a_REMOVE(tmp_path):
    """I-3: a removed name is not registered (4 such names were on the live list)."""
    ref, ext = _base_index_world(tmp_path)
    m = ext / "m_rm"
    _manifest(m, "m_rm")
    _w(m / "index" / "macros.xml", "<diff><remove sel=\"/index/entry[@name='bullet_q_macro']\"/></diff>")
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False, overlays=(m,)),
                               [], _resolve.MACRO_INDEX)
    assert "bullet_q_macro" not in idx and "ship_x_macro" in idx


def test_build_index_applies_a_value_REPLACE_outside_the_class(tmp_path):
    """I-3: an @value <replace> re-points a weapon macro (2 such names on the live list read the
    copier's file); here a mod adds its own entry and a later one re-points it back."""
    ref, ext = _base_index_world(tmp_path)
    a, c = ext / "a_add", ext / "c_back"
    _manifest(a, "a_add")
    _manifest(c, "c_back")
    _w(a / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_other"/></macro></macros>')
    _w(a / "index" / "macros.xml",
       '<diff><add sel="/index"><entry name="bullet_q_macro" '
       r'value="extensions\a_add\assets\fx\weaponfx\macros\bullet_q_macro"/></add></diff>')
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro'][last()]/@value\">"
       r"assets\fx\weaponfx\macros\bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(a, c))
    located = _resolve.read_indexed(_resolve.build_index(cfg, [], _resolve.MACRO_INDEX), "bullet_q_macro")
    assert located is not None and b"bullet_q" in located.data and b"bullet_other" not in located.data


def test_TWIN_build_index_a_mod_s_bare_value_for_its_OWN_new_file_stays_in_that_mod(tmp_path):
    ref, ext = _base_index_world(tmp_path)
    n = ext / "n_new"
    _manifest(n, "n_new")
    _w(n / "assets/units/size_s/macros/ship_new_macro.xml",
       '<macros><macro name="ship_new_macro" class="ship_s"/></macros>')
    _w(n / "index" / "macros.xml",
       '<diff><add sel="/index"><entry name="ship_new_macro" '
       r'value="assets\units\size_s\macros\ship_new_macro"/></add></diff>')
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False, overlays=(n,)),
                               [], _resolve.MACRO_INDEX)
    assert idx["ship_new_macro"][0] == n


def test_TWIN_build_index_a_COMPLETE_mod_index_file_still_registers_its_entries(tmp_path):
    ref, ext = _base_index_world(tmp_path)
    n = ext / "n_full"
    _manifest(n, "n_full")
    _w(n / "index" / "macros.xml",
       r'<index><entry name="ship_full_macro" value="assets\units\size_s\macros\ship_full_macro"/></index>')
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False, overlays=(n,)),
                               [], _resolve.MACRO_INDEX)
    assert "ship_full_macro" in idx and "ship_x_macro" in idx


def test_TWIN_build_index_an_unreadable_mod_index_is_still_a_degraded_skip(tmp_path):
    ref, ext = _base_index_world(tmp_path)
    bad = ext / "e_bad"
    _manifest(bad, "e_bad")
    _w(bad / "index" / "macros.xml", "<diff><add sel='/index'>")
    rep = _check.Report()
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False, overlays=(bad,)),
                               [], _resolve.MACRO_INDEX, rep)
    assert "ship_x_macro" in idx
    assert [s for s in rep.skipped if s.degraded and "e_bad" in s.why], rep.skipped


def test_the_mod_under_test_sits_at_its_LOAD_POSITION_in_the_runtime_index(tmp_path):
    """I-2 (probe B4): validating the COPIER while a LATER mod re-points the entry back. The
    runtime index must apply the copier's edit before the later re-point, not after it."""
    d, cfg = _world(tmp_path)
    a, c = cfg.overlays
    _w(a / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="ship_x_macro"><macros>'
       '<engine macro="engine_x_macro" path="../c_a"/></macros></loadout></loadouts>')
    tier_b = _merge.Config(reference=cfg.reference, include_packed_dlc=False,
                           overlays=(), final_overlays=(c,))
    assert _connection_errors(_check.validate(a, tier_b)) == []


def test_TWIN_an_UNKNOWN_load_position_is_disclosed(tmp_path):
    d, cfg = _world(tmp_path)
    rep = _check.validate(d, cfg)    # explicit overlays, no Tier B tree: position unknown
    assert [n for n in rep.notes if "load position" in n and "LAST" in n], rep.notes


def test_a_merged_document_that_cannot_be_built_is_a_degraded_skip_not_a_verdict(tmp_path):
    """I-1 (probe B2): a third mod's malformed file at the macro path leaves the merged document
    unknown; no connection verdict is drawn from the copier's file."""
    d, cfg = _world(tmp_path)
    m = tmp_path / "extensions" / "m_badpatch"
    _manifest(m, "m_badpatch")
    _w(m / REL, "<diff><replace sel='/macros'>")
    cfg = _merge.replace(cfg, overlays=tuple(cfg.overlays) + (m,))
    rep = _check.validate(d, cfg)
    assert _connection_errors(rep) == [], rep.findings
    assert [s for s in rep.skipped if s.degraded and "ship_x_macro" in s.why], rep.skipped


def _fit_world(tmp_path, repoint_back=True):
    """The fitting path: both components have slot c_a, with different size tags; the engine
    macro's attachment needs 'small'."""
    d, cfg = _world(tmp_path, repoint_back=repoint_back)
    ref, a = cfg.reference, cfg.overlays[0]
    _w(ref / "assets/units/size_xl/ship_x.xml",
       '<components><component name="ship_x" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine small"/></connections></component></components>')
    _w(a / "assets/units/size_xl/ship_other.xml",
       '<components><component name="ship_other" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine large"/></connections></component></components>')
    _w(ref / "assets/props/engines/macros/engine_x_macro.xml",
       '<macros><macro name="engine_x_macro" class="engine"><component ref="engine_x"/></macro></macros>')
    _w(ref / "assets/props/engines/engine_x.xml",
       '<components><component name="engine_x" class="engine"><connections>'
       '<connection name="con" tags="component engine small"/></connections></component></components>')
    _w(ref / "index" / "macros.xml",
       f'<index><entry name="ship_x_macro" value="{IDX_BASE}"/>'
       r'<entry name="engine_x_macro" value="assets\props\engines\macros\engine_x_macro"/></index>')
    _w(ref / "index" / "components.xml",
       r'<index><entry name="ship_x" value="assets\units\size_xl\ship_x"/>'
       r'<entry name="engine_x" value="assets\props\engines\engine_x"/></index>')
    return d, cfg


def _fit_hits(rep):
    return [f for f in rep.findings if f.category == "fit" and "tag rule" in f.message]


def test_the_FITTING_check_judges_the_engine_s_component(tmp_path):
    """I-4: the reviewer's plugin disabling EngineDocs inside check_fitting_compatibility left
    every test green. The engine's component (ship_x, small slot) fits the small engine."""
    d, cfg = _fit_world(tmp_path)
    assert _fit_hits(_check.validate(d, cfg)) == []


def test_TWIN_the_FITTING_check_on_the_copier_s_own_document_reports_the_misfit(tmp_path):
    d, cfg = _fit_world(tmp_path, repoint_back=False)
    hits = _fit_hits(_check.validate(d, cfg))
    assert len(hits) == 1 and "ship_x_macro" in hits[0].message, hits


def test_a_component_file_with_SEVERAL_components_reads_only_the_matched_one(tmp_path):
    """The reader scopes to the matched <component>: a connection of ANOTHER component in the
    same merged file is not the hull's."""
    d, cfg = _world(tmp_path, copier=False, repoint_back=False)
    ref = cfg.reference
    _w(ref / "assets/units/size_xl/ship_x.xml",
       '<components><component name="ship_x" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine"/></connections></component>'
       '<component name="ship_z" class="ship_xl"><connections>'
       '<connection name="c_b" tags="engine"/></connections></component></components>')
    a, c = tmp_path / "extensions" / "a_comp", tmp_path / "extensions" / "c_back"
    _manifest(a, "a_comp")
    _manifest(c, "c_back")
    _w(a / "assets/units/size_xl/ship_x.xml", _component("ship_x", "c_q"))
    _w(a / "index" / "components.xml",
       '<diff><add sel="/index"><entry name="ship_x" '
       'value="extensions/a_comp/assets/units/size_xl/ship_x"/></add></diff>')
    _w(c / "index" / "components.xml",
       "<diff><replace sel=\"/index/entry[@name='ship_x'][last()]/@value\">"
       "assets/units/size_xl/ship_x</replace></diff>")
    _w(d / "libraries" / "loadouts.xml",
       '<loadouts><loadout id="lo_x" macro="ship_x_macro"><macros>'
       '<engine macro="engine_x_macro" path="../c_b"/></macros></loadout></loadouts>')
    errs = _connection_errors(_check.validate(d, _merge.replace(cfg, overlays=(a, c))))
    assert len(errs) == 1 and "'c_b'" in errs[0].message, errs


def test_TWIN_a_bare_re_point_with_NO_game_file_lands_on_the_last_mod_that_ships_one(tmp_path):
    ref, ext = _base_index_world(tmp_path)
    (ref / "assets/fx/weaponfx/macros/bullet_q_macro.xml").unlink()
    a, c = ext / "a_add", ext / "c_back"
    _manifest(a, "a_add")
    _manifest(c, "c_back")
    _w(a / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_other"/></macro></macros>')
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro']/@value\">"
       "assets/fx/weaponfx/macros/bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(a, c))
    assert _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)["bullet_q_macro"][0] == a



def _misfit_world(tmp_path):
    """The engine's hull component (ship_x) has slot c_a tagged LARGE; the engine is small."""
    d, cfg = _fit_world(tmp_path)
    _w(cfg.reference / "assets/units/size_xl/ship_x.xml",
       '<components><component name="ship_x" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine large"/></connections></component></components>')
    return d, cfg


def test_the_FITTING_check_skips_a_hull_whose_merged_document_cannot_be_built(tmp_path):
    """I-1 on the fitting path: no INFO drawn from a file the engine may not use."""
    d, cfg = _misfit_world(tmp_path)
    assert len(_fit_hits(_check.validate(d, cfg))) == 1, "control: the misfit is seen"
    m = tmp_path / "extensions" / "m_badpatch"
    _manifest(m, "m_badpatch")
    _w(m / REL, "<diff><replace sel='/macros'>")
    rep = _check.validate(d, _merge.replace(cfg, overlays=tuple(cfg.overlays) + (m,)))
    assert _fit_hits(rep) == [], rep.findings
    assert [s for s in rep.skipped if s.degraded and "ship_x_macro" in s.why], rep.skipped


def test_the_FITTING_check_reads_only_the_matched_component_of_a_merged_file(tmp_path):
    """I-4: in a merged component file holding two components, the hull's slot tags are its
    own, not the other component's (a whole-file read takes the LAST c_a it meets)."""
    d, cfg = _fit_world(tmp_path, repoint_back=False)
    ref = cfg.reference
    a = cfg.overlays[0]
    (a / "index" / "macros.xml").unlink()        # the macro stays the game's
    _w(ref / "assets/units/size_xl/ship_x.xml",
       '<components><component name="ship_x" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine small"/></connections></component>'
       '<component name="ship_z" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine large"/></connections></component></components>')
    comp, back = tmp_path / "extensions" / "a_comp", tmp_path / "extensions" / "c_back"
    _manifest(comp, "a_comp")
    _manifest(back, "c_back")
    _w(comp / "assets/units/size_xl/ship_x.xml",
       '<components><component name="ship_x" class="ship_xl"><connections>'
       '<connection name="c_a" tags="engine huge"/></connections></component></components>')
    _w(comp / "index" / "components.xml",
       '<diff><add sel="/index"><entry name="ship_x" '
       'value="extensions/a_comp/assets/units/size_xl/ship_x"/></add></diff>')
    _w(back / "index" / "components.xml",
       "<diff><replace sel=\"/index/entry[@name='ship_x'][last()]/@value\">"
       "assets/units/size_xl/ship_x</replace></diff>")
    rep = _check.validate(d, _merge.replace(cfg, overlays=(a, comp, back)))
    assert _fit_hits(rep) == [], rep.findings


def test_file_existence_judges_nothing_when_the_merged_document_cannot_be_built(tmp_path):
    """I-1 on the file-existence path. The game's macro names a component nobody registers here,
    so a verdict from the file would be an error; with the merged document unknown (a third mod's
    malformed patch at the path) no verdict is drawn and the skip says why."""
    d, cfg = _world(tmp_path)
    _w(cfg.reference / "index" / "components.xml", "<index/>")
    _w(d / "libraries" / "wares.xml",
       '<diff><add sel="/wares"><ware id="w_x"><component ref="ship_x_macro"/></ware></add></diff>')
    files = [f for f in _check.validate(d, cfg).errors if f.category == "file"]
    assert len(files) == 1, "control: the unregistered component is reported"
    m = tmp_path / "extensions" / "m_badpatch"
    _manifest(m, "m_badpatch")
    _w(m / REL, "<diff><replace sel='/macros'>")
    rep = _check.validate(d, _merge.replace(cfg, overlays=tuple(cfg.overlays) + (m,)))
    assert [f for f in rep.errors if f.category == "file"] == [], rep.findings
    assert [s for s in rep.skipped if s.degraded and "ship_x_macro" in s.why], rep.skipped


def test_a_bare_re_point_by_a_mod_that_ships_only_a_DIFF_there_lands_on_the_game_file(tmp_path):
    """The measured re-pointing overlay also PATCHES the file it re-points to; its diff is no
    document, so the entry resolves to the game's file, not to the overlay's diff."""
    ref, ext = _base_index_world(tmp_path)
    c = ext / "c_back"
    _manifest(c, "c_back")
    _w(c / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       "<diff><replace sel=\"/macros/macro/@class\">bullet</replace></diff>")
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro']/@value\">"
       "assets/fx/weaponfx/macros/bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(c,))
    assert _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)["bullet_q_macro"][0] == ref


# --- round 5: a bare value whose writer ADDS the name through a diff; the DLC step --------

CL = "maps/x_universe/clusters.xml"


def _cluster_world(tmp_path, game_defines: bool = False):
    """The measured shape: the game ships a complete clusters file WITHOUT the name; a mod's
    <diff> at that path ADDS the macro and the mod registers it with a bare value."""
    ref, ext = _base_index_world(tmp_path)
    extra = '<macro name="cluster_new_macro" class="cluster"><component ref="game_cl"/></macro>' \
        if game_defines else ""
    _w(ref / CL, f'<macros><macro name="cluster_base_macro" class="cluster"/>{extra}</macros>')
    r = ext / "r_clusters"
    _manifest(r, "r_clusters")
    _w(r / CL, '<diff><add sel="/macros"><macro name="cluster_new_macro" class="cluster">'
               '<component ref="cl_new_comp"/></macro></add></diff>')
    _w(r / "index" / "macros.xml",
       '<diff><add sel="/index"><entry name="cluster_new_macro" '
       r'value="maps\x_universe\clusters"/></add></diff>')
    return ref, r


def test_a_bare_value_whose_writer_ADDS_the_name_by_diff_reads_the_merged_document(tmp_path):
    """Round-5 minor 1: the game's raw file has no such macro, so reading it narrowed the data
    to nothing and the component chain silently came back empty."""
    ref, r = _cluster_world(tmp_path)
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    located = _resolve.read_indexed(_resolve.build_index(cfg, [], _resolve.MACRO_INDEX),
                                    "cluster_new_macro")
    assert located is not None and b"cluster_new_macro" in located.data, located
    msgs = _resolve.macro_component_links(
        "cluster_new_macro", _resolve.build_index(cfg, [], _resolve.MACRO_INDEX),
        _resolve.build_index(cfg, [], _resolve.COMPONENT_INDEX))
    assert len(msgs) == 1 and "cl_new_comp" in msgs[0] and "not registered" in msgs[0], msgs


def test_TWIN_the_game_s_file_defining_the_name_is_read_as_stored(tmp_path):
    ref, r = _cluster_world(tmp_path, game_defines=True)
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    located = _resolve.read_indexed(_resolve.build_index(cfg, [], _resolve.MACRO_INDEX),
                                    "cluster_new_macro")
    assert located is not None and located.data == (ref / CL).read_bytes()


def test_a_merged_document_that_cannot_be_built_for_such_a_name_is_a_degraded_skip(tmp_path):
    ref, r = _cluster_world(tmp_path)
    bad = tmp_path / "extensions" / "s_bad"
    _manifest(bad, "s_bad")
    _w(bad / CL, "<diff><add sel='/macros'>")
    rep = _check.Report()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r, bad))
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    assert [s for s in rep.skipped if s.degraded and "cluster_new_macro" in s.why], rep.skipped


def test_a_bare_re_point_lands_on_a_DLC_s_complete_file_before_a_mod_s(tmp_path):
    """Bare-value step 2 (INFERRED): no reference file, a DLC ships the document, a mod also
    ships a complete copy, a re-pointer writes the bare value -> the DLC's file."""
    ref, ext = _base_index_world(tmp_path)
    (ref / "assets/fx/weaponfx/macros/bullet_q_macro.xml").unlink()
    dlc = ref / "extensions" / "ego_dlc_q"
    _w(dlc / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_dlc"/></macro></macros>')
    a, c = ext / "a_add", ext / "c_back"
    _manifest(a, "a_add")
    _manifest(c, "c_back")
    _w(a / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_other"/></macro></macros>')
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro']/@value\">"
       "assets/fx/weaponfx/macros/bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(a, c))
    assert _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)["bullet_q_macro"][0] == dlc


def test_TWIN_with_no_DLC_file_the_reference_file_comes_before_a_mod_s(tmp_path):
    ref, ext = _base_index_world(tmp_path)
    a, c = ext / "a_add", ext / "c_back"
    _manifest(a, "a_add")
    _manifest(c, "c_back")
    _w(a / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_other"/></macro></macros>')
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro']/@value\">"
       "assets/fx/weaponfx/macros/bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(a, c))
    assert _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)["bullet_q_macro"][0] == ref


def test_a_DLC_s_complete_file_comes_before_the_reference_file(tmp_path):
    """Step 2 before step 3: both the reference tree and a DLC ship the document."""
    ref, ext = _base_index_world(tmp_path)
    dlc = ref / "extensions" / "ego_dlc_q"
    _w(dlc / "assets/fx/weaponfx/macros/bullet_q_macro.xml",
       '<macros><macro name="bullet_q_macro" class="bullet"><component ref="bullet_dlc"/></macro></macros>')
    c = ext / "c_back"
    _manifest(c, "c_back")
    _w(c / "index" / "macros.xml",
       "<diff><replace sel=\"/index/entry[@name='bullet_q_macro']/@value\">"
       "assets/fx/weaponfx/macros/bullet_q_macro</replace></diff>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(c,))
    assert _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)["bullet_q_macro"][0] == dlc


# --- final delta: one build per path, a name defined nowhere, an unreadable file ----------

def _two_cluster_world(tmp_path, second_defined: bool = True):
    ref, r = _cluster_world(tmp_path)
    extra = ('<macro name="cluster_two_macro" class="cluster"><component ref="cl_two"/></macro>'
             if second_defined else "")
    _w(r / CL, '<diff><add sel="/macros"><macro name="cluster_new_macro" class="cluster">'
               f'<component ref="cl_new_comp"/></macro>{extra}</add></diff>')
    _w(r / "index" / "macros.xml",
       '<diff><add sel="/index"><entry name="cluster_new_macro" '
       r'value="maps\x_universe\clusters"/><entry name="cluster_two_macro" '
       r'value="maps\x_universe\clusters"/></add></diff>')
    return ref, r


def test_the_merged_document_is_built_ONCE_per_path_per_index(tmp_path, monkeypatch):
    """Two names a diff adds into one file: one merged build of that file, not one per name (a
    map mod adding hundreds of macros by diff would otherwise rebuild it hundreds of times)."""
    ref, r = _two_cluster_world(tmp_path)
    calls: list[str] = []
    real = _merge.build_effective

    def counting(vpath, *a, **k):
        calls.append(vpath)
        return real(vpath, *a, **k)

    monkeypatch.setattr(_resolve._merge, "build_effective", counting)
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    idx = _resolve.build_index(cfg, [], _resolve.MACRO_INDEX)
    assert calls.count(CL) == 1, calls
    for n in ("cluster_new_macro", "cluster_two_macro"):
        assert n.encode() in _resolve.read_indexed(idx, n).data


def test_a_name_defined_NOWHERE_at_its_path_is_a_degraded_skip(tmp_path):
    """Neither the file nor the merged document defines the name: say so, never a plain entry
    whose readers then find nothing and report success."""
    ref, r = _two_cluster_world(tmp_path, second_defined=False)
    rep = _check.Report()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    hits = [s for s in rep.skipped if s.degraded and "cluster_two_macro" in s.why]
    assert len(hits) == 1 and "defines it" in hits[0].why, rep.skipped
    assert not [s for s in rep.skipped if "cluster_new_macro" in s.why], rep.skipped


def test_TWIN_a_name_the_raw_file_defines_records_no_skip(tmp_path):
    ref, r = _cluster_world(tmp_path, game_defines=True)
    rep = _check.Report()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    assert not [s for s in rep.skipped if "cluster_new_macro" in s.why], rep.skipped


def test_an_UNREADABLE_file_at_the_path_is_a_degraded_skip_not_a_traceback(tmp_path, monkeypatch):
    ref, r = _cluster_world(tmp_path)
    target = (ref / CL).resolve()
    real = Path.read_bytes

    def flaky(self):
        if self.resolve() == target:
            raise PermissionError("locked")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    rep = _check.Report()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    hits = [s for s in rep.skipped if s.degraded and "cluster_new_macro" in s.why]
    assert len(hits) == 1 and "could not be read" in hits[0].why, rep.skipped


def test_TWIN_a_readable_file_records_no_read_skip(tmp_path):
    ref, r = _cluster_world(tmp_path)
    rep = _check.Report()
    cfg = _merge.Config(reference=ref, include_packed_dlc=False, overlays=(r,))
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    assert not [s for s in rep.skipped if "could not be read" in s.why], rep.skipped

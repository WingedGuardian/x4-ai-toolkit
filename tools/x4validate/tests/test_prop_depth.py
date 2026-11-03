"""Full-depth <properties> flattening (docs/BLIND-SPOTS.md F1, F4, F5, F6).

Until 2026-08-12 `_effective.flatten_with_prov` and `_stats.flatten_macro_props`
walked exactly ONE level of children, which made the entire flight model
(`physics/drag`, `physics/inertia`, `jerk`, `steeringcurve`) and ware recipe
inputs (`production/primary/ware`) invisible to x4effective and x4stats, while
the merged tree carried them correctly all along.

The load-bearing guarantee is that the fix is purely ADDITIVE: depth-1 keys must
come out byte-identical, or every stored value silently re-keys.
"""

from pathlib import Path

import ast
import pathlib

from lxml import etree

from x4validate import _effective, _merge, _stats
from x4validate._provenance import BASE, Recorder


SHIP = (
    '<macros><macro name="ship_x_macro" class="ship_s"><properties>'
    '<hull max="4000"/>'
    '<physics mass="6">'
    '  <inertia pitch="0.9" yaw="0.9" roll="1.1"/>'
    '  <drag forward="2.0" reverse="6" pitch="4.62"/>'
    '</physics>'
    '<steeringcurve><point position="0" value="1"/><point position="1" value="2"/></steeringcurve>'
    '</properties></macro></macros>')


def _rows(xml):
    root = etree.fromstring(xml)
    rec = Recorder(BASE)
    macro = root.find("macro")
    props = macro.find("properties")
    out = _effective.flatten_with_prov(
        macro, rec, no_recurse=(props,) if props is not None else ())
    out += _effective.flatten_with_prov(props, rec, child_scope=props)
    return {prop: value for prop, value, _num, _chain in out}


def test_depth_two_and_three_attrs_are_flattened():
    r = _rows(SHIP)
    assert r["physics.mass"] == "6"                 # depth 1, unchanged
    assert r["physics.drag.forward"] == "2.0"       # depth 2, previously invisible
    assert r["physics.inertia.roll"] == "1.1"


def test_depth_one_keys_are_unchanged_by_the_recursion():
    """The golden-vector guard: recursion must ADD keys, never re-key existing ones."""
    r = _rows(SHIP)
    assert r["hull.max"] == "4000"
    assert r["@name"] == "ship_x_macro"
    assert r["@class"] == "ship_s"


def test_repeated_grandchildren_are_disambiguated_positionally():
    r = _rows(SHIP)
    # <point> repeats with no id/name, so it takes the zero-based positional key
    # -- the same grammar the depth-1 walk has always used for repeated siblings.
    assert r["steeringcurve.point[0].value"] == "1"
    assert r["steeringcurve.point[1].value"] == "2"


def test_properties_subtree_is_not_emitted_twice():
    """`extract_macros` walks a macro twice (whole macro, then scoped to
    <properties>). Without `no_recurse` the second walk's rows reappeared under a
    `properties.` prefix -- MEASURED 67,333 duplicate rows, 23.1% of the store."""
    r = _rows(SHIP)
    assert not [k for k in r if k.startswith("properties.")]


def test_recursion_depth_guard_reports_rather_than_truncating_silently():
    deep = "<properties>" + "<a>" * 12 + '<leaf v="1"/>' + "</a>" * 12 + "</properties>"
    root = etree.fromstring(f'<macro name="m">{deep}</macro>')
    props = root.find("properties")
    _effective.truncated_props.clear()
    _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props)
    assert _effective.truncated_props, "hitting the guard must be recorded, never silent"
    _effective.truncated_props.clear()


def test_a_truncation_record_NAMES_the_document_it_came_from():
    """M6. `where=` was added so a truncation says WHICH document truncated; the
    release reviewer deleted it at all three `build()` call sites and the suite stayed
    green, because the only test exercising truncation calls flatten_with_prov
    DIRECTLY and passes no `where` at all.

    A bare `properties.a.a.a...` with no document is the same non-answer the whole
    channel exists to replace -- it tells you something was cut and not what."""
    deep = "<properties>" + "<a>" * 12 + '<leaf v="1"/>' + "</a>" * 12 + "</properties>"
    root = etree.fromstring(f'<macro name="m">{deep}</macro>')
    props = root.find("properties")
    _effective.truncated_props.clear()
    _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props,
                                 where="assets/props/engines/macros/eng_01.xml")
    assert _effective.truncated_props, "the guard must record, never truncate silently"
    rec = _effective.truncated_props[0]
    assert "eng_01.xml" in rec, rec
    assert rec.startswith("assets/props/engines/macros/eng_01.xml: "), rec
    _effective.truncated_props.clear()


def test_the_truncation_list_does_not_CARRY_OVER_between_builds():
    """M7. `build()` clears `truncated_props` at start because it is a MODULE GLOBAL:
    two builds in one process otherwise make the second re-report the first's
    truncations as its own. Deleting the clear left the suite green -- nothing built
    twice in one process."""
    deep = "<properties>" + "<a>" * 12 + '<leaf v="1"/>' + "</a>" * 12 + "</properties>"
    root = etree.fromstring(f'<macro name="m">{deep}</macro>')
    props = root.find("properties")
    _effective.truncated_props.clear()
    _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props, where="first.xml")
    assert any("first.xml" in r for r in _effective.truncated_props)

    # What build() does at its start, and the only thing standing between one run's
    # findings and the next run's report.
    _effective.truncated_props.clear()
    assert _effective.truncated_props == [], (
        "a second build would inherit the first's truncations")
    _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props, where="second.xml")
    assert not any("first.xml" in r for r in _effective.truncated_props),         _effective.truncated_props
    _effective.truncated_props.clear()


def test_every_flatten_call_site_in_effective_PASSES_where():
    """M6, and my first attempt at it was shadowed exactly as the reviewer's was.

    The test above pins that `flatten_with_prov` HONOURS `where=`; it calls the helper
    directly and passes one, so deleting `where=vpath` from `_effective`'s own call
    sites leaves it green. The helper was never the risk -- the WIRING was, and a
    truncation with no document name is the non-answer the channel exists to replace.

    Asserted over the AST of the module rather than by running a build, because
    triggering a real truncation needs a corpus with a >8-deep property subtree.
    """
    src = pathlib.Path(_effective.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "flatten_with_prov"]
    assert calls, "no flatten_with_prov call sites found -- the scan is wrong, not the code"
    missing = [n.lineno for n in calls
               if not any(k.arg == "where" for k in n.keywords)]
    assert not missing, (
        "flatten_with_prov called without `where=` at %s -- a truncation recorded "
        "there cannot name the document it came from" % missing)


def test_build_itself_clears_the_list_before_it_starts():
    """The wiring, asserted STRUCTURALLY rather than by building twice (a real second
    build needs a corpus): `build()` must call the clear, and it is the call the
    reviewer's M7 removed."""
    import inspect
    src = inspect.getsource(_effective.build)
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "clear"
             and getattr(n.func.value, "id", "") == "truncated_props"]
    assert calls, "build() no longer clears truncated_props; a second build in one "                   "process would report the first's truncations as its own"
    # ...AND BEFORE THE WALKS THAT POPULATE IT, not merely somewhere in the
    # function. This asserted only that the call EXISTS: MEASURED 2026-09-08 by
    # moving it down to just above the `if truncated_props:` report, which
    # discards every truncation THIS build found and silences the WARNING --
    # and the test stayed green. My first repair was also insufficient: it
    # looked for READS before the clear, and build() never reads the list by
    # name until the report, because the population happens through .append
    # inside a callee. The ordering that matters is against the WALKS.
    populators = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", getattr(n.func, "attr", ""))
                  in ("extract_macros", "extract_components", "flatten_with_prov")]
    assert populators, (
        "no populating walk found in build() -- the scan is wrong, not the code, "
        "and an ordering assertion over an empty set proves nothing")
    first_clear = min(n.lineno for n in calls)
    assert first_clear < min(populators), (
        "build() clears truncated_props at line %d, AFTER the first walk that "
        "populates it at line %d -- so the clear discards this build's own "
        "findings and the WARNING never prints" % (first_clear, min(populators)))


def test_ware_recipe_inputs_are_flattened():
    """`production/primary/ware` is depth 3 -- the actual recipe, invisible before."""
    root = etree.fromstring(
        '<wares><ware id="hullparts"><price min="1"/>'
        '<production time="900" method="default"><primary>'
        '<ware ware="energycells" amount="80"/>'
        '<ware ware="graphene" amount="40"/></primary></production>'
        '</ware></wares>')
    rec = Recorder(BASE)
    rows = {p: v for p, v, _n, _c in
            _effective.flatten_with_prov(root.find("ware"), rec)}
    # Repeated <ware> siblings take the keyed form via _child_ident's "ware" attr;
    # a single non-repeating child stays bare (production.primary...), which is
    # why this fixture ships two inputs.
    assert rows["production.primary.ware[energycells].amount"] == "80"
    assert rows["production.primary.ware[graphene].amount"] == "40"
    assert rows["price.min"] == "1"          # depth 1 still intact


# --- x4stats / x4similar side (same defect, separate implementation) ----------

def test_stats_flatten_is_depth_recursive():
    v = _stats.flatten_macro_props(etree.fromstring(SHIP))
    assert v["physics.mass"] == 6.0
    assert v["physics.drag.forward"] == 2.0      # previously absent
    assert v["hull.max"] == 4000.0               # depth 1 unchanged
    assert v["class"] == "ship_s"


def test_iter_macros_sees_every_macro_not_just_the_first():
    """`_effective`/`_compat` read all macros via `iter`; `_stats`/`_similarity`
    read only the first. Two tools disagreeing about a file's contents is how the
    nested-door defect started, so they now share one reader."""
    root = etree.fromstring(
        '<macros><macro name="a" class="ship_s"><properties><hull max="1"/></properties></macro>'
        '<macro name="b" class="ship_m"><properties><hull max="2"/></properties></macro></macros>')
    assert [m.get("name") for m in _stats.iter_macros(root)] == ["a", "b"]
    assert _stats.flatten_props_of(_stats.iter_macros(root)[1])["hull.max"] == 2.0


# --- F4: packed mini-DLC enumeration -----------------------------------------

def test_packed_dlc_assets_are_enumerated(monkeypatch, tmp_path):
    """A DLC with no loose assets/ on disk is a PACKED mini-DLC; its macro files
    must still be enumerated (via _cat), not silently skipped.

    Real cost of the old `continue`: 26 of 40 mini-DLC macros absent from the
    store, including the Envoy Pack's disabler/gatling/shieldpierce weapons.
    """
    ref = tmp_path / "reference"
    (ref / "assets").mkdir(parents=True)
    (ref / "assets" / "base_macro.xml").write_bytes(b"<macros/>")
    packed = tmp_path / "ego_dlc_mini_99"
    packed.mkdir()

    cfg = _merge.Config(reference=ref)
    monkeypatch.setattr(type(cfg), "dlc_dirs", lambda self: [packed], raising=False)
    monkeypatch.setattr(type(cfg), "packed_dlc_names",
                        lambda self: {"ego_dlc_mini_99"}, raising=False)
    monkeypatch.setattr(_effective._cat, "mod_vfs",
                        lambda p, **kw: ["assets/props/weapon_x_macro.xml",
                                         "assets/notes.txt",
                                         "libraries/wares.xml"])

    out = _effective.reference_vpaths(cfg, "*_macro.xml")
    assert "extensions/ego_dlc_mini_99/assets/props/weapon_x_macro.xml" in out
    assert "assets/base_macro.xml" in out            # loose path still works
    assert not any("notes.txt" in k for k in out)    # pattern still filters
    assert not any("wares.xml" in k for k in out)    # assets/-only still holds


def test_unpacked_dlc_without_assets_is_not_invented(monkeypatch, tmp_path):
    """Only PACKED dlc take the _cat route -- a non-packed dir with no assets/
    must still be skipped, or a missing unpack would silently fabricate vpaths."""
    ref = tmp_path / "reference"
    (ref / "assets").mkdir(parents=True)
    empty = tmp_path / "ego_dlc_real"
    empty.mkdir()
    cfg = _merge.Config(reference=ref)
    monkeypatch.setattr(type(cfg), "dlc_dirs", lambda self: [empty], raising=False)
    monkeypatch.setattr(type(cfg), "packed_dlc_names", lambda self: set(), raising=False)
    monkeypatch.setattr(_effective._cat, "mod_vfs",
                        lambda p, **kw: ["assets/should_not_appear_macro.xml"])
    assert _effective.reference_vpaths(cfg, "*_macro.xml") == {}


# --- F5: admit macro files by content, not filename ---------------------------

def test_macro_file_admitted_by_content_when_filename_does_not_match():
    """VRO ships live macro files not named *_macro.xml (`bullet_ter_m_graviton`,
    and two with a `_marco.xml` typo). The engine loads them via index/macros.xml,
    so a filename rule loses real balance data."""
    vpath = "assets/fx/weaponfx/macros/bullet_ter_m_graviton.xml"
    touch = {vpath: [("vro", "REAL")]}

    def macro_file(base, real):
        return b'<macros><macro name="bullet_ter_m_graviton" class="bullet"/></macros>'

    def plain_file(base, real):
        return b"<components><component name='x'/></components>"

    orig = _effective._cat.read_path
    try:
        _effective._cat.read_path = macro_file
        assert _effective._defines_macro({"vro": "BASE"}, touch[vpath]) is True
        _effective._cat.read_path = plain_file
        assert _effective._defines_macro({"vro": "BASE"}, touch[vpath]) is False
    finally:
        _effective._cat.read_path = orig


def test_unreadable_candidate_fails_OPEN_not_closed():
    """"I could not read it" is not "it holds no macros". Failing closed would be
    the exact silent-narrowing defect this whole change removes."""
    def boom(base, real):
        raise OSError("locked")

    orig = _effective._cat.read_path
    try:
        _effective._cat.read_path = boom
        assert _effective._defines_macro({"m": "BASE"}, [("m", "x.xml")]) is True
    finally:
        _effective._cat.read_path = orig


# --- F34: base_vpaths is the WHOLE tree; reference_vpaths is its assets subset --

def _mini_dlc_cfg(monkeypatch, tmp_path):
    """reference/ with content in and out of assets/, plus one PACKED mini-DLC."""
    ref = tmp_path / "reference"
    (ref / "assets" / "props").mkdir(parents=True)
    (ref / "libraries").mkdir(parents=True)
    (ref / "maps" / "xu_ep2_universe").mkdir(parents=True)
    (ref / "assets" / "props" / "weapon_a_macro.xml").write_bytes(b"<macros/>")
    (ref / "libraries" / "character_macros.xml").write_bytes(b"<macros/>")
    (ref / "maps" / "xu_ep2_universe" / "clusters.xml").write_bytes(b"<macros/>")

    packed = tmp_path / "ego_dlc_mini_99"
    packed.mkdir()
    cfg = _merge.Config(reference=ref)
    monkeypatch.setattr(type(cfg), "dlc_dirs", lambda self: [packed], raising=False)
    monkeypatch.setattr(type(cfg), "packed_dlc_names",
                        lambda self: {"ego_dlc_mini_99"}, raising=False)
    monkeypatch.setattr(_effective._cat, "mod_vfs",
                        lambda p, **kw: ["assets/units/ship_z_macro.xml",
                                         "libraries/god.xml",
                                         "md/setup.xml"])
    return cfg


def test_base_vpaths_covers_the_whole_tree_loose_and_packed(monkeypatch, tmp_path):
    """x4eff indexes the whole game, not just `assets/`. The loose-only form held
    23 of 142 mini-DLC documents; the assets-only form would ALSO have dropped
    every `libraries/`, `maps/`, `index/` and `md/` document in the base tree."""
    cfg = _mini_dlc_cfg(monkeypatch, tmp_path)
    out = _effective.base_vpaths(cfg, "*.xml")

    # loose, outside assets/ -- invisible to reference_vpaths by design
    assert "libraries/character_macros.xml" in out
    assert "maps/xu_ep2_universe/clusters.xml" in out
    # packed, outside assets/ -- invisible to BOTH of the old walks
    assert "extensions/ego_dlc_mini_99/libraries/god.xml" in out
    assert "extensions/ego_dlc_mini_99/md/setup.xml" in out
    # and the ordinary cases still hold
    assert "assets/props/weapon_a_macro.xml" in out
    assert "extensions/ego_dlc_mini_99/assets/units/ship_z_macro.xml" in out


def test_base_has_accepts_EITHER_spelling_of_a_DLC_vpath(monkeypatch, tmp_path):
    """The 58-false-GONE trap, pinned.

    `base_vpaths` keys base files BARE but keeps a DLC's `extensions/ego_dlc_x/`
    prefix. MEASURED 2026-08-25: an audit stripped the prefix before looking up 58
    DLC targets and reported all 58 GONE when every one exists. Both spellings are
    legitimate; a caller that knows only one gets a uniform, confident, false
    answer.
    """
    cfg = _mini_dlc_cfg(monkeypatch, tmp_path)
    _effective.base_vpaths.cache_clear()
    known = _effective.base_vpaths(cfg, "*.xml")

    bare = [v for v in known if not v.startswith("extensions/")]
    dlc = [v for v in known if v.startswith("extensions/")]
    assert bare and dlc, "fixture must contain both spellings for this to prove anything"

    for v in bare + dlc:
        assert _effective.base_has(cfg, v), f"the stored spelling must resolve: {v}"

    # ...and the OTHER spelling of the same DLC file must resolve too -- this is
    # the assertion that goes red against a plain `vpath in base_vpaths(...)`.
    for v in dlc:
        stripped = v.split("/", 2)[2]
        assert _effective.base_has(cfg, stripped), (
            f"a DLC vpath with the prefix stripped must still resolve: {stripped}")
        owner = v.split("/")[1]
        assert _effective.base_has(cfg, stripped, owner=owner)

    # The falsification twin: something genuinely absent must still say NO, or the
    # assertions above would pass for a function that returns True unconditionally.
    assert not _effective.base_has(cfg, "libraries/definitely_not_a_real_file.xml")
    assert not _effective.base_has(cfg, "extensions/ego_dlc_nope/libraries/x.xml")


def test_reference_vpaths_is_exactly_the_assets_subset(monkeypatch, tmp_path):
    """F3's scope is a documented decision, and this pins that the refactor to
    `base_vpaths` did not quietly widen or narrow it.

    MEASURED against the live corpus at the moment of the refactor: both call
    patterns were SET-EQUAL before and after -- 4,002 for `*_macro.xml` and 7,551
    for `*.xml`, 0 added, 0 removed, 0 changed.
    """
    cfg = _mini_dlc_cfg(monkeypatch, tmp_path)
    base = _effective.base_vpaths(cfg, "*.xml")
    ref = _effective.reference_vpaths(cfg, "*.xml")

    assert ref == {k: v for k, v in base.items() if _effective._under_assets(k)}
    assert set(ref) < set(base), "the filter must actually exclude something"
    assert not any("/libraries/" in k or k.startswith("libraries/") for k in ref)
    assert not any("/md/" in k or k.startswith("md/") for k in ref)


def test_under_assets_is_exact_not_a_substring_match():
    """`"assets/" in low` also matches `libraries/assets/...`. A filter that is
    NEARLY the old walk is how a documented scope quietly becomes a different one."""
    assert _effective._under_assets("assets/units/x.xml")
    assert _effective._under_assets("extensions/ego_dlc_mini_01/assets/units/x.xml")
    assert not _effective._under_assets("libraries/assets/decoy.xml")
    assert not _effective._under_assets("extensions/ego_dlc_x/libraries/assets/d.xml")
    assert not _effective._under_assets("md/setup.xml")


# --- F33 attr axis: a repeated bracket must not collapse onto one key ---------

def _flatten_props(xml: str) -> list[tuple[str, str]]:
    """(prop, value) rows for a fragment, through the real walker."""
    el = etree.fromstring(xml)
    rec = _effective.Recorder()
    rows: list = []
    _effective._walk_props(el, rec, "", 1, rows)
    return [(p, v) for p, v, _num, _chain in rows]


def test_siblings_sharing_a_bracket_get_distinct_keys():
    """THE FIX. The discriminator is an ATTRIBUTE, and attributes repeat.

    Real worst case, MEASURED: `faction/player` had EIGHT rows under
    `licences.licence[generaluseequipment].factions`, holding eight DIFFERENT
    faction lists. `select value ... where prop=?` + `fetchone()` returned an
    arbitrary one of them, and nothing indicated there were seven more.
    """
    rows = _flatten_props(
        '<licences>'
        '  <licence type="generaluseequipment" factions="argon"/>'
        '  <licence type="generaluseequipment" factions="boron"/>'
        '  <licence type="generaluseequipment" factions="teladi"/>'
        '</licences>')
    props = [p for p, _ in rows if p.endswith(".factions")]
    assert len(props) == len(set(props)), f"keys still collide: {props}"
    assert props == ["licence[generaluseequipment].factions",
                     "licence[generaluseequipment#1].factions",
                     "licence[generaluseequipment#2].factions"]
    assert [v for p, v in rows if p.endswith(".factions")] == ["argon", "boron", "teladi"]


def test_the_first_claimant_keeps_the_original_key():
    """Collision-ONLY. Indexing every key positionally would have rewritten
    358,415 rows (61.6%) to repair 1,071 (0.18%), broken the golden-vector
    regression guard, and invalidated every stored baseline for nothing."""
    rows = _flatten_props(
        '<licences>'
        '  <licence type="a" factions="x"/>'
        '  <licence type="b" factions="y"/>'
        '</licences>')
    assert [p for p, _ in rows] == ["licence[a].type", "licence[a].factions",
                                    "licence[b].type", "licence[b].factions"]


def test_an_only_child_is_still_keyed_by_bare_tag():
    """The `tag_counts[tag] > 1` gate is untouched -- a lone child must not
    suddenly acquire a bracket."""
    assert _flatten_props('<physics><drag forward="1.5"/></physics>') == [
        ("drag.forward", "1.5")]


def test_identless_siblings_keep_their_original_positional_numbering():
    """The pre-existing ident-less path is byte-identical -- 0/1/2, not 0/0#1/0#2."""
    rows = _flatten_props('<a><b v="1"/><b v="2"/><b v="3"/></a>')
    assert [p for p, _ in rows] == ["b[0].v", "b[1].v", "b[2].v"]


# --- the depth guard: ONE definition, and neither walk truncates in silence -------

def test_the_two_walks_share_ONE_depth_constant():
    """`_stats` carried a SECOND literal named MAX_PROP_DEPTH with no test pinning it
    equal, so raising one silently diverged the tools -- and the two answer the same
    question about the same macro. It is imported now, not mirrored."""
    from x4validate import _stats
    # IMPORTED, not merely EQUAL. `is` on the value 8 is satisfied by a re-declared
    # literal, because CPython interns small ints -- MEASURED 2026-09-08: putting
    # `MAX_PROP_DEPTH = 8` back into _stats.py left this green, so it caught later
    # DIVERGENCE and never the MIRRORING it is named for. Asserted over the AST of
    # the module's own source, which a re-declaration cannot satisfy.
    import ast as _ast
    src = Path(_stats.__file__).read_text(encoding="utf-8")
    mirrors = [n.lineno for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.Assign) and isinstance(n.value, _ast.Constant)
               for t in n.targets
               if isinstance(t, _ast.Name) and t.id == "MAX_PROP_DEPTH"]
    assert not mirrors, (
        "_stats.py assigns MAX_PROP_DEPTH a LITERAL at line(s) %s -- a mirrored "
        "constant is a constant that drifts. Deriving it (`= _effective.MAX_PROP_DEPTH`) is correct; re-declaring the value is not." % (mirrors,))
    assert _stats.MAX_PROP_DEPTH is _effective.MAX_PROP_DEPTH, (
        "_stats has its own copy again; a mirrored constant is a constant that drifts")


def test_the_stats_walk_RECORDS_what_it_abandons(monkeypatch):
    """It truncated in SILENCE while `_effective` recorded and the build printed. Two
    tools disagreeing about a macro's properties with only one able to say why is the
    narrowing-without-announcing shape this register is named after."""
    from lxml import etree
    from x4validate import _stats

    deep = ("<properties>" + "<a>" * (_effective.MAX_PROP_DEPTH + 3)
            + '<leaf v="1"/>' + "</a>" * (_effective.MAX_PROP_DEPTH + 3) + "</properties>")
    root = etree.fromstring('<macro name="m">' + deep + "</macro>")
    _stats.truncated_props.clear()
    _stats.flatten_props_of(root)
    assert _stats.truncated_props, (
        "the stats walk hit its depth guard and reported nothing")
    _stats.truncated_props.clear()


def test_a_SHALLOW_macro_records_no_truncation(monkeypatch):
    """The twin. If every walk recorded a truncation the signal would be worthless."""
    from lxml import etree
    from x4validate import _stats
    root = etree.fromstring(
        '<macro name="m"><properties><physics><drag forward="1"/></physics>'
        "</properties></macro>")
    _stats.truncated_props.clear()
    _stats.flatten_props_of(root)
    assert _stats.truncated_props == []


def test_a_truncation_NAMES_the_document_it_came_from():
    """★ The reason the constant could not be re-derived on 2026-09-06.

    The record printed a property PATH and never the vpath, so a build reporting 10
    truncations gave no way to open one -- and a packed-inclusive sweep of the mod
    corpus (deepest subtree 5) plus base+DLC maps (deepest 4) could not reproduce a
    single one. A narrowing step must announce WHERE, or the announcement cannot be
    acted on.
    """
    from lxml import etree
    deep = ("<properties>" + "<a>" * (_effective.MAX_PROP_DEPTH + 3)
            + '<leaf v="1"/>' + "</a>" * (_effective.MAX_PROP_DEPTH + 3) + "</properties>")
    props = etree.fromstring('<macro name="m">' + deep + "</macro>").find("properties")
    _effective.truncated_props.clear()
    _effective.flatten_with_prov(props, Recorder(BASE), child_scope=props,
                                 where="assets/only/here.xml")
    assert _effective.truncated_props, "the guard did not fire, so this proves nothing"
    assert all(r.startswith("assets/only/here.xml: ")
               for r in _effective.truncated_props), _effective.truncated_props
    _effective.truncated_props.clear()


def test_the_depth_fixture_is_DERIVED_from_the_constant():
    """`test_recursion_depth_guard_reports_rather_than_truncating_silently` nests a
    literal 12, so it stays green for any MAX_PROP_DEPTH below 12 and goes red the
    moment someone raises it to 12 -- for the wrong reason. Pinned here so a future
    raise fixes the fixture instead of being blamed on the guard."""
    # ASSERTED OVER THE AST, and not over this file. It read its own source and
    # looked for the literal "MAX_PROP_DEPTH + 3" -- a string that IS the assertion
    # line, so it was a tautology: MEASURED 2026-09-08 by replacing all four derived
    # fixtures with a literal 11, which left it green.
    import ast as _ast
    src = (Path(__file__)).read_text(encoding="utf-8")
    derived = [n for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.BinOp) and isinstance(n.op, _ast.Add)
               and (getattr(n.left, "id", None) == "MAX_PROP_DEPTH"
                    or getattr(n.left, "attr", None) == "MAX_PROP_DEPTH")]
    assert len(derived) >= 4, (
        "the deep fixtures no longer derive their nesting from the constant (found "
        "%d `MAX_PROP_DEPTH + n` expressions, expected the four fixtures); a raise "
        "will make them pass vacuously or fail for an unrelated reason" % len(derived))


def test_a_build_persists_its_truncations_in_the_store(tmp_path, monkeypatch):
    """Printed at build time only until 4.1.0 (review T2): nothing read later could see this loss, and an
    `x4fresh accept` could quiet a build that newly lost a subtree."""
    import json
    import os
    import sqlite3
    for k in list(os.environ):
        if k.startswith("X4_"):
            monkeypatch.delenv(k, raising=False)
    cfg_file = tmp_path / "x4-paths.env"
    cfg_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("X4_CONFIG", str(cfg_file))
    ref = tmp_path / "reference"
    macros = ref / "assets" / "units" / "size_s" / "macros"
    macros.mkdir(parents=True)
    deep = "<a>" * 12 + '<leaf v="1"/>' + "</a>" * 12
    (macros / "ship_x_macro.xml").write_text(
        f'<macros><macro name="ship_x_macro" class="ship_s"><properties>{deep}</properties></macro></macros>',
        encoding="utf-8")
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_effective, "active_mods", lambda *a, **k: [])
    _effective.base_vpaths.cache_clear()
    db = tmp_path / "s.sqlite"
    try:
        _effective.build(_merge.Config(reference=ref), db, kinds=("macro",))
    finally:
        _effective.base_vpaths.cache_clear()
    con = sqlite3.connect(db)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    con.close()
    assert int(meta["truncated_props"]) >= 1
    samples = json.loads(meta["truncated_prop_samples"])
    assert "ship_x_macro.xml" in samples[0]
    assert any(k.startswith("truncated ") and "ship_x_macro.xml" in k for k in json.loads(meta["loss_keys"]))

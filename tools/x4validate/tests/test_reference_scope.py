"""C3 (F14 + F12): reference checking beyond `<add>` ops in `<diff>` files.

Both reference checks iterated `iter_diff_files` + `_added_subtrees`, so a mod
shipping FULL files got no reference checking at all -- MEASURED 1,625 full files
across 56 mods, 38% of their XML.

Widening the SCOPE without first fixing the ORACLE would have multiplied two
pre-existing false-positive classes. Measured over all 114 installed mods:

  ware refs   22 unresolved today -> 134 when widened. ALL false: in md/ and
              aiscripts/ the @ware attribute holds a script EXPRESSION
              ($tradeware, ware.energycells), not an id. The forms also appear
              outside those directories (a mod's backups/), so the filter must
              key on the VALUE SHAPE, not the path.
  text refs   45 -> 52. `collect_text_defs` read a hardcoded 2-entry TEXT_FILES
              list, so a mod shipping strings at a non-standard path
              (sfx/weapons/t/0001.xml) had its OWN strings missing from the
              definition set -- 56 of 90 strings for the one mod that does this.

The macro/component oracle is namespace-AGNOSTIC on purpose. `<component ref>`
names a macro inside libraries/wares.xml but a COMPONENT inside a macro file
(gotcha #11), and in a <diff> with `<replace sel="//component">` element
ancestry cannot classify it at all -- only the selector target can. Checking
against the UNION of both namespaces sidesteps every one of those traps and
still yields exactly the 3 genuine dangling refs over 2,300 references.
"""

import hashlib

from lxml import etree

from x4validate import _check, _merge, _refs, _scan


def _write_cat(mod_dir, cat_name, members):
    """Write a .cat/.dat pair — same helper shape as tests/test_cat.py."""
    mod_dir.mkdir(parents=True, exist_ok=True)
    cat = mod_dir / cat_name
    lines, blob = [], bytearray()
    for vpath, data in members:
        lines.append(f"{vpath} {len(data)} 1700000000 {hashlib.md5(data).hexdigest()}")
        blob += data
    cat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cat.with_suffix(".dat").write_bytes(bytes(blob))


# --- C3b: @ware holds a script expression in md/ and aiscripts/ ---------------

def test_script_variable_is_not_a_ware_reference():
    """`<ware ware="$tradeware">` names a script variable. MEASURED: 7 of 7
    aiscript ware refs are this shape, and every one read as dangling."""
    assert _refs.is_script_expression("$tradeware")
    assert _refs.is_script_expression("$possibleware")


def test_dotted_lookup_is_not_a_ware_reference():
    """`ware.energycells` is MD expression syntax. MEASURED: 170 of 172 md/
    ware refs are this shape."""
    assert _refs.is_script_expression("ware.energycells")
    assert _refs.is_script_expression("ware.inv_codex_military_merit")


def test_a_real_ware_id_is_never_treated_as_an_expression():
    """The predicate must not silently skip real ids -- that would turn a false
    POSITIVE into a false NEGATIVE, which is strictly worse (invisible).
    MEASURED: 0 of 2,462 effective ware ids and 0 of 1,980 vanilla ids match."""
    for real in ("ore", "energycells", "da_adv_schematics", "inv_codex_military_merit",
                 "spacefuel", "hullparts", "ware_with_underscores_01"):
        assert not _refs.is_script_expression(real), real


def test_find_dangling_skips_expressions_but_still_flags_a_plain_unknown_id():
    """Both halves in one test: the filter must not disable the check."""
    tree = etree.fromstring(
        b'<_added><a ware="$tradeware"/><b ware="ware.energycells"/>'
        b'<c ware="da_adv_schematics"/><d ware="ore"/></_added>')
    dangling = _refs.find_dangling(tree, ware_def_set={"ore"}, text_def_set=set())
    refs = {d.ref for d in dangling if d.kind == "ware"}
    assert refs == {"da_adv_schematics"}, (
        "expressions must be skipped and real unknown ids must still be caught")


# --- C3c: t-files are discovered by shape, not a hardcoded 2-entry list -------

def _t_file(path, page, tid, text="hi"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f'<language id="44"><page id="{page}">'
                     f'<t id="{tid}">{text}</t></page></language>'.encode())


def test_text_defs_discovers_a_non_standard_t_path(tmp_path):
    """MEASURED: code_vgr_battlecruiser ships 90 strings under sfx/weapons/t/,
    56 of which were missing from the definition set -- so its OWN {page,t}
    references read as dangling. The mod is fine; the oracle was short."""
    ref = tmp_path / "reference"
    _t_file(ref / "t" / "0001-l044.xml", "20101", "1")
    mod = tmp_path / "mod"
    _t_file(mod / "sfx" / "weapons" / "t" / "0001.xml", "996000", "10")

    defs = _check.collect_text_defs(_merge.Config(reference=ref), [mod])
    assert ("996000", "10") in defs, (
        "a t-file outside t/ was not read -- TEXT_FILES is still a fixed list")


def test_text_defs_still_reads_the_standard_paths(tmp_path):
    """Discovery must not lose what the hardcoded list already covered."""
    ref = tmp_path / "reference"
    _t_file(ref / "t" / "0001-l044.xml", "20101", "1")
    _t_file(ref / "t" / "0001.xml", "20102", "2")
    defs = _check.collect_text_defs(_merge.Config(reference=ref))
    assert ("20101", "1") in defs and ("20102", "2") in defs


def test_text_defs_ignores_other_languages(tmp_path):
    """l007 is German. Folding every language into one English definition set
    would hide a genuinely missing English string."""
    ref = tmp_path / "reference"
    _t_file(ref / "t" / "0001-l044.xml", "20101", "1")
    _t_file(ref / "t" / "0001-l007.xml", "20101", "999")
    defs = _check.collect_text_defs(_merge.Config(reference=ref))
    assert ("20101", "999") not in defs


# --- C3a: full files are checked, with a both-namespace oracle ----------------

def _game(tmp_path):
    ref = tmp_path / "reference"
    (ref / "index").mkdir(parents=True)
    (ref / "index" / "macros.xml").write_bytes(
        b'<index><entry name="ship_known_macro" value="assets\\ship_known_macro"/></index>')
    (ref / "index" / "components.xml").write_bytes(
        b'<index><entry name="ship_known" value="assets\\ship_known"/></index>')
    (ref / "assets").mkdir()
    (ref / "assets/ship_known_macro.xml").write_bytes(b'<macros><macro name="ship_known_macro"/></macros>')
    (ref / "assets/ship_known.xml").write_bytes(b'<components><component name="ship_known"/></components>')
    (ref / "libraries").mkdir(parents=True, exist_ok=True)
    (ref / "libraries" / "wares.xml").write_bytes(b'<wares><ware id="ore"/></wares>')
    _t_file(ref / "t" / "0001-l044.xml", "1", "1")
    return ref


def test_reference_check_sees_a_full_file_not_only_diff_adds(tmp_path):
    """The F14 gap itself: a mod shipping a FULL file got no reference checking."""
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "baskets.xml").write_bytes(
        b'<baskets><basket id="b"><ware ware="totally_missing"/></basket></baskets>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    refs = {f.message for f in report.findings}
    assert any("totally_missing" in m for m in refs), (
        "a dangling ref in a full (non-diff) file was not reported")


def test_full_file_findings_gate_as_ERROR(tmp_path):
    """PROMOTED 2026-08-13, after the INFO run earned it.

    Shipped as INFO first so a newly-visible check could not break a build before
    its hit list had ever been read. The gate for promotion was "one clean corpus
    run, every finding verified": MEASURED over 114 installed mods, **14 findings,
    every one confirmed genuine** — 3 unresolvable macro/component refs and 11
    text refs (page 20104 holds 1,213 strings but starts at 10000, so VRO's
    {20104,1601/1602} are absent; page 2203201 does not exist at all; {98231753,230}
    is out of range on a 54-string page). Zero false positives, so it now gates.

    A reference that resolves to nothing is the mod's defect whether it arrived
    via an `<add>` or in a file the mod ships wholesale — the message still says
    which scope found it, but the severity no longer differs."""
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "baskets.xml").write_bytes(
        b'<baskets><basket id="b"><ware ware="totally_missing"/></basket></baskets>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    hits = [f for f in report.findings if "totally_missing" in f.message]
    assert hits, "precondition: the finding must exist"
    assert all(f.severity == "error" for f in hits), (
        f"full-file refs must gate, got {[f.severity for f in hits]}")
    assert report.errors, "the run must exit non-zero on an unresolvable reference"


def test_diff_add_findings_stay_ERROR(tmp_path):
    """The other half of the same rule: nothing that gated before stops gating."""
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "wares.xml").write_bytes(
        b'<diff><add sel="/wares"><ware id="n"><production><primary>'
        b'<ware ware="totally_missing"/></primary></production></ware></add></diff>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    hits = [f for f in report.findings if "totally_missing" in f.message]
    assert hits and any(f.severity == "error" for f in hits), (
        f"an introduced <add> ref must still gate, got {[f.severity for f in hits]}")


def test_diff_add_component_ref_to_a_real_COMPONENT_does_not_gate(tmp_path):
    """The gating scope had the SAME wrong oracle, and it was costing real errors.

    MEASURED on the live modlist: 23 of the 77 gating ERRORs were `standardzone`
    and `standardregion` — both defined in `libraries/component.xml` and
    registered in `index/components.xml`, i.e. components, checked against the
    MACRO index. Two scopes must not answer one question with two oracles; that
    is the two-doors shape that has already cost this codebase a defect.
    """
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "maps").mkdir(parents=True)
    (mod / "maps" / "zones.xml").write_bytes(
        b'<diff><add sel="/macros"><zone><component ref="ship_known"/></zone></add></diff>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    gating = [f for f in report.findings if f.severity == "error" and "ship_known" in f.message]
    assert not gating, f"a real component ref gated as a dangling macro: {gating}"


def test_component_ref_naming_a_COMPONENT_is_not_flagged(tmp_path):
    """gotcha #11: inside a macro file `<component ref>` names a COMPONENT, but
    inside libraries/wares.xml it names a MACRO. Checking a component name
    against the macro index produced 1,792 naive misses over 114 mods."""
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "ship_known_macro.xml").write_bytes(
        b'<macros><macro name="ship_known_macro" class="ship_s">'
        b'<component ref="ship_known"/></macro></macros>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    bad = [f for f in report.findings if "ship_known" in f.message]
    assert not bad, f"a valid component ref was reported as dangling: {bad}"


def test_a_genuinely_undefined_ref_is_still_caught_in_a_full_file(tmp_path):
    """The both-namespace union must not become a check that passes everything."""
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "ship_x_macro.xml").write_bytes(
        b'<macros><macro name="ship_x_macro" class="ship_s">'
        b'<component ref="nothing_defines_this"/></macro></macros>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    assert any("nothing_defines_this" in f.message for f in report.findings), (
        "the union oracle swallowed a genuinely undefined reference")


def test_a_mods_own_unindexed_entity_resolves_without_a_corpus_scan(tmp_path):
    """MEASURED: 753 macro names are defined in asset files without ever being
    registered in index/macros.xml, and a mod's own content is the common case.

    Resolving those from the mod itself — one small directory — must not fall
    through to the corpus tier, which parses 13,579 files and costs ~13s. This
    pins the CHEAP tier: correctness alone would pass with a corpus scan, so the
    assertion is on `corpus_scanned` being False.
    """
    ref = _game(tmp_path)
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "thing_macro.xml").write_bytes(
        b'<macros><macro name="my_unindexed_macro" class="ship_s"/></macros>')

    defs = _check.EntityDefs(_merge.Config(reference=ref), [mod])
    assert "my_unindexed_macro" in defs, "a mod's own unindexed macro must resolve"
    assert not defs.corpus_scanned, (
        "resolving the mod's OWN entity fell through to the 13s corpus scan")


def test_the_corpus_tier_does_not_parse_files_that_cannot_contain_the_name(tmp_path):
    """Perf, caught by our own gate rather than by a user.

    Building the whole corpus name set parses every file: MEASURED 8.9s for the
    reference tree alone, which `gates/perf_guard.py` flagged as 3 regressions
    (ebi 5.8x, cpsdo_vro 4.9x, code_vgr 4.9x). A byte pre-filter is **3.5x**
    faster (8.9s -> 2.5s) and stays exact, because a file whose bytes contain the
    name is still PARSED to confirm — the speed comes from skipping the ones that
    provably cannot match, never from guessing.

    This pins the pre-filter by counting parses: a corpus where no file mentions
    the name must cost zero parses.
    """
    ref = _game(tmp_path)
    for i in range(5):
        (ref / "libraries" / f"noise{i}.xml").write_bytes(
            b'<macros><macro name="something_else" class="ship_s"/></macros>')
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "x.xml").write_bytes(b'<macros/>')

    defs = _check.EntityDefs(_merge.Config(reference=ref), [mod])
    assert "name_nothing_mentions" not in defs
    assert defs.corpus_parses == 0, (
        f"parsed {defs.corpus_parses} file(s) that cannot contain the name")


def test_the_corpus_tier_still_runs_when_the_mod_cannot_answer(tmp_path):
    """The cheap tier must not become a way to answer 'no' quickly — a name the
    mod does not define still has to reach the corpus before being called
    dangling."""
    ref = _game(tmp_path)
    (ref / "libraries" / "extra.xml").write_bytes(
        b'<macros><macro name="defined_only_in_a_library" class="ship_s"/></macros>')
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "thing_macro.xml").write_bytes(b'<macros/>')

    defs = _check.EntityDefs(_merge.Config(reference=ref), [mod])
    assert "defined_only_in_a_library" in defs
    assert defs.corpus_scanned, "the corpus tier was skipped, so the answer is unsound"


def test_library_defined_macros_count_as_defined(tmp_path):
    """The index is NOT the definition set (gotcha #11). MEASURED: 726 macros in
    libraries/character_macros.xml and 30 components in character_components.xml
    appear in NEITHER index/macros.xml NOR the effective store."""
    ref = _game(tmp_path)
    (ref / "libraries" / "character_macros.xml").write_bytes(
        b'<macros><macro name="character_arg_f_diplomat_01_macro" class="npc"/></macros>')
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "wares.xml").write_bytes(
        b'<wares><ware id="w">'
        b'<component ref="character_arg_f_diplomat_01_macro"/></ware></wares>')

    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    bad = [f for f in report.findings if "diplomat" in f.message]
    assert not bad, f"a library-defined macro read as undefined: {bad}"


# --- the byte reader must not reintroduce packed blindness -------------------

def test_byte_reader_sees_packed_members(tmp_path):
    """`iter_mod_xml_bytes` is a NEW enumeration, and enumeration that misses
    packed content is the single most repeated defect in this codebase (the
    DLC walk alone was written five times). Pin it directly."""
    mod = tmp_path / "packedmod"
    _write_cat(mod, "ext_01.cat", [
        ("assets/units/thing_macro.xml",
         b'<macros><macro name="packed_only_macro" class="ship_s"/></macros>'),
    ])
    got = dict(_scan.iter_mod_xml_bytes(mod))
    assert "assets/units/thing_macro.xml" in got
    assert b"packed_only_macro" in got["assets/units/thing_macro.xml"]


def test_loose_shadows_packed_in_the_byte_reader(tmp_path):
    """Same rule the engine uses, and the same rule `iter_mod_xml` follows: a
    loose file wins over its packed twin. Diverging here would make the two
    readers disagree about the same mod — the two-doors shape."""
    mod = tmp_path / "shadowmod"
    _write_cat(mod, "ext_01.cat", [("libraries/x.xml", b"<packed/>")])
    (mod / "libraries").mkdir(parents=True, exist_ok=True)
    (mod / "libraries" / "x.xml").write_bytes(b"<loose/>")
    got = dict(_scan.iter_mod_xml_bytes(mod))
    assert got["libraries/x.xml"] == b"<loose/>"


def test_corpus_search_resolves_a_name_defined_only_in_a_packed_mod(tmp_path):
    """End to end: a packed overlay's entity must satisfy the corpus tier."""
    ref = _game(tmp_path)
    supplier = tmp_path / "supplier"
    _write_cat(supplier, "ext_01.cat", [
        ("assets/units/s_macro.xml",
         b'<macros><macro name="only_in_a_packed_mod" class="ship_s"/></macros>'),
    ])
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "x.xml").write_bytes(b"<macros/>")

    defs = _check.EntityDefs(_merge.Config(reference=ref, overlays=(supplier,)), [mod])
    assert "only_in_a_packed_mod" in defs, (
        "the corpus tier is blind to packed mods — the recurring defect class")
    assert defs.corpus_parses == 1, (
        f"expected exactly the one candidate file to be parsed, got {defs.corpus_parses}")


# --- F65: the BULK door must answer from the same set as the per-name door ------
#
# `EntityDefs.__contains__` answers a per-name question at per-name cost, and its
# docstring states the population that makes that affordable: 7 distinct references
# across 114 mods miss the eager tiers. True for a MOD -- and a property of that
# CALLER, not of the tool. MEASURED 2026-08-26 with a different caller: one savegame
# carries 5,022 distinct macro references, ~2,500 of which miss, at ~2.5s each. It
# blew a 600s cap with no result. `all_names()` answers the same question in bulk
# (19.4s) and is what `x4save check` builds its whole oracle from.
#
# THE INVARIANT THAT MATTERS is the docstring's own claim: the bulk set is "the same
# set `__contains__` would answer from, NEVER A NARROWER ONE". A narrower bulk set
# would not crash -- it would silently report defined names as DANGLING, inflating
# `x4save check`'s count with false positives that look exactly like real findings.
# That is the narrowing-step shape, in an oracle.
#
# Shipped 2026-08-26 with NO test. This is that test.

def test_all_names_is_not_narrower_than_the_per_name_door(tmp_path):
    """The load-bearing claim. A macro defined ONLY in an asset file -- never
    registered in the index (gotcha #11: MEASURED, 753 such names in the reference
    tree) -- resolves through `__contains__`, so it MUST be in `all_names()`."""
    ref = _game(tmp_path)
    (ref / "assets").mkdir(parents=True, exist_ok=True)
    (ref / "assets" / "unindexed.xml").write_bytes(
        b'<macros><macro name="only_in_an_asset_file" class="ship_s"/></macros>')
    defs = _check.EntityDefs(_merge.Config(reference=ref))
    assert "only_in_an_asset_file" in defs, "per-name door must resolve it"
    assert "only_in_an_asset_file" in defs.all_names(), (
        "the BULK door is narrower than the per-name door -- an oracle built on it "
        "would report a defined name as DANGLING")


def test_all_names_agrees_with_the_per_name_door_in_BOTH_directions(tmp_path):
    """Agreement, not merely containment. A bulk set that is WIDER is also wrong:
    it would mask real dangling references, which is the costlier direction."""
    ref = _game(tmp_path)
    (ref / "assets").mkdir(parents=True, exist_ok=True)
    (ref / "assets" / "extra.xml").write_bytes(
        b'<macros><macro name="defined_a" class="ship_s"/>'
        b'<macro name="defined_b" class="ship_m"/></macros>')
    defs = _check.EntityDefs(_merge.Config(reference=ref))
    bulk = defs.all_names()
    assert len(bulk) >= 2, f"bulk set has {len(bulk)} names -- too small to mean anything"
    for name in ("defined_a", "defined_b", "nothing_defines_this", "nor_this"):
        assert (name in defs) == (name in bulk), (
            f"{name!r}: per-name says {name in defs}, bulk says {name in bulk}")


def test_all_names_is_CACHED_so_a_many_name_caller_pays_once(tmp_path):
    """The entire point of the accessor. Rebuilding per call would reintroduce F65
    in a slower shape, and nothing in the output would say so."""
    ref = _game(tmp_path)
    defs = _check.EntityDefs(_merge.Config(reference=ref))
    first = defs.all_names()
    assert defs.all_names() is first, "not cached -- a 5,022-name caller pays 5,022 times"


def test_the_agreement_check_can_actually_fail(tmp_path):
    """Proven-to-fail guard (#26). If a narrowed bulk set did NOT trip the comparison
    above, that comparison would be decoration."""
    ref = _game(tmp_path)
    (ref / "assets").mkdir(parents=True, exist_ok=True)
    (ref / "assets" / "u.xml").write_bytes(
        b'<macros><macro name="present_everywhere" class="ship_s"/></macros>')
    defs = _check.EntityDefs(_merge.Config(reference=ref))
    narrowed = defs.all_names() - {"present_everywhere"}
    assert ("present_everywhere" in defs) != ("present_everywhere" in narrowed), (
        "a deliberately narrowed set must break the agreement the tests above assert")


def test_all_names_is_NAMESPACE_AGNOSTIC_like_the_per_name_door(tmp_path):
    """macro UNION component, not macros alone (gotcha #11).

    `<component ref>` names a MACRO inside `libraries/wares.xml` but a COMPONENT
    inside a macro file — one attribute, two namespaces — and in a `<diff>` carrying
    `<replace sel="//component">` at an asset path, element ancestry cannot classify
    it at all. MEASURED: checking membership against the macro namespace alone
    produced **1,792 misses**; the union took 2,300 references down to **3 genuine,
    0 false**.

    A macro-only `all_names()` would therefore hand `x4save check` ~1,800 false
    dangling references that look exactly like real findings. This is the property
    that turns the accessor from plausible into correct."""
    ref = _game(tmp_path)
    (ref / "assets").mkdir(parents=True, exist_ok=True)
    (ref / "assets" / "both_namespaces.xml").write_bytes(
        b'<macros><macro name="a_macro_name" class="ship_s"/></macros>')
    (ref / "libraries" / "component.xml").write_bytes(
        b'<components><component name="a_component_name"/></components>')
    defs = _check.EntityDefs(_merge.Config(reference=ref))
    bulk = defs.all_names()
    assert "a_macro_name" in bulk, "macro namespace missing from the bulk set"
    assert "a_component_name" in bulk, (
        "COMPONENT namespace missing — a macro-only bulk set produced 1,792 false "
        "misses when this was measured")
    # and the per-name door must agree, or the two doors disagree about one name
    assert "a_component_name" in defs

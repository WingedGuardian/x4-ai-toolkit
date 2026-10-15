"""End-to-end checks incl. the t-file UNION regression (ATD strings in 0001.xml)."""

from pathlib import Path

from lxml import etree

from x4validate import _check, _merge


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_missing_reference_tree_is_loud_error_not_false_ok(tmp_path):
    """A mod with a real no-op sel must NOT pass just because reference is absent."""
    cfg = _merge.Config(reference=tmp_path / "does_not_exist")
    mod = tmp_path / "mod"
    _write(mod / "libraries/wares.xml",
           '<diff><replace sel="//ware[@id=\'ore\']/@x">1</replace></diff>')

    report = _check.validate(mod, cfg)
    assert report.errors, "missing reference tree must produce an error, not 'OK'"
    assert any(f.category == "reference" for f in report.errors)


def test_empty_reference_tree_is_loud_error(tmp_path):
    """Reference dir exists but has no base wares.xml -> still a loud error."""
    ref = tmp_path / "reference"
    ref.mkdir()
    cfg = _merge.Config(reference=ref)
    mod = tmp_path / "mod"
    _write(mod / "libraries/wares.xml",
           '<diff><replace sel="//ware[@id=\'ore\']/@x">1</replace></diff>')

    report = _check.validate(mod, cfg)
    assert any(f.category == "reference" for f in report.errors)


def test_text_defs_union_across_base_dlc_mod(tmp_path):
    ref = tmp_path / "reference"
    # Base English file (would be wiped by a naive override):
    _write(ref / "t/0001-l044.xml",
           '<language id="44"><page id="1"><t id="1">base</t></page></language>')
    # DLC adds a different page via a FULL <language> file (must union, not override):
    _write(ref / "extensions/ego_dlc_x/t/0001-l044.xml",
           '<language id="44"><page id="2"><t id="2">dlc</t></page></language>')
    cfg = _merge.Config(reference=ref)

    # Mod defines its strings in the language-neutral 0001.xml via a diff:
    mod = tmp_path / "mod"
    _write(mod / "t/0001.xml",
           '<diff><add sel="/language"><page id="111204"><t id="200">x</t></page></add></diff>')

    defs = _check.collect_text_defs(cfg, [mod])
    assert ("1", "1") in defs       # base survived
    assert ("2", "2") in defs       # DLC unioned
    assert ("111204", "200") in defs  # mod's neutral-file string seen


def test_validate_no_false_dangling_when_strings_in_neutral_file(tmp_path):
    ref = tmp_path / "reference"
    _write(ref / "libraries/factions.xml", '<factions/>')
    _write(ref / "t/0001-l044.xml", '<language id="44"><page id="1"><t id="1">b</t></page></language>')
    _write(ref / "libraries/wares.xml", '<wares/>')
    cfg = _merge.Config(reference=ref)

    mod = tmp_path / "mod"
    _write(mod / "libraries/factions.xml",
           '<diff><add sel="/factions"><faction id="trust" name="{111204,200}"/></add></diff>')
    _write(mod / "t/0001.xml",
           '<diff><add sel="/language"><page id="111204"><t id="200">Trust</t></page></add></diff>')

    report = _check.validate(mod, cfg)
    ref_findings = [f for f in report.findings if f.category == "ref"]
    assert ref_findings == [], [f.message for f in ref_findings]


# --------------------------------------------------------------------------
# extensions/ego_dlc_*/... whose DLC was never unpacked into reference/.
#
# Real incident (2026-07-26): ego_dlc_mini_01 (Hyperion Pack) and ego_dlc_mini_02
# (Envoy Pack) are genuinely installed+packed in the live game but were never
# unpacked into reference/ (only the 6 DLC named in CLAUDE.md were). A patch
# targeting Hyperion content reported a hard ERROR ("no base game file"), which
# asserts the file doesn't exist — something we cannot actually know, since our
# reference simply never covered that DLC.
# --------------------------------------------------------------------------

def test_unpacked_dlc_missing_from_reference_is_info_not_error(tmp_path, monkeypatch):
    ref = tmp_path / "reference"
    # only one DLC unpacked into reference/, mirroring the real gap
    _write(ref / "extensions/ego_dlc_split/libraries/wares.xml", "<wares/>")
    cfg = _merge.Config(reference=ref)

    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / "extensions/ego_dlc_mini_01/assets/units/size_l/ship_l.xml",
           '<diff><replace sel="//ship/@id">x</replace></diff>')

    # The DLC really is present in the game root — that is what makes
    # "installed but not readable from reference/" a true statement here.
    game = tmp_path / "game"
    (game / "extensions/ego_dlc_mini_01").mkdir(parents=True)
    monkeypatch.setattr(_merge, "GAME_ROOT", game)

    sev, cat, msg = _check._no_base_finding(
        "extensions/ego_dlc_mini_01/assets/units/size_l/ship_l.xml", cfg)
    assert sev == "info" and cat == "unverifiable"
    assert "is installed" in msg and "ego_dlc_mini_01" in msg


def test_dlc_absent_from_game_root_is_not_called_installed(tmp_path, monkeypatch):
    """A DLC that is not installed must never be described as installed.

    Regression for a real finding: an X Rebirth mod patching `ego_dlc_2` and
    `ego_dlc_teladi_outpost` drew 11 findings each asserting the DLC "is
    installed but was never unpacked". Neither is an X4 DLC at all. The branch
    reported an install it had never checked — and in a normally-configured run
    it is reachable ONLY when the DLC is missing, so the claim was false every
    time it could fire.
    """
    ref = tmp_path / "reference"
    _write(ref / "extensions/ego_dlc_split/libraries/wares.xml", "<wares/>")
    cfg = _merge.Config(reference=ref)

    game = tmp_path / "game"
    (game / "extensions/ego_dlc_split").mkdir(parents=True)   # exists, but not ego_dlc_2
    monkeypatch.setattr(_merge, "GAME_ROOT", game)

    sev, cat, msg = _check._no_base_finding("extensions/ego_dlc_2/md/Setup_DLC2.xml", cfg)
    assert sev == "info" and cat == "inactive"
    assert "not installed" in msg
    assert "is installed but" not in msg


def test_dlc_verdict_admits_it_cannot_tell_without_a_game_root(tmp_path, monkeypatch):
    """No game root => no evidence either way, and we must say so."""
    ref = tmp_path / "reference"
    _write(ref / "extensions/ego_dlc_split/libraries/wares.xml", "<wares/>")
    cfg = _merge.Config(reference=ref)
    monkeypatch.setattr(_merge, "GAME_ROOT", tmp_path / "no-such-game-root")

    sev, cat, msg = _check._no_base_finding("extensions/ego_dlc_2/md/Setup_DLC2.xml", cfg)
    assert sev == "info" and cat == "unverifiable"
    assert "not configured" in msg


def test_dlc_unpacked_in_reference_still_reports_real_path_mismatch(tmp_path):
    ref = tmp_path / "reference"
    _write(ref / "extensions/ego_dlc_split/libraries/wares.xml", "<wares/>")
    cfg = _merge.Config(reference=ref)

    sev, cat, msg = _check._no_base_finding(
        "extensions/ego_dlc_split/libraries/nonexistent.xml", cfg)
    assert sev == "error" and cat == "path"


# --------------------------------------------------------------------------
# F19: a <diff> at a BARE mirrored path over another MOD's file is INERT — the
# engine only consults reference/ + DLC for that path and never opens the file.
# Tier B hid this completely: an installed mod's full file satisfies `base_found`,
# so the tree is non-None and the Tier A error is CURED by installing more mods.
# Measured on the live 101-mod install before the fix: Tier A 7 errors,
# Tier B 0 errors exit 0 — a false OK, in the tier used for cross-mod work.
# Population: 7 true positives in 1 mod, against 33 t/ files that must NOT flag.
# --------------------------------------------------------------------------

def _f19_world(tmp_path, *, base_file: str | None = None):
    """reference/ (+ optional base file) and one installed supplier mod.

    `libraries/wares.xml` is NOT decoration: validate() refuses to run against an
    incomplete reference tree and returns only that error. Without the sentinel these
    tests would assert "no path finding" against a check that never executed — three
    of the six failed outright and the t/ one passed VACUOUSLY when this was omitted.
    """
    ref = tmp_path / "reference"
    (ref / "extensions").mkdir(parents=True)
    _write(ref / "libraries/wares.xml", "<wares/>")
    if base_file:
        _write(ref / base_file, "<macros/>")
    supplier = tmp_path / "supplier_mod"
    _write(supplier / "content.xml", '<content id="supplier"/>')
    return ref, supplier


def test_bare_path_diff_over_a_mod_only_file_is_an_error(tmp_path):
    """The 7-file class: names the supplier and the path the file must move to."""
    ref, supplier = _f19_world(tmp_path)
    rel = "assets/units/size_s/macros/ship_x_macro.xml"
    _write(supplier / rel, '<macros><macro name="ship_x"/></macros>')
    cfg = _merge.Config(reference=ref, overlays=(supplier,))

    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / rel, '<diff><replace sel="//macro/@name">y</replace></diff>')

    report = _check.validate(mod, cfg)
    paths = [f for f in report.errors if f.category == "path"]
    assert len(paths) == 1, [f.message for f in report.errors]
    assert "supplier_mod" in paths[0].message
    assert f"extensions/supplier_mod/{rel}" in paths[0].message


def test_bare_path_diff_over_a_real_base_file_is_fine(tmp_path):
    """The ~1,510-vpath majority. Also covers a DLC-supplied base, which reaches
    base_from_game through the overlay loop rather than the reference/ branch."""
    rel = "libraries/shipsizes.xml"
    ref, supplier = _f19_world(tmp_path, base_file=rel)
    cfg = _merge.Config(reference=ref, overlays=(supplier,))
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / rel, '<diff><add sel="//macros"><macro name="q"/></add></diff>')
    assert not [f for f in _check.validate(mod, cfg).errors if f.category == "path"]

    # DLC layer supplies it, reference/ does not.
    dlc_rel = "libraries/dlconly.xml"
    _write(ref / "extensions/ego_dlc_split" / dlc_rel, "<macros/>")
    mod2 = tmp_path / "mod2"
    _write(mod2 / "content.xml", '<content id="mod2"/>')
    _write(mod2 / dlc_rel, '<diff><add sel="//macros"><macro name="q"/></add></diff>')
    assert not [f for f in _check.validate(mod2, cfg).errors if f.category == "path"]


def test_t_file_diff_with_only_mod_suppliers_is_not_flagged(tmp_path):
    """The 33 false positives this check had to avoid. t/*.xml has no single base
    file; build_effective synthesizes a <language> root because the ENGINE supplies
    one, so a t/ diff is well-founded even when only mods ship that path."""
    ref, supplier = _f19_world(tmp_path)
    _write(supplier / "t/0001.xml", '<language id="44"><page id="1"/></language>')
    cfg = _merge.Config(reference=ref, overlays=(supplier,))

    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / "t/0001.xml",
           '<diff><add sel="/language"><page id="99"><t id="1">x</t></page></add></diff>')
    assert not [f for f in _check.validate(mod, cfg).errors if f.category == "path"]


def test_nested_form_is_never_flagged_inert(tmp_path):
    """The CORRECT idiom must stay silent — it is what the finding tells you to do."""
    ref, supplier = _f19_world(tmp_path)
    rel = "assets/units/size_s/macros/ship_x_macro.xml"
    _write(supplier / rel, '<macros><macro name="ship_x"/></macros>')
    cfg = _merge.Config(reference=ref, overlays=(supplier,))

    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / f"extensions/supplier_mod/{rel}",
           '<diff><replace sel="//macro/@name">y</replace></diff>')
    assert not [f for f in _check.validate(mod, cfg).errors if f.category == "path"]


def test_no_source_anywhere_still_reports_no_base_finding(tmp_path):
    """No double-report with the existing 16-file 'nobody supplies it' class:
    that one leaves tree None, so the inert branch is never reached."""
    ref, supplier = _f19_world(tmp_path)
    cfg = _merge.Config(reference=ref, overlays=(supplier,))
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / "libraries/nobody_has_this.xml",
           '<diff><replace sel="//x/@y">1</replace></diff>')

    paths = [f for f in _check.validate(mod, cfg).errors if f.category == "path"]
    assert len(paths) == 1
    assert "no base game file" in paths[0].message
    assert "engine never loads it" not in paths[0].message


def test_inert_bare_path_skips_op_checking(tmp_path):
    """A sel that would FAIL against the supplier's tree must not add a second
    finding: op verdicts on a file the engine never opens are noise, and a
    PASSING one would be the false reassurance this whole check exists to kill."""
    ref, supplier = _f19_world(tmp_path)
    rel = "assets/units/size_s/macros/ship_x_macro.xml"
    _write(supplier / rel, '<macros><macro name="ship_x"/></macros>')
    cfg = _merge.Config(reference=ref, overlays=(supplier,))

    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="mod"/>')
    _write(mod / rel, '<diff><replace sel="//nope/@gone">1</replace></diff>')

    report = _check.validate(mod, cfg)
    assert len([f for f in report.errors if f.category == "path"]) == 1
    assert not [f for f in report.errors if f.category == "sel"]


def test_only_file_missing_reports_instead_of_raising(tmp_path):
    """A typo'd --file must be a finding, not a traceback.

    `_merge.parse_file` raises OSError for a missing path, but the handler only
    caught XMLSyntaxError — so `x4validate --file no/such.xml` crashed with a raw
    lxml OSError instead of telling the user the path was wrong.
    """
    mod = tmp_path / "mod"
    mod.mkdir()
    report = _check.Report()
    _check.check_sel_resolution_one(mod / "nope.xml", mod, _merge.Config(), report)
    assert any(f.category == "path" and "cannot read file" in f.message
               for f in report.findings), report.findings


def test_huge_op_count_is_flagged_before_the_slow_part(tmp_path):
    """A file with a pathological op count must SAY so, not just appear to hang.

    Applying a diff is O(n^2) in ops-per-file (each op re-evaluates its selector
    against a tree the previous ops grew) — inherent, not a defect to optimize
    away. What IS fixable is the silence: a 32k-op file took >900s with no
    indication that anything was wrong.
    """
    mod = tmp_path / "big"
    (mod / "libraries").mkdir(parents=True)
    (mod / "content.xml").write_text('<content id="big" name="b" version="1"/>',
                                     encoding="utf-8")
    ops = "".join(f'<add sel="//wares"><ware id="w{i}"/></add>'
                  for i in range(_check._LARGE_OP_COUNT))
    (mod / "libraries" / "wares.xml").write_text(f"<diff>{ops}</diff>", encoding="utf-8")

    report = _check.Report()
    root = _merge.parse_file(mod / "libraries" / "wares.xml")
    _check._warn_if_pathologically_large(root, "libraries/wares.xml", report)
    assert any(f.severity == "info" and "O(n^2)" in f.message for f in report.findings), \
        report.findings


def test_normal_op_count_is_not_flagged(tmp_path):
    """The largest file in a real ~120-mod install is 1,443 ops — well under the
    threshold, so ordinary content must never see this note."""
    mod = tmp_path / "normal"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "wares.xml").write_text(
        "<diff>" + "".join(f'<add sel="//wares"><ware id="w{i}"/></add>'
                           for i in range(1443)) + "</diff>", encoding="utf-8")
    report = _check.Report()
    root = _merge.parse_file(mod / "libraries" / "wares.xml")
    _check._warn_if_pathologically_large(root, "libraries/wares.xml", report)
    assert not report.findings


# --- the nested cross-mod path (mutation survivor, 2026-09-02) ----------------
#
# `nested = _merge._nested_target(vpath, config.packed_dlc_names())` -> `nested = None`
# survived the whole suite. That call is what tells a CROSS-MOD patch from a broken
# one: `extensions/<target>/<rel>` is owned by <target>, not by the base game
# (gotcha #6, and the engine never even opens the bare form). Without it, a patch
# aimed at a mod the user does not have installed -- a designed no-op -- is reported
# as a hard path error, and the two states become indistinguishable in the output.

def test_a_patch_targeting_an_UNINSTALLED_extension_is_a_designed_no_op(monkeypatch):
    from x4validate import _check as C, _merge
    monkeypatch.setattr(C, "_installed_folders", lambda: {"some_other_mod"})
    sev, code, msg = C._no_base_finding(
        "extensions/not_installed_mod/libraries/wares.xml", _merge.Config())
    assert (sev, code) == ("info", "inactive"), (sev, code, msg)
    assert "not_installed_mod" in msg, msg


def test_a_patch_targeting_an_INSTALLED_extension_is_still_a_real_error(monkeypatch):
    """The twin. Without it, a check that excused every extensions/ path would pass
    the test above while hiding genuine path mismatches -- which is the whole reason
    the cross-mod branch has to distinguish installed from not."""
    from x4validate import _check as C, _merge
    monkeypatch.setattr(C, "_installed_folders", lambda: {"target_mod"})
    sev, code, _msg = C._no_base_finding(
        "extensions/target_mod/libraries/wares.xml", _merge.Config())
    assert (sev, code) == ("error", "path"), (sev, code)


def test_a_PLAIN_vpath_is_unaffected_by_the_cross_mod_branch(monkeypatch):
    from x4validate import _check as C, _merge
    monkeypatch.setattr(C, "_installed_folders", lambda: set())
    sev, code, _msg = C._no_base_finding("libraries/wares.xml", _merge.Config())
    assert (sev, code) == ("error", "path"), (sev, code)


def test_an_UNLISTABLE_extensions_dir_says_so_rather_than_excusing_the_patch(monkeypatch):
    """'Could not check' is never 'nothing wrong': downgrading this to `inactive`
    would silently excuse a genuine path mismatch."""
    from x4validate import _check as C, _merge
    monkeypatch.setattr(C, "_installed_folders", lambda: None)
    sev, code, msg = C._no_base_finding(
        "extensions/whatever/libraries/wares.xml", _merge.Config())
    assert (sev, code) == ("info", "unverifiable"), (sev, code, msg)


# ---------------------------------------------- the completeness ORACLE (2026-09-02)
#
# `_refs` answers the `<component ref>` question with whatever set it is handed, and the
# macro INDEX alone is the wrong oracle. A ware whose component ref names a macro defined
# in an asset file but never registered in index/macros.xml was reported "missing
# 'component'" by check_completeness while check_references reported the same ref
# resolving -- two checks contradicting each other about one attribute.


def _completeness_tree(tmp_path, *, define_macro=True, register_in_index=False):
    """A reference tree with one vanilla analogue, and a mod adding one ware.

    The only variable is WHERE the mod's macro is defined: in an asset file, in the
    index, or nowhere.
    """
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml",
           '<wares>'
           '<ware id="vanilla_thing" name="{20101,101}" description="{20101,102}" '
           'group="s" transport="container" volume="1" tags="economy">'
           '<price min="1" average="2" max="3"/>'
           '<production time="1" amount="1" method="default" name="{20206,101}"/>'
           '<component ref="vanilla_macro"/>'
           '<restriction licence="generic"/>'
           '<owner faction="argon"/>'
           '</ware></wares>')
    _write(ref / "t/0001-l044.xml",
           '<language id="44">'
           '<page id="20101"><t id="101">n</t><t id="102">d</t></page>'
           '<page id="20206"><t id="101">m</t></page>'
           '<page id="30101"><t id="101">N</t><t id="102">D</t></page>'
           '</language>')
    _write(ref / "index/macros.xml",
           '<index><entry name="vanilla_macro" value="a/b"/></index>')
    _write(ref / "a/b.xml", '<macros><macro name="vanilla_macro"/></macros>')

    mod = tmp_path / "mod"
    if register_in_index:
        _write(mod / "index/macros.xml",
               '<diff><add sel="/index"><entry name="my_macro" value="x/y"/></add></diff>')
    if define_macro:
        # An ASSET file, which is the legal-and-common case this test is about.
        _write(mod / "assets/units/size_s/macros/my_macro.xml",
               '<macros><macro name="my_macro" class="ship_s"/></macros>')
    _write(mod / "libraries/wares.xml",
           '<diff><add sel="/wares">'
           '<ware id="my_ware" name="{30101,101}" description="{30101,102}" '
           'group="s" transport="container" volume="1" tags="economy">'
           '<price min="1" average="2" max="3"/>'
           '<production time="1" amount="1" method="default" name="{20206,101}"/>'
           '<component ref="my_macro"/>'
           '<restriction licence="generic"/>'
           '<owner faction="argon"/>'
           '</ware></add></diff>')
    return ref, mod


def _completeness_component_errors(tmp_path, **kw):
    ref, mod = _completeness_tree(tmp_path, **kw)
    cfg = _merge.Config(reference=ref)
    report = _check.Report()
    _check.check_completeness(mod, cfg, report, "ware:my_ware", "ware:vanilla_thing")
    assert not report.skipped, f"the fixture degraded: {report.skipped}"
    return [e for e in report.errors if "component" in str(getattr(e, "message", e))]


def test_a_macro_defined_in_an_ASSET_file_does_not_gate_completeness(tmp_path):
    """The bug, reproduced: the macro exists, it is simply not in index/macros.xml --
    which is where a mod under development normally is."""
    assert _completeness_component_errors(tmp_path) == []


def test_a_macro_registered_in_the_INDEX_does_not_gate_either(tmp_path):
    """The half that already worked. Both routes to "this macro exists" must agree, or
    the oracle is still deciding by where the definition happens to live."""
    assert _completeness_component_errors(tmp_path, register_in_index=True) == []


def test_a_component_ref_naming_a_macro_defined_NOWHERE_still_gates(tmp_path):
    """The twin, and the reason the check exists at all. An oracle widened until it
    accepts everything would pass both tests above while checking nothing."""
    errs = _completeness_component_errors(tmp_path, define_macro=False)
    assert errs, "a ref to a macro that exists nowhere must still be reported"
    assert "missing 'component'" in str(getattr(errs[0], "message", errs[0]))


# --- the DEGRADED completeness path, which nothing reached ---------------------------
#
# `_completeness_component_errors` above asserts `not report.skipped`, so every case
# built on it is a NON-degraded one by construction. That is right for what those tests
# are about and it left the degraded branch untested: the mutation gate reported the
# None-preservation as having no test that could detect its removal.
#
#   completeness_defs = (None if macro_def_set is None else EntityDefs(...))
#
# None means "the index could not be built", and `_refs` answers that with a presence
# check alone. EntityDefs has no such state -- handed one, it reports every reference
# it cannot resolve as MISSING. So replacing None with EntityDefs turns an unreadable
# index into a wall of false "missing" errors about references that are perfectly fine.

def test_an_UNREADABLE_index_does_not_turn_real_references_into_missing_ones(
        tmp_path, monkeypatch):
    """The discriminating fixture: a macro defined NOWHERE.

    With None preserved, `_refs` checks presence alone and stays quiet. With
    EntityDefs substituted, the same reference becomes an error -- which is the
    behaviour an unreadable index must NOT produce, because the index being
    unreadable says nothing about whether the reference is real.
    """
    ref, mod = _completeness_tree(tmp_path, define_macro=False,
                                  register_in_index=False)
    # The degraded state itself: collect_macro_defs answers None when it could not
    # build the effective index. Monkeypatched rather than simulated by corrupting a
    # file, so the test pins the CONTRACT (None) instead of one way of reaching it.
    monkeypatch.setattr(_check, "collect_macro_defs",
                        lambda *a, **k: None)
    cfg = _merge.Config(reference=ref)
    report = _check.Report()
    _check.check_completeness(mod, cfg, report, "ware:my_ware", "ware:vanilla_thing")
    component_errors = [e for e in report.errors
                        if "component" in str(getattr(e, "message", e))]
    assert component_errors == [], (
        "an unreadable macro index produced 'missing component' errors; None was not "
        f"preserved: {component_errors}")


def test_the_NON_degraded_path_still_reports_a_genuinely_missing_component(tmp_path):
    """The twin that stops the test above from being satisfiable by silence.

    With a readable index, a macro defined nowhere IS an error -- so the quiet in the
    degraded case is a decision about an unreadable index, not the check being off.
    """
    ref, mod = _completeness_tree(tmp_path, define_macro=False,
                                  register_in_index=False)
    cfg = _merge.Config(reference=ref)
    report = _check.Report()
    _check.check_completeness(mod, cfg, report, "ware:my_ware", "ware:vanilla_thing")
    assert any("component" in str(getattr(e, "message", e)) for e in report.errors), (
        "a macro defined nowhere did not error even with a readable index; the "
        "degraded-path test above would then prove nothing")


# --- half a completeness request is a request that could not be honoured ------
#
# `--entity` and `--like` are two independent optional arguments with no mutual
# requirement, and `validate` guarded the check with `if entity and like:`. So
# supplying one of them fell off the end of that `if` and the run printed
# "OK: no issues found" with rc 0 -- no note, no NOT CHECKED, no warning.
#
# MEASURED, one mod, three invocations:
#   --entity X --like Y  -> "completeness checked kinds: component, definition,
#                            description_string, name_string, owner, price,
#                            production, restriction" + an INFO finding
#   --entity X           -> "OK: no issues found"
#   --like Y             -> "OK: no issues found"
# Every trace of the requested check is ABSENT in the last two -- not downgraded,
# not skipped.
#
# README: "3 degraded - a check you asked for could not run, so a clean result
# proves nothing", and it names "a --like analogue that does not exist" as a case
# that reaches it. A --like that was never supplied is a stronger form of the same
# condition. `check_completeness` already refuses the vacuous comparison one layer
# down, for the same reason.

def _minimal_mod(tmp_path):
    """A mod and a REAL reference tree.

    Both are needed: `validate` reports a hard reference error and returns
    before the completeness dispatch when the reference tree is absent, so a
    fixture without one tests the early-exit path instead of the one named in
    these tests. (It did, on the first draft.)
    """
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml", "<wares><ware id=\"ore\"/></wares>")
    _write(ref / "t/0001-l044.xml",
           '<language id="44"><page id="1"><t id="1">base</t></page></language>')
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="m" version="1"/>')
    return mod, _merge.Config(reference=ref)


def test_entity_without_like_is_a_DEGRADED_skip_not_a_clean_pass(tmp_path):
    mod, cfg = _minimal_mod(tmp_path)
    report = _check.validate(mod, cfg, entity="ware:probe")
    assert report.degraded, (
        "a completeness check was requested and did not run, and the report says "
        "nothing about it -- so a clean result would read as evidence")
    assert any("completeness" in s.what for s in report.degraded)
    assert any("--like" in s.why for s in report.degraded)


def test_like_without_entity_is_also_a_DEGRADED_skip(tmp_path):
    """The mirror. A guard written for one direction only would pass the test
    above and leave the other half live."""
    mod, cfg = _minimal_mod(tmp_path)
    report = _check.validate(mod, cfg, like="ware:ore")
    assert report.degraded
    assert any("--entity" in s.why for s in report.degraded)


def test_NEITHER_flag_is_not_a_skip(tmp_path):
    """The twin that matters most: a completeness check nobody asked for must not
    degrade every ordinary run. A guard written as `if not (entity and like)`
    would pass both tests above and turn the default invocation into rc 3.
    """
    mod, cfg = _minimal_mod(tmp_path)
    report = _check.validate(mod, cfg)
    assert not any("completeness" in s.what for s in report.skipped), (
        "an unrequested completeness check was reported as skipped work")


# --- the --file fast path erased what the full run discloses ------------------
#
# `check_sel_resolution_one` is what the auto-validate hook runs on whatever the
# user just edited, and `validate()` RETURNS immediately after calling it, so it is
# the ONLY check that runs in --file mode. It had two silent narrowings:
#
#   * `if root.tag != "diff": return` -- a complete file produced "OK: no issues
#     found", rc 0, no notes and no NOT CHECKED, having examined nothing at all.
#   * `merged.skipped` was never consulted, so an overlay that could not be parsed
#     -- which leaves the comparison tree INCOMPLETE -- vanished. MEASURED on the
#     same bytes: the full run reported
#     skipped=[('sel-resolution against a complete tree (..)', True)] and the
#     --file run reported skipped=[].

class _FakeMerged:
    """Stands in for `_merge.build_effective`'s result.

    What is under test is whether this function CONSUMES `merged.skipped`, not
    whether `build_effective` produces it -- that half has its own coverage in
    _merge. Monkeypatching keeps the two questions apart.
    """

    def __init__(self, tree, skipped):
        self.tree = tree
        self.skipped = skipped
        self.sources = []
        self.base_found = tree is not None
        self.base_from_game = tree is not None


def _diff_file(tmp_path):
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    f = mod / "libraries" / "wares.xml"
    _write(f, '<diff><replace sel="//ware[@id=\'ore\']/@volume">2</replace></diff>')
    return mod, f


def test_a_COMPLETE_file_is_disclosed_not_reported_as_a_clean_pass(tmp_path):
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    f = mod / "libraries" / "probe.xml"
    _write(f, '<wares><ware id="x"/></wares>')

    report = _check.Report()
    _check.check_sel_resolution_one(f, mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert report.skipped, (
        "a --file run examined nothing and said nothing -- the sentence the "
        "skipped channel exists to prevent")
    assert "not a <diff>" in " ".join(s.why for s in report.skipped)
    assert not report.errors, "a complete file is not an error, it is unexaminable here"


def test_that_disclosure_does_not_DEGRADE(tmp_path):
    """A complete file will never contain selectors, so exit 3 here would be
    permanent and unclearable -- the same reason the default-mode script
    disclosure is not degraded."""
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    f = mod / "libraries" / "probe.xml"
    _write(f, '<wares><ware id="x"/></wares>')
    report = _check.Report()
    _check.check_sel_resolution_one(f, mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert not report.degraded


def test_a_DIFF_file_is_not_disclosed_as_unexaminable(tmp_path):
    """The twin: a disclosure that fired for every file would pass the two tests
    above while telling every ordinary hook run that nothing was checked."""
    mod, f = _diff_file(tmp_path)
    report = _check.Report()
    _check.check_sel_resolution_one(f, mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert not any("not a <diff>" in s.why for s in report.skipped)


def test_the_fast_path_reports_the_SAME_degraded_skip_as_the_full_run(
        tmp_path, monkeypatch):
    """Parity, not a new decision: the full run already marks a dropped overlay
    degraded, because the verdict is then computed against an incomplete tree."""
    mod, f = _diff_file(tmp_path)
    monkeypatch.setattr(
        _merge, "build_effective",
        lambda *a, **k: _FakeMerged(etree.fromstring(b"<wares/>"),
                                    ["overlay_x/libraries/wares.xml: not well-formed"]))
    report = _check.Report()
    _check.check_sel_resolution_one(f, mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert report.degraded, (
        "the fast path erased a dropped overlay that the full run marks degraded")
    assert "incomplete" in " ".join(s.why for s in report.degraded)
    assert "overlay_x" in " ".join(s.why for s in report.degraded), \
        "the reason must NAME the overlay, as the full path does"


def test_no_dropped_overlay_means_no_degraded_skip(tmp_path, monkeypatch):
    """The twin for the clause above."""
    mod, f = _diff_file(tmp_path)
    monkeypatch.setattr(
        _merge, "build_effective",
        lambda *a, **k: _FakeMerged(etree.fromstring(b"<wares/>"), []))
    report = _check.Report()
    _check.check_sel_resolution_one(f, mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert not report.degraded


# --- a dropped overlay must never be silent, at ANY build_effective site ------
#
# `MergeResult.skipped`'s own field comment states the contract: "Work NOT done:
# overlays that could not be parsed and were left out of the tree. Without this
# channel a malformed overlay is indistinguishable from an absent one, and the
# resulting tree looks complete when it is not."
#
# It was honoured at 3 of 14 call sites in this module. REPRODUCED: two overlays
# differing only by a missing </diff>, the mod under test unchanged --
#     well-formed : errors=[]                               NOT CHECKED=[]  exit 0
#     MALFORMED   : ['ware reference does not resolve: ..']  NOT CHECKED=[]  exit 1
# so a THIRD-PARTY mod's unparseable file produced a gating error against an
# unrelated mod with the reason erased. Under --tier b every installed extension
# is an overlay, so one malformed file in a 125-mod install can do this.

def test_a_dropped_overlay_is_reported_by_check_references(tmp_path, monkeypatch):
    real = _merge.build_effective

    def dropping(vpath, config, **kw):
        got = real(vpath, config, **kw)
        if vpath == _check.WARES_FILE:
            got.skipped = list(got.skipped) + [
                "ov_broken/libraries/wares.xml: malformed XML, overlay skipped"]
        return got

    monkeypatch.setattr(_merge, "build_effective", dropping)
    mod = tmp_path / "mod"
    _write(mod / "libraries/wares.xml", "<diff/>")
    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert report.degraded, (
        "an overlay was dropped from the comparison tree and the report said nothing")
    why = " ".join(s.why for s in report.degraded)
    assert "ov_broken" in why, "the reason must NAME the overlay that was dropped"
    assert "incomplete" in why


class _Merged:
    """The one field `note_dropped_overlays` reads."""

    def __init__(self, skipped):
        self.skipped = list(skipped)


def test_the_same_dropped_overlay_is_reported_ONCE():
    """`Report.skip` does not deduplicate, and one cause reaches several of these
    builds through different vpaths. One cause, one line.

    ⚠ THIS TEST COULD NOT FAIL until 2026-09-08. It drove `check_references`, which
    reaches exactly ONE `note_dropped_overlays` heading, so no `(what, why)` pair
    could repeat whatever the guard did. MEASURED: deleting the two guard lines from
    `_check.note_dropped_overlays` left the ENTIRE SUITE green -- 1650 passed, rc 0.
    A test that pins a deduplicator has to hand it a duplicate.

    So the guard is now exercised where it lives: the same cause arriving twice under
    ONE heading, which is the real situation the docstring describes.
    """
    report = _check.Report()
    merged = _Merged(["ov_broken/x.xml: malformed XML, overlay skipped"])
    _check.note_dropped_overlays(merged, "ware references", report)
    _check.note_dropped_overlays(merged, "ware references", report)

    same = [s for s in report.skipped if "ov_broken/x.xml" in s.why]
    assert len(same) == 1, (
        "the same dropped overlay was reported %d times under one heading; the "
        "reader sees one cause as several unrelated failures" % len(same))


def test_the_dedup_does_NOT_collapse_the_SAME_cause_under_DIFFERENT_headings():
    """The twin for the other clause, because the guard tests `what` AND `why`.

    A dedup keyed on the reason alone would hide that a second, unrelated check was
    also degraded by that overlay -- and the heading is what tells the reader which
    verdict is now standing on an incomplete tree.
    """
    report = _check.Report()
    merged = _Merged(["ov_broken/x.xml: malformed XML, overlay skipped"])
    _check.note_dropped_overlays(merged, "ware references", report)
    _check.note_dropped_overlays(merged, "macro references", report)

    whats = sorted(s.what for s in report.skipped if "ov_broken/x.xml" in s.why)
    assert whats == ["macro references", "ware references"], (
        "one cause degrading TWO checks must be reported under both headings; got %r"
        % (whats,))


def test_a_WELL_FORMED_overlay_set_produces_no_degraded_skip(tmp_path):
    """The twin. A wiring that reported unconditionally would pass the tests above
    while degrading every ordinary run."""
    mod = tmp_path / "mod"
    _write(mod / "libraries/wares.xml", "<diff/>")
    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=tmp_path / "ref"), report)
    assert not any("could not be parsed" in s.why for s in report.skipped)


def test_EVERY_build_effective_call_site_consults_the_skip_channel():
    r"""The structural pin, and the point of this change.

    The defect was not one call site, it was ELEVEN -- the same omission repeated
    until it was the norm. A behavioural test covers whichever site it happens to
    exercise; this one makes an unwired twelfth site impossible to add quietly.

    Asserted over the AST, not a substring: a comment mentioning `skipped` would
    satisfy a text search, which is exactly the failure mode CLAUDE.md #37 records
    (`assert "FLAG" in text` satisfied by the comment that mentions FLAG).
    """
    import ast as _ast
    src = Path(_check.__file__).read_text(encoding="utf-8")
    tree = _ast.parse(src)

    def calls_build_effective(node):
        for n in _ast.walk(node):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute) \
                    and n.func.attr == "build_effective":
                return True
        return False

    def consults_skips(node):
        for n in _ast.walk(node):
            # `.skipped` ON THE MERGE RESULT, not any attribute of that name.
            # This used to match ANY `.skipped`, and `report.skipped` is all over
            # this module for unrelated reasons -- so a twelfth unwired call site
            # that mentioned `report.skipped` once satisfied the pin. MEASURED: the
            # exact scenario this docstring exists to prevent was added and PASSED.
            if isinstance(n, _ast.Attribute) and n.attr == "skipped" \
                    and not (isinstance(n.value, _ast.Name) and n.value.id == "report"):
                return True
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) \
                    and n.func.id == "note_dropped_overlays":
                return True
        return False

    builders, offenders = [], []
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if not calls_build_effective(node):
            continue
        builders.append(node.name)
        if not consults_skips(node):
            offenders.append("%s (line %d)" % (node.name, node.lineno))
    # THE DENOMINATOR. Without it this passed over ZERO examined functions:
    # MEASURED by renaming the entry point to `_merge.build_eff` and stripping all
    # 13 wired calls -- green, having looked at nothing. Its own sibling at
    # test_prop_depth.py:141 already carried this floor; this one did not.
    assert len(builders) >= 10, (
        "found only %d function(s) calling build_effective in _check.py -- the "
        "matcher has stopped finding them, so a clean result here means nothing. "
        "Found: %r" % (len(builders), builders))
    assert not offenders, (
        "these functions build an effective tree and never ask what was left out "
        "of it, so a malformed overlay is indistinguishable from an absent one:\n  "
        + "\n  ".join(offenders))


# --- the disclosure set must be MONOTONIC -------------------------------------
#
# `--xsd-fast` skips BOTH `check_xsd` and `check_effective_schema`, which reopens
# exactly the holes the default run discloses -- but the disclosure sat behind
# `if not update:`, so ADDING flags REMOVED disclosures. MEASURED on one fixture:
#
#   DEFAULT             rc=1  NOT CHECKED: script-schema, effective-schema
#   --update --xsd-fast rc=1  (no NOT CHECKED section at all)
#
# while the note claimed "Gating required-attribute breakages above are COMPLETE"
# -- an incomplete enumeration presented as complete, and false for nested
# cross-mod script patches, which only the compiled pass reaches.

def _script_mod(tmp_path):
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml", '<wares><ware id="ore"/></wares>')
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="m" version="1"/>')
    _write(mod / "md" / "probe.xml", '<mdscript name="Probe"><cues/></mdscript>')
    return mod, _merge.Config(reference=ref)


def test_xsd_fast_never_discloses_LESS_than_the_default_run(tmp_path):
    """The property, stated directly. A more thorough invocation may disclose
    more, never less -- it skips the same schema passes the default one does."""
    mod, cfg = _script_mod(tmp_path)
    default = {s.what for s in _check.validate(mod, cfg).skipped}
    fast = {s.what for s in _check.validate(mod, cfg, update=True, xsd_fast=True).skipped}
    assert default, "the fixture disclosed nothing by default; it proves nothing"
    assert default <= fast, (
        "--update --xsd-fast disclosed LESS than the plain default run. Missing: %s"
        % sorted(default - fast))


def test_the_xsd_fast_remedy_names_the_flag_the_caller_must_DROP(tmp_path):
    """A remedy of "--update" is useless to someone who just passed --update. The
    disclosure has to name the thing they can actually change."""
    mod, cfg = _script_mod(tmp_path)
    fast = _check.validate(mod, cfg, update=True, xsd_fast=True)
    why = " ".join(s.why for s in fast.skipped)
    assert "--xsd-fast" in why, why


def test_the_xsd_fast_note_no_longer_claims_UNQUALIFIED_completeness(tmp_path):
    """`check_required_attrs` filters to DIRECT CHILDREN of md/ and aiscripts/, and
    the one thing covering nested cross-mod patches is `validate_nested_scripts`,
    called from `check_xsd` -- exactly what --xsd-fast skips. So the old claim
    "Gating required-attribute breakages above are COMPLETE" was false for the 15
    such files `_xsd.strip_nesting` measures across 7 mods."""
    mod, cfg = _script_mod(tmp_path)
    note = " ".join(_check.validate(mod, cfg, update=True, xsd_fast=True).notes)
    assert "--xsd-fast" in note
    assert "DIRECT CHILDREN" in note, note
    assert "check_effective_schema" in note, (
        "the enumeration of what is skipped still omits the merged data-file pass")


def test_the_DEFAULT_run_still_names_update_as_the_remedy(tmp_path):
    """The twin. Parameterising the remedy must not change the default advice."""
    mod, cfg = _script_mod(tmp_path)
    why = " ".join(s.why for s in _check.validate(mod, cfg).skipped)
    assert "`--update`" in why
    assert "--xsd-fast" not in why, (
        "the default run advised dropping a flag the caller never passed")


# --- a CROSS-MOD reference is unanswerable under Tier A, not wrong -------------
#
# The promotion of full-file ref findings to `error` (2026-08-13) was earned: 114
# mods, 14 findings, every one confirmed genuine, zero false positives, and pinned
# by tests/test_reference_scope.py. What decayed since is the PREMISE, not the
# decision -- the modlist grew a cross-mod pair the run never covered.
#
# MEASURED 2026-09-06, Tier A, 125 installed extensions: of 16 full-file ref
# errors, `cpsdo_vro`'s `bullet_cpsdo_turret_m_ion_01_mk4` and
# `bullet_cpsdo_l_ion_01_mk1` ARE defined -- in `cpsdo_zb_modpack`, the mod
# cpsdo_vro patches -- and both findings sit at `extensions/cpsdo_zb_modpack/...`.
# `Config.overlays` empty is Tier A: "base+DLC only, which cannot see content that
# another mod adds, removes or overrides", in that field's own words.
#
# Per-item corpus effect of this change: ref errors 74 -> 72, info 0 -> 2. Exactly
# the two verified false positives moved, and cpsdo_vro goes from 2 errors to 0.

def _nested_fixture(tmp_path, target: str, *, overlays=()):
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml", '<wares><ware id="ore"/></wares>')
    _write(ref / "index/macros.xml", "<index/>")
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="m" version="1"/>')
    _write(mod / "extensions" / target / "libraries" / "wares.xml",
           '<wares><ware id="w_probe">'
           '<component ref="defined_in_the_other_mod"/></ware></wares>')
    report = _check.Report()
    _check.check_references(
        mod, _merge.Config(reference=ref, overlays=tuple(overlays)), report)
    return report


def test_a_cross_MOD_reference_is_INFO_under_tier_A(tmp_path):
    """The verified false-positive class. Tier A cannot merge the target mod, so
    the reference is unanswerable there rather than wrong."""
    report = _nested_fixture(tmp_path, "some_other_mod")
    refs = [f for f in report.findings if f.category == "ref"]
    assert refs, "the fixture produced no reference finding; it proves nothing"
    assert {f.severity for f in refs} == {"info"}, (
        "a cross-mod reference GATED under Tier A, which structurally cannot "
        "resolve it: %s" % [(f.severity, f.message) for f in refs])
    assert "--tier b" in " ".join(f.message for f in refs), (
        "the demoted finding must name the mode that CAN answer")
    assert not report.errors


def test_a_cross_DLC_reference_still_ERRORS(tmp_path):
    """The clause that keeps this narrow. Tier A merges base AND DLC, so a patch
    at `extensions/ego_dlc_*/` names content it CAN see -- demoting that would
    hide a real finding, and a first draft of this rule did exactly that to
    `ebi_timelines_faction_use_ship`'s `ship_spl_xl_ark_01_c`."""
    report = _nested_fixture(tmp_path, "ego_dlc_timelines")
    refs = [f for f in report.findings if f.category == "ref"]
    assert refs, "the fixture produced no reference finding; it proves nothing"
    assert {f.severity for f in refs} == {"error"}, (
        "a DLC-targeting reference was demoted; Tier A can resolve those")


def test_a_cross_mod_reference_ERRORS_again_under_tier_B(tmp_path):
    """Tier B DOES merge the other mods, so a reference that still dangles there
    really does dangle -- demoting it would delete the one mode that can answer.

    The overlay IS the target (same folder name). It used to be an unrelated
    `other` overlay while the target was merged nowhere and installed nowhere --
    i.e. an INACTIVE patch, which AUDIT-2026-09-24 VA-5 decided must not gate."""
    other = tmp_path / "overlays" / "some_other_mod"
    _write(other / "content.xml", '<content id="o" version="1"/>')
    report = _nested_fixture(tmp_path, "some_other_mod", overlays=(other,))
    refs = [f for f in report.findings if f.category == "ref"]
    assert refs, "the fixture produced no reference finding; it proves nothing"
    assert {f.severity for f in refs} == {"error"}, (
        "a cross-mod reference was demoted under Tier B, which CAN see the target")


def test_a_NON_nested_reference_is_untouched(tmp_path):
    """The twin. Only files under `extensions/<mod>/` are cross-mod; an ordinary
    path must keep gating exactly as the 2026-08-13 promotion established."""
    ref = tmp_path / "reference"
    _write(ref / "libraries/wares.xml", '<wares><ware id="ore"/></wares>')
    _write(ref / "index/macros.xml", "<index/>")
    mod = tmp_path / "mod"
    _write(mod / "content.xml", '<content id="m" version="1"/>')
    _write(mod / "libraries" / "wares.xml",
           '<wares><ware id="w_probe">'
           '<component ref="no_such_macro_at_all"/></ware></wares>')
    report = _check.Report()
    _check.check_references(mod, _merge.Config(reference=ref), report)
    refs = [f for f in report.findings if f.category == "ref"]
    assert refs and {f.severity for f in refs} == {"error"}, (
        "an ordinary dangling reference stopped gating: %s"
        % [(f.severity, f.message) for f in refs])


def test_the_docstring_no_longer_claims_the_promotion_is_PENDING():
    """The docstring is permanent record in the grammar of a fact, and it said the
    promotion had not happened for the module's whole history. A reader deciding
    whether to promote would have concluded the flood risk was still contained."""
    doc = _check.check_references.__doc__
    # Asserted POSITIVELY. A first draft asserted the ABSENCE of "reported as
    # INFO for now" -- which the corrected docstring legitimately contains, inside
    # the quotation explaining what it used to say. Prose satisfies substrings
    # (CLAUDE.md #37), and that cuts both ways.
    assert "Both scopes gate as errors" in doc, (
        "the docstring does not state the CURRENT severity of either scope")
    assert "PROMOTED" in doc and "2026-08-13" in doc, (
        "the docstring does not record that the promotion happened, or when")

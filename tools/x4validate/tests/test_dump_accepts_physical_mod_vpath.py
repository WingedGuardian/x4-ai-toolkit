"""`x4effective dump` must not answer a wrong-FORM query with a confident ABSENCE.

MEASURED 2026-08-27 (F71, root cause corrected): `dump --chain md/morerooms.xml`
returns rc 0 with `sources: moreroomsforships:full, ...`, while the same document
addressed as `extensions/moreroomsforships/md/morerooms.xml` returns rc 1 and
"no effective content". The information was always reachable; the query was in the
PHYSICAL form (a disk path under the extensions root) and `build_touch_map` keys by
the LOGICAL vpath -- the one the engine builds.

The asymmetry is real and NOT a bug in itself: for a DLC, `extensions/ego_dlc_split/...`
IS the game vpath, which is why `build_touch_map` deliberately does not rewrite those.
For a MOD the same shape is a disk path with no engine meaning. What IS a bug is
rendering that as an absence: 1,713 of 3,257 touched vpaths are mod-owned.

⚠ `logical_vpath` is a COMPOUND condition, so there is one falsification twin PER
CLAUSE below -- each trips exactly one guard and passes all the others. A single twin
only ever tests the first clause it hits (gotcha #26); two mutants survived that
mistake earlier in this same session.
"""
from pathlib import Path

import pytest

from x4validate import _effectivecli, _paths

MODS = {"moreroomsforships", "honshu solar cell generator"}
#: Asked of `Config.dlc_dirs()` in production -- never a name prefix.
DLC = {"ego_dlc_split", "ego_dlc_terran"}


def test_physical_mod_path_maps_to_logical_vpath():
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/md/morerooms.xml", MODS, DLC) == "md/morerooms.xml"


# --- one twin per clause; each passes every clause but the one it targets ------

def test_twin_not_under_extensions():
    """Clause 1: parts[0] == 'extensions'.

    ⚠ The obvious twin -- `md/morerooms.xml` -- is SHADOWED and proves nothing.
    MEASURED: disabling this clause left it green, because a 2-part path trips the
    LENGTH guard first and returns None for the wrong reason. To isolate this clause
    the input must satisfy every OTHER one: 3+ parts, an installed-mod folder in
    position 1, not a DLC, not double-nested -- and differ only in not living under
    `extensions/`. That is why the fixture below looks contrived; a natural-looking
    twin here is a twin that tests the guard in front of it.
    """
    assert _effectivecli.logical_vpath(
        "libraries/moreroomsforships/md/morerooms.xml", MODS, DLC) is None
    # and the plain already-logical case, which is what a user actually types
    assert _effectivecli.logical_vpath("md/morerooms.xml", MODS, DLC) is None


def test_twin_no_relative_remainder():
    """Clause 2: len(parts) >= 3. A bare `extensions/<mod>` names no document."""
    assert _effectivecli.logical_vpath("extensions/moreroomsforships", MODS, DLC) is None
    assert _effectivecli.logical_vpath("extensions/moreroomsforships/", MODS, DLC) is None


def test_twin_dlc_is_never_rewritten():
    """Clause 3: not a DLC. `extensions/ego_dlc_*/...` IS a genuine game vpath.

    This is the clause that makes the whole change safe: rewriting a DLC path would
    break the case that works today.
    """
    assert _effectivecli.logical_vpath(
        "extensions/ego_dlc_split/md/story_split.xml",
        MODS | {"ego_dlc_split"}, DLC) is None


def test_twin_unknown_folder_is_not_rewritten():
    """Clause 4: the folder is an INSTALLED mod. An unknown name may be a real vpath."""
    assert _effectivecli.logical_vpath(
        "extensions/not_installed_anywhere/md/x.xml", MODS, DLC) is None


def test_twin_double_nested_is_not_rewritten():
    """Clause 5: parts[2] != 'extensions', unless the inner folder is a known DLC.

    Mirrors `_effective.build_touch_map`, which keeps the same shape literal when the
    inner folder is not a DLC: a patch on another mod's patch is NOT engine-measured.
    Diverging here would make the CLI answer a question the merge model refuses.
    """
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/extensions/other/md/x.xml", MODS, DLC) is None


def test_double_nested_under_a_DLC_maps_to_the_DLC_vpath():
    """Clause 5's exception (F305): the mod ships a file at a DLC vpath and this
    patches its copy -- the store keys it under the DLC vpath, so the CLI does too.
    Twins: the DLC must be KNOWN (asked of dlc_dirs, not a prefix) and a rel must follow."""
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/extensions/ego_dlc_split/md/x.xml", MODS, DLC
    ) == "extensions/ego_dlc_split/md/x.xml"
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/extensions/ego_dlc_boron/md/x.xml", MODS, DLC) is None
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/extensions/ego_dlc_split", MODS, DLC) is None


# --- normalisation ------------------------------------------------------------

def test_folder_match_is_case_insensitive_and_separators_normalise():
    assert _effectivecli.logical_vpath(
        r"extensions\MoreRoomsForShips\md\morerooms.xml", MODS, DLC) == "md/morerooms.xml"


def test_folder_with_spaces_resolves():
    """Real folders have spaces -- an `awk`-style splitter would drop these (#22)."""
    assert _effectivecli.logical_vpath(
        "extensions/Honshu Solar Cell Generator/md/honshu_solar_generator.xml",
        MODS, DLC) == "md/honshu_solar_generator.xml"


def test_relative_case_is_preserved():
    """Only the FOLDER match is case-insensitive; the remainder is echoed as typed."""
    assert _effectivecli.logical_vpath(
        "extensions/moreroomsforships/md/MoreRooms.xml", MODS, DLC) == "md/MoreRooms.xml"


# --- end to end ---------------------------------------------------------------

from _layout import needs_reference_mark  # noqa: E402

# Skips only when no reference is configured, or in an INSTALLED toolkit before the unpack.
# A checkout whose configured reference is not unpacked RUNS, and fails (delta review).
needs_reference = needs_reference_mark()


@needs_reference
def test_dump_resolves_physical_mod_path_and_discloses_the_reinterpretation(capsys):
    """rc 0, the document, AND a note -- resolving silently would be the other bug."""
    rc = _effectivecli.main(
        ["dump", "--chain", "extensions/moreroomsforships/md/morerooms.xml"])
    out = capsys.readouterr().out
    assert rc == 0, "physical form must resolve, not report a confident absence"
    assert "md/morerooms.xml" in out, "the note must name the logical vpath it used"
    assert "moreroomsforships" in out
    assert "<mdscript" in out, "the actual document must still be printed"

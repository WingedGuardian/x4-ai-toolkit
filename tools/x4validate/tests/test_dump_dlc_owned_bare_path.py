"""`x4effective dump` must not answer a bare path to a DLC-only file with the UNPATCHED file.

MEASURED 2026-11-03: `dump --chain assets/props/.../turret_ter_l_beam_01_mk1_macro.xml` returned
rc 0, `sources: ego_dlc_terran:full`, and vanilla's bullet. The same document addressed as
`extensions/ego_dlc_terran/assets/...` returned `sources: base, vro:diff` and VRO's different
weapon. The bare form reads as a complete answer, so nothing announces that the patches were
never consulted. It led to two false conclusions in one audit.

The engine builds a DLC's own file under `extensions/<dlc>/...` (`logical_vpath` says so for the
mirror case), and a mod patches it at that path. `_merge.build_effective` keeps a DLC out of
`owner_folders` on purpose, so the bare door never looks there.

`dlc_owned_vpath` is a compound condition, so each clause has its own falsification twin below.
"""
import types

import pytest

from x4validate import _effectivecli as C

DLC = {"ego_dlc_x", "ego_dlc_y"}
REL = "assets/fx/macros/bullet_a_macro.xml"
DOOR = f"extensions/ego_dlc_x/{REL}"


def test_a_dlc_only_bare_path_names_the_dlc_document():
    assert C.dlc_owned_vpath(REL, ["ego_dlc_x:full"], DLC) == DOOR


def test_twin_already_under_extensions():
    assert C.dlc_owned_vpath(DOOR, ["ego_dlc_x:full"], DLC) is None


def test_twin_base_supplies_the_file():
    """A base file patched by a DLC is one ordinary document, addressed bare."""
    assert C.dlc_owned_vpath(REL, ["base", "ego_dlc_x:diff"], DLC) is None


def test_twin_a_mod_full_is_not_a_dlc_owner():
    assert C.dlc_owned_vpath(REL, ["some_mod:full"], DLC) is None


def test_twin_a_dlc_diff_is_not_ownership():
    assert C.dlc_owned_vpath(REL, ["ego_dlc_x:diff"], DLC) is None


def test_twin_two_dlc_owners_is_refused_not_guessed():
    assert C.dlc_owned_vpath(REL, ["ego_dlc_x:full", "ego_dlc_y:full"], DLC) is None


def test_dlc_folder_match_is_case_insensitive_and_keeps_the_source_spelling():
    assert C.dlc_owned_vpath(REL, ["EGO_DLC_X:full"], DLC) == f"extensions/EGO_DLC_X/{REL}"


def _macro(dmg):
    return (f'<macros><macro name="bullet_a_macro" class="bullet"><properties>'
            f'<damage value="{dmg}"/></properties></macro></macros>')


PATCH = ("<diff><replace sel=\"/macros/macro[@name='bullet_a_macro']/properties/damage/@value\">"
         "999</replace></diff>")


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    ref = tmp_path / "reference"
    dlc = ref / "extensions" / "ego_dlc_x"
    _w(dlc / REL, _macro("100"))
    (ref / "libraries").mkdir(parents=True)
    mod = tmp_path / "ext" / "patcher"
    mod.mkdir(parents=True)
    monkeypatch.setattr(C._merge.Config, "dlc_dirs", lambda self: [dlc])
    monkeypatch.setattr(C._merge.Config, "packed_dlc_names", lambda self: set())
    rows = [({"folder": "patcher", "path": str(mod)}, mod)]
    monkeypatch.setattr(C, "active_mods", lambda: [r[0] for r in rows])
    monkeypatch.setattr(C, "ordered_overlays", lambda mods: rows)
    monkeypatch.setattr(C._registry, "dropped_note", lambda mods: "")
    monkeypatch.setattr(C._registry, "model_note", lambda mods: "")
    return types.SimpleNamespace(ref=ref, mod=mod)


def _dump(world, vpath):
    return C._cmd_dump(types.SimpleNamespace(reference=str(world.ref), vpath=vpath, chain=True))


def test_bare_path_serves_the_patched_document_and_says_so(world, capsys):
    _w(world.mod / DOOR, PATCH)
    rc = _dump(world, REL)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'value="999"' in out, "the patched value must be what is printed"
    assert 'value="100"' not in out
    assert DOOR in out, "resolving a reinterpreted path silently would be the other bug"
    assert "patcher:diff" in out


def test_twin_no_patch_stays_literal_and_quiet(world, capsys):
    rc = _dump(world, REL)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'value="100"' in out
    assert "ego_dlc_x:full" in out
    assert "interpreted as" not in out, "nothing differs, so nothing is worth announcing"


def test_a_mod_shipping_the_bare_path_too_is_named_not_overridden(world, capsys):
    """Two documents: which one a macro uses is the index's call, so do not choose here."""
    _w(world.mod / DOOR, PATCH)
    _w(world.mod / REL, _macro("555"))
    rc = _dump(world, REL)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'value="555"' in out, "the literal answer is kept"
    assert DOOR in out, "the other document must be named"
    assert "ALSO" in out


def test_a_miss_names_the_document_a_mod_shipped_into_a_dlc_tree(world, capsys):
    """MEASURED: 113 of 716 patched DLC documents had this shape; the bare path read as absent."""
    only = "assets/fx/macros/bullet_new_macro.xml"
    door = f"extensions/ego_dlc_x/{only}"
    _w(world.mod / door, _macro("7"))
    rc = _dump(world, only)
    got = capsys.readouterr()
    assert rc == 1, "the bare path really has no document, so the exit code stays honest"
    assert door in got.err


def test_twin_a_real_absence_names_nothing(world, capsys):
    rc = _dump(world, "assets/fx/macros/bullet_nowhere_macro.xml")
    got = capsys.readouterr()
    assert rc == 1
    assert "does exist" not in got.err

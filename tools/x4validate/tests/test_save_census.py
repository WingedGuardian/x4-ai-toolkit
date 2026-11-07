"""x4save census: count a save's <component> elements, grouped, without a hand-written script.

Why it exists: questions like "Xenon ships by macro" or "Xenon turrets on stations vs ships"
used to mean a gzip+expat script against the save PATH, which the save guard refuses (#19).
`x4save census <save name> ...` is the supported route.

The fixture mirrors the MEASURED save layout: components nest through
<connections><connection><component>, an owner is usually only on the object (a turret
inherits it), and a ship docked at a station sits under the station's dock module.

The production change each test would catch is named in its docstring.
"""
from __future__ import annotations

import gzip
import json

import pytest

from x4validate import _savecensus, _savecli


def _c(cls, macro, inner="", owner=None):
    own = f' owner="{owner}"' if owner else ""
    cls_attr = f' class="{cls}"' if cls is not None else ""
    mac = f' macro="{macro}"' if macro is not None else ""
    body = f"<connections>{inner}</connections>" if inner else ""
    return f"<connection><component{cls_attr}{mac}{own}><offset/>{body}</component></connection>"


_T_L = "turret_xen_l_laser_01_mk1_macro"
_T_M = "turret_xen_m_laser_02_mk1_macro"

_STATION_A = _c("station", "station_xen_defence_base_01_macro", owner="xenon", inner=(
    _c("defencemodule", "defence_xen_disc_xup_01_macro", inner=_c("turret", _T_L) + _c("turret", _T_M))
    + _c("turret", _T_L)                                            # directly on the station
    + _c("dockarea", "dockarea_xen_01_macro", inner=_c("ship_s", "ship_xen_s_fighter_01_a_macro",
                                                       owner="xenon", inner=_c("turret", _T_L)))))
_STATION_B = _c("station", "station_xen_shipyard_base_01_macro", owner="xenon", inner=(
    _c("production", "xenon_small_station_01_base_macro", inner=_c("turret", _T_L))))
_SHIPS = (
    _c("ship_l", "ship_xen_l_destroyer_01_macro", owner="xenon",
       inner=_c("turret", _T_L) + _c("turret", _T_L) + _c("turret", _T_M, owner="player"))
    + _c("ship_m", "ship_arg_m_corvette_01_a_macro", owner="argon", inner=_c("turret", _T_M))
    + _c(None, "weird_no_class_macro", owner="xenon")                # malformed: no class=
    + _c("ship_s", None, owner="xenon"))                             # malformed: no macro=

_DOC = ('<?xml version="1.0" encoding="UTF-8"?><savegame><info><save name="#T"/></info><universe>'
        '<component class="galaxy" macro="xu_ep2_universe_macro"><connections>'
        + _c("sector", "sector_01_macro", owner="ownerless", inner=_c("zone", "zone_01_macro",
                                                                     inner=_STATION_A + _STATION_B + _SHIPS))
        + "</connections></component></universe><md/></savegame>").encode()

#: galaxy, sector, zone, 2 stations, 3 modules, 9 turrets... counted by hand from the fixture:
#: galaxy 1 + sector 1 + zone 1 + station 2 + defencemodule 1 + dockarea 1 + production 1
#: + ships 5 (ship_s docked, ship_l, ship_m, no-macro ship_s) -> 4 ship_* + 1 no-class + turrets 9
_TOTAL = 1 + 1 + 1 + 2 + 1 + 1 + 1 + 4 + 1 + 9


def _save(tmp_path, data: bytes = _DOC, name: str = "save_001.xml.gz"):
    p = tmp_path / name
    p.write_bytes(gzip.compress(data))
    return p


def _census(tmp_path, data=_DOC, **kw):
    return _savecensus.census(_save(tmp_path, data), **kw)


def _groups(result):
    return dict(result.groups)


def test_fixture_denominator_is_every_component(tmp_path):
    """Fails if any <component> escapes the count (the denominator every zero is stated against)."""
    r = _census(tmp_path)
    assert r.seen == _TOTAL == r.matched
    assert _TOTAL == _DOC.count(b"<component")


def test_Q1_xenon_ships_by_macro(tmp_path):
    """Fails if --class/--owner globs do not filter, or a ship with no macro= vanishes."""
    r = _census(tmp_path, classes=["ship_*"], owners=["xenon"], by=["macro"])
    assert _groups(r) == {("ship_xen_s_fighter_01_a_macro",): 1, ("ship_xen_l_destroyer_01_macro",): 1,
                          ("(no macro)",): 1}
    assert r.matched == 3 and r.no_macro == 1


def test_Q2_xenon_turrets_split_station_vs_ship(tmp_path):
    """Fails if the default parent is not the nearest station/ship_* ancestor (a turret under a
    module under a station must read as station; a turret on a docked ship as that ship)."""
    r = _census(tmp_path, classes=["turret"], owners=["xenon"], by=["parent-class", "macro"])
    assert _groups(r) == {("station", _T_L): 3, ("station", _T_M): 1,
                          ("ship_s", _T_L): 1, ("ship_l", _T_L): 2}


def test_Q3_l_turrets_by_station_module(tmp_path):
    """Fails if @module does not expand, or the parent search crosses a ship docked at a station
    (its turret would be booked to the station's dock module)."""
    r = _census(tmp_path, classes=["turret"], owners=["xenon"], macros=["turret_xen_l_*"],
                parent_classes=["@module"], by=["macro", "parent-macro", "station-macro"])
    assert _groups(r) == {
        (_T_L, "defence_xen_disc_xup_01_macro", "station_xen_defence_base_01_macro"): 1,
        (_T_L, "xenon_small_station_01_base_macro", "station_xen_shipyard_base_01_macro"): 1,
        (_T_L, "(none)", "station_xen_defence_base_01_macro"): 1,     # directly on the station
        (_T_L, "(none)", "(none)"): 3,                                  # docked ship_s 1 + ship_l 2
    }


def test_parent_anywhere_crosses_the_ship_boundary(tmp_path):
    """Twin of Q3: fails if --parent-anywhere is ignored (the docked ship's turret must now find
    the dock module and the station)."""
    r = _census(tmp_path, classes=["turret"], owners=["xenon"], macros=["turret_xen_l_*"],
                parent_classes=["@module"], parent_anywhere=True,
                by=["parent-macro", "station-macro"])
    assert _groups(r)[("dockarea_xen_01_macro", "station_xen_defence_base_01_macro")] == 1


def test_sibling_station_does_not_leak(tmp_path):
    """Fails if a closed component is not popped: station B's module turret would inherit A."""
    r = _census(tmp_path, macros=[_T_L], parent_classes=["station"], by=["parent-macro"])
    assert _groups(r)[("station_xen_shipyard_base_01_macro",)] == 1


def test_owner_is_inherited_unless_the_component_has_its_own(tmp_path):
    """Fails if a turret's own owner= is overridden by its ship, or an absent owner is not inherited."""
    r = _census(tmp_path, classes=["turret"], by=["owner"])
    assert _groups(r) == {("xenon",): 7, ("player",): 1, ("argon",): 1}
    assert r.owner_inherited == 8


def test_globs_are_case_insensitive(tmp_path):
    """Fails if --class/--owner compare case-sensitively (the corpus mixes case)."""
    r = _census(tmp_path, classes=["SHIP_*"], owners=["XenOn"], by=["class"])
    assert _groups(r) == {("ship_s",): 2, ("ship_l",): 1}


def test_class_and_owner_lists_are_alternatives(tmp_path):
    """Fails if a comma list is treated as one literal pattern."""
    r = _census(tmp_path, classes=["station", "ship_l"], by=["class"])
    assert _groups(r) == {("station",): 2, ("ship_l",): 1}


def test_component_without_class_is_counted_and_never_class_matched(tmp_path):
    """Fails if a malformed component (no class=) is dropped from the denominator, or matched."""
    r = _census(tmp_path, classes=["*"], by=["class"])
    assert r.no_class == 1 and r.seen == _TOTAL and r.matched == _TOTAL - 1
    assert ("(no class)",) not in _groups(r)
    everything = _census(tmp_path, by=["class"])
    assert _groups(everything)[("(no class)",)] == 1


@pytest.mark.parametrize("chunk", [1, 7, 997, 1 << 22])
def test_chunk_boundaries_do_not_change_the_answer(tmp_path, monkeypatch, chunk):
    monkeypatch.setattr(_savecli, "_CHUNK", chunk)
    r = _census(tmp_path, classes=["turret"], by=["parent-class"])
    assert _groups(r) == {("station",): 4, ("ship_s",): 1, ("ship_l",): 3, ("ship_m",): 1}


@pytest.mark.parametrize("body", [_DOC[:-40], b'<savegame><component class="x" macro="\xff"/></savegame>'])
def test_truncated_or_malformed_save_is_unreadable(tmp_path, body):
    with pytest.raises(_savecli.SaveUnreadable):
        _census(tmp_path, body)


# --------------------------------------------------------------------------- CLI


def test_cli_text_prints_groups_and_denominator_rc0(tmp_path, capsys):
    rc = _savecli.main(["census", str(_save(tmp_path)), "--class", "ship_*", "--owner", "xenon"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ship_xen_l_destroyer_01_macro" in out
    assert f"3 of {_TOTAL:,} components matched" in out
    assert "1 component(s) had no class=" in out


def test_cli_no_class_footer_says_what_happened_to_them(tmp_path, capsys):
    """4.1.1 review B5: the footer said "grouped as '(no class)'" even when --class had filtered every one of them
    out. With --class they are never in the groups; without it they are grouped like any other component."""
    _savecli.main(["census", str(_save(tmp_path)), "--class", "ship_*", "--by", "class"])
    out = capsys.readouterr().out
    assert "'(no class)'" not in out, out
    assert "1 component(s) had no class= (never matched by --class, so none is in the groups above)" in out, out
    _savecli.main(["census", str(_save(tmp_path)), "--by", "class"])
    out = capsys.readouterr().out
    assert "1 component(s) had no class= (in the groups above as '(no class)' wherever they matched)" in out, out


def test_cli_zero_match_states_how_many_were_scanned_rc1(tmp_path, capsys):
    """Fails if a zero reads like success, or hides the denominator."""
    rc = _savecli.main(["census", str(_save(tmp_path)), "--owner", "nobody"])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"NO MATCH: 0 of {_TOTAL:,} components" in out


def test_cli_json_schema(tmp_path, capsys):
    rc = _savecli.main(["census", str(_save(tmp_path)), "--class", "turret", "--owner", "xenon",
                        "--by", "parent-class,macro", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(doc) == {"schema", "save", "filters", "by", "parent_class", "parent_anywhere",
                        "components_seen", "matched", "no_class", "no_macro", "owner_inherited",
                        "scanned_bytes", "groups"}
    assert doc["schema"] == "x4save.census/1"
    assert doc["by"] == ["parent-class", "macro"]
    assert doc["components_seen"] == _TOTAL and doc["matched"] == 7
    assert doc["scanned_bytes"] == len(_DOC)
    assert doc["groups"][0] == {"key": {"parent-class": "station", "macro": _T_L}, "count": 3}
    assert sum(g["count"] for g in doc["groups"]) == doc["matched"]


def test_cli_json_zero_match_is_rc1_with_denominator(tmp_path, capsys):
    rc = _savecli.main(["census", str(_save(tmp_path)), "--owner", "nobody", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["matched"] == 0 and doc["components_seen"] == _TOTAL and doc["groups"] == []


def test_cli_limit_discloses_truncation(tmp_path, capsys):
    rc = _savecli.main(["census", str(_save(tmp_path)), "--by", "macro", "--limit", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and "TRUNCATED by --limit" in out


@pytest.mark.parametrize("argv", [["--by", "macro,colour"], ["--by", ""], ["--parent-class", "@nosuch"],
                                  ["--limit", "-1"]])
def test_cli_bad_arguments_refuse_rc2(tmp_path, capsys, argv):
    rc = _savecli.main(["census", str(_save(tmp_path))] + argv)
    assert rc == 2
    assert "error" in capsys.readouterr().err


def test_cli_unreadable_save_rc2(tmp_path, capsys):
    rc = _savecli.main(["census", str(_save(tmp_path, _DOC[:-40]))])
    assert rc == 2 and "error:" in capsys.readouterr().err


def test_cli_resolves_a_save_name_from_the_save_folder(tmp_path, monkeypatch, capsys):
    """Fails if `census` does not resolve a save NAME the way info/check/vars do."""
    _save(tmp_path, name="save_042.xml.gz")
    monkeypatch.setenv("X4_SAVES", str(tmp_path))
    rc = _savecli.main(["census", "save_042", "--class", "station"])
    out = capsys.readouterr().out
    assert rc == 0 and "save_042.xml.gz" in out and f"2 of {_TOTAL:,} components matched" in out


def test_cli_a_path_is_accepted_like_the_other_subcommands(tmp_path, capsys):
    """Consistency: info/check/vars all accept a path through resolve_save; census must too."""
    p = _save(tmp_path)
    assert _savecli.main(["census", str(p), "--class", "station"]) == 0
    assert _savecli.main(["vars", "nomatch_xyz", str(p)]) == 1       # the same resolver, same file
    capsys.readouterr()


def test_cli_unknown_save_name_rc2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("X4_SAVES", str(tmp_path))
    assert _savecli.main(["census", "save_999"]) == 2
    assert "no save found" in capsys.readouterr().err


def test_help_carries_the_three_example_questions(capsys):
    with pytest.raises(SystemExit):
        _savecli.main(["census", "--help"])
    out = capsys.readouterr().out
    assert "--class 'ship_*' --owner xenon --by macro" in out
    assert "--class turret --owner xenon --by parent-class,macro" in out
    assert "--parent-class @module --by macro,parent-macro,station-macro" in out


def test_streaming_keeps_only_groups(tmp_path):
    """Fails if the scan keeps per-component rows: memory must scale with distinct groups."""
    r = _census(tmp_path, by=["class"])
    assert not hasattr(r, "rows")
    assert len(r.groups) == len({k for k in _groups(r)})

"""Tests for _xref.py — MD/aiscript action·event·cue cross-index."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from x4validate import _xref


def _rows(xml, source="modx", vpath="md/x.xml"):
    root = etree.fromstring(xml)
    out = []
    _xref._walk(root, source, vpath, out)
    return out


ATD_LIKE = b"""
<mdscript name="X">
  <cues>
    <cue name="Init">
      <actions>
        <set_emergency_eject_active active="false"/>
      </actions>
    </cue>
    <cue name="OnPlayerShipHit">
      <conditions>
        <event_object_hull_damaged object="$ship"/>
      </conditions>
      <actions>
        <do_if value="1">
          <set_object_min_hull object="$ship" exact="1"/>
        </do_if>
        <signal_cue cue="PlayerDeath"/>
      </actions>
    </cue>
    <cue name="PlayerDeath">
      <actions><destroy_object object="$ship"/></actions>
    </cue>
  </cues>
</mdscript>
"""


def test_indexes_actions_events_signals_cuedefs():
    rows = _rows(ATD_LIKE)
    kinds = {(r.kind, r.name) for r in rows}
    assert ("action", "set_emergency_eject_active") in kinds
    assert ("action", "set_object_min_hull") in kinds
    assert ("action", "destroy_object") in kinds
    assert ("event", "event_object_hull_damaged") in kinds
    assert ("signal", "signal_cue") in kinds
    assert ("cuedef", "Init") in kinds
    assert ("cuedef", "PlayerDeath") in kinds


def test_skips_control_flow_and_containers():
    rows = _rows(ATD_LIKE)
    tags = {r.name for r in rows if r.kind == "action"}
    for noise in ("do_if", "actions", "conditions", "cues"):
        assert noise not in tags


def test_enclosing_cue_is_recorded():
    rows = _rows(ATD_LIKE)
    eject = next(r for r in rows if r.name == "set_emergency_eject_active")
    assert eject.cue == "Init"
    hull = next(r for r in rows if r.name == "set_object_min_hull")
    assert hull.cue == "OnPlayerShipHit"


def test_event_records_object_target():
    rows = _rows(ATD_LIKE)
    ev = next(r for r in rows if r.name == "event_object_hull_damaged")
    assert ev.target == "$ship"


def test_signal_records_cue_target():
    rows = _rows(ATD_LIKE)
    sig = next(r for r in rows if r.kind == "signal")
    assert sig.target == "PlayerDeath"


def test_query_and_cue_edges():
    rows = _rows(ATD_LIKE)
    assert len(_xref.query(rows, "action", "set_object_min_hull")) == 1
    # case-insensitive
    assert len(_xref.query(rows, "action", "SET_OBJECT_MIN_HULL")) == 1
    edges = _xref.cue_edges(rows, "PlayerDeath")
    assert "defined" in edges  # PlayerDeath cue is defined here
    assert "signal_cue" in edges  # and signalled from OnPlayerShipHit


def test_cue_edges_matches_qualified_ref():
    xml = b"""
    <mdscript name="Y">
      <cues>
        <cue name="Target"><actions/></cue>
        <cue name="Caller">
          <actions><signal_cue cue="md.OtherScript.Target"/></actions>
        </cue>
      </cues>
    </mdscript>
    """
    rows = _rows(xml)
    edges = _xref.cue_edges(rows, "Target")  # short name matches md.OtherScript.Target
    assert "signal_cue" in edges


def test_tsv_round_trip(tmp_path):
    rows = _rows(ATD_LIKE)
    p = tmp_path / "xref.tsv"
    _xref.write_tsv(rows, p)
    back = _xref.read_tsv(p)
    assert len(back) == len(rows)
    assert {(r.kind, r.name) for r in back} == {(r.kind, r.name) for r in rows}


def test_a_write_killed_mid_way_leaves_the_previous_index_intact(tmp_path, monkeypatch):
    """A build that dies while writing used to leave a TRUNCATED index in place of a valid one (the
    file was opened 'w' over the old one). Now the old bytes survive and no temp is left behind."""
    p = tmp_path / "xref.tsv"
    _xref.write_tsv(_rows(ATD_LIKE), p)
    before = p.read_bytes()

    def dies(src, dst):
        assert Path(src).read_bytes(), "the temp must be fully written before the swap"
        raise KeyboardInterrupt("killed at the swap")

    monkeypatch.setattr(_xref.os, "replace", dies)
    with pytest.raises(KeyboardInterrupt):
        _xref.write_tsv(_rows(ATD_LIKE)[:1], p)
    assert p.read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["xref.tsv"]


def test_a_freshness_stamp_is_replaced_never_left_half_written(tmp_path, monkeypatch):
    from x4validate import _freshness
    target = tmp_path / "xref.tsv"
    _freshness.stamp_sidecar(target, {"content": "a", "engine": "b"})
    before = (tmp_path / "xref.tsv.freshness.json").read_bytes()
    monkeypatch.setattr(_freshness.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        _freshness.stamp_sidecar(target, {"content": "c", "engine": "d"})
    assert (tmp_path / "xref.tsv.freshness.json").read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["xref.tsv.freshness.json"]


def test_aiscript_uses_script_name_as_context():
    xml = b"""
    <aiscript name="order.fight">
      <actions><create_order object="$x"/></actions>
    </aiscript>
    """
    rows = _rows(xml, vpath="aiscripts/order.fight.xml")
    order = next(r for r in rows if r.name == "create_order")
    assert order.cue == "order.fight"


# --- the hint path was case-SENSITIVE while the query was not ----------------
#
# `query` matches `r.name.lower() == name.lower()`, teaching the user that case
# does not matter for X4 identifiers -- which is correct, and CLAUDE.md #9 says so
# explicitly, because the corpus genuinely mixes case (`Cluster_104` vs
# `cluster_104`). `_hint_other_kinds` then compared `r.name == name`, so a
# differently-cased argument fell into the "does not appear under ANY kind"
# branch and was printed as "a real negative", with a denominator attached to
# lend it weight.
#
# That inverts the function's whole purpose. Its docstring: "A name that exists
# under another kind is a wrong-command mistake; a name that exists nowhere is a
# real negative. They must never read the same." They read the same, in the
# direction that manufactures a false fact.
#
# REPRODUCED through the shipped CLI over a fully-covered index:
#   who-calls Event_Player_Ejected -> "does not appear under ANY kind - a real negative"
#   who-calls event_player_ejected -> "BUT ... IS in the index under other kind(s)"
#   who-listens EVENT_PLAYER_EJECTED -> 1 occurrence(s)
# The single differing variable is the case of the argument.

def _row(kind, name):
    return _xref.XrefRow(kind=kind, name=name, source="base",
                         file="md/x.xml", cue="C", line=1)


def test_the_hint_finds_a_name_indexed_under_DIFFERENT_case(capsys):
    """The defect: an exact-case miss was upgraded into an asserted negative."""
    rows = [_row("event", "Event_Player_Ejected")]
    _xref._hint_other_kinds(rows, "event_player_ejected", "action")
    out = capsys.readouterr().out
    assert "does not appear under ANY kind" not in out, (
        "a name that IS indexed was reported as a real negative because its case "
        "differed from the argument")
    assert "IS in the index under other kind(s)" in out
    assert "event" in out


def test_the_hint_is_case_insensitive_in_BOTH_directions(capsys):
    """The argument may be the upper one and the index the lower one, or the
    reverse -- folding only one side would pass the test above and still be wrong."""
    rows = [_row("event", "event_player_ejected")]
    _xref._hint_other_kinds(rows, "EVENT_PLAYER_EJECTED", "action")
    out = capsys.readouterr().out
    assert "IS in the index under other kind(s)" in out, out


def test_a_name_that_really_is_absent_is_STILL_a_real_negative(capsys):
    """The falsification twin. A hint that claimed a match for everything would
    pass both tests above while deleting the distinction the function exists for."""
    rows = [_row("event", "something_else")]
    _xref._hint_other_kinds(rows, "definitely_not_a_real_thing", "action")
    out = capsys.readouterr().out
    assert "does not appear under ANY kind" in out, out
    assert "IS in the index" not in out


def test_the_asked_kind_is_still_excluded_from_the_hint(capsys):
    """The other clause of the same condition, tested separately so a guard in
    front of it cannot shadow it: a row of the kind the user ALREADY asked for is
    not an 'other kind', and must not be offered as one."""
    rows = [_row("action", "Set_Object_Min_Hull")]
    _xref._hint_other_kinds(rows, "set_object_min_hull", "action")
    out = capsys.readouterr().out
    assert "does not appear under ANY kind" in out, out

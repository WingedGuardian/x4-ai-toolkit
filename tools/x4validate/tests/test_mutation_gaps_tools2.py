"""Mutation-campaign gaps in the analysis CLIs (campaign 1, re-verified 2026-09-26):
x4save check, x4debug bucketing/comparison, the debug-log parser, x4xref, x4stats and
x4effective. Each test names the surviving mutant it was written to kill and was verified
to FAIL with it applied and PASS without it. Only DEPENDENCIES are stubbed (the active mod
set, the definition set); each tool's own decision logic runs for real.
"""
from __future__ import annotations

import gzip
import io
import os
import types
from pathlib import Path

from lxml import etree

from x4validate import (_debugcli, _debuglog, _effective, _effectivecli, _merge,
                        _savecli, _stats, _xref)


# --------------------------------------------------------------------------- x4save
def _save(path: Path, macros: list[str]) -> Path:
    body = "".join(f'<component macro="{m}"/>\n' for m in macros)
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n<savegame>\n<info>\n'
           '<save name="#T" date="1"/>\n<game id="X4" version="900" build="1" time="1"/>\n'
           "<patches>\n<history>\n</history>\n</patches>\n</info>\n<universe>\n"
           + body + "</universe>\n</savegame>\n")
    with gzip.open(path, "wb") as fh:
        fh.write(doc.encode("utf-8"))
    return path


def _defs(monkeypatch, names):
    monkeypatch.setattr(_savecli._registry, "mods", lambda scope, *a, **k: [])
    monkeypatch.setattr(_savecli._effective, "ordered_overlays", lambda mods: [])
    monkeypatch.setattr(_savecli._merge, "Config",
                        lambda **k: types.SimpleNamespace(overlays=[], dlc_dirs=lambda: []))
    monkeypatch.setattr(_savecli._effective, "require_base_documents", lambda *a: (1, 0))
    monkeypatch.setattr(_savecli._check, "EntityDefs",
                        lambda cfg, **k: types.SimpleNamespace(all_names=lambda: set(names)))


def test_save_check_folds_case_on_the_REFERENCE_side_too(tmp_path, monkeypatch):
    """SV3: the comparison is case-insensitive on BOTH sides; a mixed-case reference
    to a defined name is resolved, and the run is clean (rc 0)."""
    _defs(monkeypatch, {"ship_a_macro"})
    rc = _savecli.cmd_check(_save(tmp_path / "s.xml.gz", ["Ship_A_Macro"]), out=io.StringIO())
    assert rc == 0


def test_save_check_exits_1_on_an_unresolved_reference(tmp_path, monkeypatch):
    """SV4: an unresolved macro reference is rc 1, never 0."""
    _defs(monkeypatch, {"ship_a_macro"})
    out = io.StringIO()
    rc = _savecli.cmd_check(_save(tmp_path / "s.xml.gz", ["gone_macro"]), out=out)
    assert rc == 1 and "gone_macro" in out.getvalue()


def test_save_check_with_no_references_is_degraded_not_clean(tmp_path, monkeypatch):
    """SV5: zero macro references is a NON-ANSWER -- rc 3, never the clean rc 0."""
    _defs(monkeypatch, {"ship_a_macro"})
    assert _savecli.cmd_check(_save(tmp_path / "s.xml.gz", []), out=io.StringIO()) == 3


def test_resolve_save_defaults_to_the_NEWEST_save(tmp_path, monkeypatch):
    """SV6: with no argument, the newest save by mtime."""
    old, new = _save(tmp_path / "old.xml.gz", []), _save(tmp_path / "new.xml.gz", [])
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    monkeypatch.setattr(_savecli._paths, "savegames", lambda: tmp_path)
    assert _savecli.resolve_save(None) == new


# --------------------------------------------------------------------------- x4debug
def _err(folder="", script="", cardinality="", vpath="libraries/wares.xml", sel="/x"):
    return _debuglog.DebugError("path" if folder else "script", folder, vpath, script, 0,
                                "m", "error", sel=sel, cardinality=cardinality)


def test_observed_ops_are_this_folders_CARDINALITY_verdicts_only():
    """DC3 (entries without a cardinality verdict counted) and DC4 (other folders'
    entries counted)."""
    parsed = types.SimpleNamespace(entries=[
        _err(folder="ModA", cardinality="none", sel="/a"),
        _err(folder="moda", cardinality="", sel="/b"),        # not a diff-op verdict
        _err(folder="other", cardinality="multiple", sel="/c"),
    ])
    assert _debugcli.observed_ops(parsed, "modA") == [
        _debugcli.op_key("libraries/wares.xml", "/a")]


def test_an_observed_only_op_is_not_clean():
    """DC6: an op the ENGINE rejected that the validator did not predict is a
    disagreement -- not clean."""
    assert not _debugcli.compare_ops(observed=["x"], predicted=[]).clean
    assert _debugcli.compare_ops(observed=["x"], predicted=["x"]).clean


def test_a_script_error_is_bucketed_as_a_script_not_a_mod():
    """DC7."""
    assert _debugcli._bucket(_err(script="md.foo")) == ("script", "md.foo")


def test_advisory_engine_wording_is_a_warning():
    """D1/D2: 'not recommended' and 'inefficient' lines are advisories, not errors."""
    assert _debuglog._sev("Using X here is not recommended") == "warn"
    assert _debuglog._sev("This lookup is inefficient") == "warn"
    assert _debuglog._sev("Could not find X") == "error"


def test_the_game_time_stamp_is_not_part_of_the_message():
    """D7: '[=ERROR=] 84.60 <message>' -- the number is the game clock, not message."""
    parsed = _debuglog.parse_log_text("[=ERROR=] 84.60 something nobody has a shape for\n")
    assert [e.message for e in parsed.entries] == ["something nobody has a shape for"]


# --------------------------------------------------------------------------- x4xref
def _rows(xml: str):
    out: list = []
    _xref._walk(etree.fromstring(xml), "base", "md/s.xml", out)
    return out


def test_a_nameless_cue_is_not_a_cue_definition():
    """X1."""
    rows = _rows('<mdscript name="s"><cues><cue><actions/></cue>'
                 '<cue name="Real"><actions/></cue></cues></mdscript>')
    assert [r.name for r in rows if r.kind == "cuedef"] == ["Real"]


def test_an_event_target_given_by_BY_is_recorded():
    """X2."""
    rows = _rows('<mdscript name="s"><cues><cue name="C"><conditions>'
                 '<event_object_destroyed by="player.ship"/></conditions></cue></cues>'
                 '</mdscript>')
    assert [r.target for r in rows if r.kind == "event"] == ["player.ship"]


def test_cue_edges_match_a_qualified_reference_by_its_short_name():
    """X5: 'md.Script.Cue' finds the definition of 'Cue'."""
    rows = _rows('<mdscript name="Script"><cues><cue name="Cue"/></cues></mdscript>')
    assert len(_xref.cue_edges(rows, "md.Script.Cue")["defined"]) == 1


def test_a_mod_present_in_two_roots_is_indexed_once(tmp_path, monkeypatch):
    """X6: one folder in two install roots is ONE mod to the engine."""
    roots = [tmp_path / "r1", tmp_path / "r2"]
    for r in roots:
        (r / "m" / "md").mkdir(parents=True)
        (r / "m" / "content.xml").write_text('<content id="m" version="1"/>', encoding="utf-8")
        (r / "m" / "md" / "x.xml").write_text('<mdscript name="x"><cues><cue name="C"/>'
                                               '</cues></mdscript>', encoding="utf-8")
    (tmp_path / "ref").mkdir()
    monkeypatch.setattr(_xref, "index_roots", lambda ext: list(roots))
    rows = _xref.build_index(tmp_path / "ref", roots[0], [], dlc_dirs=[])
    assert [(r.source, r.name) for r in rows if r.kind == "cuedef"] == [("m", "C")]


# --------------------------------------------------------------------------- x4stats
def _ware(wid, group, avg):
    return _stats.Ware(wid, group, "container", 1.0, avg, avg, avg)


def test_peer_set_excludes_unpriced_wares():
    """S2: a same-group ware with no price is not a price peer."""
    cmp = _stats.compare_wares({"c": _ware("c", "g", 100.0)},
                               {"p1": _ware("p1", "g", 50.0), "p0": _ware("p0", "g", 0.0)})
    assert cmp[0].peer_count == 1


def test_percentile_counts_only_STRICTLY_cheaper_peers():
    """S4: a peer at the same price is not below the candidate."""
    peers = {f"p{i}": _ware(f"p{i}", "g", v) for i, v in enumerate((50.0, 100.0, 150.0))}
    cmp = _stats.compare_wares({"c": _ware("c", "g", 100.0)}, peers)
    assert round(cmp[0].percentile, 1) == 33.3


def test_a_price_equal_to_the_cheapest_peer_is_not_CHEAPER_than_every_peer():
    """S6."""
    peers = {"p0": _ware("p0", "g", 50.0), "p1": _ware("p1", "g", 100.0)}
    cmp = _stats.compare_wares({"c": _ware("c", "g", 50.0)}, peers)
    assert "CHEAPER" not in cmp[0].note


def test_a_FAILED_op_does_not_count_its_target_as_touched(tmp_path):
    """S8: an op that could not apply changed nothing; its ware is not a candidate
    ware (it would otherwise be reported at its untouched base price)."""
    mod = tmp_path / "m"
    (mod / "libraries").mkdir(parents=True)
    (mod / "libraries" / "wares.xml").write_text(
        "<diff><replace sel=\"//ware[@id='ore']/price/@average\"><x/></replace></diff>",
        encoding="utf-8")
    base = etree.fromstring('<wares><ware id="ore" group="g"><price min="1" average="2" '
                            'max="3"/></ware></wares>')
    assert _stats.candidate_wares(mod, base) == {}


# --------------------------------------------------------------------------- x4effective
def _store(tmp_path, monkeypatch):
    import test_effective as te
    ref, exts = te._world(tmp_path)
    # aaa_thrust owns a SECOND value too (not just thrust.forward), so a --limit
    # smaller than its total can actually truncate it (finding 4's twin: --limit 0
    # now means unlimited, so truncation must be tested with a real positive limit).
    (exts / "aaa_thrust" / "assets" / "props" / "engines" / "macros"
     / "engine_arg_s_01_macro.xml").write_bytes(
        b'<diff><replace sel="//macro[@name=\'engine_arg_s_01_macro\']/properties/thrust/@forward">'
        b'250</replace><replace sel="//macro[@name=\'engine_arg_s_01_macro\']/properties/thrust/@reverse">'
        b'77</replace></diff>')
    z = exts / "ccc_z" / "assets" / "props" / "engines" / "macros"
    z.mkdir(parents=True)
    (z / "zzz_last_macro.xml").write_bytes(
        b'<macros><macro name="zzz_last_macro" class="engine">'
        b'<properties><thrust forward="5"/></properties></macro></macros>')
    (exts / "ccc_z" / "content.xml").write_bytes(b'<content id="ccc_z" name="Z" version="1"/>')
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, dirs=[exts], kinds=("ware", "macro"))
    return db


def test_attr_sort_num_orders_by_value(tmp_path, monkeypatch, capsys):
    """EC3: --sort num orders by the NUMBER; the default orders by name."""
    db = _store(tmp_path, monkeypatch)
    capsys.readouterr()
    assert _effectivecli.main(["--db", str(db), "attr", "macro", "thrust.forward",
                               "--class", "engine", "--sort", "num"]) == 0
    names = [ln.split()[0] for ln in capsys.readouterr().out.splitlines()
             if ln.endswith("_macro") or "_macro " in ln]
    assert names[:3] == ["zzz_last_macro", "engine_arg_s_01_macro", "engine_new_01_macro"]


def test_diff_mod_count_line_states_the_TOTAL_not_the_shown_rows(tmp_path, monkeypatch, capsys):
    """EC4: under --limit the headline must still carry the real total. (--limit 0
    is exercised separately in test_effective.py: finding 4 made it mean unlimited,
    not zero rows, so a real truncation here needs a positive limit under the
    total.)"""
    db = _store(tmp_path, monkeypatch)
    capsys.readouterr()
    assert _effectivecli.main(["--db", str(db), "diff-mod", "aaa_thrust", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert _effective._count_line(1, 2, "value(s) won by aaa_thrust") in out


def test_diff_mod_refuses_a_name_that_is_no_stored_origin(tmp_path, monkeypatch, capsys):
    """EC5: '0 values won' by a name that cannot exist is a statement about the NAME."""
    db = _store(tmp_path, monkeypatch)
    assert _effectivecli.main(["--db", str(db), "diff-mod", "zzznotamodatall"]) == 2

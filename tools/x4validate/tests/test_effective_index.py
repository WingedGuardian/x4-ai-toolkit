"""F383/F384 open parts: ONE index resolver (`_merge.EffectiveIndex`) and the opt-in
`build_effective(..., index_roots=...)` that loads the document the effective index names.

MEASURED (BLIND-SPOTS F383, one log, 10 of 10 `assets/units/` complete copies): when the LAST
effective index entry for a file's name points at `extensions/<copier>/<rel>`, the engine loads
the copier's file plus its nested patches and opens NO plain-path file (5 files); when it points
at the base path, the copy merges at the plain path and no nested patch of it is opened (5).
That the LAST of several entries decides is INFERRED from 3 of those 10.

Every behavioural clause has a twin; `test_every_reader_agrees_on_the_loaded_document` is the
agreement test the hand mutant (last -> first entry) must turn red.
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _check, _merge, _resolve
from x4validate._provenance import Recorder

REL = "assets/units/size_xl/macros/ship_x_macro.xml"
NESTED = f"extensions/a_copy/{REL}"
SEL = "/macros/macro[@name='ship_x_macro']/properties"
IDX_BASE = r"assets\units\size_xl\macros\ship_x_macro"
IDX_OWN = r"extensions\a_copy\assets\units\size_xl\macros\ship_x_macro"
ADD_OWN = f'<add sel="/index"><entry name="ship_x_macro" value="{IDX_OWN}"/></add>'


def _macro(value: int, comp: str = "ship_x") -> str:
    return (f'<macros><macro name="ship_x_macro" class="ship_xl"><component ref="{comp}"/>'
            f'<properties><hull max="{value}"/></properties></macro></macros>')


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path: Path, copier_index: str | None = ADD_OWN, copy: str | None = None,
           base_entries=(IDX_BASE,)):
    """reference: base macro (hull 1) + index; a_copy: complete copy (hull 2) + an index diff;
    b_patch: a plain-path diff (hull 7); c_nest: a nested patch of a_copy's file (hull 9)."""
    ref = tmp_path / "reference"
    _w(ref / REL, _macro(1))
    _w(ref / "index" / "macros.xml",
       "<index>" + "".join(f'<entry name="ship_x_macro" value="{v}"/>' for v in base_entries)
       + "</index>")
    ext = tmp_path / "extensions"
    a, b, c = ext / "a_copy", ext / "b_patch", ext / "c_nest"
    for d in (a, b, c):
        _w(d / "content.xml", f'<content id="{d.name}" name="{d.name}" version="1"/>')
    _w(a / REL, copy if copy is not None else _macro(2, comp="ship_copy"))
    if copier_index is not None:
        _w(a / "index" / "macros.xml", f"<diff>{copier_index}</diff>")
    _w(b / REL, f'<diff><replace sel="{SEL}/hull/@max">7</replace></diff>')
    _w(c / NESTED, f'<diff><replace sel="{SEL}/hull/@max">9</replace></diff>')
    return ref, a, b, c


def _cfg(ref: Path, *overlays: Path) -> _merge.Config:
    return _merge.Config(reference=ref, include_packed_dlc=False, overlays=tuple(overlays))


def _hulls(tree) -> list[str]:
    return tree.xpath("//macro[@name='ship_x_macro']/properties/hull/@max")


# --- the resolver ----------------------------------------------------------------------------

def test_choice_reads_the_LAST_entry_as_the_copiers_own_document(tmp_path):
    ref, a, b, c = _world(tmp_path)
    got = _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c))
    assert got == _merge.OwnCopy("a_copy", IDX_OWN, 2), got


def test_TWIN_choice_base_entry_last_is_PLAIN(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=(IDX_OWN, IDX_BASE))
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_honours_a_REMOVE_and_a_value_REPLACE(tmp_path):
    rm = "<remove sel=\"/index/entry[@name='ship_x_macro'][last()]\"/>"
    ref, a, b, c = _world(tmp_path, copier_index=ADD_OWN)
    _w(b / "index" / "macros.xml", f"<diff>{rm}</diff>")
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN, "removed"
    _merge.EffectiveIndex.clear()
    rp = f'<replace sel="/index/entry[@name=\'ship_x_macro\']/@value">{IDX_OWN}</replace>'
    ref, a, b, c = _world(tmp_path / "second", copier_index=rp)
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == \
        _merge.OwnCopy("a_copy", IDX_OWN, 1), "re-pointed"


def test_TWIN_choice_a_copier_that_ships_a_DIFF_owns_nothing(tmp_path):
    ref, a, b, c = _world(tmp_path, copy=f'<diff><replace sel="{SEL}/hull/@max">3</replace></diff>')
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_a_file_the_game_does_not_supply_is_PLAIN(tmp_path):
    ref, a, b, c = _world(tmp_path)
    (ref / REL).unlink()
    assert _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c)) == _merge.PLAIN


def test_TWIN_choice_a_partial_index_is_no_verdict(tmp_path):
    ref, a, b, c = _world(tmp_path)
    _w(b / "index" / "macros.xml", "<diff><add sel='/index'>")
    got = _merge.EffectiveIndex.choice(REL, _cfg(ref), (a, b, c))
    assert isinstance(got, _merge.IndexPartial) and got.skipped, got


def test_the_index_is_built_once_per_tuple_of_roots(tmp_path):
    ref, a, b, c = _world(tmp_path)
    one = _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), (a, b, c))
    assert _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), [a, b, c]) is one
    assert _merge.EffectiveIndex.of("index/macros.xml", _cfg(ref), (a, b)) is not one


def test_every_reader_agrees_on_the_loaded_document(tmp_path):
    """build_index, index_loads_copy, EngineDocs, EntityDefs and the opt-in merge all name the
    copier's own document. The hand mutant (last -> first entry) turns this red."""
    ref, a, b, c = _world(tmp_path)
    cfg = _cfg(ref, a, b, c)
    roots = [a, b, c]
    root, value = _resolve.build_index(cfg, [], "index/macros.xml")["ship_x_macro"]
    assert (root.name, value.replace("\\", "/")) == ("a_copy", REL[:-4]), (root, value)
    assert _check.index_loads_copy(REL, None, cfg) == ("a_copy", IDX_OWN, 2)
    docs = _check.EngineDocs(cfg, None, _check.Report())
    assert docs.node("macro", "ship_x_macro") is None, "own document: build_index's file is it"
    defs = _check.EntityDefs(_cfg(ref), roots)
    assert [v.lower() for v in defs._registered["ship_x_macro"]] == [NESTED.lower()]
    res = _merge.build_effective(REL, cfg, index_roots=roots)
    assert res.tree.xpath("//component/@ref") == ["ship_copy"], res.sources
    assert _merge.EffectiveIndex.choice(REL, cfg, roots).owner == "a_copy"


def test_TWIN_agreement_with_the_base_entry_last(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=(IDX_OWN, IDX_BASE))
    cfg = _cfg(ref, a, b, c)
    assert _check.index_loads_copy(REL, None, cfg) is None
    defs = _check.EntityDefs(_cfg(ref), [a, b, c])
    assert [v.lower() for v in defs._registered["ship_x_macro"]] == [REL.lower()]
    res = _merge.build_effective(REL, cfg, index_roots=[a, b, c])
    assert sorted(_hulls(res.tree)) == ["1", "2"] and res.never_opened == [], res.sources


# --- build_effective(index_roots=...) : the plain door --------------------------------------

def test_without_index_roots_the_merge_is_unchanged(tmp_path):
    """Opt-in: no index_roots, no index. The copy merges and the plain diff is evaluated."""
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(REL, _cfg(ref, a, b, c))
    assert res.never_opened == [] and "a_copy:merge" in res.sources, res.sources


def test_index_roots_load_the_copiers_document_and_never_open_the_plain_diff(tmp_path):
    ref, a, b, c = _world(tmp_path)
    rec = Recorder()
    res = _merge.build_effective(REL, _cfg(ref, a, b, c), recorder=rec, index_roots=[a, b, c])
    assert res.tree.xpath("//component/@ref") == ["ship_copy"], "the copier's document"
    assert _hulls(res.tree) == ["9"], "the nested patch applies, the plain diff (7) does not"
    assert res.never_opened == ["b_patch:diff(unopened:index)"], res.never_opened
    assert res.sources[0] == f"index:{IDX_OWN}" and "b_patch:diff(unopened:index)" in res.sources
    assert any("F383" in u and "a_copy" in u for u in res.unmeasured), res.unmeasured
    comp = res.tree.xpath("//component")[0]
    assert rec.winner(comp).source == "a_copy", "the copier's values are never 'base'"


def test_TWIN_index_roots_out_of_the_class_change_nothing(tmp_path):
    ref, a, b, c = _world(tmp_path)
    other = "assets/fx/weaponfx/macros/bullet_x_macro.xml"
    _w(ref / other, _macro(1))
    _w(a / other, _macro(2))
    plain = _merge.build_effective(other, _cfg(ref, a, b))
    res = _merge.build_effective(other, _cfg(ref, a, b), index_roots=[a, b, c])
    assert res.sources == plain.sources and res.never_opened == []


def test_TWIN_index_roots_with_no_complete_copy_among_the_layers(tmp_path):
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(REL, _cfg(ref, b), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["7"] and res.never_opened == [], res.sources


def test_TWIN_index_roots_with_the_copier_not_merged_here(tmp_path):
    """The index names a_copy, but only b and a second copier are merged: no document to
    build, so the plain model stands."""
    ref, a, b, c = _world(tmp_path)
    d = tmp_path / "extensions" / "d_copy"
    _w(d / REL, _macro(4))
    res = _merge.build_effective(REL, _cfg(ref, b, d), index_roots=[a, b, c, d])
    assert res.never_opened == [] and "d_copy:merge" in res.sources, res.sources


def test_TWIN_index_roots_a_partial_index_is_a_skip_on_the_plain_model(tmp_path):
    ref, a, b, c = _world(tmp_path)
    _w(b / "index" / "macros.xml", "<diff><add sel='/index'>")
    res = _merge.build_effective(REL, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.never_opened == [] and "a_copy:merge" in res.sources
    assert any("F383" in s and "could not be built completely" in s for s in res.skipped)


# --- build_effective(index_roots=...) : the nested door -------------------------------------

def test_a_nested_patch_on_a_copy_that_merges_at_the_base_path_builds_no_tree(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.tree is None and res.never_opened == ["c_nest:diff(unopened:index)"], res.sources
    assert any("never opens a nested patch" in u for u in res.unmeasured), res.unmeasured


def test_TWIN_nested_without_index_roots_still_applies_the_patch(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c))
    assert _hulls(res.tree) == ["9"] and res.never_opened == []


def test_TWIN_nested_with_the_own_entry_last_applies_the_patch(tmp_path):
    ref, a, b, c = _world(tmp_path)
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["9"] and res.never_opened == []


def test_TWIN_nested_a_copy_with_another_root_keeps_the_nested_model(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None,
                          copy='<components><component name="ship_x_macro"/></components>')
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert res.never_opened == [] and res.tree is not None


def test_TWIN_nested_no_entry_for_the_name_keeps_the_nested_model(tmp_path):
    ref, a, b, c = _world(tmp_path, copier_index=None, base_entries=())
    res = _merge.build_effective(NESTED, _cfg(ref, a, b, c), index_roots=[a, b, c])
    assert _hulls(res.tree) == ["9"] and res.never_opened == []


# --- the callers: the store, x4effective dump, x4similar ------------------------------------

def _no_profile(monkeypatch):
    from x4validate import _registry
    monkeypatch.setattr(_registry, "ingest_content_xml", lambda *a, **k: [])
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", None)


def test_the_store_holds_the_copiers_document_and_no_never_opened_origin(tmp_path, monkeypatch):
    import sqlite3

    from x4validate import _effective
    ref, a, b, c = _world(tmp_path)
    _no_profile(monkeypatch)
    db = tmp_path / "eff.sqlite"
    _effective.build(_cfg(ref), db, dirs=[tmp_path / "extensions"], kinds=("macro",))
    con = sqlite3.connect(db)
    rows = con.execute("SELECT a.value, a.origin FROM attrs a JOIN entities e ON a.entity_id = e.id "
                       "WHERE e.name = 'ship_x_macro' AND a.prop = 'hull.max'").fetchall()
    origins = {r[0] for r in con.execute("SELECT origin FROM attrs")}
    con.close()
    assert [r[0] for r in rows] == ["9"], rows
    assert not any(o.startswith("b_patch") for o in origins), origins


def test_dump_names_the_never_opened_layer(tmp_path, monkeypatch, capsys):
    from x4validate import _effectivecli
    ref, a, b, c = _world(tmp_path)
    mods = [{"folder": d.name, "path": str(d), "id": d.name, "enabled": True} for d in (a, b, c)]
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: mods)
    monkeypatch.setattr(_effectivecli, "ordered_overlays", lambda m: [(x, Path(x["path"])) for x in m])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *x, **k: [a, b, c])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", REL,
                             "--reference", str(ref)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "NEVER OPENED by the engine (not in this tree): b_patch:diff(unopened:index)" in out, out
    assert 'max="9"' in out and 'ref="ship_copy"' in out and 'max="1"' not in out, out


def test_x4similar_scores_the_copiers_document(tmp_path, monkeypatch):
    from x4validate import _similarity
    ref, a, b, c = _world(tmp_path)
    _no_profile(monkeypatch)
    vecs = [v for v in _similarity._collect_all(ref, tmp_path / "extensions", [], dlc_dirs=[])
            if v.macro_name == "ship_x_macro"]
    assert [v.stats.get("hull.max") for v in vecs] == [9.0], vecs


# --- x4compat: NEVER OPENED (user decision 2026-11-18: advisory, exit 0) ---------------------

def _compat_run(ext, ref, *extra):
    import contextlib
    import io

    from x4validate import _compat
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _compat.main(["check", *extra, "--ext-dir", str(ext), "--reference", str(ref)])
    return rc, buf.getvalue()


def _no_disagreement(rep):
    assert not [s for s in rep.skipped if "disagreed" in s.why], rep.skipped


def test_x4compat_lists_the_plain_diff_as_NEVER_OPENED_and_in_no_other_list(tmp_path):
    from x4validate import _compat
    ref, a, b, c = _world(tmp_path)
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    assert [(n.vpath, n.mod, n.owner, n.line > 0, n.sel) for n in rep.never_opened] == [
        (REL, "b_patch", "a_copy", True, f"{SEL}/hull/@max")], rep.never_opened
    assert not [u for u in rep.unapplied if "b_patch" in u], rep.unapplied
    assert not [x for x in rep.collisions if "b_patch" in x.mods], rep.collisions
    _no_disagreement(rep)


def test_TWIN_x4compat_with_the_base_entry_last_opens_the_plain_diff(tmp_path):
    from x4validate import _compat
    ref, a, b, c = _world(tmp_path, copier_index=None)
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    assert [n.mod for n in rep.never_opened] == ["c_nest"], "only the nested patch (converse)"
    assert [u for u in rep.unapplied if "b_patch" in u], "the plain diff is evaluated (2 nodes)"
    _no_disagreement(rep)


def test_x4compat_lists_a_nested_patch_on_a_MERGING_copy_as_NEVER_OPENED(tmp_path):
    from x4validate import _compat
    ref, a, b, c = _world(tmp_path, copier_index=None)
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    rows = [n for n in rep.never_opened if n.mod == "c_nest"]
    assert [(n.vpath.lower(), n.owner) for n in rows] == [(NESTED.lower(), "a_copy")], rows
    assert "MERGES" in rows[0].reason


def test_TWIN_x4compat_with_the_own_entry_last_opens_the_nested_patch(tmp_path):
    from x4validate import _compat
    ref, a, b, c = _world(tmp_path)
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    assert "c_nest" not in [n.mod for n in rep.never_opened], rep.never_opened


def test_x4compat_NEVER_OPENED_is_advisory_exit_0_and_in_the_json(tmp_path):
    import json
    ref, a, b, c = _world(tmp_path)
    rc, out = _compat_run(tmp_path / "extensions", ref, "--json")
    rows = json.loads(out)["never_opened"]
    assert rc == 0 and [(r["mod"], r["owner"]) for r in rows] == [("b_patch", "a_copy")], out
    rc, text = _compat_run(tmp_path / "extensions", ref)
    assert rc == 0 and "NEVER OPENED BY THE ENGINE" in text and "b_patch" in text, text


# --- the gates: cross_tool (no store origin) and tool_properties (never a winner) -----------

def _gate(name):
    from conftest import hermetic_gate
    return hermetic_gate(name)


def _store_con(tmp_path, monkeypatch, index_roots: bool):
    import sqlite3

    from x4validate import _effective
    ref, a, b, c = _world(tmp_path)
    # `[1]`: one node in either model, so only the index decides whether b's value lands.
    _w(b / REL, "<diff><replace sel=\"/macros/macro[@name='ship_x_macro'][1]/properties"
                "/hull/@max\">7</replace></diff>")
    _no_profile(monkeypatch)
    if not index_roots:   # the store as built before 4.3: no index consulted
        real = _effective._merge_one
        monkeypatch.setattr(_effective, "_merge_one",
                            lambda v, cfg, ov, roots=None: real(v, cfg, ov, None))
    db = tmp_path / "eff.sqlite"
    _effective.build(_cfg(ref), db, dirs=[tmp_path / "extensions"], kinds=("macro",))
    from x4validate import _compat
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    return sqlite3.connect(db), rep


def test_cross_tool_a_never_opened_mod_is_no_store_origin(tmp_path, monkeypatch):
    con, rep = _store_con(tmp_path, monkeypatch, index_roots=True)
    assert _gate("cross_tool").never_opened_origins(con, rep) == (1, [])


def test_TWIN_cross_tool_goes_red_on_a_store_built_without_the_index(tmp_path, monkeypatch):
    con, rep = _store_con(tmp_path, monkeypatch, index_roots=False)
    n, bad = _gate("cross_tool").never_opened_origins(con, rep)
    assert n == 1 and len(bad) == 1 and "b_patch" in bad[0], bad


def test_tool_properties_a_never_opened_mod_is_never_a_winner(tmp_path):
    from x4validate import _compat
    ref, a, b, c = _world(tmp_path)
    rep = _compat.analyze(tmp_path / "extensions", config=_merge.Config(reference=ref,
                                                                       include_packed_dlc=False))
    gate = _gate("tool_properties")
    assert gate.never_opened_winners(rep) == []
    rep.collisions.append(_compat.Collision(REL, "HARD", f"{SEL}/hull/@max",
                                            ["a_copy", "b_patch"], "b_patch", "planted"))
    assert gate.never_opened_winners(rep) == [f"HARD {REL}: b_patch"], "TWIN: a planted row"

"""Issue #17: a NESTED full copy of a MOD-owned macro file is MERGED, not a replacement.

MEASURED (debug.txt 2026-11-05 and 2026-11-06, two launches): `cpsdo_vro` ships complete
`<macros>` copies of two `cpsdo_zb_modpack` macro files at
`extensions/cpsdo_zb_modpack/assets/fx/weaponfx/macros/...`, and a later mod's
`/macros/macro[@name='X']/...` ops were skipped with "Multiple matching nodes" -- the
engine's document holds BOTH macros. The toolkit modelled the copy as a replacement (one
macro), so x4validate passed those ops and `x4debug crosscheck` predicted no skip.

Only that class changes. Every twin below pins a shape that must NOT change: an indexed
selector, a nested DIFF, a non-macro nested full copy, and a plain-path full copy of a
base file (both still replacements, now DISCLOSED as unmeasured).
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _check, _compat, _debugcli, _debuglog, _effectivecli, _merge

REL = "assets/fx/weaponfx/macros/bullet_x_macro.xml"
NESTED = f"extensions/a_owner/{REL}"
SEL = "/macros/macro[@name='bullet_x_macro']/properties/damage"


def _macro(value: int) -> str:
    return ('<macros><macro name="bullet_x_macro" class="bullet"><properties>'
            f'<damage value="{value}"/></properties></macro></macros>')


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _world(tmp_path: Path, copy_text: str | None = None, patch_ops: str | None = None,
           rel: str = REL):
    """a_owner owns *rel* (full, base-less); b_copy ships *copy_text* at its nested path;
    c_patch ships a nested <diff> of *patch_ops*. Load order a, b, c (folder order)."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    ext = tmp_path / "extensions"
    _w(ext / "a_owner" / "content.xml", '<content id="a_owner" name="a" version="1"/>')
    _w(ext / "a_owner" / rel, _macro(1))
    if copy_text is not None:
        _w(ext / "b_copy" / "content.xml",
           '<content id="b_copy" name="b" version="1"><dependency id="a_owner"/></content>')
        _w(ext / "b_copy" / "extensions" / "a_owner" / rel, copy_text)
    if patch_ops is not None:
        _w(ext / "c_patch" / "content.xml",
           '<content id="c_patch" name="c" version="1"><dependency id="a_owner"/></content>')
        _w(ext / "c_patch" / "extensions" / "a_owner" / rel, f"<diff>{patch_ops}</diff>")
    dirs = {p.name: p for p in sorted(ext.iterdir())}
    return ext, ref, dirs


def _cfg(ref, dirs, names):
    return _merge.Config(reference=ref, include_packed_dlc=False,
                         overlays=tuple(dirs[n] for n in names))


# --- the merge model ------------------------------------------------------------

def test_a_nested_full_copy_of_a_mod_macro_file_MERGES_through_both_doors(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    cfg = _cfg(ref, dirs, ["a_owner", "b_copy"])
    plain = _merge.build_effective(REL, cfg)
    nested = _merge.build_effective(NESTED, cfg)
    for res in (plain, nested):
        values = [d.get("value") for d in res.tree.iter("damage")]
        assert values == ["1", "2"], (values, res.sources)
        # The merge is MEASURED; the copy ORDER and which copy the engine reads are not.
        assert len(res.unmeasured) == 1, res.unmeasured
        assert "copy order" in res.unmeasured[0] and "UNMEASURED" in res.unmeasured[0]
    assert plain.sources == ["a_owner:full", "b_copy:merge(nested:a_owner)"], plain.sources
    assert nested.sources == ["a_owner:owner", "b_copy:merge"], nested.sources


def test_TWIN_a_nested_DIFF_is_unchanged(tmp_path):
    _ext, ref, dirs = _world(tmp_path, patch_ops=f'<replace sel="{SEL}/@value">7</replace>')
    res = _merge.build_effective(REL, _cfg(ref, dirs, ["a_owner", "c_patch"]))
    assert [d.get("value") for d in res.tree.iter("damage")] == ["7"], res.sources


def test_TWIN_a_nested_full_copy_outside_the_macro_class_still_replaces_and_says_so(tmp_path):
    rel = "assets/fx/weaponfx/bullet_x.xml"           # not .../macros/*_macro.xml
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2), rel=rel)
    res = _merge.build_effective(rel, _cfg(ref, dirs, ["a_owner", "b_copy"]))
    assert [d.get("value") for d in res.tree.iter("damage")] == ["2"], res.sources
    assert len(res.unmeasured) == 1 and "UNMEASURED" in res.unmeasured[0], res.unmeasured
    assert "NESTED" in res.unmeasured[0]


def test_TWIN_a_plain_path_full_copy_of_a_base_file_still_replaces_and_says_so(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / REL, _macro(1))
    mod = tmp_path / "extensions" / "m_full"
    _w(mod / REL, _macro(2))
    res = _merge.build_effective(REL, _merge.Config(reference=ref, include_packed_dlc=False,
                                                    overlays=(mod,)))
    assert [d.get("value") for d in res.tree.iter("damage")] == ["2"], res.sources
    assert res.sources == ["base", "m_full:full"], res.sources
    assert len(res.unmeasured) == 1 and "plain path" in res.unmeasured[0]


def test_TWIN_a_nested_copy_over_a_DLC_owner_is_not_merged(tmp_path):
    """The measured owner is a MOD. A packed-DLC owner keeps the old model."""
    tree = _merge.parse_bytes(_macro(1).encode())
    out, mode = _merge.apply_overlay(tree, _merge.parse_bytes(_macro(2).encode()),
                                     f"extensions/ego_dlc_x/{REL}", "m", nested_on_mod=False)
    assert mode == "full" and [d.get("value") for d in out.iter("damage")] == ["2"]


def test_TWIN_a_lone_nested_copy_with_no_owner_document_is_the_document(tmp_path):
    """Clause `tree is not None`: nothing to merge into (19 such files live)."""
    out, mode = _merge.apply_overlay(None, _merge.parse_bytes(_macro(2).encode()),
                                     REL, "b_copy", nested_on_mod=True)
    assert mode == "full" and len(out.findall("macro")) == 1


def test_TWIN_a_copy_with_a_different_root_tag_still_replaces(tmp_path):
    """Clause `oroot.tag == tree.tag`."""
    tree = _merge.parse_bytes(_macro(1).encode())
    other = _merge.parse_bytes(b'<components><component name="x"/></components>')
    out, mode = _merge.apply_overlay(tree, other, REL, "b_copy", nested_on_mod=True)
    assert mode == "full" and out.tag == "components"


# --- x4validate -----------------------------------------------------------------

def test_x4validate_flags_a_later_unindexed_selector_as_multiple_match(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2),
                             patch_ops=f'<add sel="{SEL}" type="@shield">5</add>')
    rep = _check.validate(dirs["c_patch"], _cfg(ref, dirs, ["a_owner", "b_copy"]))
    sel = [f for f in rep.errors if f.category == "sel"]
    assert len(sel) == 1 and "matched 2 nodes" in sel[0].message, rep.findings
    assert sel[0].sel == SEL


def test_TWIN_x4validate_passes_indexed_selectors(tmp_path):
    ops = "".join(f'<add sel="/macros/macro[@name=\'bullet_x_macro\'][{i}]/properties/damage" '
                  f'type="@shield">5</add>' for i in (1, 2))
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2), patch_ops=ops)
    rep = _check.validate(dirs["c_patch"], _cfg(ref, dirs, ["a_owner", "b_copy"]))
    assert [f for f in rep.errors if f.category == "sel"] == [], rep.findings


def test_TWIN_x4validate_passes_the_unindexed_selector_without_the_copy(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2),
                             patch_ops=f'<add sel="{SEL}" type="@shield">5</add>')
    rep = _check.validate(dirs["c_patch"], _cfg(ref, dirs, ["a_owner"]))
    assert [f for f in rep.errors if f.category == "sel"] == [], rep.findings


def test_x4validate_tells_the_copying_mod_its_file_merges(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    rep = _check.validate(dirs["b_copy"], _cfg(ref, dirs, ["a_owner"]))
    merge = [f for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and merge[0].severity == "info", rep.findings
    assert "MERGES" in merge[0].message and "bullet_x_macro" in merge[0].message


def test_x4validate_discloses_a_plain_path_full_copy_as_unmeasured(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    _w(ref / REL, _macro(1))
    mod = tmp_path / "extensions" / "m_full"
    _w(mod / "content.xml", '<content id="m_full" name="m" version="1"/>')
    _w(mod / REL, _macro(2))
    rep = _check.validate(mod, _merge.Config(reference=ref, include_packed_dlc=False))
    merge = [f for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0].message, rep.findings
    assert merge[0].severity == "info"


# --- x4debug crosscheck -----------------------------------------------------------

def test_crosscheck_predicts_the_engine_skip(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2),
                             patch_ops=f'<add sel="{SEL}" type="@shield">5</add>')
    rep = _check.validate(dirs["c_patch"], _cfg(ref, dirs, ["a_owner", "b_copy"]))
    log = ("[=ERROR=] 0.00 Multiple matching nodes for path '" + SEL + "' in patch file "
           "'extensions\\c_patch\\extensions\\a_owner\\assets\\fx\\weaponfx\\macros\\"
           "bullet_x_macro'. Skipping node.\n")
    observed = _debugcli.observed_ops(_debuglog.parse_log_text(log), "c_patch")
    cmp = _debugcli.compare_ops(observed, _debugcli.predicted_ops(rep))
    assert cmp.clean, cmp


# --- x4effective dump --chain --------------------------------------------------------

def test_dump_chain_says_merge_and_shows_both_macros(tmp_path, monkeypatch, capsys):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for",
                        lambda *a, **k: [dirs["a_owner"], dirs["b_copy"]])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", REL,
                             "--reference", str(ref), "--chain"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "b_copy:merge(nested:a_owner)" in out, out
    assert out.count('name="bullet_x_macro"') == 2, out
    assert "<!-- UNMEASURED:" in out and "copy order" in out, out


def test_dump_prints_UNMEASURED_for_a_plain_path_replacement(tmp_path, monkeypatch, capsys):
    ref = tmp_path / "reference"
    _w(ref / REL, _macro(1))
    mod = tmp_path / "extensions" / "m_full"
    _w(mod / REL, _macro(2))
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *a, **k: [mod])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", REL,
                             "--reference", str(ref)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "<!-- UNMEASURED: m_full: a complete (non-diff) file at a plain path" in out, out


# --- x4compat -----------------------------------------------------------------------

def test_x4compat_reports_the_duplicate_not_a_full_override(tmp_path):
    ext, ref, _dirs = _world(tmp_path, copy_text=_macro(2))
    rep = _compat.analyze(ext, config=_merge.Config(reference=ref, include_packed_dlc=False))
    assert rep.by_kind("FULL-OVERRIDE") == [], rep.collisions
    hard = [c for c in rep.by_kind("HARD") if "merged document" in c.detail]
    assert [(c.mods, c.target) for c in hard] == [(["a_owner", "b_copy"], "/macros/macro")], \
        rep.collisions
    assert "not engine-measured" in hard[0].detail
    assert hard[0].live_value_owner() is None, "which copy the engine uses is UNMEASURED"


# --- review round 2: one falsification twin per clause, per door ---------------------

import sqlite3  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from x4validate import _compat_state, _livecli  # noqa: E402


@pytest.mark.parametrize("rel, merges", [
    ("assets/fx/weaponfx/macros/bullet_x_macro.xml", True),
    ("assets/fx/weaponfx/bullet_x_macro.xml", False),             # parent is not macros/
    ("assets/fx/weaponfx/macros/bullet_x.xml", False),            # name is not *_macro.xml
    ("extensions/ego_dlc_x/assets/fx/macros/bullet_x_macro.xml", False),  # doubly nested
])
def test_merges_nested_copy_one_twin_per_clause(rel, merges):
    assert _merge.merges_nested_copy(rel) is merges


@pytest.mark.parametrize("rel", ["assets/fx/weaponfx/bullet_x_macro.xml",
                                 "assets/fx/weaponfx/macros/bullet_x.xml"])
def test_TWIN_each_class_clause_alone_keeps_the_replacement(tmp_path, rel):
    """Each file fails exactly ONE clause of the class, so each clause has a twin."""
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2), rel=rel)
    for vp in (rel, f"extensions/a_owner/{rel}"):
        res = _merge.build_effective(vp, _cfg(ref, dirs, ["a_owner", "b_copy"]))
        assert [d.get("value") for d in res.tree.iter("damage")] == ["2"], res.sources


def test_TWIN_a_doubly_nested_copy_keeps_the_replacement(tmp_path):
    """Clause `parts[0] != "extensions"`: b owns its own doubly-nested patch file
    (F305), c ships a complete copy of THAT at c/extensions/b/extensions/<dlc>/..."""
    inner = "extensions/ego_dlc_x/assets/fx/macros/bullet_x_macro.xml"
    ext = tmp_path / "extensions"
    _w(ext / "b_mod" / inner, _macro(1))
    _w(ext / "c_copy" / "extensions" / "b_mod" / inner, _macro(2))
    cfg = _merge.Config(reference=tmp_path / "reference", include_packed_dlc=False,
                        overlays=(ext / "b_mod", ext / "c_copy"))
    res = _merge.build_effective(f"extensions/b_mod/{inner}", cfg)
    assert [d.get("value") for d in res.tree.iter("damage")] == ["2"], res.sources


def test_TWIN_build_owned_with_a_DLC_owner_keeps_the_replacement(tmp_path):
    """`_build_owned(owner_is_dlc=True)`: a packed-only DLC owner, never merged."""
    dlc, copier = tmp_path / "ego_dlc_x", tmp_path / "m_copy"
    _w(dlc / REL, _macro(1))
    _w(copier / "extensions" / "ego_dlc_x" / REL, _macro(2))
    res = _merge._build_owned("ego_dlc_x", REL, f"extensions/ego_dlc_x/{REL}",
                              [dlc, copier], None, owner_is_dlc=True)
    assert [d.get("value") for d in res.tree.iter("damage")] == ["2"], res.sources
    assert res.sources == ["ego_dlc_x:owner", "m_copy:full"], res.sources


def test_the_trace_nested_door_merges_like_the_plain_door(tmp_path):
    """`_compat_state.trace`'s nested callback (x4compat's sequential instrument) must
    pass the flag, or its byte check against the normal merge fails."""
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    want = _merge.build_effective(REL, _cfg(ref, dirs, ["a_owner", "b_copy"])).tree
    copy_root = _merge.parse_bytes(_macro(2).encode())
    nested = (lambda src, owner: copy_root if (src, owner) == ("b_copy", "a_owner") else None)
    _rec, _paths, qualified = _compat_state.trace(
        REL, _merge.parse_bytes(b"<macros/>"),
        [("a_owner", _merge.parse_bytes(_macro(1).encode())),
         ("b_copy", _merge.parse_bytes(b"<diff/>"))], want, nested=nested)
    assert qualified


@pytest.mark.parametrize("order", [["a_owner", "b_copy"], ["b_copy", "a_owner"]])
def test_walk_positions_agrees_with_the_merge_at_both_nested_sites(tmp_path, order):
    """The later-patcher site (owner first) and the earlier-patcher site (copier first)."""
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    assert _compat._walk_positions(REL, order, dirs, cfg, lambda *a: None)


def test_TWIN_x4validate_a_copy_with_another_root_tag_is_no_merge(tmp_path):
    """`_check`'s `oroot.tag == root.tag` clause: a <components> file at the owner's
    macro path does not merge, so it is disclosed as UNMEASURED, never as MERGES."""
    _ext, ref, dirs = _world(tmp_path, copy_text='<components><component name="x"/></components>')
    rep = _check.validate(dirs["b_copy"], _cfg(ref, dirs, ["a_owner"]))
    merge = [f.message for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and "UNMEASURED" in merge[0] and "MERGES it" not in merge[0], merge


def test_every_merge_reader_names_the_unmeasured_copy_order(tmp_path):
    _ext, ref, dirs = _world(tmp_path, copy_text=_macro(2))
    rep = _check.validate(dirs["b_copy"], _cfg(ref, dirs, ["a_owner"]))
    merge = [f.message for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and "copy order" in merge[0], merge
    ext, _ref, _dirs = _world(tmp_path / "c", copy_text=_macro(2))
    crep = _compat.analyze(ext, config=_merge.Config(reference=_ref, include_packed_dlc=False))
    hard = [c for c in crep.by_kind("HARD") if "merged document" in c.detail]
    assert hard and "copy order" in hard[0].detail, crep.collisions


# --- readers that return ONE value for a duplicated name ---------------------------

def _store(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        "CREATE TABLE entities(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, klass TEXT,"
        " vpath TEXT, origin TEXT, chain TEXT);"
        "CREATE TABLE attrs(entity_id INTEGER, prop TEXT, value TEXT, num REAL,"
        " origin TEXT, chain TEXT);")
    for eid, speed in rows:
        con.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)",
                    (eid, "macro", "bullet_x_macro", "bullet", REL, "a_owner", None))
        con.execute("INSERT INTO attrs VALUES(?,?,?,?,?,?)",
                    (eid, "bullet.speed", speed, float(speed), "a_owner", None))
    return con


def test_live_store_props_is_deterministic_and_announces_copies(capsys, monkeypatch):
    monkeypatch.setattr(_livecli, "_ANNOUNCED_DUPLICATES", set())
    con = _store([(7, "1500"), (3, "2500")])       # inserted out of id order
    assert _livecli._store_props(con, "bullet_x_macro") == {"bullet.speed": "2500"}
    err = capsys.readouterr().err
    assert "2 entities" in err and "id=3" in err and "UNMEASURED" in err, err


def test_TWIN_live_store_props_is_silent_for_one_entity(capsys):
    assert _livecli._store_props(_store([(3, "2500")]), "bullet_x_macro") == {
        "bullet.speed": "2500"}
    assert capsys.readouterr().err == ""


def test_show_names_the_copy_and_the_unmeasured_choice(capsys):
    con = _store([(3, "2500"), (7, "1500")])
    assert _effectivecli._one_entity(con, "macro", "bullet_x_macro")["id"] == 3
    err = capsys.readouterr().err
    assert "copy [1]" in err and "UNMEASURED" in err, err


GATES = Path(__file__).resolve().parents[1] / "gates"


@pytest.fixture()
def ca(gate_install):
    return gate_install.load("consistency_audit")


_TWO = ('<macros><macro name="bullet_x_macro"><properties><bullet speed="2500"/>'
        '</properties></macro><macro name="bullet_x_macro"><properties>'
        '<bullet speed="1500"/></properties></macro></macros>')


def test_consistency_audit_refuses_a_duplicated_name_in_the_merge(ca, monkeypatch):
    monkeypatch.setattr(ca, "_overlays_for", lambda vp: [])
    monkeypatch.setattr(ca._merge, "build_effective", lambda *a, **k: types.SimpleNamespace(
        tree=_merge.parse_bytes(_TWO.encode()), sources=[], skipped=[], unmeasured=[]))
    assert ca.from_merge(REL, "bullet_x_macro", "bullet.speed") is ca._DUPLICATE


def test_consistency_audit_refuses_a_duplicated_name_in_the_dump(ca, monkeypatch):
    monkeypatch.setattr(ca.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=_TWO))
    assert ca.from_dump(REL, "bullet_x_macro", "bullet.speed") is ca._DUPLICATE


def test_a_full_copy_at_a_DLC_nested_path_is_named_as_that_shape(tmp_path):
    """Review round 2: `extensions/ego_dlc_*/...` is #17's nested shape over a DLC,
    the largest unmodelled exposure (118 live files), never "a plain path"."""
    ref = tmp_path / "reference"
    _w(ref / "libraries" / "wares.xml", "<wares/>")
    dvp = f"extensions/ego_dlc_x/{REL}"
    _w(ref / dvp, _macro(1))
    mod = tmp_path / "extensions" / "m_full"
    _w(mod / "content.xml", '<content id="m_full" name="m" version="1"/>')
    _w(mod / dvp, _macro(2))
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    res = _merge.build_effective(dvp, _merge.replace(cfg, overlays=(mod,)))
    assert len(res.unmeasured) == 1 and "DLC's nested path" in res.unmeasured[0], res
    rep = _check.validate(mod, cfg)
    merge = [f.message for f in rep.findings if f.category == "merge"]
    assert len(merge) == 1 and "DLC's nested path" in merge[0], merge

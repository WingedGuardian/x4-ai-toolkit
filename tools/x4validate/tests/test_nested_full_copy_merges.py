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
        assert res.unmeasured == [], res.unmeasured
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
    assert "UNMEASURED" not in out, out


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

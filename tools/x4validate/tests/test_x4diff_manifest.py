"""x4diff compares a mod's content.xml, and keeps four answers apart.

WHY THIS EXISTS. `_modfiles.mod_xml_paths` drops `content.xml`, which is right for the
effective store, BaseX and x4compat (every mod ships one, and it is not a game document).
`x4diff` enumerated through the same function, so two folders differing ONLY in the
manifest printed `changed files: 0 added: 0 removed: 0` with no manifest line, and
`--file content.xml` printed "no differences". A release review that checks the version
bump could not see it, and was told the opposite.

States that must stay distinct: identical / differs / absent on one side / absent on both
/ unreadable. An unreadable manifest must never read as identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from x4validate import _diff, _diffcli, _modfiles, _threeway

_MANIFEST = ('<content id="m" name="Mod" version="{version}" date="{date}" save="0">'
             '<dependency id="ego_dlc_boron"/></content>')
_WARES = '<wares><ware id="a" price="{price}"/></wares>'


def _mod(root: Path, name: str, *, version: str | None = "100", date: str = "2026-01-01",
         price: str = "1", manifest: str | None = None) -> Path:
    """A mod folder. `version=None` ships no manifest; `manifest` overrides its bytes."""
    d = root / name
    (d / "libraries").mkdir(parents=True)
    (d / "libraries" / "wares.xml").write_text(_WARES.format(price=price), encoding="utf-8")
    if manifest is not None:
        (d / "content.xml").write_text(manifest, encoding="utf-8")
    elif version is not None:
        (d / "content.xml").write_text(_MANIFEST.format(version=version, date=date),
                                       encoding="utf-8")
    return d


def _run(capsys, *argv) -> tuple[int, str]:
    rc = _diffcli.main([str(a) for a in argv])
    return rc, capsys.readouterr().out


def _manifest_lines(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if ln.strip().startswith("manifest content.xml")]


# --- two-way: the summary line, per state --------------------------------------------------

def test_manifest_only_difference_is_reported_and_is_not_a_changed_file(tmp_path, capsys):
    old = _mod(tmp_path, "old", version="100", date="2026-01-01")
    new = _mod(tmp_path, "new", version="101", date="2026-02-01")
    rc, out = _run(capsys, old, new)
    assert rc == 0
    assert "changed files: 0   added: 0   removed: 0" in out, out
    assert _manifest_lines(out) == [
        "manifest content.xml: differs (version 100->101, date 2026-01-01->2026-02-01)"]
    assert _diff.diff_mods(old, new).manifest.state == "differs"


def test_manifest_plus_file_difference_counts_only_the_game_file(tmp_path, capsys):
    old = _mod(tmp_path, "old", version="100", price="1")
    new = _mod(tmp_path, "new", version="101", price="2")
    _, out = _run(capsys, old, new)
    assert "changed files: 1   added: 0   removed: 0" in out, out
    assert "total attr changes: 1" in out, "the manifest's rows must not join this total"
    assert _manifest_lines(out) == ["manifest content.xml: differs (version 100->101)"]
    assert "libraries/wares.xml" in out
    assert "content.xml" not in {f.vpath for f in _diff.diff_mods(old, new).files}


def test_identical_twin_says_identical(tmp_path, capsys):
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    rc, out = _run(capsys, old, new)
    assert rc == 0
    assert _manifest_lines(out) == ["manifest content.xml: identical"]
    assert _diff.diff_mods(old, new).manifest.state == "identical"


def test_reformatting_alone_is_identical_like_every_other_document(tmp_path, capsys):
    """Comparison is semantic, as it is for every file x4diff reads."""
    pretty = ('<content  save="0" date="2026-01-01" version="100" name="Mod" id="m">\n'
              '  <!-- a comment -->\n  <dependency id="ego_dlc_boron"/>\n</content>\n')
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new", manifest=pretty)
    _, out = _run(capsys, old, new)
    assert _manifest_lines(out) == ["manifest content.xml: identical"]


@pytest.mark.parametrize("old_ver,new_ver,state,fragment", [
    (None, "100", "absent-old", "absent from OLD, present in NEW"),
    ("100", None, "absent-new", "present in OLD, absent from NEW"),
    (None, None, "absent-both", "absent from both"),
])
def test_absence_is_named_per_side_and_never_identical(tmp_path, capsys, old_ver, new_ver,
                                                       state, fragment):
    old = _mod(tmp_path, "old", version=old_ver)
    new = _mod(tmp_path, "new", version=new_ver)
    _, out = _run(capsys, old, new)
    assert _manifest_lines(out) == [f"manifest content.xml: {fragment}"]
    assert _diff.diff_mods(old, new).manifest.state == state


@pytest.mark.parametrize("bad_side", ["old", "new"])
def test_malformed_manifest_is_NOT_COMPARED_and_names_its_side(tmp_path, capsys, bad_side):
    bad = '<content id="m"><unclosed></content>'
    old = _mod(tmp_path, "old", manifest=bad if bad_side == "old" else None)
    new = _mod(tmp_path, "new", manifest=bad if bad_side == "new" else None)
    _, out = _run(capsys, old, new)
    line = _manifest_lines(out)
    assert len(line) == 1 and "NOT COMPARED" in line[0], out
    assert not line[0].startswith("manifest content.xml: identical")
    assert f"- {bad_side.upper()}:" in line[0], "a reader has to know which copy to go and fix"
    assert _diff.diff_mods(old, new).manifest.state == "unreadable"


def test_unreadable_beats_absent_when_the_other_side_has_none(tmp_path):
    """One side broken, the other missing: nothing can be said about a difference."""
    old = _mod(tmp_path, "old", manifest="<content")
    new = _mod(tmp_path, "new", version=None)
    assert _diff.diff_mods(old, new).manifest.state == "unreadable"


def test_read_failure_that_is_not_bad_xml_is_also_unreadable(tmp_path, monkeypatch):
    """The other half of `overlay_root`'s channel: an OSError is not an absence."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    real = _diff._merge.parse_file

    def _boom(path):
        if path.name == "content.xml" and path.parent.name == "new":
            raise OSError("simulated read failure")
        return real(path)

    monkeypatch.setattr(_diff._merge, "parse_file", _boom)
    m = _diff.compare_manifests(old, new)
    assert m.state == "unreadable" and "simulated read failure" in m.reason


def test_a_changed_id_is_one_row_not_every_node_replaced(tmp_path, capsys):
    """`_index` keys the root on its id, so a plain `diff_file` turns a renamed id into
    the whole document removed plus added, and the headline would show no id change."""
    old = _mod(tmp_path, "old")
    new = _mod(tmp_path, "new", manifest=_MANIFEST.format(version="100", date="2026-01-01")
               .replace('id="m"', 'id="other"'))
    _, out = _run(capsys, old, new)
    assert _manifest_lines(out) == ["manifest content.xml: differs (id m->other)"]
    m = _diff.diff_mods(old, new).manifest
    assert m.detail.nodes_added == [] and m.detail.nodes_removed == []


def test_child_changes_are_counted_when_no_root_attribute_moved(tmp_path, capsys):
    """A changed dependency is what a save-compatibility review wants to see."""
    old = _mod(tmp_path, "old")
    new = _mod(tmp_path, "new", manifest=_MANIFEST.format(version="100", date="2026-01-01")
               .replace('<dependency id="ego_dlc_boron"/>', '<dependency id="ego_dlc_split"/>'))
    _, out = _run(capsys, old, new)
    assert _manifest_lines(out) == [
        "manifest content.xml: differs (2 other change(s); --file content.xml lists them)"]


def test_a_long_description_is_cut_in_the_summary_and_whole_under_file(tmp_path, capsys):
    long_old, long_new = "a" * 200, "b" * 200
    tpl = '<content id="m" version="100" description="{}"/>'
    old = _mod(tmp_path, "old", manifest=tpl.format(long_old))
    new = _mod(tmp_path, "new", manifest=tpl.format(long_new))
    _, out = _run(capsys, old, new)
    line = _manifest_lines(out)[0]
    assert long_new not in line and "..." in line
    _, out = _run(capsys, old, new, "--file", "content.xml")
    assert long_new in out


def test_an_added_root_attribute_shows_the_absence_sentinel(tmp_path, capsys):
    old = _mod(tmp_path, "old", manifest='<content id="m"/>')
    new = _mod(tmp_path, "new", manifest='<content id="m" sync="false"/>')
    _, out = _run(capsys, old, new)
    assert _manifest_lines(out) == ["manifest content.xml: differs (sync ∅->false)"]


def test_overlay_layers_are_not_merged_into_the_manifest_and_it_says_so(tmp_path, capsys):
    """A manifest is one mod's identity: it is not a document layers patch."""
    old = _mod(tmp_path, "old", version="100")
    layer = _mod(tmp_path, "layer", version="555")
    new = _mod(tmp_path, "new", version="100")
    _, out = _run(capsys, old, new, "--overlay", layer)
    line = _manifest_lines(out)
    assert len(line) == 1 and line[0].startswith("manifest content.xml: identical"), out
    assert "old only" in line[0] and "not merged" in line[0]


# --- two-way: --file and --detail report from the same result ------------------------------

@pytest.mark.parametrize("spelling", ["content.xml", "CONTENT.XML", "./content.xml", ".\\content.xml"])
def test_file_flag_shows_the_real_rows(tmp_path, capsys, spelling):
    old = _mod(tmp_path, "old", version="100")
    new = _mod(tmp_path, "new", version="101")
    rc, out = _run(capsys, old, new, "--file", spelling)
    assert rc == 0
    assert "/content @version: 100 -> 101" in out, out
    assert "no differences" not in out


def test_file_flag_on_an_identical_manifest_is_no_differences(tmp_path, capsys):
    """The control: without it, a fix that never said "no differences" would pass."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    rc, out = _run(capsys, old, new, "--file", "content.xml")
    assert rc == 0 and "content.xml: no differences" in out, out


def test_file_flag_on_an_unreadable_manifest_refuses(tmp_path, capsys):
    old = _mod(tmp_path, "old")
    new = _mod(tmp_path, "new", manifest="<content")
    rc, out = _run(capsys, old, new, "--file", "content.xml")
    assert rc == 1
    assert "content.xml: NOT COMPARED" in out and "no differences" not in out, out


@pytest.mark.parametrize("old_ver,new_ver,says", [
    (None, "100", "content.xml: ADDED (present only in NEW)"),
    ("100", None, "content.xml: REMOVED (present only in OLD)"),
])
def test_file_flag_on_a_one_sided_manifest_says_which(tmp_path, capsys, old_ver, new_ver, says):
    old = _mod(tmp_path, "old", version=old_ver)
    new = _mod(tmp_path, "new", version=new_ver)
    rc, out = _run(capsys, old, new, "--file", "content.xml")
    assert rc == 0 and says in out, out


def test_file_flag_when_neither_side_has_a_manifest_is_an_absence_of_the_file(tmp_path, capsys):
    old, new = _mod(tmp_path, "old", version=None), _mod(tmp_path, "new", version=None)
    rc, out = _run(capsys, old, new, "--file", "content.xml")
    assert rc == 1
    assert "NOT IN THE COMPARISON" in out and "no differences" not in out, out


def test_detail_lists_the_manifest_rows_only_with_detail(tmp_path, capsys):
    old = _mod(tmp_path, "old", version="100")
    new = _mod(tmp_path, "new", version="101")
    row = "/content @version: 100 -> 101"
    _, plain = _run(capsys, old, new)
    assert row not in plain
    _, detailed = _run(capsys, old, new, "--detail")
    assert row in detailed


def test_a_nested_content_xml_is_still_an_ordinary_document(tmp_path, capsys):
    """Only the mod ROOT's manifest takes the manifest path; the guard is not a suffix."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    for d, v in ((old, "1"), (new, "2")):
        nested = d / "extensions" / "x"
        nested.mkdir(parents=True)
        (nested / "content.xml").write_text(f'<content id="x" version="{v}"/>', encoding="utf-8")
    rc, out = _run(capsys, old, new, "--file", "extensions/x/content.xml")
    assert rc == 0 and "@version: 1 -> 2" in out, out


# --- --file: every spelling reaches the comparison; "no differences" is only ever earned ----

@pytest.mark.parametrize("spelling", [
    "libraries/wares.xml", "./libraries/wares.xml", "libraries\\wares.xml",
    ".\\libraries\\wares.xml", "libraries//wares.xml", "LIBRARIES/WARES.XML"])
def test_file_flag_spellings_of_a_changed_file_reach_its_rows(tmp_path, capsys, spelling):
    old, new = _mod(tmp_path, "old", price="1"), _mod(tmp_path, "new", price="2")
    rc, out = _run(capsys, old, new, "--file", spelling)
    assert rc == 0
    assert "@price: 1 -> 2" in out, out
    assert "no differences" not in out


@pytest.mark.parametrize("spelling", ["libraries/wares.xml", "./libraries/wares.xml",
                                      "libraries\\wares.xml"])
def test_file_flag_on_a_genuinely_identical_file_is_no_differences(tmp_path, capsys, spelling):
    """The twin: refusing more must not cost the true answer."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    rc, out = _run(capsys, old, new, "--file", spelling)
    assert rc == 0
    assert f"{spelling}: no differences" in out, out


def test_file_flag_on_an_unreadable_file_spelled_differently_refuses(tmp_path, capsys):
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    (new / "libraries" / "wares.xml").write_text("<wares", encoding="utf-8")
    rc, out = _run(capsys, old, new, "--file", ".\\libraries\\wares.xml")
    assert rc == 1
    assert "NOT COMPARED" in out and "no differences" not in out, out


@pytest.mark.parametrize("name,body", [("libraries/s.xsd", '<xs a="{}"/>'),
                                       ("libraries/notes.txt", "note {}")])
@pytest.mark.parametrize("same", [True, False])
def test_file_flag_on_a_type_x4diff_does_not_compare_says_so(tmp_path, capsys, name, body, same):
    """Differing OR identical: x4diff read neither, so it can claim neither."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    (old / name).write_text(body.format(1), encoding="utf-8")
    (new / name).write_text(body.format(1 if same else 2), encoding="utf-8")
    rc, out = _run(capsys, old, new, "--file", name)
    assert rc == 1
    assert "NOT IN THE COMPARISON" in out and "does not compare" in out, out
    assert "no differences" not in out


@pytest.mark.parametrize("name", ["libraries/nope.xml", "./libraries/nope.xml",
                                  "libraries\\nope.xml", "libraries", "../old/libraries/wares.xml"])
def test_file_flag_on_a_path_neither_side_has_says_so(tmp_path, capsys, name):
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    rc, out = _run(capsys, old, new, "--file", name)
    assert rc == 1
    assert "NOT IN THE COMPARISON" in out and "no differences" not in out, out


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("sep", ["native", "forward"])
def test_file_flag_with_an_absolute_path_under_either_mod_reaches_its_rows(tmp_path, capsys, side, sep):
    old, new = _mod(tmp_path, "old", price="1"), _mod(tmp_path, "new", price="2")
    path = (old if side == "old" else new).resolve() / "libraries" / "wares.xml"
    rc, out = _run(capsys, old, new, "--file", path if sep == "native" else path.as_posix())
    assert rc == 0, out
    assert "@price: 1 -> 2" in out, out
    assert "NOT IN THE COMPARISON" not in out


def test_file_flag_with_an_absolute_path_to_a_file_one_side_has(tmp_path, capsys):
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    (old / "libraries" / "gone.xml").write_text("<gone/>", encoding="utf-8")
    rc, out = _run(capsys, old, new, "--file", (old / "libraries" / "gone.xml").resolve())
    assert rc == 0 and "REMOVED (present only in OLD)" in out, out


def test_file_flag_with_an_absolute_path_to_an_overlay_file(tmp_path, capsys):
    old, new, layer = _mod(tmp_path, "old"), _mod(tmp_path, "new"), _mod(tmp_path, "layer")
    (layer / "libraries" / "extra.xml").write_text("<extra/>", encoding="utf-8")
    rc, out = _run(capsys, old, new, "--overlay", layer, "--file", (layer / "libraries" / "extra.xml").resolve())
    assert rc == 0 and "REMOVED (present only in OLD)" in out, out


def test_file_flag_with_an_absolute_path_to_the_manifest(tmp_path, capsys):
    old, new = _mod(tmp_path, "old", version="100"), _mod(tmp_path, "new", version="101")
    rc, out = _run(capsys, old, new, "--file", (new / "content.xml").resolve())
    assert rc == 0, out
    assert "NOT IN THE COMPARISON" not in out and "no differences" not in out, out


def test_file_flag_with_an_absolute_path_to_an_identical_file_is_no_differences(tmp_path, capsys):
    """Twin: the mapping does not turn a true answer into a refusal, or print the folder."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    rc, out = _run(capsys, old, new, "--file", (new / "libraries" / "wares.xml").resolve())
    assert rc == 0
    assert "libraries/wares.xml: no differences" in out, out
    assert str(tmp_path.resolve()) not in out


@pytest.mark.parametrize("folder", ["other", "new2"], ids=["elsewhere", "sibling-sharing-a-prefix"])
def test_file_flag_with_an_absolute_path_outside_both_says_so(tmp_path, capsys, folder):
    old, new = _mod(tmp_path, "old", price="1"), _mod(tmp_path, "new", price="2")
    outside = _mod(tmp_path, folder)
    rc, out = _run(capsys, old, new, "--file", (outside / "libraries" / "wares.xml").resolve())
    assert rc == 1
    assert "NOT IN THE COMPARISON" in out and "no differences" not in out, out


def test_three_way_file_flag_with_an_absolute_path_reaches_the_listing(tmp_path, capsys):
    base = _mod(tmp_path, "base", price="1")
    arch, curr = _mod(tmp_path, "arch", price="2"), _mod(tmp_path, "curr", price="3")
    rc, relative = _run(capsys, arch, curr, "--base", base, "--file", "libraries/wares.xml")
    rc2, absolute = _run(capsys, arch, curr, "--base", base, "--file", (curr / "libraries" / "wares.xml").resolve())
    assert rc2 == rc and absolute == relative
    assert "libraries/wares.xml" in relative


def test_file_flag_on_a_one_sided_file_keeps_its_answer(tmp_path, capsys):
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    (new / "libraries" / "extra.xml").write_text("<extra/>", encoding="utf-8")
    rc, out = _run(capsys, old, new, "--file", ".\\libraries\\extra.xml")
    assert rc == 0 and "ADDED (present only in NEW)" in out, out


def test_file_flag_counts_an_overlay_layer_as_part_of_the_baseline(tmp_path, capsys):
    """A file only the --overlay layer ships is in the comparison, as REMOVED."""
    old, new = _mod(tmp_path, "old"), _mod(tmp_path, "new")
    layer = _mod(tmp_path, "layer")
    (layer / "libraries" / "extra.xml").write_text("<extra/>", encoding="utf-8")
    rc, out = _run(capsys, old, new, "--overlay", layer, "--file", "libraries/extra.xml")
    assert rc == 0 and "REMOVED (present only in OLD)" in out, out


def test_three_way_file_flag_spellings_reach_the_listing(tmp_path, capsys):
    base = _mod(tmp_path, "base", price="1")
    arch, curr = _mod(tmp_path, "arch", price="2"), _mod(tmp_path, "curr", price="3")
    rc, out = _run(capsys, arch, curr, "--base", base, "--file", ".\\libraries\\wares.xml")
    assert rc == 1, "a both-moved attribute is a decision"
    assert "base 1  ->  archived 2  |  upstream 3" in out, out


# --- the shared enumerator stays as it was --------------------------------------------------

def test_the_shared_enumerator_still_drops_the_manifest(tmp_path):
    """The store, BaseX and x4compat rely on this; the fix must not move it."""
    assert "content.xml" not in _modfiles.mod_xml_paths(_mod(tmp_path, "m"))


# --- three-way ------------------------------------------------------------------------------

def _three(tmp_path, base=None, archived=None, current=None):
    kw = lambda v: {"manifest": v} if v is not None and v.startswith("<") else {"version": v}
    return (_mod(tmp_path, "base", **kw(base or "100")),
            _mod(tmp_path, "arch", **kw(archived or "100")),
            _mod(tmp_path, "curr", **kw(current or "100")))


def test_three_way_reports_each_side_and_does_not_classify_it(tmp_path, capsys):
    """The version moves on nearly every release. Classifying it would make it BOTH-MOVED
    and turn every port into exit 1, so it is reported beside the buckets, not in them."""
    base, arch, curr = _three(tmp_path, "100", "101", "102")
    rc, out = _run(capsys, arch, curr, "--base", base)
    assert rc == 0, out
    assert _manifest_lines(out) == [
        "manifest content.xml base -> archived: differs (version 100->101)",
        "manifest content.xml base -> current: differs (version 100->102)"]
    assert "BOTH-MOVED ......... 0" in out
    assert "attributes classified              : 0" in out
    r = _threeway.three_way(base, arch, curr)
    assert r.manifest_archived.state == "differs" and r.manifest_current.state == "differs"


def test_three_way_identical_manifests_say_so(tmp_path, capsys):
    base, arch, curr = _three(tmp_path)
    rc, out = _run(capsys, arch, curr, "--base", base)
    assert rc == 0
    assert _manifest_lines(out) == ["manifest content.xml base -> archived: identical",
                                    "manifest content.xml base -> current: identical"]


@pytest.mark.parametrize("broken", ["base", "archived", "current"])
def test_three_way_unreadable_manifest_is_not_a_mechanical_port(tmp_path, capsys, broken):
    bad = "<content"
    base, arch, curr = _three(tmp_path, **{broken: bad})
    rc, out = _run(capsys, arch, curr, "--base", base)
    assert rc == 1, out
    assert any("NOT COMPARED" in ln for ln in _manifest_lines(out)), out


def test_three_way_one_sided_manifest_does_not_change_the_exit(tmp_path, capsys):
    base = _mod(tmp_path, "base")
    arch, curr = _mod(tmp_path, "arch", version=None), _mod(tmp_path, "curr")
    rc, out = _run(capsys, arch, curr, "--base", base)
    assert rc == 0
    assert "manifest content.xml base -> archived: present in BASE, absent from ARCHIVED" in out


def test_three_way_file_flag_shows_the_rows_and_drops_the_classified_absence_note(tmp_path, capsys):
    base, arch, curr = _three(tmp_path, "100", "101", "102")
    rc, out = _run(capsys, arch, curr, "--base", base, "--file", "content.xml")
    assert rc == 0
    assert "/content @version: 100 -> 101" in out and "/content @version: 100 -> 102" in out, out
    assert "no classified attribute" not in out


def test_three_way_file_flag_on_another_document_keeps_the_absence_note(tmp_path, capsys):
    """The control for the suppression above: it is scoped to the manifest."""
    base, arch, curr = _three(tmp_path)
    _, out = _run(capsys, arch, curr, "--base", base, "--file", "libraries/wares.xml")
    assert "no classified attribute" in out


def test_three_way_base_stack_says_only_its_first_layer_is_compared(tmp_path, capsys):
    base, arch, curr = _three(tmp_path)
    layer = _mod(tmp_path, "layer", version="555")
    _, out = _run(capsys, arch, curr, "--base", base, "--base", layer)
    lines = _manifest_lines(out)
    assert len(lines) == 2 and all(ln.startswith("manifest content.xml base -> ") for ln in lines)
    assert all(": identical" in ln and "base only" in ln and "not merged" in ln for ln in lines), out


def test_an_absolute_path_maps_to_the_innermost_compared_folder(tmp_path):
    """A folder compared inside another: the first root that contains the path used to win, so a file in the inner
    folder read as `sub/...` of the outer one and printed REMOVED."""
    from x4validate._diffcli import _vpath_under
    outer = tmp_path / "old"
    inner = outer / "extensions" / "sub"
    inner.mkdir(parents=True)
    target = inner / "md" / "a.xml"
    assert _vpath_under(str(target), [outer, inner]) == "md/a.xml"
    assert _vpath_under(str(target), [inner, outer]) == "md/a.xml"
    assert _vpath_under(str(outer / "md" / "b.xml"), [outer, inner]) == "md/b.xml"

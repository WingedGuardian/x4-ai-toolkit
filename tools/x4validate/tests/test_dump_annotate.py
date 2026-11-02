"""`x4effective dump --annotate`: per-node provenance for ANY vpath (incl. md/, aiscripts/).

`dump --chain` names which overlays touched a FILE. It cannot say which NODE a mod
changed, which is the question asked of a merged script ("which of these five mods
rewrote this do_if?"). The Recorder already captures that during the merge; these
tests pin the reader that turns it into comments in the printed tree.

The production change each test would catch is named in its docstring.
"""

from lxml import etree

from x4validate import _effectivecli, _merge
from x4validate._provenance import Origin, Recorder, annotate_tree


def _tree():
    return etree.fromstring(b"""<aiscript name="t">
  <actions>
    <do_if value="a" chance="50"><set_value name="$x" exact="1"/></do_if>
    <wait exact="1s"/>
  </actions>
</aiscript>""")


def _diff(body: bytes):
    return etree.fromstring(b"<diff>" + body + b"</diff>")


def _comments(tree):
    return [c.text for c in tree.iter(etree.Comment)]


def test_untouched_tree_gets_no_annotations():
    """Fails if annotate_tree annotates base nodes (e.g. drops the non-default check)."""
    tree, rec = _tree(), Recorder()
    header, n = annotate_tree(tree, rec)
    assert n == 0 and header == [] and _comments(tree) == []


def test_added_element_is_annotated_with_mod_op_and_line():
    """Fails if elem_created records are not read, or the comment omits mod/op/line."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<add sel="//actions"><stop_moving/></add>'),
                      recorder=rec, source="modA")
    header, n = annotate_tree(tree, rec)
    added = tree.xpath("//stop_moving")[0]
    prev = added.getprevious()
    assert n == 1
    assert isinstance(prev, etree._Comment), "annotation must sit directly before the node"
    assert "modA add:1" in prev.text


def test_descendant_of_added_subtree_is_not_annotated_again():
    """Fails if the 'own record' clause is replaced by elem_chain (ancestor fallback)."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<add sel="//actions"><do_all><wait/></do_all></add>'),
                      recorder=rec, source="modA")
    _, n = annotate_tree(tree, rec)
    inner = tree.xpath("//do_all/wait")[0]
    assert n == 1
    assert not isinstance(inner.getprevious(), etree._Comment)


def test_replaced_attribute_is_annotated_with_full_chain():
    """Fails if attribute records are not read, or the chain is truncated to the winner."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<replace sel="//do_if/@chance">80</replace>'),
                      recorder=rec, source="modA")
    _merge.apply_diff(tree, _diff(b'<replace sel="//do_if/@chance">90</replace>'),
                      recorder=rec, source="modB")
    _, n = annotate_tree(tree, rec)
    prev = tree.xpath("//do_if")[0].getprevious()
    assert n == 1
    assert "@chance" in prev.text
    assert "base -> modA replace-attr:1 -> modB replace-attr:1" in prev.text


def test_removed_node_is_reported_in_the_header():
    """Fails if Recorder.removed is not read: a removal leaves no node to annotate."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<remove sel="//wait"/>'), recorder=rec, source="modC")
    header, _ = annotate_tree(tree, rec)
    assert any("removed" in h and "modC remove:1" in h for h in header)


def test_own_record_ending_at_default_is_not_annotated():
    """Clause twin: fails if the 'winner is not the default origin' clause is dropped."""
    tree, rec = _tree(), Recorder()
    rec.elem_created(tree.xpath("//wait")[0], rec.default_origin)
    _, n = annotate_tree(tree, rec)
    assert n == 0 and _comments(tree) == []


def test_attribute_record_ending_at_default_is_not_annotated():
    """Clause twin: fails if the attribute 'winner is not the default origin' clause is dropped."""
    tree, rec = _tree(), Recorder()
    rec.attr_set(tree.xpath("//do_if")[0], "chance", rec.default_origin)
    _, n = annotate_tree(tree, rec)
    assert n == 0 and _comments(tree) == []


def test_root_record_goes_to_the_header_not_a_sibling():
    """Fails if the root is annotated with addprevious (lost when the root is printed)."""
    tree, rec = _tree(), Recorder()
    rec.elem_created(tree, Origin("modR", "replace", 3))
    header, n = annotate_tree(tree, rec)
    assert n == 1
    assert any("<aiscript>" in h and "modR replace:3" in h for h in header)


def test_comment_text_never_contains_a_double_hyphen():
    """Fails if a source name with '--' is written raw: '--' is illegal in an XML comment."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<add sel="//actions"><stop_moving/></add>'),
                      recorder=rec, source="my--mod")
    header, _ = annotate_tree(tree, rec)
    etree.fromstring(etree.tostring(tree))          # still well-formed
    assert all("--" not in c for c in _comments(tree))
    assert all("--" not in h for h in header)


def test_dump_cli_annotate_prints_node_provenance(tmp_path, monkeypatch, capsys):
    """Fails if --annotate is not wired into _cmd_dump (flag parsed but output unchanged)."""
    ref = tmp_path / "ref"
    (ref / "aiscripts").mkdir(parents=True)
    (ref / "aiscripts" / "t.xml").write_bytes(etree.tostring(_tree()))
    mod = tmp_path / "modA"
    (mod / "aiscripts").mkdir(parents=True)
    (mod / "aiscripts" / "t.xml").write_bytes(
        b'<diff><replace sel="//do_if/@chance">80</replace></diff>')
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *a, **k: [mod])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", "aiscripts/t.xml",
                             "--reference", str(ref), "--annotate"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "<!-- sources:" in out, "--annotate implies the file chain line"
    assert "modA replace-attr:1" in out


def test_dump_cli_without_annotate_prints_no_node_provenance(tmp_path, monkeypatch, capsys):
    """Twin: fails if annotation leaks into the default output (it must be opt-in)."""
    ref = tmp_path / "ref"
    (ref / "aiscripts").mkdir(parents=True)
    (ref / "aiscripts" / "t.xml").write_bytes(etree.tostring(_tree()))
    mod = tmp_path / "modA"
    (mod / "aiscripts").mkdir(parents=True)
    (mod / "aiscripts" / "t.xml").write_bytes(
        b'<diff><replace sel="//do_if/@chance">80</replace></diff>')
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *a, **k: [mod])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", "aiscripts/t.xml",
                             "--reference", str(ref)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "replace-attr" not in out


def test_whole_file_replace_is_named_in_the_header():
    """Review item 5: fails if a root replace leaves the tree looking like untouched vanilla
    (no node is annotated after full_override, so the header must say where they came from)."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<replace sel="/aiscript"><aiscript name="t2"><actions/></aiscript></replace>'),
                      recorder=rec, source="modR")
    header, _ = annotate_tree(tree, rec)
    assert any("unannotated nodes come from: modR replace-root:1" in h for h in header)


def test_removals_listed_before_a_whole_file_replace_are_flagged():
    """Review item 6: fails if a removal recorded before a whole-file replace is listed as if
    it still applied, with nothing saying it may predate the replace."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<remove sel="//wait"/>'), recorder=rec, source="modA")
    _merge.apply_diff(tree, _diff(b'<replace sel="/aiscript"><aiscript name="t2"><actions/></aiscript></replace>'),
                      recorder=rec, source="modR")
    header, _ = annotate_tree(tree, rec)
    assert any("may predate" in h and "modR replace-root:1" in h for h in header)


def test_attribute_removed_after_replace_is_not_annotated_as_present():
    """Review item 7: fails if an attribute the element no longer has is still annotated."""
    tree, rec = _tree(), Recorder()
    _merge.apply_diff(tree, _diff(b'<replace sel="//do_if/@chance">80</replace>'),
                      recorder=rec, source="modA")
    _merge.apply_diff(tree, _diff(b'<remove sel="//do_if/@chance"/>'), recorder=rec, source="modB")
    annotate_tree(tree, rec)
    assert all("@chance" not in c for c in _comments(tree))


def test_dump_sources_line_is_comment_safe(tmp_path, monkeypatch, capsys):
    """Review item 9: fails if a mod folder name containing '--' is printed raw into the
    sources comment, which makes the dumped XML not well-formed."""
    ref = tmp_path / "ref"
    (ref / "aiscripts").mkdir(parents=True)
    (ref / "aiscripts" / "t.xml").write_bytes(etree.tostring(_tree()))
    mod = tmp_path / "my--mod"
    (mod / "aiscripts").mkdir(parents=True)
    (mod / "aiscripts" / "t.xml").write_bytes(
        b'<diff><replace sel="//do_if/@chance">80</replace></diff>')
    monkeypatch.setattr(_effectivecli, "active_mods", lambda: [])
    monkeypatch.setattr(_effectivecli, "touchers_for", lambda *a, **k: [mod])
    rc = _effectivecli.main(["--db", str(tmp_path / "x.sqlite"), "dump", "aiscripts/t.xml",
                             "--reference", str(ref), "--annotate"])
    out = capsys.readouterr().out
    assert rc == 0
    for line in out.splitlines():
        if line.startswith("<!--"):
            assert "--" not in line[4:-3], line


def test_first_layer_with_no_base_is_the_sole_provider_not_a_replacement():
    """Fails if a mod-only file's header says it REPLACED a whole file, or flags later removals as possibly
    predating a replace when no earlier layer existed (the first layer had tree=None)."""
    rec = Recorder()
    tree, mode = _merge.apply_overlay(None, _tree(), "aiscripts/new.xml", "modOnly", recorder=rec)
    assert mode == "full"
    _merge.apply_diff(tree, _diff(b'<remove sel="//wait"/>'), recorder=rec, source="modB")
    header, _ = annotate_tree(tree, rec)
    joined = "\n".join(header)
    assert "modOnly" in joined and "sole provider" in joined and "no earlier layer" in joined
    assert "history before it is gone" not in joined and "may predate" not in joined
    assert any(h.startswith("removed:") and "modB" in h for h in header)


def test_a_full_override_of_an_existing_tree_still_says_whole_file_replace():
    """Twin of the sole-provider case: with a tree already in place the old wording stands."""
    rec = Recorder()
    _merge.apply_overlay(_tree(), _tree(), "aiscripts/t.xml", "modR", recorder=rec)
    header, _ = annotate_tree(_tree(), rec)
    joined = "\n".join(header)
    assert "whole-file replace" in joined and "sole provider" not in joined


def test_a_second_full_override_over_a_sole_provider_is_a_replace():
    """The sole-provider wording must not outlive a later layer that replaces that file."""
    rec = Recorder()
    tree, _m = _merge.apply_overlay(None, _tree(), "aiscripts/new.xml", "modOnly", recorder=rec)
    _merge.apply_overlay(tree, _tree(), "aiscripts/new.xml", "modLate", recorder=rec)
    header, _ = annotate_tree(tree, rec)
    joined = "\n".join(header)
    assert "modLate" in joined and "whole-file replace" in joined and "sole provider" not in joined


def test_nested_path_names_the_owner_of_the_unannotated_nodes(tmp_path):
    """Fails if a nested extensions/<owner>/... dump leaves its unannotated nodes reading as 'base': the owner's
    file is the base there and was never recorded."""
    owner = tmp_path / "ownermod"
    (owner / "aiscripts").mkdir(parents=True)
    (owner / "aiscripts" / "t.xml").write_bytes(etree.tostring(_tree()))
    patcher = tmp_path / "patcher"
    (patcher / "extensions" / "ownermod" / "aiscripts").mkdir(parents=True)
    (patcher / "extensions" / "ownermod" / "aiscripts" / "t.xml").write_bytes(
        b'<diff><replace sel="//do_if/@chance">80</replace></diff>')
    (tmp_path / "ref").mkdir()
    rec = Recorder()
    res = _merge.build_effective("extensions/ownermod/aiscripts/t.xml", _merge.Config(reference=tmp_path / "ref"),
                                 extra_overlays=[owner, patcher], recorder=rec)
    assert res.tree is not None
    header, n = annotate_tree(res.tree, rec)
    assert n == 1 and any("ownermod" in h and "owner" in h and "unannotated" in h for h in header), header
    assert rec.default_origin.op == "base"          # display-only: the recorded answers did not change

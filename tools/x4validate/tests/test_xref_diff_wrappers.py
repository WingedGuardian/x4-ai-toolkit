"""x4xref: a `<diff>` document's patch STRUCTURE is not script behaviour.

The `<diff>` root and its operation children (`add`, `replace`, `remove`) were indexed
as `action` rows. MEASURED per row on a real install (index rebuilt before and after):
2,524 structure rows removed -- diff 458, add 1,364, remove 47, replace 655 -- including
331 of the 690 rows the nested-patch fix added; the 1,456 `replace` rows that remain are
the schema's own string-replacement element. (Earlier counts of 3,580 and 1,439 were
counting slips: an arithmetic error, and distinct rows counted instead of rows.) Their
CHILDREN are the patched-in script and stay indexed,
with the enclosing cue taken from the op's `sel` only where the selector names it
unambiguously (see `_op_context`); otherwise the cue is left empty.

`add`, `replace` and `remove` are ALSO real MD/aiscript elements (READ via X4CodeSense
describe_element over md.xsd and aiscripts.xsd: `add`/`remove` hold existing_people /
matching_people; `replace` is a string/with sub-string replacement). `diff` is not an
md.xsd element. So the skip is positional -- the root's direct op children only --
never a tag in `_SKIP_TAGS`.
"""

from __future__ import annotations

from lxml import etree

from x4validate import _xref


def _rows(xml: str, vpath: str = "md/p.xml"):
    out: list = []
    _xref._walk(etree.fromstring(xml), "moda", vpath, out)
    return out


_OPS = {"diff", "add", "replace", "remove"}


def test_patch_structure_is_not_indexed():
    rows = _rows("""<diff>
      <add sel="//cue[@name='Host']/actions"><find_ship name="$s"/></add>
      <replace sel="//cue[@name='Host']/actions/set_value/@exact">2</replace>
      <remove sel="//cue[@name='Host']/actions/debug_text"/>
    </diff>""")
    assert not [r for r in rows if r.kind == "action" and r.name in _OPS], rows


def test_children_of_an_add_are_indexed_every_kind():
    rows = _rows("""<diff><add sel="//cues">
      <cue name="NewCue">
        <conditions><event_object_destroyed object="player.entity"/></conditions>
        <actions><find_ship name="$s"/><signal_cue cue="Other"/></actions>
      </cue></add></diff>""")
    kinds = {(r.kind, r.name) for r in rows}
    assert {("cuedef", "NewCue"), ("event", "event_object_destroyed"),
            ("action", "find_ship"), ("signal", "signal_cue")} <= kinds


def test_children_of_a_replace_are_indexed():
    rows = _rows("""<diff><replace sel="//cue[@name='Host']/actions/find_ship">
      <find_station name="$s"/></replace></diff>""")
    assert [(r.kind, r.name, r.cue) for r in rows] == [("action", "find_station", "Host")]


def test_enclosing_cue_comes_from_the_add_selector():
    rows = _rows("""<diff><add sel="//cue[@name='Outer']/cues/cue[@name='Host']/actions">
      <find_ship name="$s"/></add></diff>""")
    assert [(r.name, r.cue) for r in rows] == [("find_ship", "Host")]


def test_a_replaced_cue_is_enclosed_by_its_parent_cue():
    rows = _rows("""<diff><replace sel="//cue[@name='Outer']/cues/cue[@name='Inner']">
      <cue name="Inner"><actions><find_ship name="$s"/></actions></cue></replace></diff>""")
    assert [(r.kind, r.name, r.cue) for r in rows] == [
        ("cuedef", "Inner", "Outer"), ("action", "find_ship", "Inner")]


def test_a_sibling_add_is_enclosed_by_the_parent_cue_not_the_sibling():
    rows = _rows("""<diff><add sel="//cue[@name='P']/cues/cue[@name='Sib']" pos="after">
      <cue name="New"/></add></diff>""")
    assert [(r.name, r.cue) for r in rows] == [("New", "P")]


def test_twin_no_cue_in_the_selector_leaves_the_context_empty():
    rows = _rows("""<diff><add sel="/mdscript/cues"><cue name="Top"/></add></diff>""")
    assert [(r.name, r.cue) for r in rows] == [("Top", "")]


def test_twin_real_add_remove_replace_elements_are_still_indexed():
    """The schema's own add/remove/replace, inside a full script and inside patched
    content, are behaviour and keep their rows."""
    full = _rows("""<mdscript name="S"><cues><cue name="C"><actions>
      <add><matching_people/></add><remove><existing_people/></remove>
      <replace string="'a'" with="'b'"/>
    </actions></cue></cues></mdscript>""")
    assert {(r.name, r.cue) for r in full if r.kind == "action"} >= {
        ("add", "C"), ("remove", "C"), ("replace", "C")}
    patched = _rows("""<diff><add sel="//cue[@name='C']/actions">
      <add><matching_people/></add><replace string="'a'" with="'b'"/></add></diff>""")
    assert {(r.name, r.cue) for r in patched if r.kind == "action"} >= {
        ("add", "C"), ("replace", "C")}


def test_twin_an_aiscript_diff_is_handled_the_same():
    rows = _rows("""<diff><add sel="//attention/actions"><move_to object="$t"/></add></diff>""",
                 vpath="aiscripts/p.xml")
    assert [(r.kind, r.name) for r in rows] == [("action", "move_to")]


def test_twin_a_non_op_child_of_a_diff_is_still_indexed():
    """Not an op, so not patch structure we understand: indexed as before, never dropped."""
    rows = _rows("""<diff><find_ship name="$s"/></diff>""")
    assert [(r.kind, r.name) for r in rows] == [("action", "find_ship")]


# --- the enclosing cue: a NAME only when the selector states it unambiguously -----
#
# A wrong cue name is worse than an empty one: `cue X` would list the row under a cue
# it is not in. Each shape below was MEASURED wrong by a review probe of the first cut,
# which took the last `cue[@name=...]` text anywhere in the selector.

def _ctx(tag, sel, pos=None):
    op = etree.Element(tag)
    op.set("sel", sel)
    if pos:
        op.set("pos", pos)
    etree.SubElement(op, "cue", name="NEW")
    d = etree.Element("diff")
    d.append(op)
    rows: list = []
    _xref._walk(d, "m", "md/x.xml", rows)
    return [r.cue for r in rows if r.kind == "cuedef"][0]


import pytest  # noqa: E402


@pytest.mark.parametrize("tag,sel,pos,want", [
    # positives
    ("add", "//cue[@name='a']/actions", None, "a"),
    ("add", '//cue[@name="a"]/actions', None, "a"),
    ("add", "//cue[ @name = 'a' ]/actions", None, "a"),
    ("add", "/mdscript/cues/cue[@name='a']/cues/cue[@name='b']", None, "b"),
    ("add", "//cue[@name='p']//cue[@name='q']/actions", None, "q"),
    ("add", "//cue[@name='a']", "prepend", "a"),
    ("add", "//cue[@name='a']/@checkinterval", None, "a"),
    ("replace", "/mdscript/cues/cue[@name='a']/cues/cue[@name='b']", None, "a"),
    ("replace", "//cue[@name='a']/conditions/check_value/@value", None, "a"),
    ("add", "//cue[@name='p']/cues/cue[@name='a']", "after", "p"),
    ("replace", "//cue[@name='p']/cues/cue[@name='a'][1]", None, "p"),
    ("add", "//cue[@name='p']/cues/cue[@name='a'][1]", "after", "p"),
    # no cue, or no PARENT cue, named
    ("add", "/mdscript/cues", None, ""),
    ("add", "//cue[@name='a']", "before", ""),
    ("add", "//library[@name='lib']/actions", None, ""),
    # ambiguous: empty, never a wrong name
    ("add", "//cue[@name='a' or @name='b']/actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[@name='a' or @name='b']/actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[starts-with(@name,'x')]/actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[2]/actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[@name='a']/cues/cue[not(@name)]/actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[@name='a'][1]/actions", None, ""),
    ("add", "//cue[@name='p']//actions", None, ""),
    ("add", "//cue[@name='p']/cues/cue[@name='a']/..", None, ""),
    ("add", "//cue[@name='p']/cues/cue[@name='a']/../cue[@name='b']", "after", ""),
    # the REPLACED or flanked node reached through `//` may sit in a nested cue, so its
    # parent is not known (MEASURED on real data: the first fix named the outer cue here)
    ("replace", "//cue[@name='a']//do_if[@value='x']", None, ""),
    ("add", "//cue[@name='a']//do_if[@value='x']", "after", ""),
    ("replace", "//cue[@name='p']//cue[@name='a']", None, ""),
    # twin: a `/`-reached replaced node keeps the named cue
    ("replace", "//cue[@name='a']/actions/do_if[@value='x']", None, "a"),
    ("add", "//cue[@name='a']/actions/do_if[@value='x']", "before", "a"),
    # a union or a parenthesised expression is not one location path: refuse
    ("add", "//cue[@name='a']/actions | //cue[@name='b']/actions", None, ""),
    ("add", "(//cue[@name='a']/actions)[1]", None, ""),
    ("add", "//cue[@name='a|b']/actions", None, "a|b"),          # a pipe inside quotes
    ("add", "//cue[@name='a/b']/actions", None, "a/b"),          # a slash inside quotes
    ("add", "//cue[@name='x]y']/actions", None, "x]y"),          # a bracket inside quotes
])
def test_op_context_names_a_cue_only_when_unambiguous(tag, sel, pos, want):
    assert _ctx(tag, sel, pos) == want


# --- review delta 5: after the named cue, only WHITELISTED steps -----------------
# Every remaining step must be a plain child step naming a literal element other than
# `cue` (predicates allowed). `*`, node(), an axis, `.`, `..` or `//` give "".

@pytest.mark.parametrize("tag,sel,pos,want", [
    ("add", "cue[@name='a']/cues/*[@name='b']/actions", None, ""),
    ("add", "cue[@name='a']/cues/*/actions", None, ""),
    ("add", "cue[@name='a']/cues/*", None, ""),
    ("add", "//cue[@name='a']/cues/*[1]/conditions", None, ""),
    ("add", "//cue[@name='a']/*/*/actions", None, ""),
    ("add", "//cue[@name='a']/cues/cue[@name='b']/conditions", None, "b"),
    ("add", "//cue[@name='a']/cues/self::cue/actions", None, ""),
    ("add", "//cue[@name='a']/cues/child::cue[@name='b']/actions", None, ""),
    ("add", "//cue[@name='a][x']/actions", None, "a][x"),
    ("add", "//cue[@name=\"a'b\"]/actions", None, "a'b"),
    ("add", "//cue[@name='a'][@instantiate='true']/actions", None, ""),
    ("add", "//cue[ @name = 'a' ]/actions", None, "a"),
    ("add", "//cue [@name='a']/actions", None, "a"),
    ("add", "//*[@name='a']/actions", None, ""),
    ("add", "//cue[@name='a']/actions | //cue[@name='b']/actions", None, ""),
    ("add", "(//cue[@name='a'])[1]/actions", None, ""),
    ("add", "//cue[@name='a']/actions/do_if[@value='$x/y']", None, "a"),
    ("add", "//cue[@name='a']/actions/do_if[@value='a|b']", None, "a"),
    ("add", "//cue[@name='a']/cues/cue[@name='b']/descendant-or-self::actions", None, ""),
    ("add", "//cue[@name='a']/cues/node()/actions", None, ""),
    ("add", "//cue[@name='a']/cues/Cue[@name='b']/actions", None, ""),
    ("add", "//cue[@name='a']/following-sibling::cue[1]/actions", None, ""),
    ("replace", "//cue[@name='a']/cues/*[@name='b']/actions", None, ""),
    ("add", "//cue[@name='a']/cues/*[@name='b']", "after", "a"),
    ("add", "//cue[@name='a']/cues/cue[@name='b']/cues/*", None, ""),
    ("add", "//cue[@name='a']/cues/cue[@name='b']/", None, "b"),
    ("add", "//cue[@name='a']/.", None, ""),
    ("add", "//cue[@name='a']/cues/cue[@name='b']/conditions[.//x]/../..", None, ""),
    # an attribute step only as the LAST step; the dropped (replaced/flanked) step
    # must itself be a plain child step
    ("add", "//cue[@name='a']/@checkinterval/actions", None, ""),
    ("replace", "//cue[@name='a']/cues/cue[@name='b']/..", None, ""),
    ("replace", "//cue[@name='a']/cues/self::cue", None, ""),
    ("add", "//cue[@name='a']/cues/node()", "before", ""),
])
def test_op_context_whitelist_after_the_named_cue(tag, sel, pos, want):
    assert _ctx(tag, sel, pos) == want

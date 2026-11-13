"""x4xref: a `<diff>` document's patch STRUCTURE is not script behaviour.

The `<diff>` root and its operation children (`add`, `replace`, `remove`) were indexed
as `action` rows -- MEASURED on a real install: 3,580 such rows, and 331 of the 690 rows
the nested-patch fix added. Their CHILDREN are the patched-in script and stay indexed,
with the enclosing cue taken from the op's `sel` where the selector names one.

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

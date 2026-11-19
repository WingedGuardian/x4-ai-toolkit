"""cross_tool maps a HARD row on a COMPONENT file to the store's key, by the store's own flattener.

4.3 (F387) made x4compat report per-connection HARD rows on component files (one mod's own remove-and-re-add of
connections over another mod's `@tags` edits). cross_tool's `_entity_of` knew only macros and library registries, so
every such row fell into UNMAPPED_KIND and pushed the HARD section over its 10% coverage floor (MEASURED 2026-11-19 on
the live install: 44 of 169 mappable HARD rows unmapped, section CANNOT). The store DOES key component connections
(`connection[<name>].<attr>`, `_effective.extract_components`), so those rows are comparable.
"""
from __future__ import annotations

import sqlite3

from lxml import etree

from conftest import import_gate

ct = import_gate("cross_tool")
from x4validate import _effective  # noqa: E402

DOC = b"""<components>
  <component name="ship_k" class="ship_xl">
    <connections>
      <connection name="con_a" tags="turret large standard combat" group="g1"><offset/></connection>
      <connection name="con_b" tags="shield medium"/>
      <connection name="con_b" tags="shield medium"/>
    </connections>
  </component>
</components>"""

VP = "assets/units/size_xl/ship_k.xml"


def _tree():
    return etree.fromstring(DOC)


def _store(*names):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, vpath TEXT)")
    for n in names:
        con.execute("INSERT INTO entities (kind, name, vpath) VALUES ('component', ?, ?)", (n, VP))
    return con


def _store_keys():
    """Every key the STORE writes for the fixture, from the store's own extractor."""
    class _Rec:
        def attr_chain(self, el, attr):
            return []

        def elem_chain(self, el):
            return []

        def winner(self, el):
            return type("W", (), {"source": "base"})()
    ents = _effective.extract_components(_tree(), VP, _Rec())
    return {p for e in ents for p, *_ in e.attrs}


def test_a_connection_under_a_stored_component_maps_to_its_store_key():
    t = _tree()
    node = t.find(".//connection[@name='con_a']")
    ent = ct._entity_of(_store("ship_k"), VP, node)
    assert ent[:2] == ("component", "ship_k"), ent
    key = ct._store_key(ent[2], "component", node)
    assert key == "connection[con_a]"
    assert f"{key}.tags" in _store_keys()          # the key really is one the store wrote


def test_a_repeated_connection_name_takes_the_stores_ordinal():
    t = _tree()
    second = t.findall(".//connection[@name='con_b']")[1]
    ent = ct._entity_of(_store("ship_k"), VP, second)
    assert ct._store_key(ent[2], "component", second) == "connection[con_b#1]"
    assert "connection[con_b#1].tags" in _store_keys()


def test_the_component_element_itself_is_the_entity():
    t = _tree()
    comp = t.find("component")
    ent = ct._entity_of(_store("ship_k"), VP, comp)
    assert ct._store_key(ent[2], "component", comp) == ""


def test_twin_a_node_the_store_does_not_walk_has_no_key():
    """`<offset>` is geometry: extract_components keys connections only, so there is no store key (NO_STORE_KEY)."""
    t = _tree()
    off = t.find(".//connection[@name='con_a']/offset")
    ent = ct._entity_of(_store("ship_k"), VP, off)
    assert ct._store_key(ent[2], "component", off) is None


def test_twin_a_component_missing_from_the_store_is_removed_not_unmapped():
    t = _tree()
    node = t.find(".//connection[@name='con_a']")
    assert ct._entity_of(_store("other"), VP, node) == ct.ENTITY_REMOVED


def test_twin_a_kind_still_without_a_flattening_stays_unmapped():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, vpath TEXT)")
    # A kind neither a macro, a component nor a library registry (every real one is one of those today).
    con.execute("INSERT INTO entities (kind, name, vpath) VALUES ('notakind', 'x', ?)", (VP,))
    node = _tree().find(".//connection[@name='con_a']")
    assert ct._entity_of(con, VP, node) == ct.UNMAPPED_KIND

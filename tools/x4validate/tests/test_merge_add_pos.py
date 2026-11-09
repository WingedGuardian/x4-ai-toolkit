"""<add pos=> outside diff.xsd's enumeration is refused, never modelled as an append.

MEASURED 2026-11-08 (X4 9.0, debug.txt of a diagnostic mod): the engine logged
"Invalid pos attribute 'append' ... Skipping node" for 11 ops (12 lines: one per child)
and added nothing. _merge used to send any unknown pos to its default-append arm, so
static validation modelled a tree the game never built. READ reference/libraries/diff.xsd
:48-53: pos is before | after | prepend; an ABSENT pos appends.
Corpus census the same day (233 installed mods, 5,772 <add> ops): pos absent 4,723,
after 463, before 459, prepend 116, append 11 (all in that one diagnostic mod).
"""
from __future__ import annotations

import pytest
from lxml import etree

from x4validate import _merge

BASE = "<r><a><k/></a></r>"


def _apply(diff: str):
    tree = _merge.parse_bytes(BASE.encode("utf-8"))
    ops = _merge.apply_diff(tree, _merge.parse_bytes(diff.encode("utf-8")))
    return etree.tostring(tree), ops


@pytest.mark.parametrize("pos", ["append", "", "Prepend", "middle", "last"])
@pytest.mark.parametrize("payload", ["<n/>", "<n/><m/>", "T"])
def test_an_invalid_pos_is_refused_and_changes_nothing(pos, payload):
    out, ops = _apply(f'<diff><add sel="//a" pos="{pos}">{payload}</add></diff>')
    assert len(ops) == 1 and not ops[0].ok
    assert repr(pos) in ops[0].detail and "skips" in ops[0].detail
    assert out == BASE.encode()


@pytest.mark.parametrize("pos,want", [
    (None, b"<r><a><k/><n/></a></r>"),
    ("prepend", b"<r><a><n/><k/></a></r>"),
    ("before", b"<r><n/><a><k/></a></r>"),
    ("after", b"<r><a><k/></a><n/></r>"),
])
def test_the_valid_positions_still_apply(pos, want):
    attr = "" if pos is None else f' pos="{pos}"'
    out, ops = _apply(f'<diff><add sel="//a"{attr}><n/></add></diff>')
    assert ops[0].ok, ops[0].detail
    assert out == want


def test_an_invalid_pos_on_an_attribute_add_is_refused_too():
    # The engine reads pos before it looks at type=; refusing here is INFERRED from the
    # skipped-node message, not measured for type="@...". 0 corpus ops combine them.
    out, ops = _apply('<diff><add sel="//a" type="@x" pos="append">1</add></diff>')
    assert not ops[0].ok and out == BASE.encode()


def test_one_refused_op_does_not_stop_the_next():
    out, ops = _apply('<diff><add sel="//a" pos="append"><n/></add>'
                      '<add sel="//a"><m/></add></diff>')
    assert [o.ok for o in ops] == [False, True]
    assert out == b"<r><a><k/><m/></a></r>"

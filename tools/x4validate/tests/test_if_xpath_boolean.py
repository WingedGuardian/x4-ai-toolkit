"""`if=` is an XPath expression, so its verdict is XPath `boolean()`, not Python truthiness.

The two agree on every result type lxml returns EXCEPT one: a number that is NaN.
`bool(float('nan'))` is True in Python, but XPath 1.0 section 4.3 says a number is
true "if and only if it is neither positive or negative zero nor NaN". So a guard
like `if="number(@missing)"` -- false to the engine -- was modelled as OPEN and the
op applied, producing an effective tree the game never has.

One test per clause of boolean() (boolean, number, string, node-set), each with a
true and a false twin, plus the refusal for a type that is none of those.
"""

from __future__ import annotations

import math

import pytest
from lxml import etree

from x4validate import _merge


def _apply(cond: str):
    tree = etree.fromstring(b'<wares><ware id="a" price="1"/></wares>')
    diff = etree.fromstring(
        ('<diff><replace sel="/wares/ware[@id=\'a\']/@price" if="%s">9</replace></diff>'
         % cond).encode())
    ops = _merge.apply_diff(tree, diff)
    return tree.find("ware").get("price"), ops[0]


@pytest.mark.parametrize("cond", ["number('x')", "number(/wares/ware/@nope)", "0 div 0"])
def test_a_NaN_guard_is_FALSE_and_the_op_is_skipped(cond):
    price, op = _apply(cond)
    assert price == "1", f"if={cond} (NaN) applied the op"
    assert op.skipped_if and op.ok


@pytest.mark.parametrize("cond", ["0", "-0", "0.0", "1 - 1"])
def test_a_zero_guard_is_false(cond):
    price, op = _apply(cond)
    assert price == "1" and op.skipped_if


@pytest.mark.parametrize("cond", ["1", "-2.5", "1 div 0", "-1 div 0", "count(/wares/ware)"])
def test_a_nonzero_guard_including_infinity_is_true(cond):
    price, op = _apply(cond)
    assert price == "9" and not op.skipped_if


@pytest.mark.parametrize("cond,want", [("string('')", "1"), ("''", "1"),
                                       ("'x'", "9"), ("'false'", "9"),
                                       ("string(/wares/ware/@id)", "9")])
def test_a_string_guard_is_true_iff_non_empty(cond, want):
    assert _apply(cond)[0] == want, cond


@pytest.mark.parametrize("cond,want", [("/wares/nope", "1"), ("/wares/ware", "9"),
                                       ("/wares/ware/@id", "9"), ("/wares/ware/@nope", "1"),
                                       ("not(/wares/ware)", "1")])
def test_a_node_set_guard_is_true_iff_non_empty(cond, want):
    assert _apply(cond)[0] == want, cond


@pytest.mark.parametrize("cond,want", [("true()", "9"), ("false()", "1")])
def test_a_boolean_guard_is_itself(cond, want):
    assert _apply(cond)[0] == want, cond


def test_the_helper_directly_per_type():
    b = _merge._xpath_boolean
    assert b(True) is True and b(False) is False
    assert b(1.0) is True and b(math.inf) is True
    assert b(0.0) is False and b(-0.0) is False and b(math.nan) is False
    assert b("x") is True and b("") is False
    assert b([object()]) is True and b([]) is False


def test_an_UNKNOWN_result_type_is_refused_not_guessed():
    with pytest.raises(TypeError, match="if="):
        _merge._xpath_boolean(object())
    with pytest.raises(TypeError):
        _merge._xpath_boolean(None)


def test_apply_diff_reports_an_unknown_if_type_as_invalid(monkeypatch):
    def boom(_value):
        raise TypeError("if= evaluated to an unsupported type")
    monkeypatch.setattr(_merge, "_xpath_boolean", boom)
    price, op = _apply("1")
    assert price == "1" and not op.ok and op.detail.startswith("invalid if=: ")

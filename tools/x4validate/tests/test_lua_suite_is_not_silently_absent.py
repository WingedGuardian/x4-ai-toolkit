"""The Lua contract suite may not go dormant behind a single skip.

`tests/test_modlua_rearm.py` opens with a module-level
`lupa = pytest.importorskip("lupa", ...)`, which is the right call for a machine that
genuinely cannot build it -- but it puts **211 tests** behind ONE skip entry.

MEASURED 2026-09-20 with a 5-test probe: a module-level `importorskip` registers exactly
one entry in `terminalreporter.stats["skipped"]`. `X4_MAX_SKIPS` (per OS, set in `ci.yml`)
therefore absorbs the entire file going dormant, and `ci.yml` deliberately pins no pass
count, so nothing else would notice. Among those 211 are the only tests of the release's
first WRITE verbs -- `pause` / `unpause`, which mutate a running game: ownership,
read-back-vs-intention, advisory wrapping, refusal reasons.

That is the shape `X4_MAX_SKIPS` exists to catch (125 tests dormant behind a green tick),
reached by a route the ceiling cannot see. A lupa import or ABI break -- a Python bump is
the realistic one -- would silence the lot with CI green.

So the absence is asserted HERE, outside that module, where it cannot be skipped away.
Set `X4_ALLOW_NO_LUPA=1` to accept the gap deliberately; then it is a decision with a name
rather than a silence.
"""
from __future__ import annotations

import os

import pytest


def test_the_lua_contract_suite_is_not_silently_absent():
    if os.environ.get("X4_ALLOW_NO_LUPA") == "1":
        pytest.skip("X4_ALLOW_NO_LUPA=1: the Lua contract gap is accepted deliberately")
    try:
        import lupa  # noqa: F401
    except ImportError as exc:  # pragma: no cover - the whole point is that CI has it
        raise AssertionError(
            "lupa is not importable, so tests/test_modlua_rearm.py's 211 tests are skipped "
            "as ONE entry that X4_MAX_SKIPS absorbs -- including the only coverage of the "
            "write verbs (pause/unpause) against a running game. Install it (`uv sync`), or "
            "set X4_ALLOW_NO_LUPA=1 to accept the gap deliberately."
        ) from exc


def test_the_write_verb_contract_tests_are_where_this_claims_they_are():
    """A population check, not a pass-by-absence: if the write-verb tests move or are
    renamed, this guard would keep passing while guarding nothing."""
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "test_modlua_rearm.py")
    assert os.path.isfile(target), "test_modlua_rearm.py is gone; this guard is now inert"
    text = open(target, encoding="utf-8").read()
    assert 'importorskip("lupa.luajit21"' in text, (
        "test_modlua_rearm.py no longer skips on lupa; if the dependency became hard, "
        "delete this guard rather than leaving it to pass for the wrong reason")
    for verb in ("pause", "unpause"):
        assert verb in text, f"no {verb} coverage found in test_modlua_rearm.py"

r"""The BaseX tests must actually RUN, not merely exist.

THE GAP THIS CLOSES (MEASURED 2026-08-22). `tools/basex/` ships `test_ask.py` and
`test_staleness.py` — **23 tests that pass** — and `pyproject.toml` sets
`testpaths = ["tests"]`, so the main suite never collected one of them. It
reported "593 passed" and said nothing whatsoever about 23 tests it had not
looked at.

That is this register's founding shape, in the test runner itself: a step that
narrows the population and reports success anyway. And the two files are not
incidental — `ask.py` is what REFUSES to render a zero-result as a finding
without a coverage denominator, and `staleness.py` is the freshness contract.
A silent regression in either would remove a guard while every gate stayed green.

Why a subprocess rather than adding `../basex` to `testpaths`. The original
reason was that BaseX was dev-only and absent from the public bundle; that stopped
being true in v2.6.0, which vendors it. The mechanism stays, for a reason that was
always the stronger one: these tests are cwd-sensitive and manipulate `sys.path`
themselves (`test_staleness.py` inserts its own directory before importing), so
collecting them through a shared `testpaths` would run them from the wrong working
directory. A subprocess with `cwd=BASEX` is what they are written against.

BaseX now ships, so the skip below is a safety net rather than the normal path --
it still fires if someone prunes the optional component. Present → run it;
absent → SKIP with a reason, so "not checked here" can never read as
"checked and fine".
"""

import subprocess
import sys
from pathlib import Path

import pytest

BASEX = Path(__file__).resolve().parent.parent.parent / "basex"
TEST_FILES = ("test_ask.py", "test_ask_e2e.py", "test_build_scripts_refuse_empty_reference.py",
              "test_coverage.py", "test_preflight.py", "test_staleness.py",
    "test_unknown_is_not_stale.py", "test_x4v_tree.py")


def test_the_basex_tests_pass():
    if not BASEX.is_dir():
        pytest.skip(f"no BaseX tooling at {BASEX} — it ships, so this means a pruned checkout")
    present = [f for f in TEST_FILES if (BASEX / f).is_file()]
    if not present:
        pytest.skip(f"BaseX tooling present at {BASEX} but ships no test files")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *present],
        cwd=BASEX, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        f"the BaseX test suite FAILED ({' '.join(present)}). It is not collected by "
        f"this suite's `testpaths`, so nothing else would have told you.\n\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-1500:]}")


def test_the_expected_basex_test_files_still_exist():
    """Tripwire against the check above quietly becoming a no-op: if a file is
    renamed, the runner would pass over a shrinking set and report green."""
    if not BASEX.is_dir():
        pytest.skip(f"no BaseX tooling at {BASEX} — it ships, so this means a pruned checkout")
    missing = [f for f in TEST_FILES if not (BASEX / f).is_file()]
    assert not missing, (
        f"expected BaseX test file(s) are gone: {missing}. Either they were renamed "
        f"(update TEST_FILES) or deleted (say so deliberately) — but a shrinking test "
        f"set must never be silent.")


def test_no_basex_test_file_is_unlisted():
    """The other direction, and the one that actually bit (2026-08-24).

    `TEST_FILES` is a hand-maintained tuple, and the tripwire above only pins a
    SHRINKING set. Adding `test_preflight.py` to `tools/basex/` therefore created
    22 tests that passed when run by hand and were never collected by anything —
    the precise orphan this module exists to prevent, reintroduced through the
    module's own blind spot.

    A guard that catches removals but not additions has a denominator taken from
    itself: it can only ever be as complete as the list it is checking.
    """
    if not BASEX.is_dir():
        pytest.skip(f"no BaseX tooling at {BASEX} — it ships, so this means a pruned checkout")
    on_disk = sorted(p.name for p in BASEX.glob("test_*.py"))
    unlisted = [f for f in on_disk if f not in TEST_FILES]
    assert not unlisted, (
        f"BaseX test file(s) present but NOT in TEST_FILES: {unlisted}. They are "
        f"not collected by this suite's `testpaths`, so nothing runs them and "
        f"nothing reports that nothing ran them. Add them to TEST_FILES.")

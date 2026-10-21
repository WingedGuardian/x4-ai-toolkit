"""The skip ceiling itself was never pinned by a test -- which is how an EMPTY
X4_MAX_SKIPS came to print "that is a NON-ANSWER, not a pass" and then leave the run
GREEN. The sentence was right and the exit code contradicted it.

The realistic accident is not garbage but emptiness: a workflow writing
`X4_MAX_SKIPS: ${{ <expression> }}` where the expression evaluates to nothing sets
the variable to "", which is not None -- so the CI leg loses its ceiling while every
log line still says the suite ran with one. `X4_MAX_SKIPS` exists because 125 tests
sat dormant for weeks behind a green tick; a ceiling that silently does not apply is
that same failure with an extra step.

Unit-tested against the REAL conftest function (loaded by path, not a copy), plus one
subprocess case proving pytest actually wires the exit status through.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
CONFTEST = TESTS / "conftest.py"


def _conftest_module():
    spec = importlib.util.spec_from_file_location("_x4_conftest_under_test", CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeSession:
    exitstatus = 0


class _FakeReporter:
    def __init__(self, n_skipped):
        self.stats = {"skipped": [object()] * n_skipped}
        self.lines = []
        self._session = _FakeSession()

    def write_line(self, s):
        self.lines.append(s)


def _run(monkeypatch, cap, n_skipped=3):
    mod = _conftest_module()
    if cap is None:
        monkeypatch.delenv("X4_MAX_SKIPS", raising=False)
    else:
        monkeypatch.setenv("X4_MAX_SKIPS", cap)
    tr = _FakeReporter(n_skipped)
    mod.pytest_terminal_summary(tr, 0, None)
    return tr


def test_an_EMPTY_ceiling_FAILS_rather_than_passing_silently(monkeypatch):
    tr = _run(monkeypatch, "")
    assert tr._session.exitstatus == 1
    assert any("not a number" in l for l in tr.lines)


def test_a_GARBAGE_ceiling_FAILS(monkeypatch):
    tr = _run(monkeypatch, "fifty")
    assert tr._session.exitstatus == 1


def test_a_NEGATIVE_ceiling_FAILS_because_nothing_could_satisfy_it(monkeypatch):
    """A ceiling no run can meet is a broken instrument, not a strict one -- and
    it would otherwise fire on every run and be turned off."""
    tr = _run(monkeypatch, "-1")
    assert tr._session.exitstatus == 1
    assert any("negative" in l for l in tr.lines)


def test_EXCEEDING_the_ceiling_still_FAILS(monkeypatch):
    tr = _run(monkeypatch, "2", n_skipped=3)
    assert tr._session.exitstatus == 1
    assert any("exceeds" in l for l in tr.lines)


def test_a_ceiling_that_is_MET_passes(monkeypatch):
    """The twin. Without it every test above passes on a function that always fails."""
    tr = _run(monkeypatch, "3", n_skipped=3)
    assert tr._session.exitstatus == 0
    assert not any("FAIL" in l for l in tr.lines)


def test_ZERO_is_a_real_ceiling_and_not_confused_with_unset(monkeypatch):
    """`0` is falsy; an implementation testing truthiness rather than `is None`
    would silently drop the strictest ceiling there is."""
    assert _run(monkeypatch, "0", n_skipped=1)._session.exitstatus == 1
    assert _run(monkeypatch, "0", n_skipped=0)._session.exitstatus == 0


def test_UNSET_applies_no_ceiling(monkeypatch):
    """Opt-in by design: a fixed ceiling would be wrong, because a cold machine
    legitimately skips more than a configured one."""
    tr = _run(monkeypatch, None, n_skipped=99)
    assert tr._session.exitstatus == 0


@pytest.mark.parametrize("cap, expected", [("", 1), ("0", 0)])
def test_the_exit_status_really_reaches_PYTEST(tmp_path, cap, expected):
    """The unit tests above drive the function directly, so they cannot prove pytest
    calls it or honours `_session.exitstatus`. One subprocess run does -- and it is
    the wiring, not the logic, that the empty-string hole actually lived in."""
    root = tmp_path / "toolkit"
    cases = root / "tools" / "x4validate" / "tests"
    cases.mkdir(parents=True)
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("x4pytest.py", "x4temp.py"):
        (scripts / name).write_bytes((CONFTEST.parents[3] / "scripts" / name).read_bytes())
    t = cases / "test_one.py"
    t.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (cases / "conftest.py").write_bytes(CONFTEST.read_bytes())
    env = {**__import__("os").environ, "X4_MAX_SKIPS": cap}
    env.pop("PYTHONPATH", None)  # the fixture must supply its own production bootstrap
    r = subprocess.run([sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:cacheprovider"],
                       cwd=cases, env=env, capture_output=True, text=True)
    assert r.returncode == expected, r.stdout[-2000:] + r.stderr[-2000:]
    assert "1 passed" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
    assert ("not a number" in r.stdout) == (cap == ""), r.stdout[-2000:]

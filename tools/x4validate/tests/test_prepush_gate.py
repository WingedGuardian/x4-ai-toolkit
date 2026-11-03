"""scripts/prepush-gate.py: a base it cannot resolve is a refusal, never "0 changed files, passed"."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from _layout import require_repo

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "prepush-gate.py"


def _module():
    require_repo("scripts/prepush-gate.py", why="the maintainers' pre-push gate is not shipped")
    spec = importlib.util.spec_from_file_location("prepush_gate_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prepush_gate_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    r = tmp_path / "checkout"
    (r / "tools" / "x4validate" / "tests").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "first")
    return r


@pytest.fixture
def stepless(monkeypatch):
    """Every step passes without running anything, and uv counts as present."""
    gate = _module()
    real = subprocess.run
    ran = []

    def run(cmd, *a, **k):
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real(cmd, *a, **k)
    monkeypatch.setattr(gate.subprocess, "run", run)
    monkeypatch.setattr(gate.Gate, "step", lambda self, name, cmd, cwd=None, check=None: ran.append(name) or True)
    return gate, ran


@pytest.mark.parametrize("bad", ["no-such-rev", "0000000000000000000000000000000000000000", "HEAD~99"])
def test_a_bad_base_refuses_before_any_step(checkout, stepless, capsys, bad):
    gate, ran = stepless
    assert gate.main(["--root", str(checkout), "--base", bad]) == 2
    assert bad in capsys.readouterr().out
    assert ran == []


def test_a_good_base_goes_on_to_the_steps(checkout, stepless):
    gate, ran = stepless
    (checkout / "b.txt").write_text("b")
    _git(checkout, "add", "b.txt")
    _git(checkout, "commit", "-q", "-m", "second")
    assert gate.main(["--root", str(checkout), "--base", "HEAD~1"]) == 0
    assert ran


def test_no_base_at_all_says_the_changed_file_tests_did_not_run(checkout, stepless, capsys):
    gate, ran = stepless
    _git(checkout, "branch", "-m", "feature")                   # no upstream, and no master to take a merge-base with
    assert gate.main(["--root", str(checkout)]) == 0            # a first push with no upstream still runs the rest
    assert "NOT CHECKED" in capsys.readouterr().out


def test_a_failing_diff_is_a_refusal_not_an_empty_list(checkout, stepless, capsys, monkeypatch):
    gate, ran = stepless
    real_git = gate._git_result

    def broken(*a):
        if a[0] == "diff":
            return subprocess.CompletedProcess(a, 128, stdout="", stderr="fatal: bad object")
        return real_git(*a)
    monkeypatch.setattr(gate, "_git_result", broken)
    assert gate.main(["--root", str(checkout), "--base", "HEAD"]) == 2
    assert ran == []


def test_the_gate_is_not_shipped_and_nothing_that_ships_needs_it():
    require_repo(".git", "scripts/prepush-gate.py", why="asks git for the export rule")
    r = subprocess.run(["git", "-C", str(REPO), "check-attr", "export-ignore", "--", "scripts/prepush-gate.py"],
                       capture_output=True, text=True, check=True)
    assert r.stdout.strip().endswith("set"), r.stdout
    listed = subprocess.run(["git", "-C", str(REPO), "grep", "-l", "prepush-gate", "--", ":(exclude)scripts/prepush-gate.py",
                             ":(exclude)tools/x4validate/tests/test_prepush_gate.py"],
                            capture_output=True, text=True)
    # Allowed by name: the CHANGELOG only describes it, and test_verify_hook_tests.py skips on an install
    # (require_repo), so neither makes an installed copy look for the gate.
    allowed = {".gitattributes", "CHANGELOG.md", "tools/x4validate/tests/test_verify_hook_tests.py"}
    users = [p for p in listed.stdout.splitlines() if p not in allowed]
    # Anything naming it must itself be outside the bundle (export-ignored), or an installed copy would look for it.
    shipped = []
    for p in users:
        a = subprocess.run(["git", "-C", str(REPO), "check-attr", "export-ignore", "--", p],
                           capture_output=True, text=True, check=True)
        if not a.stdout.strip().endswith("set"):
            shipped.append(p)
    assert not shipped, shipped

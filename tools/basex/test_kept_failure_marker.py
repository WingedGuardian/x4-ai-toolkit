"""4.1.1 review B1: a failed test leaves its session folder marked as failure evidence, in THIS suite too.

Only tools/x4validate/tests/conftest.py wrote `.x4-kept-failure`, and only into a failing test's own tmp_path. This
suite (its own conftest, the shared x4pytest session) and a failing test that used tmp_path_factory left no marker,
so x4housekeep would DELETE that evidence instead of recycling it. The marker now comes from scripts/x4pytest.py,
which every toolkit conftest loads, and it is also written into the session's base folder.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLKIT = HERE.parents[1]


def _child(tmp_path, body: str):
    """Run *body* as a test module in a child pytest that loads THIS suite's conftest. Returns (result, basetemp)."""
    mod = tmp_path / "case_mod"
    mod.mkdir()
    (mod / "test_case.py").write_text(body, encoding="utf-8")
    base = tmp_path / "child-base"
    # env-ok: the child pytest inherits this process's environment; no configuration is read here
    env = dict(os.environ, PYTHONPATH=str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                        "--basetemp", str(base), "--rootdir", str(mod), str(mod / "test_case.py")],
                       capture_output=True, text=True, env=env, cwd=str(mod), timeout=300)
    return r, base


def test_the_marker_is_shared_with_housekeep():
    sys.path.insert(0, str(TOOLKIT / "scripts"))
    import x4pytest
    src = (TOOLKIT / "scripts" / "x4housekeep.py").read_text(encoding="utf-8")
    assert x4pytest.KEPT_FAILURE == ".x4-kept-failure"
    assert 'KEPT_FAILURE = ".x4-kept-failure"' in src


def test_a_failing_test_in_this_suite_marks_its_folder_and_the_session(tmp_path):
    r, base = _child(tmp_path, "def test_fails(tmp_path):\n    (tmp_path / 'evidence.txt').write_text('e')\n"
                               "    assert False\n")
    assert "1 failed" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    failed = [d for d in base.iterdir() if d.name.startswith("test_fails") and not d.is_symlink()]
    assert len(failed) == 1 and (failed[0] / ".x4-kept-failure").is_file()
    assert (base / ".x4-kept-failure").is_file()


def test_a_failing_test_that_used_only_tmp_path_factory_marks_the_session(tmp_path):
    r, base = _child(tmp_path, "def test_fails(tmp_path_factory):\n"
                               "    (tmp_path_factory.mktemp('made') / 'evidence.txt').write_text('e')\n"
                               "    assert False\n")
    assert "1 failed" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    assert (base / ".x4-kept-failure").is_file()


def test_TWIN_a_passing_session_is_not_marked(tmp_path):
    r, base = _child(tmp_path, "def test_passes(tmp_path, tmp_path_factory):\n"
                               "    (tmp_path / 'x.txt').write_text('x')\n    tmp_path_factory.mktemp('made')\n")
    assert "1 passed" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    assert not any(p.name == ".x4-kept-failure" for p in base.rglob("*"))

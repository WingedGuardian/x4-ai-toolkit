"""`.github/scripts/installed_suite_verdict.py`: the verdict of CI's installed-copy leg (4.1 review D-C2).

The leg's whole value is that it can go RED for the right reasons and cannot go green on nothing, so each
clause of the verdict has its own twin here, and the junit reader is checked against a REAL pytest junit
file rather than only against a fixture written to agree with it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from _layout import ROOT, require_repo

require_repo(".github/scripts/installed_suite_verdict.py", module_level=True,
             why="CI scripts are not installed")
_spec = importlib.util.spec_from_file_location("installed_suite_verdict",
                                               ROOT / ".github" / "scripts" / "installed_suite_verdict.py")
isv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(isv)


def _install_tree(base: Path) -> Path:
    root = base / "tk"
    for marker in isv.INSTALL_MARKERS:
        path = root / marker
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return root


def _junit(path: Path, passed=0, failed=0, errors=0, skipped=0, root=None) -> Path:
    cases = ['<testcase classname="t.m" name="ok%d"/>' % i for i in range(passed)]
    cases += ['<testcase classname="t.m" name="bad%d"><failure message="x">tb</failure></testcase>' % i
              for i in range(failed)]
    cases += ['<testcase classname="t.m" name="err%d"><error message="x">tb</error></testcase>' % i
              for i in range(errors)]
    cases += ['<testcase classname="t.m" name="skip%d"><skipped message="why"/></testcase>' % i
              for i in range(skipped)]
    suite = "installed-copy:%s" % root if root is not None else "pytest"
    path.write_text('<?xml version="1.0"?><testsuites><testsuite name="%s">%s</testsuite></testsuites>'
                    % (suite, "".join(cases)), encoding="utf-8")
    return path


def _verdict(tmp_path, root=None, junit=None, **counts):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = root or _install_tree(tmp_path)
    junit = junit or _junit(tmp_path / "j.xml", root=root, **counts)
    return isv.verdict(junit, root, min_tests=10, min_passed=8, max_skips=3)


def test_a_clean_installed_run_passes(tmp_path):
    problems, counts = _verdict(tmp_path, passed=9, skipped=3)          # exactly AT the ceiling still passes
    assert problems == [] and counts["total"] == 12 and counts["skipped"] == 3


def test_TWIN_a_failure_or_an_error_is_refused_by_name(tmp_path):
    problems, _ = _verdict(tmp_path, passed=9, failed=1)
    assert any("1 failed" in p and "FAILED t.m::bad0" in p for p in problems), problems
    problems, _ = _verdict(tmp_path / "e", passed=9, errors=1)
    assert any("1 errored" in p and "ERROR t.m::err0" in p for p in problems), problems


def test_TWIN_one_skip_over_the_ceiling_is_refused(tmp_path):
    problems, _ = _verdict(tmp_path, passed=9, skipped=4)
    assert any("4 skipped, above the ceiling of 3" in p for p in problems), problems


def test_TWIN_a_run_that_collected_next_to_nothing_is_refused_not_passed(tmp_path):
    problems, counts = _verdict(tmp_path, passed=0)                     # zero cases: the vacuous green
    assert counts["total"] == 0 and any("fewer than the floor of 10" in p for p in problems), problems
    problems, _ = _verdict(tmp_path / "s", passed=2, skipped=3)
    assert any("only 2 passed" in p for p in problems), problems        # enough cases, too few that RAN


def test_TWIN_a_tree_that_still_has_agent_or_git_is_not_an_installed_copy(tmp_path):
    root = _install_tree(tmp_path)
    (root / "agent").mkdir()
    problems, _ = _verdict(tmp_path, root=root, passed=9)
    assert any("agent/" in p and "not an installed copy" in p for p in problems), problems
    (root / "agent").rmdir()
    (root / ".git").mkdir()
    problems, _ = _verdict(tmp_path, root=root, passed=9)
    assert any(".git" in p and "checkout" in p for p in problems), problems


def test_TWIN_a_tree_missing_what_every_install_carries_is_refused(tmp_path):
    root = _install_tree(tmp_path)
    (root / "x4-paths.env").unlink()
    problems, _ = _verdict(tmp_path, root=root, passed=9)
    assert any("missing x4-paths.env" in p for p in problems), problems


def test_TWIN_a_missing_or_unreadable_junit_is_refused(tmp_path):
    root = _install_tree(tmp_path)
    problems, counts = isv.verdict(tmp_path / "nope.xml", root, 10, 8, 3)
    assert counts is None and any("no junit file" in p for p in problems), problems
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testcase", encoding="utf-8")
    problems, counts = isv.verdict(broken, root, 10, 8, 3)
    assert counts is None and any("unreadable" in p for p in problems), problems


def test_TWIN_a_junit_from_another_tree_or_no_tree_is_refused(tmp_path):
    """The verdict read the tree at --root and the counts from --junit, and nothing tied them together: a junit written by
    a run of any other copy, a checkout for instance, passed as the install's."""
    root = _install_tree(tmp_path)
    other = tmp_path / "another-tree"
    problems, _ = isv.verdict(_junit(tmp_path / "a.xml", passed=9, root=other), root, 10, 8, 3)
    assert any("another-tree" in p and "not the tree" in p for p in problems), problems
    problems, _ = isv.verdict(_junit(tmp_path / "n.xml", passed=9), root, 10, 8, 3)
    assert any("names no tree" in p for p in problems), problems


def test_a_REAL_pytest_run_ties_its_junit_to_the_tree(tmp_path):
    """The real option, on real pytest: the fixture above is written to agree with the reader."""
    root = _install_tree(tmp_path)
    (tmp_path / "test_sample.py").write_text("def test_pass(): pass\n", encoding="utf-8")
    junit = tmp_path / "real.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", str(junit),
                    "-o", "junit_suite_name=installed-copy:%s" % root, str(tmp_path / "test_sample.py")],
                   capture_output=True, timeout=120, cwd=tmp_path)
    problems, counts = isv.verdict(junit, root, 1, 1, 0)
    assert counts["passed"] == 1 and problems == [], problems


def test_the_reader_agrees_with_a_REAL_pytest_junit_file(tmp_path):
    """The fixture above is written to agree with the reader. A real run is not."""
    (tmp_path / "test_sample.py").write_text(textwrap.dedent("""
        import pytest

        @pytest.fixture
        def boom():
            raise RuntimeError("setup fails")

        def test_pass(): pass
        def test_fail(): assert False
        def test_skip(): pytest.skip("because")
        def test_error(boom): pass
        @pytest.mark.parametrize("n", [1, 2])
        def test_two_more(n): pass
    """), encoding="utf-8")
    junit = tmp_path / "real.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", str(junit),
                    str(tmp_path / "test_sample.py")], capture_output=True, timeout=120, cwd=tmp_path)
    counts = isv.tally(junit)
    assert (counts["total"], counts["passed"], counts["failed"], counts["errors"], counts["skipped"]) == (
        6, 3, 1, 1, 1), counts


def test_the_command_line_exits_1_with_every_reason_and_0_only_when_clean(tmp_path, capsys):
    root = _install_tree(tmp_path)
    argv = ["--junit", str(_junit(tmp_path / "ok.xml", passed=10, root=root)), "--root", str(root),
            "--min-tests", "10", "--min-passed", "8", "--max-skips", "3"]
    assert isv.main(argv) == 0
    out = capsys.readouterr().out
    assert "INSTALLED-COPY SUITE OK" in out and "10 passed" in out
    argv[1] = str(_junit(tmp_path / "bad.xml", passed=2, failed=1, skipped=9, root=root))
    assert isv.main(argv) == 1
    err = capsys.readouterr().err
    assert err.count("REFUSED:") == 3, err          # too few passes, a failure, too many skips
    with pytest.raises(SystemExit):                  # a ceiling/floor that cannot bite is a usage error
        isv.main(argv[:-6] + ["--min-tests", "0", "--min-passed", "1", "--max-skips", "3"])


# ------------------------------------------------ the CI job that calls it cannot be hollowed out
def _verdict_args(run: str) -> dict:
    import re
    return {k: int(v) for k, v in re.findall(r"--(min-tests|min-passed|max-skips)\s+(\d+)", run)}


def _installed_copy_job():
    from ci_workflow import load_ci
    ci = load_ci(ROOT)    # ci.yml + full-qualification.yml: the full jobs moved to the second file
    job = ci["jobs"]["installed-copy"]
    steps = {s.get("id") or s.get("name"): s for s in job["steps"]}
    verdict = next(s for s in job["steps"] if "installed_suite_verdict.py" in str(s.get("run", "")))
    return job, steps, verdict, _verdict_args(verdict["run"])


def test_the_ci_leg_builds_a_real_install_runs_its_suite_and_judges_the_junit():
    require_repo(".github/workflows/ci.yml", why="CI configuration is not installed")
    job, steps, verdict, args = _installed_copy_job()
    assert "continue-on-error" not in job, "the leg must GATE"
    install = steps["install"]["run"]
    for must in ("git archive HEAD", "install.sh --method separate", "test ! -e \"$TK/agent\"",
                 "test ! -e \"$TK/.git\"", "-name 'test_*.py'", "installed_layout()"):
        assert must in install, must
    suite = steps["suite"]["run"]
    assert "uv run --frozen pytest" in suite and "--junitxml=\"$RUNNER_TEMP/installed-suite.xml\"" in suite
    assert "--junit \"$RUNNER_TEMP/installed-suite.xml\"" in verdict["run"], "judges a different file than the suite wrote"
    assert "-o junit_suite_name=\"installed-copy:$RUNNER_TEMP/installed-toolkit\"" in suite, "the junit must name its tree"
    assert "--root \"$RUNNER_TEMP/installed-toolkit\"" in verdict["run"] and "TK=\"$RUNNER_TEMP/installed-toolkit\"" in install
    # runs after a red suite (so the counts print) and after a cancelled-free setup only
    assert "!cancelled()" in verdict["if"] and "steps.install.outcome == 'success'" in verdict["if"]
    # floors that can bite: a ceiling above the floor, floors in the thousands
    assert set(args) == {"min-tests", "min-passed", "max-skips"}, args
    assert args["min-tests"] >= 5000 and args["min-passed"] >= 5000, args
    assert 0 < args["max-skips"] < args["min-tests"] // 2, args


def test_TWIN_the_floor_extraction_goes_red_when_a_floor_is_dropped_or_zeroed():
    full = "python3 x.py --min-tests 6000 --min-passed 5300 --max-skips 900"
    assert set(_verdict_args(full)) == {"min-tests", "min-passed", "max-skips"}
    assert set(_verdict_args(full.replace(" --min-passed 5300", ""))) != {"min-tests", "min-passed", "max-skips"}
    assert _verdict_args(full.replace("6000", "0"))["min-tests"] == 0      # and the job test rejects a zero floor

#!/usr/bin/env python3
"""The verdict on the INSTALLED-COPY suite (CI job `installed-copy`).

WHY IT EXISTS. SETUP_PROMPT.txt tells every new user to run `uv run pytest -q` in their installed
toolkit and promises "0 failed and 0 errors". An installed toolkit is runtime-only (no `agent/`, no
`docs/`, no git history), and for a whole release arc nothing ran the suite in that shape: the `tests`
job runs a checkout, the cold legs run a checkout and a `git archive` extract that STILL carries
`agent/`. 4.1 review D-C2 measured 74 errors and 14 failures there. This script turns the junit file of
that run into a verdict that cannot pass on nothing.

IT REFUSES TO PASS WHEN:
  * the tree under test has an `agent/` or a `.git` (it would not be an installed copy), or lacks the
    files an install always carries (the check is on the tree, not on how it was made);
  * the junit file does not name that tree: the suite step runs with
    `-o junit_suite_name=installed-copy:<root>`, because the tree and the counts come from two arguments and
    a junit file from any other copy would otherwise pass as this one's;
  * the junit file is missing or unreadable, or holds fewer than --min-tests test cases, or fewer than
    --min-passed passes (a suite that collected a handful, or skipped itself, is not a green suite);
  * ANY test failed or errored;
  * more than --max-skips tests skipped (a skip is a test that did not run: a ceiling stops a quiet
    slide into dormancy, as X4_MAX_SKIPS does for the `tests` job).

Exit 0 only when every one of those holds; 1 with every reason otherwise; 2 on a usage error. Nothing is
printed that is not derived from the files it was given.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: Files an install always carries. Their absence means the tree is not an install of this toolkit.
INSTALL_MARKERS = ("install.sh", "setup.sh", "x4-paths.env", "tools/x4validate/pyproject.toml",
                   "tools/x4validate/tests/_layout.py")
SUITE_PREFIX = "installed-copy:"


def tally(junit: Path) -> dict:
    """Counts by test CASE (not by the suite attributes, which an interrupted run can leave stale)."""
    tree = ET.parse(junit)
    out = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "bad": [],
           "suites": sorted({s.get("name", "") for s in tree.getroot().iter("testsuite")})}
    for case in tree.getroot().iter("testcase"):
        out["total"] += 1
        kinds = {child.tag for child in case}
        name = "%s::%s" % (case.get("classname", "?"), case.get("name", "?"))
        if "failure" in kinds:
            out["failed"] += 1
            out["bad"].append("FAILED " + name)
        elif "error" in kinds:
            out["errors"] += 1
            out["bad"].append("ERROR " + name)
        elif "skipped" in kinds:
            out["skipped"] += 1
        else:
            out["passed"] += 1
    return out


def verdict(junit, root, min_tests: int, min_passed: int, max_skips: int) -> tuple[list[str], dict | None]:
    """(problems, tally). No problems = pass."""
    problems: list[str] = []
    root = Path(root)
    if (root / "agent").exists():
        problems.append("%s has an agent/ folder: this is not an installed copy (an install is runtime-only)" % root)
    if (root / ".git").exists():
        problems.append("%s has a .git: this is a checkout, not an installed copy" % root)
    for marker in INSTALL_MARKERS:
        if not (root / marker).is_file():
            problems.append("%s is missing %s: not an installed toolkit" % (root, marker))
    junit = Path(junit)
    if not junit.is_file():
        problems.append("no junit file at %s: the suite did not run to the end" % junit)
        return problems, None
    try:
        counts = tally(junit)
    except ET.ParseError as exc:
        problems.append("junit file %s is unreadable (%s)" % (junit, exc))
        return problems, None
    ran_on = [n[len(SUITE_PREFIX):] for n in counts["suites"] if n.startswith(SUITE_PREFIX)]
    if not ran_on:
        problems.append("junit file %s names no tree (run the suite with -o junit_suite_name=%s<root>): nothing ties "
                        "its counts to %s" % (junit, SUITE_PREFIX, root))
    elif any(Path(n).resolve() != root.resolve() for n in ran_on):
        problems.append("junit file %s is from %s, not the tree under judgement (%s)" % (junit, ", ".join(ran_on), root))
    if counts["total"] < min_tests:
        problems.append("only %d test case(s) in the junit file, fewer than the floor of %d: the suite collected "
                        "next to nothing" % (counts["total"], min_tests))
    if counts["passed"] < min_passed:
        problems.append("only %d passed, fewer than the floor of %d" % (counts["passed"], min_passed))
    if counts["failed"] or counts["errors"]:
        problems.append("%d failed and %d errored (SETUP_PROMPT promises 0 and 0): %s" % (
            counts["failed"], counts["errors"], "; ".join(counts["bad"][:20])
            + (" ..." if len(counts["bad"]) > 20 else "")))
    if counts["skipped"] > max_skips:
        problems.append("%d skipped, above the ceiling of %d: tests went dormant (read the -rs list)" % (
            counts["skipped"], max_skips))
    return problems, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--junit", required=True)
    ap.add_argument("--root", required=True, help="the installed toolkit root")
    ap.add_argument("--min-tests", type=int, required=True)
    ap.add_argument("--min-passed", type=int, required=True)
    ap.add_argument("--max-skips", type=int, required=True)
    args = ap.parse_args(argv)
    if min(args.min_tests, args.min_passed) < 1 or args.max_skips < 0:
        ap.error("the floors must be positive and the ceiling non-negative")
    problems, counts = verdict(args.junit, args.root, args.min_tests, args.min_passed, args.max_skips)
    if counts is not None:
        print("INSTALLED-COPY SUITE: %(total)d cases, %(passed)d passed, %(failed)d failed, %(errors)d errors, "
              "%(skipped)d skipped" % counts)
    if problems:
        for problem in problems:
            print("REFUSED: " + problem, file=sys.stderr)
        return 1
    print("INSTALLED-COPY SUITE OK: 0 failed, 0 errors, skips within the ceiling of %d" % args.max_skips)
    return 0


if __name__ == "__main__":
    sys.exit(main())

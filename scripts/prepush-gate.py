#!/usr/bin/env python3
"""The local pre-push gate: what a push must pass at the desk before it costs a CI round.

    python scripts/prepush-gate.py [--base REV]

Full suites run on GitHub CI, never on the dev machine (owner ruling 2026-10-18). This is the targeted set that
catches what actually turned CI red (owner ruling 2026-10-31, after a month of multi-hour rounds across the toolkits:
no push without this gate; it is installed as the maintainers' git pre-push hook beside the identifier scan, never
shipped). The two red X4 candidate runs of the week before (rc7, rc8) each failed on ONE deterministic hook test
that step 2 below runs in minutes.

Steps, each red on its own:
  1. the dependency lock is fresh (`uv lock --check`) and the shipped lua BUILD stamps are current
     (`scripts/stamp-mod-build.py --check`), both in tools/x4validate;
  2. the hook test files CI runs whole: the parse-pass unit tests, the audit tests (matcher coverage, PowerShell
     front-end E2E) and the hook regression suite (.claude/hooks/);
  3. collection: `pytest --collect-only` of tools/x4validate/tests imports every module;
  4. the tests for the files this push changes (`git diff --name-only BASE...HEAD`): a changed test file runs itself;
     a changed module under tools/x4validate/x4validate/ or scripts/ runs every tests/test_*<its stem>*.py; and every
     test file whose text names a changed file's basename runs, since that is the test that goes red on it.

BASE defaults to the branch's upstream, else the merge-base with origin/master. Exit 0 = passed, 1 = a step failed,
2 = could not run. Prints each step's seconds so growth is seen; over 15 minutes it says so.
"""
from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "tools" / "x4validate"


def _bash():
    """Git Bash by path on Windows: a bare `bash` resolves to System32's WSL relay first, which has no /bin/bash."""
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if sys.platform == "win32" and Path(p).is_file():
            return p
    return "bash"


HOOK_TESTS = [
    ["python", ".claude/hooks/test_hook_facts.py"],
    ["python", ".claude/hooks/test_audit0924_hooks.py"],
    [_bash(), ".claude/hooks/test-protect-bash.sh"],
]


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def base_rev(explicit):
    if explicit:
        return explicit
    up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if up and _git("rev-parse", "--verify", "-q", up):
        return up
    for ref in ("origin/master", "origin/main", "master"):
        mb = _git("merge-base", "HEAD", ref)
        if mb:
            return mb
    return ""


def changed_files(base):
    if not base:
        return []
    return [l.replace("\\", "/") for l in _git("diff", "--name-only", base + "...HEAD").splitlines() if l.strip()]


def referencing_tests(changed):
    """Every test file whose TEXT names a changed file's basename.

    Stem matching alone missed the Skyrim toolkit's CI run 41254615914 (3 deterministic reds on all 8 legs): the
    push changed workflow and build files, and the tests asserting on them by name shared no stem with them. A test
    that spells the changed file is the one that goes red on it, so select by reference as well.
    """
    names = {Path(p).name for p in changed if not re.fullmatch(r"tools/x4validate/tests/test_[^/]+\.py", p)}
    # A test can also reach a workflow through its folder (a glob of .github/workflows), naming no file: this missed
    # test_workflow_ownership on the 2026-11-02 cold-job comment, red on all four legs of CI run 41399417477.
    if any(p.startswith(".github/workflows/") for p in changed):
        names.add(".github/workflows")
    if not names:
        return []
    out = []
    for f in sorted((PKG / "tests").glob("test_*.py")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(n in text for n in names):
            out.append(f.relative_to(REPO).as_posix())
    return out


def tests_for(changed):
    """Repo-relative test files for the changed paths (only files that exist)."""
    want = []
    for p in changed:
        if re.fullmatch(r"tools/x4validate/tests/test_[^/]+\.py", p):
            want.append(p)
        elif p.endswith(".py") and (p.startswith("tools/x4validate/x4validate/") or p.startswith("scripts/")
                                    or p.startswith("tools/x4validate/scripts/")):
            stem = Path(p).stem.replace("-", "_")
            want.extend(Path(f).relative_to(REPO).as_posix()
                        for f in glob.glob(str(PKG / "tests" / ("test_*" + stem + "*.py"))))
    want.extend(referencing_tests(changed))
    out = []
    for t in want:
        if t not in out and (REPO / t).is_file():
            out.append(t)
    return out


# Tests that read what is DEPLOYED on this machine, not only the tree being pushed. Guard code may only be deployed
# after green CI, so running them here made any branch that changes agent/ unpushable: the gate wanted the deploy, the
# deploy wanted CI, CI wanted the push. Excluded, and said so on every run. What still covers them:
#   deploy parity: CI SKIPS it (no game root). deploy-claude-dir.py re-checks its own files after --apply; x4doctor and
#     the release gates check on demand. NOTHING checks routinely between deploys.
#   routing: CI RUNS it against the shipped CLAUDE.md/AGENTS.md. Only the game root's own CLAUDE.md goes unchecked here,
#     and the deploy script does not write that file.
DEPLOYED_STATE_TESTS = [
    "tests/test_deploy_parity.py::test_the_real_trees",
    "tests/test_routing_coverage.py::test_the_live_file_routes_every_cli",
]


def deployed_state_args(rel):
    """--deselect arguments for the deployed-state tests in `rel`, each announced as NOT CHECKED."""
    args = []
    for node in DEPLOYED_STATE_TESTS:
        if node.split("::")[0] in rel:
            print("   NOT CHECKED here (reads deployed state; run after deploying): " + node)
            args += ["--deselect", node]
    return args


class Gate:
    def __init__(self):
        self.failed = []
        self.t0 = time.time()

    def step(self, name, cmd, cwd=None, check=None):
        # cwd resolves at CALL time: a `cwd=REPO` default was bound at import, so --root never reached the hook-test
        # steps and the snapshot copy looked for .claude/hooks beside itself (another session's push, 2026-10-31).
        cwd = cwd or REPO
        t = time.time()
        print("== {}".format(name), flush=True)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        problems = check(r) if check else []
        ok = r.returncode == 0 and not problems
        if not ok:
            print("\n".join("   " + l for l in (r.stdout + r.stderr).strip().splitlines()[-40:]))
            for p in problems:
                print("   " + p)
            self.failed.append(name)
        print("   {} in {:.0f}s".format("ok" if ok else "FAILED", time.time() - t), flush=True)
        return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="")
    ap.add_argument("--root", type=Path, default=None,
                    help="the checkout to gate (default: this script's repo); the hook passes the tree being pushed")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")   # a cp1252 console must not crash the gate on a test's output
        except Exception:                           # (UnicodeEncodeError printing a failing step, 2026-11-01)
            pass
    global REPO, PKG
    if a.root is not None:
        REPO = a.root.resolve()
        PKG = REPO / "tools" / "x4validate"
        if not (PKG / "tests").is_dir():
            print("prepush-gate: {} is not an X4 toolkit checkout (no tools/x4validate/tests)".format(REPO))
            return 2
    if subprocess.run(["uv", "--version"], capture_output=True).returncode != 0:
        print("prepush-gate: uv is not on PATH; the suite runs through `uv run --frozen`")
        return 2
    base = base_rev(a.base)
    changed = changed_files(base)
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    print("prepush-gate: {} @ {}  base {}  {} changed file(s){}".format(
        _git("rev-parse", "--abbrev-ref", "HEAD"), _git("rev-parse", "--short", "HEAD"), base or "(none)",
        len(changed), "  [DIRTY TREE: the gate runs on the tree, the push sends the commits]" if dirty else ""))
    g = Gate()
    g.step("dependency lock is fresh", ["uv", "lock", "--check"], cwd=PKG)
    g.step("shipped lua BUILD stamps are current",
           ["uv", "run", "--frozen", "python", "scripts/stamp-mod-build.py", "--check"], cwd=PKG)
    for cmd in HOOK_TESTS:
        g.step(cmd[-1], cmd)

    def collect_check(r):
        if re.search(r"\d+ errors?\b", r.stdout):
            return ["collection reported errors: a module did not import"]
        m = re.search(r"(\d+) tests? collected", r.stdout)
        if m:
            print("   collected {}".format(m.group(1)))
        return [] if m else ["no 'N tests collected' line"]
    g.step("the suite collects", ["uv", "run", "--frozen", "python", "-m", "pytest", "--collect-only", "-q",
                                  "-p", "no:cacheprovider", "tests"], cwd=PKG, check=collect_check)
    files = tests_for(changed)
    if files:
        rel = [str(Path(f).relative_to("tools/x4validate").as_posix()) for f in files]
        print("   tests for changed files: " + ", ".join(rel))
        g.step("changed-file tests ({} files)".format(len(rel)),
               ["uv", "run", "--frozen", "python", "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider",
                *deployed_state_args(rel), *rel], cwd=PKG)
    total = time.time() - g.t0
    if g.failed:
        print("prepush-gate: FAILED ({}) in {:.0f}s -- fix before pushing; CI would have gone red on it".format(
            ", ".join(g.failed), total))
        return 1
    print("prepush-gate: passed in {:.0f}s{}".format(total, "  (over 15 min: trim this gate)" if total > 900 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

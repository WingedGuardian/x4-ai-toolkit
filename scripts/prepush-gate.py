#!/usr/bin/env python3
"""The local pre-push gate: what a push must pass at the desk before it costs a CI round.

    python scripts/prepush-gate.py [--base REV] [--no-reuse]

Full suites run on GitHub CI, never on the dev machine (owner ruling 2026-10-18). This is the targeted set that
catches what actually turned CI red (owner ruling 2026-10-31, after a month of multi-hour rounds across the toolkits:
no push without this gate; it is installed as the maintainers' git pre-push hook beside the identifier scan, never
shipped). The two red X4 candidate runs of the week before (rc7, rc8) each failed on ONE deterministic hook test
that step 2 below runs in minutes.

Steps, each red on its own:
  0. the push preflight (scripts/preflight.py: syntax, `uv lock --check`, the shared ci-policy contract at the
     pinned commit, generated files, the repo-wide meta tests). It runs FIRST and the gate stops on anything but a
     pass: exit 1 when it fails, 2 when it could not run;
  1. the shipped lua BUILD stamps are current (`scripts/stamp-mod-build.py --check`) and the shipped CLAUDE.md is
     within its committed floor (`gates/claude_md_budget.py --shipped`, issue #21), both in tools/x4validate;
  2. the hook test files CI runs whole: the parse-pass unit tests, the audit tests (matcher coverage, PowerShell
     front-end E2E) and the hook regression suite (.claude/hooks/), after the mutant table's anchors (no trial is
     run here, and CI's mutation step refuses outright on one stale anchor);
  3. collection: `pytest --collect-only` of tools/x4validate/tests imports every module;
  4. the tests for the files this push changes (`git diff --name-only BASE...HEAD`): a changed test file runs itself;
     a changed module under tools/x4validate/x4validate/ or scripts/ runs every tests/test_*<its stem>*.py; and every
     test file whose text names a changed file's basename runs, since that is the test that goes red on it.

PASS RECORDS (4.3). Every push since a release re-tested everything the branch changed since the base, so each
round paid for the last one again. A run that passes writes `<git-common-dir>/prepush-gate/passes/<tree>.json`
(shared by every worktree, never pushed), LAST and atomically, and only when every step passed, the base and the
toolchain versions are known, the tree was clean (untracked files included) at the start and the end, and HEAD's tree
did not change during the run. A failed or killed run writes none. A later run REUSES the closest record (fewest
changed files to the current tree) whose base, gate sha256, SELECTION_VERSION and python/uv versions match, whose
tree object still exists, and when uv.lock and pyproject.toml are unchanged since it: the changed set is then the
diff from that tree, and the three hook suites are skipped when no guard path (GUARD_PATHS) changed since it. The
anchor check, the preflight, collection and step 1 run every time. Every skip and every reason a record was not used
is printed. `--no-reuse` ignores every record (a passing run still writes one).

BASE defaults to the branch's upstream, else the merge-base with origin/master. Exit 0 = passed, 1 = a step failed,
2 = could not run. Prints each step's seconds so growth is seen; over 15 minutes it says so.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
    ["python", "scripts/verify-hook-tests.py", "--check-anchors"],
    ["python", ".claude/hooks/test_hook_facts.py"],
    ["python", ".claude/hooks/test_audit0924_hooks.py"],
    [_bash(), ".claude/hooks/test-protect-bash.sh"],
]


def _git_result(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def _git(*args):
    return _git_result(*args).stdout.strip()


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
    """The files the push changes. None when git could not answer: an empty list is the answer "nothing changed"."""
    if not base:
        return []
    r = _git_result("diff", "--name-only", base + "...HEAD")
    if r.returncode:
        return None
    return [l.replace("\\", "/") for l in r.stdout.splitlines() if l.strip()]


def referencing_tests(changed, left_out=None):
    """Every test file whose TEXT names a changed file's basename.

    Stem matching alone missed the Skyrim toolkit's CI run 41254615914 (3 deterministic reds on all 8 legs): the
    push changed workflow and build files, and the tests asserting on them by name shared no stem with them. A test
    that spells the changed file is the one that goes red on it, so select by reference as well.

    A test left out under NOT_A_REFERENCE is appended to `left_out` as (test, names) for the caller to report, or
    printed here when no list is given.
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
        rel = f.relative_to(REPO).as_posix()
        named = sorted(n for n in names if n in text)
        fixture_only = [n for n in named if (rel, n) in NOT_A_REFERENCE]
        if len(named) > len(fixture_only):
            out.append(rel)
        elif fixture_only:
            if left_out is None:
                print(_left_out_line(rel, fixture_only))
            else:
                left_out.append((rel, fixture_only))
    return out


def _left_out_line(rel, names):
    """A narrowing step says so: never dropped quietly."""
    return "   not selected: {} names {} only as {}".format(rel, ", ".join(names), NOT_A_REFERENCE[(rel, names[0])])


#: (test file, changed file's name) -> why that test's text spelling the name is NOT a reference to the repository's
#: file. referencing_tests selects by text, so a test that writes a FIXTURE with a common name is selected by every
#: push that touches the real one. Only a pair listed here is left out, only when no other changed name is in the
#: test, and the run says so. Full CI still runs the file. Each row needs a measurement, and the test must still be
#: selected by the files it does depend on.
#:   MEASURED 2026-11-11 (two gate runs, 3,245 s and 3,041 s; the changed-file step 2,660 s and 2,584 s): timed per
#:   file, test_install_over_existing.py was 2,209 s of 2,575 s (86%), selected by ONE match: line 3337 writes a
#:   fixture CHANGELOG.md into a scratch install. Nearly every push changes CHANGELOG.md. The installers read only the
#:   DESTINATION's CHANGELOG (the previous install's version heading), which that test supplies itself.
NOT_A_REFERENCE = {
    ("tools/x4validate/tests/test_install_over_existing.py", "CHANGELOG.md"):
        "the name of a fixture it writes into a scratch install (it never reads the repository's)",
}


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
    left_out = []
    want.extend(referencing_tests(changed, left_out))
    out = []
    for t in want:
        if t not in out and (REPO / t).is_file():
            out.append(t)
    for rel, names in left_out:
        if rel not in out:              # selected another way (its own change, its stem): nothing was left out
            print(_left_out_line(rel, names))
    return out


# Tests that read what is DEPLOYED on this machine, not only the tree being pushed. Until 2026-11-16 guard code could
# only be deployed after green CI, so running them here made any branch that changes agent/ unpushable: the gate wanted
# the deploy, the deploy wanted CI, CI wanted the push. The deploy now needs only a commit and its local hook tests (user
# rule 2026-11-16), but a push still should not wait on what this machine has deployed. Excluded, and said so on every
# run. What still covers them:
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


#: Bump whenever WHICH tests a change selects changes in a way the gate's own bytes would not show (a NOT_A_REFERENCE
#: row is in this file, so its hash covers it; a rule moved to another file would not be). A record made under another
#: selection is not reused.
SELECTION_VERSION = 1

#: What the three hook suites (HOOK_TESTS[1:]) read: a change under one of these since a record runs them again.
#: A trailing "/" is a folder. The gate itself is listed too, though a changed gate already fails the sha256 match.
#: DERIVED by tests/test_prepush_gate.py from the path literals in .claude/hooks/ (suites and the hook scripts they run):
#: the suites copy tools/modding_harness, its lock and tools/x4_harness, read next-blind-spot-id.py; the hooks run
#: x4canary, x4deploydrift, x4lock and x4conformance from <hooks>/../../scripts. tools/agent-harness.py is only named
#: (the save-lock probes test its exact command shape) and is listed so a rename re-runs them. ONE level deep: what
#: those scripts import in turn (x4validate._paths) is not derived.
GUARD_PATHS = (".claude/", ".codex/", ".opencode/", "agent/", "scripts/verify-hook-tests.py", "scripts/test-hooks.sh",
               "scripts/gitbash.py", "scripts/prepush-gate.py", "scripts/x4canary.py", "scripts/x4deploydrift.py",
               "scripts/x4lock.py", "scripts/x4conformance.py", "tools/modding_harness/",
               "tools/modding-harness.lock.json", "tools/x4_harness/", "tools/agent-harness.py",
               "tools/x4validate/scripts/next-blind-spot-id.py")

#: A change to one of these since a record means the suite runs on other packages: never reuse across it.
DEPENDENCY_FILES = ("uv.lock", "pyproject.toml")


def is_guard_path(path):
    return any(path.startswith(g) if g.endswith("/") else path == g for g in GUARD_PATHS)


def gate_sha256():
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def toolchain():
    """{"python": the version of the interpreter the tests run under, "uv": `uv --version`}, or None when either cannot
    be read. Unknown versions mean no record is reused or written: a record is only as good as what it was run on."""
    uv = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    py = subprocess.run(["uv", "run", "--frozen", "python", "-c", "import sys; print(sys.version)"], cwd=PKG,
                        capture_output=True, text=True)
    if uv.returncode or py.returncode or not (uv.stdout or "").strip() or not (py.stdout or "").strip():
        return None
    return {"python": py.stdout.strip().splitlines()[-1], "uv": uv.stdout.strip().splitlines()[-1]}


def passes_dir():
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "prepush-gate" / "passes" if common else None


def head_tree():
    return _git("rev-parse", "--verify", "-q", "HEAD^{tree}")


def is_clean():
    r = _git_result("status", "--porcelain", "--untracked-files=all")
    return r.returncode == 0 and not r.stdout.strip()


def tree_diff(old, new):
    r = _git_result("diff", "--name-only", old, new)
    if r.returncode:
        return None
    return [l.replace("\\", "/") for l in r.stdout.splitlines() if l.strip()]


def _identity(base_sha, tools):
    return {"base": base_sha, "gate_sha256": gate_sha256(), "selection_version": SELECTION_VERSION,
            "python": tools["python"], "uv": tools["uv"]}


def closest_record(tree, base_sha, tools):
    """(record, changed files from its tree to `tree`) for the closest reusable record, or None. Says why each one
    on file was not used."""
    folder = passes_dir()
    if folder is None or not folder.is_dir():
        print("   pass records: none on file")
        return None
    want = _identity(base_sha, tools)
    best, rejected = None, []
    for f in sorted(folder.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as e:
            rejected.append("{}: unreadable ({})".format(f.name, type(e).__name__))
            continue
        if not isinstance(rec, dict) or rec.get("tree") != f.stem or rec.get("status") != "passed":
            rejected.append("{}: not a passed record for the tree it is named after".format(f.name[:12]))
            continue
        off = [k for k, v in want.items() if rec.get(k) != v]
        if off:
            rejected.append("{}: different {}".format(f.stem[:12], ", ".join(off)))
            continue
        if _git_result("cat-file", "-e", f.stem + "^{tree}").returncode:
            rejected.append("{}: its tree object is not in this repository".format(f.stem[:12]))
            continue
        changed = tree_diff(f.stem, tree)
        if changed is None:
            rejected.append("{}: git could not diff it against this tree".format(f.stem[:12]))
            continue
        deps = [p for p in changed if Path(p).name in DEPENDENCY_FILES]
        if deps:
            rejected.append("{}: {} changed since".format(f.stem[:12], ", ".join(deps)))
            continue
        if best is None or len(changed) < len(best[1]):
            best = (rec, changed)
    for line in rejected:
        print("   pass record not used: " + line)
    if best is None and not rejected:
        print("   pass records: none on file")
    return best


def write_record(tree, base_sha, tools, selected, hook_suites, reused_from):
    """Atomically, as the run's LAST act. Returns the path, or None (said why)."""
    folder = passes_dir()
    if folder is None:
        print("   pass record NOT written: git could not name the git directory")
        return None
    rec = {"tree": tree, "head": _git("rev-parse", "HEAD"), **_identity(base_sha, tools), "status": "passed",
           "selected_tests": selected, "hook_suites": hook_suites, "reused_from": reused_from,
           "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = folder / (tree + ".json")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=tree + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write((json.dumps(rec, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        print("   pass record NOT written: {}".format(e))
        return None
    print("   pass record written: {}".format(path))
    return path


class Gate:
    def __init__(self):
        self.failed = []
        self.t0 = time.time()
        self.kept = False
        self.last_rc = None

    def _keep_full_output(self, name, text):
        """The screen shows the last 40 lines. A test failure's traceback fell off that in rc2 and its temp folder went
        with the run, so it could never be explained. Kept in the git directory: per worktree, and never tracked."""
        try:
            path = _git("rev-parse", "--path-format=absolute", "--git-path", "prepush-gate-failures.log")
            if not path:
                raise OSError("no git directory")
            with open(path, "a" if self.kept else "w", encoding="utf-8", errors="replace") as f:
                f.write("== {} at {}\n{}\n".format(name, time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except (OSError, UnicodeError) as e:
            print("   full output NOT kept: {}".format(e))
            return
        self.kept = True
        print("   full output: {}".format(path))

    def step(self, name, cmd, cwd=None, check=None):
        # cwd resolves at CALL time: a `cwd=REPO` default was bound at import, so --root never reached the hook-test
        # steps and the snapshot copy looked for .claude/hooks beside itself (another session's push, 2026-10-31).
        cwd = cwd or REPO
        t = time.time()
        print("== {}".format(name), flush=True)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        problems = check(r) if check else []
        self.last_rc = r.returncode
        ok = r.returncode == 0 and not problems
        if not ok:
            print("\n".join("   " + l for l in (r.stdout + r.stderr).strip().splitlines()[-40:]))
            self._keep_full_output(name, r.stdout + r.stderr)
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
    ap.add_argument("--no-reuse", action="store_true",
                    help="ignore every pass record: test the whole push since the base (a pass still writes one)")
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
    base = base_rev(a.base)
    # A bad --base gave an empty diff and "0 changed files, passed": the changed-file tests were the ones that never ran.
    if a.base and _git_result("rev-parse", "--verify", "-q", a.base + "^{commit}").returncode:
        print("prepush-gate: --base {} is not a commit here; nothing was checked".format(a.base))
        return 2
    changed = changed_files(base)
    if changed is None:
        print("prepush-gate: git could not diff {}...HEAD; nothing was checked".format(base))
        return 2
    if not base:
        print("   NOT CHECKED: no base found (no upstream, no origin/master), so no changed-file tests run")
    if subprocess.run(["uv", "--version"], capture_output=True).returncode != 0:
        print("prepush-gate: uv is not on PATH; the suite runs through `uv run --frozen`")
        return 2
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    tree0, clean0 = head_tree(), is_clean()
    base_sha = _git("rev-parse", "--verify", "-q", base + "^{commit}") if base else ""
    tools = toolchain()
    reused = None
    if a.no_reuse:
        print("   pass records: not used (--no-reuse)")
    elif not base_sha:
        print("   pass records: not used (no base commit)")
    elif tools is None:
        print("   pass records: not used (the python/uv versions could not be read)")
    elif tree0:
        reused = closest_record(tree0, base_sha, tools)
    if reused:
        changed = reused[1]
        print("   REUSING the pass record of tree {} (written {}): the changed set is the diff from it".format(
            reused[0]["tree"][:12], reused[0].get("written", "?")))
    print("prepush-gate: {} @ {}  base {}  {} changed file(s){}".format(
        _git("rev-parse", "--abbrev-ref", "HEAD"), _git("rev-parse", "--short", "HEAD"), base or "(none)",
        len(changed), "  [DIRTY TREE: the gate runs on the tree, the push sends the commits]" if dirty else ""))
    g = Gate()
    preflight = Path(__file__).resolve().parent / "preflight.py"
    if not preflight.is_file():
        print("prepush-gate: no {} beside the gate; nothing was checked".format(preflight.name))
        return 2
    # FIRST, and the gate stops on anything but a pass: it is the minute that would have saved two 2 h rounds.
    if not g.step("preflight (scripts/preflight.py)", [sys.executable, "-B", str(preflight), str(REPO)]):
        rc = 2 if g.last_rc == 2 else 1
        print("prepush-gate: {} at the preflight; no other step ran".format(
            "COULD NOT RUN" if rc == 2 else "FAILED"))
        return rc
    g.step("shipped lua BUILD stamps are current",
           ["uv", "run", "--frozen", "python", "scripts/stamp-mod-build.py", "--check"], cwd=PKG)
    g.step("shipped CLAUDE.md within its committed floor",
           ["uv", "run", "--frozen", "python", "gates/claude_md_budget.py", "--shipped"], cwd=PKG)
    guard_changes = sorted(p for p in changed if is_guard_path(p))
    skip_suites = bool(reused) and not guard_changes
    for cmd in HOOK_TESTS:
        name = " ".join(cmd[1:])
        if skip_suites and cmd is not HOOK_TESTS[0]:
            print("== {}\n   SKIPPED: no guard path changed since the pass record of tree {}".format(
                name, reused[0]["tree"][:12]))
            continue
        g.step(name, cmd)

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
    rel = [str(Path(f).relative_to("tools/x4validate").as_posix()) for f in files]
    if rel:
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
    # The record, LAST: only for a tree that is exactly HEAD's, unchanged from start to end.
    why_not = ("no base commit" if not base_sha else "the python/uv versions could not be read" if tools is None
               else "the tree was not clean at the start (untracked files count)" if not clean0
               else "HEAD's tree changed during the run" if head_tree() != tree0
               else "the tree was not clean at the end (untracked files count)" if not is_clean() else "")
    if why_not:
        print("   pass record NOT written: " + why_not)
    else:
        write_record(tree0, base_sha, tools, rel, "skipped" if skip_suites else "ran",
                     reused[0]["tree"] if reused else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

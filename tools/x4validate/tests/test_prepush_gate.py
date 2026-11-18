"""scripts/prepush-gate.py: a base it cannot resolve is a refusal, never "0 changed files, passed"."""
from __future__ import annotations

import importlib.util
import json
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


INSTALL = "tools/x4validate/tests/test_install_over_existing.py"
OTHER = "tools/x4validate/tests/test_other.py"


@pytest.fixture
def selecting(checkout, monkeypatch):
    """The gate pointed at a checkout holding a stand-in for the installer test (which writes a fixture CHANGELOG.md
    and runs install.sh) and another test that names CHANGELOG.md."""
    gate = _module()
    monkeypatch.setattr(gate, "REPO", checkout)
    monkeypatch.setattr(gate, "PKG", checkout / "tools" / "x4validate")
    (checkout / INSTALL).write_text("(dest / 'CHANGELOG.md').write_text('x')\nrun('install.sh')\n", encoding="utf-8")
    (checkout / OTHER).write_text("assert open('CHANGELOG.md').read()\n", encoding="utf-8")
    return gate


def test_RED_a_fixture_name_does_not_select_the_installer_tests_and_the_run_says_so(selecting, capsys):
    """MEASURED 2026-11-11: the installer tests were 2,209 s of the changed-file step's 2,575 s, selected on every push
    by the name of a fixture they write. Left out for that name alone, and never quietly."""
    assert selecting.referencing_tests(["CHANGELOG.md"]) == [OTHER]
    out = capsys.readouterr().out
    assert "not selected: " + INSTALL + " names CHANGELOG.md only as" in out
    # ...and on the path a real gate run takes (main calls tests_for, which reports what it left out itself):
    # 4.1.2 release review, MEASURED: with tests_for's own report removed, the four tests here stayed green.
    assert selecting.tests_for(["CHANGELOG.md"]) == [OTHER]
    assert capsys.readouterr().out.count("not selected: " + INSTALL) == 1


def test_TWIN_any_other_changed_name_still_selects_them(selecting, capsys):
    assert selecting.referencing_tests(["install.sh"]) == [INSTALL]
    assert selecting.referencing_tests(["docs/x.md", "CHANGELOG.md", "install.sh"]) == [INSTALL, OTHER]
    assert "not selected" not in capsys.readouterr().out


def test_TWIN_the_exemption_is_one_pair_not_a_name_or_a_file(selecting, capsys):
    # Another test naming CHANGELOG.md is still selected (above); the installer test's own change still runs it.
    assert selecting.tests_for([INSTALL]) == [INSTALL]
    assert selecting.tests_for(["CHANGELOG.md", INSTALL]) == [INSTALL, OTHER]
    assert "not selected" not in capsys.readouterr().out


def test_every_exempted_pair_still_describes_the_real_test():
    """A stale row excuses nothing it should: each exempted test exists and still spells that name."""
    gate = _module()
    assert gate.NOT_A_REFERENCE, "the table is empty: delete this test with it"
    for (rel, name), why in gate.NOT_A_REFERENCE.items():
        assert (REPO / rel).is_file(), rel
        assert name in (REPO / rel).read_text(encoding="utf-8", errors="replace"), (rel, name)
        assert why.strip()
    # ...and the installer row is a fixture write, not a read of the repository's file: the name appears once.
    text = (REPO / INSTALL).read_text(encoding="utf-8", errors="replace")
    assert text.count("CHANGELOG.md") == 1 and '(dest / "CHANGELOG.md").write_text(' in text


LOUD = "import sys\nprint('FIRST LINE')\nfor i in range(100): print('line', i)\nsys.exit(1)\n"


def _kept_path(out):
    return Path(next(l for l in out.splitlines() if "full output:" in l).split("full output:", 1)[1].strip())


def test_a_failed_step_keeps_its_full_output_and_says_where(checkout, capsys, monkeypatch):
    """The screen shows the last 40 lines. An rc2 failure's traceback fell off that, its temp folder went when the run
    ended, and a one-off test failure could then never be explained."""
    gate = _module()
    monkeypatch.setattr(gate, "REPO", checkout)
    g = gate.Gate()
    assert g.step("loud", [sys.executable, "-c", LOUD], cwd=checkout) is False
    out = capsys.readouterr().out
    assert "FIRST LINE" not in out, "the control: the screen really does cut this off"
    kept = _kept_path(out).read_text(encoding="utf-8")
    assert "FIRST LINE" in kept and "line 99" in kept and "loud" in kept


def test_TWIN_two_failed_steps_of_one_run_are_both_kept_and_a_new_run_starts_over(checkout, capsys, monkeypatch):
    gate = _module()
    monkeypatch.setattr(gate, "REPO", checkout)
    g = gate.Gate()
    g.step("one", [sys.executable, "-c", "import sys; print('ONE'); sys.exit(1)"], cwd=checkout)
    g.step("two", [sys.executable, "-c", "import sys; print('TWO'); sys.exit(1)"], cwd=checkout)
    path = _kept_path(capsys.readouterr().out)
    kept = path.read_text(encoding="utf-8")
    assert "ONE" in kept and "TWO" in kept
    gate.Gate().step("three", [sys.executable, "-c", "import sys; print('THREE'); sys.exit(1)"], cwd=checkout)
    kept = path.read_text(encoding="utf-8")
    assert "THREE" in kept and "ONE" not in kept


def test_TWIN_a_passing_step_keeps_nothing(checkout, capsys, monkeypatch):
    gate = _module()
    monkeypatch.setattr(gate, "REPO", checkout)
    assert gate.Gate().step("quiet", [sys.executable, "-c", "print('fine')"], cwd=checkout) is True
    assert "full output:" not in capsys.readouterr().out
    assert not list((checkout / ".git").glob("prepush-gate*"))


def test_TWIN_output_that_cannot_be_kept_says_so_and_the_step_still_fails(tmp_path, capsys, monkeypatch):
    gate = _module()
    monkeypatch.setattr(gate, "REPO", tmp_path)                 # not a repository: there is no git directory to keep it in
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    g = gate.Gate()
    assert g.step("loud", [sys.executable, "-c", LOUD], cwd=tmp_path) is False
    out = capsys.readouterr().out
    assert "full output NOT kept" in out and "line 99" in out and g.failed == ["loud"]


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


# ------------------------------------------------------------------ 4.3: preflight first, pass records, hook-suite skip
ANCHORS = "scripts/verify-hook-tests.py --check-anchors"
SUITES = (".claude/hooks/test_hook_facts.py", ".claude/hooks/test_audit0924_hooks.py", ".claude/hooks/test-protect-bash.sh")
TOOLS = {"python": "3.13.9 (test)", "uv": "uv 9.9.9 (test)"}


def _commit(repo, rel, text="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "change " + rel)
    return _git(repo, "rev-parse", "HEAD^{tree}")


class Recorder:
    """Gate.step without running anything: names the steps run, fails the ones named in `fail` with its rc."""

    def __init__(self):
        self.ran, self.cmds, self.fail, self.during = [], {}, {}, None

    def install(self, gate, monkeypatch):
        rec = self

        def step(self, name, cmd, cwd=None, check=None):
            rec.ran.append(name)
            rec.cmds[name] = cmd
            if rec.during:
                rec.during(name)
            rc = next((v for k, v in rec.fail.items() if k in name), 0)
            self.last_rc = rc
            if rc:
                self.failed.append(name)
            return rc == 0
        monkeypatch.setattr(gate.Gate, "step", step)


@pytest.fixture
def recording(checkout, monkeypatch):
    gate = _module()
    real = subprocess.run

    def run(cmd, *a, **k):
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real(cmd, *a, **k)
    monkeypatch.setattr(gate.subprocess, "run", run)
    monkeypatch.setattr(gate, "toolchain", lambda: dict(TOOLS))
    r = Recorder()
    r.install(gate, monkeypatch)
    base = _git(checkout, "rev-parse", "HEAD")
    _commit(checkout, "tools/x4validate/tests/test_a.py", "assert True\n")

    def go(*extra, base_rev=base):
        r.ran.clear()
        r.cmds.clear()
        return gate.main(["--root", str(checkout), "--base", base_rev, *extra])
    return gate, r, go, checkout


def _passes(repo):
    return sorted((repo / ".git" / "prepush-gate" / "passes").glob("*.json"))


def _suites_ran(r):
    return [s for s in SUITES if any(s in n for n in r.ran)]


def test_RED_the_preflight_is_the_first_step_and_a_failure_stops_the_gate(recording):
    gate, r, go, repo = recording
    r.fail = {"preflight": 1}
    assert go() == 1
    assert len(r.ran) == 1 and "preflight" in r.ran[0], r.ran
    assert r.cmds[r.ran[0]][-1] == str(repo.resolve()), "the preflight checks the tree being pushed"


def test_TWIN_a_preflight_that_could_not_run_is_exit_2(recording):
    gate, r, go, repo = recording
    r.fail = {"preflight": 2}
    assert go() == 2
    assert len(r.ran) == 1


def test_TWIN_a_passing_preflight_goes_on_to_every_step(recording):
    gate, r, go, repo = recording
    assert go() == 0
    assert "preflight" in r.ran[0] and ANCHORS in r.ran and _suites_ran(r) == list(SUITES)


def test_RED_a_passing_run_on_a_clean_tree_writes_its_pass_record(recording):
    gate, r, go, repo = recording
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    assert go() == 0
    files = _passes(repo)
    assert [f.name for f in files] == [tree + ".json"]
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["tree"] == tree and rec["status"] == "passed"
    assert rec["base"] == _git(repo, "rev-parse", "HEAD~1")
    assert rec["gate_sha256"] == gate.gate_sha256() and rec["selection_version"] == gate.SELECTION_VERSION
    assert rec["python"] == TOOLS["python"] and rec["uv"] == TOOLS["uv"]
    assert rec["selected_tests"] == ["tests/test_a.py"]


def test_TWIN_a_failed_run_writes_no_record(recording):
    gate, r, go, repo = recording
    r.fail = {"changed-file tests": 1}
    assert go() == 1
    assert _passes(repo) == []


def test_TWIN_a_dirty_tree_writes_no_record(recording):
    gate, r, go, repo = recording
    (repo / "untracked.txt").write_text("u")                      # untracked counts: the gate ran on it
    assert go() == 0
    assert _passes(repo) == []


def test_TWIN_a_tree_dirty_at_the_start_and_cleaned_during_the_run_writes_no_record(recording):
    """The steps ran on the dirty tree; that it is clean at the end does not make the result HEAD's (hand mutant
    2026-11-18: with only a tree dirty throughout, dropping the start check survived)."""
    gate, r, go, repo = recording
    (repo / "untracked.txt").write_text("u")
    r.during = lambda name: (repo / "untracked.txt").unlink() if "collects" in name else None
    assert go() == 0
    assert _passes(repo) == []


def test_TWIN_a_tree_dirtied_during_the_run_writes_no_record(recording):
    gate, r, go, repo = recording
    r.during = lambda name: (repo / "late.txt").write_text("l") if "collects" in name else None
    assert go() == 0
    assert _passes(repo) == []


def test_TWIN_HEAD_moved_during_the_run_writes_no_record(recording):
    gate, r, go, repo = recording
    r.during = lambda name: _commit(repo, "moved.txt") if "collects" in name else None
    assert go() == 0
    assert _passes(repo) == []


def test_TWIN_unknown_toolchain_versions_write_no_record(recording, monkeypatch):
    gate, r, go, repo = recording
    monkeypatch.setattr(gate, "toolchain", lambda: None)
    assert go() == 0
    assert _passes(repo) == []


def test_RED_a_valid_record_is_reused_and_a_non_guard_change_skips_the_hook_suites(recording, capsys):
    gate, r, go, repo = recording
    t1 = _git(repo, "rev-parse", "HEAD^{tree}")
    assert go() == 0
    _commit(repo, "docs/note.md")
    capsys.readouterr()
    assert go() == 0
    out = capsys.readouterr().out
    assert ANCHORS in r.ran, "--check-anchors always runs"
    assert _suites_ran(r) == [], r.ran
    for s in SUITES:
        assert "SKIPPED" in out and s in out
    assert t1[:12] in out and "1 changed file(s)" in out
    assert not any("changed-file tests" in n for n in r.ran), "test_a.py was tested at the recorded tree"
    assert len(_passes(repo)) == 2, "the new tree gets its own record"


def _second_run_after(repo, go, rel="docs/note.md"):
    assert go() == 0
    _commit(repo, rel)


@pytest.mark.parametrize("field,value", [("gate_sha256", "0" * 64), ("selection_version", -1),
                                         ("python", "3.12.0 other"), ("uv", "uv 0.0.1 other"),
                                         ("status", "failed")])
def test_TWIN_a_record_with_another_identity_is_not_reused(recording, field, value, capsys):
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    (f,) = _passes(repo)
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec[field] = value
    f.write_text(json.dumps(rec), encoding="utf-8")
    assert go() == 0
    assert _suites_ran(r) == list(SUITES), (field, r.ran)
    assert any("changed-file tests" in n for n in r.ran), "the changed set is the whole push again"


def test_TWIN_a_record_for_another_base_is_not_reused(recording):
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    assert go(base_rev=_git(repo, "rev-parse", "HEAD~1")) == 0
    assert _suites_ran(r) == list(SUITES)


def test_TWIN_a_record_whose_tree_object_is_gone_is_not_reused(recording):
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    (f,) = _passes(repo)
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["tree"] = "e" * 40
    f.unlink()
    (f.parent / (rec["tree"] + ".json")).write_text(json.dumps(rec), encoding="utf-8")
    assert go() == 0
    assert _suites_ran(r) == list(SUITES)


@pytest.mark.parametrize("name", ["absent", "existing"])
def test_TWIN_a_record_filed_under_another_trees_name_is_not_reused(recording, name):
    """`existing`: named after a tree that IS in the repository (the base's), so the tree-object check cannot be what
    refuses it (hand mutant 2026-11-18: with only the absent name, dropping the name check survived)."""
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    (f,) = _passes(repo)
    other = "d" * 40 if name == "absent" else _git(repo, "rev-parse", "HEAD~2^{tree}")
    f.rename(f.parent / (other + ".json"))
    assert go() == 0
    assert _suites_ran(r) == list(SUITES)


def test_TWIN_an_unreadable_record_is_not_reused_and_does_not_crash(recording):
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    (f,) = _passes(repo)
    f.write_text("{not json", encoding="utf-8")
    assert go() == 0
    assert _suites_ran(r) == list(SUITES)


@pytest.mark.parametrize("rel", ["tools/x4validate/uv.lock", "tools/x4validate/pyproject.toml"])
def test_TWIN_a_dependency_change_since_the_record_is_not_reused(recording, rel):
    gate, r, go, repo = recording
    _second_run_after(repo, go, rel)
    assert go() == 0
    assert _suites_ran(r) == list(SUITES)


@pytest.mark.parametrize("rel", [".claude/hooks/x.sh", ".codex/hooks/x.py", ".opencode/plugins/x.js", "agent/x.md",
                                 "scripts/verify-hook-tests.py", "scripts/test-hooks.sh", "scripts/gitbash.py"])
def test_TWIN_a_guard_change_since_the_record_runs_the_hook_suites(recording, rel):
    gate, r, go, repo = recording
    _second_run_after(repo, go, rel)
    assert go() == 0
    assert _suites_ran(r) == list(SUITES), rel


def test_TWIN_no_reuse_runs_everything_over_a_valid_record(recording, capsys):
    gate, r, go, repo = recording
    _second_run_after(repo, go)
    assert go("--no-reuse") == 0
    assert _suites_ran(r) == list(SUITES)
    assert any("changed-file tests" in n for n in r.ran)
    assert "--no-reuse" in capsys.readouterr().out


def test_the_closest_record_is_the_one_used(recording, capsys):
    gate, r, go, repo = recording
    assert go() == 0
    _commit(repo, "docs/a.md")
    _commit(repo, "docs/b.md")
    t_mid = _git(repo, "rev-parse", "HEAD^{tree}")
    assert go() == 0                                               # reuses the first; records t_mid
    _commit(repo, "docs/c.md")
    capsys.readouterr()
    assert go() == 0
    out = capsys.readouterr().out
    assert t_mid[:12] in out and "1 changed file(s)" in out, out


def test_a_record_is_written_only_after_every_step(recording, monkeypatch):
    """Written LAST: a run killed (rb watchdog, Ctrl-C) during any step leaves no record."""
    gate, r, go, repo = recording

    def killed(name):
        if "changed-file tests" in name:
            raise KeyboardInterrupt
    r.during = killed
    with pytest.raises(KeyboardInterrupt):
        go()
    assert _passes(repo) == []


def test_the_guard_paths_cover_every_hook_suite_input():
    gate = _module()
    for p in (".claude/", ".codex/", ".opencode/", "agent/", "scripts/verify-hook-tests.py", "scripts/test-hooks.sh",
              "scripts/gitbash.py", "scripts/prepush-gate.py"):
        assert gate.is_guard_path(p + ("x" if p.endswith("/") else "")), p
    assert not gate.is_guard_path("docs/x.md") and not gate.is_guard_path("agents/x")


def test_RED_the_gate_runs_the_shipped_claude_md_floor(recording):
    """Issue #21: the shipped CLAUDE.md against its committed floor, every push, in tools/x4validate."""
    gate, r, go, repo = recording
    assert go() == 0
    cmds = [c for c in r.cmds.values() if "gates/claude_md_budget.py" in c]
    assert cmds == [["uv", "run", "--frozen", "python", "gates/claude_md_budget.py", "--shipped"]], r.cmds

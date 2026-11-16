"""deploy-claude-dir.py: what proves guard code before it goes live, and what a copy without git is told.

User rule 2026-11-16: CI gates releases, not local work. Guard code reaches a game root as a COMMIT (uncommitted guard
sources refuse before anything runs) whose local hook tests (`bash scripts/test-hooks.sh`) exit 0. GitHub is not asked.
The runner is stubbed here: the real suite takes minutes, so only a one-line fake test-hooks.sh ever runs for real.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("deploy_claude_dir_evidence", PKG / "scripts" / "deploy-claude-dir.py")
dep = importlib.util.module_from_spec(_spec)
sys.modules["deploy_claude_dir_evidence"] = dep
_spec.loader.exec_module(dep)

FAKE_BASH = "fake-bash-for-the-hook-tests"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / ".claude" / "hooks").mkdir(parents=True)
    (r / "agent" / "guards").mkdir(parents=True)
    (r / "scripts").mkdir()
    _git(tmp_path, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    (r / "scripts" / "test-hooks.sh").write_bytes(b"exit 0\n")
    _git(r, "add", ".claude/hooks/h.sh", "scripts/test-hooks.sh")
    _git(r, "commit", "-q", "-m", "c")
    return r


@pytest.fixture(autouse=True)
def _github_is_never_asked(monkeypatch):
    """Every test here: a `gh` call fails the test. The CI proof is gone, not merely unused."""
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "gh":
            pytest.fail(f"GitHub was asked: {cmd}")
        return real(cmd, *a, **k)
    monkeypatch.setattr(dep.subprocess, "run", fake)


def _hooks(monkeypatch, rc=0, raises=None, output=b"", during=None):
    """Stub the hook-test run: a fake bash, and subprocess.run answering for it. Returns the recorded calls."""
    monkeypatch.setattr(dep, "_find_bash", lambda repo_root: (FAKE_BASH, ""))
    real = dep.subprocess.run       # the autouse gh tripwire
    calls = []

    def fake(cmd, *a, **k):
        if cmd[0] != FAKE_BASH:
            return real(cmd, *a, **k)
        calls.append((cmd, k))
        if during:
            during()
        if raises:
            raise raises
        k["stdout"].write(output)
        return subprocess.CompletedProcess(cmd, rc)
    monkeypatch.setattr(dep.subprocess, "run", fake)
    return calls


# ---------------------------------------------------------------- the hook-test gate, one twin per clause

def test_passing_hook_tests_are_proof(repo, monkeypatch, capsys):
    calls = _hooks(monkeypatch, rc=0)
    assert dep.guard_evidence(repo) is None
    assert len(calls) == 1
    cmd, k = calls[0]
    assert cmd == [FAKE_BASH, "scripts/test-hooks.sh"]
    assert Path(k["cwd"]) == repo and k["timeout"] == dep.HOOK_TESTS_TIMEOUT_S
    assert "Running the hook tests" in capsys.readouterr().err     # the one progress line before a slow run


def test_failing_hook_tests_are_a_refusal_naming_the_exit_and_showing_the_tail(repo, monkeypatch, capsys):
    lines = [f"line {i}" for i in range(60)] + ["RESULT: 473 passed, 1 failed"]
    _hooks(monkeypatch, rc=3, output=("\n".join(lines) + "\n").encode())
    why = dep.guard_evidence(repo)
    assert why and "exited 3" in why and "failed" in why
    err = capsys.readouterr().err
    assert "RESULT: 473 passed, 1 failed" in err and "(exit 3)" in err
    assert "line 0\n" not in err                                   # the tail, not the whole log


@pytest.mark.parametrize("rc", [1, 2, 124, -9])
def test_any_nonzero_exit_is_a_refusal(repo, monkeypatch, rc):
    _hooks(monkeypatch, rc=rc)
    assert f"exited {rc}" in dep.guard_evidence(repo)


def test_a_timeout_is_a_refusal(repo, monkeypatch):
    _hooks(monkeypatch, raises=subprocess.TimeoutExpired([FAKE_BASH], dep.HOOK_TESTS_TIMEOUT_S))
    why = dep.guard_evidence(repo)
    assert why and "timed out" in why


def test_bash_that_will_not_start_is_a_refusal(repo, monkeypatch):
    _hooks(monkeypatch, raises=FileNotFoundError(FAKE_BASH))
    why = dep.guard_evidence(repo)
    assert why and "could not start" in why


def test_no_usable_bash_is_a_refusal_and_nothing_runs(repo, monkeypatch):
    calls = _hooks(monkeypatch)
    monkeypatch.setattr(dep, "_find_bash", lambda repo_root: (None, "setx X4_BASH ..."))
    why = dep.guard_evidence(repo)
    assert why and "no usable bash" in why and "setx X4_BASH" in why
    assert not calls


def test_a_checkout_without_the_hook_suite_is_a_refusal(repo, monkeypatch):
    calls = _hooks(monkeypatch)
    _git(repo, "rm", "-q", "scripts/test-hooks.sh")
    _git(repo, "commit", "-q", "-m", "no suite")
    why = dep.guard_evidence(repo)
    assert why and "test-hooks.sh" in why
    assert not calls


@pytest.mark.parametrize("edit", ["modified", "untracked"])
def test_uncommitted_guard_edits_are_refused_before_the_hook_tests_run(repo, monkeypatch, edit):
    calls = _hooks(monkeypatch, rc=0)
    if edit == "modified":
        (repo / ".claude" / "hooks" / "h.sh").write_bytes(b"echo edited\n")
    else:
        (repo / "agent" / "guards" / "new.py").write_bytes(b"x = 1\n")
    assert "differ from HEAD" in dep.guard_evidence(repo)
    assert not calls


@pytest.mark.parametrize("rel", ["scripts/test-hooks.sh", "scripts/gitbash.py", "tools/modding_harness/x.py",
                                 "tools/x4validate/scripts/deploy-claude-dir.py", "tools/x4_harness/x.py",
                                 ".codex/hooks/x.py", ".claude/settings.json"])
def test_the_verifier_and_its_inputs_are_guard_sources(repo, monkeypatch, rel):
    """Review I3: an uncommitted edit of the suite (EXPECT lowered, an early `exit 0`) or of what it runs under passed."""
    calls = _hooks(monkeypatch, rc=0)
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"# edited, uncommitted\nexit 0\n")
    assert "differ from HEAD" in dep.guard_evidence(repo)
    assert not calls


def test_a_non_guard_edit_is_not_refused(repo, monkeypatch):
    # The twin: the clean check is scoped to the guard sources, not the whole tree.
    calls = _hooks(monkeypatch, rc=0)
    (repo / "README.md").write_bytes(b"docs\n")
    assert dep.guard_evidence(repo) is None and len(calls) == 1


def test_a_pass_prints_the_suites_result_line(repo, monkeypatch, capsys):
    _hooks(monkeypatch, rc=0, output=b"  OK   a\nRESULT: 474 passed, 0 failed, 0 skipped\n")
    assert dep.guard_evidence(repo) is None
    assert "Hook tests passed: RESULT: 474 passed, 0 failed, 0 skipped" in capsys.readouterr().err


def test_a_pass_without_a_result_line_says_so(repo, monkeypatch, capsys):
    _hooks(monkeypatch, rc=0, output=b"nothing useful\n")
    assert dep.guard_evidence(repo) is None
    assert "no RESULT line" in capsys.readouterr().err


# ---------------------------------------------------------------- review I1: the tested state must still be there

def test_a_guard_edit_made_while_the_hook_tests_run_is_refused_and_not_deployed(repo, tmp_path, monkeypatch, capsys):
    """Adapted from the review's probe: the suite passed on the commit, then the deploy read the edited working tree
    and wrote b'echo UNTESTED' with rc 0."""
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0, during=lambda: (repo / ".claude" / "hooks" / "h.sh").write_bytes(b"echo UNTESTED\n"))
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "after the hook tests ran" in err and "Re-run the deploy" in err
    assert not (dest / "hooks" / "h.sh").exists()


def test_head_moving_while_the_hook_tests_run_is_refused(repo, tmp_path, monkeypatch, capsys):
    def commit_another():
        (repo / ".claude" / "hooks" / "h.sh").write_bytes(b"echo UNTESTED\n")
        _git(repo, "commit", "-q", "-am", "landed during the run")
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0, during=commit_another)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "HEAD moved" in err and "untested" in err
    assert not (dest / "hooks" / "h.sh").exists()


def test_nothing_changing_during_the_run_still_deploys_the_committed_bytes(repo, tmp_path, monkeypatch):
    # The control for the two above: the same flow with no edit.
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0, during=lambda: None)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "hooks" / "h.sh").read_bytes() == b"echo hi\n"


# ---------------------------------------------------------------- review M3: the resolver of the repo being deployed

def test_the_bash_resolver_comes_from_the_repo_being_deployed(repo):
    bash, why = dep._find_bash(repo)              # the scratch repo has no scripts/gitbash.py
    assert bash is None and "gitbash.py" in why and str(repo) in why


def test_a_broken_resolver_is_a_reason_not_a_traceback(repo):
    (repo / "scripts" / "gitbash.py").write_bytes(b"raise RuntimeError('broken resolver')\n")
    bash, why = dep._find_bash(repo)
    assert bash is None and "RuntimeError" in why and "broken resolver" in why


# ---------------------------------------------------------------- review I2: what the hook tests do not cover

def _act(name, kind="update"):
    return dep.Action(name, kind)


def test_codex_only_guard_files_are_named_and_identical_copies_are_not(repo):
    (repo / ".codex" / "hooks").mkdir(parents=True)
    (repo / ".codex" / "rules").mkdir(parents=True)
    (repo / ".codex" / "hooks" / "h.sh").write_bytes(b"echo hi\n")              # byte-identical to .claude/hooks/h.sh
    (repo / ".codex" / "hooks" / "codex_adapter.py").write_bytes(b"x = 1\n")    # Codex-only
    (repo / ".codex" / "rules" / "x4.rules").write_bytes(b"rule\n")
    codex = [_act(".codex/hooks/h.sh"), _act(".codex/hooks/codex_adapter.py"), _act(".codex/rules/x4.rules"),
             _act(".codex/hooks.json"), _act(".agents/skills/a/SKILL.md"), _act(".codex/hooks/x.py", "identical")]
    claude = [_act("hooks/h.sh"), _act("skills/a/SKILL.md")]
    got = dep.untested_guards(repo, [(dep.parity.CODEX, codex), (dep.parity.CLAUDE, claude)])
    assert got == [".codex/hooks/codex_adapter.py", ".codex/rules/x4.rules", ".codex/hooks.json"]


def test_a_changed_settings_json_is_named(repo):
    got = dep.untested_guards(repo, [(dep.parity.CLAUDE, [_act("settings.json"), _act("hooks/h.sh")])])
    assert got == [".claude/settings.json (its hook wiring)"]


def test_the_note_names_codex_files_and_their_tests_and_does_not_refuse(repo, tmp_path, monkeypatch, capsys):
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0)
    monkeypatch.setattr(dep, "untested_guards", lambda r, ja: [".codex/hooks/codex_adapter.py"])
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    err = capsys.readouterr().err
    assert "NOTE: the hook tests do not exercise 1 changed Codex guard file(s): .codex/hooks/codex_adapter.py" in err
    assert "test_codex_adapter.py" in err and "settings.json" not in err


def test_the_settings_note_appears_for_a_settings_change(repo, tmp_path, monkeypatch, capsys):
    (repo / ".claude" / "settings.json").write_bytes(b"{}\n")
    _git(repo, "add", ".claude/settings.json")
    _git(repo, "commit", "-q", "-m", "settings")
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    err = capsys.readouterr().err
    assert "changes .claude/settings.json" in err and "Codex guard file" not in err


def test_no_note_when_only_tested_hooks_change(repo, tmp_path, monkeypatch, capsys):
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    _hooks(monkeypatch, rc=0)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert "NOTE:" not in capsys.readouterr().err


def test_the_override_help_matches_the_rule():
    src = (PKG / "scripts" / "deploy-claude-dir.py").read_text(encoding="utf-8")
    block = src[src.index('"--unverified-guards"'):src.index('"--repo"')]
    assert "hook tests" in block and "test-hooks.sh" in block and "already-reviewed" in block
    assert "CI" not in block and "origin/master" not in block


# ---------------------------------------------------------------- the real runner, on a one-line suite

def _real_bash(monkeypatch):
    """The bash this toolkit resolves; a scratch repo has no scripts/gitbash.py of its own."""
    bash, _ = dep._find_bash(dep.REPO_ROOT)
    if not bash:
        pytest.skip("no usable bash on this machine")
    monkeypatch.setattr(dep, "_find_bash", lambda repo_root: (bash, ""))
    return bash


@pytest.mark.parametrize("body, expect_pass", [(b"echo all good\nexit 0\n", True),
                                               (b"echo first\necho LAST-LINE\nexit 7\n", False)])
def test_the_real_runner_reads_the_exit_of_a_real_bash(repo, monkeypatch, capsys, body, expect_pass):
    """No stub: the real subprocess call, output file and exit code, against a fake test-hooks.sh."""
    _real_bash(monkeypatch)
    (repo / "scripts" / "test-hooks.sh").write_bytes(body)
    why = dep.run_hook_tests(repo)
    if expect_pass:
        assert why is None
    else:
        assert why and "exited 7" in why
        assert "LAST-LINE" in capsys.readouterr().err


# ---------------------------------------------------------------- M7: no git, or not a checkout

def _deployable(tmp_path, repo):
    dest = tmp_path / "game" / ".claude"
    dest.mkdir(parents=True)
    return dest


def _no_git(monkeypatch):
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd[0] == "git":
            raise FileNotFoundError("git")
        return real(cmd, *a, **k)
    monkeypatch.setattr(dep.subprocess, "run", fake)


def test_no_git_on_path_is_a_refusal_not_a_traceback(repo, tmp_path, monkeypatch, capsys):
    dest = _deployable(tmp_path, repo)
    calls = _hooks(monkeypatch)
    _no_git(monkeypatch)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "git" in err and "--unverified-guards" in err and "hook tests" not in err.split("Nothing was written")[1]
    assert not (dest / "hooks" / "h.sh").exists()
    assert not calls


def test_committed_versions_survive_git_vanishing_or_hanging(repo, monkeypatch):
    real = subprocess.run
    calls = []

    def fake(cmd, *a, **k):
        if cmd[0] == "git" and "show" in cmd:
            calls.append(cmd)
            raise subprocess.TimeoutExpired(cmd, 60)
        return real(cmd, *a, **k)
    monkeypatch.setattr(dep.subprocess, "run", fake)
    assert dep.committed_versions(repo, ".claude/hooks/h.sh") == []
    assert calls


def test_a_real_checkout_git_cannot_read_is_not_a_release_zip(repo, tmp_path, capsys):
    """rc != 0 from rev-parse was read as "no checkout", so a damaged or refused repository was told it was a zip install,
    and the zip's advice is to use the override. REAL git on a REAL damaged repository."""
    (repo / ".git" / "HEAD").write_bytes(b"not a ref\n")
    why = dep.guard_evidence(repo)
    assert why and "not a git checkout" not in why and "git could not read" in why
    dest = _deployable(tmp_path, repo)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "Fix what git says" in err and "failing hook tests" not in err and "naming the release" not in err


def test_dubious_ownership_is_said_as_itself(repo, monkeypatch):
    """Git refuses a repository another account owns; the same rc as "no repository" with a different sentence."""
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd[0] == "git" and "--show-toplevel" in cmd:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: detected dubious ownership in repository at 'x'\n")
        return real(cmd, *a, **k)
    monkeypatch.setattr(dep.subprocess, "run", fake)
    assert "dubious ownership" in dep.guard_evidence(repo)


def test_a_zip_install_is_told_what_to_do_not_to_commit(tmp_path, monkeypatch, capsys):
    unzipped = tmp_path / "toolkit"
    (unzipped / ".claude" / "hooks").mkdir(parents=True)
    (unzipped / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    calls = _hooks(monkeypatch)
    dest = _deployable(tmp_path, unzipped)
    assert dep.main(["--repo", str(unzipped), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "not a git checkout" in err and "--unverified-guards" in err and "naming the release" in err
    assert "failing hook tests" not in err and "Commit the guard change" not in err
    assert not (dest / "hooks" / "h.sh").exists()
    assert not calls


def test_a_zip_unpacked_inside_another_repo_does_not_borrow_its_head(tmp_path, monkeypatch):
    # The game root is a git repository of its own. Its HEAD says nothing about this copy's guard code.
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(tmp_path, "init", "-q", str(outer))
    _git(outer, "config", "user.email", "t@example.invalid")
    _git(outer, "config", "user.name", "t")
    (outer / "x").write_bytes(b"x")
    _git(outer, "add", "x")
    _git(outer, "commit", "-q", "-m", "c")
    inner = outer / "toolkit"
    (inner / ".claude" / "hooks").mkdir(parents=True)
    calls = _hooks(monkeypatch, rc=0)
    assert "not a git checkout" in dep.guard_evidence(inner)
    assert not calls


def test_a_checkout_with_failing_hook_tests_is_told_to_fix_them(repo, tmp_path, monkeypatch, capsys):
    # The twin of the zip message: a real checkout gets the instruction that works for it.
    dest = _deployable(tmp_path, repo)
    _hooks(monkeypatch, rc=1)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "Fix the failing hook tests" in err and "pull request" not in err and "naming the release" not in err
    assert not (dest / "hooks" / "h.sh").exists()


def test_a_checkout_with_uncommitted_guard_edits_is_told_to_commit_them(repo, tmp_path, monkeypatch, capsys):
    dest = _deployable(tmp_path, repo)
    calls = _hooks(monkeypatch, rc=0)
    (repo / ".claude" / "hooks" / "h.sh").write_bytes(b"echo edited\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    assert "Commit the guard change" in capsys.readouterr().err
    assert not calls and not (dest / "hooks" / "h.sh").exists()


def test_a_checkout_with_passing_hook_tests_deploys(repo, tmp_path, monkeypatch, capsys):
    dest = _deployable(tmp_path, repo)
    calls = _hooks(monkeypatch, rc=0)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "hooks" / "h.sh").read_bytes() == b"echo hi\n"
    assert len(calls) == 1 and "UNVERIFIED" not in capsys.readouterr().err


def test_the_override_still_works_for_a_zip_install(tmp_path, capsys):
    unzipped = tmp_path / "toolkit"
    (unzipped / ".claude" / "hooks").mkdir(parents=True)
    (unzipped / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    dest = _deployable(tmp_path, unzipped)
    assert dep.main(["--repo", str(unzipped), "--dest", str(dest), "--apply",
                     "--unverified-guards", "release 4.1.0 zip"]) == 0
    assert (dest / "hooks" / "h.sh").read_bytes() == b"echo hi\n"
    assert "DEPLOYING UNVERIFIED GUARD CODE" in capsys.readouterr().err

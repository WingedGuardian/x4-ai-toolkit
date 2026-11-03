"""deploy-claude-dir.py: what proves guard code before it goes live, and what a copy without git is told.

GitHub is asked first. "It is on origin/master" is only the fallback, and it says so when it is used: master can hold a
red commit, and a push's CI runs for the pushed head only, so older master commits have no run of their own.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("deploy_claude_dir_evidence", PKG / "scripts" / "deploy-claude-dir.py")
dep = importlib.util.module_from_spec(_spec)
sys.modules["deploy_claude_dir_evidence"] = dep
_spec.loader.exec_module(dep)


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / ".claude" / "hooks").mkdir(parents=True)
    (r / "agent" / "guards").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    _git(r, "add", ".claude/hooks/h.sh")
    _git(r, "commit", "-q", "-m", "c")
    return r


def _publish(repo):
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")


def _gh(monkeypatch, stdout="[]", rc=0, raises=None):
    real = subprocess.run
    asked = []

    def fake(cmd, *a, **k):
        if cmd[0] != "gh":
            return real(cmd, *a, **k)
        asked.append(cmd)
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="gh: not logged in")
    monkeypatch.setattr(dep.subprocess, "run", fake)
    return asked


def test_github_is_asked_first_even_for_a_published_commit(repo, monkeypatch, capsys):
    _publish(repo)
    asked = _gh(monkeypatch, stdout='[{"conclusion": "success"}]')
    assert dep.guard_evidence(repo) is None
    assert asked and "--commit" in asked[0]
    assert "origin/master" not in capsys.readouterr().err       # no fallback, no note


def test_a_red_commit_on_master_is_not_proof(repo, monkeypatch):
    _publish(repo)
    _gh(monkeypatch, stdout='[{"conclusion": "failure"}]')
    why = dep.guard_evidence(repo)
    assert why and "failed" in why


def test_failure_then_success_on_the_commit_is_proof(repo, monkeypatch):
    _gh(monkeypatch, stdout='[{"conclusion": "failure"}, {"conclusion": "success"}]')
    assert dep.guard_evidence(repo) is None


@pytest.mark.parametrize("gh", [dict(stdout="[]"), dict(rc=1), dict(raises=FileNotFoundError("gh")),
                                dict(stdout="not json"), dict(stdout='[{"conclusion": ""}]')])
def test_no_answer_on_a_published_commit_falls_back_and_prints_a_note(repo, monkeypatch, capsys, gh):
    _publish(repo)
    _gh(monkeypatch, **gh)
    assert dep.guard_evidence(repo) is None
    err = capsys.readouterr().err
    assert "NOTE" in err and "origin/master" in err


@pytest.mark.parametrize("gh", [dict(stdout="[]"), dict(rc=1), dict(raises=FileNotFoundError("gh")),
                                dict(stdout="not json"), dict(stdout='[{"conclusion": "failure"}]')])
def test_no_answer_on_an_unpublished_commit_is_a_refusal(repo, monkeypatch, capsys, gh):
    _gh(monkeypatch, **gh)
    assert dep.guard_evidence(repo)
    assert "NOTE" not in capsys.readouterr().err


def test_uncommitted_guard_edits_are_refused_before_anything_is_asked(repo, monkeypatch):
    _publish(repo)
    asked = _gh(monkeypatch, stdout='[{"conclusion": "success"}]')
    (repo / ".claude" / "hooks" / "h.sh").write_bytes(b"echo edited\n")
    assert "differ from HEAD" in dep.guard_evidence(repo)
    assert not asked


def test_the_override_help_matches_the_rule():
    src = (PKG / "scripts" / "deploy-claude-dir.py").read_text(encoding="utf-8")
    block = src[src.index('"--unverified-guards"'):src.index('"--repo"')]
    assert "successful CI run" in block and "origin/master" in block


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
    _no_git(monkeypatch)
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "git" in err and "--unverified-guards" in err and "ci/ branch" not in err
    assert not (dest / "hooks" / "h.sh").exists()


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


def test_a_zip_install_is_told_what_to_do_not_to_push(tmp_path, capsys):
    unzipped = tmp_path / "toolkit"
    (unzipped / ".claude" / "hooks").mkdir(parents=True)
    (unzipped / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    dest = _deployable(tmp_path, unzipped)
    assert dep.main(["--repo", str(unzipped), "--dest", str(dest), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "not a git checkout" in err and "--unverified-guards" in err
    assert "ci/ branch" not in err
    assert not (dest / "hooks" / "h.sh").exists()


def test_a_zip_unpacked_inside_another_repo_does_not_borrow_its_head(tmp_path, monkeypatch, capsys):
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
    asked = _gh(monkeypatch, stdout='[{"conclusion": "success"}]')
    assert "not a git checkout" in dep.guard_evidence(inner)
    assert not asked


def test_a_git_checkout_is_still_told_to_push_a_ci_branch(repo, tmp_path, monkeypatch, capsys):
    # The twin of the zip message: a real checkout keeps the instruction that works for it.
    dest = _deployable(tmp_path, repo)
    _gh(monkeypatch, stdout="[]")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 2
    assert "ci/ branch" in capsys.readouterr().err


def test_the_override_still_works_for_a_zip_install(tmp_path, capsys):
    unzipped = tmp_path / "toolkit"
    (unzipped / ".claude" / "hooks").mkdir(parents=True)
    (unzipped / ".claude" / "hooks" / "h.sh").write_bytes(b"echo hi\n")
    dest = _deployable(tmp_path, unzipped)
    assert dep.main(["--repo", str(unzipped), "--dest", str(dest), "--apply",
                     "--unverified-guards", "release 4.1.0 zip"]) == 0
    assert (dest / "hooks" / "h.sh").read_bytes() == b"echo hi\n"
    assert "DEPLOYING UNVERIFIED GUARD CODE" in capsys.readouterr().err

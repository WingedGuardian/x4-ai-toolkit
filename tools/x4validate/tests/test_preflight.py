"""scripts/preflight.py: the push preflight the pre-push gate runs first.

It runs the checks CI rejects in its first minutes. The one this file is about is the SHARED ci-policy contract: it
must be the exact file the workflows pin, and when no copy of it can be had the check COULD NOT RUN (exit 2), never a
pass. Its sources are a cache in the git directory, a local clone named by X4_HARNESS_CLONE, and the pinned raw URL.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _layout import require_repo

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "preflight.py"
SHA = "1" * 40


def _module():
    require_repo("scripts/preflight.py", why="the maintainers' push preflight is not shipped")
    spec = importlib.util.spec_from_file_location("preflight_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["preflight_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def _repo(path):
    path.mkdir(parents=True)
    _git(path.parent, "init", "-q", str(path))
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    return path


PASSES = "def check_contract(root, profile):\n    return {'profile': profile}\n"
REFUSES = "def check_contract(root, profile):\n    raise ValueError('unbounded runner job')\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout whose one workflow pins the shared policy at SHA, with no source of the contract at all."""
    r = _repo(tmp_path / "checkout")
    wf = r / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("jobs:\n  a:\n    steps:\n      - uses: WingedGuardian/modding-agent-harness/"
                               ".github/actions/ci-policy@" + SHA + " # v1\n", encoding="utf-8")
    monkeypatch.delenv("X4_HARNESS_CLONE", raising=False)
    return r


def _offline(pf, monkeypatch, calls=None):
    def no_network(url, timeout=0):
        if calls is not None:
            calls.append(url)
        raise OSError("offline (test)")
    monkeypatch.setattr(pf, "fetch_url", no_network, raising=False)


def _cache(root, sha=SHA):
    return Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "prepush-gate" / "contracts" / (sha + ".py")


def test_no_source_of_the_contract_is_COULD_NOT_RUN_never_a_pass(root, monkeypatch):
    """The copy this replaced looked in two clones under the home folder: on any other machine it found none, and on
    this one it read whatever those clones held. With no cache, no X4_HARNESS_CLONE and no network: exit 2."""
    pf = _module()
    _offline(pf, monkeypatch)
    rc, msg = pf.check_contract(root)
    assert rc == 2, msg
    assert SHA[:12] in msg


def test_TWIN_a_cached_contract_at_the_pinned_sha_is_used_without_the_network(root, monkeypatch):
    pf = _module()
    calls = []
    _offline(pf, monkeypatch, calls)
    c = _cache(root)
    c.parent.mkdir(parents=True)
    c.write_text(PASSES, encoding="utf-8")
    rc, msg = pf.check_contract(root)
    assert rc == 0 and "holds" in msg, msg
    assert calls == []


def test_TWIN_the_contracts_refusal_is_a_FAIL_not_a_crash(root, monkeypatch):
    pf = _module()
    _offline(pf, monkeypatch)
    c = _cache(root)
    c.parent.mkdir(parents=True)
    c.write_text(REFUSES, encoding="utf-8")
    rc, msg = pf.check_contract(root)
    assert rc == 1 and "unbounded runner job" in msg, msg


def test_a_cache_for_ANOTHER_sha_is_not_used(root, monkeypatch):
    pf = _module()
    _offline(pf, monkeypatch)
    c = _cache(root, "2" * 40)
    c.parent.mkdir(parents=True)
    c.write_text(PASSES, encoding="utf-8")
    assert pf.check_contract(root)[0] == 2


def test_the_pinned_url_is_fetched_once_and_cached(root, monkeypatch):
    pf = _module()
    calls = []

    def fetch(url, timeout=0):
        calls.append(url)
        return PASSES.encode("utf-8")
    monkeypatch.setattr(pf, "fetch_url", fetch)
    assert pf.check_contract(root)[0] == 0
    assert calls == ["https://raw.githubusercontent.com/WingedGuardian/modding-agent-harness/" + SHA + "/ci/contracts.py"]
    assert _cache(root).read_text(encoding="utf-8") == PASSES
    assert pf.check_contract(root)[0] == 0
    assert len(calls) == 1, "the second run must read the cache"


def test_TWIN_a_fetched_body_that_is_not_the_contract_is_not_cached_and_could_not_run(root, monkeypatch):
    pf = _module()
    monkeypatch.setattr(pf, "fetch_url", lambda url, timeout=0: b"404: Not Found")
    assert pf.check_contract(root)[0] == 2
    assert not _cache(root).exists()


def test_a_local_clone_is_the_offline_source(root, tmp_path, monkeypatch):
    pf = _module()
    _offline(pf, monkeypatch)
    clone = _repo(tmp_path / "harness")
    (clone / "ci").mkdir()
    (clone / "ci" / "contracts.py").write_text(PASSES, encoding="utf-8")
    _git(clone, "add", "ci/contracts.py")
    _git(clone, "commit", "-q", "-m", "c")
    sha = _git(clone, "rev-parse", "HEAD")
    wf = root / ".github" / "workflows" / "ci.yml"
    wf.write_text(wf.read_text(encoding="utf-8").replace(SHA, sha), encoding="utf-8")
    monkeypatch.setenv("X4_HARNESS_CLONE", str(clone))
    rc, msg = pf.check_contract(root)
    assert rc == 0, msg
    assert _cache(root, sha).read_text(encoding="utf-8") == PASSES


def test_TWIN_a_clone_without_the_pinned_commit_could_not_run(root, tmp_path, monkeypatch):
    pf = _module()
    _offline(pf, monkeypatch)
    clone = _repo(tmp_path / "harness")
    (clone / "x").write_text("x")
    _git(clone, "add", "x")
    _git(clone, "commit", "-q", "-m", "c")
    monkeypatch.setenv("X4_HARNESS_CLONE", str(clone))
    assert pf.check_contract(root)[0] == 2


def test_two_different_pins_could_not_run(root, monkeypatch):
    pf = _module()
    _offline(pf, monkeypatch)
    (root / ".github" / "workflows" / "b.yml").write_text(
        "      - uses: WingedGuardian/modding-agent-harness/.github/actions/ci-policy@" + "3" * 40 + "\n", encoding="utf-8")
    rc, msg = pf.check_contract(root)
    assert rc == 2 and "ONE pinned" in msg


def test_RED_the_shipped_source_names_no_home_or_private_path():
    """It is tracked (export-ignored, but public in the repository): nothing in it may point into one person's home."""
    require_repo("scripts/preflight.py", why="the maintainers' push preflight is not shipped")
    text = SCRIPT.read_text(encoding="utf-8")
    for pattern in (r"Path\.home\(", r"expanduser", r"~/", r"[A-Za-z]:[\\/]+Users", r"/home/", r"\.claude/tools"):
        assert not re.search(pattern, text), pattern


def test_the_preflight_is_export_ignored_like_the_gate():
    require_repo(".git", "scripts/preflight.py", why="asks git for the export rule")
    for p in ("scripts/preflight.py", "tools/x4validate/tests/test_preflight.py"):
        r = subprocess.run(["git", "-C", str(REPO), "check-attr", "export-ignore", "--", p],
                           capture_output=True, text=True, check=True)
        assert r.stdout.strip().endswith("set"), r.stdout


# --------------------------------------------------------------------------- syntax under an older interpreter

NEW_FSTRING = "x = ['a', 'b']\ny = f\"{'\n'.join(x)}\"\n"   # a backslash inside an f-string expression: 3.12+ only


def _py_repo(root, body):
    (root / "tools" / "x4validate").mkdir(parents=True)
    (root / "tools" / "x4validate" / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.13"\n',
                                                                  encoding="utf-8")
    (root / "m.py").write_text(body, encoding="utf-8")
    _git(root, "add", "m.py")


def test_an_older_interpreter_hands_the_parse_to_the_project_python(root, monkeypatch):
    """The pre-push hook started this under Python 3.10 (2026-11-18): 3 valid files read as syntax errors."""
    pf = _module()
    _py_repo(root, NEW_FSTRING)
    seen = []
    monkeypatch.setattr(pf, "RUNNING_PYTHON", (3, 10))
    monkeypatch.setattr(pf, "_syntax_under_project_python", lambda r, files, need: seen.append((files, need)) or (0, "ok"))
    assert pf.check_syntax(root) == (0, "ok")
    assert seen == [(["m.py"], (3, 13))]


def test_twin_a_new_enough_interpreter_parses_in_process(root, monkeypatch):
    pf = _module()
    _py_repo(root, "x = 1\n")
    monkeypatch.setattr(pf, "RUNNING_PYTHON", (3, 13))
    monkeypatch.setattr(pf, "_syntax_under_project_python", lambda *a: pytest.fail("delegated although new enough"))
    assert pf.check_syntax(root)[0] == 0


def test_twin_a_real_syntax_error_still_fails_in_process(root, monkeypatch):
    pf = _module()
    _py_repo(root, "def (:\n")
    monkeypatch.setattr(pf, "RUNNING_PYTHON", (3, 13))
    rc, msg = pf.check_syntax(root)
    assert rc == 1 and "m.py:1" in msg


def test_the_project_interpreter_that_cannot_run_is_could_not_run(root, monkeypatch):
    pf = _module()
    _py_repo(root, "x = 1\n")
    monkeypatch.setattr(pf, "RUNNING_PYTHON", (3, 10))
    real = pf.subprocess.run

    def no_uv(cmd, *a, **k):
        if cmd[0] == "uv":
            raise OSError("no uv")
        return real(cmd, *a, **k)
    monkeypatch.setattr(pf.subprocess, "run", no_uv)
    rc, msg = pf.check_syntax(root)
    assert rc == 2 and "uv could not run" in msg


def test_the_real_repo_parses_under_the_project_python_when_started_by_an_old_one(monkeypatch):
    """End to end on this checkout, through real uv: what the 3.10-started gate must report."""
    pf = _module()
    monkeypatch.setattr(pf, "RUNNING_PYTHON", (3, 10))
    rc, msg = pf.check_syntax(REPO)
    assert rc == 0, msg
    assert "project interpreter" in msg

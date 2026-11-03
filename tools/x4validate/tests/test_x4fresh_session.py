"""Session start launches `x4fresh rebuild --auto` detached, exactly like the housekeep sweep: only $X4_TOOLKIT's
launcher, never under a test harness unless asked, never with either opt-out, and the previous run's note first."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK_SRC = REPO / "agent" / "guards" / "claude-hooks"
_spec = importlib.util.spec_from_file_location("x4fresh_session", REPO / "scripts" / "x4fresh_session.py")
xs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xs)

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hook is NOT checked here")
MARKER = "import sys\nopen(sys.argv[-1] + '.launched', 'w').write(' '.join(sys.argv[1:]))\n"


def _tree(tmp_path):
    hooks = tmp_path / "proj" / ".claude" / "hooks"
    shutil.copytree(HOOK_SRC, hooks)
    tk = tmp_path / "tk"
    (tk / "scripts").mkdir(parents=True)
    (tk / "scripts" / "x4fresh_session.py").write_text(MARKER, encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tk), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"), X4_SWEEP_IN_TESTS="1")
    for k in ("X4_HOUSEKEEP_SWEEP", "X4_FRESH_AUTO"):
        env.pop(k, None)
    return hooks, env


def _run(hooks, env):
    return subprocess.run([BASH, str(hooks / "session-canary.sh")], input=b"{}", capture_output=True,
                          env=env, timeout=120).stdout.decode("utf-8", "replace")


def _launched(hooks, wait):
    flag = Path(str(hooks.parent / "x4fresh-session.txt") + ".launched")
    for _ in range(int(wait / 0.2)):
        if flag.exists():
            return flag
        time.sleep(0.2)
    return flag if flag.exists() else None


@needs_bash
def test_the_previous_runs_note_is_shown_at_session_start(tmp_path):
    hooks, env = _tree(tmp_path)
    env["X4_FRESH_AUTO"] = "off"
    assert "x4fresh:" not in _run(hooks, env)                           # twin: no note, nothing shown
    (hooks.parent / "x4fresh-session.txt").write_text("x4fresh: NOT CURRENT: x4eff STALE\n", encoding="utf-8")
    assert "x4fresh: NOT CURRENT: x4eff STALE" in _run(hooks, env)


@needs_bash
def test_it_launches_the_toolkits_launcher_with_the_note_path(tmp_path):
    hooks, env = _tree(tmp_path)
    _run(hooks, env)
    flag = _launched(hooks, 10)
    assert flag, "the launcher was not started"
    assert Path(flag.read_text(encoding="utf-8").split()[-1]).name == "x4fresh-session.txt"


@needs_bash
@pytest.mark.parametrize("off", [("X4_FRESH_AUTO", "off"), ("X4_HOUSEKEEP_SWEEP", "off"),
                                 ("X4_SWEEP_IN_TESTS", None)])
def test_each_opt_out_stops_the_launch(tmp_path, off):
    """One twin per guard: each alone must stop it (X4_SWEEP_IN_TESTS unset = a plain pytest run)."""
    hooks, env = _tree(tmp_path)
    key, val = off
    if val is None:
        env.pop(key, None)
    else:
        env[key] = val
    _run(hooks, env)
    assert not _launched(hooks, 2), f"{key} did not stop the launch"


@needs_bash
def test_it_never_runs_the_launcher_beside_the_hooks(tmp_path):
    hooks, env = _tree(tmp_path)
    (tmp_path / "proj" / "scripts").mkdir()
    (tmp_path / "proj" / "scripts" / "x4fresh_session.py").write_text(MARKER, encoding="utf-8")
    env["X4_TOOLKIT"] = str(tmp_path / "no-toolkit-here")
    _run(hooks, env)
    assert not _launched(hooks, 2), "the launcher beside the hooks was started"


def test_the_launcher_runs_x4fresh_auto_in_the_toolkits_environment(tmp_path):
    note = tmp_path / "n.txt"
    assert xs.command(note, "uv") == ["uv", "run", "--frozen", "--directory", str(REPO / "tools" / "x4validate"),
                                      "x4fresh", "rebuild", "--auto", "--note", str(note)]


def test_the_launcher_says_so_in_the_note_when_uv_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(xs.shutil, "which", lambda name: None)
    monkeypatch.delenv("X4_FRESH_AUTO", raising=False)
    note = tmp_path / "n.txt"
    assert xs.main([str(note)]) == 2
    assert "NOT checked" in note.read_text(encoding="utf-8")


def test_the_launcher_honours_auto_off(tmp_path, monkeypatch):
    monkeypatch.setenv("X4_FRESH_AUTO", "off")
    monkeypatch.setattr(xs.subprocess, "run", lambda *a, **k: pytest.fail("ran with X4_FRESH_AUTO=off"))
    assert xs.main([str(tmp_path / "n.txt")]) == 0


class _Done:
    def __init__(self, returncode, stderr=""):
        self.returncode, self.stderr = returncode, stderr


def _launch(monkeypatch, tmp_path, run):
    monkeypatch.setattr(xs.shutil, "which", lambda name: "uv")
    monkeypatch.delenv("X4_FRESH_AUTO", raising=False)
    monkeypatch.setattr(xs.subprocess, "run", run)
    note = tmp_path / "n.txt"
    return note, xs.main([str(note)])


def test_the_launcher_writes_did_not_complete_before_starting(tmp_path, monkeypatch):
    """A launcher killed in `uv run` must not leave the previous run's empty note to read as fine."""
    seen = []
    note = tmp_path / "n.txt"
    note.write_text("", encoding="utf-8")

    def run(*a, **k):
        seen.append(note.read_text(encoding="utf-8"))
        return _Done(0)
    _launch(monkeypatch, tmp_path, run)
    assert seen and seen[0].startswith(xs.PENDING)


def test_a_nonzero_exit_with_no_report_says_so_in_the_note(tmp_path, monkeypatch):
    note, rc = _launch(monkeypatch, tmp_path, lambda *a, **k: _Done(2, "x4fresh: REFUSING: not configured\n"))
    text = note.read_text(encoding="utf-8")
    assert rc == 2 and "rc 2" in text and "REFUSING: not configured" in text
    assert not text.startswith(xs.PENDING)


def test_a_nonzero_exit_keeps_the_report_x4fresh_wrote(tmp_path, monkeypatch):
    """Twin: rc 1 with a report is x4fresh's own account of a failed build, far better than a bare rc."""
    def run(*a, **k):
        (tmp_path / "n.txt").write_text("x4fresh: NOT CURRENT: xref STALE\n", encoding="utf-8")
        return _Done(1)
    note, rc = _launch(monkeypatch, tmp_path, run)
    assert rc == 1 and note.read_text(encoding="utf-8") == "x4fresh: NOT CURRENT: xref STALE\n"


def test_a_clean_exit_leaves_the_empty_note_alone(tmp_path, monkeypatch):
    def run(*a, **k):
        (tmp_path / "n.txt").write_text("", encoding="utf-8")
        return _Done(0)
    note, rc = _launch(monkeypatch, tmp_path, run)
    assert rc == 0 and note.read_text(encoding="utf-8") == ""


def test_a_launch_that_cannot_start_says_so_in_the_note(tmp_path, monkeypatch):
    def run(*a, **k):
        raise OSError("no such file")
    note, rc = _launch(monkeypatch, tmp_path, run)
    assert rc == 2 and "could not start" in note.read_text(encoding="utf-8")


def test_the_launcher_and_x4fresh_agree_on_the_pending_text():
    """The launcher is stdlib-only and cannot import it, so the two spellings are pinned together."""
    from x4validate import _freshcli
    assert _freshcli.NOTE_PENDING.startswith(xs.PENDING)

"""The PostToolUse save sentinel (agent/guards/claude-hooks/save_sentinel.py over modding_harness/sentinel.py).

Fake saves in tmp_path; the toolkit root is a temporary copy holding the vendored harness, so the sentinel's state
and store never land in the repository; the OS Known Folder is switched off (X4_NO_KNOWN_FOLDERS)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _layout import require_repo

REPO = Path(__file__).resolve().parents[3]
HOOKS = REPO / "agent" / "guards" / "claude-hooks"


@pytest.fixture
def world(tmp_path, monkeypatch):
    require_repo("agent/guards/claude-hooks/save_sentinel.py", why="the sentinel source lives in agent/")
    monkeypatch.setenv("X4_NO_KNOWN_FOLDERS", "1")
    monkeypatch.syspath_prepend(str(HOOKS))
    spec = importlib.util.spec_from_file_location("x4_save_sentinel_t", HOOKS / "save_sentinel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "game_running", lambda: False)
    tk = tmp_path / "toolkit"
    shutil.copytree(REPO / "tools" / "modding_harness", tk / "tools" / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    saves = tmp_path / "profile" / "save"
    saves.mkdir(parents=True)
    roots = {"toolkit": str(tk), "saves": str(saves), "profile": str(tmp_path / "profile")}
    return mod, roots, saves, tk


def _bump(path, data):
    path.write_bytes(data)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_an_unbacked_change_is_reported_once_and_loudly(world):
    mod, roots, saves, tk = world
    s = saves / "save_001.xml.gz"
    s.write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)           # first look: the baseline
    _bump(s, b"changed")
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "modified: " + str(s) in text
    assert mod.report(roots, now=1020.0) == ("", False)
    assert (tk / ".modding-agent-harness" / "save-sentinel.json").is_file()


def test_new_saves_and_other_files_are_not_changes(world):
    mod, roots, saves, tk = world
    mod.report(roots, now=1000.0)
    (saves / "save_002.xml.gz").write_bytes(b"x")
    (saves / "notes.txt").write_bytes(b"x")
    assert mod.report(roots, now=1010.0) == ("", False)


def test_while_x4_runs_it_is_noted_not_raised(world, monkeypatch):
    mod, roots, saves, tk = world
    s = saves / "quicksave.xml.gz"
    s.write_bytes(b"x")
    mod.report(roots, now=1000.0)
    s.unlink()
    monkeypatch.setattr(mod, "game_running", lambda: True)
    text, loud = mod.report(roots, now=1010.0)
    assert not loud and "removed: " + str(s) in text and "may be the game itself" in text


def test_a_marked_folder_under_the_toolkit_is_watched(world):
    mod, roots, saves, tk = world
    kept = tk / "evidence" / "kept"
    kept.mkdir(parents=True)
    (kept / mod.MARKER).write_text("kept", encoding="utf-8")
    k = kept / "save_009.xml.gz"
    k.write_bytes(b"x")
    mod.report(roots, now=1000.0)
    k.unlink()
    assert "removed: " + str(k) in mod.report(roots, now=1010.0)[0]


def test_a_failure_is_disclosed_never_silent(world, monkeypatch, capsys):
    mod, roots, saves, tk = world
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": type("B", (), {"read": lambda self: b"not a packet"})()})())
    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["additionalContext"].startswith("SAVE SENTINEL DID NOT RUN")

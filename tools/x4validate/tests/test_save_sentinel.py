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


# --- optional save locks (user ruling 2026-10-31): Windows calls replaced with recorders, no real ACL changes ---
def _fake_locks(mod, tk, monkeypatch, elevated=False):
    lock = mod.save_policy.core(str(tk), "save_lock")
    files = []
    monkeypatch.setattr(lock, "supported", lambda: True)
    monkeypatch.setattr(lock, "is_elevated", lambda: elevated)
    monkeypatch.setattr(lock, "user_sid", lambda: "S-1-5-21-1")
    monkeypatch.setattr(lock, "status_map", lambda folder, exts: {})
    monkeypatch.setattr(lock, "is_locked", lambda p: True)
    monkeypatch.setattr(lock, "lock_folder", lambda p, sid: None)
    monkeypatch.setattr(lock, "lock_file", lambda p, sid: files.append(os.path.basename(p)))
    return files


def _switch(mod, tk, enabled):
    s = mod.lock_switch(str(tk))
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")


def _old(path):
    path.write_bytes(b"x")
    os.utime(path, (500.0, 500.0))


def test_locking_off_locks_nothing(world, monkeypatch):
    mod, roots, saves, tk = world
    files = _fake_locks(mod, tk, monkeypatch)
    _old(saves / "save_001.xml.gz")
    mod.report(roots, now=1000.0)
    mod.report(roots, now=1010.0)
    assert files == []


def test_locking_on_locks_settled_saves_once_and_leaves_rotating_slots(world, monkeypatch):
    mod, roots, saves, tk = world
    files = _fake_locks(mod, tk, monkeypatch)
    for name in ("save_001.xml.gz", "autosave_01.xml.gz", "quicksave.xml.gz"):
        _old(saves / name)
    _switch(mod, tk, True)
    text, loud = mod.report(roots, now=1000.0)
    assert files == ["save_001.xml.gz"] and not loud, text
    files.clear()
    mod.report(roots, now=1010.0)
    assert files == []


def test_switching_locking_off_is_raised_once(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch)
    _switch(mod, tk, True)
    mod.report(roots, now=1000.0)
    _switch(mod, tk, False)
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "switched OFF" in text
    assert "switched OFF" not in mod.report(roots, now=1020.0)[0]


def test_an_elevated_agent_is_warned_while_locking_is_on(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch, elevated=True)
    _switch(mod, tk, True)
    text, loud = mod.report(roots, now=1000.0)
    assert loud and "runs as administrator" in text
    assert "runs as administrator" not in mod.report(roots, now=1010.0)[0]


def test_the_switch_lives_in_the_protected_harness_state(world):
    """save_policy refuses any agent change under <toolkit>/.modding-agent-harness ("neutral harness state is
    protected"), which is what keeps the switch the user's alone."""
    mod, roots, saves, tk = world
    switch = mod.lock_switch(str(tk))
    assert switch.parent == tk / ".modding-agent-harness"
    for op in ("write", "delete", "move"):
        _, plan, _, _ = mod.save_policy.resolve(str(tk), roots, [{"path": str(switch), "operation": op,
                                                                  "literal": True, "scope": "file"}])
        assert "neutral harness state is protected" in plan.unresolved, op

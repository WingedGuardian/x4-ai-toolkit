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


# ------------------------------------------- the hook script, as the host runs it (4.1.0 G-M2)
def _bash():
    """Never a bare `bash`: on Windows that is System32's WSL relay (see scripts/gitbash.py)."""
    spec = importlib.util.spec_from_file_location("_gitbash", REPO / "scripts" / "gitbash.py")
    gitbash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gitbash)
    found = gitbash.find_bash()
    if not found:
        pytest.skip("no real (non-WSL) bash on this machine -- not checked")
    return str(found)


def _run_hook(tmp_path, broken=None):
    """save-sentinel.sh from a copy of the hook folder, optionally with one module made to fail on import."""
    require_repo("agent/guards/claude-hooks/save-sentinel.sh", why="the hook source lives in agent/")
    hooks = tmp_path / "toolkit" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("save-sentinel.sh", "_x4-env.sh", "save_sentinel.py", "save_policy.py", "hook_facts.py"):
        shutil.copy2(HOOKS / name, hooks / name)
    shutil.copytree(REPO / "tools" / "modding_harness", tmp_path / "toolkit" / "tools" / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if broken:
        (hooks / broken).write_text("raise ImportError('planted')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("X4_", "CLAUDE_"))}
    env.update(X4_NO_KNOWN_FOLDERS="1", X4_TOOLKIT=str(tmp_path / "toolkit"), X4_SAVES=str(tmp_path / "saves"),
               X4_PROFILE=str(tmp_path / "profile"), X4_DOCUMENTS=str(tmp_path / "documents"))
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
    return subprocess.run([_bash(), str(hooks / "save-sentinel.sh")], input=payload, capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=str(tmp_path), timeout=120)


@pytest.mark.parametrize("module", ["save_policy.py", "save_sentinel.py"])
def test_a_module_that_will_not_import_is_disclosed(tmp_path, module):
    """The import sits above main()'s try, and the script's exit 0 hid Python's own exit status."""
    r = _run_hook(tmp_path, broken=module)
    assert r.returncode == 0                      # advisory: it never fails the call
    answer = json.loads(r.stdout)["hookSpecificOutput"]
    assert answer["hookEventName"] == "PostToolUse"
    assert "SAVE SENTINEL DID NOT RUN" in answer["additionalContext"]


def test_TWIN_a_run_that_worked_discloses_nothing(tmp_path):
    r = _run_hook(tmp_path)
    assert r.returncode == 0
    assert "DID NOT RUN" not in r.stdout, r.stdout + r.stderr

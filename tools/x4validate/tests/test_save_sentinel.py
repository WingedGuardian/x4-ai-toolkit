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


# --- F4: a save the game wrote while the sentinel was not looking. X4 rewrites <profile>/uidata.xml on every exit. ---
def _stamp(path, when, data=None):
    if data is not None:
        path.write_bytes(data)
    os.utime(path, (when, when))


def _session_world(world):
    """A baseline look at t=1000 over a save and a removable one, stamped before it."""
    mod, roots, saves, tk = world
    s, gone = saves / "save_001.xml.gz", saves / "save_002.xml.gz"
    _stamp(s, 900.0, b"x")
    _stamp(gone, 900.0, b"x")
    uidata = Path(roots["profile"]) / "uidata.xml"
    _stamp(uidata, 900.0, b"<uidata/>")
    _stamp(saves, 900.0)
    assert mod.report(roots, now=1000.0) == ("", False)
    return mod, roots, saves, s, gone, uidata


def test_a_save_from_a_play_session_since_the_last_look_is_noted_not_raised(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"played")
    _stamp(uidata, 1600.0)                                         # the game wrote it on the way out, after the save
    text, loud = mod.report(roots, now=2000.0)
    assert not loud, text
    assert "modified: " + str(s) in text and "may be the game itself" in text
    assert "tell the user" not in text


def test_TWIN_the_game_closed_for_good_a_change_still_alarms(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"agent")                                    # uidata.xml untouched: no session since the last look
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text and "tell the user" in text


def test_TWIN_a_change_after_the_play_session_ended_still_alarms(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(uidata, 1500.0)
    _stamp(s, 1600.0, b"agent")                                    # dated after the game left
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


def test_TWIN_a_restored_file_with_an_old_stamp_still_alarms_after_a_session(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(uidata, 1500.0)
    _stamp(s, 500.0, b"restored by copy")                          # older than the last look: no game saved this now
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


def test_TWIN_a_removal_after_the_play_session_ended_still_alarms(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(uidata, 1500.0)
    gone.unlink()
    _stamp(saves, 1600.0)                                          # the folder records when
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "removed: " + str(gone) in text


def test_a_removal_during_the_play_session_is_noted_not_raised(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    gone.unlink()
    _stamp(saves, 1400.0)
    _stamp(uidata, 1500.0)
    text, loud = mod.report(roots, now=2000.0)
    assert not loud and "removed: " + str(gone) in text


def test_mixed_changes_raise_only_the_ones_outside_the_session(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(gone, 1400.0, b"played")
    _stamp(uidata, 1500.0)
    _stamp(s, 1600.0, b"agent")
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "1 save file(s) changed since the last look with no backup" in text
    head, tail = text.split("may be the game itself")
    assert "modified: " + str(s) in head and "modified: " + str(gone) in tail


def test_the_game_never_writes_a_marked_folder_so_a_change_there_alarms_even_while_it_runs(world, monkeypatch):
    mod, roots, saves, tk = world
    kept = tk / "evidence" / "kept"
    kept.mkdir(parents=True)
    (kept / mod.MARKER).write_text("kept", encoding="utf-8")
    k = kept / "save_009.xml.gz"
    k.write_bytes(b"x")
    mod.report(roots, now=1000.0)
    k.unlink()
    monkeypatch.setattr(mod, "game_running", lambda: True)
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "removed: " + str(k) in text


def test_a_restored_old_stamp_alarms_even_while_the_game_runs(world, monkeypatch):
    mod, roots, saves, tk = world
    s = saves / "save_001.xml.gz"
    _stamp(s, 900.0, b"x")
    mod.report(roots, now=1000.0)
    _stamp(s, 500.0, b"restored")
    monkeypatch.setattr(mod, "game_running", lambda: True)
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "modified: " + str(s) in text


def test_a_tasklist_that_cannot_be_decoded_cannot_kill_the_look(tmp_path, monkeypatch):
    """game_running unstubbed: the subprocess call is what meets bytes no locale decodes strictly."""
    require_repo("agent/guards/claude-hooks/save_sentinel.py", why="the sentinel source lives in agent/")
    monkeypatch.syspath_prepend(str(HOOKS))
    spec = importlib.util.spec_from_file_location("x4_save_sentinel_t2", HOOKS / "save_sentinel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, '"X4.exe","1"\n', "")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "_on_windows", lambda: True)
    assert mod.game_running() is True
    assert seen.get("errors") == "replace"


# --- F2/F7: a lock failure is loud, and never kills the look ---
def test_a_lock_failure_is_raised_once_until_it_changes(world, monkeypatch):
    mod, roots, saves, tk = world
    lock = mod.save_policy.core(str(tk), "save_lock")
    _fake_locks(mod, tk, monkeypatch)
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: ["F:/save (the rule failed)"])
    _switch(mod, tk, True)
    text, loud = mod.report(roots, now=1000.0)
    assert loud and "could not lock" in text and "the rule failed" in text
    assert mod.report(roots, now=1010.0) == ("", False)            # the same failure: not every tool call
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: ["F:/other (new)"])
    text, loud = mod.report(roots, now=1020.0)
    assert loud and "F:/other" in text
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: [])
    assert mod.report(roots, now=1030.0) == ("", False)
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: ["F:/other (new)"])
    assert mod.report(roots, now=1040.0)[1]                        # back after it cleared: raised again


def test_TWIN_a_lock_failure_repeats_after_the_quiet_period(world, monkeypatch):
    mod, roots, saves, tk = world
    lock = mod.save_policy.core(str(tk), "save_lock")
    _fake_locks(mod, tk, monkeypatch)
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: ["F:/save (the rule failed)"])
    _switch(mod, tk, True)
    assert mod.report(roots, now=1000.0)[1]
    assert mod.report(roots, now=1000.0 + mod.ELEVATED_REPEAT + 1)[1]


@pytest.mark.parametrize("boom", [UnicodeDecodeError("cp1252", b"\x81", 0, 1, "strict"), ValueError("x"),
                                  subprocess.CalledProcessError(1, "whoami"), OSError("x")])
def test_any_failure_inside_locking_is_loud_and_the_sentinel_still_reports(world, monkeypatch, boom):
    mod, roots, saves, tk = world
    lock = mod.save_policy.core(str(tk), "save_lock")
    _fake_locks(mod, tk, monkeypatch)

    def fail(*a, **k):
        raise boom
    monkeypatch.setattr(lock, "auto_lock", fail)
    _switch(mod, tk, True)
    s = saves / "save_001.xml.gz"
    s.write_bytes(b"x")
    text, loud = mod.report(roots, now=1000.0)
    assert loud and "locking unavailable" in text
    _bump(s, b"changed")
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "modified: " + str(s) in text                  # the look itself still reached the agent
    assert "DID NOT RUN" not in text


def test_the_switched_off_note_leaves_switching_back_on_to_the_user(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch)
    _switch(mod, tk, True)
    mod.report(roots, now=1000.0)
    _switch(mod, tk, False)
    assert "user's decision" in mod.report(roots, now=1010.0)[0]


def test_auto_lock_gets_the_switch_so_off_can_account_for_a_new_folder(world, monkeypatch):
    mod, roots, saves, tk = world
    lock = mod.save_policy.core(str(tk), "save_lock")
    _fake_locks(mod, tk, monkeypatch)
    seen = {}
    monkeypatch.setattr(lock, "auto_lock", lambda *a, **k: seen.update(k) or [])
    _switch(mod, tk, True)
    mod.report(roots, now=1000.0)
    assert seen["config"] == mod.lock_switch(str(tk))


def _bash():
    spec = importlib.util.spec_from_file_location("x4guard_for_bash", HOOKS / "x4guard.py")
    guard = importlib.util.module_from_spec(spec)
    sys.modules["x4guard_for_bash"] = guard
    spec.loader.exec_module(guard)
    found = guard.resolve_bash()[0]
    if not found:
        pytest.skip("no bash here: the hook's shell wrapper NOT checked")
    return found


def _hook_copy(tmp_path, policy_source):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("save-sentinel.sh", "_x4-env.sh", "save_sentinel.py"):
        shutil.copy2(HOOKS / name, hooks / name)
    (hooks / "save_policy.py").write_text(policy_source, encoding="utf-8")
    return hooks


def test_a_sentinel_that_dies_before_its_own_handler_still_says_so(tmp_path):
    require_repo("agent/guards/claude-hooks/save-sentinel.sh", why="the hook source lives in agent/")
    hooks = _hook_copy(tmp_path, "raise ImportError('broken on purpose')\n")
    r = subprocess.run([_bash(), str(hooks / "save-sentinel.sh")], input=b'{"tool_name":"Bash"}', capture_output=True,
                       timeout=120)
    assert r.returncode == 0
    answer = json.loads(r.stdout.decode("utf-8"))
    assert "SAVE SENTINEL DID NOT RUN" in answer["hookSpecificOutput"]["additionalContext"]


def test_TWIN_a_sentinel_that_answers_is_not_also_reported_as_dead(tmp_path):
    require_repo("agent/guards/claude-hooks/save-sentinel.sh", why="the hook source lives in agent/")
    hooks = _hook_copy(tmp_path, "def packet(text):\n    return {}, {}\n")
    (hooks / "save_sentinel.py").write_text("import sys\nsys.stdout.write('{}')\n", encoding="utf-8")
    r = subprocess.run([_bash(), str(hooks / "save-sentinel.sh")], input=b'{"tool_name":"Bash"}', capture_output=True,
                       timeout=120)
    assert r.returncode == 0 and r.stdout == b"{}"


def test_forget_missing_goes_only_with_off():
    require_repo("tools/agent-harness.py", why="the host command")
    r = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "agent-harness.py"), "save-lock", "status",
                        "--forget-missing"], capture_output=True, timeout=120)
    assert r.returncode == 2 and b"--forget-missing goes with save-lock off" in r.stdout

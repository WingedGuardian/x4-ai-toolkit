"""The PostToolUse save sentinel (agent/guards/claude-hooks/save_sentinel.py over modding_harness/sentinel.py).

Fake saves in tmp_path; the toolkit root is a temporary copy holding the vendored harness, so the sentinel's state
and store never land in the repository; the OS Known Folder is switched off (X4_NO_KNOWN_FOLDERS)."""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from _layout import claude_hooks_source, require_repo

REPO = Path(__file__).resolve().parents[3]


def _hooks():
    """The hooks this layout can test: agent/ in a checkout, .claude/hooks on an install (see claude_hooks_source)."""
    return claude_hooks_source(REPO, why="the save sentinel's Claude hooks")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("X4_NO_KNOWN_FOLDERS", "1")
    monkeypatch.syspath_prepend(str(_hooks()))
    spec = importlib.util.spec_from_file_location("x4_save_sentinel_t", _hooks() / "save_sentinel.py")
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




def test_switching_locking_off_is_raised_once(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch)
    _switch(mod, tk, True)
    mod.report(roots, now=1000.0)
    _switch(mod, tk, False)
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "switched OFF" in text
    assert "switched OFF" not in mod.report(roots, now=1020.0)[0]




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


def _exit(uidata, when, companion="pipelinecache.bin"):
    """What a real exit leaves: uidata.xml, and within seconds another file of the exit set."""
    _stamp(uidata, when)
    _stamp(uidata.parent / companion, when + 1.0, b"x")


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
    _exit(uidata, 1600.0)                                          # the game wrote it on the way out, after the save
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
    _exit(uidata, 1500.0)
    _stamp(s, 1600.0, b"agent")                                    # dated after the game left
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


def test_TWIN_a_restored_file_with_an_old_stamp_still_alarms_after_a_session(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _exit(uidata, 1500.0)
    _stamp(s, 500.0, b"restored by copy")                          # older than the last look: no game saved this now
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


def test_TWIN_a_removal_after_the_play_session_ended_still_alarms(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _exit(uidata, 1500.0)
    gone.unlink()
    _stamp(saves, 1600.0)                                          # the folder records when
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "removed: " + str(gone) in text


def test_a_removal_during_the_play_session_is_noted_not_raised(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    gone.unlink()
    _stamp(saves, 1400.0)
    _exit(uidata, 1500.0)
    text, loud = mod.report(roots, now=2000.0)
    assert not loud and "removed: " + str(gone) in text


def test_mixed_changes_raise_only_the_ones_outside_the_session(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(gone, 1400.0, b"played")
    _exit(uidata, 1500.0)
    _stamp(s, 1600.0, b"agent")
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "1 save file(s) changed since the last look with no backup" in text
    head, tail = text.split("may be the game itself")
    assert "modified: " + str(s) in head and "modified: " + str(gone) in tail


# --- R2-I3: uidata.xml alone is not a session. Real exits move another file of the exit set within seconds; a touch,
#     a bulk overwrite and a whole-profile copy must alarm. Measured by stat on real profiles: see save_sentinel.py.
@pytest.mark.parametrize("companion", ["pipelinecache.bin", "config.xml", "userdata.xml", "debug.txt"])
def test_any_one_exit_file_beside_uidata_is_the_second_sign(world, companion):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"played")
    _stamp(uidata, 1600.0)
    _stamp(uidata.parent / companion, 1600.0 - mod.EXIT_SPREAD, b"x")
    text, loud = mod.report(roots, now=2000.0)
    assert not loud and "may be the game itself" in text


def test_a_change_and_a_touch_of_uidata_alone_alarm(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"script")
    _stamp(uidata, 1600.0)
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


def test_a_removal_and_a_touch_of_uidata_alone_alarm(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    gone.unlink()
    _stamp(saves, 1500.0)
    _stamp(uidata, 1600.0)
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "removed: " + str(gone) in text


@pytest.mark.parametrize("off_by", [-1.0, 1.0])
def test_TWIN_an_exit_file_too_far_from_uidata_is_no_second_sign(world, off_by):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"script")
    _stamp(uidata, 1600.0)
    _stamp(uidata.parent / "config.xml", 1600.0 + off_by * (mod.EXIT_SPREAD + 1), b"x")
    assert mod.report(roots, now=2000.0)[1]


def test_TWIN_an_exit_file_moved_with_no_new_uidata_stamp_is_no_session(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"script")
    _stamp(uidata.parent / "config.xml", 1500.0, b"x")
    assert mod.report(roots, now=2000.0)[1]


def test_TWIN_an_exit_from_before_the_last_look_is_no_session_even_inside_the_clock_slack(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _exit(uidata, 990.0)                                           # the baseline look was at 1000
    _stamp(s, 995.0, b"script")                                    # dated_within's slack would still take this
    assert mod.report(roots, now=2000.0)[1] is True


def _many(saves, names):
    for name in names:
        _stamp(saves / name, 900.0, b"x")


def _session_with_changes(world, count):
    """A baseline over `count` saves, all then rewritten before a real exit."""
    mod, roots, saves, tk = world
    names = ["save_{:03d}.xml.gz".format(i) for i in range(count)]
    _many(saves, names)
    uidata = Path(roots["profile"]) / "uidata.xml"
    _stamp(uidata, 900.0, b"<uidata/>")
    assert mod.report(roots, now=1000.0) == ("", False)
    for name in names:
        _stamp(saves / name, 1500.0, b"changed")
    return mod, roots, uidata


def test_a_typical_play_session_stays_quiet(world):
    mod, roots, saves, tk = world
    names = ["autosave_01.xml.gz", "autosave_02.xml.gz", "autosave_03.xml.gz", "quicksave.xml.gz", "save_001.xml.gz",
             "save_002.xml.gz"]
    _many(saves, names)
    uidata = Path(roots["profile"]) / "uidata.xml"
    _stamp(uidata, 900.0, b"<uidata/>")
    mod.report(roots, now=1000.0)
    for i, name in enumerate(names):
        _stamp(saves / name, 1100.0 + 60 * i, b"played")
    _exit(uidata, 1600.0)
    text, loud = mod.report(roots, now=2000.0)
    assert not loud and "6 save file(s) changed while X4 was running" in text


@pytest.mark.parametrize("running", [False, True])
def test_more_saves_than_a_session_changes_alarm_with_the_game_or_without(world, monkeypatch, running):
    mod, roots, uidata = _session_with_changes(world, world[0].MOST_A_SESSION_CHANGES + 1)
    _exit(uidata, 1600.0)
    monkeypatch.setattr(mod, "game_running", lambda: running)
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "{} save file(s) changed since the last look".format(mod.MOST_A_SESSION_CHANGES + 1) in text
    assert "may be the game itself" not in text


@pytest.mark.parametrize("running", [False, True])
def test_TWIN_as_many_saves_as_a_session_can_change_stay_quiet(world, monkeypatch, running):
    mod, roots, uidata = _session_with_changes(world, world[0].MOST_A_SESSION_CHANGES)
    _exit(uidata, 1600.0)
    monkeypatch.setattr(mod, "game_running", lambda: running)
    assert mod.report(roots, now=2000.0)[1] is False


def _bystanders(profile, moved, still):
    for i in range(moved):
        _stamp(profile / "moved_{}.xml".format(i), 1400.0, b"x")
    for i in range(still):
        _stamp(profile / "still_{}.xml".format(i), 900.0, b"x")


def test_a_whole_profile_copy_without_its_times_alarms(world):
    """The shape of a real copy, measured by stat: uidata.xml and userdata.xml written together, as the end of an
    alphabetical copy, and every other file of the profile moved with them."""
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _bystanders(uidata.parent, 6, 0)
    _stamp(s, 1500.0, b"copied")
    _exit(uidata, 1600.0, companion="userdata.xml")
    text, loud = mod.report(roots, now=2000.0)
    assert loud and "modified: " + str(s) in text


@pytest.mark.parametrize("moved,still,loud", [(5, 5, True),    # the floor, and exactly half
                                              (4, 6, False),   # the floor not reached
                                              (4, 0, False),   # all of them, under the floor: too few to tell
                                              (5, 6, False),   # the floor, under half
                                              (6, 6, True)])
def test_TWIN_a_rewritten_profile_needs_the_floor_and_half_of_its_other_files(world, moved, still, loud):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _bystanders(uidata.parent, moved, still)
    _stamp(s, 1500.0, b"played")
    _exit(uidata, 1600.0)
    assert mod.report(roots, now=2000.0)[1] is loud


def test_TWIN_the_exit_files_themselves_are_not_bystanders(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _bystanders(uidata.parent, 0, 5)
    _stamp(s, 1500.0, b"played")
    _stamp(uidata, 1600.0)
    for name in ("pipelinecache.bin", "config.xml", "userdata.xml", "debug.txt"):
        _stamp(uidata.parent / name, 1600.0, b"x")
    assert mod.report(roots, now=2000.0)[1] is False


def test_a_profile_that_cannot_be_listed_is_no_session(world, monkeypatch):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"played")
    _exit(uidata, 1600.0)
    real = os.scandir

    def refuse(path):
        if os.path.abspath(path) == os.path.abspath(uidata.parent):
            raise PermissionError("no")
        return real(path)
    monkeypatch.setattr(mod.os, "scandir", refuse)
    assert mod.report(roots, now=2000.0)[1] is True


def test_an_alarm_on_a_game_folder_says_it_can_be_a_false_one_and_a_marked_folder_does_not(world):
    mod, roots, saves, s, gone, uidata = _session_world(world)
    _stamp(s, 1500.0, b"script")
    assert "can be a false alarm" in mod.report(roots, now=2000.0)[0]
    tk = world[3]
    kept = tk / "evidence" / "kept"
    kept.mkdir(parents=True)
    (kept / mod.MARKER).write_text("kept", encoding="utf-8")
    k = kept / "save_009.xml.gz"
    k.write_bytes(b"x")
    state = tk / ".modding-agent-harness" / "save-sentinel.json"
    record = json.loads(state.read_text(encoding="utf-8"))
    record["marked"] = [str(kept)]
    state.write_text(json.dumps(record), encoding="utf-8")
    mod.report(roots, now=2010.0)
    k.unlink()
    text, loud = mod.report(roots, now=2020.0)
    assert loud and "removed: " + str(k) in text and "can be a false alarm" not in text


def test_a_deleted_record_of_the_last_look_is_raised_to_the_user(world):
    mod, roots, saves, tk = world
    s = saves / "save_001.xml.gz"
    s.write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)
    (tk / ".modding-agent-harness" / "save-sentinel.json").unlink()
    _bump(s, b"changed after the record went")
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "gone or could not be read" in text
    assert mod.report(roots, now=1020.0) == ("", False)            # one look's disclosure


def _hold_the_record(monkeypatch, tk, reads=True, replaces=False):
    """The record locked through every retry, as by another look or a scanner. The retries do not wait here."""
    import time as clock
    record = tk / ".modding-agent-harness" / "save-sentinel.json"
    monkeypatch.setattr(clock, "sleep", lambda _s: None)
    if reads:
        real_read = Path.read_text

        def read_text(this, *a, **k):
            if this == record:
                raise PermissionError(13, "held", str(this))
            return real_read(this, *a, **k)
        monkeypatch.setattr(Path, "read_text", read_text)
    if replaces:
        real_replace = os.replace

        def replace(a, b):
            if Path(b) == record:
                raise PermissionError(13, "held", str(b))
            return real_replace(a, b)
        monkeypatch.setattr(os, "replace", replace)
    return record


def test_a_look_that_meets_a_held_record_is_quiet_keeps_the_baseline_and_the_next_look_reports(world, monkeypatch):
    mod, roots, saves, tk = world
    s = saves / "save_001.xml.gz"
    s.write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)
    _bump(s, b"changed while the record was held")
    with monkeypatch.context() as held:
        record = _hold_the_record(held, tk)
        before = record.read_bytes()
        held.setattr(mod, "marked_folders", lambda _r: pytest.fail("a look that compares nothing walks nothing"))
        text, loud = mod.report(roots, now=1010.0)
    assert not loud
    assert "held by another process" in text and "could not be read" not in text
    assert record.read_bytes() == before
    text, loud = mod.report(roots, now=1020.0)
    assert loud and "modified: " + str(s) in text, "the change the held look could not see"
    assert "could not be read" not in text


def test_TWIN_a_record_held_past_the_window_is_raised_to_the_user_and_says_so(world, monkeypatch):
    mod, roots, saves, tk = world
    (saves / "save_001.xml.gz").write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)
    with monkeypatch.context() as held:
        _hold_the_record(held, tk, replaces=True)
        sentinel = mod.save_policy.core(mod.save_policy.native(roots["toolkit"]), "sentinel")
        assert mod.report(roots, now=1010.0)[1] is False
        assert mod.report(roots, now=1010.0 + sentinel.STUCK_AFTER - 1)[1] is False
        text, loud = mod.report(roots, now=1010.0 + sentinel.STUCK_AFTER)
    assert loud and "could not be read (it stayed locked" in text, "the alarm reaches the user though the record cannot be replaced"
    assert "could not be recorded (PermissionError)" in text


def test_TWIN_a_corrupt_record_is_raised_at_once_and_says_so(world):
    mod, roots, saves, tk = world
    (saves / "save_001.xml.gz").write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)
    (tk / ".modding-agent-harness" / "save-sentinel.json").write_text("{not json", encoding="utf-8")
    text, loud = mod.report(roots, now=1010.0)
    assert loud and "gone or could not be read (its contents are corrupt)" in text
    assert mod.report(roots, now=1020.0) == ("", False)


def test_a_look_that_cannot_record_still_reports_and_the_old_baseline_stays(world, monkeypatch):
    mod, roots, saves, tk = world
    s = saves / "save_001.xml.gz"
    s.write_bytes(b"x")
    assert mod.report(roots, now=1000.0) == ("", False)
    _bump(s, b"changed")
    with monkeypatch.context() as held:
        _hold_the_record(held, tk, reads=False, replaces=True)
        text, loud = mod.report(roots, now=1010.0)
    assert loud and "modified: " + str(s) in text and "could not be recorded (PermissionError)" in text
    text, loud = mod.report(roots, now=1020.0)
    assert loud and "modified: " + str(s) in text, "unrecorded, so reported again, never lost"
    assert mod.report(roots, now=1030.0) == ("", False)


def test_TWIN_a_save_state_failure_that_is_not_an_os_error_still_escapes_to_did_not_run(world, monkeypatch):
    mod, roots, saves, tk = world
    sentinel = mod.save_policy.core(mod.save_policy.native(roots["toolkit"]), "sentinel")
    monkeypatch.setattr(sentinel, "save_state", lambda *_a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        mod.report(roots, now=1000.0)


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
    monkeypatch.syspath_prepend(str(_hooks()))
    spec = importlib.util.spec_from_file_location("x4_save_sentinel_t2", _hooks() / "save_sentinel.py")
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






def test_the_switched_off_note_tells_the_user_and_offers_no_command_that_refuses(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch)
    _switch(mod, tk, True)
    mod.report(roots, now=1000.0)
    _switch(mod, tk, False)
    note = mod.report(roots, now=1010.0)[0]
    assert "tell them now" in note and "save-lock on" not in note




def _hook_copy(tmp_path, policy_source):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("save-sentinel.sh", "_x4-env.sh", "save_sentinel.py"):
        shutil.copy2(_hooks() / name, hooks / name)
    (hooks / "save_policy.py").write_text(policy_source, encoding="utf-8")
    return hooks


def test_a_sentinel_that_dies_before_its_own_handler_still_says_so(tmp_path):
    hooks = _hook_copy(tmp_path, "raise ImportError('broken on purpose')\n")
    r = subprocess.run([_bash(), str(hooks / "save-sentinel.sh")], input=b'{"tool_name":"Bash"}', capture_output=True,
                       timeout=120)
    assert r.returncode == 0
    answer = json.loads(r.stdout.decode("utf-8"))
    assert "SAVE SENTINEL DID NOT RUN" in answer["hookSpecificOutput"]["additionalContext"]


def test_TWIN_a_sentinel_that_answers_is_not_also_reported_as_dead(tmp_path):
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
    hooks = tmp_path / "toolkit" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("save-sentinel.sh", "_x4-env.sh", "save_sentinel.py", "save_policy.py", "hook_facts.py"):
        shutil.copy2(_hooks() / name, hooks / name)
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


def _harness_that_cannot_lock(monkeypatch, core):
    """tools/agent-harness.py with the lock core replaced. Never run these through the real command: while `on` still
    worked, a first run of a test that did locked the real saves of the machine it ran on (2026-11-04). A regression
    here must fail the test and touch nothing."""
    harness = REPO / "tools" / "agent-harness.py"
    monkeypatch.syspath_prepend(str(REPO / "tools" / "x4_harness"))
    spec = importlib.util.spec_from_file_location("x4_agent_harness_t", harness)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.save_policy, "core", core)
    assert mod.save_policy.core is core
    return mod


def test_save_locks_cannot_be_turned_on_in_this_release(monkeypatch):
    """A lock binds the game as well, and a player must be able to save over a save (owner ruling 2026-11-04)."""
    def reached(root, name):
        raise AssertionError("`save-lock on` reached the real " + name)
    mod = _harness_that_cannot_lock(monkeypatch, reached)
    out = mod.save_lock_command(argparse.Namespace(action="on", target=None, forget_missing=False))
    assert out["decision"] == "refused" and "save over" in out["reason"], out


def test_TWIN_the_way_back_out_of_locks_is_still_there(monkeypatch):
    """Someone may hold locks from a pre-release build: `off` must still parse and reach its own checks. An empty core
    has no turn_off, so reaching further raises and changes nothing."""
    mod = _harness_that_cannot_lock(monkeypatch, lambda root, name: types.SimpleNamespace())
    out = mod.save_lock_command(argparse.Namespace(action="off", target="somewhere", forget_missing=False))
    assert out["decision"] == "refused" and "takes one save or folder" in out["reason"], out


def test_setup_neither_asks_about_locks_nor_turns_them_on():
    setup = REPO / "setup.sh"
    if not setup.exists():
        pytest.skip("setup.sh is not in this tree")
    text = setup.read_text(encoding="utf-8")
    assert "Lock your saves" not in text
    assert "agent-harness.py\" save-lock on" not in text
    assert "--save-lock: save locks cannot be turned on" in text


def test_a_switch_that_says_on_locks_nothing_and_says_how_to_lift_it(world, monkeypatch):
    """A player must be able to save over a save (owner ruling 2026-11-04), so a switch left by a pre-release build, or
    written by a script, must not start or spread locks."""
    mod, roots, saves, tk = world
    files = _fake_locks(mod, tk, monkeypatch)
    lock = mod.save_policy.core(str(tk), "save_lock")

    def reached(*a, **k):
        raise AssertionError("the sentinel called auto_lock")
    monkeypatch.setattr(lock, "auto_lock", reached)
    _old(saves / "save_001.xml.gz")
    _switch(mod, tk, True)
    text, loud = mod.report(roots, now=1000.0)
    assert files == [] and "the sentinel called auto_lock" not in text
    assert loud and "save-lock off" in text and "cannot be on in this version" in text


def test_TWIN_that_warning_repeats_only_after_the_quiet_period(world, monkeypatch):
    mod, roots, saves, tk = world
    _fake_locks(mod, tk, monkeypatch)
    _switch(mod, tk, True)
    assert mod.report(roots, now=1000.0)[1]
    assert "save-lock off" not in mod.report(roots, now=1010.0)[0]
    assert "save-lock off" in mod.report(roots, now=1000.0 + mod.ELEVATED_REPEAT + 1)[0]

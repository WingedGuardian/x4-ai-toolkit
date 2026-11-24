r"""x4run approval record and recoverable state file.

The state file exists exactly while a game may be running, so it is created exclusively,
replaced atomically and never silently cleared. Nothing here starts, or could start, Steam
or X4: the only outside world is a temp folder and the scripted `FakeSystem` clock.
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _x4run as X


def _approval(**kw) -> X.Approval:
    base = dict(approved_by="user", mode="menu-only", target="main menu",
                desktop="secondary desktop", recorded_at="2026-11-24T10:00:00Z")
    base.update(kw)
    return X.Approval(**base)


def _state(**kw) -> X.RunState:
    base = dict(run_id="run-1", approval=_approval(), phase="starting", process=None,
                started_at="2026-11-24T10:00:01Z")
    base.update(kw)
    return X.RunState(**base)


def _store(tmp_path, fake=None, **kw) -> X.StateStore:
    fake = fake or FakeSystem()
    return X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock, **kw)


# ----------------------------------------------------------------------------- approval


def test_a_valid_approval_is_returned_unchanged():
    a = _approval()
    assert X.require_approval(a) is a
    for mode in ("menu-only", "load-save", "new-game"):
        X.require_approval(_approval(mode=mode))


def test_missing_or_blank_approval_refuses():
    with pytest.raises(X.UnapprovedLaunch):
        X.require_approval(None)
    for field in ("approved_by", "mode", "target", "desktop", "recorded_at"):
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(X.UnapprovedLaunch, match=field):
                X.require_approval(_approval(**{field: blank}))


def test_unknown_mode_refuses():
    for bad in ("headless", "MENU-ONLY", "menu_only", "load save"):
        with pytest.raises(X.UnapprovedLaunch, match="mode"):
            X.require_approval(_approval(mode=bad))


def test_unapproved_launch_is_a_value_error():
    assert issubclass(X.UnapprovedLaunch, ValueError)


# ------------------------------------------------------------------------------- begin


def test_begin_creates_and_a_second_begin_refuses_with_the_recovery_command(tmp_path):
    store = _store(tmp_path)
    first = _state(run_id="first")
    store.begin(first)
    assert store.path.is_file()
    with pytest.raises(X.UnfinishedRun) as exc:
        store.begin(_state(run_id="second"))
    msg = str(exc.value)
    assert str(store.path) in msg
    assert "x4run stop" in msg
    # the refusal must not have touched the existing record
    assert store.read() == first


def test_begin_creates_the_folder_and_a_failed_write_leaves_no_file(tmp_path, monkeypatch):
    store = _store(tmp_path)
    assert not store.folder.exists()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(X.os, "write", boom)
    with pytest.raises(OSError):
        store.begin(_state())
    monkeypatch.undo()
    # a file left half-written would block every later start with a bogus 'unfinished run'
    assert not store.path.exists()
    store.begin(_state())  # and a later begin works


def test_begin_uses_exclusive_create(tmp_path, monkeypatch):
    flags = []
    real = os.open

    def spy(path, f, *a, **k):
        flags.append(f)
        return real(path, f, *a, **k)

    monkeypatch.setattr(X.os, "open", spy)
    _store(tmp_path).begin(_state())
    want = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    assert flags and flags[0] & want == want


# ------------------------------------------------------------------------------ update


def test_update_is_atomic_and_round_trips(tmp_path):
    store = _store(tmp_path)
    store.begin(_state())
    proc = make_proc(4242, path="G:/Games/X4 Foundations/X4.exe",
                     started="2026-11-24T10:00:05.1234567Z")
    new = _state(phase="running", process=proc)
    store.update(new)
    got = store.read()
    assert got == new
    # the EXACT strings survive: they are compared by equality later and never normalised
    assert got.process == proc
    assert got.process.path == "G:/Games/X4 Foundations/X4.exe"
    assert got.process.started == "2026-11-24T10:00:05.1234567Z"
    assert isinstance(got.approval, X.Approval)
    # no temp file is left beside the state file
    assert [p.name for p in store.folder.iterdir()] == [store.path.name]


def test_a_failed_replace_leaves_the_previous_content_and_no_temp_file(tmp_path):
    old = _state(phase="starting")
    store = _store(tmp_path, replace=_always_fail)
    store.begin(old)
    before = store.path.read_bytes()
    with pytest.raises(OSError):
        store.update(_state(phase="running", process=make_proc()))
    assert store.path.read_bytes() == before
    assert store.read() == old
    assert [p.name for p in store.folder.iterdir()] == [store.path.name]


def _always_fail(src, dst):
    raise OSError("replace failed")


def test_update_retries_a_transient_PermissionError_then_succeeds(tmp_path):
    fake = FakeSystem()
    attempts = []

    def flaky(src, dst):
        attempts.append(fake.clock())
        if len(attempts) <= 3:
            raise PermissionError("locked by a scanner")
        os.replace(src, dst)

    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock, replace=flaky)
    store.begin(_state())
    new = _state(phase="running")
    store.update(new)
    assert len(attempts) == 4
    assert attempts == sorted(attempts) and attempts[-1] > attempts[0]  # it waited, virtually
    assert store.read() == new


def test_update_gives_up_on_a_persistent_PermissionError_after_about_ten_seconds(tmp_path):
    fake = FakeSystem()

    def locked(src, dst):
        raise PermissionError("still locked")

    store = X.StateStore(tmp_path / "state", sleep=fake.sleep, clock=fake.clock, replace=locked)
    store.begin(_state())
    old = store.path.read_bytes()
    with pytest.raises(PermissionError):
        store.update(_state(phase="running"))
    assert 9.5 <= fake.now <= 12  # about 10 s of virtual time, then the real error
    assert store.path.read_bytes() == old
    assert [p.name for p in store.folder.iterdir()] == [store.path.name]


def test_update_without_a_begun_run_refuses_rather_than_creating(tmp_path):
    store = _store(tmp_path)
    store.folder.mkdir(parents=True)  # so only the missing RECORD can be the reason
    with pytest.raises(FileNotFoundError, match="begin"):
        store.update(_state())
    assert not store.path.exists()


#: States `read` would reject; each must be refused at WRITE time on both paths.
_UNREADABLE = {
    "unknown phase": lambda: _state(phase="stopping", process=make_proc()),
    "unknown mode": lambda: _state(approval=_approval(mode="headless")),
    "non-int pid": lambda: _state(process=dataclasses.replace(make_proc(), pid="123")),
    "bool pid": lambda: _state(process=dataclasses.replace(make_proc(), pid=True)),
    "non-str field": lambda: _state(run_id=7),
}


@pytest.mark.parametrize("name", list(_UNREADABLE))
def test_update_refuses_a_record_read_would_reject_and_keeps_the_old_one(tmp_path, name):
    store = _store(tmp_path)
    good = _state(phase="running", process=make_proc(4242))
    store.begin(good)
    before = store.path.read_bytes()
    with pytest.raises(X.InvalidState):
        store.update(_UNREADABLE[name]())
    assert store.path.read_bytes() == before
    assert store.read() == good  # the live game's process is still readable
    assert [p.name for p in store.folder.iterdir()] == [store.path.name]


@pytest.mark.parametrize("name", list(_UNREADABLE))
def test_begin_refuses_a_record_read_would_reject_and_creates_no_file(tmp_path, name):
    store = _store(tmp_path)
    with pytest.raises(X.InvalidState):
        store.begin(_UNREADABLE[name]())
    assert not store.path.exists()
    assert not store.folder.exists() or list(store.folder.iterdir()) == []
    store.begin(_state())  # nothing was left to block a valid start


def test_invalid_state_is_a_value_error():
    assert issubclass(X.InvalidState, ValueError)


# -------------------------------------------------------------------------------- read


def test_read_of_a_missing_file_is_none(tmp_path):
    assert _store(tmp_path).read() is None


@pytest.mark.parametrize("text", [
    "", "   ", "{not json", "[]", "null", "42", "{}",
    json.dumps({"run_id": "r"}),
])
def test_read_of_a_corrupt_file_raises_not_none(tmp_path, text):
    store = _store(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_text(text, encoding="utf-8")
    with pytest.raises(X.CorruptState) as exc:
        store.read()
    assert str(store.path) in str(exc.value)
    assert store.path.read_text(encoding="utf-8") == text  # never silently cleared


def test_read_of_undecodable_bytes_raises(tmp_path):
    store = _store(tmp_path)
    store.folder.mkdir(parents=True)
    store.path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(X.CorruptState):
        store.read()


def _good_dict(store) -> dict:
    store.begin(_state(phase="running", process=make_proc()))
    return json.loads(store.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(phase="exploding"),
    lambda d: d.update(run_id=7),
    lambda d: d["approval"].update(mode="headless"),
    lambda d: d["approval"].pop("desktop"),
    lambda d: d["process"].update(pid="123"),
    lambda d: d["process"].update(pid=True),
    lambda d: d["process"].pop("started"),
    lambda d: d.update(approval="x"),
    lambda d: d.update(process=[]),
])
def test_read_of_a_structurally_wrong_file_raises(tmp_path, mutate):
    store = _store(tmp_path)
    d = _good_dict(store)
    mutate(d)
    store.path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(X.CorruptState):
        store.read()


def test_read_does_not_invent_a_process_when_none_was_recorded(tmp_path):
    store = _store(tmp_path)
    store.begin(_state(process=None))
    got = store.read()
    assert got is not None and got.process is None


# ------------------------------------------------------------------------------- clear


def test_clear_removes_it(tmp_path):
    store = _store(tmp_path)
    store.begin(_state())
    store.clear()
    assert not store.path.exists()
    assert store.read() is None
    store.begin(_state())  # the slot is free again


def test_clear_with_nothing_to_clear_is_not_an_error(tmp_path):
    _store(tmp_path).clear()


# ----------------------------------------------------------------------- state folder


def test_state_folder_is_dot_x4run_under_the_toolkit(tmp_path, monkeypatch):
    monkeypatch.setattr(X._paths, "toolkit_root", lambda: tmp_path)
    assert X.state_folder() == tmp_path / ".x4run"


def test_state_folder_refuses_when_the_toolkit_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(X._paths, "toolkit_root", lambda: None)
    with pytest.raises(X.StateFolderError, match="X4_TOOLKIT"):
        X.state_folder()


def test_the_state_folder_is_gitignored():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    lines = [ln.strip() for ln in (root / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "/.x4run/" in lines


def test_dataclasses_are_the_documented_shapes():
    assert [f.name for f in dataclasses.fields(X.Approval)] == [
        "approved_by", "mode", "target", "desktop", "recorded_at"]
    assert [f.name for f in dataclasses.fields(X.RunState)] == [
        "run_id", "approval", "phase", "process", "started_at"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _approval().mode = "x"  # type: ignore[misc]

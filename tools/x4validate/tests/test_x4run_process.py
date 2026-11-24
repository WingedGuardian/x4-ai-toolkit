r"""x4run process snapshot: a failed read is a THIRD state, never 'none running'.

Everything goes through `FakeSystem`; nothing here starts, or could start, Steam or X4.
"""
from __future__ import annotations

import subprocess
import sys

import pytest
from _x4run_fake import FakeSystem, make_proc

from x4validate import _x4run as X


def _read(fake, **kw):
    return X.read_processes(fake.run, sleep=fake.sleep, **kw)


def test_read_returns_the_matching_processes():
    a, b = make_proc(1001), make_proc(1002, started="2026-11-24T10:05:00.5000000Z")
    fake = FakeSystem([a, b])
    assert _read(fake) == [a, b]
    # PowerShell serialises ONE object without a list; that shape must parse to one row.
    fake = FakeSystem([a])
    assert _read(fake) == [a]


def test_empty_output_is_an_empty_list_not_an_error():
    fake = FakeSystem([])
    assert _read(fake) == []
    assert len(fake.calls) == 1  # a real answer: no retry


def test_a_transient_failure_retries_then_succeeds():
    p = make_proc()
    fake = FakeSystem([p])
    fake.fail_reads(2)
    assert _read(fake) == [p]
    assert len(fake.calls) == 3
    assert fake.clock() > 0  # it waited (virtually) between attempts


def test_a_persistent_failure_raises_ProcessReadError_never_an_empty_list():
    fake = FakeSystem([make_proc()])  # a game IS running: an [] here would be the lie
    fake.fail_reads(10)
    with pytest.raises(X.ProcessReadError):
        _read(fake)
    assert len(fake.calls) == 3  # exactly `attempts`
    fake = FakeSystem([])
    fake.fail_reads(10)
    with pytest.raises(X.ProcessReadError):
        _read(fake, attempts=1)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("bad", [
    '[{"ProcessId": 12',                      # truncated
    "not json at all",
    '"just a string"',
    '{"ProcessId": 1, "Name": "X4.exe"}',     # a row missing path and start time
    '[{"ProcessId": "7", "Name": "X4.exe", "ExecutablePath": "p", "Started": "t"}]',
    '[{"ProcessId": 7, "Name": "X4.exe", "ExecutablePath": null, "Started": "t"}]',
    '[1, 2]',
])
def test_malformed_json_is_rejected(bad):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, bad, "")

    with pytest.raises(X.ProcessReadError):
        X.read_processes(run, sleep=lambda s: None)


def test_scripted_malformed_output_raises_after_retrying():
    fake = FakeSystem([make_proc()])
    fake.malformed_reads(3)
    with pytest.raises(X.ProcessReadError):
        _read(fake)


def test_a_timeout_is_a_read_failure():
    p = make_proc()
    fake = FakeSystem([p])
    fake.timeout_reads(1)
    assert _read(fake) == [p]  # transient: retried
    fake = FakeSystem([p])
    fake.timeout_reads(5)
    with pytest.raises(X.ProcessReadError):
        _read(fake)


def test_a_missing_powershell_is_a_read_failure():
    def run(argv, **kw):
        raise FileNotFoundError("powershell.exe")

    with pytest.raises(X.ProcessReadError):
        X.read_processes(run, sleep=lambda s: None)


def test_every_read_is_started_hidden():
    if sys.platform != "win32":
        pytest.skip("CREATE_NO_WINDOW exists only on Windows (NO_WINDOW is 0 here)")
    fake = FakeSystem([make_proc()])
    fake.fail_reads(1)
    fake.timeout_reads(1)
    _read(fake)
    assert len(fake.calls) == 3
    assert X.NO_WINDOW == 0x08000000
    for call in fake.calls:
        assert call.creationflags & X.NO_WINDOW
        assert not call.creationflags & 0x8  # DETACHED_PROCESS


def test_no_read_is_ever_detached_on_any_platform():
    fake = FakeSystem([make_proc()])
    fake.fail_reads(1)
    _read(fake)
    assert fake.calls
    for call in fake.calls:
        assert not call.creationflags & 0x8  # DETACHED_PROCESS


def test_missing_stdout_is_a_read_failure_never_an_empty_list():
    # exit 0 with stdout None is what a dead reader thread leaves behind
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, None, "")

    with pytest.raises(X.ProcessReadError):
        X.read_processes(run, sleep=lambda s: None)
    assert len(calls) == 3  # retried like any other failure


def test_a_decode_error_from_the_runner_is_a_read_failure():
    def run(argv, **kw):
        raise UnicodeDecodeError("utf-8", bytes([0xFF]), 0, 1, "invalid start byte")

    with pytest.raises(X.ProcessReadError):
        X.read_processes(run, sleep=lambda s: None)


def test_the_read_decodes_leniently():
    fake = FakeSystem([])
    _read(fake)
    # the fake records only argv/flags; pin the kwarg through a direct capture
    seen = {}

    def run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    X.read_processes(run, sleep=lambda s: None)
    assert seen["errors"] == "replace"


def test_the_read_is_a_bounded_powershell_query_filtered_by_name():
    fake = FakeSystem([])
    _read(fake, name="Other.exe")
    (call,) = fake.calls
    assert call.timeout == 15
    script = call.argv[-1]
    assert call.argv[0] == "powershell.exe"
    for needle in ("Get-CimInstance Win32_Process", "Name = 'Other.exe'", "ProcessId",
                   "ExecutablePath", "ToUniversalTime", "ConvertTo-Json"):
        assert needle in script


@pytest.mark.parametrize("name", ["X4.exe'; calc", 'a"b', "", "a b", "x\n"])
def test_an_unsafe_process_name_is_refused_before_any_call(name):
    fake = FakeSystem([])
    with pytest.raises(ValueError):
        _read(fake, name=name)
    assert fake.calls == []


def test_the_fake_refuses_a_call_it_does_not_model():
    fake = FakeSystem([])
    with pytest.raises(AssertionError):
        fake.run(["something-else.exe"], creationflags=X.NO_WINDOW)


def test_the_virtual_clock_advances_on_sleep_and_fires_events():
    fake = FakeSystem([])
    fake.at(5, lambda f: f.table.append(make_proc()))
    assert _read(fake) == []
    fake.sleep(5)
    assert _read(fake) == [make_proc()]


# ------------------------------------------------------------------------- exit watch

def test_one_missing_read_does_not_end_the_run():
    rec = make_proc(1001)
    w = X.ExitWatch(rec)
    assert w.observe([rec]) is False
    assert w.observe([]) is False


def test_three_consecutive_misses_end_it():
    rec = make_proc(1001)
    w = X.ExitWatch(rec)
    assert [w.observe([]) for _ in range(3)] == [False, False, True]
    # a custom threshold is honoured, not hard-coded to 3
    w = X.ExitWatch(rec, misses=1)
    assert w.observe([]) is True
    w = X.ExitWatch(rec, misses=5)
    assert [w.observe([]) for _ in range(5)] == [False] * 4 + [True]


def test_a_reappearance_resets_the_count():
    rec = make_proc(1001)
    w = X.ExitWatch(rec)
    assert w.observe([]) is False
    assert w.observe([]) is False
    assert w.observe([rec]) is False  # back: the two misses are forgotten
    assert w.observe([]) is False
    assert w.observe([]) is False     # only two since the reappearance, not four
    assert w.observe([]) is True


def test_a_failed_read_neither_counts_nor_resets():
    rec = make_proc(1001)
    # None between misses must not advance the count: miss, None, miss is still only 2
    w = X.ExitWatch(rec)
    assert [w.observe(x) for x in ([], None, [])] == [False, False, False]
    assert w.observe([]) is True      # the third REAL miss ends it
    # ... and must not reset it: two misses survive any number of failed reads
    w = X.ExitWatch(rec)
    w.observe([])
    w.observe([])
    for _ in range(5):
        assert w.observe(None) is False  # never ends the run on its own
    assert w.observe([]) is True
    # a run of failed reads alone is never an exit
    w = X.ExitWatch(rec)
    assert not any(w.observe(None) for _ in range(20))


def test_a_reused_pid_with_a_different_start_time_counts_as_a_miss():
    rec = make_proc(1001, started="2026-11-24T10:00:00.0000000Z")
    impostor = make_proc(1001, started="2026-11-24T11:30:00.0000000Z")  # same pid, new process
    w = X.ExitWatch(rec)
    assert [w.observe([impostor]) for _ in range(3)] == [False, False, True]


@pytest.mark.parametrize("field, value", [
    ("name", "Other.exe"),
    ("path", "G:/elsewhere/X4.exe"),
    ("started", "2026-11-24T10:00:00.0000001Z"),  # exact comparison, one tick apart
    ("pid", 2002),
])
def test_every_identity_field_must_match_to_count_as_present(field, value):
    rec = make_proc(1001)
    other = make_proc(**{"pid": 1001, field: value}) if field != "pid" else make_proc(value)
    w = X.ExitWatch(rec)
    assert [w.observe([other]) for _ in range(3)] == [False, False, True]


def test_the_recorded_process_is_found_among_others():
    rec = make_proc(1001)
    noise = [make_proc(1002), make_proc(1001, started="2026-11-24T09:00:00.0000000Z"), rec]
    w = X.ExitWatch(rec)
    for _ in range(10):
        assert w.observe(noise) is False


@pytest.mark.parametrize("bad", [0, -1])
def test_a_miss_threshold_below_one_is_refused(bad):
    with pytest.raises(ValueError):
        X.ExitWatch(make_proc(), misses=bad)


def test_exit_watch_is_not_latched_so_observing_must_stop_at_the_first_true():
    rec = make_proc(1001)
    w = X.ExitWatch(rec)
    assert [w.observe([]) for _ in range(3)] == [False, False, True]
    # a failed read after True cannot lower the count: it stays True
    assert w.observe(None) is True
    # ... but a present read after True does, and it returns False again
    assert w.observe([rec]) is False
    assert w.observe(None) is False

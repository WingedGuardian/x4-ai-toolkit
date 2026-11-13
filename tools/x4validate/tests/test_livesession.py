"""Synthetic public identity controls; no native game calls or persistence claim."""
from __future__ import annotations

import ctypes

import pytest

from x4validate import _livesession as S

PEER = {"pid": 123, "creation_filetime": "133700000000000000", "image": "C:/Game/X4.exe"}
REV = "a" * 64
UI = "b" * 32


class Transport:
    def __init__(self):
        self.peer = dict(PEER)
        self.session = ""
        self.calls = []
        self.mutate = lambda reply: reply
        self.drop = False

    def exchange(self, request):
        self.calls.append(dict(request))
        mode = request["mode"]
        okay = mode == "probe" or (mode == "fresh" and not self.session) or (
            mode == "resume" and self.session == request["session"])
        if okay and mode == "fresh":
            self.session = request["session"]
        reply = dict(request, kind=("STATE" if mode == "probe" else "READY") if okay else "REFUSED",
                     provider=S.PROVIDER, revision=REV, session=self.session,
                     has_session=bool(self.session), reason="", age=10, world_token="saved-token")
        if self.drop:
            raise TimeoutError("lost reply")
        return self.mutate(reply)


def setup(tmp_path):
    transport = Transport()
    journal = S.SessionJournal(tmp_path / "loads")
    link = S.SessionLink(journal, lambda: transport.peer, transport.exchange, REV, UI)
    return transport, journal, link


def start(link):
    return link.fresh(link.probe())


def stamp(identity):
    return {"provider": identity["provider"], "revision": identity["revision"],
            "session": identity["session"], "generation": identity["ui_generation"],
            "world_token": identity["world_token"]}


def test_fresh_ready_and_bound_reply(tmp_path):
    t, journal, link = setup(tmp_path)
    identity = start(link)
    assert identity["load_generation"] == 1
    assert identity["world_lineage"] == "UNKNOWN"
    assert identity["world_token"] == "saved-token"
    assert link.verify_reply(stamp(identity)) == identity
    assert journal.records()[0]["session"] == identity["session"]
    assert [r["mode"] for r in t.calls] == ["probe", "fresh"]


def test_ui_reload_resumes_without_new_load(tmp_path):
    t, journal, link = setup(tmp_path)
    first = start(link)
    resumed = S.SessionLink(journal, lambda: t.peer, t.exchange, REV, "c" * 32)
    second = resumed.resume(resumed.probe())
    assert second["session"] == first["session"]
    assert second["ui_generation"] != first["ui_generation"]
    assert len(journal.records()) == 1
    with pytest.raises(S.SessionUnavailable, match="different"):
        resumed.verify_reply(stamp(first))


def test_same_old_save_reloaded_gets_new_nonce_and_host_generation(tmp_path):
    t, journal, link = setup(tmp_path)
    first = start(link)
    t.session = ""  # authoritative MD load invalidation; world token/age unchanged
    second = start(link)
    assert second["session"] != first["session"]
    assert second["load_generation"] == 2
    assert second["world_token"] == first["world_token"]
    with pytest.raises(S.SessionUnavailable):
        link.verify_reply(stamp(first))


def test_lost_fresh_ack_consumes_nonce_resume_only(tmp_path):
    t, journal, link = setup(tmp_path)
    probe = link.probe()
    t.drop = True
    with pytest.raises(S.SessionUnavailable, match="no automatic retry"):
        link.fresh(probe)
    assert len(journal.records()) == 1
    with pytest.raises(S.SessionUnavailable):
        link.identity()
    t.drop = False
    with pytest.raises(S.SessionUnavailable, match="absent"):
        link.fresh(link.probe())
    result = link.resume(link.probe())
    assert result["session"] == journal.records()[0]["session"]
    assert [r["mode"] for r in t.calls].count("fresh") == 1


@pytest.mark.parametrize("field,value", [
    ("challenge", "d" * 32), ("generation", "d" * 32), ("revision", "d" * 64),
    ("provider", "ror-fixtures-v1"), ("mode", "resume"), ("kind", "READY"),
    ("has_session", 0), ("age", True), ("age", float("nan")),
    ("age", float("inf")), ("age", -1), ("session", "wrong"),
    ("world_token", 5), ("reason", None),
])
def test_probe_malformed_refused(tmp_path, field, value):
    t, _, link = setup(tmp_path)
    t.mutate = lambda r: dict(r, **{field: value})
    with pytest.raises(S.SessionUnavailable):
        link.probe()


def test_helper_unavailable_envelope_reports_reason(tmp_path):
    # Run-03 (2026-11-10): the helper's fault envelope was reported only as "malformed".
    t, _, link = setup(tmp_path)
    t.mutate = lambda r: {"kind": "UNAVAILABLE", "reason": "SESSION_PROTOCOL_FAULT: invalid reply session"}
    with pytest.raises(S.SessionUnavailable, match="helper session unavailable: SESSION_PROTOCOL_FAULT"):
        link.probe()
    t.mutate = lambda r: {"kind": "UNAVAILABLE", "reason": 5}
    with pytest.raises(S.SessionUnavailable, match="malformed"):
        link.probe()


def test_replayed_ready_challenge_refused(tmp_path):
    t, journal, link = setup(tmp_path)
    start(link)
    probe = link.probe()
    t.mutate = lambda r: dict(r, challenge=t.calls[0]["challenge"])
    with pytest.raises(S.SessionUnavailable):
        link.resume(probe)
    assert len(journal.records()) == 1


def test_stale_probe_no_dispatch(tmp_path):
    t, _, link = setup(tmp_path)
    stale = link.probe()
    link.probe()
    with pytest.raises(S.SessionUnavailable, match="stale"):
        link.fresh(stale)
    assert len(t.calls) == 2


def test_peer_changed_during_exchange(tmp_path):
    t, _, link = setup(tmp_path)
    def change(reply):
        t.peer["creation_filetime"] = "133700000000000001"
        return reply
    t.mutate = change
    with pytest.raises(S.SessionUnavailable):
        link.probe()


@pytest.mark.parametrize("field,value", [("pid", 124), ("creation_filetime", "133700000000000001"),
                                         ("image", "C:/Other/X4.exe")])
def test_peer_changed_after_ready(tmp_path, field, value):
    t, _, link = setup(tmp_path)
    start(link)
    t.peer[field] = value
    with pytest.raises(S.SessionUnavailable):
        link.identity()


def test_missing_durable_record_cannot_resume(tmp_path):
    t, _, link = setup(tmp_path)
    t.session = "f" * 32
    with pytest.raises(S.SessionUnavailable, match="durable"):
        link.resume(link.probe())


def test_resume_absent_does_not_fresh_fallback(tmp_path):
    t, _, link = setup(tmp_path)
    with pytest.raises(S.SessionUnavailable):
        link.resume(link.probe())
    assert [r["mode"] for r in t.calls] == ["probe"]


def test_old_saved_nonce_refused_even_same_peer(tmp_path):
    t, _, link = setup(tmp_path)
    old = start(link)["session"]
    t.session = ""
    start(link)
    t.session = old
    with pytest.raises(S.SessionUnavailable, match="latest"):
        link.resume(link.probe())


def test_journal_changes_after_probe_refuses(tmp_path):
    t, journal, link = setup(tmp_path)
    probe = link.probe()
    journal.allocate(PEER, REV, [])
    with pytest.raises(S.SessionUnavailable, match="journal changed"):
        link.fresh(probe)
    assert len(t.calls) == 1


def test_incomplete_journal_refuses_without_repair(tmp_path):
    _, journal, link = setup(tmp_path)
    journal.path.mkdir()
    path = journal.path / "00000001.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(S.SessionUnavailable, match="unreadable"):
        link.probe()
    assert path.read_text() == "{"


def test_duplicate_journal_key_refused(tmp_path):
    _, journal, link = setup(tmp_path)
    start(link)
    path = journal.path / "00000001.json"
    path.write_text(path.read_text().replace('"version":1', '"version":1,"version":1'))
    with pytest.raises(S.SessionUnavailable, match="duplicate"):
        journal.records()


def test_nonce_collision_no_reuse(tmp_path, monkeypatch):
    _, journal, link = setup(tmp_path)
    first = start(link)
    monkeypatch.setattr(S.secrets, "token_hex", lambda n: first["session"])
    with pytest.raises(S.SessionUnavailable, match="collision"):
        journal.allocate(PEER, REV, journal.records())
    assert len(journal.records()) == 1


def test_nonlatest_link_invalidated(tmp_path):
    _, journal, link = setup(tmp_path)
    start(link)
    journal.allocate(PEER, REV, journal.records())
    with pytest.raises(S.SessionUnavailable):
        link.identity()


def test_unknown_or_extra_reply_identity_not_accepted(tmp_path):
    _, _, link = setup(tmp_path)
    identity = start(link)
    with pytest.raises(S.SessionUnavailable):
        link.verify_reply(dict(stamp(identity), qualified=True))


def test_identity_return_is_copy(tmp_path):
    _, _, link = setup(tmp_path)
    identity = start(link)
    identity["peer"]["pid"] = 99
    assert link.identity()["peer"]["pid"] == 123


def test_windows_peer_unavailable_platform_refuses(monkeypatch):
    monkeypatch.setattr(S.os, "name", "posix")
    with pytest.raises(S.SessionUnavailable, match="Windows-only"):
        S.connected_pipe_peer(1)


@pytest.mark.parametrize("value", [None, {"bad": float("nan")}, {"bad": object()}])
def test_invalid_reply_identity_clears_ready(tmp_path, value):
    _, _, link = setup(tmp_path)
    start(link)
    with pytest.raises(S.SessionUnavailable):
        link.verify_reply(value)
    with pytest.raises(S.SessionUnavailable, match="not ready"):
        link.identity()


class NativeFunction:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class Kernel:
    def __init__(self, fail=None, changed=False, exited=False, image="C:/Game/X4.exe"):
        self.closed = []
        self.peer_calls = 0
        def peer(handle, output):
            self.peer_calls += 1
            output._obj.value = 124 if changed and self.peer_calls == 2 else 123
            return fail != "peer"
        def times(handle, created, exited, kernel, user):
            created._obj.dwLowDateTime = 123456789
            created._obj.dwHighDateTime = 321
            return fail != "times"
        def name(handle, flags, output, size):
            output.value = image
            size._obj.value = len(image)
            return fail != "image"
        def exitcode(handle, output):
            output._obj.value = 0 if exited else 259
            return fail != "exit"
        self.GetNamedPipeClientProcessId = NativeFunction(peer)
        self.OpenProcess = NativeFunction(lambda access, inherit, pid: 0 if fail == "open" else 900)
        self.GetProcessTimes = NativeFunction(times)
        self.QueryFullProcessImageNameW = NativeFunction(name)
        self.GetExitCodeProcess = NativeFunction(exitcode)
        self.CloseHandle = NativeFunction(lambda handle: self.closed.append(handle) or True)


@pytest.mark.skipif(S.os.name != "nt", reason="Windows ctypes ABI stub")
def test_windows_peer_reads_exact_filetime_and_closes(monkeypatch):
    kernel = Kernel()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel)
    result = S.connected_pipe_peer(800)
    assert result == dict(PEER, creation_filetime=str((321 << 32) | 123456789))
    assert kernel.closed == [900]
    assert kernel.peer_calls == 2


@pytest.mark.skipif(S.os.name != "nt", reason="Windows ctypes ABI stub")
@pytest.mark.parametrize("options", [
    {"fail": "peer"}, {"fail": "open"}, {"fail": "times"}, {"fail": "image"},
    {"fail": "exit"}, {"changed": True}, {"exited": True}, {"image": "C:/Game/other.exe"},
])
def test_windows_peer_refuses_unavailable_changed_or_foreign(monkeypatch, options):
    kernel = Kernel(**options)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel)
    with pytest.raises(S.SessionUnavailable):
        S.connected_pipe_peer(800)
    assert kernel.closed == ([] if options.get("fail") in {"peer", "open"} else [900])


def test_journal_file_root_refused(tmp_path):
    path = tmp_path / "file"
    path.write_text("preserved")
    with pytest.raises(S.SessionUnavailable, match="not a directory"):
        S.SessionJournal(path).records()
    assert path.read_text() == "preserved"


def test_journal_link_root_refused(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"local symlink creation unavailable: {exc}")
    for path in (link, link / "child"):
        with pytest.raises(S.SessionUnavailable, match="linked"):
            S.SessionJournal(path).records()


def test_journal_oversized_record_refused(tmp_path):
    _, journal, _ = setup(tmp_path)
    journal.path.mkdir()
    (journal.path / "00000001.json").write_bytes(b" " * 65537)
    with pytest.raises(S.SessionUnavailable, match="oversized"):
        journal.records()


def test_wire_stamp_does_not_accept_host_facts_as_native(tmp_path):
    _, _, link = setup(tmp_path)
    identity = start(link)
    with pytest.raises(S.SessionUnavailable):
        link.verify_reply(identity)


def test_ready_requires_producer_world_observation_without_lineage_claim(tmp_path):
    t, _, link = setup(tmp_path)
    probe = link.probe()
    t.mutate = lambda row: dict(row, world_token="")
    with pytest.raises(S.SessionUnavailable, match="malformed"):
        link.fresh(probe)
    with pytest.raises(S.SessionUnavailable, match="not ready"):
        link.identity()

"""Synthetic action journal controls; no game or native behavior is qualified."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from x4validate._liveactions import (
    ActionJournal, ActionRefused, ActionUncertain, request_digest, validate_request,
)

REQUEST = {"version": 1, "id": "a" * 32, "session": "world-token",
           "provider": "test-fixture", "action": "start",
           "arguments": {"case": "fixture-1"}, "binding": "prepared-token"}


def receipt(request=REQUEST, status="prepared", **changes):
    result = {"version": 1, "id": request["id"], "session": request["session"],
              "digest": request_digest(request), "status": status,
              "observations": {}, "reason": ""}
    result.update(changes)
    return result


class Transport:
    def __init__(self, outcome="completed"):
        self.outcome = outcome
        self.calls = []
        self.observed = 0

    def execute(self, request):
        self.calls.append(copy.deepcopy(request))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return receipt(request, self.outcome, observations={"synthetic": True})

    def status(self, request):
        self.observed += 1
        return receipt(request, "completed", observations={"synthetic": True})


def create(tmp_path, request=REQUEST):
    return ActionJournal.create(tmp_path, request, receipt(request))


def test_dispatch_marker_exists_before_mutating_transport_call(tmp_path):
    journal = create(tmp_path)

    class Checked(Transport):
        def execute(self, request):
            marker = json.loads((journal.path / "dispatch.json").read_bytes())
            assert marker == {"id": request["id"], "digest": request_digest(request)}
            return super().execute(request)

    transport = Checked()
    assert journal.execute(transport)["status"] == "completed"
    assert len(transport.calls) == 1
    assert (journal.path / "observation-000001.json").exists()


def test_lost_reply_is_not_retried_even_after_reopening(tmp_path):
    journal = create(tmp_path)
    transport = Transport(TimeoutError("reply lost after payment"))
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    reopened = ActionJournal(journal.path)
    with pytest.raises(ActionRefused, match="already reserved"):
        reopened.execute(transport)
    assert len(transport.calls) == 1
    assert reopened.observe(transport)["status"] == "completed"
    assert transport.observed == 1
    with pytest.raises(ActionRefused):
        reopened.execute(transport)


def test_two_clients_cannot_dispatch_same_request(tmp_path):
    journal = create(tmp_path)
    transport = Transport()

    def attempt(_):
        try:
            ActionJournal(journal.path).execute(transport)
            return "sent"
        except ActionRefused:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["refused", "sent"]
    assert len(transport.calls) == 1


def test_crash_between_marker_and_send_is_not_called_unacted(tmp_path):
    journal = create(tmp_path)
    (journal.path / "dispatch.json").write_bytes(b"partial crash marker")
    transport = Transport()
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    assert not transport.calls


@pytest.mark.parametrize("winner,loser", [("completed", "failed"), ("failed", "completed")])
def test_concurrent_observation_preserves_losing_receipt_and_blocks_later_success(
        tmp_path, monkeypatch, winner, loser):
    import x4validate._liveactions as module

    journal = create(tmp_path)
    journal.execute(Transport("running"))
    original = module._write_new

    def race(path, value):
        if path.name == "observation-000002.json":
            original(path, receipt(status=winner))
            raise FileExistsError(path)
        original(path, value)

    monkeypatch.setattr(module, "_write_new", race)

    class Competing(Transport):
        def status(self, request):
            self.observed += 1
            return receipt(request, loser)

    transport = Competing()
    with pytest.raises(ActionUncertain, match="concurrent observation"):
        journal.observe(transport)
    assert json.loads((journal.path / "observation-000002.json").read_bytes())["status"] == winner
    conflicts = list(journal.path.glob("contradiction-race-*.json"))
    assert len(conflicts) == 1
    assert json.loads(conflicts[0].read_bytes())["received"]["status"] == loser
    with pytest.raises(ActionUncertain):
        ActionJournal(journal.path).observe(transport)
    assert transport.observed == 1


@pytest.mark.parametrize("change", [
    {"id": "b" * 32}, {"session": "different-world"},
    {"digest": "0" * 64}, {"status": "PASS"}, {"version": True},
    {"observations": []}, {"observations": {"bad": float("nan")}},
])
def test_unbound_or_malformed_execution_cannot_pass_or_replay(tmp_path, change):
    journal = create(tmp_path)

    class Bad(Transport):
        def execute(self, request):
            self.calls.append(request)
            return receipt(request, "completed", **change)

    transport = Bad()
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    with pytest.raises(ActionRefused):
        journal.execute(transport)
    assert len(transport.calls) == 1
    assert not list(journal.path.glob("observation-*.json"))


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"id": "../escape"},
    {"session": "world\nother"}, {"provider": "eval;evil()"},
    {"arguments": []}, {"arguments": {"bad": float("inf")}},
    {"arguments": {"too_large": "x" * 1500}},
])
def test_bad_request_is_refused_before_journal_creation(tmp_path, change):
    request = REQUEST | change
    with pytest.raises(ActionRefused):
        validate_request(request)
    assert not list(tmp_path.iterdir())


def test_duplicate_id_does_not_overwrite_original(tmp_path):
    journal = create(tmp_path)
    before = (journal.path / "prepared.json").read_bytes()
    other = copy.deepcopy(REQUEST)
    other["arguments"]["case"] = "different"
    with pytest.raises(ActionRefused):
        create(tmp_path, other)
    assert (journal.path / "prepared.json").read_bytes() == before


def test_mutated_preparation_refuses_before_send(tmp_path):
    journal = create(tmp_path)
    prepared = json.loads((journal.path / "prepared.json").read_bytes())
    prepared["request"]["arguments"]["case"] = "mutated"
    (journal.path / "prepared.json").write_text(json.dumps(prepared), encoding="utf-8")
    transport = Transport()
    with pytest.raises(ActionRefused):
        journal.execute(transport)
    assert not transport.calls
    assert not (journal.path / "dispatch.json").exists()


def test_returned_preparation_cannot_erase_dispatch(tmp_path):
    journal = create(tmp_path)
    with pytest.raises(ActionUncertain):
        journal.execute(Transport("prepared"))
    with pytest.raises(ActionRefused):
        journal.execute(Transport())


def test_terminal_contradiction_does_not_replace_earlier_evidence(tmp_path):
    journal = create(tmp_path)
    journal.execute(Transport())
    before = (journal.path / "observation-000001.json").read_bytes()

    class Contradiction(Transport):
        def status(self, request):
            return receipt(request, "failed", reason="contradictory")

    with pytest.raises(ActionUncertain, match="terminal"):
        journal.observe(Contradiction())
    assert (journal.path / "observation-000001.json").read_bytes() == before
    conflict = json.loads((journal.path / "contradiction-000001.json").read_bytes())
    assert conflict["previous"]["status"] == "completed"
    assert conflict["received"]["status"] == "failed"


def test_unfinished_or_refused_preparation_cannot_create_journal(tmp_path):
    with pytest.raises(ActionRefused):
        ActionJournal.create(tmp_path, REQUEST, receipt(status="refused"))
    assert not list(tmp_path.iterdir())


def test_unexpected_request_fields_are_not_ignored():
    with pytest.raises(ActionRefused):
        validate_request(REQUEST | {"automatic_retry": True})


def test_transport_cannot_rebind_request_by_mutating_its_argument(tmp_path):
    journal = create(tmp_path)

    class Mutating(Transport):
        def execute(self, request):
            self.calls.append(copy.deepcopy(request))
            request["arguments"]["case"] = "wrong-target"
            return receipt(request, "completed")

    transport = Mutating()
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    assert journal.request == REQUEST
    assert not list(journal.path.glob("observation-*.json"))
    with pytest.raises(ActionRefused):
        journal.execute(transport)
    assert len(transport.calls) == 1


def test_missing_marker_with_completed_evidence_never_dispatches(tmp_path):
    journal = create(tmp_path)
    transport = Transport()
    journal.execute(transport)
    (journal.path / "dispatch.json").unlink()
    with pytest.raises(ActionUncertain):
        ActionJournal(journal.path).execute(transport)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("damage", ["earlier", "marker", "duplicate"])
def test_corrupt_history_refuses_before_observing_or_dispatching(tmp_path, damage):
    journal = create(tmp_path)
    transport = Transport()
    journal.execute(transport)
    journal.observe(transport)
    if damage == "earlier":
        path = journal.path / "observation-000001.json"
        data = json.loads(path.read_bytes())
        data["session"] = "wrong"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "marker":
        (journal.path / "dispatch.json").write_text('{"id":"wrong","digest":"wrong"}')
    else:
        path = journal.path / "observation-000001.json"
        path.write_text(path.read_text().replace('"version":1', '"version":2,"version":1'))
    with pytest.raises(ActionUncertain):
        journal.observe(transport)
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    assert len(transport.calls) == 1 and transport.observed == 1


@pytest.mark.parametrize("state", ["completed", "running", "refused", "failed", "unknown"])
def test_fresh_status_cannot_claim_an_execution_without_dispatch(tmp_path, state):
    journal = create(tmp_path)

    class Unexpected(Transport):
        def status(self, request):
            return receipt(request, state)

    transport = Unexpected()
    with pytest.raises(ActionUncertain, match="without dispatch"):
        journal.observe(transport)
    assert not list(journal.path.glob("observation-*.json"))
    assert (journal.path / "contradiction-000001.json").exists()
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    assert not transport.calls

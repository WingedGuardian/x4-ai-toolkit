import json

import pytest

from x4validate import _modlist, _nexus


def page(total=1):
    return {"data": {"mods": {"totalCount": total, "nodes": [
        {"modId": 77, "name": "A candidate", "version": "1", "author": "Author",
         "updatedAt": "2026-10-15T00:00:00Z", "status": "hidden"}]}}}


def test_discovery_preserves_key_lookup_and_reports_capped_candidate_metadata(monkeypatch):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: "fake-key")
    seen = []
    def post(url, body, headers):
        seen.append((url, body, headers))
        return page(9)
    monkeypatch.setattr(_nexus, "_post_json", post)
    result = _nexus.discover("ships", 2)
    assert seen[0][2]["apikey"] == "fake-key"
    assert seen[0][1]["variables"]["f"]["gameDomainName"][0]["value"] == "x4foundations"
    assert result["state"] == "partial" and result["total"] == 9
    assert result["candidates"][0]["status"] == "hidden"
    assert result["installed_evidence"] == "not_checked" and "fake-key" not in str(result)


@pytest.mark.parametrize("payload", [{}, {"errors": [{"message": "secret"}]},
                                    {"data": {"mods": {"nodes": [None]}}}])
def test_legacy_identity_search_cannot_turn_failure_into_zero(monkeypatch, payload):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: "fake-key")
    monkeypatch.setattr(_nexus, "_post_json", lambda *a: payload)
    with pytest.raises(_nexus.NexusError):
        _nexus.search_mods("a")


def test_search_command_is_read_only_and_exposes_nonanswer(monkeypatch, capsys):
    monkeypatch.setattr(_nexus, "discover", lambda *a: (_ for _ in ()).throw(
        _nexus.NexusAuthError("secret-server-body")))
    assert _modlist.main(["search", "a", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "unavailable" and "secret" not in str(result)


def test_search_zero_is_an_answer_not_an_outage(monkeypatch, capsys):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: "fake-key")
    monkeypatch.setattr(_nexus, "_post_json", lambda *a: {"data": {"mods": {"totalCount": 0, "nodes": []}}})
    assert _modlist.main(["search", "a", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "zero"

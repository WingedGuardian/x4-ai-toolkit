"""The Nexus transport maps every failure, and a run-wide one stops the run (RG-1, RG-2).

`test_audit0924_runtime.py` pins the two headline shapes (a network drop mid-refresh keeps
what was fetched; a 401 stops the run with every lane intact). These pin the parts it does
not reach: the rate-limit paths -- an HTTP 429, and a response whose `X-RL-*-Remaining`
header says the budget is spent, which must stop the NEXT call before it is sent -- and the
stub seam: a test or gate that replaces `_get_json` must get the same mapping as the real
transport. No network: `urlopen` is stubbed throughout.
"""
from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

from x4validate import _nexus


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_META = json.dumps({"name": "M", "version": "1", "updated_timestamp": 0,
                    "status": "published", "author": "a"}).encode()


@pytest.fixture(autouse=True)
def _key_and_clean_budget(monkeypatch):
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")
    _nexus.reset_rate_limit()
    yield
    _nexus.reset_rate_limit()


def test_http_429_is_a_run_wide_stop(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    with pytest.raises(_nexus.NexusRateLimited):
        _nexus.fetch_mod(1)


def test_a_spent_budget_header_stops_the_NEXT_call_before_it_is_sent(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Resp(_META, {"X-RL-Hourly-Remaining": "0", "X-RL-Daily-Remaining": "500"})
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    assert _nexus.fetch_mod(1).name == "M"          # the answer that reported 0 is still valid
    with pytest.raises(_nexus.NexusRateLimited, match="X-RL-Hourly-Remaining"):
        _nexus.fetch_mod(2)
    assert len(calls) == 1, "the call after a spent budget must not reach the network"


def test_a_budget_above_zero_does_not_stop_anything(monkeypatch):
    """The twin: without it the test above passes on a transport that refuses always."""
    monkeypatch.setattr(_nexus.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_META, {"X-RL-Hourly-Remaining": "7"}))
    assert _nexus.fetch_mod(1).name == "M"
    assert _nexus.fetch_mod(2).name == "M"


@pytest.mark.parametrize("exc, kind", [
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), _nexus.NexusError),
    (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), _nexus.NexusAuthError),
    (urllib.error.URLError("no route"), _nexus.NexusUnreachable),
    (TimeoutError("timed out"), _nexus.NexusUnreachable),
    (json.JSONDecodeError("x", "<html>", 0), _nexus.NexusError),
])
def test_a_stubbed_transport_gets_the_same_mapping(monkeypatch, exc, kind):
    """Gates and tests replace `_get_json` directly; its failures must still be mapped."""
    def boom(url, headers):
        raise exc
    monkeypatch.setattr(_nexus, "_get_json", boom)
    with pytest.raises(kind):
        _nexus.fetch_mod(1)


def test_a_403_on_the_FIRST_call_of_a_run_is_a_key_failure(monkeypatch):
    monkeypatch.setattr(_nexus, "_get_json", lambda url, headers: (_ for _ in ()).throw(
        urllib.error.HTTPError(url, 403, "Forbidden", {}, None)))
    with pytest.raises(_nexus.NexusAuthError):
        _nexus.fetch_mod(1)


def test_a_403_AFTER_a_successful_call_is_per_request_not_fatal(monkeypatch):
    """Review item 2: the key has already been accepted this run, so a later 403 is about
    THAT request (a hidden mod's files.json, a Cloudflare block), and must not stop the
    run. No network call is made to find out."""
    def get(url, headers):
        if url.endswith("/2.json"):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return json.loads(_META)
    monkeypatch.setattr(_nexus, "_get_json", get)
    assert _nexus.fetch_mod(1).name == "M"
    with pytest.raises(_nexus.NexusError) as exc:
        _nexus.fetch_mod(2)
    assert not isinstance(exc.value, _nexus.NexusFatal), type(exc.value)


def test_a_401_is_fatal_even_after_a_successful_call(monkeypatch):
    def get(url, headers):
        if url.endswith("/2.json"):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        return json.loads(_META)
    monkeypatch.setattr(_nexus, "_get_json", get)
    _nexus.fetch_mod(1)
    with pytest.raises(_nexus.NexusAuthError):
        _nexus.fetch_mod(2)


@pytest.mark.parametrize("exc", [http.client.IncompleteRead(b"par"),
                                 http.client.BadStatusLine("garbage")])
def test_http_client_protocol_errors_are_mapped_not_tracebacks(monkeypatch, exc):
    """Review item 4: http.client.HTTPException is neither URLError nor OSError."""
    def urlopen(req, timeout=None):
        raise exc
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    with pytest.raises(_nexus.NexusUnreachable):
        _nexus.fetch_mod(1)


def test_only_run_wide_causes_are_fatal():
    assert issubclass(_nexus.NexusAuthError, _nexus.NexusFatal)
    assert issubclass(_nexus.NexusRateLimited, _nexus.NexusFatal)
    assert issubclass(_nexus.NexusUnreachable, _nexus.NexusFatal)
    assert not issubclass(_nexus.NexusFatal, _nexus.NexusAuthError)


# --- steam_title: "could not ask" vs "asked, no title" (finding 1) ---------------------

@pytest.mark.parametrize("exc", [
    urllib.error.URLError("no route"),
    TimeoutError("timed out"),
    OSError("connection reset"),
    ValueError("not json"),  # json.load on a non-JSON body
    http.client.IncompleteRead(b"partial", 100),
    http.client.BadStatusLine("bad status"),
])
def test_steam_title_RAISES_on_a_transport_failure_not_returns_none(monkeypatch, exc):
    """A transport failure is not the same fact as 'Steam has no title for this item' --
    the caller must be able to tell them apart (AUDIT: a Steam outage was recording
    `unmatched` over a real prior identity verdict)."""
    def urlopen(req, timeout=None):
        raise exc
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    with pytest.raises(_nexus.SteamUnavailable):
        _nexus.steam_title("ws_1")


def test_steam_title_still_returns_none_on_a_REAL_no_title_answer(monkeypatch):
    """Twin: Steam answering successfully with nothing found is unaffected."""
    def urlopen(req, timeout=None):
        return _Resp(json.dumps({"response": {"publishedfiledetails": [{"result": 9}]}}).encode())
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    assert _nexus.steam_title("ws_1") is None
    assert issubclass(_nexus.NexusFatal, _nexus.NexusError)   # old `except NexusError` still catches


def test_steam_title_maps_a_dropped_response_body_to_unavailable(monkeypatch):
    class Dropped(_Resp):
        def read(self, *args):
            raise http.client.IncompleteRead(b"partial", 100)
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", lambda *a, **k: Dropped(b""))
    with pytest.raises(_nexus.SteamUnavailable):
        _nexus.steam_title("ws_1")


@pytest.mark.parametrize("payload", [[], {}, {"response": {}},
    {"response": {"publishedfiledetails": []}},
    {"response": {"publishedfiledetails": [{}]}},
    {"response": {"publishedfiledetails": [{"result": 1, "title": 123}]}},
    {"response": {"publishedfiledetails": [{"result": True, "title": "name"}]}},
    {"response": {"publishedfiledetails": [{"result": 1}]}},
])
def test_steam_malformed_answers_cannot_impersonate_a_real_no_title(monkeypatch, payload):
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    with pytest.raises(_nexus.SteamUnavailable):
        _nexus.steam_title("ws_1")


@pytest.mark.parametrize("status", [2, 3, 15, 16, 20, 22, 999])
def test_steam_failure_status_is_unavailable_not_absence(monkeypatch, status):
    payload = {"response": {"publishedfiledetails": [{"result": status}]}}
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    with pytest.raises(_nexus.SteamUnavailable):
        _nexus.steam_title("ws_1")


def test_steam_valid_named_item_keeps_its_title_and_creator(monkeypatch):
    payload = {"response": {"publishedfiledetails": [{"result": 1, "title": "A real title", "creator": "123"}]}}
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    assert _nexus.steam_title("ws_1") == ("A real title", "123")

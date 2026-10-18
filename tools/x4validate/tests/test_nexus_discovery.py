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


# ---------------------------------------------------------------------------
# Issue #13 follow-up (K1, K3-K6): what the user sees, not only what the provider returns.
# ---------------------------------------------------------------------------
import urllib.error

from x4validate import _registry

KEY = "SENTINEL-KEY-0123456789abcdef"

COVERAGE_PARTS = ("names only", "hidden", "Workshop", "none exist")


def _node(i, **over):
    row = {"modId": i, "name": f"Candidate {i}", "version": "1.0", "author": "someone",
           "updatedAt": "2026-10-01T00:00:00Z", "status": "published"}
    row.update(over)
    return row


def _serve(monkeypatch, nodes, total=None, key=KEY):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: key)
    body = {"data": {"mods": {"totalCount": len(nodes) if total is None else total,
                              "nodes": nodes}}}
    monkeypatch.setattr(_nexus, "_post_json", lambda *a: body)


def _registry_file(tmp_path, rows):
    """rows: (mod id, nexus id or None, id_state or None, installed, ignored)."""
    reg = _registry._new_registry()
    for mod_id, nid, state, installed, ignored in rows:
        m = _registry._new_entry(mod_id, True)
        m["auto"]["installed"] = installed
        if nid is not None:
            m["auto"]["nexus_id"] = nid
        if state is not None:
            m["auto"]["id_state"] = state
        m["human"]["ignored"] = ignored
        reg["mods"].append(m)
    path = tmp_path / "modlist.yaml"
    _registry.save_registry(reg, path)
    return path


def _run(capsys, *argv):
    rc = _modlist.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


# ---- K1: a missing or refused key must say what to do ------------------------------------

def _no_key(monkeypatch):
    def raise_auth():
        raise _nexus.NexusAuthError("X4_NEXUS_KEY not set (internal wording)")
    monkeypatch.setattr(_nexus, "nexus_key", raise_auth)
    monkeypatch.setattr(_nexus, "_post_json",
                        lambda *a: pytest.fail("network reached without a key"))


def test_no_key_names_the_setting_to_fix_in_text_and_json(monkeypatch, capsys):
    _no_key(monkeypatch)
    rc, out, _ = _run(capsys, "search", "bail")
    assert rc == 2
    assert "X4_NEXUS_KEY" in out and "x4-paths.env" in out
    rc, out, _ = _run(capsys, "search", "bail", "--json")
    assert rc == 2
    reasons = json.loads(out)["reasons"]
    assert any("X4_NEXUS_KEY" in r and "x4-paths.env" in r for r in reasons), reasons


def test_a_refused_key_gets_the_same_instruction_and_never_the_server_text(monkeypatch, capsys):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: KEY)

    def refuse(*a):
        raise urllib.error.HTTPError("https://x", 401, f"bad key {KEY}", {}, None)
    monkeypatch.setattr(_nexus, "_post_json", refuse)
    rc, out, err = _run(capsys, "search", "bail")
    assert rc == 2 and "X4_NEXUS_KEY" in out
    assert KEY not in out + err


# ---- K3: have / have? / new, from the registry --------------------------------------------

def test_candidates_are_marked_have_guess_or_new_from_active_registry_mods(
        monkeypatch, capsys, tmp_path):
    reg = _registry_file(tmp_path, [
        ("pinned_mod", 1001, "pinned", True, False),
        ("exact_mod", 1002, "exact", True, False),
        ("guess_mod", 1003, "guess", True, False),
        ("gone_mod", 1004, "exact", False, False),      # not installed -> not active
        ("junk_mod", 1005, "exact", True, True),         # ignored -> not active
        ("no_id_mod", None, "unsearched", True, False),  # active, no Nexus id
        ("no_id_mod2", None, "unmatched", True, False),
    ])
    _serve(monkeypatch, [_node(i) for i in range(1001, 1007)])
    rc, out, _ = _run(capsys, "--registry", str(reg), "search", "x", "--json")
    assert rc == 0
    doc = json.loads(out)
    marks = {c["mod_id"]: c["local"] for c in doc["candidates"]}
    assert marks == {1001: "have", 1002: "have", 1003: "have?", 1004: "new",
                     1005: "new", 1006: "new"}
    assert doc["installed_evidence"] == "registry"
    assert doc["registry"]["active_without_nexus_id"] == 2
    assert doc["registry"]["active_mods"] == 5


def test_text_output_shows_the_marks_and_the_unidentified_count(monkeypatch, capsys, tmp_path):
    reg = _registry_file(tmp_path, [("m1", 1001, "pinned", True, False),
                                    ("m2", 1002, "guess", True, False),
                                    ("m3", None, "unsearched", True, False)])
    _serve(monkeypatch, [_node(1001), _node(1002), _node(1003)])
    rc, out, _ = _run(capsys, "--registry", str(reg), "search", "x")
    assert rc == 0
    line = {i: next(ln for ln in out.splitlines() if ln.lstrip().startswith(f"[{i}]"))
            for i in (1001, 1002, 1003)}
    assert "[have" in line[1001] and "m1" in line[1001]
    assert "[have?" in line[1002] and "[have]" not in line[1002]
    assert "[new]" in line[1003]
    assert "1 active mod(s) lack a Nexus id" in out
    assert "installation and local identity were not checked" not in out


def test_an_unconfigured_registry_still_prints_results_and_says_marking_is_unavailable(
        monkeypatch, capsys):
    monkeypatch.setattr(_registry, "DEFAULT_REGISTRY", None)
    _serve(monkeypatch, [_node(1001)])
    rc, out, _ = _run(capsys, "search", "x")
    assert rc == 0 and "[1001]" in out
    assert "marking is unavailable" in out.lower() and "[new]" not in out and "[have" not in out
    rc, out, _ = _run(capsys, "search", "x", "--json")
    doc = json.loads(out)
    assert doc["installed_evidence"] == "not_checked"
    assert doc["candidates"][0]["local"] is None
    assert doc["registry"]["status"] == "unavailable"


def test_a_missing_registry_file_is_unavailable_never_everything_is_new(
        monkeypatch, capsys, tmp_path):
    _serve(monkeypatch, [_node(1001)])
    rc, out, _ = _run(capsys, "--registry", str(tmp_path / "nope.yaml"), "search", "x", "--json")
    doc = json.loads(out)
    assert rc == 0 and doc["candidates"][0]["local"] is None
    assert doc["registry"]["status"] == "unavailable"


def test_search_never_writes_the_registry(monkeypatch, capsys, tmp_path):
    reg = _registry_file(tmp_path, [("m1", 1001, "pinned", True, False)])
    before = reg.read_bytes()
    _serve(monkeypatch, [_node(1001)])
    _run(capsys, "--registry", str(reg), "search", "x")
    assert reg.read_bytes() == before


# ---- K4: the coverage statement ------------------------------------------------------------

@pytest.mark.parametrize("nodes", [[_node(1)], []])
def test_coverage_footer_follows_every_result_in_text_and_json(monkeypatch, capsys, nodes):
    monkeypatch.setattr(_registry, "DEFAULT_REGISTRY", None)
    _serve(monkeypatch, nodes)
    rc, out, _ = _run(capsys, "search", "x")
    assert rc == 0
    for part in COVERAGE_PARTS:
        assert part.lower() in out.lower(), part
    rc, out, _ = _run(capsys, "search", "x", "--json")
    cov = " ".join(json.loads(out)["coverage"])
    for part in COVERAGE_PARTS:
        assert part.lower() in cov.lower(), part


def test_coverage_footer_also_follows_a_non_answer(monkeypatch, capsys):
    _no_key(monkeypatch)
    rc, out, _ = _run(capsys, "search", "x", "--json")
    assert rc == 2 and json.loads(out)["coverage"]


# ---- K5: the malformed count is printed ---------------------------------------------------

def test_text_output_counts_the_excluded_malformed_candidates(monkeypatch, capsys):
    monkeypatch.setattr(_registry, "DEFAULT_REGISTRY", None)
    _serve(monkeypatch, [_node(1), {"name": "no id"}, _node(1), {"modId": "x", "name": "y"}],
           total=4)
    rc, out, _ = _run(capsys, "search", "x")
    assert rc == 0
    assert "3 malformed or duplicate candidate(s) were excluded" in out


# ---- K6: no transport failure may carry the key to any stream ------------------------------

def _raise(exc):
    def post(*a):
        raise exc
    return post


def _answer(body):
    return lambda *a: body


@pytest.mark.parametrize("make_post", [
    lambda: _raise(urllib.error.HTTPError("https://x", 500, f"boom {KEY}", {"apikey": KEY}, None)),
    lambda: _raise(urllib.error.HTTPError("https://x", 403, f"denied {KEY}", {}, None)),
    lambda: _raise(urllib.error.HTTPError("https://x", 429, f"slow {KEY}", {}, None)),
    lambda: _raise(urllib.error.URLError(f"refused for apikey={KEY}")),
    lambda: _raise(TimeoutError(f"timed out sending {KEY}")),
    lambda: _answer({"errors": [{"message": f"invalid apikey {KEY}"}], "data": None}),
    lambda: _answer({"errors": [{"message": f"echo {KEY}"}],
                     "data": {"mods": {"totalCount": 1, "nodes": [_node(1)]}}}),
    lambda: _answer({"errors": [{"message": KEY}], "data": {"mods": {"totalCount": 1, "nodes": [
        _node(1), {"modId": 2, "name": "x", "extra": KEY}]}}}),
], ids=["http500", "http403", "http429", "urlerror", "timeout", "graphql-errors",
        "graphql-errors-with-rows", "errors-and-rows"])
@pytest.mark.parametrize("as_json", [False, True])
def test_no_stream_carries_the_key_when_transport_or_graphql_fails(
        monkeypatch, capsys, make_post, as_json):
    monkeypatch.setattr(_registry, "DEFAULT_REGISTRY", None)
    monkeypatch.setattr(_nexus, "nexus_key", lambda: KEY)
    monkeypatch.setattr(_nexus, "_post_json", make_post())
    argv = ["search", "bail"] + (["--json"] if as_json else [])
    rc, out, err = _run(capsys, *argv)
    assert rc in (0, 2)
    assert KEY.lower() not in (out + err).lower()
    if as_json:
        json.loads(out)  # still one parseable document


def test_a_blank_search_text_is_a_refusal_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(_nexus, "nexus_key", lambda: KEY)
    monkeypatch.setattr(_nexus, "_post_json", lambda *a: pytest.fail("network reached"))
    rc, out, err = _run(capsys, "search", "   ")
    assert rc == 2 and "Traceback" not in out + err

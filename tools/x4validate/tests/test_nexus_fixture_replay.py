"""`gates/nexus_fixture.py` replay -- the update verdict is asserted, not only exercised.

Release review 2026-09-26: the committed fixture carried no `files` key, so every
files.json reply the replay served was DERIVED from the REST record (one synthetic MAIN
file), and nothing asserted the update verdict `x4modlist refresh` computes from it. A
refresh that got the verdict wrong -- judged an OPTIONAL upload as the newest release,
say -- replayed green.
"""
from __future__ import annotations

import json

from conftest import import_gate

nf = import_gate("nexus_fixture")


def _fixture() -> dict:
    return json.loads(nf.FIXTURE.read_text(encoding="utf-8"))


def test_the_committed_fixture_carries_a_recorded_file_list_for_every_mod():
    fx = _fixture()
    files = fx.get("files") or {}
    assert set(files) == set(fx["rest"]), "every REST mod needs its files.json record"
    for mod_id, body in files.items():
        cats = {f["category_name"] for f in body["files"]}
        assert "MAIN" in cats, f"{mod_id}: no MAIN file -- the verdict has nothing to judge"
        for f in body["files"]:
            # The fields `_nexus.fetch_file_listing` reads, in the live API's shapes.
            assert isinstance(f["file_id"], int)
            assert f["uploaded_time"][4] == "-" and "T" in f["uploaded_time"]


def test_the_committed_fixture_is_key_free_and_anonymized():
    blob = nf.FIXTURE.read_text(encoding="utf-8")
    fx = json.loads(blob)
    for body in (fx.get("files") or {}).values():
        for f in body["files"]:
            assert f["name"].startswith("Example File "), f["name"]
    assert "apikey" not in blob.lower()


def test_replay_passes_and_serves_no_derived_file_list(capsys):
    assert nf.replay() == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "DERIVED" not in out
    assert "update verdict" in out


def test_replay_goes_RED_when_the_update_verdict_is_wrong(monkeypatch, capsys):
    """The planted defect: refresh judges every row 'none'. It used to replay green."""
    monkeypatch.setattr(nf._modlist, "_update_verdict",
                        lambda installed, upstream, trusted: ("none", "planted"))
    assert nf.replay() == 1, capsys.readouterr().out


def test_replay_goes_RED_when_a_newer_OPTIONAL_file_is_taken_for_the_release(
        monkeypatch, capsys):
    """The planted defect: the newest upload of ANY category is judged the release."""
    real = nf._modlist._upstream_newest

    def any_category(nid, fmeta):
        files = nf._nexus.fetch_files(nid)
        newest = max((f for f in files if f.uploaded), key=lambda f: f.uploaded)
        return newest.uploaded, f"MAIN file {newest.file_id} {newest.name!r} v{newest.version}"
    monkeypatch.setattr(nf._modlist, "_upstream_newest", any_category)
    assert real is not any_category
    assert nf.replay() == 1, capsys.readouterr().out


# ---------------------------------------------------------------------------
# Issue #13 (K7b): the replay must route `discover` by its GraphQL VARIABLES.
# ---------------------------------------------------------------------------

def test_the_committed_fixture_carries_every_discover_reply_key_free_and_anonymized():
    fx = _fixture()
    block = fx.get("discover") or {}
    assert set(block) == set(nf.DISCOVER_CASES)
    blob = json.dumps(block)
    assert "apikey" not in blob.lower() and "key" not in blob.lower().replace("monkey", "")
    for row in block["complete"]["data"]["mods"]["nodes"]:
        assert row["name"].startswith("Example Mod ") and row["author"].startswith("example_author_")
        assert set(row) == {"modId", "name", "version", "author", "updatedAt", "status"}
    assert "errors" in block["errors"] and block["errors"]["data"] is None


def test_replay_goes_RED_when_discover_is_routed_by_query_text_not_variables(
        monkeypatch, capsys):
    """The planted defect: the old routing, which looked for the term in the query text.
    `discover` sends a constant query, so every call got the same reply and replayed green."""
    def by_query_text_only(self, url, body, headers):
        self.calls.append(url)
        query = body.get("query", "")
        for name in ("nomatch", "malformed", "example"):
            if f'"{name}"' in query:
                return self.fx["graphql"][name]
        return self.fx["graphql"]["example"]
    monkeypatch.setattr(nf._Replay, "post_json", by_query_text_only)
    assert nf.replay() == 1
    out = capsys.readouterr().out
    assert "FAIL  discover complete" in out and "FAIL  discover errors" in out


def test_replay_goes_RED_when_the_parser_stops_telling_a_capped_page_from_a_complete_one(
        monkeypatch, capsys):
    """A second planted defect, on the parser side: a capped page is reported complete."""
    real = nf._nexus.discover

    def forgiving(name, count=15):
        out = real(name, count)
        if out["state"] == "partial":
            out["state"] = "complete"
        return out
    monkeypatch.setattr(nf._nexus, "discover", forgiving)
    assert nf.replay() == 1, capsys.readouterr().out


def test_an_unrecorded_discover_term_is_a_hard_failure_not_a_default_reply():
    rp = nf._Replay(_fixture())
    body = {"query": "q", "variables": {"f": {"nameStemmed": [{"value": "never recorded"}]}}}
    try:
        rp.post_json("https://x", body, {})
    except AssertionError as exc:
        assert "unrecorded discover term" in str(exc)
    else:
        raise AssertionError("an unrecorded term was served a reply")


def test_record_writes_a_discover_block_the_replay_accepts(monkeypatch, tmp_path, capsys):
    """`--record` is the only way live shapes enter the fixture and it cannot run in CI, so
    drive it with a stubbed transport and prove its OUTPUT replays green."""
    live_nodes = [{"modId": 5000 + i, "name": f"Real Name {i}", "version": f"{i}.0",
                   "author": f"Real Author {i}", "updatedAt": "2026-09-0%dT01:02:03Z" % i,
                   "status": "published"} for i in (1, 2, 3)]

    def get_json(url, headers):
        if url.endswith("/files.json"):
            return {"files": [{"file_id": 1, "name": "Real File", "version": "1.0",
                               "uploaded_time": "2026-01-02T03:04:05.000Z",
                               "category_name": "MAIN"}]}
        return {"name": "Real", "version": "1.0", "updated_timestamp": 1722087970,
                "status": "published", "author": "someone", "mod_id": 1}

    def post_json(url, body, headers):
        if body.get("variables") is not None:
            return {"data": {"mods": {"totalCount": 40, "nodes": live_nodes}}}
        return {"data": {"mods": {"nodes": [{"modId": 5001, "name": "Real Name 1"}]}}}

    monkeypatch.setattr(nf, "FIXTURE", tmp_path / "nexus.json")
    monkeypatch.setattr(nf, "_registry_ids", lambda limit=3: [11, 12, 13])
    monkeypatch.setattr(nf._nexus, "nexus_key", lambda: "record-key-sentinel")
    monkeypatch.setattr(nf._nexus, "_get_json", get_json)
    monkeypatch.setattr(nf._nexus, "_post_json", post_json)
    assert nf.record() == 0, capsys.readouterr().out
    blob = (tmp_path / "nexus.json").read_text(encoding="utf-8")
    assert "record-key-sentinel" not in blob
    assert "Real Name" not in blob and "Real Author" not in blob
    block = json.loads(blob)["discover"]
    assert set(block) == set(nf.DISCOVER_CASES)
    assert [r["version"] for r in block["complete"]["data"]["mods"]["nodes"]] == ["1.0", "2.0", "3.0"]
    assert nf.replay() == 0, capsys.readouterr().out

#!/usr/bin/env python
r"""Offline replay gate for the Nexus layer (`x4modlist refresh`).

`refresh` was the last untested command, and for a structural reason: it is the
only code path that talks to the network. Testing it live burns rate budget,
needs a personal API key, and fails for reasons that have nothing to do with our
code (Cloudflare, a mod going hidden). So it never got tested at all — which is
the worst outcome, because the parsing and classification around it are exactly
the kind of code that rots silently when an API adds or renames a field.

Two modes:

  --record   Hit the REAL API once, using mod ids DISCOVERED from the local
             registry, and assert our parsers handle the live responses. Then
             write an ANONYMIZED fixture: field names, types and status values
             are preserved (that is what the parsers actually read), while ids,
             names and authors become neutral placeholders. So the committed
             artifact leaks neither the API key nor which mods are installed,
             and it was still verified against reality at the moment it was made.

  (default)  Replay the fixture with no network at all (urlopen is stubbed to
             refuse and count), exercising fetch_mod, discover, search_mods,
             the classifier, the failure paths
             (HTTP 404, empty search, malformed node), and the UPDATE VERDICT
             `refresh` derives from files.json -- asserted both ways ("available"
             and "none"), judged against the newest MAIN file even when a newer
             OPTIONAL upload is listed.

The key is read only via `_nexus.nexus_key()` and is NEVER written to the
fixture, printed, or logged.

Run:  uv run python gates/nexus_fixture.py [--record]
Exit: 0 all replays behave as recorded, 1 any mismatch, 2 no fixture.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4validate import _harness, _modlist, _nexus, _registry  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nexus.json"
RECORD = "--record" in sys.argv

#: An id that must not exist, for the 404 path. Far above the live id space.
MISSING_ID = 99999999
#: How many AVAILABLE mods --record records; it scans up to RECORD_SCAN registry ids to find them.
RECORD_MODS, RECORD_SCAN = 3, 12


#: The terms the replay sends through `discover`, and the reply each one is routed to.
DISCOVER_CASES = ("complete", "partial", "malformed", "errors", "zero")


def discover_term(case: str) -> str:
    return f"discover {case}"


def _page(total, nodes):
    return {"data": {"mods": {"totalCount": total, "nodes": nodes}}}


def discover_bodies(rows: list[dict]) -> dict:
    """The five replies the discover contract must tell apart, from ONE row list in the
    recorded shape (`modId name version author updatedAt status`, `totalCount`).

    Only `complete` is a real reply when recorded; the other four are DERIVED from it with
    the same field names, because a live account cannot be made to return a capped page, a
    damaged node or a GraphQL error on demand. The fixture's note says which it holds.
    """
    n = len(rows)
    return {
        "complete": _page(n, rows),
        "partial": _page(n + 7, rows),
        "malformed": _page(n + 2, rows + [{"name": "no id here"}, dict(rows[0])]),
        "errors": {"errors": [{"message": "Example GraphQL error"}], "data": None},
        "zero": _page(0, []),
    }


def _anonymize_discover_rows(nodes: list[dict]) -> list[dict]:
    """Keep the fields discover reads, with their live values; ids, names, authors neutral."""
    return [{"modId": 1000 + i, "name": f"Example Mod {i}",
             "version": n.get("version"), "author": f"example_author_{i}",
             "updatedAt": n.get("updatedAt"), "status": n.get("status")}
            for i, n in enumerate(nodes, 1)]


# ---------------------------------------------------------------- recording --

def _registry_ids(limit: int = 3) -> list[int]:
    """Nexus ids discovered from the local registry (never hardcoded)."""
    reg = _registry.load_registry()
    ids: list[int] = []
    for entry in reg.get("mods", []):
        auto = dict(entry).get("auto") or {}
        nid = auto.get("nexus_id")
        if nid and int(nid) not in ids:
            ids.append(int(nid))
        if len(ids) >= limit:
            break
    return ids


def _anonymize_rest(raw: dict, ordinal: int) -> dict:
    """Keep every field the parser reads; replace identifying VALUES.

    `updated_timestamp` is kept as a real epoch because `fetch_mod` converts it
    and a placeholder would not exercise that conversion. `name` is kept ABSENT when the
    live reply had none: Nexus omits it for a removed or hidden mod (MEASURED 2026-10-20),
    and a placeholder there would record a shape the API never sends.
    """
    out = {
        "version": str(raw.get("version", "")),
        "updated_timestamp": int(raw.get("updated_timestamp") or 0),
        "status": raw.get("status", ""),
        "author": f"example_author_{ordinal}",
        "mod_id": 1000 + ordinal,
    }
    if "name" in raw:
        out["name"] = f"Example Mod {ordinal}"
    if "available" in raw:
        out["available"] = bool(raw["available"])
    return out


def record() -> int:
    ids = _registry_ids(RECORD_SCAN)
    if not ids:
        print("no nexus ids in the local registry — cannot record", file=sys.stderr)
        return 2
    try:
        _nexus.nexus_key()
    except _nexus.NexusError as exc:
        print(f"cannot record: {exc}", file=sys.stderr)
        return 2

    print(f"recording against the live API: up to {RECORD_MODS} available mod(s) among "
          f"{len(ids)} discovered id(s)")
    fixture: dict = {"recorded": date.today().isoformat(),
                     "note": "anonymized; shapes verified against the live API when recorded",
                     "rest": {}, "graphql": {}, "steam": {}, "missing_id": MISSING_ID}

    headers = {"apikey": _nexus.nexus_key(), **_nexus._APP}
    i = 0
    for nid in ids:
        if i >= RECORD_MODS:
            break
        raw = _nexus._get_json(f"{_nexus.NEXUS_REST}/{nid}.json", headers)
        for field in ("version", "status", "updated_timestamp"):
            assert field in raw, f"live REST response lost field {field!r}"
        # A removed/hidden mod (`available: false`) is SKIPPED: Nexus sends it without
        # `name` (MEASURED 2026-10-20, one removed and one hidden mod), and a removed mod's
        # files.json answered 403 (MEASURED; hidden INFERRED), so it cannot feed the
        # update-verdict replay. The registry order put two of them first.
        if raw.get("available") is False:
            print(f"  rest  id={nid} -> status={raw.get('status')!r}, unavailable: skipped")
            continue
        assert "name" in raw, f"live REST response lost field 'name' (available mod {nid})"
        i += 1
        # Prove the live shape still parses BEFORE we anonymize it.
        meta = _nexus.fetch_mod(nid)
        assert meta.nexus_id == nid and meta.status, f"live parse failed for {nid}"
        fixture["rest"][str(1000 + i)] = _anonymize_rest(raw, i)
        # The file list feeds the update verdict (RG-3). Names anonymized; the fields the
        # parser reads (version, upload time, category) kept as recorded.
        rawf = _nexus._get_json(f"{_nexus.NEXUS_REST}/{nid}/files.json", headers)
        fixture.setdefault("files", {})[str(1000 + i)] = {"files": [
            {"file_id": j, "name": f"Example File {j}", "version": str(f.get("version", "")),
             "uploaded_time": f.get("uploaded_time", ""),
             "category_name": f.get("category_name", "")}
            for j, f in enumerate((rawf or {}).get("files") or [], 1)]}
        print(f"  rest  id={nid} -> status={meta.status!r} version={meta.version!r} (anonymized)")

    # The replay's update verdicts need two recorded mods; refuse rather than write a
    # fixture that cannot exercise them.
    if i < 2:
        print(f"cannot record: only {i} available mod(s) among the first {len(ids)} "
              "registry ids", file=sys.stderr)
        return 2

    # A real search, recorded under a neutral term.
    hits = _nexus.search_mods("station", 3)
    assert isinstance(hits, list), "search_mods no longer returns a list"
    fixture["graphql"]["example"] = {
        "data": {"mods": {"nodes": [
            {"modId": 1000 + i, "name": f"Example Mod {i}"} for i in range(1, len(hits) + 1)
        ]}}}
    fixture["graphql"]["nomatch"] = {"data": {"mods": {"nodes": []}}}
    # The discovery query (`x4modlist search`): one live reply, parsed by OUR parser before it
    # is anonymized, so the field names it asks for are checked against the schema here.
    provider = _harness.module("discovery")
    raw = _nexus._mapped("discover", _nexus._post_json, _nexus.NEXUS_GQL,
                         provider.request("x4foundations", "station", 3), headers)
    live = provider.parse(raw, "x4foundations", "station", 3)
    assert live["state"] in ("complete", "partial") and live["candidates"],         f"live discover reply was not usable: {live['state']} {live['reasons']}"
    for c in live["candidates"]:
        assert not c["unavailable_fields"], f"live discover lost fields {c['unavailable_fields']}"
    fixture["discover"] = discover_bodies(
        _anonymize_discover_rows(raw["data"]["mods"]["nodes"]))
    print(f"  graphql discover -> {live['state']}, {len(live['candidates'])} live row(s) "
          "(anonymized; partial/malformed/errors/zero derived)")
    # A node the parser must survive (missing modId) — the silent-ok path.
    fixture["graphql"]["malformed"] = {
        "data": {"mods": {"nodes": [{"name": "no id here"}, {"modId": 1001, "name": "ok"}]}}}
    print(f"  graphql search -> {len(hits)} live hit(s) (anonymized to "
          f"{len(fixture['graphql']['example']['data']['mods']['nodes'])} node(s))")

    fixture["steam"]["1000000001"] = {
        "response": {"publishedfiledetails": [
            {"title": "Example Workshop Item", "creator": "76500000000000001", "result": 1}]}}
    fixture["steam"]["missing"] = {"response": {"publishedfiledetails": [{"result": 9}]}}

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    blob = FIXTURE.read_text(encoding="utf-8")
    key = _nexus.nexus_key()
    assert key not in blob, "REFUSING to keep a fixture containing the API key"
    print(f"\nwrote {FIXTURE} ({len(blob)} bytes), key-free")
    return 0


# ------------------------------------------------------------------ replay --

class _Replay:
    """Serves recorded bodies; anything unrecorded is a hard failure, never a
    silent fallthrough to the network."""

    def __init__(self, fx: dict):
        self.fx = fx
        self.calls: list[str] = []
        #: Every attempt to reach the real network while the replay runs. MEASURED by
        #: `urlopen` below, so "network calls attempted: 0" is a count, not a claim.
        self.network: list[str] = []

    def urlopen(self, req, *args, **kwargs):
        """Stands in for `urllib.request.urlopen` during the replay: records and RAISES,
        so a path that bypasses `_get_json`/`_post_json` can neither reach the network
        nor pass unseen (a caller that swallows the error is still counted)."""
        self.network.append(getattr(req, "full_url", str(req)))
        raise AssertionError(f"network call attempted during replay: {self.network[-1]}")

    def get_json(self, url: str, headers: dict) -> dict:
        self.calls.append(url)
        if url.endswith("/files.json"):
            return self._files(url)
        mod_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        if mod_id == str(self.fx["missing_id"]):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        body = self.fx["rest"].get(mod_id)
        if body is None:
            raise AssertionError(f"unrecorded REST call: {url}")
        return body

    #: files.json replies DERIVED from the recorded REST body, served only when the
    #: fixture predates files recording. Counted and printed, never silent: a derived
    #: reply exercises the parser and the update verdict, not the live file-list shape.
    derived_files = 0

    def _files(self, url: str) -> dict:
        mod_id = url.rsplit("/", 2)[-2]
        if mod_id == str(self.fx["missing_id"]):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        recorded = (self.fx.get("files") or {}).get(mod_id)
        if recorded is not None:
            return recorded
        body = self.fx["rest"].get(mod_id)
        if body is None:
            raise AssertionError(f"unrecorded REST call: {url}")
        self.derived_files += 1
        ts = int(body.get("updated_timestamp") or 0)
        uploaded = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""
        return {"files": [{"file_id": 1, "name": "main", "version": body.get("version", ""),
                           "uploaded_time": uploaded, "category_name": "MAIN"}]}

    @staticmethod
    def _variable_term(variables: dict) -> str:
        """The search term of a discovery request: `f.nameStemmed[0].value` (or `f.name`)."""
        f = variables.get("f") or {}
        for field in ("nameStemmed", "name"):
            terms = f.get(field)
            if terms:
                return str(terms[0].get("value", ""))
        raise AssertionError("a discovery request carried no search term")

    def post_json(self, url: str, body: dict, headers: dict) -> dict:
        self.calls.append(url)
        variables = body.get("variables")
        if variables is not None:
            # `discover` puts the term in GraphQL VARIABLES, not in the query text. An
            # unrecorded term is a hard failure, never a quiet default reply.
            term = self._variable_term(variables)
            case = term.removeprefix("discover ") if term.startswith("discover ") else None
            block = (self.fx.get("discover") or {}).get(case)
            if block is None:
                raise AssertionError(f"unrecorded discover term: {term!r}")
            return block
        query = body.get("query", "")
        for name in ("nomatch", "malformed", "example"):
            if f'"{name}"' in query:
                return self.fx["graphql"][name]
        # Like the discover branch: a query naming no recorded reply is a hard failure,
        # never a quiet `example` default that lets a changed request replay green.
        raise AssertionError(f"unrecorded GraphQL query: {query[:120]!r}")


def _check(label: str, got, want, failures: list[str]) -> None:
    ok = got == want
    print(f"  {'  ok ' if ok else ' FAIL'}  {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r}")


#: Installed-manifest dates for the two update-verdict rows (see `_sandbox_registry`).
#: OLD predates every upload in the fixture by years; CURRENT is set to the second mod's
#: newest MAIN upload day by `_sandbox_registry`, so its verdict is "none".
_INSTALLED_OLD = "2000-01-01"


def _newest_main(fx: dict, mod_id: str) -> str | None:
    """The upload DATE of the newest MAIN file the fixture lists for *mod_id* --
    computed from the fixture, independently of `_modlist._upstream_newest`."""
    files = ((fx.get("files") or {}).get(mod_id) or {}).get("files") or []
    days = [str(f.get("uploaded_time", ""))[:10] for f in files
            if str(f.get("category_name", "")).upper() == "MAIN" and f.get("uploaded_time")]
    if days:
        return max(days)
    ts = int(fx["rest"][mod_id].get("updated_timestamp") or 0)   # a derived reply
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else None


def _sandbox_registry(tmp: Path, fx: dict) -> Path:
    """A throwaway registry: one seeded mod, one whose id 404s, and two TRUSTED rows
    whose update verdicts are known in advance -- one installed long before its newest
    MAIN upload ("available"), one dated that very upload day ("none").

    Never the real registry: `cmd_refresh` WRITES (registry + dashboard), and a
    gate must not mutate the user's triage state to test itself.
    """
    good, second = sorted(fx["rest"])[0], sorted(fx["rest"])[1]
    reg = {
        "meta": {"generated": "gate"},
        "mods": [
            {"id": "gate_good", "auto": {"installed": True, "nexus_id": int(good),
                                         "id_state": "exact",
                                         "installed_date": _INSTALLED_OLD},
             "human": {"custom_edited": False}},
            {"id": "gate_current", "auto": {"installed": True, "nexus_id": int(second),
                                            "id_state": "exact",
                                            "installed_date": _newest_main(fx, second)},
             "human": {"custom_edited": False}},
            {"id": "gate_404", "auto": {"installed": True, "nexus_id": fx["missing_id"]},
             "human": {"custom_edited": False}},
        ],
    }
    path = tmp / "modlist.yaml"
    _registry.save_registry(reg, path)
    return path


def _check_refresh_end_to_end(fx: dict, failures: list[str]) -> None:
    """Drive `x4modlist refresh` itself, not just the API helpers underneath it.

    Added after noticing the gate claimed to make `refresh` testable while never
    invoking it. These branches are only reachable through the command: the
    once-per-day TTL, `--force`, the 404 -> classification="error" path, and the
    registry/dashboard writes.
    """
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="x4gate_refresh_"))
    try:
        reg_path = _sandbox_registry(tmp, fx)
        # --registry is a TOP-LEVEL argument, before the subcommand.
        argv = ["--registry", str(reg_path), "refresh", "--seeded"]

        rc = _modlist.main(argv)
        reg = _registry.load_registry(reg_path)
        by_id = {m["id"]: m["auto"] for m in reg["mods"]}
        ok = rc == 0 and by_id["gate_good"].get("status") == \
            fx["rest"][sorted(fx["rest"])[0]]["status"]
        _check("refresh populates a seeded mod", ok, True, failures)
        _check("refresh marks an unreachable id as error",
               by_id["gate_404"].get("classification"), "error", failures)
        _check("refresh recorded the check date",
               bool(by_id["gate_good"].get("checked_at")), True, failures)
        # The UPDATE VERDICT, the one thing refresh computes from files.json (RG-3):
        # judged against the newest MAIN upload -- never a newer OPTIONAL one -- and
        # both directions, so a verdict stuck on one answer cannot replay green.
        good, second = sorted(fx["rest"])[0], sorted(fx["rest"])[1]
        _check("update verdict: installed long before the newest MAIN upload",
               by_id["gate_good"].get("update"), "available", failures)
        _check("update verdict judged against the newest MAIN file (not a newer OPTIONAL)",
               by_id["gate_good"].get("upstream_newest_uploaded"), _newest_main(fx, good),
               failures)
        _check("update verdict: installed on the newest MAIN upload day",
               by_id["gate_current"].get("update"), "none", failures)

        # TTL: a second run must fetch nothing, --force must fetch again.
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _modlist.main(argv)
        _check("second run is a TTL no-op", "0 fetched" in buf.getvalue(), True, failures)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _modlist.main(argv + ["--force"])
        _check("--force re-fetches", "0 fetched" not in buf.getvalue(), True, failures)

        dash = _registry.write_dashboard.__name__ and (reg_path.parent / "WORKLIST.md")
        _check("dashboard written beside the registry", dash.is_file(), True, failures)
    except Exception as exc:                      # a crash here IS the finding
        _check(f"refresh end-to-end raised {type(exc).__name__}", str(exc), "", failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_discover(fx: dict, failures: list[str]) -> None:
    """Drive `discover` and `x4modlist search` through the replay -- not only search_mods.

    `discover` sends its term in GraphQL `variables`, not in the query text the old routing
    searched, so every discover call silently got the same reply and a parser that stopped
    telling complete / partial / malformed / errors apart replayed green.
    """
    block = fx.get("discover")
    if not block or set(block) != set(DISCOVER_CASES):
        failures.append("the fixture has no complete `discover` block -- run --record once")
        print("   FAIL  fixture lacks the discover block")
        return
    rows = block["complete"]["data"]["mods"]["nodes"]
    ids = [r["modId"] for r in rows]
    if len(ids) < 3:
        # The search check below needs rows on BOTH sides of the registry (some "have",
        # some "new"); with fewer rows it would index past the reply or go one-sided.
        failures.append(f"the fixture's discover reply has {len(ids)} row(s), fewer than 3 "
                        "-- the installed/new check cannot be two-sided; re-run --record")
        print(f"   FAIL  discover reply has fewer than 3 rows ({len(ids)})")
        return
    got = {c: _nexus.discover(discover_term(c), 5) for c in DISCOVER_CASES}
    _check("discover complete: state", got["complete"]["state"], "complete", failures)
    _check("discover complete: ids in order",
           [c["mod_id"] for c in got["complete"]["candidates"]], ids, failures)
    _check("discover complete: version/date/status all present",
           [c["unavailable_fields"] for c in got["complete"]["candidates"]],
           [[] for _ in ids], failures)
    _check("discover partial: state", got["partial"]["state"], "partial", failures)
    _check("discover partial: total exceeds returned",
           (got["partial"]["total"], got["partial"]["returned"]),
           (len(ids) + 7, len(ids)), failures)
    _check("discover malformed: usable rows kept, damaged ones counted",
           (got["malformed"]["state"], got["malformed"]["returned"],
            got["malformed"]["malformed"]), ("partial", len(ids), 2), failures)
    _check("discover errors: a non-answer with no candidates",
           (got["errors"]["state"], got["errors"]["candidates"]), ("unavailable", []),
           failures)
    _check("discover zero: an answer", (got["zero"]["state"], got["zero"]["total"]),
           ("zero", 0), failures)

    # The command itself, against a throwaway registry (search never writes it).
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="x4gate_search_"))
    try:
        reg_path = _sandbox_registry(tmp, fx)

        def run(case: str):
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = _modlist.main(["--registry", str(reg_path), "search",
                                    discover_term(case), "--json"])
            return rc, buf.getvalue(), err.getvalue()

        # What "have" means, derived from the sandbox registry itself rather than from
        # positions in the reply: a candidate is "have" iff its id is a registry nexus id.
        registered = {int(m["auto"]["nexus_id"]) for m in
                      _registry.load_registry(reg_path)["mods"]}
        want = {i: ("have" if i in registered else "new") for i in ids}
        if set(want.values()) != {"have", "new"}:
            failures.append(f"search check is one-sided ({sorted(set(want.values()))}): the "
                            "fixture's discover ids and the sandbox registry do not overlap "
                            "partially -- re-run --record")
        rc, out, _err = run("complete")
        doc = json.loads(out)
        _check("search complete: rc", rc, 0, failures)
        _check("search marks installed ids from the registry",
               {c["mod_id"]: c["local"] for c in doc["candidates"]}, want, failures)
        _check("search carries the coverage statement", bool(doc["coverage"]), True, failures)
        rc, out, err = run("errors")
        _check("search errors: rc 2 and no key in the output (stdout or stderr)",
               (rc, "fixture-key-not-a-real-credential" in out + err), (2, False), failures)
        rc, out, _err = run("zero")
        _check("search zero: rc 0", (rc, json.loads(out)["state"]), (0, "zero"), failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def replay() -> int:
    if not FIXTURE.is_file():
        print(f"no fixture at {FIXTURE} — run with --record once (needs X4_NEXUS_KEY)",
              file=sys.stderr)
        return 2
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rp = _Replay(fx)
    failures: list[str] = []

    orig_get, orig_post, orig_key = _nexus._get_json, _nexus._post_json, _nexus.nexus_key
    orig_urlopen = urllib.request.urlopen
    _nexus._get_json, _nexus._post_json = rp.get_json, rp.post_json
    urllib.request.urlopen = rp.urlopen
    _nexus.nexus_key = lambda: "fixture-key-not-a-real-credential"
    try:
        print("=" * 88)
        print(f"NEXUS FIXTURE REPLAY — recorded {fx.get('recorded')}, no network")
        print("=" * 88)

        first = sorted(fx["rest"])[0]
        meta = _nexus.fetch_mod(int(first))
        _check("fetch_mod .nexus_id", meta.nexus_id, int(first), failures)
        _check("fetch_mod .status", meta.status, fx["rest"][first]["status"], failures)
        _check("fetch_mod .version", meta.version,
               str(fx["rest"][first]["version"]), failures)
        # updated_timestamp -> YYYY-MM-DD is real conversion logic, not a passthrough
        ok_date = len(meta.updated) == 10 and meta.updated[4] == "-"
        _check("fetch_mod .updated is YYYY-MM-DD", ok_date, True, failures)

        # 404 must become NexusError, not an HTTPError escaping to the caller
        try:
            _nexus.fetch_mod(fx["missing_id"])
            failures.append("a 404 did not raise NexusError")
            print("   FAIL  404 -> NexusError: no exception raised")
        except _nexus.NexusError:
            print("    ok   404 -> NexusError")
        except Exception as exc:
            failures.append(f"404 raised {type(exc).__name__}, not NexusError")
            print(f"   FAIL  404 -> {type(exc).__name__}")

        _check("search_mods returns pairs", _nexus.search_mods("example", 3),
               [(n["modId"], n["name"])
                for n in fx["graphql"]["example"]["data"]["mods"]["nodes"]], failures)
        _check("search_mods empty result", _nexus.search_mods("nomatch"), [], failures)
        # A malformed page cannot be used as a complete identity search. The
        # richer discovery command can retain good candidates labelled partial.
        try:
            _nexus.search_mods("malformed")
            failures.append("malformed search page was accepted for identity resolution")
        except _nexus.NexusError:
            print("    ok   malformed search page -> explicit non-answer")

        # classification is what refresh actually DOES with the metadata
        today = date(2026, 8, 9)
        label, _why = _modlist._classify(meta, today)
        print(f"    ok   _classify -> {label!r}")
        if not label:
            failures.append("_classify returned an empty label")

        _check_discover(fx, failures)
        _check_refresh_end_to_end(fx, failures)
        print(f"\n  recorded calls served: {len(rp.calls)}")
        _check("network calls attempted", len(rp.network), 0, failures)
        if rp.derived_files:
            print(f"  NOTE: {rp.derived_files} files.json repl(y/ies) DERIVED from the REST "
                  "record -- this fixture predates file-list recording; re-run --record")
    finally:
        _nexus._get_json, _nexus._post_json = orig_get, orig_post
        _nexus.nexus_key = orig_key
        urllib.request.urlopen = orig_urlopen

    print("\n" + "=" * 88)
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Nexus layer replays exactly as recorded — refresh is testable offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(record() if RECORD else replay())

"""`x4modlist refresh` says which installed mods HAVE AN UPDATE (AUDIT-2026-09-24 RG-3).

Docs promised update detection and nothing compared installed against upstream. The rule,
decided by the user 2026-09-25: a mod has an update when upstream's newest MAIN file was
UPLOADED AFTER the installed copy's manifest date, with both dates printed. Version strings
are not compared -- Nexus uses several shapes and manifests carry integers.

No network: `urlopen` is stubbed; everything else runs for real.
"""
from __future__ import annotations

import io
import json
import types
import urllib.error
from datetime import date, datetime, timezone

import pytest

from x4validate import _modlist, _nexus, _registry


# --- the verdict -----------------------------------------------------------------------

def test_an_upload_after_the_manifest_date_is_an_update_and_both_dates_are_stated():
    v, basis = _modlist._update_verdict("2026-06-01", "2026-07-01", trusted=True)
    assert v == "available"
    assert "2026-06-01" in basis and "2026-07-01" in basis


@pytest.mark.parametrize("upstream", ["2026-06-01", "2026-05-01"])
def test_an_upload_on_or_before_the_manifest_date_is_not_an_update(upstream):
    assert _modlist._update_verdict("2026-06-01", upstream, trusted=True)[0] == "none"


# --- RG-3 refinement: the grace window (user decision 2026-09-26) ----------------------

@pytest.mark.parametrize("upstream, days", [
    ("2026-06-02", 1),   # 1 day after: within the grace window
    ("2026-06-04", 3),   # exactly 3 days after: still within the window
])
def test_an_upload_within_the_grace_window_is_same_release_not_available(upstream, days):
    v, basis = _modlist._update_verdict("2026-06-01", upstream, trusted=True)
    assert v == "same-release?", (days, v, basis)
    assert "same" in basis.lower() and "3 day" in basis
    assert "2026-06-01" in basis and upstream in basis


def test_an_upload_more_than_the_grace_window_after_is_available():
    v, basis = _modlist._update_verdict("2026-06-01", "2026-06-05", trusted=True)  # 4 days
    assert v == "available", (v, basis)


@pytest.mark.parametrize("upstream", ["2026-06-01", "2026-05-01"])
def test_an_upload_on_or_before_the_manifest_date_is_still_none_not_same_release(upstream):
    assert _modlist._update_verdict("2026-06-01", upstream, trusted=True)[0] == "none"


@pytest.mark.parametrize("installed, upstream", [(None, "2026-07-01"), ("2026-06-01", None),
                                                 ("sometime", "2026-07-01")])
def test_a_missing_or_unparseable_date_is_UNKNOWN_never_no_update(installed, upstream):
    v, basis = _modlist._update_verdict(installed, upstream, trusted=True)
    assert v == "unknown", (v, basis)


def test_a_guessed_identity_never_claims_an_update():
    """The upstream may be another mod entirely; same rule as cap_classification."""
    assert _modlist._update_verdict("2026-06-01", "2026-07-01", trusted=False)[0] == "unconfirmed"


@pytest.mark.parametrize("text, want", [
    ("2026-06-17", date(2026, 6, 17)),          # 123 of 156 installed manifests
    ("2026-07-8", date(2026, 7, 8)),            # 1 of 156
    ("25 December 2024", date(2024, 12, 25)),   # 1 of 156
    ("2024-03-11T12:00:00.000+00:00", date(2024, 3, 11)),   # the API's uploaded_time
    ("", None), (None, None), ("13/13/2026", None),
])
def test_parse_date_covers_the_measured_shapes_and_refuses_the_rest(text, want):
    assert _modlist._parse_date(text) == want


# --- which upstream file ----------------------------------------------------------------

def _files(*rows):
    return [_nexus.FileMeta(i, 7, n, v, up, cat) for i, (n, v, up, cat) in enumerate(rows, 1)]


def test_the_newest_MAIN_file_is_the_one_judged_not_a_newer_optional(monkeypatch):
    monkeypatch.setattr(_nexus, "fetch_files", lambda nid: _files(
        ("old main", "1.0", "2026-05-01", "MAIN"),
        ("new main", "2.0", "2026-06-20", "MAIN"),
        ("optional", "2.1", "2026-08-01", "OPTIONAL"),
        ("archived", "0.9", "2026-09-01", "OLD_VERSION")))
    up, what = _modlist._upstream_newest(7, None)
    assert up == "2026-06-20" and "new main" in what


def test_no_MAIN_file_is_unknown_with_the_denominator(monkeypatch):
    monkeypatch.setattr(_nexus, "fetch_files",
                        lambda nid: _files(("opt", "1", "2026-06-01", "OPTIONAL")))
    up, what = _modlist._upstream_newest(7, None)
    assert up is None and "among 1" in what


def test_a_row_pinned_to_a_FILE_is_judged_against_that_file_not_the_MAIN(monkeypatch):
    f = _files(("addon", "3", "2026-07-02", "OPTIONAL"))[0]
    newer_main = _nexus.FileMeta(2, 7, "main", "9", "2026-09-01", "MAIN")
    monkeypatch.setattr(_nexus, "fetch_file_listing", lambda nid: ([f, newer_main], {}))
    assert _modlist._upstream_newest(7, f)[0] == "2026-07-02"


# --- end to end through refresh ---------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_refresh_records_and_prints_the_update_with_both_dates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")
    ts = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
    meta = {"name": "Cool Mod", "version": "2.0", "updated_timestamp": ts,
            "status": "published", "author": "a"}
    files = {"files": [{"file_id": 11, "name": "Cool Mod main", "version": "2.0",
                        "uploaded_time": "2026-07-01T10:00:00.000+00:00",
                        "category_name": "MAIN"}]}

    def urlopen(req, timeout=None):
        body = files if req.full_url.endswith("/files.json") else meta
        return _Resp(json.dumps(body).encode())
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)

    regp = tmp_path / "r.yaml"
    reg = _registry._new_registry()
    e = _registry._new_entry("cool_mod", True)
    e["auto"].update(installed=True, installed_date="2026-06-01", installed_name="Cool Mod")
    e["human"]["nexus_id"] = 7                     # pinned: a trusted identity
    reg["mods"].append(e)
    _registry.save_registry(reg, regp)

    rc = _modlist.cmd_refresh(types.SimpleNamespace(
        registry=str(regp), ids=None, seeded=False, limit=None, force=True, no_resolve=True))
    assert rc == 0
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["update"] == "available", dict(a)
    assert a["upstream_newest_uploaded"] == "2026-07-01"
    out = capsys.readouterr().out
    assert "UPDATE  cool_mod" in out and "2026-06-01" in out and "2026-07-01" in out, out
    dash = (tmp_path / "WORKLIST.md").read_text(encoding="utf-8")
    assert "UPDATE AVAILABLE  (1)" in dash and "2026-06-01" in dash, dash


# --- a Steam outage must never overwrite an existing identity verdict (finding 1) ------

def test_a_steam_outage_leaves_the_existing_id_state_UNCHANGED_and_is_counted(
        tmp_path, monkeypatch):
    """Reviewer probe: a ws_ row that already carries a real 'ambiguous' verdict must
    not come out of a refresh reading 'unmatched' just because Steam timed out --
    that silently discards a stronger answer for a weaker one and says nothing."""
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")

    def urlopen(req, timeout=None):
        raise urllib.error.URLError("steam down")
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(_modlist, "_search_with_fallback", lambda hint: (_ for _ in ()).throw(
        AssertionError("must not search while the Steam title is unknown due to an outage")))

    regp = tmp_path / "r.yaml"
    reg = _registry._new_registry()
    e = _registry._new_entry("ws_3691358137", True)
    e["auto"].update(installed=True, id_state="ambiguous",
                     resolve="ambiguous (2 strong matches)")
    reg["mods"].append(e)
    _registry.save_registry(reg, regp)

    rc = _modlist.cmd_refresh(types.SimpleNamespace(
        registry=str(regp), ids=None, seeded=False, limit=None, force=True, no_resolve=False))
    assert rc == 0
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["id_state"] == "ambiguous", a          # unchanged, not "unmatched"
    assert a["resolve"] == "ambiguous (2 strong matches)", a
    assert "error" in a and "unreachable" in a["error"].lower(), a


def test_a_real_steam_404_STILL_records_unmatched(tmp_path, monkeypatch):
    """Twin: Steam answering with a real 'no title' result is unaffected -- only the
    transport-failure path must be silent about identity."""
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")

    def urlopen(req, timeout=None):
        return _Resp(json.dumps({"response": {"publishedfiledetails": [{"result": 9}]}}
                                ).encode())
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(_modlist, "_search_with_fallback", lambda hint: [])

    regp = tmp_path / "r.yaml"
    reg = _registry._new_registry()
    e = _registry._new_entry("ws_3691358137", True)
    e["auto"].update(installed=True, id_state="ambiguous",
                     resolve="ambiguous (2 strong matches)")
    reg["mods"].append(e)
    _registry.save_registry(reg, regp)

    rc = _modlist.cmd_refresh(types.SimpleNamespace(
        registry=str(regp), ids=None, seeded=False, limit=None, force=True, no_resolve=False))
    assert rc == 0
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["id_state"] == "unmatched", a


def _one_pinned_row(tmp_path, installed_date="2026-06-01"):
    regp = tmp_path / "r.yaml"
    reg = _registry._new_registry()
    e = _registry._new_entry("cool_mod", True)
    e["auto"].update(installed=True, installed_date=installed_date, installed_name="Cool Mod")
    e["human"]["nexus_id"] = 7
    reg["mods"].append(e)
    _registry.save_registry(reg, regp)
    return regp


def _refresh(regp, force=True):
    return _modlist.cmd_refresh(types.SimpleNamespace(
        registry=str(regp), ids=None, seeded=False, limit=None, force=force, no_resolve=True))


_META_BODY = {"name": "Cool Mod", "version": "2.0", "updated_timestamp": 1782900000,
              "status": "published", "author": "a"}


def test_a_fatal_stop_while_judging_the_update_leaves_the_row_UNCHECKED(tmp_path, monkeypatch):
    """Review item 1: checked_at was written before the update verdict, so a fatal stop
    fetching files.json saved the row as checked today with `update: unknown`, and the
    re-run the tool itself recommends skipped it all day (TTL)."""
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")
    import urllib.error

    def urlopen(req, timeout=None):
        if req.full_url.endswith("/files.json"):
            raise urllib.error.URLError("network is unreachable")
        return _Resp(json.dumps(_META_BODY).encode())
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    regp = _one_pinned_row(tmp_path)
    assert _refresh(regp) == 2
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["name"] == "Cool Mod", "what WAS fetched must be kept"
    assert a.get("checked_at") != datetime.now(timezone.utc).date().isoformat(), dict(a)


def _stale_available(regp, **auto):
    reg = _registry.load_registry(regp)
    a = reg["mods"][0]["auto"]
    a.update(update="available", update_basis="upstream uploaded 2026-07-01 vs installed "
             "manifest dated 2026-06-01", upstream_newest="MAIN file 1",
             upstream_newest_uploaded="2026-07-01", **auto)
    _registry.save_registry(reg, regp)


def test_a_row_that_ends_in_ERROR_does_not_keep_a_stale_update_verdict(tmp_path, monkeypatch):
    """Review item 5: the verdict belongs to the fetch that produced it."""
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")
    import urllib.error

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", urlopen)
    regp = _one_pinned_row(tmp_path)
    _stale_available(regp)
    _refresh(regp)
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["classification"] == "error"
    assert a.get("update") != "available", dict(a)


def test_a_row_that_ends_UNTRIAGED_does_not_keep_a_stale_update_verdict(tmp_path):
    regp = tmp_path / "r.yaml"
    reg = _registry._new_registry()
    e = _registry._new_entry("no_id_mod", True)
    e["auto"].update(installed=True, update="available")
    reg["mods"].append(e)
    _registry.save_registry(reg, regp)
    _refresh(regp)                                   # no id, --no-resolve: untriaged
    a = _registry.load_registry(regp)["mods"][0]["auto"]
    assert a["classification"] == "untriaged"
    assert a.get("update") != "available", dict(a)


def test_a_verdict_NOT_rechecked_this_run_is_labelled_carried_over(tmp_path, monkeypatch, capsys):
    """A TTL-skipped row's verdict is from an earlier run; printing it as a plain UPDATE
    line read as 'just checked'."""
    monkeypatch.setattr(_nexus.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("a TTL-skipped row makes no call"))
    regp = _one_pinned_row(tmp_path)
    _stale_available(regp, checked_at=datetime.now(timezone.utc).date().isoformat())
    assert _refresh(regp, force=False) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "cool_mod" in ln)
    assert "carried over" in line, out


# --- a pinned FILE follows its successors (review item 3) --------------------------------

def _listing(files, updates):
    return {"files": [{"file_id": i, "name": f"f{i}", "version": str(i),
                       "uploaded_time": up + "T00:00:00.000+00:00", "category_name": cat}
                      for i, up, cat in files],
            "file_updates": [{"old_file_id": o, "new_file_id": n} for o, n in updates]}


@pytest.mark.parametrize("updates, want_id, want_date", [
    ([], 5, "2026-05-01"),                          # no chain: the pinned file itself
    ([(5, 6)], 6, "2026-06-01"),                    # one successor
    ([(5, 6), (6, 8)], 8, "2026-08-01"),            # followed to the END of the chain
    ([(5, 6), (6, 5)], 6, "2026-06-01"),            # a cycle terminates
    ([(5, 6), (6, 99)], 6, "2026-06-01"),           # successor not listed: last listed one
    ([(1, 2)], 5, "2026-05-01"),                    # a chain for ANOTHER file is ignored
])
def test_a_pinned_file_is_judged_against_its_newest_successor(monkeypatch, updates, want_id,
                                                              want_date):
    """A pin to file 5 used to be judged against file 5 forever. The page's
    `file_updates` records old -> new file ids; the pinned file's successor chain is
    followed to its end, falling back to the pinned file when there is none."""
    listing = _listing([(5, "2026-05-01", "OPTIONAL"), (6, "2026-06-01", "OPTIONAL"),
                        (8, "2026-08-01", "OPTIONAL"), (9, "2026-09-09", "MAIN")], updates)
    monkeypatch.setenv("X4_NEXUS_KEY", "test-key-not-real")
    monkeypatch.setattr(_nexus, "_get_json", lambda url, headers: listing)
    pinned = _nexus.FileMeta(5, 7, "f5", "5", "2026-05-01", "OPTIONAL")
    up, what = _modlist._upstream_newest(7, pinned)
    assert up == want_date and f"file {want_id} " in what, (up, what)


# --- the dashboard excludes same-release? from UPDATE AVAILABLE (RG-3 refinement) ------

def _installed_entry(mod_id, **auto_overrides):
    e = _registry._new_entry(mod_id, True)
    e["auto"]["installed"] = True
    e["auto"].update(auto_overrides)
    return e


def test_dashboard_update_available_table_excludes_same_release():
    reg = _registry._new_registry()
    reg["mods"].append(_installed_entry(
        "avail_mod", name="Really Updated", classification="ready",
        installed_date="2026-06-01", update="available",
        upstream_newest_uploaded="2026-06-10"))
    reg["mods"].append(_installed_entry(
        "same_mod", name="Same Release", classification="ready",
        installed_date="2026-06-01", update="same-release?",
        upstream_newest_uploaded="2026-06-02"))
    out = _registry.generate_dashboard(reg)
    assert "UPDATE AVAILABLE  (1)" in out
    assert "Really Updated" in out
    # the same-release? row must not appear in the UPDATE AVAILABLE table
    avail_block = out.split("## ⬆ UPDATE AVAILABLE")[1].split("## ")[0]
    assert "Same Release" not in avail_block
    # but it is still counted in the tally, so it isn't invisible
    assert "same-release?" in out


# --- refresh --dry-run ---------------------------------------------------------------------

def _plan_registry(regp, today):
    reg = _registry._new_registry()
    rows = {"has_id": {"human": 7}, "needs_search": {}, "checked": {"human": 8, "checked_at": today},
            "gone": {"human": "none"}}
    for name, spec in rows.items():
        e = _registry._new_entry(name, True)
        e["auto"].update(installed=True, installed_date="2026-06-01", installed_name=name)
        if "human" in spec:
            e["human"]["nexus_id"] = spec["human"]
        if "checked_at" in spec:
            e["auto"]["checked_at"] = spec["checked_at"]
        reg["mods"].append(e)
    _registry.save_registry(reg, regp)


def _dry(regp, **kw):
    args = dict(registry=str(regp), ids=None, seeded=False, limit=None, force=False, no_resolve=False, dry_run=True)
    args.update(kw)
    return _modlist.cmd_refresh(types.SimpleNamespace(**args))


def test_dry_run_makes_no_call_needs_no_key_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("X4_NEXUS_KEY", raising=False)

    def no_network(*a, **k):
        raise AssertionError("a dry run opened the network")
    monkeypatch.setattr(_nexus.urllib.request, "urlopen", no_network)
    regp = tmp_path / "r.yaml"
    _plan_registry(regp, datetime.now(timezone.utc).date().isoformat())
    before = regp.read_bytes()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert _dry(regp) == 0
    out = capsys.readouterr().out
    assert regp.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == files
    assert "4 rows in scope" in out and "Nothing was fetched or written" in out


def test_dry_run_sorts_each_row_the_way_the_real_loop_would(tmp_path, monkeypatch, capsys):
    """The plan repeats the loop's tests, so run both over one registry and compare per row."""
    regp = tmp_path / "r.yaml"
    today = datetime.now(timezone.utc).date()
    _plan_registry(regp, today.isoformat())
    reg = _registry.load_registry(regp)
    args = types.SimpleNamespace(force=False, no_resolve=False)
    plan = {m["id"]: _modlist._refresh_plan_row(m, args, today) for m in reg["mods"]}
    assert plan == {"has_id": "fetch", "needs_search": "resolve", "checked": "skip-today", "gone": "off-nexus"}

    fetched, searched = [], []
    monkeypatch.setattr(_nexus, "fetch_mod", lambda nid: fetched.append(nid) or _nexus.ModMeta(
        nexus_id=nid, name="n", author="a", version="1", updated="2026-07-01", status="published"))
    monkeypatch.setattr(_modlist, "_record_update", lambda *a, **k: None)
    monkeypatch.setattr(_modlist, "_resolve_identity", lambda mod_id, a: searched.append(mod_id) or (None, "unmatched"))
    _modlist._refresh_rows(reg["mods"], args, today, set())
    assert fetched == [7] and searched == ["needs_search"]   # 8 was checked today, off-nexus made no call


def test_dry_run_honours_force_no_resolve_and_limit(tmp_path, capsys):
    regp = tmp_path / "r.yaml"
    today = datetime.now(timezone.utc).date()
    _plan_registry(regp, today.isoformat())
    reg = _registry.load_registry(regp)
    by = {m["id"]: m for m in reg["mods"]}
    forced = types.SimpleNamespace(force=True, no_resolve=False)
    assert _modlist._refresh_plan_row(by["checked"], forced, today) == "fetch"
    no_res = types.SimpleNamespace(force=False, no_resolve=True)
    assert _modlist._refresh_plan_row(by["needs_search"], no_res, today) == "no-id"
    assert _dry(regp, limit=1) == 0
    assert "1 rows in scope" in capsys.readouterr().out


def test_dry_run_still_refuses_a_registry_path_that_is_not_a_file(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _dry(tmp_path / "typo.yaml")

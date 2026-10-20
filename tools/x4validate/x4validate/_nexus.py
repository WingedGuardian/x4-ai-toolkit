"""Nexus + Steam API clients. API-FIRST: all Nexus access is via the API, NEVER scraped.

- Nexus metadata by id : v1 REST   /v1/games/x4foundations/mods/{id}.json   (apikey header)
- Nexus name -> id     : v2 GraphQL /v2/graphql  (nameStemmed filter, gameId 2659)
- Steam ws_ title      : keyless ISteamRemoteStorage/GetPublishedFileDetails

OPERATING NOTES (were resident in CLAUDE.md until 2026-09-20; they belong with the code).

- `status` on the v1 payload is `published` / `removed` / `hidden`; the last two mean the
  mod is unavailable, which is a finding about the mod, not an error to swallow.
- Folder ids rarely match mod names. Humanize (split camelCase and underscores) and DROP a
  leading author token when the first search comes back empty.
- Rate budget ~20k/day and ~2k/hour; `X-RL-Daily-Remaining` carries what is left.
- A key is per-user and free: nexusmods.com -> Site preferences -> API Access -> Personal
  API Key. The AUP makes it PERSONAL: a public tool must have each user supply their own,
  so never bundle, commit or log one.
- Identity/version resolution is LOCAL-FIRST, cheapest first: the installed `content.xml`,
  then the mod folder README/changelog, then the Steam Workshop page, and the Nexus API
  last -- it is the only source for what upstream currently ships.
- The same host serves every game: the endpoints take a game_domain_name and a mod_id, so the
  SHAPE ports to another Nexus-modded game -- but this module does not, unchanged: the domain
  is baked into NEXUS_REST and the numeric game id into X4_GAMEID.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from x4validate import _paths

NEXUS_REST = "https://api.nexusmods.com/v1/games/x4foundations/mods"
NEXUS_GQL = "https://api.nexusmods.com/v2/graphql"
STEAM_GPFD = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
X4_GAMEID = "2659"
# A real User-Agent matters: the GraphQL endpoint is behind Cloudflare and 403s
# urllib's default "Python-urllib/x.y" UA.
_APP = {"Application-Name": "x4modlist", "Application-Version": "0.1",
        "User-Agent": "x4modlist/0.1 (+X4 mod registry tool)"}


class NexusError(Exception):
    """Any failed Nexus lookup. A lookup failure is a NON-ANSWER about the mod."""


class NexusFatal(NexusError):
    """A failure no later call in the same run can get past, so a batch must STOP.

    AUDIT-2026-09-24 RG-2: a revoked key used to be recorded per row as `error`, so a
    refresh overwrote EVERY row's lane and kept calling the API with a key it already
    knew was refused. These are the causes that apply to the whole run, not one mod.
    """


class NexusAuthError(NexusFatal):
    """No key, or the key was refused (HTTP 401/403)."""


class NexusRateLimited(NexusFatal):
    """HTTP 429, or the last response reported 0 requests remaining (X-RL-* headers)."""


class NexusUnreachable(NexusFatal):
    """The network failed: no route, DNS, refused, or timed out (URLError/OSError)."""


class SteamUnavailable(Exception):
    """Steam could not be asked at all: network failure, timeout, or a non-JSON body.

    Deliberately NOT a NexusError/NexusFatal -- Steam being unreachable says nothing
    about whether Nexus is. Distinguished from `steam_title` returning None (Steam
    answered and the item genuinely has no title) so a transport outage can never be
    read as the identity answer "unmatched" (a real prior verdict was being overwritten
    by a Steam hiccup, rc 0, with no mention of the outage)."""


#: The most recent `X-RL-*-Remaining` values seen, by header name. Module state on
#: purpose: the budget is per KEY, not per call, so the next call must know what the last
#: one was told. `reset_rate_limit()` clears it at the start of a run.
_rate_remaining: dict[str, int] = {}
_RL_HEADERS = ("X-RL-Hourly-Remaining", "X-RL-Daily-Remaining")

#: Requests that SUCCEEDED since the last `reset_rate_limit()`. A 403 before any success
#: is the key being refused; after one, the key has been accepted this run, so a 403 is
#: about THAT request -- a hidden mod's file list, a Cloudflare block (see `_APP`) -- and
#: must not stop the run (review of RG-2). Counted in `_mapped`, so a stubbed transport
#: counts the same as the real one.
_ok_this_run = [0]


def reset_rate_limit() -> None:
    """Start a run: forget the rate budget and the accepted-key evidence."""
    _rate_remaining.clear()
    _ok_this_run[0] = 0


def _open_json(req: urllib.request.Request):
    """Send *req*, record the rate budget it reports, and decode its JSON.

    Raises the RAW transport/decode exception: `_mapped` is the single place those
    become NexusErrors, so a caller (or a test) that stubs `_get_json`/`_post_json`
    gets the same mapping as the real transport.
    """
    spent = [h for h, n in _rate_remaining.items() if n <= 0]
    if spent:
        raise NexusRateLimited(f"not sent -- the last response reported "
                               f"{', '.join(spent)} = 0")
    with urllib.request.urlopen(req, timeout=30) as r:
        headers = getattr(r, "headers", None)
        body = r.read()
    for h in _RL_HEADERS:
        v = headers.get(h) if headers is not None else None
        try:
            if v is not None:
                _rate_remaining[h] = int(v)
        except (TypeError, ValueError):
            pass  # silent-ok: an unparseable budget header is simply not a budget reading
    return json.loads(body)


def _mapped(what: str, fn, *args):
    """Call a transport function, mapping EVERY failure to a NexusError.

    AUDIT-2026-09-24 RG-1: only `HTTPError` was caught, so a `URLError`, a timeout or a
    Cloudflare HTML page (not JSON) escaped as a raw exception and a `refresh` lost every
    row it had already fetched. A run-wide cause raises a `NexusFatal` subclass (RG-2); a
    per-request one (another HTTP status, a body that is not JSON) a plain NexusError.
    """
    try:
        out = fn(*args)
    except NexusError as exc:
        raise type(exc)(f"{what}: {exc}") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 401 or (exc.code == 403 and not _ok_this_run[0]):
            raise NexusAuthError(f"{what}: HTTP {exc.code} -- the Nexus API key is missing, "
                                 "invalid or revoked (check X4_NEXUS_KEY)") from exc
        if exc.code == 429:
            raise NexusRateLimited(f"{what}: HTTP 429 -- the Nexus rate limit is spent "
                                   "(about 2,000/hour, 20,000/day per key)") from exc
        raise NexusError(f"{what} HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        # http.client.HTTPException (IncompleteRead, BadStatusLine, ...) is neither a
        # URLError nor an OSError, and escaped as a traceback (review of RG-1).
        raise NexusUnreachable(
            f"{what}: network failure ({type(exc).__name__}: {exc})") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise NexusError(f"{what}: the response is not JSON ({exc})") from exc
    _ok_this_run[0] += 1
    return out


def nexus_key() -> str:
    """The personal API key, from the environment OR `x4-paths.env`.

    Resolved through `_paths`, not `os.environ`: `setup.sh` tells users they may
    put `X4_NEXUS_KEY` in the config file, and reading only the environment made
    that documented placement silently ineffective. `value()` (not `path_value()`)
    because a key is not a path and must come back byte-for-byte.
    """
    k = _paths.value("X4_NEXUS_KEY")
    if not k:
        raise NexusAuthError("X4_NEXUS_KEY not set (Nexus personal API key). Export it, "
                         "or add it to x4-paths.env.")
    return k


def _get_json(url: str, headers: dict) -> dict:
    return _open_json(urllib.request.Request(url, headers=headers))


def _post_json(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    return _open_json(req)


#: ACCOUNT-WIDE: every mod the key's account tracks, across every game. Filtering
#: it to one game is a NARROWING STEP, which is why `fetch_tracked` returns the
#: denominator alongside the kept ids rather than a bare list.
NEXUS_TRACKED = "https://api.nexusmods.com/v1/user/tracked_mods.json"


@dataclass
class Tracked:
    """Tracked mods for ONE game, carrying the account-wide denominator.

    `ids` alone would read as the whole answer. MEASURED 2026-08-27 on a live
    key: 1,616 tracked rows across 9 domains, of which 413 are x4foundations —
    so a bare "413" hides three quarters of what the endpoint returned, and an
    empty `ids` would be indistinguishable from "you track nothing at all".
    """
    ids: list[int]            # deduped, sorted, for the requested domain
    kept: int                 # len(ids) — stated so the pair reads as a fraction
    total: int                # rows returned ACROSS ALL GAMES: the denominator
    domains: dict[str, int]   # per-domain counts, so the exclusion is nameable
    malformed: int            # rows dropped for having no usable mod_id
    #: rows with NO NAMEABLE DOMAIN -- not a dict, or `domain_name` absent/None.
    #: These were counted in NEITHER `domains` NOR `malformed`, so `total` minus the
    #: sum of `domains` was an unexplained remainder with no field to name it, in the
    #: one class this dataclass exists to make nameable. MEASURED 2026-09-05 with a
    #: stubbed payload (no network): 7 rows -> kept 1, domains sum 4, malformed 1,
    #: and 3 rows reported by nothing at all.
    no_domain: int = 0


def fetch_tracked(domain: str = "x4foundations") -> Tracked:
    """Mods the account TRACKS on Nexus, filtered to *domain*.

    Tracking is a SUPERSET of installing and a different question from either
    "what is on disk" or "what is enabled": it is what the user asked Nexus to
    watch. That makes it the right source for two things `_registry` cannot
    answer — mods followed but never installed (candidates), and mods installed
    but NOT followed (updates nobody will hear about).

    Raises rather than returning an empty result when the payload is not a list.
    A shape change upstream must be a NON-ANSWER; rendering it as "you track 0"
    is the absence-versus-non-answer confusion this toolkit exists to refuse.
    """
    rows = _mapped("fetch_tracked", _get_json, NEXUS_TRACKED, {"apikey": nexus_key(), **_APP})
    if not isinstance(rows, list):
        raise NexusError(
            f"unexpected payload from {NEXUS_TRACKED}: expected a list, got "
            f"{type(rows).__name__}. Refusing to report a count from a shape "
            "this function does not understand.")
    domains: dict[str, int] = {}
    ids: set[int] = set()
    malformed = 0
    no_domain = 0
    for r in rows:
        d = r.get("domain_name") if isinstance(r, dict) else None
        if d:
            domains[d] = domains.get(d, 0) + 1
        else:
            # NAMED, not dropped between two channels. See Tracked.no_domain.
            no_domain += 1
        if d != domain:
            continue
        raw = r.get("mod_id")
        # A missing key must be COUNTED, never silently skipped: `el.get(...)`
        # dropping rows quietly is a registered defect shape in this workspace.
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            malformed += 1
    out = sorted(ids)
    return Tracked(ids=out, kept=len(out), total=len(rows),
                   domains=domains, malformed=malformed, no_domain=no_domain)


@dataclass
class ModMeta:
    nexus_id: int
    name: str
    version: str
    updated: str  # YYYY-MM-DD
    status: str   # published | removed | ...
    author: str


def fetch_mod(nexus_id: int) -> ModMeta:
    """v1 REST metadata-by-id. Raises NexusError on HTTP failure."""
    h = {"apikey": nexus_key(), **_APP}
    m = _mapped(f"fetch_mod({nexus_id})", _get_json, f"{NEXUS_REST}/{int(nexus_id)}.json", h)
    if not isinstance(m, dict):
        raise NexusError(f"fetch_mod({nexus_id}): expected an object, got {type(m).__name__}")
    ts = int(m.get("updated_timestamp") or 0)
    upd = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""
    return ModMeta(int(nexus_id), m.get("name", ""), str(m.get("version", "")),
                   upd, m.get("status", ""), m.get("author", ""))


@dataclass
class FileMeta:
    """One FILE on a mod page.

    Exists because a mod is not always a page. Plenty of add-ons ship as a file on
    someone else's mod page, and for those the page's own `version` is not the
    add-on's version — it tracks whatever the page owner last uploaded. Comparing
    an installed add-on against the page version silently answers a different
    question than the one asked.
    """
    file_id: int
    mod_id: int
    name: str
    version: str
    uploaded: str  # YYYY-MM-DD
    category: str  # MAIN | OPTIONAL | OLD_VERSION | ARCHIVED | ...


def fetch_files(nexus_id: int) -> list[FileMeta]:
    """Every file on a mod page, newest-uploaded last (API order preserved)."""
    return fetch_file_listing(nexus_id)[0]


def fetch_file_listing(nexus_id: int) -> tuple[list[FileMeta], dict[int, int]]:
    """(every file on the page, the page's `file_updates` as old_file_id -> new_file_id).

    `file_updates` is how an author records that a file was superseded by another, so a
    pin to one FILE can follow its successors instead of being judged against the
    pinned file forever. A malformed update row is dropped like a malformed file row.
    """
    h = {"apikey": nexus_key(), **_APP}
    d = _mapped(f"fetch_files({nexus_id})", _get_json,
                f"{NEXUS_REST}/{int(nexus_id)}/files.json", h)
    updates: dict[int, int] = {}
    for u in (d or {}).get("file_updates") or []:
        try:
            updates[int(u["old_file_id"])] = int(u["new_file_id"])
        except (KeyError, TypeError, ValueError):
            # silent-ok: one malformed supersession row. Its only consumer follows a
            # chain and falls back to the pinned file, which it names, when a link is
            # missing -- a dropped row cannot turn into a false "up to date".
            continue
    out = []
    for f in (d or {}).get("files") or []:
        try:
            out.append(FileMeta(int(f["file_id"]), int(nexus_id), f.get("name", ""),
                                str(f.get("version", "")),
                                str(f.get("uploaded_time", ""))[:10],
                                f.get("category_name") or ""))
        except (KeyError, TypeError, ValueError):
            # silent-ok: one malformed entry in a file listing. Callers that need a
            # SPECIFIC file use fetch_file(), which raises when its id is absent —
            # so a dropped row here can never render as "that file is gone".
            continue
    return out, updates


def fetch_file(nexus_id: int, file_id: int) -> FileMeta:
    """One file by id.

    Raises NexusError when the id is not on the page — which is real information,
    not a lookup failure: an add-on file that has been superseded and archived off
    the page is exactly the "you are running something upstream no longer offers"
    signal the registry should surface, so it must never be swallowed.
    """
    for f in fetch_files(nexus_id):
        if f.file_id == int(file_id):
            return f
    raise NexusError(f"file {file_id} is not listed on mod {nexus_id} "
                     f"(superseded, archived, or the wrong page)")


def search_mods(name: str, count: int = 5) -> list[tuple[int, str]]:
    """Legacy identity-search bridge. Bad pages raise; only a real empty page is []."""
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("search count must be from 1 to 100")
    safe = json.dumps(name)  # JSON-quoted+escaped GraphQL string literal
    query = ('query { mods(filter: {gameId: [{value: "%s"}], nameStemmed: [{value: %s}]}, '
             'count: %d) { nodes { modId name } } }' % (X4_GAMEID, safe, count))
    res = _mapped(f"search_mods({name!r})", _post_json, NEXUS_GQL, {"query": query},
                  {"apikey": nexus_key(), **_APP})
    data = res.get("data") if isinstance(res, dict) else None
    page = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(page, dict) or not isinstance(page.get("nodes"), list) or res.get("errors"):
        raise NexusError("search_mods: GraphQL did not return a complete usable search page")
    nodes = page["nodes"]
    out = []
    for n in nodes:
        try:
            raw_id = n["modId"]
            title = n["name"]
            if isinstance(raw_id, bool) or not str(raw_id).isdigit() or int(raw_id) < 1 or not isinstance(title, str) or not title.strip():
                raise ValueError("invalid candidate")
            out.append((int(raw_id), title))
        except (KeyError, TypeError, ValueError):
            raise NexusError("search_mods: malformed candidate; identity resolution is unavailable")
    return out


def discover(name: str, count: int = 15) -> dict:
    """Candidate discovery with version/date/status and explicit page coverage.

    Preserve X4's credential/rate/error mapping; the optional shared layer owns
    only the game-parameterized query and response contract.
    """
    from . import _harness
    provider = _harness.module("discovery")
    body = provider.request("x4foundations", name, count)
    payload = _mapped("discover", _post_json, NEXUS_GQL, body,
                      {"apikey": nexus_key(), **_APP})
    return provider.parse(payload, "x4foundations", name, count)


def steam_title(ws_number: str) -> tuple[str, str] | None:
    """Keyless Steam Workshop title lookup.

    Returns (title, creator_steamid) when Steam answers with one, or None when Steam
    answered and the item genuinely has no title (a real "not found"/removed result).
    Raises SteamUnavailable when Steam could not be asked at all -- network failure,
    timeout, or a non-JSON body -- which is a transport outage, not a fact about the
    mod, and must never be conflated with the real "no title" answer above (the two
    used to share one None, and a caller took the outage as "unmatched").
    """
    ws_number = str(ws_number).removeprefix("ws_")
    form = urllib.parse.urlencode({"itemcount": "1", "publishedfileids[0]": ws_number}).encode()
    req = urllib.request.Request(STEAM_GPFD, data=form, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        # URLError (HTTPError's parent), timeouts, other OSErrors and a non-JSON body
        # (json.load raising ValueError) are all "could not ask", never "asked, no
        # title" -- catching only HTTPError previously let some of these crash a
        # refresh (RG-1) while others silently impersonated a real answer.
        raise SteamUnavailable(f"Steam GetPublishedFileDetails unreachable: {exc}") from exc
    response = d.get("response") if isinstance(d, dict) else None
    details = response.get("publishedfiledetails") if isinstance(response, dict) else None
    if (not isinstance(details, list) or len(details) != 1 or not isinstance(details[0], dict)
            or type(details[0].get("result")) is not int):
        raise SteamUnavailable("Steam GetPublishedFileDetails returned an unusable item response")
    item = details[0]
    if item["result"] == 9:  # EResult.FileNotFound: an explicit per-item absence
        return None
    if item["result"] != 1:
        # Other EResult values include access, service and timeout failures.
        # https://partner.steamgames.com/doc/api/steam_api#EResult
        raise SteamUnavailable(f"Steam GetPublishedFileDetails failed with result {item['result']}")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise SteamUnavailable("Steam reported a successful item without a usable title")
    return title, str(item.get("creator", ""))

"""Strict reader for the fixed, read-only equipment capture protocol.

The game retains one bounded capture. Pages only retrieve it; they never re-query
the engine. A fresh UUID per request is part of the protocol, not authentication.
No compatibility, loadout correctness, or operational verdict is inferred here.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import unquote

HEADER = set("""version token ship build loaded_at start finish clock atomic page pages
enumeration_complete reads_complete equipment_identities_complete errors limited slots
unknown_slots groups_reported groups_returned groups summaries engine_reported engine_captured
shield_reported shield_captured turret_reported turret_captured weapon_reported weapon_captured
calls slot_cap group_cap call_cap row_bytes_cap page_bytes_cap cache_bytes_cap ttl""".split())
SLOT = set("type slot path group component macro slot_macro state read".split())
GROUP = set("index context path group read".split())
SUMMARY = set("index type context path group component macro size count operational total read".split())
TYPES = {"engine", "shield", "turret", "weapon"}
BOOLS = {"atomic", "enumeration_complete", "reads_complete", "equipment_identities_complete", "limited"}
CAPS = dict(slot_cap=1024, group_cap=256, call_cap=8192, row_bytes_cap=240000,
            page_bytes_cap=32000, cache_bytes_cap=262144, ttl=60)
HANDLE = re.compile(r"(?:0|[1-9][0-9]*)ULL\Z")
UINT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def number(s: str) -> int:
    if not UINT.fullmatch(s):
        raise ValueError("non-canonical equipment integer")
    return int(s)


def record(line: str) -> tuple[str, dict[str, str]]:
    parts = line.split("\t")
    result = {}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if not sep or not key or key in result:
            raise ValueError("malformed or duplicate equipment field")
        # Producer escapes control bytes, pipe and percent. Keep raw values until
        # after validation: decoding early could turn an escaped '?' into a sentinel.
        if re.search(r"[\x00-\x1f\x7f|]", value) or re.search(r"%(?![0-9A-F]{2})", value):
            raise ValueError("unescaped equipment delimiter or invalid escape")
        result[key] = value
    return parts[0], result


class EquipmentRefused(ValueError):
    """The engine answered a capture request with a non-OK status.

    Keeps `status`, the engine's `payload` and the `page` it answered (1 = the request).
    """

    def __init__(self, status: str, message: str, *, payload: str = "", page: int = 1):
        super().__init__(message)
        self.status, self.payload, self.page = status, payload, page

    # Engine answers ABOUT THE REQUESTED ID (live_query.lua equipment verb), the only
    # non-OK replies that are findings rather than non-answers.
    ID_ANSWERS = ("equipment id was not issued by this UI session",
                  "equipment target is not a ship")

    def is_finding(self) -> bool:
        if self.page != 1:
            return False
        if self.status == "ABSENT":
            return True
        return self.status == "ERR" and self.payload.startswith(self.ID_ANSWERS)


def fetch(pipe, ship: str, nonce: str) -> dict:
    """Retrieve one bounded capture through an existing open LivePipe."""
    if not HANDLE.fullmatch(ship) or ship == "0ULL":
        raise ValueError("equipment requires a nonzero issued ship ID")
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("equipment requires a fresh UUID nonce")
    header = None
    rows = []
    total_bytes = 0
    row_bytes = 0
    next_page = 1
    while True:
        response = pipe.ask("equipment", ship, nonce) if next_page == 1 else pipe.ask(
            "equipment-page", header["token"], str(next_page))
        if response.status != "OK":
            raise EquipmentRefused(response.status,
                                   f"equipment capture refused ({response.status}): {response.payload}",
                                   payload=str(response.payload), page=next_page)
        payload = response.payload
        encoded = len(payload.encode("utf-8"))
        total_bytes += encoded
        if encoded > CAPS["page_bytes_cap"] or total_bytes > CAPS["cache_bytes_cap"]:
            raise ValueError("equipment reply/cache byte bound exceeded")
        lines = payload.split("\n")
        kind, h = record(lines[0])
        if kind != "EQUIPMENT" or set(h) != HEADER:
            raise ValueError("equipment header fields differ from protocol")
        if h["version"] != "1" or h["ship"] != ship or not re.fullmatch(nonce + r"-[1-9][0-9]*", h["token"]):
            raise ValueError("equipment capture identity mismatch")
        if not re.fullmatch(r"[0-9a-f]{8}", h["build"]):
            raise ValueError("equipment build stamp is malformed")
        for key in BOOLS:
            if h[key] not in {"true", "false"}:
                raise ValueError("invalid equipment boolean")
        if h["atomic"] != "false" or h["clock"] != "ui_elapsed":
            raise ValueError("unsupported equipment timing claim")
        times = [float(h[k]) for k in ("loaded_at", "start", "finish")]
        if not all(math.isfinite(v) and v >= 0 for v in times) or times[2] < times[1]:
            raise ValueError("invalid equipment time window")
        for key in ("version", "page", "pages", "errors", "slots", "unknown_slots",
                    "groups", "summaries", "calls", *(t + "_captured" for t in TYPES)):
            number(h[key])
        for key in ("groups_reported", "groups_returned", *(t + "_reported" for t in TYPES)):
            if h[key] != "?":
                number(h[key])
        for key, value in CAPS.items():
            if number(h[key]) != value:
                raise ValueError("unsupported equipment bounds")
        pages = number(h["pages"])
        if not 1 <= pages <= 16 or number(h["page"]) != next_page:
            raise ValueError("equipment page order/count mismatch")
        if header is not None and {k: v for k, v in h.items() if k != "page"} != {
                k: v for k, v in header.items() if k != "page"}:
            raise ValueError("equipment metadata changed between immutable pages")
        header = h
        for line in lines[1:]:
            if line:
                row_bytes += len(line.encode("utf-8")) + 1
                if row_bytes > CAPS["row_bytes_cap"]:
                    raise ValueError("equipment captured-row byte bound exceeded")
                rows.append(record(line))
            elif len(lines) != 2:
                raise ValueError("unexpected empty equipment row")
        if next_page == pages:
            break
        next_page += 1
    return validate(header, rows)


def validate(h: dict[str, str], rows: list[tuple[str, dict[str, str]]]) -> dict:
    slots, groups, summaries = [], [], []
    seen_slots, seen_groups, seen_summary = set(), set(), set()
    for kind, r in rows:
        expected = {"SLOT": SLOT, "GROUP": GROUP, "SUMMARY": SUMMARY}.get(kind)
        if expected is None or set(r) != expected or r["read"] not in {"ok", "failed"}:
            raise ValueError("invalid equipment row")
        for field in expected & {"path", "group", "macro", "slot_macro", "size"}:
            native_text = unquote(r[field], encoding="utf-8", errors="strict")
            if len(native_text.encode("utf-8")) > 256 or (native_text == "?" and r[field] != "?"):
                raise ValueError("equipment native string violates producer bounds")
        if kind == "SLOT":
            if r["type"] not in TYPES or r["state"] not in {"installed", "empty", "unknown"}:
                raise ValueError("invalid slot type/state")
            key = (r["type"], number(r["slot"]))
            if key[1] < 1 or key in seen_slots:
                raise ValueError("duplicate/invalid equipment slot")
            seen_slots.add(key)
            component = r["component"]
            if component != "?" and not HANDLE.fullmatch(component):
                raise ValueError("invalid slot component")
            if r["read"] == "ok" and any(r[k] == "?" for k in ("component", "path", "group")):
                raise ValueError("unknown slot identity labelled readable")
            if r["state"] == "installed" and (component in {"?", "0ULL"} or r["macro"] in {"?", ""} or r["read"] != "ok"):
                raise ValueError("unsupported installed-slot claim")
            direct = r["type"] == "engine" or (r["path"] == ".." and r["group"] == "")
            if direct and r["read"] == "ok" and r["slot_macro"] == "?":
                raise ValueError("direct slot macro unknown despite readable claim")
            if direct and r["state"] == "installed" and r["slot_macro"] != r["macro"]:
                raise ValueError("direct slot macro disagrees with component")
            if r["state"] == "empty" and (component != "0ULL" or r["macro"] != "?" or r["slot_macro"] != "" or r["read"] != "ok"
                    or not (r["type"] == "engine" or (r["path"] == ".." and r["group"] == ""))):
                raise ValueError("unsupported empty-slot claim")
            slots.append(r)
        elif kind == "GROUP":
            key = number(r["index"])
            if key in seen_groups:
                raise ValueError("duplicate group descriptor")
            seen_groups.add(key)
            if r["context"] != "?" and not HANDLE.fullmatch(r["context"]):
                raise ValueError("invalid group context")
            if r["read"] == "ok" and any(r[k] == "?" for k in ("context", "path", "group")):
                raise ValueError("unknown group identity labelled readable")
            groups.append(r)
        else:
            key = (number(r["index"]), r["type"])
            if key in seen_summary or key[1] not in TYPES - {"weapon"}:
                raise ValueError("duplicate/invalid group summary")
            seen_summary.add(key)
            for f in ("component", "context"):
                if r[f] != "?" and not HANDLE.fullmatch(r[f]):
                    raise ValueError("invalid group summary handle")
            for f in ("count", "operational", "total"):
                if r[f] != "?":
                    number(r[f])
            if r["read"] == "ok" and any(r[k] == "?" for k in SUMMARY - {"read", "type", "index"}):
                raise ValueError("unknown group summary labelled readable")
            summaries.append(r)
    if len(slots) != number(h["slots"]) or len(groups) != number(h["groups"]) or len(summaries) != number(h["summaries"]):
        raise ValueError("equipment row accounting mismatch")
    if len(slots) > CAPS["slot_cap"] or len(groups) > CAPS["group_cap"] or number(h["calls"]) > CAPS["call_cap"]:
        raise ValueError("equipment work bounds exceeded")
    counts = Counter(r["type"] for r in slots)
    unknown = sum(r["state"] == "unknown" for r in slots)
    if unknown != number(h["unknown_slots"]):
        raise ValueError("unknown-slot accounting mismatch")
    for typ in TYPES:
        n = number(h[typ + "_captured"])
        if n != counts[typ] or {i for t, i in seen_slots if t == typ} != set(range(1, n + 1)):
            raise ValueError("slot coverage has holes")
        reported = h[typ + "_reported"]
        if reported != "?" and number(reported) < n:
            raise ValueError("slot count exceeds native denominator")
    if seen_groups != set(range(len(groups))):
        raise ValueError("group coverage has holes")
    group_map = {number(r["index"]): r for r in groups}
    for r in summaries:
        g = group_map.get(number(r["index"]))
        if not g or any(r[k] != g[k] for k in ("context", "path", "group")):
            raise ValueError("group summary identity mismatch")
    for key in ("groups_reported", "groups_returned"):
        if h[key] != "?" and number(h[key]) < len(groups):
            raise ValueError("group denominator mismatch")
    if h["enumeration_complete"] == "true":
        if h["limited"] != "false" or any(h[t + "_reported"] != h[t + "_captured"] for t in TYPES):
            raise ValueError("unsupported complete slot enumeration")
        if h["groups_reported"] != h["groups"] or h["groups_returned"] != h["groups"]:
            raise ValueError("unsupported complete group enumeration")
        expected_summary = {(number(g["index"]), t) for g in groups
                            if g["read"] == "ok" and (g["path"] != ".." or g["group"] != "")
                            for t in TYPES - {"weapon"}}
        if seen_summary != expected_summary:
            raise ValueError("group summary enumeration incomplete")
    if h["reads_complete"] == "true" and (number(h["errors"]) or any(r["read"] != "ok" for _, r in rows)):
        raise ValueError("unsupported complete equipment reads")
    complete = (h["enumeration_complete"] == h["reads_complete"] == "true" and unknown == 0)
    if (h["equipment_identities_complete"] == "true") != complete:
        raise ValueError("equipment identity coverage contradicts rows")
    # Percent-decoding is display only. json.dumps escapes decoded control characters.
    def decode(r):
        return {k: unquote(v, encoding="utf-8", errors="strict") for k, v in r.items()}
    return {"status": "complete" if complete else "partial", "snapshot": decode(h),
            "slots": [decode(r) for r in slots], "groups": [decode(r) for r in groups],
            "group_summaries": [decode(r) for r in summaries]}

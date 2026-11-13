"""Strict reconstruction of read-only menu pages; no transport retry or game writes."""
from __future__ import annotations

import hashlib
import json
import math
import re

REVISION = "menu-observation-pages-v1"
COMMON = {"version", "revision", "status", "capture", "total_bytes", "total_nodes",
          "total_string_bytes", "pages", "chunk_bytes", "captured_age", "read_complete",
          "option_counts"}
MAX_SAFE = 2**53 - 1


class MenuPagesUnavailable(ValueError):
    """Incomplete, mixed, stale or malformed capture; no completeness claim."""


def _integer(v, low, high):
    return type(v) is int and low <= v <= high


def _finite(v):
    return type(v) in (int, float) and abs(v) <= MAX_SAFE and math.isfinite(v)


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise MenuPagesUnavailable("duplicate JSON key")
        out[k] = v
    return out


def _decode(raw, limit):
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
            raise MenuPagesUnavailable("message byte limit")
        return json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              MenuPagesUnavailable("nonfinite JSON")))
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise MenuPagesUnavailable("invalid page encoding") from exc


def _header(value, status):
    keys = COMMON | ({"index", "offset", "length", "hex"} if status == "PAGE" else set())
    if (not isinstance(value, dict) or set(value) != keys
            or type(value["version"]) is not int or value["version"] != 1
            or value["revision"] != REVISION or value["status"] != status
            or not isinstance(value["capture"], str)
            or not re.fullmatch("[0-9a-f]{32}", value["capture"])
            or not _integer(value["total_bytes"], 1, 1048576)
            or not _integer(value["total_nodes"], 1, 12000)
            or not _integer(value["total_string_bytes"], 0, 131072)
            or type(value["chunk_bytes"]) is not int or value["chunk_bytes"] != 1000
            or not _integer(value["pages"], 1, 1049)
            or value["pages"] != (value["total_bytes"] + 999) // 1000
            or not _finite(value["captured_age"]) or value["captured_age"] < 0
            or type(value["read_complete"]) is not bool):
        raise MenuPagesUnavailable("invalid menu capture header")
    options = value["option_counts"]
    if not isinstance(options, dict) or set(options) != {"class", "ship", "loadout"}:
        raise MenuPagesUnavailable("missing option counts")
    if any(not (_integer(v, 0, 2048) or v == "UNKNOWN") for v in options.values()):
        raise MenuPagesUnavailable("invalid option counts")
    if value["read_complete"] and "UNKNOWN" in options.values():
        raise MenuPagesUnavailable("unknown option count is not complete")
    return {k: value[k] for k in COMMON if k != "status"}


def _tree(encoded):
    budget = {"nodes": 0, "strings": 0}

    def read(v, depth=0):
        budget["nodes"] += 1
        if budget["nodes"] > 12000 or depth > 20:
            raise MenuPagesUnavailable("model structure limit")
        if isinstance(v, str):
            size = len(v.encode("utf-8"))
            budget["strings"] += size
            if size > 4096 or budget["strings"] > 131072:
                raise MenuPagesUnavailable("model string limit")
            return v
        if type(v) is bool:
            return v
        if _finite(v) and abs(v) <= MAX_SAFE:
            return v
        if not isinstance(v, dict) or set(v) != {"$table"} or not isinstance(v["$table"], list):
            raise MenuPagesUnavailable("invalid typed model value")
        out, previous = {}, None
        for entry in v["$table"]:
            if not isinstance(entry, list) or len(entry) != 3:
                raise MenuPagesUnavailable("invalid typed table entry")
            kind, key, child = entry
            if kind == "n":
                if not _integer(key, -MAX_SAFE, MAX_SAFE):
                    raise MenuPagesUnavailable("invalid integer table key")
                order = (0, key)
            elif kind == "s" and isinstance(key, str):
                order = (1, key.encode("utf-8"))
            else:
                raise MenuPagesUnavailable("invalid table key type")
            if previous is not None and order <= previous:
                raise MenuPagesUnavailable("duplicate or unsorted table key")
            previous = order
            read(key, depth + 1)
            out[key] = read(child, depth + 1)
        return out
    value = read(encoded)
    return value, budget


def _observation(value):
    if (not isinstance(value, dict) or value.get("status") != "OBSERVED"
            or not isinstance(value.get("model"), dict) or not isinstance(value.get("widgets"), dict)
            or not isinstance(value.get("epoch"), str) or not value["epoch"]
            or not _finite(value.get("age")) or value["age"] < 0):
        raise MenuPagesUnavailable("missing source observation")
    widgets = value["widgets"]
    if set(widgets) != {"class", "ship", "loadout", "add", "confirm"}:
        raise MenuPagesUnavailable("missing widget population")
    complete, counts = True, {}
    for name, widget in widgets.items():
        if not isinstance(widget, dict) or widget.get("status") not in {"OBSERVED", "UNKNOWN", "UNAVAILABLE"}:
            raise MenuPagesUnavailable("invalid widget observation")
        if widget["status"] == "OBSERVED":
            if not isinstance(widget.get("model"), dict):
                raise MenuPagesUnavailable("missing widget model")
        else:
            complete = False
        if name in {"class", "ship", "loadout"}:
            if widget["status"] == "OBSERVED":
                rows = widget["model"].get("options")
                if (not isinstance(rows, dict) or len(rows) > 2048
                        or any(type(k) is not int for k in rows)
                        or set(rows) != set(range(1, len(rows) + 1))):
                    raise MenuPagesUnavailable("invalid source option population")
                counts[name] = len(rows)
            else:
                counts[name] = "UNKNOWN"
    return complete, counts


class MenuCapture:
    """Collect exactly one immutable capture, sequentially, without retries.

    Pass raw PAGE JSON returned through a qualified BoundPipe. The outer binding
    remains responsible for process/session/helper identity; this reader does not
    claim to authenticate a standalone page document.
    """
    def __init__(self, manifest):
        header = _decode(manifest, 4096)
        self.header = _header(header, "CAPTURED")
        self.parts = []
        self.failed = False
        self.finished = False

    def add(self, raw):
        if self.failed or self.finished:
            raise MenuPagesUnavailable("capture already failed or finished")
        try:
            page = _decode(raw, 4096)
            if _header(page, "PAGE") != self.header:
                raise MenuPagesUnavailable("mixed capture or header")
            index = len(self.parts)
            expected = min(1000, self.header["total_bytes"] - index * 1000)
            if (not _integer(page["index"], 0, self.header["pages"] - 1) or page["index"] != index
                    or type(page["offset"]) is not int or page["offset"] != index * 1000
                    or type(page["length"]) is not int or page["length"] != expected
                    or not isinstance(page["hex"], str)
                    or not re.fullmatch("[0-9a-f]*", page["hex"])
                    or len(page["hex"]) != 2 * expected):
                raise MenuPagesUnavailable("missing, duplicate or malformed page")
            self.parts.append(bytes.fromhex(page["hex"]))
        except Exception as exc:
            self.failed = True
            if isinstance(exc, MenuPagesUnavailable):
                raise
            raise MenuPagesUnavailable("unreadable page") from exc

    def finish(self):
        if self.failed or self.finished:
            raise MenuPagesUnavailable("capture already failed or finished")
        self.finished = True
        try:
            if len(self.parts) != self.header["pages"]:
                raise MenuPagesUnavailable("capture incomplete")
            raw = b"".join(self.parts)
            if len(raw) != self.header["total_bytes"]:
                raise MenuPagesUnavailable("capture byte count mismatch")
            value, budget = _tree(_decode(raw.decode("utf-8"), 1048576))
            complete, counts = _observation(value)
            if (budget["nodes"] != self.header["total_nodes"]
                    or budget["strings"] != self.header["total_string_bytes"]
                    or value["age"] != self.header["captured_age"]
                    or complete != self.header["read_complete"] or counts != self.header["option_counts"]):
                raise MenuPagesUnavailable("source model differs from capture header")
            return {"capture": self.header["capture"], "capture_complete": True,
                    "read_complete": complete, "option_counts": counts,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "digest_scope": "HOST_REASSEMBLED_CANONICAL_BYTES",
                    "value": value, "atomic": False, "action_preparation_verified": False}
        except Exception as exc:
            self.failed = True
            if isinstance(exc, MenuPagesUnavailable):
                raise
            raise MenuPagesUnavailable("invalid reassembled source model") from exc

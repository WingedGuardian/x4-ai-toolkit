"""Bounded wire codec for registered live actions, not a permission grant.

Requests use a closed scalar argument grammar: printable ASCII strings, booleans
and finite numbers within the exact integer range of Lua doubles. Nested/null
arguments refuse. No Lua source, eval or automatic retries. The existing pipe
still applies its full-frame 1900-byte ceiling. This module is not wired to CLI
or installed helper yet.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from ._liveactions import (
    ActionRefused, ActionUncertain, request_digest, validate_request, validate_receipt,
)

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
VERBS = {"prepare": "action_prepare", "execute": "action_execute", "status": "action_status"}
MAX_PAYLOAD = 1400


def encode_request(request: dict[str, Any]) -> str:
    req = validate_request(request)
    args = []
    if len(req["arguments"]) > 24:
        raise ActionRefused("at most 24 scalar action arguments")
    for key, value in sorted(req["arguments"].items()):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ActionRefused("invalid action argument key")
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ActionRefused("wire string arguments must be printable ASCII")
            encoded = "s" + value.encode("ascii").hex()
        elif type(value) is bool:
            encoded = "b" + ("1" if value else "0")
        elif type(value) in (int, float):
            if abs(value) > 2**53 - 1 or not math.isfinite(value):
                raise ActionRefused("wire numbers must be finite and within safe Lua range")
            encoded = "n" + repr(value)
        else:
            raise ActionRefused("wire arguments must be scalar strings, booleans or numbers")
        args.append(key + "=" + encoded)
    packet = "|".join(("1", req["id"], req["session"], req["provider"], req["action"],
                       req["binding"], request_digest(req), ",".join(args)))
    if len(packet.encode("ascii")) > MAX_PAYLOAD:
        raise ActionRefused("encoded action exceeds 1400-byte wire bound")
    return packet


def decode_receipt(payload: str, request: dict[str, Any], *, preparing: bool = False) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate receipt key")
            result[key] = value
        return result

    try:
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > 65536:
            raise ValueError("receipt payload limit/type")
        result = json.loads(payload, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        if preparing and isinstance(result, dict) and result.get("status") == "preparing":
            # Validate the same identity/shape without publishing a prepared journal.
            check = dict(result, status="running")
            validate_receipt(check, request)
            return result
        return validate_receipt(result, request)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ActionUncertain("invalid or unbound action wire receipt") from exc


class PipeActionTransport:
    """One ask per invocation. Journal.execute owns mutation dispatch, never retry here."""
    def __init__(self, pipe):
        self.pipe = pipe

    def _ask(self, phase: str, request: dict) -> dict:
        packet = encode_request(request)
        reply = self.pipe.ask(VERBS[phase], packet)
        if reply.status != "OK":
            raise ActionUncertain("action pipe returned non-OK; reconcile without resending")
        return decode_receipt(reply.payload, request, preparing=(phase != "execute"))

    def prepare(self, request: dict) -> dict:
        return self._ask("prepare", request)

    def execute(self, request: dict) -> dict:
        return self._ask("execute", request)

    def status(self, request: dict) -> dict:
        return self._ask("status", request)


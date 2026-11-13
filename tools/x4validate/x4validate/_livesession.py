"""Host load identity; unwired foundation, not native persistence attestation.

The public MD provider must invalidate its nonce on EVERY load before accepting
requests. UI reload alone resumes it. A private fixture provider cannot substitute
for that public authority. Save-world lineage remains explicitly UNKNOWN.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable

PROVIDER = "x4-live-session-v1"
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_REV = re.compile(r"[0-9a-f]{64}\Z")
_DEC = re.compile(r"[1-9][0-9]{0,19}\Z")
_MAX_LOADS = 4096


class SessionUnavailable(RuntimeError):
    """No current identity is qualified; do not dispatch an action."""


def _token(value: Any, pattern=_HEX) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SessionUnavailable("invalid identity token")
    return value


def _peer(value: Any) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"pid", "creation_filetime", "image"}
            or type(value["pid"]) is not int or not 0 < value["pid"] < 2**32
            or not isinstance(value["creation_filetime"], str)
            or not _DEC.fullmatch(value["creation_filetime"])
            or int(value["creation_filetime"]) >= 2**64
            or not isinstance(value["image"], str)
            or not value["image"] or len(value["image"]) > 32768
            or "\x00" in value["image"]
            or value["image"].replace("\\", "/").split("/")[-1].casefold() != "x4.exe"):
        raise SessionUnavailable("unavailable or invalid X4 pipe peer identity")
    return dict(value)


def connected_pipe_peer(handle: Any) -> dict:
    """Read the *client* of our existing Windows named-pipe server handle.

    Microsoft GetNamedPipeClientProcessId/GetProcessTimes APIs; creation FILETIME
    is retained as integer text, never a rounded datetime. No input/game action.
    The caller must keep the same connected pipe handle throughout a handshake.
    """
    if os.name != "nt":
        raise SessionUnavailable("connected pipe identity is Windows-only")
    from ctypes import wintypes as W

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetNamedPipeClientProcessId.argtypes = [W.HANDLE, ctypes.POINTER(W.ULONG)]
    k.GetNamedPipeClientProcessId.restype = W.BOOL
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    k.GetProcessTimes.restype = W.BOOL
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    k.QueryFullProcessImageNameW.restype = W.BOOL
    k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    k.GetExitCodeProcess.restype = W.BOOL
    k.CloseHandle.argtypes = [W.HANDLE]
    k.CloseHandle.restype = W.BOOL
    pid = W.ULONG()
    if not k.GetNamedPipeClientProcessId(int(handle), ctypes.byref(pid)):
        raise SessionUnavailable("cannot identify connected pipe client")
    process = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not process:
        raise SessionUnavailable("cannot open pipe client for identity query")
    try:
        created, exited, kernel, user = (W.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(process, *[ctypes.byref(x) for x in (created, exited, kernel, user)]):
            raise SessionUnavailable("cannot read process creation FILETIME")
        image = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(image))
        if not k.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size)):
            raise SessionUnavailable("cannot read pipe client image")
        peer = _peer({"pid": pid.value, "creation_filetime": str(
            (created.dwHighDateTime << 32) | created.dwLowDateTime), "image": image.value})
        again = W.ULONG()
        exitcode = W.DWORD()
        if (not k.GetNamedPipeClientProcessId(int(handle), ctypes.byref(again))
                or again.value != pid.value
                or not k.GetExitCodeProcess(process, ctypes.byref(exitcode))
                or exitcode.value != 259):  # STILL_ACTIVE; other results refuse
            raise SessionUnavailable("pipe peer changed or exited during identity query")
        return peer
    finally:
        k.CloseHandle(process)


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SessionUnavailable("duplicate journal key")
        result[key] = value
    return result


class SessionJournal:
    """Exclusive, bounded load allocations. Preserve this directory across clients.

    Deleting/rolling back the journal defeats its monotonic-history guarantee.
    Fsync is not a portable directory-entry power-loss guarantee. Corrupt or
    incomplete records stop access; no repair, overwrite, pruning, or nonce reuse.
    """
    def __init__(self, path: Path):
        self.path = Path(path)

    def _root(self) -> None:
        # Reject links in the supplied route too: checking only the last element
        # would accept a journal below a junction. This is not a race-proof sandbox.
        for path in (self.path, *self.path.parents):
            if path.is_symlink() or path.is_junction():
                raise SessionUnavailable("linked load journal root or ancestor")
        if self.path.exists() and not self.path.is_dir():
            raise SessionUnavailable("load journal root is not a directory")

    def records(self) -> list[dict]:
        self._root()
        if not self.path.exists():
            return []
        paths = sorted(self.path.iterdir())
        if len(paths) > _MAX_LOADS:
            raise SessionUnavailable("load journal capacity exceeded")
        records, nonces = [], set()
        for index, path in enumerate(paths, 1):
            if path.name != f"{index:08d}.json" or not path.is_file() or path.is_symlink():
                raise SessionUnavailable("unexpected or missing load journal entry")
            try:
                with path.open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise SessionUnavailable("oversized load journal entry")
                row = json.loads(raw, object_pairs_hook=_unique)
            except (OSError, ValueError) as exc:
                raise SessionUnavailable("unreadable load journal entry") from exc
            if (not isinstance(row, dict) or set(row) != {"version", "generation", "session", "peer", "revision"}
                    or type(row["version"]) is not int or row["version"] != 1
                    or type(row["generation"]) is not int or row["generation"] != index):
                raise SessionUnavailable("invalid load allocation")
            _token(row["session"])
            _token(row["revision"], _REV)
            _peer(row["peer"])
            if row["session"] in nonces:
                raise SessionUnavailable("reused load nonce")
            nonces.add(row["session"])
            records.append(row)
        return records

    def allocate(self, peer: dict, revision: str, expected: list[dict]) -> dict:
        if self.records() != expected or len(expected) >= _MAX_LOADS:
            raise SessionUnavailable("load journal changed or full")
        nonce = secrets.token_hex(16)
        if nonce in {r["session"] for r in expected}:
            raise SessionUnavailable("nonce collision; no reuse permitted")
        row = {"version": 1, "generation": len(expected) + 1,
               "session": _token(nonce), "peer": _peer(peer), "revision": _token(revision, _REV)}
        self.path.mkdir(parents=True, exist_ok=True)
        self._root()
        try:
            with (self.path / f'{row["generation"]:08d}.json').open("xb") as stream:
                stream.write(_json(row) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise SessionUnavailable("allocation failed or concurrent allocation won; no handshake sent") from exc
        if self.records() != expected + [row]:
            raise SessionUnavailable("load journal changed after allocation")
        return row


class SessionLink:
    """Challenge-bound public handshake, with no automatic fresh/resume fallback.

    Exchange is synchronous and must use one connected transport; late replies
    cannot be fed into this API. Timeout/exception leaves identity unavailable.
    Injected callbacks are trusted transport code, never caller-supplied evidence.
    """
    def __init__(self, journal: SessionJournal, peer: Callable[[], dict],
                 exchange: Callable[[dict], dict], revision: str, ui_generation: str):
        self.journal, self.peer, self.exchange = journal, peer, exchange
        self.revision = _token(revision, _REV)
        self.ui_generation = _token(ui_generation)
        self._ready = self._probe = None
        self._handshake = None

    def _call(self, mode: str, session: str) -> tuple[dict, dict]:
        self._ready = None
        self._handshake = None
        try:
            before = _peer(self.peer())
            request = {"mode": mode, "session": session, "challenge": secrets.token_hex(16),
                       "generation": self.ui_generation}
            reply = self.exchange(dict(request))
            after = _peer(self.peer())
            # The helper answers a fault or timeout with {kind: UNAVAILABLE, reason: ...}. Run-03 (2026-11-10)
            # reported that only as "malformed", losing the helper's reason; surface it, still a refusal.
            if (isinstance(reply, dict) and set(reply) == {"kind", "reason"} and reply["kind"] == "UNAVAILABLE"
                    and isinstance(reply["reason"], str)):
                raise SessionUnavailable("helper session unavailable: " + reply["reason"][:300])
            keys = {"kind", "provider", "revision", "mode", "challenge", "generation", "session",
                    "has_session", "reason", "age", "world_token"}
            if (before != after or not isinstance(reply, dict) or set(reply) != keys
                    or reply["provider"] != PROVIDER or reply["revision"] != self.revision
                    or any(reply[k] != request[k] for k in ("mode", "challenge", "generation"))
                    or type(reply["has_session"]) is not bool
                    or not isinstance(reply["reason"], str) or len(reply["reason"]) > 4096
                    or type(reply["age"]) not in (int, float) or not math.isfinite(reply["age"])
                    or reply["age"] < 0 or not isinstance(reply["world_token"], str)
                    or len(reply["world_token"]) > 128
                    or (reply["has_session"] and reply["world_token"] == "")
                    or not isinstance(reply["session"], str)
                    or reply["has_session"] != bool(reply["session"])):
                raise SessionUnavailable("unbound or malformed public session response")
            if reply["session"]:
                _token(reply["session"])
            if (reply["kind"] != ("STATE" if mode == "probe" else "READY")
                    or (mode != "probe" and reply["session"] != session)):
                raise SessionUnavailable("public session handshake refused or not ready")
            return json.loads(_json(reply)), before
        except SessionUnavailable:
            raise
        except Exception as exc:
            raise SessionUnavailable("public session handshake unavailable; no automatic retry") from exc

    def probe(self) -> object:
        self._ready = self._probe = None
        history = self.journal.records()
        state, peer = self._call("probe", "")
        if self.journal.records() != history:
            raise SessionUnavailable("journal changed during public probe")
        self._probe = object()
        self._state, self._peer, self._history = state, peer, history
        return self._probe

    def _consume(self, probe: object) -> None:
        self._ready = None
        self._handshake = None
        if probe is not self._probe or probe is None:
            raise SessionUnavailable("stale or foreign public probe")
        self._probe = None
        if _peer(self.peer()) != self._peer or self.journal.records() != self._history:
            raise SessionUnavailable("peer or journal changed since public probe")

    def fresh(self, probe: object) -> dict:
        self._consume(probe)
        if self._state["has_session"]:
            raise SessionUnavailable("fresh requires authoritative absent session")
        row = self.journal.allocate(self._peer, self.revision, self._history)
        return self._connect("fresh", row)

    def resume(self, probe: object) -> dict:
        self._consume(probe)
        if not self._state["has_session"] or not self._history:
            raise SessionUnavailable("resume requires an existing durable session")
        row = self._history[-1]
        if (row["session"] != self._state["session"] or row["peer"] != self._peer
                or row["revision"] != self.revision):
            raise SessionUnavailable("public session is not the latest durable allocation")
        return self._connect("resume", row)

    def _connect(self, mode: str, row: dict) -> dict:
        reply, peer = self._call(mode, row["session"])
        rows = self.journal.records()
        if peer != row["peer"] or not rows or rows[-1] != row:
            raise SessionUnavailable("load identity changed before readiness")
        self._ready = {"provider": PROVIDER, "revision": self.revision,
                       "session": row["session"], "load_generation": row["generation"],
                       "ui_generation": self.ui_generation, "peer": peer,
                       "world_token": reply["world_token"], "world_lineage": "UNKNOWN"}
        identity = self.identity()
        # _call already matched these request fields and both measured peers;
        # publish only after the durable allocation and final identity checks.
        self._handshake = {
            "version": 1, "scope": "VERIFIED_EXCHANGE_RECORD_NOT_FRESH_NATIVE_STATE",
            "origin": "SESSIONLINK_TRUSTED_TRANSPORT_CALLBACK",
            "identity": identity,
            "request": {k: reply[k] for k in ("mode", "session", "challenge", "generation")},
            "reply": reply,
            "peer_before": peer, "peer_after": peer,
            "native_age_domain": "MD_PLAYER_AGE",
            "world_lineage": "UNKNOWN", "cryptographic_attestation": False,
        }
        self._handshake = json.loads(_json(self._handshake))
        return identity

    def handshake_receipt(self) -> dict:
        """Copy the last verified fresh/resume exchange; send no native query.

        This is a trusted-callback record, not a MACHINE tier assertion: synthetic
        transports remain synthetic. Current peer/journal checks do not replace
        a fresh native stamp. The next probe/connection attempt invalidates it.
        """
        identity = self.identity()
        if self._handshake is None or self._handshake["identity"] != identity:
            raise SessionUnavailable("verified handshake record unavailable")
        return json.loads(_json(self._handshake))

    def identity(self) -> dict:
        if self._ready is None:
            raise SessionUnavailable("public session is not ready")
        try:
            rows = self.journal.records()
            if (not rows or rows[-1]["session"] != self._ready["session"]
                    or rows[-1]["generation"] != self._ready["load_generation"]
                    or rows[-1]["revision"] != self.revision
                    or rows[-1]["peer"] != self._ready["peer"]
                    or _peer(self.peer()) != self._ready["peer"]):
                raise SessionUnavailable("public load identity changed")
            return json.loads(_json(self._ready))
        except Exception as exc:
            self._ready = None
            self._handshake = None
            if isinstance(exc, SessionUnavailable):
                raise
            raise SessionUnavailable("public load identity unavailable") from exc

    def verify_reply(self, identity: Any) -> dict:
        """Validate a reply's identity envelope, not its payload or native effect.

        The helper must check current MD identity before EVERY reply/action. This
        host cannot discover a save load from an unchanged process ID alone.
        """
        expected = self.identity()
        # The game knows these five fields. PID/start and load generation are
        # measured host facts, never supplied as purported native observations.
        stamp = {"provider": expected["provider"], "revision": expected["revision"],
                 "session": expected["session"], "generation": expected["ui_generation"],
                 "world_token": expected["world_token"]}
        try:
            matches = isinstance(identity, dict) and _json(identity) == _json(stamp)
        except (ValueError, TypeError, RecursionError):
            matches = False
        if not matches:
            self._ready = None
            self._handshake = None
            raise SessionUnavailable("reply belongs to a different or unqualified session")
        return expected

"""Durable single-dispatch journal for explicit live actions.

Infrastructure only: this module neither grants permission nor implements a game
action. Adapters must bind real preparation receipts to an actual world/session,
revalidate before mutating, and independently measure the outcome. Synthetic
receipts never qualify native behavior. A lost reply consumes the dispatch.
At most one dispatch is enforced per preserved journal, not exactly-once game
effects or portable power-loss durability. File fsync does not prove directory
entry durability on every filesystem.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

_VERSION = 1
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_STATES = {"prepared", "running", "completed", "refused", "failed", "unknown"}
_TERMINAL = {"completed", "refused", "failed"}
_MAX_HISTORY = 2048


class ActionRefused(ValueError):
    """No dispatch was made by this attempt."""


class ActionUncertain(RuntimeError):
    """Dispatch may have taken effect; never automatically retry."""


class ActionTransport(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def status(self, request: dict[str, Any]) -> dict[str, Any]: ...


def _json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False, ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ActionRefused("action data must be bounded, finite JSON") from exc


def validate_request(request: Any) -> dict[str, Any]:
    keys = {"version", "id", "session", "provider", "action", "arguments", "binding"}
    if not isinstance(request, dict) or set(request) != keys:
        raise ActionRefused("unexpected or missing action request fields")
    if type(request["version"]) is not int or request["version"] != _VERSION:
        raise ActionRefused("unsupported action request version")
    if not isinstance(request["id"], str) or not _ID.fullmatch(request["id"]):
        raise ActionRefused("request id must be 32 lowercase hex characters")
    for name in ("session", "provider", "action", "binding"):
        value = request[name]
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise ActionRefused(f"invalid {name} token")
    if not isinstance(request["arguments"], dict):
        raise ActionRefused("arguments must be a JSON object")
    encoded = _json(request)
    # The live transport's full-frame limit is 1900 bytes. Adapters may need a
    # smaller bound for their encoding and MUST still use encode_command's cap.
    if len(encoded) > 1400:
        raise ActionRefused("action request exceeds 1400-byte journal contract")
    return json.loads(encoded)


def request_digest(request: Any) -> str:
    return hashlib.sha256(_json(validate_request(request))).hexdigest()


def validate_receipt(receipt: Any, request: dict[str, Any]) -> dict[str, Any]:
    keys = {"version", "id", "session", "digest", "status", "observations", "reason"}
    if not isinstance(receipt, dict) or set(receipt) != keys:
        raise ActionUncertain("missing or unexpected receipt fields")
    if (type(receipt["version"]) is not int or receipt["version"] != _VERSION
            or receipt["id"] != request["id"]
            or receipt["session"] != request["session"]
            or receipt["digest"] != request_digest(request)):
        raise ActionUncertain("receipt is not bound to this exact prepared request")
    if (not isinstance(receipt["status"], str)
            or receipt["status"] not in _STATES
            or not isinstance(receipt["observations"], dict)
            or not isinstance(receipt["reason"], str)):
        raise ActionUncertain("invalid action receipt types or status")
    try:
        encoded = _json(receipt)
    except ActionRefused as exc:
        raise ActionUncertain("invalid receipt JSON") from exc
    if len(encoded) > 65536:
        raise ActionUncertain("receipt exceeds bounded observation contract")
    return json.loads(encoded)


def _write_new(path: Path, value: Any) -> None:
    """Exclusive, flushed creation. An incomplete file is evidence, never reusable."""
    data = _json(value)
    with path.open("xb") as stream:
        stream.write(data)
        stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_json(path: Path) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    if path.is_symlink():
        raise ValueError("journal member cannot be a symlink")
    with path.open("rb") as stream:
        data = stream.read(131073)
    if len(data) > 131072:
        raise ValueError("journal member exceeds size bound")
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


class ActionJournal:
    """One directory per immutable request; no deletion, retry or overwrite path."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ActionRefused("journal directory cannot be a symlink")
        prepared_path = self.path / "prepared.json"
        if prepared_path.is_symlink():
            raise ActionRefused("prepared receipt cannot be a symlink")
        try:
            prepared = _read_json(prepared_path)
            if set(prepared) != {"request", "receipt"}:
                raise ValueError("unexpected prepared journal fields")
            self.request = validate_request(prepared["request"])
            self.preparation = validate_receipt(prepared["receipt"], self.request)
            if self.path.name != self.request["id"]:
                raise ValueError("journal directory and request id differ")
            if self.preparation["status"] != "prepared":
                raise ValueError("request was not prepared")
        except (OSError, ValueError, KeyError, TypeError, ActionUncertain) as exc:
            raise ActionRefused("invalid/incomplete prepared journal; preserve it") from exc

    @classmethod
    def create(cls, root: Path, request: Any, receipt: Any) -> ActionJournal:
        request = validate_request(request)
        receipt = validate_receipt(receipt, request)
        if receipt["status"] != "prepared":
            raise ActionRefused("only an actual prepared receipt can create a journal")
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            raise ActionRefused("journal root must be an existing non-symlink directory")
        directory = root / request["id"]
        try:
            directory.mkdir()  # existing or partially-created request is never replaced
        except FileExistsError as exc:
            raise ActionRefused("request id already has a journal; reconcile it") from exc
        _write_new(directory / "prepared.json", {"request": request, "receipt": receipt})
        return cls(directory)

    def _reload(self) -> dict[str, Any]:
        current = ActionJournal(self.path)
        if current.request != self.request or current.preparation != self.preparation:
            raise ActionRefused("prepared journal changed; refusing dispatch")
        return current.request

    def _history(self) -> list[dict[str, Any]]:
        try:
            marker = self.path / "dispatch.json"
            if marker.exists() or marker.is_symlink():
                if _read_json(marker) != {
                    "id": self.request["id"], "digest": request_digest(self.request)
                }:
                    raise ValueError("dispatch marker does not bind prepared request")
            if any(self.path.glob("contradiction-*.json")):
                raise ValueError("contradictory outcome requires review")
            rows = sorted(self.path.glob("observation-*.json"))
            if len(rows) > _MAX_HISTORY:
                raise ValueError("observation history exceeds bound")
            result = []
            terminal = None
            for index, path in enumerate(rows, 1):
                if path.name != f"observation-{index:06d}.json":
                    raise ValueError("observation history is discontinuous")
                row = validate_receipt(_read_json(path), self.request)
                if terminal is not None and row["status"] != terminal:
                    raise ValueError("terminal history is contradictory")
                if row["status"] in _TERMINAL:
                    terminal = row["status"]
                result.append(row)
            if not marker.exists() and any(row["status"] != "prepared" for row in result):
                raise ValueError("execution evidence without its dispatch marker")
            return result
        except (OSError, ValueError, TypeError) as exc:
            raise ActionUncertain("journal evidence is corrupt or inconsistent; preserve it") from exc

    def _record(self, receipt: dict[str, Any]) -> None:
        # Append-only files retain every observation. Exclusive creation detects
        # concurrent writers rather than overwriting one writer's evidence.
        receipt = validate_receipt(receipt, self.request)
        rows = self._history()
        if not (self.path / "dispatch.json").exists() and receipt["status"] != "prepared":
            _write_new(self.path / "contradiction-000001.json",
                       {"previous": "no dispatch marker", "received": receipt})
            raise ActionUncertain("execution receipt without dispatch; preserve contradiction")
        if len(rows) >= _MAX_HISTORY:
            raise ActionUncertain("observation history limit reached")
        if rows:
            previous = rows[-1]
            if previous["status"] in _TERMINAL and receipt["status"] != previous["status"]:
                _write_new(self.path / "contradiction-000001.json",
                           {"previous": previous, "received": receipt})
                raise ActionUncertain("terminal outcome changed; preserve contradictory evidence")
        try:
            _write_new(self.path / f"observation-{len(rows) + 1:06d}.json", receipt)
        except FileExistsError as exc:
            # Preserve the losing receipt too: the winning observation alone may
            # falsely appear complete. Any contradiction marker blocks later reads.
            _write_new(self.path / f"contradiction-race-{uuid.uuid4().hex}.json",
                       {"previous": "concurrent observation filename collision",
                        "received": receipt})
            raise ActionUncertain("concurrent observation; reconcile journal") from exc

    def execute(self, transport: ActionTransport) -> dict[str, Any]:
        request = self._reload()
        self._history()
        try:
            _write_new(self.path / "dispatch.json",
                       {"id": request["id"], "digest": request_digest(request)})
        except FileExistsError as exc:
            raise ActionRefused("dispatch already reserved; use status, never retry") from exc
        # The durable marker PRECEDES the only mutating transport call. A crash
        # between them is deliberately unknown, not evidence that execution failed.
        try:
            receipt = validate_receipt(transport.execute(json.loads(_json(request))), request)
            if receipt["status"] == "prepared":
                raise ActionUncertain("execution returned preparation, not an outcome")
            self._record(receipt)
            return receipt
        except Exception as exc:
            raise ActionUncertain(
                "dispatch outcome requires reconciliation; request will not be resent"
            ) from exc

    def observe(self, transport: ActionTransport) -> dict[str, Any]:
        request = self._reload()
        self._history()
        receipt = validate_receipt(transport.status(json.loads(_json(request))), request)
        if (self.path / "dispatch.json").exists() and receipt["status"] == "prepared":
            raise ActionUncertain("prepared status cannot erase a reserved dispatch")
        self._record(receipt)
        return receipt

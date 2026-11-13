"""Public session-bound pipe envelopes. Legacy replies cannot qualify a campaign.

The caller establishes SessionLink through this exact connected pipe and binds
the expected helper build to its verified deployment. No game launch, retry,
implicit fresh/resume, profile edits or action permissions are provided here.
"""
from __future__ import annotations
import json
import copy
import math
import re
from ._livesession import SessionUnavailable
from ._livepipe import Reply


def _decode(payload):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate field")
            out[key] = value
        return out
    try:
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > 65536:
            raise ValueError("payload limit")
        return json.loads(payload, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SessionUnavailable("malformed bound response") from exc


def session_exchange(pipe, request, *, ui_proof):
    """Single request; SessionLink validates exact challenge/revision/identity."""
    if set(request) != {"mode", "session", "challenge", "generation"}:
        raise SessionUnavailable("invalid session exchange keys")
    if not isinstance(ui_proof, str) or not re.fullmatch("[0-9a-f]{32}",ui_proof):
        raise SessionUnavailable("UI instance proof required")
    reply = pipe.ask("session_connect", ui_proof, *(request[k] for k in
        ("mode", "session", "challenge", "generation")))
    if reply.status != "OK":
        raise SessionUnavailable("session exchange refused; no retry")
    return _decode(reply.payload)


class BoundPipe:
    """One ask, checked before and after against the same SessionLink/pipe peer.

    After any transport/identity/encoding failure this object is poisoned. An
    action may already have happened; use the preserved action journal for
    status-only reconciliation through a newly qualified connection.
    """
    def __init__(self, pipe, link, expected_build):
        if not isinstance(expected_build, str) or not re.fullmatch("[0-9a-f]{8,64}", expected_build):
            raise SessionUnavailable("expected helper build required")
        self.pipe, self.link, self.build = pipe, link, expected_build
        self.failed = False
        self.last_envelope = None
        self.last_age = None
        self._read_trace = None

    def read_trace(self):
        """Closed read-only trace; collection is not source/gameplay qualification."""
        return _ReadTrace(self)

    def ask(self, verb, *args):
        if self.failed:
            raise SessionUnavailable("bound connection is unqualified after earlier failure")
        self.last_envelope = None
        try:
            if self._read_trace is not None:
                self._read_trace.check(verb,args)
            before = self.link.identity()
            reply = self.pipe.ask("bound", before["session"], before["ui_generation"],
                                  before["revision"], before["world_token"], self.build, verb, *args)
            if reply.status != "OK":
                raise SessionUnavailable("bound command refused or uncertain; do not resend")
            value = _decode(reply.payload)
            if (not isinstance(value, dict)
                    or set(value) != {"version", "stamp", "build", "age", "status", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["build"] != self.build
                    or type(value["age"]) not in (int, float)
                    or not math.isfinite(value["age"]) or value["age"] < 0
                    or value["status"] not in ("OK", "ABSENT", "ERR")
                    or not isinstance(value["payload"], str)):
                raise SessionUnavailable("invalid or wrong-build bound envelope")
            after = self.link.verify_reply(value["stamp"])
            if before != after:
                raise SessionUnavailable("host identity changed during command")
            if self.last_age is not None and value["age"] < self.last_age:
                raise SessionUnavailable("game age rolled back within a qualified load")
            self.last_age = value["age"]
            self.last_envelope = value
            if self._read_trace is not None:
                self._read_trace.append(verb,args,before,after,reply)
            return Reply(reply.seq, value["status"], value["payload"])
        except Exception as exc:
            self.failed = True
            if self._read_trace is not None:
                self._read_trace.failed = True
            if isinstance(exc, SessionUnavailable):
                raise
            raise SessionUnavailable("bound command unavailable; action outcome may be unknown") from exc


class _ReadTrace:
    """At most one capture header plus 32 pages; never permits an action."""
    def __init__(self,bound):
        self.bound=bound;self.frames=[];self.bytes=0;self.closed=False;self.failed=False

    def __enter__(self):
        if self.bound._read_trace is not None or self.closed or self.bound.failed:
            raise SessionUnavailable("nested/reused/unqualified read trace")
        self.bound._read_trace=self
        return self

    def check(self,verb,args):
        if (self.failed or verb not in ("read_capture","read_page") or len(self.frames)>=33
                or len(args)>18 or any(not isinstance(a,str) or len(a.encode("utf-8"))>4096 for a in args)):
            self.failed=True
            raise SessionUnavailable("read trace scope/capacity refused before dispatch")

    def append(self,verb,args,before,after,reply):
        size=len(reply.payload.encode("utf-8"))
        if self.bytes+size>33*65536:
            self.failed=True
            raise SessionUnavailable("raw read trace byte capacity")
        self.bytes+=size
        self.frames.append(copy.deepcopy(dict(verb=verb,args=list(args),before=before,after=after,
            reply=dict(seq=reply.seq,status=reply.status,raw=reply.payload))))

    def __exit__(self,kind,value,tb):
        self.closed=True
        self.failed=self.failed or kind is not None or self.bound._read_trace is not self
        if self.bound._read_trace is self:self.bound._read_trace=None

    def snapshot(self):
        if not self.closed or self.failed or not self.frames:
            raise SessionUnavailable("read trace incomplete/unqualified")
        return copy.deepcopy(self.frames)


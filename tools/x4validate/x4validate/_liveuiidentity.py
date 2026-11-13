"""Host UI-instance allocation, correlated to a volatile native bootstrap ticket.

Uses SessionJournal's exclusive append-only allocation storage in a SEPARATE
ui-generations directory. Its internal 'session' column holds a UI nonce here;
public results call it generation. This is not a world/load identity or lineage.
"""
from __future__ import annotations
import secrets
from ._livesession import SessionJournal, SessionUnavailable, _peer, _token, _REV
from ._livebound import _decode


class UILink:
    def __init__(self, pipe, journal: SessionJournal, peer, expected_build: str):
        self.pipe, self.journal, self.peer = pipe, journal, peer
        self.build = _token(expected_build, _REV)
        self.generation = self.proof = None
        self._row = self._peer = None

    def probe(self):
        self.generation = self.proof = None
        history = self.journal.records()
        before = _peer(self.peer())
        challenge = secrets.token_hex(16)
        try:
            reply = self.pipe.ask("ui_state", challenge)
            if reply.status != "OK":
                raise SessionUnavailable("UI bootstrap unavailable")
            value = _decode(reply.payload)
            if (not isinstance(value, dict) or set(value) != {"kind","challenge","generation","build"}
                    or value["kind"] != "UI_STATE" or value["challenge"] != challenge
                    or value["build"] != self.build or not isinstance(value["generation"], str)
                    or _peer(self.peer()) != before or self.journal.records() != history):
                raise SessionUnavailable("unbound UI bootstrap response")
            if value["generation"]:
                nonce = _token(value["generation"])
                if (not history or history[-1]["session"] != nonce
                        or history[-1]["peer"] != before or history[-1]["revision"] != self.build):
                    raise SessionUnavailable("active UI generation is not our latest durable allocation")
                row = history[-1]
            else:
                # Reserve before first handshake; failed/ambiguous allocations never reuse a nonce.
                row = self.journal.allocate(before, self.build, history)
            self._row,self._peer = row,before
            self.generation,self.proof = row["session"],challenge
            return {"generation":self.generation,"sequence":row["generation"],
                    "build":self.build,"peer":before}
        except Exception as exc:
            self.generation = self.proof = None
            if isinstance(exc, SessionUnavailable):
                raise
            raise SessionUnavailable("UI qualification unavailable; no retry") from exc

    def exchange(self, request):
        try:
            if (self.proof is None or self.generation is None
                    or not isinstance(request, dict)
                    or set(request) != {"mode","session","challenge","generation"}
                    or request["generation"] != self.generation):
                raise SessionUnavailable("missing or mismatched UI qualification")
            rows = self.journal.records()
            if not rows or rows[-1] != self._row or _peer(self.peer()) != self._peer:
                raise SessionUnavailable("UI peer or journal changed")
            reply = self.pipe.ask("session_connect", self.proof, *(request[k] for k in
                ("mode","session","challenge","generation")))
            if reply.status != "OK":
                raise SessionUnavailable("UI ticket/session handshake refused")
            if _peer(self.peer()) != self._peer or self.journal.records() != rows:
                raise SessionUnavailable("UI identity changed during handshake")
            return _decode(reply.payload)
        except Exception as exc:
            self.generation = self.proof = None
            if isinstance(exc, SessionUnavailable):
                raise
            raise SessionUnavailable("session exchange uncertain; no retry") from exc


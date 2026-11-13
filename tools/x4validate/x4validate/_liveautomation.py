"""Qualified automation client with durable preparation and no implicit retries."""
from __future__ import annotations
import json
from pathlib import Path
import re
import secrets
from ._liveactions import (
    ActionJournal, ActionRefused, validate_request, _read_json, _write_new,
)
from ._liveactionwire import PipeActionTransport
from ._livebound import BoundPipe
from ._livesession import SessionJournal, SessionLink, SessionUnavailable
from ._liveuiidentity import UILink
from ._livepeerprofile import ProfilePeer
from ._livereadpages import query_capture
from ._livepipe import Reply
from ._livetrees import journal_refusal


def _directory(path, *, create=False):
    path=Path(path)
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():
            raise ActionRefused("linked automation journal route refused")
    if create:
        path.mkdir(exist_ok=True)
    if not path.is_dir():
        raise ActionRefused("existing automation journal directory required")
    return path


def _id(value):
    if not isinstance(value,str) or not re.fullmatch("[0-9a-f]{32}",value):
        raise ActionRefused("32-hex request id required")
    return value


class AutomationClient:
    """The constructor accepts a trusted BoundPipe; production uses connect()."""
    def __init__(self,bound,root):
        self.bound=bound
        self.root=_directory(root)
        why=journal_refusal(self.root)
        if why:raise ActionRefused(why)
        self.pending=_directory(self.root/"requests",create=True)
        self.actions=_directory(self.root/"actions",create=True)
        self.transport=PipeActionTransport(bound)
        self.last_read=None
        self._native_connection=False

    @classmethod
    def connect(cls,pipe,root,*,image,profile_id,build,revision,mode):
        if mode not in ("fresh","resume"):
            raise SessionUnavailable("explicit fresh or resume mode required")
        root=_directory(root)
        # A journal never overlaps the game install, the reference folder or the
        # personal tree (every profile and its saves); checked before the pipe is used.
        overlap=journal_refusal(root,image=image)
        if overlap:
            raise ActionRefused(overlap)
        peer=ProfilePeer(pipe._h,image,profile_id)
        # Qualification uses the same connected pipe handle, never a process list guess.
        ui=UILink(pipe,SessionJournal(root/"ui-generations"),peer,build)
        ui_identity=ui.probe()
        link=SessionLink(SessionJournal(root/"loads"),peer,ui.exchange,revision,
                         ui_identity["generation"])
        probe=link.probe()
        getattr(link,mode)(probe)  # explicit mode; never falls back to the other
        client=cls(BoundPipe(pipe,link,build),root)
        client._native_connection=True
        return client

    def identity(self):
        return self.bound.link.identity()

    def _request(self,request):
        request=validate_request(request)
        if request["session"]!=self.identity()["session"]:
            raise ActionRefused("action belongs to another load")
        return request

    def _pending_request(self,request_id):
        request_id=_id(request_id)
        req=self._request(_read_json(self.pending/(request_id+".json")))
        if req["id"]!=request_id:
            raise ActionRefused("pending request identity mismatch")
        return req

    @staticmethod
    def _provider_access(request,access):
        if request.get("provider")=="x4-persistence-v1":
            from ._livepersistport import _valid_access
            if not _valid_access(access):
                raise ActionRefused("persistence requires its trusted in-process dispatcher")

    def prepare(self,request,*,_persistence_access=None):
        request=self._request(request)
        self._provider_access(request,_persistence_access)
        path=self.pending/(request["id"]+".json")
        try:
            _write_new(path,request)  # before the first asynchronous preparation call
        except FileExistsError as exc:
            raise ActionRefused("request already reserved; use status") from exc
        receipt=self.transport.prepare(request)
        if receipt["status"]=="prepared":
            ActionJournal.create(self.actions,request,receipt)
        return receipt

    def execute(self,request_id,*,_persistence_access=None):
        req=self._pending_request(request_id)
        self._provider_access(req,_persistence_access)
        journal=ActionJournal(self.actions/req["id"])
        if journal.request!=req:
            raise ActionRefused("prepared and pending requests disagree")
        return journal.execute(self.transport)

    def status(self,request_id,*,_persistence_access=None):
        req=self._pending_request(request_id)
        self._provider_access(req,_persistence_access)
        directory=self.actions/req["id"]
        if directory.exists():
            journal=ActionJournal(directory)
            if journal.request!=req:
                raise ActionRefused("prepared and pending requests disagree")
            return journal.observe(self.transport)
        receipt=self.transport.status(req) # reconcile lost preparation, never resend prepare
        if receipt["status"]=="prepared":
            ActionJournal.create(self.actions,req,receipt)
        return receipt

    def query(self,verb,*args):
        # The Lua router's exact read whitelist remains authoritative.
        if verb.startswith("action_") or verb in ("bound","session_connect","ui_state","pause","unpause","read_capture","read_page"):
            raise ActionRefused("dedicated action/session operation required")
        self.last_read=None
        nonce=secrets.token_hex(16)
        reply,self.last_read=query_capture(self.bound,nonce,verb,args)
        return reply

    def output(self,**values):
        metadata=None if self.last_read is None else {k:v for k,v in self.last_read.items() if k!="payload"}
        return dict(identity=self.identity(),envelope=self.bound.last_envelope,read_capture=metadata,**values)


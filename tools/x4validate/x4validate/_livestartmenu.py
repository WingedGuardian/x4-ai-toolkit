"""UI-only native new-game client; never launches a process or invents a world session.

Conversation authorization is policy evidence supplied by the trusted agent
dispatcher, not cryptographic human attestation. Packet booleans/CLI flags cannot
mint an opaque capability. No actual game calls occur on import.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
from ._liveactions import _json, _read_json, _write_new
from ._liveactionwire import encode_request
from ._liveautomation import _directory
from ._livebound import _decode
from ._livepeerprofile import ProfilePeer
from ._livepipe import LivePipe, _win32
from ._livesession import SessionJournal, SessionUnavailable, _peer
from ._livetrees import journal_refusal

PROVIDER="x4-ui-startmenu-v1"
SCOPE="UI_ONLY_NO_WORLD"

def require(ok,why):
    if not ok:raise SessionUnavailable(why)

def token(value,n):
    require(isinstance(value,str) and re.fullmatch("[0-9a-f]{"+str(n)+"}",value) is not None,"invalid identity token")
    return value

def digest(value):
    return hashlib.sha256(_json(value)).hexdigest()

class UIStartPipe(LivePipe):
    """Actual fixed named-pipe transport; no automatic connect/retry after failure."""
    def __init__(self,timeout=10.0):
        super().__init__(name="x4live_startmenu",timeout=timeout)
        self.failed=False

    def wait_for_game(self,timeout=None):
        pipes,_,errors=_win32();deadline=time.monotonic()+(self.timeout if timeout is None else timeout)
        while True:
            try:
                pipes.ConnectNamedPipe(self._h,None);self._connected=True;return
            except Exception as exc:
                code=getattr(exc,"winerror",None)
                if code==errors.ERROR_PIPE_CONNECTED:self._connected=True;return
                if code not in (errors.ERROR_PIPE_LISTENING,errors.ERROR_NO_DATA):
                    self.failed=True
                    raise SessionUnavailable("dedicated UI pipe connection unavailable") from exc
            if time.monotonic()>=deadline:
                self.failed=True
                raise SessionUnavailable("no connection to dedicated UI pipe; pre-world loading is not proved")
            time.sleep(0.05)

    def _read_raw(self,deadline):
        _,files,errors=_win32()
        while True:
            try:
                error,data=files.ReadFile(self._h,4097)
                require(error==0,"UI read incomplete; outcome unknown")
                return data
            except Exception as exc:
                if getattr(exc,"winerror",None) not in (errors.ERROR_NO_DATA,errors.ERROR_PIPE_LISTENING):
                    raise SessionUnavailable("dedicated UI reply unavailable; outcome unknown") from exc
            if time.monotonic()>=deadline:
                raise SessionUnavailable("dedicated UI pipe sent no complete reply; outcome unknown")
            time.sleep(0.02)

    def exchange(self,packet):
        require(not self.failed and self._connected,"UI pipe not connected or previously failed")
        require(isinstance(packet,str) and 0<len(packet.encode("ascii"))<=1400,"UI packet bound")
        try:
            _,files,_=_win32()
            data=packet.encode("ascii")
            error,written=files.WriteFile(self._h,data)
            require(error==0 and written==len(data),"UI write incomplete; outcome unknown")
            raw=self._read_raw(time.monotonic()+self.timeout)
            require(isinstance(raw,bytes) and 0<len(raw)<=4096,"UI reply byte limit")
            return raw.decode("utf-8",errors="strict")
        except Exception as exc:
            self.failed=True
            raise SessionUnavailable("UI exchange uncertain; never automatically resend") from exc

class ConversationAuthority:
    """Trusted agent-side policy callback; no authority is inferred from caller JSON.

    decide(context) must consult the actual applicable conversation approval.
    Returns exact source/reference/scope/context_sha256 policy evidence. This
    module validates scope/correlation, not the truth of the human conversation.
    """
    def __init__(self,root,decide,*,clock=time.monotonic):
        self.root=_directory(root);require(callable(decide) and callable(clock),"trusted decision source required")
        why=journal_refusal(self.root);require(why is None,"authority journal overlap: "+str(why))
        self.decide,self.clock=decide,clock
        self.records={};self.last=None

    def _now(self):
        value=self.clock()
        require(type(value) in (int,float) and 0<=value<=2**53-1 and
                (self.last is None or value>=self.last),"invalid authority clock")
        self.last=value;return value

    def issue(self,context):
        now=self._now();context=json.loads(_json(context));wanted=digest(context)
        require(len(self.records)<128,"authorization capacity")
        decision=self.decide(copy.deepcopy(context))
        require(isinstance(decision,dict) and set(decision)=={"source","reference","scope","context_sha256"} and
                decision["source"]=="AGENT_RECORDED_CONVERSATION" and
                decision["scope"]=="NEW_WORLD_IN_APPROVED_RUNNING_DISPOSABLE_CAMPAIGN" and
                decision["context_sha256"]==wanted and isinstance(decision["reference"],str) and
                1<=len(decision["reference"])<=4096,"applicable conversation authorization unavailable")
        nonce=secrets.token_hex(16)
        evidence={"version":1,"capability":nonce,"context":context,"context_sha256":wanted,
            "decision":decision,"issued_monotonic":now,"human_attestation":"NOT_CRYPTOGRAPHICALLY_VERIFIED"}
        path=self.root/(nonce+"-issued.json");_write_new(path,evidence)
        opaque=object();self.records[opaque]=(evidence,path,digest(evidence),False)
        return opaque

    def consume(self,opaque,context):
        require(type(opaque) is object and opaque in self.records,"foreign/serialized authorization")
        evidence,path,sha,used=self.records[opaque]
        require(not used and self._now()-evidence["issued_monotonic"]<=30,"authorization consumed/expired")
        require(digest(_read_json(path))==sha and evidence["context_sha256"]==digest(context),"authorization scope/evidence drift")
        # Irrevocable before the dispatcher can send any native Start request.
        self.records[opaque]=(evidence,path,sha,True)
        _write_new(self.root/(evidence["capability"]+"-consumed.json"),
                   {"capability":evidence["capability"],"context_sha256":digest(context)})
        return copy.deepcopy(evidence)

class StartClient:
    """Constructor requires trusted connected pipe/peer; connect() supplies ProfilePeer."""
    def __init__(self,pipe,root,peer,profile_id,build,authority,*,desktop,focus):
        self.root=_directory(root)
        why=journal_refusal(self.root);require(why is None,"start journal overlap: "+str(why))
        self.requests=_directory(self.root/"requests",create=True)
        self.allocations=SessionJournal(self.root/"ui-generations")
        require(callable(peer) and isinstance(profile_id,str) and re.fullmatch("[0-9]{1,20}",profile_id),
                "qualified connected profile required")
        require(isinstance(authority,ConversationAuthority),"trusted conversation authority required")
        for v in (desktop,focus):require(isinstance(v,str) and 0<len(v)<=512,"explicit desktop/focus context required")
        self.pipe,self.peer,self.profile,self.build=pipe,peer,profile_id,token(build,64)
        self.authority,self.desktop,self.focus=authority,desktop,focus
        self.ready=None;self.failed=False;self.last_envelope=None

    @classmethod
    def connect(cls,pipe,root,*,image,profile_id,build,authority,desktop,focus):
        require(isinstance(pipe,UIStartPipe) and pipe._connected,"connected dedicated UI pipe required")
        peer=ProfilePeer(pipe._h,image,profile_id)
        result=cls(pipe,root,peer,profile_id,build,authority,desktop=desktop,focus=focus)
        result.bind()
        return result

    def _send(self,action,args,*,bootstrap=False):
        require(not self.failed,"UI client failed; no automatic retry")
        call=secrets.token_hex(16)
        qualified=None if bootstrap else self.identity()
        generation="bootstrap" if bootstrap else qualified["ui_generation"]
        req={"version":1,"id":call,"session":generation,"provider":PROVIDER,
             "action":action,"arguments":args,"binding":generation}
        before=_peer(self.peer())
        expected={"peer":before,"profile_id":self.profile}
        self.last_envelope=None
        try:
            if qualified is not None:
                require(before==qualified["peer"] and self.profile==qualified["profile_id"],
                        "connected process/profile changed before UI request")
            raw=self.pipe.exchange(encode_request(req))
            require(isinstance(raw,str) and len(raw.encode("utf-8"))<=4096,"UI reply byte bound")
            value=_decode(raw)
            keys={"version","scope","provider","call_id","ui_instance","world_session","result",
                  "build","ui_generation","process_binding","profile_id"}
            require(isinstance(value,dict) and set(value)==keys and type(value["version"]) is int and
                    value["version"]==1 and value["scope"]==SCOPE and value["provider"]==PROVIDER and
                    value["call_id"]==call and value["world_session"] is False and value["build"]==self.build and
                    isinstance(value["result"],dict),"unqualified UI envelope")
            require(_peer(self.peer())==before,"connected process changed during UI request")
            if not bootstrap:
                current=self.identity()
                require(value["ui_generation"]==current["ui_generation"] and
                        value["ui_instance"]==current["ui_generation"] and
                        value["process_binding"]==digest(expected) and value["profile_id"]==self.profile,
                        "UI generation/process/profile scope changed")
            self.last_envelope=copy.deepcopy(value)
            return value["result"]
        except Exception as exc:
            self.failed=True;self.ready=None
            if isinstance(exc,SessionUnavailable):raise
            raise SessionUnavailable("UI request uncertain; preserve journal, do not resend") from exc

    def bind(self):
        try:
            return self._bind()
        except Exception:
            self.failed=True;self.ready=None
            raise

    def _bind(self):
        require(not self.ready and not self.failed,"UI already bound or failed")
        history=self.allocations.records();before=_peer(self.peer());challenge=secrets.token_hex(16)
        state=self._send("ui_state",{"challenge":challenge},bootstrap=True)
        require(set(state)=={"kind","challenge","generation","build"} and state["kind"]=="UI_STATE" and
                state["challenge"]==challenge and state["build"]==self.build,"UI bootstrap proof mismatch")
        env=self.last_envelope
        require(env["ui_generation"]==state["generation"] and
                env["ui_instance"]==(state["generation"] or "unbound"),"UI probe envelope mismatch")
        require(_peer(self.peer())==before and self.allocations.records()==history,"UI allocation inputs changed")
        if state["generation"]:
            nonce=token(state["generation"],32)
            require(history and history[-1]["session"]==nonce and history[-1]["peer"]==before and
                    history[-1]["revision"]==self.build,"UI generation not our latest allocation")
            row=history[-1]
        else:
            row=self.allocations.allocate(before,self.build,history)
        scoped={"peer":before,"profile_id":self.profile}
        result=self._send("ui_bind",{"proof":challenge,"generation":row["session"],
            "process_binding":digest(scoped),"profile_id":self.profile},bootstrap=True)
        require(result=={"kind":"UI_BOUND","challenge":challenge,"generation":row["session"],"build":self.build,
                         "process_binding":digest(scoped),"profile_id":self.profile},"UI bind refused")
        envelope=self.last_envelope
        require(envelope["ui_generation"]==row["session"] and envelope["ui_instance"]==row["session"] and
                envelope["process_binding"]==digest(scoped) and envelope["profile_id"]==self.profile,
                "UI bind envelope mismatch")
        require(_peer(self.peer())==before and self.allocations.records()[-1]==row,"UI binding inputs changed")
        self.ready=(row,before)
        return self.identity()

    def identity(self):
        require(not self.failed and self.ready is not None,"UI identity unavailable")
        row,peer=self.ready;history=self.allocations.records()
        require(history and history[-1]==row and _peer(self.peer())==peer,"UI peer/allocation changed")
        return {"scope":SCOPE,"peer":copy.deepcopy(peer),"profile_id":self.profile,"build":self.build,
                "ui_generation":row["session"],"ui_sequence":row["generation"],"world_session":False}

    def observe(self):
        return self._send("observe",{})

    def prepare(self,action,option=None,*,request_id=None):
        require(action in ("open_new","select","start"),"unknown UI action")
        require((action=="open_new" and option is None) or
                (action!="open_new" and isinstance(option,str) and 0<len(option)<=256),"invalid selected start")
        rid=token(request_id or secrets.token_hex(16),32)
        request={"version":1,"request_id":rid,"action":action,"option":option,"identity":self.identity()}
        _write_new(self.requests/(rid+"-request.json"),request)
        args={"request_id":rid,"action":action}
        if option is not None:args["option"]=option
        result=self._send("prepare",args)
        require(result.get("status")=="PREPARED" and result.get("request_id")==rid and
                result.get("action")==action and result.get("option")==option and
                result.get("binding")==request["identity"]["ui_generation"] and
                result.get("scope")==SCOPE and result.get("world_started") is False,
                "native preparation not qualified")
        _write_new(self.requests/(rid+"-prepared.json"),{"request_sha256":digest(request),
            "result":result,"envelope":self.last_envelope})
        return result

    def _request(self,rid):
        token(rid,32)
        request=_read_json(self.requests/(rid+"-request.json"))
        require(isinstance(request,dict) and request.get("request_id")==rid and
                request.get("identity")==self.identity(),"prepared UI context changed")
        prepared=_read_json(self.requests/(rid+"-prepared.json"))
        require(prepared.get("request_sha256")==digest(request) and
                prepared.get("result",{}).get("status")=="PREPARED","prepared journal drift")
        return request,prepared

    def execute(self,rid):
        request,prepared=self._request(rid)
        require(not (self.requests/(rid+"-dispatch.json")).exists(),"already dispatched; use status")
        context={"scope":"NEW_WORLD_IN_APPROVED_RUNNING_DISPOSABLE_CAMPAIGN","identity":self.identity(),
                 "request_sha256":digest(request),"request_id":rid,"gamestart":request["option"],
                 "desktop":self.desktop,"focus_impact":self.focus,
                 "desktop_evidence":"AGENT_DECLARED_POLICY_CONTEXT"}
        authorization=None
        if request["action"]=="start":
            opaque=self.authority.issue(context)
            require(self.identity()==request["identity"],"UI context changed during authorization")
            current_request,current_prepared=self._request(rid)
            require(current_request==request and current_prepared==prepared,"request evidence changed during authorization")
            authorization=self.authority.consume(opaque,context)
        _write_new(self.requests/(rid+"-dispatch.json"),{"request_sha256":digest(request),
            "authorization":authorization,"status":"DISPATCH_RESERVED_OUTCOME_UNKNOWN"})
        result=self._send("execute",{"request_id":rid})
        _write_new(self.requests/(rid+"-execute-reply.json"),{"result":result,"envelope":self.last_envelope})
        require(result.get("request_id")==rid and result.get("action")==request["action"] and
                result.get("option")==request["option"] and result.get("scope")==SCOPE and
                result.get("world_started") is False,"execute result rebound or overclaimed")
        if request["action"]=="start" and result.get("status")=="AUTHORIZATION_PENDING":
            require(authorization is not None and self.identity()==request["identity"],"Start authority no longer bound")
            _write_new(self.requests/(rid+"-authorization-dispatch.json"),
                       {"capability":authorization["capability"],"context_sha256":digest(context)})
            result=self._send("authorize",{"request_id":rid,"gamestart":request["option"],
                "capability":authorization["capability"],"context_sha256":digest(context)})
            require(result.get("kind")=="START_AUTHORIZATION_CONSUMED" and result.get("request_id")==rid and
                    result.get("capability")==authorization["capability"] and result.get("context_sha256")==digest(context),
                    "Start authorization acknowledgement unbound")
            observed=result.get("result")
            require(isinstance(observed,dict) and observed.get("request_id")==rid and
                    observed.get("action")=="start" and observed.get("option")==request["option"] and
                    observed.get("scope")==SCOPE and observed.get("world_started") is False,
                    "Start result rebound or overclaimed")
            _write_new(self.requests/(rid+"-authorization-reply.json"),{"result":result,"envelope":self.last_envelope})
        return result

    def status(self,rid):
        request=_read_json(self.requests/(token(rid,32)+"-request.json"))
        require(request.get("identity")==self.identity(),"status belongs to another UI scope")
        result=self._send("status",{"request_id":rid})
        require(result.get("request_id")==rid and result.get("action")==request["action"] and
                result.get("option")==request["option"] and result.get("scope")==SCOPE and
                result.get("world_started") is False,"status result rebound or overclaimed")
        # Status never sends prepare, execute or authorization, even after lost replies.
        _write_new(self.requests/(rid+"-status-"+self.last_envelope["call_id"]+".json"),
                   {"result":result,"envelope":self.last_envelope})
        return result

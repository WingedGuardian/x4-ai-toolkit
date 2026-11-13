"""Trusted agent-side save/load dispatch gate and scoped own-save transition join.

No CLI authorization flags, process launches, native calls on import, or save writes.
The native port and policy/guard callbacks are trusted integration dependencies.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import secrets
import time
from ._liveactions import _write_new, _read_json
from ._liveautomation import _directory
from ._livepersistence import SlotVerifier, PersistenceUnavailable, _file, _json, _hex, _finite
from ._livesession import SessionLink

def need(ok, why):
    if not ok: raise PersistenceUnavailable(why)

def sha(value):
    import json
    return hashlib.sha256(json.dumps(_json(value),sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def guard_reply(reply):
    """Canonical evaluated reply only. Legacy advisory is not a canonical deny."""
    need(isinstance(reply,dict) and reply.get("inert") is False and isinstance(reply.get("transactions"),list),
         "guard transport/preservation unavailable")
    r=reply.get("result")
    need(isinstance(r,dict) and type(r.get("api_version")) is int and r["api_version"]==1 and
         r.get("decision")=="allow" and r.get("evaluation")=="evaluated" and
         r.get("preservation") in ("verified","not_required") and
         isinstance(r.get("reason"),str) and isinstance(r.get("validation"),str) and
         isinstance(r.get("warnings"),list) and all(isinstance(x,str) for x in r["warnings"]),
         "guard refused or incomplete")
    need(reply.get("decision","allow") in ("allow","advise"),"legacy guard refusal")
    return _json(reply)

class PersistenceAuthority:
    """An actual agent decision callback supplies policy evidence, not user attestation.

    decide receives exact operation/profile/slot/inventory/identity context.
    canonical_check, if used, must invoke the real non-preview harness on the
    exact source-qualified native effect scope. This module invents no paths.
    """
    def __init__(self,journal,decide,*,canonical_check=None,effect_scope=None,clock=time.monotonic):
        self.journal=_directory(journal).resolve(strict=True)
        need(callable(decide) and callable(clock),"trusted policy source required")
        self.decide=decide;self.check=canonical_check;self.effects=effect_scope;self.clock=clock
        self.records={};self.last=None

    def now(self):
        value=self.clock()
        need(_finite(value) and value>=0 and (self.last is None or value>=self.last),"authority clock invalid")
        self.last=value;return value

    def issue(self,context):
        context=_json(context);age=self.now()
        keys={"operation","request_id","profile","profile_id","save_directory","slot","label",
              "identity","source_evidence_sha256","inventory_sha256","native_operation"}
        need(isinstance(context,dict) and set(context)==keys and context["operation"] in ("SAVE","LOAD") and
             _hex(context["request_id"],32) and _hex(context["source_evidence_sha256"],64) and
             _hex(context["inventory_sha256"],64) and isinstance(context["profile"],str) and
             isinstance(context["save_directory"],str) and Path(context["profile"]).is_absolute() and
             Path(context["profile"]).name==context["profile_id"] and
             Path(context["save_directory"])==Path(context["profile"])/"save" and
             context["slot"] in {f"save_{i:03}" for i in range(1,11)} and
             isinstance(context["label"],str) and context["label"].startswith("x4live-") and
             _hex(context["label"][7:],32) and isinstance(context["identity"],dict) and
             context["native_operation"]==("SaveGame" if context["operation"]=="SAVE" else "LoadGame"),
             "complete named-profile native operation context required")
        profile=Path(context["profile"])
        need(not self.journal.is_relative_to(profile) and not profile.is_relative_to(self.journal),
             "authority journal/profile overlap")
        need(len(self.records)<128,"authority capacity")
        decision=_json(self.decide(copy.deepcopy(context)))
        need(isinstance(decision,dict) and set(decision)=={"source","reference","context_sha256","operation","mode"} and
             decision["source"]=="AGENT_RECORDED_CONVERSATION" and decision["context_sha256"]==sha(context) and
             decision["operation"]==context["operation"] and isinstance(decision["reference"],str) and
             0<len(decision["reference"])<=4096,"applicable recorded approval required")
        mode=decision["mode"];guard=None;scope=None
        if mode=="CANONICAL_GUARD":
            need(callable(self.check) and callable(self.effects),"native effect scope/check not integrated")
            scope=_json(self.effects(copy.deepcopy(context)))
            need(isinstance(scope,dict) and set(scope)=={"status","source","action"} and
                 scope["status"]=="EXACT_NATIVE_EFFECTS_SOURCE_QUALIFIED" and
                 isinstance(scope["source"],str) and 0<len(scope["source"])<=4096,"native effects unqualified")
            action=scope["action"]
            need(isinstance(action,dict) and type(action.get("api_version")) is int and
                 action["api_version"]==1 and action.get("surface")=="files" and
                 isinstance(action.get("paths"),list) and len(action["paths"])>0 and
                 len(action["paths"])==len(set(action["paths"])) and
                 action.get("operations")==["write" if context["operation"]=="SAVE" else "read"]*len(action["paths"]),
                 "exact guard action required")
            for path in action["paths"]:
                need(isinstance(path,str) and Path(path).is_absolute() and
                     Path(path).parent==Path(context["save_directory"]),"guard scope outside named save directory")
            guard=guard_reply(self.check(copy.deepcopy(action)))
        else:
            need(mode=="EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION",
                 "generic continuation is not a native-effect exception")
        nonce=secrets.token_hex(16)
        evidence={"version":1,"capability":nonce,"context":context,"context_sha256":sha(context),
                  "decision":decision,"mode":mode,"guard_qualified":guard is not None,
                  "guard":guard,"effect_scope":scope,"issued":age,
                  "attestation":"AGENT_RECORDED_CONVERSATION_NOT_CRYPTOGRAPHIC_PROOF"}
        path=self.journal/(nonce+"-issued.json");_write_new(path,evidence)
        handle=object();self.records[handle]=(evidence,path,sha(evidence),False)
        return handle

    def consume(self,handle,context):
        need(type(handle) is object and handle in self.records,"foreign/serialized authority")
        row,path,digest,used=self.records[handle]
        need(not used and self.now()-row["issued"]<=30,"authority consumed/expired")
        need(sha(_read_json(path))==digest and sha(context)==row["context_sha256"],"authority context/evidence drift")
        self.records[handle]=(row,path,digest,True)
        _write_new(self.journal/(row["capability"]+"-consumed.json"),
                   {"capability":row["capability"],"context_sha256":row["context_sha256"]})
        return copy.deepcopy(row)

class PersistenceDispatcher:
    """Fixed trusted port: port(operation, payload) -> {identity, result}.

    The production port must use the qualified world-bound transport and the
    native persistence gate; arbitrary packet callers must not obtain this object.
    A returned envelope is evidence from that trusted port, not a caller proof.
    """
    def __init__(self,verifier,journal,authority,port,*,clock=time.monotonic):
        need(isinstance(verifier,SlotVerifier) and isinstance(authority,PersistenceAuthority) and
             callable(port) and callable(clock),"qualified dependencies required")
        self.v=verifier;self.root=_directory(journal).resolve(strict=True);self.authority=authority;self.port=port;self.clock=clock
        need(not self.root.is_relative_to(verifier.profile) and not verifier.profile.is_relative_to(self.root),
             "dispatch journal/profile overlap")
        self.records={};self.last=None

    def now(self):
        n=self.clock();need(_finite(n) and n>=0 and (self.last is None or n>=self.last),"dispatcher clock invalid")
        self.last=n;return n

    def context(self,operation,rid,source,identity):
        return {"operation":operation,"request_id":rid,"profile":str(self.v.profile),
                "profile_id":self.v.profile.name,"save_directory":str(self.v.save),
                "slot":source["slot"],"label":source["label"],"identity":identity,
                "source_evidence_sha256":sha(source),"inventory_sha256":sha(self.v._frame()),
                "native_operation":"SaveGame" if operation=="SAVE" else "LoadGame"}

    def call(self,operation,payload,identity):
        need(self.v._identity()==identity,"identity changed immediately before native port")
        reply=_json(self.port(operation,copy.deepcopy(payload)))
        need(isinstance(reply,dict) and set(reply)=={"identity","result"} and
             reply["identity"]==identity and isinstance(reply["result"],dict),"native port envelope mismatch")
        need(self.v._identity()==identity,"identity changed during native port")
        return reply["result"]

    def reserve_save(self,rid,slot):
        need(_hex(rid,32) and len(self.records)<64,"request/capacity")
        handle=self.v.reserve(rid,slot)
        self.v.preflight(handle)
        source=copy.deepcopy(self.v._record(handle)["row"]);identity=source["identity"]
        _write_new(self.root/(rid+"-prepare.json"),{"operation":"SAVE","source":source})
        result=self.call("prepare_save",{"ticket":rid,"slot":slot,"reservation_sha256":sha(source)},identity)
        need(result.get("status")=="PREPARED" and result.get("ticket")==rid and result.get("slot")==slot and
             result.get("label")==source["label"] and result.get("stamp")==self.v._stamp(identity),
             "native save preparation mismatch")
        ticket=object();self.records[ticket]={"rid":rid,"operation":"SAVE","source":source,"identity":identity,
            "reservation":handle,"state":"PREPARED","prepared":result}
        _write_new(self.root/(rid+"-prepared.json"),result)
        return ticket

    def prepare_load(self,rid,save_ticket):
        need(_hex(rid,32) and len(self.records)<64,"load request/capacity")
        saved=self.record(save_ticket)
        need(saved["operation"]=="SAVE" and saved["state"]=="SETTLED","own settled save required")
        source=self.v.receipt(saved["receipt"],recheck=True);identity=source["identity"]
        _write_new(self.root/(rid+"-prepare.json"),{"operation":"LOAD","source":source})
        result=self.call("prepare_load",{"ticket":rid,"save_ticket":saved["rid"]},identity)
        need(result.get("status")=="LOAD_PREPARED" and result.get("ticket")==rid and
             result.get("slot")==source["slot"] and result.get("source_save_ticket")==saved["rid"],
             "native load preparation mismatch")
        ticket=object();self.records[ticket]={"rid":rid,"operation":"LOAD","source":source,"identity":identity,
            "receipt":saved["receipt"],"state":"PREPARED","prepared":result}
        _write_new(self.root/(rid+"-prepared.json"),result)
        return ticket

    def record(self,ticket):
        need(type(ticket) is object and ticket in self.records,"foreign/serialized dispatch ticket")
        return self.records[ticket]

    def recheck(self,row):
        if row["operation"]=="SAVE":
            self.v.preflight(row["reservation"])
            need(self.v._record(row["reservation"])["row"]==row["source"],"reservation source drift")
        else:
            need(self.v.receipt(row["receipt"],recheck=True)==row["source"],"settled source drift")
        need(self.v._identity()==row["identity"],"dispatch session drift")

    def execute(self,ticket):
        row=self.record(ticket);need(row["state"]=="PREPARED","dispatch already consumed or unavailable")
        rid=row["rid"];self.recheck(row)
        context=self.context(row["operation"],rid,row["source"],row["identity"])
        capability=self.authority.issue(context)
        self.recheck(row)
        need(self.context(row["operation"],rid,row["source"],row["identity"])==context,"post-policy inventory drift")
        evidence=self.authority.consume(capability,context)
        row["state"]="CONSUMED";row["started"]=self.now();row["frame"]=self.v._frame()
        need(sha(row["frame"])==context["inventory_sha256"],"inventory changed during authority consumption")
        _write_new(self.root/(rid+"-dispatch.json"),{"context":context,"authorization":evidence})
        # Nothing below may retry; a lost response remains possible native effect.
        row["state"]="UNKNOWN"
        payload={"ticket":rid,"capability":evidence["capability"],"context_sha256":sha(context),
                 "mode":evidence["mode"],"guard_qualified":evidence["guard_qualified"]}
        result=self.call("execute_save" if row["operation"]=="SAVE" else "execute_load",payload,row["identity"])
        need(result.get("ticket")==rid and result.get("completed") is False,"invalid native dispatch result")
        allowed=("SAVE_REQUESTED","UNKNOWN","REFUSED") if row["operation"]=="SAVE" else ("LOAD_SCHEDULED","UNKNOWN","REFUSED")
        need(result.get("status") in allowed,"unsupported native effect claim")
        _write_new(self.root/(rid+"-reply.json"),result);row["native"]=result
        row["state"]=result["status"]
        return copy.deepcopy(result)

    def settle(self,ticket):
        row=self.record(ticket)
        need(row["operation"]=="SAVE" and row["state"] in ("SAVE_REQUESTED","UNKNOWN"),"no own save dispatch")
        receipt=self.v.observe_settlement(row["reservation"])
        if receipt is None:return None
        source=self.v.receipt(receipt,recheck=True)
        native={"ticket":row["rid"],"slot":source["slot"],"label":source["label"],"path":source["path"],
                "sha256":source["file"]["sha256"],"size":source["file"]["size"],"stamp":self.v._stamp(row["identity"])}
        result=self.call("confirm_settlement",native,row["identity"])
        need(result.get("status")=="SAVE_COMPLETED" and result.get("ticket")==row["rid"] and
             result.get("receipt")==native,"native settlement correspondence unavailable")
        row["receipt"]=receipt;row["state"]="SETTLED";row["source_receipt"]=source
        _write_new(self.root/(row["rid"]+"-settled-join.json"),{"host":source,"native":result})
        return copy.deepcopy(source)

    def acknowledge_load(self,ticket):
        row=self.record(ticket)
        need(row["operation"]=="LOAD" and row["state"] in ("LOAD_SCHEDULED","UNKNOWN"),"no own load dispatch")
        # Still the OLD identity; source rechecked before accepting dispatch evidence.
        self.recheck(row)
        result=self.call("status",{"ticket":row["rid"]},row["identity"])
        need(result.get("ticket")==row["rid"] and result.get("slot")==row["source"]["slot"] and
             result.get("status")=="LOAD_REQUESTED" and result.get("load_completed") is False,
             "native LoadGame invocation not observed")
        row["state"]="LOAD_REQUESTED";row["load_ack"]=result
        _write_new(self.root/(row["rid"]+"-load-requested.json"),result)
        return copy.deepcopy(result)

    def observe_loaded(self,ticket,new_link,native_current):
        row=self.record(ticket)
        need(row["operation"]=="LOAD" and row["state"]=="LOAD_REQUESTED","no qualified own LoadGame invocation")
        need(isinstance(new_link,SessionLink) and callable(native_current),"new actual SessionLink/current observer required")
        need(0<=self.now()-row["started"]<=300,"transition observation expired")
        # The old live identity is invalid after load; use historical receipt plus fresh file proof.
        source=self.v.receipt(row["receipt"],recheck=False)
        need(source==row["source"] and _file(Path(source["path"]),self.v.max_file_bytes)==source["file"],
             "loaded-source file/evidence drift")
        need(self.v._frame()==row["frame"],"save frame changed across load")
        new=new_link.verify_reply(native_current())
        old=row["identity"]
        need(new["peer"]==old["peer"] and new["revision"]==old["revision"] and
             new["session"]!=old["session"] and new["load_generation"]==old["load_generation"]+1 and
             new["world_token"]==old["world_token"] and new["world_lineage"]=="UNKNOWN",
             "own-save transition identity mismatch")
        need(new_link.verify_reply(native_current())==new and self.v._frame()==row["frame"] and
             _file(Path(source["path"]),self.v.max_file_bytes)==source["file"],"transition end drift")
        result={"status":"OWN_SAVE_LOAD_TRANSITION_OBSERVED","request_id":row["rid"],"old_identity":old,
                "new_identity":new,"save_path":source["path"],"save_sha256":source["file"]["sha256"],
                "native_catalogue":source["native"],"load_ack":row["load_ack"],
                "scope":"ONE_OWN_SETTLED_SAVE_AND_BOUND_LOAD","generic_world_lineage":"UNKNOWN",
                "engine_loaded_file_digest_measured":False}
        _write_new(self.root/(row["rid"]+"-transition.json"),result)
        row["state"]="TRANSITION_OBSERVED"
        return copy.deepcopy(result)

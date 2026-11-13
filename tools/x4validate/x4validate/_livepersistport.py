"""Qualified persistence-only port over AutomationClient and its ActionJournal.

Only opaque in-process capabilities reach the reserved provider. This prevents
JSON/CLI adoption, not arbitrary code running as the same operating-system user.
"""
from __future__ import annotations
from ._livetrees import overlaps, personal_tree
import copy
import secrets
from pathlib import Path
from ._liveactions import _write_new, _read_json
from ._liveautomation import AutomationClient, _directory
from ._livepersistdispatch import PersistenceAuthority, need, sha
from ._livepersistence import SlotVerifier, _hex, _route
from ._livepeerprofile import ProfilePeer
from ._livebound import _decode

PROVIDER="x4-persistence-v1"
_ACCESS=set()
def _valid_access(value):
    return type(value) is object and value in _ACCESS

class PersistencePort:
    def __init__(self,client,journal,revision,verifier,authority):
        need(isinstance(client,AutomationClient) and isinstance(verifier,SlotVerifier) and
             isinstance(authority,PersistenceAuthority) and _hex(revision,64),"qualified persistence port dependencies")
        self.client=client;self.root=_directory(journal).resolve(strict=True);self.revision=revision
        need(not overlaps(self.root, personal_tree(verifier.profile)),
             "port journal/personal tree overlap")
        self.v=verifier;self.authority=authority;self.claimed=set();self.last=None
        self.access=object();_ACCESS.add(self.access)

    def close(self):
        _ACCESS.discard(self.access)

    def _grant_record(self,operation,args):
        expected="SAVE" if operation=="execute_save" else "LOAD"
        matches=[(e,path,digest,used) for e,path,digest,used in self.authority.records.values()
                 if e["capability"]==args.get("capability")]
        need(len(matches)==1 and matches[0][3],"host capability was not issued/consumed here")
        evidence,path,digest,_=matches[0];context=evidence["context"];nonce=evidence["capability"]
        need(sha(_read_json(path))==digest and
             _read_json(self.authority.journal/(nonce+"-consumed.json"))==
                {"capability":nonce,"context_sha256":evidence["context_sha256"]},"authority journals changed")
        need(context["operation"]==expected and context["profile"]==str(self.v.profile) and
             context["save_directory"]==str(self.v.save) and context["profile_id"]==self.v.profile.name and
             context["request_id"]==args.get("ticket") and evidence["context_sha256"]==args.get("context_sha256") and
             evidence["mode"]==args.get("mode") and evidence["guard_qualified"] is args.get("guard_qualified") and
             self.authority.now()-evidence["issued"]<=30 and context["identity"]==self.v._identity() and
             context["inventory_sha256"]==sha(self.v._frame()),"native grant scope changed/replayed")
        return evidence

    def _grant(self,operation,args):
        evidence=self._grant_record(operation,args);nonce=evidence["capability"];context=evidence["context"]
        need(nonce not in self.claimed,"native grant already claimed")
        self.claimed.add(nonce)
        _write_new(self.root/(nonce+"-native-grant.json"),{"context":context,"authorization":evidence})
        return evidence

    def _one(self,operation,args):
        need(_valid_access(self.access),"persistence port retired")
        identity=self.client.identity()
        need(identity==self.v._identity(),"port/profile identity mismatch")
        rid=secrets.token_hex(16)
        request={"version":1,"id":rid,"session":identity["session"],"provider":PROVIDER,
                 "action":operation,"arguments":copy.deepcopy(args),"binding":self.revision}
        _write_new(self.root/(rid+"-port.json"),{"operation":operation,"outer_ticket":args.get("ticket"),
                                              "request":request,"identity":identity})
        self.last={"request_id":rid,"operation":operation}
        prepared=self.client.prepare(request,_persistence_access=self.access)
        need(prepared["status"]=="prepared","persistence fixed call not prepared; do not resend")
        if operation in ("execute_save","execute_load"):
            self._grant_record(operation,args)  # Recheck after the actual SDK preparation round trip.
        result=self.client.execute(rid,_persistence_access=self.access)
        observation=result.get("observations",{})
        need(observation.get("provider_revision")==self.revision and
             observation.get("native_effect_complete") is False and
             isinstance(observation.get("native_result"),dict),"persistence result unavailable; preserve journal")
        native_status=observation["native_result"].get("status")
        expected={"REFUSED":"refused","UNKNOWN":"unknown","SAVE_REQUESTED":"running",
                  "LOAD_SCHEDULED":"running","LOAD_REQUESTED":"running"}.get(native_status,"completed")
        need(isinstance(observation.get("action_core"),dict) and
             observation["action_core"].get("protocol_fault") is False and result.get("status")==expected,
             "SDK outcome/protocol contradicts native result; preserve consumed journal")
        need(self.client.identity()==identity and self.v._identity()==identity,"port identity changed")
        if operation=="observe" and observation["native_result"].get("status")=="CATALOGUE_CAPTURED":
            need(observation["native_result"].get("capture")==rid,"catalogue capture belongs to another request")
        return {"identity":identity,"result":observation["native_result"]}

    def __call__(self,operation,args):
        need(_valid_access(self.access),"persistence port retired")
        allowed={"refresh","observe","prepare_save","execute_save","confirm_settlement","prepare_load","execute_load","status"}
        need(operation in allowed and isinstance(args,dict),"unsupported persistence port operation")
        if operation in ("execute_save","execute_load"):self._grant(operation,args)
        flat=copy.deepcopy(args)
        if operation=="confirm_settlement":
            stamp=flat.pop("stamp")
            for key,value in stamp.items():flat["source_"+key]=value
        reply=self._one(operation,flat)
        if operation!="observe" or reply["result"].get("status")!="CATALOGUE_CAPTURED":return reply
        result=reply["result"];header=result.get("header")
        need(isinstance(header,dict) and header.get("status")=="OBSERVED" and
             type(header.get("count")) is int and 0<=header["count"]<=64 and
             type(result.get("pages")) is int and result["pages"]==header["count"] and
             header.get("stamp")==self.v._stamp(reply["identity"]) and _hex(result.get("capture"),32),
             "catalogue header/count unqualified")
        rows=[]
        for i in range(header["count"]):
            page=self._one("catalogue_page",{"capture":result["capture"],"index":i})
            value=page["result"]
            need(page["identity"]==reply["identity"] and value.get("status")=="CATALOGUE_PAGE" and
                 value.get("capture")==result["capture"] and type(value.get("index")) is int and value["index"]==i and
                 type(value.get("count")) is int and value["count"]==header["count"] and
                 isinstance(value.get("row"),dict),"mixed/missing catalogue page")
            rows.append(value["row"])
        native=copy.deepcopy(header);native["rows"]=rows
        need(self.client.identity()==reply["identity"],"catalogue end identity changed")
        return {"identity":reply["identity"],"result":native}

    def catalogue(self):
        return self("observe",{})["result"]

    def refresh(self):
        return self("refresh",{})["result"]

def connect_persistence(client,profile,journals,revision,authority):
    """Bind actual native save-root evidence before constructing the dispatcher.

    Does a read-only query on the already connected game; never opens/launches it.
    Three existing task directories are required; no profile files are created.
    """
    from ._livepersistdispatch import PersistenceDispatcher
    need(isinstance(authority,PersistenceAuthority) and _hex(revision,64),"qualified authority/revision required")
    need(isinstance(client,AutomationClient) and isinstance(client.bound.link.peer,ProfilePeer),
         "actual connected ProfilePeer required")
    need(isinstance(journals,dict) and set(journals)=={"observer","port","dispatcher"},"exact task journals required")
    roots={k:_directory(v) for k,v in journals.items()}
    need(len({str(v.resolve()) for v in roots.values()})==3,"distinct task journals required")
    profile=_route(profile,directory=True);save=_route(profile/"save",directory=True)
    for root in [*roots.values(),authority.journal]:
        root=root.resolve(strict=True)
        need(not overlaps(root, personal_tree(profile)),"task journal/personal tree overlap")
    identity=client.identity();reply=client.query("native_save_path")
    need(reply.status=="OK","native save path transport unavailable")
    observed=_decode(reply.payload)
    need(isinstance(observed,dict) and observed.get("status")=="OBSERVED" and
         observed.get("scope")=="NATIVE_SAVE_FOLDER_PATH" and
         observed.get("stamp")==SlotVerifier._stamp(identity) and isinstance(observed.get("save_folder"),str),
         "native save path observation unqualified")
    native=_route(observed["save_folder"],directory=True)
    need(native==save and client.identity()==identity,"native/profile save directory mismatch")
    peer=client.bound.link.peer
    need(profile.name==peer.profile_id and peer()==identity["peer"],"connected profile path mismatch")
    holder={}
    verifier=SlotVerifier(profile,roots["observer"],identity=client.identity,
        catalogue=lambda:holder["port"].catalogue(),
        profile_peer=lambda:{"peer":peer(),"profile_id":peer.profile_id})
    port=PersistencePort(client,roots["port"],revision,verifier,authority);holder["port"]=port
    dispatcher=PersistenceDispatcher(verifier,roots["dispatcher"],authority,port)
    _write_new(roots["port"]/"native-path-binding.json",{"identity":identity,"native":observed,
        "profile":str(profile),"read_capture":client.last_read})
    return dispatcher,port,verifier

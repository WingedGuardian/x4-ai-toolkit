"""Trusted still-capture orchestration; file settlement never implies visual PASS.

Only opaque locally reserved tickets can reach release. Native release is solely
reservation bookkeeping. Ambiguous effects quarantine this owner; no retries or
automatic journal reopening are provided.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import secrets
from pathlib import Path
from ._liveactions import _write_new, _read_json, request_digest, validate_receipt
from ._liveautomation import AutomationClient
from ._livebound import _decode
from ._livecapture import CaptureObserver, CaptureUnavailable, need, _FrameJournalRef
from ._livepersistence import _route, _json, _hex, _finite
from ._liveevidence import ReadEvidence
from ._livecapacity import resolve as capacity_profile_for

PROVIDER="x4-screen-capture-v1"
CAMERA_SCOPE="NATIVE_CAMERA_ORIENTATION_AND_OFFSET_SETTING"
CAMERA_SAMPLE_BYTES=128*1024
CAMERA_TOTAL_BYTES=16*1024*1024

class CaptureDispatcher:
    """Actual client plus exclusive host journals; never accepts file attestation JSON."""
    def __init__(self,client,journal,observer_journal,revision,native_revision,*,synthetic=False,capacity_profile=None):
        self.capacity=capacity_profile_for(capacity_profile)
        need(isinstance(client,AutomationClient),"actual automation client required")
        need(_hex(revision,64) and _hex(native_revision,64),"qualified adapter/native revisions required")
        self.client=client;self.root=_route(journal,directory=True)
        observer_root=_route(observer_journal,directory=True)
        need(self.root!=observer_root and not self.root.is_relative_to(observer_root)
             and not observer_root.is_relative_to(self.root),"distinct capture journals required")
        self.revision=revision;self.native_revision=native_revision
        self.records={};self.active=None;self.fault=None;self.sequence=0
        need(type(synthetic)is bool,"explicit synthetic evidence switch required")
        self.synthetic=synthetic;self.camera_bytes=0
        # Validate production connection before allocating any capture journals.
        ReadEvidence(client,synthetic=synthetic)
        self.identity=copy.deepcopy(client.identity())
        self.observer=CaptureObserver(observer_root,identity=client.identity,
            native_paths=self._paths,capture_receipt=self._native_status,capacity_profile=self.capacity['name'])
        personal=self.observer.native_savedir.parent.parent
        need(not self.root.is_relative_to(personal) and not personal.is_relative_to(self.root),
             "dispatcher journal/personal-tree overlap")
        self.root_identity=self._root_identity()
        self.owner={"version":1,"identity":self.identity,"revision":revision,"native_revision":native_revision,
                    "capacity_profile":self.capacity,
                    "policy":"NO_AUTOMATIC_REOPEN_REPLAY_OR_QUARANTINE_RESET"}
        _write_new(self.root/"capture-dispatch-owner.json",self.owner)

    def _root_identity(self):
        p=_route(self.root,directory=True);s=p.stat();return [s.st_dev,s.st_ino]

    def _current(self):
        need(self.fault is None,"capture dispatcher quarantined: "+str(self.fault))
        need(self._root_identity()==self.root_identity,"capture dispatcher journal replaced")
        need(_read_json(self.root/"capture-dispatch-owner.json")==self.owner,"capture owner journal changed")
        need(self.client.identity()==self.identity,"capture public identity changed")
        self.observer._current()

    def _paths(self):
        identity=self.client.identity();reply=self.client.query("native_save_path")
        need(reply.status=="OK","native save-folder read unavailable")
        observed=_decode(reply.payload)
        need(self.client.identity()==identity,"native save-folder read identity changed")
        return observed

    def _record(self,handle):
        self._current()
        need(handle in self.records,"opaque local capture ticket required")
        row=self.records[handle]
        need(self.active is handle or row["stage"]=="RELEASED","capture ticket not active")
        if row['stage']=='RELEASED':
            self.observer._before(row['observer']);self._stored_frame(row)
        for sample in row["camera"].values():
            if sample is not None:
                need(_read_json(Path(sample["journal"]))==sample["entry"],"camera evidence journal changed")
        self._current()
        return row

    def _log(self,row,stage,**values):
        self.sequence+=1
        entry={"version":1,"id":row["request"]["id"],"stage":stage,"identity":self.identity,**values}
        path=self.root/(row["request"]["id"]+"-"+str(self.sequence)+"-"+stage.lower()+".json")
        _write_new(path,entry)
        row["stage"]=stage
        return path,entry

    def _quarantine(self,row,exc):
        self.fault=str(exc) or type(exc).__name__
        row["stage"]="QUARANTINED"
        # The original exception remains primary; no second write can turn it into a retry.
        return self.fault

    def reserve(self,view_binding):
        self._current()
        need(self.active is None and len(self.records)<self.capacity['host_capture_records'],"previous capture not released/capacity")
        need(type(view_binding)is str and 0<len(view_binding.encode("utf-8"))<=128,"bounded view attribution required")
        request={"version":1,"id":secrets.token_hex(16),"session":self.identity["session"],
                 "provider":PROVIDER,"action":"capture","arguments":{"view_binding":view_binding},"binding":self.revision}
        correlation={k:request[k] for k in ("id","session","provider","binding")}
        correlation.update(digest=request_digest(request),native_revision=self.native_revision,view_binding=view_binding)
        observer_handle=self.observer.prepare(correlation)
        handle=object()
        row={"request":request,"observer":observer_handle,"stage":"RESERVED","last":None,
             "release":None,"settled":None,"settled_frame":None,"settled_ref":None,"settled_journal":None,
             "camera":{"before":None,"after":None},"camera_collector":ReadEvidence(self.client,synthetic=self.synthetic)}
        self.records[handle]=row;self.active=handle
        try:self._log(row,"RESERVED",request=request,correlation=correlation)
        except Exception as exc:self._quarantine(row,exc);raise
        return handle
    def capacity_usage(self):
        """Measured same-owner retention; no future count/size assumption."""
        self._current();observed=self.observer.capacity_usage()
        need(observed['capacity_profile']==self.capacity,'observer capacity profile changed')
        self._current()
        return {'scope':'CURRENT_CAPTURE_DISPATCH_OWNER','capacity_profile':copy.deepcopy(self.capacity),
                'records_used':len(self.records),'records_limit':self.capacity['host_capture_records'],
                'camera_bytes':self.camera_bytes,'camera_byte_limit':CAMERA_TOTAL_BYTES,
                'camera_sample_limit':CAMERA_SAMPLE_BYTES,'observer':observed,
                'active':self.active is not None,'future_fit_verified':False}

    def _receipt(self,row,value,*,release=False):
        request=row["release"] if release else row["request"]
        preparing=type(value)is dict and value.get("status")=="preparing"
        checked=dict(value,status="running") if preparing else value
        result=validate_receipt(checked,request)
        if preparing:result["status"]="preparing"
        o=result["observations"]
        core=o.get("action_core")
        need(type(core)is dict and core.get("protocol_fault") is False
             and core.get("provider_revision")==self.revision,"capture SDK protocol/source unqualified")
        # An asynchronous SDK call may not have any provider observation yet.
        # UNKNOWN retains the reservation; it never permits execute, settle or reuse.
        if result["status"] not in ("unknown","preparing"):
            need(o.get("adapter_revision")==self.revision and o.get("native_revision")==self.native_revision
                 and o.get("visual_pass") is False and o.get("file_completion_qualified") is False,
                 "capture adapter result source/scope unqualified")
            cap=o.get('capacity')
            if cap is not None or self.capacity['name']!='interactive':
                legacy=(self.capacity['name']=='interactive' and type(cap)is dict
                        and cap.get('revision')=='UNBOUND_LEGACY_INTERACTIVE'
                        and cap.get('source_sha256')=='UNBOUND_LEGACY_INTERACTIVE')
                need(type(cap)is dict and cap.get('profile')==self.capacity['name']
                     and (legacy or (cap.get('revision')==self.capacity['revision']
                          and cap.get('source_sha256')==self.capacity['source_sha256']))
                     and cap.get('scope')=='LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS',
                     'capture capacity source/profile mismatch')
                for used,limit,key in [('history_used','history_limit','capture_history'),('calls_used','calls_limit','capture_calls')]:
                    need(type(cap.get(used))is int and type(cap.get(limit))is int
                         and cap[limit]==self.capacity[key] and 0<=cap[used]<=cap[limit],
                         'capture capacity counters/limits differ')
        self._current()
        return result

    def prepare(self,handle):
        row=self._record(handle)
        need(row["stage"]=="RESERVED","capture prepare already issued; use status")
        try:
            self._log(row,"PREPARE_SENT")
            result=self._receipt(row,self.client.prepare(row["request"]))
            row["last"]=result
            need(result["status"] in ("prepared","preparing","unknown"),"native capture preparation refused")
            if result["status"]=="prepared":self._log(row,"PREPARED")
            return copy.deepcopy(result)
        except Exception as exc:self._quarantine(row,exc);raise

    def status(self,handle):
        row=self._record(handle)
        need(row["stage"] not in ("RESERVED","QUARANTINED"),"capture has no issued request")
        release=row["release"] is not None
        try:
            request=row["release"] if release else row["request"]
            result=self._receipt(row,self.client.status(request["id"]),release=release)
            row["last"]=result
            if result["status"]=="refused":raise CaptureUnavailable("capture action refused; owner quarantined")
            if not release and row["stage"]=="PREPARE_SENT" and result["status"]=="prepared":
                self._log(row,"PREPARED")
            if not release and row["stage"]=="EXECUTE_SENT" and self._capture_observed(row,result):
                self._camera(row,"after")
                self._log(row,"RUNNING")
            if release and row["stage"]=="RELEASE_PREPARE_SENT" and result["status"]=="prepared":
                self._log(row,"RELEASE_PREPARED")
            if release and row["stage"]=="RELEASE_SENT" and result["status"]=="completed":
                self._released(row,result)
            return copy.deepcopy(result)
        except Exception as exc:self._quarantine(row,exc);raise

    def execute(self,handle):
        row=self._record(handle)
        need(row["stage"]=="PREPARED","capture not prepared or execute already issued")
        result=row["last"];o=result["observations"]
        need(result["status"]=="prepared" and _finite(o.get("age")) and _finite(o.get("prepared_age"))
             and o["prepared_age"]+4<=o["age"]<=o["prepared_age"]+30,
             "need source-observed native preparation age within capture window")
        try:
            need(self.camera_bytes+2*CAMERA_SAMPLE_BYTES<=CAMERA_TOTAL_BYTES,"camera evidence retention capacity")
            self._camera(row,"before")
            self._log(row,"EXECUTE_SENT")  # consumed before native transport
            result=self._receipt(row,self.client.execute(row["request"]["id"]))
            row["last"]=result
            need(result["status"] in ("running","unknown"),"capture execute not observed")
            if self._capture_observed(row,result):
                self._camera(row,"after")
                self._log(row,"RUNNING")
            return copy.deepcopy(result)
        except Exception as exc:self._quarantine(row,exc);raise

    def _capture_observed(self,row,result):
        # SDK running means dispatch began. Only the bound MD observation says capture ran.
        o=result["observations"]
        if result["status"]!="running" or o.get("bridge_phase") not in ("execute","status"):
            return False
        self.observer._native(self.observer.records[row["observer"]],result)
        return True

    def _camera(self,row,phase):
        need(phase in ("before","after") and row["camera"][phase] is None,"camera sample already consumed")
        self._current()
        collector=row["camera_collector"]
        handle=collector.collect("camera_state")
        receipt=collector.receipt(handle);transcript=collector.transcript(handle)
        need(receipt["identity"]==self.identity and receipt["build"]==self.client.bound.build
             and receipt["request"]=={"verb":"camera_state","args":[]}
             and receipt["status"]=="OK" and receipt["transport_complete"] is True,"unqualified camera read")
        expected_tier="SYNTHETIC" if self.synthetic else "MACHINE_OBSERVED"
        need(receipt["evidence_tier"]==expected_tier,"camera evidence provenance differs")
        value=_decode(receipt["capture"]["payload"])
        need(type(value)is dict and value.get("scope")==CAMERA_SCOPE,"camera observation scope")
        if value.get("status")=="UNAVAILABLE":
            need(set(value)=={"status","scope","reason"} and type(value["reason"])is str
                 and 0<len(value["reason"])<=1024,"malformed camera unavailability")
        else:
            fields={"status","scope","stamp","interval","rotation_native","rotation_units","rotation_reference_frame",
                    "follow_camera_base_offset_setting","external_view_active","external_reference_handle",
                    "external_reference_nonzero","nested_handle_issued","achieved_pose_qualified",
                    "target_distance_qualified","engine_atomicity_qualified"}
            need(set(value)==fields and value["status"]=="OBSERVED"
                 and value["stamp"]==self.observer._stamp(self.identity),"camera identity/fields differ")
            for key in ("nested_handle_issued","achieved_pose_qualified","target_distance_qualified","engine_atomicity_qualified"):
                need(value[key] is False,"camera scope falsely qualified")
            need(value["rotation_units"]==value["rotation_reference_frame"]=="UNKNOWN","camera angle frame invented")
            for key,keys in (("rotation_native",{"yaw","pitch","roll"}),
                             ("follow_camera_base_offset_setting",{"x","y","z"})):
                v=value[key];need(type(v)is dict and set(v)==keys and all(_finite(x) for x in v.values()),"camera vector invalid")
            ref=value["external_reference_handle"]
            need(type(ref)is str and re.fullmatch(r"0|[1-9][0-9]{0,19}",ref) is not None
                 and int(ref)<=2**64-1 and type(value["external_view_active"])is bool
                 and value["external_reference_nonzero"] is (ref!="0"),"camera reference invalid")
            interval=value["interval"]
            need(type(interval)is dict and set(interval)=={"started","finished","clock"}
                 and interval["clock"]=="INJECTED_UI_CLOCK" and _finite(interval["started"])
                 and _finite(interval["finished"]) and 0<=interval["started"]
                 <=interval["finished"],"camera UI interval invalid")
        before=row["camera"]["before"]
        if phase=="after":
            need(before is not None and receipt["first_envelope_age"]>=before["entry"]["receipt"]["last_envelope_age"],
                 "camera observations reordered")
            need(receipt["source_pins"]==before["entry"]["receipt"]["source_pins"],"camera reader changed between samples")
            old=before["entry"]["observation"]
            if old["status"]==value["status"]=="OBSERVED":
                need(value["interval"]["started"]>=old["interval"]["finished"],"camera UI clock regressed between samples")
        entry={"version":1,"capture_id":row["request"]["id"],"capture_digest":request_digest(row["request"]),
               "phase":phase,"receipt":receipt,"transcript":transcript,"observation":value,
               "qualification":"SAMPLED_CONTEXT_ONLY_NOT_ACHIEVED_POSE"}
        raw=json.dumps(entry,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")
        need(len(raw)<=CAMERA_SAMPLE_BYTES and self.camera_bytes+len(raw)<=CAMERA_TOTAL_BYTES,"camera evidence byte capacity")
        path=self.root/(row["request"]["id"]+"-camera-"+phase+".json")
        self._current();_write_new(path,entry)
        row["camera"][phase]={"journal":str(path),"entry":entry,"sha256":hashlib.sha256(raw).hexdigest()}
        self.camera_bytes+=len(raw)
        if phase=="after":row["camera_collector"]=None

    @staticmethod
    def _camera_pair(row):
        a,b=row["camera"]["before"],row["camera"]["after"]
        status="PENDING"
        if a is not None and b is not None:
            av,bv=a["entry"]["observation"],b["entry"]["observation"]
            if av["status"]!="OBSERVED" or bv["status"]!="OBSERVED":status="UNAVAILABLE"
            else:
                keys=("rotation_native","follow_camera_base_offset_setting","external_reference_handle","external_view_active")
                status="SAME_SAMPLED_VALUES" if all(av[k]==bv[k] for k in keys) else "CHANGED"
        return {"status":status,"samples":copy.deepcopy(row["camera"]),"achieved_pose_qualified":False,
                "stable_throughout_qualified":False,"pixel_acquisition_time_qualified":False}

    def _native_status(self,correlation):
        need(self.active in self.records,"no active capture")
        row=self.records[self.active]
        need(correlation["id"]==row["request"]["id"] and
             correlation["digest"]==request_digest(row["request"]),"observer ticket rebound")
        need(row["stage"] in ("RUNNING","SETTLED"),"capture execution not observed")
        result=self._receipt(row,self.client.status(row["request"]["id"]))
        need(result["status"]=="running","native capture status became unqualified")
        return result

    def _frame(self,row):
        observed=self.observer.records[row["observer"]]
        _,roots=self.observer._roots()
        need(roots==observed["roots"],"capture roots rebound")
        return self.observer._frame(roots)

    def settle(self,handle):
        row=self._record(handle)
        need(row["stage"] in ("RUNNING","SETTLED"),"native execution not observed")
        try:
            receipt=self.observer.observe(row["observer"])
            if receipt["status"]=="PENDING":return receipt
            need(receipt["status"]=="NATIVE_CAPTURE_FILE_OBSERVED" and receipt["file_observed"] is True
                 and receipt["visual_pass"] is False,"file observation not settled")
            frame=self._frame(row)
            before=self.observer.records[row["observer"]]["before"]
            expected=copy.deepcopy(before["files"]);expected[receipt["path"]]=receipt["file"]
            need(frame["files"]==expected,"unqualified extra/changed file after settlement")
            for path,identity in before["directories"].items():
                if identity is not None:need(frame["directories"].get(path)==identity,"capture directory replaced")
            if row["settled_frame"] is not None:need(frame==row["settled_frame"],"settled frame changed")
            self._current()
            if row["settled"] is None:
                path,entry=self._log(row,"SETTLED",receipt=receipt,frame=frame)
                row['settled_journal']=path;row['settled_ref']=_FrameJournalRef.bind(path,entry)
                row["settled"]=copy.deepcopy(receipt);row["settled_frame"]=frame
            return copy.deepcopy(receipt)
        except Exception as exc:self._quarantine(row,exc);raise

    def _stored_frame(self,row):
        need(row['settled_ref'] is not None and row['settled_journal'] is not None,'settled journal absent')
        p=row['settled_journal']
        need(p.parent==self.root and p.name.startswith(row['request']['id']+'-') and p.name.endswith('-settled.json'),
             'settled frame journal substitution')
        return row['settled_ref'].load(p,self.identity,row['request']['id'],'frame')

    def _settled(self,row):
        need(row["settled"] is not None and row["settled_frame"] is not None,"opaque file settlement absent")
        need(self._stored_frame(row)==row['settled_frame'],'settled journal differs')
        actual=self.observer.observe(row["observer"])
        need(actual.get("file")==row["settled"]["file"] and actual.get("path")==row["settled"]["path"],
             "settled file changed")
        need(self._frame(row)==row["settled_frame"],"settled directory frame changed")
        self._current()

    def prepare_release(self,handle):
        row=self._record(handle)
        need(row["stage"]=="SETTLED" and row["release"] is None,"release already issued/not settled")
        try:
            self._settled(row)
            row["release"]={"version":1,"id":secrets.token_hex(16),"session":self.identity["session"],
                "provider":PROVIDER,"action":"release","binding":self.revision,
                "arguments":{"capture_id":row["request"]["id"],"capture_digest":request_digest(row["request"])}}
            self._log(row,"RELEASE_PREPARE_SENT",request=row["release"])
            result=self._receipt(row,self.client.prepare(row["release"]),release=True);row["last"]=result
            need(result["status"] in ("prepared","unknown"),"release preparation refused")
            if result["status"]=="prepared":self._log(row,"RELEASE_PREPARED")
            return copy.deepcopy(result)
        except Exception as exc:self._quarantine(row,exc);raise

    def release(self,handle):
        row=self._record(handle)
        need(row["stage"]=="RELEASE_PREPARED","release unprepared/already consumed")
        try:
            self._settled(row)
            self._log(row,"RELEASE_SENT")
            result=self._receipt(row,self.client.execute(row["release"]["id"]),release=True);row["last"]=result
            need(result["status"] in ("completed","running","unknown"),"release outcome unavailable")
            if result["status"]=="completed":self._released(row,result)
            return copy.deepcopy(result)
        except Exception as exc:self._quarantine(row,exc);raise

    def _released(self,row,result):
        o=result["observations"]
        need(result["reason"]=="RESERVATION_RELEASED_NOT_FILE_COMPLETION" and
             o.get("reservation_released") is True,"reservation release not observed")
        self._settled(row)
        self._log(row,"RELEASED",receipt=result,file_completion_qualified=False,visual_pass=False)
        self.observer._archive_released(row['observer'])
        row['settled_frame']=None
        self.active=None

    def receipt(self,handle):
        row=self._record(handle)
        return {"stage":row["stage"],"request":copy.deepcopy(row["request"]),
                "file_observation":copy.deepcopy(row["settled"]),"last_action":copy.deepcopy(row["last"]),
                "camera_observations":self._camera_pair(row),
                "reservation_released":row["stage"]=="RELEASED","visual_pass":False,"runtime_accepted":False}

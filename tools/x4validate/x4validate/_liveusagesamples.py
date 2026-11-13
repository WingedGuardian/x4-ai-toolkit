"""Owned read-only MD usage requests; retained samples are never reservations.

Uses existing action journals by reference. No gameplay action, retry, journal
reopening, caller-supplied usage dictionary or inferred fresh-zero state.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import secrets
import time
from ._liveactions import ActionJournal, _json, _read_json, validate_receipt, request_digest
from ._liveautomation import AutomationClient, _directory
from ._livecapacity import resolve
from ._liveevidence import ReadEvidence, _identity

FIXED={
    'capture':('x4-screen-capture-v1','capacity_observe','capture_history','capture_calls','capture_native_records'),
    'orbit':('x4-orbit-camera-v1','camera_observe','orbit_history','orbit_calls','orbit_history'),
    'roomentry':('x4-diagnostic-roomentry-v1','capacity_observe','roomentry_records','roomentry_calls','roomentry_records'),
}
SOURCES=('_liveusagesamples.py','_livecaptureplan.py','_liveactions.py','_liveautomation.py',
         '_livecapacity.py','_liveactionwire.py','_livebound.py','_liveevidence.py','_livesession.py','_livetrees.py')


def need(ok,message):
    if not ok:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def pins():
    root=Path(__file__).resolve().parent
    return {name:sha((root/name).read_bytes())for name in SOURCES}


def integer(value,maximum,label):
    need(type(value)is int and 0<=value<=maximum,'invalid '+label)
    return value


class CapacitySamples:
    """Fixed three-provider sampler, created only on an actual connected client.

    start prepares one request; execute sends its one read-only MD observation.
    status reconciles that same request. receipt never queries or implies current
    counters. Every new collection requires a new start, counted by SDK/Lua.
    """
    def __init__(self,client,frozen_receipts,expected_build,*,capacity_profile='campaign_v1',synthetic=False):
        need(type(client)is AutomationClient,'actual AutomationClient required')
        ReadEvidence(client,synthetic=synthetic)  # Existing production peer/link qualification.
        self.client=client;self.synthetic=synthetic;self.profile=resolve(capacity_profile)
        need(type(expected_build)is str and client.bound.build==expected_build,'expected helper build mismatch')
        self.build=expected_build;self.identity=_identity(client.identity())
        self.peer=client.bound.link.peer;self.profile_id=self.peer.profile_id
        self.sources=pins();self.receipts=copy.deepcopy(frozen_receipts)
        self.records={};self.latest={};self.active=None;self.fault=None;self.last_clock=time.monotonic()
        self.sequence=0;self.checkpoint=None
        self.roots={str(p):(p.stat().st_dev,p.stat().st_ino)for p in
                    (_directory(client.root),_directory(client.pending),_directory(client.actions))}
        self.bindings=self._bindings()
        self._current()

    def _bindings(self):
        from ._livecaptureplan import _receipts
        checked=_receipts(self.receipts,self.profile)
        need(set(checked)==set(FIXED),'all three frozen provider bindings required')
        values={}
        for kind,binding in checked.items():
            path=Path(binding['path'])
            with path.open('rb')as f:raw=f.read(4*1024**2+1)
            need(len(raw)<=4*1024**2 and sha(raw)==binding['sha256'],'provider receipt changed')
            r=json.loads(raw);provider,action,*_=FIXED[kind]
            md=r.get('native_revision')if kind=='capture'else r.get('md_revision')
            revision=r.get('revision')
            for v in (revision,md):
                need(type(v)is str and len(v)==64 and all(c in '0123456789abcdef'for c in v),'source revision missing')
            need(r.get('provider')==provider and action in r.get('actions',[]),'fixed source action unavailable')
            values[kind]={'provider':provider,'action':action,'revision':revision,'md_revision':md}
        return values

    def _current(self):
        need(self.fault is None,'usage collector quarantined')
        try:
            now=time.monotonic();need(now>=self.last_clock,'host monotonic clock regressed');self.last_clock=now
            need(self.client.identity()==self.identity and self.client.bound.build==self.build,'usage identity/build changed')
            need(self.client.bound.link.peer is self.peer and self.peer()==self.identity['peer']
                 and self.peer.profile_id==self.profile_id,'usage profile/peer changed')
            for path,identity in self.roots.items():
                p=_directory(path);need((p.stat().st_dev,p.stat().st_ino)==identity,'action journal root replaced')
            need(pins()==self.sources,'usage collector source changed')
            need(self._bindings()==self.bindings,'usage source binding changed')
            return now
        except Exception as exc:
            self.fault=str(exc);raise

    def _record(self,handle):
        self._current();need(handle in self.records,'opaque local usage handle required')
        row=self.records[handle]
        need(self.client._pending_request(row['request']['id'])==row['request'],'owned pending request changed')
        return row

    def start(self,kind):
        self._current();need(kind in FIXED and self.active is None,'fixed provider or unresolved sample')
        need(len(self.records)<self.profile['sdk_history'],'selected SDK history bound reached')
        b=self.bindings[kind]
        request=dict(version=1,id=secrets.token_hex(16),session=self.identity['session'],provider=b['provider'],
                     binding=b['revision'],action=b['action'],arguments={})
        handle=object();row={'kind':kind,'request':request,'executed':False,'status':'preparing','sample':None}
        self.records[handle]=row;self.latest[kind]=handle;self.active=handle;self.sequence+=1
        try:self._accept(row,self.client.prepare(request))
        except Exception as exc:self.fault=str(exc);raise
        self._current();return handle

    def execute(self,handle):
        row=self._record(handle)
        need(self.active is handle and not row['executed'] and row['status']=='prepared','usage not prepared or already consumed')
        row['executed']=True;self.sequence+=1  # Before transport; never resend.
        try:self._accept(row,self.client.execute(row['request']['id']))
        except Exception as exc:self.fault=str(exc);raise
        self._current();return self.receipt(handle)

    def status(self,handle):
        row=self._record(handle)
        if row['sample'] is None:
            self.sequence+=1
            try:self._accept(row,self.client.status(row['request']['id']))
            except Exception as exc:self.fault=str(exc);raise
        self._current();return self.receipt(handle)

    def _accept(self,row,receipt):
        value=validate_receipt(receipt,row['request']);row['status']=value['status']
        if value['status']!='completed':return
        need(row['executed'],'observation without own execute')
        self._parse(row,value)
        journal=ActionJournal(_directory(self.client.actions/row['request']['id']));history=journal._history()
        need(journal.request==row['request'] and history and history[-1]==value,'actual action journal receipt required')
        path=journal.path/f'observation-{len(history):06d}.json'
        raw=_json(value)+b'\n'
        with path.open('rb')as f:actual=f.read(65537)
        need(actual==raw,'action receipt bytes differ')
        row['sample']={'path':str(path),'sha256':sha(raw),'size':len(raw),'host_observed_at':self._current()}
        self.active=None

    def _parse(self,row,value):
        kind=row['kind'];b=self.bindings[kind];_,_,hist,calls,md_limit=FIXED[kind]
        o=value['observations'];core=o.get('action_core',{})
        need(core.get('provider_revision')==b['revision'] and core.get('protocol_fault')is False,'SDK provider/source fault')
        cap=o.get('capacity')
        need(type(cap)is dict and cap.get('profile')==self.profile['name'] and cap.get('revision')==self.profile['revision']
             and cap.get('source_sha256')==self.profile['source_sha256']
             and cap.get('scope')=='LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS','Lua capacity source mismatch')
        need(cap.get('history_limit')==self.profile[hist] and cap.get('calls_limit')==self.profile[calls],'Lua capacity limit')
        integer(cap.get('history_used'),self.profile[hist],'Lua history');integer(cap.get('calls_used'),self.profile[calls],'Lua calls')
        s=o if kind=='capture'else o.get('snapshot')
        need(type(s)is dict and s.get('revision')==b['md_revision'],'native source revision mismatch')
        need(s.get('capacity_profile')==self.profile['name'] and s.get('capacity_revision')==self.profile['revision'],'native capacity profile mismatch')
        need(s.get('world_token')==self.identity['world_token'] and s.get('public_revision')==self.identity['revision'],'native world/source mismatch')
        call=s.get('bridge_call')if kind=='capture'else s.get('call')
        generation=s.get('bridge_generation')if kind=='capture'else s.get('generation')
        need(type(call)is str and 0<len(call)<=128 and generation==self.identity['ui_generation'],'native call/generation mismatch')
        if kind=='capture':need(s.get('bridge_phase')=='capacity','native capacity phase')
        else:
            need(s.get('id')==row['request']['id'] and s.get('digest')==request_digest(row['request'])
                 and s.get('session')==self.identity['session'] and s.get('phase')==('snapshot'if kind=='orbit'else'capacity'),'native request correlation')
        need(s.get('md_limit')==self.profile[md_limit] and s.get('md_queue_limit')==64,'native limit mismatch')
        used=integer(s.get('md_used'),self.profile[md_limit],'MD retained records')
        queued=integer(s.get('md_queued'),63,'MD queued replies')
        need(type(s.get('md_busy'))is bool,'native busy unavailable')
        age=s.get('age');need(type(age)in(int,float) and 0<=age<2**53,'invalid MD player age')
        return dict(lua=copy.deepcopy(cap),md=dict(records_used=used,records_limit=self.profile[md_limit],busy=s['md_busy'],
            queued=queued,queue_limit=64,age=age,call=call,revision=b['md_revision'],
            scope='CURRENT_MD_WORLD_RETAINED_RECORDS_ALL_UI_GENERATIONS',queue_sample='BEFORE_APPENDING_OWN_REPLY'))

    def receipt(self,handle):
        row=self._record(handle);sample=row['sample']
        out=dict(status=row['status'],kind=row['kind'],request_id=row['request']['id'],identity=copy.deepcopy(self.identity),
            profile_id=self.profile_id,build=self.build,capacity_profile=copy.deepcopy(self.profile),
            evidence_tier='SYNTHETIC'if self.synthetic else'MACHINE_OBSERVED',source_binding=copy.deepcopy(self.bindings[row['kind']]),
            current_counts_verified=False,atomic=False,reserved=False,future_fit_verified=False)
        if sample is None:return out
        path=Path(sample['path']);need(not path.is_symlink() and not path.is_junction(),'linked sample journal')
        journal=ActionJournal(_directory(self.client.actions/row['request']['id']));history=journal._history()
        need(journal.request==row['request'],'sample request journal changed')
        with path.open('rb')as f:raw=f.read(65537)
        need(len(raw)==sample['size'] and sha(raw)==sample['sha256'],'sample journal changed')
        value=validate_receipt(json.loads(raw),row['request']);need(value in history,'sample absent from journal history')
        out.update(self._parse(row,value));out['journal']=dict(sample)
        out['host_sample_age_seconds']=self._current()-sample['host_observed_at']
        out['native_observation']='RETAINED_SAMPLE_NOT_FRESH_STATUS'
        with path.open('rb')as f:need(f.read(65537)==raw,'sample journal changed during read')
        self._current();return out

    def _journal_frame(self):
        """Membership only: supported action journals append, never rewrite.

        This detects later known-provider requests, dispatch and status records.
        It is not attestation of arbitrary other journal contents or concurrency.
        """
        paths=list(self.client.pending.iterdir())
        need(len(paths)<=self.profile['sdk_history'],'pending journal exceeds selected SDK bound')
        rows=[]
        for path in paths:
            need(path.is_file() and not path.is_symlink() and path.suffix=='.json','unexpected pending journal member')
            request=self.client._pending_request(path.stem)
            if request['provider'] not in {v[0]for v in FIXED.values()}:continue
            folder=self.client.actions/request['id'];members=[]
            if folder.exists():
                _directory(folder)
                for p in folder.iterdir():
                    need(p.is_file() and not p.is_symlink(),'unexpected action journal member')
                    members.append(p.name)
                    need(len(members)<=2052,'action journal membership bound')
            rows.append((request['id'],request_digest(request),sorted(members)))
        return sha(_json(sorted(rows)))

    def capture_global_usage(self,evidence):
        """Issue the fixed global counter read AFTER the latest three samples."""
        self._current()
        need(type(evidence)is ReadEvidence and evidence.client is self.client,'same-client ReadEvidence required')
        need(evidence.synthetic==self.synthetic and self.active is None and set(self.latest)==set(FIXED),
             'three completed samples and matching evidence tier required')
        for h in self.latest.values():need(self.receipt(h)['status']=='completed','sample incomplete')
        frame=self._journal_frame();sequence=self.sequence;latest=dict(self.latest)
        h=evidence.collect('automation_capacity')
        need(self.sequence==sequence and self.latest==latest and self._journal_frame()==frame,'sample collection changed during global read')
        self._current()
        self.checkpoint=(evidence,h,sequence,latest,frame)
        return h

    def global_evidence(self,handles):
        self._current();need(self.checkpoint is not None,'ordered global counter sample absent')
        e,h,sequence,latest,frame=self.checkpoint
        need(type(handles)is dict and handles==latest and self.latest==latest and self.sequence==sequence
             and self.active is None and self._journal_frame()==frame,'sample ordering invalidated by later known action')
        need(e.client is self.client and e.synthetic==self.synthetic,'counter evidence owner changed')
        return e,h

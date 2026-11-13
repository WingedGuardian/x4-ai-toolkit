"""Fixed host orchestration for agent-reviewed dialogue input. Runtime UNQUALIFIED.

No launch/focus/automatic replay. Capture and input are explicit separate methods.
Use an existing production AutomationClient; this is not a raw command/eval port.
"""
from __future__ import annotations
from ._livetrees import journal_refusal, overlaps
import copy
import hashlib
import os
from pathlib import Path
import re
import secrets
import threading
import time
from ._liveactions import _write_new, _read_json
from ._liveclipport import connect_clips
from ._livedialoginput import DialogInput, InputUnavailable, need, finite
from ._livepersistence import _route, _file


SOURCES=('_livedialogport.py','_livedialoginput.py','_liveclipport.py','_liveclip.py',
         '_livecapture.py','_liveactions.py','_liveevidence.py','_liveautomation.py',
         '_livebound.py','_livepeerprofile.py','_livesession.py','_livereadpages.py','_livepersistence.py')


def pins():
    root=Path(__file__).resolve().parent
    return {n:hashlib.sha256((root/n).read_bytes()).hexdigest()for n in SOURCES}


class DialogPort:
    def __init__(self,engine,journal,*,synthetic=False,clock=time.monotonic,sleep=time.sleep):
        need(isinstance(engine,DialogInput) and type(synthetic)is bool and engine.synthetic is synthetic,
             'actual bound input engine and matching evidence tier required')
        need(synthetic or (clock is time.monotonic and sleep is time.sleep),'production host clocks cannot be injected')
        self.engine,self.root=engine,_route(journal,directory=True)
        why=journal_refusal(self.root,profile=engine.clips.profile)
        need(why is None,'port journal cannot be in personal tree: '+str(why))
        for other in (engine.root,engine.clips.root,engine.clips.recorder.journal):
            need(not overlaps(self.root,other),
                 'port and evidence journals must be separate nonnested directories')
        self.directory=self.root.stat().st_dev,self.root.stat().st_ino
        self.synthetic,self.clock,self.sleep=synthetic,clock,sleep
        self.lock=threading.Lock();self.records={};self.closed=False;self.last=None
        self.sources=pins();self.owner={'version':1,'source_pins':self.sources,'identity':engine.initial,
             'build':engine.build,'tier':'SYNTHETIC' if synthetic else 'LOCAL_AGENT_REVIEWED',
             'scope':'EXPLICIT_CAPTURE_REVIEW_DIGIT_INPUT','runtime_qualified':False}
        engine._current();_write_new(self.root/'dialog-port-owner.json',self.owner)

    def _now(self):
        t=self.clock();need(finite(t) and (self.last is None or t>=self.last),'port monotonic clock')
        self.last=t;return t

    def _current(self):
        need(not self.closed,'dialog port retired')
        self.engine._current();need(pins()==self.sources,'host orchestration source drift')
        need(_route(self.root,directory=True)==self.root and
             (self.root.stat().st_dev,self.root.stat().st_ino)==self.directory and
             _read_json(self.root/'dialog-port-owner.json')==self.owner,'port owner journal drift')
        self._now()

    def _row(self,token,stage=None):
        need(type(token)is str and re.fullmatch('[0-9a-f]{32}',token) and token in self.records,'local opaque token required')
        row=self.records[token]
        need(stage is None or row['stage']==stage,'wrong or consumed operation stage')
        return row

    def _log(self,token,row,stage,**evidence):
        row['stage']=stage
        _write_new(self.root/(token+'-'+stage.lower()+'.json'),{
            'version':1,'id':token,'stage':stage,'identity':self.engine.initial,'build':self.engine.build,
            'tier':'SYNTHETIC' if self.synthetic else 'LOCAL_AGENT_REVIEWED',**evidence})

    def _finish(self,row,value):
        try:self._current()
        except Exception:
            row['stage']='QUARANTINED'
            raise
        return value

    def _context(self):
        """One fixed snapshot send. Poll status only; never retry preparation/send."""
        handle=self.engine.begin_actor();start=self._now()
        for _ in range(48):
            self._current();need(self._now()-start<=2.25,'native snapshot polling deadline')
            value=self.engine.context(handle)
            if value is not None:return value
            self.sleep(0.05)
        raise InputUnavailable('native snapshot incomplete; no automatic retry')

    def capture(self,case):
        """Explicit five-second HWND clip and decoded PNG for agent inspection."""
        need(self.lock.acquire(blocking=False),'dialog operation already active')
        token=None
        try:
            self._current();need(type(case)is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',case),'case label required')
            need(len(self.records)<32,'dialog capture capacity')
            need(len(self.engine.frames)<16,'input engine frame capacity; no pointless capture')
            token=secrets.token_hex(16);row={'stage':'RESERVED'};self.records[token]=row
            self._log(token,row,'RESERVED',case=case)
            context=self._context()
            clip=self.engine.clips.reserve(case,duration=5,fps=5)
            self._log(token,row,'CAPTURING') # journal before the explicit host capture
            self.engine.clips.execute(clip)
            frame=self.engine.frame(clip,context);raw=self.engine.image(frame)
            need(type(raw)is bytes and 0<len(raw)<=32*1024**2,'decoded image byte bound')
            path=self.root/(token+'.png')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
            with os.fdopen(fd,'wb')as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            file=_file(path,32*1024**2);need(file['sha256']==hashlib.sha256(raw).hexdigest(),'review image readback differs')
            self._current();row.update(frame=frame,path=path,file=file,context=context)
            self._log(token,row,'REVIEW_REQUIRED',image_path=str(path),image_file=file,
                      frame_sha256=file['sha256'],source_context=self.engine.contexts[context]['projection'],
                      visual_verdict='NOT_REVIEWED',capture_automatic=False)
            return {'token':token,'image_path':str(path),'image_sha256':file['sha256'],
                    'status':'REVIEW_REQUIRED','expected_actor_label':self.engine.label,
                    'evidence_tier':'SYNTHETIC' if self.synthetic else 'LOCAL_TRUSTED_CAPTURE',
                    'native_context':copy.deepcopy(self.engine.contexts[context]['projection']),
                    'rendered_verified':False}
        except Exception:
            if token is not None:self.records[token]['stage']='QUARANTINED'
            raise
        finally:self.lock.release()

    def review(self,token,*,reviewer,transcript):
        need(self.lock.acquire(blocking=False),'dialog operation already active')
        try:
            self._current();row=self._row(token,'REVIEW_REQUIRED')
            need(_file(row['path'],32*1024**2)==row['file'],'exported review image changed')
            handle=self.engine.review(row['frame'],reviewer=reviewer,transcript=transcript)
            row['review']=handle
            self._log(token,row,'REVIEWED',review_record=str(self.engine.reviews[handle]['path']),
                      review_tier='AGENT_REVIEWED',judgment_verified=False)
            return self._finish(row,{'token':token,'status':'REVIEWED','judgment_verified':False})
        finally:self.lock.release()

    def choose(self,token):
        """Explicit one-choice action. Fresh contexts and journal precede dispatch."""
        need(self.lock.acquire(blocking=False),'dialog operation already active')
        row=None
        try:
            self._current();row=self._row(token,'REVIEWED')
            need(_file(row['path'],32*1024**2)==row['file'],'review image changed')
            self._log(token,row,'PREPARING_INPUT') # prevents replay even if later preparation fails
            ticket=self.engine.prepare(row['review'],self._context());row['input']=ticket
            self._log(token,row,'INPUT_REQUESTED',request_id=self.engine.tickets[ticket]['journal'].request['id'])
            result=self.engine.execute(ticket,self._context())
            row['result']=result;self._log(token,row,'INPUT_OBSERVED',receipt=result)
            return self._finish(row,{'token':token,'status':'INPUT_OBSERVED','receipt':copy.deepcopy(result),'selection_verified':False})
        except Exception:
            if row is not None:row['stage']='QUARANTINED'
            raise
        finally:self.lock.release()

    def release_owned(self,token):
        """Explicit separate keyup request only; never called by choose()."""
        need(self.lock.acquire(blocking=False),'dialog operation already active')
        try:
            self._current();row=self._row(token)
            need('input'in row and not row.get('release_reserved'),'no local input or release already requested')
            row['release_reserved']=True
            release=self.engine.prepare_release(row['input']);row['release']=release
            self._log(token,row,'RELEASE_REQUESTED',request_id=self.engine.releases[release]['journal'].request['id'])
            result=self.engine.execute_release(release)
            row['release_result']=result
            self._log(token,row,'RELEASE_OBSERVED',receipt=result)
            return self._finish(row,{'token':token,'status':'RELEASE_OBSERVED','receipt':result,'cleanup_verified':False})
        finally:self.lock.release()

    def outcome(self,token):
        """Explicit later native observation; no new capture or selection dispatch."""
        need(self.lock.acquire(blocking=False),'dialog operation already active')
        try:
            self._current();row=self._row(token);need('input'in row,'no input attempt to observe')
            handle=self.engine.begin_actor();start=self._now()
            for _ in range(48):
                need(self._now()-start<=2.25,'outcome polling deadline')
                value=self.engine.outcome(row['input'],handle)
                if value['status']!='PENDING':
                    row['outcome']=value
                    return self._finish(row,value)
                self.sleep(0.05)
            raise InputUnavailable('outcome incomplete; no automatic input replay')
        finally:self.lock.release()

    def close(self):
        need(self.lock.acquire(blocking=False),'dialog operation active')
        try:self.closed=True
        finally:self.lock.release()

    def status(self,token):
        """Retained local state only; does not query the game or retry anything."""
        need(self.lock.acquire(blocking=False),'dialog operation active')
        try:
            row=self._row(token)
            return {'token':token,'stage':row['stage'],'closed':self.closed,
                    'receipt':copy.deepcopy(row.get('result')),'scope':'RETAINED_LOCAL_STATE',
                    'release_receipt':copy.deepcopy(row.get('release_result')),
                    'outcome':copy.deepcopy(row.get('outcome')),
                    'selection_verified':False}
        finally:self.lock.release()


def connect_dialog(client,profile,journals,*,ffmpeg,ffmpeg_sha256,observer_revision,
                   expected_actor,expected_actor_label):
    """Production entrypoint using an already connected AutomationClient.

    Four existing task-owned nonnested directories; connect queries native profile
    context and writes evidence journals. It never captures, inputs or launches.
    """
    need(type(journals)is dict and set(journals)=={'port','input','clip_port','clip_recorder'},'four exact journal roots required')
    roots={k:_route(v,directory=True)for k,v in journals.items()}
    for a,p in roots.items():
        for b,q in roots.items():
            if a!=b:need(not overlaps(p,q),'distinct nonnested journal roots required')
    clips=connect_clips(client,profile,{'port':roots['clip_port'],'recorder':roots['clip_recorder']},
                        ffmpeg=ffmpeg,ffmpeg_sha256=ffmpeg_sha256)
    engine=DialogInput(client,clips,roots['input'],observer_revision=observer_revision,
                       expected_actor=expected_actor,expected_actor_label=expected_actor_label)
    return DialogPort(engine,roots['port'])

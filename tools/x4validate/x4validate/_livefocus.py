"""Explicit exact-process restore/foreground action; never a query side effect.

MS Learn: winuser/nf-winuser-showwindowasync (posted, not completed),
nf-winuser-showwindow (SW_RESTORE=9), nf-winuser-setforegroundwindow
(OS can deny), nf-winuser-getforegroundwindow (sample, possibly NULL).
No AttachThreadInput, injected keys, foreground-lock changes or hidden retries.
"""
from __future__ import annotations
import copy
import ctypes
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
from ._liveactions import ActionJournal, _write_new, _read_json, request_digest
from ._liveautomation import AutomationClient
from ._livetrees import journal_refusal
from ._livebound import BoundPipe
from ._liveclip import window_for_peer
from ._liveevidence import _identity
from ._livepeerprofile import ProfilePeer
from ._livepersistence import _route
from ._livesession import SessionLink
from ._livedialoginput import finite


class FocusUnavailable(ValueError):pass


def need(ok,why):
    if not ok:raise FocusUnavailable(why)


SOURCES=('_livefocus.py','_liveclip.py','_liveactions.py','_liveautomation.py',
         '_livebound.py','_liveevidence.py','_livepeerprofile.py','_livesession.py',
         '_livepersistence.py','_livedialoginput.py')


def pins():
    root=Path(__file__).resolve().parent
    return {n:hashlib.sha256((root/n).read_bytes()).hexdigest()for n in SOURCES}


class WindowsFocus:
    def __init__(self):
        need(os.name=='nt','Windows focus backend required')
        from ctypes import wintypes as W
        self.W=W;self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId.restype=W.DWORD
        self.u.IsWindowEnabled.argtypes=[W.HWND];self.u.IsWindowEnabled.restype=W.BOOL
        self.u.GetForegroundWindow.argtypes=[];self.u.GetForegroundWindow.restype=W.HWND
        self.u.ShowWindowAsync.argtypes=[W.HWND,ctypes.c_int];self.u.ShowWindowAsync.restype=W.BOOL
        self.u.SetForegroundWindow.argtypes=[W.HWND];self.u.SetForegroundWindow.restype=W.BOOL

    def state(self,peer):
        first=int(self.u.GetForegroundWindow() or 0)
        w=window_for_peer(peer,allow_minimized=True)
        pid=self.W.DWORD();thread=int(self.u.GetWindowThreadProcessId(int(w['hwnd']),ctypes.byref(pid)))
        need(thread>0 and pid.value==peer['pid'] and self.u.IsWindowEnabled(int(w['hwnd'])),
             'window thread/PID/eligibility unavailable')
        second=int(self.u.GetForegroundWindow() or 0)
        need(first==second,'foreground changed during sample')
        # Reuses complete independent process incarnation and unique-HWND check.
        need(window_for_peer(peer,allow_minimized=True)==w,'window changed during sample')
        return {'window':w,'thread':thread,'foreground':str(second)}

    def restore(self,state):
        return bool(self.u.ShowWindowAsync(int(state['window']['hwnd']),9))

    def foreground(self,state):
        return bool(self.u.SetForegroundWindow(int(state['window']['hwnd'])))


class FocusPort:
    """One explicit prepare/execute; status is local, not a replay or permission.

    Uses a previously established session and its exact connected ProfilePeer.
    No native MD read before restore: minimized X4 may not service that read.
    A fresh bound ping is mandatory AFTER OS observations. It may refuse a load
    that occurred while minimized; such a refusal never renews the session.
    """
    def __init__(self,client,journal,*,synthetic=False,backend=None,clock=time.monotonic,sleep=time.sleep):
        need(isinstance(client,AutomationClient) and type(synthetic)is bool,'bound automation client required')
        need(synthetic or (client._native_connection is True and isinstance(client.bound,BoundPipe)
             and isinstance(client.bound.link,SessionLink) and isinstance(client.bound.link.peer,ProfilePeer)),
             'production ProfilePeer/BoundPipe required')
        need(synthetic or (backend is None and clock is time.monotonic and sleep is time.sleep),
             'production backend/clocks cannot be injected')
        self.client,self.peer,self.synthetic=client,client.bound.link.peer,synthetic
        self.root=_route(journal,directory=True);self.directory=(self.root.stat().st_dev,self.root.stat().st_ino)
        why=journal_refusal(self.root);need(why is None,'focus journal overlap: '+str(why))
        self.clock,self.sleep,self.last=clock,sleep,None
        self.initial=_identity(client.identity());self.profile=self.peer.profile_id;self.build=client.bound.build
        need(self.peer()==self.initial['peer'],'connected process differs')
        self.backend=backend if backend is not None else WindowsFocus()
        self.sources=pins();self.records={};self.lock=threading.Lock();self.closed=False
        self.owner={'version':1,'identity':self.initial,'profile_id':self.profile,'build':self.build,
            'source_pins':self.sources,'tier':'SYNTHETIC' if synthetic else 'LOCAL_HOST_ACTION',
            'scope':'EXPLICIT_RESTORE_FOREGROUND_ONLY','runtime_qualified':False}
        _write_new(self.root/'focus-owner.json',self.owner)

    def _now(self):
        t=self.clock();need(finite(t) and (self.last is None or t>=self.last),'focus monotonic clock')
        self.last=t;return t

    def _current(self):
        need(not self.closed,'focus owner retired')
        need(pins()==self.sources and _route(self.root,directory=True)==self.root and
             (self.root.stat().st_dev,self.root.stat().st_ino)==self.directory and
             _read_json(self.root/'focus-owner.json')==self.owner,'focus sources/owner drift')
        need(self.client.identity()==self.initial and self.peer()==self.initial['peer'] and
             self.peer.profile_id==self.profile and self.client.bound.build==self.build,'focus peer/profile/session changed')
        self._now()

    def _state(self,expected=None):
        self._current();s=self.backend.state(self.initial['peer']);w=s.get('window',{})
        need(type(s)is dict and set(s)=={'window','thread','foreground'} and type(w)is dict and
             set(w)=={'hwnd','pid','width','height','minimized','scope'} and
             w['scope']=='OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED' and type(w['minimized'])is bool and
             type(w['pid'])is int and w['pid']==self.initial['peer']['pid'] and
             type(w['hwnd'])is str and w['hwnd'].isdigit() and 0<int(w['hwnd'])<2**64 and
             type(s['foreground'])is str and s['foreground'].isdigit() and 0<=int(s['foreground'])<2**64 and
             type(s['thread'])is int and s['thread']>0 and
             all(type(w[k])is int and 0<=w[k]<=limit for k,limit in [('width',7680),('height',4320)]) and
             (w['minimized'] or (w['width']>0 and w['height']>0)),'window metadata unqualified')
        if expected is not None:
            need((w['hwnd'],w['pid'],s['thread'])==(expected['window']['hwnd'],expected['window']['pid'],expected['thread']),
                 'prepared HWND/thread changed')
        self._current();return copy.deepcopy(s)

    def prepare(self):
        need(self.lock.acquire(blocking=False),'focus operation active')
        try:
            self._current();need(len(self.records)<32,'focus request capacity')
            state=self._state();age=self._now()
            request={'version':1,'id':secrets.token_hex(16),'session':self.initial['session'],
                'provider':'x4-host-focus-v1','action':'restore_foreground','binding':self.sources['_livefocus.py'],
                'arguments':{'hwnd':state['window']['hwnd'],'pid':state['window']['pid'],'profile':self.profile}}
            receipt={'version':1,'id':request['id'],'session':request['session'],'digest':request_digest(request),
                'status':'prepared','reason':'exact connected game process/window; no fresh MD claim yet',
                'observations':{'before':state,'gameplay_ready':False,'runtime_qualified':False}}
            journal=ActionJournal.create(self.root,request,receipt)
            h=object();self.records[h]={'journal':journal,'state':state,'age':age,'consumed':False,'result':None}
            return h
        finally:self.lock.release()

    def execute(self,handle):
        need(self.lock.acquire(blocking=False),'focus operation active')
        try:
            need(type(handle)is object and handle in self.records,'local focus ticket required')
            r=self.records[handle];need(not r['consumed'],'focus ticket consumed; never retry')
            need(self._state()==r['state'] and self._now()-r['age']<=5,'focus prepared state expired/changed')
            r['consumed']=True;outer=self
            class Transport:
                def execute(self,request):
                    attempted={'restore':False,'foreground':False};returns={};post=None;envelope=None;qualified=False
                    start=outer._now();reason='focus observation incomplete'
                    try:
                        need(outer._state()==r['state'] and outer._now()-r['age']<=5,'final focus preflight changed')
                        state=r['state']
                        if state['window']['minimized']:
                            attempted['restore']=True;returns['restore']=outer.backend.restore(state)
                            need(type(returns['restore'])is bool and returns['restore'],'restore request denied/unknown')
                            state=outer._poll(r['state'],lambda s:not s['window']['minimized'])
                        need(state['foreground']in(r['state']['foreground'],state['window']['hwnd']),
                             'foreign focus changed during restore')
                        if state['foreground']!=state['window']['hwnd']:
                            attempted['foreground']=True;returns['foreground']=outer.backend.foreground(state)
                            need(type(returns['foreground'])is bool and returns['foreground'],'foreground request denied/unknown')
                        post=outer._poll(r['state'],lambda s:not s['window']['minimized'] and s['foreground']==s['window']['hwnd'])
                        # BoundPipe performs source build, load/UI stamp, peer and age validation.
                        reply=outer.client.bound.ask('ping');envelope=copy.deepcopy(outer.client.bound.last_envelope)
                        need(reply.status=='OK' and type(reply.payload)is str and reply.payload.startswith('pong\t'),
                             'fresh bound ping unavailable')
                        after=outer._state(r['state'])
                        need(after==post and outer._now()-start<=15,'post-ping foreground/state/deadline changed')
                        outer._current();qualified=True;reason='sampled foreground plus independent bound ping observed'
                    except Exception as exc:reason=str(exc)[:512]
                    receipt={'version':1,'id':request['id'],'session':request['session'],'digest':request_digest(request),
                        'status':'completed' if qualified else 'unknown','reason':reason,
                        'observations':{'attempted':attempted,'native_returns':returns,'post':post,'bound_envelope':envelope,
                            'scope':'SAMPLED_FOREGROUND_AND_BOUND_PING_ONLY','tier':'SYNTHETIC' if outer.synthetic else 'MACHINE_OBSERVED',
                            'focus_observed':qualified,'gameplay_ready':False,'runtime_qualified':False,
                            'atomic_targeting':False,'automatic_retry':False,'dependent_actions_blocked':not qualified}}
                    r['result']=copy.deepcopy(receipt);return receipt
            return r['journal'].execute(Transport())
        finally:self.lock.release()

    def _poll(self,expected,predicate):
        start=self._now()
        for _ in range(41):
            need(self._now()-start<=2,'focus observation deadline')
            state=self._state(expected)
            need(self._now()-start<=2,'focus observation exceeded deadline')
            if predicate(state):return state
            self.sleep(0.05)
        raise FocusUnavailable('focus observation incomplete; no automatic retry')

    def status(self,handle):
        need(type(handle)is object and handle in self.records,'local focus ticket required')
        return copy.deepcopy(self.records[handle]['result'])

    def close(self):
        need(self.lock.acquire(blocking=False),'focus operation active')
        try:self.closed=True
        finally:self.lock.release()

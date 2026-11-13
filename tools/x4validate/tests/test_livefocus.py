"""Fake Win32/client only. Never restores/focuses windows or queries a game."""
import copy
import json
import sys
from types import SimpleNamespace
import pytest
from x4validate import _livefocus as m
import test_livedialoginput as fixtures


class Backend:
    def __init__(self,c):
        self.c=c;self.calls=[];self.bad=None;self.restore_result=True;self.foreground_result=True;self.delay=0;self.pending=False
        self.s={'window':{'hwnd':'23','pid':42,'width':160,'height':120,'minimized':True,
                         'scope':'OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED'},'thread':8,'foreground':'9'}
    def state(self,peer):
        assert peer==self.c.i['peer']
        if self.bad:raise m.FocusUnavailable(self.bad)
        if self.pending:
            self.delay-=1
            if self.delay<=0:self.s['window']['minimized']=False
        return copy.deepcopy(self.s)
    def restore(self,state):
        self.calls.append('restore')
        if self.restore_result=='error':raise OSError('unknown restore result')
        if self.restore_result is True:
            self.pending=True
            if self.delay==0:self.s['window']['minimized']=False
        return self.restore_result
    def foreground(self,state):
        self.calls.append('foreground')
        if self.foreground_result=='error':raise OSError('unknown foreground result')
        if self.foreground_result is True:self.s['foreground']=self.s['window']['hwnd']
        return self.foreground_result


def setup(tmp,monkeypatch):
    c=fixtures.Client();b=Backend(c);c.bound.last_envelope=None;c.pings=0
    def ask(verb):
        assert verb=='ping';c.pings+=1;b.calls.append('ping')
        c.bound.last_envelope={'version':1,'stamp':c.stamp(),'build':c.bound.build,'age':4,'status':'OK','payload':'pong\t4'}
        return SimpleNamespace(status='OK',payload='pong\t4')
    c.bound.ask=ask;root=tmp/'focus';root.mkdir()
    f=m.FocusPort(c,root,synthetic=True,backend=b,clock=lambda:c.t,sleep=lambda dt:setattr(c,'t',c.t+dt))
    return f,c,b


def test_minimized_same_game_restore_foreground_then_one_bound_ping(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);h=f.prepare();assert b.calls==[] and c.pings==0
    out=f.execute(h);assert b.calls==['restore','foreground','ping']
    assert out['status']=='completed' and out['observations']['focus_observed']
    assert not out['observations']['gameplay_ready'] and not out['observations']['runtime_qualified']
    assert (f.records[h]['journal'].path/'dispatch.json').is_file()
    with pytest.raises(m.FocusUnavailable,match='consumed'):f.execute(h)
    assert b.calls==['restore','foreground','ping']


def test_already_foreground_still_requires_fresh_ping_no_effect_calls(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);b.s['window']['minimized']=False;b.s['foreground']='23'
    out=f.execute(f.prepare());assert b.calls==['ping'] and out['status']=='completed'
    assert out['observations']['attempted']=={'restore':False,'foreground':False}


def test_delayed_restore_observed_not_assumed_from_acceptance(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);b.delay=4
    out=f.execute(f.prepare());assert out['status']=='completed' and c.t>100
    assert b.calls==['restore','foreground','ping']


def test_restore_timeout_consumes_no_foreground_or_ping(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);b.delay=1000
    out=f.execute(f.prepare());assert out['status']=='unknown' and b.calls==['restore'] and c.pings==0
    assert out['observations']['dependent_actions_blocked']


def test_slow_successful_observation_does_not_escape_deadline(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);original=b.state
    def slow(peer):
        s=original(peer)
        if b.calls:c.t+=2.01
        return s
    b.state=slow;out=f.execute(f.prepare())
    assert out['status']=='unknown' and b.calls==['restore'] and c.pings==0
    assert 'exceeded deadline'in out['reason']


@pytest.mark.parametrize('phase,result',[('restore',False),('restore',1),('restore','error'),
                                        ('foreground',False),('foreground',1),('foreground','error')])
def test_os_denial_malformed_or_exception_never_qualifies(tmp_path,monkeypatch,phase,result):
    f,c,b=setup(tmp_path,monkeypatch);setattr(b,phase+'_result',result)
    out=f.execute(f.prepare());assert out['status']=='unknown' and c.pings==0
    assert out['observations']['attempted'][phase] is True
    assert not out['observations']['focus_observed']


@pytest.mark.parametrize('change',['pid','creation','profile','window','thread','foreground','minimized','owner','sources','expired'])
def test_changed_prepared_binding_refuses_before_effect(tmp_path,monkeypatch,change):
    f,c,b=setup(tmp_path,monkeypatch);h=f.prepare()
    if change=='pid':c.i['peer']['pid']=99
    if change=='creation':c.i['peer']['creation_filetime']='101'
    if change=='profile':c.bound.link.peer.profile_id='999'
    if change=='window':b.s['window']['hwnd']='24'
    if change=='thread':b.s['thread']=9
    if change=='foreground':b.s['foreground']='10'
    if change=='minimized':b.s['window']['minimized']=False
    if change=='owner':(f.root/'focus-owner.json').write_text('{}')
    if change=='sources':monkeypatch.setattr(m,'pins',lambda:{})
    if change=='expired':c.t+=5.01
    with pytest.raises((m.FocusUnavailable,ValueError)):f.execute(h)
    assert b.calls==[]


@pytest.mark.parametrize('bad',['ambiguous game windows','foreign process window','disabled game window'])
def test_window_resolver_refusal_no_calls(tmp_path,monkeypatch,bad):
    f,c,b=setup(tmp_path,monkeypatch);b.bad=bad
    with pytest.raises(m.FocusUnavailable):f.prepare()
    assert b.calls==[]


def test_focus_race_during_restore_stops_before_activation(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch);original=b.restore
    def changed(s):r=original(s);b.s['foreground']='10';return r
    b.restore=changed;out=f.execute(f.prepare())
    assert out['status']=='unknown' and b.calls==['restore']


@pytest.mark.parametrize('bad',['stamp','focus','time','owner'])
def test_post_focus_freshness_failure_retains_attempts(tmp_path,monkeypatch,bad):
    f,c,b=setup(tmp_path,monkeypatch);original=c.bound.ask
    def changed(v):
        r=original(v)
        if bad=='stamp':raise ValueError('actual bound reader refused changed native stamp')
        if bad=='focus':b.s['foreground']='11'
        if bad=='time':c.t+=16
        if bad=='owner':(f.root/'focus-owner.json').write_text('{}')
        return r
    c.bound.ask=changed;h=f.prepare();out=f.execute(h)
    assert out['status']=='unknown' and out['observations']['attempted']=={'restore':True,'foreground':True}
    assert b.calls==['restore','foreground','ping'] and f.status(h)==out


def test_no_production_injection_or_missing_connection(tmp_path,monkeypatch):
    c=fixtures.Client();root=tmp_path/'focus';root.mkdir()
    with pytest.raises(m.FocusUnavailable,match='production'):m.FocusPort(c,root,backend=Backend(c))
    assert list(root.iterdir())==[]


@pytest.mark.parametrize('kw',[{'backend':object()},{'clock':lambda:1},{'sleep':lambda _:None}])
def test_production_injections_refuse_with_real_type_shells_before_writes(tmp_path,kw):
    c=object.__new__(m.AutomationClient);c._native_connection=True
    c.bound=object.__new__(m.BoundPipe);c.bound.link=object.__new__(m.SessionLink)
    c.bound.link.peer=object.__new__(m.ProfilePeer)
    root=tmp_path/'focus';root.mkdir()
    with pytest.raises(m.FocusUnavailable,match='cannot be injected'):m.FocusPort(c,root,**kw)
    assert list(root.iterdir())==[]


@pytest.mark.parametrize('mode',['correct','wrongstamp','wrongbuild'])
def test_actual_boundpipe_validates_postfocus_reply(tmp_path,monkeypatch,mode):
    f,c,b=setup(tmp_path,monkeypatch)
    class Link(m.SessionLink):
        def __init__(self):self.peer=c.bound.link.peer
        def identity(self):return c.identity()
    class Pipe:
        def ask(self,verb,*args):
            assert verb=='bound' and args[-1]=='ping';c.pings+=1;b.calls.append('bound-ping')
            v={'version':1,'stamp':c.stamp(),'build':'d'*8,'age':10,'status':'OK','payload':'pong\t10'}
            if mode=='wrongstamp':v['stamp']['session']='e'*32
            if mode=='wrongbuild':v['build']='0'*8
            return SimpleNamespace(seq=1,status='OK',payload=json.dumps(v))
    c.bound=m.BoundPipe(Pipe(),Link(),'d'*8)
    out=f.execute(f.prepare())
    assert b.calls==['restore','foreground','bound-ping'] and c.pings==1
    assert out['status']==('completed'if mode=='correct'else'unknown')
    assert out['observations']['focus_observed']is(mode=='correct')


def test_close_and_foreign_ticket_never_focus(tmp_path,monkeypatch):
    f,c,b=setup(tmp_path,monkeypatch)
    with pytest.raises(m.FocusUnavailable):f.execute(object())
    h=f.prepare();f.close()
    with pytest.raises(m.FocusUnavailable,match='retired'):f.execute(h)
    assert b.calls==[]


@pytest.mark.skipif(sys.platform!='win32',reason='Windows ctypes signature control using fake DLL only')
def test_native_backend_exact_calls_with_fake_win32(monkeypatch):
    import ctypes
    from ctypes import wintypes as W
    class Fn:
        def __init__(self,f):self.f=f
        def __call__(self,*a):return self.f(*a)
    calls=[];u=SimpleNamespace()
    u.GetForegroundWindow=Fn(lambda:9);u.IsWindowEnabled=Fn(lambda h:True)
    def pid(h,p):ctypes.cast(p,ctypes.POINTER(W.DWORD)).contents.value=42;return 8
    u.GetWindowThreadProcessId=Fn(pid)
    u.ShowWindowAsync=Fn(lambda h,flag:calls.append(('show',h,flag))or 1)
    u.SetForegroundWindow=Fn(lambda h:calls.append(('foreground',h))or 1)
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**k:u)
    peer={'pid':42,'creation_filetime':'1','image':'X4.exe'}
    window={'hwnd':'23','pid':42,'width':0,'height':0,'minimized':True,'scope':'OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED'}
    def resolve(p,*,allow_minimized):assert p==peer and allow_minimized is True;return copy.deepcopy(window)
    monkeypatch.setattr(m,'window_for_peer',resolve)
    backend=m.WindowsFocus();assert calls==[]
    state=backend.state(peer);assert calls==[]
    assert backend.restore(state) is True and backend.foreground(state) is True
    assert calls==[('show',23,9),('foreground',23)]
    assert u.ShowWindowAsync.argtypes==[W.HWND,ctypes.c_int] and u.SetForegroundWindow.restype==W.BOOL

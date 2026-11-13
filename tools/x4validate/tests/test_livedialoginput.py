"""Synthetic bound-client/clip/Windows functions; never loads or invokes SendInput."""
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from x4validate import _livedialoginput as m


def image(color):
    Image=pytest.importorskip('PIL.Image',reason='optional capture decoder required for image fixtures')
    b=io.BytesIO();Image.new('RGB',(160,120),color).save(b,format='PNG');return b.getvalue()


def test_missing_optional_decoder_refuses_without_image_fixture(monkeypatch):
    import builtins
    original=builtins.__import__
    def missing(name,*args,**kwargs):
        if name=='PIL' or name.startswith('PIL.'):raise ImportError('controlled missing optional decoder')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',missing)
    with pytest.raises(ValueError,match='optional Pillow capture decoder'):
        m.decode_image(b'not decoded without optional dependency')


class Client(m.AutomationClient):
    def __init__(self):
        self.t=100.;self.requests={};self._native_connection=False
        self.i={'provider':'x4-live-session-v1','revision':'a'*64,'session':'b'*32,'ui_generation':'c'*32,
                'world_token':'world','world_lineage':'UNKNOWN','load_generation':1,
                'peer':{'pid':42,'creation_filetime':'100','image':'X4.exe'}}
        class Peer:
            profile_id='12345678'
            def __call__(p):return copy.deepcopy(self.i['peer'])
        self.bound=SimpleNamespace(build='d'*8,link=SimpleNamespace(peer=Peer()))
        self.a={'kind':'COMPONENT','component':'42'};self.seq=2;self.instance=1;self.choice_text='Buy blueprints';self.wrong=False
    def identity(self):return copy.deepcopy(self.i)
    def stamp(self):return {k:self.i[k] for k in ('provider','revision','session','world_token')}|{'generation':self.i['ui_generation']}
    def prepare(self,r):self.requests[r['id']]=copy.deepcopy(r);return {'status':'prepared'}
    def execute(self,i):return {'status':'running'}
    def status(self,i):
        return {'status':'completed','observations':{'read_only':True,'gameplay_action_attempted':False,
            'proof_scope':'CONVERSATION_SNAPSHOT_ONLY','snapshot':{'request':i,'stamp':self.stamp(),'active':True,
            'stable':True,'history_matches':True,'status':'ACTOR_AND_HISTORICAL_SECTION_OBSERVED',
            'actor':copy.deepcopy(self.a),'instance':self.instance,'sequence':self.seq,'conversation':'C','section':'S'}}}
    def model(self):
        return {'status':'UNBOUND_MODEL_OBSERVED','stamp':self.stamp(),'dialog_active':True,'dialog_active_after':True,
            'conversation_active':True,'conversation_cancelling':False,'rows_read':6,'issues':[],
            'options':[{'index':i,'readable':True,'text':self.choice_text if i==1 else '',
                        'selectable':i==1,'shortcut':getattr(self,'shortcut',str(i)),'active':i==1} for i in range(1,7)],
            'current_bindings':{'status':'TWO_EQUAL_PROJECTED_READS','rows':[
                {'choice':i,'action':96+i,'status':'OBSERVED','count':1,'inputs':[[1,i+1,0,'NOT_PRESENT']]}
                for i in range(1,7)]}}


class Evidence:
    def __init__(self,c,**kw):self.c=c
    def collect(self,verb):assert verb=='dialog_probe';return object()
    def receipt(self,h):return {'status':'OK','identity':self.c.identity(),'build':self.c.bound.build,
                               'capture':{'payload':json.dumps(self.c.model())}}


class Clips(m.ClipPort):
    def __init__(self,c,tmp):
        self.client=c;self.synthetic=True;self.profile=tmp/'personal'/'12345678';self.frames={};self.bytes=image('black')
        self.binary=tmp/'ffmpeg.exe';self.binary.write_bytes(b'synthetic decoder')
        self.recorder=SimpleNamespace(binary=self.binary,binary_row=m._file(self.binary,256*1024**2),runner=self.run)
    def run(self,args,**kw):assert args[0]==str(self.binary) and '-i'in args and 'image2pipe'in args;return self.bytes
    def capture(self,tmp,*,start=100,end=100):
        p=tmp/('clip'+str(len(self.frames))+'.mkv');p.write_bytes(self.bytes)
        h=object();self.frames[h]={'identity':self.client.identity(),'build':self.client.bound.build,'tier':'SYNTHETIC',
            'clip':{'window':{'pid':42,'hwnd':'23','width':160,'height':120},'identity':self.client.identity(),
                'host_interval':[start,end],'path':str(p),'file':m._file(p,256*1024**2),
                'evidence':{'decoded_frames':2}}};return h
    def receipt(self,h):assert h in self.frames;return copy.deepcopy(self.frames[h])


class Backend:
    def __init__(self):self.sent=[];self.n=2;self.bad=False;self.post=False;self.calls=0;self.down=[];self.released=[];self.release_n=1
    def snapshot(self,peer,expected=None,*,owned_scan=None):
        self.calls+=1
        if self.bad or (self.post and self.sent):raise m.InputUnavailable('foreign foreground')
        if owned_scan is None and self.down:raise m.InputUnavailable('held key')
        return {'window':expected,'thread':5,'layout':'6','keyboard_scope':'synthetic','down_bits':self.down[:]}
    def translate(self,scan,state):return scan
    def send_pair(self,scan):
        self.sent.append(scan)
        if self.n=='error':raise OSError('unknown insertion')
        if type(self.n)is int and self.n==1:self.down=[0x30+scan-1]
        return {'inserted':self.n,'winerror':0}
    def send_release(self,scan):
        self.released.append(scan)
        if self.release_n=='error':raise OSError('release unknown')
        if self.release_n==1:self.down=[]
        return {'inserted':self.release_n,'winerror':0}


def setup(tmp,monkeypatch):
    monkeypatch.setattr(m,'ReadEvidence',Evidence)
    c=Client();clips=Clips(c,tmp);root=tmp/'input';root.mkdir();backend=Backend()
    port=m.DialogInput(c,clips,root,observer_revision='f'*64,expected_actor=c.a,expected_actor_label='Representative',
                       clock=lambda:c.t,backend=backend,synthetic=True)
    return port,c,clips,backend


def context(p):return p.context(p.begin_actor())


def reviewed(p,c,clips,tmp,change=None):
    ct=context(p);fh=p.frame(clips.capture(tmp,start=c.t,end=c.t),ct)
    frame=p.frames[fh]
    transcript={'frame_sha256':frame['sha256'],'actor_label':'Representative','actor_region':[2,2,80,20],
        'choice':1,'overlays':[],'choices':[{'index':i,'text':c.choice_text if i==1 else '',
          'appearance':'ENABLED' if i==1 else 'ABSENT','shortcut':'1' if i==1 else '',
          'region':[2,30,150,50] if i==1 else []} for i in range(1,7)]}
    if change:change(transcript)
    rh=p.review(fh,reviewer='synthetic visual reviewer',transcript=transcript)
    return rh,ct,fh


def test_real_journal_once_and_no_effect_claim(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);ticket=p.prepare(r,ct)
    result=p.execute(ticket,context(p));assert b.sent==[2] and result['status']=='running'
    assert result['observations']['game_result_verified'] is False
    assert (p.tickets[ticket]['journal'].path/'dispatch.json').exists()
    with pytest.raises(m.InputUnavailable):p.execute(ticket,context(p))
    assert b.sent==[2]


@pytest.mark.parametrize('n',[0,1,'error',True,3])
def test_partial_unknown_quarantines_dependent_actions_no_replay(tmp_path,monkeypatch,n):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);ticket=p.prepare(r,ct);b.n=n
    result=p.execute(ticket,context(p));assert result['status']=='unknown' and b.sent==[2]
    assert result['observations']['dependent_input_blocked'] is True
    assert result['observations']['key_cleanup']=='UNRESOLVED_NO_AUTOMATIC_COMPENSATION'
    with pytest.raises(m.InputUnavailable,match='quarantined'):p.prepare(r,context(p))
    assert b.sent==[2]


def test_post_focus_race_uncertain_and_blocked(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct);b.post=True
    out=p.execute(t,context(p));assert out['status']=='unknown' and out['observations']['dependent_input_blocked']


def test_before_focus_change_no_input(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct);b.bad=True
    with pytest.raises(m.InputUnavailable):p.execute(t,context(p))
    assert b.sent==[]


@pytest.mark.parametrize('change',[
    lambda t:t.update(actor_label='Wrong actor'),lambda t:t.update(overlays=['Confirmation dialog']),
    lambda t:t['choices'][0].update(text='Wrong choice'),lambda t:t['choices'][0].update(appearance='DISABLED'),
    lambda t:t.update(frame_sha256='0'*64),lambda t:t.update(qualified=True),
    lambda t:t['choices'][0].update(region=[0,0,9999,9999])])
def test_broken_visual_review_refused(tmp_path,monkeypatch,change):
    p,c,clips,b=setup(tmp_path,monkeypatch)
    with pytest.raises(m.InputUnavailable):reviewed(p,c,clips,tmp_path,change)
    assert b.sent==[]


def test_harmless_animated_background_fresh_review_can_dispatch(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);_,_,f1=reviewed(p,c,clips,tmp_path)
    clips.bytes=image('navy');c.t+=1;r,ct,f2=reviewed(p,c,clips,tmp_path)
    assert p.frames[f1]['sha256']!=p.frames[f2]['sha256']
    t=p.prepare(r,ct);assert p.execute(t,context(p))['status']=='running' and b.sent==[2]


def test_rendered_digit_translation_does_not_guess_raw_shortcut_encoding(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);c.shortcut='{opaque native text formatting}'
    r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct)
    assert p.execute(t,context(p))['status']=='running'


def test_wrong_visually_reviewed_shortcut_refuses_before_dispatch(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch)
    r,ct,_=reviewed(p,c,clips,tmp_path,lambda t:t['choices'][0].update(shortcut='2'))
    with pytest.raises(m.InputUnavailable,match='mapped digit'):p.prepare(r,ct)
    assert b.sent==[]


@pytest.mark.parametrize('change',[lambda b:b.update(count=True),lambda b:b['inputs'][0].__setitem__(0,True),
                                 lambda b:b['inputs'][0].__setitem__(2,False),lambda b:b.update(status='UNAVAILABLE')])
def test_malformed_or_unaccounted_binding_refuses(tmp_path,monkeypatch,change):
    p,c,clips,b=setup(tmp_path,monkeypatch);old=c.model
    def bad():v=old();change(v['current_bindings']['rows'][0]);return v
    c.model=bad
    with pytest.raises(m.InputUnavailable):context(p)
    assert b.sent==[]


@pytest.mark.parametrize('change',[lambda c:setattr(c,'a',{'kind':'COMPONENT','component':'99'}),
    lambda c:setattr(c,'choice_text','Changed choice'),lambda c:setattr(c,'instance',2),
    lambda c:setattr(c,'seq',3),lambda c:c.i.update(ui_generation='e'*32)])
def test_changed_native_actor_choice_instance_session_blocks(tmp_path,monkeypatch,change):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct);change(c)
    with pytest.raises(m.InputUnavailable):p.execute(t,context(p))
    assert b.sent==[]


def test_context_age_budget_and_different_snapshot_required(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct)
    with pytest.raises(m.InputUnavailable,match='fresh'):p.execute(t,ct)
    fresh=context(p);c.t+=3.01
    with pytest.raises(m.InputUnavailable,match='expired'):p.execute(t,fresh)
    assert b.sent==[]


def test_foreign_handle_and_caller_json_never_authorize(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch)
    with pytest.raises((m.InputUnavailable,TypeError)):p.prepare({'qualified':True},object())
    assert b.sent==[]


def test_changed_owner_journal_refuses(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);(p.root/'dialog-input-owner.json').write_text('{}')
    with pytest.raises(m.InputUnavailable,match='owner record'):p.begin_actor()


def test_constructor_no_input_and_frame_decode_is_not_capture(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);assert b.sent==[] and b.calls==0
    _,_,fh=reviewed(p,c,clips,tmp_path);assert p.image(fh).startswith(b'\x89PNG') and b.sent==[]


@pytest.mark.parametrize('custom',['clock','backend'])
def test_production_rejects_untrusted_native_or_clock_before_journal(tmp_path,monkeypatch,custom):
    c=Client();clips=Clips(c,tmp_path);c._native_connection=True;clips.synthetic=False
    args={'clock':lambda:100} if custom=='clock' else {'backend':Backend()}
    with pytest.raises(m.InputUnavailable,match='actual Windows backend'):
        m.DialogInput(c,clips,tmp_path,observer_revision='f'*64,expected_actor=c.a,
                      expected_actor_label='Representative',**args)
    assert not (tmp_path/'dialog-input-owner.json').exists()


def test_deleted_owner_refuses(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);(p.root/'dialog-input-owner.json').unlink()
    with pytest.raises(m.InputUnavailable,match='owner record missing'):p.begin_actor()


def test_nonfinite_oversized_clock_refuses(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);c.t=10**400
    with pytest.raises(m.InputUnavailable,match='monotonic'):p.begin_actor()


def test_review_record_drift_prevents_input(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path)
    p.reviews[r]['path'].write_text('{}')
    with pytest.raises(m.InputUnavailable,match='review record changed'):p.prepare(r,ct)
    assert b.sent==[]


class _RefuseLoader:
    def __getattr__(self,name):raise AssertionError('real Win32 loader reached: '+name)


def test_actual_ctypes_event_layout_calls_only_fake_win32(monkeypatch):
    if m.os.name!='nt':pytest.skip('Windows ctypes ABI only; no OS input')
    calls=[]
    class Fn:
        def __init__(self,name):self.name=name
        def __call__(self,*args):
            assert self.name=='SendInput'
            n,events,size=args
            calls.append((n,size,[(events[i].type,events[i].value.ki.vk,events[i].value.ki.scan,
                events[i].value.ki.flags,events[i].value.ki.time,events[i].value.ki.extra)for i in range(n)]))
            return n
    class DLL:
        def __getattr__(self,name):fn=Fn(name);setattr(self,name,fn);return fn
    monkeypatch.setattr(m.ctypes,'WinDLL',lambda *a,**k:DLL())
    # Review r4-5: also close the other loader, so a product change to ctypes.windll
    # fails here instead of reaching REAL Win32 input.
    monkeypatch.setattr(m.ctypes,'windll',_RefuseLoader())
    backend=m.WindowsDigits();assert calls==[]
    result=backend.send_pair(2)
    assert result['inserted']==2 and calls==[(2,m.ctypes.sizeof(backend.INPUT),[(1,0,2,8,0,0),(1,0,2,10,0,0)])]
    for scan in (True,1,8,0x102):
        with pytest.raises(m.InputUnavailable):backend.send_pair(scan)
    assert len(calls)==1
    assert backend.send_release(2)['inserted']==1
    assert calls[-1]==(1,m.ctypes.sizeof(backend.INPUT),[(1,0,2,10,0,0)])


def partial(p,c,clips,b,tmp):
    r,ct,_=reviewed(p,c,clips,tmp);ticket=p.prepare(r,ct);b.n=1;p.execute(ticket,context(p));return ticket


def test_owned_partial_release_is_separate_journal_no_repress(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);t=partial(p,c,clips,b,tmp_path)
    release=p.prepare_release(t);assert b.released==[]
    out=p.execute_release(release);assert out['status']=='running' and b.sent==[2] and b.released==[2]
    assert not out['observations']['cleanup_verified'] and not out['observations']['dependent_input_blocked']
    assert p.releases[release]['journal'].request['id']!=p.tickets[t]['journal'].request['id']
    with pytest.raises(m.InputUnavailable):p.execute_release(release)
    with pytest.raises(m.InputUnavailable):p.prepare_release(t)
    assert b.released==[2]


@pytest.mark.parametrize('change',['foreignkey','foreground','expired','tampered','unknowncount'])
def test_foreign_stale_uncertain_release_refuses(tmp_path,monkeypatch,change):
    p,c,clips,b=setup(tmp_path,monkeypatch);t=partial(p,c,clips,b,tmp_path)
    if change=='foreignkey':b.down=[0x12]
    elif change=='foreground':b.bad=True
    elif change=='expired':c.t+=5.01
    elif change=='tampered':p.tickets[t]['result']['observations']['insertion']['inserted']=0
    else:p.tickets[t]['result']['observations']['insertion']=None
    with pytest.raises(m.InputUnavailable):p.prepare_release(t)
    assert b.released==[] and p.input_fault


@pytest.mark.parametrize('n',[0,2,True,'error'])
def test_release_failure_never_unblocks_or_retries(tmp_path,monkeypatch,n):
    p,c,clips,b=setup(tmp_path,monkeypatch);t=partial(p,c,clips,b,tmp_path);r=p.prepare_release(t);b.release_n=n
    out=p.execute_release(r);assert out['status']=='unknown' and out['observations']['dependent_input_blocked']
    with pytest.raises(m.InputUnavailable):p.execute_release(r)
    assert b.released==[2] and b.sent==[2]


def test_foreign_release_handle_refuses(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch)
    with pytest.raises(m.InputUnavailable):p.execute_release(object())
    assert b.released==[]


def test_independent_post_input_observation_never_promotes_selection(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);t=p.prepare(r,ct)
    p.execute(t,context(p));c.t+=0.1;c.choice_text='Different section choice';c.seq+=1
    a=p.begin_actor();out=p.outcome(t,a)
    assert out['status']=='POST_INPUT_NATIVE_OBSERVATIONS' and not out['selection_verified']
    assert not out['gameplay_effect_verified'] and len(b.sent)==1
    assert 'Different section choice' in out['dialog_read']['capture']['payload']


def test_stale_outcome_is_not_post_input_evidence(tmp_path,monkeypatch):
    p,c,clips,b=setup(tmp_path,monkeypatch);r,ct,_=reviewed(p,c,clips,tmp_path);old=p.begin_actor()
    c.t+=0.1;t=p.prepare(r,context(p));p.execute(t,context(p))
    with pytest.raises(m.InputUnavailable,match='fresh post-input'):p.outcome(t,old)


@pytest.mark.parametrize('broken',['none','foreground','pid','disabled','gui','modal','focus','layout','held','translation'])
def test_actual_windows_preflight_uses_only_fake_symbols_and_refuses_unknowns(monkeypatch,broken):
    if m.os.name!='nt':pytest.skip('Windows ctypes ABI only; no OS input')
    row={'pid':42,'hwnd':'23','width':160,'height':120};calls=[]
    class Fn:
        def __init__(self,name):self.name=name
        def __call__(self,*args):
            calls.append(self.name)
            if self.name=='GetForegroundWindow':return 0 if broken=='foreground' else 23
            if self.name=='GetWindowThreadProcessId':args[1]._obj.value=99 if broken=='pid' else 42;return 5
            if self.name=='IsWindowEnabled':return broken!='disabled'
            if self.name=='GetGUIThreadInfo':
                o=args[1]._obj;o.active=23;o.focus=99 if broken=='focus' else 23;o.flags=4 if broken=='modal' else 0
                return broken!='gui'
            if self.name=='GetKeyboardLayout':return 0 if broken=='layout' else 6
            if self.name=='GetAsyncKeyState':return 0x8000 if broken=='held' and args[0]==0x12 else 0
            if self.name=='MapVirtualKeyExW':return 0 if broken=='translation' else (0x31 if args[1]==3 else 2)
            raise AssertionError('must never call '+self.name)
    class DLL:
        def __getattr__(self,name):fn=Fn(name);setattr(self,name,fn);return fn
    monkeypatch.setattr(m.ctypes,'WinDLL',lambda *a,**k:DLL())
    # Review r4-5: also close the other loader, so a product change to ctypes.windll
    # fails here instead of reaching REAL Win32 input.
    monkeypatch.setattr(m.ctypes,'windll',_RefuseLoader())
    monkeypatch.setattr(m,'window_for_peer',lambda peer,expected:row.copy())
    backend=m.WindowsDigits()
    def observe():state=backend.snapshot({'pid':42},row);return backend.translate(2,state)
    if broken=='none':assert observe()==2
    else:
        with pytest.raises(m.InputUnavailable):observe()
    assert 'SendInput' not in calls


# Review r1-8: the frame decoder used its own bounds (256 MiB clip, 1,800 decoded frames)
# while the recorder caps a clip at _liveclip.MAX_BYTES (512 MiB) and MAX_FRAMES (900).
@pytest.mark.parametrize('frames,ok',[(901,False),(900,True),(2,True),(1,False)])
def test_decoded_frame_bound_is_the_recorders(tmp_path,monkeypatch,frames,ok):
    p,c,clips,b=setup(tmp_path,monkeypatch);ct=context(p)
    h=clips.capture(tmp_path,start=c.t,end=c.t);clips.frames[h]['clip']['evidence']['decoded_frames']=frames
    if ok:p.frame(h,ct)
    else:
        with pytest.raises(m.InputUnavailable,match='decoded frame bound'):p.frame(h,ct)


def test_clip_file_cap_is_the_recorders(tmp_path,monkeypatch):
    from x4validate import _liveclip
    p,c,clips,b=setup(tmp_path,monkeypatch);ct=context(p)
    h=clips.capture(tmp_path,start=c.t,end=c.t);clip_path=clips.frames[h]['clip']['path']
    caps=[];real=m._file
    def spy(path,cap):
        if str(path)==clip_path:caps.append(cap)
        return real(path,cap)
    monkeypatch.setattr(m,'_file',spy);p.frame(h,ct)
    assert caps and set(caps)=={_liveclip.MAX_BYTES}

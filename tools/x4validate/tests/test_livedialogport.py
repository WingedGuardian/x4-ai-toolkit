"""Actual DialogInput/journals with fake client, clip runner and Win32 backend.

No capture, input, game launch or native Win32 functions are executed.
"""
from pathlib import Path
import pytest
from x4validate import _livedialogport as m
import test_livedialoginput as fixtures


def setup(tmp,monkeypatch):
    engine,c,clips,b=fixtures.setup(tmp,monkeypatch)
    for name in ('clip_port','clip_recorder','port'):(tmp/name).mkdir()
    clips.root=tmp/'clip_port';clips.recorder.journal=tmp/'clip_recorder'
    clips.reserved=[];clips.executed=[]
    def reserve(case,*,duration,fps):
        assert duration==5 and fps==5
        h=object();clips.reserved.append(h);return h
    def execute(h):
        assert h in clips.reserved and h not in clips.executed
        clips.executed.append(h);start=c.t;c.t+=5
        original=clips.capture(tmp,start=start,end=c.t)
        clips.frames[h]=clips.frames[original]
    clips.reserve=reserve;clips.execute=execute
    p=m.DialogPort(engine,tmp/'port',synthetic=True,clock=lambda:c.t,sleep=lambda dt:setattr(c,'t',c.t+dt))
    return p,engine,c,clips,b


def transcript(c,result):
    return {'frame_sha256':result['image_sha256'],'actor_label':'Representative','actor_region':[2,2,80,20],
        'choice':1,'overlays':[],'choices':[{'index':i,'text':c.choice_text if i==1 else '',
        'appearance':'ENABLED' if i==1 else 'ABSENT','shortcut':'1' if i==1 else '',
        'region':[2,30,150,50] if i==1 else []}for i in range(1,7)]}


def review(p,c,result):
    return p.review(result['token'],reviewer='synthetic agent',transcript=transcript(c,result))


def test_actual_capture_review_choose_outcome_path(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch)
    assert not clips.executed and not b.sent
    r=p.capture('dialog.1');assert Path(r['image_path']).read_bytes()==clips.bytes
    assert r['status']=='REVIEW_REQUIRED' and not r['rendered_verified']
    review(p,c,r);assert not b.sent
    value=p.choose(r['token']);assert b.sent==[2] and value['receipt']['status']=='running'
    assert value['selection_verified'] is False and len(clips.executed)==1
    outcome=p.outcome(r['token']);assert outcome['scope']=='SAMPLED_NOT_CAUSAL_NOT_RENDERED'
    assert not outcome['selection_verified'] and b.sent==[2]
    assert p.status(r['token'])['scope']=='RETAINED_LOCAL_STATE'


def test_animation_can_receive_new_review(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);first=p.capture('one')
    clips.bytes=fixtures.image('navy');second=p.capture('two')
    assert first['image_sha256']!=second['image_sha256']
    review(p,c,second);p.choose(second['token']);assert b.sent==[2]


@pytest.mark.parametrize('step',['choose_before_review','repeat_choose','repeat_review'])
def test_stage_and_once_only(tmp_path,monkeypatch,step):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one')
    if step!='choose_before_review':review(p,c,r)
    if step=='repeat_choose':p.choose(r['token'])
    with pytest.raises(m.InputUnavailable):
        if step=='repeat_review':review(p,c,r)
        else:p.choose(r['token'])
    assert len(b.sent)==(1 if step=='repeat_choose' else 0)


@pytest.mark.parametrize('change',['image','actor','choice','session','owner','sources'])
def test_drift_refuses_before_input(tmp_path,monkeypatch,change):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');review(p,c,r)
    if change=='image':Path(r['image_path']).write_bytes(b'altered')
    if change=='actor':c.a={'kind':'COMPONENT','component':'99'}
    if change=='choice':c.choice_text='Different choice'
    if change=='session':c.i['ui_generation']='9'*32
    if change=='owner':(p.root/'dialog-port-owner.json').write_text('{}')
    if change=='sources':monkeypatch.setattr(m,'pins',lambda:{})
    with pytest.raises((m.InputUnavailable,ValueError)):p.choose(r['token'])
    assert b.sent==[]


def test_expired_review_refuses_no_capture_retry(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');review(p,c,r);c.t+=16
    with pytest.raises(m.InputUnavailable):p.choose(r['token'])
    assert len(clips.executed)==1 and not b.sent


def test_partial_input_explicit_owned_release_only(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');review(p,c,r);b.n=1
    result=p.choose(r['token']);assert result['receipt']['status']=='unknown' and b.released==[]
    out=p.release_owned(r['token']);assert b.released==[2] and not out['cleanup_verified']
    with pytest.raises(m.InputUnavailable):p.release_owned(r['token'])
    assert b.released==[2] and b.sent==[2]


def test_unknown_insertion_does_not_authorize_cleanup(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');review(p,c,r);b.n='error'
    p.choose(r['token'])
    with pytest.raises(m.InputUnavailable):p.release_owned(r['token'])
    assert not b.released and b.sent==[2]


def test_pending_native_request_is_polled_not_resent(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);calls=[]
    def pending(h):calls.append(h);return None
    monkeypatch.setattr(e,'context',pending)
    with pytest.raises(m.InputUnavailable,match='deadline'):p.capture('one')
    assert len(set(calls))==1 and len(c.requests)==1 and not clips.reserved and not b.sent
    assert next(iter(p.records.values()))['stage']=='QUARANTINED'


def test_close_does_not_act_and_retained_status_stays_historical(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');p.close()
    assert p.status(r['token'])['closed']
    with pytest.raises(m.InputUnavailable,match='retired'):review(p,c,r)
    assert not b.sent


def test_production_constructor_rejects_synthetic_or_clock_before_writes(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);new=tmp_path/'new';new.mkdir()
    with pytest.raises(m.InputUnavailable):m.DialogPort(e,new)
    e.synthetic=False
    with pytest.raises(m.InputUnavailable,match='clocks'):m.DialogPort(e,new,clock=lambda:1)
    assert list(new.iterdir())==[]


@pytest.mark.parametrize('kind',['missing','nested','same'])
def test_connect_bad_roots_refuse_before_connect(tmp_path,monkeypatch,kind):
    roots={n:tmp_path/n for n in ('port','input','clip_port','clip_recorder')}
    for p in roots.values():p.mkdir()
    if kind=='missing':roots.pop('input')
    if kind=='same':roots['input']=roots['port']
    if kind=='nested':roots['input']=roots['port']/'nested';roots['input'].mkdir()
    calls=[];monkeypatch.setattr(m,'connect_clips',lambda *a,**k:calls.append(a))
    with pytest.raises((m.InputUnavailable,ValueError)):
        m.connect_dialog(object(),tmp_path,roots,ffmpeg='unused',ffmpeg_sha256='a'*64,
                         observer_revision='b'*64,expected_actor={},expected_actor_label='unused')
    assert calls==[]


def test_connect_routes_exact_existing_interfaces(tmp_path,monkeypatch):
    roots={n:tmp_path/n for n in ('port','input','clip_port','clip_recorder')}
    for p in roots.values():p.mkdir()
    calls=[];client=object();clips=object();engine=object()
    def connect(c,p,j,**kw):calls.append(('clips',c,p,j,kw));return clips
    def input(c,cl,j,**kw):calls.append(('input',c,cl,j,kw));return engine
    def port(e,j):calls.append(('port',e,j));return 'opaque'
    monkeypatch.setattr(m,'connect_clips',connect);monkeypatch.setattr(m,'DialogInput',input);monkeypatch.setattr(m,'DialogPort',port)
    out=m.connect_dialog(client,tmp_path,roots,ffmpeg='decoder',ffmpeg_sha256='a'*64,
                         observer_revision='b'*64,expected_actor={'kind':'COMPONENT','component':'42'},expected_actor_label='Representative')
    assert out=='opaque' and [c[0]for c in calls]==['clips','input','port']
    assert calls[0][3]=={'port':roots['clip_port'],'recorder':roots['clip_recorder']}
    assert calls[1][2]is clips and calls[2][1]is engine


def test_lock_and_foreign_tokens_never_act(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch)
    with pytest.raises(m.InputUnavailable):p.choose('f'*32)
    p.lock.acquire()
    try:
        with pytest.raises(m.InputUnavailable,match='active'):p.capture('one')
    finally:p.lock.release()
    assert not clips.reserved and not b.sent


@pytest.mark.parametrize('phase',['review','choose','release','outcome'])
def test_postoperation_owner_drift_quarantines_but_retains_effect_evidence(tmp_path,monkeypatch,phase):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);r=p.capture('one');token=r['token']
    def wrap(name):
        original=getattr(e,name)
        def changed(*a,**kw):
            result=original(*a,**kw);(p.root/'dialog-port-owner.json').write_text('{}');return result
        monkeypatch.setattr(e,name,changed)
    if phase=='review':
        wrap('review')
        with pytest.raises(m.InputUnavailable,match='owner journal'):review(p,c,r)
        assert b.sent==[]
    else:
        review(p,c,r)
        if phase=='choose':
            wrap('execute')
            with pytest.raises(m.InputUnavailable,match='owner journal'):p.choose(token)
        else:
            if phase=='release':b.n=1
            p.choose(token);wrap('execute_release' if phase=='release' else 'outcome')
            with pytest.raises(m.InputUnavailable,match='owner journal'):
                if phase=='release':p.release_owned(token)
                else:p.outcome(token)
        assert b.sent==[2] and p.status(token)['receipt']['observations']['attempted'] is True
    status=p.status(token);assert status['stage']=='QUARANTINED'
    if phase=='release':assert status['release_receipt'] is not None and b.released==[2]
    if phase=='outcome':assert status['outcome']['selection_verified'] is False


def test_full_engine_frame_capacity_refuses_before_capture(tmp_path,monkeypatch):
    p,e,c,clips,b=setup(tmp_path,monkeypatch);e.frames={object():{}for _ in range(16)}
    with pytest.raises(m.InputUnavailable,match='frame capacity'):p.capture('one')
    assert not clips.reserved and not b.sent

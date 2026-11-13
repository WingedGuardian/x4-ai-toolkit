"""Fixed-profile host capacity controls; synthetic files only, no screenshots."""
import copy
from pathlib import Path
import pytest
from x4validate import _livecapture as C
from x4validate import _livecapturedispatch as D
from x4validate._livecapacity import resolve


def observer(tmp,profile=None,**kw):
    save=tmp/'personal'/'123'/'save';save.mkdir(parents=True)
    journal=tmp/'journal';journal.mkdir()
    identity={'provider':'x4-live-session-v1','revision':'a'*64,'session':'b'*32,
        'load_generation':1,'ui_generation':'c'*32,'peer':{'pid':123,'creation_filetime':'456','image':'C:/Synthetic/X4.exe'},
        'world_token':'world','world_lineage':'UNKNOWN'}
    stamp={k:identity[k]for k in ('provider','revision','session','world_token')};stamp['generation']='c'*32
    paths={'status':'OBSERVED','scope':'NATIVE_SAVE_FOLDER_PATH','stamp':stamp,'save_folder':str(save)}
    out=C.CaptureObserver(journal,identity=lambda:copy.deepcopy(identity),native_paths=lambda:copy.deepcopy(paths),
        capture_receipt=lambda req:None,decoder=lambda raw:{},capacity_profile=profile,**kw)
    request={'id':'d'*32,'digest':'e'*64,'session':'b'*32,'provider':'x4-screen-capture-v1',
             'binding':'f'*64,'native_revision':'1'*64,'view_binding':'synthetic'}
    return out,request


@pytest.mark.parametrize('profile,cap',[(None,512),('interactive',512),('campaign_v1',2048)])
def test_fixed_host_limits_and_unchanged_bytes(tmp_path,profile,cap):
    o,_=observer(tmp_path,profile)
    assert o.capacity['host_capture_records']==o.max_members==cap
    assert o.max_total_bytes==8*1024**3
    assert D.CAMERA_TOTAL_BYTES==o.capacity['camera_total_bytes']==16*1024**2


@pytest.mark.parametrize('profile',['other',{},True,2048])
def test_unknown_profile_refuses_before_observer_journal(tmp_path,profile):
    with pytest.raises(ValueError,match='capacity profile'):observer(tmp_path,profile)
    assert not (tmp_path/'journal'/'capture-owner.json').exists()


def test_interactive_record_513_refused(tmp_path):
    o,r=observer(tmp_path);o.records={object():{}for _ in range(512)}
    with pytest.raises(C.CaptureUnavailable,match='record limit'):o.prepare(r)
    assert len(list(o.journal.iterdir()))==1


def test_campaign_record_513_supported_without_eviction(tmp_path):
    o,r=observer(tmp_path,'campaign_v1');o.records={object():{}for _ in range(512)}
    old=set(o.records);o.prepare(r)
    assert len(o.records)==513 and old.issubset(o.records)


def test_campaign_record_2049_refused(tmp_path):
    o,r=observer(tmp_path,'campaign_v1');o.records={object():{}for _ in range(2048)}
    with pytest.raises(C.CaptureUnavailable,match='record limit'):o.prepare(r)


@pytest.mark.parametrize('profile,allowed',[(None,False),('campaign_v1',True)])
def test_actual_513_member_frame(tmp_path,profile,allowed):
    o,_=observer(tmp_path,profile);root=o.native_roots[0];root.mkdir(parents=True)
    for i in range(513):(root/str(i)).write_bytes(b'x')
    if not allowed:
        with pytest.raises(C.CaptureUnavailable,match='membership'):o.capacity_usage()
    else:
        value=o.capacity_usage()
        assert value['directory_members']==513 and value['image_bytes']==513
        assert not value['future_fit_verified'] and not value['logical_charge_is_process_rss']


def test_full_directory_refuses_before_capture_reservation(tmp_path):
    o,r=observer(tmp_path,'campaign_v1',max_members=1)
    root=o.native_roots[0];root.mkdir(parents=True);(root/'existing').write_bytes(b'x')
    with pytest.raises(C.CaptureUnavailable,match='no directory capacity'):o.prepare(r)
    assert not o.records and len(list(o.journal.iterdir()))==1


@pytest.mark.parametrize('profile,max_members',[(None,513),('campaign_v1',2049)])
def test_numeric_override_cannot_raise_profile(tmp_path,profile,max_members):
    with pytest.raises(C.CaptureUnavailable,match='supported maximum'):observer(tmp_path,profile,max_members=max_members)


def test_retained_image_bytes_are_counted_not_assumed(tmp_path):
    o,r=observer(tmp_path,'campaign_v1',max_total_bytes=3)
    root=o.native_roots[0];root.mkdir(parents=True);(root/'existing').write_bytes(b'123')
    with pytest.raises(C.CaptureUnavailable,match='no retained-image byte capacity'):o.prepare(r)
    assert o.capacity_usage()['image_bytes']==3


def test_usage_is_copy_and_tracks_frame_charge(tmp_path):
    o,r=observer(tmp_path,'campaign_v1');o.prepare(r);u=o.capacity_usage()
    assert u['retained_before_frame_json_bytes']>0 and u['records_used']==1
    u['capacity_profile']['host_capture_records']=9
    assert o.capacity_usage()['records_limit']==2048


@pytest.mark.parametrize('profile,expected',[(None,512),('campaign_v1',2048)])
def test_dispatcher_constructor_forwards_same_fixed_profile(tmp_path,monkeypatch,profile,expected):
    # Native/client transport is deliberately stubbed; constructor plumbing is the assertion.
    save=tmp_path/'personal'/'123'/'save';save.mkdir(parents=True)
    root=tmp_path/'dispatch';root.mkdir();obs=tmp_path/'observer';obs.mkdir()
    calls=[]
    class FakeObserver:
        def __init__(self,*a,**kw):calls.append(kw['capacity_profile']);self.native_savedir=save
    client=object.__new__(D.AutomationClient);client.identity=lambda:{'synthetic':True}
    monkeypatch.setattr(D,'CaptureObserver',FakeObserver);monkeypatch.setattr(D,'ReadEvidence',lambda *a,**kw:None)
    d=D.CaptureDispatcher(client,root,obs,'a'*64,'b'*64,synthetic=True,capacity_profile=profile)
    assert calls==[resolve(profile)['name']] and d.capacity['host_capture_records']==expected
    assert d.owner['capacity_profile']==resolve(profile)

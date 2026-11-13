"""Actual dispatcher release/immutable history; synthetic image and native plumbing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from x4validate._livecapture import CaptureUnavailable, _FrameJournalRef, FRAME_RAM_JSON_LIMIT
import test_livecapture_dispatch as D
from test_livecapture_capacity import observer


def released(tmp):
    c=D.setup(tmp);d=c['d'];h=D.settled(c)
    assert d.prepare_release(h)['status']=='prepared'
    assert d.release(h)['status']=='completed'
    return c,h


def check_release_archives_only_frames_preserves_history(tmp):
    c,h=released(tmp);d=c['d'];r=d.records[h];o=d.observer.records[r['observer']]
    assert o['before'] is None and r['settled_frame'] is None
    assert isinstance(o['before_ref'],_FrameJournalRef) and isinstance(r['settled_ref'],_FrameJournalRef)
    assert d.observer.retained_frame_json_bytes==0 and len(d.records)==len(d.observer.records)==1
    assert d.receipt(h)['reservation_released'] and d.observer._before(r['observer'])['files']=={}
    assert len(d._stored_frame(r)['files'])==1


def check_active_frames_remain_in_memory(tmp):
    c=D.setup(tmp);h=D.settled(c);d=c['d'];r=d.records[h]
    assert isinstance(r['settled_frame'],dict) and isinstance(d.observer.records[r['observer']]['before'],dict)
    assert d.observer.retained_frame_json_bytes>0


def check_tampered_before_journal_refused(tmp):
    c,h=released(tmp);d=c['d'];o=d.observer.records[d.records[h]['observer']]
    Path(o['before_ref'].path).write_bytes(b'{}')
    with pytest.raises(CaptureUnavailable,match='journal changed'):d.receipt(h)


def check_tampered_settled_journal_refused(tmp):
    c,h=released(tmp);d=c['d'];Path(d.records[h]['settled_ref'].path).write_bytes(b'{}')
    with pytest.raises(CaptureUnavailable,match='journal changed'):d.receipt(h)


def check_reference_substitution_refused(tmp):
    c,h=released(tmp);d=c['d'];r=d.records[h];o=d.observer.records[r['observer']]
    o['before_ref']=r['settled_ref']
    with pytest.raises(CaptureUnavailable,match='substitution'):d.receipt(h)


def check_owner_change_refuses_archived_read(tmp):
    c,h=released(tmp);d=c['d'];(d.observer.journal/'capture-owner.json').write_bytes(b'{}')
    with pytest.raises(CaptureUnavailable,match='owner journal'):d.receipt(h)


def check_later_capture_does_not_rebind_old_frame(tmp):
    c,h=released(tmp);d=c['d'];old=d.records[h];oldref=old['settled_ref']
    n=d.reserve('second')
    assert len(d.records)==2 and oldref==old['settled_ref']
    assert len(d.observer.records[d.records[n]['observer']]['before']['files'])==1
    assert len(d._stored_frame(old)['files'])==1
    assert d.receipt(h)['reservation_released']


def campaign(tmp):
    import test_livecamera_capacity as C
    rt,receipt=C.capture_runtime('campaign_v1')
    prior_runtime,prior_inventory,prior_dispatch=D.A.runtime,D.A.INV,D.CaptureDispatcher
    try:
        D.A.runtime=lambda:rt;D.A.INV=receipt
        D.CaptureDispatcher=lambda *a,**kw:prior_dispatch(*a,**kw,capacity_profile='campaign_v1')
        return D.setup(tmp)
    finally:
        D.A.runtime=prior_runtime;D.A.INV=prior_inventory;D.CaptureDispatcher=prior_dispatch


def check_actual_campaign_capture_settle_release(tmp):
    c=campaign(tmp);d=c['d'];h=D.settled(c)
    assert d.prepare_release(h)['status']=='prepared' and d.release(h)['status']=='completed'
    assert d.receipt(h)['reservation_released'] and d.capacity['name']=='campaign_v1'
    assert d.records[h]['last']['observations']['capacity']['revision']==d.capacity['revision']


def bad_capacity(tmp,mutation):
    c=campaign(tmp);d=c['d'];h=d.reserve('capacity-boundary')
    c['rt'].execute('old_capacity=adapter.capacity;adapter.capacity=function()local v=old_capacity();'+mutation+';return v end')
    with pytest.raises(CaptureUnavailable,match='capacity'):d.prepare(h)
    assert 'action_execute' not in c['bound'].calls and d.fault


def check_campaign_wrong_revision_refused_before_execute(tmp):
    bad_capacity(tmp,"v.revision=string.rep('0',64)")


def check_campaign_wrong_profile_refused_before_execute(tmp):
    bad_capacity(tmp,"v.profile='interactive'")


def check_campaign_wrong_source_refused_before_execute(tmp):
    bad_capacity(tmp,"v.source_sha256=string.rep('0',64)")


def check_campaign_boolean_count_refused_before_execute(tmp):
    bad_capacity(tmp,'v.history_used=true')


def check_campaign_absent_capacity_refused_before_execute(tmp):
    bad_capacity(tmp,'v=nil')


def check_campaign_unbound_legacy_metadata_refused(tmp):
    bad_capacity(tmp,"v.revision='UNBOUND_LEGACY_INTERACTIVE';v.source_sha256='UNBOUND_LEGACY_INTERACTIVE'")


def check_interactive_partial_legacy_metadata_refused(tmp):
    c=D.setup(tmp);d=c['d'];h=d.reserve('mixed-legacy')
    c['rt'].execute("old_capacity=adapter.capacity;adapter.capacity=function()local v=old_capacity();v.source_sha256=string.rep('0',64);return v end")
    with pytest.raises(CaptureUnavailable,match='capacity'):d.prepare(h)
    assert 'action_execute' not in c['bound'].calls and d.fault


def check_interactive_legacy_missing_capacity_remains_scoped(tmp):
    c=D.setup(tmp);c['rt'].execute('adapter.capacity=function()return nil end')
    d=c['d'];h=d.reserve('legacy-interactive')
    assert d.prepare(h)['status']=='prepared'
    assert 'capacity' not in d.records[h]['last']['observations']


def test_unreleased_metadata_limit_prevents_new_reservation(tmp_path):
    o,r=observer(tmp_path,'campaign_v1');o.retained_frame_json_bytes=FRAME_RAM_JSON_LIMIT
    with pytest.raises(CaptureUnavailable,match='metadata capacity'):o.prepare(r)
    assert not o.records and len(list(o.journal.iterdir()))==1


@pytest.mark.parametrize('name',[k for k in list(globals())if k.startswith('check_')])
def test_actual_released_history(name,tmp_path):
    pytest.importorskip('PIL.Image',reason='optional capture decoder extra')
    p=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),name,str(tmp_path)],
        capture_output=True,text=True,timeout=45,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip().endswith('CONTROL_PASSED')


if __name__=='__main__':
    assert sys.argv[1].startswith('check_')
    globals()[sys.argv[1]](Path(sys.argv[2]));print('CONTROL_PASSED')

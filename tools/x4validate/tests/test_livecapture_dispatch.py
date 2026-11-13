"""Actual SDK/codec/ActionJournal+observer; synthetic MD/path, temporary image files.

This qualifies synthetic dispatcher integration, not real native capture or visual content.
"""
import copy,io,json,os,subprocess,sys
from pathlib import Path
import pytest
import test_livecapture_adapter as A
from x4validate._liveautomation import AutomationClient
from x4validate._livecapturedispatch import CaptureDispatcher
from x4validate._livecapture import CaptureUnavailable
from x4validate._livepipe import Reply
from x4validate._livebound import BoundPipe
from x4validate._livesession import SessionLink
from x4validate._livepeerprofile import ProfilePeer

ROOT=Path(__file__).resolve().parents[3]
def setup(tmp):
    from PIL import Image
    rt=A.runtime()
    rt.globals().codec=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text(encoding="utf-8"))()
    identity={"provider":"x4-live-session-v1","revision":"9"*64,"session":"a"*32,"ui_generation":"b"*32,
              "world_token":"world","world_lineage":"UNKNOWN","load_generation":1,
              "peer":{"pid":42,"creation_filetime":"1234","image":"C:/synthetic/X4.exe"}}
    class Peer(ProfilePeer):
        def __init__(self):self.profile_id="123"
        def __call__(self):return copy.deepcopy(identity["peer"])
    class Link(SessionLink):
        def __init__(self):self.peer=Peer()
        def identity(self):return copy.deepcopy(identity)
    class Pipe:
        def __init__(self):self.calls=[];self.all_calls=[];self.seq=0
        def ask(self,verb,*args):
            assert verb=="bound";self.seq+=1;self.all_calls.append(args)
            inner=args[5]
            if inner.startswith("action_"):self.calls.append(inner)
            raw=rt.globals().router.bound(rt.table_from(args))
            if bound.lose==inner:raise TimeoutError("synthetic lost reply")
            return Reply(self.seq,"OK",raw)
    pipe=Pipe();bound=BoundPipe(pipe,Link(),"12345678");bound.calls=pipe.calls;bound.lose=None
    for name in ("automation","observer","dispatch"):(tmp/name).mkdir()
    save=tmp/"personal"/"123"/"save";save.mkdir(parents=True)
    rt.globals().savefolder=str(save)
    rt.globals().camerafactory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_camerastate.lua").read_text(encoding="utf-8"))
    rt.globals().routerfactory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_automation.lua").read_text(encoding="utf-8"))
    rt.execute("""
    ffi=require('ffi');uiage=100;envelopeage=5000;camera_calls=0;camera_yaw=1;camera_ref=123ULL
    C={GetCameraRotation=function()return ffi.new('Rotation',{camera_yaw,2,3})end,
       GetFollowCameraBasePos=function()return ffi.new('Coord3D',{4,5,6})end,
       GetExternalViewRefObject=function()return camera_ref end,IsExternalViewActive=function()return true end}
    camera=camerafactory{ffi=ffi,C=C,current=function()return stamp end,now=function()return uiage end}
    router=routerfactory{session={current=function()return stamp end},wire=codec,registry=sdk,build='12345678',
      ui_authorize=function()return false end,age=function()return envelopeage end,
      read_verbs={camera_state=true,native_save_path=true},read=function(verb,args)
        if verb=='native_save_path' then return 'OK',codec.encode{status='OBSERVED',scope='NATIVE_SAVE_FOLDER_PATH',stamp=stamp,save_folder=savefolder} end
        assert(verb=='camera_state' and #args==0);camera_calls=camera_calls+1
        local value=camera.read();if camera_mutator then camera_mutator(value,camera_calls)end
        return 'OK',codec.encode(value)
      end}
    """)
    client=AutomationClient(bound,tmp/"automation")
    d=CaptureDispatcher(client,tmp/"dispatch",tmp/"observer",A.INV["revision"],A.INV["native_revision"],synthetic=True)
    clock=[d.observer.last];d.observer.clock=lambda:clock[0]
    def image(name="capture.png"):
        folder=save.parent/"screenshots"/"x4_toolkit_capture";folder.mkdir(parents=True,exist_ok=True)
        b=io.BytesIO();Image.new("RGB",(3,2),(30,50,70)).save(b,format="PNG")
        p=folder/name;p.write_bytes(b.getvalue());return p
    return dict(d=d,rt=rt,bound=bound,pipe=pipe,client=client,clock=clock,image=image,identity=identity,tmp=tmp)

def running(c):
    d=c["d"];h=d.reserve("front")
    assert d.prepare(h)["status"]=="prepared"
    assert d.execute(h)["status"]=="running"
    return h

def settled(c):
    h=running(c);c["image"]()
    assert c["d"].settle(h)["status"]=="PENDING"
    c["clock"][0]+=2
    assert c["d"].settle(h)["status"]=="NATIVE_CAPTURE_FILE_OBSERVED"
    return h

def check_full_exact_capture_settle_release(tmp_path):
    c=setup(tmp_path);d=c["d"];h=settled(c)
    assert d.prepare_release(h)["status"]=="prepared"
    assert d.release(h)["status"]=="completed"
    r=d.receipt(h)
    assert r["reservation_released"] and not r["visual_pass"] and not r["runtime_accepted"]
    assert r["file_observation"]["image"]["fully_decoded"]
    assert c["rt"].eval("(function()local n=0;for _,r in ipairs(sent)do if r.phase==\'execute\' or r.phase==\'release\' then n=n+1 end end;return n end)()")==2
    assert d.reserve("next") is not h

@pytest.mark.parametrize("method",["prepare","execute","prepare_release","release","settle","status","receipt"])
def check_foreign_ticket_refuses_without_native(tmp_path,method):
    c=setup(tmp_path);before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):getattr(c["d"],method)(object())
    assert len(c["bound"].calls)==before

def check_release_requires_opaque_settlement(tmp_path):
    c=setup(tmp_path);h=running(c);before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):c["d"].prepare_release(h)
    assert len(c["bound"].calls)==before
    with pytest.raises(CaptureUnavailable):c["d"].reserve("second")

def check_execute_cannot_repeat(tmp_path):
    c=setup(tmp_path);h=running(c);before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):c["d"].execute(h)
    assert len(c["bound"].calls)==before

def check_release_cannot_repeat(tmp_path):
    c=setup(tmp_path);h=settled(c);d=c["d"];d.prepare_release(h);d.release(h)
    before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):d.release(h)
    assert len(c["bound"].calls)==before

def check_unknown_execute_blocks_settlement_and_next_capture(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["rt"].execute("auto=false")
    assert d.execute(h)["status"]=="running"
    assert d.receipt(h)["stage"]=="EXECUTE_SENT"
    with pytest.raises(CaptureUnavailable):d.settle(h)
    with pytest.raises(CaptureUnavailable):d.reserve("second")
    before=c["rt"].eval("#sent")
    with pytest.raises(CaptureUnavailable):d.execute(h)
    assert c["rt"].eval("#sent")==before

def check_actual_late_execute_status_recovers_without_replay(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["rt"].execute("auto=false")
    d.execute(h);c["rt"].execute("reply();auto=true")
    assert d.status(h)["status"]=="running"
    assert d.receipt(h)["stage"]=="RUNNING"
    assert c["rt"].eval("nativeRecords[sent[1].id].state")=="running"

def check_initial_async_prepare_status_recovery(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");c["rt"].execute("auto=false")
    assert d.prepare(h)["status"]=="preparing"
    c["rt"].execute("reply();auto=true")
    assert d.status(h)["status"]=="prepared"
    assert d.execute(h)["status"]=="running"

def check_extra_file_after_settlement_blocks_release(tmp_path):
    c=setup(tmp_path);h=settled(c);c["image"]("extra.png");before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable,match="frame"):c["d"].prepare_release(h)
    assert len(c["bound"].calls)==before
    assert c["d"].fault

def check_file_change_between_release_prepare_execute_blocks(tmp_path):
    c=setup(tmp_path);h=settled(c);d=c["d"];d.prepare_release(h)
    p=Path(d.records[h]["settled"]["path"]);p.write_bytes(b"changed")
    before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):d.release(h)
    assert len(c["bound"].calls)==before

def check_unknown_release_quarantines_until_actual_reply(tmp_path):
    c=setup(tmp_path);h=settled(c);d=c["d"];d.prepare_release(h);c["rt"].execute("auto=false")
    assert d.release(h)["status"]=="running"
    assert d.receipt(h)["stage"]=="RELEASE_SENT"
    with pytest.raises(CaptureUnavailable):d.reserve("next")
    c["rt"].execute("reply();auto=true")
    assert d.status(h)["status"]=="completed"
    assert d.receipt(h)["reservation_released"]

def check_lost_execute_transport_is_sticky_no_retry(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["bound"].lose="action_execute"
    with pytest.raises(Exception):d.execute(h)
    c["bound"].lose=None;before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable):d.execute(h)
    with pytest.raises(CaptureUnavailable):d.reserve("next")
    assert len(c["bound"].calls)==before

def check_sdk_fault_retained_observation_cannot_qualify(tmp_path):
    c=setup(tmp_path);h=running(c);c["rt"].execute("reply()")
    with pytest.raises(CaptureUnavailable,match="protocol"):c["d"].status(h)
    assert c["d"].fault

def check_owner_journal_change_refuses(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front")
    (d.root/"capture-dispatch-owner.json").write_text("{}")
    with pytest.raises(CaptureUnavailable,match="owner"):d.prepare(h)
    assert not c["bound"].calls

def check_identity_drift_refuses_before_dispatch(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");c["identity"]["peer"]["pid"]=43
    with pytest.raises(CaptureUnavailable,match="identity"):d.prepare(h)
    assert not c["bound"].calls

def check_native_age_not_host_clock_controls_execute(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front")
    c["rt"].execute("mutator=function(r)r.observations.age=2 end")
    d.prepare(h);c["clock"][0]+=20;before=len(c["bound"].calls)
    with pytest.raises(CaptureUnavailable,match="age"):d.execute(h)
    assert len(c["bound"].calls)==before

def check_timeout_settlement_blocks_release(tmp_path):
    c=setup(tmp_path);h=running(c);c["clock"][0]+=40
    with pytest.raises(CaptureUnavailable):c["d"].settle(h)
    with pytest.raises(CaptureUnavailable):c["d"].prepare_release(h)

def check_camera_pair_actual_raw_evidence_and_no_pose(tmp_path):
    c=setup(tmp_path);h=running(c);pair=c["d"].receipt(h)["camera_observations"]
    assert pair["status"]=="SAME_SAMPLED_VALUES"
    assert not pair["achieved_pose_qualified"] and not pair["stable_throughout_qualified"]
    assert not pair["pixel_acquisition_time_qualified"]
    for phase,sample in pair["samples"].items():
        e=sample["entry"];assert e["phase"]==phase
        assert e["capture_id"]==c["d"].records[h]["request"]["id"]
        assert e["receipt"]["evidence_tier"]=="SYNTHETIC"
        assert e["receipt"]["capture"]["started_age"]==5000
        assert e["observation"]["interval"]["started"]==100
        assert e["transcript"]["frames"][0]["verb"]=="read_capture"
        assert json.loads(Path(sample["journal"]).read_text(encoding="utf-8"))==e
    assert c["rt"].eval("camera_calls")==2
    c["d"].status(h);c["d"].status(h)
    assert c["d"].receipt(h)["camera_observations"]==pair
    assert c["rt"].eval("camera_calls")==2

def check_camera_post_waits_native_ack_not_sdk_running(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["rt"].execute("auto=false")
    d.execute(h)
    assert c["rt"].eval("camera_calls")==1
    assert d.receipt(h)["camera_observations"]["status"]=="PENDING"
    c["rt"].execute("reply();auto=true;camera_yaw=9")
    d.status(h)
    assert c["rt"].eval("camera_calls")==2
    assert d.receipt(h)["camera_observations"]["status"]=="CHANGED"

def check_camera_unavailable_does_not_fabricate_or_block_release(tmp_path):
    c=setup(tmp_path);c["rt"].execute("C.GetCameraRotation=function()return nil end")
    h=settled(c);d=c["d"];d.prepare_release(h);d.release(h)
    r=d.receipt(h)
    assert r["camera_observations"]["status"]=="UNAVAILABLE"
    assert r["reservation_released"] and not r["visual_pass"]

@pytest.mark.parametrize("change",[
    "value.achieved_pose_qualified=true", "value.stamp.session=string.rep('e',32)",
    "value.rotation_units='degrees'", "value.interval.finished=99",
    "value.external_reference_handle='18446744073709551616'", "value.extra=true",
    "value.rotation_native.yaw='unknown'", "value.external_reference_nonzero=false",
])
def check_camera_malformed_before_refuses_dispatch(tmp_path,change):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h)
    c["rt"].execute("camera_mutator=function(value,n)"+change+" end")
    before=list(c["bound"].calls)
    with pytest.raises(Exception):d.execute(h)
    assert c["bound"].calls==before and d.fault

def check_camera_bad_post_quarantines_without_repeat(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h)
    c["rt"].execute("camera_mutator=function(value,n)if n==2 then value.stamp.generation=string.rep('e',32) end end")
    with pytest.raises(Exception):d.execute(h)
    assert c["bound"].calls.count("action_execute")==1 and d.fault
    with pytest.raises(CaptureUnavailable):d.execute(h)
    assert c["bound"].calls.count("action_execute")==1

def check_camera_capacity_refuses_before_native_execute(tmp_path):
    from x4validate._livecapturedispatch import CAMERA_TOTAL_BYTES
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);d.camera_bytes=CAMERA_TOTAL_BYTES
    with pytest.raises(CaptureUnavailable,match="capacity"):d.execute(h)
    assert "action_execute" not in c["bound"].calls
    assert c["rt"].eval("camera_calls")==0

def check_camera_journal_change_refuses_next_status(tmp_path):
    c=setup(tmp_path);h=running(c);d=c["d"]
    Path(d.records[h]["camera"]["before"]["journal"]).write_text("{}")
    calls=list(c["bound"].calls)
    with pytest.raises(CaptureUnavailable,match="journal"):d.status(h)
    assert c["bound"].calls==calls

def check_camera_helper_build_drift_refuses_before_execute(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["bound"].build="87654321"
    with pytest.raises(Exception):d.execute(h)
    assert "action_execute" not in c["bound"].calls

def check_camera_ui_clock_regression_independent_of_envelope(tmp_path):
    c=setup(tmp_path);d=c["d"];h=d.reserve("front");d.prepare(h);c["rt"].execute("auto=false")
    d.execute(h);c["rt"].execute("reply();auto=true;uiage=99;envelopeage=5001")
    with pytest.raises(CaptureUnavailable,match="UI clock regressed"):d.status(h)
    assert c["bound"].calls.count("action_execute")==1

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            for i,arg in enumerate(marks[0].args[1]):out.append(pytest.param(name,[arg],id=name+"-"+str(i)))
        else:out.append(pytest.param(name,[],id=name))
    return out

@pytest.mark.parametrize("name,args",_cases())
def test_actual_capture_dispatch(name,args,tmp_path):
    pytest.importorskip("PIL.Image",reason="optional capture decoder extra is not installed")
    run=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args),str(tmp_path)],
        capture_output=True,text=True,timeout=45,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert run.returncode==0,run.stdout+run.stderr
    assert run.stdout.strip().endswith("CONTROL_PASSED"),run.stdout+run.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](Path(sys.argv[3]),*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

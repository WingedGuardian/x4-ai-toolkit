"""Synthetic still-observer controls. No game capture or desktop access."""
import copy
import io
import os
from pathlib import Path
import pytest
from x4validate import _livecapture as m


def image(fmt="PNG"):
    Image=pytest.importorskip('PIL.Image',reason='optional capture decoder extra is not installed')
    stream=io.BytesIO()
    Image.new("RGB",(8,6),(20,40,60)).save(stream,format=fmt)
    return stream.getvalue()


@pytest.fixture
def setup(tmp_path):
    pytest.importorskip('PIL',reason='optional capture decoder extra is not installed')
    save=tmp_path/"personal"/"1234"/"save";save.mkdir(parents=True)
    journal=tmp_path/"evidence";journal.mkdir()
    identity={"provider":"x4-live-session-v1","revision":"a"*64,"session":"b"*32,
        "load_generation":1,"ui_generation":"c"*32,
        "peer":{"pid":123,"creation_filetime":"456","image":"C:/Synthetic/X4.exe"},
        "world_token":"world","world_lineage":"UNKNOWN"}
    stamp={k:identity[k] for k in ("provider","revision","session","world_token")}
    stamp["generation"]=identity["ui_generation"]
    paths={"status":"OBSERVED","scope":"NATIVE_SAVE_FOLDER_PATH","stamp":stamp,"save_folder":str(save)}
    request={"id":"d"*32,"digest":"e"*64,"session":"b"*32,"provider":"x4-screen-capture-v1",
        "binding":"f"*64,"native_revision":"1"*64,"view_binding":"synthetic-view"}
    native={"version":1,"id":request["id"],"session":"b"*32,"digest":"e"*64,"status":"running","reason":"synthetic",
        "observations":{"adapter_revision":"f"*64,"revision":"1"*64,"native_revision":"1"*64,
        "directory":m.DIRECTORY,"bridge_phase":"execute","bridge_generation":"c"*32,
        "file_confirmed":False,"visual_pass":False,"prepared_age":1,"capture_age":5,"age":5,"bridge_call":"synthetic"}}
    clock=[10]
    observer=m.CaptureObserver(journal,identity=lambda:copy.deepcopy(identity),
        native_paths=lambda:copy.deepcopy(paths),capture_receipt=lambda req:copy.deepcopy(native),
        clock=lambda:clock[0])
    return observer,identity,paths,request,native,clock,save


def output(setup,name="capture.bin",fmt="PNG",root=0):
    observer,*_=setup
    folder=observer.native_roots[root];folder.mkdir(parents=True,exist_ok=True)
    path=folder/name;path.write_bytes(image(fmt));return path


def settled(setup,handle):
    observer=setup[0]
    first=observer.observe(handle)
    assert first["status"]=="PENDING"
    setup[5][0]+=2
    return observer.observe(handle)


@pytest.mark.parametrize("fmt",["PNG","JPEG","BMP","TGA"])
@pytest.mark.parametrize("root",[0,1])
def test_new_decoded_image_is_observation_only(setup,fmt,root):
    observer=setup[0];handle=observer.prepare(setup[3]);path=output(setup,fmt=fmt,root=root)
    value=settled(setup,handle)
    assert value["status"]=="NATIVE_CAPTURE_FILE_OBSERVED"
    assert value["image"]["format"]==fmt and value["image"]["fully_decoded"]
    assert value["image"]["width"]==8 and value["image"]["height"]==6
    assert value["path"]==str(path) and value["root_status"]=="OBSERVED_FOR_THIS_REQUEST"
    assert not value["visual_pass"] and not value["causal_origin_proven"] and not value["pose_qualified"]
    assert observer.observe(handle)["historical"] is True


def test_missing_output_is_pending_then_timeout_and_quarantine(setup):
    observer=setup[0];handle=observer.prepare(setup[3])
    assert observer.observe(handle)["reason"]=="NO_NEW_FILE"
    setup[5][0]=41
    with pytest.raises(m.CaptureUnavailable,match="timeout"):observer.observe(handle)
    with pytest.raises(m.CaptureUnavailable,match="unresolved"):observer.prepare(setup[3])


def test_existing_image_cannot_be_credited_to_new_capture(setup):
    path=output(setup);observer=setup[0];handle=observer.prepare(setup[3])
    assert observer.observe(handle)["reason"]=="NO_NEW_FILE"
    path.write_bytes(image("BMP"))
    with pytest.raises(m.CaptureUnavailable,match="pre-existing"):observer.observe(handle)


def test_two_outputs_void_attribution(setup):
    observer=setup[0];handle=observer.prepare(setup[3])
    output(setup,"one");output(setup,"two",root=1)
    with pytest.raises(m.CaptureUnavailable,match="multiple"):observer.observe(handle)


def test_output_must_stabilize_again_after_change(setup):
    observer=setup[0];handle=observer.prepare(setup[3]);path=output(setup)
    assert observer.observe(handle)["reason"]=="FILE_NOT_YET_STABLE"
    setup[5][0]+=2;path.write_bytes(image("BMP"))
    assert observer.observe(handle)["reason"]=="FILE_NOT_YET_STABLE"
    setup[5][0]+=1
    assert observer.observe(handle)["reason"]=="SETTLEMENT_INTERVAL"
    setup[5][0]+=1
    assert observer.observe(handle)["file_observed"] is True


@pytest.mark.parametrize("key",["session","ui_generation","world_token"])
def test_foreign_identity_never_credits_file(setup,key):
    observer=setup[0];handle=observer.prepare(setup[3]);output(setup)
    setup[1][key]="9"*32
    with pytest.raises(m.CaptureUnavailable):observer.observe(handle)


@pytest.mark.parametrize("key,value",[("digest","0"*64),("id","0"*32),("status","completed")])
def test_foreign_or_promoted_native_receipt_refuses(setup,key,value):
    observer=setup[0];handle=observer.prepare(setup[3]);output(setup)
    setup[4][key]=value
    with pytest.raises(m.CaptureUnavailable):observer.observe(handle)


@pytest.mark.parametrize("key,value",[("visual_pass",True),("file_confirmed",True),
    ("capture_age",4),("bridge_generation","0"*32),("directory","other")])
def test_native_receipt_scope_and_chronology_refuse(setup,key,value):
    observer=setup[0];handle=observer.prepare(setup[3]);output(setup)
    setup[4]["observations"][key]=value
    with pytest.raises(m.CaptureUnavailable):observer.observe(handle)


def test_invalid_image_is_not_a_file_success(setup):
    observer=setup[0];handle=observer.prepare(setup[3]);path=output(setup)
    path.write_bytes(b"not an image")
    assert observer.observe(handle)["status"]=="PENDING"
    setup[5][0]+=2
    with pytest.raises(m.CaptureUnavailable,match="decoding"):observer.observe(handle)


def test_observed_output_cannot_change_after_receipt(setup):
    observer=setup[0];handle=observer.prepare(setup[3]);path=output(setup)
    settled(setup,handle);path.write_bytes(image("BMP"))
    with pytest.raises(m.CaptureUnavailable,match="previously"):observer.observe(handle)


def test_opaque_handle_and_single_inflight(setup):
    observer=setup[0];observer.prepare(setup[3])
    with pytest.raises(m.CaptureUnavailable):observer.observe(object())
    with pytest.raises(m.CaptureUnavailable):observer.prepare(setup[3])


def test_clock_rollback_refuses(setup):
    observer=setup[0];handle=observer.prepare(setup[3]);setup[5][0]=9
    with pytest.raises(m.CaptureUnavailable,match="clock"):observer.observe(handle)


def test_hardlinked_output_refuses(setup,tmp_path):
    observer=setup[0];handle=observer.prepare(setup[3])
    source=tmp_path/"source.png";source.write_bytes(image())
    root=observer.native_roots[0];root.mkdir(parents=True)
    os.link(source,root/"linked.png")
    with pytest.raises(ValueError):observer.observe(handle)


def test_decode_rejects_bad_bounds_and_truncated_image():
    with pytest.raises(m.CaptureUnavailable):m.decode_image(image(),max_dimension=4)
    with pytest.raises(m.CaptureUnavailable):m.decode_image(image()[:30])


def test_decoder_must_not_allow_truncation(monkeypatch):
    ImageFile=pytest.importorskip('PIL.ImageFile',reason='optional capture decoder extra is not installed')
    monkeypatch.setattr(ImageFile,"LOAD_TRUNCATED_IMAGES",True)
    with pytest.raises(m.CaptureUnavailable,match="truncated"):m.decode_image(image())


def test_missing_optional_decoder_is_unavailable(monkeypatch):
    import builtins
    original=builtins.__import__
    def unavailable(name,*args,**kwargs):
        if name=='PIL' or name.startswith('PIL.'):
            raise ImportError('synthetic missing optional dependency')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',unavailable)
    with pytest.raises(m.CaptureUnavailable,match='not installed'):m.decode_image(b'anything')


def test_changed_image_during_decode_quarantines(setup):
    observer=setup[0];handle=observer.prepare(setup[3]);path=output(setup)
    assert observer.observe(handle)['status']=='PENDING'
    def change(raw):
        value=m.decode_image(raw);path.write_bytes(image('BMP'));return value
    observer.decoder=change;setup[5][0]+=2
    with pytest.raises(m.CaptureUnavailable,match='during decode'):observer.observe(handle)


@pytest.mark.parametrize('bound',[('max_members',1),('max_file_bytes',1),('max_total_bytes',1)])
def test_capture_enumeration_bounds_never_become_complete(setup,bound):
    observer=setup[0];handle=observer.prepare(setup[3]);output(setup)
    setattr(observer,*bound)
    if bound[0]=='max_members':output(setup,'second')
    with pytest.raises(ValueError):observer.observe(handle)


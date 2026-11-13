"""Host verifier tests use synthetic tmp profiles; never the user's saves."""
import copy
import gzip
import json
import importlib.util
from pathlib import Path
import pytest
from x4validate._livepersistence import SlotVerifier, PersistenceUnavailable

ID="1"*32
STAMP={"provider":"x4-live-session-v1","revision":"b"*64,"session":"c"*32,
       "generation":"d"*32,"world_token":"world"}
IDENT={"provider":STAMP["provider"],"revision":STAMP["revision"],"session":STAMP["session"],
       "load_generation":1,"ui_generation":STAMP["generation"],
       "peer":{"pid":123,"creation_filetime":"123456","image":"C:/game/X4.exe"},
       "world_token":"world","world_lineage":"UNKNOWN"}

@pytest.fixture
def case(tmp_path):
    profile=tmp_path/"X4"/"12345";(profile/"save").mkdir(parents=True)
    journal=tmp_path/"journal";journal.mkdir()
    state={"time":10,"identity":copy.deepcopy(IDENT),"rows":[],"saveable":True}
    def catalogue():
        return {"status":"OBSERVED","scope":"NATIVE_STANDARD_SAVE_CATALOGUE","atomic":False,
                "stamp":copy.deepcopy(STAMP),"rows":copy.deepcopy(state["rows"]),"count":len(state["rows"]),"saveable":state["saveable"]}
    v=SlotVerifier(profile,journal,identity=lambda:copy.deepcopy(state["identity"]),
                   catalogue=catalogue,clock=lambda:state["time"],
                   profile_peer=lambda:{"peer":copy.deepcopy(IDENT["peer"]),"profile_id":"12345"})
    return v,state,profile/"save",journal

def create(case, *, compressed=True, body=None):
    v,state,folder,journal=case
    label="x4live-"+ID
    xml=(f'<savegame><info><save name="{label}" date="1"/><game id="X4" version="900"/></info></savegame>').encode()
    data=xml if body is None else body
    path=folder/("save_001.xml.gz" if compressed else "save_001.xml")
    path.write_bytes(gzip.compress(data) if compressed else data)
    state["rows"]=[{"filename":"save_001","name":label,"rawtime":123,"isonline":False}]
    return path

def settle(case, token):
    v,state,_,_=case
    assert v.observe_settlement(token) is None
    state["time"]+=2
    return v.observe_settlement(token)

@pytest.mark.parametrize("compressed",[False,True])
def test_complete_read_only_preserves_other_file_and_receipt(case,compressed):
    v,state,folder,journal=case
    other=folder/"old.xml.gz";other.write_bytes(b"untouched")
    token=v.reserve(ID,"save_001")
    assert v.preflight(token)["approved"] is False
    create(case,compressed=compressed)
    handle=settle(case,token)
    report=v.receipt(handle,recheck=True)
    assert report["status"]=="SETTLED_SAVE_OBSERVED_NOT_AUTHORIZATION"
    assert report["load_approved"] is False and report["causal_engine_completion"] is False
    assert report["preserved_files"]==1 and other.read_bytes()==b"untouched"
    assert len(list(journal.iterdir()))==3
    with pytest.raises(PersistenceUnavailable):v.receipt(copy.deepcopy(report))

@pytest.mark.parametrize("name",["save_001.xml","save_001.xml.gz","SAVE_001.xml","save_001.xml.gz.bak",
                                 "save_001.tmp","save_001_backup"])
def test_existing_related_any_variant_refuses(case,name):
    v,_,folder,_=case;(folder/name).write_bytes(b"old")
    with pytest.raises(PersistenceUnavailable):v.reserve(ID,"save_001")

def test_nested_related_refuses(case):
    v,_,folder,_=case;(folder/"backup").mkdir();(folder/"backup/save_001.xml").write_bytes(b"old")
    with pytest.raises(PersistenceUnavailable):v.reserve(ID,"save_001")

@pytest.mark.parametrize("slot",["save_000","save_011","quicksave","../save_001","save_001.xml","SAVE_001"])
def test_fixed_slot_boundary(case,slot):
    with pytest.raises(PersistenceUnavailable):case[0].reserve(ID,slot)

def test_same_slot_or_request_not_reusable(case):
    v,state,folder,journal=case
    v.reserve(ID,"save_001")
    with pytest.raises(FileExistsError):v.reserve("2"*32,"save_001")
    fresh=SlotVerifier(v.profile,journal,identity=v.identity,catalogue=v.catalogue,profile_peer=v.profile_peer,clock=lambda:10)
    with pytest.raises(FileExistsError):fresh.reserve(ID,"save_001")

@pytest.mark.parametrize("mutation",["content","added","removed","native","session","backward","journal","claim"])
def test_drift_refuses_preflight(case,mutation):
    v,state,folder,journal=case
    old=folder/"old.xml";old.write_bytes(b"old")
    token=v.reserve(ID,"save_001")
    if mutation=="content":old.write_bytes(b"new")
    elif mutation=="added":(folder/"new.xml").write_bytes(b"x")
    elif mutation=="removed":old.unlink()
    elif mutation=="native":state["rows"]=[{"filename":"save_002","name":"other","rawtime":1}]
    elif mutation=="session":state["identity"]["session"]="e"*32
    elif mutation=="backward":state["time"]=9
    elif mutation=="journal":(journal/(ID+"-reservation.json")).write_text("{}")
    elif mutation=="claim":(journal/"save_001-claim.json").write_text("{}")
    with pytest.raises(PersistenceUnavailable):v.preflight(token)

@pytest.mark.parametrize("mutation",["changed_old","extra","both","directory","wrong_label","bad_game",
                                     "truncated","doctype","expanded","invalid_native"])
def test_settlement_never_qualifies_bad_state(case,mutation):
    v,state,folder,_=case
    old=folder/"old.xml";old.write_bytes(b"old")
    token=v.reserve(ID,"save_001")
    path=create(case)
    if mutation=="changed_old":old.write_bytes(b"changed")
    elif mutation=="extra":(folder/"extra").write_bytes(b"x")
    elif mutation=="both":(folder/"save_001.xml").write_bytes(b"x")
    elif mutation=="directory":(folder/"newdir").mkdir()
    elif mutation=="wrong_label":create(case,body=b'<savegame><info><save name="wrong"/><game id="X4"/></info></savegame>')
    elif mutation=="bad_game":create(case,body=(f'<savegame><info><save name="x4live-{ID}"/><game id="Other"/></info></savegame>').encode())
    elif mutation=="truncated":path.write_bytes(path.read_bytes()[:-4])
    elif mutation=="doctype":create(case,body=b'<!DOCTYPE savegame [<!ENTITY x "bad">]><savegame>&x;</savegame>')
    elif mutation=="expanded":v.max_expanded_bytes=10
    elif mutation=="invalid_native":state["rows"][0]["invalidpatches"]=True
    with pytest.raises(PersistenceUnavailable):
        settle(case,token)

def test_missing_native_and_zero_file_are_pending_not_empty_success(case):
    v,state,folder,_=case;token=v.reserve(ID,"save_001")
    (folder/"save_001.xml").write_bytes(b"")
    assert v.observe_settlement(token) is None
    (folder/"save_001.xml").write_bytes(b"<savegame/>")
    assert v.observe_settlement(token) is None
    assert not list(case[3].glob("*settled*"))

def test_changed_new_file_restarts_stability(case):
    v,state,_,_=case;token=v.reserve(ID,"save_001");path=create(case,compressed=False)
    assert v.observe_settlement(token) is None
    state["time"]+=2;path.write_bytes(path.read_bytes()+b" ")
    assert v.observe_settlement(token) is None
    state["time"]+=2
    assert v.receipt(v.observe_settlement(token))["stable_seconds"]>=2

def test_receipt_recheck_detects_tamper(case):
    v,_,_,_=case;token=v.reserve(ID,"save_001");path=create(case)
    handle=settle(case,token);path.write_bytes(b"changed")
    with pytest.raises(PersistenceUnavailable):v.receipt(handle,recheck=True)

def test_foreign_object_and_caller_json_are_not_capabilities(case):
    v,_,_,_=case
    for value in (object(),{},True,ID):
        with pytest.raises(PersistenceUnavailable):v.preflight(value)
        with pytest.raises(PersistenceUnavailable):v.receipt(value)

@pytest.mark.parametrize("kind",["symlink","hardlink"])
def test_linked_save_refuses(case,tmp_path,kind):
    v,_,folder,_=case;outside=tmp_path/"outside";outside.write_bytes(b"private")
    target=folder/"other.xml"
    if kind=="symlink":
        try:target.symlink_to(outside)
        except OSError as exc:pytest.skip("symlink privilege unavailable: "+str(exc))
    else:
        target.hardlink_to(outside)
    with pytest.raises(PersistenceUnavailable):v.reserve(ID,"save_001")

def test_journal_cannot_live_in_profile(case):
    v,_,_,_=case
    with pytest.raises(PersistenceUnavailable):
        SlotVerifier(v.profile,v.save,identity=v.identity,catalogue=v.catalogue,profile_peer=v.profile_peer)

def test_poll_and_time_bounds(case):
    v,state,_,_=case;token=v.reserve(ID,"save_001")
    for _ in range(64):assert v.observe_settlement(token) is None
    with pytest.raises(PersistenceUnavailable):v.observe_settlement(token)
    state["time"]=311
    with pytest.raises(PersistenceUnavailable):v.preflight(token)

@pytest.mark.parametrize("binding",[
    {"peer":IDENT["peer"],"profile_id":"54321"},
    {"peer":{**IDENT["peer"],"pid":124},"profile_id":"12345"},
    {"peer":IDENT["peer"],"profile_id":"12345","approved":True},
])
def test_wrong_connected_profile_binding(case,binding):
    v=case[0];v.profile_peer=lambda:binding
    with pytest.raises(PersistenceUnavailable):v.reserve(ID,"save_001")

def test_corrupt_deflate_refuses(case):
    v,state,folder,_=case;token=v.reserve(ID,"save_001");path=create(case)
    path.write_bytes(bytes.fromhex("1f8b0800000000000003070000000000000000"))
    with pytest.raises(PersistenceUnavailable):settle(case,token)

def test_receipt_recheck_keeps_reservation_provenance(case):
    v,_,_,journal=case;token=v.reserve(ID,"save_001");create(case)
    handle=settle(case,token)
    (journal/"save_001-claim.json").write_text("{}")
    with pytest.raises(PersistenceUnavailable):v.receipt(handle,recheck=True)

@pytest.mark.parametrize("populated",[False,True])
def test_actual_lua_producer_wire_catalogue(case,populated):
    spec=importlib.util.spec_from_file_location("persistence_fixture",Path(__file__).with_name("test_livepersistence.py"))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    lua=fixture.env()
    if populated:
        lua.execute('rows={{filename="save_002",name="other",rawtime=1}}')
    lua.execute('assert(api.refresh().status=="REFRESH_REQUESTED")')
    root=Path(__file__).resolve().parents[3]
    wire=lua.execute((root/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text(encoding="utf-8"))()
    observed=json.loads(wire.encode(lua.globals().api.observe()))
    assert observed["count"]==int(populated)
    assert (isinstance(observed["rows"],list) if populated else observed["rows"]=={})
    v=case[0];v.catalogue=lambda:copy.deepcopy(observed)
    assert v.preflight(v.reserve(ID,"save_001"))["approved"] is False

@pytest.mark.parametrize("rows,count",[({},1),({"1":{}},1),([],1),({},True)])
def test_malformed_wire_denominator_refuses(case,rows,count):
    v=case[0];native=v.catalogue()
    native.update(rows=rows,count=count);v.catalogue=lambda:native
    with pytest.raises(PersistenceUnavailable):v.reserve(ID,"save_001")

def test_receipt_final_identity_drift_refuses(case):
    v,state,_,_=case;token=v.reserve(ID,"save_001");create(case)
    handle=settle(case,token);original=v.catalogue
    def drift():
        result=original();state["identity"]["session"]="e"*32;return result
    v.catalogue=drift
    with pytest.raises(PersistenceUnavailable):v.receipt(handle,recheck=True)

def test_settlement_final_catalogue_identity_drift_refuses(case):
    v,state,_,_=case;token=v.reserve(ID,"save_001");create(case)
    assert v.observe_settlement(token) is None
    state["time"]+=2;original=v.catalogue;calls=0
    def drift():
        nonlocal calls
        calls+=1;result=original()
        if calls==2:state["identity"]["session"]="e"*32
        return result
    v.catalogue=drift
    with pytest.raises(PersistenceUnavailable):v.observe_settlement(token)
    assert not list(case[3].glob("*settled*"))

@pytest.mark.parametrize("drift",[False,True])
def test_descriptor_ctime_uses_same_api_comparison(case,monkeypatch,drift):
    from types import SimpleNamespace
    import x4validate._livepersistence as module
    path=case[2]/"other.xml";path.write_bytes(b"untouched")
    original=module.os.fstat;calls=0
    def descriptor(fd):
        nonlocal calls
        calls+=1;value=original(fd)
        fields={key:getattr(value,key) for key in
                ("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")}
        fields["st_ctime_ns"]+=1000+(calls if drift else 0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(module.os,"fstat",descriptor)
    if drift:
        with pytest.raises(PersistenceUnavailable,match="while reading"):module._file(path,1024)
    else:
        assert module._file(path,1024)["size"]==9

"""Optional interface-only Extra Data controls; no vendor code or native execution."""
from pathlib import Path
import json
import pytest
from lupa.luajit21 import LuaRuntime

UI=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui"
SETUP=r"""
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
 generation=string.rep('c',32),world_token='synthetic'}
expected={extension_id='ws_3825599643',release_version=103,interface_version=100,
 lua_sha256=string.rep('d',64),archive_sha256=string.rep('e',64),
 installed_receipt_sha256=string.rep('f',64),game_build='synthetic-build'}
function copy(t)
 if type(t)~='table' then return t end
 local out={};for k,v in pairs(t)do out[k]=copy(v)end;return out
end
actual=copy(expected);build='synthetic-build'
calls=0;currentcalls=0;externalcalls=0;dependencycalls=0;availablecalls=0;encodecalls=0
boxA={max={1,2,3},centre={4,5,6},radius=4}
boxB={max={7,8,9},centre={-1,-2,-3},radius=15}
function connection(i)
 return {name='connection_'..i,tags={'dock','synthetic'},position={1,2,3},
 direction={0,0,1},rows={{1,0,0},{0,1,0},{0,0,1}},macro='child_macro',count=2}
end
rows={connection(1),connection(2)}
external={version=100,Available=function()availablecalls=availablecalls+1;error('Available forbidden')end}
external.GetMacroDataEx=function(macro,...)
 calls=calls+1
 assert(macro=='test_macro')
 local keys={...}
 if hook then hook(calls,keys) end
 if keys[1]=='connections' then assert(#keys==1);return rows end
 assert(#keys==2 and keys[1]=='boundingbox' and keys[2]=='fullboundingbox')
 return boxA,boxB
end
d={expected=expected,current=function()currentcalls=currentcalls+1;return stamp end,
 game_build=function()return build end,
 dependency=function()dependencycalls=dependencycalls+1;return actual end,
 external=function()externalcalls=externalcalls+1;return external end,
 encode=function(v)encodecalls=encodecalls+1;if encodehook then encodehook(v)end;return codec.encode(v)end}
"""
def runtime(before=""):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().codec=r.execute((UI/"live_actionwire.lua").read_text(encoding="utf-8"))()
    r.execute(SETUP+before)
    r.globals().factory=r.execute((UI/"live_macrogeometry.lua").read_text(encoding="utf-8"))
    r.execute("api=factory(d)")
    return r

def result(r,method="boxes",args="'test_macro'"):
    r.execute("result=api."+method+"("+args+")")
    return json.loads(r.eval("codec.encode(result)"))

def test_no_constructor_or_availability_probes():
    r=runtime();assert r.eval("calls+currentcalls+externalcalls+dependencycalls+availablecalls")==0
    assert r.eval("api.availability().status")=="UNPROBED"
    assert r.eval("calls+currentcalls+externalcalls+dependencycalls+availablecalls")==0

def test_actual_multireturn_boxes_and_raw_field_scope():
    r=runtime();v=result(r)
    assert v["status"]=="OBSERVED"
    assert v["boundingbox"]["max"]=={"x":1,"y":2,"z":3}
    assert v["fullboundingbox"]["centre"]=={"x":-1,"y":-2,"z":-3}
    assert v["dependency"]["release_version"]==103 and v["dependency"]["interface_version"]==100
    assert v["achieved_pose_verified"] is False
    assert v["numeric_accuracy_qualified"] is False
    assert v["evidence_tier"]=="EXTERNAL_API_OBSERVATION_UNCALIBRATED"
    assert v["scope"]=="COMPILED_MACRO_DEFINITION_NOT_FITTED_INSTANCE"
    assert r.eval("calls")==2 and r.eval("availablecalls")==0

@pytest.mark.parametrize("code",["stamp=nil","stamp.session=nil","stamp.extra=true","build='wrong'",
    "actual.lua_sha256=string.rep('0',64)","actual.installed_receipt_sha256=string.rep('0',64)",
    "actual.archive_sha256=string.rep('0',64)","actual.release_version=104",
    "external.version=103","external.GetMacroDataEx=nil","external=nil"])
def test_unqualified_or_missing_dependency_never_gets_called(code):
    r=runtime(code+";");v=result(r)
    assert v["status"]=="UNAVAILABLE" and r.eval("calls")==0

@pytest.mark.parametrize("code",["boxA=nil","boxB=nil","boxA=nil;boxB=nil"])
def test_nil_box_is_unknown_not_empty_or_absent(code):
    r=runtime(code+";");v=result(r)
    assert v["status"]==("UNKNOWN" if code=="boxA=nil;boxB=nil" else "PARTIAL")
    assert any(v[k]["status"]=="UNKNOWN" for k in ("boundingbox","fullboundingbox"))
    assert "ABSENT" not in v.values()

@pytest.mark.parametrize("code",["boxA.max={1,2}","boxA.max={1,2,3,4}","boxA.max[1]=-1",
    "boxA.centre[2]=0/0","boxA.radius=math.huge","boxA.radius=-1","boxA.extra=true",
    "boxA=setmetatable(boxA,{})","boxA.max={x=1,y=2,z=3}"])
def test_malformed_box_refuses_without_geometry(code):
    r=runtime(code+";");v=result(r)
    assert v["status"]=="UNAVAILABLE" and "boundingbox" not in v

def test_repeat_box_mutation_detected_even_when_external_reuses_table():
    r=runtime("hook=function(n)if n==2 then boxA.max[1]=99 end end;")
    assert result(r)["status"]=="UNAVAILABLE"

@pytest.mark.parametrize("field",["session","generation","world_token","revision"])
def test_last_getter_identity_drift_refuses(field):
    r=runtime("hook=function(n)if n==2 then stamp."+field+"='changed' end end;")
    assert result(r)["status"]=="UNAVAILABLE"

def test_final_encoding_boundary_identity_rechecked():
    r=runtime("encodehook=function()stamp.session=string.rep('9',32)end;")
    assert result(r)["status"]=="UNAVAILABLE"

def test_connections_final_encoding_boundary_identity_rechecked():
    r=runtime("encodehook=function(v)if v.cross_page_snapshot==false then stamp.session=string.rep('9',32)end end;")
    assert result(r,"connections")["status"]=="UNAVAILABLE"

def test_getter_replacement_during_read_refuses():
    r=runtime("hook=function(n)if n==2 then external.GetMacroDataEx=function()return boxA,boxB end end end;")
    assert result(r)["status"]=="UNAVAILABLE"

def test_expected_identity_is_copied():
    r=runtime();r.execute("expected.game_build='changed'")
    assert result(r)["status"]=="OBSERVED"

def test_source_attestation_cannot_promote_numeric_accuracy():
    r=runtime("d.numeric_accuracy_qualified=true;external.numeric_accuracy_qualified=true;")
    assert result(r)["numeric_accuracy_qualified"] is False
    assert result(r,"connections")["numeric_accuracy_qualified"] is False

def test_exception_not_zero():
    r=runtime("hook=function()error('external failed')end;")
    assert result(r)["status"]=="UNAVAILABLE"

def test_dependency_callback_drift_refuses_before_first_getter():
    r=runtime("d.external=function()stamp.session=string.rep('9',32);return external end;")
    assert result(r)["status"]=="UNAVAILABLE" and r.eval("calls")==0

@pytest.mark.parametrize("method",["boxes","connections"])
def test_first_getter_drift_prevents_second_getter(method):
    r=runtime("hook=function(n)if n==1 then stamp.session=string.rep('9',32)end end;")
    assert result(r,method)["status"]=="UNAVAILABLE" and r.eval("calls")==1

def test_connections_observed_empty_has_denominator():
    r=runtime("rows={};");v=result(r,"connections")
    assert v["status"]=="OBSERVED" and v["total"]==0 and v["returned"]==0
    assert v["full_census_in_this_reply"] is True

def test_nil_connections_not_empty():
    r=runtime("rows=nil;");v=result(r,"connections")
    assert v["status"]=="UNAVAILABLE" and v["reason"]=="EXTERNAL_NIL_NOT_EMPTY"
    assert "total" not in v
    assert v["stamp"]["session"]=="b"*32
    assert v["dependency"]["interface_version"]==100
    assert v["numeric_accuracy_qualified"] is False

def test_actual_connection_shape_and_optional_child_and_zone():
    r=runtime("rows[1].macro=nil;rows[2].sizetag='xl';rows[2].size={1,2,3};rows[2].centre={4,5,6};")
    v=result(r,"connections")
    assert v["rows"][0]["child_macro_status"]=="NOT_REPORTED"
    assert v["rows"][1]["macro"]=="child_macro"
    assert v["rows"][1]["size"]=={"x":1,"y":2,"z":3}
    assert v["cross_page_snapshot"] is False and r.eval("calls")==2

@pytest.mark.parametrize("code",["rows={[2]=connection(1)}","rows.extra=true","rows[1].tags={[2]='tag'}",
    "rows[1].direction[1]=1","rows[1].count=-1","rows[1].count=0.5","rows[1].count=4097",
    "rows[1].position[1]=math.huge","rows[1].name=''","rows[1].macro=''",
    "rows[1].sizetag='xl'","rows[1].extra=true"])
def test_bad_connection_row_never_becomes_empty(code):
    r=runtime(code+";");v=result(r,"connections")
    assert v["status"]=="UNAVAILABLE" and "total" not in v

def test_repeat_complete_census_compares_unreturned_rows():
    r=runtime("for i=1,20 do rows[i]=connection(i)end;hook=function(n)if n==2 then rows[20].count=3 end end;")
    assert result(r,"connections")["status"]=="UNAVAILABLE"

def test_all256_bounded_rows_paged_with_explicit_denominator():
    r=runtime("rows={};for i=1,256 do rows[i]=connection(i)end;")
    v=result(r,"connections")
    assert v["total"]==256 and 0<v["returned"]<=8 and v["omitted"]==256-v["returned"]
    assert v["full_census_in_this_reply"] is False
    assert len(r.eval("codec.encode(result)").encode())<=3000
    nxt=v["next_offset"];v2=result(r,"connections",f"'test_macro',{nxt}")
    assert v2["rows"][0]["name"]==f"connection_{nxt+1}"
    assert v2["cross_page_snapshot"] is False

def test_above_input_denominator_bound_refuses():
    r=runtime("rows={};for i=1,257 do rows[i]=connection(i)end;")
    assert result(r,"connections")["status"]=="UNAVAILABLE"

@pytest.mark.parametrize("arg",["false","-1","0.5","257","'1'"])
def test_invalid_offsets_refuse_before_api(arg):
    r=runtime();assert result(r,"connections","'test_macro',"+arg)["status"]=="UNAVAILABLE"
    assert r.eval("calls")==0

def test_oversized_single_row_cannot_make_no_progress_page():
    r=runtime("rows={connection(1)};rows[1].tags={};for i=1,32 do rows[1].tags[i]=string.rep('t',128)end;")
    assert result(r,"connections")["status"]=="UNAVAILABLE"

@pytest.mark.parametrize("arg",["''","'x;eval'","string.rep('x',257)","false"])
def test_bad_macro_before_getter(arg):
    r=runtime();assert result(r,args=arg)["status"]=="UNAVAILABLE"
    assert r.eval("calls")==0

def test_disposal_no_getter_and_no_reactivation():
    r=runtime();assert r.eval("api.dispose()") is True
    assert result(r)["status"]=="UNAVAILABLE" and r.eval("calls")==0
    assert r.eval("api.availability().status")=="DISPOSED"

def test_encode_exception_refuses():
    r=runtime("d.encode=function()error('wire unavailable')end;")
    assert result(r)["status"]=="UNAVAILABLE"

def test_each_call_observes_fresh_data_without_shared_capture_claim():
    r=runtime();a=result(r,"connections");r.execute("rows[1].count=3")
    b=result(r,"connections")
    assert a["rows"][0]["count"]==2 and b["rows"][0]["count"]==3
    assert b["cross_page_snapshot"] is False

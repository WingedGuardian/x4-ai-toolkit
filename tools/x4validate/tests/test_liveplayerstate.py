"""Actual LuaJIT/native FFI buffers with synthetic player-state data; no engine."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_playerstate.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t UniverseID;typedef struct {const char *macro,*ware,*productionmethodid;} UIBlueprint;]]
function u(s)
 local n=ffi.new('uint64_t',0)
 for digit in s:gmatch('.') do n=n*10+tonumber(digit) end
 return n
end
peer=u('9007199254740993')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='world-observed'}
clock=10
faction='native_peer_faction'
cash=50000000
catalog={{id='permit_alpha',type='alpha'},{id='permit_beta',type='beta'}}
holdings={alpha=true,beta=false}
wares={'ware_alpha','ware_beta'}
strings={}
nativecalls=0;bluefills=0;catalogreads=0;moneyreads=0
function cs(s)
 local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return b
end
C={}
C.GetNumBlueprints=function()
 nativecalls=nativecalls+1
 if count_error then error('count unavailable') end
 return badcount or #wares
end
C.GetBlueprints=function(buf,n,a,b,c)
 nativecalls=nativecalls+1;bluefills=bluefills+1
 assert(a=='' and b=='' and c=='')
 if fillerror then error('fill unavailable') end
 for i=0,n-1 do
  if nullware then buf[i].ware=nil else buf[i].ware=cs(wares[i+1]) end
  buf[i].macro=cs('');buf[i].productionmethodid=cs('')
 end
 if swap_after_first and bluefills==1 then wares[1]='ware_replacement' end
 if change_stamp then stamp.session=string.rep('d',32) end
 return shortfill and n-1 or n
end
g={}
g.IsValidComponent=function(v) return not invalid and v==peer end
g.GetComponentData=function(v,key)
 assert(v==peer and key=='owner')
 if owner_error then error('owner unavailable') end
 if owner_change and bluefills>0 then return 'different_faction' end
 return faction
end
g.GetPlayerMoney=function()
 moneyreads=moneyreads+1
 if money_error then error('money unavailable') end
 if money_change and moneyreads>1 then return cash+1 end
 return cash
end
g.GetOwnLicences=function(f)
 assert(f==faction);catalogreads=catalogreads+1
 if catalogue_error then error('catalogue unavailable') end
 if catalogue_change and catalogreads>1 then return {} end
 return catalog
end
g.HasLicence=function(who,typ,f)
 assert(who=='player' and f==faction)
 if held_error then error('held state unavailable') end
 if held_unknown then return nil end
 return holdings[typ]
end
d={ffi=ffi,C=C,globals=g,
 resolve_issued=function(token,kind)
  assert(token=='peer' and kind=='object')
  if revoked then return nil end
  return peer
 end,
 current=function()return stamp end,
 now=function()return clock end,
 encode=function(x)return py_encode(x) end}
nonce=string.rep('e',32)
"""
def plain(value):
    if hasattr(value,"items"):
        return {str(k):plain(v) for k,v in value.items()}
    return value
def build(code=""):
    r=luajit.LuaRuntime(unpack_returned_tuples=True)
    r.globals().py_encode=lambda value:json.dumps(plain(value),sort_keys=True,separators=(",",":"))
    r.execute(SETUP)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"))
    r.execute("api=factory(d)")
    r.execute(code)
    return r
def capture(r):
    return r.globals().api.capture("peer",r.globals().nonce)
def page(r,c,kind="blueprints",offset=0):
    return r.globals().api.page(c["capture_id"],kind,offset)

def check_complete_known_states_and_ids():
    r=build();c=capture(r)
    assert c["status"]=="OBSERVED" and c["complete"] is True
    assert c["peer_id"]=="9007199254740993" and c["money"]["credits"]=="50000000"
    assert c["licences"]["count"]==2 and c["blueprints"]["reported_native_rows"]==2
    rows=page(r,c,"licences")["rows"]
    assert rows[1]["held"] is True and rows[2]["held"] is False
    assert c["atomic"] is False and c["retained_json_bytes"]>0

def check_known_zero_differs_failed_count():
    r=build("wares={};catalog={}")
    c=capture(r);assert c["complete"] is True and c["blueprints"]["reported_native_rows"]==0
    r=build("count_error=true");c=capture(r)
    assert c["status"]=="PARTIAL" and c["blueprints"]["status"]=="UNKNOWN"
    p=page(r,c);assert p["total"]==0 and p["complete"] is False

@pytest.mark.parametrize("code",["cash=0/0","cash=math.huge","cash=9007199254740992","cash=0.25","cash='50000000'","money_error=true","money_change=true"])
def check_money_never_guesses_precision(code):
    r=build(code);c=capture(r)
    assert c["money"]["status"]=="UNKNOWN" and c["money"]["credits"] is None
    assert c["licences"]["complete"] and c["blueprints"]["complete"] and not c["complete"]

@pytest.mark.parametrize("code,value",[("cash=u('18446744073709551615')","18446744073709551615"),("cash=ffi.new('int64_t',-123)","-123"),("cash=-0.0","0")])
def check_exact_integer_strings(code,value):
    c=capture(build(code));assert c["money"]["status"]=="OBSERVED" and c["money"]["credits"]==value

@pytest.mark.parametrize("code",["invalid=true","revoked=true","owner_error=true","owner_change=true","change_stamp=true","stamp=nil"])
def check_identity_failure_never_caches(code):
    r=build(code);c=capture(r)
    assert c["status"]=="REFUSED" and c["capture_id"] is None

@pytest.mark.parametrize("code",["held_unknown=true","held_error=true","catalogue_error=true","catalogue_change=true","catalog[2].id=catalog[1].id","catalog={[2]=catalog[1]}"])
def check_licence_failures_are_unknown(code):
    r=build(code);c=capture(r)
    assert c["status"]=="PARTIAL" and c["licences"]["status"]=="UNKNOWN"
    assert page(r,c,"licences")["complete"] is False

@pytest.mark.parametrize("code",["fillerror=true","shortfill=true","nullware=true","swap_after_first=true","badcount=8193","badcount=-1","badcount=0/0","badcount=0.5"])
def check_blueprint_failure_never_claims_census_complete(code):
    r=build(code);c=capture(r)
    assert c["status"]=="PARTIAL" and c["blueprints"]["status"]=="UNKNOWN"
    assert page(r,c)["complete"] is False

def check_duplicate_native_blueprints_have_explicit_denominator():
    r=build("wares={'ware_alpha','ware_alpha','ware_beta'}");c=capture(r);p=page(r,c)
    assert c["blueprints"]["reported_native_rows"]==3 and c["blueprints"]["unique_wares"]==2
    assert p["rows"][1]["native_occurrences"]==2

def check_large_census_pages_are_stable_and_complete():
    r=build("wares={};for i=1,40 do wares[i]=string.format('ware_%03d',i) end")
    c=capture(r);offset=0;names=[]
    while True:
        p=page(r,c,offset=offset)
        assert p["total"]==40 and len(p["rows"])<=16
        names.extend(v["ware"] for v in p["rows"].values())
        if p["done"]:break
        offset=p["next_offset"]
    assert len(names)==40 and len(set(names))==40

def check_licence_count_bound_refuses_without_empty_claim():
    r=build("catalog={};for i=1,513 do catalog[i]={id='id_'..i,type='type_'..i} end")
    c=capture(r)
    assert c["licences"]["status"]=="UNKNOWN" and c["licences"]["complete"] is False

def check_licence_possession_drift_is_unknown():
    r=build("heldreads=0;g.HasLicence=function(who,typ,f) heldreads=heldreads+1;if heldreads>2 then return not holdings[typ] end;return holdings[typ] end")
    c=capture(r)
    assert c["licences"]["status"]=="UNKNOWN" and not c["licences"]["complete"]

def check_exact_ttl_boundary_is_readable():
    r=build();c=capture(r);r.execute("clock=310")
    assert page(r,c)["status"]=="OBSERVED"

def check_capture_history_bound_is_explicit():
    r=build()
    r.execute("for i=1,256 do nonce=string.format('%032x',i);last=api.capture('peer',nonce);assert(last.status=='OBSERVED') end;nonce=string.format('%032x',257)")
    assert capture(r)["status"]=="REFUSED"

def check_snapshot_output_mutation_does_not_change_capture():
    r=build();c=capture(r);p=page(r,c)
    p["rows"][1]["ware"]="edited";c["money"]["credits"]="edited"
    assert page(r,c)["rows"][1]["ware"]=="ware_alpha"
    assert r.globals().api.metadata(c["capture_id"])["money"]["credits"]=="50000000"

@pytest.mark.parametrize("code",["stamp.session=string.rep('f',32)","stamp.generation=string.rep('f',32)","stamp.world_token='different'","stamp.revision=string.rep('f',64)","clock=311","clock=9","stamp=nil"])
def check_page_lifecycle_refuses(code):
    r=build();c=capture(r);r.execute(code)
    assert page(r,c)["status"]=="REFUSED"

def check_capture_nonce_supersession_and_retry():
    r=build();c=capture(r);calls=r.globals().nativecalls
    assert capture(r)["capture_id"]==c["capture_id"]
    assert r.globals().nativecalls==calls
    r.execute("nonce=string.rep('f',32)")
    c2=capture(r);assert c2["capture_id"]!=c["capture_id"]
    assert page(r,c)["status"]=="REFUSED"
    r.execute("nonce=string.rep('e',32)")
    assert capture(r)["status"]=="REFUSED"

@pytest.mark.parametrize("kind,offset",[("foreign",0),("blueprints",-1),("blueprints",0.5),("blueprints",3)])
def check_page_arguments_refuse(kind,offset):
    r=build();c=capture(r)
    assert page(r,c,kind,offset)["status"]=="REFUSED"

def check_bounded_encoded_cache_and_page():
    r=build("d.encode=function(x)return string.rep('x',524289) end")
    assert capture(r)["status"]=="REFUSED"
    r=build();c=capture(r);r.execute("d.encode=function(x)return string.rep('x',12001) end")
    assert page(r,c)["status"]=="REFUSED"

def check_dispose_invalidates_capture():
    r=build();c=capture(r);r.globals().api.dispose()
    assert page(r,c)["status"]=="REFUSED" and capture(r)["status"]=="REFUSED"

def check_native_blueprint_pointer_uses_existing_type():
    r=build()
    r.execute("native_fill=ffi.cast('uint32_t(*)(UIBlueprint*,uint32_t,const char*,const char*,const char*)',function(buf,n,a,b,c) buf[0].ware=cs('ware_alpha');return n end);C.GetBlueprints=native_fill;wares={'ware_alpha'}")
    c=capture(r);assert c["blueprints"]["complete"] is True

@pytest.mark.parametrize("layout",["const char *ware,*macro,*productionmethodid;","void *macro,*ware,*productionmethodid;"])
def check_bad_native_layout_refuses(layout):
    r=luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute(SETUP.replace("const char *macro,*ware,*productionmethodid;",layout))
    r.globals().factory=r.execute(SOURCE.read_text())
    ok,why=r.eval("function()return pcall(factory,d)end")()
    assert not ok and "blueprint ABI" in str(why)


def use_native_wire(r):
    wire=SOURCE.with_name("live_actionwire.lua")
    r.globals().wire=r.execute(wire.read_text(encoding="utf-8"))()
    r.execute("d.encode=wire.encode")

def assert_outer_wire(r,value):
    r.globals().returned=value
    r.execute("payload=wire.encode(returned);assert(#payload<=3000);outer=wire.encode({version=1,stamp=stamp,build='synthetic-build',age=clock,status='OK',payload=payload});assert(#outer<65536)")
    decoded=json.loads(r.globals().outer)
    assert json.loads(decoded["payload"])["status"]==value["status"]

def check_actual_wire_large_census_pages_cover_every_identity():
    r=build("wares={};for i=1,200 do wares[i]=string.format('%04d',i)..string.rep('x',252) end")
    use_native_wire(r);c=capture(r)
    assert c["status"]=="OBSERVED" and c["blueprints"]["count"]==200
    assert_outer_wire(r,c)
    offset=0;seen=[];sizes=[]
    while offset<200:
        p=page(r,c,offset=offset);assert p["status"]=="OBSERVED"
        assert_outer_wire(r,p)
        sizes.append(len(p["rows"]))
        seen.extend(p["rows"][i]["ware"] for i in range(1,len(p["rows"])+1))
        assert p["next_offset"]>offset
        offset=p["next_offset"]
    assert len(seen)==len(set(seen))==200 and max(sizes)<16 and p["done"] is True

def check_actual_wire_licence_long_identifiers_are_paged():
    r=build("catalog={};holdings={};for i=1,40 do local s=string.format('%04d',i)..string.rep('x',252);catalog[i]={id=s,type=s};holdings[s]=i%2==0 end")
    use_native_wire(r);c=capture(r);assert c["status"]=="OBSERVED"
    offset=0;seen=[]
    while offset<40:
        p=page(r,c,"licences",offset);assert p["status"]=="OBSERVED"
        assert_outer_wire(r,p)
        seen.extend(p["rows"][i]["id"] for i in range(1,len(p["rows"])+1))
        assert p["next_offset"]>offset
        offset=p["next_offset"]
    assert len(seen)==len(set(seen))==40

def check_actual_wire_oversized_metadata_refuses():
    r=build("stamp.world_token=string.rep('x',4000)")
    use_native_wire(r)
    result=capture(r);assert result["status"]=="REFUSED"
    assert_outer_wire(r,result)

def check_actual_wire_one_escaped_row_cannot_spin_or_truncate():
    r=build("local s=string.rep(string.char(34),256);catalog={{id=s,type=s}};holdings={[s]=true}")
    use_native_wire(r);c=capture(r);assert c["status"]=="OBSERVED"
    p=page(r,c,"licences");assert p["status"]=="REFUSED"
    assert_outer_wire(r,p)
    assert r.globals().api.metadata(c["capture_id"])["licences"]["count"]==1

def check_actual_wire_cache_bound_is_still_enforced():
    r=build("wares={};for i=1,8192 do wares[i]=string.format('%04d',i)..string.rep('x',252) end")
    use_native_wire(r);assert capture(r)["status"]=="REFUSED"

def check_actual_wire_empty_complete_and_failed_censuses_differ():
    r=build("wares={}");use_native_wire(r);c=capture(r);p=page(r,c)
    assert p["complete"] is True and p["total"]==0 and p["done"] is True
    assert_outer_wire(r,p)
    r=build("count_error=true");use_native_wire(r);c=capture(r);p=page(r,c)
    assert p["complete"] is False and p["status"]=="UNKNOWN"
    assert_outer_wire(r,p)

def check_piecewise_cache_accounting_matches_full_json_for_small_capture():
    r=build();use_native_wire(r);c=capture(r)
    # The cache is private: reconstruct its documented source-shaped fields.
    r.globals().meta=c
    r.execute("lic=api.page(meta.capture_id,'licences',0);blue=api.page(meta.capture_id,'blueprints',0);expected={nonce=nonce,id=meta.capture_id,stamp=meta.stamp,start_time=meta.start_time,end_time=meta.end_time,calls=meta.native_calls,peer_id=meta.peer_id,faction=meta.faction,money=meta.money,licences={info=meta.licences,rows=lic.rows},blueprints={info=meta.blueprints,rows=blue.rows},complete=meta.complete,retained_bytes=meta.retained_json_bytes};assert(#wire.encode(expected)==meta.retained_json_bytes)")

def _cases():
    cases=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                cases.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:cases.append(pytest.param(name,[],id=name))
    return cases
@pytest.mark.parametrize("name,args",_cases())
def test_luajit_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

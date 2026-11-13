"""Actual LuaJIT crew-role census controls; natives are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_people.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t UniverseID;typedef uint64_t NPCSeed;
typedef struct {const char *id,*name,*desc;uint32_t amount,numtiers;bool canhire;} PeopleInfo;
typedef struct {const char *name;int32_t skilllevel;uint32_t amount;} RoleTierData;]]
function u(s)local n=ffi.new('uint64_t',0);for v in s:gmatch('.')do n=n*10+tonumber(v)end;return n end
obj=u('9007199254740993')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
clock=10;nonce=string.rep('d',32);calls=0;fills=0;strings={}
roles={{id='service',amount=2,tiers={{skill=2,people={'9007199254740993','18446744073709551615'}}}},
 {id='unassigned',amount=1,people={'3'}}}
function cs(s)local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return b end
function role(id)for _,v in ipairs(roles)do if v.id==id then return v end end;error('unknown role')end
C={}
C.IsComponentClass=function(id,kind)return not wrongclass and id==obj and kind=='ship'end
C.GetPeopleCapacity=function(id,macro,pilot)assert(id==obj and macro=='' and pilot==false);return badcapacity or 20 end
C.GetNumAllRoles=function()calls=calls+1;if callerror then error('unreadable')end;return badroles or #roles+1 end
C.GetPeople2=function(buf,n,id,arriving)
 assert(id==obj and arriving==true);fills=fills+1
 for i,r in ipairs(roles)do
  buf[i-1].id=cs(r.id);buf[i-1].name=nullname and nil or cs(r.name or r.id);buf[i-1].desc=cs('native description')
  buf[i-1].amount=r.amount;buf[i-1].numtiers=r.tiers and #r.tiers or 0;buf[i-1].canhire=r.id~='unassigned'
 end
 if nullname and #roles>0 then buf[0].name=nil end
 if fielddrift and fills==2 and #roles>0 then buf[0].canhire=false end
 if worlddrift then stamp.generation=string.rep('e',32)end
 return overflowfill and n+1 or #roles
end
C.GetRoleTiers=function(buf,n,id,rid)
 local r=role(rid);assert(id==obj and n==(r.tiers and #r.tiers or 0))
 for i,t in ipairs(r.tiers or {})do buf[i-1].name=cs('Tier');buf[i-1].skilllevel=t.skill;buf[i-1].amount=#t.people end
 return shorttiers and n+1 or n
end
C.GetRoleTierNPCs=function(buf,n,id,rid,skill)
 local r=role(rid);local p=r.people
 if r.tiers then for _,t in ipairs(r.tiers)do if t.skill==skill then p=t.people;break end end end
 assert(id==obj and #p==n)
 for i,s in ipairs(p)do buf[i-1]=u(s)end
 return shortpeople and math.max(0,n-1) or n
end
C.IsPersonTransferScheduled=function(id,seed)assert(id==obj);if unknownbool then return nil end;return seed==u('3')end
C.HasPersonArrived=function(id,seed)assert(id==obj);return seed~=u('18446744073709551615')end
g={IsValidComponent=function(id)return not invalid and id==obj end}
d={ffi=ffi,C=C,globals=g,resolve_issued=function(token,kind)assert(token=='ship' and kind=='object');if revoked then return nil end;return obj end,
current=function()return stamp end,now=function()return clock end,encode=function(v)return wire.encode(v)end}
"""
def build(code="",setup=SETUP):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(setup)
    r.globals().wire=r.execute(SOURCE.with_name("live_actionwire.lua").read_text(encoding="utf-8"))()
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)");r.execute(code);return r
def capture(r):return r.globals().api.capture("ship",r.globals().nonce)
def pages(r,c):
    rows=[];offset=0
    while True:
        p=r.globals().api.page(c["capture_id"],offset);assert p["status"]=="OBSERVED"
        r.globals().out=p
        raw=r.eval("wire.encode(out)");assert len(raw.encode())<=3000
        rows.extend(p["rows"].values())
        if p["done"]:break
        assert p["next_offset"]>offset;offset=p["next_offset"]
    assert len(rows)==c["total"];return rows

def check_full_roles_and_exact_seeds():
    r=build();c=capture(r);assert c["status"]=="OBSERVED",c["reason"]
    assert c["counts"]["people"]==3 and c["counts"]["roles"]==2 and c["counts"]["role_capacity"]==3
    assert c["counts"]["arrived"]==2 and c["counts"]["not_arrived"]==1 and c["counts"]["transfer_scheduled"]==1
    rows=pages(r,c);ps=[p for p in rows if p["record"]=="PERSON"]
    assert {p["npc_seed"] for p in ps}=={"9007199254740993","18446744073709551615","3"}
    assert all(p["identity_kind"]=="NPCSeed_NOT_COMPONENT" for p in ps)
    assert next(p for p in rows if p["record"]=="ROLE" and p["role"]=="unassigned")["can_hire"] is False
    assert next(p for p in rows if p["record"]=="TIER" and p["role"]=="unassigned")["implicit_zero_tier"] is True

@pytest.mark.parametrize("code",[
"badroles=65","badroles=-1","badroles=0/0","badcapacity=0/0","overflowfill=true","shorttiers=true",
"shortpeople=true","unknownbool=true","callerror=true","invalid=true","revoked=true","wrongclass=true",
"nullname=true","fielddrift=true","worlddrift=true",
"roles[2].id=roles[1].id","roles[2].people={'9007199254740993'}","roles[1].amount=3",
"roles[1].tiers[2]={skill=2,people={}}","roles[2].people={'0'}",
"roles[1].name=string.rep('x',513)","clock=0/0","clock=-1","stamp.world_token=''",
"stamp.extra=true","stamp.world_token=string.rep('x',4000)",
])
def check_refusals(code):
    r=build(code);out=capture(r);assert out["status"]=="REFUSED" and out["complete"] is False

def check_empty_is_qualified():
    r=build("roles={}");c=capture(r);assert c["status"]=="OBSERVED" and c["total"]==0
    assert c["counts"]["people"]==0 and pages(r,c)==[]

def check_multiple_pages_and_copy_isolation():
    r=build("roles={{id='service',amount=120,people={}}};for i=1,120 do roles[1].people[i]=tostring(i)end")
    c=capture(r);assert c["status"]=="OBSERVED",c["reason"];rows=pages(r,c);assert len(rows)==122
    p=r.globals().api.page(c["capture_id"],0);p["rows"][1]["role"]="corrupted"
    assert r.globals().api.page(c["capture_id"],0)["rows"][1]["role"]=="service"

@pytest.mark.parametrize("code",["clock=400","clock=1","stamp.session=string.rep('f',32)","api.dispose()"])
def check_stale_page(code):
    r=build();c=capture(r);r.execute(code);assert r.globals().api.page(c["capture_id"],0)["status"]=="REFUSED"

def check_nonce_reuse_and_supersession():
    r=build();c=capture(r);calls=r.globals().calls;assert capture(r)["capture_id"]==c["capture_id"] and r.globals().calls==calls
    r.execute("nonce=string.rep('e',32)");assert capture(r)["status"]=="OBSERVED"
    assert r.globals().api.page(c["capture_id"],0)["status"]=="REFUSED"
    r.execute("nonce=string.rep('d',32)");assert capture(r)["status"]=="REFUSED"

@pytest.mark.parametrize("old,new",[
("uint64_t NPCSeed","int64_t NPCSeed"),
("uint32_t amount,numtiers","int32_t amount;uint32_t numtiers"),
("int32_t skilllevel","uint32_t skilllevel"),
("const char *id,*name,*desc","const char *name,*id,*desc"),
("bool canhire","uint8_t canhire"),
])
def check_bad_abi_no_calls(old,new):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP.replace(old,new))
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));ok,_=r.eval("function()return pcall(factory,d)end")()
    assert not ok and r.globals().calls==0 and r.globals().fills==0

def check_retained_native_signature():
    r=build()
    r.execute("original=C.GetPeople2;cb=ffi.cast('uint32_t(*)(PeopleInfo*,uint32_t,UniverseID,bool)',function(...)return original(...)end);C.GetPeople2=cb")
    assert capture(r)["status"]=="OBSERVED"

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                out.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:out.append(pytest.param(name,[],id=name))
    return out
@pytest.mark.parametrize("name,args",_cases())
def test_actual_luajit_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

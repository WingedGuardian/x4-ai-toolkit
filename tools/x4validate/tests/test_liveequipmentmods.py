"""Native installed-mod point reads using actual LuaJIT and synthetic native calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_equipmentmods.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t UniverseID;
typedef struct {const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float MassFactor,DragFactor,MaxHullFactor,RadarRangeFactor;uint32_t AddedUnitCapacity,AddedMissileCapacity,AddedCountermeasureCapacity,AddedDeployableCapacity;float RadarCloakFactor,RegionDamageProtection,HideCargoChance;} UIShipMod2;
typedef struct {const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float ForwardThrustFactor,StrafeAccFactor,StrafeThrustFactor,RotationThrustFactor,BoostAccFactor,BoostThrustFactor,BoostDurationFactor,BoostAttackTimeFactor,BoostReleaseTimeFactor,BoostChargeTimeFactor,BoostRechargeTimeFactor,TravelThrustFactor,TravelStartThrustFactor,TravelAttackTimeFactor,TravelReleaseTimeFactor,TravelChargeTimeFactor;} UIEngineMod2;
typedef struct {const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float CapacityFactor,RechargeDelayFactor,RechargeRateFactor;} UIShieldMod;
typedef struct {const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float DamageFactor,CoolingFactor,ReloadFactor,SpeedFactor,LifeTimeFactor,MiningFactor,StickTimeFactor,ChargeTimeFactor,BeamLengthFactor;uint32_t AddedAmount;float RotationSpeedFactor,SurfaceElementFactor;} UIWeaponMod;
typedef struct {UniverseID context;const char *group;UniverseID component;} ShieldGroup;]]
function u(s)local n=ffi.new('uint64_t',0);for v in s:gmatch('.')do n=n*10+tonumber(v)end;return n end
ship=u('9007199254740993');component=u('18446744073709551615');context=u('9007199254740995')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
clock=10;calls=0;groups=0;strings={}
function cs(s)local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return b end
function fill(buf)
 calls=calls+1;if nativeerror then error('native unavailable')end
 if presentunknown then return nil end
 if absent then return false end
 buf.Name=cs(longname or 'native label');buf.RawName=cs('native raw');buf.Ware=cs('mod_alpha');buf.PropertyType=cs('native property');buf.Quality=3
 if nulltext then buf.Ware=nil end
 if changed and calls==2 then buf.Quality=2 end
 if worlddrift then stamp.session=string.rep('e',32)end
 if pcall(function()buf.MassFactor=nanroll and 0/0 or 1.25 end)then end
 if pcall(function()buf.ForwardThrustFactor=1.125 end)then end
 if pcall(function()buf.CapacityFactor=0.875 end)then end
 if pcall(function()buf.DamageFactor=1.5;buf.AddedAmount=4294967295 end)then end
 return true
end
C={}
C.IsComponentClass=function(id,kind)return not wrongclass and ((id==ship and kind=='ship')or(id==component and kind=='weapon'))end
C.GetContextByClass=function(id,kind,inc)assert(id==component and kind=='container' and inc==false);return foreign and u('2')or ship end
C.GetShieldGroup=function(buf,id,part)
 assert(id==ship and part==component);groups=groups+1
 if groupmissing then return false end
 buf.context=context;buf.group=cs(groupempty and '' or 'group_alpha');buf.component=component
 if groupdrift and groups==2 then buf.context=u('77')end
 return true
end
C.GetInstalledShipMod2=function(id,buf)assert(id==ship);return fill(buf)end
C.GetInstalledEngineMod2=function(id,buf)assert(id==ship);return fill(buf)end
C.GetInstalledWeaponMod=function(id,buf)assert(id==component);return fill(buf)end
C.GetInstalledShieldMod=function(id,ctx,group,buf)assert(id==ship and ctx==context and ffi.string(group)=='group_alpha');return fill(buf)end
C.GetInstalledGroupedWeaponMod=C.GetInstalledShieldMod
g={IsValidComponent=function(id)return not invalid and(id==ship or id==component)end}
d={ffi=ffi,C=C,globals=g,resolve_issued=function(token,kind)assert(kind=='object');if revoked then return nil end;if token=='ship'then return ship elseif token=='part'then return component end end,
current=function()return stamp end,now=function()return clock end,encode=function(v)return wire.encode(v)end}
"""
def build(code="",setup=SETUP):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(setup)
    r.globals().wire=r.execute(SOURCE.with_name("live_actionwire.lua").read_text(encoding="utf-8"))()
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)");r.execute(code);return r
def read(r,kind="ship"):
    return r.globals().api.read("ship",kind,None if kind in ("ship","engine") else "part")
@pytest.mark.parametrize("kind",["ship","engine","weapon","turret","shield","grouped_turret"])
def check_native_targets(kind):
    r=build();out=read(r,kind);assert out["status"]=="OBSERVED",out["reason"]
    assert out["target"]["installed"] is True and out["target"]["rolls"]["Ware"]=="mod_alpha"
    assert out["target"]["rolls"]["Quality"]==3 and out["samples"]==2 and out["fitting_census"] is False
    if kind in ("shield","grouped_turret"):
        assert out["target"]["context_id"]=="9007199254740995" and out["target"]["group"]=="group_alpha"
    if kind in ("weapon","turret","grouped_turret"):
        assert out["target"]["rolls"]["AddedAmount"]==4294967295
    r.globals().out=out;assert len(r.eval("wire.encode(out)").encode())<=3000

@pytest.mark.parametrize("kind",["ship","engine","weapon","turret","shield","grouped_turret"])
def check_native_false_is_exact_absence(kind):
    r=build("absent=true");out=read(r,kind)
    assert out["status"]=="OBSERVED" and out["target"]["installed"] is False
    assert list(out["target"]["rolls"].items())==[]

@pytest.mark.parametrize("code,kind",[
("nativeerror=true","ship"),("presentunknown=true","ship"),("nulltext=true","ship"),("nanroll=true","ship"),
("invalid=true","ship"),("revoked=true","ship"),("wrongclass=true","ship"),("changed=true","engine"),
("worlddrift=true","ship"),("foreign=true","weapon"),("groupmissing=true","shield"),("groupempty=true","shield"),
("groupdrift=true","shield"),("clock=0/0","ship"),("clock=-1","ship"),("stamp.world_token=''","ship"),
("stamp.extra=true","ship"),("stamp.world_token=string.rep('x',4000)","ship"),
("longname=string.rep('x',513)","ship"),("api.dispose()","ship"),
])
def check_refusals(code,kind):
    r=build(code);out=read(r,kind);assert out["status"]=="REFUSED" and out["complete"] is False

def check_unsupported_target_before_native():
    r=build();assert read(r,"paint")["status"]=="REFUSED" and r.globals().calls==0
    assert r.globals().api.read("ship","engine","part")["status"]=="REFUSED" and r.globals().calls==0

@pytest.mark.parametrize("old,new",[
("uint64_t UniverseID","int64_t UniverseID"),
("float MassFactor","int32_t MassFactor"),
("uint32_t Quality","int32_t Quality"),
("const char *Name,*RawName,*Ware","const char *RawName,*Name,*Ware"),
("UniverseID context;const char *group;UniverseID component","UniverseID component;const char *group;UniverseID context"),
("float CapacityFactor,RechargeDelayFactor,RechargeRateFactor","double CapacityFactor,RechargeDelayFactor,RechargeRateFactor"),
])
def check_wrong_abi_before_native(old,new):
    assert old in SETUP
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP.replace(old,new))
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));ok,_=r.eval("function()return pcall(factory,d)end")()
    assert not ok and r.globals().calls==0 and r.globals().groups==0

def check_retained_native_pointer_signature():
    r=build();r.execute("original=C.GetInstalledShipMod2;cb=ffi.cast('bool(*)(UniverseID,UIShipMod2*)',function(...)return original(...)end);C.GetInstalledShipMod2=cb")
    assert read(r)["status"]=="OBSERVED"

def check_unknown_group_is_not_absence():
    r=build("absent=true;groupmissing=true");assert read(r,"shield")["status"]=="REFUSED" and r.globals().calls==0

def check_point_results_are_independent():
    r=build();a=read(r);a["target"]["rolls"]["Ware"]="edited"
    assert read(r)["target"]["rolls"]["Ware"]=="mod_alpha"

def check_final_membership_read_session_drift():
    r=build("oldcontext=C.GetContextByClass;contextcalls=0;C.GetContextByClass=function(...)contextcalls=contextcalls+1;if contextcalls==2 then stamp.session=string.rep('e',32)end;return oldcontext(...)end")
    assert read(r,"weapon")["status"]=="REFUSED"

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

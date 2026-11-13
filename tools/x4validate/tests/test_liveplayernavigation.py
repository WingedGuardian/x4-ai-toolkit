"""Real Lua adapter, synthetic engine results. No game or OS input."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_playernavigation.lua"


def make(code="", pre=""):
    r=LuaRuntime(unpack_returned_tuples=True)
    if pre:
        r.execute("ffi=require('ffi');"+pre)
    r.execute("""
ffi=require('ffi')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),
 session=string.rep('b',32),generation=string.rep('c',32),world_token='world'}
time=10;writes=0;docked=false;docking=false;active=false;parent=0ULL
controlled=100ULL;occupied=100ULL;guidance=0ULL;autotarget=0ULL
soft=200ULL;environment=0ULL;eligible=true;possible=true;toggle=true;verdict=true
C={}
function C.GetPlayerControlledShipID()return controlled end
function C.GetPlayerOccupiedShipID()return occupied end
function C.GetContextByClass(v,kind,include)
 assert(kind=='container')
 if v==100ULL then assert(not include);return parent end
 assert(include);return v
end
function C.IsComponentClass(v,kind)return v==200ULL and kind=='station' end
function C.IsAutoPilotActive()return active end
function effect(name)
 writes=writes+1;last_effect=name
 if after_effect then after_effect()end
 return verdict
end
function C.ToggleAutoPilot(check)
 if check then return toggle end
 return effect('autopilot')
end
function C.SetGuidance(target,offset)
 assert(target==200ULL and offset.x==0 and offset.yaw==0)
 effect('guidance');guidance=target
end
function C.RequestDockAt(target,check)
 assert(target==200ULL)
 if check then return eligible end
 return effect('dock_request')
end
function C.QuickDock()return effect('quick_dock')end
function C.GetSofttarget2()return{softtargetID=soft}end
function C.GetEnvironmentObject()return environment end
function C.SetSofttarget(target,connection)
 assert(target==200ULL and connection=='');return effect('target')
end
g={}
function g.GetComponentData(v,key)
 assert(v==100ULL)
 if key=='isdocked'then return docked end
 assert(key=='isdocking');return docking
end
function g.IsValidComponent(v)return v==100ULL or v==200ULL end
function g.IsDockingPossible(s,t)assert(s==100ULL and t==200ULL);return possible end
function g.GetActiveGuidanceMissionComponent()return guidance end
function g.GetAutoPilotTarget()return autotarget end
function g.ConvertIDTo64Bit(v)return v end
deps={ffi=ffi,C=C,globals=g,current=function()return stamp end,now=function()return time end,
 resolve_issued=function(token,kind)
 if token=='ship'then assert(kind=='ship');return 100ULL end
 assert(token=='target' and kind=='object');return 200ULL
 end}
"""+code)
    r.globals().factory=r.execute(SOURCE.read_text())
    r.execute("api=factory(deps)")
    return r


def test_constructor_read_and_docked_observation_never_act():
    r=make()
    assert r.eval("writes")==0
    out=r.eval("api.observe('ship','target')")
    assert out.status=="OBSERVED" and out.atomic is False
    assert out.docked_at_requested_container is False
    r.execute("docked=true;parent=200ULL")
    out=r.eval("api.observe('ship','target')")
    assert out.docked_at_requested_container is True
    assert out.arrival_verified is False
    assert r.eval("writes")==0


@pytest.mark.parametrize("action",["guidance","target","autopilot_start","dock_request","quick_dock"])
def test_actual_dispatch_is_once_only_and_never_arrival(action):
    r=make("guidance=200ULL")
    r.globals().action=action
    r.execute("p=api.prepare(action,'ship','target')")
    assert r.eval("p.status")=="PREPARED" and r.eval("writes")==0
    out=r.eval("api.execute(p.ticket)")
    assert out.status=="DISPATCHED_UNVERIFIED" and out.action_attempted
    assert out.arrival_verified is False and r.eval("writes")==1
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("writes")==1


@pytest.mark.parametrize("code",[
    "controlled=0ULL","occupied=0ULL","controlled=101ULL","docked=true",
    "docking=true","active=true","stamp=nil","time=0/0",
    "guidance=nil","toggle=false",
    "g.IsValidComponent=function()return false end",
    "g.IsValidComponent=function()return nil end",
    "C.IsComponentClass=function()return false end",
    "C.GetPlayerControlledShipID=function()return 100 end",
])
def test_preparation_failure_never_acts(code):
    r=make("guidance=200ULL;"+code)
    out=r.eval("api.prepare('autopilot_start','ship','target')")
    assert out.status=="REFUSED" and not out.action_attempted
    assert r.eval("writes")==0


@pytest.mark.parametrize("code",[
    "eligible=false","possible=false","soft=999ULL",
    "soft=0ULL;environment=999ULL",
])
def test_quick_dock_rejects_eligibility_and_other_target(code):
    r=make(code)
    assert r.eval("api.prepare('quick_dock','ship','target').status")=="REFUSED"
    assert r.eval("writes")==0


def test_environment_fallback_uses_same_native_container():
    r=make("soft=0ULL;environment=200ULL")
    assert r.eval("api.prepare('quick_dock','ship','target').status")=="PREPARED"


@pytest.mark.parametrize("code",[
    "time=16","time=9","stamp.generation=string.rep('d',32)",
    "guidance=300ULL","soft=300ULL","active=true","docking=true",
    "controlled=101ULL","eligible=false",
])
def test_changed_preconditions_consume_without_action(code):
    r=make("guidance=200ULL")
    r.execute("p=api.prepare('autopilot_start','ship','target');"+code)
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    r.execute("time=10;guidance=200ULL;soft=200ULL;active=false;docking=false;controlled=100ULL;eligible=true")
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("writes")==0


@pytest.mark.parametrize("code",[
    "error('native exception')","stamp=nil","time=9",
])
def test_post_native_failure_is_uncertain_and_never_retried(code):
    r=make("after_effect=function()"+code+" end")
    r.execute("p=api.prepare('target','ship','target')")
    out=r.eval("api.execute(p.ticket)")
    assert out.status=="UNCERTAIN" and out.action_attempted
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("writes")==1


@pytest.mark.parametrize("verdict,status",[("false","NATIVE_REFUSED"),("nil","UNCERTAIN"),("1","UNCERTAIN")])
def test_native_return_is_not_success(verdict,status):
    r=make("verdict="+verdict)
    r.execute("p=api.prepare('dock_request','ship','target')")
    out=r.eval("api.execute(p.ticket)")
    assert out.status==status and out.action_attempted
    assert not out.arrival_verified and r.eval("writes")==1


def test_unavailable_guidance_is_not_measured_absence():
    r=make("guidance=nil;autotarget=nil")
    out=r.eval("api.observe('ship','target')")
    assert out.status=="OBSERVED" and out.guidance=="UNKNOWN" and out.autopilot_target=="UNKNOWN"
    assert r.eval("api.prepare('autopilot_start','ship','target').status")=="REFUSED"


def test_unknown_tokens_action_and_retired_tickets_refuse():
    r=make()
    assert r.eval("api.prepare('eval','ship','target').status")=="REFUSED"
    assert r.eval("api.prepare('guidance','foreign','target').status")=="REFUSED"
    r.execute("p=api.prepare('target','ship','target');api.dispose()")
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("api.observe('ship','target').status")=="REFUSED"
    assert r.eval("writes")==0


@pytest.mark.parametrize("declaration,reason",[
    ("typedef struct {double x,y,z,yaw,pitch,roll;} UIPosRot;","position ABI"),
    ("typedef struct {uint32_t x,y,z,yaw,pitch,roll;} UIPosRot;","position scalar ABI"),
    ("typedef struct {int64_t softtargetID;const char* softtargetConnectionName;uint32_t messageID;} SofttargetDetails2;","softtarget identity ABI"),
    ("typedef struct {uint64_t softtargetID;uint32_t messageID;const char* softtargetConnectionName;} SofttargetDetails2;","softtarget field ABI"),
])
def test_actual_constructor_refuses_incompatible_native_layout(declaration,reason):
    with pytest.raises(Exception,match=reason):
        make(pre="ffi.cdef[["+declaration+"]]")


"""Real LuaJIT with stub native order buffers; no game or arrival qualification."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

luajit = pytest.importorskip("lupa.luajit21")
SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_orders.lua"
SETUP = r"""
ffi=require("ffi")
ffi.cdef[[
typedef uint64_t UniverseID; typedef uint64_t AIOrderID;
typedef struct {
size_t queueidx; const char *state,*statename,*orderdef; size_t actualparams;
bool enabled,isinfinite,issyncpointreached,istemporder;
} Order;
typedef struct {
size_t queueidx; const char *state,*statename,*orderdef; size_t actualparams;
bool enabled,isinfinite,issyncpointreached,istemporder,isoverride,ispriority;
} Order3;
]]
function u(s)
 local x=ffi.new("uint64_t",0)
 for c in s:gmatch(".") do x=x*10+tonumber(c) end
 return x
end
ship=u("9007199254740993");target=u("9007199254740994")
pilot=u("9007199254740995");orderid=u("18446744073709551615")
zero=ffi.new("uint64_t",0)
calls,strings,orders,params={},{},{},{}
epoch,age="session-1",10
defaultdef="Wait"
function cs(s)
 local p=ffi.new("char[?]",#s+1,s);strings[#strings+1]=p;return p
end
function fill(b,r,i)
 b.queueidx=i;b.state=cs(r.enabled and "started" or "setup")
 b.statename=cs("state");b.orderdef=cs(r.order)
 b.actualparams=1;b.enabled=r.enabled;b.isinfinite=true
 b.issyncpointreached=sync or false;b.istemporder=false
end
C={}
C.GetNumOrders=function()
 if numorders_fail_after_remove and removed_once then error('queue read failed') end
 return badcount or #orders
end
C.GetOrders3=function(buf,n)
 for i=0,n-1 do
  fill(buf[i],orders[i+1],i+1)
  buf[i].isoverride=false;buf[i].ispriority=false
 end
 return short and n-1 or n
end
C.GetDefaultOrder=function(buf)
 if nodefault then return false end
 fill(buf,{order=defaultdef,enabled=true},0);return true
end
C.GetOrderID=function(_,index)
 if idfail_after_create and created_once then
  idfail_after_create=false
  if swap_def_on_idfail then orders[index].order='Wait' end
  error("order id readback failed")
 end
 return wrongid and u("6") or (orders[index] and orders[index].id or zero)
end
C.GetOrderQueueCurrentIdx=function() return current or (#orders>0 and 1 or 0) end
C.CreateOrder3=function(s,name,def,override,temp)
 assert(s==ship and def==false and override==false and temp==false)
 calls[#calls+1]={"create",name}
 if reenter then reentered=api.execute(ticket) end
 if createerror then error("unknown create outcome") end
 if createzero then return 0 end
 orders[#orders+1]={order=name,enabled=createdenabled or false,id=orderid}
 created_once=true
 return #orders
end
C.EnableOrder=function(s,index)
 calls[#calls+1]={"enable",index}
 if enableerror then error("unknown enable outcome") end
 if enableunknown then return nil end
 if enablefalse then return false end
 orders[index].enabled=true
 if epoch_after_enable then epoch='after-enable' end
 if disappear then orders={} end
 return true
end
C.RemoveOrder=function(s,index,playercancelled,checkonly)
 assert(s==ship and playercancelled==false and type(checkonly)=="boolean")
 if checkonly then
  if check_cancel_drift then orders[#orders+1]={order='Wait',enabled=false,id=u('77')};check_cancel_drift=false end
  return not cancel_disabled
 end
 calls[#calls+1]={"remove",index};removed_once=true
 if cancel_reenter then reentered_cancel=api.execute_cancel(cancel_ticket) end
 if remove_error then error("removal uncertain") end
 if remove_false then return false end
 if not remove_keeps then table.remove(orders,index);table.remove(params,index) end
 if remove_foreign and orders[1] then table.remove(orders,1) end
 if remove_replaces then orders[#orders+1]={order='Wait',enabled=false,id=u('77')} end
 if remove_default then defaultdef='Patrol' end
 if remove_unknown then return nil end
 return true
end
C.IsOrderSelectableFor=function() return not unselectable end
C.IsComponentClass=function(v,class)
 if class=="ship" then return v==ship end
 if class=="sector" then return v==target and not notsector end
 if class=="destructible" or class=="defensible" then return v==target and not notattackable end
 return false
end
C.IsComponentOperational=function() return not inoperational end
C.GetPlayerControlledShipID=function() return controlled and ship or zero end
C.GetPlayerOccupiedShipID=function() return occupied and ship or zero end
g={}
g.IsValidComponent=function() return not invalid end
g.GetComponentData=function(v,key)
 if key=="isplayerowned" then
  if v==ship then return not unowned end
  return targetowned or false
 end
 if key=="assignedpilot" then
  if nopilot then return nil end
  return changedpilot and target or pilot
 end
 error("unknown field")
end
g.ConvertStringToLuaID=function(s)
 if s==tostring(target) then return target end
 error("unexpected conversion")
end
g.ConvertIDTo64Bit=function(v) return v end
g.SetOrderParam=function(s,index,field,unused,value)
 calls[#calls+1]={"param",field}
 assert(s==ship and field==1 and unused==nil)
 if paramerror then error("parameter mutation unknown") end
 local name=orders[index].order=="Attack" and "primarytarget" or "destination"
 params[index]={{name=name,type=orders[index].order=="MoveWait" and "position" or "object",
    value=paramwrong and ship or value}}
end
g.GetOrderParams=function(s,index)
 local p=params[index]
 if p and p[1].type=='position' then
  local v=p[1].value[2]
  if namedpoint and v[1]~=nil then p[1].value[2]={x=v[1],y=v[2],z=v[3]};v=p[1].value[2] end
  if missingpoint then v.z=nil;v[3]=nil end
  if mixedpoint then v.x=1 end
 end
 return p
end
g.IsDockingPossible=function() return not dockrefused end
d={ffi=ffi,C=C,globals=g,
 resolve_issued=function(token,kind)
  if revoked then return nil end
  if token=="ship" and kind=="ship" then return ship end
  if token=="target" and (kind=="object" or kind=="sector") then return target end
 end,
 epoch=function() return epoch end,now=function() return age end}
"""


def build(code=""):
    r = luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute(SETUP)
    r.globals().factory = r.execute(SOURCE.read_text(encoding="utf-8"))
    r.execute("api=factory(d)")
    r.execute(code)
    return r


def prepare(r, action="dock"):
    point = r.table_from([10, 20, -30]) if action == "move" else None
    return r.globals().api.prepare("ship", action, "target", "require_empty", point)


@pytest.mark.parametrize("action,definition", [("move", "MoveWait"), ("dock", "DockAndWait"), ("attack", "Attack")])
def check_native_order_path_and_exact_ids(action, definition):
    r = build()
    p = prepare(r, action)
    assert p["status"] == "PREPARED"
    out = r.globals().api.execute(p["ticket"])
    assert out["status"] == "ORDER_OBSERVED", out["reason"]
    assert out["order_id"] == "18446744073709551615"
    assert out["queue_index"] == 1 and out["arrival_verified"] is False
    assert out["after"]["ship"] == "9007199254740993"
    calls = list(r.globals().calls.values())
    assert [c[1] for c in calls] == ["create", "param", "enable"]
    assert calls[0][2] == definition
    assert r.globals().api.execute(p["ticket"])["status"] == "ORDER_OBSERVED"
    assert len(r.globals().calls) == 3


def check_full_queue_is_observed_not_assumed_empty():
    r = build("orders={{order='DockAndWait',enabled=false,id=orderid},{order='Wait',enabled=true,id=u('88')}}")
    q = r.globals().api.observe("ship")
    assert q["status"] == "OBSERVED" and q["count"] == 2
    assert q["rows"][1]["enabled"] is False
    assert q["rows"][2]["id"] == "88"
    assert q["atomic"] is False
    assert prepare(r)["status"] == "REFUSED"


@pytest.mark.parametrize("code", [
    "unowned=true", "nopilot=true", "inoperational=true", "unselectable=true",
    "controlled=true", "occupied=true", "invalid=true", "revoked=true",
    "dockrefused=true", "defaultdef='Patrol'", "badcount=65", "badcount=-1",
    "badcount=0/0", "badcount=1.5",
    "orders={{order='Wait',enabled=false,id=orderid}}",
])
def check_prerequisites_refuse_without_native_mutation(code):
    r = build(code)
    assert prepare(r)["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


@pytest.mark.parametrize("action,code", [
    ("move", "notsector=true"), ("attack", "notattackable=true"),
    ("attack", "targetowned=true"),
])
def check_target_specific_guards(action, code):
    r = build(code)
    assert prepare(r, action)["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


@pytest.mark.parametrize("code", [
    "epoch='different'", "age=41", "age=9", "changedpilot=true",
    "occupied=true", "revoked=true", "defaultdef='Patrol'",
    "orders={{order='Wait',enabled=false,id=orderid}}",
])
def check_prepare_drift_consumed_without_calls(code):
    r = build()
    p = prepare(r)
    assert p["status"] == "PREPARED"
    r.execute(code)
    out = r.globals().api.execute(p["ticket"])
    assert out["status"] == "REFUSED" and out["action_attempted"] is False
    assert r.globals().api.execute(p["ticket"])["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


@pytest.mark.parametrize("code,status,callcount", [
    ("createzero=true", "NOT_CREATED", 1),
    ("createerror=true", "UNCERTAIN", 1),
    ("enableerror=true", "UNCERTAIN", 3),
    ("enableunknown=true", "UNCERTAIN", 3),
    ("enablefalse=true", "PARTIAL_CREATED_NOT_ENABLED", 3),
    ("disappear=true", "UNCERTAIN", 3),
    ("epoch_after_enable=true", "UNCERTAIN", 3),
])
def check_partial_attempts_retained_never_retried_or_cleaned(code, status, callcount):
    r = build(code)
    p = prepare(r)
    out = r.globals().api.execute(p["ticket"])
    assert out["status"] == status, out["reason"]
    assert out["action_attempted"] is True
    assert out["automatic_cleanup"] is False and out["automatic_retry"] is False
    assert r.globals().api.execute(p["ticket"])["status"] == status
    assert len(r.globals().calls) == callcount


def check_reentrant_dispatch_consumed_before_create():
    r = build("reenter=true")
    p = prepare(r)
    r.globals().ticket = p["ticket"]
    assert r.globals().api.execute(p["ticket"])["status"] == "ORDER_OBSERVED"
    assert r.globals().reentered["status"] == "UNCERTAIN"
    assert len(r.globals().calls) == 3


def check_move_refuses_inexact_unknown_coordinates_and_policy():
    r = build()
    for point in ([0, float("nan"), 1], [1, 2, 10000001], [1, 2]):
        out = r.globals().api.prepare("ship", "move", "target", "require_empty", r.table_from(point))
        assert out["status"] == "REFUSED"
    assert r.globals().api.prepare("ship", "dock", "target", "clear", None)["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


def check_syncpoint_is_raw_not_arrival_pass():
    r = build("orders={{order='MoveWait',enabled=true,id=orderid}};sync=true")
    out = r.globals().api.observe("ship")
    assert out["rows"][1]["syncpoint_reached"] is True
    assert out["arrival_verified"] is False


def check_prepared_evidence_is_not_mutable_internal_state():
    r = build()
    p = prepare(r)
    p["before"]["default"]["orderdef"] = "Patrol"
    r.execute("defaultdef='Patrol'")
    assert r.globals().api.execute(p["ticket"])["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


def check_bad_alias_refuses_before_any_native_calls():
    r = luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute('ffi=require("ffi");ffi.cdef("typedef int64_t AIOrderID;")')
    r.globals().factory = r.execute(SOURCE.read_text(encoding="utf-8"))
    r.execute("""
    calls=0
    local function f() calls=calls+1;error("native called") end
    d={ffi=ffi,C={},globals={
      IsValidComponent=f,GetComponentData=f,ConvertStringToLuaID=f,
      ConvertIDTo64Bit=f,SetOrderParam=f,GetOrderParams=f,IsDockingPossible=f},
      resolve_issued=f,epoch=f,now=f}
    """)
    ok, why = r.eval("function() return pcall(factory,d) end")()
    assert not ok and "unsigned 64-bit alias" in str(why)
    assert r.globals().calls == 0


@pytest.mark.parametrize("old,new", [
    ("bool enabled,isinfinite,issyncpointreached,istemporder,isoverride,ispriority;",
     "uint8_t enabled,isinfinite,issyncpointreached,istemporder,isoverride,ispriority;"),
    ("size_t queueidx;", "int64_t queueidx;"),
    ("const char *state,*statename,*orderdef;", "void *state,*statename,*orderdef;"),
    ("bool enabled,isinfinite,issyncpointreached,istemporder,isoverride,ispriority;",
     "bool enabled,isinfinite,issyncpointreached,istemporder,ispriority,isoverride;"),
])
def check_wrong_native_layout_refuses_before_calls(old, new):
    r = luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute(SETUP.replace(old, new))
    r.globals().factory = r.execute(SOURCE.read_text(encoding="utf-8"))
    r.execute("native_count=0;d.C=setmetatable({},{__index=function() native_count=native_count+1;error('native called') end})")
    ok, why = r.eval("function() return pcall(factory,d) end")()
    assert not ok and "native order" in str(why)
    assert r.globals().native_count == 0


def check_empty_queue_never_guesses_current_native_index():
    r = build("C.GetOrderQueueCurrentIdx=function() error('must not query empty queue') end")
    out = r.globals().api.observe("ship")
    assert out["status"] == "OBSERVED" and out["current"] is None
    assert out["current_scope"] == "NOT_QUERIED_EMPTY_QUEUE"


@pytest.mark.parametrize("code,status", [("namedpoint=true", "ORDER_OBSERVED"),
    ("namedpoint=true;missingpoint=true", "ROLLED_BACK"), ("mixedpoint=true", "ROLLED_BACK")])
def check_native_position_readback_shapes(code, status):
    r = build(code)
    p = r.globals().api.prepare("ship", "move", "target", "require_empty", r.table_from([0.1, 20, 0]))
    assert p["status"] == "PREPARED"
    assert p["requested_position"][1] == 0.1
    assert p["position"][1] != 0.1 and p["position_format"] == "NATIVE_FLOAT32_XYZ"
    out = r.globals().api.execute(p["ticket"])
    assert out["status"] == status, out["reason"]
    # ORDER_OBSERVED: create, param, enable. ROLLED_BACK: create, param, remove.
    assert len(r.globals().calls) == 3


@pytest.mark.parametrize("fields", ["int32_t x,y,z,yaw,pitch,roll;", "double x,y,z,yaw,pitch,roll;",
    "float y,x,z,yaw,pitch,roll;"])
def check_wrong_position_layout_refuses_before_calls(fields):
    r = luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute(SETUP)
    r.execute('ffi.cdef("typedef struct {' + fields + '} UIPosRot;")')
    r.globals().factory = r.execute(SOURCE.read_text(encoding="utf-8"))
    r.execute("native_count=0;d.C=setmetatable({},{__index=function() native_count=native_count+1;error('native called') end})")
    ok, why = r.eval("function() return pcall(factory,d) end")()
    assert not ok and "native position" in str(why)
    assert r.globals().native_count == 0


def submitted(code=""):
    r=build(code)
    p=prepare(r)
    out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ORDER_OBSERVED",out["reason"]
    r.globals().submitted_ticket=p["ticket"]
    return r,p,out


def check_cancel_exact_created_order_once():
    r,p,out=submitted()
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    assert c["status"]=="PREPARED",c["reason"]
    assert len(r.globals().calls)==3
    result=r.globals().api.execute_cancel(c["ticket"])
    assert result["status"]=="ORDER_REMOVAL_OBSERVED",result["reason"]
    assert result["order_id"]==out["order_id"] and result["after"]["count"]==0
    assert result["arrival_verified"] is False
    assert r.globals().api.execute_cancel(c["ticket"])["status"]=="ORDER_REMOVAL_OBSERVED"
    assert len(r.globals().calls)==4


def check_cancel_preserves_other_orders_and_reindexes():
    r,p,out=submitted()
    r.execute("table.insert(orders,1,{order='Wait',enabled=false,id=u('88')});table.insert(params,1,{});orders[#orders+1]={order='DockAndWait',enabled=false,id=u('99')};params[#params+1]={}")
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    assert c["status"]=="PREPARED" and c["queue_index"]==2
    result=r.globals().api.execute_cancel(c["ticket"])
    assert result["status"]=="ORDER_REMOVAL_OBSERVED",result["reason"]
    assert [v["id"] for v in result["after"]["rows"].values()]==["88","99"]
    assert [v["queueidx"] for v in result["after"]["rows"].values()]==[1,2]


@pytest.mark.parametrize("code",[
    "orders={}", "orders[1].id=u('88')", "orders[1].order='Patrol'",
    "unowned=true", "nopilot=true", "occupied=true", "controlled=true",
    "inoperational=true", "revoked=true", "epoch='different'", "cancel_disabled=true",
    "orders[2]={order='Wait',enabled=false,id=orderid}", "check_cancel_drift=true",
])
def check_cancel_preparation_refuses_foreign_stale_ambiguous(code):
    r,p,out=submitted();r.execute(code)
    result=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    assert result["status"]=="REFUSED",result
    assert len(r.globals().calls)==3


def check_cancel_requires_own_bound_creation_and_exact_id():
    r=build("orders={{order='DockAndWait',enabled=true,id=orderid}}")
    assert r.globals().api.prepare_cancel("foreign","18446744073709551615")["status"]=="REFUSED"
    r,p,out=submitted()
    assert r.globals().api.prepare_cancel(p["ticket"],"88")["status"]=="REFUSED"
    # A creation that never returned an index binds nothing.
    r=build("createzero=true")
    p=prepare(r);result=r.globals().api.execute(p["ticket"])
    assert result["status"]=="NOT_CREATED"
    assert r.globals().api.prepare_cancel(p["ticket"],"18446744073709551615")["status"]=="REFUSED"


# Delta review 3, item 1: a failure between CreateOrder3 and EnableOrder rolls the still
# disabled order back in the same execute, at the creation index, definition-checked.
@pytest.mark.parametrize("code,calls", [
    ("createdenabled=true", ["create", "remove"]),     # r2-7: strict check after creation
    ("idfail_after_create=true", ["create", "remove"]),  # the id readback itself fails
    ("paramerror=true", ["create", "param", "remove"]),
    ("paramwrong=true", ["create", "param", "remove"]),
])
def check_failure_before_enable_rolls_the_order_back(code, calls):
    r=build(code)
    p=prepare(r);out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ROLLED_BACK" and "rolled back" in out["reason"], out["reason"]
    assert out["action_attempted"] is True and out["automatic_cleanup"] is True
    assert r.eval("#orders")==0
    assert [c[1] for c in r.globals().calls.values()]==calls
    assert r.globals().api.execute(p["ticket"])["status"]=="ROLLED_BACK"


@pytest.mark.parametrize("failure", ["remove_false=true", "remove_error=true", "cancel_disabled=true"])
def check_failed_rollback_is_reported_with_the_index(failure):
    r=build("createdenabled=true;"+failure)
    p=prepare(r);out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ROLLBACK_FAILED" and "left in the queue at index 1" in out["reason"], out["reason"]
    assert r.eval("#orders")==1


# Delta review 4, items 1-2: the rollback's own checks.
def check_rollback_refuses_a_different_definition_at_the_index():
    # The definition check: something else at the creation index is never removed.
    r=build("idfail_after_create=true;swap_def_on_idfail=true")
    p=prepare(r);out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ROLLBACK_FAILED" and "not the requested definition" in out["reason"], out["reason"]
    assert "is left in the queue at index 1" in out["reason"]
    assert [c[1] for c in r.globals().calls.values()]==["create"] and r.eval("#orders")==1


def check_rollback_whose_queue_does_not_shrink_says_may_be_left():
    # The shrink check: removal reported, order still queued.
    r=build("createdenabled=true;remove_keeps=true")
    p=prepare(r);out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ROLLBACK_FAILED" and "did not shrink" in out["reason"], out["reason"]
    assert "may be left in the queue at index 1" in out["reason"] and r.eval("#orders")==1


def check_rollback_whose_queue_cannot_be_reread_says_may_be_left():
    # rb_probe scenario C: removed, then the confirming queue read throws.
    r=build("createdenabled=true;numorders_fail_after_remove=true")
    p=prepare(r);out=r.globals().api.execute(p["ticket"])
    assert out["status"]=="ROLLBACK_FAILED", out["reason"]
    assert "may be left in the queue at index 1" in out["reason"]
    assert "could not be confirmed" in out["reason"] and r.eval("#orders")==0


def check_rolled_back_submission_never_cancels_a_later_order():
    # A later same-definition order at the creation index is never touched by any cancel.
    r=build("createdenabled=true")
    p=prepare(r);r.globals().api.execute(p["ticket"])
    r.execute("orders[1]={order='DockAndWait',enabled=true,id=u('55')}")
    before=len(list(r.globals().calls.values()))
    for oid in (None,"55","18446744073709551615"):
        assert r.globals().api.prepare_cancel(p["ticket"],oid)["status"]=="REFUSED"
    assert len(list(r.globals().calls.values()))==before and r.eval("#orders")==1


@pytest.mark.parametrize("code",[
    "orders[2]={order='Wait',enabled=false,id=u('88')}", "orders[1].id=u('99')",
    "defaultdef='Patrol'", "occupied=true", "controlled=true", "changedpilot=true",
    "unowned=true", "epoch='changed'", "age=41", "cancel_disabled=true",
])
def check_cancel_execute_rechecks_full_context(code):
    r,p,out=submitted()
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    r.execute(code)
    result=r.globals().api.execute_cancel(c["ticket"])
    assert result["status"]=="REFUSED",result["reason"]
    assert len(r.globals().calls)==3


@pytest.mark.parametrize("code,status",[
    ("remove_false=true","NOT_REMOVED"),("remove_error=true","UNCERTAIN"),
    ("remove_keeps=true","UNCERTAIN"),("remove_unknown=true","UNCERTAIN"),
    ("remove_default=true","UNCERTAIN"),("remove_replaces=true","UNCERTAIN"),
])
def check_cancel_native_failure_never_retries(code,status):
    r,p,out=submitted()
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    r.execute(code)
    result=r.globals().api.execute_cancel(c["ticket"])
    assert result["status"]==status,result["reason"]
    r.globals().api.execute_cancel(c["ticket"])
    assert len(r.globals().calls)==4
    assert r.globals().api.prepare_cancel(p["ticket"],out["order_id"])["status"]=="REFUSED"


def check_cancel_detects_foreign_order_loss():
    r,p,out=submitted()
    r.execute("orders[2]={order='Wait',enabled=false,id=u('88')};params[2]={};remove_foreign=true")
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    result=r.globals().api.execute_cancel(c["ticket"])
    assert result["status"]=="UNCERTAIN"
    assert "other observed" in result["reason"]


def check_cancel_reentry_and_second_ticket_never_repeat_mutation():
    r,p,out=submitted()
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    c2=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    r.globals().cancel_ticket=c["ticket"];r.execute("cancel_reenter=true")
    assert r.globals().api.execute_cancel(c["ticket"])["status"]=="ORDER_REMOVAL_OBSERVED"
    assert r.globals().reentered_cancel["status"]=="UNCERTAIN"
    assert r.globals().api.execute_cancel(c2["ticket"])["status"]=="REFUSED"
    assert len(r.globals().calls)==4


def check_partial_disabled_creation_is_rolled_back_not_left_for_cancel():
    # Delta review 3: a failure before EnableOrder rolls back in the same execute, so no
    # partial disabled creation is left for a later cancel.
    r=build("paramerror=true");p=prepare(r)
    result=r.globals().api.execute(p["ticket"])
    assert result["status"]=="ROLLED_BACK" and r.eval("#orders")==0
    assert r.globals().api.prepare_cancel(p["ticket"],"18446744073709551615")["status"]=="REFUSED"


def check_cancel_prepared_evidence_is_immutable():
    r,p,out=submitted()
    c=r.globals().api.prepare_cancel(p["ticket"],out["order_id"])
    c["before"]["rows"][1]["orderdef"]="Patrol"
    r.execute("orders[1].order='Patrol'")
    assert r.globals().api.execute_cancel(c["ticket"])["status"]=="REFUSED"


def docking_env(code=""):
    return build("""
        olddata=g.GetComponentData
        docked=false; dockhost=target; dockreads=0; hostreads=0
        g.GetComponentData=function(s,key)
          if key=='isdocked' then
            assert(s==ship);dockreads=dockreads+1
            if dockhook then dockhook(dockreads) end
            return docked
          end
          return olddata(s,key)
        end
        C.GetContextByClass=function(s,kind,self)
          assert(s==ship and kind=='container' and self==false)
          hostreads=hostreads+1
          if hosthook then hosthook(hostreads) end
          return dockhost
        end
    """+code)


def check_docking_exact_subject_and_container():
    r=docking_env("docked=true")
    row=r.globals().api.observe("ship")
    assert row["status"]=="OBSERVED"
    assert row["ship"]==row["docking"]["subject"]=="9007199254740993"
    assert row["docking"]["isdocked"] is True
    assert row["docking"]["container"]=="9007199254740994"
    assert row["docking"]["order_caused_docking_verified"] is False
    assert row["arrival_verified"] is False and len(r.globals().calls)==0


def check_other_same_macro_docking_not_attributed():
    r=docking_env("""
        other=target; otherdocked=true
        -- Both ships can have the same macro; the reader never asks for it.
        g.GetComponentData=function(s,key)
          if key=='isdocked' then
            dockreads=dockreads+1
            return s==other and otherdocked or false
          end
          if key=='macro' then error('macro identity must not be used')end
          return olddata(s,key)
        end
    """)
    row=r.globals().api.observe("ship")
    assert row["docking"]["status"]=="OBSERVED"
    assert row["docking"]["isdocked"] is False
    assert row["docking"]["container_scope"]=="NOT_QUERIED_UNDOCKED"
    assert r.globals().hostreads==0 and len(r.globals().calls)==0


@pytest.mark.parametrize("code",[
    "docked=nil", "docked=1",
    "g.GetComponentData=function()error('native unavailable')end",
    "docked=true;dockhost=zero",
    "docked=true;dockhost=ship",
    "docked=true;dockhost=9007199254740992",
    "docked=true;C.GetContextByClass=function()error('native unavailable')end",
    "docked=true;hosthook=function(n)if n==2 then dockhost=pilot end end",
    "dockhook=function(n)if n==2 then docked=true end end",
    "docked=true;g.IsValidComponent=function(v)return v~=target end",
])
def check_docking_unknown_never_default(code):
    r=docking_env(code);row=r.globals().api.observe("ship")
    assert row["status"]=="OBSERVED"
    assert row["docking"]["status"]=="UNKNOWN"
    assert row["docking"]["isdocked"] is None and row["docking"]["container"] is None
    assert len(r.globals().calls)==0


@pytest.mark.parametrize("code",[
    "dockhook=function()epoch='new-world'end",
    "dockhook=function()revoked=true end",
])
def check_docking_identity_drift_refuses_whole_observation(code):
    r=docking_env(code)
    assert r.globals().api.observe("ship")["status"]=="REFUSED"
    assert len(r.globals().calls)==0

def _cases():
    cases = []
    for name, fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):
            continue
        params = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        if params:
            assert len(params) == 1
            for index, args in enumerate(params[0].args[1]):
                values = [args] if len(params[0].args[0].split(",")) == 1 else list(args)
                cases.append(pytest.param(name, values, id=f"{name}-{index}"))
        else:
            cases.append(pytest.param(name, [], id=name))
    return cases


@pytest.mark.parametrize("name,args", _cases())
def test_luajit_control(name, args):
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), name, json.dumps(args)],
        capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CONTROL_PASSED", result.stdout + result.stderr


if __name__ == "__main__":
    case = globals()[sys.argv[1]]
    assert sys.argv[1].startswith("check_") and callable(case)
    case(*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

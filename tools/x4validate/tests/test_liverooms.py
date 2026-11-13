"""Actual LuaJIT transporter mechanism controls; synthetic native/menu responses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_rooms.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t UniverseID;typedef struct {UniverseID component;const char* connection;} UIComponentSlot;]]
function u(s)local n=ffi.new('uint64_t',0);for v in tostring(s):gmatch('.')do n=n*10+tonumber(v)end;return n end
function sid(v)return tostring(v):match('^(%d+)ULL$')end
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
clock=10;counts=0;fills=0;transports=0;player=u('18446744073709551615');playerroom=u('10');container=u('100');ship=u('0')
strings={}
function cs(s)local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return ffi.cast('const char*',b)end
m={name='TransporterMenu',shown=true,minimized=false,transportercomponent=u('100'),transporterconnection='source',
 param={[3]=u('100'),[4]='source'},buttonGoTo=function()error('rendered handler must not be faked')end}
menus={m}
targets={{component='100',connection='source',room='10',locked=false,zone=false,name='current'},
 {component='200',connection='bridge',room='20',locked=false,zone=false,name='bridge'}}
function row(slot)
 local id,c=sid(slot.component),ffi.string(slot.connection)
 for _,r in ipairs(targets)do if r.component==id and r.connection==c then return r end end
 error('unknown synthetic slot')
end
C={}
C.GetPlayerID=function()return player end
C.GetPlayerContainerID=function()return container end
C.GetPlayerOccupiedShipID=function()return ship end
C.GetContextByClass=function(id,kind,self)assert(id==player and kind=='room' and self==false);return playerroom end
C.IsComponentClass=function(id,kind)
 if kind=='room' then return not wrongroom end
 if kind=='zone' then for _,r in ipairs(targets)do if sid(id)==r.component then return r.zone end end end
 error('class query')
end
C.GetNumValidTransporterTargets2=function(id,slot)
 counts=counts+1;assert(id==m.transportercomponent and ffi.istype('UIComponentSlot',slot))
 if count_error then error('unknown count')end
 if badcount~=nil then return badcount end
 if count_drift and counts==2 then return #targets+1 end
 return #targets
end
C.GetValidTransporterTargets2=function(buf,n,id,slot)
 fills=fills+1;assert(ffi.istype('UIComponentSlot*',ffi.cast('UIComponentSlot*',buf)))
 if fill_error then error('unknown fill')end
 for i,r in ipairs(targets)do buf[i-1].component=u(r.component);buf[i-1].connection=cs(r.connection)end
 if null_connection then buf[0].connection=nil end
 if row_drift and fills==2 then buf[1].connection=cs('other') end
 if world_drift then stamp.session=string.rep('f',32)end
 if location_drift then container=u('999')end
 if clock_back then clock=9 end
 if badfill~=nil then return badfill end
 return n
end
C.GetRoomForTransporter=function(slot)
 if slot.component==m.transportercomponent and ffi.string(slot.connection)==m.transporterconnection then
  if source_room_override then return u(source_room_override)end
  if sid(slot.component)=='100' and m.transporterconnection=='source' then return u('10')end
  if sid(slot.component)=='200' and m.transporterconnection=='bridge' then return u('20')end
 end
 return u(row(slot).room)
end
C.GetTransporterLocationName=function(slot)return cs(row(slot).name)end
C.TransportPlayerToTarget=function(slot)
 transports=transports+1;last_target=sid(slot.component);last_connection=ffi.string(slot.connection)
 if reenter then nested=api.execute(ticket)end
 if transport_error then error('uncertain native effect')end
 if arrive then playerroom=u(row(slot).room)end
end
d={ffi=ffi,C=C,current=function()return stamp end,now=function()return clock end,
 encode=function(v)return wire.encode(v)end,menus=function()return menus end,
 convert_id=function(v)if type(v)=='cdata' then return v end;return u(v)end,
 component_data=function(id,key)
  assert(key=='islocked');if lock_unknown then return nil end
  for _,r in ipairs(targets)do if r.room==sid(id)then return r.locked end end
  error('lock unknown')
 end}
"""
def build(code="",setup=SETUP):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(setup)
    r.globals().wire=r.execute(SOURCE.with_name("live_actionwire.lua").read_text(encoding="utf-8"))()
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)");r.execute(code);return r
def capture(r):
    return r.globals().api.targets()
def prepare(r,index=2):
    v=capture(r);assert v["status"]=="OBSERVED",v["reason"]
    result=r.globals().api.prepare(v["capture"],index)
    ticket,reason=result if isinstance(result,tuple) else (result,None)
    return ticket,reason
def success_ticket(r):
    t,reason=prepare(r);assert t is not None,reason;r.globals().ticket=t;return t

def check_location_exact_identity_unknown_zero_and_no_walk():
    r=build();v=r.globals().api.location()
    assert v["status"]=="OBSERVED" and v["location"]["player"]=="18446744073709551615"
    assert v["location"]["room"]=="10" and v["location"]["walking_verified"] is False
    r.execute("playerroom=u('0')")
    assert r.globals().api.location()["location"]["room_status"]=="UNKNOWN"

def check_targets_denominator_and_real_source_slot():
    r=build();v=capture(r)
    assert v["status"]=="OBSERVED",v["reason"]
    assert v["count"]==2 and v["complete_native_target_list"] is True and v["targets"][1]["is_current"] is True
    assert v["source"]["connection"]=="source" and v["menu_opening_supported"] is False
    assert v["rendered_row_coverage"] is False and r.globals().fills==2 and r.globals().counts==4

def check_empty_native_list_is_valid_not_failure():
    r=build("targets={}");v=capture(r)
    assert v["status"]=="OBSERVED" and v["count"]==0 and r.globals().fills==0

def check_success_once_native_mechanism_without_arrival_claim():
    r=build();t=success_ticket(r);out=r.globals().api.execute(t)
    assert out["status"]=="TRANSPORT_REQUESTED" and out["arrival_verified"] is False
    assert out["mechanism"]=="NATIVE_TRANSPORTER_API_NOT_RENDERED_BUTTON"
    assert r.globals().last_target=="200" and r.globals().last_connection=="bridge" and r.globals().transports==1
    assert r.globals().api.execute(t)["status"]=="REFUSED" and r.globals().transports==1
    assert r.globals().api.status(t)["historical"] is True

def check_readback_actual_parent_room_separate_from_request():
    r=build("arrive=true");t=success_ticket(r);out=r.globals().api.execute(t)
    assert out["arrival_verified"] is False
    assert r.globals().api.location()["location"]["room"]=="20"

@pytest.mark.parametrize("code",[
 "menus={}","menus[2]=m","m.shown=false","m.minimized=true","m.param[3]=u('101')",
 "m.param[4]='wrong'","m.transporterconnection='wrong'","m.buttonGoTo=nil",
 "menus[2]={name='OtherMenu',shown=true}","menus.foo=true",
 "badcount=-1","badcount=129","badcount=0/0","badcount=1.5","count_error=true","count_drift=true",
 "badfill=0","badfill=3","badfill=0/0","fill_error=true","null_connection=true",
 "row_drift=true","world_drift=true","location_drift=true","clock_back=true",
 "lock_unknown=true","wrongroom=true","targets[2].component='0'",
 "targets[2]=targets[1]","targets[2].name=string.rep('x',257)",
 "targets[2].connection=string.rep('x',257)","player=u('0')","stamp.extra=true",
 "clock=-1","clock=0/0","api.dispose()"])
def check_invalid_observation_refuses_no_partial(code):
    r=build(code);v=capture(r)
    assert v["status"]=="UNAVAILABLE" and v["targets"] is None and r.globals().transports==0

@pytest.mark.parametrize("code",[
 "targets[2].zone=true","targets[2].locked=true","targets[2].room='0'"])
def check_unsafe_target_not_prepared(code):
    r=build(code);t,reason=prepare(r)
    assert t is None and reason and r.globals().transports==0

def check_current_target_not_actionable():
    r=build();t,reason=prepare(r,1);assert t is None and reason

@pytest.mark.parametrize("code",[
 "stamp.generation=string.rep('f',32)","m.shown=false","targets[2].locked=true",
 "targets[2].zone=true","targets[2].room='30'","targets[2].name='changed'",
 "m.param[4]='different'","container=u('101')","clock=26","api.dispose()"])
def check_prepared_drift_consumes_without_native(code):
    r=build();t=success_ticket(r);r.execute(code)
    assert r.globals().api.execute(t)["status"]=="REFUSED" and r.globals().transports==0
    assert r.globals().api.execute(t)["status"]=="REFUSED"

def check_capture_return_mutation_not_trusted():
    r=build();v=capture(r);v["targets"][2]["component"]="999"
    t,reason=r.globals().api.prepare(v["capture"],2)
    assert t is not None,reason
    assert r.globals().api.execute(t)["target"]["component"]=="200"

def check_expired_capture_and_bad_indices_refused():
    r=build();v=capture(r)
    for idx in (0,3,-1,1.5):
        t,reason=r.globals().api.prepare(v["capture"],idx);assert t is None and reason
    r.execute("clock=41")
    t,reason=r.globals().api.prepare(v["capture"],2);assert t is None and reason

def check_superseded_capture_refused():
    r=build();a=capture(r);capture(r)
    t,reason=r.globals().api.prepare(a["capture"],2);assert t is None and reason

def check_reentrant_execute_consumed():
    r=build("reenter=true");t=success_ticket(r);out=r.globals().api.execute(t)
    assert out["status"]=="TRANSPORT_REQUESTED" and r.globals().nested["status"]=="REFUSED"
    assert r.globals().transports==1

def check_native_throw_unknown_never_replayed():
    r=build("transport_error=true");t=success_ticket(r);out=r.globals().api.execute(t)
    assert out["status"]=="UNKNOWN" and out["action_attempted"] is True
    assert r.globals().api.execute(t)["status"]=="REFUSED" and r.globals().transports==1

def check_capture_and_ticket_caps():
    r=build()
    for _ in range(128):assert capture(r)["status"]=="OBSERVED"
    assert capture(r)["status"]=="UNAVAILABLE"
    r=build();v=capture(r)
    for _ in range(128):
        t,reason=r.globals().api.prepare(v["capture"],2);assert t is not None,reason
    t,reason=r.globals().api.prepare(v["capture"],2);assert t is None and reason

@pytest.mark.parametrize("old,new",[
 ("uint64_t UniverseID","int64_t UniverseID"),
 ("UniverseID component;const char* connection","const char* connection;UniverseID component"),
 ("const char* connection","double* connection")])
def check_wrong_abi_refuses_constructor(old,new):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP.replace(old,new))
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));ok,_=r.eval("function()return pcall(factory,d)end")()
    assert not ok and r.globals().transports==0


@pytest.mark.parametrize("code",[
 "stamp.generation=string.rep('f',32)","container=u('999')","clock=26"])
def check_final_conversion_drift_refuses(code):
    r=build();t=success_ticket(r)
    r.globals().drift_code=code
    r.execute("oldconvert=d.convert_id;d.convert_id=function(v)local out=oldconvert(v);if v=='200' then assert(loadstring(drift_code))()end;return out end")
    assert r.globals().api.execute(t)["status"]=="REFUSED" and r.globals().transports==0

def check_second_prepared_ticket_blocked_while_unresolved():
    r=build();v=capture(r);a,_=r.globals().api.prepare(v["capture"],2);b,_=r.globals().api.prepare(v["capture"],2)
    assert r.globals().api.execute(a)["status"]=="TRANSPORT_REQUESTED"
    assert r.globals().api.execute(b)["status"]=="REFUSED" and r.globals().transports==1
    v=capture(r);assert v["unresolved_transport"] is True
    t,reason=r.globals().api.prepare(v["capture"],2);assert t is None and reason

@pytest.mark.parametrize("code",[
 "clock=1000","playerroom=u('20')",
 "m.transportercomponent=u('200');m.param[3]=u('200');m.transporterconnection='bridge';m.param[4]='bridge'",
 "stamp.generation=string.rep('f',32)"])
def check_timeout_or_partial_successor_does_not_unlock(code):
    r=build();t=success_ticket(r);r.globals().api.execute(t);r.execute(code)
    v=capture(r)
    assert v["status"]=="UNAVAILABLE" or v["unresolved_transport"] is True
    assert r.globals().api.status(t)["unresolved_transport"] is True

@pytest.mark.parametrize("omit_current",[False,True])
def check_exact_successor_menu_slot_and_player_room_unlock(omit_current):
    r=build();t=success_ticket(r);r.globals().api.execute(t)
    r.execute("m.transportercomponent=u('200');m.param[3]=u('200');m.transporterconnection='bridge';m.param[4]='bridge';playerroom=u('20');container=u('200')")
    if omit_current:r.execute("targets[2]=nil")
    v=capture(r);assert v["status"]=="OBSERVED" and v["unresolved_transport"] is False
    b,reason=r.globals().api.prepare(v["capture"],1);assert b is not None,reason
    assert r.globals().api.execute(b)["status"]=="TRANSPORT_REQUESTED" and r.globals().transports==2

def check_native_failure_retains_quarantine():
    r=build("transport_error=true");t=success_ticket(r)
    assert r.globals().api.execute(t)["status"]=="UNKNOWN"
    assert capture(r)["unresolved_transport"] is True

def check_successor_source_room_disagreement_retains_quarantine():
    r=build();t=success_ticket(r);r.globals().api.execute(t)
    r.execute("m.transportercomponent=u('200');m.param[3]=u('200');m.transporterconnection='bridge';m.param[4]='bridge';playerroom=u('20');source_room_override='30';targets[2].room='30'")
    v=capture(r);assert v["status"]=="OBSERVED" and v["unresolved_transport"] is True
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

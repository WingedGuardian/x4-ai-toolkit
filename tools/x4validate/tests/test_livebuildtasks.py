"""Actual LuaJIT/native FFI build-task controls using synthetic data only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_buildtasks.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t UniverseID;typedef uint64_t BuildTaskID;
typedef struct {BuildTaskID id;UniverseID buildingcontainer,component;const char *macro,*factionid;UniverseID buildercomponent;int64_t price;bool ismissingresources;uint32_t queueposition;} BuildTaskInfo;]]
function u(s)
 local n=ffi.new('uint64_t',0)
 for digit in s:gmatch('.') do n=n*10+tonumber(digit) end
 return n
end
obj=u('9007199254740993')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic-world'}
clock=10;nonce=string.rep('d',32);fills=0;countcalls=0;pointcalls=0
tasks={{id='18446744073709551615',active=true,macro='ship_alpha',price='9223372036854775807'},{id='2',active=false,macro='ship_beta',price='-25'}}
strings={}
function cs(s)
 local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return b
end
function signed(s)
 if s:sub(1,1)=='-' then return -ffi.cast('int64_t',u(s:sub(2))) end
 return ffi.cast('int64_t',u(s))
end
function fill(r,t)
 r.id=u(t.id);r.buildingcontainer=foreign and u('9') or obj
 r.component=u(t.component or '0');r.buildercomponent=u(t.builder or '0')
 if nulltext then r.macro=nil else r.macro=cs(t.macro or '') end
 r.factionid=cs(t.faction or 'faction_alpha')
 r.price=signed(t.price or '0');r.ismissingresources=t.missing or false;r.queueposition=t.position or 0
end
C={}
C.IsComponentClass=function(id,kind)return not wrongclass and id==obj and kind=='station' end
C.GetNumBuildTasks=function(id,module,active,upgrades)
 assert(id==obj and module==0 and upgrades==true);countcalls=countcalls+1
 if counterror then error('count unreadable') end
 if badcount then return badcount end
 local n=0;for _,t in ipairs(tasks)do if t.active==active then n=n+1 end end
 if countdrift and countcalls==2 then return n+1 end
 return n
end
C.GetBuildTasks=function(buf,n,id,module,active,upgrades)
 assert(id==obj and module==0 and upgrades==true);fills=fills+1
 if fillerror then error('fill unreadable')end
 local i=0
 for _,t in ipairs(tasks)do if t.active==active then fill(buf[i],t);i=i+1 end end
 if identitydrift and fills==2 then tasks[1].id='8' end
 if fielddrift and fills==2 then tasks[1].missing=true end
 if worlddrift then stamp.session=string.rep('f',32) end
 return shortfill and n-1 or n
end
C.GetBuildTaskInfo=function(id)
 pointcalls=pointcalls+1
 if pointerror then error('task unreadable') end
 local r=ffi.new('BuildTaskInfo')
 if disappeared then return r end
 for _,t in ipairs(tasks)do if id==u(t.id) then fill(r,t);break end end
 if pointwrongid then r.id=u('333') end
 if pointworld then stamp.generation=string.rep('e',32) end
 return r
end
g={IsValidComponent=function(id)return not invalid and id==obj end}
d={ffi=ffi,C=C,globals=g,
 resolve_issued=function(token,kind)assert(token=='container' and kind=='object');if revoked then return nil end;return obj end,
 current=function()return stamp end,now=function()return clock end,
 encode=function(v)return wire.encode(v)end}
"""
def build(code=""):
    r=luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute(SETUP)
    r.globals().wire=r.execute(SOURCE.with_name("live_actionwire.lua").read_text())()
    r.globals().factory=r.execute(SOURCE.read_text())
    r.execute("api=factory(d)")
    r.execute(code)
    return r
def capture(r):
    return r.globals().api.capture("container",r.globals().nonce)
def page(r,c,offset=0):
    return r.globals().api.page(c["capture_id"],offset)
def task(r,c,ident="18446744073709551615"):
    return r.globals().api.task(c["capture_id"],ident)
def outer(r,out):
    r.globals().out=out
    r.execute("payload=wire.encode(out);assert(#payload<=3000);encoded=wire.encode({version=1,stamp=stamp,build='synthetic',age=clock,status='OK',payload=payload})")
    assert json.loads(json.loads(r.globals().encoded)["payload"])["status"]==out["status"]

def check_complete_exact_native_fields():
    r=build();c=capture(r);assert c["status"]=="OBSERVED" and c["total"]==2
    assert c["counts"]["in_progress"]==1 and c["counts"]["other"]==1
    p=page(r,c);a=p["rows"][1];b=p["rows"][2]
    assert a["task_id"]=="18446744073709551615" and a["container_id"]=="9007199254740993"
    assert a["price_native_integer"]=="9223372036854775807" and b["price_native_integer"]=="-25"
    assert a["in_progress_query"] is True and b["in_progress_query"] is False
    assert a["component_id"]=="0" and a["missing_resources"] is False
    assert a["price_unit"]=="NATIVE_UNQUALIFIED";outer(r,c);outer(r,p)

def check_complete_empty_is_not_failure():
    r=build("tasks={}");c=capture(r);p=page(r,c)
    assert c["complete"] is True and c["total"]==0
    assert p["done"] is True and p["rows"] is not None;outer(r,p)
    r=build("counterror=true");c=capture(r)
    assert c["status"]=="REFUSED" and c["complete"] is False and c["total"] is None

@pytest.mark.parametrize("code",["invalid=true","revoked=true","wrongclass=true","counterror=true",
 "badcount=-1","badcount=0/0","badcount=math.huge","badcount=1.5","badcount=4097",
 "countdrift=true","fillerror=true","shortfill=true","identitydrift=true","fielddrift=true",
 "worlddrift=true","foreign=true","nulltext=true","tasks[1].id='0'",
 "tasks[2].id=tasks[1].id","tasks[1].macro=string.rep('x',257)"])
def check_census_refusals(code):
    r=build(code);c=capture(r);assert c["status"]=="REFUSED" and not c["complete"]

def check_known_empty_native_strings_are_preserved():
    r=build("tasks[1].macro='';tasks[1].faction=''");c=capture(r)
    p=page(r,c);assert p["rows"][1]["macro"]=="" and p["rows"][1]["faction"]==""

def check_actual_wire_large_pagination_complete():
    r=build("tasks={};for i=1,80 do tasks[i]={id=tostring(i),active=i%2==0,macro=string.format('%04d',i)..string.rep('x',252)} end")
    c=capture(r);assert c["status"]=="OBSERVED" and c["total"]==80
    offset=0;seen=[]
    while offset<80:
        p=page(r,c,offset);outer(r,p)
        assert p["status"]=="OBSERVED" and p["next_offset"]>offset
        seen.extend(p["rows"][i]["task_id"] for i in range(1,len(p["rows"])+1))
        offset=p["next_offset"]
    assert len(seen)==len(set(seen))==80 and p["done"] is True

@pytest.mark.parametrize("code",["stamp.session=string.rep('e',32)","stamp.generation=string.rep('e',32)",
 "stamp.world_token='different'","stamp.revision=string.rep('e',64)","clock=311","clock=9","stamp=nil"])
def check_cached_page_lifecycle(code):
    r=build();c=capture(r);r.execute(code)
    assert page(r,c)["status"]=="REFUSED"

@pytest.mark.parametrize("offset",[-1,0.5,3])
def check_page_offsets(offset):
    r=build();c=capture(r);assert page(r,c,offset)["status"]=="REFUSED"

def check_ttl_exact_boundary():
    r=build();c=capture(r);r.execute("clock=310")
    assert page(r,c)["status"]=="OBSERVED"

def check_capture_supersession_and_no_replay():
    r=build();c=capture(r);calls=r.globals().fills
    assert capture(r)["capture_id"]==c["capture_id"] and r.globals().fills==calls
    r.execute("nonce=string.rep('e',32)");new=capture(r)
    assert new["capture_id"]!=c["capture_id"] and page(r,c)["status"]=="REFUSED"
    r.execute("nonce=string.rep('d',32)");assert capture(r)["status"]=="REFUSED"

def check_capture_history_cap():
    r=build("tasks={}")
    r.execute("for i=1,128 do nonce=string.format('%032x',i);assert(api.capture('container',nonce).status=='OBSERVED')end;nonce=string.format('%032x',129)")
    assert capture(r)["status"]=="REFUSED"

def check_point_read_is_observation_not_completion():
    r=build();c=capture(r);out=task(r,c)
    assert out["status"]=="OBSERVED" and out["completion_inferred"] is False
    assert out["row"]["in_progress_query"] is None;outer(r,out)
    r.execute("disappeared=true");out=task(r,c)
    assert out["status"]=="NOT_OBSERVED" and out["completion_inferred"] is False

@pytest.mark.parametrize("code",["pointerror=true","pointwrongid=true","pointworld=true","foreign=true","revoked=true","invalid=true"])
def check_point_refusal(code):
    r=build();c=capture(r);r.execute(code);assert task(r,c)["status"]=="REFUSED"

def check_unobserved_task_never_calls_getter():
    r=build();c=capture(r)
    for v in ["8","18446744073709551616","1;evil",""]:
        assert task(r,c,v)["status"]=="REFUSED"
    assert r.globals().pointcalls==0

def check_point_can_show_native_field_transition_without_updating_capture():
    r=build();c=capture(r);r.execute("tasks[1].component='999';tasks[1].missing=true")
    out=task(r,c);assert out["row"]["component_id"]=="999" and out["row"]["missing_resources"] is True
    assert page(r,c)["rows"][1]["component_id"]=="0"

def check_output_copy_isolation():
    r=build();c=capture(r);p=page(r,c);p["rows"][1]["macro"]="changed";c["counts"]["other"]=500
    assert page(r,c)["rows"][1]["macro"]=="ship_alpha"
    assert r.globals().api.metadata(c["capture_id"])["counts"]["other"]==1

def check_wire_header_and_oversized_single_row_refuse():
    r=build("stamp.world_token=string.rep('x',4000)")
    assert capture(r)["status"]=="REFUSED"
    r=build("tasks[1].macro=string.rep(string.char(34),256);tasks[1].faction=string.rep(string.char(34),256)")
    c=capture(r);assert c["status"]=="OBSERVED"
    assert page(r,c)["status"]=="REFUSED"

def check_retained_byte_cap():
    r=build("tasks={};for i=1,4096 do tasks[i]={id=tostring(i),active=true,macro=string.rep('x',256)}end")
    assert capture(r)["status"]=="REFUSED"

def check_dispose():
    r=build();c=capture(r);r.globals().api.dispose()
    assert page(r,c)["status"]=="REFUSED" and capture(r)["status"]=="REFUSED"

def check_retained_native_buffer_signature():
    r=build("tasks={{id='1',active=true}}")
    r.execute("native_fill=ffi.cast('uint32_t(*)(BuildTaskInfo*,uint32_t,UniverseID,UniverseID,bool,bool)',function(buf,n,id,module,active,upgrades)fill(buf[0],tasks[1]);return n end);C.GetBuildTasks=native_fill")
    assert capture(r)["status"]=="OBSERVED"

@pytest.mark.parametrize("old,new",[("int64_t price","uint64_t price"),("uint32_t queueposition","int32_t queueposition"),("const char *macro,*factionid","const char *factionid,*macro")])
def check_incompatible_native_layout(old,new):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP.replace(old,new))
    r.globals().factory=r.execute(SOURCE.read_text())
    ok,why=r.eval("function()return pcall(factory,d)end")()
    assert not ok and "ABI" in str(why)

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

"""Actual Lua control-flow tests with stub native/host calls, never save files."""
from pathlib import Path
import json
import os
import subprocess
import sys
import pytest
luajit = pytest.importorskip("lupa.luajit21")
SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_persistence.lua"
SETUP = r"""
age=10; rows={}; calls={}; ready=true; saveable=true; online=false
stamp={provider="x4-live-session-v1",revision=string.rep("b",64),
session=string.rep("c",32),generation=string.rep("d",32),world_token="world"}
sid=string.rep("1",32); lid=string.rep("2",32)
function cp(v) if type(v)~="table" then return v end local t={} for k,x in pairs(v) do t[k]=cp(x) end return t end
function note(n) calls[#calls+1]=n end
g={
GetSaveList=function() return cp(rows) end,
IsSavingPossible=function()return saveable end,
OnlineIsOnlineModeActive=function()return online end,
getElapsedTime=function()return age end,
SaveGame=function(slot,label) note("save");if saveerror then error("post dispatch error") end end,
LoadGame=function(slot)note("load");if loaderror then error("post dispatch error") end end}
C={ReloadSaveList=function()note("refresh")end,IsSaveListLoadingComplete=function()return ready end,
IsSaveValid=function()return valid~=false end}
H={addDelayedOneTimeCallbackOnUpdate=function(fn,block,due)
assert(block==true and due==age+0.1);callback=fn;note("scheduled");if scheduleerror then error("schedule ambiguous")end end}
host={
current=function()return cp(stamp)end,now=function()return age end,
reserve_save=function(c)note("reserve");if reservation_fail then return false end return {}end,
authorize_save=function(c,t)note("authorize_save");return not gatefail end,
settled_save=function(c,t,row)
 if not settled then return nil end
 return {ticket=c.ticket,slot=c.slot,label=c.label,path="QUALIFIED_HOST_PATH",sha256=string.rep("f",64),
 size=123,stamp=cp(c.stamp)}
end,
authorize_load=function(c,r)note("authorize_load");if loadgatefail then return false end return {}end,
verify_load=function(c,t,r)note("verify_load");return not verifyfail end}
d={globals=g,C=C,Helper=H}
for k,v in pairs(host)do d[k]=v end
function nc(n)local c=0 for _,v in ipairs(calls)do if v==n then c=c+1 end end return c end
function prep() assert(api.refresh().status=="REFRESH_REQUESTED");return api.prepare_save(sid,"save_001") end
function complete()
 assert(prep().status=="PREPARED");assert(api.execute_save(sid).status=="SAVE_REQUESTED")
 rows={{filename="save_001",name="x4live-"..sid,rawtime=123,isonline=false}}
 settled=true;assert(api.refresh().status=="REFRESH_REQUESTED");assert(api.poll_save(sid).status=="SAVE_COMPLETED")
end
"""
def env():
    l=luajit.LuaRuntime(unpack_returned_tuples=True)
    l.execute(SETUP)
    l.globals().factory=l.execute(SOURCE.read_text(encoding="utf-8"))
    l.execute("api=factory(d)")
    return l

def check_full_flow_does_not_claim_load_completion():
    l=env()
    l.execute("""
complete()
assert(api.prepare_load(lid,sid).status=="LOAD_PREPARED")
assert(nc("load")==0)
assert(api.execute_load(lid).status=="LOAD_SCHEDULED")
assert(nc("load")==0)
callback()
assert(nc("load")==1)
assert(api.status(lid).status=="LOAD_REQUESTED" and not api.status(lid).load_completed)
assert(api.execute_load(lid).status=="REFUSED" and nc("load")==1)
""")

@pytest.mark.parametrize("change",[
    "online=true","online=nil","ready=false","saveable=false","saveable=nil",
    'rows={{filename="save_001",name="other",rawtime=1}}',
    'rows={{filename="save_002",name="x4live-"..sid,rawtime=1}}',
    'rows={{filename="foreign",name="n",rawtime=1}}',
    'rows={{filename="save_002",name="n",rawtime=1},{filename="save_002",name="n",rawtime=2}}',
    'rows={{filename="save_002",rawtime=1}}',
    'rows={{filename="save_002",name="n",rawtime=0/0}}',
    "rows={[2]={filename='save_002',name='n',rawtime=1}}",
    "reservation_fail=true","stamp.world_token=''","age=-1",
])
def check_prepare_refusals(change):
    l=env();l.execute(change)
    l.execute('api.refresh();assert(api.prepare_save(sid,"save_001").status=="REFUSED");assert(nc("save")==0)')

@pytest.mark.parametrize("change",[
    "gatefail=true","saveable=false","ready=false","online=true","age=400",
    "stamp.generation=string.rep('e',32)","age=9",
    "rows={{filename='save_002',name='other',rawtime=1}}",
    "rows={{filename='save_001',name='other',rawtime=1}}",
])
def check_execute_recheck(change):
    l=env();l.execute('assert(prep().status=="PREPARED")');l.execute(change)
    l.execute('assert(api.execute_save(sid).status=="REFUSED");assert(nc("save")==0)')

def check_save_exception_no_retry_and_reconcile():
    l=env();l.execute("""
assert(prep().status=="PREPARED");saveerror=true
assert(api.execute_save(sid).status=="UNKNOWN")
assert(api.execute_save(sid).status=="REFUSED" and nc("save")==1)
rows={{filename="save_001",name="x4live-"..sid,rawtime=100}}
assert(api.poll_save(sid).status=="WAITING_HOST_SETTLEMENT")
settled=true
assert(api.poll_save(sid).status=="SAVE_COMPLETED")
""")

def check_no_catalogue_no_completion():
    l=env();l.execute("""
assert(prep().status=="PREPARED");api.execute_save(sid);settled=true
assert(api.poll_save(sid).status=="WAITING_NATIVE_CATALOGUE")
assert(api.prepare_load(lid,sid).status=="REFUSED")
""")

@pytest.mark.parametrize("change",[
    "verifyfail=true","valid=false","online=true","age=400",
    "stamp.session=string.rep('a',32)","rows[1].rawtime=124","api.dispose()",
])
def check_delayed_callback_refusal_never_throws_to_native_helper(change):
    l=env();l.execute('complete();assert(api.prepare_load(lid,sid).status=="LOAD_PREPARED");api.execute_load(lid)')
    l.execute(change);l.execute('callback();assert(nc("load")==0)')

def check_load_exception_not_success():
    l=env();l.execute("""
complete();api.prepare_load(lid,sid);api.execute_load(lid);loaderror=true;callback()
assert(api.status(lid).status=="LOAD_UNCERTAIN" and nc("load")==1)
assert(api.execute_load(lid).status=="REFUSED")
""")

def check_missing_host_gate_refuses_constructor():
    l=env();l.execute("d.reserve_save=nil;assert(not pcall(factory,d));assert(nc('save')==0)")

def check_host_receipt_identity_not_caller_boolean():
    l=env();l.execute("""
prep();api.execute_save(sid)
rows={{filename="save_001",name="x4live-"..sid,rawtime=1}}
d.settled_save=function()return true end
assert(api.poll_save(sid).status=="REFUSED")
d.settled_save=function(c)return {ticket=c.ticket,slot="save_002",label=c.label,path="x",
sha256=string.rep("f",64),size=1,stamp=cp(stamp)}end
assert(api.poll_save(sid).status=="REFUSED")
""")

def check_native_catalogue_preserved_in_preparation():
    l=env();l.execute("""
rows={{filename="save_002",name="other",rawtime=1,extra={nested=true}}}
assert(prep().status=="PREPARED")
rows[1].extra.nested=false
assert(api.execute_save(sid).status=="REFUSED" and nc("save")==0)
""")

def check_no_automatic_load_or_approval_reuse():
    l=env();l.execute('complete();assert(nc("load")==0);api.prepare_load(lid,sid);loadgatefail=true;assert(api.execute_load(lid).status=="REFUSED");loadgatefail=false;assert(api.execute_load(lid).status=="REFUSED");assert(nc("load")==0)')

def check_host_gate_context_drift():
    l=env();l.execute('prep();d.authorize_save=function() stamp.generation=string.rep("e",32);return true end;assert(api.execute_save(sid).status=="REFUSED");assert(nc("save")==0)')

def check_duplicate_delayed_callback_cannot_reload_twice():
    l=env();l.execute('complete();api.prepare_load(lid,sid);api.execute_load(lid);callback();callback();assert(nc("load")==1 and api.status(lid).status=="LOAD_REQUESTED")')

def check_schedule_error_after_capture_is_unknown():
    l=env();l.execute('complete();api.prepare_load(lid,sid);scheduleerror=true;assert(api.execute_load(lid).status=="UNKNOWN");callback();assert(nc("load")==0)')

def check_no_refresh_and_unknown_save_ticket():
    l=env();l.execute('assert(api.prepare_save(sid,"save_001").status=="REFUSED");assert(api.prepare_load(lid,sid).status=="REFUSED");assert(nc("save")==0 and nc("load")==0)')

def _cases():
    result=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):
            continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,arg in enumerate(marks[0].args[1]):
                result.append(pytest.param(name,[arg],id=f"{name}-{i}"))
        else:
            result.append(pytest.param(name,[],id=name))
    return result

@pytest.mark.parametrize("name,args",_cases())
def test_actual_luajit_control(name,args):
    # Caught LuaJIT errors use SEH on Windows; preserve child assertions/exit.
    proc=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert proc.stdout.strip()=="CONTROL_PASSED",proc.stdout+proc.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

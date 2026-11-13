"""Actual Lua adapter with native-UI stubs; no game calls or new worlds."""
from pathlib import Path
import pytest
import json
import os
import subprocess
import sys
lua = pytest.importorskip("lupa.luajit21")
ROOT=Path(__file__).resolve().parents[3]
MODULE=ROOT/"mods/x4_toolkit_helper/ui/live_startmenu.lua"
SETUP=r'''
age,binding,startmenu=10,"ui-instance-1",true
calls,enabled,currentrow={},true,1
option={id="argongamestart",name="The Young Gun",available=true,unlocked=true,
    requirement="",extensionid="",customeditor=false,mapeditor=false,tutorial=false,
    timelinesscenario=false,intro="",introvoice="",introlength=0,introfadeoutlength=0}
other={id="other",name="Other",available=true,unlocked=true,requirement="",extensionid=""}
menu={name="OptionsMenu",shown=true,minimized=false,isStartmenu=true,currentOption="new",
    openSubmenu=function(p,s) calls[#calls+1]="submenu:"..p..":"..s;if submenufail then error("submenu failed") end;menu.currentOption=p end,
    frames={[2]=10},optionTable=20,rowDataMap={[20]={[1]=option,[2]=other}},selectedOption=option}
options={type="table",id=20,properties={tabOrder=1},rows={{rowdata=option},{rowdata=other}}}
cell={type="button",id=30,colspan=3,properties={text={text="Start"},active=true},handlers={}}
handler=function()
    calls[#calls+1]="start"
    if recurse then again=api.execute(ID) end
    if callbackfail then error("native callback failed") end
end
cell.handlers.onClick=handler
starttable={type="table",id=21,properties={tabOrder=4},rows={{cell}}}
frame={id=10,properties={layer=2},content={options,starttable}}
menu.optionsFrame=frame
wrapper=function() calls[#calls+1]="wrapper";return cell.handlers.onClick() end
menu.buttonScriptMap={{layer=2,tableobj=21,row=1,col=1,type="onClick",button=30,script=wrapper}}
menu.buttonStartGameActive=function() return enabled end
menus={menu};ID=string.rep("1",32)
deps={
    menus=function() return menus end,
    now=function() return age end,
    ui_binding=function() return binding end,
    is_startmenu=function() return startmenu end,
    cell_id=function(t,r,c) return wrongcell and 31 or 30 end,
    button_active=function() return enabled end,
    valid_widget=function() return not invalid end,
    current_row=function() return currentrow end,
    read_text=function(p,t) assert(p==1001 and t==9902);return "Start" end,
    select_row=function(t,r)
        calls[#calls+1]="select"
        if not noselect then currentrow=r;menu.selectedOption=menu.rowDataMap[t][r] end
        if selectfail then error("select failed") end
        -- Native exported SelectRow intentionally has no return.
    end,
    open_menu=function(name,param,unused,start)
        assert(name=="OptionsMenu" and param=="new" and unused==nil and start==true)
        calls[#calls+1]="open"
        if openfail then error("open failed") end
    end,
    authorize_start=function(context,cb)
        calls[#calls+1]="authorize";authcontext=context;authcallback=cb
        if not defer then cb(approve~=false,"test dependency only") end
        if authfail then error("authority transport failed") end
    end,
}
'''
def env(code=""):
    rt=lua.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(SETUP)
    rt.globals().factory=rt.execute(MODULE.read_text(encoding="utf-8"))
    rt.execute("api=factory.new(deps)")
    rt.execute(code)
    return rt

def prepare(rt,action="start",option="argongamestart"):
    return rt.globals().api.prepare("1"*32,action,option)

def execute(rt):
    return rt.globals().api.execute("1"*32)

def check_actual_built_callback_and_explicit_unproved_world():
    rt=env();assert prepare(rt)["status"]=="PREPARED"
    result=execute(rt)
    assert result["status"]=="DISPATCHED_UNVERIFIED" and result["world_started"] is False
    assert [rt.globals().calls[i] for i in range(1,4)]==["authorize","wrapper","start"]
    assert execute(rt)["status"]=="REFUSED"
    assert rt.globals().authcontext.scope=="UI_ONLY_NO_WORLD"

def check_select_uses_native_route_and_readback_not_return_value():
    rt=env();assert prepare(rt,"select","other")["status"]=="PREPARED"
    assert execute(rt)["status"]=="SELECTION_OBSERVED"
    assert rt.globals().menu.selectedOption.id=="other"
    assert len(rt.globals().calls)==1

def check_open_new_uses_actual_native_parameters():
    rt=env('menu.currentOption="settings"')
    assert prepare(rt,"open_new",None)["status"]=="PREPARED"
    assert execute(rt)["status"]=="DISPATCHED_UNVERIFIED"
    assert rt.globals().calls[1]=="submenu:new:new" and len(rt.globals().calls)==1   # the vanilla click, not OpenMenu
    assert rt.globals().menu.currentOption=="new"

def check_open_new_refuses_without_native_open_submenu():
    rt=env('menu.currentOption="settings";menu.openSubmenu=nil')
    assert prepare(rt,"open_new",None)["status"]=="REFUSED"
    assert len(rt.globals().calls)==0

def check_open_new_submenu_failure_is_unknown_not_success():
    rt=env('menu.currentOption="settings";submenufail=true')
    assert prepare(rt,"open_new",None)["status"]=="PREPARED"
    assert execute(rt)["status"]=="UNKNOWN"

@pytest.mark.parametrize("change",[
    "startmenu=false","menu.isStartmenu=false","menu.shown=false","menu.minimized=true",
    'menu.contextMenuMode="uisecurity"',"menu.contextFrame={}","menu.animationDelay={}",
    'menus[2]={name="OtherMenu",shown=true}',"menus[2]=menu",
    'menu.currentOption="multiplayer_server"',"invalid=true","wrongcell=true",
    "menu.frames[2]=99","menu.optionTable=99","menu.rowDataMap[20][1]=other",
    'option.id=""',"other.id=option.id","currentrow=2","menu.selectedOption=other",
    "enabled=false;cell.properties.active=false","cell.properties.active=false",
    "menu.buttonScriptMap={}","menu.buttonScriptMap[2]=menu.buttonScriptMap[1]",
    "menu.buttonScriptMap[1].button=31","cell.handlers.onClick=nil",
    "option.available=false","option.unlocked=false",'option.requirement="DLC"',
    "option.customeditor=true","option.mapeditor=true","option.tutorial=true",
    "option.timelinesscenario=true",'option.id="x4ep1_gamestart_hub"',
    'binding=""',"age=0/0","option.corrupt=function() end",
])
def check_preparation_refuses_unknown_or_disallowed_native_state(change):
    rt=env(change);result=prepare(rt)
    assert result["status"]=="REFUSED"
    assert len(rt.globals().calls)==0

@pytest.mark.parametrize("change",[
    "age=41","age=9",'binding="new-ui"', "startmenu=false",
    'menu.contextMenuMode="question"', 'menu.currentOption="load"',
    "frame.id=11;menu.frames[2]=11","frame.content[2]=nil",
    "cell.handlers.onClick=function() end","menu.buttonScriptMap[1].script=function() end",
    'option.name="changed"', "menu.selectedOption=other", "enabled=false;cell.properties.active=false",
])
def check_stale_execute_refuses_before_authority_or_native_callback(change):
    rt=env();assert prepare(rt)["status"]=="PREPARED";rt.execute(change)
    assert execute(rt)["status"]=="REFUSED"
    assert len(rt.globals().calls)==0

def check_deferred_approval_rechecks_and_is_single_use():
    rt=env("defer=true");prepare(rt)
    assert execute(rt)["status"]=="AUTHORIZATION_PENDING"
    rt.execute("authcallback(true);authcallback(true)")
    assert len(rt.globals().calls)==3
    assert rt.globals().api.status("1"*32)["status"]=="DISPATCHED_UNVERIFIED"

@pytest.mark.parametrize("change",['binding="different"', "age=41","menu.selectedOption=other","api.dispose()"])
def check_late_approval_cannot_act_on_changed_context(change):
    rt=env("defer=true");prepare(rt);execute(rt);rt.execute(change+";authcallback(true)")
    assert len(rt.globals().calls)==1
    assert rt.globals().api.status("1"*32)["action_attempted"] is False

def check_authority_refusal_is_not_action():
    rt=env("approve=false");prepare(rt)
    assert execute(rt)["status"]=="REFUSED"
    assert len(rt.globals().calls)==1

@pytest.mark.parametrize("change",["callbackfail=true","authfail=true","recurse=true"])
def check_possible_callback_effect_is_not_replayed(change):
    rt=env(change);prepare(rt);r=execute(rt)
    assert r["action_attempted"] is True
    assert r["status"]==("DISPATCHED_UNVERIFIED" if change=="recurse=true" else "UNKNOWN")
    assert execute(rt)["status"]=="REFUSED"
    assert len(rt.globals().calls)==3

def check_failed_selection_is_unknown_not_selected():
    rt=env("noselect=true");prepare(rt,"select","other")
    assert execute(rt)["status"]=="UNKNOWN"

def check_observation_denominator_and_disabled_button():
    rt=env("enabled=false;cell.properties.active=false")
    r=rt.globals().api.observe()
    assert r["count"]==2 and r["start"]["enabled"] is False
    assert r["world_session"] is False and r["atomic"] is False

def check_serial_limit_and_disposal():
    rt=env('menu.currentOption="settings"')
    for i in range(128):
        assert rt.globals().api.prepare(f"{i:032x}","open_new",None)["status"]=="PREPARED"
    assert rt.globals().api.prepare("f"*32,"open_new",None)["status"]=="REFUSED"
    rt.globals().api.dispose()
    assert rt.globals().api.observe()["status"]=="REFUSED"

def check_unknown_action_and_forged_request():
    rt=env()
    assert rt.globals().api.prepare("1"*32,"rawNewGame",None)["status"]=="REFUSED"
    assert rt.globals().api.prepare("bad","start","argongamestart")["status"]=="REFUSED"
    assert execute(rt)["status"]=="REFUSED"

def pump_env(code=""):
    rt=env()
    rt.globals().wirefactory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text(encoding="utf-8"))
    rt.globals().pumpfactory=rt.execute((ROOT/"mods/x4_toolkit_helper/ui/live_startpipe.lua").read_text(encoding="utf-8"))
    rt.execute(r'''
pending,inputs,outputs={}, {}, {}
pd={
    adapter=api,codec=wirefactory(),now=deps.now,is_startmenu=deps.is_startmenu,ui_binding=deps.ui_binding,
    read_pipe=function(name)
        assert(name=="x4live_startmenu")
        if readfail then return false end
        local value=table.remove(inputs,1);return true,value
    end,
    write_pipe=function(name,value)
        assert(name=="x4live_startmenu");outputs[#outputs+1]=value
        return not writefail
    end,
    schedule=function(cb,block,when)
        assert(block==false and when>age)
        if sync then cb() else pending[#pending+1]=cb end
    end,
}
pump=pumpfactory.new(pd)
function tick()
    age=age+0.2
    local cb=table.remove(pending,1);assert(cb);cb()
end
''')
    rt.execute(code)
    return rt

def packet(op,call=1,**args):
    values=",".join(k+"=s"+str(v).encode().hex() for k,v in args.items())
    return f"1|{call:032x}|ui-instance-1|x4-ui-startmenu-v1|{op}|ui-instance-1|{'a'*64}|{values}"

def feed(rt,value):
    rt.globals().inputs[len(rt.globals().inputs)+1]=value
    rt.globals().tick()

def check_native_dependency_binding():
    rt=env()
    rt.execute(r'''
g={Menus=menus,GetCellContent=deps.cell_id,IsButtonActive=deps.button_active,
IsValidWidgetElement=deps.valid_widget,SelectRow=deps.select_row,OpenMenu=deps.open_menu,
ReadText=deps.read_text,getElapsedTime=deps.now}
C={IsStartmenu=deps.is_startmenu};Helper={currentTableRow={[20]=1}}
nativeapi=factory.native(g,C,Helper,deps.ui_binding,deps.authorize_start)
assert(nativeapi.observe().status=="OBSERVED")
assert(nativeapi.prepare(ID,"start",option.id).status=="PREPARED")
assert(nativeapi.execute(ID).status=="DISPATCHED_UNVERIFIED")
''')

def check_pipe_actual_codec_closed_action_flow():
    rt=pump_env();assert rt.globals().pump.start()["scope"]=="UI_ONLY_NO_WORLD"
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    first=json.loads(rt.globals().outputs[1])
    assert first["result"]["status"]=="PREPARED" and first["world_session"] is False
    feed(rt,packet("execute",2,request_id="1"*32))
    assert json.loads(rt.globals().outputs[2])["result"]["status"]=="DISPATCHED_UNVERIFIED"
    assert len(rt.globals().calls)==3

def check_pipe_exact_retry_is_cached_not_reexecuted():
    rt=pump_env();rt.globals().pump.start()
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    p=packet("execute",2,request_id="1"*32)
    feed(rt,p);feed(rt,p)
    assert len(rt.globals().calls)==3
    assert rt.globals().outputs[2]==rt.globals().outputs[3]

@pytest.mark.parametrize("bad",[
    packet("observe").replace("ui-instance-1","foreign"),
    packet("observe").replace("x4-ui-startmenu-v1","x4-native-v1"),
    packet("observe",extra="x"),packet("rawNewGame"),
    packet("execute",request_id="bad"),"x"*1401,
])
def check_pipe_foreign_malformed_or_unknown_request_never_acts(bad):
    rt=pump_env();rt.globals().pump.start();feed(rt,bad)
    assert len(rt.globals().calls)==0

def check_pipe_changed_raw_under_existing_call_faults():
    rt=pump_env();rt.globals().pump.start()
    feed(rt,packet("observe"))
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    assert rt.globals().pump.status()["status"]=="UNAVAILABLE"
    assert len(rt.globals().calls)==0

@pytest.mark.parametrize("change",[
    "startmenu=false",'binding="changed"',"age=1900","age=-2","age=0/0","readfail=true",
])
def check_pipe_lifecycle_fault_stops_before_dispatch(change):
    rt=pump_env();rt.globals().pump.start();rt.execute(change)
    rt.globals().inputs[1]=packet("prepare",request_id="1"*32,action="start",option="argongamestart")
    rt.globals().tick()
    assert rt.globals().pump.status()["status"]=="UNAVAILABLE"
    assert len(rt.globals().calls)==0 and len(rt.globals().pending)==0

def check_pipe_write_failure_is_not_retry_or_action_success():
    rt=pump_env();rt.globals().pump.start()
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    rt.execute("writefail=true")
    feed(rt,packet("execute",2,request_id="1"*32))
    assert len(rt.globals().calls)==3
    assert rt.globals().pump.status()["status"]=="UNAVAILABLE"
    assert len(rt.globals().pending)==0

def check_pipe_leaving_menu_after_callback_is_uncertain_transport():
    rt=pump_env();rt.globals().pump.start()
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    rt.execute("handler2=handler;cell.handlers.onClick=function() handler2();startmenu=false end")
    # New callback makes the existing preparation stale, so no dispatch occurs.
    feed(rt,packet("execute",2,request_id="1"*32))
    assert len(rt.globals().calls)==0
    assert json.loads(rt.globals().outputs[2])["result"]["status"]=="REFUSED"

def check_pipe_response_overflow_announces_incomplete():
    rt=pump_env('option.name=string.rep("n",4000)');rt.globals().pump.start()
    feed(rt,packet("observe"))
    result=json.loads(rt.globals().outputs[1])["result"]
    assert result["status"]=="UNAVAILABLE" and result["complete"] is False

def check_pipe_pending_reply_requires_status_not_execute_retry():
    rt=pump_env("defer=true");rt.globals().pump.start()
    feed(rt,packet("prepare",request_id="1"*32,action="start",option="argongamestart"))
    p=packet("execute",2,request_id="1"*32);feed(rt,p)
    rt.execute("authcallback(true)")
    feed(rt,p)
    assert json.loads(rt.globals().outputs[3])["result"]["status"]=="AUTHORIZATION_PENDING"
    feed(rt,packet("status",3,request_id="1"*32))
    assert json.loads(rt.globals().outputs[4])["result"]["status"]=="DISPATCHED_UNVERIFIED"

def check_pipe_disposal_and_single_owner():
    rt=pump_env();rt.globals().pump.start()
    rt.execute("assert(not pcall(pumpfactory.new,pd));assert(pump.dispose());other=pumpfactory.new(pd)")
    rt.globals().tick()
    assert len(rt.globals().outputs)==0

def check_pipe_refuses_synchronous_delayed_callback():
    rt=pump_env("sync=true")
    assert rt.globals().pump.start()["status"]=="UNAVAILABLE"

@pytest.mark.parametrize("failure",["return false","error('failed retirement')"])
def check_pipe_failed_retirement_cannot_be_retried_into_replacement(failure):
    rt=pump_env();rt.globals().pump.start()
    rt.execute("pd.adapter.dispose=function() "+failure+" end;readfail=true;tick()")
    rt.execute("pd.adapter.dispose=function() return true end;assert(pump.dispose()==false);assert(not pcall(pumpfactory.new,pd))")

def check_pipe_idle_read_does_not_use_shared_scheduler_or_mutate_game():
    rt=pump_env();rt.globals().pump.start()
    for _ in range(3):rt.globals().tick()
    assert len(rt.globals().outputs)==0 and len(rt.globals().calls)==0
    assert len(rt.globals().pending)==1

def check_pipe_native_wrapper_uses_only_own_pipe():
    rt=pump_env()
    rt.execute(r'''
assert(pump.dispose())
api=factory.new(deps)
local pipes={Read_Pipe=pd.read_pipe,Write_Pipe=pd.write_pipe}
local helper={addDelayedOneTimeCallbackOnUpdate=pd.schedule}
local globals={getElapsedTime=deps.now}
nativepump=pumpfactory.native(pipes,helper,globals,{IsStartmenu=deps.is_startmenu},pd.codec,api,deps.ui_binding)
assert(nativepump.start().status=="SCHEDULED")
''')
    feed(rt,packet("observe"))
    assert json.loads(rt.globals().outputs[1])["result"]["status"]=="OBSERVED"

def _cases():
    result=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,arg in enumerate(marks[0].args[1]):
                result.append(pytest.param(name,[arg],id=f"{name}-{i}"))
        else:result.append(pytest.param(name,[],id=name))
    return result

@pytest.mark.parametrize("name,args",_cases())
def test_actual_luajit_control(name,args):
    # Caught LuaJIT errors use Windows SEH. A bounded child retains assertions/exit.
    p=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip()=="CONTROL_PASSED",p.stdout+p.stderr

if __name__=="__main__":
    assert sys.argv[1].startswith("check_")
    globals()[sys.argv[1]](*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

"""Synthetic actual-Lua/FFI boundary checks, never engine or economic proof."""
from pathlib import Path
import os
import json
import subprocess
import sys

import pytest

luajit = pytest.importorskip("lupa.luajit21")
MODULE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_menu.lua"
DECL = """
typedef struct { uint32_t red,green,blue,alpha; } Color;
typedef struct {
const char *id,*iconid,*text1,*text2,*text3,*text4,*mouseovertext,*font;
Color overrideColor; bool displayRemoveOption,active,hasOverrideColor;
} DropDownOption3;
"""
SETUP = r"""
ffi=require("ffi")
calls, nativecalls, strings = {}, {}, {}
function u(s)
    local x=ffi.new("uint64_t",0)
    for c in s:gmatch(".") do x=x*10+tonumber(c) end
    return x
end
station=u("9007199254740993")
subject=u("9007199254740994")
menu={name="ShipConfigurationMenu",shown=true,minimized=false,mode="purchase",
    param={0,0,station,"purchase",{}},container=station,isReadOnly=false,
    macro="ship_test_macro",object=0,total=100,crewtotal=10,shoppinglisttotal=110,
    shoppinglistrefund=0,shoppinglist={{amount=1,macro="ship_test_macro",price=100,crewprice=10}},
    upgradeplan={engine={{macro="engine_macro"}}},crew={},
    errors={},criticalerrors={},warnings={},missingResources={},
    validLicence=true,validLoadoutPossible=true,captainSelected=true,
    frames={[2]=77},buttonScriptMap={},dropdownScriptMap={}}
function action(name,...)
    calls[#calls+1]={name,...}
    if recurse then again=api.execute(ticket) end
    if callbackfail then error("callback failed after possible effects") end
end
menu.dropdownShipClass=function(widget,option) action("class",widget,option) end
menu.dropdownShip=function(widget,option) action("ship",widget,option) end
menu.dropdownLoadout=function(widget,option) action("loadout",widget,option) end
menu.buttonConfirm=function() action("confirm") end
captured_add=function() action("add",true,false) end
frame={id=77,properties={layer=2,panelMode=false},content={}}
tbl={type="table",id=88,rows={}}
row={}
tbl.rows[1]=row
frame.content[1]=tbl
menu.infoFrame=frame
local names={"class","ship","loadout","add","confirm"}
local ids={"shipconfig_classoptions","shipconfig_shipoptions",nil,
    "shipconfig_addtoshoppinglist","shipconfig_confirm"}
local handlers={menu.dropdownShipClass,menu.dropdownShip,menu.dropdownLoadout,
    captured_add,menu.buttonConfirm}
local opts={"ship_l","ship_test_macro","default"}
cells,nativeoptions,enabled={},{},{}
for i,name in ipairs(names) do
    local isdrop=i<4
    local cell={type=isdrop and "dropdown" or "button",id=100+i,
        properties={active=true,helpOverlayID=ids[i],mouseOverText="reason"},
        handlers={}}
    if isdrop then
        cell.properties.options={{id=opts[i],text="\27[icon] "..name}}
        if name=="loadout" then cell.properties.options[1].active=true end
        cell.handlers.onDropDownConfirmed=handlers[i]
        nativeoptions[cell.id]={{id=opts[i],text="\27[icon] "..name,active=true}}
    else cell.properties.uiTriggerID=ids[i];cell.handlers.onClick=handlers[i] end
    enabled[cell.id]=true
    cells[name]=cell;row[i]=cell
    local nativehandler=handlers[i]
    local wrapper=function(...)
        calls[#calls+1]={"ui_trigger",name}
        return nativehandler(...)
    end
    local entry={layer=2,tableobj=88,row=1,col=i,button=cell.id,
        type=isdrop and "onDropDownConfirmed" or "onClick",script=wrapper}
    local maps=isdrop and menu.dropdownScriptMap or menu.buttonScriptMap
    maps[#maps+1]=entry
end
menus={menu}
epoch,age="epoch-1",10
C={}
C.GetNumDropDownOptions=function(widget)
    nativecalls[#nativecalls+1]="count"
    return badcount or #nativeoptions[widget]
end
local function cs(s)
    if nulltext then return nil end
    local b=ffi.new("char[?]",#s+1,s);strings[#strings+1]=b;return b
end
C.GetDropDownOptions3=function(buf,n,widget)
    nativecalls[#nativecalls+1]="rows"
    for i=0,n-1 do
        local o=nativeoptions[widget][i+1]
        for _,k in ipairs({"iconid","text2","text3","text4","mouseovertext","font"}) do buf[i][k]=cs("") end
        buf[i].id=cs(o.id);buf[i].text1=cs(o.text);buf[i].active=o.active
    end
    return shortrows and n-1 or n
end
C.IsDropDownActive=function(widget) return enabled[widget] end
C.SetDropDownCurOption=function(widget,option) calls[#calls+1]={"set_option",widget,option} end
g={
    IsValidWidgetElement=function(widget) return not invalid end,
    IsButtonActive=function(widget) return enabled[widget] end
}
deps={
    menus=function() return menus end,
    cell_id=function(t,r,c) return wrongcell and 999 or row[c].id end,
    epoch=function() return epoch end,now=function() return age end,
    resolve_issued=function(token,kind)
        if revoked then return nil end
        if token=="yard" and kind=="station" then return station end
        if token=="ship" and kind=="ship" then return subject end
    end,
    convert_id=function(v) return v end,
    convert_params=function(p) return p end,
    open_menu=function(name,p) calls[#calls+1]={"open",name,p};if openfail then error("open failed") end end,
}
expected={mode="purchase",container="9007199254740993"}
"""


def build(code=""):
    rt = luajit.LuaRuntime(unpack_returned_tuples=True)
    rt.execute('ffi=require("ffi")')
    rt.globals().decl = DECL
    rt.execute("ffi.cdef(decl)")
    rt.execute(SETUP)
    rt.globals().mod = rt.execute(MODULE.read_text(encoding="utf-8"))
    rt.execute("deps.widget_state,deps.set_dropdown_current=mod.native_reader(ffi,C,g);api=mod.new(deps)")
    rt.execute(code)
    return rt


def prepare(rt, action="ship", option="ship_test_macro"):
    return rt.globals().api.prepare(action, option, rt.globals().expected)


def check_actual_current_native_option_read_and_wrapper_dispatch():
    rt = build()
    obs = rt.globals().api.observe()
    assert obs["status"] == "OBSERVED" and obs["atomic"] is False
    assert obs["widgets"]["ship"]["model"]["options"][1]["active"] is True
    assert obs["entry_route_verified"] is False
    p = prepare(rt)
    assert p["status"] == "PREPARED"
    r = rt.globals().api.execute(p["ticket"])
    assert r["status"] == "DISPATCHED_UNVERIFIED" and not r["economic_outcome_verified"]
    calls = list(rt.globals().calls.values())
    assert [v[1] for v in calls] == ["set_option", "ui_trigger", "ship"]
    assert calls[-1][2] == 102 and calls[-1][3] == "ship_test_macro"


@pytest.mark.parametrize("action,option", [
    ("class", "ship_l"), ("loadout", "default"), ("add", None), ("confirm", None),
])
def check_each_allowlisted_built_callback(action, option):
    rt = build()
    p = prepare(rt, action, option)
    assert p["status"] == "PREPARED"
    assert rt.globals().api.execute(p["ticket"])["status"] == "DISPATCHED_UNVERIFIED"
    assert list(rt.globals().calls.values())[-1][1] == action


@pytest.mark.parametrize("code", [
    "menu.shown=false",
    "menu.minimized=true",
    "menu.mode='comparison'",
    "menu.isReadOnly=true",
    "menu.param[3]=subject",
    "menu.frames[2]=90",
    "frame.id=0",
    "frame.id=1.5;menu.frames[2]=1.5",
    "cells.ship.id=2147483648",
    "frame.properties.panelMode=true",
    "menu.contextMode={mode='userquestion'}",
    "menus[2]={name='Other',shown=true}",
    "wrongcell=true",
    "invalid=true",
    "enabled[102]=false;cells.ship.properties.active=false",
    "enabled[102]=nil",
    "cells.ship.properties.active=function() return true end",
    "nativeoptions[102][1].active=false",
    "nativeoptions[102][1].id='unoffered'",
    "nativeoptions[102][2]=nativeoptions[102][1]",
    "cells.ship.handlers.onDropDownConfirmed=function() end",
    "menu.dropdownScriptMap[2].script=nil",
    "menu.dropdownScriptMap[4]=menu.dropdownScriptMap[2]",
    "badcount=2049",
    "badcount=-1",
    "badcount=0/0",
    "badcount=1.5",
    "shortrows=true",
    "nulltext=true",
    "menu.upgradeplan.self=menu.upgradeplan",
    "menu.total=math.huge",
    "menu.total=9007199254740992",
    "menu.total=string.rep('x',4097)",
    "nativeoptions[102][1].text=string.rep('x',4097)",
    "row[10]=cells.ship",
])
def check_prepare_refuses_without_actions(code):
    rt = build(code)
    assert prepare(rt)["status"] == "REFUSED"
    assert len(rt.globals().calls) == 0


@pytest.mark.parametrize("code", [
    "epoch='epoch-2'",
    "age=41",
    "age=9",
    "menu.container=subject",
    "menu.infoFrame={id=77,properties=frame.properties,content=frame.content}",
    "frame.id=78;menu.frames[2]=78",
    "menu.total=101",
    "menu.settings={blacklists={travel=2}}",
    "menu.selectedPaintMod={ware='different_paint'}",
    "menu.customshipname='changed'",
    "menu.loadoutName='changed'",
    "menu.duration=123",
    "menu.objectgroup={idx=1,states={}}",
    "menu.shoppinglist[1].price=101",
    "nativeoptions[102][1].text='changed'",
    "menu.dropdownScriptMap[2].script=function() end",
    "enabled[102]=false;cells.ship.properties.active=false",
])
def check_stale_preparation_consumed_no_action(code):
    rt = build()
    p = prepare(rt)
    assert p["status"] == "PREPARED"
    rt.execute(code)
    assert rt.globals().api.execute(p["ticket"])["status"] == "REFUSED"
    assert rt.globals().api.execute(p["ticket"])["status"] == "REFUSED"
    assert len(rt.globals().calls) == 0


def check_replay_reentrancy_and_after_callback_uncertainty():
    rt = build("recurse=true;callbackfail=true")
    p = prepare(rt, "confirm", None)
    rt.globals().ticket = p["ticket"]
    r = rt.globals().api.execute(p["ticket"])
    assert r["status"] == "UNCERTAIN" and r["action_attempted"]
    assert rt.globals().again["status"] == "UNCERTAIN"
    assert rt.globals().api.execute(p["ticket"])["status"] == "UNCERTAIN"
    assert len(rt.globals().calls) == 2


@pytest.mark.parametrize("code", [
    "menu.shoppinglist[2]=menu.shoppinglist[1]",
    "menu.shoppinglist[1].amount=2",
    "menu.shoppinglist[1].objectgroup={}",
    "menu.editingshoppinglist=1",
])
def check_confirm_does_not_commandeer_cart(code):
    rt = build(code)
    assert prepare(rt, "confirm", None)["status"] == "REFUSED"


def check_no_unoffered_or_generic_callback():
    rt = build()
    assert prepare(rt, "ship", "not_offered")["status"] == "REFUSED"
    assert prepare(rt, "raw", None)["status"] == "REFUSED"
    assert rt.globals().api.execute("invented")["status"] == "REFUSED"


@pytest.mark.parametrize("mode,ship", [("purchase", None), ("upgrade", "ship")])
def check_open_through_native_entry_params(mode, ship):
    rt = build("menu.shown=false")
    p = rt.globals().api.prepare_open(mode, "yard", ship)
    assert p["status"] == "PREPARED"
    assert rt.globals().api.execute(p["ticket"])["status"] == "DISPATCHED_UNVERIFIED"
    c = rt.globals().calls[1]
    assert c[1] == "open" and c[2] == "ShipConfigurationMenu"
    assert str(c[3][3])  # Cdata stays exact; no numeric coercion.
    assert c[3][4] == mode and len(c[3][5]) == (1 if ship else 0)
    assert c[3][6] is None  # No fabricated docked/immediate privilege.


def check_open_refuses_existing_menu_and_revoked_identity():
    rt = build()
    assert rt.globals().api.prepare_open("purchase", "yard", None)["status"] == "REFUSED"
    rt.execute("menu.shown=false")
    p = rt.globals().api.prepare_open("purchase", "yard", None)
    rt.execute("revoked=true")
    assert rt.globals().api.execute(p["ticket"])["status"] == "REFUSED"
    assert len(rt.globals().calls) == 0


def check_open_failure_is_uncertain_and_never_retried():
    rt = build("menu.shown=false;openfail=true")
    p = rt.globals().api.prepare_open("purchase", "yard", None)
    assert rt.globals().api.execute(p["ticket"])["status"] == "UNCERTAIN"
    assert rt.globals().api.execute(p["ticket"])["status"] == "UNCERTAIN"
    assert len(rt.globals().calls) == 1


def check_abi_wrong_existing_field_type_refuses_before_native_calls():
    # Isolated child keeps Windows LuaJIT protected-exception diagnostics out of
    # pytest's own fault handler; child assertions and exit are mandatory.
    child = r"""
from lupa.luajit21 import LuaRuntime
from pathlib import Path
import sys
r=LuaRuntime(unpack_returned_tuples=True)
r.execute('ffi=require("ffi")')
decl=sys.argv[2].replace("bool displayRemoveOption,active,hasOverrideColor;",
                       "uint8_t displayRemoveOption,active,hasOverrideColor;")
r.globals().decl=decl
r.execute("ffi.cdef(decl)")
r.globals().m=r.execute(Path(sys.argv[1]).read_text())
r.execute("calls=0; C=setmetatable({},{__index=function() calls=calls+1;error('native called') end})")
ok,error=r.eval("function() return pcall(m.native_reader,ffi,C,{}) end")()
assert not ok and "bool field type" in str(error)
assert r.globals().calls==0
print("ABI_REFUSAL_VERIFIED")
"""
    result = subprocess.run([sys.executable, "-B", "-c", child, str(MODULE), DECL],
                            capture_output=True, text=True, timeout=20,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ABI_REFUSAL_VERIFIED" in result.stdout

VIEW_SETUP = r"""
menu.shown=false
owner=true;ownership_reads=0
deps.is_player_owned=function(v) ownership_reads=ownership_reads+1;assert(v==subject);return owner end
deps.convert_id=function(v) if v==tostring(subject) then return subject end;return v end
function show_view()
    menu.shown=true;menu.mode='upgrade';menu.object=subject;menu.container=nil;menu.isReadOnly=true
    menu.param={0,0,nil,'upgrade',{tostring(subject)}}
    menu.mainFrame={id=66,properties={layer=1,standardButtons={close=true}}}
    menu.frames[1]=66
    menu.onCloseElement=function()end
    menu.closeElement=function(widget,reason)
        assert(widget==66 and reason=='close');calls[#calls+1]={'close'};menu.shown=false
    end
end
"""

REPAIR_SETUP = r"""
menu.mode='upgrade';menu.param[4]='upgrade';menu.param[5]={subject};menu.object=subject
menu.damagedcomponents={'engine_01','shield_01'};menu.repairplan={}
menu.repairslots={{{1,'ship_test_macro',1,subject}}}
expected={mode='upgrade',container='9007199254740993',ship='9007199254740994'}
local function repair_cell(name,marker,handler,help)
    local i=#row+1;local widget=100+i
    local p={active=true};p[help and 'helpOverlayID' or 'uiTriggerID']=marker
    local cell={type='button',id=widget,properties=p,handlers={onClick=handler}}
    row[i]=cell;cells[name]=cell;enabled[widget]=true
    menu.buttonScriptMap[#menu.buttonScriptMap+1]={layer=2,tableobj=88,row=1,col=i,
        button=widget,type='onClick',script=function()
            calls[#calls+1]={'ui_trigger',name};handler()
        end}
end
repair_cell('repair_tab','shipconfig_leftbar_repair',function()
    action('repair_tab');menu.upgradetypeMode='repair'
end,true)
repair_cell('repair_select','shipconfig_repair',function()
    action('repair_select');menu.repairplan[tostring(subject)]={engine_01=true,shield_01=true}
    if change_loadout then menu.upgradeplan.engine[1].macro='changed' end
end,false)
"""


def check_view_native_readonly_shape_and_observation():
    rt = build(VIEW_SETUP)
    assert rt.eval('ownership_reads') == 0
    p = rt.globals().api.prepare_open('view', None, 'ship')
    assert p.status == 'PREPARED' and len(rt.globals().calls) == 0
    assert rt.globals().api.execute(p.ticket).status == 'DISPATCHED_UNVERIFIED'
    rt.execute("assert(calls[1][3][3]==nil and calls[1][3][4]=='upgrade' and calls[1][3][5][1]==tostring(subject));show_view()")
    obs = rt.globals().api.observe()
    assert obs.status == 'OBSERVED' and obs.read_only is True and obs.container == '0'
    assert obs.economic_outcome_verified is False
    assert prepare(rt, 'add', None).status == 'REFUSED'


@pytest.mark.parametrize('code', [
    'deps.is_player_owned=nil', 'owner=false', 'owner=nil', "owner='true'",
    'revoked=true', "menus={}", 'menu.shown=true',
])
def check_view_refuses_missing_ownership_identity_or_registration(code):
    rt = build(VIEW_SETUP + '\n' + code)
    assert rt.globals().api.prepare_open('view', None, 'ship').status == 'REFUSED'
    assert len(rt.globals().calls) == 0


@pytest.mark.parametrize('code', [
    'owner=false', 'revoked=true', "epoch='changed'", 'age=41',
    "deps.convert_params=function(p) epoch='changed';return p end",
    "deps.is_player_owned=function() age=41;return true end",
])
def check_view_execute_rechecks_before_once_only_dispatch(code):
    rt = build(VIEW_SETUP)
    p = rt.globals().api.prepare_open('view', None, 'ship')
    assert p.status == 'PREPARED'
    rt.execute(code)
    assert rt.globals().api.execute(p.ticket).status == 'REFUSED'
    assert rt.globals().api.execute(p.ticket).status == 'REFUSED'
    assert len(rt.globals().calls) == 0


def opened_view():
    rt = build(VIEW_SETUP)
    p = rt.globals().api.prepare_open('view', None, 'ship')
    assert rt.globals().api.execute(p.ticket).status == 'DISPATCHED_UNVERIFIED'
    rt.globals().opening = p.ticket
    rt.execute('show_view();close_expected={opening=opening}')
    return rt


@pytest.mark.parametrize('closed_state', ['nil', 'false'])
def check_owned_native_close_requires_later_visibility_readback(closed_state):
    rt = opened_view()
    rt.execute("menu.closeElement=function(widget,reason) assert(widget==66 and reason=='close');calls[#calls+1]={'close'};menu.shown="+closed_state+" end")
    p = rt.globals().api.prepare('close', None, rt.globals().close_expected)
    assert p.status == 'PREPARED'
    assert rt.globals().api.observe(p.ticket).status == 'REFUSED'
    r = rt.globals().api.execute(p.ticket)
    assert r.status == 'DISPATCHED_UNVERIFIED' and not r.economic_outcome_verified
    obs = rt.globals().api.observe(p.ticket)
    assert obs.status == 'MENU_CLOSED_OBSERVED' and not obs.rendered_pixels_verified
    assert obs.shown is False and obs.shown_normalized is True
    assert obs.native_shown_state == ('UNSET' if closed_state == 'nil' else 'FALSE')
    rt.globals().api.execute(p.ticket)
    assert len(rt.globals().calls) == 2


@pytest.mark.parametrize('closed_state', ['true', '0', "'false'", '{}'])
def check_close_readback_refuses_shown_or_malformed_flag(closed_state):
    rt = opened_view()
    rt.execute("menu.closeElement=function(widget,reason) calls[#calls+1]={'close'};menu.shown="+closed_state+" end")
    p = rt.globals().api.prepare('close', None, rt.globals().close_expected)
    assert p.status == 'PREPARED'
    assert rt.globals().api.execute(p.ticket).status == 'DISPATCHED_UNVERIFIED'
    assert rt.globals().api.observe(p.ticket).status == 'REFUSED'
    rt.globals().api.execute(p.ticket)
    assert len(rt.globals().calls) == 2


def check_close_final_lifecycle_callback_cannot_reopen_observed_menu():
    rt = opened_view()
    p = rt.globals().api.prepare('close', None, rt.globals().close_expected)
    assert p.status == 'PREPARED'
    assert rt.globals().api.execute(p.ticket).status == 'DISPATCHED_UNVERIFIED'
    rt.execute('''
        menu.shown=nil;closing_reads=0
        deps.epoch=function()
            closing_reads=closing_reads+1
            if closing_reads==2 then menu.shown=true end
            return epoch
        end
    ''')
    assert rt.globals().api.observe(p.ticket).status == 'REFUSED'
    assert rt.eval('closing_reads') == 2
    rt.globals().api.execute(p.ticket)
    assert len(rt.globals().calls) == 2


@pytest.mark.parametrize('code', [
    "close_expected.opening='foreign'", 'menu.mainFrame.properties.standardButtons.close=false',
    'menu.closeElement=nil', 'menu.onCloseElement=nil', "menu.contextMode={mode='confirm'}",
    'menu.object=station', 'menu.frames[1]=67',
])
def check_close_prepare_requires_owned_exact_offered_view(code):
    rt = opened_view(); rt.execute(code)
    assert rt.globals().api.prepare('close', None, rt.globals().close_expected).status == 'REFUSED'
    assert len(rt.globals().calls) == 1


@pytest.mark.parametrize('code', [
    'menu.closeElement=function()end', 'menu.onCloseElement=function()end',
    'menu.mainFrame.properties.standardButtons.close=false', 'menu.total=200',
    "epoch='other'", 'age=41', 'menu.shown=false',
])
def check_close_dispatch_refuses_changed_owned_view(code):
    rt = opened_view()
    p = rt.globals().api.prepare('close', None, rt.globals().close_expected)
    assert p.status == 'PREPARED'; rt.execute(code)
    assert rt.globals().api.execute(p.ticket).status == 'REFUSED'
    assert len(rt.globals().calls) == 1


def check_repair_native_buttons_preserve_loadout_without_completion_claim():
    rt = build(REPAIR_SETUP)
    for name in ('repair_tab', 'repair_select'):
        p = prepare(rt, name, None)
        assert p.status == 'PREPARED'
        r = rt.globals().api.execute(p.ticket)
        assert r.status == 'DISPATCHED_UNVERIFIED' and not r.economic_outcome_verified
        rt.globals().api.execute(p.ticket)
    rt.execute("assert(menu.repairplan[tostring(subject)].engine_01 and menu.upgradeplan.engine[1].macro=='engine_macro')")
    assert [x[1] for x in rt.globals().calls.values()] == ['ui_trigger', 'repair_tab', 'ui_trigger', 'repair_select']
    assert prepare(rt, 'repair_select', None).status == 'REFUSED'


@pytest.mark.parametrize('code', [
    'menu.objectgroup={subject}', 'expected.ship=expected.container', 'menu.damagedcomponents={}',
    "menu.upgradetypeMode=nil", 'menu.repairslots[1][1][4]=station',
    'menu.repairslots[2]=menu.repairslots[1]', "menu.repairplan.x={engine_01=true}",
    'enabled[107]=false;cells.repair_select.properties.active=false',
])
def check_repair_requires_exact_single_damaged_ship_and_empty_plan(code):
    rt = build(REPAIR_SETUP + "\nmenu.upgradetypeMode='repair';" + code)
    assert prepare(rt, 'repair_select', None).status == 'REFUSED'
    assert len(rt.globals().calls) == 0


def check_repair_changed_loadout_is_uncertain_and_never_retried():
    rt = build(REPAIR_SETUP + "\nmenu.upgradetypeMode='repair';change_loadout=true")
    p = prepare(rt, 'repair_select', None)
    assert p.status == 'PREPARED'
    assert rt.globals().api.execute(p.ticket).status == 'UNCERTAIN'
    assert rt.globals().api.execute(p.ticket).status == 'UNCERTAIN'
    assert len(rt.globals().calls) == 2


@pytest.mark.parametrize('code', ['menu.repairplan.x={a=true}', 'menu.damagedcomponents[1]=nil',
                                    'menu.upgradeplan.engine[1].macro="other"'])
def check_repair_model_drift_refuses_before_native_callback(code):
    rt = build(REPAIR_SETUP + "\nmenu.upgradetypeMode='repair'")
    p = prepare(rt, 'repair_select', None); assert p.status == 'PREPARED'
    rt.execute(code)
    assert rt.globals().api.execute(p.ticket).status == 'REFUSED'
    assert len(rt.globals().calls) == 0


def check_actual_menu_pages_preserve_five_widget_contract_and_repair_supplement():
    rt = build(REPAIR_SETUP)
    rt.globals().pages_factory = rt.execute(MODULE.with_name('live_menupages.lua').read_text(encoding='utf-8'))
    rt.execute('''
        local observed=api.observe();local n=0;for k in pairs(observed.widgets)do n=n+1 end
        assert(n==5 and observed.repair_widgets.repair_tab.status=='OBSERVED')
        pages=pages_factory{observe=api.observe}
        header=pages.capture(string.rep('a',32));assert(header.status=='CAPTURED')
        for i=0,header.pages-1 do assert(pages.page(header.capture,i).status=='PAGE')end
    ''')


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
    # Protected LuaJIT errors use Windows SEH. Keep real assertions/exit/timeout
    # in children rather than mislabeling expected pcall refusals as fatal dumps.
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

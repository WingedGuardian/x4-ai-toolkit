"""Actual Lua actor-context adapter; synthetic native frame and right-click callback."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/'mods/x4_toolkit_helper/ui/live_actorcontext.lua'


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        ffi=require('ffi');writes=0;reads=0;time=10;issued=42ULL;mousex=1;mousey=2
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        raw={'info_shiptrader',43ULL,42};row={rowdata=raw,index=1};tbl={type='table',id=20,rows={row}}
        frame={id=10,properties={layer=5},content={tbl}}
        function handler(t,i,x,y) assert(t==20 and i==1 and x==nil and y==nil);writes=writes+1;if callback then callback() end end
        m={name='MapMenu',shown=true,minimized=false,searchTableMode='info',infoMode={right='objectinfo'},
            infoFrame2=frame,frames={[5]=10},infoTableRight=20,infoTable=21,rowDataMap={[20]={[1]=raw}},
            onTableRightMouseClick=handler,prepareInfoContext=function() end}
        menus={m}
        d={menus=function() reads=reads+1;return menus end,public_current=function() return stamp end,clock=function() return time end,
            resolve_issued=function(token,kind) assert(token=='context' and kind=='object');return issued end,
            component_id=function(v) return v end,mouse_position=function() return mousex,mousey end,
            table_valid=function(v) return v==20 end}
        function prepare() p=api.prepare('context','info_shiptrader','43');return p end
    """)
    r.globals().factory=r.execute(SOURCE.read_text());r.execute('api=factory(d)');return r


def test_constructor_is_pure_and_actual_row_dispatch_remains_unverified():
    r=runtime();assert r.eval('reads+writes')==0
    assert r.eval('api.observe().rows[1].actor')=='43'
    assert r.eval('prepare().status')=='PREPARED' and r.eval('writes')==0
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.action_attempted is True
    assert out.context_open_verified is False and out.conversation_open_verified is False
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==1


@pytest.mark.parametrize('change',[
    'm.shown=false','m.minimized=true','m.panelMode=true','m.showMultiverse=true',"m.mode='hire'",
    "m.contextMenuMode='trade'","m.searchTableMode='other'","m.infoMode.right='objectcrew'",
    'm.createInfoFrame2Running=true','frame.properties.layer=2','m.frames[5]=11','m.infoTableRight=21',
    'm.rowDataMap[20][1]={raw[1],raw[2],raw[3]}','row.index=2','m.numFixedRows=1',
    "raw[1]='info_inventory'",'raw[3]=42ULL','raw[2]=44ULL','raw[3]=44','issued=44ULL',
    'mousex=nil','mousey=0/0','time=0/0','stamp=nil','menus[2]=m',
    "menus[2]={name='Other',shown=true}",'m.onTableRightMouseClick=nil',
])
def test_invalid_or_wrong_actor_context_never_invokes_native_handler(change):
    r=runtime();r.execute(change)
    assert r.eval('prepare().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('change',[
    'time=41','time=9',"stamp.generation=string.rep('d',32)",'raw[2]=44ULL','issued=44ULL',
    'm.onTableRightMouseClick=function() writes=writes+1 end','m.prepareInfoContext=function() end',
    'mousex=nil','m.panelMode=true','frame.id=11;m.frames[5]=11',
    'tbl.id=22;m.infoTableRight=22;m.rowDataMap[22]=m.rowDataMap[20];d.table_valid=function() return true end',
])
def test_prepare_to_execute_drift_refuses_and_consumes(change):
    r=runtime();r.execute('prepare();'+change)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r.execute('time=10;api.execute(p.ticket)');assert r.eval('writes')==0


@pytest.mark.parametrize('callback',["error('callback failure')",'stamp=nil','time=9'])
def test_post_write_failure_retains_attempt(callback):
    r=runtime();r.execute('prepare();callback=function() '+callback+' end')
    out=r.eval('api.execute(p.ticket)');assert out.status=='UNCERTAIN' and out.action_attempted is True
    assert r.eval('writes')==1


def test_person_token_not_converted_and_public_record_cannot_rebind_private_actor():
    r=runtime();r.execute("raw[1]='info_crewperson';raw[2]=18446744073709551615ULL;d.component_id=function(v) assert(v==42);return v end")
    out=r.eval("api.prepare('context','info_crewperson','18446744073709551615')")
    assert out.status=='PREPARED' and out.actor.kind=='PERSON'
    r.globals().p=out;r.execute("p.actor.actor='44';raw[2]=44ULL")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED'


def test_retirement_prevents_dispatch():
    r=runtime();r.execute('prepare();api.dispose()')
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('panel',[False,True])
def test_explicit_left_pane_routes_actual_row(panel):
    r=runtime();r.globals().panel=panel
    r.execute("m.infoTableMode='info';m.infoMode.left='objectinfo';m.infoFrame=frame;frame.properties.layer=4;m.frames[4]=10;m.infoTable=20;m.infoTableRight=21;m.panelMode=panel;m.panelState={leftmenu=true}")
    assert r.eval("api.observe('left').side")=='left'
    r.execute("p=api.prepare('context','info_shiptrader','43','left')")
    assert r.eval('p.status')=='PREPARED'
    assert r.eval('api.execute(p.ticket).status')=='DISPATCHED_UNVERIFIED'
    assert r.eval('writes')==1


def test_left_selection_does_not_fall_back_to_right_info_mode():
    r=runtime()
    assert r.eval("api.prepare('context','info_shiptrader','43','left').status")=='REFUSED'
    assert r.eval('writes')==0


def test_panel_hidden_after_preparation_refuses():
    r=runtime();r.execute("m.panelMode=true;m.panelState={rightmenu=true};prepare();m.panelState.rightmenu=false")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0

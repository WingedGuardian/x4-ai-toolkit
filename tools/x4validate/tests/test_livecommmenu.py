"""Actual Lua rendered comm adapter with synthetic native menu/widget state."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_commmenu.lua"


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        ffi=require('ffi');time=10;writes=0;resolves=0;component_converts=0;native_active=true
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        cell={type='button',id=30,colspan=1,properties={active=true},handlers={onClick=function() end}}
        row={cell,rowdata='info_actor_comm',index=1}
        tbl={type='table',id=20,rows={row}}
        frame={id=10,properties={layer=2},content={tbl}}
        function wrapper() writes=writes+1;if callback then callback() end end
        m={name='MapMenu',shown=true,minimized=false,contextMenuMode='info_context',contextFrame=frame,
            contextMenuData={component=42ULL,entity=43ULL,instance=1},frames={[2]=10},
            buttonScriptMap={{layer=2,tableobj=20,row=1,col=1,type='onClick',button=30,script=wrapper}}}
        menus={m};issued=42ULL
        d={menus=function() return menus end,public_current=function() return stamp end,clock=function() return time end,
            resolve_issued=function(token,kind) assert(token=='context' and kind=='object');resolves=resolves+1;return issued end,
            component_id=function(x) component_converts=component_converts+1;return x end,
            cell_id=function(t,ri,ci) assert(t==20 and ri==1 and ci==1);return cell.id end,
            widget_state=function(widget) assert(widget==30);return {active=native_active} end}
        function prepare() p=api.prepare('context','ENTITY','43');return p end
    """)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute('api=factory(d)')
    return r


def test_pure_constructor_read_and_private_prepare_do_not_click():
    r=runtime();assert r.eval('writes+resolves+component_converts')==0
    out=r.eval('api.observe()');assert out.status=='OBSERVED' and out.actor_context.entity=='43'
    assert r.eval('prepare().status')=='PREPARED' and r.eval('writes')==0


def test_dispatch_uses_built_wrapper_once_and_never_claims_opening():
    r=runtime();r.execute('prepare()')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.action_attempted is True
    assert out.conversation_actor_verified is False and out.rendered_dialogue_verified is False
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==1


def test_native_person_identity_is_not_converted_as_component():
    r=runtime();r.execute('m.contextMenuData.entity=nil;m.contextMenuData.person=18446744073709551615ULL')
    out=r.eval("api.prepare('context','PERSON','18446744073709551615')")
    assert out.status=='PREPARED' and out.actor_context.person=='18446744073709551615'
    assert r.eval('component_converts')==1
    assert r.eval("api.execute('1').status")=='DISPATCHED_UNVERIFIED'


@pytest.mark.parametrize('change',[
    "m.shown=false","m.minimized=true","m.contextMenuMode='trade'","m.frames[2]=11",
    "frame.properties.layer=1","frame.id=0","row.rowdata='info_person_fire'","row.index=2",
    "cell.id=0","cell.type='text'","cell.properties.active=function() return true end",
    "cell.properties.active=false;native_active=false","native_active=false","native_active=nil",
    "m.buttonScriptMap={}","m.buttonScriptMap[2]=m.buttonScriptMap[1]","m.buttonScriptMap[1].button=31",
    "tbl.rows[2]=row","m.contextMenuData.component=44ULL","m.contextMenuData.entity=44ULL",
    "m.contextMenuData.person=44ULL","m.contextMenuData.inv_ware='inventory'","issued=44ULL",
    "menus[2]={name='OtherMenu',shown=true}","menus[2]=m","time=0/0","stamp=nil",
])
def test_invalid_built_context_never_clicks(change):
    r=runtime();r.execute(change)
    assert r.eval('prepare().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('change',[
    "time=41","time=9","stamp.generation=string.rep('d',32)","issued=44ULL",
    "m.contextMenuData.entity=44ULL","m.contextMenuData.instance=2",
    "cell.handlers.onClick=function() end","m.buttonScriptMap[1].script=function() writes=writes+1 end",
    "cell.properties.active=false;native_active=false","m.contextFrame={id=10,properties={layer=2},content={tbl}}",
])
def test_revalidation_refuses_drift_and_consumes_ticket(change):
    r=runtime();r.execute('prepare();'+change)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r.execute('time=10;issued=42ULL;api.execute(p.ticket)')
    assert r.eval('writes')==0


@pytest.mark.parametrize('callback',["error('native callback error')","stamp=nil","time=9"])
def test_after_dispatch_failure_retains_attempt_and_never_says_nothing_happened(callback):
    r=runtime();r.execute('prepare();callback=function() '+callback+' end')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True and r.eval('writes')==1


def test_public_preparation_record_cannot_change_private_expected_context():
    r=runtime();r.execute("prepare();p.actor_context.entity='44';m.contextMenuData.entity=44ULL")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def test_retirement_invalidates_tickets():
    r=runtime();r.execute('prepare();api.dispose()')
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0

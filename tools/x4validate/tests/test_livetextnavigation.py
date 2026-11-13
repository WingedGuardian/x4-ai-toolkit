"""Actual Lua navigation with synthetic native menu descriptors; no native calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaError
from lupa.luajit21 import LuaRuntime
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_textnavigation.lua"

def make(domain="encyclopedia",extra=""):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        ffi=require('ffi');age=10;calls=0;opens=0;reads=0;encodes=0
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        function cell(id,kind,value)return {id=id,type=kind,colspan=1,properties={text=value,icon=value,active=true},handlers={}}end
        function wrapped() calls=calls+1;if throwing then error('uncertain dispatch')end end
        rd={'Wares','wares',{id='energycells'}}
        row={index=2,rowdata=rd};for i=1,6 do row[i]=cell(30+i,'text','name'..i)end
        first={index=1,rowdata=false};for i=1,6 do first[i]=cell(40+i,'text','header')end
        tbl={id=20,type='table',numcolumns=6,rows={first,row}}
        frame={id=10,properties={layer=1},content={tbl}}
        m={name='EncyclopediaMenu',shown=true,minimized=false,encyclopediaMode='encyclopedia',
            frame=frame,frames={[1]=10},table_index=20,rowDataMap={[20]={[1]=false,[2]=rd}},
            rowChanged=function(t,n)assert(t==20 and n==2);wrapped()end,onRowChanged=wrapped}
        menus={m}
        d={menus=function()reads=reads+1;if menu_hook then menu_hook()end;return menus end,
            current=function()return stamp end,now=function()return age end,
            encode=function(v)encodes=encodes+1;if encode_hook then encode_hook()end;return string.rep('x',size or 20)end,
            resolve_issued=function(s,k)assert(s=='issued' and k=='object');return ffi.new('uint64_t',123)end,
            component_id=function(v)return v end,
            entry_for_component=function(s)return {component=ffi.new('uint64_t',123),mode='Ships',library='shiptypes_l',id=macro or 'ship_macro'}end,
            unlocked=function()return unlocked~=false end,
            open_menu=function(name,p)opens=opens+1;opened=name;params=p;if throwing then error('native throw')end end,
            convert_parameters=function(p)if convert_hook then convert_hook()end;return p end,
            valid_widget=function(id)return id~=invalid end,
            cell_id=function(t,ri,ci)if cell_hook then cell_hook()end;return tbl.rows[ri][ci].id end,
            widget_state=function()return {active=active~=false}end}
    """)
    if domain=="property":
        r.execute("""
            rd={'property',ffi.new('uint64_t',123),nil,0};row.rowdata=rd;m.rowDataMap[20][2]=rd
            m.name='MapMenu';m.infoFrame=frame;m.infoTable=20;m.infoTableMode='propertyowned';m.panelState={leftmenu=true,leftbar=true}
            frame.properties.layer=4;m.frames={[4]=10};tbl.numcolumns=10
            for i=7,10 do row[i]=cell(30+i,'icon','ship');first[i]=cell(40+i,'text','header')end
        """)
    if domain=="sidebar":
        r.execute("""
            m.name='MapMenu';m.mainFrame=frame;m.sideBar=20;m.panelMode=true;m.infoTableMode='objectlist'
            m.panelState={leftbar=true,leftmenu=true};frame.properties.layer=6;m.frames={[6]=10}
            tbl.numcolumns=1;row.rowdata='propertyowned';m.rowDataMap[20][2]='propertyowned'
            for i=2,6 do row[i]=nil;first[i]=nil end
            row[1].type='button';row[1].handlers.onClick=wrapped
            m.buttonScriptMap={{layer=6,tableobj=20,row=2,col=1,type='onClick',button=31,script=wrapped}}
        """)
    if extra:r.execute(extra)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)")
    return r

def prepare(r,action,arg=None):
    out=r.globals().api.prepare(action,arg)
    assert out.status=="PREPARED",out.reason
    return out.ticket

def test_lazy_constructor():
    r=make();assert r.eval("reads+opens+calls+encodes")==0

@pytest.mark.parametrize("action,name", [("root_open","EncyclopediaMenu"),("map_open","MapMenu"),("component_open","EncyclopediaMenu")])
def test_fixed_open_routes(action,name):
    r=make(extra=f"m.shown=false;m.name='{name}'")
    ticket=prepare(r,action,"issued" if action=="component_open" else None)
    out=r.globals().api.execute(ticket)
    assert out.status=="DISPATCHED_UNVERIFIED" and out.selection_verified is False
    assert r.eval("opens")==1 and r.eval("opened")==name
    if action=="component_open":assert r.eval("params[5]")=="ship_macro"
    r.globals().api.execute(ticket);assert r.eval("opens")==1

def test_exact_native_row_issued_and_selected_once():
    r=make();out=r.globals().api.observe("encyclopedia",2)
    assert out.rows[1].entry=="energycells" and out.full_catalogue is False
    ticket=prepare(r,"select",out.rows[1].token)
    assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"
    r.globals().api.execute(ticket);assert r.eval("calls")==1

@pytest.mark.parametrize("change",[
    "rd[3].id='other'","row[2].properties.text='other'","m.frames[1]=99","frame.id=99",
    "tbl.id=99","m.rowDataMap[20][2]={}","row[2].id=99;invalid=99",
    "m.rowChanged=function()end","m.onRowChanged=function()end","age=26",
    "stamp.generation=string.rep('d',32)","m.refresh=true","m.minimized=true",
])
def test_observed_or_prepared_drift_refuses_without_dispatch(change):
    r=make();out=r.globals().api.observe("encyclopedia",2);ticket=prepare(r,"select",out.rows[1].token)
    r.execute(change)
    assert r.globals().api.execute(ticket).status=="REFUSED"
    assert r.eval("calls+opens")==0

def test_superseded_window_and_unissued_names_refuse():
    r=make();out=r.globals().api.observe("encyclopedia",2)
    r.globals().api.observe("encyclopedia",2)
    assert r.globals().api.prepare("select",out.rows[1].token).status=="REFUSED"
    assert r.globals().api.prepare("select","energycells").status=="REFUSED"

def test_dynamic_descriptor_unknown_no_callback():
    r=make(extra="row[2].properties.text=function()error('must not execute')end")
    out=r.globals().api.observe("encyclopedia",2)
    assert out.status=="OBSERVED" and out.rows[1].cells[2].text.status=="UNKNOWN"

def test_property_rows_have_exact_component_and_static_icons():
    r=make("property");out=r.globals().api.observe("property",2)
    assert out.rows[1].component=="123" and out.rows[1].cells[7].icon.value=="ship"
    assert out.rendered_pixels_verified is False
    assert r.globals().api.prepare("select",out.rows[1].token).status=="REFUSED"

def test_panel_sidebar_uses_native_row_wrapper():
    r=make("sidebar");ticket=prepare(r,"property_open")
    assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"
    assert r.eval("calls")==1

def test_nonpanel_sidebar_uses_registered_button():
    r=make("sidebar","m.panelMode=false")
    ticket=prepare(r,"property_open");assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"
    assert r.eval("calls")==1

def test_hidden_property_panel_uses_registered_button():
    r=make("sidebar","m.infoTableMode='propertyowned';m.panelState.leftmenu=false")
    ticket=prepare(r,"property_open");assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"

def test_postconversion_expiry_refuses():
    r=make(extra="m.shown=false;convert_hook=function()age=26 end")
    ticket=prepare(r,"root_open");assert r.globals().api.execute(ticket).status=="REFUSED"
    assert r.eval("opens")==0

def test_component_rebinding_refuses():
    r=make(extra="m.shown=false")
    ticket=prepare(r,"component_open","issued");r.execute("macro='changed'")
    assert r.globals().api.execute(ticket).status=="REFUSED" and r.eval("opens")==0

def test_encoder_drift_does_not_publish_tokens():
    r=make(extra="encode_hook=function()stamp.generation=string.rep('d',32)end")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"
    assert r.globals().api.prepare("select","1:2").status=="REFUSED"

def test_unknown_after_attempt_no_retry():
    r=make(extra="m.shown=false;throwing=true")
    ticket=prepare(r,"root_open");out=r.globals().api.execute(ticket)
    assert out.status=="UNCERTAIN" and out.action_attempted is True
    r.globals().api.execute(ticket);assert r.eval("opens")==1

def test_disposal_and_bound_drift_are_sticky():
    r=make();r.globals().api.observe("encyclopedia",2);r.execute("stamp=nil")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"
    r.execute("stamp={provider='x4-live-session-v1'}")
    assert r.globals().api.capacity().retired is True
    assert r.globals().api.dispose() is True

@pytest.mark.parametrize("bad",["age=0/0","age=9"])
def test_clock_loss_after_binding_is_sticky(bad):
    r=make();r.globals().api.observe("encyclopedia",2);r.execute(bad)
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"
    r.execute("age=10")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"

def test_captured_current_exception_after_binding_is_sticky():
    r=make(extra="d.current=function()if lost then error('lost')end;return stamp end")
    r.globals().api.observe("encyclopedia",2);r.execute("lost=true")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"
    r.execute("lost=false")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"

def test_capacity_no_eviction():
    r=make(extra="m.shown=false;d.capacity_resolver=function()return {name='campaign_v1',native_history=2}end;d.capacity_profile='campaign_v1'")
    prepare(r,"root_open");prepare(r,"root_open")
    assert r.globals().api.prepare("root_open").status=="REFUSED"
    assert r.globals().api.capacity().history_used==2

@pytest.mark.parametrize("extra",["d.capacity_profile=false","d.capacity_profile='campaign_v1'"])
def test_untrusted_capacity_refuses_before_native(extra):
    with pytest.raises(LuaError,match='unknown capacity profile|campaign resolver required'):make(extra=extra)

def test_exact_native_close_handler_once():
    r=make(extra="m.onCloseElement=function(reason)assert(reason=='close');wrapped()end")
    ticket=prepare(r,"close","encyclopedia")
    assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"
    r.globals().api.execute(ticket);assert r.eval("calls")==1

def test_read_final_publication_rejects_same_world_view_drift():
    r=make(extra="encode_hook=function()row[2].properties.text='changed' end")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"
    assert r.globals().api.prepare("select","1:2").status=="REFUSED"

def test_close_same_menu_new_frame_refuses():
    r=make(extra="m.onCloseElement=wrapped")
    ticket=prepare(r,"close","encyclopedia");r.execute("frame.id=11;m.frames[1]=11")
    assert r.globals().api.execute(ticket).status=="REFUSED" and r.eval("calls")==0
def test_same_pointer_frame_id_rebinding_at_encode_refuses():
    r=make(extra="encode_hook=function()frame.id=11;m.frames[1]=11 end")
    assert r.globals().api.observe("encyclopedia",2).status=="REFUSED"

def test_observed_expand_uses_exact_registered_wrapper():
    r=make(extra="""
        row[1].type='button';row[1].handlers.onClick=wrapped
        m.buttonScriptMap={{layer=1,tableobj=20,row=2,col=1,type='onClick',button=31,script=wrapped}}
    """)
    out=r.globals().api.observe("encyclopedia",2)
    ticket=prepare(r,"expand",out.rows[1].token)
    assert r.globals().api.execute(ticket).status=="DISPATCHED_UNVERIFIED"
    assert r.eval("calls")==1

@pytest.mark.parametrize("change",["active=false","row[1].handlers.onClick=function()end",
    "m.buttonScriptMap[1].script=function()end","m.panelState.leftbar=false","m.panelMode=false"])
def test_property_route_drift_before_dispatch_refuses(change):
    r=make("sidebar");ticket=prepare(r,"property_open");r.execute(change)
    assert r.globals().api.execute(ticket).status=="REFUSED" and r.eval("calls")==0

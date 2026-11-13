"""Built OptionsMenu callback buttons; actual Lua adapter, synthetic native UI."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("owned_options_fixture", Path(__file__).with_name("test_liveoptions.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


def build():
    r = _fixture.build()
    r.execute("""
        m.currentOption='ror_report'
        button_calls=0
        function effect() button_calls=button_calls+1;if after_write then after_write()end end
        br={id='report',valuetype='button',name='Diagnostics',value='Generate report',callback=effect}
        bc={type='button',id=40,colspan=1,properties={active=true},handlers={onClick=effect}}
        brow={{type='text'},{type='text'},bc,rowdata=br,index=3,properties={interactive=true}}
        tbl.rows[3]=brow;m.rowDataMap[20][3]=br
        function click() assert(select('#')==0);effect() end
        m.buttonScriptMap={{layer=4,tableobj=20,row=3,col=3,button=40,type='onClick',script=click}}
        old_cell=d.cell_id;old_valid=d.valid_widget;old_state=d.widget_state
        d.cell_id=function(t,ri,ci)if ri==3 and ci==3 then assert(t==20);return bc.id end;return old_cell(t,ri,ci)end
        d.valid_widget=function(id)return id==40 or old_valid(id)end
        d.widget_state=function(id,kind)
            if id==40 then assert(kind=='button');return {active=button_active~=false}end
            return old_state(id,kind)
        end
        function button_prep()bp=api.prepare_button('ror_report',3,3,40,'report','Generate report');return bp end
        api=factory(d)
    """)
    return r


def test_observe_and_native_wrapper_dispatch_once():
    r = build()
    assert r.eval("button_calls") == 0
    obs = r.eval("api.observe('ror_report')")
    assert obs.status == "OBSERVED"
    assert obs.button_cells == 1 and obs.button_cells_unqualified == 0
    assert obs.callback_button_scope_complete is True
    assert obs.buttons[1].id == "report" and obs.buttons[1].text == "Generate report"
    assert r.eval("button_prep().status") == "PREPARED"
    out = r.eval("api.execute(bp.ticket)")
    assert out.status == "DISPATCHED_UNVERIFIED"
    assert out.callback_effects_verified is False and out.rendered_pixels_verified is False
    r.execute("api.execute(bp.ticket)")
    assert r.eval("button_calls") == 1


@pytest.mark.parametrize("change", [
    "button_active=false;bc.properties.active=false",
    "brow.properties.interactive=false",
])
def test_disabled_button_is_observed_but_not_dispatched(change):
    r = build();r.execute(change)
    obs = r.eval("api.observe('ror_report')")
    assert obs.status == "OBSERVED" and obs.buttons[1].enabled is False
    assert r.eval("button_prep().status") == "REFUSED"
    assert r.eval("button_calls") == 0


@pytest.mark.parametrize("change", [
    "bc.id=41",
    "m.buttonScriptMap={}",
    "m.buttonScriptMap[2]=m.buttonScriptMap[1]",
    "m.buttonScriptMap[1].button=41",
    "m.buttonScriptMap[1].type='onDoubleClick'",
    "m.rowDataMap[20][3]={}",
    "bc.handlers.onClick=function()error('wrong callback')end",
    "br.valuetype='confirmation'",
    "br.submenu='save'",
    "br.value=function()return 'Generate report'end",
    "br.id='wrong'",
    "br.value='Other action'",
    "br.callback=nil",
])
def test_wrong_button_or_unknown_model_cannot_prepare(change):
    r = build();r.execute(change)
    assert r.eval("button_prep().status") == "REFUSED"
    assert r.eval("button_calls") == 0


@pytest.mark.parametrize("change", [
    "time=41", "time=9",
    "stamp.generation=string.rep('e',32)",
    "br.value='Changed'", "br.name='Changed'", "br.id='Changed'",
    "bc.handlers.onClick=function()end",
    "m.buttonScriptMap[1].script=function()error('changed wrapper')end",
    "brow.properties.interactive=false", "button_active=false;bc.properties.active=false",
    "m.currentOption='other'", "m.shown=false",
])
def test_native_frame_callback_or_identity_drift_consumes_without_dispatch(change):
    r = build()
    assert r.eval("button_prep().status") == "PREPARED"
    r.execute(change)
    out = r.eval("api.execute(bp.ticket)")
    assert out.status == "REFUSED" and out.action_attempted is False
    assert r.eval("button_calls") == 0
    assert r.eval("api.execute(bp.ticket).action_attempted") is False


def test_post_callback_failure_is_uncertain_and_not_replayed():
    r = build()
    assert r.eval("button_prep().status") == "PREPARED"
    r.execute("after_write=function()stamp.generation=string.rep('f',32)end")
    out = r.eval("api.execute(bp.ticket)")
    assert out.status == "UNCERTAIN" and out.action_attempted is True
    r.execute("api.execute(bp.ticket)")
    assert r.eval("button_calls") == 1


def test_unknown_button_cells_are_counted_not_silent_absence():
    r = build();r.execute("br.valuetype='confirmation'")
    out = r.eval("api.observe('ror_report')")
    assert out.status == "OBSERVED" and out.button_cells == 1
    assert out.button_cells_unqualified == 1 and out.callback_button_scope_complete is False


@pytest.mark.parametrize("page", ["main","save","load","extensions","bonus","new","profile_load","simple_menu_extension_options"])
def test_reserved_pages_never_dispatch_callback(page):
    r = build();r.globals().page = page;r.execute("m.currentOption=page")
    out = r.eval("api.prepare_button(page,3,3,40,'report','Generate report')")
    assert out.status == "REFUSED" and r.eval("button_calls") == 0


def msa():
    """SN API db52b195 Add_Row/Make_Button shape, not a native option row."""
    r=build()
    r.execute("""
        brow.rowdata=true;m.rowDataMap[20][3]=true
        bc.properties.text={text='activated'}
        function button_prep()bp=api.prepare_button('ror_report',3,3,40,'widget:40','activated');return bp end
    """)
    return r


def test_msa_boolean_row_uses_built_identity_without_inventing_authored_id():
    r=msa();out=r.eval("api.observe('ror_report')")
    assert out.status=='OBSERVED' and out.button_cells_unqualified==0
    model=out.buttons[1]
    assert model.id=='widget:40' and model.text=='activated'
    assert model.identity_kind=='BUILT_WIDGET_ONLY' and model.name==''
    assert r.eval('button_prep().status')=='PREPARED'
    assert r.eval('api.execute(bp.ticket).status')=='DISPATCHED_UNVERIFIED'
    r.execute('api.execute(bp.ticket)')
    assert r.eval('button_calls')==1


@pytest.mark.parametrize('change',[
    'brow.rowdata=false;m.rowDataMap[20][3]=false',
    'bc.properties.active=false;button_active=false',
    'brow.properties.interactive=false',
    "bc.properties.text.text=function()error('must not run')end",
    'bc.properties.text=nil',
    'bc.handlers.onClick=nil',
    'm.rowDataMap[20][3]=false',
])
def test_msa_unknown_disabled_or_unbound_button_refuses(change):
    r=msa();r.execute(change)
    assert r.eval('button_prep().status')=='REFUSED'
    assert r.eval('button_calls')==0


@pytest.mark.parametrize('change',[
    "bc.properties.text.text='changed'",
    'brow.rowdata=false;m.rowDataMap[20][3]=false',
    'bc.handlers.onClick=function()end',
    'm.buttonScriptMap[1].script=function()end',
])
def test_msa_text_row_or_callback_drift_refuses_without_replay(change):
    r=msa();assert r.eval('button_prep().status')=='PREPARED'
    r.execute(change);assert r.eval('api.execute(bp.ticket).status')=='REFUSED'
    assert r.eval('button_calls')==0


def test_msa_does_not_accept_an_invented_authored_id():
    r=msa()
    assert r.eval("api.prepare_button('ror_report',3,3,40,'report','activated').status")=='REFUSED'
    assert r.eval('button_calls')==0

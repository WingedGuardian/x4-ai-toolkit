"""OptionsMenu close lifecycle, actual Lua with synthetic native UI."""
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('close_options_fixture',Path(__file__).with_name('test_liveoptions.py'))
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def build():
    r=fixture.build()
    r.execute("""
        close_calls=0
        m.cleanup=function()end
        m.closeMenu=function()end
        m.onCloseElement=function(reason)
            assert(reason=='close');close_calls=close_calls+1
            if after_write then after_write()end
            if not keep_open then m.shown=false;m.currentOption=nil;m.optionTable=nil end
        end
        function close_prep()cp=api.prepare_close('simple_menu_extension_options');return cp end
    """)
    return r


@pytest.mark.parametrize('native_flag',['false','nil'])
def test_close_uses_native_close_once_and_observes_only_qualified_visibility(native_flag):
    r=build()
    assert r.eval("api.observe_closed('1').status")=='REFUSED'
    assert r.eval('close_prep().status')=='PREPARED'
    assert r.eval('api.observe_closed(cp.ticket).status')=='REFUSED'
    out=r.eval('api.execute(cp.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED'
    assert out.setting_effect_verified is False and out.rendered_pixels_verified is False
    r.execute('api.execute(cp.ticket)')
    assert r.eval('close_calls')==1
    r.execute('m.shown='+native_flag)
    observed=r.eval('api.observe_closed(cp.ticket)')
    assert observed.status=='MENU_CLOSED_OBSERVED' and observed.shown is False
    assert observed.shown_normalized is True
    assert observed.native_shown_state==('UNSET' if native_flag=='nil' else 'FALSE')
    assert observed.simulation_unpaused_verified is False


@pytest.mark.parametrize('change',[
    'm.isStartmenu=true','m.remapControl={}','m.directInputActive=true',
    'm.cutsceneid=5','m.playNewGameCutscene={}','m.contextMenuMode="question"',
    'm.userQuestion={}','m.onCloseElement=nil','m.closeMenu=nil','m.cleanup=nil',
])
def test_close_refuses_start_menu_special_activity_and_missing_handlers(change):
    r=build();r.execute(change)
    assert r.eval('close_prep().status')=='REFUSED' and r.eval('close_calls')==0


@pytest.mark.parametrize('change',[
    'm.onCloseElement=function()end','m.closeMenu=function()end','m.cleanup=function()end',
    'm.remapControl={}','time=41','m.isStartmenu=true','m.currentOption="other"',
    "stamp.generation=string.rep('e',32)",
])
def test_pre_dispatch_drift_consumes_ticket_without_close(change):
    r=build();assert r.eval('close_prep().status')=='PREPARED'
    r.execute(change)
    out=r.eval('api.execute(cp.ticket)')
    assert out.status=='REFUSED' and out.action_attempted is False
    r.execute('api.execute(cp.ticket)')
    assert r.eval('close_calls')==0


def test_native_return_does_not_prove_close_or_unpause():
    r=build();r.execute('keep_open=true;close_prep()')
    assert r.eval('api.execute(cp.ticket).status')=='DISPATCHED_UNVERIFIED'
    assert r.eval('api.observe_closed(cp.ticket).status')=='REFUSED'


def test_post_close_identity_failure_remains_uncertain():
    r=build();r.execute("close_prep();after_write=function()stamp.generation=string.rep('e',32)end")
    out=r.eval('api.execute(cp.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True
    assert r.eval('api.observe_closed(cp.ticket).status')=='REFUSED'
    r.execute('api.execute(cp.ticket)')
    assert r.eval('close_calls')==1


def test_slow_final_native_read_cannot_dispatch_an_expired_ticket():
    r=build()
    r.execute('''
        old_valid=d.valid_widget
        d.valid_widget=function(id)if slow then time=41 end;return old_valid(id)end
        api=factory(d)
    ''')
    assert r.eval('close_prep().status')=='PREPARED'
    r.execute('slow=true')
    out=r.eval('api.execute(cp.ticket)')
    assert out.status=='REFUSED' and out.action_attempted is False
    assert r.eval('close_calls')==0

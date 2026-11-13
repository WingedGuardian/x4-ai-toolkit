"""Actual SDK/codec forwarding for observed callback buttons; native UI is fake."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("owned_native_provider_fixture", Path(__file__).with_name("test_livenativeproviders.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


def build(with_button=True):
    r = _fixture.runtime.__wrapped__()
    if with_button:
        r.execute("options_adapter.prepare_button=pre;setup()")
    return r


def test_button_schema_exact_forwarding_once_and_bounded_codec():
    r = build()
    result = r.eval("""prepare('options.button',
        {page='ror_report',row=3,col=3,widget=40,id='report',text='Generate report'})""")
    assert result.status == "prepared"
    assert [r.eval("last_prepare[" + str(i) + "]") for i in range(1, 7)] == [
        "ror_report", 3, 3, 40, "report", "Generate report"]
    r.execute("button_result=execute();execute()")
    assert r.eval("writes") == 1
    wire = r.execute(_fixture.source("actionwire"))()
    inner = wire.encode(r.globals().button_result)
    assert len(inner) <= 4096
    outer = wire.encode(r.table_from({"payload": inner}))
    assert '"payload"' in outer


def test_missing_optional_button_adapter_refuses_without_dispatch():
    r = build(False)
    result = r.eval("""prepare('options.button',
        {page='ror_report',row=3,col=3,widget=40,id='report',text='Generate report'})""")
    assert result.status == "refused"
    assert r.eval("prepares+writes") == 0


@pytest.mark.parametrize("bad", [
    "{page='ror_report',row=0,col=3,widget=40,id='report',text='Generate report'}",
    "{page='ror_report',row=3.5,col=3,widget=40,id='report',text='Generate report'}",
    "{page='ror_report',row=3,col=3,widget=40,id='',text='Generate report'}",
    "{page='ror_report',row=3,col=3,widget=40,id='report',text='Generate report',callback='eval'}",
    "{page='ror_report',row=3,col=3,widget=40,id='report'}",
])
def test_bad_button_arguments_never_reach_adapter(bad):
    r = build()
    result = r.eval("prepare('options.button'," + bad + ")")
    assert result.status == "refused"
    assert r.eval("prepares+writes") == 0


def test_options_close_exact_page_once_and_later_visibility_readback():
    r=build()
    r.execute("""
        options_adapter.prepare_close=pre
        options_adapter.observe_closed=function(ticket)
            assert(ticket=='private-1');return{status='MENU_CLOSED_OBSERVED'}
        end
        setup()
    """)
    assert r.eval("prepare('options.close',{page='ror_settings_menu'}).status")=='prepared'
    assert r.eval('last_prepare[1]')=='ror_settings_menu'
    assert r.eval('execute().status')=='running'
    assert r.eval('status().status')=='unknown'
    r.execute('time=time+1')
    result=r.eval('status()')
    assert result.status=='completed'
    assert result.observations.scope=='OWNED_OPTIONS_CLOSED_READBACK'
    assert result.observations.setting_effect_verified is False
    r.execute('execute()')
    assert r.eval('writes')==1


def test_options_close_without_optional_adapter_refuses():
    r=build()
    assert r.eval("prepare('options.close',{page='ror_settings_menu'}).status")=='refused'
    assert r.eval('prepares+writes')==0


@pytest.mark.parametrize('args',["{}","{page='ror_settings_menu',quit=true}","{page=''}"])
def test_options_close_requires_exact_closed_schema(args):
    r=build()
    assert r.eval("prepare('options.close',"+args+").status")=='refused'
    assert r.eval('prepares+writes')==0

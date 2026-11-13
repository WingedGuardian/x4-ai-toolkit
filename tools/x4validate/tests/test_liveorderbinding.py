"""Actual native provider/SDK association accessor; no game calls or order effects."""
import pytest
from test_livenativeproviders import runtime


def bound(rt):
    rt.execute("""
        deps.resolve_issued=function(token,kind)
            calls=(calls or 0)+1
            if drift_call and calls==drift_call then subject='43' end
            if epoch_call and calls==epoch_call then stamp.generation=string.rep('e',32) end
            if token=='ship' and kind=='ship' then return subject or '42' end
            if token=='target' and kind=='object' then return target or '7' end
            error('unissued')
        end
        order_adapter.prepare=function() return {status='PREPARED',ticket='private',
            ship='42',target='7',order='DockAndWait',action_attempted=false} end
        setup()
        prepare('order.submit',{ship='ship',target='target',kind='dock',policy='require_empty'})
    """)


def test_prepared_copy_has_no_private_capability(runtime):
    bound(runtime)
    runtime.execute("a=adapter.order_binding(request.id)")
    assert runtime.eval("a.ship=='42' and a.target=='7' and a.order=='DockAndWait' and not a.dispatched")
    assert runtime.eval("a.ticket==nil and a.order_id=='UNKNOWN' and not a.order_caused_event_verified")
    runtime.execute("a.stamp.session='wrong';a.ship='other';a.kind='attack';b=adapter.order_binding(request.id)")
    assert runtime.eval("b.stamp.session==NONCE and b.ship=='42' and b.kind=='dock' and writes==0")


@pytest.mark.parametrize('change', ["subject='43'", "target='8'", "stamp.generation=string.rep('e',32)",
                                    "adapter.dispose()"])
def test_changed_identity_refused(runtime, change):
    bound(runtime);runtime.execute(change)
    assert runtime.eval("pcall(adapter.order_binding,request.id)")[0] is False
    assert runtime.eval('writes') == 0


@pytest.mark.parametrize('key', ["string.rep('9',32)", "'bad'"])
def test_foreign_request_refused(runtime,key):
    bound(runtime)
    assert runtime.eval('pcall(adapter.order_binding,'+key+')')[0] is False


@pytest.mark.parametrize('state', ['ORDER_OBSERVED','PARTIAL_CREATED_NOT_ENABLED','UNCERTAIN'])
def test_actual_native_status_not_promoted(runtime,state):
    bound(runtime);runtime.execute("native_status='"+state+"';execute();a=adapter.order_binding(request.id)")
    assert runtime.eval('a.native_status') == state
    assert runtime.eval("a.order_id=='123' and a.dispatched and a.action_attempted and not a.arrival_verified")
    assert runtime.eval('a.wrapper_dispatch_readback_qualified')


def test_dispatch_exception_keeps_unknown_attempt(runtime):
    bound(runtime);runtime.execute('throw_write=true;execute();a=adapter.order_binding(request.id)')
    assert runtime.eval("a.dispatched and a.native_status=='UNKNOWN' and a.order_id=='UNKNOWN' and a.action_attempted=='UNKNOWN'")


def test_non_order_request_refused(runtime):
    runtime.execute("prepare('camera.view',{target='target'})")
    assert runtime.eval('pcall(adapter.order_binding,request.id)')[0] is False


def test_mid_read_identity_change_refused(runtime):
    bound(runtime)
    runtime.execute("calls=0;drift_call=3")
    assert runtime.eval('pcall(adapter.order_binding,request.id)')[0] is False


def test_last_resolver_epoch_change_refused(runtime):
    bound(runtime)
    runtime.execute('calls=0;epoch_call=4')
    assert runtime.eval('pcall(adapter.order_binding,request.id)')[0] is False


def test_retained_native_result_does_not_hide_wrapper_timing_failure(runtime):
    bound(runtime)
    runtime.execute("""
      order_adapter.execute=function()time=9;return {status='ORDER_OBSERVED',order_id='123',action_attempted=true}end
      setup();prepare('order.submit',{ship='ship',target='target',kind='dock',policy='require_empty'})
      execute();a=adapter.order_binding(request.id)
    """)
    assert runtime.eval("a.native_status=='ORDER_OBSERVED' and a.order_id=='123' and not a.wrapper_dispatch_readback_qualified")

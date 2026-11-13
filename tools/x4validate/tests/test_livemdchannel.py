"""Actual MD channel with real LuaJIT integers and synthetic engine callbacks."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_mdchannel.lua"


def runtime(setup=""):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
      ffi=require('ffi'); now=10; callbacks={}; timers={}; reads=0; clears=0; sends=0; drains=0; ticks=0
      player=42ULL; converted=42; component=43; queue={{item='one'}}
      stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='world'}
      g={RegisterEvent=function(name,fn) assert(name=='Fixture.Replies');callbacks[#callbacks+1]=fn end,
        GetNPCBlackboard=function(id,name) assert(id==42 and name=='$fixture_replies');reads=reads+1;if switch_read then player=43ULL;converted=43 end;return queue end,
        SetNPCBlackboard=function(id,name,value) assert(id==42 and name=='$fixture_replies' and value==nil);clears=clears+1;queue=nil end,
        AddUITriggeredEvent=function(screen,control,body) assert(screen=='Fixture' and #timers>0);sends=sends+1 end,
        ConvertStringTo64Bit=function(text) return converted end,
        ConvertIDTo64Bit=function(value) return component end,
        getElapsedTime=function() return now end}
      H={addDelayedOneTimeCallbackOnUpdate=function(fn,block,deadline) assert(block==false and deadline==now+10.25);timers[#timers+1]=fn end}
      d={ffi=ffi,C={GetPlayerID=function() return player end},globals=g,Helper=H,
        public_current=function() return stamp end,
        config={screen='Fixture',event='Fixture.Replies',queue='$fixture_replies',controls={connect=true,request=true}}}
    """)
    r.execute(setup);r.globals().factory=r.execute(SOURCE.read_text())
    r.execute("api=factory(d)")
    return r


def refused(r,code):
    assert r.execute("return pcall(function() "+code+" end)")[0] is False


def test_constructor_does_not_query_send_or_subscribe():
    r=runtime();assert r.eval("reads+clears+sends+#callbacks")==0


def test_fixed_read_clear_and_component_observation():
    r=runtime();assert r.eval("api.read()[1].item")=="one"
    r.execute("api.clear()")
    assert r.eval("clears")==1 and r.eval("api.component_id({})")=="43ULL"


def test_schedule_precedes_one_native_send_and_ticks_once():
    r=runtime();r.execute("api.arm_tick(function() ticks=ticks+1 end);api.send('connect',{})")
    assert r.eval("sends")==1 and r.eval("#timers")==1
    r.execute("timers[1]();timers[1]()")
    assert r.eval("ticks")==1


def test_early_event_does_not_drain_before_session_binding():
    r=runtime();r.execute("api.subscribe(function() drains=drains+1 end);stamp=nil;callbacks[1]()")
    assert r.eval("drains")==0 and r.eval("reads")==0


def test_subscription_drains_only_current_bound_lifetime():
    r=runtime();r.execute("api.subscribe(function() drains=drains+1 end);api.read();callbacks[1]()")
    assert r.eval("drains")==1
    r.execute("api.dispose();callbacks[1]()")
    assert r.eval("drains")==1


@pytest.mark.parametrize("value",["nil","false","'42ULL'","43","-42","42.5","0/0","math.huge","9007199254740992"])
def test_bad_player_conversion_never_reads_or_clears(value):
    r=runtime("converted="+value);refused(r,"api.read()")
    assert r.eval("reads+clears")==0


def test_different_player_during_read_cannot_clear_either_queue():
    r=runtime("switch_read=true");refused(r,"api.read()")
    refused(r,"api.clear()")
    assert r.eval("clears")==0


def test_changed_player_after_read_cannot_clear():
    r=runtime();r.execute("api.read();player=43ULL;converted=43")
    refused(r,"api.clear()");assert r.eval("clears")==0


def test_changed_session_fault_is_sticky():
    r=runtime();r.execute("api.read();stamp.session=string.rep('d',32)")
    refused(r,"api.read()")
    r.execute("stamp.session=string.rep('b',32)")
    refused(r,"api.read()");assert r.eval("reads")==1


def test_clear_requires_prior_read_and_cannot_repeat():
    r=runtime();refused(r,"api.clear()");assert r.eval("clears")==0
    r=runtime();r.execute("api.read();api.clear()")
    refused(r,"api.clear()");assert r.eval("clears")==1


def test_unlisted_control_and_missing_tick_never_send():
    r=runtime();refused(r,"api.send('eval',{})");assert r.eval("sends")==0
    r=runtime();refused(r,"api.send('connect',{})");assert r.eval("sends")==0


def test_failed_scheduler_prevents_send_and_retry():
    r=runtime("H.addDelayedOneTimeCallbackOnUpdate=function() error('unavailable') end")
    r.execute("api.arm_tick(function() end)")
    refused(r,"api.send('connect',{})");refused(r,"api.send('connect',{})")
    assert r.eval("sends")==0


def test_old_deadline_cannot_call_replacement_after_dispose():
    r=runtime();r.execute("api.arm_tick(function() ticks=ticks+1 end);api.send('connect',{});api.dispose();timers[1]()")
    assert r.eval("ticks")==0


def test_configuration_mutation_cannot_select_other_native_channel():
    r=runtime();r.execute("d.config.queue='$other';d.config.controls.eval=true;api.read()")
    assert r.eval("reads")==1
    refused(r,"api.send('eval',{})");assert r.eval("sends")==0


def test_component_observation_rejects_unsafe_double():
    r=runtime("component=9007199254740992");refused(r,"api.component_id({})")
    assert r.eval("api.read()[1].item")=="one"


def test_deadline_capacity_is_bounded_without_replaying_sends():
    r=runtime();r.execute("api.arm_tick(function() end);for i=1,64 do api.send('request',{}) end")
    refused(r,"api.send('request',{})")
    assert r.eval("sends")==64 and r.eval("#timers")==64


def test_uncertain_native_send_cannot_be_retried():
    r=runtime("g.AddUITriggeredEvent=function() sends=sends+1;error('lost outcome') end")
    r.execute("api.arm_tick(function() end)")
    refused(r,"api.send('connect',{})");refused(r,"api.send('connect',{})")
    assert r.eval("sends")==1

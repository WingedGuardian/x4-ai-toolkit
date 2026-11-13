"""Actual local channel state and generated bound read; no game calls."""
import json
import pytest
from lupa.luajit21 import LuaRuntime
from test_livemdchannel import runtime as channel_runtime,refused
from test_livebundle_capacity import B,ROOT,synthetic
from test_livebound import Pipe,Link
from x4validate._livebound import BoundPipe
from x4validate._liveautomation import AutomationClient

def test_capacity_read_does_not_bind_clock_send_or_schedule():
    lua=channel_runtime("d.public_current=function() error('must not bind') end;g.getElapsedTime=function() error('must not read clock') end")
    row=lua.globals().api.capacity()
    assert row.scheduled_callbacks_used==0 and row.scheduled_callbacks_limit==64
    assert row.deadline_interval_ui_clock_units==10.25
    assert row.stamp_bound is False and row.lifecycle_open is True and row.tick_armed is False
    assert lua.eval("reads+clears+sends+#callbacks+#timers")==0

def test_completed_reply_does_not_retire_deadline_but_scheduler_does_once():
    lua=channel_runtime()
    lua.execute("api.arm_tick(function() ticks=ticks+1 end);api.send('request',{});api.read();api.clear()")
    assert lua.globals().api.capacity().scheduled_callbacks_used==1
    row=lua.globals().api.capacity();row.scheduled_callbacks_used=0
    assert lua.globals().api.capacity().scheduled_callbacks_used==1
    lua.execute("timers[1]();timers[1]()")
    assert lua.globals().api.capacity().scheduled_callbacks_used==0
    assert lua.globals().ticks==1

def test_counter_read_never_clears_full_or_faulted_channel():
    lua=channel_runtime()
    lua.execute("api.arm_tick(function() end);for i=1,64 do api.send('request',{}) end")
    assert lua.globals().api.capacity().scheduled_callbacks_used==64
    refused(lua,"api.send('request',{})")
    row=lua.globals().api.capacity()
    assert row.faulted is True and row.lifecycle_open is False and row.scheduled_callbacks_used==64
    lua.execute("for _,fn in ipairs(timers) do fn() end")
    row=lua.globals().api.capacity()
    assert row.scheduled_callbacks_used==0 and row.faulted is True and row.lifecycle_open is False
    refused(lua,"api.send('request',{})")
    assert lua.globals().sends==64

def test_disposal_preserves_deadline_count_until_actual_callback_retires():
    lua=channel_runtime()
    lua.execute("api.arm_tick(function() ticks=ticks+1 end);api.send('request',{});api.dispose()")
    row=lua.globals().api.capacity()
    assert row.disposed is True and row.lifecycle_open is False and row.scheduled_callbacks_used==1
    lua.execute("timers[1]()")
    assert lua.globals().api.capacity().scheduled_callbacks_used==0 and lua.globals().ticks==0

def routed(tmp_path,*,counter=True,channels=2):
    src=synthetic()
    for key in ("actions","wire","automation"):src[key]=(ROOT/B.SOURCES[key]).read_bytes()
    src["bootstrap"]=b"""return function(d)
      local core=d.modules.actions(d.capacity_profile,d.capacity_resolver)
      local wire=d.modules.wire()
      local session={current=function()
        return {provider=stamp.provider,revision=stamp.revision,session=stamp.session,
          generation=stamp.generation,world_token=stamp.world_token}
      end}
      router=d.modules.automation{session=session,registry=core,wire=wire,
        read=d.read,read_verbs=d.read_verbs,build=d.build,
        age=function() return 17 end,ui_authorize=function() return true end,
        capacity_profile=d.capacity_profile,capacity_resolver=d.capacity_resolver}
      return {session=session,encode=wire.encode,router=router,
        register_provider=function() end,dispose=function() return true end}
    end"""
    src["mdchannel"]=b"""return function(d)
      channel_count=(channel_count or 0)+1
      local index=channel_count
      local out={dispose=function() return true end}
      if capacity_available then out.capacity=function()
        counter_reads=(counter_reads or 0)+1
        return {scope='LOCAL_CHANNEL_DEADLINE_CALLBACKS_NOT_PENDING_REQUESTS',
          scheduled_callbacks_used=index,scheduled_callbacks_limit=64,disposed=false,
          faulted=false,lifecycle_open=true,tick_armed=true,stamp_bound=false,deadline_interval_ui_clock_units=10.25,
          future_reservation=false}
      end end
      return out
    end"""
    payload,receipt=B.build(src,"c"*64,capacity_profile="campaign_v1")
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().capacity_available=counter
    lua.globals().wanted_channels=channels
    lua.execute("""stamp={provider='x4-live-session-v1',revision=string.rep('c',64),
      session=string.rep('a',32),generation=string.rep('b',32),world_token=string.rep('d',32)}""")
    lua.execute(payload.decode())
    lua.execute("""function descriptor(name,provider,channels)
      return {id=name,revision=string.rep('e',64),providers={[provider]=string.rep('f',64)},
        attach=function(ctx)
          for i=1,channels do ctx.create_md_channel{screen='Test',event='Test.Replies',queue='$test',controls={query=true}} end
          ctx.registry.register{id=provider,revision=string.rep('f',64),actions={observe=true},
            session=function() return 'test' end,invoke=function() end,dispose=function() return true end}
          return {dispose=function() return true end}
        end}
      end
      X4ToolkitAutomationBundle.register_extension(descriptor('ext-a','provider-a',wanted_channels))
      X4ToolkitAutomationBundle.register_extension(descriptor('ext-empty','provider-empty',0))
      X4ToolkitAutomationBundle.new{globals={},read=function() error('legacy read must not run') end,read_verbs={probe=true}}""")
    pipe=Pipe(lua);client=AutomationClient(BoundPipe(pipe,Link(),receipt["build"]),tmp_path)
    return lua,client

def test_catalog_is_complete_for_owned_extensions_without_probing_native_md(tmp_path):
    lua,client=routed(tmp_path)
    row=json.loads(client.query("md_channel_capacity").payload)
    assert row["extensions_returned"]==2
    assert [(r["extension"],r["channel_count"])for r in row["entries"]]==[("ext-a",2),("ext-empty",0)]
    assert row["native_md_loaded_verified"] is False and row["future_fit_verified"] is False
    assert lua.globals().counter_reads is None

def test_selected_extension_uses_owned_counters_through_actual_bound_capture(tmp_path):
    lua,client=routed(tmp_path)
    row=json.loads(client.query("md_channel_capacity","ext-a").payload)
    assert row["extensions_returned"]==1
    assert [v["scheduled_callbacks_used"]for v in row["entries"][0]["channels"]]==[1,2]
    assert row["atomic"] is False and row["reserved"] is False
    assert client.last_read["transport_complete"] and lua.globals().counter_reads==2

def test_zero_channels_is_explicit_owned_denominator_not_native_absence(tmp_path):
    _,client=routed(tmp_path)
    row=json.loads(client.query("md_channel_capacity","ext-empty").payload)
    assert row["extensions_returned"]==1 and row["entries"][0]["channel_count"]==0
    assert row["native_md_loaded_verified"] is False

@pytest.mark.parametrize("args",[("missing",),("provider-a",),("ext-a","extra"),("",),("x"*97,)])
def test_unregistered_or_invalid_selector_refuses_without_counter_calls(tmp_path,args):
    from x4validate._livesession import SessionUnavailable
    lua,client=routed(tmp_path)
    with pytest.raises(SessionUnavailable):client.query("md_channel_capacity",*args)
    assert lua.globals().counter_reads is None

def test_missing_counter_is_unavailable_not_zero(tmp_path):
    from x4validate._livesession import SessionUnavailable
    _,client=routed(tmp_path,counter=False)
    with pytest.raises(SessionUnavailable):client.query("md_channel_capacity","ext-a")

def test_full_cleanup_bound_channel_count_stays_within_wire_budget(tmp_path):
    _,client=routed(tmp_path,channels=16)
    row=json.loads(client.query("md_channel_capacity","ext-a").payload)
    assert row["entries"][0]["channel_count"]==16
    assert len(client.last_read["payload"].encode())<=32000

def test_legacy_namespace_cannot_substitute_channel_counter():
    payload,_=B.build(synthetic(),"a"*64)
    lua=LuaRuntime(unpack_returned_tuples=True);lua.execute(payload.decode())
    ok,why=lua.execute("return pcall(X4ToolkitAutomationBundle.new,{read=function() end,read_verbs={md_channel_capacity=true}})")
    assert ok is False and "namespace" in why


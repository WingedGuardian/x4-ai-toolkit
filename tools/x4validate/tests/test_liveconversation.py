"""Actual Lua observer with synthetic channel; no game/native actions."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_conversation.lua"


def runtime():
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
        now=0; sends=0; reads=0; clears=0; converts=0; subscribed=0; disposed=0
        id=string.rep('d',32); revision=string.rep('e',64)
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        channel={public_current=function() return stamp end,now=function() return now end,
            arm_tick=function(fn) tick=fn end,subscribe=function(fn) subscribed=subscribed+1;drain=fn end,
            send=function(control,body) assert(control=='snapshot');sends=sends+1;sent=body end,
            read=function() reads=reads+1;return queue end,clear=function() clears=clears+1;queue=nil end,
            component_id=function(value) converts=converts+1;assert(type(value)~='table');return tostring(value)..'ULL' end,
            dispose=function() disposed=disposed+1;return true end}
        function row()
            return {kind='SNAPSHOT',provider='x4-conversation-observer-v1',revision=revision,
                request=id,session=stamp.session,generation=stamp.generation,world_token=stamp.world_token,
                age_before=10,age_after=10,sequence=2,instance=1,history_session=stamp.session,
                history_conversation='default',event_age=9,event_kind='SECTION',section='g_main',
                history_matches=true,stable=true,active_before=true,active_after=true,
                conversation_before='default',conversation_after='default',
                live_kind='COMPONENT',live_component=42,live_context=0,live_seed='UNKNOWN',
                history_kind='COMPONENT',history_component=42,history_context=0,history_seed='UNKNOWN'}
        end
        function deliver(r) queue={r};drain() end
    """)
    lua.globals().factory=lua.execute(SOURCE.read_text(encoding="utf-8"))
    lua.execute("api=factory{channel=channel,revision=revision}")
    return lua


def test_constructor_is_pure_and_begin_poll_does_not_repeat_request():
    r=runtime();assert r.eval("sends+reads+clears+subscribed")==0
    r.execute("api.begin(id)")
    assert r.eval("api.poll(id).status")=="PENDING"
    assert r.eval("api.poll(id).status")=="PENDING" and r.eval("sends")==1


def test_same_instance_actor_section_is_only_historical_observation():
    r=runtime();r.execute("api.begin(id);deliver(row())")
    assert r.eval("api.poll(id).status")=="ACTOR_AND_HISTORICAL_SECTION_OBSERVED"
    assert r.eval("api.poll(id).section_scope")=="EVENT_HISTORY_SAME_OBSERVED_INSTANCE"
    assert r.eval("api.poll(id).rendered_route")=="BLOCKED"
    assert r.eval("api.poll(id).runtime_verified") is False
    r.execute("local x=api.poll(id);x.actor.component='wrong';x.stamp.session='wrong'")
    assert r.eval("api.poll(id).actor.component")=="42ULL"


def test_template_actor_preserves_exact_seed_and_context():
    r=runtime();r.execute("""api.begin(id);local x=row()
        for _,p in ipairs({'live_','history_'}) do x[p..'kind']='TEMPLATE';x[p..'component']=0;
            x[p..'context']=43;x[p..'seed']='18446744073709551615' end;deliver(x)""")
    assert r.eval("api.poll(id).actor.seed")=="18446744073709551615"
    assert r.eval("api.poll(id).actor.context")=="43ULL"


@pytest.mark.parametrize("change",[
    "x.revision=string.rep('f',64)","x.session=string.rep('f',32)","x.generation=string.rep('f',32)",
    "x.world_token='other'","x.request=string.rep('f',32)","x.extra=true","x.section=nil",
    "x.stable='true'","x.age_after=0/0","x.age_after=math.huge","x.event_age=11","x.age_before=11",
    "x.sequence=1.5","x.sequence=1000001","x.instance=3","x.event_kind='GUESSED'",
    "x.live_component={42,'template'}","x.live_component=9007199254740992","x.live_component=0",
    "x.live_component='garbage'","x.history_component=43","x.history_session=string.rep('f',32)",
    "x.conversation_after='another'","x.stable=false","x.section=string.rep('a',513)",
])
def test_malformed_or_contradictory_snapshot_never_qualifies(change):
    r=runtime();r.execute("api.begin(id);local x=row();"+change+";deliver(x)")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"


def test_raw_actor_table_is_never_passed_to_component_converter():
    r=runtime();r.execute("api.begin(id);local x=row();x.live_component={42,'template'};deliver(x)")
    assert r.eval("converts")==0


@pytest.mark.parametrize("change,status",[
    ("x.history_matches=false;x.section='';x.instance=0;x.history_kind='UNKNOWN';x.history_component=0", "ACTOR_OBSERVED_SECTION_UNKNOWN"),
    ("x.history_matches=false;x.event_kind='FINISHED';x.section=''", "ACTOR_OBSERVED_SECTION_UNKNOWN"),
    ("x.history_matches=false;x.active_before=false;x.active_after=false;x.conversation_before='';x.conversation_after=''", "INACTIVE_OBSERVED"),
    ("x.history_matches=false;x.live_kind='UNKNOWN';x.live_component=0", "UNQUALIFIED"),
    ("x.history_matches=false;x.stable=false", "UNQUALIFIED"),
])
def test_missing_history_or_inactive_is_not_a_section_pass(change,status):
    r=runtime();r.execute("api.begin(id);local x=row();"+change+";deliver(x)")
    assert r.eval("api.poll(id).status")==status
    assert r.eval("api.poll(id).section_scope")=="UNKNOWN"


def test_timeout_ignores_late_reply_and_never_retries():
    r=runtime();r.execute("api.begin(id);now=11;tick();deliver(row())")
    assert r.eval("api.poll(id).status")=="UNAVAILABLE" and r.eval("sends")==1


def test_duplicate_success_faults_instead_of_replacing_evidence():
    r=runtime();r.execute("api.begin(id);deliver(row());deliver(row())")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"


@pytest.mark.parametrize("change",["stamp.session=string.rep('f',32)","stamp.generation=string.rep('f',32)","now=-1"])
def test_identity_or_clock_drift_faults_before_acceptance(change):
    r=runtime();r.execute("api.begin(id);"+change+";deliver(row())")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"


def test_retired_listener_does_not_read_or_clear_new_queue():
    r=runtime();r.execute("api.begin(id);api.dispose();queue={row()};drain();tick()")
    assert r.eval("reads+clears")==0 and r.eval("disposed")==1


def test_second_capture_cannot_roll_back_event_counter():
    r=runtime();r.execute("api.begin(id);deliver(row());id=string.rep('f',32);api.begin(id);local x=row();x.sequence=1;deliver(x)")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"


def test_pending_and_reused_requests_refused_without_more_sends():
    r=runtime();r.execute("api.begin(id)")
    assert r.execute("return pcall(api.begin,string.rep('f',32))")[0] is False
    r.execute("deliver(row())")
    assert r.execute("return pcall(api.begin,id)")[0] is False
    assert r.eval("sends")==1


def test_disposal_failure_is_sticky_and_success_is_idempotent():
    r=runtime();r.execute("channel.dispose=function() disposed=disposed+1;return false end")
    assert r.eval("api.dispose()") is False
    r.execute("channel.dispose=function() disposed=disposed+1;return true end")
    assert r.eval("api.dispose()") is False and r.eval("disposed")==1
    r=runtime();assert r.eval("api.dispose()") is True
    assert r.eval("api.dispose()") is True and r.eval("disposed")==1


# MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0
# (KB 2026-11-10; ROR handover defect (b)). MDNUM re-encodes every boolean of a
# synthetic reply the way the engine delivers it.
MDNUM = """
function mdnum(v)
    if v==true then return 1 elseif v==false then return 0 end
    if type(v)=='table' and getmetatable(v)==nil then
        local o={};for k,x in pairs(v) do o[k]=mdnum(x) end;return o
    end
    return v
end
plain_deliver=deliver;function deliver(v) return plain_deliver(mdnum(v)) end
"""


def test_md_numeric_booleans_snapshot_qualifies():
    r=runtime();r.execute(MDNUM);r.execute("api.begin(id);deliver(row())")
    plain=runtime();plain.execute("api.begin(id);deliver(row())")
    assert r.eval("api.poll(id).status")==plain.eval("api.poll(id).status")
    assert r.eval("api.poll(id).status")!="PENDING"

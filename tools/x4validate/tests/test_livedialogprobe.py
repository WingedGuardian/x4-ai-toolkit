"""Actual Lua probe with native read stubs; no game or mutation calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[3]
SOURCE=(ROOT/'mods/x4_toolkit_helper/ui/live_dialogprobe.lua').read_text(encoding="utf-8")


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().factory=r.execute(SOURCE)
    r.execute("""
        ffi=require('ffi');reads=0;writes=0;active=true;now=1
        bound={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        local function no_write() writes=writes+1;error('write forbidden') end
        g={IsDialogActive=function() return active end,
            GetDialogOption=function(i) reads=reads+1;return 'Option '..i,true,false,tostring(i),'tooltip' end,
            SelectDialogOption=no_write,clickDialogButton=no_write,leaveDialogMenu=no_write,
            onDialog1MouseClick=no_write,ProceedFromMenu=no_write}
        C={IsConversationActive=function() return true end,IsConversationCancelling=function() return false end}
        probe=factory{ffi=ffi,C=C,globals=g,public_current=function() return bound end,clock=function() return now end}
    """)
    return r


def test_constructor_has_no_native_reads_and_capture_never_writes():
    r=runtime();assert r.eval('reads+writes')==0
    out=r.eval('probe.capture()')
    assert out.status=='UNBOUND_MODEL_OBSERVED' and out.rows_read==6
    assert out.actor==out.section==out.rendered_visibility=='UNKNOWN'
    assert out.route_qualified is False and out.atomic is False
    assert r.eval('writes')==0
    assert list(out.options.keys())==list(range(1,7))


@pytest.mark.parametrize('value',['false','nil','1',"'true'"])
def test_inactive_or_unknown_never_reads_options(value):
    r=runtime();r.execute('active='+value);out=r.eval('probe.capture()')
    assert out.rows_read==0 and r.eval('reads')==0
    assert out.status==('INACTIVE_OBSERVED' if value=='false' else 'UNAVAILABLE')


@pytest.mark.parametrize('name',['IsDialogActive','GetDialogOption'])
def test_missing_global_reported_without_fallback(name):
    r=runtime();r.execute('g.'+name+'=nil');out=r.eval('probe.capture()')
    assert out.status=='UNAVAILABLE' and out.visibility[name].callable is False
    assert out.rows_read==0 and r.eval('writes')==0


def test_inactive_empty_is_not_an_empty_active_menu():
    r=runtime();r.execute("g.GetDialogOption=function(i) reads=reads+1;return '',false,false,'','' end")
    out=r.eval('probe.capture()')
    assert out.status=='UNBOUND_MODEL_OBSERVED' and out.rows_read==6
    assert all(x.active is False for x in out.options.values())


@pytest.mark.parametrize('returns',[
    "nil,true,false,'1','tip'", "'x',1,false,'1','tip'", "'x',true,nil,'1','tip'",
    "'x',true,false,1,'tip'", "'x',true,false,'1',{}",
    "string.rep('x',1025),true,false,'1','tip'", "'x',true,false,string.rep('x',129),'tip'",
    "'x',true,false,'1',string.rep('x',1025)",
])
def test_unreadable_or_oversized_fields_stay_unknown_partial(returns):
    r=runtime();r.execute('g.GetDialogOption=function(i) reads=reads+1;return '+returns+' end')
    out=r.eval('probe.capture()')
    assert out.status=='PARTIAL_UNBOUND_MODEL' and out.rows_read==6
    assert all(x.readable is False for x in out.options.values())
    assert r.eval('writes')==0


def test_native_failure_retains_row_denominator():
    r=runtime();r.execute("g.GetDialogOption=function(i) reads=reads+1;if i==3 then error('unreadable') end;return 'x',true,false,'','' end")
    out=r.eval('probe.capture()')
    assert out.status=='PARTIAL_UNBOUND_MODEL' and out.rows_read==6 and out.options[3].readable is False


@pytest.mark.parametrize('change',[
    "bound.session=string.rep('d',32)", "active=false", "now=0", "now=0/0",
    "g.GetDialogOption=function() error('replacement') end",
])
def test_identity_activation_clock_or_symbol_drift_never_qualifies(change):
    r=runtime();r.execute("g.GetDialogOption=function(i) reads=reads+1;if i==2 then "+change+" end;return 'x',true,false,'','' end")
    out=r.eval('probe.capture()');assert out.status=='UNQUALIFIED'
    assert out.route_qualified is False and r.eval('writes')==0


@pytest.mark.parametrize('change',[
    "bound=nil", "bound.world_token=''", "bound.extra=true", "now=-1", "now=math.huge",
])
def test_missing_initial_identity_or_clock_stops_option_reads(change):
    r=runtime();r.execute(change);out=r.eval('probe.capture()')
    assert out.status=='UNQUALIFIED' and r.eval('reads')==0


def test_optional_conversation_flags_do_not_fabricate_actor_or_hide_uncertainty():
    r=runtime();r.execute("C.IsConversationActive=nil;C.IsConversationCancelling=function() error('absent') end")
    out=r.eval('probe.capture()')
    assert out.status=='PARTIAL_UNBOUND_MODEL'
    assert out.conversation_active==out.conversation_cancelling=='UNKNOWN'
    assert out.actor==out.section=='UNKNOWN'


def test_retired_probe_refuses_before_reads():
    r=runtime();assert r.eval('probe.dispose()') is True
    assert r.eval('pcall(probe.capture)')[0] is False
    assert r.eval('reads+writes')==0


@pytest.mark.parametrize('change',['active=false','probe.dispose()'])
def test_midread_invalidation_stops_later_native_option_calls(change):
    r=runtime();r.execute("g.GetDialogOption=function(i) reads=reads+1;"+change+";return 'x',true,false,'','' end")
    out=r.eval('probe.capture()')
    assert out.status=='UNQUALIFIED' and out.rows_read==1 and r.eval('reads')==1


def mapped(r, change=""):
    r.execute("""
        inputreads=0;inputmap={}
        for code=97,102 do inputmap[code]={{1,code-95,0}} end
        g.GetInputActionMap=function(...)
            assert(select('#',...)==0,'must request actual current map, not defaults')
            inputreads=inputreads+1
    """ + change + """
            return inputmap
        end
    """)


def test_current_mapping_exact_six_actions_raw_codes_no_host_translation():
    r=runtime();mapped(r)
    r.execute("inputmap[1]={{1,999,0}};inputmap[97][2]={1,514,0,false}")
    out=r.eval('probe.capture()');b=out.current_bindings
    assert b.status=='TWO_EQUAL_PROJECTED_READS' and b.action_count==6
    assert r.eval('inputreads')==2 and r.eval('writes')==0
    assert [x.action for x in b.rows.values()]==list(range(97,103))
    assert list(b.input_fields.values())==['source','code','signum','toggle']
    assert b.rows[1].inputs[1][2]==2
    assert b.rows[1].inputs[1][4]=='NOT_PRESENT'
    assert b.rows[1].inputs[2][4] is False
    assert b.host_key_translation=='UNKNOWN' and out.route_qualified is False


def test_missing_action_is_unknown_and_empty_action_is_observed_empty():
    r=runtime();mapped(r);r.execute('inputmap[97]=nil;inputmap[98]={}')
    b=r.eval('probe.capture().current_bindings')
    assert b.rows[1].status=='UNAVAILABLE' and b.rows[1].count is None
    assert b.rows[2].status=='OBSERVED' and b.rows[2].count==0


@pytest.mark.parametrize('change',[
    'inputmap=nil','inputmap=false',
    'inputmap=setmetatable({}, {})',
    'inputmap[97]=false','inputmap[97]=setmetatable({}, {})',
    'inputmap[97]={[2]={1,2,0}}','inputmap[97][5]={1,2,0}',
    "inputmap[97].extra={1,2,0}",
    'inputmap[97][1]=setmetatable({1,2,0},{})',
    'inputmap[97][1][5]=true','inputmap[97][1][1]=0/0',
    'inputmap[97][1][2]=math.huge','inputmap[97][1][2]=-1',
    'inputmap[97][1][3]=nil','inputmap[97][1][3]=2',
    'inputmap[97][1][4]=1',
])
def test_malformed_current_map_never_becomes_empty_or_qualified(change):
    r=runtime();mapped(r);r.execute(change)
    out=r.eval('probe.capture()')
    assert out.current_bindings.status=='UNAVAILABLE'
    assert out.current_bindings.rows is None and out.route_qualified is False
    assert r.eval('writes')==0


@pytest.mark.parametrize('change',[
    'inputmap[97][1][2]=8','inputmap[98]=nil',
    "error('second read unavailable')",
])
def test_changed_or_lost_second_read_keeps_first_evidence_unqualified(change):
    r=runtime();mapped(r,'if inputreads==2 then '+change+' end')
    b=r.eval('probe.capture().current_bindings')
    assert b.status=='UNQUALIFIED' and b.rows[1].inputs[1][2]==2


@pytest.mark.parametrize('change',[
    "bound.session=string.rep('d',32)",
    'probe.dispose()',
    'g.GetInputActionMap=function()return inputmap end',
])
def test_session_retirement_or_symbol_drift_invalidates_binding_claim(change):
    r=runtime();mapped(r,'if inputreads==2 then '+change+' end')
    out=r.eval('probe.capture()')
    assert out.status=='UNQUALIFIED' and out.current_bindings.status=='UNQUALIFIED'


def test_bounded_maximum_mapping_payload_with_actual_wire_encoder():
    r=runtime();mapped(r)
    r.execute("""
        for code=97,102 do
            inputmap[code]={}
            for i=1,4 do inputmap[code][i]={65535,2147483647,-1,true} end
        end
        g.GetDialogOption=function()return string.rep('x',1024),true,false,string.rep('x',128),string.rep('x',1024)end
    """)
    r.globals().wirefactory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text(encoding="utf-8"))
    payload=r.eval('wirefactory().encode(probe.capture())')
    assert len(payload.encode())<32000
    assert r.eval('writes')==0

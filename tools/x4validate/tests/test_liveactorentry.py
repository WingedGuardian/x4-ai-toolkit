"""Actual Lua/FFI adapter with synthetic native state; no game calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'mods/x4_toolkit_helper/ui/live_actorentry.lua'


def runtime(profile=None):
    r = LuaRuntime(unpack_returned_tuples=True)
    r.execute('ffi=require("ffi")')
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute('''
      reads=0;writes=0;time=10;currentroom=20ULL;currentactor=42ULL
      natives={42ULL,43ULL};possible=true;displayed=true;kind='comm';instant=true
      stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
        generation=string.rep('c',32),world_token='world'}
      function read() reads=reads+1;if onread then onread() end end
      C={GetPlayerID=function()read();return 1ULL end,
        GetPlayerContainerID=function()read();return 30ULL end,
        GetContextByClass=function(v,k,self)read();assert(k=='room' and self==false);return currentroom end,
        IsComponentClass=function(v,k)read();return k=='npc' end,
        GetSofttarget2=function()read();return ffi.new('SofttargetDetails2',currentactor,'',7)end,
        GetMessageDetails3=function(m)read();assert(m==7);return ffi.new('MessageDetails3',0,currentactor,'npc','',false,false,false,true,false)end,
        SetSofttarget=function(v,c)writes=writes+1;assert(c=='');currentactor=v;if onwrite then onwrite()end;return true end,
        IsConversationActive=function()read();return active or false end,
        IsConversationCancelling=function()read();return false end,
        GetNumCompSlotPlayerActions=function(slot)read();return action_count or 1 end,
        GetCompSlotPlayerActions=function(buf,n,slot)
          read();buf[0].id=5;buf[0].text='Comm';buf[0].type=kind;buf[0].ispossible=possible;buf[0].istobedisplayed=displayed
          if n==2 then buf[1]=buf[0];buf[1].id=6 end
          return filled or n
        end,
        PerformCompSlotPlayerAction=function(slot,action)
          assert(slot.component==42ULL and action==5 and ffi.string(slot.connection)=='')
          writes=writes+1;if onwrite then onwrite()end;return true
        end}
      g={ConvertStringTo64Bit=function(v)return ffi.new('uint64_t',tonumber(v:match('^(%d+)')))end,
        GetPrioritizedPlatformNPCs=function(room)read();assert(room==currentroom);return natives end,
        GetActionInfo=function(m)read();assert(m==7);return nil,possible,1,instant,nil,nil,1 end,
        PerformAction=function(m,i)assert(m==7 and i==1);writes=writes+1;if onwrite then onwrite()end end}
      function encode(v)
        if onencode then onencode()end
        if type(v)~='table'then return tostring(v)end
        local t={};for k,x in pairs(v)do t[#t+1]=tostring(k)..':'..encode(x)end;table.sort(t);return '{'..table.concat(t,',')..'}'
      end
      d={ffi=ffi,C=C,globals=g,current=function()return stamp end,now=function()return time end,
         encode=encode,component_id=function(v)if onconvert then onconvert()end;return v end,
         actor_info=function(v)read();return {name='Native Actor',typestring='npc',rolename='representative',owner='synthetic'}end}
      function observe() o=api.observe();key=o.rows and o.rows[1] and o.rows[1].actor;return o end
      function prepare(action) p=api.prepare(action or 'comm',key);return p end
    ''')
    if profile is not None:
        r.globals().profile = profile
        r.execute("d.capacity_profile=profile;d.capacity_resolver=function(name)return {name=name,native_history=512,revision=string.rep('a',64)}end")
    r.execute('api=factory(d)')
    return r


@pytest.mark.parametrize('action', ['focus', 'comm', 'interact'])
def test_no_constructor_calls_actual_native_once_dispatch_without_completion(action):
    r=runtime();assert r.eval('reads+writes')==0
    assert r.eval('observe().status')=='OBSERVED'
    r.globals().action=action
    assert r.eval('prepare(action).status')=='PREPARED'
    assert r.eval('writes')==0
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.conversation_verified is False
    assert out.economic_effect_verified is False
    assert r.eval('api.execute(p.ticket).status')=='REFUSED'
    assert r.eval('writes')==1


@pytest.mark.parametrize('change', [
    'possible=false','displayed=false',"kind='containertrade'",'active=true',
    'currentactor=43ULL','natives={43ULL}','natives={42ULL,42ULL}',
    'natives={[2]=42ULL}','natives={}', 'filled=0','action_count=33',
    'action_count=2','g.GetPrioritizedPlatformNPCs=nil','currentroom=0ULL',
    'time=0/0','stamp=nil','C.IsComponentClass=function()return false end',
])
def test_invalid_offered_model_or_actor_refuses_without_effect(change):
    r=runtime();r.execute('observe();'+change)
    assert r.eval('prepare().status')=='UNAVAILABLE' and r.eval('writes')==0


@pytest.mark.parametrize('change', [
    'possible=false','displayed=false',"kind='trade'",'active=true','currentactor=43ULL',
    'natives={43ULL}','currentroom=21ULL','time=16','time=9',
    "stamp.generation=string.rep('d',32)", 'stamp=nil',
])
def test_prepare_execute_drift_consumes_and_never_replays(change):
    r=runtime();r.execute('observe();prepare();'+change)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED'
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('field', ['GetActionInfo','PerformAction'])
def test_core_missing_is_explicit_unavailable_without_fallback(field):
    r=runtime();r.execute('observe();g.'+field+'=nil')
    assert r.eval("prepare('interact').status")=='UNAVAILABLE' and r.eval('writes')==0
    assert r.eval("prepare('comm').status")=='PREPARED'


@pytest.mark.parametrize('change', ['stamp=nil','time=16',"error('native failure')"])
def test_post_effect_failure_is_uncertain_and_retires(change):
    r=runtime();r.execute('observe();prepare();onwrite=function()'+change+' end')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True
    r.execute('onwrite=nil;time=10')
    assert r.eval('api.observe().status')=='UNAVAILABLE' and r.eval('writes')==1


def test_final_conversion_clock_advance_refuses_and_restored_stamp_stays_retired():
    r=runtime();r.execute('observe();prepare();onconvert=function()time=16 end')
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r=runtime();r.execute("observe();saved=stamp;onconvert=function()stamp=nil end")
    assert r.eval('prepare().status')=='UNAVAILABLE'
    r.execute('onconvert=nil;stamp=saved')
    assert r.eval('api.observe().status')=='UNAVAILABLE'


def test_latest_observation_tokens_are_not_caller_actor_ids_or_old_pages():
    r=runtime();r.execute('observe();old=key;observe()')
    assert r.eval("api.prepare('comm',old).status")=='UNAVAILABLE'
    assert r.eval("api.prepare('comm','42').status")=='UNAVAILABLE'


@pytest.mark.parametrize('profile,limit', [(None,128),('campaign_v1',512)])
def test_actual_retained_counts_and_fixed_profile_capacity(profile,limit):
    r=runtime(profile);r.execute('observe()')
    for _ in range(limit):
        assert r.eval('prepare().status')=='PREPARED'
    assert r.eval('prepare().status')=='UNAVAILABLE'
    out=r.eval('api.capacity()')
    assert out.requests_used==out.requests_limit==limit and out.observations_used==1
    assert r.eval('writes')==0


def test_nil_only_profile_and_missing_campaign_resolver_refuse_before_native_reads():
    r=runtime()
    assert r.eval("pcall(factory,setmetatable({capacity_profile=false},{__index=d}))")[0] is False
    assert r.eval("pcall(factory,setmetatable({capacity_profile='campaign_v1'},{__index=d}))")[0] is False
    assert r.eval('reads+writes')==0


def test_census_count_zero_is_native_scope_not_global_absence_and_output_encoder_drift_refuses():
    r=runtime();r.execute('natives={}')
    out=r.eval('observe()');assert out.native_count==out.matched==0 and out.complete_actor_census is False
    r=runtime();r.execute("onencode=function()stamp.generation=string.rep('d',32)end")
    assert r.eval('observe().status')=='UNAVAILABLE'


def test_metadata_is_observation_not_identity_and_drift_prevents_dispatch():
    r=runtime();out=r.eval('observe()')
    assert out.rows[1].info.rolename=='representative'
    r.execute("prepare();d.actor_info=function()return {name='Other',typestring='npc',rolename='representative',owner='synthetic'}end")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def test_last_current_callback_rebinds_message_before_effect_is_refused():
    r=runtime();r.execute('''
      observe();prepare();contextcalls=0
      d.current=function()contextcalls=contextcalls+1;if contextcalls==2 then currentactor=43ULL end;return stamp end
    ''')
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def test_max_uint64_actor_and_actual_codec_adaptive_page_preserve_exact_ids():
    r=runtime()
    r.globals().wire_factory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text())
    r.execute('''
      wire=wire_factory();d.encode=wire.encode
      natives={18446744073709551615ULL,43ULL,44ULL,45ULL,46ULL,47ULL,48ULL,49ULL}
      d.actor_info=function()return {name=string.rep('N',256),typestring='npc',rolename=string.rep('R',128),owner=string.rep('O',64)}end
    ''')
    out=r.eval('observe()')
    assert out.status=='OBSERVED' and out.native_count==8 and 0<out.returned<8
    assert out.rows[1].component=='18446744073709551615'
    assert len(r.eval("d.encode({payload=d.encode(o),reserve=string.rep('x',900)})").encode())<=4000


def test_current_room_converter_roundtrip_must_match_exactly():
    r=runtime();r.execute('g.ConvertStringTo64Bit=function()return 21ULL end')
    assert r.eval('observe().status')=='UNAVAILABLE' and r.eval('writes')==0


def test_dense_native_action_bound_is_not_silently_truncated():
    r=runtime();r.execute('''
      observe();action_count=32
      C.GetCompSlotPlayerActions=function(buf,n,slot)
        for i=0,n-1 do buf[i].id=i+1;buf[i].text='Action';buf[i].type=i==0 and 'comm' or 'info'
          buf[i].ispossible=true;buf[i].istobedisplayed=true end
        return n
      end
      C.PerformCompSlotPlayerAction=function(slot,id)assert(id==1);writes=writes+1;return true end
    ''')
    assert r.eval('prepare().status')=='PREPARED'
    assert r.eval('api.execute(p.ticket).status')=='DISPATCHED_UNVERIFIED'
    assert r.eval('writes')==1


def test_same_offset_double_action_id_is_rejected_before_any_native_call():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute('''ffi=require('ffi');ffi.cdef[[typedef struct {
      double id;const char* text;const char* type;bool ispossible;bool istobedisplayed;
    } UIAction;]];function never()error('native callback called')end''')
    r.globals().factory=r.execute(SOURCE.read_text())
    ok,reason=r.eval("pcall(factory,{ffi=ffi,C={},globals={},current=never,now=never,encode=never,component_id=never,actor_info=never})")
    assert not ok and 'uint32 representation' in reason

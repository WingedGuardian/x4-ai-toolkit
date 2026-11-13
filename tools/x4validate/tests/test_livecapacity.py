"""Actual SDK/router/codec capacity controls. Synthetic only; no game transport."""
from pathlib import Path
import json
import pytest
from lupa import LuaRuntime
from x4validate import _livecapacity as C

UI=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui"

def runtime(profile="campaign_v1"):
    r=LuaRuntime(unpack_returned_tuples=True)
    for key,name in (("sdkFactory","live_actions.lua"),("routerFactory","live_automation.lua"),("wireFactory","live_actionwire.lua")):
        r.globals()[key]=r.execute((UI/name).read_text(encoding="utf-8"))
    r.globals().resolveCapacity=r.execute(C.lua_source())
    r.globals().profile=profile
    r.execute("""
      core=sdkFactory(profile,resolveCapacity)
      codec=wireFactory();calls={prepare=0,execute=0,status=0};callbacks={}
      stamp={provider='x4-live-session-v1',revision=string.rep('c',64),session=string.rep('a',32),
             generation=string.rep('b',32),world_token='world'}
      function request(i)
        return {version=1,id=string.format('%032x',i),session=stamp.session,provider='fixture',
          action='doit',binding='fixed',digest=string.rep('d',64),arguments={}}
      end
      function packet(i)
        local q=request(i)
        return '1|'..q.id..'|'..q.session..'|fixture|doit|fixed|'..q.digest..'|'
      end
      function invoke(phase,q,done)
        calls[phase]=calls[phase]+1;callbacks[q.id]=done
        if phase=='prepare' then done({status='prepared',reason='',observations={}})
        else done({status='completed',reason='observed synthetic',observations={measured=false}}) end
      end
      core.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
        session=function()return stamp.session end,invoke=invoke})
      reads=0;age=0
      router=routerFactory({capacity_profile=profile,capacity_resolver=resolveCapacity,
        session={current=function()return stamp end},wire=codec,registry=core,
        read=function()reads=reads+1;return 'OK','bounded' end,read_verbs={sample=true},
        age=function()age=age+1;return age end,build='build',ui_authorize=function()return true end})
      function bound(verb,a,b)
        local args={stamp.session,stamp.generation,stamp.revision,stamp.world_token,'build',verb,a}
        if b~=nil then args[8]=b end
        return router.bound(args)
      end
      function execute(i)
        local q=request(i);return core.execute(q.id,q.session,q.digest)
      end
    """)
    return r

@pytest.mark.parametrize("name",["interactive","campaign_v1"])
def test_python_and_emitted_lua_profiles_are_exact_copies(name):
    r=LuaRuntime(unpack_returned_tuples=True);resolve=r.execute(C.lua_source())
    assert dict(resolve(name))==C.resolve(name)
    a=C.resolve(name);a["sdk_history"]=1
    assert C.resolve(name)["sdk_history"]!=1
    first=resolve(name);first["sdk_history"]=1
    assert resolve(name)["sdk_history"]!=1

@pytest.mark.parametrize("name",["", "campaign", "CAMPAIGN_V1", False, 4096, {}])
def test_unknown_profile_not_numeric_override(name):
    with pytest.raises(ValueError):C.resolve(name)

def test_lua_unknown_profile_and_missing_campaign_resolver_refuse():
    r=runtime()
    assert r.eval("pcall(sdkFactory,'campaign_v1')")[0] is False
    assert r.eval("pcall(sdkFactory,'caller-defined',resolveCapacity)")[0] is False
    assert r.eval("pcall(resolveCapacity,'unknown')")[0] is False
    assert r.eval("pcall(resolveCapacity,{sdk_history=8192})")[0] is False

def test_legacy_no_argument_constructor_defaults_are_preserved():
    r=runtime()
    assert r.eval("sdkFactory().capacity().requests_limit")==256
    assert r.eval("sdkFactory().capacity().result_reservation_bytes")==131072

@pytest.mark.parametrize("constructor",["sdkFactory(false,resolveCapacity)",
    "routerFactory({capacity_profile=false,capacity_resolver=resolveCapacity,"
    "session={},wire=codec,registry=core,read=function() error('unexpected read') end,"
    "read_verbs={},age=function() error('unexpected clock') end,build='build',"
    "ui_authorize=function() error('unexpected authority') end})"])
def test_explicit_false_profile_is_not_the_nil_default(constructor):
    r=runtime()
    ok,reason=r.eval("function() return pcall(function() return "+constructor+" end) end")()
    assert ok is False and "unknown fixed capacity profile" in reason
    assert r.eval("calls.prepare+calls.execute+calls.status+reads")==0

def test_constructor_performs_no_provider_or_read_calls():
    r=runtime()
    assert r.eval("calls.prepare+calls.execute+calls.status+reads")==0

def test_interactive_sdk_keeps_256_and_retains_first_terminal_id():
    r=runtime("interactive")
    r.execute("for i=1,256 do core.prepare(request(i));execute(i) end")
    assert r.eval("pcall(core.prepare,request(257))")[0] is False
    assert r.eval("execute(1).status")=="completed"
    assert r.eval("calls.execute")==256
    assert r.eval("core.capacity().requests_used")==256

def test_campaign_actual_sdk_router_all8192_terminal_records():
    r=runtime()
    r.execute("""
      for i=1,8192 do bound('action_prepare',packet(i));bound('action_execute',packet(i)) end
      sdkusage=core.capacity();routerusage=router.capacity()
    """)
    assert r.eval("calls.execute")==8192
    assert r.eval("sdkusage.requests_used")==8192
    assert r.eval("routerusage.requests_used")==8192
    assert 0<r.eval("sdkusage.retained_charge_bytes")<64*1024**2
    assert r.eval("pcall(bound,'action_prepare',packet(8193))")[0] is False
    assert r.eval("core.capacity().requests_used")==8192
    r.execute("bound('action_execute',packet(1))")
    assert r.eval("calls.execute")==8192

def test_unknown_preparations_reserve_before_provider_and_terminal_refunds_only_unused():
    r=runtime()
    r.execute("""
      accepted=0
      for i=1,8192 do
        if not pcall(core.prepare,request(i)) then blocked=i;break end
        accepted=accepted+1
      end
      before=core.capacity()
    """)
    accepted=r.eval("accepted")
    assert 256<accepted<8192
    assert r.eval("calls.prepare")==accepted
    assert r.eval("calls.execute")==0
    r.execute("execute(1);after=core.capacity()")
    assert r.eval("after.requests_used")==accepted
    assert 0<r.eval("after.retained_charge_bytes")<r.eval("before.retained_charge_bytes")
    r.execute("core.prepare(request(blocked))")
    assert r.eval("core.capacity().requests_used")==accepted+1
    assert r.eval("execute(1).status")=="completed"
    assert r.eval("calls.execute")==1

@pytest.mark.parametrize("status",["refused","failed","completed"])
def test_terminal_slot_refund_preserves_actual_result_and_duplicate_fault_cannot_regrow(status):
    r=runtime();r.globals().terminal_status=status
    r.execute("""
      done_saved=nil
      other=sdkFactory(profile,resolveCapacity)
      other.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
        session=function()return stamp.session end,
        invoke=function(phase,q,done)
          if phase=='prepare' then done({status='prepared',reason='',observations={}})
          else done_saved=done;done({status=terminal_status,reason='kept',observations={kept=string.rep('x',1000)}})end
        end})
      q=request(1);other.prepare(q);reserved=other.capacity().retained_charge_bytes
      other.execute(q.id,q.session,q.digest);settled=other.capacity().retained_charge_bytes
      done_saved({status='completed',reason='contradiction',observations={giant=string.rep('q',4096)}})
      final=other.status(q.id,q.session,q.digest);charged=other.capacity().retained_charge_bytes
    """)
    assert 0<r.eval("settled")<r.eval("reserved")
    assert r.eval("charged")==r.eval("settled")
    assert r.eval("final.status")=="unknown"
    assert r.eval("final.observations.action_core.protocol_fault") is True
    assert r.eval("#final.observations.kept")==1000

def test_oversized_posteffect_result_quarantines_with_reservation_and_no_retry():
    r=runtime()
    r.execute("""
      n=0;big=sdkFactory(profile,resolveCapacity)
      big.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
       session=function()return stamp.session end,invoke=function(phase,q,done)
        if phase=='prepare'then done({status='prepared',reason='',observations={}})
        else n=n+1;local o={};for i=1,5 do o[i]=string.rep('x',4096)end
          done({status='completed',reason='oversize',observations=o})end end})
      q=request(1);big.prepare(q);reserved=big.capacity().retained_charge_bytes
      first=big.execute(q.id,q.session,q.digest);second=big.execute(q.id,q.session,q.digest)
    """)
    assert r.eval("n")==1
    assert r.eval("first.status")=="unknown"
    assert r.eval("first.observations.action_core.protocol_fault") is True
    assert r.eval("big.capacity().retained_charge_bytes")==r.eval("reserved")

def test_dense_actual_wire_accepted_result_fits_reserved_campaign_slot():
    r=runtime()
    r.execute("""
      dense=sdkFactory(profile,resolveCapacity)
      dense.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
       session=function()return stamp.session end,invoke=function(phase,q,done)
        if phase=='prepare'then done({status='prepared',reason='',observations={}})
        else local o={};for i=1,500 do o[i]=false end
          result={status='completed',reason='',observations=o};assert(#codec.encode(result)<=4096);done(result)end end})
      q=request(1);dense.prepare(q);answer=dense.execute(q.id,q.session,q.digest)
    """)
    assert r.eval("answer.status")=="completed"
    assert r.eval("answer.observations.action_core.protocol_fault") is False

def test_interactive_preserves_large_legacy_result_that_campaign_refuses():
    r=runtime("interactive")
    r.execute("""
      legacy=sdkFactory()
      legacy.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
       session=function()return stamp.session end,invoke=function(phase,q,done)
        if phase=='prepare'then done({status='prepared',reason='',observations={}})
        else local o={};for i=1,400 do o[string.rep('0',125)..string.format('%03d',i)]=true end
          done({status='completed',reason='',observations=o})end end})
      q=request(1);legacy.prepare(q);answer=legacy.execute(q.id,q.session,q.digest)
    """)
    assert r.eval("answer.status")=="completed"

def test_preflight_is_copied_advisory_and_does_not_reset_or_reserve_history():
    r=runtime()
    r.execute("""
      a=core.preflight(10);b=router.preflight(10,120)
      a.requests_limit=999999;b.reads_limit=999999
    """)
    assert r.eval("a.fits and b.fits") is True
    assert r.eval("a.reserved or b.reserved") is False
    assert r.eval("core.capacity().requests_used+router.capacity().reads_used")==0
    assert r.eval("core.capacity().requests_limit")==8192
    assert r.eval("router.capacity().reads_limit")==65536

@pytest.mark.parametrize("key,value",[("sdk_history",8193),("sdk_history",1),
    ("sdk_result_bytes",8192),("sdk_retained_bytes",1),("revision","z"*64)])
def test_altered_trusted_resolver_shape_refuses_before_any_invocation(key,value):
    r=runtime();r.globals().key=key;r.globals().value=value
    assert r.eval("pcall(sdkFactory,'campaign_v1',function(n)local x=resolveCapacity(n);x[key]=value;return x end)")[0] is False
    assert r.eval("calls.prepare+calls.execute+reads")==0

def test_superseded_status_cannot_charge_or_replace_terminal_evidence():
    r=runtime()
    r.execute("""
      saved={};a=sdkFactory(profile,resolveCapacity)
      a.register({id='fixture',revision=string.rep('e',64),actions={doit=true},
        session=function()return stamp.session end,invoke=function(phase,q,done)
          if phase=='prepare'then done({status='prepared',reason='',observations={}})
          elseif phase=='execute'then done({status='running',reason='',observations={}})
          else saved[#saved+1]=done end end})
      q=request(1);a.prepare(q);a.execute(q.id,q.session,q.digest)
      a.status(q.id,q.session,q.digest);a.status(q.id,q.session,q.digest)
      saved[2]({status='completed',reason='final',observations={kept=true}})
      before=a.capacity().retained_charge_bytes
      saved[1]({status='running',reason='old',observations={large=string.rep('x',4096)}})
      answer=a.status(q.id,q.session,q.digest);after=a.capacity().retained_charge_bytes
    """)
    assert r.eval("answer.status")=="completed"
    assert r.eval("answer.observations.kept") is True
    assert r.eval("after")==r.eval("before")

def test_campaign_router_exact65536_read_nonce_boundary_without_eviction():
    r=runtime()
    r.execute("for i=1,65536 do bound('read_capture',string.format('%032x',i),'sample') end")
    assert r.eval("reads")==65536
    assert r.eval("pcall(bound,'read_capture',string.format('%032x',65537),'sample')")[0] is False
    assert r.eval("pcall(bound,'read_capture',string.format('%032x',1),'sample')")[0] is False
    assert r.eval("reads")==65536
    assert r.eval("router.capacity().reads_used")==65536

def test_interactive_router_read_nonce_boundary_is_unchanged():
    r=runtime("interactive")
    r.execute("for i=1,4096 do bound('read_capture',string.format('%032x',i),'sample') end")
    assert r.eval("pcall(bound,'read_capture',string.format('%032x',4097),'sample')")[0] is False
    assert r.eval("reads")==4096

def test_packet_change_cannot_reuse_quota_or_dispatch():
    r=runtime()
    r.execute("bound('action_prepare',packet(1));before=router.capacity().retained_charge_bytes")
    assert r.eval("pcall(bound,'action_execute',packet(1)..'x=s61')")[0] is False
    assert r.eval("calls.execute")==0
    assert r.eval("router.capacity().retained_charge_bytes")==r.eval("before")

def test_quota_charges_are_monotonic_for_retained_router_identities():
    r=runtime()
    r.execute("""
      before=router.capacity().retained_charge_bytes
      bound('action_prepare',packet(1));bound('action_execute',packet(1))
      bound('read_capture',string.format('%032x',1),'sample')
      after=router.capacity()
    """)
    assert r.eval("after.retained_charge_bytes")==r.eval("before+#packet(1)+64+64")
    assert r.eval("after.requests_used")==1 and r.eval("after.reads_used")==1

@pytest.mark.parametrize("bad",[-1,1.5,float("inf"),float("nan")])
def test_nonintegral_stage_cost_refuses(bad):
    r=runtime();r.globals().bad=bad
    assert r.eval("pcall(core.preflight,bad)")[0] is False
    assert r.eval("pcall(router.preflight,bad,0)")[0] is False
    assert r.eval("calls.prepare+reads")==0

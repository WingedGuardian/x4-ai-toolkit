"""Actual LuaJIT bootstrap with native callback stubs; no X4 calls."""
from pathlib import Path

import pytest

luajit = pytest.importorskip("lupa.luajit21", reason="bootstrap native FFI tests require LuaJIT")
UI = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui"


@pytest.fixture
def boot():
    lua = luajit.LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
        ffi=require('ffi'); ffi.cdef('typedef uint64_t UniverseID;')
        state={player=42,time=5,age=100,sends=0,clears=0,reads=0,disposals=0}
        callbacks={}; bb={}; requests={}
        held={ffi.cast('uint64_t (*)(void)',function() return ffi.new('uint64_t',state.player) end),
              ffi.cast('double (*)(void)',function() return state.age end)}
        C={GetPlayerID=held[1],GetCurrentGameTime=held[2]}
        native={
            RegisterEvent=function(name,fn) callbacks[#callbacks+1]={name=name,fn=fn} end,
            GetNPCBlackboard=function(id,name)
                assert(id==42 or id==43);state.reads=state.reads+1
                if state.switch_player then state.player=43 end
                return bb[name] end,
            SetNPCBlackboard=function(id,name,value)
                assert(id==42 and name=='$x4_toolkit_session_replies' and value==nil)
                state.clears=state.clears+1;bb[name]=nil end,
            AddUITriggeredEvent=function(screen,control,r)
                assert(screen=='X4ToolkitSession' and control=='handshake')
                state.sends=state.sends+1;requests[#requests+1]=r end,
            ConvertStringTo64Bit=function(id)
                if state.bad_conversion then return state.converted end
                return tonumber(id:match('^(%d+)ULL$')) end,
            getElapsedTime=function() return state.time end}
        REV=string.rep('a',64); BUILD=string.rep('b',64); GEN=string.rep('c',32)
        NONCE=string.rep('d',32); PROOF=string.rep('e',32); CHALLENGE=string.rep('f',32)
        modules={}
        modules.automation=function(env) router_env=env;return {test_env=env} end
        deps={ffi=ffi,C=C,native=native,modules=modules,revision=REV,build=BUILD,
              read=function(verb,args) return 'OK','read-result' end,
              read_verbs={probe=true},provider_revisions={test=string.rep('9',64)}}
        function ready()
            api.ui_state(PROOF);api.ui_authorize(PROOF,GEN)
            api.session.connect({mode='fresh',session=NONCE,challenge=CHALLENGE,generation=GEN},function(r) result=r end)
            bb['$x4_toolkit_session_state']={provider='x4-live-session-v1',revision=REV,
                session=NONCE,generation=GEN,world_token=NONCE,age=100}
            bb['$x4_toolkit_session_replies']={{kind='READY',provider='x4-live-session-v1',revision=REV,
                mode='fresh',session=NONCE,challenge=CHALLENGE,generation=GEN,world_token=NONCE,
                age=100,has_session=true,reason=''}}
            callbacks[#callbacks-1].fn()
        end
        function spec()
            return {id='test',revision=string.rep('9',64),actions={inspect=true},
                session=function() return NONCE end,
                invoke=function(phase,request,done)
                    state.invokes=(state.invokes or 0)+1
                    done({status='prepared',observations={},reason=''}) end}
        end
        function req()
            return {version=1,id=string.rep('1',32),session=NONCE,provider='test',action='inspect',
                arguments={},binding='test-binding',digest=string.rep('2',64)}
        end
    """)
    for name, filename in (("session", "live_session.lua"), ("actions", "live_actions.lua"), ("wire", "live_actionwire.lua")):
        lua.globals().modules[name] = lua.execute((UI / filename).read_text(encoding="utf-8"))
    factory = lua.execute((UI / "live_bootstrap.lua").read_text(encoding="utf-8"))
    lua.globals().factory = factory
    okay, api = lua.execute("return pcall(factory,deps)")
    assert okay, api
    lua.globals().api = api
    return lua, api, factory


def refused(lua, code, reason=None):
    okay, detail = lua.execute("return pcall(function() " + code + " end)")
    assert not okay
    if reason is not None:
        assert reason in str(detail)


def test_native_wrappers_and_fresh_age(boot):
    lua, api, _ = boot
    assert lua.globals().state.sends == 0
    lua.execute("ready()")
    assert lua.globals().result.kind == "READY"
    assert api.session.current().session == "d" * 32
    assert lua.globals().state.clears == 1
    assert api.router.test_env.age() == 100
    lua.globals().state.age = 123
    assert api.router.test_env.age() == 123


def test_campaign_profile_reaches_actual_sdk_and_router_and_counters_are_copied(boot):
    from x4validate import _livecapacity
    lua,_,factory=boot
    lua.globals().resolver=lua.execute(_livecapacity.lua_source())
    lua.globals().modules.automation=lua.execute((UI/'live_automation.lua').read_text(encoding='utf-8'))
    lua.execute("deps.capacity_profile='campaign_v1';deps.capacity_resolver=resolver")
    api=factory(lua.globals().deps)
    lua.globals().api=api
    lua.execute('ready()')
    value=api.capacity()
    assert value.scope=='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'
    assert value.sdk.requests_limit==8192 and value.router.reads_limit==65536
    assert value.sdk.profile==value.router.profile=='campaign_v1'
    value.sdk.requests_used=999
    assert api.capacity().sdk.requests_used==0
    api.dispose()
    assert lua.eval('pcall(api.capacity)')[0] is False


def test_ui_probe_does_not_adopt_and_ticket_survives_two_handshakes(boot):
    lua, api, _ = boot
    g = lua.globals()
    assert api.ui_state(g.PROOF).generation == ""
    assert g.state.sends == 0
    assert api.ui_authorize(g.PROOF, g.GEN)
    assert api.ui_authorize(g.PROOF, g.GEN)
    assert api.ui_state("1" * 32).generation == g.GEN


def test_unbound_ticket_stale_after_other_ticket_binds(boot):
    lua, api, _ = boot
    api.ui_state("1" * 32)
    api.ui_state("2" * 32)
    api.ui_authorize("2" * 32, lua.globals().GEN)
    refused(lua, "api.ui_authorize(string.rep('1',32),string.rep('3',32))", "superseded")


def test_vm_replacement_rejects_prior_ticket(boot):
    lua, api, factory = boot
    proof = lua.globals().PROOF
    api.ui_state(proof)
    replacement = factory(lua.globals().deps)
    lua.globals().replacement = replacement
    refused(lua, "replacement.ui_authorize(PROOF,GEN)", "not from this instance")
    refused(lua, "api.ui_state(string.rep('1',32))", "disposed")


@pytest.mark.parametrize("code", [
    "api.ui_authorize(PROOF,GEN)",
    "api.ui_state('bad')",
    "api.ui_state(PROOF);api.ui_state(PROOF)",
    "api.ui_state(PROOF);api.ui_authorize(PROOF,'bad')",
    "api.ui_state(PROOF);api.ui_authorize(PROOF,GEN);api.ui_authorize(PROOF,NONCE)",
])
def test_invalid_ui_proofs_do_not_send(boot, code):
    lua, _, _ = boot
    refused(lua, code)
    assert lua.globals().state.sends == 0


def test_direct_session_cannot_bypass_ui_ticket(boot):
    lua, api, _ = boot
    lua.execute("api.session.connect({mode='fresh',session=NONCE,challenge=CHALLENGE,generation=GEN},function(r) result=r end)")
    assert lua.globals().state.sends == 0
    assert lua.globals().result.kind == "UNAVAILABLE"
    assert api.session.current()[0] is None


def test_player_changes_prevent_queue_clear_and_ready(boot):
    lua, api, _ = boot
    lua.execute("state.switch_player=true;ready()")
    assert lua.globals().state.clears == 0
    assert api.session.current()[0] is None


def test_registered_provider_requires_allowed_source_and_current_session(boot):
    lua, api, _ = boot
    lua.execute("api.register_provider(spec())")
    result = api.registry.prepare(lua.globals().req())
    assert result.status == "unknown"
    assert lua.globals().state.invokes is None
    lua.execute("ready();r=req();r.id=string.rep('3',32)")
    assert api.registry.prepare(lua.globals().r).status == "prepared"
    assert lua.globals().state.invokes == 1


@pytest.mark.parametrize("code", [
    "s.id='unknown'", "s.revision=string.rep('8',64)", "s.dispose=true",
])
def test_provider_registration_refuses_unreviewed_or_malformed(boot, code):
    lua, _, _ = boot
    lua.execute("s=spec();" + code)
    refused(lua, "api.register_provider(s)")


def test_provider_revision_and_callbacks_copied_and_disposed(boot):
    lua, api, factory = boot
    lua.execute("s=spec();api.register_provider(s);"
                "api.register_cleanup('test',function() state.disposals=state.disposals+1 end);"
                "s.revision=string.rep('8',64);s.invoke=function() error('mutated callback') end;ready()")
    result = api.registry.prepare(lua.globals().req())
    assert result.status == "prepared"
    assert result.observations.action_core.provider_revision == "9" * 64
    factory(lua.globals().deps)
    assert lua.globals().state.disposals == 1
    api.dispose()
    assert lua.globals().state.disposals == 1


def test_old_callbacks_cannot_clear_replacement_queue(boot):
    lua, api, factory = boot
    replacement = factory(lua.globals().deps)
    lua.execute("callbacks[1].fn();callbacks[2].fn()")
    assert lua.globals().state.reads == 0
    assert lua.globals().state.clears == 0
    replacement.dispose()


def test_native_symbol_wrong_type_refused_before_replacing_old(boot):
    lua, api, factory = boot
    lua.execute("wrong=ffi.cast('int (*)(void)',function() return 42 end);C.GetPlayerID=wrong")
    refused(lua, "factory(deps)", "signature")
    assert api.ui_state("1" * 32).kind == "UI_STATE"


@pytest.mark.parametrize("code", [
    "C.GetPlayerID=ffi.cast('uint64_t (*)(void)',0)",
    "C.GetPlayerID=nil",
    "wrong=ffi.cast('int (*)(void)',function() return 5 end);C.GetCurrentGameTime=wrong",
])
def test_native_null_missing_and_age_return_refused(boot, code):
    lua, _, _ = boot
    lua.execute(code)
    refused(lua, "factory(deps)", "signature")


def test_parameter_abi_is_explicitly_unattested_not_a_false_signature_pass(boot):
    lua, _, factory = boot
    lua.execute("wrong_called=0;wrong=ffi.cast('uint64_t (*)(int)',function(x) "
                "wrong_called=wrong_called+1;return ffi.new('uint64_t',x) end);C.GetPlayerID=wrong")
    # Supported LuaJIT metadata omits the parameter list. Do not call this wrong
    # callback or pretend the inspector rejected it; disclose the actual boundary.
    candidate = factory(lua.globals().deps)
    assert candidate.abi_status().parameter_abi_attested is False
    assert candidate.abi_status().status == "SOURCE_DECLARED_PARAMETER_ABI_RUNTIME_UNVERIFIED"
    assert lua.globals().wrong_called == 0


def test_native_alias_wrong_type_refused():
    lua = luajit.LuaRuntime(unpack_returned_tuples=True)
    lua.execute("ffi=require('ffi');ffi.cdef('typedef double UniverseID;');"
                "f=function() end;deps={ffi=ffi,C={},revision=string.rep('a',64),build=string.rep('b',64),"
                "modules={session=f,actions=f,wire=f,automation=f},read=f,read_verbs={},"
                "native={RegisterEvent=f,GetNPCBlackboard=f,SetNPCBlackboard=f,"
                "AddUITriggeredEvent=f,ConvertStringTo64Bit=f,getElapsedTime=f}}")
    factory = lua.execute((UI / "live_bootstrap.lua").read_text(encoding="utf-8"))
    lua.globals().factory = factory
    refused(lua, "factory(deps)", "UniverseID")


def test_failed_provider_disposal_blocks_all_replacement_attempts(boot):
    lua, _, _ = boot
    lua.execute("api.register_provider(spec());api.register_cleanup('test',function() error('cannot retire') end)")
    refused(lua, "factory(deps)", "disposal failed")
    refused(lua, "factory(deps)", "disposal failed")
    assert len(lua.globals().callbacks) == 2


def test_source_build_and_read_dispatch_captured_at_construction(boot):
    lua, api, _ = boot
    lua.execute("deps.build=string.rep('f',64);deps.read=function() error('mutated') end")
    assert api.ui_state(lua.globals().PROOF).build == "b" * 64
    assert api.router.test_env.read("probe", lua.table()) == ("OK", "read-result")


def test_false_disposer_is_sticky_refusal_before_replacement(boot):
    lua, _, _ = boot
    lua.execute("api.register_provider(spec());api.register_cleanup('test',function() return false end)")
    refused(lua, "factory(deps)", "disposal failed")
    refused(lua, "factory(deps)", "disposal failed")
    assert len(lua.globals().callbacks) == 2


@pytest.mark.parametrize("value", [None, False, True, 0, "0", "0ULL", "0LL", "42ULL", 43, -42, 42.5, float("nan"), float("inf"), 9007199254740992])
def test_null_player_conversion_never_reads_or_clears(boot, value):
    lua, api, _ = boot
    lua.globals().state.bad_conversion = True
    lua.globals().state.converted = value
    lua.execute("ready()")
    assert lua.globals().state.reads == 0 and lua.globals().state.clears == 0
    assert api.session.current()[0] is None


def test_native_zero_player_conversion_never_reads_or_clears(boot):
    lua, api, _ = boot
    lua.execute("state.bad_conversion=true;state.converted=ffi.new('uint64_t',0);ready()")
    assert lua.globals().state.reads == 0 and lua.globals().state.clears == 0
    assert api.session.current()[0] is None

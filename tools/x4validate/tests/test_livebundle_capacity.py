"""Actual generation/Lua constructor controls. Synthetic natives, never a game run."""
import hashlib
import importlib.util
from pathlib import Path
import pytest
from lupa import LuaRuntime

ROOT=Path(__file__).resolve().parents[3]
def load(name):
    spec=importlib.util.spec_from_file_location("capacity_"+name,ROOT/"scripts"/(name+".py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
B=load("live_automation_bundle")
P=load("prepare_live_helper")

def sources():
    return {key:(ROOT/path).read_bytes() for key,path in B.SOURCES.items()}

@pytest.mark.parametrize("name",["interactive","campaign_v1"])
def test_actual_bundle_capacity_has_exact_source_and_emitted_resolver_identity(name):
    payload,receipt=B.build(sources(),"a"*64,capacity_profile=name)
    cap=receipt["capacity"]
    assert cap["profile"]==name
    assert cap["source_raw_sha256"]==hashlib.sha256(B.CAPACITY_SOURCE.read_bytes()).hexdigest()
    lua=LuaRuntime(unpack_returned_tuples=True);lua.execute(payload.decode())
    assert lua.globals().X4ToolkitAutomationBundle.capacity_profile==name
    assert B.verify(payload)==receipt["build"]
    assert len(receipt["modules"])==28
    assert receipt["runtime_verified"] is False

@pytest.mark.parametrize("name",[False,True,1,{},[],"campaign","",None])
def test_invalid_generation_selection_cannot_widen_profile(name):
    # None is the canonical resolver's documented interactive default.
    if name is None:
        assert B.build(sources(),"a"*64,capacity_profile=name)[1]["capacity"]["profile"]=="interactive"
    else:
        with pytest.raises(ValueError):B.build(sources(),"a"*64,capacity_profile=name)

def test_profile_changes_loaded_source_identity():
    a,_=B.build(sources(),"a"*64)
    b,_=B.build(sources(),"a"*64,capacity_profile="campaign_v1")
    assert B.verify(a)!=B.verify(b)

def synthetic():
    s={key:b"return function() return {} end" for key in B.MODULES}
    s["bootstrap"]=b"""return function(d)
      captured=d;constructed=(constructed or 0)+1
      return {session={current=function() return {session='test'} end},
        capacity=function() return {sdk={requests_used=17},router={reads_used=23},
          scope='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'} end,
        encode=function(value) encoded_value=value;return 'encoded' end,
        register_provider=function() end,dispose=function() disposed=(disposed or 0)+1;return true end}
    end"""
    s["nativeassembly"]=b"""return {
      read_verbs=function() return {} end,
      new=function(d) native_captured=d;return {
        capacity=function() return {requests_used=7} end,
        read=function() error('unexpected native read') end,dispose=function() return true end,
        provider_spec={id='x4-native-v1',revision=d.build,actions={},session=function() return 'test' end,invoke=function() end}}
      end}
    """
    return s

def start():
    payload,receipt=B.build(synthetic(),"a"*64,capacity_profile="campaign_v1")
    lua=LuaRuntime(unpack_returned_tuples=True);lua.execute(payload.decode())
    lua.execute("function deps() return {read=function() error('legacy read must not run') end,read_verbs={probe=true}} end")
    return lua,receipt

def test_generation_owned_profile_reaches_bootstrap_native_and_extension_before_attach():
    lua,receipt=start()
    lua.execute("""X4ToolkitAutomationBundle.register_extension{
      id='test',revision=string.rep('d',64),providers={test=string.rep('e',64)},
      attach=function(ctx)
        extension_context=ctx
        ctx.registry.register{id='test',revision=string.rep('e',64),actions={},
          session=function() return 'test' end,invoke=function() end,dispose=function() return true end}
        return {dispose=function() return true end}
      end}
      X4ToolkitAutomationBundle.new(deps())""")
    for owner in ["captured","native_captured","extension_context"]:
        assert lua.globals()[owner].capacity_profile=="campaign_v1"
        limits=lua.globals()[owner].capacity_resolver("campaign_v1")
        assert limits.revision==receipt["capacity"]["revision"]
        assert limits.sdk_history==8192
    assert lua.globals().extension_context.capacity_revision==receipt["capacity"]["revision"]

@pytest.mark.parametrize("mutation",[
    "d.capacity_profile='interactive'","d.capacity_profile=false",
    "d.capacity_resolver=function() return {} end"])
def test_caller_substitution_refuses_before_retiring_or_constructing(mutation):
    lua,_=start();lua.execute("X4ToolkitAutomationBundle.new(deps());d=deps();"+mutation)
    assert lua.eval("pcall(X4ToolkitAutomationBundle.new,d)")[0] is False
    assert lua.globals().constructed==1
    assert lua.globals().disposed is None

def test_fixed_capacity_read_routes_only_actual_counters_and_takes_no_args():
    lua,_=start();lua.execute("X4ToolkitAutomationBundle.new(deps())")
    status,payload=lua.execute("return captured.read('automation_capacity',{})")
    value=lua.globals().encoded_value
    assert status=="OK" and payload=='encoded'
    assert (value.sdk.requests_used,value.router.reads_used,value.native.requests_used)==(17,23,7)
    assert value.reserved is False and value.future_fit_verified is False
    assert lua.eval("pcall(captured.read,'automation_capacity',{reset=true})")[0] is False

def test_capacity_read_namespace_cannot_be_spoofed_by_legacy_reader():
    lua,_=start();lua.execute("d=deps();d.read_verbs.automation_capacity=true")
    assert lua.eval("pcall(X4ToolkitAutomationBundle.new,d)")[0] is False
    assert lua.globals().constructed is None

def test_complete_nine_extension_campaign_composition_shares_fixed_revision():
    mod=ROOT/"mods/x4_toolkit_helper"
    files,receipt=P.build((mod/"content.xml").read_bytes(),(mod/"ui.xml").read_bytes(),
                         sources(),extensions=tuple(P.EXTENSIONS),capacity_profile="campaign_v1")
    assert len(files)==22
    assert receipt["bundle"]["capacity"]["profile"]=="campaign_v1"
    for name in ["orbit","capture","roomentry","placement","surface"]:
        # Source contracts include the emitted profile in generated bytes.
        loader=P.EXTENSIONS[name][1]
        assert b"campaign_v1" in files[loader]
        assert receipt["bundle"]["capacity"]["revision"].encode() in files[loader]
    assert receipt["generators"]["_livecapacity.py"]==receipt["bundle"]["capacity"]["source_raw_sha256"]


def test_generated_bundle_capacity_crosses_actual_router_codec_boundpipe_and_readcapture(tmp_path):
    import json
    from test_livebound import Pipe,Link
    from x4validate._livebound import BoundPipe
    from x4validate._liveautomation import AutomationClient
    src=synthetic()
    for key in ("actions","wire","automation"):
        src[key]=(ROOT/B.SOURCES[key]).read_bytes()
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
        capacity=function() return {sdk=core.capacity(),router=router.capacity(),
          scope='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'} end,
        register_provider=function() end,dispose=function() return true end}
    end"""
    payload,receipt=B.build(src,"c"*64,capacity_profile="campaign_v1")
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""stamp={provider='x4-live-session-v1',revision=string.rep('c',64),
      session=string.rep('a',32),generation=string.rep('b',32),world_token=string.rep('d',32)}""")
    lua.execute(payload.decode())
    lua.execute("X4ToolkitAutomationBundle.new{read=function() error('unexpected legacy read') end,read_verbs={probe=true}}")
    pipe=Pipe(lua)
    client=AutomationClient(BoundPipe(pipe,Link(),receipt["build"]),tmp_path)
    reply=client.query("automation_capacity")
    assert reply.status=="OK"
    result=json.loads(reply.payload)
    assert result["sdk"]["requests_used"]==0
    assert result["router"]["reads_used"]==1
    assert result["sdk"]["profile"]==result["router"]["profile"]=="campaign_v1"
    assert result["native"]["requests_used"]==7
    assert result["reserved"] is False
    assert result["scope"]=='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'
    assert client.last_read["transport_complete"] and client.last_read["atomic"] is False
    assert len(reply.payload.encode())<=32000
    assert len(pipe.calls)>=2



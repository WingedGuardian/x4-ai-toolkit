"""Actual emitted resolvers/providers with synthetic natives; no capture/cutscene."""
import importlib.util
from pathlib import Path
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
import test_liveorbit as O
import test_livecapture_adapter as C

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('camera_capacity',ROOT/'tools/x4validate/x4validate/_livecapacity.py')
B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)

def capture_runtime(name):
    parts,receipt=C.P.prepare_parts(capacity_profile=name)
    r=LuaRuntime(unpack_returned_tuples=True);g=r.globals()
    g.real_sdk=r.execute(C.P.SDK.read_text(encoding="utf-8"))
    g.real_adapter=r.execute(parts['ui/x4_capture_adapter.lua'].decode())
    g.capacity=r.execute(B.lua_source());g.capacity_name=name
    g.revision=receipt['revision'];g.mdrevision=receipt['native_revision']
    # Isolate the provider's own ceiling behind a larger actual SDK ceiling;
    # an integrated interactive SDK can refuse earlier at256 requests.
    r.execute("""sdkfactory=function()return real_sdk('campaign_v1',capacity)end
adapterfactory=function(s,e)return real_adapter(s,e,{capacity_profile=capacity_name,capacity_resolver=capacity})end""")
    r.execute(C.SETUP)
    return r,receipt

@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_coupled_md_lua_metadata(name):
    profile=B.resolve(name)
    orbit,ro=O.g.prepare(name);cap,rc=C.P.prepare(capacity_profile=name)
    assert ro['capacity_profile']==rc['capacity_profile']==profile
    assert ro['provider_limits']==dict(records=profile['orbit_history'],calls=profile['orbit_calls'])
    assert rc['provider_limits']==dict(records=profile['capture_history'],calls=profile['capture_calls'])
    assert ro['md_contract']['limits']['records']==profile['orbit_history']
    assert rc['native']['contract']['max_records']==profile['capture_native_records']
    assert E.fromstring(orbit['md/X4ToolkitOrbit.xml']).xpath(
        "//do_if[contains(@value, '$Records.count lt %s')]"%profile['orbit_history'])
    assert E.fromstring(cap['md/X4ToolkitCapture.xml']).xpath(
        "//do_if[contains(@value, '$Count lt %s')]"%profile['capture_native_records'])
    assert not ro['runtime_verified'] and not rc['runtime_verified']

def test_interactive_default_and_profile_revision_separation():
    assert O.g.prepare()==O.g.prepare('interactive')
    assert C.P.prepare()==C.P.prepare(capacity_profile='interactive')
    for generator in (lambda n:O.g.prepare(n),lambda n:C.P.prepare(capacity_profile=n)):
        a,ra=generator('interactive');b,rb=generator('campaign_v1')
        assert a!=b and ra['revision']!=rb['revision']
        for bad in (False,{},'custom',123):
            with pytest.raises(ValueError):generator(bad)

@pytest.mark.parametrize('kind',['orbit','capture'])
@pytest.mark.parametrize('context',['missing','wrong_name','wrong_revision'])
def test_campaign_attach_rejects_mismatch_before_channel(kind,context):
    if kind=='orbit':
        files,receipt=O.g.prepare('campaign_v1');path='ui/live_orbit_extension.lua'
    else:
        files,receipt=C.P.prepare(capacity_profile='campaign_v1');path='ui/live_capture_extension.lua'
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("allocations=0;X4ToolkitAutomationBundle={register_extension=function(d)descriptor=d end}")
    r.execute(files[path].decode())
    r.globals().name='interactive' if context=='wrong_name' else 'campaign_v1'
    r.globals().revision='0'*64 if context=='wrong_revision' else receipt['capacity_profile']['revision']
    r.execute("ctx={capacity_profile=name,capacity_revision=revision,create_md_channel=function()allocations=allocations+1;error('unexpected allocation')end}")
    if context=='missing':r.execute('ctx.capacity_profile=nil;ctx.capacity_revision=nil')
    ok,why=r.eval('pcall(descriptor.attach,ctx)')
    assert ok is False and 'capacity' in why
    assert r.eval('allocations')==0

@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_orbit_exact_history_boundary_keeps_old_record(name):
    r,receipt=O.runtime(name);r.globals().limit=receipt['provider_limits']['records']
    r.execute("""
for i=1,limit do
 req=request('camera_observe','d',{});req.id=string.format('%032x',i);req.digest=string.format('%064x',i)
 if i==1 then first=req end
 call('prepare');assert(last.status=='prepared')
end
req=request('camera_observe','e',{});call('prepare');assert(last.status=='unknown')
assert(last.observations.capacity.history_used==limit and last.observations.capacity.history_limit==limit)
assert(sends==0)
req=first;call('execute');deliver(row('observed'));assert(last.status=='completed' and sends==1)
""")
    assert r.eval('last.observations.capacity.revision')==B.resolve(name)['revision']
    assert r.eval('last.observations.capacity.source_sha256')==B.resolve(name)['source_sha256']

@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_orbit_exact_transport_boundary_no_retry(name):
    r,receipt=O.runtime(name);r.globals().limit=receipt['provider_limits']['calls']
    r.execute("""
prep()
for i=2,limit do call('status');deliver(row('prepared'));assert(last.status=='prepared') end
assert(sends==limit)
call('status');assert(last.status=='unknown' and sends==limit)
call('status');assert(last.status=='unknown' and sends==limit)
assert(last.observations.capacity.calls_used==limit and last.observations.capacity.calls_limit==limit)
""")

@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_capture_release_pairs_exact_history_boundary(name):
    r,receipt=capture_runtime(name);r.globals().limit=receipt['provider_limits']['records']
    r.execute("""
for i=1,limit/2 do
 assert(prep().status=='prepared');assert(exec().status=='running')
 releaseprep();assert(exec().status=='completed')
 if i==1 then firstrelease=r end
end
before=#sent
assert(prep().status~='prepared' and #sent==before)
r=firstrelease;assert(status().status=='completed' and #sent==before)
assert(adapter.capacity().history_used==limit)
assert(status().observations.capacity.history_used==2) -- Terminal receipt remains historical.
""")
    assert r.eval('adapter.capacity().revision')==B.resolve(name)['revision']
    assert r.eval('adapter.capacity().source_sha256')==B.resolve(name)['source_sha256']


@pytest.mark.parametrize('kind',['orbit','capture'])
@pytest.mark.parametrize('field',['revision','source_sha256'])
def test_malformed_profile_identity_refuses_before_native(kind,field):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().resolver=r.execute(B.lua_source());r.globals().field=field
    r.execute("calls=0;function bad(n)local v=resolver(n);v[field]='wrong';return v end")
    path='live_orbit.lua' if kind=='orbit' else 'live_capture_adapter.lua'
    r.globals().factory=r.execute((ROOT/'mods/x4_toolkit_helper/ui'/path).read_text(encoding="utf-8"))
    if kind=='orbit':
        r.execute("""d={ffi={},capacity_profile='campaign_v1',capacity_resolver=bad,
          encode=function()end,resolve_issued=function()end,channel={}}
        for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
          d.channel[k]=function()calls=calls+1;error('native')end end""")
        result=r.eval('pcall(factory,d)')
    else:
        result=r.eval("pcall(factory,{}, {}, {capacity_profile='campaign_v1',capacity_resolver=bad})")
    assert result[0] is False and 'capacity source identity' in result[1]
    assert r.eval('calls')==0

@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_capture_exact_transport_boundary_no_resend(name):
    r,receipt=capture_runtime(name);r.globals().limit=receipt['provider_limits']['calls']
    r.execute("""
prep();exec()
for i=3,limit do status() end
assert(#sent==limit)
status();assert(#sent==limit)
status();assert(#sent==limit)
assert(adapter.capacity().calls_used==limit and adapter.capacity().calls_limit==limit)
""")

def test_capture_capacity_observations_fit_actual_wire_and_are_copied():
    r,_=capture_runtime('campaign_v1')
    r.globals().wirefactory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text(encoding="utf-8"))
    r.execute("""
prep();out=exec();w=wirefactory()
assert(#w.encode({payload=w.encode(out),reserved=string.rep('x',900)})<=4096)
assert(out.observations.capacity.history_used==1 and out.observations.capacity.calls_used==2)
assert(out.observations.capacity.revision==adapter.capacity().revision)
assert(#out.observations.capacity.revision==64 and #out.observations.capacity.source_sha256==64)
out.observations.capacity.history_limit=999999
assert(adapter.capacity().history_limit==4096)
""")

@pytest.mark.parametrize('kind',['orbit','capture'])
def test_missing_campaign_resolver_refuses_without_native_call(kind):
    r=LuaRuntime(unpack_returned_tuples=True);r.execute("calls=0")
    if kind=='orbit':
        r.globals().factory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_orbit.lua').read_text(encoding="utf-8"))
        r.execute("""d={ffi={},capacity_profile='campaign_v1',encode=function()end,resolve_issued=function()end,channel={}}
for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'}) do
d.channel[k]=function()calls=calls+1;error('native')end end""")
        result=r.eval('pcall(factory,d)')
    else:
        r.globals().factory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_capture_adapter.lua').read_text(encoding="utf-8"))
        result=r.eval("pcall(factory,{}, {}, {capacity_profile='campaign_v1'})")
    assert result[0] is False and r.eval('calls')==0

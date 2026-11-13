"""Actual room provider/SDK with fixed profiles and synthetic MD replies."""
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
import test_liveroomentry as R
from x4validate._livecapacity import resolve


@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_coupled_limits_preserve_room_enumeration(name):
    files,receipt=R.g.prepare(name);p=resolve(name)
    assert receipt['capacity_profile']==p
    assert receipt['provider_limits']==dict(records=p['roomentry_records'],calls=p['roomentry_calls'])
    assert receipt['md_contract']['limits']['records']==p['roomentry_records']
    xml=E.fromstring(files['md/X4ToolkitRoomEntry.xml'])
    assert xml.xpath("//do_if[contains(@value, '$Records.count lt %s')]"%p['roomentry_records'])
    for k,v in dict(room_candidates=16,preview_navcontexts=16,rooms_per_navcontext=64,replies=64).items():
        assert receipt['md_contract']['limits'][k]==v


@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_exact_history_boundary_counts_discovery_attempts(name):
    r,receipt=R.runtime(capacity_profile=name);r.globals().limit=receipt['provider_limits']['records']
    r.execute("""
req.arguments.selector='dynamicrooms'
for i=1,limit do
 req.id=string.format('%032x',i);req.digest=string.format('%064x',i)
 assert(registry.prepare(req).status=='preparing');deliver(row('refused'))
end
assert(sends==limit)
req.id=string.rep('f',32);req.digest=string.rep('f',64)
v=registry.prepare(req);assert(v.status=='unknown' and sends==limit)
assert(v.observations.capacity.history_used==limit and v.observations.capacity.history_limit==limit)
assert(v.observations.capacity.calls_used==limit)
""")


@pytest.mark.parametrize('name',['interactive','campaign_v1'])
def test_exact_transport_boundary_no_replay(name):
    r,receipt=R.runtime(capacity_profile=name);r.globals().limit=receipt['provider_limits']['calls']
    r.execute("""
assert(registry.prepare(req).status=='preparing');deliver(row('prepared'))
-- Since aba2ed7b, status() of a prepared request reports the arrived ack and sends nothing,
-- so the transport calls are spent by status reads of a RUNNING (dispatched) request.
local before=sends;local v=status();assert(sends==before and v.status=='prepared')
run();deliver(row('dispatched'))
for i=sends+1,limit do v=status();assert(v.status=='running');deliver(row('dispatched'))end
assert(sends==limit);v=status();assert(v.status=='unknown' and sends==limit)
v=status();assert(v.status=='unknown' and sends==limit)
assert(v.observations.capacity.calls_used==limit)
""")


@pytest.mark.parametrize('context',['missing','wrong_name','wrong_revision','false'])
def test_campaign_mismatch_before_channel(context):
    files,receipt=R.g.prepare('campaign_v1');r=LuaRuntime(unpack_returned_tuples=True)
    r.execute('allocations=0;X4ToolkitAutomationBundle={register_extension=function(d)descriptor=d end}')
    r.execute(files['ui/live_roomentry_extension.lua'].decode())
    r.globals().revision=receipt['capacity_profile']['revision']
    r.execute("ctx={capacity_profile='campaign_v1',capacity_revision=revision,create_md_channel=function()allocations=allocations+1;error('allocated')end}")
    r.execute({'missing':'ctx.capacity_profile=nil;ctx.capacity_revision=nil','wrong_name':"ctx.capacity_profile='interactive'",
        'wrong_revision':"ctx.capacity_revision=string.rep('0',64)",'false':'ctx.capacity_profile=false'}[context])
    ok,reason=r.eval('pcall(descriptor.attach,ctx)')
    assert ok is False and 'capacity' in reason and r.eval('allocations')==0


@pytest.mark.parametrize('bad',[False,{},'custom',256])
def test_unknown_generator_profile_refuses(bad):
    with pytest.raises(ValueError):R.g.prepare(bad)


def test_default_remains_interactive_distinct_from_campaign():
    assert R.g.prepare()==R.g.prepare('interactive')
    assert R.g.prepare()[1]['revision']!=R.g.prepare('campaign_v1')[1]['revision']

"""Actual provider fixed-profile history; fake adapters never execute game calls."""
import pytest
from test_livenativeproviders import runtime
from x4validate._livecapacity import lua_source,resolve


@pytest.mark.parametrize('profile,limit',[('interactive',128),('campaign_v1',512)])
def test_fixed_native_history_and_no_eviction(runtime,profile,limit):
    runtime.globals().capacity=runtime.execute(lua_source())
    runtime.execute("deps.capacity_profile='"+profile+"';deps.capacity_resolver=capacity;setup()")
    # Direct provider prepare boundary, so SDK capacity is not substituted for this limit.
    runtime.execute("""
      function prep_id(i)
        local r=req('camera.view',{target='target'});r.id=string.format('%032x',i);r.digest=string.format('%064x',i)
        adapter.spec.invoke('prepare',r,function(v)last=v end)
      end
    """)
    runtime.execute('for i=1,'+str(limit)+' do prep_id(i);assert(last.status=="prepared")end')
    runtime.execute('prep_id('+str(limit+1)+')')
    assert runtime.eval('last.status')=='refused'
    value=runtime.eval('adapter.capacity()')
    assert value['requests_used']==value['requests_limit']==limit
    assert value['revision']==resolve(profile)['revision'] and not value['eviction']
    assert runtime.eval('writes')==0


@pytest.mark.parametrize('profile', ['false','{}','512',"'arbitrary'"])
def test_invalid_profile_refuses_before_native(runtime,profile):
    runtime.execute('deps.capacity_profile='+profile)
    assert runtime.eval('pcall(factory,deps)')[0] is False
    assert runtime.eval('prepares')==runtime.eval('writes')==0


def test_campaign_requires_generated_resolver(runtime):
    runtime.execute("deps.capacity_profile='campaign_v1'")
    assert runtime.eval('pcall(factory,deps)')[0] is False


def test_legacy_default_remains_128(runtime):
    value=runtime.eval('adapter.capacity()')
    assert value['profile']=='interactive' and value['requests_limit']==128
    assert value['revision']=='UNBOUND_LEGACY_INTERACTIVE'


def test_capacity_copy_cannot_change_owner(runtime):
    runtime.execute("v=adapter.capacity();v.requests_limit=999999;v.requests_used=99")
    assert runtime.eval('adapter.capacity().requests_limit')==128
    assert runtime.eval('adapter.capacity().requests_used')==0


def test_changed_generation_refuses_capacity_read(runtime):
    runtime.execute("adapter.capacity();stamp.generation=string.rep('e',32)")
    assert runtime.eval('pcall(adapter.capacity)')[0] is False

"""Source-budget and UNKNOWN controls; never screenshot or query a game."""
import copy
import hashlib
import json
import pytest
from x4validate import _livecaptureplan as P


def plan(**changes):
    v={k:0 for k in P.COUNTS};v.update({k:None for k in P.BYTES});v.update(changes);return v


def items(value):return {r['name']:r for r in value['items']}


def test_missing_current_usage_is_unknown_never_zero_or_pass():
    result=P.evaluate(plan(captures=388,capture_calls=1552,read_calls=776,image_bytes_each=4*1024**2,camera_sample_bytes_each=4096))
    assert result['status']=='NOT_READY' and not result['runtime_accepted']
    assert all(r['used'] is None for r in result['items'])
    assert items(result)['sdk_requests']['planned']==776
    assert items(result)['retained_image_bytes']['planned']==388*4*1024**2
    assert items(result)['retained_camera_bytes']['planned']==388*8192


def test_unknown_bytes_remain_unknown():
    r=items(P.evaluate(plan(captures=1,capture_calls=4,read_calls=2)))
    assert r['retained_image_bytes']['planned'] is None and r['retained_image_bytes']['status']=='UNKNOWN'
    assert r['retained_camera_bytes']['planned'] is None


def test_camera_actual_byte_ceiling_cannot_be_raised_by_count():
    r=items(P.evaluate(plan(captures=388,capture_calls=1552,read_calls=776,camera_sample_bytes_each=128*1024)))
    assert r['retained_camera_bytes']['status']=='EXCEEDS_LIMIT'


def test_dynamic_discovery_distinct_from_selected_rooms():
    r=items(P.evaluate(plan(room_entries=240,room_discoveries=20,room_calls=500)))
    assert r['room_provider_requests']['planned']==260 and r['room_provider_requests']['status']=='EXCEEDS_LIMIT'
    assert r['room_md_selected_records']['planned']==240
    assert r['sdk_requests']['planned']==260


def test_future_observation_costs_lua_sdk_calls_but_no_md_operation_records():
    r=items(P.evaluate(plan(capture_observations=2,room_observations=3,orbit_observations=4,
                           capture_calls=2,room_calls=3,orbit_calls=4)))
    assert r['sdk_requests']['planned']==9
    assert r['capture_provider_requests']['planned']==r['capture_transport_calls']['planned']==2
    assert r['room_provider_requests']['planned']==r['room_transport_calls']['planned']==3
    assert r['orbit_requests']['planned']==r['orbit_transport_calls']['planned']==4
    assert all(r[k]['planned']==0 for k in ('capture_md_records','room_md_selected_records','orbit_md_records'))


@pytest.mark.parametrize('field',['capture_observations','room_observations'])
def test_old_plan_cannot_default_missing_observation_cost_to_zero(field):
    p=plan();p.pop(field)
    with pytest.raises(ValueError,match='exact stage plan'):P.evaluate(p)


@pytest.mark.parametrize('field',['capture_observations','room_observations'])
def test_observation_transport_cost_cannot_be_omitted(field):
    with pytest.raises(ValueError,match='fixed operation minimum'):P.evaluate(plan(**{field:1}))


def test_clip_128_count_does_not_guarantee_byte_fit():
    r=items(P.evaluate(plan(clips=128,clip_bytes_each=20*1024**2)))
    assert r['clip_records']['planned']==128 and r['clip_records']['status']=='UNKNOWN'
    assert r['retained_clip_bytes']['status']=='EXCEEDS_LIMIT'
    assert r['clip_pre_effect_reservation']['planned']==127*20*1024**2+512*1024**2


@pytest.mark.parametrize('change',[{'captures':True},{'clips':-1},{'captures':1,'capture_calls':3},
    {'room_entries':2,'room_calls':3},{'native_requests':1},{'image_bytes_each':257*1024**2},
    {'camera_sample_bytes_each':128*1024+1},{'clip_bytes_each':512*1024**2+1},
    {'captures':1,'capture_calls':4,'read_calls':0},{'captures':1,'capture_calls':4,'read_calls':1}])
def test_invalid_or_underdeclared_plan_refuses(change):
    with pytest.raises(ValueError):P.evaluate(plan(**change))


def test_arbitrary_measured_host_mapping_refused():
    with pytest.raises(ValueError,match='actual capture'):P.evaluate(plan(),capture={'records_used':0})
    with pytest.raises(ValueError,match='actual clip'):P.evaluate(plan(),clip={'retained_bytes':0})


def frozen(tmp):
    source=tmp/'source';source.write_bytes(b'fixed source')
    profile=P.resolve('campaign_v1')
    receipt={'capacity_profile':profile,'source_sha256':{str(source):hashlib.sha256(source.read_bytes()).hexdigest()},
        'provider_limits':{'records':profile['capture_history'],'calls':profile['capture_calls']},
        'native':{'contract':{'max_records':profile['capture_native_records']}}}
    path=tmp/'receipt.json';path.write_text(json.dumps(receipt))
    return source,path,receipt,{'capture':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}


def test_exact_receipt_limits_only_bind_static_source_not_live_usage(tmp_path):
    _,_,_,binding=frozen(tmp_path);r=P.evaluate(plan(),frozen_receipts=binding)
    i=items(r)['capture_provider_requests']
    assert i['source_bound'] and i['used'] is None and i['status']=='UNKNOWN'


def test_receipt_source_drift_refused(tmp_path):
    source,_,_,binding=frozen(tmp_path);source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='source changed'):P.evaluate(plan(),frozen_receipts=binding)


@pytest.mark.parametrize('field',['capacity_profile','provider_limits','native'])
def test_semantic_receipt_change_refused_even_with_rehashed_bytes(tmp_path,field):
    _,path,r,binding=frozen(tmp_path);r[field]={};path.write_text(json.dumps(r))
    binding['capture']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):P.evaluate(plan(),frozen_receipts=binding)


def test_plan_and_profile_copies_do_not_share_mutable_state():
    p=plan();r=P.evaluate(p);r['capacity_profile']['host_capture_records']=1;r['plan']['captures']=12
    assert p['captures']==0 and P.evaluate(p)['capacity_profile']['host_capture_records']==2048


def evidence(tmp,bad=None):
    from test_liveevidence import setup
    from x4validate._livecapacity import lua_source
    r,_,_,_,e=setup(tmp)
    r.globals().capacity=r.execute(lua_source())
    r.execute("""
cap=capacity('campaign_v1');core=coreFactory('campaign_v1',capacity)
router=routerFactory{capacity_profile='campaign_v1',capacity_resolver=capacity,
 session=session,wire=wire,registry=core,build='12345678',ui_authorize=function()return false end,
 age=function()return nativeage end,read_verbs={automation_capacity=true},read=function()
 local out={sdk=core.capacity(),router=router.capacity(),native={profile=cap.name,revision=cap.revision,
  provider_revision=string.rep('f',64),requests_used=3,requests_limit=cap.native_history,
  eviction=false,future_fit_verified=false,scope='RETAINED_NATIVE_PROVIDER_REQUEST_COUNT'},
  scope='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS',reserved=false,future_fit_verified=false}
 if mutate then mutate(out)end
 return 'OK',wire.encode(out)
end}
""")
    if bad:r.execute('mutate=function(v)'+bad+' end')
    return e,e.collect('automation_capacity')


def test_actual_bound_counter_read_keeps_sample_tier(tmp_path):
    result=P.evaluate(plan(),native_evidence=evidence(tmp_path),expected_build='12345678',
        expected_native_revision='f'*64,synthetic=True)
    rows=items(result)
    assert rows['native_requests']['used']==3 and rows['native_requests']['usage_tier']=='SYNTHETIC'
    assert rows['sdk_requests']['used']==0 and rows['router_reads']['used']>=1
    assert result['native_current_usage']=='SAMPLED_BOUND_COUNTERS'
    assert result['status']=='NOT_READY' and result['native_evidence']['atomic'] is False
    assert rows['capture_md_records']['used'] is None


@pytest.mark.parametrize('bad',["v.sdk.requests_used=true","v.sdk.requests_limit=99999",
    "v.router.revision=string.rep('0',64)","v.router.retained_charge_bytes=-1",
    "v.native.provider_revision=string.rep('0',64)","v.reserved=true"])
def test_malformed_bound_counter_refuses(tmp_path,bad):
    with pytest.raises(ValueError):P.evaluate(plan(),native_evidence=evidence(tmp_path,bad),
        expected_build='12345678',expected_native_revision='f'*64,synthetic=True)


def test_synthetic_handle_cannot_become_production_usage(tmp_path):
    with pytest.raises(ValueError,match='synthetic'):P.evaluate(plan(),native_evidence=evidence(tmp_path),
        expected_build='12345678',expected_native_revision='f'*64)


def test_changed_identity_refuses_historical_native_counters(tmp_path):
    e,h=evidence(tmp_path);e.client.bound.link.value['ui_generation']='e'*32
    with pytest.raises(ValueError,match='identity'):P.evaluate(plan(),native_evidence=(e,h),
        expected_build='12345678',expected_native_revision='f'*64,synthetic=True)


def test_actual_generated_capacity_route_to_read_evidence_to_planner(tmp_path):
    import ast
    from pathlib import Path
    import test_livebundle_capacity as B
    from test_livebound import Pipe
    from test_liveevidence import Link,IDENTITY
    from x4validate._livebound import BoundPipe
    from x4validate._liveautomation import AutomationClient
    from x4validate._liveevidence import ReadEvidence
    # Reuse the exact owned native-plumbing fixture, with actual generated bundle,
    # SDK, router, codec, pages and host collector. Native counter data is synthetic.
    src=B.synthetic()
    for key in ('actions','wire','automation'):src[key]=(B.ROOT/B.B.SOURCES[key]).read_bytes()
    tree=ast.parse(Path(B.__file__).read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='test_generated_bundle_capacity_crosses_actual_router_codec_boundpipe_and_readcapture')
    src['bootstrap']=next(ast.literal_eval(n.value)for n in ast.walk(fn) if isinstance(n,ast.Assign)
        and isinstance(n.value,ast.Constant) and isinstance(n.value.value,bytes))
    src['nativeassembly']=b"""return {read_verbs=function()return {}end,new=function(d)
      local cap=d.capacity_resolver(d.capacity_profile)
      return {capacity=function()return {profile=cap.name,revision=cap.revision,provider_revision=d.build,
        scope='RETAINED_NATIVE_PROVIDER_REQUEST_COUNT',requests_used=7,requests_limit=cap.native_history,
        eviction=false,future_fit_verified=false}end,read=function()error('no native read')end,
        dispose=function()return true end,provider_spec={id='x4-native-v1',revision=d.build,
          actions={},session=function()return stamp.session end,invoke=function()error('no execute')end}}
    end}"""
    payload,receipt=B.B.build(src,'c'*64,capacity_profile='campaign_v1')
    lua=B.LuaRuntime(unpack_returned_tuples=True)
    stamp={k:IDENTITY[k]for k in ('provider','revision','session','world_token')};stamp['generation']=IDENTITY['ui_generation']
    lua.globals().stamp=lua.table_from(stamp);lua.execute(payload.decode())
    lua.execute("X4ToolkitAutomationBundle.new{read=function()error('no legacy read')end,read_verbs={probe=true}}")
    client=AutomationClient(BoundPipe(Pipe(lua),Link(),receipt['build']),tmp_path)
    e=ReadEvidence(client,synthetic=True);h=e.collect('automation_capacity')
    value=P.evaluate(plan(),native_evidence=(e,h),expected_build=receipt['build'],
        expected_native_revision=receipt['build'],synthetic=True)
    assert items(value)['native_requests']['used']==7
    assert items(value)['sdk_requests']['used']==0 and items(value)['router_reads']['used']==1
    assert value['native_evidence']['evidence_tier']=='SYNTHETIC' and value['status']=='NOT_READY'

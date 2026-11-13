"""Actual Lua providers/router/codec and host journals, synthetic MD/native only."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
import test_livecapacity_observation as N
from x4validate._liveusagesamples import CapacitySamples
from x4validate._liveautomation import AutomationClient
from x4validate._livebound import BoundPipe
from x4validate._livepipe import Reply
from x4validate._livesession import SessionLink
from x4validate._livepeerprofile import ProfilePeer


def setup(tmp,kind='capture'):
    rt,_=N.runtime(kind)
    rt.globals().wirefactory=rt.execute((N.O.ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text())
    rt.globals().routerfactory=rt.execute((N.O.ROOT/'mods/x4_toolkit_helper/ui/live_automation.lua').read_text())
    rt.execute("""wire=wirefactory();router=routerfactory{session={current=function()return stamp end},wire=wire,
      registry=registry,build='12345678',age=function()return 5 end,ui_authorize=function()return false end,
      read_verbs={},read=function()error('no reads')end}""")
    s={k:rt.eval('stamp.'+k)for k in ('provider','revision','session','generation','world_token')}
    identity={**s,'ui_generation':s.pop('generation'),'world_lineage':'UNKNOWN','load_generation':1,
              'peer':{'pid':42,'creation_filetime':'1234','image':'C:/synthetic/X4.exe'}}
    identity.pop('generation',None)
    class Peer(ProfilePeer):
        def __init__(self):self.profile_id='123'
        def __call__(self):return copy.deepcopy(identity['peer'])
    class Link(SessionLink):
        def __init__(self):self.peer=Peer()
        def identity(self):return copy.deepcopy(identity)
    class Pipe:
        def __init__(self):self.seq=0;self.calls=[];self.deliver=lambda:rt.execute('deliver_sample()')
        def ask(self,verb,*args):
            assert verb=='bound';self.seq+=1;self.calls.append(args[5])
            raw=rt.globals().router.bound(rt.table_from(args))
            if args[5]=='action_execute':self.deliver()
            return Reply(self.seq,'OK',raw)
    pipe=Pipe();client=AutomationClient(BoundPipe(pipe,Link(),'12345678'),tmp)
    sources={}
    for k,g in [('orbit',N.O.g.prepare),('capture',N.C.C.P.prepare),('roomentry',N.R.g.prepare)]:
        _,receipt=g(capacity_profile='campaign_v1');path=tmp/(k+'.json')
        raw=json.dumps(receipt,sort_keys=True).encode();path.write_bytes(raw)
        sources[k]={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
    return dict(rt=rt,identity=identity,pipe=pipe,client=client,sources=sources,
                sampler=CapacitySamples(client,sources,'12345678',synthetic=True))


def completed(c,kind):
    h=c['sampler'].start(kind)
    assert c['sampler'].receipt(h)['status']=='prepared'
    assert c['sampler'].execute(h)['status'] in ('running','unknown','completed')
    r=c['sampler'].status(h);assert r['status']=='completed'
    return h,r


@pytest.mark.parametrize('kind',['capture','orbit','roomentry'])
def test_actual_sample_journal_reference_and_terminal_status_is_historical(tmp_path,kind):
    c=setup(tmp_path,kind);h,r=completed(c,kind)
    assert r['md']['records_used']==11 and r['lua']['history_used']==1 and r['lua']['calls_used']==1
    assert not r['current_counts_verified'] and r['evidence_tier']=='SYNTHETIC'
    assert r['native_observation']=='RETAINED_SAMPLE_NOT_FRESH_STATUS'
    before=list(c['pipe'].calls);again=c['sampler'].status(h)
    assert c['pipe'].calls==before and again['journal']==r['journal']
    assert again['host_sample_age_seconds']>=r['host_sample_age_seconds']
    with pytest.raises(ValueError,match='already consumed'):c['sampler'].execute(h)
    assert c['pipe'].calls==before
    h2=c['sampler'].start(kind);assert h2 is not h


def test_caller_dict_or_foreign_opaque_handle_refused(tmp_path):
    c=setup(tmp_path)
    with pytest.raises(ValueError,match='opaque'):c['sampler'].receipt(object())
    with pytest.raises(TypeError):c['sampler'].receipt({'qualified':True})


@pytest.mark.parametrize('field,value',[('session','f'*32),('ui_generation','f'*32),('world_token','new'),('load_generation',2)])
def test_world_transition_never_relabels_old_sample(tmp_path,field,value):
    c=setup(tmp_path);h,_=completed(c,'capture');c['identity'][field]=value
    with pytest.raises(ValueError,match='identity'):c['sampler'].receipt(h)
    assert c['sampler'].fault


def test_journal_tamper_refuses_original_hash(tmp_path):
    c=setup(tmp_path);h,r=completed(c,'capture');Path(r['journal']['path']).write_bytes(b'{}')
    with pytest.raises((ValueError,RuntimeError)):c['sampler'].receipt(h)


def test_existing_journal_rejects_later_sdk_fault_before_old_sample_reuse(tmp_path):
    from x4validate._liveactions import ActionJournal,ActionUncertain
    c=setup(tmp_path);h,r=completed(c,'capture')
    j=ActionJournal(Path(r['journal']['path']).parent)
    original=j._history()[-1]
    j._record(original)  # Identical terminal observations remain historical evidence.
    assert c['sampler'].receipt(h)['journal']==r['journal']
    fault=copy.deepcopy(original);fault['status']='unknown';fault['reason']='protocol fault'
    fault['observations']['action_core']['protocol_fault']=True
    with pytest.raises(ActionUncertain,match='terminal outcome changed'):j._record(fault)
    assert list(j.path.glob('contradiction-*.json'))
    with pytest.raises(ActionUncertain,match='corrupt or inconsistent'):c['sampler'].receipt(h)


def test_source_receipt_tamper_refuses(tmp_path):
    c=setup(tmp_path);h,_=completed(c,'capture')
    Path(c['sources']['capture']['path']).write_bytes(b'{}')
    with pytest.raises(ValueError,match='hash'):c['sampler'].receipt(h)


def test_missing_native_source_revision_refuses_constructor(tmp_path):
    c=setup(tmp_path);p=Path(c['sources']['capture']['path']);r=json.loads(p.read_bytes());r.pop('native_revision')
    raw=json.dumps(r).encode();p.write_bytes(raw);c['sources']['capture']['sha256']=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError,match='revision'):CapacitySamples(c['client'],c['sources'],'12345678',synthetic=True)


def test_synthetic_client_cannot_claim_production(tmp_path):
    c=setup(tmp_path)
    with pytest.raises(RuntimeError):CapacitySamples(c['client'],c['sources'],'12345678')


def test_wrong_bundle_before_sampling(tmp_path):
    c=setup(tmp_path)
    with pytest.raises(ValueError,match='build'):CapacitySamples(c['client'],c['sources'],'00000000',synthetic=True)


def test_sampler_history_uses_selected_sdk_bound(tmp_path):
    c=setup(tmp_path)
    assert c['sampler'].profile['sdk_history']==8192


def setup_all(tmp):
    c=setup(tmp);r=c['rt']
    r.execute("""channels={};last_screen='capture';old_send=env.send
      env.send=function(p,b)last_screen='capture';return old_send(p,b)end
      ffi=require('ffi');X4ToolkitAutomationBundle={register_extension=function(d)descriptor=d end}
      function attach_usage()
        descriptor.attach{ffi=ffi,registry=registry,encode=wire.encode,capacity_profile=cap_profile,
          capacity_revision=cap_revision,resolve_issued=function()error('no target for read')end,
          on_dispose=function()end,create_md_channel=function(config)
            local slot={};channels[config.screen]=slot
            return {public_current=function()return stamp end,now=function()return now end,
              subscribe=function(fn)slot.drain=fn end,arm_tick=function()end,component_id=function()error('no target')end,
              dispose=function()return true end,read=function()return slot.queue end,clear=function()slot.queue=nil end,
              send=function(p,b)slot.sent=b;slot.phase=p;last_screen=config.screen end}
          end}
      end""")
    for generator,path in [(N.O.g.prepare,'ui/live_orbit_extension.lua'),(N.R.g.prepare,'ui/live_roomentry_extension.lua')]:
        files,_=generator('campaign_v1');r.execute(files[path].decode());r.execute('attach_usage()')
    r.globals().allcaps=r.execute(N.C.B.lua_source())
    r.execute("""
      function deliver_all()
        if last_screen=='capture'then return deliver_sample()end
        local slot=channels[last_screen];local w=slot.sent;local orbit=last_screen=='X4ToolkitOrbit'
        local p=allcaps('campaign_v1')
        local v={kind=orbit and 'ORBIT' or 'ROOM_CAPACITY',revision=w.revision,id=w.id,digest=w.digest,
          call=w.call,phase=slot.phase,session=w.session,generation=w.generation,world_token=w.world_token,
          public_revision=w.public_revision,status='observed',reason='READ_ONLY_CAPACITY_SAMPLE',age=5,
          md_used=11,md_limit=orbit and p.orbit_history or p.roomentry_records,md_busy=false,md_queued=0,
          md_queue_limit=64,capacity_profile=p.name,capacity_revision=p.revision}
        if orbit then
          v.consumed=false;v.dispatched=false;v.handle='';v.stopped=false;v.stop_requested=false
          v.target=0;v.yaw=0;v.pitch=0;v.zoom=1;v.external=false;v.fullscreen=false
        end
        slot.queue={v};slot.drain()
      end
      router=routerfactory{session={current=function()return stamp end},wire=wire,registry=registry,
        build='12345678',age=function()return 5 end,ui_authorize=function()return false end,
        capacity_profile='campaign_v1',capacity_resolver=allcaps,read_verbs={automation_capacity=true},
        read=function(verb,args)
          assert(verb=='automation_capacity' and #args==0);local p=allcaps('campaign_v1')
          return 'OK',wire.encode{sdk=registry.capacity(),router=router.capacity(),
            native={profile=p.name,revision=p.revision,provider_revision=string.rep('7',64),requests_used=0,
              requests_limit=p.native_history,eviction=false,future_fit_verified=false,scope='RETAINED_NATIVE_PROVIDER_REQUEST_COUNT'},
            scope='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS',reserved=false,future_fit_verified=false}
        end}
    """)
    c['pipe'].deliver=lambda:r.execute('deliver_all()')
    return c


def test_fixed_global_capture_follows_actual_three_provider_samples(tmp_path):
    from x4validate._liveevidence import ReadEvidence
    from x4validate._livecaptureplan import evaluate
    from test_livecaptureplan import plan,items
    c=setup_all(tmp_path);s=c['sampler'];e=ReadEvidence(c['client'],synthetic=True)
    with pytest.raises(ValueError,match='three completed'):s.capture_global_usage(e)
    handles={k:completed(c,k)[0]for k in ('capture','orbit','roomentry')}
    h=s.capture_global_usage(e);assert s.global_evidence(handles)==(e,h)
    result=evaluate(plan(),provider_samples=(s,handles),frozen_receipts=c['sources'],
                    expected_build='12345678',expected_native_revision='7'*64,synthetic=True)
    assert result['global_sample_after_own_provider_samples'] and result['status']=='NOT_READY'
    assert items(result)['sdk_requests']['used']==3
    assert items(result)['capture_md_records']['used']==items(result)['room_md_selected_records']['used']==11
    assert items(result)['orbit_md_records']['used']==11
    assert items(result)['room_provider_requests']['used']==1
    assert 'PRIVATE_PROVIDER_DEMANDS_AND_FOREIGN_CONCURRENCY_NOT_QUALIFIED'in result['blockers']
    c['client'].status(s.records[handles['capture']]['request']['id'])
    with pytest.raises(ValueError,match='later known action'):s.global_evidence(handles)


def test_new_sample_invalidates_own_ordered_checkpoint(tmp_path):
    from x4validate._liveevidence import ReadEvidence
    c=setup_all(tmp_path);s=c['sampler'];handles={k:completed(c,k)[0]for k in ('capture','orbit','roomentry')}
    s.capture_global_usage(ReadEvidence(c['client'],synthetic=True));s.start('capture')
    with pytest.raises(ValueError,match='ordering'):s.global_evidence(handles)


def test_provider_sample_raw_mapping_cannot_supply_usage():
    from x4validate._livecaptureplan import evaluate
    from test_livecaptureplan import plan
    with pytest.raises(ValueError,match='actual CapacitySamples'):
        evaluate(plan(),provider_samples=({'qualified':True},{}))


def test_same_identity_other_client_cannot_supply_global_sample(tmp_path):
    from x4validate._liveevidence import ReadEvidence
    from x4validate._livecaptureplan import evaluate
    from test_livecaptureplan import plan
    (tmp_path/'a').mkdir();(tmp_path/'b').mkdir()
    a=setup_all(tmp_path/'a');b=setup_all(tmp_path/'b')
    s=a['sampler'];handles={k:completed(a,k)[0]for k in ('capture','orbit','roomentry')}
    foreign=ReadEvidence(b['client'],synthetic=True)
    assert a['identity']==b['identity']
    with pytest.raises(ValueError,match='same-client'):
        s.capture_global_usage(foreign)
    h=foreign.collect('automation_capacity')
    with pytest.raises(ValueError,match='same-client'):
        evaluate(plan(),provider_samples=(s,handles),frozen_receipts=a['sources'],native_evidence=(foreign,h),
                 expected_build='12345678',expected_native_revision='7'*64,synthetic=True)

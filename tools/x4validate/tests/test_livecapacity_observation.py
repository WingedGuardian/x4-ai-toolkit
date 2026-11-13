"""Actual generated providers/SDK/wire; synthetic MD counts, no native effects."""
import pytest
from lxml import etree as E
import test_liveorbit as O
import test_liveroomentry as R
import test_livecamera_capacity as C
from x4validate._livecapacity import resolve


def runtime(kind,profile='campaign_v1'):
    if kind=='orbit':
        r,receipt=O.runtime(profile)
        r.execute("registry=sdk;req=request('camera_observe','d',{});send_count=function()return sends end")
    elif kind=='roomentry':
        r,receipt=R.runtime(capacity_profile=profile)
        r.execute("req.action='capacity_observe';req.arguments={};c.send=function(p,b)sends=sends+1;sent=b;sentphase=p end;send_count=function()return sends end")
    else:
        r,receipt=C.capture_runtime(profile)
        r.execute("registry=sdk;req=req('capacity_observe',{});auto=false;env.send=function(p,b)sent[#sent+1]=b;sentphase=p end;send_count=function()return #sent end")
    p=resolve(profile)
    r.globals().kind=kind;r.globals().cap_profile=profile;r.globals().cap_revision=p['revision']
    r.globals().mdlimit=p[{'orbit':'orbit_history','capture':'capture_native_records','roomentry':'roomentry_records'}[kind]]
    r.execute("""
    function usage_prepare()return registry.prepare(req)end
    function usage_execute()return registry.execute(req.id,req.session,req.digest)end
    function usage_status()return registry.status(req.id,req.session,req.digest)end
    function usage_snapshot(v)return kind=='capture' and v.observations or v.observations.snapshot end
    function sample_row()
      local w=kind=='capture' and sent[#sent] or sent
      local v
      if kind=='orbit'then v=row('observed')
      else
        v={kind='ROOM_CAPACITY',revision=w.revision,id=w.id,digest=w.digest,call=w.call,phase='capacity',
          session=w.session,generation=w.generation,world_token=w.world_token,public_revision=w.public_revision,
          status='observed',reason='READ_ONLY_CAPACITY_SAMPLE',age=5}
      end
      v.md_used=11;v.md_limit=mdlimit;v.md_busy=false;v.md_queued=0;v.md_queue_limit=64
      v.capacity_profile=cap_profile;v.capacity_revision=cap_revision
      if kind=='capture'then
        local o={bridge_phase='capacity',bridge_call=w.call,bridge_generation=w.generation,revision=w.md_revision,
          age=5,world_token=w.world_token,public_revision=w.public_revision,md_used=v.md_used,md_limit=v.md_limit,
          md_busy=v.md_busy,md_queued=v.md_queued,md_queue_limit=v.md_queue_limit,
          capacity_profile=cap_profile,capacity_revision=cap_revision}
        return {version=1,id=w.id,digest=w.digest,session=w.session,status='observed',
          reason='READ_ONLY_CAPACITY_SAMPLE',observations=o}
      end
      return v
    end
    function deliver_sample(v)queue={v or sample_row()};if kind=='capture'then callback()else drain()end end
    function incoming(v)return kind=='capture' and v.observations or v end
    """)
    return r,receipt


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_sample_one_sdk_record_one_call_no_md_record_inference(kind,profile):
    r,_=runtime(kind,profile)
    r.globals().wirefactory=r.execute((O.ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text())
    r.execute("""
      wire=wirefactory()
      p=usage_prepare();assert(p.status=='prepared' and send_count()==0)
      assert(p.observations.capacity.history_used==1 and p.observations.capacity.calls_used==0)
      usage_execute();assert(send_count()==1);deliver_sample();out=usage_status()
      assert(out.status=='completed');v=usage_snapshot(out)
      assert(v.md_used==11 and v.md_limit==mdlimit and not v.md_busy)
      assert(out.observations.capacity.history_used==1 and out.observations.capacity.calls_used==1)
      before=send_count();usage_execute();usage_status();assert(send_count()==before)
      -- Actual SDK terminal status bypasses provider.invoke: the original flag
      -- remains false. This retained result must never be interpreted as fresh.
      again=usage_status();assert(again.observations.native_observation_cached==false)
      assert(#wire.encode({payload=wire.encode(out),reserved=string.rep('x',900)})<=4096)
    """)


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
@pytest.mark.parametrize('change',[
    "o.capacity_profile='wrong'","o.capacity_revision=string.rep('0',64)",
    "o.revision=string.rep('0',64)","v.session=string.rep('0',32)",
    "o.world_token='old-world'","o.public_revision=string.rep('0',64)",
    "o.md_used=true","o.md_used=-1","o.md_used=mdlimit+1","o.md_limit=1",
    "o.md_busy=0","o.md_queued=64","o.md_queue_limit=65","o.md_used=nil",
])
def test_foreign_malformed_or_stale_sample_never_qualifies(kind,change):
    r,_=runtime(kind)
    r.execute("usage_prepare();usage_execute();v=sample_row();o=incoming(v);"+change+";deliver_sample(v);out=usage_status()")
    assert r.eval('out.status')!='completed'
    assert r.eval('send_count()')==1


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
def test_wrong_call_is_not_a_fresh_sample(kind):
    r,_=runtime(kind)
    r.execute("usage_prepare();usage_execute();v=sample_row();o=incoming(v);if kind=='capture'then o.bridge_call='foreign'else o.call='foreign'end;deliver_sample(v);out=usage_status()")
    assert r.eval('out.status')!='completed'
    assert r.eval('send_count()')==1


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
def test_busy_is_observed_not_turned_into_readiness(kind):
    r,_=runtime(kind)
    r.execute("usage_prepare();usage_execute();v=sample_row();incoming(v).md_busy=true;deliver_sample(v);out=usage_status()")
    assert r.eval('out.status')=='completed'
    assert r.eval('usage_snapshot(out).md_busy') is True


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
def test_public_generation_change_invalidates_pending(kind):
    r,_=runtime(kind)
    r.execute("usage_prepare();usage_execute();stamp.generation=string.rep('f',32);deliver_sample();out=usage_status()")
    assert r.eval('out.status')!='completed'
    assert r.eval('send_count()')==1


@pytest.mark.parametrize('kind',['orbit','capture','roomentry'])
def test_delayed_reply_after_deadline_cannot_complete(kind):
    r,_=runtime(kind)
    r.execute("usage_prepare();usage_execute();now=20;deliver_sample();out=usage_status()")
    assert r.eval('out.status')!='completed'


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_emitted_capacity_cues_do_not_allocate_or_dispatch(profile):
    for kind,generator in [('orbit',O.g.prepare),('capture',C.C.P.prepare),('roomentry',R.g.prepare)]:
        files,receipt=generator(capacity_profile=profile)
        raw=next(b for p,b in files.items()if p.startswith('md/'))
        root=E.fromstring(raw);name='Snapshot' if kind=='orbit' else 'Capacity'
        cue=root.xpath(f"//cue[@name='{name}']")[0]
        assert cue.find('actions')[0].tag=='debug_text'
        assert not cue.xpath('.//capture_screen|.//play_cutscene|.//stop_cutscene|.//teleport_player|.//add_actor_to_room|.//leave_control_position')
        assert not cue.xpath(".//set_value[contains(@name,'$Records') or contains(@name,'$Count') or contains(@name,'$Busy')]")
        assert not cue.xpath(".//append_to_list[contains(@name,'$Records')]")
        text=E.tostring(cue).decode()
        for token in ('md_used','md_limit','md_busy','md_queued','md_queue_limit','capacity_profile','capacity_revision'):
            assert token in text
        for token in ('$world_token','$public_revision','$session','$generation'):
            assert token in text
        if kind=='roomentry':
            contract=receipt['md_contract']
            assert 'target'in contract['fields'] and 'md_used'not in contract['fields']
            assert 'md_used'in contract['capacity_fields'] and 'target'not in contract['capacity_fields']

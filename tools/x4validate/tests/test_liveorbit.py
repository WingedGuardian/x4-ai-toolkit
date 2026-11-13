"""Actual generated Lua/SDK/wire with synthetic MD replies; no game/cutscene."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from unittest.mock import patch
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("orbit_ext", ROOT / "scripts/live_orbit_extension.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


def runtime(capacity_profile=None):
    r = LuaRuntime(unpack_returned_tuples=True)
    files, receipt = g.prepare(capacity_profile)
    r.globals().capacity_name=receipt['capacity_profile']['name']
    r.globals().capacity_revision=receipt['capacity_profile']['revision']
    for name in ("actions", "actionwire"):
        r.globals()[name] = r.execute((ROOT / f"mods/x4_toolkit_helper/ui/live_{name}.lua").read_text())
    r.execute("""
        ffi=require('ffi');ship=ffi.cast('uint64_t',-1);now=0;sends=0;reads=0;clears=0;disposals=0
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        wire=actionwire();sdk=actions();hooks={}
        function resolve(token,kind)assert(token=='issued' and kind=='ship');return ship end
        c={public_current=function()return stamp end,now=function()return now end,
            subscribe=function(fn)drain=fn end,arm_tick=function(fn)tick=fn end,
            send=function(phase,body)sends=sends+1;sent=body;sentphase=phase end,
            read=function()reads=reads+1;return queue end,clear=function()clears=clears+1;queue=nil end,
            component_id=function(v)assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0);return tostring(v)end,
            dispose=function()disposals=disposals+1;return true end}
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
    """)
    r.execute(files["ui/live_orbit_extension.lua"].decode())
    r.execute("""
        attached=descriptor.attach{ffi=ffi,encode=wire.encode,resolve_issued=resolve,
            capacity_profile=capacity_name,capacity_revision=capacity_revision,
            registry={register=function(s)provider=s;sdk.register(s)end},
            on_dispose=function(fn)hooks[#hooks+1]=fn end,create_md_channel=function(cfg)
                assert(cfg.screen=='X4ToolkitOrbit' and cfg.controls.snapshot and cfg.controls.status)
                return c end}
        function request(action,id,args)
            return {version=1,id=string.rep(id,32),digest=string.rep('e',64),session=stamp.session,
                provider='x4-orbit-camera-v1',binding=descriptor.revision,action=action,arguments=args}
        end
        req=request('orbit_start','d',{ship='issued',distance=1000,elevation=100,duration=5})
        function call(phase)provider.invoke(phase,req,function(v)last=v;callbacks=(callbacks or 0)+1 end)end
        function row(status)
            local dispatch=status=='requested' or status=='stop_requested'
            return {kind='ORBIT',revision=sent.revision,id=sent.id,digest=sent.digest,call=sent.call,phase=sentphase,
                session=sent.session,generation=sent.generation,world_token=sent.world_token,public_revision=sent.public_revision,
                status=status,reason='synthetic native sample',age=5,consumed=dispatch,dispatched=dispatch,
                handle=(dispatch or sent.op=='stop') and '42' or '',stopped=false,stop_requested=status=='stop_requested',
                target=sent.op=='observe' and 0 or ship,yaw=0.4,pitch=-0.3,zoom=1.5,external=true,fullscreen=dispatch}
        end
        function deliver(v)queue={v};drain()end
        function prep()call('prepare');deliver(row('prepared'))end
        function start()prep();now=2;call('execute');deliver(row('requested'))end
    """)
    r.globals().md_record_limit=receipt['capacity_profile']['orbit_history']
    r.execute("""original_row=row;function row(status)local v=original_row(status)
      if sentphase=='snapshot'then
        v.md_used=0;v.md_limit=md_record_limit;v.md_busy=false;v.md_queued=0;v.md_queue_limit=64
        v.capacity_profile=capacity_name;v.capacity_revision=capacity_revision
      end;return v end""")
    return r, receipt


def check_factory_deterministic_and_pins():
    files, receipt = g.prepare(); other, again = g.prepare()
    assert files == other and receipt == again
    assert set(files) == {"md/X4ToolkitOrbit.xml", "ui/live_orbit_extension.lua"}
    assert not receipt["achieved_pose_verified"] and not receipt["target_distance_verified"]
    assert receipt["angle_serialization_units"] == "UNKNOWN"
    for path, sha in receipt["source_sha256"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha


def check_md_native_owned_handle_timer_and_foreign_guard():
    files, _ = g.prepare(); xml = files["md/X4ToolkitOrbit.xml"]
    root = E.fromstring(xml)
    plays = root.xpath("//play_cutscene"); assert len(plays) == 2
    assert dict(plays[0].attrib) == {"result": "$Record.$scene", "key": "'OrbitParameterized'"}
    assert [dict(x.attrib) for x in plays[0]] == [
        {"name": "targetobject", "object": "$Record.$target"},
        {"name": "orbitdist", "number": "$Record.$distance"},
        {"name": "orbitelevation", "number": "$Record.$elevation"}]
    assert root.xpath("//stop_cutscene/@cutscene") == ["$Record.$owner.$scene", "parent.$Record.$scene"]
    assert not root.xpath("//stop_cutscene[@key] | //play_cutscene[@timeout]")
    startguard = plays[0].getparent().getparent().get("value")
    for token in ("not player.isinfullscreencutscene", "not player.isinconversation", "not player.isinfullscreenmenu",
                  "player.age ge $Record.$age + 1.2s", "$Record.$target.sector == player.sector"):
        assert token in startguard
    assert xml.index(b'name="$Record.$consumed" exact="true"') < xml.index(b'<play_cutscene')
    gate = "(@parent.$Record.$op == 'start' or @parent.$Record.$op == 'follow') and @parent.$Record.$dispatched"
    # F370: no event-less <conditions> on this sub-cue; the gate is in the delay and the actions.
    assert not root.xpath("//cue[@name='BoundedStop']/conditions")
    assert root.xpath("//cue[@name='BoundedStop']/delay/@exact") == [
        "(if " + gate + " and @parent.$Record.$duration then parent.$Record.$duration else 0) * 1s"]
    stopguard = root.xpath("//cue[@name='BoundedStop']/actions/do_if/@value")[0]
    assert stopguard.startswith(gate + " and ")
    assert "parent.$Record == md.X4ToolkitOrbit.State.$Busy" in stopguard
    stopped = root.xpath("//cue[@name='Stopped']/actions/do_if")[0]
    assert "event.param2 == md.X4ToolkitOrbit.State.$Busy.$scene" in stopped.get("value")
    assert not root.xpath("//cue[@name='Snapshot']//play_cutscene | //cue[@name='Status']//stop_cutscene")
    for cue in root.xpath("//cue[conditions/event_ui_triggered]"):
        assert cue.find("actions")[0].tag == "debug_text"
    assert root.xpath("//set_value[@name='$yaw']/@exact") == ["player.camera.yaw"] * 4


def check_follow_native_equal_endpoint_source_contract():
    files, receipt = g.prepare(); root = E.fromstring(files["md/X4ToolkitOrbit.xml"])
    play = root.xpath("//play_cutscene[@key=\"'Cutscene_follow_and_rotate'\"]")[0]
    assert play.get("result") == "$Record.$scene"
    params = {p.get("name"): dict(p.attrib) for p in play}
    assert len(params) == 8
    assert params["target_object"]["object"] == "$Record.$target"
    assert params["duration"]["number"] == "$Record.$duration * 1s"
    for field in ("distance", "yaw", "pitch"):
        assert params[field+"_start"]["number"] == params[field+"_end"]["number"] == "$Record.$"+field
    assert "follow_start" in receipt["actions"]
    assert "or @parent.$Record.$op == 'follow'" in root.xpath("//cue[@name='BoundedStop']/actions/do_if/@value")[0]
    assert "or $Record.$op == 'follow'" in play.getparent().getparent().get("value")


def check_follow_own_stop_and_raw_requested_values():
    r,_=runtime()
    r.execute("""
        req=request('follow_start','d',{ship='issued',distance=1250,yaw=-70,pitch=15,duration=5})
        prep();assert(sent.op=='follow' and sent.target==ship and sent.yaw==-70 and sent.pitch==15)
        now=2;call('execute');deliver(row('requested'));owner=req.id
        req=request('orbit_stop','f',{owner=owner});prep();call('execute');deliver(row('stop_requested'))
    """)
    assert r.eval("last.status") == "running" and not r.eval("last.observations.achieved_pose_verified")


@pytest.mark.parametrize("edit", [
    "yaw=361", "pitch=-91", "yaw=0/0", "pitch=math.huge", "yaw=1.5", "distance=0", "duration=31",
    "extra=true", "elevation=0",
])
def check_follow_invalid_never_sends(edit):
    r,_=runtime()
    r.execute("a={ship='issued',distance=1250,yaw=0,pitch=15,duration=5};a."+edit+
              ";req=request('follow_start','d',a);call('prepare')")
    assert r.eval("sends") == 0 and r.eval("last.status") == "unknown"


def check_follow_prepared_parameter_drift_refused():
    r,_=runtime()
    r.execute("req=request('follow_start','d',{ship='issued',distance=1250,yaw=0,pitch=15,duration=5});prep();now=2;req.arguments.yaw=1;call('execute')")
    assert r.eval("sends") == 1 and r.eval("last.status") == "unknown"


def check_channel_ownership_registered_before_provider_constructor():
    files,_=g.prepare()
    source=files["ui/live_orbit_extension.lua"].decode()
    tail=source[source.index("bundle.register_extension"):]
    assert tail.index("ctx.on_dispose(c.dispose)") < tail.index("local api=provider")


@pytest.mark.parametrize("target", ["md", "provider", "self"])
def check_source_drift_refuses(target):
    read = Path.read_bytes; selected = Path(g.__file__) if target == "self" else ROOT / g.PATHS[target]; count = 0
    def changed(p):
        nonlocal count
        value = read(p)
        if p == selected:
            count += 1
            if count > 1: return value + b"\n"
        return value
    with patch.object(Path, "read_bytes", changed), pytest.raises(AssertionError): g.prepare()


def check_uint64_prepare_and_raw_camera_no_pose_claim():
    r, _ = runtime(); assert r.eval("sends+reads+clears") == 0
    r.execute("prep()")
    assert r.eval("sent.target==ship") and r.eval("tostring(sent.target)") == "18446744073709551615ULL"
    assert r.eval("last.status") == "prepared"
    assert r.eval("last.observations.snapshot.pitch") == -0.3
    assert r.eval("last.observations.angle_units") == "UNKNOWN"
    assert not r.eval("last.observations.achieved_pose_verified")


def check_start_never_implies_achieved_pose_and_once_only():
    r, _ = runtime(); r.execute("start()")
    assert r.eval("last.status") == "running"
    assert r.eval("last.observations.snapshot.handle") == "42"
    r.execute("call('execute')")
    assert r.eval("sends") == 2 and r.eval("last.status") == "unknown"


def check_owned_stop_lifecycle_requires_matching_event_observation():
    r, _ = runtime(); r.execute("""
        start();owner=req.id
        req=request('orbit_stop','f',{owner=owner});prep();call('execute');deliver(row('stop_requested'))
    """)
    assert r.eval("last.status") == "running"
    r.execute("call('status');local x=row('requested');x.stopped=true;x.stop_requested=true;deliver(x)")
    assert r.eval("last.status") == "completed"
    assert not r.eval("last.observations.achieved_pose_verified")


def check_foreign_stop_never_sends():
    r, _ = runtime(); r.execute("req=request('orbit_stop','f',{owner=string.rep('a',32)});call('prepare')")
    assert r.eval("sends") == 0 and r.eval("last.status") == "unknown"


def check_refused_stop_with_owner_stopped_does_not_complete_operation():
    r, _ = runtime(); r.execute("""
        start();req=request('orbit_stop','f',{owner=req.id});prep();call('execute')
        local x=row('refused');x.consumed=true;x.stopped=true;x.stop_requested=true;deliver(x)
    """)
    assert r.eval("last.status") == "refused"
    assert r.eval("last.observations.snapshot.stopped") is True


def check_read_only_snapshot_actual_sdk_and_wire():
    r, _ = runtime(); r.execute("""
        req=request('camera_observe','f',{});sdk.prepare(req);sdk.execute(req.id,req.session,req.digest)
        deliver(row('observed'));v=sdk.status(req.id,req.session,req.digest)
        encoded=wire.encode({payload=wire.encode(v)})
    """)
    assert r.eval("sends") == 1 and r.eval("sentphase") == "snapshot"
    assert r.eval("v.status") == "completed" and len(r.eval("encoded").encode()) <= 4096
    assert not r.eval("v.observations.snapshot.dispatched")


@pytest.mark.parametrize("change", [
    "x.digest=string.rep('f',64)", "x.call=string.rep('f',32)", "x.phase='execute'",
    "x.session=string.rep('f',32)", "x.generation=string.rep('f',32)", "x.world_token='other'",
    "x.public_revision=string.rep('f',64)", "x.revision=string.rep('f',64)", "x.extra=true", "x.reason=nil",
    "x.target=ffi.new('uint64_t',3)", "x.target='18446744073709551615ULL'", "x.age=-1", "x.age=0/0",
    "x.pitch=math.huge", "x.yaw='0'", "x.zoom=-1", "x.external=2", "x.status='observed'",
    "x.dispatched=true", "x.consumed=true", "x.stopped=true"])
def check_bad_preparation_never_dispatches(change):
    r, _ = runtime(); r.execute("call('prepare');local x=row('prepared');" + change + ";deliver(x);call('execute')")
    assert r.eval("sends") == 1 and r.eval("last.status") != "completed"


@pytest.mark.parametrize("change", ["x.handle=''", "x.status='observed'", "x.consumed=false"])
def check_bad_dispatch_unknown_never_replayed(change):
    r, _ = runtime(); r.execute("prep();call('execute');local x=row('requested');" + change + ";deliver(x);call('execute')")
    assert r.eval("sends") == 2 and r.eval("last.status") != "completed"


@pytest.mark.parametrize("change", ["req.arguments.duration=31", "req.arguments.duration=0", "req.arguments.distance=9",
    "req.arguments.elevation=100001", "req.arguments.duration=1.5", "req.arguments.extra=0", "req.arguments.ship='foreign'"])
def check_invalid_arguments_do_not_send(change):
    r, _ = runtime(); r.execute(change + ";call('prepare')")
    assert r.eval("sends") == 0


def check_duplicate_batch_faults_before_any_callback():
    r, _ = runtime(); r.execute("call('prepare');local x=row('prepared');queue={x,x};drain();call('execute')")
    assert r.eval("sends") == 1 and r.eval("last.status") == "unknown"


def check_timeout_and_lost_ack_no_resend_no_stop_all():
    r, _ = runtime(); r.execute("prep();call('execute');now=11;tick();call('status');call('execute')")
    assert r.eval("sends") == 2 and r.eval("last.status") == "unknown"


def check_public_drift_before_reply_refuses():
    r, _ = runtime(); r.execute("call('prepare');stamp.world_token='new';deliver(row('prepared'));call('execute')")
    assert r.eval("sends") == 1 and r.eval("last.status") == "unknown"


def check_prepared_argument_drift_refuses():
    r, _ = runtime(); r.execute("prep();req.arguments.distance=2000;call('execute')")
    assert r.eval("sends") == 1


def check_handle_change_and_clock_rollback_refuse():
    r, _ = runtime(); r.execute("start();call('status');local x=row('requested');x.handle='43';deliver(x)")
    assert r.eval("last.status") == "unknown"
    r, _ = runtime(); r.execute("now=10;call('prepare');now=9;tick();call('execute')")
    assert r.eval("sends") == 1


def check_retirement_failure_sticky_callbacks_inert():
    r, _ = runtime(); r.execute("call('prepare');c.dispose=function()return false end")
    assert not r.eval("attached.dispose()") and not r.eval("attached.dispose()")
    r.execute("queue={row('prepared')};drain()")
    assert r.eval("reads+clears") == 0


def check_actual_sdk_start_stop_dispatch_and_wire():
    r, _ = runtime(); r.execute("""
        sdk.prepare(req);deliver(row('prepared'));now=2;sdk.execute(req.id,req.session,req.digest)
        deliver(row('requested'));v=sdk.status(req.id,req.session,req.digest)
        encoded=wire.encode({payload=wire.encode(v)})
    """)
    assert r.eval("v.status") != "completed" and len(r.eval("encoded").encode()) <= 4096
    assert not r.eval("v.observations.target_distance_verified")

# MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0
# (KB 2026-11-10; ROR handover defect (b)). MDNUM re-encodes every boolean of a
# synthetic reply the way the engine delivers it.
MDNUM = """
function mdnum(v)
    if v==true then return 1 elseif v==false then return 0 end
    if type(v)=='table' and getmetatable(v)==nil then
        local o={};for k,x in pairs(v) do o[k]=mdnum(x) end;return o
    end
    return v
end
plain_deliver=deliver;function deliver(v) return plain_deliver(mdnum(v)) end
"""


def check_md_numeric_booleans_complete_owned_stop():
    r, _ = runtime(); r.execute(MDNUM); r.execute("""
        start();owner=req.id
        req=request('orbit_stop','f',{owner=owner});prep();call('execute');deliver(row('stop_requested'))
    """)
    assert r.eval("last.status") == "running"
    r.execute("call('status');local x=row('requested');x.stopped=true;x.stop_requested=true;deliver(x)")
    assert r.eval("last.status") == "completed"
    assert r.eval("last.observations.snapshot.stopped") is True


def check_md_numeric_boolean_other_than_0_or_1_still_refused():
    # Twin: only exactly 1/0 are MD booleans; any other number is still refused.
    r, _ = runtime(); r.execute(MDNUM); r.execute("prep();now=2;call('execute');local x=row('requested');x.dispatched=2;plain_deliver(x)")
    assert r.eval("last.status") != "running"

def _cases():
    out = []
    for name, fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn): continue
        marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        if marks:
            for i, value in enumerate(marks[0].args[1]): out.append(pytest.param(name, [value], id=f"{name}-{i}"))
        else: out.append(pytest.param(name, [], id=name))
    return out


@pytest.mark.parametrize("name,args", _cases())
def test_actual_luajit_control(name, args):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), name, json.dumps(args)],
        capture_output=True, text=True, timeout=20, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CONTROL_PASSED", result.stdout + result.stderr


if __name__ == "__main__":
    name = sys.argv[1]; assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2])); print("CONTROL_PASSED")

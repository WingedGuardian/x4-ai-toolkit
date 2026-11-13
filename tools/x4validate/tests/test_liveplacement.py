"""Actual generated placement provider + SDK + wire; synthetic MD responses, no game."""
from pathlib import Path
import hashlib,importlib.util,json,os,subprocess,sys
from unittest.mock import patch
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("placement_ext",ROOT/"scripts/live_placement_extension.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def runtime(real_channel=False):
    r=LuaRuntime(unpack_returned_tuples=True);files,receipt=g.prepare()
    for name in ("actions","actionwire"):
        r.globals()[name]=r.execute((ROOT/f"mods/x4_toolkit_helper/ui/live_{name}.lua").read_text(encoding="utf-8"))
    r.globals().mdfactory=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_mdchannel.lua").read_text(encoding="utf-8"))
    r.globals().real_channel=real_channel
    r.execute("""
        ffi=require('ffi');u=function(s)return ffi.new('uint64_t',s)end
        -- Exact maximum uint64 construction avoids decimal→Lua-double conversion.
        ship=ffi.cast('uint64_t',-1);now=0;sends=0;reads=0;clears=0;disposals=0
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        wire=actionwire();registry=actions();hooks={}
        function resolve(token,kind)assert(token=='issued' and kind=='ship');return ship end
        c={public_current=function()return stamp end,now=function()return now end,
            subscribe=function(fn)drain=fn end,arm_tick=function(fn)tick=fn end,
            send=function(phase,body)assert(type(body.target)=='cdata');sends=sends+1;sent=body;sentphase=phase end,
            read=function()reads=reads+1;return queue end,clear=function()clears=clears+1;queue=nil end,
            component_id=function(v)assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0);return tostring(v)end,
            dispose=function()disposals=disposals+1;return true end}
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
    """)
    r.execute(files["ui/live_placement_extension.lua"].decode())
    r.execute("""
        attached=descriptor.attach{ffi=ffi,encode=wire.encode,resolve_issued=resolve,registry=registry,
            on_dispose=function(fn)hooks[#hooks+1]=fn end,
            create_md_channel=function(cfg)
                if cfg.screen=='X4ToolkitShipPlacement' then
                    -- This fixture exercises only the preserved player channel.
                    -- The second real provider is registered cold; its own full tests
                    -- use a distinct channel and exercise every ship request.
                    local other={};for k,v in pairs(c)do other[k]=v end
                    other.subscribe=function()end;other.arm_tick=function()end
                    return other
                end
                assert(cfg.screen=='X4ToolkitPlacement' and cfg.controls.prepare and cfg.controls.execute and cfg.controls.status)
                if real_channel then
                    local globals={
                        RegisterEvent=function(name,fn)assert(name==cfg.event);drain=fn end,
                        GetNPCBlackboard=function(id,key)assert(id==ship and key==cfg.queue);reads=reads+1;return queue end,
                        SetNPCBlackboard=function(id,key,v)assert(id==ship and key==cfg.queue and v==nil);clears=clears+1;queue=nil end,
                        AddUITriggeredEvent=function(screen,control,body)
                            assert(screen==cfg.screen and cfg.controls[control])
                            assert(type(body.target)=='cdata' and body.target==ship)
                            sends=sends+1;sent=body;sentphase=control
                        end,
                        ConvertStringTo64Bit=function(v)assert(v==tostring(ship));return ship end,
                        ConvertIDTo64Bit=function(v)return v end,getElapsedTime=function()return now end}
                    return mdfactory{config=cfg,ffi=ffi,C={GetPlayerID=function()return ship end},
                        globals=globals,Helper={addDelayedOneTimeCallbackOnUpdate=function(fn)scheduled=fn end},
                        public_current=c.public_current}
                end
                return c
            end}
        req={version=1,id=string.rep('d',32),digest=string.rep('e',64),session=stamp.session,
            provider='x4-diagnostic-placement-v1',binding=descriptor.revision,
            action='diagnostic_place_player',arguments={ship='issued'}}
        function row(status)
            return {kind='PLACEMENT',revision=sent.revision,id=sent.id,digest=sent.digest,
                call=sent.call,phase=sentphase,session=sent.session,generation=sent.generation,
                world_token=sent.world_token,public_revision=sent.public_revision,status=status,
                reason='synthetic observed response',target=ship,target_exists=true,occupied=false,controlled=false,
                container=false,age=5,dispatched=status=='dispatched',consumed=status=='dispatched'}
        end
        function deliver(v)queue={v};drain()end
        function prep()
            local v=registry.prepare(req);deliver(row('prepared'));return registry.status(req.id,req.session,req.digest)
        end
        function run()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
    """)
    return r,receipt

def check_factory_scope_pins_and_reproducibility():
    files,receipt=g.prepare();again,other=g.prepare()
    assert files==again and receipt==other
    assert set(files)=={"md/X4ToolkitPlacement.xml","md/X4ToolkitShipPlacement.xml","ui/live_placement_extension.lua"}
    for p,h in receipt["source_sha256"].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
    assert receipt["runtime_verified"] is False
    assert receipt["native_target_transport"]=="UINT64_CDATA_IN_TABLE_RUNTIME_UNQUALIFIED"

def check_md_action_native_shape_and_consumption_precedes_action():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitPlacement.xml"])
    actions=root.xpath("//teleport_player");assert len(actions)==1
    assert dict(actions[0].attrib)==dict(object="$Target",instant="true",force="true")
    xml=files["md/X4ToolkitPlacement.xml"].decode()
    assert xml.index('name="$Record.$consumed" exact="true"')<xml.index("<teleport_player")
    assert "$Target.owner == faction.player" in xml and "not $Target.isclass.spacesuit" in xml
    assert root.xpath("//event_ui_triggered/@control")==["'prepare'","'execute'","'status'"]
    assert len(root.xpath("//show_notification"))==len(root.xpath("//write_to_logbook"))==1
    assert "player.controlled == $Record.$controlled" in xml
    assert "player.occupiedship == $Record.$occupied" in xml
    assert "component.{@$R.$target}" in xml
    for cue in root.xpath("//cue[conditions/event_ui_triggered]"):
        assert cue.find("actions")[0].tag=="debug_text"
    assert root.xpath("//cue[@name='State']/actions/*")[0].tag=="debug_text"

@pytest.mark.parametrize("target",["md","provider","self"])
def check_factory_source_drift_refuses(target):
    read=Path.read_bytes;selected=Path(g.__file__) if target=="self" else ROOT/g.PATHS[target];n=0
    def changed(p):
        nonlocal n
        b=read(p)
        if p==selected:
            n+=1
            if n>1:return b+b"\n"
        return b
    with patch.object(Path,"read_bytes",changed),pytest.raises(ValueError):g.prepare()

def check_constructor_does_not_dispatch_and_prepare_keeps_native_uint64():
    r,_=runtime();assert r.eval("sends+reads+clears")==0
    r.execute("registry.prepare(req)")
    assert r.eval("sends")==1 and r.eval("sentphase")=="prepare"
    assert r.eval("sent.target==ship") and r.eval("tostring(sent.target)")=="18446744073709551615ULL"

def check_actual_mdchannel_preserves_uint64_through_native_event_boundary():
    r,_=runtime(real_channel=True)
    r.execute("registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'))")
    assert r.eval("sends")==2 and r.eval("reads")==2 and r.eval("clears")==2
    assert r.eval("type(sent.target)")=="cdata" and r.eval("sent.target==ship")

def check_actual_sdk_prepare_execute_status_and_observed_control():
    r,_=runtime();r.execute("prep()")
    # SDK status may issue a new read-only observation.
    if r.eval("sentphase")=="status":r.execute("deliver(row('prepared'))")
    r.execute("run()");assert r.eval("sentphase")=="execute"
    r.execute("deliver(row('dispatched'));v=status()")
    assert r.eval("v.status") in ("running","unknown")
    r.execute("local x=row('dispatched');x.occupied=true;x.controlled=true;deliver(x);v=status()")
    if r.eval("sentphase")=="status":r.execute("local x=row('dispatched');x.occupied=true;x.controlled=true;deliver(x)")
    assert r.eval("registry.status(req.id,req.session,req.digest).observations.snapshot.controlled")
    assert r.eval("sends")>=3

def check_sdk_running_is_not_occupied_or_controlled():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'));v=status()")
    assert r.eval("v.status")!="completed"
    assert r.eval("v.observations.snapshot.controlled") is False
    assert r.eval("v.observations.walking_verified") is False

@pytest.mark.parametrize("change",[
    "x.digest=string.rep('f',64)","x.id=string.rep('f',32)","x.call=string.rep('f',32)",
    "x.phase='execute'","x.session=string.rep('f',32)","x.generation=string.rep('f',32)",
    "x.world_token='other'","x.public_revision=string.rep('f',64)","x.revision=string.rep('f',64)",
    "x.kind='OTHER'","x.extra=true","x.reason=nil","x.target=ffi.new('uint64_t',3)",
    "x.target='18446744073709551615ULL'","x.target_exists=false","x.age=0/0","x.age=math.huge",
    "x.age=-1","x.controlled=2","x.status='completed'","x.dispatched=true","x.consumed=true"])
def check_malformed_reply_never_executes(change):
    r,_=runtime();r.execute("registry.prepare(req);local x=row('prepared');"+change+";deliver(x);v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

def check_duplicate_batch_preflight_prevents_reentrant_execute():
    r,_=runtime();r.execute("registry.prepare(req);local x=row('prepared');queue={x,x};drain();v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

@pytest.mark.parametrize("value",["0","9007199254740992","'18446744073709551615'","ffi.new('double',3)","ffi.new('uint64_t',0)"])
def check_unissued_or_inexact_target_never_sends(value):
    r,_=runtime();r.execute("ship="+value+";registry.prepare(req)")
    assert r.eval("sends")==0

def check_repeat_execute_after_dispatch_never_sends_second_execute():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'))")
    before=r.eval("sends");r.execute("run();run()")
    assert r.eval("sends")==before

def check_refused_native_preparation_never_executes():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('refused'));run()")
    assert r.eval("sends")==1

def check_read_failure_never_clears_unknown_or_executes():
    r,_=runtime();r.execute("registry.prepare(req);c.read=function()error('unreadable')end;drain();run()")
    assert r.eval("sends")==1 and r.eval("clears")==0

def check_timeout_never_resends_or_rebinds():
    r,_=runtime();r.execute("registry.prepare(req);now=11;tick();v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"
    r.execute("tick();status()");assert r.eval("sends")==1

def check_world_change_before_reply_refuses():
    r,_=runtime();r.execute("registry.prepare(req);stamp.world_token='changed';deliver(row('prepared'));v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

def check_clock_backward_refuses():
    r,_=runtime();r.execute("now=10;registry.prepare(req);now=9;tick();v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

def check_expired_preparation_does_not_execute():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));now=11;v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

def check_target_changes_before_execute_refuses():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));ship=ffi.new('uint64_t',2);v=run()")
    assert r.eval("sends")==1 and r.eval("v.status")!="completed"

def check_send_throw_is_consumed_unknown_without_retry():
    r,_=runtime();r.execute("""
        registry.prepare(req);deliver(row('prepared'))
        c.send=function()sends=sends+1;error('native uncertainty')end
        run();run();status()
    """)
    assert r.eval("sends")==2

def check_disposal_failure_sticky_and_abandoned_callback_inert():
    r,_=runtime();r.execute("registry.prepare(req);c.dispose=function()return false end")
    assert r.eval("attached.dispose()") is False and r.eval("attached.dispose()") is False
    r.execute("queue={row('prepared')};drain()");assert r.eval("reads+clears")==0

def check_status_does_not_teleport_or_claim_continuous_outcome():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitPlacement.xml"])
    assert not root.xpath("//cue[@name='Status']//teleport_player")
    assert root.xpath("//cue[@name='Status']//set_value[@name='$Result']")

def check_md_settlement_binds_same_request_and_target_before_releasing():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitPlacement.xml"])
    releases=root.xpath("//set_value[@name='md.X4ToolkitPlacement.State.$Busy' and @exact='null']")
    assert len(releases)==4  # one load invalidation and three response branches
    for release in releases[1:]:
        guard=release.getparent().get("value")
        for required in ("$Record.$digest == $R.$digest","$Record.$target == $Target",
            "$Record.$session == $R.$session","$Record.$generation == $R.$generation",
            "$Record.$world_token == $R.$world_token","$Record.$public_revision == $R.$public_revision"):
            assert required in guard
        assert "($Occupied or $Controlled)" in guard and "$Record.$dispatched" in guard

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


def check_md_numeric_booleans_prepare_execute_status():
    r,_=runtime();r.execute(MDNUM);r.execute("prep()")
    if r.eval("sentphase")=="status":r.execute("deliver(row('prepared'))")
    r.execute("run()");assert r.eval("sentphase")=="execute"
    r.execute("deliver(row('dispatched'));v=status()")
    assert r.eval("v.status") in ("running","unknown")
    r.execute("local x=row('dispatched');x.occupied=true;x.controlled=true;deliver(x);v=status()")
    if r.eval("sentphase")=="status":r.execute("local x=row('dispatched');x.occupied=true;x.controlled=true;deliver(x)")
    snap=r.eval("registry.status(req.id,req.session,req.digest).observations.snapshot")
    assert snap.controlled is True and snap.occupied is True and snap.dispatched is True


def check_tick_before_the_session_is_bound_does_not_fault():
    # Review r2-10: a deadline tick before the first session binding faulted the provider for good.
    r,_=runtime();r.execute("local s=stamp;stamp=nil;tick();stamp=s")
    assert r.eval("prep().status")=="prepared"


def check_tick_after_binding_still_faults_on_timeout():
    # Twin: once bound, a missed deadline still faults.
    r,_=runtime();r.execute("registry.prepare(req);now=11;tick()")
    assert r.eval("status().status")!="prepared"


def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                out.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:out.append(pytest.param(name,[],id=name))
    return out
@pytest.mark.parametrize("name,args",_cases())
def test_actual_luajit_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

"""Actual generated roomentry provider + SDK + wire; synthetic MD responses, no game."""
from pathlib import Path
import hashlib,importlib.util,json,os,subprocess,sys
from unittest.mock import patch
import pytest
from lxml import etree as E
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("roomentry_ext",ROOT/"scripts/live_roomentry_extension.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def runtime(real_channel=False,constructor_failure=False,capacity_profile=None):
    from x4validate._livecapacity import lua_source,resolve
    r=LuaRuntime(unpack_returned_tuples=True);files,receipt=g.prepare(capacity_profile)
    r.globals().capacity_resolver=r.execute(lua_source())
    r.globals().capacity_name=resolve(capacity_profile)['name']
    r.globals().capacity_revision=resolve(capacity_profile)['revision']
    for name in ("actions","actionwire"):
        r.globals()[name]=r.execute((ROOT/f"mods/x4_toolkit_helper/ui/live_{name}.lua").read_text())
    r.globals().mdfactory=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_mdchannel.lua").read_text())
    r.globals().real_channel=real_channel
    r.globals().constructor_failure=constructor_failure
    r.execute("""
        ffi=require('ffi');u=function(s)return ffi.new('uint64_t',s)end
        -- Exact maximum uint64 construction avoids decimal→Lua-double conversion.
        ship=ffi.cast('uint64_t',-1);room=ffi.new('uint64_t',40);now=0;sends=0;reads=0;clears=0;disposals=0
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        wire=actionwire();registry=actions('campaign_v1',capacity_resolver);hooks={}
        function resolve(token,kind)assert(token=='issued' and kind=='object');return ship end
        c={public_current=function()return stamp end,now=function()return now end,
            subscribe=function(fn)drain=fn end,arm_tick=function(fn)tick=fn end,
            send=function(phase,body)assert(type(body.target)=='cdata');sends=sends+1;sent=body;sentphase=phase end,
            read=function()reads=reads+1;return queue end,clear=function()clears=clears+1;queue=nil end,
            component_id=function(v)assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0);return tostring(v)end,
            dispose=function()disposals=disposals+1;return true end}
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
    """)
    r.execute(files["ui/live_roomentry_extension.lua"].decode())
    r.execute("""
        function attach()return descriptor.attach{ffi=ffi,encode=wire.encode,resolve_issued=constructor_failure and {} or resolve,registry=registry,
            capacity_profile=capacity_name,capacity_revision=capacity_revision,
            on_dispose=function(fn)hooks[#hooks+1]=fn end,
            create_md_channel=function(cfg)
                assert(cfg.screen=='X4ToolkitRoomEntry' and cfg.controls.prepare and cfg.controls.execute and cfg.controls.status)
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
            end}end
        if constructor_failure then constructor_ok,constructor_error=pcall(attach)else attached=attach()end
        req={version=1,id=string.rep('d',32),digest=string.rep('e',64),session=stamp.session,
            provider='x4-diagnostic-roomentry-v1',binding=descriptor.revision,
            action='diagnostic_enter_room',arguments={container='issued',selector='controlroom'}}
        function row(status)
            return {kind='ROOM_ENTRY',revision=sent.revision,id=sent.id,digest=sent.digest,
                call=sent.call,phase=sentphase,session=sent.session,generation=sent.generation,
                world_token=sent.world_token,public_revision=sent.public_revision,status=status,
                reason='synthetic observed response',target=ship,target_exists=true,room=room,room_exists=true,entered=false,controlled=false,
                positioned=false,age=5,dispatched=status=='dispatched',consumed=status=='dispatched',
                room_count=sentphase=='prepare' and 1 or 0,candidates=sentphase=='prepare' and {room} or {},
                candidate_macros=sentphase=='prepare' and {'synthetic_room_macro'} or {},room_macro='synthetic_room_macro',
                scope_count=sent.selector=='dynamicrooms' and 1 or 0,scope_complete=true,choice_required=sent.selector=='dynamicrooms' and sent.choice==0}
        end
        function deliver(v)queue={v};drain()end
        function prep()
            local v=registry.prepare(req);deliver(row('prepared'));return registry.status(req.id,req.session,req.digest)
        end
        function run()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
    """)
    return r,receipt

def check_factory_is_pure_reproducible_source_bound():
    files,receipt=g.prepare();again,other=g.prepare()
    assert files==again and receipt==other
    assert set(files)=={"md/X4ToolkitRoomEntry.xml","ui/live_roomentry_extension.lua"}
    assert receipt["runtime_verified"] is False
    for p,h in receipt["source_sha256"].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h

def check_native_actions_and_preexisting_listeners():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitRoomEntry.xml"])
    assert len(root.xpath("//teleport_player"))==1
    assert dict(root.xpath("//teleport_player")[0].attrib)==dict(object="$Record.$slot.component.controllable",instant="true",force="true")
    assert len(root.xpath("//leave_control_position"))==1
    assert len(root.xpath("//add_actor_to_room"))==1
    assert dict(root.xpath("//add_actor_to_room")[0].attrib)==dict(actor="player.entity",object="$B.$slot.component")
    assert root.xpath("//create_position/@value")==["$B.$slot.offset"]
    assert root.xpath("//create_position/@y")==["player.entity.race.height"]
    for cue in root.xpath("//cue[conditions]"):
        assert cue.find("actions")[0].tag=="debug_text"
    assert root.xpath("//cue[@name='EnterRoom']/conditions/check_any/event_player_stopped_control")
    assert root.xpath("//cue[@name='Teleported']/conditions/event_player_teleport_successful")
    assert all(c.getparent().tag=="cues" and c.getparent().getparent().tag=="mdscript"
               for c in root.xpath("//cue[@name='Teleported' or @name='EnterRoom' or @name='LeaveControl']"))
    assert not root.xpath("//reset_cue|//cancel_cue|//destroy_object")

def check_every_continuation_rechecks_world_target_and_stage():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitRoomEntry.xml"])
    for name in ("Teleported","LeaveControl","EnterRoom"):
        gate=root.xpath(f"//cue[@name='{name}']/actions/do_if")[0].get("value")
        for word in ("$P.$session == $B.$session","$P.$generation == $B.$generation","$P.$revision == $B.$revision",
                     "$B.$room.exists","$B.$slot.component.exists","player.entity.hascontext.{$B.$target}",
                     "player.age ge $B.$dispatch_age","player.age le $B.$dispatch_age + 30s", "$B.$room.macro.id == $B.$room_macro_id"):
            assert word in gate
    enter=root.xpath("//cue[@name='EnterRoom']/actions/do_if/do_if")[0]
    assert "not player.controlled" in enter.get("value")
    assert enter[0].get("exact")=="'POSITION_CALLED'"
    assert enter[1].tag=="add_actor_to_room"

def check_consumption_membership_and_exact_interlock():
    files,_=g.prepare();root=E.fromstring(files["md/X4ToolkitRoomEntry.xml"]);xml=files["md/X4ToolkitRoomEntry.xml"].decode()
    assert xml.index('name="$Record.$consumed" exact="true"')<xml.index("<teleport_player")
    assert "$ScopeComplete and $Present == 1 and $Rooms.count le 16" in xml
    assert '$Record.$room.macro.id == $Record.$room_macro_id' in xml
    releases=root.xpath("//set_value[@name='md.X4ToolkitRoomEntry.State.$Busy' and @exact='null']")
    assert len(releases)==4
    for node in releases[1:]:
        value=node.getparent().get("value")
        for exact in ("$Record.$digest == $R.$digest","$Record.$choice == $Choice","$Record.$selector == $R.$selector",
                      "$Record.$positioned","$Entered","$Record.$revision == $R.$public_revision"):
            assert exact in value
    assert not root.xpath("//cue[@name='Status']//teleport_player|//cue[@name='Status']//add_actor_to_room")

def check_initial_unavailable_does_not_read_clear_or_poison():
    r,_=runtime();r.execute("stamp=nil;tick();drain()")
    assert r.eval("reads+clears+sends")==0

def check_prepare_then_transport_remains_running_until_exact_room_observed():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'))")
    assert r.eval("sends")==2
    r.execute("v=status()");assert r.eval("v.status")!="completed"
    r.execute("local x=row('dispatched');x.positioned=true;x.entered=true;deliver(x);v=status()")
    assert r.eval("v.observations.snapshot.entered") is True
    assert r.eval("v.observations.walking_verified") is False

def check_actual_md_channel_preserves_typed_container_and_choice():
    r,_=runtime(real_channel=True);r.execute("registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'))")
    assert r.eval("type(sent.target)")=="cdata" and r.eval("sent.target==ship")
    assert r.eval("type(sent.choice)")=="cdata" and r.eval("sent.choice==0")

def check_ambiguous_native_rooms_require_an_observed_choice_not_index():
    r,_=runtime()
    r.execute("""
        registry.prepare(req);local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0)
        x.room_macro='';x.candidates={room,ffi.new('uint64_t',41)};x.room_count=2;x.candidate_macros={'synthetic_room_macro','synthetic_room_macro'};deliver(x)
        v=registry.status(req.id,req.session,req.digest)
        choice=v.observations.snapshot.candidates[2].choice
        req.id=string.rep('f',32);req.digest=string.rep('1',64);req.arguments.room_choice=choice
        registry.prepare(req)
    """)
    assert r.eval("sent.choice==ffi.new('uint64_t',41)")
    r.execute("local x=row('prepared');x.candidates={room,ffi.new('uint64_t',41)};x.room_count=2;x.candidate_macros={'synthetic_room_macro','synthetic_room_macro'};x.room=ffi.new('uint64_t',41);deliver(x);run()")
    assert r.eval("sends")==3

@pytest.mark.parametrize("change",[
    "req.arguments.room_choice='invented:1'","req.arguments.selector='macro.bad()'",
    "req.arguments.room_choice=1","req.arguments.extra=true","req.arguments.container=nil",
    "req.arguments.selector=nil","req.arguments.selector=string.rep('a',129)",
])
def check_closed_arguments_never_dispatch(change):
    r,_=runtime();r.execute(change+";registry.prepare(req)");assert r.eval("sends")==0

@pytest.mark.parametrize("change",[
    "x.digest=string.rep('f',64)","x.call=string.rep('f',32)","x.phase='execute'",
    "x.session=string.rep('f',32)","x.generation=string.rep('f',32)","x.world_token='other'",
    "x.public_revision=string.rep('f',64)","x.revision=string.rep('f',64)","x.kind='OTHER'",
    "x.extra=true","x.target=ffi.new('uint64_t',3)","x.target_exists=false","x.age=0/0",
    "x.room_count=2","x.candidates={room,room};x.room_count=2","x.candidates={[2]=room}",
    "x.room_exists=false","x.entered=true;x.controlled=true","x.positioned=true",
    "x.status='completed'","x.dispatched=true","x.consumed=true",
])
def check_corrupt_reply_refuses_before_execute(change):
    r,_=runtime();r.execute("registry.prepare(req);local x=row('prepared');"+change+";deliver(x);run()")
    assert r.eval("sends")==1

def check_target_and_selected_room_drift_refuse():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));run();local x=row('dispatched');x.room=ffi.new('uint64_t',41);deliver(x);v=status()")
    assert r.eval("v.status")!="completed"

def check_duplicate_full_batch_never_reentrant_execute():
    r,_=runtime();r.execute("registry.prepare(req);queue={row('prepared'),row('prepared')};drain();run()")
    assert r.eval("sends")==1

def check_send_throw_consumes_without_retry():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));c.send=function()sends=sends+1;error('unknown')end;run();run();status()")
    assert r.eval("sends")==2

def check_timeout_and_world_drift_never_replay():
    r,_=runtime();r.execute("registry.prepare(req);now=11;tick();run();status()")
    assert r.eval("sends")==1
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('prepared'));stamp.session=string.rep('f',32);run()")
    assert r.eval("sends")==1

def check_failed_disposal_sticky_and_callback_inert():
    r,_=runtime();r.execute("registry.prepare(req);c.dispose=function()return false end")
    assert r.eval("attached.dispose()") is False and r.eval("attached.dispose()") is False
    r.execute("queue={row('prepared')};drain()");assert r.eval("reads+clears")==0

@pytest.mark.parametrize("target",["md","provider","self"])
def check_factory_source_drift_refuses(target):
    original=Path.read_bytes;selected=Path(g.__file__) if target=="self" else ROOT/g.PATHS[target];count=0
    def changed(p):
        nonlocal count
        value=original(p)
        if p==selected:
            count+=1
            if count>1:return value+b"\n"
        return value
    with patch.object(Path,"read_bytes",changed),pytest.raises(AssertionError):g.prepare()

def check_observed_choice_cannot_change_selector():
    r,_=runtime();r.execute("registry.prepare(req);deliver(row('refused'));v=registry.status(req.id,req.session,req.digest)")
    r.execute("choice=v.observations.snapshot.candidates[1].choice;req.id=string.rep('f',32);req.digest=string.rep('1',64);req.arguments.room_choice=choice;req.arguments.selector='other_macro';registry.prepare(req)")
    assert r.eval("sends")==1  # no pending read could mask the changed-selector refusal

def check_unavailable_initial_tick_can_later_prepare_without_recreation():
    r,_=runtime();r.execute("saved=stamp;stamp=nil;tick();drain();stamp=saved;registry.prepare(req)")
    assert r.eval("sends")==1

def check_native_over_cap_refusal_preserves_denominator_without_partial_choices():
    r,_=runtime();r.execute("registry.prepare(req);local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro='';x.room_count=17;x.candidates={};x.candidate_macros={};deliver(x);v=registry.status(req.id,req.session,req.digest)")
    assert r.eval("v.observations.snapshot.room_count")==17
    assert r.eval("#v.observations.snapshot.candidates")==0
    assert r.eval("v.status")=="refused"

def check_channel_disposal_registered_before_provider_constructor_can_fail():
    r,_=runtime(constructor_failure=True)
    assert r.eval("constructor_ok") is False
    assert r.eval("#hooks")==1
    assert r.eval("hooks[1]==c.dispose") is True
    assert r.eval("hooks[1]()") is True
    assert r.eval("disposals")==1
    assert r.eval("sends+reads+clears")==0

def check_dynamic_preview_requires_choice_even_with_one_room():
    r,_=runtime();r.execute("req.arguments.selector='dynamicrooms';registry.prepare(req);local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro='';x.scope_count=1;deliver(x);v=registry.status(req.id,req.session,req.digest)")
    assert r.eval("v.status")=="refused"
    assert r.eval("v.observations.snapshot.choice_required") is True
    assert r.eval("v.observations.snapshot.macro_ids[1]")=="synthetic_room_macro"
    assert r.eval("v.observations.snapshot.candidates[1].macro_index")==1
    assert r.eval("sends")==1

def check_dynamic_preview_selection_then_exact_observed_room():
    r,_=runtime();r.execute("""
        req.arguments.selector='dynamicrooms';registry.prepare(req)
        local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro='';x.scope_count=1;deliver(x)
        v=registry.status(req.id,req.session,req.digest);choice=v.observations.snapshot.candidates[1].choice
        req.id=string.rep('f',32);req.digest=string.rep('1',64);req.arguments.room_choice=choice
        registry.prepare(req);deliver(row('prepared'));run();deliver(row('dispatched'))
        v=status();local x=row('dispatched');x.positioned=true;x.entered=true;deliver(x);v=status()
    """)
    assert r.eval("v.observations.snapshot.entered") is True
    assert r.eval("v.observations.snapshot.room_macro")=="synthetic_room_macro"
    assert r.eval("v.observations.snapshot.room_query_observed") is False
    assert r.eval("v.observations.snapshot.room_count") is None

def check_dynamic_context_incomplete_is_unknown_count_not_zero():
    r,_=runtime();r.execute("req.arguments.selector='dynamicrooms';registry.prepare(req);local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro='';x.scope_count=17;x.scope_complete=false;x.room_count=false;x.candidates={};x.candidate_macros={};deliver(x);v=registry.status(req.id,req.session,req.digest)")
    assert r.eval("v.observations.snapshot.scope_complete") is False
    assert r.eval("v.observations.snapshot.room_count") is False
    assert r.eval("v.observations.snapshot.scope_count")==17
    assert r.eval("v.status")=="refused"

@pytest.mark.parametrize("change",[
    "x.scope_complete=false", "x.scope_count=0/0", "x.candidate_macros={}",
    "x.candidate_macros={'not a macro'}", "x.room_macro='different_macro'", "x.choice_required=true",
])
def check_preview_metadata_corruption_refuses(change):
    r,_=runtime();r.execute("registry.prepare(req);local x=row('prepared');"+change+";deliver(x);run()")
    assert r.eval("sends")==1

def check_dynamic_md_scopes_and_never_automatic_first_match():
    files,_=g.prepare();root=E.fromstring(files['md/X4ToolkitRoomEntry.xml']);xml=files['md/X4ToolkitRoomEntry.xml'].decode()
    assert root.xpath("//find_object_component[@class='class.navcontext' and @multiple='true']")
    assert '$Interiors.{$interior_index}.rooms' in xml
    assert '$InteriorRooms.count le 64' in xml and '$Interiors.count le 16' in xml
    assert "$Rooms.count == 1 and $R.$selector != 'dynamicrooms'" in xml
    assert 'if $ScopeComplete then $RoomCount else false' in xml
    assert '$Rooms.{$j}.macro.id' in xml

def check_max_preview_wire_overflow_settles_callback_unknown_once():
    r,_=runtime();r.execute("""
        req.arguments.selector='dynamicrooms';registry.prepare(req)
        local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro=''
        x.room_count=16;x.candidates={};x.candidate_macros={}
        for i=1,16 do x.candidates[i]=ffi.new('uint64_t',40+i);x.candidate_macros[i]=string.rep('a',115)..i..'_macro'end
        deliver(x);v=registry.status(req.id,req.session,req.digest);run()
    """)
    assert r.eval("v.status")=="unknown"
    assert r.eval("sends")==1
    assert r.eval("v.observations.snapshot") is None
    assert r.eval("#wire.encode(v)")<4096

def check_dynamic_choice_cannot_transfer_to_other_container_identity():
    r,_=runtime();r.execute("""
        req.arguments.selector='dynamicrooms';registry.prepare(req)
        local x=row('refused');x.room_exists=false;x.room=ffi.new('uint64_t',0);x.room_macro='';deliver(x)
        v=registry.status(req.id,req.session,req.digest);choice=v.observations.snapshot.candidates[1].choice
        req.id=string.rep('f',32);req.digest=string.rep('1',64);req.arguments.room_choice=choice
        ship=ffi.new('uint64_t',123);registry.prepare(req)
    """)
    assert r.eval("sends")==1

def check_native_dynamic_preview_cannot_falsely_prepare_singleton():
    r,_=runtime();r.execute("req.arguments.selector='dynamicrooms';registry.prepare(req);deliver(row('prepared'));run()")
    assert r.eval("sends")==1

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

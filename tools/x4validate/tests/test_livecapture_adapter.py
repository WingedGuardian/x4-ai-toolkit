"""Actual frozen SDK + generated adapter; synthetic channel, never engine calls."""
import unittest
import os, subprocess, sys
import pytest
from lupa.luajit21 import LuaRuntime
from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("public_capture_extension",ROOT/"scripts/live_capture_extension.py")
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
C,INV=P.prepare_parts()
SETUP="""
now=1; sent={}; queue=nil; callback=nil; reads=0; clears=0; disposed=0; auto=true
nonce=string.rep('a',32);generation=string.rep('b',32)
stamp={provider='x4-live-session-v1',revision=string.rep('9',64),session=nonce,generation=generation,world_token='world'}
nativeRecords={}
function reply(changes,selected)
 local w=selected or sent[#sent];local rec=nativeRecords[w.id] or {state='prepared',released=false}
 nativeRecords[w.id]=rec
 if w.phase=='execute' then rec.state='running' end
 if w.phase=='release' then rec.released=true end
 local o={bridge_phase=w.phase,bridge_call=w.call,bridge_generation=w.generation,
 revision=mdrevision,directory='x4_toolkit_capture',file_confirmed=false,visual_pass=false,
 age=5,prepared_age=1,capture_age=w.phase=='prepare' and nil or 5,
 reservation_released=rec.released,camera_yaw=-2}
 local r={version=1,id=w.id,session=w.session,digest=w.digest,status=rec.state,reason=w.phase=='release' and 'RESERVATION_RELEASED_NOT_FILE_CONFIRMATION' or 'observed',observations=o}
 if changes then changes(r) end
 queue={r};callback();return r
end
env={now=function()return now end,public_current=function()return stamp end,
 subscribe=function(cb)callback=cb;if subscribefail then error('subscribe')end end,
 read=function()reads=reads+1;return queue end,clear=function()clears=clears+1;queue=nil end,
 component_id=function(x)if x=='bad' then error('object')end;return '123ULL'end,
 dispose=function()disposed=disposed+1;if disposefail then return false end;return true end,
 send=function(control,w)assert(control=='request');sent[#sent+1]=w
   if sendfail then error('send')end
   if auto then reply(mutator)end end}
sdk=sdkfactory();adapter=adapterfactory(sdk,env);serial=0
function req(action,args)
 serial=serial+1
 return {version=1,id=string.format('%032x',serial),session=nonce,provider='x4-screen-capture-v1',
 action=action,arguments=args or {view_binding='hull'},binding=revision,digest=string.format('%064x',serial)}
end
function prep() r=req('capture');return sdk.prepare(r)end
function exec()return sdk.execute(r.id,r.session,r.digest)end
function status()return sdk.status(r.id,r.session,r.digest)end
function releaseprep()
 capture=r;r=req('release',{capture_id=capture.id,capture_digest=capture.digest});return sdk.prepare(r)
end
"""
def runtime():
    l=LuaRuntime(unpack_returned_tuples=True);g=l.globals()
    g.sdkfactory=l.execute(P.SDK.read_text(encoding="utf-8"))
    g.adapterfactory=l.execute(C["ui/x4_capture_adapter.lua"].decode())
    g.revision=INV["revision"];g.mdrevision=INV["native_revision"]
    l.execute(SETUP);return l
class AdapterTests(unittest.TestCase):
    __test__ = False
    def test_constructor_does_not_read_public_identity(self):
        l=runtime();l.execute("adapter.dispose();env.public_current=function()error('must not read during construction')end;sdk=sdkfactory();adapter=adapterfactory(sdk,env)")
        self.assertEqual(l.eval("#sent"),0)
        self.assertEqual(l.eval("reads+clears"),0)
    def test_unready_state_and_session_do_not_quarantine_first_handshake(self):
        l=runtime();l.execute("saved=stamp;stamp=nil;first=adapter.state();before=#sent;result=prep()")
        self.assertFalse(l.eval("first.ready"));self.assertIsNone(l.eval("first.fault"))
        self.assertNotEqual(l.eval("result.status"),"prepared")
        self.assertEqual(l.eval("#sent"),0);self.assertEqual(l.eval("reads+clears"),0)
        l.execute("stamp=saved")
        self.assertEqual(l.eval("prep().status"),"prepared")
    def test_throwing_initial_public_lookup_remains_unavailable_without_replay(self):
        l=runtime();l.execute("env.public_current=function()error('public handshake pending')end;first=adapter.state();result=prep()")
        self.assertFalse(l.eval("first.ready"));self.assertIsNone(l.eval("first.fault"))
        self.assertNotEqual(l.eval("result.status"),"prepared");self.assertEqual(l.eval("#sent"),0)
        l.execute("env.public_current=function()return stamp end")
        self.assertEqual(l.eval("prep().status"),"prepared")
    def test_initial_queue_event_cannot_bind_or_clear(self):
        l=runtime();l.execute("saved=stamp;stamp=nil;queue={untrusted='reply'};callback();first=adapter.state()")
        self.assertEqual(l.eval("reads+clears"),0);self.assertTrue(l.eval("queue~=nil"))
        self.assertFalse(l.eval("first.ready"));self.assertIsNone(l.eval("first.fault"))
        l.execute("stamp=saved;ready=adapter.state()")
        self.assertTrue(l.eval("ready.ready"));self.assertEqual(l.eval("reads+clears+#sent"),0)
    def test_bound_public_loss_is_sticky_even_before_first_capture(self):
        l=runtime();l.execute("adapter.state();saved=stamp;stamp=nil;lost=adapter.state();stamp=saved;restored=adapter.state()")
        self.assertFalse(l.eval("lost.ready"));self.assertFalse(l.eval("restored.ready"))
        self.assertIsNotNone(l.eval("restored.fault"));self.assertEqual(l.eval("#sent"),0)
    def test_bound_identity_drift_cannot_rebind_before_first_capture(self):
        l=runtime();l.execute("adapter.state();stamp.generation=string.rep('f',32);changed=adapter.state()")
        self.assertFalse(l.eval("changed.ready"));self.assertIsNotNone(l.eval("changed.fault"))
        self.assertNotEqual(l.eval("prep().status"),"prepared");self.assertEqual(l.eval("#sent"),0)
    def test_constructor_no_dispatch(self):
        l=runtime();self.assertEqual(l.eval("#sent"),0)
    def test_prepare_and_capture_are_not_file_completion(self):
        l=runtime();self.assertEqual(l.eval("prep().status"),"prepared")
        self.assertEqual(l.eval("exec().status"),"running")
        self.assertFalse(l.eval("status().observations.file_completion_qualified"))
        self.assertEqual(l.eval("status().observations.native_revision"),INV["native_revision"])
    def test_release_exact_original_native_identity(self):
        l=runtime();l.execute("prep();exec();releaseprep();result=exec()")
        self.assertEqual(l.eval("result.status"),"completed")
        self.assertEqual(l.eval("sent[#sent].id==capture.id and sent[#sent].digest==capture.digest"),True)
        self.assertTrue(l.eval("result.observations.reservation_released"))
        self.assertFalse(l.eval("result.observations.file_completion_qualified"))
        self.assertFalse(l.eval("result.observations.visual_pass"))
    def test_only_confirmed_release_allows_next_capture(self):
        l=runtime();l.execute("prep();exec();releaseprep();exec()")
        self.assertEqual(l.eval("prep().status"),"prepared")
    def test_execute_never_replays(self):
        l=runtime();l.execute("prep();exec();before=#sent;exec();exec()");self.assertEqual(l.eval("#sent"),l.eval("before"))
    def test_release_never_replays(self):
        l=runtime();l.execute("prep();exec();releaseprep();exec();before=#sent;exec();status()")
        self.assertEqual(l.eval("#sent"),l.eval("before"))
    def test_capture_busy_blocks_second(self):
        l=runtime();l.execute("prep();exec()");self.assertEqual(l.eval("prep().status"),"unknown")
        self.assertEqual(l.eval("#sent"),2)
    def test_prepared_capture_cannot_release(self):
        l=runtime();l.execute("prep()");self.assertEqual(l.eval("releaseprep().status"),"unknown");self.assertEqual(l.eval("#sent"),1)
    def test_foreign_release_refuses(self):
        l=runtime();l.execute("prep();exec();r=req('release',{capture_id=string.rep('f',32),capture_digest=r.digest})")
        self.assertEqual(l.eval("sdk.prepare(r).status"),"unknown");self.assertEqual(l.eval("#sent"),2)
    def test_wrong_target_digest_refuses(self):
        l=runtime();l.execute("prep();exec();r=req('release',{capture_id=r.id,capture_digest=string.rep('f',64)})")
        self.assertEqual(l.eval("sdk.prepare(r).status"),"unknown")
    def test_two_release_tickets_refuse_second(self):
        l=runtime();l.execute("prep();exec();releaseprep();first=r;r=req('release',first.arguments)")
        self.assertEqual(l.eval("sdk.prepare(r).status"),"unknown")
    def test_unknown_release_does_not_open_reservation(self):
        l=runtime();l.execute("prep();exec();releaseprep();auto=false;exec();now=12;adapter.state()")
        self.assertEqual(l.eval("status().status"),"unknown")
        self.assertFalse(l.eval("adapter.state().ready"))
        self.assertEqual(l.eval("prep().status"),"unknown")
    def test_unconfirmed_release_refuses_completion(self):
        l=runtime();l.execute("prep();exec();releaseprep();mutator=function(x)x.observations.reservation_released=false end")
        self.assertNotEqual(l.eval("exec().status"),"completed")
        self.assertFalse(l.eval("adapter.state().ready"))
    def test_release_status_cannot_change_capture_state(self):
        l=runtime();l.execute("prep();exec();releaseprep();mutator=function(x)x.status='refused' end")
        self.assertNotEqual(l.eval("exec().status"),"completed")
        self.assertFalse(l.eval("adapter.state().ready"))
    def test_release_reason_must_confirm_bookkeeping(self):
        l=runtime();l.execute("prep();exec();releaseprep();mutator=function(x)x.reason='UNKNOWN_OR_UNQUALIFIED_REQUEST' end")
        self.assertNotEqual(l.eval("exec().status"),"completed")
    def test_native_refused_capture_can_only_release_bookkeeping(self):
        l=runtime();l.execute("prep();mutator=function(x)x.status='refused';nativeRecords[x.id].state='refused' end;exec();mutator=nil;releaseprep();result=exec()")
        self.assertEqual(l.eval("result.status"),"completed")
        self.assertFalse(l.eval("result.observations.file_completion_qualified"))
    def test_duplicate_batch_preflight_prevents_callback_dispatch(self):
        l=runtime();l.execute("""
adapter.dispose();auto=false;observed=0
direct={register=function(x)spec=x end}
adapter=adapterfactory(direct,env);q=req('capture')
spec.invoke('prepare',q,function(result)
 observed=observed+1
 spec.invoke('execute',q,function()end)
end)
local w=sent[#sent]
local o={bridge_phase=w.phase,bridge_call=w.call,bridge_generation=w.generation,
 revision=mdrevision,directory='x4_toolkit_capture',file_confirmed=false,visual_pass=false,age=1}
local r={version=1,id=w.id,session=w.session,digest=w.digest,status='prepared',reason='observed',observations=o}
queue={r,r};before=#sent;callback()
""")
        self.assertEqual(l.eval("#sent"),l.eval("before"))
        self.assertFalse(l.eval("adapter.state().ready"))
    def test_timeout_late_reply_stays_quarantined(self):
        l=runtime();l.execute("auto=false;prep();now=12;adapter.state();before=reads;reply()")
        self.assertEqual(l.eval("reads"),l.eval("before"));self.assertEqual(l.eval("status().status"),"unknown")
    def test_send_failure_quarantines(self):
        l=runtime();l.execute("sendfail=true");self.assertEqual(l.eval("prep().status"),"unknown")
        l.execute("before=#sent;exec();status()");self.assertEqual(l.eval("#sent"),l.eval("before"))
    def test_rebound_receipt_refuses(self):
        for code in ("x.id=string.rep('f',32)","x.digest=string.rep('f',64)",
                     "x.session=string.rep('f',32)","x.observations.bridge_generation=string.rep('f',32)",
                     "x.observations.revision=string.rep('f',64)","x.observations.bridge_phase='release'",
                     "x.observations.directory='other'","x.observations.file_confirmed=true",
                     "x.observations.visual_pass=true","x.status='completed'"):
            with self.subTest(code=code):
                l=runtime();l.execute("mutator=function(x)"+code+" end")
                self.assertNotEqual(l.eval("prep().status"),"prepared");self.assertFalse(l.eval("adapter.state().ready"))
    def test_duplicate_receipt_faults(self):
        l=runtime();l.execute("prep();reply()");self.assertFalse(l.eval("adapter.state().ready"))
    def test_public_drift_before_action(self):
        for field in ("session","generation","revision","world_token"):
            l=runtime();l.execute("prep();stamp."+field+"='changed';before=#sent")
            self.assertEqual(l.eval("exec().status"),"unknown");self.assertEqual(l.eval("#sent"),l.eval("before"))
    def test_public_drift_pending_reply(self):
        l=runtime();l.execute("auto=false;prep();stamp.generation=string.rep('f',32);reply()")
        self.assertEqual(l.eval("status().status"),"unknown")
    def test_backward_clock_refuses(self):
        l=runtime();l.execute("prep();now=0");self.assertEqual(l.eval("exec().status"),"unknown")
    def test_bad_request_binding_or_args(self):
        for code in ("r.binding='bad'","r.arguments.extra=true","r.arguments={}"):
            l=runtime();l.execute("r=req('capture');"+code);self.assertEqual(l.eval("sdk.prepare(r).status"),"unknown")
            self.assertEqual(l.eval("#sent"),0)
    def test_target_objects_normalized_or_unknown(self):
        l=runtime();l.execute("mutator=function(x)x.observations.prepared_target='native';x.observations.capture_target='bad' end;prep()")
        self.assertEqual(l.eval("status().observations.prepared_target.id"),"123ULL")
        self.assertEqual(l.eval("status().observations.capture_target.status"),"UNKNOWN")
    def test_negative_camera_angle_preserved(self):
        l=runtime();l.execute("prep()");self.assertEqual(l.eval("status().observations.camera_yaw"),-2)
    def test_dispose_inerts_subscription(self):
        l=runtime();l.execute("adapter.dispose();before=reads;queue={};callback()")
        self.assertEqual(l.eval("reads"),l.eval("before"));self.assertEqual(l.eval("disposed"),1)
    def test_dispose_failure_sticky(self):
        l=runtime();l.execute("disposefail=true")
        self.assertFalse(l.eval("adapter.dispose()"));l.execute("disposefail=false")
        self.assertFalse(l.eval("adapter.dispose()"));self.assertEqual(l.eval("disposed"),1)
    def test_constructor_registration_failure_retires(self):
        l=runtime();l.execute("adapter.dispose();bad={register=function()error('registry')end};ok=pcall(adapterfactory,bad,env);before=reads;queue={};callback()")
        self.assertFalse(l.eval("ok"));self.assertEqual(l.eval("reads"),l.eval("before"))
    def test_constructor_subscription_failure_retires(self):
        l=runtime();l.execute("adapter.dispose();subscribefail=true;ok=pcall(adapterfactory,sdkfactory(),env);before=reads;queue={};callback()")
        self.assertFalse(l.eval("ok"));self.assertEqual(l.eval("reads"),l.eval("before"))

    def test_delayed_preparation_status_does_not_send_again(self):
        l=runtime();l.execute("auto=false;prep();before=#sent")
        self.assertEqual(l.eval("status().status"),"unknown")
        self.assertEqual(l.eval("#sent"),l.eval("before"))
        l.execute("reply();result=status()")
        self.assertEqual(l.eval("result.status"),"prepared")
    def test_delayed_status_does_not_manufacture_execute_deadlock(self):
        l=runtime();l.execute("prep();auto=false;status();readwire=sent[#sent];result=exec()")
        self.assertEqual(l.eval("result.status"),"running")
        self.assertEqual(l.eval("sent[#sent].phase"),"execute")
        l.execute("executewire=sent[#sent];reply(nil,executewire);reply(function(x)x.status='prepared';x.observations.age=1 end,readwire)")
        self.assertEqual(l.eval("status().status"),"running")
    def test_status_coalesces_while_read_inflight(self):
        l=runtime();l.execute("prep();exec();auto=false;status();before=#sent;status();status();status()")
        self.assertEqual(l.eval("#sent"),l.eval("before"))
        self.assertTrue(l.eval("status().observations.native_observation_cached"))
        self.assertTrue(l.eval("status().observations.native_status_refresh_pending"))
    def test_release_permitted_with_only_read_pending(self):
        l=runtime();l.execute("prep();exec();auto=false;status();readwire=sent[#sent]")
        self.assertEqual(l.eval("releaseprep().status"),"prepared")
        l.execute("exec();releasewire=sent[#sent];reply(nil,releasewire)")
        self.assertEqual(l.eval("status().status"),"completed")
        l.execute("reply(nil,readwire)")
        self.assertTrue(l.eval("adapter.state().ready"))
    def test_release_blocked_by_unresolved_execute(self):
        l=runtime();l.execute("prep();auto=false;exec()")
        self.assertEqual(l.eval("releaseprep().status"),"unknown")
    def test_read_timeout_faults_even_after_cached_running(self):
        l=runtime();l.execute("prep();exec();auto=false;status();now=12")
        self.assertEqual(l.eval("status().status"),"unknown")
        self.assertFalse(l.eval("adapter.state().ready"))
    def test_mutated_delayed_status_does_not_replace_cache(self):
        l=runtime();l.execute("prep();exec();auto=false;status();reply(function(x)x.digest=string.rep('f',64)end)")
        self.assertEqual(l.eval("status().status"),"unknown")
    def test_duplicate_detached_status_still_faults(self):
        l=runtime();l.execute("prep();exec();auto=false;status();wire=sent[#sent];reply(nil,wire);reply(nil,wire)")
        self.assertFalse(l.eval("adapter.state().ready"))

    def test_factory_preserves_md(self):
        files,current=P.prepare()
        self.assertEqual(current["md_sha256"],P.sha(files["md/X4ToolkitCapture.xml"]))
        self.assertEqual(INV["md_sha256"],current["md_sha256"])
        self.assertFalse(current["runtime_verified"])
    def test_loader_registers_channel_disposal_before_adapter(self):
        l=runtime();l.globals().loader=l.execute(C["ui/x4_capture_extension.lua"].decode())
        l.execute("descriptor=nil;loader({register_extension=function(x)descriptor=x end},function()error('constructor')end);hooks={};ctx={create_md_channel=function()return {dispose=function()return true end}end,on_dispose=function(f)hooks[#hooks+1]=f end};ok=pcall(descriptor.attach,ctx)")
        self.assertFalse(l.eval("ok"));self.assertEqual(l.eval("#hooks"),1)

def bundle_runtime():
    l=runtime();l.execute("adapter.dispose();timers={}")
    builder=P.bundle_builder()
    sources={key:b"return function() end" for key in builder.MODULES}
    sources["actions"]=P.SDK.read_bytes()
    sources["mdchannel"]=P.CHANNEL.read_bytes()
    sources["bootstrap"]=b"""return function(d)
      sdk=d.modules.actions()
      return {dispose=function()return true end,register_provider=sdk.register,
        encode=function()return '' end,session={current=function()return stamp end}}
    end"""
    sources["nativeassembly"]=b"""return {read_verbs=function()return {}end,new=function(d)
      return {read=function()error('unused')end,dispose=function()return true end,
        provider_spec={id='x4-native-v1',revision=d.build,actions={unused=true},
          session=function()return stamp.session end,invoke=function()error('unused')end}}
    end}"""
    payload,receipt=builder.build(sources,"9"*64)
    assert builder.verify(payload)==receipt["build"]
    l.execute(payload.decode())
    final,_=P.prepare()
    l.execute(final["ui/live_capture_extension.lua"].decode())
    l.execute("""
ffi=require('ffi')
g={RegisterEvent=function(event,cb)assert(event=='X4ToolkitCapture.Replies');callback=cb end,
 GetNPCBlackboard=function(id,key)assert(id==42 and key=='$x4_toolkit_capture_replies');reads=reads+1;return queue end,
 SetNPCBlackboard=function(id,key,value)assert(id==42 and key=='$x4_toolkit_capture_replies' and value==nil);clears=clears+1;queue=nil end,
 ConvertStringTo64Bit=function()return 42 end,ConvertIDTo64Bit=function()return 123 end,
 getElapsedTime=function()return now end,
 AddUITriggeredEvent=function(screen,control,w)
  assert(screen=='X4ToolkitCapture' and control=='request');sent[#sent+1]=w
  if auto then reply(mutator) end
 end}
X4ToolkitAutomationBundle.new{read=function()error('unused')end,read_verbs={},ffi=ffi,
 C={GetPlayerID=function()return 42ULL end},globals=g,
 Helper={addDelayedOneTimeCallbackOnUpdate=function(fn,block,deadline)
  assert(block==false and deadline==now+10.25);timers[#timers+1]=fn
 end}}
""")
    return l

class GeneratedBundleTests(unittest.TestCase):
    __test__ = False
    def test_generated_catalog_actual_sdk_channel_capture_release(self):
        l=bundle_runtime();self.assertEqual(l.eval("prep().status"),"prepared")
        self.assertEqual(l.eval("exec().status"),"running")
        l.execute("releaseprep();result=exec()")
        self.assertEqual(l.eval("result.status"),"completed")
        self.assertFalse(l.eval("result.observations.file_completion_qualified"))
        self.assertEqual(l.eval("reads"),3);self.assertEqual(l.eval("clears"),3)
    def test_generated_channel_deadline_retires_without_resend(self):
        l=bundle_runtime();l.execute("auto=false;prep();before=#sent;now=12;timers[1]()")
        self.assertEqual(l.eval("status().status"),"unknown")
        self.assertEqual(l.eval("#sent"),l.eval("before"))
    def test_generated_bundle_dispose_retires_queue_reader(self):
        l=bundle_runtime();l.execute("auto=false;prep();X4ToolkitAutomationBundle.dispose();before=reads;queue={};callback();now=12;timers[1]()")
        self.assertEqual(l.eval("reads"),l.eval("before"));self.assertTrue(l.eval("queue~=nil"))

CASES=[f"{cls.__name__}.{name}" for cls in (AdapterTests,GeneratedBundleTests)
       for name in unittest.defaultTestLoader.getTestCaseNames(cls)]

@pytest.mark.parametrize("case",CASES)
def test_capture_adapter_child(case):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),case],
        capture_output=True,text=True,timeout=30,
        env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,(result.stdout+result.stderr)[-16000:]
    assert result.stdout.strip().endswith("CONTROL_PASSED"),(result.stdout+result.stderr)[-16000:]

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in CASES:raise SystemExit("one exact control required")
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromName(sys.argv[1],sys.modules[__name__]))
    if not result.wasSuccessful():raise SystemExit(1)
    print("CONTROL_PASSED")

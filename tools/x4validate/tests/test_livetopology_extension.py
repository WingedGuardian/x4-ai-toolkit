"""Pure source closure and actual generated extension + SDK/native-channel fixture."""
from pathlib import Path
import importlib.util, hashlib, json, os, subprocess, sys
from unittest.mock import patch
import pytest
from test_livetopology import runtime
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("topology_extension",ROOT/"scripts/live_topology_extension.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def check_pure_factory_exact_outputs_and_all_source_pins():
    files,receipt=g.prepare()
    assert set(files)=={"md/X4ToolkitTopology.xml","ui/live_topology_extension.lua"}
    assert receipt["runtime_verified"] is False and receipt["atomic"] is False
    for p,h in receipt["source_sha256"].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
    for p,h in receipt["output_sha256"].items():assert hashlib.sha256(files[p]).hexdigest()==h
    assert hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest()==receipt["generator_sha256"]

@pytest.mark.parametrize("target",["md","observer","provider","self"])
def check_mid_generation_source_drift_refused(target):
    original=Path.read_bytes;count=0
    selected=Path(g.__file__) if target=="self" else ROOT/g.PATHS[target]
    def changed(p):
        nonlocal count
        data=original(p)
        if p==selected:
            count+=1
            if count>1:return data+b"\n"
        return data
    with patch.object(Path,"read_bytes",changed),pytest.raises(ValueError):g.prepare()

def check_generated_loader_actual_observer_provider_sdk_and_delayed_reply():
    files,receipt=g.prepare();r=runtime()
    r.globals().sdk=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actions.lua").read_text())
    r.execute("X4ToolkitAutomationBundle={register_extension=function(v) descriptor=v end}")
    r.execute(files["ui/live_topology_extension.lua"].decode())
    assert r.eval("descriptor.providers['x4-topology-observer-v1']")==receipt["revision"]
    r.globals().mdrevision=receipt["md_revision"]
    r.execute("""
        registry=sdk();callbacks={}
        attached=descriptor.attach{public_current=function()return stamp end,encode=wire.encode,
            registry=registry,on_dispose=function(f)callbacks[#callbacks+1]=f end,
            create_md_channel=function(config)
                assert(config.screen=='X4ToolkitTopology' and config.event=='X4ToolkitTopology.Replies')
                assert(config.queue=='$x4_toolkit_topology_replies' and config.controls.snapshot==true)
                return c
            end}
        req={version=1,id=id,session=stamp.session,provider='x4-topology-observer-v1',action='capture',
            arguments={kind='gates',sector_macro='sector_macro'},binding=descriptor.revision,digest=string.rep('f',64)}
    """)
    assert r.eval("sends")==0 and r.eval("#callbacks")==2
    assert r.eval("registry.prepare(req).status")=="prepared" and r.eval("sends")==0
    assert r.eval("registry.execute(req.id,req.session,req.digest).status")=="running"
    assert r.eval("registry.status(req.id,req.session,req.digest).status")=="running" and r.eval("sends")==1
    r.execute("local x=row(1);x.revision=mdrevision;deliver(x);result=registry.status(req.id,req.session,req.digest)")
    assert r.eval("result.status")=="completed"
    assert json.loads(r.eval("result.observations.payload_json"))["enumerated_total"]==1
    assert r.eval("attached.dispose()") is True
    r.execute("for _,fn in ipairs(callbacks) do assert(fn()==true) end")
    assert r.eval("registry.status(req.id,req.session,req.digest).status")=="unknown"

def check_missing_encoder_refuses_before_channel_creation():
    files,_=g.prepare();r=runtime()
    r.execute("X4ToolkitAutomationBundle={register_extension=function(v) descriptor=v end}")
    r.execute(files["ui/live_topology_extension.lua"].decode())
    assert r.execute("return pcall(descriptor.attach,{create_md_channel=function()error('must not call')end})")[0] is False

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
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

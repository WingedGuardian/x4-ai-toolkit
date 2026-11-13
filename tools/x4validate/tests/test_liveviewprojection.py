"""Actual LuaJIT projection boundary controls with synthetic native values."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
luajit=pytest.importorskip("lupa.luajit21")
ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/"mods/x4_toolkit_helper/ui/live_viewprojection.lua"
SETUP=r"""
ffi=require('ffi')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
id=18446744073709551615ULL; message=17; connection=''; message_type='object'; valid=true
calls=0;reads=0;clockcalls=0;resolves=0;mode=true;ref=id
function count()calls=calls+1;if hook then hook(calls)end end
C={GetSofttarget2=function()count();return ffi.new('SofttargetDetails2',{id,connection,message})end,
GetMessageDetails3=function(m)count();assert(m==message);return ffi.new('MessageDetails3',{0,id,message_type,connection,false,false,false,true,false})end,
IsExternalViewActive=function()count();return mode end,
GetExternalViewRefObject=function()count();return ref end}
g={IsValidComponent=function(v)return valid end,
GetMessageScreenPosition=function(m)reads=reads+1;assert(m==message);if during then during()end;return 20,-5,true,1234 end}
d={ffi=ffi,C=C,globals=g,is_valid=g.IsValidComponent,current=function()return stamp end,
now=function()clockcalls=clockcalls+1;return clockcalls end,
resolve_issued=function(t,k)assert(t=='issued' and k=='object');resolves=resolves+1;return id end}
"""
def build(before=""):
    l=luajit.LuaRuntime(unpack_returned_tuples=True)
    l.execute(SETUP+before)
    l.globals().wire=l.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text(encoding="utf-8"))
    l.execute("wire=wire();d.encode=wire.encode")
    l.globals().factory=l.execute(SOURCE.read_text(encoding="utf-8"))
    l.execute("api=factory(d)")
    return l

def check_optional_core_global_unavailable():
    l=build("g.GetMessageScreenPosition=nil;")
    l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="UNAVAILABLE"
    assert "unavailable in this Lua environment" in l.eval("r.reason")
    assert l.eval("calls+reads+resolves")==0

def check_validity_conversion_refuses_before_native_data():
    l=build("d.is_valid=function()error('conversion refused')end;")
    l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="UNAVAILABLE" and l.eval("calls+reads")==0

def check_observation():
    l=build();assert l.eval("calls")==0
    l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="OBSERVED",l.eval("r.reason")
    assert l.eval("r.binding.component")=="18446744073709551615"
    assert l.eval("r.projection.camera_distance_native")==1234
    assert l.eval("r.projection.on_screen") is True
    assert l.eval("calls")==8 and l.eval("reads")==1
    for k in ("atomic","achieved_pose_verified","cutscene_view_verified","rendered_pixels_verified","nested_handle_issued","runtime_verified"):
        assert l.eval("r."+k) is False
    l.execute("encoded=wire.encode({payload=wire.encode(r)})")
    assert len(l.eval("encoded").encode())<=4096

@pytest.mark.parametrize("code",[
    "message=0","id=0ULL","connection=nil","message_type=nil","connection=string.rep('x',129)",
    "C.GetSofttarget2=function()return {}end","C.GetMessageDetails3=function()return nil end",
    "C.GetMessageDetails3=function()return ffi.new('MessageDetails3',{0,42,'object','',false,false,false,true,false})end",
    "C.GetMessageDetails3=function()return ffi.new('MessageDetails3',{0,id,'object','foreign',false,false,false,true,false})end",
    "d.resolve_issued=function()return 9007199254740992 end","valid=false",
    "C.IsExternalViewActive=function()return 1 end","C.GetExternalViewRefObject=function()return '4' end",
])
def check_bad_binding(code):
    l=build(code+";");l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="UNAVAILABLE"
    assert l.eval("reads")==0

@pytest.mark.parametrize("change",[
    "message=18","id=42ULL","connection='different'","message_type='other'","mode=false","ref=3ULL",
    "stamp.generation=string.rep('f',32)","valid=false",
])
def check_drift(change):
    l=build("during=function()"+change+" end;");l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="UNAVAILABLE" and l.eval("reads")==1

@pytest.mark.parametrize("values",[
    "nil,nil,nil,nil","0/0,0,true,1","0,math.huge,true,1","0,0,1,1","0,0,true,-1","0,0,true,math.huge",
])
def check_projection_failure(values):
    l=build("g.GetMessageScreenPosition=function()return "+values+" end;")
    assert l.eval("api.read('issued').status")=="UNAVAILABLE"

def check_offscreen_zero_distance_retained():
    l=build("g.GetMessageScreenPosition=function()return 0,0,false,0 end;")
    l.execute("r=api.read('issued')")
    assert l.eval("r.status")=="OBSERVED" and l.eval("r.projection.on_screen") is False
    assert l.eval("r.projection.camera_distance_native")==0

@pytest.mark.parametrize("declaration",[
    "typedef int64_t UniverseID;",
    "typedef struct {UniverseID softtargetID;const char* softtargetConnectionName;int32_t messageID;} SofttargetDetails2;",
    "typedef struct {const char* softtargetConnectionName;UniverseID softtargetID;uint32_t messageID;} SofttargetDetails2;",
    "typedef struct {UniverseID softtargetID;char* softtargetConnectionName;uint64_t messageID;} SofttargetDetails2;",
])
def check_wrong_abi(declaration):
    prefix="" if declaration=="typedef int64_t UniverseID;" else "typedef uint64_t UniverseID;"
    with pytest.raises(luajit.LuaError,match='native unsigned identity|projection ABI'):
        build("ffi.cdef("+json.dumps(prefix+declaration)+");")

def check_native_first():
    l=build("ffi.cdef('typedef uint64_t UniverseID;typedef struct {UniverseID softtargetID;const char* softtargetConnectionName;uint32_t messageID;} SofttargetDetails2;');")
    assert l.eval("api.read('issued').status")=="OBSERVED"

@pytest.mark.parametrize("code",["stamp=nil","stamp.extra=1","stamp.session=''","stamp.world_token=''","stamp.revision='x'"])
def check_initial_identity(code):
    l=build(code+";")
    assert l.eval("api.read('issued').status")=="UNAVAILABLE" and l.eval("calls")==0

@pytest.mark.parametrize("code",["d.now=function()return -1 end","d.now=function()return 0/0 end",
    "d.now=function()clockcalls=clockcalls+1;return 3-clockcalls end"])
def check_bad_clock(code):
    l=build(code+";")
    assert l.eval("api.read('issued').status")=="UNAVAILABLE"

def check_dispose_and_detached():
    l=build();l.execute("r=api.read('issued');stamp.world_token='changed';api.dispose();a=api.read('issued')")
    assert l.eval("r.stamp.world_token")=="synthetic" and l.eval("a.status")=="UNAVAILABLE"
    assert l.eval("calls")==8

def check_wire_bound():
    l=build("stamp.world_token=string.rep('x',512);connection=string.rep('x',128);message_type=string.rep('y',128);")
    l.execute("r=api.read('issued');encoded=wire.encode({payload=wire.encode(r)})")
    assert l.eval("r.status")=="OBSERVED",l.eval("r.reason")
    assert len(l.eval("encoded").encode())<=4096

def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            for i,arg in enumerate(marks[0].args[1]):out.append(pytest.param(name,[arg],id=f"{name}-{i}"))
        else:out.append(pytest.param(name,[],id=name))
    return out
@pytest.mark.parametrize("name,args",_cases())
def test_luajit_control(name,args):
    p=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=20,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip()=="CONTROL_PASSED",p.stdout+p.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_");globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

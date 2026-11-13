"""Synthetic native camera readback controls; no game or camera commands."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
luajit = pytest.importorskip("lupa.luajit21")
SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_camerastate.lua"
SETUP = r"""
ffi=require('ffi')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
calls=0;currentcalls=0;clockcalls=0;mode=true;ref=18446744073709551615ULL
function count() calls=calls+1;if hook then hook(calls) end end
C={GetCameraRotation=function() count();return ffi.new('Rotation',{1.25,-2.5,0}) end,
GetFollowCameraBasePos=function() count();return ffi.new('Coord3D',{10,20,-30}) end,
GetExternalViewRefObject=function() count();return ref end,
IsExternalViewActive=function() count();return mode end}
d={ffi=ffi,C=C,current=function()currentcalls=currentcalls+1;return stamp end,
 now=function()clockcalls=clockcalls+1;return clockcalls end}
"""

def build(before=""):
    runtime=luajit.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(SETUP+before)
    runtime.globals().factory=runtime.execute(SOURCE.read_text(encoding="utf-8"))
    runtime.execute("api=factory(d)")
    return runtime

def check_exact_observation_scope():
    l=build();assert l.eval("calls")==0
    l.execute("r=api.read()")
    assert l.eval("r.status")=="OBSERVED",l.eval("r.reason")
    assert l.eval("r.rotation_native.yaw")==1.25
    assert l.eval("r.follow_camera_base_offset_setting.z")==-30
    assert l.eval("r.external_reference_handle")=="18446744073709551615"
    for key in ("achieved_pose_qualified","target_distance_qualified","engine_atomicity_qualified","nested_handle_issued"):
        assert l.eval("r."+key) is False
    assert l.eval("r.rotation_units")=="UNKNOWN"
    assert l.eval("calls")==6 and l.eval("currentcalls")==2

@pytest.mark.parametrize("declaration",[
    "typedef struct {float yaw;float pitch;float roll;} Rotation;",
    "typedef struct {float x;float y;float z;} Coord3D;",
    "typedef uint64_t UniverseID;",
])
def check_native_first(declaration):
    l=build("ffi.cdef("+json.dumps(declaration)+");")
    assert l.eval("api.read().status")=="OBSERVED"

@pytest.mark.parametrize("declaration",[
    "typedef struct {double yaw;double pitch;double roll;} Rotation;",
    "typedef struct {int yaw;int pitch;int roll;} Rotation;",
    "typedef struct {float roll;float pitch;float yaw;} Rotation;",
    "typedef struct {float x;float z;float y;} Coord3D;",
    "typedef int64_t UniverseID;",
])
def check_wrong_abi(declaration):
    with pytest.raises(Exception):build("ffi.cdef("+json.dumps(declaration)+");")

@pytest.mark.parametrize("code",[
    "C.GetCameraRotation=function()return {yaw=0,pitch=0,roll=0}end",
    "C.GetFollowCameraBasePos=function()return nil end",
    "C.GetCameraRotation=function()return ffi.new('Rotation',{0/0,0,0})end",
    "C.GetFollowCameraBasePos=function()return ffi.new('Coord3D',{math.huge,0,0})end",
    "C.GetExternalViewRefObject=function()return 9007199254740992 end",
    "C.GetExternalViewRefObject=function()return '123' end",
    "C.GetExternalViewRefObject=function()return ffi.new('int64_t',123) end",
    "C.IsExternalViewActive=function()return 1 end",
    "C.GetCameraRotation=function()error('native failed')end",
    "C.GetCameraRotation=nil",
])
def check_bad_native(code):
    l=build(code+";");l.execute("r=api.read()")
    assert l.eval("r.status")=="UNAVAILABLE"
    assert l.eval("r.rotation_native==nil") is True

def check_zero_reference_not_pose():
    l=build("ref=0ULL;mode=false;");l.execute("r=api.read()")
    assert l.eval("r.status")=="OBSERVED"
    assert l.eval("r.external_reference_nonzero") is False
    assert l.eval("r.achieved_pose_qualified") is False

@pytest.mark.parametrize("code",["ref=42ULL","mode=false"])
def check_mid_read_reference_change(code):
    l=build("hook=function(n)if n==4 then "+code+" end end;")
    assert l.eval("api.read().status")=="UNAVAILABLE"

@pytest.mark.parametrize("field",["provider","revision","session","generation","world_token"])
def check_mid_read_public_drift(field):
    l=build("hook=function(n)if n==4 then stamp."+field+"='changed' end end;")
    assert l.eval("api.read().status")=="UNAVAILABLE"

@pytest.mark.parametrize("code",["stamp=nil","stamp.extra=1","stamp.session=nil","stamp.world_token=''","stamp.generation='x'"])
def check_invalid_stamp_before_native(code):
    l=build(code+";");assert l.eval("api.read().status")=="UNAVAILABLE"
    assert l.eval("calls")==0

@pytest.mark.parametrize("expression",["-1","0/0","math.huge","3-clockcalls"])
def check_clock_failure(expression):
    l=build("d.now=function()clockcalls=clockcalls+1;return "+expression+" end;")
    assert l.eval("api.read().status")=="UNAVAILABLE"

def check_detached_observation_and_dispose():
    l=build();l.execute("r=api.read();stamp.world_token='new';mode=false")
    assert l.eval("r.stamp.world_token")=="synthetic"
    assert l.eval("r.external_view_active") is True
    assert l.eval("api.dispose()") is True
    assert l.eval("api.read().status")=="UNAVAILABLE" and l.eval("calls")==6

def _cases():
    rows=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,arg in enumerate(marks[0].args[1]):rows.append(pytest.param(name,[arg],id=f"{name}-{i}"))
        else:rows.append(pytest.param(name,[],id=name))
    return rows

@pytest.mark.parametrize("name,args",_cases())
def test_actual_camera_observation_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=15,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr

if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

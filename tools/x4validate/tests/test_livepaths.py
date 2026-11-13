"""Native const-char pointer and identity controls; synthetic paths only."""
import json, os, subprocess, sys
from pathlib import Path
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_paths.lua"
SETUP=r"""
ffi=require('ffi')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
 generation=string.rep('c',32),world_token='synthetic-world'}
calls=0;currentcalls=0;path='/synthetic/profile/save'
buffer=nil;retained=nil
function getter()
 calls=calls+1
 if hook then hook() end
 buffer=ffi.new('char[?]',#path+1,path)
 return ffi.cast('const char *',buffer)
end
C={GetSaveFolderPath=getter}
d={ffi=ffi,C=C,current=function()currentcalls=currentcalls+1;return stamp end}
"""
def build(before=""):
    l=luajit.LuaRuntime(unpack_returned_tuples=True);l.execute(SETUP+before)
    l.globals().factory=l.execute(SOURCE.read_text(encoding="utf-8"));l.execute("api=factory(d)")
    return l
def check_observed_exact_path_and_scope():
    l=build();assert l.eval("calls")==0
    l.execute("result=api.read()")
    assert l.eval("result.status")=="OBSERVED"
    assert l.eval("result.save_folder")=="/synthetic/profile/save"
    assert set(dict(l.globals().result.items()))=={"status","scope","stamp","save_folder"}
    assert l.eval("result.scope")=="NATIVE_SAVE_FOLDER_PATH"
    assert l.eval("calls")==1 and l.eval("currentcalls")==2
def check_native_first_retained_callback():
    l=build("ffi.cdef('const char* GetSaveFolderPath(void);');retained=ffi.cast('const char*(*)(void)',getter);C.GetSaveFolderPath=retained;")
    assert l.eval("api.read().status")=="OBSERVED"
def check_helper_first_retained_callback():
    l=build();l.execute("retained=ffi.cast('const char*(*)(void)',getter);C.GetSaveFolderPath=retained")
    assert l.eval("api.read().status")=="OBSERVED"
@pytest.mark.parametrize("value",["nil","42","'pretend path'","ffi.cast('const char *',nil)","ffi.new('uint64_t',42)"])
def check_invalid_native_return(value):
    l=build("C.GetSaveFolderPath=function()calls=calls+1;return "+value+" end;")
    l.execute("result=api.read()");assert l.eval("result.status")=="UNAVAILABLE"
    assert l.eval("result.save_folder==nil") is True
def check_native_failure_and_missing_symbol():
    for code in ("C.GetSaveFolderPath=nil;","C.GetSaveFolderPath=function()error('native unavailable')end;"):
        l=build(code);assert l.eval("api.read().status")=="UNAVAILABLE"
@pytest.mark.parametrize("length,expected",[(0,"UNAVAILABLE"),(1,"OBSERVED"),(4096,"OBSERVED"),(4097,"UNAVAILABLE"),(10000,"UNAVAILABLE")])
def check_string_bounds(length,expected):
    l=build("path=string.rep('x',"+str(length)+");")
    assert l.eval("api.read().status")==expected
def check_control_bytes_refuse():
    for value in ("'a'..string.char(1)..'b'","'a'..string.char(127)..'b'"):
        l=build("path="+value+";");assert l.eval("api.read().status")=="UNAVAILABLE"
def check_unicode_bytes_preserved():
    l=build();l.globals().path="/synthetic/é家/save"
    assert l.eval("api.read().save_folder")=="/synthetic/é家/save"
def check_no_path_normalization_or_root_inference():
    l=build();l.globals().path="Z:\\Synthetic\\profile\\save\\"
    assert l.eval("api.read().save_folder")=="Z:\\Synthetic\\profile\\save\\"
@pytest.mark.parametrize("field",["provider","revision","session","generation","world_token"])
def check_drift_during_native_read(field):
    l=build("hook=function()stamp."+field+"='changed' end;")
    assert l.eval("api.read().status")=="UNAVAILABLE"
@pytest.mark.parametrize("code",["stamp=nil","stamp.session=nil","stamp.extra=true","stamp.generation='short'","stamp.world_token=''"])
def check_bad_current_stamp_never_calls_native(code):
    l=build(code+";");assert l.eval("api.read().status")=="UNAVAILABLE";assert l.eval("calls")==0
def check_snapshot_does_not_alias_current():
    l=build();l.execute("result=api.read();stamp.world_token='changed'")
    assert l.eval("result.stamp.world_token")=="synthetic-world"
def check_each_read_refreshes_native_path():
    l=build();l.execute("one=api.read();path='/synthetic/other/save';two=api.read()")
    assert l.eval("one.save_folder")!=l.eval("two.save_folder");assert l.eval("calls")==2
def check_disposed_observer_no_native_call():
    l=build();assert l.eval("api.dispose()") is True
    assert l.eval("api.read().status")=="UNAVAILABLE";assert l.eval("calls")==0
def check_no_unknown_zero_or_empty_path():
    l=build("path='';");l.execute("result=api.read()")
    assert l.eval("result.status")=="UNAVAILABLE" and l.eval("result.save_folder==nil") is True

def _cases():
    rows=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                rows.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:rows.append(pytest.param(name,[],id=name))
    return rows
@pytest.mark.parametrize("name,args",_cases())
def test_actual_native_path_control(name,args):
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=15,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=="CONTROL_PASSED",result.stdout+result.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

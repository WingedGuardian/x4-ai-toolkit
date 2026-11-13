"""Synthetic native logbook controls, isolated LuaJIT children; no game calls."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_logbook.lua"
SETUP="""
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
 generation=string.rep('c',32),world_token='synthetic-world'}
time=10;calls=0;totals=0;reads=0;total=3;encoded='encoded'
function entry(i) return {time=i,title='title'..i,text='text'..i,category='general',
 entityname='',factionname='',money=0,bonus=0} end
g={GetNumLogbook=function(category)
 assert(category=='all');calls=calls+1;totals=totals+1
 if total_hook then return total_hook() end
 return total
end,
GetLogbook=function(start,n,category)
 assert(category=='all');calls=calls+1;reads=reads+1
 if read_hook then return read_hook(start,n) end
 local rows={};for i=1,n do rows[i]=entry(start+i-1) end;return rows
end}
d={globals=g,current=function()return stamp end,now=function()return time end,
 encode=function()return encoded end}
"""
def build(before=""):
    r=LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP+before)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)")
    return r

def check_native_window_and_disclosed_denominator():
    r=build();assert r.eval("calls")==0
    r.execute("v=api.read(2,1)")
    assert r.eval("v.status")=="OBSERVED" and r.eval("v.returned_count")==1
    assert r.eval("v.rows[1].index")==2 and r.eval("v.rows[1].text")=="text2"
    assert r.eval("v.total_before")==3 and r.eval("v.total_after")==3
    assert r.eval("v.next_index")==3 and r.eval("v.covers_total") is False
    assert r.eval("v.atomic") is False and r.eval("v.notifications_included") is False
    assert r.eval("v.stability")=="TWO_EQUAL_PROJECTED_READS_WITH_EQUAL_TOTALS"
    assert r.eval("calls")==4 and r.eval("v.rows[1].money")==None

def check_zero_requires_two_native_totals():
    r=build("total=0;");r.execute("v=api.read(1,16)")
    assert r.eval("v.status")=="OBSERVED" and r.eval("v.returned_count")==0
    assert r.eval("v.covers_total") is True and r.eval("totals")==2 and r.eval("reads")==0

@pytest.mark.parametrize("code",[
 "g.GetNumLogbook=nil", "total=nil", "total=0/0", "total=-1", "total=1048577",
 "total_hook=function()return totals==1 and 3 or 4 end",
 "read_hook=function()return nil end",
 "read_hook=function()return {entry(1)} end",
 "read_hook=function()return {[1]=entry(1),[3]=entry(3)} end",
 "read_hook=function()local t={entry(1),entry(2),entry(3)};t[2].time=0/0;return t end",
 "read_hook=function()local t={entry(1),entry(2),entry(3)};t[1].text=nil;return t end",
 "read_hook=function()local t={entry(1),entry(2),entry(3)};t[1].title=tostring(reads);return t end",
 "read_hook=function()stamp.generation=string.rep(tostring(reads),32);return {entry(1),entry(2),entry(3)} end",
 "read_hook=function()time=9;return {entry(1),entry(2),entry(3)} end",
 "encoded=string.rep('x',30001)",
 "read_hook=function()error('native failure')end"
])
def check_unavailable_never_becomes_empty_success(code):
    r=build(code+";");r.execute("v=api.read(1,16)")
    assert r.eval("v.status")=="UNAVAILABLE"
    assert r.eval("v.rows")==None and r.eval("v.total_before")==None
    assert r.eval("v.complete_window") is False

@pytest.mark.parametrize("start,limit",[(0,1),(1,0),(1,17),(1048577,1),(1.5,1),(1,1.5)])
def check_invalid_window_no_native_calls(start,limit):
    r=build();assert r.eval("api.read")(start,limit).status=="UNAVAILABLE";assert r.eval("calls")==0

def check_retained_rows_and_stamp_do_not_alias_native_data():
    r=build("shared={entry(1),entry(2),entry(3)};read_hook=function()return shared end;")
    r.execute("v=api.read(1,16);shared[1].text='changed';stamp.world_token='changed'")
    assert r.eval("v.rows[1].text")=="text1" and r.eval("v.stamp.world_token")=="synthetic-world"

def check_disposed_and_malformed_stamp_do_not_call_native():
    for code in ("stamp.session=nil;","stamp.extra=true;","stamp.world_token='';"):
        r=build(code);assert r.eval("api.read(1,1).status")=="UNAVAILABLE";assert r.eval("calls")==0
    r=build();assert r.eval("api.dispose()") is True
    assert r.eval("api.read(1,1).status")=="UNAVAILABLE" and r.eval("calls")==0

def check_exact_text_preserved():
    r=build();r.execute("total=1;")
    r.globals().text="é家\nline"
    r.execute("read_hook=function()local e=entry(1);e.text=text;return {e} end;v=api.read(1,1)")
    assert r.eval("v.rows[1].text")=="é家\nline"

def _cases():
    result=[]
    for name,fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):continue
        marks=[m for m in getattr(fn,"pytestmark",[]) if m.name=="parametrize"]
        if marks:
            assert len(marks)==1
            for i,args in enumerate(marks[0].args[1]):
                values=[args] if len(marks[0].args[0].split(","))==1 else list(args)
                result.append(pytest.param(name,values,id=f"{name}-{i}"))
        else:result.append(pytest.param(name,[],id=name))
    return result
@pytest.mark.parametrize("name,args",_cases())
def test_native_logbook_control(name,args):
    r=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=15,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    assert r.returncode==0,r.stdout+r.stderr
    assert r.stdout.strip()=="CONTROL_PASSED",r.stdout+r.stderr
if __name__=="__main__":
    name=sys.argv[1];assert name.startswith("check_") and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print("CONTROL_PASSED")

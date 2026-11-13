"""Actual LuaJIT ticker-cache ABI/window controls; native results are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
luajit=pytest.importorskip("lupa.luajit21")
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_ticker.lua"
SETUP=r"""
ffi=require('ffi')
ffi.cdef[[typedef uint64_t TickerCacheID;
typedef struct {TickerCacheID id;double time;const char *category,*title,*text;} TickerCacheEntry;]]
function u(s)local n=ffi.new('uint64_t',0);for v in s:gmatch('.')do n=n*10+tonumber(v)end;return n end
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),generation=string.rep('c',32),world_token='synthetic'}
clock=10;total=3;counts=0;fills=0;strings={}
function cs(s)local b=ffi.new('char[?]',#s+1,s);strings[#strings+1]=b;return b end
C={}
C.GetNumTickerCache=function(cat)
 assert(cat=='');counts=counts+1
 if counterror then error('native unavailable')end
 if countdrift and counts==2 then return total+1 end
 return total
end
C.GetTickerCache=function(buf,n,start,limit,cat)
 assert(cat=='' and n==limit and start>=1);fills=fills+1
 if fillerror then error('native unreadable')end
 for i=0,n-1 do
  buf[i].id=u(i==0 and '18446744073709551615' or tostring(start+i))
  buf[i].time=badtime or start+i+0.125
  buf[i].category=cs('native category');buf[i].title=cs('native title');buf[i].text=cs(longtext or 'native text')
  if nulltext then buf[i].text=nil end
  if rowdrift and fills==2 then buf[i].title=cs('changed')end
 end
 if zeroid and n>0 then buf[0].id=u('0')end
 if worlddrift then stamp.session=string.rep('e',32)end
 if clockback then clock=9 end
 if badfill~=nil then return badfill end
 return n
end
d={ffi=ffi,C=C,current=function()return stamp end,now=function()return clock end,encode=function(v)return wire.encode(v)end}
"""
def build(code="",setup=SETUP):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(setup)
    r.globals().wire=r.execute(SOURCE.with_name("live_actionwire.lua").read_text(encoding="utf-8"))()
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute("api=factory(d)");r.execute(code);return r
def read(r,start=1,limit=16):return r.globals().api.read(start,limit)

def check_native_window_and_raw_fields():
    r=build();out=read(r,2,1);assert out["status"]=="OBSERVED",out["reason"]
    assert out["returned_count"]==1 and out["total_before"]==out["total_after"]==3
    assert out["next_index"]==3 and out["covers_total"] is False and out["complete_window"] is True
    assert out["rows"][1]["ticker_cache_id"]=="18446744073709551615" and out["rows"][1]["index"]==2
    assert out["rows"][1]["time"]==2.125 and out["entry_time_domain"]=="NATIVE_UNQUALIFIED"
    assert out["rendered_notification_verified"] is False and out["outside_cache_coverage"] is False
    assert r.globals().counts==2 and r.globals().fills==2

def check_full_and_empty_window_denominators():
    r=build();out=read(r);assert out["covers_total"] is True and out["returned_count"]==3 and out["next_index"]==0
    r=build("total=0");out=read(r);assert out["status"]=="OBSERVED" and out["covers_total"] is True
    assert out["returned_count"]==0 and r.globals().counts==2 and r.globals().fills==0

def check_end_boundary_is_empty_window_not_empty_cache():
    r=build();out=read(r,4,1)
    assert out["status"]=="OBSERVED" and out["returned_count"]==0 and out["total_before"]==3 and out["covers_total"] is False

def check_zero_native_id_retained_not_absence():
    r=build("zeroid=true");out=read(r)
    assert out["status"]=="OBSERVED" and out["rows"][1]["ticker_cache_id"]=="0" and out["returned_count"]==3

@pytest.mark.parametrize("code",[
"total=nil","total=-1","total=0/0","total=1048577","total=1.5","counterror=true","countdrift=true",
"fillerror=true","badfill=0","badfill=17","badfill=0/0","badfill=-1","badfill=1.5",
"badtime=0/0","badtime=-1","badtime=math.huge","nulltext=true","rowdrift=true","worlddrift=true","clockback=true",
"clock=0/0","clock=-1","stamp.world_token=''","stamp.extra=true","api.dispose()",
"longtext=string.rep('x',16385)","longtext=string.rep('x',4097)",
])
def check_refuse_without_partial_or_empty(code):
    r=build(code);out=read(r)
    assert out["status"]=="UNAVAILABLE" and out["complete_window"] is False and out["rows"] is None

@pytest.mark.parametrize("start,limit",[(0,1),(-1,1),(1,0),(1,17),(1.5,1),(1,1.5),(1048578,1)])
def check_invalid_request_before_native(start,limit):
    r=build();assert read(r,start,limit)["status"]=="UNAVAILABLE" and r.globals().counts==r.globals().fills==0

def check_beyond_cache_refused_with_no_fill():
    r=build();assert read(r,5,1)["status"]=="UNAVAILABLE" and r.globals().fills==0

def check_actual_encoder_window_bound_and_smaller_retry():
    r=build("total=16;longtext=string.rep('x',2000)");assert read(r)["status"]=="UNAVAILABLE"
    out=read(r,1,1);assert out["status"]=="OBSERVED" and out["returned_count"]==1 and out["covers_total"] is False

def check_same_total_replaced_rows_refuse():
    r=build("rowdrift=true");assert read(r)["status"]=="UNAVAILABLE" and r.globals().fills==2

def check_returned_values_not_aliases():
    r=build();out=read(r);r.execute("stamp.world_token='changed'")
    assert out["stamp"]["world_token"]=="synthetic"

@pytest.mark.parametrize("old,new",[
("uint64_t TickerCacheID","int64_t TickerCacheID"),
("double time","int64_t time"),
("double time","float time"),
("const char *category,*title,*text","const char *title,*category,*text"),
])
def check_bad_abi_before_native(old,new):
    r=luajit.LuaRuntime(unpack_returned_tuples=True);r.execute(SETUP.replace(old,new))
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));ok,_=r.eval("function()return pcall(factory,d)end")()
    assert not ok and r.globals().counts==r.globals().fills==0

def check_retained_native_pointer_signature():
    r=build()
    r.execute("original=C.GetTickerCache;cb=ffi.cast('uint32_t(*)(TickerCacheEntry*,uint32_t,size_t,size_t,const char*)',function(buf,n,start,limit,cat)return original(buf,tonumber(n),tonumber(start),tonumber(limit),ffi.string(cat))end);C.GetTickerCache=cb")
    assert read(r)["status"]=="OBSERVED"

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

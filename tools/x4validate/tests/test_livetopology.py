"""Actual topology Lua + actual bounded encoder, synthetic MD transfer only."""
from pathlib import Path
import pytest
import os, sys, json, subprocess
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[3]
def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().wirefactory=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actionwire.lua").read_text(encoding="utf-8"))
    r.globals().factory=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_topology.lua").read_text(encoding="utf-8"))
    r.execute("""
        now=0;sends=0;reads=0;clears=0;disposed=0;id=string.rep('d',32);rev=string.rep('e',64)
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        c={public_current=function() return stamp end,now=function() return now end,
            subscribe=function(f) drain=f end,arm_tick=function(f) tick=f end,
            send=function(control,r) assert(control=='snapshot');sends=sends+1;sent=r end,
            read=function() reads=reads+1;return queue end,clear=function() clears=clears+1;queue=nil end,
            component_id=function(v) assert(type(v)~='table');return tostring(v) end,
            dispose=function() disposed=disposed+1;return true end}
        function object(i)
            return {object=tostring(i),exists=true,macro='gate_macro',cluster='1',cluster_macro='cluster_macro',
                sector='2',sector_macro='sector_macro',zone='3',zone_macro='zone_macro',active=false,
                exit='5',exit_macro='exit_macro',exit_sector='6',exit_sector_macro='other_sector_macro'}
        end
        function row(total)
            local rows={};for i=1,math.min(total,512) do rows[i]=object(i+10) end
            return {kind='SNAPSHOT',provider='x4-topology-observer-v1',revision=rev,request=id,session=stamp.session,
                generation=stamp.generation,world_token=stamp.world_token,query_kind='gates',sector_macro='sector_macro',
                age_before=10,age_after=10,stable=true,status='CAPTURED',total=total,scope_total=100,
                scope_matches=1,unknown=0,rows=rows}
        end
        function deliver(v) queue={v};drain() end
        wire=wirefactory();api=factory{channel=c,revision=rev,encode=wire.encode}
    """)
    return r

def check_constructor_begin_poll_and_ownerless_inactive_exit():
    r=runtime();assert r.eval("sends+reads+clears")==0
    r.execute("api.begin(id,'gates','sector_macro')")
    assert r.eval("api.poll(id).status")=="PENDING" and r.eval("sends")==1
    r.execute("deliver(row(1))")
    assert r.eval("api.poll(id).enumeration_complete") is True
    assert r.eval("api.poll(id).owner_filter") is False
    assert r.eval("api.page(id,0).rows[1].active") is False
    assert r.eval("api.page(id,0).rows[1].exit.id")=="5"
    assert r.eval("api.poll(id).runtime_verified") is False

@pytest.mark.parametrize("total",[0,1,16,17,512,513])
def check_bounded_complete_partial_and_full_actual_wire_paging(total):
    r=runtime();r.execute(f"api.begin(id,'gates','sector_macro');deliver(row({total}))")
    assert r.eval("api.poll(id).enumeration_complete") is (total<=512)
    assert r.eval("api.poll(id).omitted")==max(0,total-512)
    offset=0;seen=[]
    while True:
        page=r.globals().api.page(r.globals().id,offset)
        assert len(r.globals().wire.encode(page).encode())<=3000
        for _,value in page["rows"].items():seen.append(value["id"])
        assert page["offset"]==offset
        offset=page["next_offset"]
        if page["end_of_retained"]:break
    assert len(seen)==min(total,512) and len(set(seen))==len(seen)

def check_nonexistent_row_is_unknown_not_empty_census():
    r=runtime();r.execute("""api.begin(id,'gates','sector_macro');local x=row(1);local a=x.rows[1]
        a.exists=false;a.object=0;a.macro='';a.active=-1
        for _,k in ipairs({'cluster','sector','zone','exit','exit_sector'}) do a[k]=0;a[k..'_macro']='' end
        x.unknown=1;deliver(x)""")
    assert r.eval("api.poll(id).status")=="PARTIAL"
    assert r.eval("api.poll(id).unreadable")==1 and r.eval("api.poll(id).enumerated_total")==1

@pytest.mark.parametrize("change",[
    "x.extra=1","x.total=2","x.rows={}","x.scope_matches=0","x.scope_total=4097","x.unknown=1",
    "x.age_after=9","x.age_before=-1","x.age_after=0/0","x.stable=false",
    "x.session=string.rep('f',32)","x.revision=string.rep('f',64)","x.request=string.rep('f',32)",
    "x.generation=string.rep('f',32)","x.world_token='other'","x.query_kind='sectors'","x.sector_macro='other'",
    "x.rows[1].macro=''","x.rows[1].object=9007199254740992","x.rows[1].object='18446744073709551616'",
    "x.rows[1].object={}","x.rows[1].active=-1","x.rows[1].sector_macro='other'",
    "x.rows[1].cluster=0","x.rows[1].exit=0","x.rows[1].exists='true'",
    "x.rows[1].extra=true","x.rows[2]=x.rows[1];x.total=2",
])
def check_malformed_snapshot_never_becomes_complete(change):
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');local x=row(1);"+change+";deliver(x)")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"
    assert r.execute("return pcall(api.page,id,0)")[0] is False

def check_exact_uint64_observation_and_copy():
    r=runtime();r.execute("""api.begin(id,'gates','sector_macro');local x=row(1)
        x.rows[1].object='18446744073709551615';deliver(x)
        local p=api.page(id,0);p.rows[1].id='changed'""")
    assert r.eval("api.page(id,0).rows[1].id")=="18446744073709551615"

@pytest.mark.parametrize("mode",["clusters","sectors","zones"])
def check_other_kinds_have_their_exact_scope(mode):
    r=runtime();r.globals().kind=mode
    r.execute("""local sector=kind=='zones' and 'sector_macro' or '';api.begin(id,kind,sector)
        local x=row(1);x.query_kind=kind;x.sector_macro=sector
        if kind~='zones' then x.scope_total=-1;x.scope_matches=0 end
        local a=x.rows[1];a.active=-1;a.exit=0;a.exit_macro='';a.exit_sector=0;a.exit_sector_macro=''
        local own=kind=='clusters' and 'cluster' or kind=='sectors' and 'sector' or 'zone'
        a.object=a[own];a.macro=a[own..'_macro'];deliver(x)""")
    assert r.eval("api.poll(id).enumeration_complete") is True

@pytest.mark.parametrize("state",["UNAVAILABLE","SCOPE_UNAVAILABLE","SCOPE_CAPACITY"])
def check_unavailable_scope_is_not_absence(state):
    r=runtime();r.globals().source_status=state
    r.execute("""api.begin(id,'gates','sector_macro');local x=row(0);x.status=source_status;x.total=-1
        x.scope_matches=0;deliver(x)""")
    assert r.eval("api.poll(id).status")=="UNAVAILABLE"
    assert r.eval("api.poll(id).enumeration_complete") is False

def check_duplicate_batch_preflight_and_retired_listener():
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');queue={row(1),row(1)};drain()")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED" and r.eval("clears")==0
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');api.dispose();queue={row(1)};drain()")
    assert r.eval("reads+clears")==0

def check_timeout_no_retry_and_late_response_has_no_authority():
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');now=11;tick();deliver(row(1))")
    assert r.eval("api.poll(id).status")=="UNAVAILABLE" and r.eval("sends")==1

@pytest.mark.parametrize("change",["now=301","stamp.session=string.rep('f',32)","now=-1"])
def check_snapshot_lifetime_and_identity_refuse_pages(change):
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');deliver(row(1));"+change)
    assert r.execute("return pcall(api.page,id,0)")[0] is False

def check_new_request_supersedes_old_pages():
    r=runtime();r.execute("api.begin(id,'gates','sector_macro');deliver(row(1));api.begin(string.rep('f',32),'sectors','')")
    assert r.execute("return pcall(api.page,id,0)")[0] is False

def check_disposal_failure_is_sticky():
    r=runtime();r.execute("c.dispose=function() return false end")
    assert r.eval("api.dispose()") is False and r.eval("api.dispose()") is False

@pytest.mark.parametrize("args",["id,'zones',''","id,'clusters','sector_macro'","id,'eval','x'"])
def check_closed_query_args_refuse_without_send(args):
    r=runtime();assert r.execute("return pcall(api.begin,"+args+")")[0] is False
    assert r.eval("sends")==0

def check_native_age_cannot_roll_back_between_captures():
    r=runtime();r.execute("""api.begin(id,'gates','sector_macro');deliver(row(1))
        id=string.rep('f',32);api.begin(id,'gates','sector_macro');local x=row(1)
        x.age_before=9;x.age_after=9;deliver(x)""")
    assert r.eval("api.poll(id).status")=="UNQUALIFIED"

def check_identity_drift_during_initial_subscribe_sends_nothing():
    r=runtime();r.execute("""c.subscribe=function(f) drain=f;stamp.session=string.rep('f',32) end""")
    assert r.execute("return pcall(api.begin,id,'gates','sector_macro')")[0] is False
    assert r.eval("sends")==0

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


def check_md_numeric_booleans_gate_row_and_stable_snapshot():
    r=runtime();r.execute(MDNUM);r.execute("api.begin(id,'gates','sector_macro');deliver(row(1))")
    assert r.eval("api.poll(id).enumeration_complete") is True
    assert r.eval("api.page(id,0).rows[1].active") is False

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

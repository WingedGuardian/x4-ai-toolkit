"""Actual SDK/provider/observer/wire; only native MD transport is synthetic."""
from pathlib import Path
import json, os, subprocess, sys
import pytest
from test_livetopology import runtime as observer_runtime
ROOT=Path(__file__).resolve().parents[3]

def runtime():
    r=observer_runtime()
    r.globals().provider=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_topology_provider.lua").read_text(encoding="utf-8"))
    r.globals().sdk=r.execute((ROOT/"mods/x4_toolkit_helper/ui/live_actions.lua").read_text(encoding="utf-8"))
    r.execute("""
        watch=api
        providerapi=provider{observer=watch,revision=string.rep('f',64),
            public_current=function() return stamp end,encode=wire.encode}
        registry=sdk();registry.register(providerapi.spec)
        request={version=1,id=id,session=stamp.session,provider='x4-topology-observer-v1',
            action='capture',arguments={kind='gates',sector_macro='sector_macro'},
            binding=string.rep('f',64),digest=string.rep('1',64)}
        function prep() return registry.prepare(request) end
        function run() return registry.execute(request.id,request.session,request.digest) end
        function status() return registry.status(request.id,request.session,request.digest) end
        function page_request(offset)
            request={version=1,id=string.rep('2',32),session=stamp.session,provider='x4-topology-observer-v1',
                action='page',arguments={capture=id,offset=offset},binding=string.rep('f',64),digest=string.rep('3',64)}
        end
    """)
    return r

def check_pending_then_partial_and_actual_nested_wire():
    r=runtime()
    assert r.eval("prep().status")=="prepared" and r.eval("sends")==0
    assert r.eval("run().status")=="running"
    assert r.eval("status().status")=="running" and r.eval("sends")==1
    r.execute("deliver(row(513));result=status()")
    assert r.eval("result.status")=="completed"
    meta=json.loads(r.eval("result.observations.payload_json"))
    assert meta["status"]=="PARTIAL" and meta["omitted"]==1
    assert meta["enumeration_complete"] is False
    r.execute("page_request(0);prep();result=run()")
    assert r.eval("result.status")=="completed"
    page=json.loads(r.eval("result.observations.payload_json"))
    assert page["offset"]==0 and page["next_offset"]>0
    assert page["end_of_retained"] is False
    inner=r.eval("wire.encode(result)")
    assert len(inner.encode())<=4096
    assert len(r.eval("wire.encode({status='ok',payload=wire.encode(result)})").encode())<=4096
    assert r.eval("status().observations.payload_json")==r.eval("result.observations.payload_json")

def check_empty_capture_and_page_have_explicit_zero_denominator():
    r=runtime();r.execute("prep();run();deliver(row(0));result=status()")
    meta=json.loads(r.eval("result.observations.payload_json"))
    assert meta["enumerated_total"]==meta["retained"]==0 and meta["enumeration_complete"]
    r.execute("page_request(0);prep();result=run()")
    page=json.loads(r.eval("result.observations.payload_json"))
    assert page["total_retained"]==page["next_offset"]==0 and page["end_of_retained"]

@pytest.mark.parametrize("change",[
    "request.arguments.extra=true","request.arguments.kind='ships'","request.arguments.sector_macro=''",
    "request.binding=string.rep('0',64)","request.session=string.rep('0',32)",
    "request.arguments={capture=id,offset=0};request.action='page'",
])
def check_invalid_or_unowned_arguments_send_nothing(change):
    r=runtime();r.execute(change)
    assert r.eval("prep().status") not in ("prepared","completed","running") and r.eval("sends")==0

@pytest.mark.parametrize("change",[
    "stamp.generation=string.rep('0',32)","request.arguments.sector_macro='other'",
    "request.arguments.kind='zones'",
])
def check_prepared_request_change_never_sends(change):
    r=runtime();r.execute("prep();"+change)
    # Direct provider control: the actual SDK separately retains its own immutable request.
    r.execute("providerapi.spec.invoke('execute',request,function(v) direct=v end)")
    assert r.eval("direct.status")=="unknown" and r.eval("sends")==0

def check_sdk_retains_original_request_despite_caller_mutation():
    r=runtime();r.execute("prep();request.arguments.sector_macro='other';request.arguments.kind='zones';run()")
    assert r.eval("sent.kind")=="gates" and r.eval("sent.sector_macro")=="sector_macro"

def check_actual_unknown_native_census_never_completes():
    r=runtime();r.execute("prep();run();local x=row(0);x.status='SCOPE_UNAVAILABLE';x.total=-1;x.scope_matches=0;deliver(x)")
    assert r.eval("status().status")=="unknown"

def check_uncertain_send_is_consumed_and_no_retry():
    r=runtime();r.execute("c.send=function() sends=sends+1;error('lost') end;prep();run();run();status()")
    assert r.eval("sends")==1

def check_new_capture_invalidates_old_pages():
    r=runtime();r.execute("prep();run();deliver(row(1));status();old=id;id=string.rep('4',32);request.id=id;request.digest=string.rep('5',64);prep();run();deliver(row(1));status();page_request(0);request.arguments.capture=old;prep()")
    assert r.eval("run().status")=="unknown"

def check_disposal_failure_stays_failed():
    r=runtime();r.execute("c.dispose=function() error('retire') end")
    assert r.eval("providerapi.dispose()") is False
    assert r.eval("providerapi.dispose()") is False
    assert r.eval("providerapi.session()") is None

def check_all_returned_rows_fit_real_sdk_and_wire():
    r=runtime();r.execute("prep();run();deliver(row(512));status()")
    offset=0;total=0
    while offset<512:
        r.globals().offset=offset
        r.execute("page_request(offset);request.id=string.format('%032x',offset+100);request.digest=string.rep('3',64);prep();result=run()")
        assert r.eval("result.status")=="completed"
        raw=r.eval("result.observations.payload_json");page=json.loads(raw)
        assert page["offset"]==offset and page["next_offset"]>offset
        assert len(r.eval("wire.encode(result)").encode())<=4096
        total+=len(page["rows"]);offset=page["next_offset"]
    assert total==512 and page["end_of_retained"]

def check_compact_tuple_is_lossless_for_active_false_and_absent_context():
    r=runtime();r.execute("prep();run();local x=row(1);x.rows[1].exit=0;x.rows[1].exit_macro='';x.rows[1].exit_sector=0;x.rows[1].exit_sector_macro='';deliver(x);status();page_request(0);prep();result=run()")
    page=json.loads(r.eval("result.observations.payload_json"))
    assert page["row_encoding"]=="TOPOLOGY_TUPLE_V1"
    assert page["rows"][0]==[True,"11","gate_macro",False,["1","cluster_macro"],["2","sector_macro"],["3","zone_macro"],False,False]

def check_compact_tuple_preserves_dead_object_denominator():
    r=runtime();r.execute("""prep();run();local x=row(1);local a=x.rows[1]
        a.exists=false;a.object=0;a.macro='';a.active=-1
        for _,k in ipairs({'cluster','sector','zone','exit','exit_sector'}) do a[k]=0;a[k..'_macro']='' end
        x.unknown=1;deliver(x);status();page_request(0);prep();result=run()""")
    page=json.loads(r.eval("result.observations.payload_json"))
    assert page["total_retained"]==1 and page["enumeration_complete"] is False
    assert page["rows"][0]==[False,"","","NOT_APPLICABLE",False,False,False,False,False]

def check_one_oversized_row_cannot_be_truncated_or_completed():
    r=runtime();r.execute("""prep();run();local x=row(1);local a=x.rows[1]
        for _,k in ipairs({'macro','cluster_macro','zone_macro','exit_macro','exit_sector_macro'}) do a[k]=string.rep('"',512) end
        deliver(x);status();page_request(0);prep();result=run()""")
    assert r.eval("result.status")=="unknown"
    assert r.eval("result.observations.payload_json")==None

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

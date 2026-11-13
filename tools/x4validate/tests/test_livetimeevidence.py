"""Actual raw router/BoundPipe/ReadEvidence, synthetic clocks and native payload."""
import copy
import json
from fractions import Fraction
import pytest
from test_liveevidence import setup as raw_setup
from test_livebound import STAMP
from x4validate import _livetimeevidence as m

def state(**changes):
    out=dict(status="OBSERVED",scope="NATIVE_SETA_STATE_AND_CLOCK",stamp=copy.deepcopy(STAMP),
        ship="18446744073709551615ULL",activity="seta",has_seta=True,can_activate=False,active=True,
        game_time=5,game_clock="NATIVE_GET_CURRENT_GAME_TIME",ui_clock="UI_ELAPSED",ui_begin=100,ui_end=101,
        atomic=False,acceleration_factor_qualified=False,outcome_invariance_qualified=False)
    out.update(changes);return out

def setup(tmp_path,times=(0,2_000_000_000,10_000_000_000,12_000_000_000)):
    r,p,b,c,_=raw_setup(tmp_path)
    r.execute("""
    router=routerFactory({session=session,wire=wire,registry=core,build='12345678',
      ui_authorize=function()return false end,
      age=function()return nativeage end,read_verbs={time_state=true},
      read=function(verb,args)
        read_calls=(read_calls or 0)+1;read_args=args
        return read_status or 'OK',read_payload
      end})
    """)
    r.globals().read_payload=json.dumps(state())
    iterator=iter(times)
    obj=m.TimeEvidence(c,synthetic=True,synthetic_clock=lambda:next(iterator))
    return obj,r,p,b,c

def pair(tmp_path,second=None,times=(0,2_000_000_000,10_000_000_000,12_000_000_000)):
    obj,r,p,b,c=setup(tmp_path,times)
    a=obj.sample("issued-ship")
    r.globals().read_payload=json.dumps(second or state(game_time=65,ui_begin=110,ui_end=111))
    z=obj.sample("issued-ship")
    return obj,a,z,r,p,b,c

def test_actual_raw_evidence_exact_bounds_and_separate_clocks(tmp_path):
    obj,a,b,r,p,_,_=pair(tmp_path)
    out=obj.compare(a,b)
    assert out["lower"]==dict(numerator="5",denominator="1",decimal_bound="5")
    assert out["upper"]==dict(numerator="15",denominator="2",decimal_bound="7.5")
    assert out["host_widest_ns"]==12_000_000_000 and out["host_narrowest_ns"]==8_000_000_000
    assert out["evidence_tier"]=="SYNTHETIC" and out["historical"]
    for key in ("continuous_active_state_qualified","acceleration_factor_qualified","outcome_invariance_qualified",
                "cross_clock_origin_qualified","native_clock_resolution_qualified","fresh_current_identity"):
        assert out[key] is False
    assert r.globals().read_calls==2
    assert all(call[0]=="bound" and call[1][5] in ("read_capture","read_page") for call in p.calls)
    t=obj.transcript(a)
    assert t["request"]=={"verb":"time_state","args":["issued-ship"]}
    assert t["tier"]=="SYNTHETIC" and obj.receipt(a)["raw_receipt"]["provenance"]=="SYNTHETIC_COLLECTION"
    assert obj.receipt(a)["state"]["game_time"]==5 and obj.receipt(a)["state"]["ui_begin"]==100
    assert obj.receipt(a)["raw_receipt"]["first_envelope_age"]==17
    t["frames"].clear();out["first"]["state"]["game_time"]=999
    assert obj.transcript(a)["frames"] and obj.receipt(a)["state"]["game_time"]==5

def test_zero_native_advance_is_zero_not_unknown(tmp_path):
    obj,a,b,*_=pair(tmp_path,state(game_time=5,ui_begin=102,ui_end=103))
    out=obj.compare(a,b)
    assert out["lower"]["numerator"]==out["upper"]["numerator"]=="0"

@pytest.mark.parametrize("changes",[
    {"status":"REFUSED"},{"scope":"other"},{"ship":"0ULL"},{"ship":"18446744073709551616ULL"},
    {"ship":"7"},{"ship":"-1ULL"},{"activity":"bad\n"},{"active":1},
    {"can_activate":None},{"game_time":True},{"game_time":float("inf")},{"game_time":-1},
    {"ui_begin":-1},{"ui_end":99},{"atomic":True},{"acceleration_factor_qualified":True},
    {"outcome_invariance_qualified":True},{"ui_clock":"GAME_TIME"},{"game_clock":"UI_ELAPSED"},
    {"extra":"field"},{"stamp":dict(STAMP,session="f"*32)}
])
def test_unknown_or_promoted_native_state_refuses(tmp_path,changes):
    obj,r,*_=setup(tmp_path);r.globals().read_payload=json.dumps(state(**changes))
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")
    assert not obj._records

@pytest.mark.parametrize("times",[(0,-1),(0,True),(0,0.5),(2,1)])
def test_host_clock_invalid_or_backward_refuses(tmp_path,times):
    obj,*_=setup(tmp_path,times)
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")
    assert not obj._records

@pytest.mark.parametrize("changes",[
    {"game_time":4,"ui_begin":110,"ui_end":111},
    {"game_time":65,"ui_begin":99,"ui_end":100},
    {"game_time":65,"ui_begin":110,"ui_end":111,"active":False},
    {"game_time":65,"ui_begin":110,"ui_end":111,"activity":"none"},
    {"game_time":65,"ui_begin":110,"ui_end":111,"ship":"77ULL"},
])
def test_pair_state_or_clock_drift_refuses(tmp_path,changes):
    obj,a,b,*_=pair(tmp_path,state(**changes))
    with pytest.raises(m.SessionUnavailable):obj.compare(a,b)

def test_touching_brackets_cannot_bound_upper(tmp_path):
    obj,a,b,*_=pair(tmp_path,times=(0,2,2,4))
    with pytest.raises(m.SessionUnavailable,match="nonoverlapping"):obj.compare(a,b)

def test_distinct_ordered_own_handles_required(tmp_path):
    obj,a,b,*_=pair(tmp_path)
    for x,y in ((a,a),(b,a),(a,object())):
        with pytest.raises(m.SessionUnavailable):obj.compare(x,y)
    with pytest.raises(m.SessionUnavailable):obj.receipt(object())
    with pytest.raises(m.SessionUnavailable):obj.transcript(object())

def test_requested_token_binding_even_same_native_ship(tmp_path):
    obj,r,*_=setup(tmp_path);a=obj.sample("one")
    r.globals().read_payload=json.dumps(state(game_time=65,ui_begin=110,ui_end=111))
    b=obj.sample("two")
    with pytest.raises(m.SessionUnavailable,match="ship"):obj.compare(a,b)

@pytest.mark.parametrize("status",["ERR","ABSENT"])
def test_source_status_not_promoted(tmp_path,status):
    obj,r,*_=setup(tmp_path);r.globals().read_status=status
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")
    assert not obj._records

def test_duplicate_json_field_refuses(tmp_path):
    obj,r,*_=setup(tmp_path)
    r.globals().read_payload=json.dumps(state()).replace('"game_time": 5','"game_time": 5,"game_time": 6')
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")

def test_production_cannot_inject_clock_or_adopt_synthetic_client(tmp_path):
    *_,c=setup(tmp_path)
    with pytest.raises(m.SessionUnavailable):m.TimeEvidence(c,synthetic_clock=lambda:1)
    with pytest.raises(m.SessionUnavailable):m.TimeEvidence(c)
    with pytest.raises(m.SessionUnavailable):m.TimeEvidence(c,synthetic="yes")
    with pytest.raises(m.SessionUnavailable):m.TimeEvidence(object(),synthetic=True)

def test_source_drift_refuses_sample_and_historical_comparison(tmp_path,monkeypatch):
    obj,a,b,*_=pair(tmp_path)
    monkeypatch.setattr(m,"source_pins",lambda:{"changed":"x"})
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")
    with pytest.raises(m.SessionUnavailable):obj.compare(a,b)

def test_capacity_and_no_native_mutator(tmp_path):
    obj,r,p,*_=setup(tmp_path,range(100))
    for _ in range(16):obj.sample("issued-ship")
    before=len(p.calls)
    with pytest.raises(m.SessionUnavailable,match="capacity"):obj.sample("issued-ship")
    assert len(p.calls)==before and r.globals().read_calls==16

def test_nonterminating_decimal_bounds_are_outward(tmp_path):
    obj,a,b,*_=pair(tmp_path,state(game_time=6,ui_begin=110,ui_end=111),times=(0,1_000_000_000,4_000_000_000,6_000_000_000))
    out=obj.compare(a,b)
    assert Fraction(out["lower"]["decimal_bound"])<=Fraction(1,6)
    assert Fraction(out["upper"]["decimal_bound"])>=Fraction(1,3)

def test_large_finite_game_advance_has_no_float_ratio_overflow(tmp_path):
    obj,a,b,*_=pair(tmp_path,state(game_time=1e308,ui_begin=110,ui_end=111),times=(0,1,2,3))
    out=obj.compare(a,b)
    assert "Infinity" not in out["upper"]["decimal_bound"]
    assert int(out["upper"]["numerator"])>10**308

def test_nested_sample_refuses_without_second_read(tmp_path):
    obj,r,*_=setup(tmp_path)
    def clock():
        with pytest.raises(m.SessionUnavailable,match="nested"):obj.sample("other")
        return 1
    obj._clock=clock
    obj.sample("issued-ship")
    assert r.globals().read_calls==1

@pytest.mark.parametrize("token",["",None,"bad token","a"*129])
def test_bad_token_before_read(tmp_path,token):
    obj,r,p,*_=setup(tmp_path)
    with pytest.raises(m.SessionUnavailable):obj.sample(token)
    assert not p.calls

@pytest.mark.parametrize("change",["profile","peer","generation"])
def test_actual_second_capture_binding_drift_cannot_be_compared(tmp_path,change):
    obj,r,p,b,c=setup(tmp_path);a=obj.sample("issued-ship")
    value=state(game_time=65,ui_begin=110,ui_end=111)
    if change=="profile":b.link.peer.profile_id="9000001"
    elif change=="peer":
        b.link.peer.value["pid"]=8;b.link.value["peer"]["pid"]=8
    else:
        b.link.value["ui_generation"]="e"*32
        r.globals().stamp.generation="e"*32
        value["stamp"]["generation"]="e"*32
    r.globals().read_payload=json.dumps(value)
    z=obj.sample("issued-ship")
    with pytest.raises(m.SessionUnavailable,match="binding"):obj.compare(a,z)

def test_drift_inside_actual_read_never_creates_time_handle(tmp_path):
    obj,r,p,b,c=setup(tmp_path);original=p.ask
    def drift(*args):
        result=original(*args)
        b.link.value["ui_generation"]="e"*32
        return result
    p.ask=drift
    with pytest.raises(m.SessionUnavailable):obj.sample("issued-ship")
    assert not obj._records and b.failed

def test_giant_json_integer_is_controlled_refusal(tmp_path):
    obj,r,*_=setup(tmp_path)
    r.globals().read_payload=json.dumps(state(game_time=10**400))
    with pytest.raises(m.SessionUnavailable,match="clock invalid"):obj.sample("issued-ship")
    assert not obj._records

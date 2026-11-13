"""Actual Lua time-control module with synthetic native state; no game actions."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime
SOURCE=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_timecontrol.lua"


def make(code=""):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
ffi=require('ffi')
stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
 generation=string.rep('c',32),world_token='world'}
time=10;game=100;ship=42ULL;activity='';has=true;eligible=true;active=false;writes=0
C={}
function C.GetPlayerOccupiedShipID()return ship end
function C.GetCurrentGameTime()return game end
function C.HasSeta()return has end
function C.CanActivateSeta(check)assert(check==false);return eligible end
function C.IsSetaActive()return active end
function C.StartPlayerActivity(id)assert(id=='seta');writes=writes+1;last='start';if after then after()end end
function C.StopPlayerActivity(id)assert(id=='seta');writes=writes+1;last='stop';if after then after()end end
deps={ffi=ffi,C=C,current=function()return stamp end,now=function()return time end,
 activity=function()return activity end,resolve_issued=function(token,kind)
 assert(token=='ship' and kind=='ship');return 42ULL end}
"""+code)
    r.globals().factory=r.execute(SOURCE.read_text())
    r.execute("api=factory(deps)")
    return r


def test_distinct_clock_samples_do_not_claim_factor_or_invariance():
    r=make()
    out=r.eval("api.observe('ship')")
    assert out.game_time==100 and out.ui_begin==out.ui_end==10
    assert out.game_clock=="NATIVE_GET_CURRENT_GAME_TIME"
    assert out.ui_clock=="UI_ELAPSED" and out.atomic is False
    assert not out.acceleration_factor_qualified and not out.outcome_invariance_qualified
    assert r.eval("writes")==0


@pytest.mark.parametrize("mode,code",[("start",""),("stop","active=true;activity='seta'")])
def test_once_only_separate_start_and_stop_never_claim_speed(mode,code):
    r=make(code);r.globals().mode=mode
    r.execute("p=api.prepare(mode,'ship')")
    assert r.eval("p.status")=="PREPARED" and r.eval("writes")==0
    out=r.eval("api.execute(p.ticket)")
    assert out.status=="DISPATCHED_UNVERIFIED" and out.action_attempted
    assert not out.acceleration_factor_qualified and not out.outcome_invariance_qualified
    assert r.eval("api.execute(p.ticket).status")=="REFUSED" and r.eval("writes")==1


@pytest.mark.parametrize("code",[
    "has=false","eligible=false","active=true","activity='seta'","ship=0ULL",
    "ship=43ULL","game=0/0","time=math.huge","stamp=nil","activity=nil",
    "has=nil","eligible=1","C.GetPlayerOccupiedShipID=function()return 42 end",
])
def test_unknown_or_ineligible_preparation_never_calls_setter(code):
    r=make(code)
    out=r.eval("api.prepare('start','ship')")
    assert out.status=="REFUSED" and not out.action_attempted
    assert r.eval("writes")==0


@pytest.mark.parametrize("code",[
    "time=16","time=9","game=99","activity='scan'","has=false","eligible=false",
    "stamp.generation=string.rep('d',32)","ship=43ULL",
])
def test_changed_snapshot_consumes_preparation_without_write(code):
    r=make();r.execute("p=api.prepare('start','ship');"+code)
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    r.execute("time=10;game=100;activity='';has=true;eligible=true;ship=42ULL")
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("writes")==0


@pytest.mark.parametrize("code",["error('native failure')","stamp=nil","time=9"])
def test_post_dispatch_failure_remains_uncertain(code):
    r=make("after=function()"+code+" end")
    r.execute("p=api.prepare('start','ship')")
    out=r.eval("api.execute(p.ticket)")
    assert out.status=="UNCERTAIN" and out.action_attempted
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("writes")==1


def test_stop_does_not_replace_unrelated_activity():
    r=make("active=false;activity='scan'")
    assert r.eval("api.prepare('stop','ship').status")=="REFUSED"
    assert r.eval("writes")==0


def test_invalid_action_subject_and_retirement():
    r=make()
    assert r.eval("api.prepare('factor_100','ship').status")=="REFUSED"
    assert r.eval("api.prepare('start','foreign').status")=="REFUSED"
    r.execute("p=api.prepare('start','ship');api.dispose()")
    assert r.eval("api.execute(p.ticket).status")=="REFUSED"
    assert r.eval("api.observe('ship').status")=="REFUSED"
    assert r.eval("writes")==0


def test_native_reads_are_bracketed_by_final_session_observation():
    r=make("reads=0;C.GetPlayerOccupiedShipID=function()reads=reads+1;if reads==2 then stamp.generation=string.rep('d',32)end;return ship end")
    assert r.eval("api.observe('ship').status")=="REFUSED"


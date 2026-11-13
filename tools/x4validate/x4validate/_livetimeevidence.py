"""Host-bracketed native game-clock observations; no actions or sleeping."""
from __future__ import annotations
import copy
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import re
import threading
import time
from ._livebound import _decode
from ._liveevidence import ReadEvidence, source_pins as read_pins
from ._livesession import SessionUnavailable

def need(ok,why):
    if not ok:raise SessionUnavailable(why)

def source_pins():
    return read_pins() | {"_livetimeevidence.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

def finite(v):
    try:return type(v) in (int,float) and math.isfinite(v) and v>=0
    except OverflowError:return False  # silent-ok: False drives the explicit invalid-clock refusal.

def stamp(identity):
    return {k:identity[k] for k in ("provider","revision","session","world_token")}|{"generation":identity["ui_generation"]}

def measured_state(value,identity):
    keys={"status","scope","stamp","ship","activity","has_seta","can_activate","active","game_time",
          "game_clock","ui_clock","ui_begin","ui_end","atomic","acceleration_factor_qualified","outcome_invariance_qualified"}
    need(type(value)is dict and set(value)==keys,"native time fields differ")
    need(value["status"]=="OBSERVED" and value["scope"]=="NATIVE_SETA_STATE_AND_CLOCK"
         and value["stamp"]==stamp(identity),"native time observation/stamp unavailable")
    need(type(value["ship"])is str and re.fullmatch(r"[1-9][0-9]{0,19}ULL",value["ship"])
         and int(value["ship"][:-3])<=2**64-1,"native uint64 occupied ship unavailable")
    need(type(value["activity"])is str and len(value["activity"])<=128
         and not any(ord(c)<32 or ord(c)==127 for c in value["activity"]),"native activity unavailable")
    need(all(type(value[k])is bool for k in ("has_seta","can_activate","active")),"native boolean unavailable")
    need(all(finite(value[k]) for k in ("game_time","ui_begin","ui_end"))
         and value["ui_end"]>=value["ui_begin"],"native clock invalid")
    need(value["game_clock"]=="NATIVE_GET_CURRENT_GAME_TIME" and value["ui_clock"]=="UI_ELAPSED"
         and all(value[k] is False for k in ("atomic","acceleration_factor_qualified","outcome_invariance_qualified")),
         "native clock scope changed")
    return copy.deepcopy(value)

def ratio(value,rounding):
    with localcontext() as ctx:
        ctx.prec=24;ctx.rounding=rounding
        decimal=str(Decimal(value.numerator)/Decimal(value.denominator))
    return {"numerator":str(value.numerator),"denominator":str(value.denominator),"decimal_bound":decimal}

class TimeEvidence:
    """One local collector; production reads/clock cannot be supplied as JSON.

    Synthetic fixtures may supply a monotonic-nanosecond callable explicitly.
    Local provenance is not a security boundary against arbitrary same-user code.
    """
    def __init__(self,client,*,synthetic=False,synthetic_clock=None):
        need(type(synthetic)is bool,"explicit synthetic switch required")
        need(synthetic_clock is None or (synthetic and callable(synthetic_clock)),
             "clock injection requires explicit synthetic mode")
        self.collector=ReadEvidence(client,synthetic=synthetic)
        self.synthetic=synthetic;self._clock=synthetic_clock if synthetic_clock is not None else time.monotonic_ns
        self._records={};self._lock=threading.Lock();self._last=None;self._pins=source_pins()
    def _now(self):
        n=self._clock()
        need(type(n)is int and n>=0 and (self._last is None or n>=self._last),"host clock invalid/backward")
        self._last=n
        return n
    def sample(self,issued_ship):
        need(self._lock.acquire(blocking=False),"nested time collection refused")
        try:
            need(source_pins()==self._pins,"time evidence sources changed")
            need(type(issued_ship)is str and 0<len(issued_ship)<=128
                 and not any(ord(c)<33 or ord(c)==127 for c in issued_ship),"bounded issued ship token required")
            need(len(self._records)<16,"time sample capacity")
            began=self._now()
            raw_handle=self.collector.collect("time_state",issued_ship)
            ended=self._now()
            receipt=self.collector.receipt(raw_handle)
            need(receipt["status"]=="OK" and receipt["request"]=={"verb":"time_state","args":[issued_ship]}
                 and receipt["transport_complete"] is True,"native time read unavailable")
            state=measured_state(_decode(receipt["capture"]["payload"]),receipt["identity"])
            need(source_pins()==self._pins,"time sources changed during read")
            handle=object()
            self._records[handle]={"read_handle":raw_handle,"serial":len(self._records)+1,
                "host_begin_ns":began,"host_end_ns":ended,"issued_ship":issued_ship,"state":state,
                "receipt":receipt}
            return handle
        finally:self._lock.release()
    def _row(self,handle):
        need(handle in self._records,"unknown or foreign time handle")
        need(source_pins()==self._pins,"time evidence sources changed")
        return self._records[handle]
    def receipt(self,handle):
        row=self._row(handle)
        return {"scope":"HOST_BRACKETED_NATIVE_TIME_SAMPLE","evidence_tier":"SYNTHETIC" if self.synthetic else "MACHINE_OBSERVED",
            "host_clock":"PYTHON_MONOTONIC_NS","host_begin_ns":row["host_begin_ns"],"host_end_ns":row["host_end_ns"],
            "serial":row["serial"],"issued_ship":row["issued_ship"],"state":copy.deepcopy(row["state"]),
            "raw_receipt":copy.deepcopy(row["receipt"]),"source_pins":copy.deepcopy(self._pins),
            "historical":True,"fresh_current_identity":False,"cross_clock_origin_qualified":False}
    def transcript(self,handle):
        row=self._row(handle)
        return self.collector.transcript(row["read_handle"])
    def compare(self,first,second):
        need(first is not second,"two distinct samples required")
        a=self._row(first);b=self._row(second)
        need(a["serial"]<b["serial"] and a["host_end_ns"]<b["host_begin_ns"],"ordered nonoverlapping host brackets required")
        for key in ("identity","profile_id","build","source_pins"):
            need(a["receipt"][key]==b["receipt"][key],"sample binding changed: "+key)
        need(a["issued_ship"]==b["issued_ship"] and a["state"]["ship"]==b["state"]["ship"],"occupied ship changed")
        need(a["state"]["active"]==b["state"]["active"] and a["state"]["activity"]==b["state"]["activity"],
             "sampled activity/active state changed")
        need(b["state"]["game_time"]>=a["state"]["game_time"] and b["state"]["ui_begin"]>=a["state"]["ui_end"],
             "native game/UI clock regressed")
        need(b["receipt"]["first_envelope_age"]>=a["receipt"]["last_envelope_age"],"envelope clock regressed")
        # Each native GetCurrentGameTime call occurs inside its host bracket.
        # Exact arithmetic is relative to the native numeric values as reported.
        game=Fraction(str(b["state"]["game_time"]))-Fraction(str(a["state"]["game_time"]))
        widest=Fraction(b["host_end_ns"]-a["host_begin_ns"],1_000_000_000)
        narrowest=Fraction(b["host_begin_ns"]-a["host_end_ns"],1_000_000_000)
        return {"status":"SAMPLED_GAME_TIME_PER_HOST_SECOND_BOUNDED","evidence_tier":"SYNTHETIC" if self.synthetic else "MACHINE_OBSERVED",
            "lower":ratio(game/widest,ROUND_FLOOR),"upper":ratio(game/narrowest,ROUND_CEILING),
            "game_delta":{"numerator":str(game.numerator),"denominator":str(game.denominator)},
            "host_widest_ns":b["host_end_ns"]-a["host_begin_ns"],"host_narrowest_ns":b["host_begin_ns"]-a["host_end_ns"],
            "first":self.receipt(first),"second":self.receipt(second),
            "endpoints_same_active_state":True,"continuous_active_state_qualified":False,
            "acceleration_factor_qualified":False,"outcome_invariance_qualified":False,
            "cross_clock_origin_qualified":False,"native_clock_resolution_qualified":False,
            "historical":True,"fresh_current_identity":False,
            "scope":"Average reported game-clock advance over an unknown interval contained by host brackets; no SETA factor or simulation-outcome proof."}

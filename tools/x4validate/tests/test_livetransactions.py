"""Actual LuaJIT module with synthetic native functions; never engine proof."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

luajit = pytest.importorskip("lupa.luajit21", reason="LuaJIT FFI contract qualification")
MODULE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_transactions.lua"
SETUP = r'''
ffi = require("ffi")
calls = {}
counts = {active=1, waiting=1}
returned = {}
after = {}
count_calls = {}
held_strings = {}
function cs(s)
    if s == false then return nil end
    local p = ffi.new("char[?]", #s+1, s)
    held_strings[#held_strings+1] = p
    return p
end
local function u(s)
    local x = ffi.new("uint64_t", 0)
    for c in s:gmatch(".") do x = x * 10 + tonumber(c) end
    return x
end
u64 = u
C = {}
C.GetNumBuildTasks = function(station, module, active, upgrades)
    assert(tostring(station)=="9007199254740993ULL")
    assert(module==0 and upgrades==true)
    local phase = active and "active" or "waiting"
    calls[#calls+1] = "count:"..phase
    count_calls[phase] = (count_calls[phase] or 0)+1
    if count_calls[phase] == 2 and after[phase] ~= nil then return after[phase] end
    return counts[phase]
end
C.GetBuildTasks = function(buf, n, station, module, active, upgrades)
    assert(module==0 and upgrades==true and n<=32)
    local phase = active and "active" or "waiting"
    calls[#calls+1] = "rows:"..phase
    for i=0,n-1 do
        buf[i].id = u((active or overlap) and "18446744073709551615" or "9007199254740994") - i
        buf[i].buildingcontainer = station
        buf[i].component = u("9007199254740995")
        buf[i].buildercomponent = u("9007199254740996")
        buf[i].macro = cs(macro == nil and "ship_test_macro" or macro)
        buf[i].factionid = cs(faction == nil and "player" or faction)
        buf[i].price = ffi.new("int64_t", -12)
        buf[i].ismissingresources = true
        buf[i].queueposition = i
        if zeroid then buf[i].id=0 end
        if missingcontainer then buf[i].buildingcontainer=0 end
        if zerocomponents then buf[i].component=0;buf[i].buildercomponent=0 end
    end
    return returned[phase] or n
end
resolve_calls = 0
resolve = function(token)
    resolve_calls=resolve_calls+1
    if token ~= "issued-station" or revoked or (revoke_after and resolve_calls>1) then return nil end
    if numeric_identity then return 9007199254740992 end
    return u("9007199254740993")
end
times = {1, 2}
tick = 0
clock = function() tick=tick+1; return times[tick] end
'''


def build(code="", pre=""):
    rt = luajit.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(SETUP)
    rt.execute(pre)
    factory = rt.execute(MODULE.read_text(encoding="utf-8"))
    rt.globals().factory = factory
    rt.execute("adapter=factory.new{ffi=ffi,C=C,resolve_issued_station=resolve,clock=clock}")
    rt.execute(code)
    return rt


def capture(code="", pre="", token="issued-station"):
    rt = build(code, pre)
    return rt, rt.globals().adapter.capture(token)


def codes(result):
    return {row["code"] for row in result["issues"].values()}


def check_actual_luajit_native_buffers_exact_ids_and_phase_contract():
    rt, r = capture()
    assert r["status"] == "complete" and r["enumeration_complete"]
    assert r["atomic"] is False and r["station"] == "9007199254740993ULL"
    rows = list(r["rows"].values())
    assert [row["id"] for row in rows] == ["18446744073709551615ULL", "9007199254740994ULL"]
    assert rows[0]["component"] == "9007199254740995ULL"
    assert rows[0]["buildercomponent"] == "9007199254740996ULL"
    assert rows[0]["price"] == "-12LL" and rows[0]["missingresources"] is True
    assert [p["phase"] for p in r["phases"].values()] == ["active", "waiting"]
    assert len(rt.globals().calls) == 6


def check_zero_has_two_count_observations_and_no_row_calls():
    rt, r = capture("counts={active=0,waiting=0}")
    assert r["status"] == "complete" and len(r["rows"]) == 0
    assert list(rt.globals().calls.values()) == ["count:active"]*2 + ["count:waiting"]*2


@pytest.mark.parametrize("code, expected", [
    ("counts.active=33", "phase_overflow"),
    ("counts.active=-1", "native_read_failed"),
    ("counts.active=0/0", "native_read_failed"),
    ("counts.active=1.5", "native_read_failed"),
    ("counts.active=false", "native_read_failed"),
    ("C.GetNumBuildTasks=function() error('unavailable') end", "native_read_failed"),
    ("C.GetBuildTasks=nil", "native_read_failed"),
    ("returned.active=2", "returned_out_of_bounds"),
    ("returned.active=-1", "returned_out_of_bounds"),
    ("returned.active=0.5", "returned_out_of_bounds"),
    ("returned.active=0", "phase_count_changed"),
    ("after.active=2", "phase_count_changed"),
    ("after.active=false", "native_read_failed"),
    ("overlap=true", "duplicate_or_phase_overlap"),
    ("zeroid=true", "invalid_row"),
    ("missingcontainer=true", "invalid_row"),
    ("macro=false", "invalid_row"),
    ("macro=''", "invalid_row"),
    ("macro='?'", "invalid_row"),
    ("faction=false", "invalid_row"),
    ("faction='player\\nforeign'", "invalid_row"),
    ("macro=string.rep('a',257)", "invalid_row"),
    ("times={2,1}", "invalid_end_clock"),
    ("times={1,math.huge}", "invalid_end_clock"),
    ("revoke_after=true", "station_binding_changed"),
])
def check_failed_unknown_inconsistent_reads_never_claim_complete(code, expected):
    _, r = capture(code)
    assert r["status"] == "partial" and not r["enumeration_complete"]
    assert expected in codes(r)


@pytest.mark.parametrize("code, expected", [
    ("revoked=true", "unissued_station"),
    ("numeric_identity=true", "invalid_station_identity"),
    ("times={0/0,1}", "clock_unavailable"),
])
def check_refusal_precedes_native_reads(code, expected):
    rt, r = capture(code)
    assert r["status"] == "refused" and expected in codes(r)
    assert len(rt.globals().calls) == 0


def check_foreign_token_refused_before_native_calls():
    rt, r = capture(token="foreign")
    assert r["status"] == "refused" and len(rt.globals().calls) == 0


def check_reported_overflow_never_allocates_or_fetches_phase():
    rt, r = capture("counts.active=4294967295")
    assert r["phases"][1]["read"] == "overflow"
    assert "rows:active" not in list(rt.globals().calls.values())
    assert len(r["rows"]) == 1  # independent waiting phase remains observed


def check_count_over_capacity_never_reads_out_of_bounds():
    _, r = capture("returned.active=99")
    assert len(r["rows"]) == 1
    assert r["rows"][1]["phase"] == "waiting"


def check_missing_rows_are_not_hidden_by_phase_counts():
    _, r = capture("macro=false")
    assert len(r["rows"]) == 0 and len(r["issues"]) == 2
    assert all(p["read"] == "observed" for p in r["phases"].values())
    assert r["status"] == "partial"


def check_zero_component_handles_are_preserved_without_inventing_ships():
    _, r = capture("zerocomponents=true")
    assert r["status"] == "complete"
    assert r["rows"][1]["component"] == "0ULL"
    assert r["rows"][1]["buildercomponent"] == "0ULL"


def check_existing_native_declaration_is_reused():
    rt = build()
    rt.execute("second=factory.new{ffi=ffi,C=C,resolve_issued_station=resolve,clock=clock}")
    r = rt.globals().second.capture("issued-station")
    assert r["status"] == "complete"


def check_bounded_maximum_population_is_preserved():
    _, r = capture("counts={active=32,waiting=32}")
    assert r["status"] == "complete" and len(r["rows"]) == 64



NATIVE_STRUCT = """
typedef struct {
    uint64_t id;
    uint64_t buildingcontainer;
    uint64_t component;
    const char* macro;
    const char* factionid;
    uint64_t buildercomponent;
    int64_t price;
    bool ismissingresources;
    uint32_t queueposition;
} BuildTaskInfo;
"""


@pytest.mark.parametrize("declaration", [
    "typedef uint32_t UniverseID;",
    "typedef int64_t UniverseID;",
    "typedef double UniverseID;",
    "typedef uint32_t BuildTaskID;",
    "typedef int64_t BuildTaskID;",
    "typedef struct { uint64_t id; } BuildTaskInfo;",
    NATIVE_STRUCT.replace("uint64_t id;", "uint64_t extra; uint64_t id;"),
    NATIVE_STRUCT.replace("uint64_t id;", "int64_t id;"),
    NATIVE_STRUCT.replace("uint64_t component;", "int64_t component;"),
    NATIVE_STRUCT.replace("int64_t price;", "uint64_t price;"),
    NATIVE_STRUCT.replace("int64_t price;", "double price;"),
    NATIVE_STRUCT.replace("const char* macro;", "void* macro;"),
    NATIVE_STRUCT.replace("const char* factionid;", "uint64_t factionid;"),
    NATIVE_STRUCT.replace("bool ismissingresources;", "uint8_t ismissingresources;"),
    NATIVE_STRUCT.replace("uint32_t queueposition;", "int32_t queueposition;"),
    NATIVE_STRUCT.replace("uint32_t queueposition;", "float queueposition;"),
    NATIVE_STRUCT.replace("uint64_t component;\n    const char* macro;",
                          "const char* macro;\n    uint64_t component;"),
    "#pragma pack(1)\n" + NATIVE_STRUCT + "\n#pragma pack()",
])
def check_incompatible_predeclared_native_types_refuse_before_calls(declaration):
    rt, r = capture(pre="ffi.cdef[[" + declaration + "]]")
    assert r["status"] == "refused"
    assert codes(r) == {"native_declaration_unavailable"}
    assert len(rt.globals().calls) == 0


def check_exact_predeclared_native_layout_is_accepted():
    pre = "ffi.cdef[[typedef uint64_t UniverseID; typedef uint64_t BuildTaskID;" + NATIVE_STRUCT + "]]"
    _, r = capture(pre=pre)
    assert r["status"] == "complete"


def _cases():
    cases = []
    for name, fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):
            continue
        params = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        if params:
            assert len(params) == 1
            for index, args in enumerate(params[0].args[1]):
                values = [args] if len(params[0].args[0].split(",")) == 1 else list(args)
                cases.append(pytest.param(name, values, id=f"{name}-{index}"))
        else:
            cases.append(pytest.param(name, [], id=name))
    return cases


@pytest.mark.parametrize("name,args", _cases())
def test_luajit_control(name, args):
    # LuaJIT uses protected Windows SEH for ordinary pcall errors. Run assertions
    # outside pytest's SEH-printing faulthandler, retaining timeout, exit status,
    # stdout and stderr on real failure. No engine or native library is loaded.
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), name, json.dumps(args)],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CONTROL_PASSED", result.stdout + result.stderr


if __name__ == "__main__":
    case = globals()[sys.argv[1]]
    assert sys.argv[1].startswith("check_") and callable(case)
    case(*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")

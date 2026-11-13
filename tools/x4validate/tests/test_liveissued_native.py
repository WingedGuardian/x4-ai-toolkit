"""Actual helper resolver with real LuaJIT integer semantics, synthetic native calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=(Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui/live_query.lua").read_text(encoding="utf-8")
WIRE=SOURCE.split("local function wire(v)",1)[1].split("--: For OBJECT ids.",1)[0]
RESOLVER=SOURCE.split("local function automation_resolve_issued(token,kind)",1)[1].split("local function ensure_automation()",1)[0]


def runtime(setup="", token="42ULL"):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().token=token
    r.execute("""
      ffi=require('ffi'); ffi.cdef('typedef uint64_t UniverseID;')
      issued_ids={[token]=true}; conversions=0; native_class='ship'; valid=true
      converted=42; second=nil
      function ConvertStringTo64Bit(s) conversions=conversions+1;if conversions>1 and second~=nil then return second end;return converted end
      function ConvertStringToLuaID(s) return {token=s} end
      function ConvertIDTo64Bit(id) return converted end
      function IsValidComponent(id) return valid end
      C={IsComponentClass=function(id,kind) return kind==native_class end}
    """)
    r.execute(setup)
    r.execute("local function wire(v)"+WIRE+"\nlocal function automation_resolve_issued(token,kind)"+RESOLVER+"\nresolve=automation_resolve_issued")
    return r


@pytest.mark.parametrize("kind,setup,token",[
    ("ship","native_class='ship'","42ULL"),
    ("station","native_class='station'","42ULL"),
    ("object","","42ULL"),
    ("sector","native_class='sector'","ID: 42"),
])
def test_exact_issued_identity_resolves(kind,setup,token):
    r=runtime(setup,token);r.globals().kind=kind
    assert r.eval("tostring(resolve(token,kind))")=="42ULL"


@pytest.mark.parametrize("setup",[
    "converted=0", "converted=-42", "converted=42.5", "converted=0/0",
    "converted=math.huge", "converted=9007199254740992", "converted=43",
    "converted='42'", "converted=ffi.new('int64_t',42)",
    "native_class='station'", "issued_ids={}",
])
def test_bad_conversion_class_or_retired_identity_refuses(setup):
    r=runtime(setup)
    assert r.eval("pcall(resolve,token,'ship')")[0] is False


@pytest.mark.parametrize("token",["042ULL","0ULL","42","ID: 42","18446744073709551616ULL"])
def test_noncanonical_object_token_refuses(token):
    r=runtime(token=token)
    assert r.eval("pcall(resolve,token,'ship')")[0] is False


def test_unknown_token_refuses_before_native_conversion():
    r=runtime()
    assert r.eval("pcall(resolve,'43ULL','ship')")[0] is False
    assert r.globals().conversions==0


def test_exact_wide_cdata_is_not_rounded_to_double():
    r=runtime("converted=9007199254740993ULL",token="9007199254740993ULL")
    assert r.eval("tostring(resolve(token,'ship'))")=="9007199254740993ULL"


def test_invalid_object_refuses():
    r=runtime("valid=false")
    assert r.eval("pcall(resolve,token,'object')")[0] is False


@pytest.mark.parametrize("value",["-42","9007199254740992","43","0/0","42.5"])
def test_validity_conversion_cannot_change_identity(value):
    r=runtime("second="+value)
    assert r.eval("pcall(resolve,token,'object')")[0] is False


def test_sector_token_cannot_be_used_as_object():
    r=runtime(token="ID: 42")
    assert r.eval("pcall(resolve,token,'object')")[0] is False


def test_reentrant_invalidation_refuses():
    r=runtime("C.IsComponentClass=function() issued_ids={};return true end")
    assert r.eval("pcall(resolve,token,'ship')")[0] is False

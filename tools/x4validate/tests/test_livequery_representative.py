"""`representative <faction>`: the actual verb source with real LuaJIT integers, synthetic native calls.
Read-only; the representative's id is ISSUED (the licence menu needs the trader as its param[3])."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE = (Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_query.lua").read_text(encoding="utf-8")
WIRE = SOURCE.split("local function wire(v)", 1)[1].split("--: For OBJECT ids.", 1)[0]
VERB = SOURCE.split("verbs.representative = function(seq, faction)", 1)[1].split("\nverbs.stations = function", 1)[0]
VANILLA = "UniverseID GetFactionRepresentative(const char* factionid);"   # menu_encyclopedia.lua:101


def runtime(setup=""):
    r = LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
      ffi=require('ffi'); ffi.cdef('typedef uint64_t UniverseID;')
      cdef_ok=true; issued={}; replies={}; rep=ffi.new('UniverseID',42); raised=false
      function reply(seq,status,payload) replies[#replies+1]={status=status,payload=payload} end
      function issue(v) issued[#issued+1]=tostring(v); return v end
      function ConvertStringTo64Bit(s) return 42 end
      function GetComponentData(id,f) return 'TTC Rep|resentative' end
      C={GetFactionRepresentative=function(f) if raised then error('boom') end; return rep end}
    """)
    r.execute(setup)
    r.execute("local function wire(v)" + WIRE + "\nverbs={}\nverbs.representative = function(seq, faction)" + VERB)
    return r


def call(r, *args):
    r.globals().verbs.representative("s", *args)
    rep = r.globals().replies
    return rep[len(rep)]["status"], rep[len(rep)]["payload"]


def test_issues_the_representative_and_names_it():
    r = runtime()
    status, payload = call(r, "terrantrader")
    assert status == "OK" and payload == "representative=42ULL|faction=terrantrader|name=TTC Rep/resentative"
    assert len(r.globals().issued) == 1


@pytest.mark.parametrize("setup,arg,status", [
    ("rep=ffi.new('UniverseID',0)", "terrantrader", "ABSENT"),
    ("raised=true", "terrantrader", "ERR"),
    ("cdef_ok=false", "terrantrader", "ERR"),
    ("", "bad faction!", "ERR"),
    ("", None, "ERR"),
])
def test_refusals_issue_nothing(setup, arg, status):
    r = runtime(setup)
    assert call(r, arg)[0] == status
    assert len(r.globals().issued) == 0


def test_signature_is_vanillas_and_the_read_is_whitelisted():
    assert VANILLA in SOURCE
    assert "pausestate=true,representative=true}" in SOURCE

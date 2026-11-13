"""Offline protocol/guard tests. Synthetic native results, never engine proof."""
from __future__ import annotations

import copy
import io
import json
import re
from types import SimpleNamespace

import pytest

from x4validate import _liveequipment as E, _livecli
from test_modlua_rearm import _build, live, ask, reload_ui, g, MOD_LUA

SHIP = "1046331ULL"
NONCE = "a" * 32
SETUP = r'''
_G.component_data = {classid="ship", macro="engine_test_macro"}
_G.eq_counts = {engine=1, shield=1, turret=1, weapon=1}
_G.eq_groups = {}
_G.eq_time = 100
_G.eq_native_calls = 0
function getElapsedTime() return _G.eq_time end
local function counted() _G.eq_native_calls = _G.eq_native_calls + 1 end
_G.fake_C.GetNumUpgradeSlots = function(id, macro, typ)
    counted(); assert(id == 1046331 and macro == "")
    return _G.eq_counts[typ]
end
_G.fake_C.GetUpgradeSlotGroup = function(id, macro, typ, slot)
    counted(); assert(slot >= 1 and slot <= _G.eq_counts[typ])
    return {path=_G.eq_path or "..", group=_G.eq_group or ""}
end
_G.fake_C.GetUpgradeSlotCurrentComponent = function(id, typ, slot)
    counted(); return _G.eq_component or "201ULL"
end
_G.fake_C.GetUpgradeSlotCurrentMacro = function(id, module, typ, slot)
    counted(); assert(module == 0)
    return _G.eq_slotmacro or "engine_test_macro"
end
_G.fake_C.GetNumUpgradeGroups = function(id, macro)
    counted(); return _G.eq_ngroups or #_G.eq_groups
end
_G.fake_C.GetUpgradeGroups2 = function(buf, n, id, macro)
    counted()
    for i=0,n-1 do buf[i] = _G.eq_groups[i+1] or {contextid="10ULL",path="p"..i,group="g"..i} end
    return _G.eq_returned or n
end
_G.fake_C.GetUpgradeGroupInfo2 = function(id, macro, context, path, group, typ)
    counted(); assert(context ~= nil and path ~= "" and group ~= "")
    return {currentcomponent="201ULL", currentmacro="engine_test_macro", slotsize="m",
            count=1, operational=1, total=1}
end
'''


class Pipe:
    def __init__(self, rt, mutate=None):
        self.rt, self.seq, self.calls, self.mutate = rt, 100, [], mutate
        self.payloads = []

    def ask(self, *args):
        self.seq += 1
        self.calls.append(args)
        result = ask(self.rt, self.seq, *args)
        if self.mutate:
            result = self.mutate(result, len(self.calls))
        self.payloads.append(result.payload)
        return result


def setup(code="", pre=""):
    rt = live(_build(pre=pre))
    rt.execute(SETUP + code)
    assert ask(rt, 1, "player").status == "OK"
    return rt


def capture(code="", rt=None):
    rt = rt or setup(code)
    pipe = Pipe(rt)
    return E.fetch(pipe, SHIP, NONCE), pipe


def test_actual_lua_and_reader_complete_capture():
    result, pipe = capture()
    assert result["status"] == "complete"
    assert {r["type"] for r in result["slots"]} == E.TYPES
    assert all(r["state"] == "installed" and r["component"] == "201ULL" for r in result["slots"])
    assert result["snapshot"]["atomic"] == "false"
    assert len(pipe.calls) == 1
    assert g(pipe.rt, "pause_calls") == g(pipe.rt, "unpause_calls") == 0


def test_complete_capture_issues_returned_fitted_component_only():
    rt = setup('_G.eq_groups={{contextid="99ULL",path="group/path",group="main"}}')
    assert ask(rt, 2, "component", "201ULL").status == "ERR"
    result, _ = capture(rt=rt)
    assert result["status"] == "complete"
    assert ask(rt, 3, "component", "201ULL").status == "OK"
    assert ask(rt, 4, "component", "99ULL").status == "ERR"
    rt.execute('_G.invalid_ids={["201"]=true}')
    assert ask(rt, 5, "component", "201ULL").status == "ABSENT"
    reload_ui(rt)
    assert ask(rt, 6, "component", "201ULL").status == "ERR"


@pytest.mark.parametrize("code", [
    '_G.eq_slotmacro="different_macro"',
    '_G.eq_component="0ULL"; _G.eq_slotmacro=""',
    '_G.eq_counts={engine=0,shield=0,turret=0,weapon=0}; '
    '_G.eq_groups={{contextid="99ULL",path="group/path",group="main"}}',
    '_G.fake_C.GetNumUpgradeGroups=function() error("late group failure") end',
    '_G.eq_counts={engine=1000000,shield=0,turret=0,weapon=0}',
    '_G.eq_counts={engine=1024,shield=0,turret=0,weapon=0}; '
    '_G.eq_path=string.rep("|",256); _G.eq_group=string.rep("|",256)',
    '_G.fake_C.GetNumUpgradeGroups=function() '
    '_G.invalid_ids={["1046331"]=true}; return 0 end',
])
def test_incomplete_empty_and_summary_only_captures_issue_no_component(code):
    rt = setup(code)
    capture(rt=rt)
    assert ask(rt, 2, "component", "201ULL").status == "ERR"


@pytest.mark.parametrize("code", [
    '_G.fake_C.GetNumUpgradeGroups=function() _G.eq_time=99; return 0 end',
    '_G.fake_C.GetNumUpgradeGroups=function() _G.__fire_game_load(); return 0 end',
    '_G.clock_reads=0; function getElapsedTime() '
    '_G.clock_reads=_G.clock_reads+1; if _G.clock_reads>=3 then return 99 end; return 100 end',
])
def test_aborted_capture_never_issues_component(code):
    rt = setup()
    rt.execute(code)
    assert ask(rt, 2, "equipment", SHIP, NONCE).status == "ERR"
    assert ask(rt, 3, "component", "201ULL").status == "ERR"


def test_complete_multipage_capture_issues_only_pages_returned():
    rt = setup('''
_G.eq_counts={engine=300,shield=0,turret=0,weapon=0}
_G.fake_C.GetUpgradeSlotCurrentComponent=function(id,typ,slot)
 return tostring(200+slot).."ULL"
end
''')
    first = ask(rt, 2, "equipment", SHIP, NONCE)
    assert first.status == "OK"
    assert "component=201ULL" in first.payload and "component=500ULL" not in first.payload
    assert ask(rt, 3, "component", "201ULL").status == "OK"
    assert ask(rt, 4, "component", "500ULL").status == "ERR"
    token = re.search(r"\ttoken=([^\t\n]+)", first.payload).group(1)
    pages = int(re.search(r"\tpages=(\d+)", first.payload).group(1))
    assert pages > 1
    last = ask(rt, 5, "equipment-page", token, str(pages))
    assert last.status == "OK" and "component=500ULL" in last.payload
    assert ask(rt, 6, "component", "500ULL").status == "OK"


def test_empty_slots_are_measurements_not_failed_zero():
    result, _ = capture('_G.eq_component="0ULL"; _G.eq_slotmacro=""')
    assert result["status"] == "complete"
    assert {r["state"] for r in result["slots"]} == {"empty"}
    zero, _ = capture('_G.eq_counts={engine=0,shield=0,turret=0,weapon=0}')
    assert zero["status"] == "complete" and zero["slots"] == []
    failed, _ = capture('_G.fake_C.GetNumUpgradeSlots=function() error("unavailable") end')
    assert failed["status"] == "partial" and failed["slots"] == []
    assert failed["snapshot"]["engine_reported"] == "?"


@pytest.mark.parametrize("code", [
    '_G.fake_C.GetUpgradeSlotCurrentComponent=nil',
    '_G.fake_C.GetUpgradeSlotCurrentMacro=function() return nil end',
    '_G.fake_C.GetUpgradeSlotGroup=function() error("bad group") end',
    '_G.eq_slotmacro="different_macro"',
    '_G.invalid_ids={["201"]=true}',
    '_G.nil_fields={macro=true}',
    '_G.component_data.macro=""',
    '_G.component_data.macro=string.rep("x",257)',
])
def test_slot_read_failures_are_partial(code):
    result, _ = capture(code)
    assert result["status"] == "partial"
    assert all(s["state"] == "unknown" for s in result["slots"])


def test_grouped_missing_component_never_claims_empty_and_avoids_direct_query():
    result, pipe = capture('''
_G.eq_path="group/path"; _G.eq_group="main"; _G.eq_component="0ULL"
_G.eq_slotmacro=""
_G.fake_C.GetUpgradeSlotCurrentMacro=function(id,module,typ,slot)
 assert(typ=="engine"); return ""
end
_G.eq_groups={{contextid="99ULL",path="group/path",group="main"}}
''')
    assert result["status"] == "partial"
    assert result["snapshot"]["reads_complete"] == "true"
    assert result["snapshot"]["enumeration_complete"] == "true"
    assert result["snapshot"]["unknown_slots"] == "3"
    assert len(result["slots"]) == 4 and len(result["group_summaries"]) == 3
    assert {r["context"] for r in result["group_summaries"]} == {"99ULL"}
    assert [s["state"] for s in result["slots"] if s["type"] == "engine"] == ["empty"]


def test_group_root_descriptor_preserved_without_fake_summaries():
    result, _ = capture('_G.eq_groups={{contextid="0ULL",path="..",group=""}}')
    assert result["status"] == "complete"
    assert len(result["groups"]) == 1 and not result["group_summaries"]


@pytest.mark.parametrize("code", [
    '_G.fake_C.GetNumUpgradeGroups=nil',
    '_G.eq_ngroups=2; _G.eq_returned=1',
    '_G.eq_ngroups=2; _G.eq_returned=3',
    '_G.eq_ngroups=2; _G.fake_C.GetUpgradeGroupInfo2=nil',
    '_G.eq_ngroups=2; _G.fake_C.GetUpgradeGroups2=nil',
])
def test_group_failures_do_not_become_complete_zero(code):
    result, _ = capture(code)
    assert result["status"] == "partial"


def test_group_allocation_cap_and_native_return_bounds():
    result, pipe = capture('_G.eq_ngroups=1000000')
    assert result["status"] == "partial"
    assert len(result["groups"]) <= 256
    assert int(result["snapshot"]["calls"]) <= 8192
    assert sum(len(s.encode()) for s in pipe.payloads) <= 262144


def test_large_slot_capture_has_immutable_pages_and_explicit_cap():
    rt = setup('_G.eq_counts={engine=1000000,shield=0,turret=0,weapon=0}')
    pipe = Pipe(rt)
    result = E.fetch(pipe, SHIP, NONCE)
    assert result["status"] == "partial"
    assert 100 < len(result["slots"]) <= 1024
    assert len(pipe.calls) > 1
    assert all(len(s.encode()) <= 32000 for s in pipe.payloads)
    assert sum(len(s.encode()) for s in pipe.payloads) <= 262144
    calls = g(rt, "eq_native_calls")
    token = result["snapshot"]["token"]
    rt.execute('_G.eq_slotmacro="changed_after_capture"')
    r = ask(rt, 999, "equipment-page", token, "1")
    assert r.status == "OK" and "changed_after_capture" not in r.payload
    assert g(rt, "eq_native_calls") == calls


def test_row_byte_cap_reports_partial_without_unbounded_cache():
    result, pipe = capture('''
_G.eq_counts={engine=1024,shield=0,turret=0,weapon=0}
_G.eq_path=string.rep("|",256); _G.eq_group=string.rep("|",256)
_G.component_data.macro=string.rep("|",256); _G.eq_slotmacro=_G.component_data.macro
''')
    assert result["status"] == "partial" and len(result["slots"]) < 1024
    assert result["snapshot"]["limited"] == "true"
    assert all(len(s.encode()) <= 32000 for s in pipe.payloads)
    assert sum(len(s.encode()) for s in pipe.payloads) <= 262144


@pytest.mark.parametrize("code,expected", [
    ('_G.component_data.classid="station"', "ERR"),
    ('_G.nil_fields={classid=true}', "ERR"),
    ('IsValidComponent=nil', "ERR"),
    ('_G.invalid_ids={["1046331"]=true}', "ABSENT"),
])
def test_class_and_validity_guards(code, expected):
    rt = setup(code)
    assert ask(rt, 2, "equipment", SHIP, NONCE).status == expected
    assert g(rt, "eq_native_calls") == 0


@pytest.mark.parametrize("ship,nonce", [
    ("999ULL", NONCE), ("ID: 7479", NONCE), ("1;os.execute()", NONCE),
    (SHIP, "bad"), (SHIP, "a" * 33), (SHIP, "A" * 32),
])
def test_invalid_requests_do_not_reach_native_equipment(ship, nonce):
    rt = setup()
    assert ask(rt, 2, "equipment", ship, nonce).status == "ERR"
    assert g(rt, "eq_native_calls") == 0


def test_equipment_declaration_failure_is_scoped():
    rt = setup(pre='_G.cdef_raises_on="GetNumUpgradeSlots"')
    assert ask(rt, 2, "equipment", SHIP, NONCE).status == "ERR"
    assert ask(rt, 3, "player").status == "OK"


def test_continuations_refuse_reload_supersession_expiry_clock_reset_and_bad_pages():
    for mode in ("reload", "load_hook", "supersede", "ttl", "clock", "page"):
        rt = setup()
        result = E.fetch(Pipe(rt), SHIP, NONCE)
        token = result["snapshot"]["token"]
        if mode == "reload":
            reload_ui(rt)
        elif mode == "load_hook":
            g(rt, "__fire_game_load")()
        elif mode == "supersede":
            assert ask(rt, 9, "equipment", SHIP, "b" * 32).status == "OK"
        elif mode == "ttl":
            rt.execute("_G.eq_time=161")
        elif mode == "clock":
            rt.execute("_G.eq_time=99")
        assert ask(rt, 10, "equipment-page", token, "2" if mode == "page" else "1").status == "ERR"
    rt = setup()
    E.fetch(Pipe(rt), SHIP, NONCE)
    assert ask(rt, 11, "equipment", SHIP, NONCE).status == "ERR"


def test_delimiters_and_controls_roundtrip_as_data_not_rows_or_terminal_escapes():
    result, pipe = capture(r'''
_G.component_data.macro="test\t\n\r|%\027[31m_macro"
_G.eq_slotmacro=_G.component_data.macro
''')
    assert result["status"] == "complete" and len(result["slots"]) == 4
    assert result["slots"][0]["macro"] == "test\t\n\r|%\x1b[31m_macro"
    assert "\\u001b" in json.dumps(result)
    assert "\x1b" not in pipe.payloads[0]


@pytest.mark.parametrize("mutator", [
    lambda s: s.replace("slots=4", "slots=5"),
    lambda s: s.replace("unknown_slots=0", "unknown_slots=1"),
    lambda s: s.replace("page=1", "page=2"),
    lambda s: s.replace("pages=1", "pages=999"),
    lambda s: s.replace("atomic=false", "atomic=true"),
    lambda s: s.replace("start=100", "start=nan"),
    lambda s: s.replace("ship=" + SHIP, "ship=999ULL"),
    lambda s: s.replace("version=1", "version=1\tversion=1"),
    lambda s: s.replace("slot=1", "slot=0", 1),
    lambda s: s.replace("state=installed", "state=empty", 1),
    lambda s: s.replace("read=ok", "read=failed", 1),
    lambda s: s + "\nBOGUS\tx=1",
    lambda s: s.replace("macro=engine_test_macro", "macro=bad%QZ", 1),
])
def test_host_refuses_malformed_or_false_complete_records(mutator):
    rt = setup()
    pipe = Pipe(rt, lambda r, n: SimpleNamespace(status=r.status, payload=mutator(r.payload)))
    with pytest.raises(ValueError):
        E.fetch(pipe, SHIP, NONCE)


def test_host_refuses_changed_metadata_or_missing_page():
    for mode in ("changed", "missing"):
        rt = setup('_G.eq_counts={engine=300,shield=0,turret=0,weapon=0}')
        def mutate(r, n):
            if n == 2:
                return SimpleNamespace(status="ERR" if mode == "missing" else "OK",
                                       payload=r.payload.replace("finish=100", "finish=101"))
            return r
        with pytest.raises(ValueError):
            E.fetch(Pipe(rt, mutate), SHIP, NONCE)


def test_cli_prints_json_and_partial_has_nonzero_status(monkeypatch):
    for code, expected in [("", 0), ('_G.eq_component="0ULL"', 3)]:
        pipe = Pipe(setup(code))
        class Context:
            def __enter__(self): return pipe
            def __exit__(self, *args): pass
        monkeypatch.setattr(_livecli, "_live_open", lambda *a: Context())
        output = io.StringIO()
        assert _livecli.cmd_equipment(SHIP, None, 10, output) == expected
        assert json.loads(output.getvalue())["snapshot"]["ship"] == SHIP


@pytest.mark.parametrize("native_first", [True, False])
def test_native_ffi_declarations_and_buffer_call_boundary(native_first):
    """Real LuaJIT pointer conversion, not engine execution or ABI certification."""
    import subprocess
    import sys
    native = '''
    typedef struct {const char* path; const char* group;} UpgradeGroup;
    typedef struct {UniverseID contextid; const char* path; const char* group;} UpgradeGroup2;
    typedef struct {UniverseID currentcomponent; const char* currentmacro; const char* slotsize;
                    uint32_t count; uint32_t operational; uint32_t total;} UpgradeGroupInfo;
    UpgradeGroup GetUpgradeSlotGroup(UniverseID,const char*,const char*,size_t);
    uint32_t GetUpgradeGroups2(UpgradeGroup2*,uint32_t,UniverseID,const char*);
    UpgradeGroupInfo GetUpgradeGroupInfo2(UniverseID,const char*,UniverseID,const char*,const char*,const char*);
    '''
    src = MOD_LUA.read_text(encoding="utf-8")
    declaration = "local declared = false\n" + src.split("    local declared = false\n", 1)[1].split(
        "    local function integer", 1)[0] + "\nassert(declared)"
    callback = '''
    cb=ffi.cast("uint32_t(*)(UpgradeGroup2*,uint32_t,UniverseID,const char*)", function(buf,n,id,macro)
        assert(buf[0].contextid==99)
        assert(ffi.string(buf[0].path)=="path" and ffi.string(buf[0].group)=="group")
        return n
    end)
    '''
    invoke = '''
    buf=ffi.new("UpgradeGroup2[?]",1)
    buf[0].contextid=99; buf[0].path="path"; buf[0].group="group"
    assert(cb(buf,1,0,"")==1)
    cb:free()
    '''
    native = "ffi.cdef[[" + native + "]]"
    script = 'ffi=require("ffi"); ffi.cdef("typedef uint64_t UniverseID;")\n'
    # Retain the FIRST signature across later declarations; creating a new callback
    # afterwards would not catch a typedef changing under a declared C function.
    script += "\n".join([native, callback, declaration] if native_first else
                        [declaration, callback, native])
    script += "\n" + invoke
    # Isolate the native call boundary. On Windows, LuaJIT's protected unknown-type
    # probes use SEH which pytest's faulthandler misleadingly labels fatal even when
    # pcall handles it. The child still must finish every assertion and exit zero.
    run = subprocess.run([sys.executable, "-B", "-c",
                          "import sys; from lupa.luajit21 import LuaRuntime; "
                          "LuaRuntime().execute(sys.stdin.read())"],
                         input=script, text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("n,expected", [(1023, "complete"), (1024, "complete"), (1025, "partial")])
def test_slot_cap_exact_boundary(n, expected):
    result, pipe = capture(f'_G.eq_counts={{engine={n},shield=0,turret=0,weapon=0}}')
    assert result["status"] == expected
    assert len(result["slots"]) == min(n, 1024)
    assert int(result["snapshot"]["calls"]) <= 8192
    assert g(pipe.rt, "eq_native_calls") <= 8192


@pytest.mark.parametrize("n,expected", [(255, "complete"), (256, "complete"), (257, "partial")])
def test_group_cap_exact_boundary(n, expected):
    result, _ = capture(f'_G.eq_ngroups={n}')
    assert result["status"] == expected
    assert len(result["groups"]) == min(n, 256)
    assert len(result["group_summaries"]) == 3 * min(n, 256)


@pytest.mark.parametrize("bad", ["-1", "0/0", "math.huge", "1.5"])
def test_invalid_native_count_is_unknown_not_zero(bad):
    result, _ = capture(f'_G.fake_C.GetNumUpgradeSlots=function() return {bad} end')
    assert result["status"] == "partial" and not result["slots"]
    assert result["snapshot"]["engine_reported"] == "?"


def test_group_returned_zero_is_not_claimed_complete():
    result, _ = capture('_G.eq_ngroups=2; _G.eq_returned=0')
    assert result["status"] == "partial" and not result["groups"]
    assert result["snapshot"]["groups_returned"] == "0"


def test_native_current_component_conversion_is_not_lossy():
    rt = setup('_G.eq_component="9007199254740993ULL"')
    rt.execute('''
local old_convert = ConvertStringTo64Bit
function ConvertStringTo64Bit(s)
 if s=="9007199254740993ULL" then return "safe_component_handle" end
 return old_convert(s)
end
local old_data = GetComponentData
function GetComponentData(id,field)
 if field=="macro" then assert(id=="safe_component_handle"); return "engine_test_macro" end
 return old_data(id,field)
end
''')
    result, _ = capture(rt=rt)
    assert result["status"] == "complete"
    assert {s["component"] for s in result["slots"]} == {"9007199254740993ULL"}


@pytest.mark.parametrize("kind,field", [
    ("GROUP", "context"), ("GROUP", "path"), ("GROUP", "group"),
    ("SUMMARY", "component"), ("SUMMARY", "context"), ("SUMMARY", "count"),
    ("SUMMARY", "operational"), ("SUMMARY", "total"), ("SUMMARY", "macro"),
])
def test_readable_group_unknowns_cannot_claim_complete(kind, field):
    rt = setup('_G.eq_ngroups=1')
    def mutate(r, n):
        lines = r.payload.split("\n")
        for i, line in enumerate(lines):
            if line.startswith(kind + "\t"):
                lines[i] = re.sub(r"(?<=\t)" + field + r"=[^\t]*", field + "=?", line)
                break
        return SimpleNamespace(status=r.status, payload="\n".join(lines))
    with pytest.raises(ValueError):
        E.fetch(Pipe(rt, mutate), SHIP, NONCE)


def test_empty_slot_cannot_have_a_fitted_macro():
    rt = setup('_G.eq_component="0ULL"; _G.eq_slotmacro=""')
    pipe = Pipe(rt, lambda r, n: SimpleNamespace(status=r.status, payload=r.payload.replace(
        "macro=?\t", "macro=contradictory_macro\t", 1)))
    with pytest.raises(ValueError):
        E.fetch(pipe, SHIP, NONCE)


def test_same_group_name_in_distinct_contexts_stays_distinct():
    result, _ = capture('''_G.eq_groups={
 {contextid="9007199254740993ULL",path="same",group="same"},
 {contextid="9007199254740994ULL",path="same",group="same"}}''')
    assert result["status"] == "complete"
    assert len(result["groups"]) == 2 and len(result["group_summaries"]) == 6
    assert {g["context"] for g in result["groups"]} == {"9007199254740993ULL", "9007199254740994ULL"}


@pytest.mark.parametrize("field,value", [("errors", "oops"), ("start", "-1"), ("loaded_at", "-1")])
def test_partial_metadata_still_requires_well_formed_nonnegative_numbers(field, value):
    rt = setup('_G.eq_component="0ULL"')
    def mutate(r, n):
        return SimpleNamespace(status=r.status, payload=re.sub(
            r"(?<=\t)" + field + r"=[^\t\n]*", field + "=" + value, r.payload, count=1))
    with pytest.raises(ValueError):
        E.fetch(Pipe(rt, mutate), SHIP, NONCE)


def test_reserved_question_mark_is_unreadable_not_literal_success():
    result, _ = capture('_G.eq_path="?"')
    assert result["status"] == "partial"
    assert result["snapshot"]["reads_complete"] == "false"


def test_actual_chunk_compiles_under_luajit():
    from lupa.luajit21 import LuaRuntime
    runtime = LuaRuntime()
    runtime.globals().source = MOD_LUA.read_text(encoding="utf-8")
    runtime.execute('assert(loadstring(source))')


def test_reader_enforces_native_decoded_string_bound():
    rt = setup()
    def mutate(r, n):
        return SimpleNamespace(status=r.status, payload=r.payload.replace(
            "engine_test_macro", "m" * 257))
    with pytest.raises(ValueError, match="native string"):
        E.fetch(Pipe(rt, mutate), SHIP, NONCE)


@pytest.mark.parametrize("value", ["?", "different_macro"])
def test_installed_direct_slot_must_agree_with_component_macro(value):
    rt = setup()
    def mutate(r, n):
        return SimpleNamespace(status=r.status, payload=r.payload.replace(
            "slot_macro=engine_test_macro", "slot_macro=" + value, 1))
    with pytest.raises(ValueError):
        E.fetch(Pipe(rt, mutate), SHIP, NONCE)


def test_reader_enforces_row_bytes_independently_of_page_and_cache_bounds():
    # Start with a real, large producer capture. Pad declared native strings within
    # their per-field bound, then re-page below32KB; metadata/counters remain valid.
    result, pipe = capture('_G.eq_counts={engine=1024,shield=0,turret=0,weapon=0}')
    lines = [line for payload in pipe.payloads for line in payload.split("\n")[1:] if line]
    lines = [line.replace("macro=engine_test_macro", "macro=" + "m" * 70) for line in lines]
    assert 240000 < sum(len(s.encode()) + 1 for s in lines) < 250000
    chunks, current = [], []
    for line in lines:
        if sum(len(s) + 1 for s in current) + len(line) + 1 > 30000:
            chunks.append(current); current = []
        current.append(line)
    chunks.append(current)
    h = pipe.payloads[0].split("\n")[0]
    payloads = []
    for i, chunk in enumerate(chunks, 1):
        head = re.sub(r"(?<=\t)page=\d+", "page=" + str(i), h)
        head = re.sub(r"(?<=\t)pages=\d+", "pages=" + str(len(chunks)), head)
        payloads.append(head + "\n" + "\n".join(chunk))
    assert sum(len(s.encode()) for s in payloads) <= 262144
    class Replay:
        def ask(self, *args): return SimpleNamespace(status="OK", payload=payloads.pop(0))
    with pytest.raises(ValueError, match="captured-row byte"):
        E.fetch(Replay(), SHIP, NONCE)

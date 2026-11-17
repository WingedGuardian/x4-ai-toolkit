"""Behavioural tests for the GAME-SIDE lua, run against a real Lua interpreter.

WHY THIS EXISTS. Everything else about the live channel is testable in Python, but the
half that decides whether the channel survives a disconnect is 40 lines of lua that only
ever ran inside X4 — where a mistake costs a game launch, and one specific mistake costs
a FORCE-KILL.

THE HANG THIS GUARDS. `sn_mod_support_apis/ui/named_pipes/pipes.lua:440` `Close_Pipe`
drains the read FIFO in a `while not FIFO.Is_Empty(...)` loop, calling every callback
with `"ERROR"`, and only sets `pipes[name] = nil` AFTERWARDS. `Declare_Pipe` reuses
existing state. So a `Schedule_Read` called synchronously from inside that callback
writes into **the very FIFO being drained** — the loop never terminates and the game
hangs on the UI thread.

So the contract is not "it re-arms". It is **"it re-arms LATER, never synchronously"**,
and the test below can tell those two apart. A test that only asserted "re-arms" would
pass on the hanging version.

The stubs are deliberately thin — they record calls rather than emulate the API — because
the questions here are *how many times* and *from where*, not *what came back*.
"""
from __future__ import annotations

import pathlib
import re

import pytest

lupa = pytest.importorskip("lupa.luajit21", reason="lua runtime; dev-only check of the game-side mod")

#: Located by GLOB, not by a spelled-out folder name. The game-side mod is a personal
#: artifact and its folder carries a personal prefix; this file ships, so naming it here
#: would put that identifier in the public package -- which the mirror's scanner catches
#: only AFTER a push. Same reason the marker assertions below match a suffix.
def _find_mod_lua(pattern):
    """The shipped game extension, wherever it lives: mods/ in the public toolkit,
    dev/ in the private workspace.

    MEASURED 2026-09-01: these tests looked ONLY at `parents[3]/"dev"`, which does not
    exist in the public repo -- the extension ships at mods/. 125 tests skipped silently,
    guarding a Lua contract whose violation HANGS X4 and needs a force-kill. A skip that
    is structurally guaranteed is not coverage; it is a green tick over untested code.

    Returns (path, roots_searched). The caller FAILS rather than skips when a root exists
    but holds no match, because that is a broken layout, not an absent one.
    """
    root = pathlib.Path(__file__).resolve().parents[3]
    searched = []
    for name in ("mods", "dev"):
        d = root / name
        if not d.is_dir():
            continue
        searched.append(str(d))
        hits = sorted(d.glob(pattern))
        if hits:
            return hits[0], searched
    return None, searched

def _require_mod_lua(hit, roots, what):
    """Skip only when there is nowhere to look; FAIL when a root exists and is empty.

    "mods/ is present but holds no live_query.lua" is a BROKEN LAYOUT, not an absent
    one, and a skip there is a green tick over untested code -- which is exactly what
    happened when these tests looked only in dev/: 125 skipped, silently, guarding a Lua
    contract whose violation hangs X4 and needs a force-kill.
    """
    if hit is not None:
        return hit
    if roots:
        raise AssertionError(
            "searched %s and found no %s. A root exists but holds no game-side mod, "
            "which is a broken layout -- refusing to skip over it." % (roots, what))
    pytest.skip("no mods/ or dev/ root in this tree, so there is nothing to check")



MOD_LUA, _ROOTS = _find_mod_lua("*/ui/*live_query.lua")

HARNESS = r"""
local src, pre = ...
_G.log = {}
_G.sched_reads = {}     -- every Schedule_Read call
_G.writes = {}          -- every Schedule_Write call
_G.deferred = {}        -- every Helper.addDelayedOneTimeCallbackOnUpdate call
_G.now = 100.0

function DebugError(s) _G.log[#_G.log+1] = tostring(s) end
function getElapsedTime() return _G.now end
-- A SEPARATE clock, so a test can tell which one the code read. nil real_now follows
-- `now`, which keeps every older test's single-clock model intact.
function GetCurRealTime() if _G.real_now ~= nil then return _G.real_now end return _G.now end

Helper = {
    addDelayedOneTimeCallbackOnUpdate = function(cb, blockinput, delaytime)
        _G.deferred[#_G.deferred+1] = {cb = cb, blockinput = blockinput, t = delaytime}
    end,
}

local fake_pipes = {
    Schedule_Read = function(name, cb, continuous)
        -- Injectable failure: the api can throw here, and until 2026-08-29 that
        -- killed the channel until the next reload.
        if _G.fail_reads then error("simulated Schedule_Read failure") end
        _G.sched_reads[#_G.sched_reads+1] = {name = name, cb = cb, continuous = continuous}
    end,
    Schedule_Write = function(name, cb, msg)
        -- Injectable failure: this is the only way a reply can fail to reach the
        -- wire, which is the case that must still leave a log line.
        if _G.fail_writes then error("simulated pipe write failure") end
        _G.writes[#_G.writes+1] = {name = name, msg = msg}
    end,
}

-- A fake FFI, so the ffi-only verbs (player/stations/ships) are testable offline.
-- Deliberately thin: it records rather than emulates, because the questions here are
-- about the GUARDS -- allowlist, validity, the payload cap, the denominator -- not
-- about LuaJIT's semantics.
_G.cdefs = {}
_G.fake_ids = {}          -- what ffi.new("UniverseID[?]", n) hands back
local fake_ffi = {
    cdef = function(src)
        -- Injectable failure for ONE declaration. `cdef_raises_on` names a symbol, and a
        -- cdef block containing it raises the way a real redefinition error would. That
        -- is how the pause verbs' own guarded declaration is shown to fail ALONE, without
        -- taking every ffi read verb down with it.
        if _G.cdef_raises_on and tostring(src):find(_G.cdef_raises_on, 1, true) then
            error("simulated ffi.cdef failure on " .. _G.cdef_raises_on)
        end
        _G.cdefs[#_G.cdefs+1] = src
    end,
    string = function(p) return tostring(p) end,
    new = function(spec, n)
        local buf = {}
        local ischar = tostring(spec):find("char") ~= nil
        for i = 0, (n or 0) - 1 do
            if ischar then
                -- const char*[?] -- the faction-id buffer, NOT an id buffer. Returning
                -- ULL strings here would make `objects` enumerate factions named
                -- "100000ULL" and quietly find nothing.
                buf[i] = (_G.fake_factions and _G.fake_factions[i+1]) or ("fac" .. tostring(i))
            else
                -- ffi buffers carry UniverseID cdata, which stringifies with a ULL
                -- suffix. Modelled as that string: it is what `wire` and rows see.
                buf[i] = _G.fake_ids[i+1] or (tostring(100000 + i) .. "ULL")
            end
        end
        return buf
    end,
    -- `wire` casts a lua number back to the canonical UniverseID rendering. Returning
    -- the finished string is exactly what tostring() of the real cdata would give.
    cast = function(ctype, v)
        -- A number is used as is: under LuaJIT tostring() keeps only 14 significant
        -- digits ("9.007199254741e+15"), which would fake a precision loss real ffi.cast
        -- does not have.
        local n = type(v) == "number" and v or tonumber(tostring(v))
        if n == nil then return tostring(v) end
        return string.format("%.0f", n) .. "ULL"
    end,
    -- Indexing a symbol the engine does not export RAISES in real ffi rather than
    -- returning nil. Mirrored, because the probe's pcall depends on it.
    C = setmetatable({}, {__index = function(_, k)
        -- Injectable error TEXT per symbol, for the census verb: real LuaJIT raises a
        -- different message for an undeclared symbol than for an unexported one, and the
        -- verb classifies on that text.
        if _G.fake_C_errors and _G.fake_C_errors[k] then error(_G.fake_C_errors[k], 0) end
        local f = _G.fake_C and _G.fake_C[k]
        if f == nil then error("C." .. tostring(k) .. " is not exported") end
        return f
    end}),
}

function require(path)
    if path == "ffi" then
        if _G.no_ffi then error("ffi unavailable") end
        return fake_ffi
    end
    return fake_pipes
end
function Register_OnLoad_Init(fn, path) _G.captured_init = fn end

-- The engine's ui event registry. md/lua_loader.xml raises
-- "Lua_Loader.Send_Priority_Ready" from a Reload_Listener whose <check_any> covers
-- BOTH <event_game_loaded/> and <event_game_started/>, so this is the hook a mod uses
-- to notice a save load. `__fire_game_load` lets a test raise it.
_G.ui_events = {}
function RegisterEvent(name, cb)
    _G.ui_events[name] = _G.ui_events[name] or {}
    table.insert(_G.ui_events[name], cb)
end
function _G.__fire_game_load()
    local subs = _G.ui_events["Lua_Loader.Send_Priority_Ready"] or {}
    for _, cb in ipairs(subs) do cb() end
    return #subs
end

-- Engine globals the live-view verbs call BARE (measured in game 2026-08-29).
--
-- ⚠ THESE MODEL TWO ID REPRESENTATIONS, because that distinction is now load-bearing.
-- MEASURED in game 2026-08-30: the ffi entry points yield `uint64` cdata that
-- stringifies as `1046331ULL`, while the BARE globals yield plain lua NUMBERS that
-- stringify as `282463` -- same id to the engine, two strings on our side. `wire`
-- exists to collapse them, so a fake that returned one shape for everything could not
-- test the thing the code is for.
--
-- A cdata UniverseID is modelled as the STRING "<n>ULL": lupa cannot produce a real
-- cdata, and `wire` deliberately keys on what a value renders as rather than on its
-- lua type, so this models it exactly where it matters.
local function to_number(s)
    s = tostring(s)
    local ull = s:match("^(%d+)ULL$")
    if ull then return tonumber(ull) end
    local luaid = s:match("^ID:%s*(%d+)$")
    if luaid then return tonumber(luaid) end
    return tonumber(s)
end
function ConvertStringTo64Bit(s) return to_number(s) end
function ConvertIDTo64Bit(s) return to_number(s) end
function ConvertStringToLuaID(s) return "luaid:" .. tostring(s) end
function IsValidComponent(id)
    if _G.invalid_ids and _G.invalid_ids[tostring(id)] then return false end
    return true
end
function GetComponentData(id, ...)
    local names = {...}
    local out = {}
    -- ⚠ THE ENGINE DOES NOT ACCEPT A RAW UniverseID cdata HERE, and it does not raise
    -- on one -- it just returns nothing about any object. MEASURED in game: the --wide
    -- path passed `buf[i]` straight through and got 1539 of 1539 unclassified, with
    -- zero read failures. Vanilla always converts first. Modelled by returning nils for
    -- a ULL-shaped id, so a caller that forgets the conversion FAILS a test instead of
    -- being discovered in game.
    if tostring(id):match("^%d+ULL$") then
        local up0 = table.unpack or unpack
        return up0({}, 1, #names)
    end
    for i = 1, #names do
        -- ⚠ NOT `planted or fallback`. A planted value of FALSE is falsy, so `or` would
        -- hand back the fallback STRING -- which is truthy -- and every boolean flag a
        -- test set to false would read as true. It did exactly that: `isObjectValid`
        -- accepted a wreck and an asteroid, and the flag column rendered every letter.
        local v
        if _G.component_data ~= nil then v = _G.component_data[names[i]] end
        if v == nil then v = names[i] .. "-of-" .. tostring(id) end
        -- `nil_fields` models a field the engine has NOTHING for. It cannot be
        -- expressed through component_data, because a nil there is indistinguishable
        -- from "not planted" and falls through to the fallback string. MEASURED in
        -- game: a nil classid makes Helper.isComponentClass raise, and that killed
        -- both compare and objects --wide.
        if _G.nil_fields ~= nil and _G.nil_fields[names[i]] then v = nil end
        out[i] = v
    end
    -- ⚠ NOT `table.unpack and table.unpack(out) or unpack(out)`. An and/or expression
    -- ADJUSTS ITS RESULT TO ONE VALUE, so that form silently returns only the first
    -- field -- and the real GetComponentData is variadic multi-return. It made the
    -- sector-filter test report 0 rows against code that was correct.
    -- ⚠ EXPLICIT BOUNDS. A nil_fields entry leaves a HOLE in `out`, and unpack stops
    -- at a hole -- so every field after the nil would silently vanish and the caller
    -- would map values to the WRONG NAMES. Passing 1, #names forces the real length.
    local up = table.unpack or unpack
    return up(out, 1, #names)
end

-- Container enumeration and class discrimination, for the `objects` design. Thin and
-- table-driven for the same reason as the rest of this fake: the questions offline are
-- about the GUARDS -- does the probe refuse cleanly, does it report BOTH container
-- forms separately, does it walk the list it was given -- not about engine semantics,
-- which only the running game can answer.
--
-- ⚠ These are deliberately NOT faithful. ConvertStringTo64Bit prefixes, so a wire-form
-- round trip does NOT come back identical here. That is correct: a test asserting the
-- round trip is IDENTICAL offline would be asserting a property of this file, not of
-- the engine, and would go green while the real answer stayed unknown.
_G.contained = nil          -- {["owner|container"] = {obj, ...}}
_G.contained_stations = nil -- {[owner] = {obj, ...}}
_G.class_by_field = nil     -- {["classid-of-A"] = "ship", ...}  (Helper form)
_G.obj_class = nil          -- {[obj] = "ship"}                  (bare form)

function GetContainedObjectsByOwner(owner, container)
    if _G.contained == nil then return {} end
    local key = tostring(owner) .. "|" .. tostring(container)
    return _G.contained[key] or _G.contained[tostring(owner)] or {}
end
function GetContainedStationsByOwner(owner, container)
    if _G.contained_stations == nil then return {} end
    return _G.contained_stations[tostring(owner)] or {}
end
function IsComponentClass(obj, cls)
    return (_G.obj_class and _G.obj_class[tostring(obj)]) == cls
end
-- Default is plain string equality, so a test can say `component_data = {classid =
-- "station"}` and mean it. `class_by_field` overrides that when a test needs DIFFERENT
-- classes per object, which is the only case the simple form cannot express.
Helper.isComponentClass = function(classid, cls)
    if _G.class_by_field ~= nil then
        return _G.class_by_field[tostring(classid)] == cls
    end
    return tostring(classid) == tostring(cls)
end

-- Anything that must be true BEFORE the mod's chunk runs goes here. The mod requires
-- ffi at LOAD time, so a flag set afterwards arrives too late -- which is exactly how
-- the no-ffi test first "passed" against a runtime that had a working ffi all along.
if pre ~= nil and pre ~= "" then
    local pchunk, perr = load(pre)
    if pchunk == nil then error("pre-chunk did not compile: " .. tostring(perr)) end
    pchunk()
end
-- A pre-chunk may extend the fake pipes module (e.g. pipes.lua's Connect_Pipe and its
-- `pipes` state table, which the F124 unref guard wraps at load time).
if type(_G.fake_pipe_module) == "table" then
    for k, v in pairs(_G.fake_pipe_module) do fake_pipes[k] = v end
end

-- A full UI RELOAD, as the engine performs it: every registered ui lua file is
-- RE-EXECUTED in the same environment. MEASURED in game 2026-08-29 with controls both
-- ways -- pressing ALT-ENTER causes one (2 presses, 2 reloads) and so does LOADING A
-- SAVE, while alt-TAB does not (75.7 s foregrounded, and 1190 s in an earlier session,
-- zero). Re-running the same source is exactly what the engine does, so this models
-- the one thing that matters here: what a SECOND incarnation inherits from the first.
_G.__src = src
function _G.__reload()
    local c2, e2 = load(_G.__src)
    if c2 == nil then error("reload did not compile: " .. tostring(e2)) end
    c2()
    return true
end

local chunk, err = load(src)
if chunk == nil then error("lua did not compile: " .. tostring(err)) end
chunk()
return true
"""


def _build(extra: str = "", pre: str = ""):
    """Load the mod, optionally with EXTRA lua appended to the same chunk.

    Appending is sound because the mod is a single chunk with no top-level `return`
    (asserted below), so appended code shares its lexical scope and can see the
    file-local `verbs` and `reply` tables. That is the only way to install a verb
    that behaves the way a FUTURE verb will.

    It matters because every verb shipped today pcalls internally and therefore
    CANNOT reach the dispatch error path at all. A guard that no current caller can
    reach is exactly the kind that rots untested and is found broken later by the
    first verb that does reach it.
    """
    _require_mod_lua(MOD_LUA, _ROOTS, "game-side live-query lua")
    src = MOD_LUA.read_text(encoding="utf-8")
    assert not any(ln.startswith("return") for ln in src.splitlines()), (
        "the mod grew a top-level `return`; appended test verbs would be unreachable "
        "and every test using _build(extra) would silently test nothing")
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.eval("function(...) " + HARNESS + " end")(src + "\n" + extra + "\n", pre)
    return rt


@pytest.fixture
def lua():
    return _build()


@pytest.fixture
def lua_factory():
    return _build


#: A verb that answers and THEN fails. No shipped verb can do this today; the live
#: view's verbs will be complex enough to.
REPLY_THEN_RAISE = """
verbs.__t_reply_then_raise = function(seq)
    reply(seq, "OK", "first")
    error("boom AFTER replying")
end
"""

#: A verb that fails before answering. The frame must still be produced.
RAISE_ONLY = """
verbs.__t_raise_only = function(seq)
    error("boom BEFORE replying")
end
"""


def g(rt, name):
    return rt.globals()[name]


def init(rt):
    g(rt, "captured_init")()


def deliver(rt, msg):
    """Hand *msg* to the callback the mod registered, exactly as pipes.lua would."""
    cb = g(rt, "sched_reads")[len(g(rt, "sched_reads"))]["cb"]
    cb(msg)


# --------------------------------------------------------------------------- #
# it loads and arms
# --------------------------------------------------------------------------- #

def test_the_mod_loads_and_arms_a_continuous_read(lua):
    init(lua)
    reads = g(lua, "sched_reads")
    assert len(reads) == 1
    assert reads[1]["name"] == "x4live"
    assert reads[1]["continuous"] is True
    assert any("_LIVE loaded" in s for s in g(lua, "log").values())


def test_the_load_marker_is_emitted_BEFORE_any_pipe_work(lua):
    """PROVE IT RAN BEFORE DEBUGGING WHAT IT DID. If the marker came after the pipe
    logic, a failure in that logic would look identical to the mod not loading — which
    is precisely the confusion that cost three attempts on 2026-08-29."""
    init(lua)
    logs = list(g(lua, "log").values())
    assert "_LIVE loaded" in logs[0]


@pytest.mark.parametrize("setup,expected", [
    ("function GetUISafeModeOption() return true end", "true"),
    ("function GetUISafeModeOption() return false end", "false"),
    ("GetUISafeModeOption = nil", "unknown"),
    ("function GetUISafeModeOption() error('unavailable') end", "unknown"),
    ("function GetUISafeModeOption() return 1 end", "unknown"),
    ("function GetUISafeModeOption() return 'false' end", "unknown"),
])
def test_first_load_marker_reads_mode_without_changing_it(lua_factory, setup, expected):
    # Review r5-M1: a raising stub alone cannot go red -- a pcall around a write would
    # swallow it. Count the calls instead, and require zero.
    rt = lua_factory(pre=setup + "\n_G.safe_mode_writes = 0\n"
                     "function SetUISafeModeOption(...) _G.safe_mode_writes = _G.safe_mode_writes + 1; "
                     "error('must not write') end")
    init(rt)
    logs = list(g(rt, "log").values())
    assert "_LIVE loaded" in logs[0]
    assert "ui_safe_mode=" + expected in logs[0]
    assert len(g(rt, "sched_reads")) == 1
    assert g(rt, "safe_mode_writes") == 0


# --------------------------------------------------------------------------- #
# THE HANG GUARD -- the reason this file exists
# --------------------------------------------------------------------------- #

def test_an_ERROR_sentinel_does_NOT_re_arm_SYNCHRONOUSLY(lua):
    """The whole point. A synchronous re-arm writes into the FIFO `Close_Pipe` is
    draining, and the game hangs on the UI thread.

    Note what this asserts: not "it re-armed", but "it did NOT re-arm *yet*". A test
    asserting only the former passes on the hanging version.
    """
    init(lua)
    before = len(g(lua, "sched_reads"))
    deliver(lua, "ERROR")
    assert len(g(lua, "sched_reads")) == before, (
        "re-armed synchronously from inside the disconnect callback -- this hangs X4")


def test_an_ERROR_sentinel_DEFERS_the_re_arm_to_the_next_update(lua):
    init(lua)
    deliver(lua, "ERROR")
    d = g(lua, "deferred")
    assert len(d) == 1, "the re-arm was not scheduled at all -- channel dies silently"


def test_the_deferred_re_arm_does_NOT_block_player_input(lua):
    """`blockinput=true` calls `C.SetAllUIInputIgnored(true)` and freezes the player's
    controls until the callback fires. Pinned because it is one positional argument
    away, and the failure is invisible in code review."""
    init(lua)
    deliver(lua, "ERROR")
    assert g(lua, "deferred")[1]["blockinput"] is False


def test_the_deferred_time_is_ABSOLUTE_and_in_the_FUTURE(lua):
    """`delaytime` is compared against `getElapsedTime()`, not treated as a duration.
    Passing a bare `2` would schedule it in the past and fire immediately -- which
    reintroduces the synchronous re-arm by another route."""
    init(lua)
    lua.globals()["now"] = 500.0
    deliver(lua, "ERROR")
    assert g(lua, "deferred")[1]["t"] > 500.0


def test_running_the_deferred_callback_DOES_re_arm(lua):
    """The other half. Deferring is only correct if the deferred thing actually arms."""
    init(lua)
    before = len(g(lua, "sched_reads"))
    deliver(lua, "ERROR")
    g(lua, "deferred")[1]["cb"]()
    assert len(g(lua, "sched_reads")) == before + 1


def test_a_disconnect_is_survivable_REPEATEDLY(lua):
    """Three disconnect/recover cycles. A fix that recovers once and then wedges would
    pass every single-cycle test above, and would present in-game as "it worked for a
    while" -- the hardest kind of report to act on."""
    init(lua)
    for cycle in range(3):
        deliver(lua, "ERROR")
        d = g(lua, "deferred")
        d[len(d)]["cb"]()
        assert len(g(lua, "sched_reads")) == cycle + 2, f"stopped recovering at cycle {cycle}"


# --------------------------------------------------------------------------- #
# the throttle -- debug.txt is x4debug's INPUT, so spam corrupts another instrument
# --------------------------------------------------------------------------- #

def test_repeated_disconnects_do_not_flood_the_debug_log(lua):
    """With no server listening the cycle is re-arm -> ERROR -> re-arm forever. The
    delay throttles the RATE; this throttles the LOG. Unbounded, it would add lines to
    debug.txt indefinitely -- and debug.txt is what `x4debug triage` reads."""
    init(lua)
    for _ in range(50):
        deliver(lua, "ERROR")
        d = g(lua, "deferred")
        d[len(d)]["cb"]()
    sentinel_lines = _pipe_lines(lua)
    assert len(sentinel_lines) <= 5, f"{len(sentinel_lines)} sentinel lines from 50 disconnects"


def test_the_throttle_reopens_after_a_long_quiet_period(lua):
    """The falsification twin for the throttle: it must suppress, not silence. A
    channel that stops reporting disconnects forever is a channel that cannot tell you
    it is broken."""
    init(lua)
    for _ in range(10):
        deliver(lua, "ERROR")
    quiet = len(_pipe_lines(lua))
    lua.globals()["now"] = g(lua, "now") + 600.0
    deliver(lua, "ERROR")
    after = len(_pipe_lines(lua))
    assert after > quiet, "the throttle silenced disconnect reporting permanently"


# --------------------------------------------------------------------------- #
# idle vs fault, and the idle back-off (2026-11-14 crash follow-up, BLIND-SPOTS F124)
# --------------------------------------------------------------------------- #

def _pipe_lines(rt):
    """Every pipe-state line the sentinel branch writes: idle, closed, or fault."""
    return [s for s in g(rt, "log").values() if "X4TOOLKIT_LIVE pipe " in s]


def _last_delay(rt):
    """The delay the most recent deferred re-arm was scheduled with (t is absolute)."""
    d = g(rt, "deferred")
    return d[len(d)]["t"] - g(rt, "now")


def _cycle_idle(rt):
    """One idle cycle: the arm is destroyed with no client, then the re-arm fires."""
    deliver(rt, "ERROR")
    d = g(rt, "deferred")
    d[len(d)]["cb"]()


#: A verb during which the pipe is torn down: Schedule_Write's synchronous poll fails,
#: Close_Pipe drains the read FIFO, and our callback gets "ERROR" while the request is
#: still being handled. pipes.lua does exactly this; the stub calls the callback itself.
TEARDOWN_MID_REQUEST = """
verbs.__t_teardown_mid_request = function(seq)
    local r = _G.sched_reads[#_G.sched_reads]
    r.cb("ERROR")
    reply(seq, "OK", "after")
end
verbs.__t_quiet_request = function(seq)
    reply(seq, "OK", "fine")
end
"""


def test_an_IDLE_teardown_is_logged_as_idle_not_as_an_ERROR(lua):
    """No client ever spoke on this arm, so the pipe simply was not there: expected."""
    init(lua)
    deliver(lua, "ERROR")
    lines = _pipe_lines(lua)
    assert lines and "pipe idle: no client" in lines[-1], lines
    assert "ERROR" not in lines[-1], lines[-1]


def test_a_teardown_AFTER_a_served_request_is_a_client_disconnect(lua_factory):
    """Twin of the idle test: one request was served on this arm, so the ERROR is the
    client hanging up -- every x4live call ends this way. Not idle, and not a fault."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    deliver(rt, "MQ\t1\t1\t__t_quiet_request")
    deliver(rt, "ERROR")
    last = _pipe_lines(rt)[-1]
    assert "client disconnected after 1 request" in last, last
    assert "idle" not in last and "ERROR" not in last, last


def test_a_teardown_MID_REQUEST_keeps_a_real_ERROR(lua_factory):
    """The genuine fault: the arm was destroyed while a request was being handled."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    deliver(rt, "MQ\t1\t1\t__t_teardown_mid_request")
    hits = [s for s in _pipe_lines(rt) if "MID-REQUEST" in s]
    assert len(hits) == 1, _pipe_lines(rt)
    assert "ERROR" in hits[0], hits[0]


def test_a_MID_REQUEST_fault_is_not_hidden_by_the_idle_throttle(lua_factory):
    """Faults are bounded by client requests, so they bypass the idle throttle: a burst
    of idle lines must not be able to swallow the one line that matters."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    for _ in range(10):
        _cycle_idle(rt)
    deliver(rt, "MQ\t1\t2\t__t_teardown_mid_request")
    assert any("MID-REQUEST" in s for s in _pipe_lines(rt)), _pipe_lines(rt)


def test_IDLE_re_arms_BACK_OFF_to_a_cap(lua):
    """2 s, then doubling, capped -- the churn of re-arm/Close_Pipe falls by ~5x."""
    init(lua)
    delays = []
    for _ in range(7):
        deliver(lua, "ERROR")
        delays.append(round(_last_delay(lua), 3))
        d = g(lua, "deferred")
        d[len(d)]["cb"]()
    # The first ERROR is the one Init's own arm meets (no client yet).
    assert delays == [2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0], delays


def test_a_REQUEST_resets_the_back_off_to_fast(lua_factory):
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    for _ in range(5):
        _cycle_idle(rt)
    assert _last_delay(rt) == 10.0
    deliver(rt, "MQ\t1\t1\t__t_quiet_request")
    deliver(rt, "ERROR")
    assert _last_delay(rt) == 2.0, "a client spoke, so the next re-arm must be fast"
    # ...and idleness after it backs off again FROM fast, counted per arm: 2 s, then 4 s.
    after = []
    for _ in range(2):
        d = g(rt, "deferred")
        d[len(d)]["cb"]()
        deliver(rt, "ERROR")
        after.append(_last_delay(rt))
    assert after == [2.0, 4.0], after


def test_WITHOUT_a_request_the_back_off_stays_slow(lua):
    """Twin of the reset test: the reset must come from the request, not from time."""
    init(lua)
    for _ in range(5):
        _cycle_idle(lua)
    lua.globals()["now"] = g(lua, "now") + 600.0
    deliver(lua, "ERROR")
    assert _last_delay(lua) == 10.0


def test_a_FAILED_arm_retries_at_the_FAST_delay(lua_factory):
    """A Schedule_Read failure is a fault, not idleness: it must not inherit a back-off."""
    rt = lua_factory()
    init(rt)
    for _ in range(5):
        _cycle_idle(rt)
    rt.execute("_G.fail_reads = true")
    d = g(rt, "deferred")
    d[len(d)]["cb"]()
    assert any("Schedule_Read failed" in s for s in g(rt, "log").values())
    assert _last_delay(rt) == 2.0
    # ...and once an arm succeeds again, idleness backs off from FAST, not from 10 s.
    rt.execute("_G.fail_reads = false")
    d = g(rt, "deferred")
    d[len(d)]["cb"]()
    deliver(rt, "ERROR")
    assert _last_delay(rt) == 2.0


def test_two_idle_lines_need_60s_of_REAL_time_between_them(lua):
    """The 2026-11-14 'two lines in one frame': debug.txt stamps GAME time, which stands
    still while the game is paused, and this throttle runs on getElapsedTime (real time,
    MEASURED to advance through a pause). Two ERRORs at the same real instant after the
    free lines are spent yield at most ONE line."""
    init(lua)
    for _ in range(4):
        lua.globals()["now"] = g(lua, "now") + 100.0
        _cycle_idle(lua)
    before = len(_pipe_lines(lua))
    lua.globals()["now"] = g(lua, "now") + 100.0
    deliver(lua, "ERROR")
    deliver(lua, "ERROR")
    assert len(_pipe_lines(lua)) - before == 1


def test_two_idle_lines_60s_of_real_time_apart_are_BOTH_logged(lua):
    """Twin: the same two ERRORs, 61 s of real time apart, both log -- which is all the
    11-14 log showed (one stamp, because the game clock was frozen)."""
    init(lua)
    for _ in range(4):
        lua.globals()["now"] = g(lua, "now") + 100.0
        _cycle_idle(lua)
    before = len(_pipe_lines(lua))
    lua.globals()["now"] = g(lua, "now") + 100.0
    deliver(lua, "ERROR")
    lua.globals()["now"] = g(lua, "now") + 61.0
    deliver(lua, "ERROR")
    assert len(_pipe_lines(lua)) - before == 2


def test_every_pipe_line_carries_the_REAL_clock(lua):
    """So a future log can tell 'two lines in one frame' from 'two lines in one paused
    game second': the line states getElapsedTime, not just the log's game-time stamp."""
    init(lua)
    lua.globals()["now"] = 1234.5
    deliver(lua, "ERROR")
    assert "t=1234.50" in _pipe_lines(lua)[-1], _pipe_lines(lua)[-1]


def test_the_t_field_is_GetCurRealTime_not_getElapsedTime(lua):
    """getElapsedTime's C body dereferences the UI host in registry slot 1 with no NULL
    check -- the 11-14 crash site. GetCurRealTime reads a process-global clock. The
    sentinel's own timing uses the latter."""
    init(lua)
    lua.globals()["now"] = 100.0
    lua.globals()["real_now"] = 7777.0
    deliver(lua, "ERROR")
    last = _pipe_lines(lua)[-1]
    assert "t=7777.00" in last and "t=100.00" not in last, last


def test_the_THROTTLE_runs_on_GetCurRealTime(lua):
    """Twin, for the throttle: the real clock advancing 61 s reopens it while
    getElapsedTime stands still."""
    init(lua)
    lua.globals()["real_now"] = 0.0
    for i in range(4):
        lua.globals()["real_now"] = 100.0 * (i + 1)
        _cycle_idle(lua)
    before = len(_pipe_lines(lua))
    deliver(lua, "ERROR")                     # same real instant: suppressed
    lua.globals()["real_now"] = g(lua, "real_now") + 61.0
    deliver(lua, "ERROR")                     # 61 real s later: logged
    assert len(_pipe_lines(lua)) - before == 1


def test_WITHOUT_GetCurRealTime_it_falls_back_to_getElapsedTime(lua):
    init(lua)
    lua.execute("GetCurRealTime = nil")
    lua.globals()["now"] = 4321.0
    deliver(lua, "ERROR")
    assert "t=4321.00" in _pipe_lines(lua)[-1]


def _served_call(rt, seq):
    deliver(rt, "MQ\t1\t%d\t__t_quiet_request" % seq)
    deliver(rt, "ERROR")
    d = g(rt, "deferred")
    d[len(d)]["cb"]()


def test_EVERY_call_end_is_logged_even_after_the_idle_lines_spent_the_budget(lua_factory):
    """Disconnect lines have their own budget, so a tester sees each call end. Init's
    idle 2/4/8 s lines spend the idle budget's free lines first."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    for _ in range(5):
        _cycle_idle(rt)
    for seq in range(1, 4):
        rt.globals()["now"] = g(rt, "now") + 3.0
        _served_call(rt, seq)
    ends = [s for s in _pipe_lines(rt) if "client disconnected" in s]
    assert len(ends) == 3, _pipe_lines(rt)


def test_disconnect_lines_are_still_RATE_limited(lua_factory):
    """Twin: a pathological burst of call ends at one real instant logs once."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    for seq in range(1, 6):
        _served_call(rt, seq)
    ends = [s for s in _pipe_lines(rt) if "client disconnected" in s]
    assert len(ends) == 1, ends


def test_disconnect_lines_do_not_spend_the_IDLE_budget(lua_factory):
    """The separation runs both ways: after call ends, the idle budget is untouched."""
    rt = lua_factory(TEARDOWN_MID_REQUEST)
    init(rt)
    for seq in range(1, 4):
        rt.globals()["now"] = g(rt, "now") + 3.0
        _served_call(rt, seq)
    for _ in range(3):
        _cycle_idle(rt)
    idle = [s for s in _pipe_lines(rt) if "pipe idle" in s]
    assert len(idle) == 3, _pipe_lines(rt)   # all three of the idle budget's free lines


# --------------------------------------------------------------------------- #
# refusal beats a hang
# --------------------------------------------------------------------------- #

def test_without_Helper_it_REFUSES_rather_than_re_arming_synchronously(lua):
    """If the deferral mechanism is missing we must NOT fall back to a synchronous
    re-arm. A channel that needs a reload is a nuisance; a hung game is a force-kill,
    and the message has to say which state we are in."""
    init(lua)
    lua.execute("Helper = nil")
    before = len(g(lua, "sched_reads"))
    deliver(lua, "ERROR")
    assert len(g(lua, "sched_reads")) == before, "re-armed synchronously without Helper"
    assert any("cannot re-arm" in s for s in g(lua, "log").values())


# --------------------------------------------------------------------------- #
# the reply framing must agree with the Python decoder
# --------------------------------------------------------------------------- #

def test_a_ping_produces_a_frame_the_python_side_accepts(lua):
    """Cross-checks the two halves of the protocol against each other in one process.
    Every other test of the frame contract feeds Python a frame PYTHON built."""
    from x4validate import _livepipe as lp

    init(lua)
    deliver(lua, "MQ\t1\t7\tping")
    w = g(lua, "writes")
    assert len(w) == 1, "the mod did not reply to a well-formed ping"
    reply = lp.decode_reply(w[1]["msg"], 7)
    assert reply.status == "OK"
    assert reply.fields[0] == "pong"


def test_an_unknown_verb_is_answered_not_ignored(lua):
    """Silence is indistinguishable from a hung game, so every command must produce
    a frame -- including a bad one."""
    from x4validate import _livepipe as lp

    init(lua)
    deliver(lua, "MQ\t1\t3\t__no_such_verb__")
    reply = lp.decode_reply(g(lua, "writes")[1]["msg"], 3)
    assert reply.status == "ERR"


# --------------------------------------------------------------------------- #
# EXACTLY ONE FRAME PER QUESTION -- the double-frame desync
# --------------------------------------------------------------------------- #

def test_a_verb_that_raises_WITHOUT_replying_still_produces_a_frame(lua_factory):
    """Silence on the wire is indistinguishable from a hung game, so a failed verb
    must still answer. This is the twin of the test below: together they pin that the
    guard suppresses a DUPLICATE frame and never the ONLY frame."""
    from x4validate import _livepipe as lp

    rt = _build(RAISE_ONLY)
    init(rt)
    deliver(rt, "MQ\t1\t5\t__t_raise_only")
    w = g(rt, "writes")
    assert len(w) == 1, f"{len(w)} frames; a raising verb must answer exactly once"
    assert lp.decode_reply(w[1]["msg"], 5).status == "ERR"


def test_a_verb_that_replies_THEN_raises_produces_exactly_ONE_frame(lua_factory):
    """The bug this guard exists for. Python reads one message per ask(), so a second
    frame stays queued and becomes the answer to the NEXT question -- tripping the
    sequence check one query AFTER the real fault, with nothing pointing back at it."""
    from x4validate import _livepipe as lp

    rt = _build(REPLY_THEN_RAISE)
    init(rt)
    deliver(rt, "MQ\t1\t7\t__t_reply_then_raise")
    w = g(rt, "writes")
    assert len(w) == 1, f"{len(w)} frames for one seq -- the next query will desync"
    r = lp.decode_reply(w[1]["msg"], 7)
    assert r.status == "OK" and r.payload == "first", (
        "the surviving frame must be the verb's real answer, not the error")


def test_the_post_reply_raise_is_still_LOGGED(lua_factory):
    """Suppressing the second FRAME must not suppress the second FACT. A verb that
    raised after answering is a real defect and has to leave a trace somewhere."""
    rt = _build(REPLY_THEN_RAISE)
    init(rt)
    deliver(rt, "MQ\t1\t7\t__t_reply_then_raise")
    assert any("raised AFTER replying" in s for s in g(rt, "log").values())


def test_a_post_reply_raise_does_not_poison_the_NEXT_query(lua_factory):
    """The consequence test, which is what actually hurt: under the old behaviour a
    stale seq-7 frame sat in the queue and became the answer to seq 8."""
    from x4validate import _livepipe as lp

    rt = _build(REPLY_THEN_RAISE)
    init(rt)
    deliver(rt, "MQ\t1\t7\t__t_reply_then_raise")
    deliver(rt, "MQ\t1\t8\tping")
    w = g(rt, "writes")
    assert len(w) == 2, f"expected one frame per question, got {len(w)}"
    assert lp.decode_reply(w[2]["msg"], 8).status == "OK"


def test_a_reply_that_CANNOT_be_sent_is_LOGGED_not_swallowed(lua_factory):
    """If even the error reply fails there is total silence on the wire, which reads
    as a hung game. The log line is then the only evidence -- and it was previously
    swallowed by a bare pcall, i.e. the exact failure its own comment claimed to
    prevent, one layer down."""
    rt = _build(RAISE_ONLY)
    init(rt)
    rt.execute("_G.fail_writes = true")
    deliver(rt, "MQ\t1\t9\t__t_raise_only")
    # len(), NOT `not ...values()`. A LuaIter is an object with no __bool__, so it is
    # truthy even when the table is empty -- that assertion could never have passed,
    # and it failed against code that was correct. The instrument, again.
    assert len(g(rt, "writes")) == 0, "the write was supposed to fail"
    assert any("could not reply to seq" in s for s in g(rt, "log").values())


# --------------------------------------------------------------------------- #
# a failed ARM must retry, and a missing CLOCK must refuse
# --------------------------------------------------------------------------- #

def test_a_FAILED_arm_schedules_a_RETRY_instead_of_dying(lua_factory):
    """Until 2026-08-29 one failed Schedule_Read killed the channel until the next
    reload, with a single log line as the only symptom."""
    rt = _build()
    rt.execute("_G.fail_reads = true")
    init(rt)
    assert any("Schedule_Read failed" in s for s in g(rt, "log").values())
    assert len(g(rt, "deferred")) == 1, "a failed arm did not schedule a retry"


def test_without_a_CLOCK_it_REFUSES_rather_than_losing_the_throttle(lua_factory):
    """An absolute deadline built from now = 0 is already in the past, so the callback
    fires on the NEXT FRAME and the 2s throttle silently disappears -- turning the
    no-server cycle into ~60 re-arms a second, each able to log, into debug.txt.
    debug.txt is x4debug's INPUT, so degrading quietly here corrupts an unrelated
    instrument. Refuse loudly instead."""
    rt = _build()
    init(rt)
    rt.execute("getElapsedTime = function() error('no clock') end")
    before = len(g(rt, "deferred"))
    deliver(rt, "ERROR")
    assert len(g(rt, "deferred")) == before, "scheduled an UNTHROTTLED re-arm"
    assert any("throttle cannot be honoured" in s for s in g(rt, "log").values())


def test_WITH_a_clock_it_still_schedules(lua_factory):
    """Falsification twin for the test above: the refusal must be caused by the
    MISSING CLOCK specifically, not by the sentinel path being broken outright."""
    rt = _build()
    init(rt)
    deliver(rt, "ERROR")
    assert len(g(rt, "deferred")) == 1


# --------------------------------------------------------------------------- #
# the capability probe
# --------------------------------------------------------------------------- #

def test_the_probe_reports_types_and_NEVER_invokes_a_symbol(lua_factory):
    """The probe's whole safety argument is that it only type()s. If it ever called
    one, a bad argument becomes reachable and the 'cannot crash' claim is void."""
    from x4validate import _livepipe as lp

    rt = _build()
    init(rt)
    rt.execute("_G.GetComponentData = function() _G.CALLED = true end")
    deliver(rt, "MQ\t1\t3\tprobe")
    r = lp.decode_reply(g(rt, "writes")[1]["msg"], 3)
    assert r.status == "OK"
    fields = dict(f.split("=", 1) for f in r.fields if "=" in f)
    assert fields["GetComponentData"] == "function"
    assert rt.globals()["CALLED"] is None, "the probe INVOKED a symbol"


def test_the_probe_reports_an_ABSENT_symbol_rather_than_omitting_it(lua_factory):
    """An absent symbol must come back as nil. Omitting it makes 'not reachable' and
    'we never asked' identical -- the narrowing-step defect, in the one instrument
    built to rule that out."""
    from x4validate import _livepipe as lp

    rt = _build()
    init(rt)
    deliver(rt, "MQ\t1\t4\tprobe")
    r = lp.decode_reply(g(rt, "writes")[1]["msg"], 4)
    names = [f.split("=", 1)[0] for f in r.fields if "=" in f]
    for required in ("GetComponentData", "GetNumAllFactionStations",
                     "GetObjectPositionInSector", "IsValidComponent"):
        assert required in names, f"probe never reported {required}"
    assert "=nil" in r.payload, (
        "nothing came back nil in a bare harness -- the probe is not actually looking")


def test_a_REUSED_seq_still_gets_its_own_frame(lua_factory):
    """The per-dispatch reset of the reply flag. `seq` comes from the CLIENT, so the
    mod must not assume it increments: if a caller reuses one, a stale flag from the
    previous query would suppress a legitimate error frame and the caller would wait
    out its timeout for an answer that was deliberately withheld."""
    from x4validate import _livepipe as lp

    rt = _build(REPLY_THEN_RAISE + RAISE_ONLY)
    init(rt)
    deliver(rt, "MQ\t1\t4\t__t_reply_then_raise")   # replies, sets the flag for seq 4
    deliver(rt, "MQ\t1\t4\t__t_raise_only")         # SAME seq, must still answer
    w = g(rt, "writes")
    assert len(w) == 2, f"the reused seq was silently dropped ({len(w)} frames)"
    assert lp.decode_reply(w[2]["msg"], 4).status == "ERR"


# --------------------------------------------------------------------------- #
# THE LIVE VIEW -- player / component / stations / ships
# --------------------------------------------------------------------------- #

FAKE_C = """
_G.fake_C = {
    -- ULL-suffixed, because these are ffi entry points returning UniverseID cdata.
    GetPlayerOccupiedShipID = function() return "1046331ULL" end,
    GetPlayerID             = function() return "77ULL" end,
    GetComponentClass       = function(id) return "ship_l" end,
    GetObjectPositionInSector = function(id)
        return {x = 1.5, y = 2.5, z = 3.5, yaw = 0.1, pitch = 0.2, roll = 0.3}
    end,
    GetNumAllFactionStations = function(f) return _G.n_stations or 0 end,
    GetAllFactionStations    = function(buf, n, f) return n end,
    GetNumAllFactionShips    = function(f) return _G.n_ships or 0 end,
    GetAllFactionShips       = function(buf, n, f) return n end,
    -- The faction list, for the cross-faction `objects` verb. Present here, but the
    -- fake ffi.C RAISES on a symbol this table lacks, so a test can delete either one
    -- to reach the "<not exported>" branch -- which is what keeps that branch a real
    -- one rather than decoration.
    -- `hidden` genuinely changes the count, so --hidden has an observable effect and a
    -- test asserting it cannot pass vacuously.
    GetNumAllFactions        = function(hidden)
        if hidden then return _G.n_factions_hidden or _G.n_factions or 0 end
        return _G.n_factions or 0
    end,
    GetAllFactions           = function(buf, n, hidden) return n end,
    -- Vanilla declares this exactly: `bool IsGamePaused(void);` (helptext.lua:9). Delete
    -- it in a test and the fake C RAISES, as real ffi does for an unexported symbol.
    IsGamePaused             = function() return _G.game_paused end,
}

-- PAUSE, as ONE engine state that all three entry points share. Separate fakes could
-- disagree, and a read-back that cannot contradict the write is a control that cannot
-- move. `*_is_noop` models an engine that ACCEPTS the call and changes nothing -- without
-- it the read-back-disagrees branch is unreachable, and an unreachable branch is one
-- nothing defends. `*_nargs` records the call shape: vanilla's argument forms
-- (`Pause(nil, true)`, `Unpause(true)`) are unexplained, so the mod must call BARE.
_G.game_paused = false
_G.pause_calls, _G.unpause_calls = 0, 0
_G.pause_raises, _G.unpause_raises = false, false
_G.pause_is_noop, _G.unpause_is_noop = false, false
function Pause(...)
    _G.pause_calls = _G.pause_calls + 1
    _G.pause_nargs = select("#", ...)
    if _G.pause_raises then error("engine refused to pause") end
    if not _G.pause_is_noop then _G.game_paused = true end
end
function Unpause(...)
    _G.unpause_calls = _G.unpause_calls + 1
    _G.unpause_nargs = select("#", ...)
    if _G.unpause_raises then error("engine refused to unpause") end
    if not _G.unpause_is_noop then _G.game_paused = false end
end
"""


def live(rt=None, **globals_):
    """A runtime with the fake engine wired up and `player` already run, so the
    allowlist is seeded the way it is in real use."""
    rt = rt or _build()
    rt.execute(FAKE_C)
    for k, v in globals_.items():
        rt.execute(f"_G.{k} = {v}")
    init(rt)
    return rt


def ask(rt, seq, *parts):
    from x4validate import _livepipe as lp

    deliver(rt, "MQ\t1\t%d\t%s" % (seq, "\t".join(parts)))
    w = g(rt, "writes")
    return lp.decode_reply(w[len(w)]["msg"], seq)


def reload_ui(rt):
    """Re-execute the mod's chunk and run the NEW chunk's Init, as a UI reload does.

    `deliver` always targets the most recent `Schedule_Read`, so after this the tests
    are talking to the new incarnation -- which is the engine's behaviour too.
    """
    g(rt, "__reload")()
    init(rt)


def probe_fields(rt, seq=99):
    r = ask(rt, seq, "probe")
    assert r.status == "OK", r.payload
    return dict(f.split("=", 1) for f in r.fields if "=" in f)


def test_component_REFUSES_an_id_this_session_never_issued(lua_factory):
    """The allowlist. MEASURED: 0 of 888 vanilla GetComponentData calls pass a foreign
    id, so the engine's behaviour on a fabricated one is unknown in BOTH directions --
    it may be harmless, it may crash the game. Never find out by accident."""
    rt = live()
    r = ask(rt, 1, "component", "999999999")
    assert r.status == "ERR"
    assert "not issued by this session" in r.payload


def test_player_SEEDS_the_allowlist_so_component_then_works(lua_factory):
    """The two halves are useless apart: without an issuer the allowlist is empty and
    `component` can never be used at all."""
    rt = live()
    p = ask(rt, 1, "player")
    assert p.status == "OK", p.payload
    ids = [f.split("=", 1)[1] for f in p.fields if f.startswith("occupiedship=")]
    assert ids, f"player did not report an occupied ship id: {p.payload}"
    r = ask(rt, 2, "component", ids[0])
    assert r.status == "OK", r.payload


def test_player_reports_position_via_the_ffi_STRUCT_not_a_data_field(lua_factory):
    """Position is not a GetComponentData field (zero such literals in vanilla); it
    comes back as a UIPosRot struct, which is why the cdef carries that typedef."""
    rt = live()
    p = ask(rt, 1, "player")
    assert any(f.startswith("pos=1.5,2.5,3.5") for f in p.fields), p.payload
    assert any(f.startswith("class=ship_l") for f in p.fields), p.payload


def test_a_STALE_component_is_ABSENT_not_ERR(lua_factory):
    """We asked and it is gone -- that is an ANSWER. Reporting it as ERR would make a
    destroyed ship indistinguishable from a malformed question."""
    rt = live()
    p = ask(rt, 1, "player")
    sid = [f.split("=", 1)[1] for f in p.fields if f.startswith("occupiedship=")][0]
    # ConvertStringTo64Bit now models the real bare global: it returns a NUMBER,
    # so the key IsValidComponent is looked up under is that number's string form.
    numeric = sid.replace("ULL", "")
    rt.execute('_G.invalid_ids = {["' + numeric + '"] = true}')
    r = ask(rt, 2, "component", sid)
    assert r.status == "ABSENT", f"{r.status}: {r.payload}"


def test_component_honours_a_CALLER_SUPPLIED_field_list(lua_factory):
    """The DevBench property: the vocabulary is the caller's, not something this mod
    has to predict. Also pins that dispatch forwards MORE than three arguments."""
    rt = live()
    p = ask(rt, 1, "player")
    sid = [f.split("=", 1)[1] for f in p.fields if f.startswith("occupiedship=")][0]
    r = ask(rt, 2, "component", sid, "hull", "shieldpercent", "owner", "macro")
    assert r.status == "OK", r.payload
    names = [f.split("=", 1)[0] for f in r.fields]
    assert names == ["hull", "shieldpercent", "owner", "macro"], names


def test_an_EMPTY_faction_list_is_OK_with_a_zero_count_never_ABSENT(lua_factory):
    """Asked, and the faction owns none. ABSENT would say 'no such question'."""
    rt = live(n_stations=0)
    r = ask(rt, 1, "stations", "xenon")
    assert r.status == "OK", r.payload
    assert "shown=0 matched=0 enumerated=0 total=0" in r.payload
    assert "CAPPED=no" in r.payload


def test_a_capped_list_REPORTS_ITS_OWN_DENOMINATOR(lua_factory):
    """A step that narrows data must announce it. GetNum* gives the true total for
    free, so there is no excuse for a silently truncated list."""
    rt = live(n_stations=4000, component_data='{classid = "station", realclassid = "station"}')
    r = ask(rt, 1, "stations", "argon", "--wide")
    assert r.status == "OK", r.payload[:200]
    header = r.fields[0]
    assert header.startswith("shown="), header
    shown = int(header.split("shown=")[1].split(" ")[0])
    assert "enumerated=4000" in header and "total=4000" in header, header
    assert shown < 4000, "nothing was capped, so this test proves nothing about capping"
    assert shown == len(r.fields) - 1, "header count disagrees with the rows actually sent"


def test_a_capped_list_STAYS_UNDER_the_payload_bound(lua_factory):
    """The bound is what stops an over-long reply, and an over-long reply does not
    truncate -- it tears the pipe down. Detection after the fact cannot help here."""
    from x4validate import _livepipe as lp

    rt = live(n_stations=4000, component_data='{classid = "station", realclassid = "station"}')
    r = ask(rt, 1, "stations", "argon", "--wide")
    # EXACT, no tolerance. This assertion used to read `<= 32000 + 200`, and that
    # 200-byte slack -- added for comfort, derived from nothing -- is precisely what
    # let a real 143-byte overrun through: the cap bounded the ROWS while the header
    # was prepended afterwards and never counted. MEASURED in game at 32143 bytes
    # against a "32000-byte" budget. A tolerance nobody derived is a place for
    # defects to live.
    assert lp.byte_len(r.payload) <= 32000, lp.byte_len(r.payload)


def test_the_cap_is_REACHABLE_so_the_bound_test_can_fail(lua_factory):
    """Falsification twin for the two above: with a small list nothing is capped, so
    a passing bound test on a small list would have proved nothing."""
    rt = live(n_stations=3, component_data='{classid = "station", realclassid = "station"}')
    r = ask(rt, 1, "stations", "argon", "--wide")
    assert "shown=3 matched=3 enumerated=3 total=3" in r.fields[0], r.fields[0]
    assert "CAPPED=no" in r.fields[0], "nothing was capped, so it must say so"


def test_enumeration_ISSUES_ids_so_they_can_then_be_inspected(lua_factory):
    rt = live(n_stations=2, component_data='{classid = "station", realclassid = "station"}')
    s = ask(rt, 1, "stations", "argon", "--wide")
    first_id = s.fields[1].split("|")[0]
    r = ask(rt, 2, "component", first_id)
    assert r.status == "OK", r.payload


def test_a_SECTOR_FILTER_narrows_the_list_and_says_so(lua_factory):
    """'What is around ME' is this, plus the sectorid that `player` hands out."""
    rt = live(n_stations=5)
    rt.execute('_G.component_data = {sectorid = "ID: 1", classid = "station", realclassid = "station"}')
    r = ask(rt, 1, "stations", "argon", "ID: 1", "--wide")
    assert "sector=ID:1" in r.fields[0], r.fields[0]
    assert len(r.fields) - 1 == 5, r.fields[0]
    r2 = ask(rt, 2, "stations", "argon", "ID: 2", "--wide")
    assert r2.status == "OK", r2.fields[0]
    assert "shown=0 matched=0 enumerated=5 total=5" in r2.fields[0], r2.fields[0]


def test_without_ffi_the_live_verbs_ERR_and_the_others_keep_working(lua_factory):
    """Graceful degradation. A missing cdef must not take the whole channel down --
    ping, macro and probe need no ffi at all."""
    # BEFORE the chunk loads. The mod requires ffi at LOAD time, so setting this
    # afterwards leaves it with a working ffi and the test passes vacuously -- which
    # is what it did on the first attempt, reporting OK where it demanded ERR.
    rt = _build(pre="_G.no_ffi = true")
    rt.execute(FAKE_C)
    init(rt)

    # Assert the MESSAGE, not just the status. Without the cdef guard these verbs
    # still fail -- `C` is nil, so the engine call raises and the dispatch pcall turns
    # it into an ERR anyway. So status alone cannot tell a deliberate refusal from an
    # accidental crash, and a mutation test proved exactly that: deleting the guard
    # left this test green. What the guard actually buys is a DIAGNOSABLE answer.
    p = ask(rt, 1, "player")
    assert p.status == "ERR"
    assert "ffi cdef block" in p.payload, f"undiagnosable failure: {p.payload}"

    s = ask(rt, 2, "stations", "argon")
    assert s.status == "ERR"
    assert "ffi cdef block" in s.payload, f"undiagnosable failure: {s.payload}"

    assert ask(rt, 3, "ping").status == "OK", "a non-ffi verb broke when ffi was absent"


def test_a_capped_FILTERED_list_separates_the_two_narrowings(lua_factory):
    """The defect the first in-game run exposed. TWO steps narrow the result here --
    the sector filter and the reply cap -- and the original header reported one number
    for both, so `shown=215 of 1785` could not distinguish "215 matched your filter"
    from "more matched and the cap ate some". A reader had no way to know rows were
    missing, which is the un-announced narrowing this workspace exists to refuse.

    Everything matches the filter here, so any shortfall is the cap and only the cap.
    """
    rt = live(n_stations=4000)
    rt.execute('_G.component_data = {sectorid = "ID: 1", classid = "station", realclassid = "station"}')
    r = ask(rt, 1, "stations", "argon", "ID: 1", "--wide")
    h = r.fields[0]

    matched = int(h.split("matched=")[1].split(" ")[0])
    shown = int(h.split("shown=")[1].split(" ")[0])
    omitted = int(h.split("omitted=")[1].split(" ")[0])

    assert matched == 4000, f"matched must count EVERY filter hit, capped or not: {h}"
    assert shown < matched, "nothing was capped, so this proves nothing about capping"
    assert "CAPPED=yes" in h, h
    assert omitted == matched - shown, f"omitted disagrees with shown/matched: {h}"
    assert shown == len(r.fields) - 1, "header count disagrees with the rows sent"


def test_an_UNCAPPED_list_says_CAPPED_no_explicitly(lua_factory):
    """Falsification twin. The flag must distinguish, not merely be present -- a
    header that always said CAPPED=yes would pass the test above."""
    rt = live(n_stations=3)
    h = ask(rt, 1, "stations", "argon").fields[0]
    assert "CAPPED=no" in h, h
    assert "omitted=" not in h, f"claimed omissions on an uncapped list: {h}"


# --------------------------------------------------------------------------- #
# the BUILD fingerprint -- "is the GAME running the file on disk?"
# --------------------------------------------------------------------------- #

def test_the_BUILD_constant_matches_the_file():
    """Keeps the stamp honest. Editing the lua and forgetting to re-stamp would make
    the probe report a build that no longer describes the code -- worse than no
    fingerprint, because it would be believed.

    This is the mechanised half of a lesson that cost two cycles in one session: a
    deployed file is not a loaded file, and a staleness check that keys on ONE new
    field only proves 'not the build before last'. A content hash cannot fail that
    way, because it moves for changes nobody enumerated.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "stamp", pathlib.Path(__file__).resolve().parents[1] / "scripts/stamp-mod-build.py")
    stamp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp)

    # EVERY stamped file, not just the primary one. `mod_lua()` returns
    # live_query.lua alone, so engine_probe.lua -- the other half of the addon, and
    # the file whose undetected change is the reason this stamp exists -- was checked
    # by nothing. MEASURED 2026-09-02: editing it without re-stamping survived all
    # 1208 tests.
    files = stamp.mod_lua_files()
    if not files:
        _require_mod_lua(None, _ROOTS, "game-side mod lua")
    assert len(files) >= 2, (
        f"expected both stamped lua files, found {[f.name for f in files]} -- if the "
        f"addon really is one file now, this assertion is the thing to update")
    for p in files:
        text = p.read_text(encoding="utf-8")
        cur, exp = stamp.current(text), stamp.expected(text)
        assert cur is not None, f"{p.name} lost its BUILD line"
        assert cur == exp, (
            f"{p.name}: BUILD is stale: stamped {cur}, content hashes to {exp}. "
            f"Run: uv run python scripts/stamp-mod-build.py")


def test_KNOWN_KINDS_matches_the_kinds_the_probe_actually_EMITS():
    """`_livedump.KNOWN_KINDS` says it is "read off its `row(...)` calls ... pinned by a
    test rather than inferred". The only assertion was a single membership check, and
    nothing parsed the Lua -- so the docstring described a guarantee that did not exist.

    Both directions, because they fail differently and both are bad:
      * a kind the probe emits and the set does not  -> `x4live dump` returns rc 3 on a
        perfectly good dump, which is loud and wrong;
      * a kind in the set the probe no longer emits  -> nothing notices, ever.
    """
    import re

    from x4validate import _livedump

    probe = None
    for p in (pathlib.Path(__file__).resolve().parents[3] / "mods").rglob("engine_probe.lua"):
        probe = p
        break
    if probe is None:
        pytest.skip("engine_probe.lua is not in this checkout")
    src = probe.read_text(encoding="utf-8")
    # TWO emission forms, and missing the second made this test a false alarm on its
    # first run: it reported EXT as listed-but-never-emitted, when EXT is emitted by
    # `local vals = { "EXT" }` ... `row(unpack(vals))`. A search finding nothing is a
    # lead, not a fact -- the second, differently-shaped search is what settled it.
    direct = set(re.findall(r'row\(\s*"([A-Z_]+)"', src))
    unpacked = set(re.findall(r'=\s*\{\s*"([A-Z_]+)"\s*\}', src))
    emitted = direct | unpacked
    assert direct, "parsed no direct row(\"KIND\" calls -- the extraction has drifted"
    assert unpacked, (
        "parsed no table-seeded kinds -- if that form is really gone, delete this "
        "assertion deliberately rather than letting the regex silently see less")
    known = set(_livedump.KNOWN_KINDS)
    assert emitted - known == set(), (
        f"the probe emits kinds KNOWN_KINDS does not list: {sorted(emitted - known)} -- "
        f"x4live dump will refuse a good dump")
    assert known - emitted == set(), (
        f"KNOWN_KINDS lists kinds the probe never emits: {sorted(known - emitted)} -- "
        f"a removed kind that nothing noticed")


def test_the_probe_REPORTS_the_build(lua_factory):
    """The fingerprint is only useful if it crosses the wire."""
    from x4validate import _livepipe as lp

    rt = live()
    rr = ask(rt, 1, "probe")
    builds = [f.split("=", 1)[1] for f in rr.fields if f.startswith("build=")]
    assert builds and len(builds[0]) == 8, f"no usable build= field: {rr.payload[:200]}"


def test_a_save_load_CLEARS_the_id_allowlist(lua_factory):
    """Ids do not survive a save load, and neither may the allowlist. IsValidComponent
    catches a DESTROYED object, but not a REUSED handle -- and a reused handle would
    return another object's data under the id you asked about, which is a wrong answer
    wearing the grammar of a right one."""
    rt = live()
    p = ask(rt, 1, "player")
    sid = [f.split("=", 1)[1] for f in p.fields if f.startswith("occupiedship=")][0]
    assert ask(rt, 2, "component", sid).status == "OK", "id was not usable to begin with"

    # Fire the same MD signal md/lua_loader.xml raises on game start AND game load.
    #
    # ⚠ CAVEAT ADDED 2026-08-29, and it is the point of the tests below. This test is
    # GREEN and the mechanism it exercises HAS NEVER RUN IN THE GAME: measured across a
    # game start and a save load, the hook's log line appears 0 times. It cannot fire --
    # it subscribes from inside Init, and Init is itself driven by that signal. So this
    # asserts "the callback does the right thing IF called", which is true and was read
    # for weeks as "ids are cleared on save load". They ARE, but by chunk re-creation
    # instead. A test can be correct and still license a false belief about production.
    rt.execute('_G.__fire_game_load()')

    r = ask(rt, 3, "component", sid)
    assert r.status == "ERR" and "not issued by this session" in r.payload, (
        f"a pre-load id survived the save load: {r.status} {r.payload[:120]}")


# --------------------------------------------------------------------------- #
# UI RELOAD -- alt-enter and save load both re-execute this chunk
# --------------------------------------------------------------------------- #

def test_a_ui_reload_EMPTIES_the_id_allowlist():
    """CONFIRMED IN GAME 2026-08-29, twice, each time with a control that passed first.

    A reload builds a FRESH chunk, so `issued_ids` starts empty and an id accepted
    seconds earlier is refused. For a save load that is REQUIRED (a reused handle would
    return another object's data under the id you asked about). For an alt-enter it is
    merely surprising. This pins the behaviour -- it is the real clearing mechanism,
    not the dead hook above.
    """
    rt = live()
    p = ask(rt, 1, "player")
    ident = [f.split("=", 1)[1] for f in p.fields if f.startswith("occupiedship=")][0]
    # CONTROL: it must resolve BEFORE the reload, or the assertion after proves nothing.
    assert ask(rt, 2, "component", ident).status == "OK", "id was not usable to begin with"

    reload_ui(rt)

    r = ask(rt, 3, "component", ident)
    assert r.status == "ERR", "the allowlist survived a reload; ids would outlive a save"
    assert "not issued by this session" in r.payload


def test_the_refusal_after_a_reload_NAMES_the_reload():
    """Two very different causes produce one refusal. Until the message distinguished
    them, 'the UI reloaded' looked exactly like 'you typed a bad id' -- so the refusal
    blamed the caller for something the engine had done."""
    rt = live()
    ask(rt, 1, "player")            # seed the allowlist, so emptiness means the reload
    reload_ui(rt)
    r = ask(rt, 2, "component", "1234ULL")
    assert r.status == "ERR"
    low = r.payload.lower()
    assert "reload" in low, f"the refusal does not mention a reload: {r.payload[:160]}"
    assert "alt-enter" in low and "save" in low, (
        f"the refusal does not name the two actions that cause one: {r.payload[:160]}")


def test_a_BOGUS_id_does_not_blame_a_reload_when_ids_ARE_issued():
    """The twin of the test above, and it targets the OTHER branch. Without it the
    message could hard-code the reload wording and both tests would still pass, which
    would make the diagnosis useless exactly when it matters."""
    rt = live()
    ask(rt, 1, "player")
    r = ask(rt, 2, "component", "1234ULL")
    assert r.status == "ERR"
    low = r.payload.lower()
    assert "mistyped" in low, f"a bogus id was not diagnosed as one: {r.payload[:160]}"


def test_probe_reports_loaded_at_and_build_CANNOT_detect_a_reload():
    """`build=` answers 'is the game running the file on disk'. It CANNOT answer 'has
    the chunk been re-executed', because a reload runs the SAME file -- which is why
    loaded_at is reported beside it.

    The `_G.now` change models a MEASURED fact: getElapsedTime() RESETS with the UI
    (0.89 then 0.82 across reloads that debug.txt timestamped 254.56 and 425.42). So
    loaded_at is a CHANGE detector, not a time.
    """
    rt = live()
    before = probe_fields(rt, 1)
    assert before["loaded_at"] == "100.00", before
    assert before["load_hook_fired"] == "false", before

    rt.execute("_G.now = 0.82")
    reload_ui(rt)
    after = probe_fields(rt, 2)

    assert after["loaded_at"] == "0.82", "loaded_at did not follow the new chunk"
    assert after["build"] == before["build"], (
        "build changed across a reload; then this test is measuring the wrong thing")


def test_probe_does_NOT_claim_a_reload_COUNTER():
    """It briefly did, and the counter could not work.

    MEASURED IN GAME 2026-08-29: `_G` does not survive a UI reload -- the engine
    rebuilds the whole lua environment -- so a global counter read 1, then 1, across
    two real reloads. Only its "no reload happened" branch was ever reachable, which
    is an instrument whose failure branch does not exist.

    Pinned so nobody reintroduces it, and so the reasoning survives with the decision:
    the same fact is why the allowlist could NOT have been persisted across reloads
    even if we had wanted to.
    """
    rt = live()
    assert "incarnation" not in probe_fields(rt, 1)


def test_the_dead_load_hook_is_INSTRUMENTED_so_its_deadness_stays_visible():
    """MEASURED IN GAME: the hook has never fired. Kept as defence in depth, but now
    reported, because 'RegisterEvent is present' was read as 'the handler runs' -- a
    symbol existing says nothing about a callback executing.

    Asserting BOTH states matters: a field hard-coded to "false" would look identical
    in game and would be worthless the day the hook came alive.
    """
    rt = live()
    assert probe_fields(rt, 1)["load_hook_fired"] == "false"
    fired = g(rt, "__fire_game_load")()
    assert fired >= 1, "the hook is not registered at all; the probe field would be a lie"
    assert probe_fields(rt, 2)["load_hook_fired"] == "true"


# --------------------------------------------------------------------------- #
# F124 pipe unref guard: wiring into the real chunk (the guard's own logic is
# tested in test_pipe_unref_guard.py against the extracted block and the real DLL)
# --------------------------------------------------------------------------- #

#: pipes.lua's shape: `pipes[name].file`, opened by Connect_Pipe when nil. The file is a
#: real (zero-size) userdata, so the guard reaches its version gate; GetExtensionList is
#: absent in this harness, so the gate refuses BEFORE any ffi access.
_FAKE_CONNECT = """
_G.fake_pipe_module = {pipes = {}}
_G.fake_pipe_module.Connect_Pipe = function(name)
    local p = _G.fake_pipe_module.pipes
    p[name] = p[name] or {}
    if p[name].file == nil then p[name].file = newproxy(false) end
    return "orig"
end
"""


def _pipes_module(rt):
    """The table the chunk required (and the guard wrapped), not the pre-chunk's source."""
    return rt.eval("require('extensions.sn_mod_support_apis.ui.named_pipes.Pipes')")


def _guard_logs(rt):
    return [s for s in g(rt, "log").values() if "pipe unref guard INACTIVE" in s]


def test_probe_reports_the_pipe_unref_guard_INACTIVE_when_Connect_Pipe_is_absent():
    """The default fake module has no Connect_Pipe: an install refusal. It must be
    reported by probe and logged once, AFTER the load marker (never before it)."""
    rt = live()
    assert probe_fields(rt, 1)["pipe_unref_guard"] == "inactive:Connect_Pipe absent"
    logs = list(g(rt, "log").values())
    assert "_LIVE loaded" in logs[0]
    assert len(_guard_logs(rt)) == 1


def test_probe_reports_ARMED_once_the_wrapper_is_installed():
    rt = live(_build(pre=_FAKE_CONNECT))
    f = probe_fields(rt, 1)
    assert (f["pipe_unref_guard"], f["pipe_unref_stamped"]) == ("armed", "0")
    assert _guard_logs(rt) == []


@pytest.mark.parametrize("name", ["x4live", "x4live_startmenu"])
def test_the_helpers_OWN_pipes_reach_the_gate(name):
    """Both helper pipe names are guarded. With no GetExtensionList the gate refuses."""
    rt = live(_build(pre=_FAKE_CONNECT))
    assert _pipes_module(rt).Connect_Pipe(name) == "orig"
    assert probe_fields(rt, 1)["pipe_unref_guard"] == (
        "inactive:extension list unavailable: GetExtensionList absent")
    assert len(_guard_logs(rt)) == 1


def test_a_FOREIGN_pipe_does_not_reach_the_gate():
    rt = live(_build(pre=_FAKE_CONNECT))
    _pipes_module(rt).Connect_Pipe("someone_elses_pipe")
    assert probe_fields(rt, 1)["pipe_unref_guard"] == "armed"


def test_a_UI_reload_does_not_stack_a_second_wrapper():
    rt = live(_build(pre=_FAKE_CONNECT))
    rt.execute("_G.__w1 = require('x').Connect_Pipe")
    reload_ui(rt)
    assert rt.eval("require('x').Connect_Pipe == _G.__w1 and _G.__w1 ~= _G.fake_pipe_module.Connect_Pipe")
    assert probe_fields(rt, 2)["pipe_unref_guard"] == "armed"


#: The start-menu install point: the UI bundle is present and C.IsStartmenu() is true, so
#: the chunk wraps the directly required Pipes module before the world install point runs.
_START_MENU = """
_G.fake_C = {IsStartmenu = function() return true end}
_G.X4ToolkitAutomationBundle = {build = "t", start_ui = function() return {status = "STARTED"} end}
"""


def _start_marker_then_guard(rt):
    logs = list(g(rt, "log").values())
    start = [i for i, s in enumerate(logs) if s.startswith("X4TOOLKIT_UI_START ")]
    guard = [i for i, s in enumerate(logs) if "pipe unref guard INACTIVE" in s]
    return logs, start, guard


def test_a_START_MENU_install_refusal_is_logged_once_after_that_paths_marker():
    """Review M3: Init's report() may never run in the start menu, so the start-menu
    install point logs its own refusal, after its X4TOOLKIT_UI_START marker. The world
    install point then reuses the same guard, so Init does not log it a second time."""
    rt = _build(pre=_START_MENU)
    logs, start, guard = _start_marker_then_guard(rt)
    assert len(start) == 1 and "OBSERVED STARTED" in logs[start[0]], logs
    assert len(guard) == 1 and guard[0] > start[0], logs
    assert "Connect_Pipe absent" in logs[guard[0]]
    live(rt)                                  # Init runs: no second INACTIVE line
    assert len(_guard_logs(rt)) == 1
    assert probe_fields(rt, 1)["pipe_unref_guard"] == "inactive:Connect_Pipe absent"


def test_outside_the_start_menu_nothing_is_logged_before_Init():
    """The twin: IsStartmenu() false returns before the start-menu install, so the only
    install point is the world one and its refusal waits for Init's load marker."""
    rt = _build(pre=_START_MENU + "_G.fake_C.IsStartmenu = function() return false end\n")
    logs, start, guard = _start_marker_then_guard(rt)
    assert len(start) == 1 and "NOT_START_MENU" in logs[start[0]] and guard == [], logs
    live(rt)
    assert len(_guard_logs(rt)) == 1


def test_the_container_probe_makes_a_LIVE_CALL_and_can_DISCRIMINATE():
    """Probe before building. A symbol's TYPE says nothing about whether a given
    ARGUMENT SHAPE works, and that is the open question for a cross-faction `objects`:
    0 of 6 vanilla call sites pass a SECTOR as the container.

    ⚠ The owner must be a FACTION THAT SPANS SECTORS. The first version asked about the
    player and reported `table:1` for both calls in game -- because the player owns one
    ship, so "filtered correctly" and "ignored the container" are the SAME OUTPUT. A
    probe whose two branches cannot produce different answers cannot answer its own
    question, and this test exists to keep that fix from being undone.
    """
    rt = _build()
    rt.execute(FAKE_C)
    rt.execute('_G.component_data = {sectorid = "ID: 7479"}')
    rt.execute("""
        _G.seen = {}
        function GetContainedObjectsByOwner(owner, container)
            _G.seen[#_G.seen+1] = tostring(owner) .. "/" .. tostring(container)
            if container == nil then return {1, 2, 3, 4, 5} end
            return {1, 2}
        end
    """)
    init(rt)
    f = probe_fields(rt, 1)
    assert f["contained_all_argon"] == "table:5", f
    assert f["contained_in_my_sector"] == "table:2", f
    seen = list(g(rt, "seen").values())
    assert any(s.endswith("/nil") for s in seen), "the owner-only call was never made"
    assert any(not s.endswith("/nil") for s in seen), "the sector call was never made"
    assert all(s.startswith("argon/") for s in seen), (
        "the probe asked about the player again; with one owned object both calls "
        f"return table:1 and the result proves nothing: {seen}")


def test_probe_still_reports_the_objects_verb_CANDIDATE_SYMBOLS():
    """If a symbol silently drops out of PROBE_SYMBOLS we lose the one cheap way to
    find out whether the design is even possible, and would discover it from a broken
    verb in game instead."""
    rt = live()
    f = probe_fields(rt, 1)
    for name in ("GetContainedObjectsByOwner", "GetNumAllFactions", "GetAllFactions"):
        assert name in f, f"probe stopped reporting {name}"


# --------------------------------------------------------------------------- #
# STEP-0 -- the containerprobe instrument
# --------------------------------------------------------------------------- #
#
# WHAT THESE CAN AND CANNOT PROVE, said out loud so a green here is not misread as an
# answer to the engine question. They assert the probe's STRUCTURE: that it refuses
# cleanly, never raises, keeps the two container forms as SEPARATE numbers, walks the
# list it was handed, and computes its verdicts rather than hard-coding them. They do
# NOT assert the engine's answers -- the fake is not the engine, and the wire-form
# round trip comes back NON-identical here by construction. Only the running game
# settles Q1/Q2/Q3; these keep the instrument honest until it gets there.

#: A REAL sector token shape. `player` reports `ID: 7479`, and both container
#: conversions accept it -- ConvertStringToLuaID wraps it, ConvertStringTo64Bit parses
#: the number out. A made-up token like "SECTOR1" converts to nil on the 64-bit path,
#: which silently collapses the two forms this test exists to tell apart.
SECT = '{sectorid = "ID: 7479"}'


def cprobe(rt, seq=1, *args):
    r = ask(rt, seq, "containerprobe", *args)
    assert r.status == "OK", r.payload
    return dict(f.split("=", 1) for f in r.fields if "=" in f)


def test_containerprobe_REFUSES_when_the_primitive_is_ABSENT():
    """An absent primitive must be an ERR naming the symbol, not a raise and not a
    reply full of blanks that reads like an answer."""
    rt = live()
    rt.execute("GetContainedObjectsByOwner = nil")
    r = ask(rt, 1, "containerprobe")
    assert r.status == "ERR"
    assert "GetContainedObjectsByOwner" in r.payload


def test_containerprobe_reports_BOTH_container_forms_SEPARATELY():
    """Q2's whole point. Agreement settles which idiom to copy; a DISAGREEMENT is the
    more valuable finding. A single merged number could express neither, so the three
    populations here are deliberately all different sizes -- 3 / 2 / 1 -- and no two
    can be confused for each other."""
    rt = live(component_data=SECT, contained=(
        '{["argon|luaid:ID: 7479"] = {"A", "B"},'
        ' ["argon|7479"] = {"A"},'
        ' argon = {"A", "B", "C"}}'))
    f = cprobe(rt)
    assert f["n.owner_only"] == "3"
    assert f["n.sector_via_luaid"] == "2"
    assert f["n.sector_via_64bit"] == "1"
    # And it must SAY which list the rest of the reply describes, because that cannot
    # be inferred from the counts.
    assert f["used_handle"] == "luaid"


def test_containerprobe_REPORTS_every_missing_symbol_rather_than_RAISING():
    """A probe that can raise is not a probe. Every optional symbol is removed at once
    -- the reply must still arrive, with each loss named in its own field."""
    rt = live(component_data=SECT, contained='{argon = {"A"}}')
    rt.execute("ConvertIDTo64Bit = nil ; GetContainedStationsByOwner = nil ; "
               "IsComponentClass = nil ; Helper.isComponentClass = nil")
    f = cprobe(rt)
    assert f["class.stations_engine_control"] == "<symbol absent>"
    assert f["wire.via_ConvertIDTo64Bit"].startswith("raised:")
    assert f["class.helper_usable"] == "false"
    assert f["class.bare_usable"] == "false"


def test_containerprobe_counts_classes_TWO_WAYS_and_against_an_ENGINE_CONTROL():
    """Q3. Three independent readings of the same question -- Helper.isComponentClass
    over classid/realclassid, bare IsComponentClass over the handle, and
    GetContainedStationsByOwner asking the engine directly. Agreement is the evidence;
    one number alone would be an assertion."""
    rt = live(
        component_data=SECT,
        contained='{["argon|luaid:ID: 7479"] = {"A", "B", "C"}}',
        class_by_field=('{["classid-of-A"] = "ship", ["classid-of-B"] = "ship",'
                        ' ["realclassid-of-C"] = "station"}'),
        obj_class='{A = "ship", B = "ship", C = "station"}',
        contained_stations='{argon = {"C"}}',
    )
    f = cprobe(rt)
    assert f["class.rows_walked"] == "3"
    assert f["class.ships_helper"] == "2" and f["class.ships_bare"] == "2"
    assert f["class.stations_helper"] == "1" and f["class.stations_bare"] == "1"
    assert f["class.stations_engine_control"] == "1"


def test_the_wire_roundtrip_verdict_CAN_go_BOTH_ways():
    """A verdict field that always reads the same is decoration, not an instrument.

    Both branches are reached. With the faithful fake -- where the bare globals return
    lua numbers, as MEASURED in game -- the round trip agrees, which is the answer the
    real engine also gave. So the falsifying half plants a conversion that DISAGREES,
    proving the field is computed rather than constant. Which value the engine produces
    is not knowable here; that it can render either is."""
    plant = '{["argon|luaid:ID: 7479"] = {"A"}}'
    rt = live(component_data=SECT, contained=plant)
    assert cprobe(rt)["wire.roundtrip_identical"] == "true"

    rt2 = live(component_data=SECT, contained=plant)
    # A conversion that does NOT round-trip: the verdict must notice.
    rt2.execute("function ConvertStringTo64Bit(s) return tostring(s) .. 'X' end")
    assert cprobe(rt2)["wire.roundtrip_identical"] == "false"


def test_an_EMPTY_sector_SAYS_SO_rather_than_omitting_the_wire_fields():
    """Nothing-to-look-at and could-not-look must not render alike. A dropped field
    would read as a non-answer; this makes the absence explicit and says what to do."""
    rt = live(component_data=SECT, contained='{}')
    f = cprobe(rt)
    assert "wire" in f
    assert "no objects here" in f["wire"]


def test_containerprobe_reports_whether_the_FACTION_LIST_is_reachable():
    """Q5. `objects` with no faction argument is built entirely on this pair, so an
    unexported symbol changes the design rather than merely degrading a field. Both
    branches are reached: exported, and deleted from the fake C table (which raises on
    lookup, as real ffi does)."""
    rt = live(component_data=SECT, contained='{}', n_factions=7)
    f = cprobe(rt)
    assert f["C.GetNumAllFactions"] == "function"
    assert f["n.factions"] == "7"

    rt2 = live(component_data=SECT, contained='{}')
    rt2.execute("_G.fake_C.GetNumAllFactions = nil")
    f2 = cprobe(rt2, 2)
    assert f2["C.GetNumAllFactions"] == "<not exported>"
    assert f2["n.factions"].startswith("raised:")


def test_a_faction_call_that_SUCCEEDS_but_yields_nil_does_NOT_blame_a_raise():
    """Three outcomes, three distinct reports. The `okn and nfac or "raised:"` idiom
    collapses the middle one into the third, so a call that worked and returned
    something unusable would be reported as an error that never happened -- a
    diagnostic misattributing its own failure, which is worse than no diagnostic.

    This branch was found UNASSERTED by a mutation probe, which had appeared to kill
    the mutant only because every edit to the mod file also fails the BUILD-fingerprint
    test. That is an adjacent question, not this one."""
    rt = live(component_data=SECT, contained='{}')
    rt.execute('_G.fake_C.GetNumAllFactions = function() return "not-a-number" end')
    f = cprobe(rt)
    assert f["n.factions"] == "<call ok but not a number>"
    assert "raised" not in f["n.factions"]


# --------------------------------------------------------------------------- #
# `objects` -- the container primitive, the flag columns, and the id wire form
# --------------------------------------------------------------------------- #
#
# WHAT THESE PROVE, and what they cannot. They pin the GUARDS: one id form per object,
# every narrowing announced, both paths reachable, and vanilla's validity predicate
# ported clause for clause. They do NOT prove what the engine returns -- the fake is
# not the engine. The in-game `compare` run is what settles method equivalence.

#: A realistic sector token. `player` reports exactly this shape, and BOTH container
#: conversions accept it: ConvertStringToLuaID wraps it, ConvertStringTo64Bit parses
#: the number out of it.
TOKEN = "ID: 7479"
LUAKEY = "argon|luaid:ID: 7479"


def objects_reply(rt, seq, *args):
    r = ask(rt, seq, "objects", *args)
    assert r.status == "OK", r.payload[:250]
    return r


def hdr(r):
    return dict(kv.split("=", 1) for kv in r.fields[0].split(" ") if "=" in kv)


# --- the id wire form -------------------------------------------------------- #

def test_wire_gives_ONE_string_for_every_shape_of_the_SAME_id(lua_factory):
    """The contract the whole migration rests on. The ffi entry points hand back cdata
    that stringifies `1046331ULL`; the bare globals hand back a lua NUMBER that
    stringifies `1046331`. Same id, two strings -- so without one choke point the same
    object gets two ids, dedupe breaks, and any set comparison between the two
    enumeration paths is meaningless."""
    rt = _build(extra="function _T_wire(v) return wire(v) end")
    rt.execute(FAKE_C)
    init(rt)
    w = g(rt, "_T_wire")
    forms = [w("1046331ULL"), w(1046331), w("1046331"), w("ID: 1046331")]
    assert len(set(forms)) == 1, f"one object rendered as several ids: {forms}"
    assert forms[0] == "1046331ULL", forms[0]


def test_wire_REFUSES_an_id_at_or_above_2_pow_53(lua_factory):
    """The bare globals return lua numbers, which are doubles: above 2^53 a double can
    no longer hold every distinct uint64, so ids would silently collide. The rule was
    written in a comment for months; this is the check that ENFORCES it."""
    rt = _build(extra="function _T_wire(v) return pcall(wire, v) end")
    rt.execute(FAKE_C)
    init(rt)
    w = g(rt, "_T_wire")
    ok_lo, val = w(2 ** 53 - 1)
    assert ok_lo is True, f"a representable id must still be accepted: {val}"
    assert val == "9007199254740991ULL", val
    ok_hi, err = w(2 ** 53)
    assert ok_hi is False, "an id at 2^53 must be refused, not silently truncated"
    assert "2^53" in str(err), err


def test_a_SECTOR_TOKEN_is_NOT_canonicalised_into_an_id(lua_factory):
    """Sector tokens are a DIFFERENT KIND. `player` hands out `ID: 7479` and every
    sector-scoped verb takes it back verbatim; running it through `wire` would rewrite
    the token and silently break every sector query."""
    rt = live(component_data='{sectorid = "ID: 7479"}')
    p = ask(rt, 1, "player")
    assert any(f == "sectorid=ID: 7479" for f in p.fields), p.payload
    # It is allowlisted in its OWN form, so it can be inspected like anything else.
    r = ask(rt, 2, "component", "ID: 7479", "name")
    assert r.status == "OK", r.payload


# --- the container path, and both paths being reachable ---------------------- #

def test_objects_uses_the_CONTAINER_and_says_which_path_ran(lua_factory):
    rt = live(n_factions=1, fake_factions='{"argon"}',
              component_data='{classid = "ship", realclassid = "ship"}',
              contained='{["' + LUAKEY + '"] = {"A", "B", "C"}}')
    r = objects_reply(rt, 1, TOKEN)
    h = hdr(r)
    assert h["path"] == "container", h
    assert h["enumerated"] == "3" and h["matched"] == "3" and h["shown"] == "3", h
    assert len(r.fields) - 1 == 3


def test_objects_and_wide_are_BOTH_reachable_and_DISTINGUISHABLE(lua_factory):
    """--wide is retained as an explicit opt-in, never an automatic fallback: an empty
    sector and a failed call must not render alike. Both populations are planted at
    DIFFERENT sizes so neither can be mistaken for the other."""
    rt = live(n_stations=4, n_ships=0,
              component_data='{classid = "station", realclassid = "station", sectorid = "' + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {"A", "B"}}')
    c = objects_reply(rt, 1, TOKEN, "station", "argon")
    w = objects_reply(rt, 2, TOKEN, "station", "argon", "--wide")
    assert hdr(c)["path"] == "container" and hdr(c)["enumerated"] == "2", hdr(c)
    assert hdr(w)["path"] == "wide" and hdr(w)["enumerated"] == "4", hdr(w)
    # `enumerated` alone is NOT enough: it counts handles the primitive returned and is
    # unaffected by whether we could read any of them. MEASURED in game -- the wide path
    # reported enumerated=1539 with unclassified=1539, every object unreadable, because
    # GetComponentData was handed a raw UniverseID cdata instead of a converted id.
    # Assert the objects were actually READ.
    assert hdr(w)["matched"] == "4", f"wide enumerated but read nothing: {hdr(w)}"
    assert "unclassified" not in hdr(w), f"wide could not classify its own rows: {hdr(w)}"
    assert hdr(c)["matched"] == "2", hdr(c)


def test_wide_without_a_faction_is_REFUSED_not_silently_empty(lua_factory):
    """The old walk has no all-factions form. Returning an empty list would be a
    silent narrowing; the refusal names the reason and what to do."""
    rt = live()
    r = ask(rt, 1, "objects", TOKEN, "all", "--wide")
    assert r.status == "ERR"
    assert "faction" in r.payload.lower(), r.payload


def test_an_EMPTY_sector_is_OK_with_zeros_never_ABSENT(lua_factory):
    """We asked and the answer is none. ABSENT would mean we could not ask."""
    # A faction IS asked -- otherwise this would pass vacuously, proving only that we
    # enumerated nobody rather than that an empty sector answers cleanly.
    rt = live(n_factions=1, fake_factions='{"argon"}', contained="{}")
    r = objects_reply(rt, 1, TOKEN)
    h = hdr(r)
    assert h["shown"] == "0" and h["matched"] == "0" and h["enumerated"] == "0", h
    assert h["CAPPED"] == "no"


# --- class filtering, and the objects that are NEITHER ----------------------- #

def test_class_filter_keeps_ships_stations_and_NEITHER_visible_under_all(lua_factory):
    """MEASURED in game: an in-sector argon list is 205 ships + 15 stations + 23
    objects that are neither (drones, deployables, lockboxes). `all` must show them and
    every row must carry its class, or those 23 are a silent remainder."""
    rt = live(
        n_factions=1, fake_factions='{"argon"}',
        contained='{["' + LUAKEY + '"] = {"A", "B", "C"}}',
        # TWO ships and ONE station, deliberately unequal: with 1 and 1 a filter that
        # confused the two classes would produce the SAME count and the test could not
        # tell. That is the shape the and/or fall-through bug hid in.
        class_by_field=('{["classid-of-A"] = "ship", ["classid-of-B"] = "ship",'
                        ' ["realclassid-of-C"] = "station"}'),
    )
    assert hdr(objects_reply(rt, 1, TOKEN, "ship"))["matched"] == "2"
    assert hdr(objects_reply(rt, 2, TOKEN, "station"))["matched"] == "1"
    assert hdr(objects_reply(rt, 3, TOKEN, "all"))["matched"] == "3"


def test_an_UNKNOWN_class_is_REFUSED_rather_than_treated_as_all(lua_factory):
    rt = live(contained='{["' + LUAKEY + '"] = {"A"}}')
    r = ask(rt, 1, "objects", TOKEN, "shp")
    assert r.status == "ERR"
    assert "ship, station or all" in r.payload, r.payload


# --- the faction axis -------------------------------------------------------- #

def test_no_faction_enumerates_EVERY_faction_and_says_how_many(lua_factory):
    rt = live(n_factions=3,
              fake_factions='{"argon", "teladi", "paranid"}',
              contained='{["argon|luaid:' + TOKEN + '"] = {"A"},'
                        ' ["teladi|luaid:' + TOKEN + '"] = {"B"},'
                        ' ["paranid|luaid:' + TOKEN + '"] = {"C"}}')
    h = hdr(objects_reply(rt, 1, TOKEN))
    assert h["factions"] == "3", h
    assert h["enumerated"] == "3", h


def test_hidden_CHANGES_the_faction_scope_and_the_header_says_so(lua_factory):
    """civilian (mass traffic) and criminal carry tags="hidden" in factions.xml, so
    GetAllFactions(false) omits them. Default excludes; --hidden widens; and the scope
    is stated either way so a narrower answer is never silent."""
    rt = live(n_factions=1, n_factions_hidden=2,
              fake_factions='{"argon", "civilian"}',
              contained='{["argon|luaid:' + TOKEN + '"] = {"A"},'
                        ' ["civilian|luaid:' + TOKEN + '"] = {"B", "C"}}')
    plain = hdr(objects_reply(rt, 1, TOKEN))
    wide = hdr(objects_reply(rt, 2, TOKEN, "all", "--hidden"))
    assert plain["hidden"] == "no" and plain["factions"] == "1", plain
    assert wide["hidden"] == "yes" and wide["factions"] == "2", wide
    assert int(wide["enumerated"]) > int(plain["enumerated"]), (plain, wide)


def test_a_faction_whose_call_FAILS_is_NAMED_not_absorbed(lua_factory):
    """One faction the engine dislikes must not quietly cost us the others. A smaller
    number with no explanation is the narrowing-without-announcement defect."""
    rt = live(n_factions=2, fake_factions='{"argon", "boom"}',
              contained='{["argon|luaid:' + TOKEN + '"] = {"A"}}')
    rt.execute("""
        local real = GetContainedObjectsByOwner
        function GetContainedObjectsByOwner(owner, container)
            if owner == "boom" then error("engine said no") end
            return real(owner, container)
        end
    """)
    r = objects_reply(rt, 1, TOKEN)
    assert "factions_failed=1(boom)" in r.fields[0], r.fields[0]


# --- vanilla's validity predicate: one twin per clause ----------------------- #

def _valid_rt(lua_factory, **flags):
    """One object, all flags true-by-default for a VALID ship, overridden per test."""
    base = {"classid": "ship", "realclassid": "ship", "isknown": "true",
            "isradarvisible": "true", "iswreck": "false", "isunit": "false",
            "isdeployable": "false", "isorphaned": "false",
            "isattachedaslimpet": "false", "ismasstraffic": "false",
            "isenemy": "false", "isdocked": "false"}
    base.update(flags)
    body = ", ".join(
        f'{k} = {v}' if v in ("true", "false") else f'{k} = "{v}"'
        for k, v in base.items())
    return live(n_factions=1, fake_factions='{"argon"}',
                component_data="{" + body + "}",
                contained='{["' + LUAKEY + '"] = {"A"}}')


def test_isObjectValid_ACCEPTS_a_plain_known_ship(lua_factory):
    """The control. Without this, every rejection test below could be passing because
    the predicate rejects everything."""
    assert hdr(objects_reply(_valid_rt(lua_factory), 1, TOKEN))["valid"] == "1"


@pytest.mark.parametrize("clause,flags", [
    ("not a listed class", {"classid": "asteroid", "realclassid": "asteroid"}),
    ("a unit",             {"isunit": "true"}),
    ("not known",          {"isknown": "false"}),
    ("not radar visible",  {"isradarvisible": "false"}),
    ("a limpet",           {"isattachedaslimpet": "true"}),
    ("a station wreck",    {"classid": "station", "realclassid": "station", "iswreck": "true"}),
    ("non-enemy traffic",  {"ismasstraffic": "true", "isenemy": "false"}),
])
def test_isObjectValid_REJECTS_each_clause_SEPARATELY(lua_factory, clause, flags):
    """SEVEN twins for seven clauses. A single twin against a compound condition only
    ever tests the FIRST clause it trips -- every guard behind it is shadowed and never
    evaluated. Each case here differs from the accepted control by ONE predicate.

    The predicate is menu_map.lua:7548-7564 plus the mass-traffic drop at :7419,
    COPIED. An earlier version of this design composed a two-field class test from the
    schema and missed five of these."""
    h = hdr(objects_reply(_valid_rt(lua_factory, **flags), 1, TOKEN))
    assert h["valid"] == "0", f"{clause} should not be valid: {h}"
    # Still LISTED -- rejection is a column, not a filter. The engine returns ids and
    # positions for these and the research use case needs them.
    assert h["matched"] != "0", f"{clause} was dropped from the list, not just marked"


def test_an_UNDECIDABLE_validity_is_reported_SEPARATELY_from_false(lua_factory):
    """`nil` is not `false`. If Helper is missing we cannot tell, and "could not tell"
    must never render as "no" -- that is a narrowing wearing the grammar of an answer."""
    rt = _valid_rt(lua_factory)
    rt.execute("Helper.isComponentClass = nil")
    h = hdr(objects_reply(rt, 1, TOKEN))
    assert h["valid"] == "0", h
    assert h.get("undecided") == "1", f"an undecidable verdict was reported as false: {h}"


def test_the_flag_column_reports_each_flag_and_the_verdict(lua_factory):
    rt = _valid_rt(lua_factory, isdocked="true", isknown="true")
    r = objects_reply(rt, 1, TOKEN)
    flags = r.fields[1].split("|")[-1]
    assert flags[0] == "k", f"known not reported: {flags}"
    assert flags[1] == "d", f"docked not reported: {flags}"
    assert flags[-1] == "v", f"validity verdict not reported: {flags}"


# --- compare: the atomic proof ---------------------------------------------- #

def test_compare_runs_BOTH_paths_in_ONE_call_and_diffs_them(lua_factory):
    """MEASURED in game: argon gained 86 objects in about five minutes. Two separate
    queries therefore differ by drift, which cannot be told apart from a real method
    difference -- so the comparison only means anything inside one call."""
    rt = live(n_stations=0, n_ships=3,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "' + TOKEN + '"}',
              fake_ids='{"1ULL", "2ULL", "3ULL"}',
              contained='{["' + LUAKEY + '"] = {"ID: 1", "ID: 2"}}')
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "OK", r.payload[:250]
    h = hdr(r)
    assert h["old"] == "3" and h["new"] == "2", h
    assert h["both"] == "2" and h["old_only"] == "1" and h["new_only"] == "0", h
    assert any(f.startswith("old_only=3ULL") for f in r.fields), r.fields


def test_compare_renders_ONE_object_through_BOTH_id_paths(lua_factory):
    """This is the measurement that settles the wire-form question. Up to here it was
    inferred from two DIFFERENT objects, which is not evidence about either."""
    rt = live(n_stations=0, n_ships=1,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "' + TOKEN + '"}',
              fake_ids='{"7ULL"}',
              contained='{["' + LUAKEY + '"] = {"ID: 7"}}')
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "OK", r.payload[:250]
    verdict = [f for f in r.fields if f.startswith("wire_same_object=")][0]
    assert "DIFFERS" in verdict, (
        "the ffi form is 7ULL and the bare form is 7; the verdict must SAY they differ "
        f"rather than hiding it behind the canonicaliser: {verdict}")


def test_compare_says_so_when_NO_object_is_in_both_sets(lua_factory):
    """A verdict computed from an empty intersection would be a fabrication."""
    rt = live(n_stations=0, n_ships=0, contained="{}")
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "OK", r.payload[:250]
    assert any("wire_same_object=<no object in both sets>" in f for f in r.fields), r.fields


def test_the_header_is_PARSEABLE_as_key_value_pairs(lua_factory):
    """The header is space-delimited `key=value`, and a SECTOR TOKEN CONTAINS A SPACE:
    the engine writes sectorid as `ID: 7479`. Emitting it raw produced
    `sector=ID: 7479`, which any consumer splitting on whitespace reads as `sector=ID:`
    plus a stray `7479` -- a corrupt field and a phantom one, from a value that looked
    fine to the eye.

    Nothing asserted the header was parseable at all, so the bug was invisible to the
    suite even after it was fixed: the helper that parses headers just skipped the
    malformed token. This is the assertion that can actually go red."""
    rt = live(n_factions=1, fake_factions='{"argon"}',
              component_data='{classid = "ship", realclassid = "ship"}',
              contained='{["' + LUAKEY + '"] = {"A"}}')
    header = objects_reply(rt, 1, TOKEN).fields[0]
    # (a loop whose entire body was `continue` stood here and asserted NOTHING --
    # it read as a check. The real parse is the one below, which excludes the
    # parenthesised CAPPED= prose by CUTTING at it rather than by skipping tokens.)
    tokens = header.split(" ")
    cut = tokens.index("CAPPED=no") if "CAPPED=no" in tokens else len(tokens)
    for token in tokens[:cut]:
        assert "=" in token, (
            f"header token {token!r} has no '=', so a key=value parse breaks: {header}")
    parsed = dict(t.split("=", 1) for t in tokens[:cut])
    assert parsed["sector"] == "ID:7479", (
        f"the sector token must survive into the header without a space: {header}")
    assert parsed["path"] == "container", header


def test_an_UNCLASSIFIABLE_object_is_COUNTED_not_silently_dropped(lua_factory):
    """FOUND IN GAME 2026-08-30, and it would have shipped. Some objects in the
    galaxy-wide faction list have NO class data, and `Helper.isComponentClass` is
    `lookup[class1 * 1000 + ...]` (helper.lua:782) -- so a nil classid is arithmetic on
    nil and RAISES. `compare` and `objects --wide` both died on the first one; the
    container path never hit it, which is exactly why the old path had to stay
    reachable and be exercised.

    The fix makes classification tri-state. This pins the half that is easy to get
    wrong: an object we could not classify is NOT an object we classified as "not a
    ship", so it must be counted and announced rather than folded into the miss."""
    rt = live(n_factions=1, fake_factions='{"argon"}',
              contained='{["' + LUAKEY + '"] = {"A", "B"}}',
              nil_fields='{classid = true, realclassid = true}')
    r = objects_reply(rt, 1, TOKEN, "ship")
    h = hdr(r)
    assert h["matched"] == "0", h
    assert h.get("unclassified") == "2", (
        f"objects with no class data were dropped without being counted: {h}")


def test_a_MALFORMED_sector_token_is_REFUSED_not_silently_widened(lua_factory):
    """★ MEASURED in game 2026-08-30, and it is the most dangerous shape there is: the
    engine SILENTLY IGNORES a container it does not recognise and returns every object
    the owner has anywhere -- labelled as the sector you asked for.

        objects "ID: 514068"   -> 310 objects
        objects "ID:"          -> 1961, header still said sector=ID:
        objects "NOT_A_SECTOR" -> 1961, header still said sector=NOT_A_SECTOR

    No error, no flag, six times the data. That is the narrowing-without-announcement
    defect inverted: a step that FAILED to narrow, reporting as though it had. Sector
    tokens change every launch, so a stale one is the ordinary case."""
    rt = live(n_factions=1, fake_factions='{"argon"}',
              contained='{argon = {"A", "B", "C"}, ["' + LUAKEY + '"] = {"A"}}')
    for bad in ("ID:", "NOT_A_SECTOR", "514068", "ID 514068"):
        r = ask(rt, 1, "objects", bad, "all", "argon")
        assert r.status == "ERR", f"{bad!r} was accepted: {r.payload[:120]}"
        assert "SILENTLY IGNORED" in r.payload, r.payload
    # The two DELIBERATE galaxy forms are not tokens and must still work.
    for ok_tok in ("-", "galaxy"):
        assert ask(rt, 2, "objects", ok_tok, "all", "argon").status == "OK", ok_tok
    # And a real token still works.
    assert ask(rt, 3, "objects", TOKEN, "all", "argon").status == "OK"


def test_compare_ALSO_refuses_a_malformed_sector_token(lua_factory):
    """compare takes a sector too, and there a silently-widened container would
    manufacture a huge bogus new_only set that reads as a method difference -- the
    exact wrong conclusion the verb exists to prevent."""
    rt = live(n_factions=1, fake_factions='{"argon"}', contained="{}")
    r = ask(rt, 1, "compare", "argon", "NOT_A_SECTOR")
    assert r.status == "ERR", r.payload[:120]
    assert "SILENTLY IGNORED" in r.payload, r.payload


def test_total_COUNTS_THE_SAME_POPULATION_as_enumerated(lua_factory):
    """★ A denominator that counts a DIFFERENT population is not a denominator, it is
    another number standing next to one. MEASURED in game 2026-08-30: a class=all query
    printed `matched=1909 total=1813` -- more matches than the total -- because
    GetNumAllFaction* counts ships and stations while the container returns every class
    (drones, buildstorage, deployables).

    So a class-scoped query gets the matching count, and a mixed-class query says
    outright that it has no cheap true denominator rather than borrowing one."""
    rt = live(n_factions=1, fake_factions='{"argon"}',
              n_ships=10, n_stations=5,
              component_data='{classid = "ship", realclassid = "ship"}',
              contained='{["' + LUAKEY + '"] = {"A", "B"}}')

    ship = hdr(objects_reply(rt, 1, TOKEN, "ship", "argon"))
    assert ship["total"] == "10", f"ship total must be the SHIP count alone: {ship}"
    assert int(ship["matched"]) <= int(ship["total"]), ship

    station = hdr(objects_reply(rt, 2, TOKEN, "station", "argon"))
    assert station["total"] == "5", f"station total must be the STATION count: {station}"

    mixed = hdr(objects_reply(rt, 3, TOKEN, "all", "argon"))
    assert mixed["total"].startswith("n/a(mixed-classes)"), (
        f"a mixed-class query must NOT borrow the ship+station total: {mixed}")
    assert mixed["ships_stations"] == "15", mixed


# --- the capability harvest: `globals` and `galaxyprobe` ---------------------- #
#
# These two verbs exist to ENUMERATE the engine surface rather than guess names out of
# vanilla. Guessing can only ever confirm names somebody already thought of, so "the
# engine has no such thing" and "nobody asked" produce an identical silence.

PAD = "for i = 1, 4000 do _G['ZZPAD' .. string.rep('x', 40) .. i] = 1 end"


def test_globals_lists_names_and_types_and_NEVER_CALLS_them(lua_factory):
    """The safety property this verb lives or dies by.

    `type(v)` cannot run a function; `v()` can, and an unknown engine global may mutate
    game state. This verb only READS, by contract -- the mod's writes are the two named
    verbs in WRITE_VERBS -- so a regression here would make it a different kind of verb.
    The planted global counts its own
    invocations, so the assertion can actually go red.
    """
    rt = live()
    rt.execute("_G.__called = 0\n"
               "_G.LandMine = function() _G.__called = _G.__called + 1 end")
    r = ask(rt, 1, "globals", "landmine")
    assert r.status == "OK", r.payload[:200]
    assert any(f.startswith("LandMine|function") for f in r.fields), r.fields[:8]
    assert g(rt, "__called") == 0, "the verb INVOKED a global it merely enumerated"


def test_globals_filter_is_a_SUBSTRING_not_a_prefix(lua_factory):
    """Engine names are not consistently prefixed -- GetSectors, IsValidComponent,
    ConvertStringToLuaID -- so an anchored match would hide most of what a caller is
    hunting for. The planted name does NOT start with the filter, so a prefix
    implementation fails this."""
    rt = live()
    rt.execute("_G.WeirdlyNamedSectorThing = 1")
    r = ask(rt, 1, "globals", "sector")
    assert any(f.startswith("WeirdlyNamedSectorThing|") for f in r.fields), r.fields[:8]


def test_globals_header_denominators_cannot_exceed_each_other(lua_factory):
    """shown <= matched <= enumerated, and functions <= enumerated. A narrowing step
    that reports a wider number than the population it narrowed is the defect this whole
    toolkit exists to refuse."""
    rt = live()
    r = ask(rt, 1, "globals", "-")
    h = hdr(r)
    assert int(h["shown"]) <= int(h["matched"]) <= int(h["enumerated"]), h
    assert int(h["functions"]) <= int(h["enumerated"]), h


def test_globals_PAGES_rather_than_silently_dropping(lua_factory):
    """An enumeration that does not fit must page, not truncate. The reply cap is a hard
    one: an over-long message tears the pipe down instead of arriving short."""
    rt = live()
    rt.execute(PAD)
    r = ask(rt, 1, "globals", "zzpad")
    h = hdr(r)
    assert int(h["pages"]) > 1, h
    assert int(h["matched"]) >= 4000, h
    assert int(h["shown"]) < int(h["matched"]), "one page cannot hold them all"


def test_globals_a_page_past_the_end_is_ABSENT_not_ERR(lua_factory):
    """A well-formed question with no rows behind it is an ANSWER. Rendering it as ERR
    would make "you asked for page 9" indistinguishable from "the verb broke"."""
    rt = live()
    rt.execute(PAD)
    pages = int(hdr(ask(rt, 1, "globals", "zzpad"))["pages"])
    beyond = ask(rt, 2, "globals", "zzpad", str(pages + 1))
    assert beyond.status == "ABSENT", (beyond.status, beyond.payload[:120])


def test_globals_never_exceeds_the_payload_cap(lua_factory):
    """The verb must PAGE, not merely fit. reply()'s shared cap turns an oversized reply
    into a short ERR, which also fits under 32000 bytes -- so asserting only the size let
    a globals that stopped paging pass. MEASURED 2026-09-13: that mutant survived the
    size-only form, on the pristine base as well as after the write-verb work."""
    rt = live()
    rt.execute(PAD)
    for page in (1, 2):
        r = ask(rt, page, "globals", "zzpad", str(page))
        assert r.status == "OK", (page, r.payload[:160])
        assert len(r.payload) <= 32000, (page, len(r.payload))


def test_globals_reply_is_TAB_separated_like_every_other_verb(lua_factory):
    """Reply.fields splits on TABS. Joining with a newline instead collapses the whole
    reply into ONE field -- and every caller keeps "working", reading the rows as part of
    the header. This was written wrong the first time and caught only by reading the
    convention."""
    rt = live()
    rt.execute("_G.OnlyOneMatch_zq = 1")
    r = ask(rt, 1, "globals", "onlyonematch_zq")
    assert len(r.fields) == 2, r.fields          # header + exactly one row
    assert "\n" not in r.payload, "rows were joined with a newline, not a tab"


# --- galaxyprobe -------------------------------------------------------------- #

GALAXY = """
_G.seen_cluster_args = {}
_G.seen_station_args = {}
function GetClusters(flag)
    _G.seen_cluster_args[#_G.seen_cluster_args+1] = tostring(flag)
    if flag then return {"c1", "c2"} end
    return {"c1"}
end
function GetSectors(c) return {"s1", "s2", "s3"} end
function GetContainedStations(container, flag)
    _G.seen_station_args[#_G.seen_station_args+1] = tostring(flag)
    if flag then return {"st1", "st2"} end
    return {"st1"}
end
"""


def test_galaxyprobe_asks_GetClusters_BOTH_WAYS(lua_factory):
    """The boolean is UNDOCUMENTED and all 8 vanilla call sites pass `true`. Asking one
    way and reporting the number would record a guess in the grammar of a measurement."""
    rt = live()
    rt.execute(GALAXY)
    h = hdr(ask(rt, 1, "galaxyprobe"))
    assert h["clusters.arg_true"] == "2", h
    assert h["clusters.arg_false"] == "1", h
    assert sorted(set(g(rt, "seen_cluster_args").values())) == ["false", "true"]


def test_galaxyprobe_asks_GetContainedStations_BOTH_WAYS(lua_factory):
    rt = live()
    rt.execute(GALAXY)
    h = hdr(ask(rt, 1, "galaxyprobe"))
    assert h["stations.arg_true"] == "2", h
    assert h["stations.arg_false"] == "1", h
    assert sorted(set(g(rt, "seen_station_args").values())) == ["false", "true"]


def test_galaxyprobe_isknown_buckets_SUM_to_the_sector_total(lua_factory):
    """This is the question everything turned on: is GetSectors knowledge-limited?
    Buckets that do not sum to the population cannot answer it -- a missing sector would
    be indistinguishable from an undiscovered one."""
    rt = live()
    rt.execute(GALAXY)
    rt.execute("""
        function GetComponentData(id, ...)
            local names = {...}
            local out = {}
            for i = 1, #names do
                if names[i] == "isknown" then
                    out[i] = (tostring(id) == "s1")
                else
                    out[i] = names[i] .. "-of-" .. tostring(id)
                end
            end
            local up0 = table.unpack or unpack
            return up0(out, 1, #names)
        end
    """)
    h = hdr(ask(rt, 1, "galaxyprobe"))
    total = int(h["sectors.total"])
    assert total == 6, h        # 2 clusters x 3 sectors
    got = (int(h["sectors.isknown_true"]) + int(h["sectors.isknown_false"])
           + int(h["sectors.isknown_undecidable"]))
    assert got == total, (got, total, h)
    assert int(h["sectors.isknown_true"]) == 2, h     # s1 in each of two clusters
    assert int(h["sectors.isknown_false"]) == 4, h


def test_galaxyprobe_DEGRADES_when_a_primitive_is_absent_and_does_not_raise(lua_factory):
    """Reachability from OUR chunk is unmeasured, so the verb has to survive every
    primitive being missing and SAY so, rather than raising and returning nothing. A `?`
    is a reported non-answer; a raise is a lost run."""
    rt = live()
    rt.execute("_G.GetClusters = nil\n_G.GetSectors = nil\n_G.GetContainedStations = nil")
    r = ask(rt, 1, "galaxyprobe")
    assert r.status == "OK", r.payload[:200]
    h = hdr(r)
    assert h["type.GetClusters"] == "nil", h
    assert h["clusters.arg_true"] == "?", h
    assert h["stations.arg_true"] == "?", h
    assert h["sectors.total"] == "0", h


def test_galaxyprobe_reports_the_TYPE_of_every_primitive_it_names(lua_factory):
    """A type line for a name nobody planted is what turns "we never asked" into "the
    engine does not have it"."""
    rt = live()
    rt.execute(GALAXY)
    h = hdr(ask(rt, 1, "galaxyprobe"))
    for name in ("GetClusters", "GetSectors", "GetContainedStations",
                 "GetContainedObjectsByOwner", "GetSectorsByOwner",
                 "GetContainedBuildStoragesByOwner", "GetSectorControlStation",
                 "GetComponentData", "ConvertStringToLuaID", "ConvertIDTo64Bit"):
        assert "type." + name in h, (name, sorted(h))


def test_galaxyprobe_NEVER_CALLS_a_global_it_only_reports_the_type_of(lua_factory):
    """Same contract as `globals`: the type table is a READ. GetSectorsByOwner is in the
    reported list and is never invoked by this verb."""
    rt = live()
    rt.execute(GALAXY)
    rt.execute("_G.__soc = 0\n"
               "_G.GetSectorsByOwner = function() _G.__soc = _G.__soc + 1 end")
    ask(rt, 1, "galaxyprobe")
    assert g(rt, "__soc") == 0, "galaxyprobe INVOKED GetSectorsByOwner"


def test_galaxyprobe_stays_inside_the_payload_cap_with_many_sectors(lua_factory):
    rt = live()
    rt.execute("""
        function GetClusters(f) return {"c1"} end
        function GetSectors(c)
            local t = {}
            for i = 1, 3000 do t[i] = "sector_with_a_long_name_" .. i end
            return t
        end
        function GetContainedStations(x, f) return {} end
    """)
    r = ask(rt, 1, "galaxyprobe")
    assert r.status == "OK", r.payload[:200]
    assert len(r.payload) <= 32000, len(r.payload)
    h = hdr(r)
    assert h["sectors.total"] == "3000", h
    assert int(h["sample.shown"]) < 3000, "the sample was not bounded"


def test_galaxyprobe_survives_a_primitive_that_EXISTS_but_RAISES(lua_factory):
    """The DEGRADES test above is stopped by the type-check guard and never reaches the
    pcall behind it -- a guard that fires first SHADOWS the clause behind it (CLAUDE.md
    #26), and a mutant that removed that pcall SURVIVED until this test existed. Two
    clauses, two twins.

    A primitive that exists and throws is a real case, not a contrived one: a wrong
    argument shape, or engine state the call cannot cope with.
    """
    rt = live()
    rt.execute("function GetClusters(f) error('engine says no') end")
    r = ask(rt, 1, "galaxyprobe")
    assert r.status == "OK", r.payload[:200]
    h = hdr(r)
    assert h["type.GetClusters"] == "function", h   # it EXISTS, so the type guard passes
    assert h["clusters.arg_true"] == "?", h         # the pcall is what saved the run
    assert h["sectors.total"] == "0", h


def test_recon_ONLY_calls_names_from_its_HARDCODED_lists(lua_factory):
    """The safety property that lets `recon` exist at all.

    Every other verb refuses to call what it finds. This one calls, so the set of names it
    may touch must be fixed in the SOURCE, never supplied by the caller -- otherwise it is
    an arbitrary-execution primitive wearing a READ label.

    THE FIRST VERSION OF THIS TEST WAS VACUOUS AND PASSED. lupa's _G contains none of the
    recon names, so every probe returned ABSENT (ok=0, absent=73), nothing was ever
    called, and "no stray calls" was true by construction -- a green that could not have
    gone red (CLAUDE.md #26). The fixture now PLANTS the real names as functions so the
    calls actually happen, and plants a DECOY in no list. If the verb ever reaches a
    global it was not given, the decoy fires and this fails.
    """
    rt = live()
    src = MOD_LUA.read_text(encoding="utf-8")
    listed = set()
    for block in re.findall(r"local RECON_\w+ = \{(.*?)\}", src, re.S):
        listed.update(re.findall(r'"(\w+)"', block))
    assert len(listed) > 50, f"only {len(listed)} names parsed out of the RECON lists"

    plant = chr(10).join(
        '_G["%s"] = function(...) _G.__called["%s"] = true return 1 end' % (n, n)
        for n in sorted(listed))
    rt.execute("_G.__called = {}" + chr(10) + plant + chr(10) +
               '_G.DECOY_NotInAnyList = function() _G.__called["DECOY"] = true end')

    r = ask(rt, 1, "recon")
    assert r.status == "OK", r.payload[:200]
    h = hdr(r)

    # NOT VACUOUS: real calls must have happened, or this proves nothing.
    assert int(h["ok"]) >= 50, f"only {h['ok']} calls succeeded; the fixture did not plant"
    called = set(g(rt, "__called").keys())
    assert len(called) >= 50, f"only {len(called)} names were actually invoked"

    assert "DECOY" not in called, (
        "recon called a global in NO hardcoded list -- it is not a fixed-vocabulary probe "
        "any more")
    stray = {c for c in called if c not in listed and c != "DECOY"}
    assert not stray, f"recon called globals outside its lists: {sorted(stray)}"


def test_recon_REFUSES_an_id_this_session_never_issued(lua_factory):
    """Same allowlist rule as `component`. A fabricated id is never handed to the engine,
    because vanilla never passes one and the behaviour on one is unknown both ways."""
    rt = live()
    r = ask(rt, 1, "recon", "999999ULL")
    assert r.status == "OK", r.payload[:200]
    assert any("SKIPPED" in f and "not issued" in f for f in r.fields), r.fields[:6]


def test_recon_header_accounts_for_every_call(lua_factory):
    """ok + raised + absent must equal called. A probe whose buckets do not sum has lost
    rows, and a lost row reads as 'the engine does not have that'."""
    rt = live()
    h = hdr(ask(rt, 1, "recon"))
    assert (int(h["ok"]) + int(h["raised"]) + int(h["absent"])) == int(h["called"]), h
    assert int(h["shown"]) <= int(h["called"]), h


# --- F88: a probe list keyed on ARGUMENT POSITION is keyed on nothing ---------- #
#
# Every regex below uses character classes rather than backslash escapes. Not style:
# backslashes collapse crossing a tool boundary, which corrupted this mod's own source
# earlier today (a lua "[|<tab><nl><cr>,]" written as real control bytes). Avoiding the
# construct is cheaper than remembering to check it.


def _issued(rt):
    """An id this session really issued, taken from the reply rather than invented."""
    r = ask(rt, 1, "player")
    f = dict(x.split("=", 1) for x in r.fields if "=" in x)
    return f["occupiedship"]


def test_GetMacroUnitStorageCapacity_is_NOT_in_the_object_id_list(lua_factory):
    """F88, structural half. It sat in RECON_OBJ and was called with an OBJECT id,
    returning `OK number:0` -- a benign-looking wrong answer with no error raised, and
    0 is indistinguishable from "this ship carries no units".

    MEASURED in recon-20260830-211605.tsv:
        GetMacroUnitStorageCapacity(33879739ULL)   OK   number:0
    Vanilla passes a MACRO (menu_map.lua:10461, :10468).

    ⚠ The explicit skip is load-bearing. `lua_factory` guards MOD_LUA is None, but only
    when it is CALLED -- this test takes the fixture and never invokes it, so on a
    fresh clone with no dev tree it sailed past the guard into `None.read_text()`.
    MEASURED by scripts/verify-cold.sh, which is the only run that has no dev tree.
    """
    if MOD_LUA is None:
        _require_mod_lua(None, _ROOTS, "game-side mod lua")
    src = MOD_LUA.read_text(encoding="utf-8")
    obj = re.search("local RECON_OBJ = [{](.*?)[}]", src, re.S).group(1)
    mac = re.search("local RECON_MACRO = [{](.*?)[}]", src, re.S).group(1)
    assert "GetMacroUnitStorageCapacity" not in obj, "still in the OBJECT-id list"
    assert "GetMacroUnitStorageCapacity" in mac


def test_recon_calls_the_macro_probe_with_a_MACRO_STRING_not_the_object_id(lua_factory):
    """F88, behavioural half -- the one that would actually have caught it.

    The structural test above passes the moment the name is moved between two lists.
    Only this one proves the ARGUMENT changed, which is the whole defect.
    """
    rt = live()
    oid = _issued(rt)
    rt.execute(
        "_G.__macro_args = {} "
        "_G.GetMacroUnitStorageCapacity = function(a) "
        "  _G.__macro_args[#_G.__macro_args+1] = tostring(a) return 42 end")

    r = ask(rt, 2, "recon", oid)
    assert r.status == "OK", r.payload[:200]
    args = list(g(rt, "__macro_args").values())
    assert args, "the macro probe was never called at all"
    # The harness stubs GetComponentData(id, key) -> "key-of-<id>", so this is the
    # exact value the vanilla hop yields -- not a shape check.
    assert args[0] == "macro-of-" + oid.replace("ULL", ""), args
    assert oid not in args, "STILL passing the object id -- F88 is not fixed"


def test_recon_SKIPS_the_macro_probe_rather_than_FALLING_BACK_to_the_id(lua_factory):
    """The falsification twin, and it targets a DIFFERENT clause than the test above.

    The guard is `mok AND type(macro)=='string' AND macro ~= ''`. The test above only
    ever exercises the success path, so a fallback that passed the object id when the
    hop failed would survive it untouched. Here the hop returns a non-string: the probe
    must NOT run, and the run must SAY so rather than quietly skipping.
    """
    rt = live()
    oid = _issued(rt)
    rt.execute(
        "_G.__macro_args = {} "
        "_G.GetMacroUnitStorageCapacity = function(a) "
        "  _G.__macro_args[#_G.__macro_args+1] = tostring(a) return 42 end "
        "_G.GetComponentData = function(id, key) return 12345 end")

    r = ask(rt, 2, "recon", oid)
    assert r.status == "OK", r.payload[:200]
    assert not list(g(rt, "__macro_args").values()), (
        "it fell back to calling the probe with a non-macro -- the exact F88 defect")
    assert any("macro-for:" in f and "SKIPPED" in f for f in r.fields), (
        "the skip is SILENT; an unrun probe must be visible in the output")
    assert hdr(r)["macros_resolved"] == "0"


# --- recon --contents: opt-in, bounded, scalar leaves only, one level --------- #


def _contents_rt(lua_payload):
    """A runtime whose GetStorageData returns `lua_payload`, plus an issued id."""
    rt = live()
    oid = _issued(rt)
    rt.execute("_G.GetStorageData = function(o) return " + lua_payload + " end")
    return rt, oid


def _storage_row(r):
    return [f for f in r.fields if f.startswith("GetStorageData(")][0]


def test_contents_is_OPT_IN_so_the_recorded_fixture_stays_comparable(lua_factory):
    """109 calls in recon-20260830-211605.tsv were recorded with the shape-only
    summary. Were contents the default, this run and that fixture would not be
    comparable while LOOKING like the same command."""
    rt, oid = _contents_rt("{alpha = 1, beta = 2}")
    r = ask(rt, 2, "recon", oid)
    row = _storage_row(r)
    assert "keys=2" in row
    assert "{" not in row, "contents leaked into the DEFAULT summary: " + row
    assert hdr(r)["contents"] == "no"


def test_contents_renders_scalar_leaves_when_asked(lua_factory):
    rt, oid = _contents_rt("{alpha = 1, beta = 'two', gamma = true}")
    r = ask(rt, 2, "recon", oid, "--contents")
    row = _storage_row(r)
    assert "alpha=1" in row and "beta=two" in row and "gamma=true" in row, row
    assert hdr(r)["contents"] == "yes"


def test_contents_does_NOT_descend_into_a_nested_table(lua_factory):
    """One level deep is what makes a cycle guard unnecessary rather than merely
    absent. A nested table must be VISIBLE as elided, never dropped."""
    rt, oid = _contents_rt("{outer = 1, inner = {secret = 99}}")
    r = ask(rt, 2, "recon", oid, "--contents")
    row = _storage_row(r)
    assert "inner=<table>" in row, row
    assert "secret" not in row, "it descended a level: " + row


def test_contents_TERMINATES_on_a_cyclic_table(lua_factory):
    """Why 'one level' is a safety property and not a convenience. If this ever starts
    recursing, this test HANGS rather than fails -- which is itself the signal."""
    rt = live()
    oid = _issued(rt)
    rt.execute("local t = {name = 'loop'} t.self = t "
               "_G.GetStorageData = function(o) return t end")
    r = ask(rt, 2, "recon", oid, "--contents")
    row = _storage_row(r)
    assert "self=<table>" in row and "name=loop" in row, row


def test_contents_is_BOUNDED_and_NAMES_what_it_omitted(lua_factory):
    """F74: an over-long reply does not truncate, it TEARS THE PIPE DOWN, costing the
    whole connection. So the bound is enforced here rather than detected afterwards --
    and a bounded cell must say how much it left out, because a silent stop reads as
    'that is all there was'."""
    big = "{" + ", ".join(
        "k%03d = 'vvvvvvvvvvvvvvvvvvvv'" % i for i in range(200)) + "}"
    rt, oid = _contents_rt(big)
    r = ask(rt, 2, "recon", oid, "--contents")
    row = _storage_row(r)
    assert "-more" in row, "bounded but silent about it: " + row[:200]
    omitted = int(re.search("[+]([0-9]+)-more", row).group(1))
    assert 0 < omitted < 200
    assert len(row) < 1200, "the bound did not hold: %d bytes" % len(row)


def test_contents_keys_are_SORTED_so_two_runs_are_DIFFABLE(lua_factory):
    """An engine table's pairs() order is not guaranteed stable. Unsorted, every diff
    between two harvests would look like a change."""
    rt, oid = _contents_rt("{zulu = 1, alpha = 2, mike = 3}")
    r = ask(rt, 2, "recon", oid, "--contents")
    row = _storage_row(r)
    body = row[row.index("{") + 1:row.index("}")]
    keys = [c.split("=")[0] for c in body.split(",")]
    assert keys == sorted(keys), keys


# --- deep contents: `macro --contents` and `recon --contents --deep` ------------ #
#
# WHY. The groundtruth harvest records `weapons=<table>` and `storagetags=<table>` for
# every ship (groundtruth-20260913-115336.tsv), so four of the eight "loadout join"
# fields were never readable at all. Two levels, bounded, opt-in -- and the renderer is
# SHARED with recon's one-level form, whose output must stay byte-identical.


def _lib(body):
    return live(GetLibraryEntry="function(lt, n) return " + body + " end")


def test_macro_DEFAULT_still_elides_a_nested_table(lua_factory):
    """The twin, and the compatibility guarantee: the `*` row a harvest stores verbatim
    must not change for anyone who does not ask."""
    r = ask(_lib("{hull = 1, weapons = {s = 2}}"), 1, "macro", "engine", "m")
    assert r.status == "OK", r.payload
    assert "weapons=<table>" in r.fields, r.fields
    assert "s=2" not in r.payload, r.payload


def test_macro_contents_is_a_FLAG_not_a_property_name(lua_factory):
    """Positional args put a trailing `--contents` in the <property> slot, where it
    would read as a lookup of a field literally named `--contents` -> ABSENT."""
    r = ask(_lib("{hull = 1, weapons = {s = 2}}"), 1, "macro", "engine", "m", "--contents")
    assert r.status == "OK", f"{r.status}: {r.payload}"
    assert "hull=1" in r.fields, r.fields


def test_macro_contents_RENDERS_two_levels_SORTED(lua_factory):
    r = ask(_lib("{hull = 1, weapons = {s = 2, m = {x = 1}}}"), 1,
            "macro", "engine", "m", "--contents")
    assert "weapons={m={x=1},s=2}" in r.fields, r.fields


def test_macro_contents_STOPS_at_two_levels(lua_factory):
    """Bounded depth is what makes a cycle guard unnecessary. The third level must be
    VISIBLE as elided, never dropped."""
    r = ask(_lib("{a = {b = {c = {d = 1}}}}"), 1, "macro", "engine", "m", "--contents")
    assert "a={b={c=<table>}}" in r.fields, r.fields
    assert "d=1" not in r.payload, r.payload


def test_macro_contents_TERMINATES_on_a_cyclic_table(lua_factory):
    """If this ever recurses without a bound, this test HANGS -- which is the signal."""
    rt = live()
    rt.execute("local t = {name = 'loop'} t.self = t "
               "_G.GetLibraryEntry = function(lt, n) return {root = t} end")
    r = ask(rt, 1, "macro", "engine", "m", "--contents")
    assert r.status == "OK", r.payload
    assert "self={name=loop,self={" not in r.payload, "rendered THREE levels: " + r.payload
    assert "root={name=loop,self={name=loop,self=<table>}}" in r.fields, r.fields


def test_macro_contents_keeps_the_FRAME_intact(lua_factory):
    """A leaf string carrying a tab or newline would split one field into several, and a
    comma would forge a sibling inside `{...}`. The field count is the check a
    substring cannot fake."""
    r = ask(_lib("{a = {s = 'x\\ty,z|w\\nq'}, b = 1}"), 1, "macro", "engine", "m",
            "--contents")
    assert r.status == "OK", r.payload
    assert len(r.fields) == 2, r.fields
    assert r.fields[0] == "a={s=x y z w q}", r.fields[0]
    # The KEY is scrubbed too, separately: a nested engine table may be keyed by strings.
    r = ask(_lib("{a = {['k,1\\t'] = 1}, b = 1}"), 2, "macro", "engine", "m", "--contents")
    assert len(r.fields) == 2, r.fields
    assert r.fields[0] == "a={k 1 =1}", r.fields[0]


def test_macro_contents_is_BOUNDED_and_NAMES_what_it_omitted(lua_factory):
    big = "{fat = {" + ", ".join(
        "k%03d = 'vvvvvvvvvvvvvvvvvvvv'" % i for i in range(200)) + "}}"
    r = ask(_lib(big), 1, "macro", "engine", "m", "--contents")
    cell = [f for f in r.fields if f.startswith("fat=")][0]
    assert "-more" in cell, "bounded but silent about it: " + cell[:200]
    omitted = int(re.search("[+]([0-9]+)-more", cell).group(1))
    assert 0 < omitted < 200
    assert len(cell) < 1200, "the bound did not hold: %d bytes" % len(cell)


# Review 2026-09-14, MEASURED: a 40-byte cut through a multibyte character makes the reply
# invalid UTF-8, and `_livepipe` decodes strictly -- the whole reply is lost, not one field.
# The file already has `utf8_prefix` for exactly this; these pin every cut that reaches a reply.
_EURO_AT_40 = "string.rep('x', 39) .. '\\226\\130\\172tail'"      # bytes 40-42 are one char


def test_contents_never_SPLITS_a_multibyte_character(lua_factory):
    r = ask(_lib("{a = {s = " + _EURO_AT_40 + "}}"), 1, "macro", "engine", "m", "--contents")
    assert r.status == "OK", r.payload
    assert r.fields[0] == "a={s=" + "x" * 39 + "..}", r.fields[0]


def test_recon_string_summary_never_SPLITS_a_multibyte_character(lua_factory):
    rt, oid = _contents_rt(_EURO_AT_40)
    r = ask(rt, 2, "recon", oid)
    assert "string:46:" + "x" * 39 in _storage_row(r), _storage_row(r)


def test_recon_RAISED_text_never_SPLITS_a_multibyte_character(lua_factory):
    rt = live()
    oid = _issued(rt)
    rt.execute("_G.GetStorageData = function(o) error(string.rep('y', 69) .. '\\226\\130\\172', 0) end")
    r = ask(rt, 2, "recon", oid)
    row = _storage_row(r)
    assert "|RAISED|" + "y" * 69 in row, row


def test_censusprobe_RAISED_text_never_SPLITS_a_multibyte_character(lua_factory):
    """The fourth cut through reply text (live_query's `:sub(1, 60)` on a RAISED message in
    censusprobe). Same shape as the three above, found by classifying every `:sub(1,` hit."""
    rt = _census_rt(["a"], ["a"])
    rt.execute("_G.GetContainedStations = function(c, flag) "
               "error(string.rep('y', 59) .. '\\226\\130\\172', 0) end")
    r = ask(rt, 2, "censusprobe", "ID: 4301")
    assert any("|RAISED|" + "y" * 59 in f for f in r.fields), r.fields


def test_contents_a_FAT_nested_table_does_not_hide_its_small_siblings(lua_factory):
    """Review 2026-09-14, MEASURED: an inner table that fills its OWN budget could never fit
    in its parent, so `{weapons={aaa=<60 strings>, zzz=1}}` rendered `weapons={} +2-more`.
    The inner render must be given the parent's REMAINING room instead.

    ⚠ The first version used 30-byte leaves; their packing slack let even a WHOLE-budget child
    leave room for `zzz=1`, and the mutant that removed the sharing SURVIVED it. Here 200
    one-byte leaves fill the child's room almost exactly and the sibling is a 20-byte string,
    so both fit only when the budget is really shared (391 of 400 bytes; 419 without)."""
    fat = "{" + ", ".join("k%03d = 'v'" % i for i in range(200)) + "}"
    r = ask(_lib("{weapons = {aaa = " + fat + ", zzz = '" + "s" * 20 + "'}}"), 1,
            "macro", "engine", "m", "--contents")
    cell = [f for f in r.fields if f.startswith("weapons=")][0]
    assert "zzz=" + "s" * 20 in cell, cell[-160:]
    assert "-more" in cell, "the fat table's own cut must still be announced"
    assert len(cell) < 1200, len(cell)


def test_recon_DEEP_descends_exactly_ONE_more_level(lua_factory):
    rt, oid = _contents_rt("{outer = 1, inner = {secret = 99, deeper = {gone = 1}}}")
    r = ask(rt, 2, "recon", oid, "--contents", "--deep")
    row = _storage_row(r)
    assert "inner={deeper=<table>,secret=99}" in row, row
    assert "gone" not in row, row
    assert hdr(r)["deep"] == "yes"


def test_recon_without_DEEP_says_so_and_stays_one_level(lua_factory):
    rt, oid = _contents_rt("{outer = 1, inner = {secret = 99}}")
    r = ask(rt, 2, "recon", oid, "--contents")
    assert "inner=<table>" in _storage_row(r)
    assert hdr(r)["deep"] == "no"


# --- ffisyms: the FFI census verb -- index ffi.C, never call, never declare ------ #
#
# MEASURED with lupa's LuaJIT 2.1 (2026-09-14): indexing ffi.C raises
#   "missing declaration for symbol 'X'"          when no cdef in the VM declares X
#   "cannot resolve symbol 'X': <os message>"     when X is declared but not exported
# and a second cdef of X with a DIFFERENT signature is SILENTLY IGNORED -- the first wins.
# So the verb must never declare: a placeholder that loaded first would corrupt the
# marshalling of a later real caller.

_SYM_ERRORS = ("{Undecl = \"missing declaration for symbol 'Undecl'\", "
               "NotExp = \"cannot resolve symbol 'NotExp': The specified procedure could not be found.\", "
               "Weird = 'something|else\\tentirely'}")


def _syms(r):
    return {f.split("|", 1)[0]: f.split("|", 1)[1] for f in r.fields[1:]}


def test_ffisyms_CLASSIFIES_every_name_and_the_buckets_SUM(lua_factory):
    rt = live(fake_C_errors=_SYM_ERRORS)
    r = ask(rt, 1, "ffisyms", "GetPlayerID", "Undecl", "NotExp", "Weird")
    assert r.status == "OK", r.payload
    rows = _syms(r)
    assert rows["GetPlayerID"].startswith("exported|"), rows
    assert rows["Undecl"] == "undeclared", rows
    assert rows["NotExp"] == "notexported", rows
    assert rows["Weird"].startswith("other|"), rows
    h = hdr(r)
    assert (h["asked"], h["exported"], h["notexported"], h["undeclared"], h["other"],
            h["invalid"]) == ("4", "1", "1", "1", "1", "0"), h
    assert len(r.fields) == 1 + 4, "one row per name, plus the header"


def test_ffisyms_keeps_the_FRAME_intact_for_an_OTHER_message(lua_factory):
    """An unrecognised error text is quoted back, and it may carry a tab or a pipe."""
    rt = live(fake_C_errors=_SYM_ERRORS)
    r = ask(rt, 1, "ffisyms", "Weird")
    assert len(r.fields) == 2, r.fields
    assert r.fields[1].count("|") == 2, r.fields[1]


def test_ffisyms_NEVER_CALLS_what_it_resolves(lua_factory):
    rt = live()
    rt.execute("_G.fake_C.Tripwire = function(...) _G.tripped = true end")
    r = ask(rt, 1, "ffisyms", "Tripwire")
    assert _syms(r)["Tripwire"].startswith("exported|"), r.payload
    assert g(rt, "tripped") is None, "the census CALLED an engine function"


def test_ffisyms_NEVER_DECLARES(lua_factory):
    rt = live(fake_C_errors=_SYM_ERRORS)
    before = len(g(rt, "cdefs"))
    r = ask(rt, 1, "ffisyms", "Undecl", "NotExp", "GetPlayerID")
    # NOT VACUOUS: an unknown verb declares nothing either, so first prove the census RAN.
    assert r.status == "OK" and hdr(r)["asked"] == "3", r.payload
    assert len(g(rt, "cdefs")) == before, "the census issued an ffi.cdef"


def test_ffisyms_marks_an_INVALID_name_and_does_not_index_it(lua_factory):
    rt = live()
    rt.execute("_G.fake_C_errors = setmetatable({}, {__index = function(_, k) "
               "_G.indexed = (_G.indexed or '') .. tostring(k) .. ';' end})")
    r = ask(rt, 1, "ffisyms", "bad|name", "1Leading", "GetPlayerID")
    assert r.status == "OK", r.payload
    assert hdr(r)["invalid"] == "2", r.fields[0]
    assert len(r.fields) == 1 + 3, r.fields
    assert "bad" not in (g(rt, "indexed") or ""), "an invalid name reached ffi.C"


def test_ffisyms_a_name_over_the_LENGTH_bound_is_INVALID(lua_factory):
    """The pattern alone accepts an identifier of any length, and every valid row echoes its
    name -- so without the length bound one request could push the reply past the cap."""
    rt = live()
    r = ask(rt, 1, "ffisyms", "A" * 101, "B" * 100)
    assert r.status == "OK", r.payload[:120]
    assert hdr(r)["invalid"] == "1", r.fields[0]
    assert r.fields[1] == "<invalid>|invalid", r.fields[1][:120]


def test_ffisyms_is_ERR_with_NO_names(lua_factory):
    r = ask(live(), 1, "ffisyms")
    assert r.status == "ERR", r.payload
    # NOT VACUOUS: an unknown verb is also ERR, so the refusal must be the verb's own.
    assert "needs one or more" in r.payload, r.payload


def test_ffisyms_REFUSES_more_names_than_it_will_bound(lua_factory):
    r = ask(live(), 1, "ffisyms", *["N%d" % i for i in range(151)])
    assert r.status == "ERR", r.payload[:120]
    assert "150" in r.payload


def test_ffisyms_is_ERR_when_ffi_is_unavailable(lua_factory):
    rt = live(_build(pre="_G.no_ffi = true"))
    r = ask(rt, 1, "ffisyms", "GetPlayerID")
    assert r.status == "ERR", r.payload
    # "ffi" alone would match the verb's own NAME in an unknown-verb reply.
    assert "ffi is not available" in r.payload, r.payload


def test_ffisyms_reply_stays_BOUNDED_at_the_maximum(lua_factory):
    from x4validate import _livepipe as lp
    long_msg = "'" + "x" * 500 + "'"
    rt = live(fake_C_errors="setmetatable({}, {__index = function(_, k) return " + long_msg + " end})")
    names = [("S%03d" % i) + "a" * 95 for i in range(150)]
    r = ask(rt, 1, "ffisyms", *names)
    assert r.status == "OK", r.payload[:120]
    assert lp.byte_len(r.payload) <= 32000, lp.byte_len(r.payload)
    assert hdr(r)["asked"] == "150"


# --- censusprobe: the SECOND cause of the census caveat, measured per item ----- #


def _census_rt(stations_all, stations_by_owner):
    """A runtime where the two station enumerators return controlled id lists."""
    rt = live()
    ask(rt, 1, "player")
    def _lua_list(ids):
        return "{" + ", ".join("'%s'" % i for i in ids) + "}"
    rt.execute(
        "_G.GetContainedStations = function(c, flag) return " + _lua_list(stations_all) + " end "
        "_G.GetContainedStationsByOwner = function(fac, c) "
        "  if fac == 'argon' then return " + _lua_list(stations_by_owner) + " end return {} end "
        "_G.GetContainedShips = function(c, flag) return {} end "
        "_G.GetContainedShipsByOwner = function(fac, c) return {} end "
        "_G.GetContainedSpacesByOwner = function(fac, c) return {} end "
        "_G.GetContainedBuildStoragesByOwner = function(fac, c) return {} end")
    return rt


def _cmp_row(r, a="GetContainedStations", flag="true"):
    return [f for f in r.fields if f.startswith("CMP:" + a + ":" + flag)][0]


def test_censusprobe_REFUSES_a_bad_sector_token(lua_factory):
    """F85: an unrecognised container is SILENTLY IGNORED and the engine hands back the
    whole galaxy labelled as your sector -- MEASURED 310 vs 1961. A census verb that
    accepted a stale token would report a galaxy-wide count as a sector count, which is
    the worst possible failure for this specific verb. Sector tokens change EVERY
    launch, so a stale one is the normal case."""
    rt = _census_rt([], [])
    r = ask(rt, 2, "censusprobe", "NOT_A_SECTOR")
    assert r.status == "ERR", r.payload[:160]
    assert "SILENTLY IGNORED" in r.payload


def test_censusprobe_accepts_the_shape_the_engine_actually_uses(lua_factory):
    """The twin of the guard above, targeting the OTHER branch. Without it the verb
    could refuse everything and the test above would still pass."""
    rt = _census_rt(["a"], ["a"])
    r = ask(rt, 2, "censusprobe", "ID: 4301")
    assert r.status == "OK", r.payload[:200]
    assert hdr(r)["sector"] == "ID:4301"


def test_censusprobe_compares_SETS_not_totals(lua_factory):
    """★ The property the verb exists for. Two totals can agree while naming DIFFERENT
    objects -- a count is exactly the aggregate a real difference hides in. Here both
    sides have THREE ids and the sets differ on two of them; a count comparison would
    report perfect agreement."""
    rt = _census_rt(["a", "b", "x"], ["a", "b", "y"])
    r = ask(rt, 2, "censusprobe", "ID: 4301", "argon")
    row = _cmp_row(r)
    assert "n_container=3" in row and "n_owner=3" in row, row
    assert "container_only=1" in row, "equal totals hid a real difference: " + row
    assert "owner_only=1" in row, row


def test_censusprobe_REFUSES_a_vacuous_comparison_when_no_factions_resolve(lua_factory):
    """★ Found by a test going red for the right reason, and it fails TOWARDS the
    conclusion we are hunting. With an empty faction list the owner-scoped side is empty
    by construction, so every container-scoped id lands in `container_only` and the row
    reads 'the non-owner call sees N objects the per-faction walk missed' -- this verb's
    target finding, manufactured out of nothing. It must refuse instead."""
    rt = live()
    ask(rt, 1, "player")
    rt.execute(
        "_G.GetContainedStations = function(c, f) return {'a', 'b'} end "
        "_G.GetContainedStationsByOwner = function(fac, c) return {} end "
        "_G.GetContainedShips = function(c, f) return {} end "
        "_G.GetContainedShipsByOwner = function(fac, c) return {} end "
        "_G.GetContainedSpacesByOwner = function(fac, c) return {} end "
        "_G.GetContainedBuildStoragesByOwner = function(fac, c) return {} end")
    r = ask(rt, 2, "censusprobe", "ID: 4301")
    assert r.status == "OK", r.payload[:200]
    assert hdr(r)["factions"] == "0"
    row = _cmp_row(r)
    assert "SKIPPED" in row and "vacuously empty" in row, (
        "it reported a manufactured difference as a finding: " + row)
    assert "container_only=2" not in row


def test_censusprobe_reports_FOUR_ZEROES_as_a_real_answer(lua_factory):
    """A row of zeroes must be reachable and legible: it is the finding 'the
    non-owner-scoped call sees nothing the per-faction walk missed', which is a real
    result and the one that would retire this line of enquiry."""
    rt = _census_rt(["a", "b"], ["a", "b"])
    r = ask(rt, 2, "censusprobe", "ID: 4301", "argon")
    row = _cmp_row(r)
    assert "container_only=0" in row and "owner_only=0" in row, row


def test_censusprobe_probes_BOTH_booleans_and_LABELS_the_guessed_one(lua_factory):
    """Vanilla passes `true` at all 7 of its call sites and never `false`, so `false` is
    a GUESS. It gets probed -- that is the point -- but it must not be recorded in the
    same grammar as the attested one."""
    rt = _census_rt(["a"], ["a"])
    r = ask(rt, 2, "censusprobe", "ID: 4301", "argon")
    rows = [f for f in r.fields if f.startswith("GetContainedStations(")]
    assert len(rows) == 2, rows
    assert any("true)" in x and "vanilla-attested" in x for x in rows), rows
    assert any("false)" in x and "GUESSED" in x for x in rows), rows


def test_censusprobe_records_an_ABSENT_function_rather_than_crashing(lua_factory):
    """Four of the five names have ZERO vanilla call sites. 'It exists in _G' is not
    evidence that calling it is safe, and F88 was a wrong-argument call that answered
    `OK number:0`. An absent or raising probe is a RECORDED RESULT, never a failure of
    the run."""
    rt = _census_rt(["a"], ["a"])
    rt.execute("_G.GetContainedSpacesByOwner = nil")
    r = ask(rt, 2, "censusprobe", "ID: 4301", "argon")
    assert r.status == "OK", r.payload[:200]
    assert any(f.startswith("GetContainedSpacesByOwner|ABSENT") for f in r.fields), r.fields


# --- the two verbs that never capped (2026-09-02) -----------------------------
#
# MEASURED against this harness before the fix: verbs.ext replied with 40,025 bytes
# and verbs.macro with 40,017, against MAX_PAYLOAD 32,000 -- status OK, nothing
# capped, no `shown=` anywhere. Three shipped claims said otherwise, including the
# user-facing content.xml description: "every enumeration caps its own reply and
# reports how many rows it omitted, because an over-long message does not truncate:
# it tears the pipe down."
#
# grep -n ROW_BUDGET found it at five call sites, none of them these two verbs, and
# neither verb had a single test at any size.

_BIG_LIB = ('function(lt, n) local t = {} for i = 1, 2000 do '
            't["key" .. i] = string.rep("D", 40) end return t end')


def test_verbs_macro_CAPS_its_reply(lua_factory):
    rt = live(GetLibraryEntry=_BIG_LIB)
    r = ask(rt, 1, "macro", "engine", "some_macro")
    from x4validate import _livepipe as lp
    assert r.status == "OK", r.payload
    assert lp.byte_len(r.payload) <= 32000, lp.byte_len(r.payload)


def test_verbs_macro_SAYS_how_much_it_omitted(lua_factory):
    """A cap that does not report is a step that narrows its data and reports success."""
    rt = live(GetLibraryEntry=_BIG_LIB)
    r = ask(rt, 1, "macro", "engine", "some_macro")
    assert "__total=2000" in r.payload, r.payload[-120:]
    shown = [f for f in r.payload.split(chr(9)) if f.startswith("__shown=")]
    assert shown, "capped without saying how many were shown"
    assert 0 < int(shown[0].split("=")[1]) < 2000


def test_an_UNCAPPED_macro_reply_carries_NO_marker(lua_factory):
    """The twin, and it is also a compatibility guarantee: an ordinary reply must be
    byte-identical to before the cap existed, because _livecli stores this payload
    verbatim as the `*` row and it feeds the oracle."""
    rt = live(GetLibraryEntry='function(lt, n) return {alpha = 1, beta = 2} end')
    r = ask(rt, 1, "macro", "engine", "some_macro")
    assert r.status == "OK", r.payload
    assert "__shown=" not in r.payload and "__total=" not in r.payload, r.payload


# --- verbs.echo: the one verb that MUST be able to exceed MAX_PAYLOAD ------------
#
# It had ZERO tests. `x4live ramp` drives it to find where the TRANSPORT gives out, so a
# cap on it makes the ramp report OUR OWN limit as the engine's -- MEASURED: last_ok fell
# from 524288 to 16384 and the tool printed "the ceiling lies in (16384, 32768]" and
# exited 0. `_livepipe._BUF` was raised 64 KiB -> 1 MiB expressly to reach 524288.


def test_verbs_echo_may_EXCEED_max_payload(lua_factory):
    """The whole point of the verb. 65536 is over MAX_PAYLOAD and under ECHO_MAX."""
    from x4validate import _livepipe as lp
    r = ask(live(), 1, "echo", "65536")
    assert r.status == "OK", f"{r.status}: {r.payload[:120]}"
    assert lp.byte_len(r.payload) == 65536, lp.byte_len(r.payload)


def test_verbs_echo_reaches_the_TOP_of_the_ramp(lua_factory):
    """524288 is the largest size RAMP_SIZES asks for. If this ERRs, `x4live ramp`
    stops early and reports a ceiling that is ours rather than the game's."""
    from x4validate import _livepipe as lp
    r = ask(live(), 1, "echo", "524288")
    assert r.status == "OK", f"{r.status}: {r.payload[:120]}"
    assert lp.byte_len(r.payload) == 524288


def test_verbs_echo_still_refuses_beyond_ECHO_MAX(lua_factory):
    """The twin. `unbounded` removes the reply() choke point for this verb, so the
    verb's OWN limit is the only thing left -- if that stopped working, echo would try
    to write an arbitrarily large frame and tear the pipe down."""
    r = ask(live(), 1, "echo", str(1024 * 1024 + 1))
    assert r.status == "ERR", f"{r.status}: {r.payload[:120]}"
    assert "ECHO_MAX" in r.payload, r.payload[:160]


def test_every_OTHER_verb_still_hits_the_reply_cap(lua_factory):
    """The twin that matters most: `unbounded` must be opt-in per call, not a hole. A
    fat single property still becomes an ERR naming MAX_PAYLOAD rather than being sent."""
    rt = live(GetLibraryEntry='function(lt, n) return {fat = string.rep("D", 40000)} end')
    r = ask(rt, 1, "macro", "engine", "some_macro", "fat")
    assert r.status == "ERR", f"{r.status}: {r.payload[:120]}"
    assert "MAX_PAYLOAD" in r.payload, r.payload[:160]


#: An extension record with enough fields to blow the budget. Same shape as _BIG_LIB,
#: but GetExtensionList returns a LIST of records and verbs.ext caps the fields of the
#: one whose id matches -- so the fat has to be inside the matching record.
_BIG_EXT = ('function() local e = {id = "ws_1"} for i = 1, 2000 do '
            'e["key" .. i] = string.rep("D", 40) end return {e} end')


def test_verbs_ext_CAPS_its_reply(lua_factory):
    """MEASURED before the cap: 40,025 bytes against MAX_PAYLOAD 32,000, status OK,
    nothing capped. An over-long frame does not truncate -- it tears the pipe down in
    both directions."""
    rt = live(GetExtensionList=_BIG_EXT)
    r = ask(rt, 1, "ext", "ws_1")
    from x4validate import _livepipe as lp
    assert r.status == "OK", r.payload[:200]
    assert lp.byte_len(r.payload) <= 32000, lp.byte_len(r.payload)


def test_verbs_ext_SAYS_how_much_it_omitted(lua_factory):
    """A cap that does not report is a step that narrows its data and reports success --
    and without the denominator the caller cannot tell a short list from a full one."""
    rt = live(GetExtensionList=_BIG_EXT)
    r = ask(rt, 1, "ext", "ws_1")
    assert "__total=2001" in r.payload, r.payload[-120:]
    shown = [f for f in r.payload.split(chr(9)) if f.startswith("__shown=")]
    assert shown, "capped without saying how many were shown"
    assert 0 < int(shown[0].split("=")[1]) < 2001


def test_an_UNCAPPED_ext_reply_carries_NO_marker(lua_factory):
    """The twin, and a compatibility guarantee: an ordinary reply must be byte-identical
    to before the cap existed, because _livecli stores these payloads verbatim."""
    rt = live(GetExtensionList='function() return {{id = "ws_1", name = "n", version = "1"}} end')
    r = ask(rt, 1, "ext", "ws_1")
    assert r.status == "OK", r.payload
    assert "__shown=" not in r.payload and "__total=" not in r.payload, r.payload


def test_verbs_ext_still_answers_ABSENT_for_an_unknown_id(lua_factory):
    """The cap must not change what a MISS looks like. ABSENT is an answer here -- the
    engine was asked and this id is not in its list -- and it is a different fact from
    ERR, which means the question could not be put."""
    rt = live(GetExtensionList='function() return {{id = "other"}} end')
    r = ask(rt, 1, "ext", "ws_1")
    assert r.status == "ABSENT", f"{r.status}: {r.payload[:80]}"


def test_a_single_oversized_PROPERTY_becomes_an_ERR_not_a_pipe_teardown(lua_factory):
    """The reply() choke point. The single-property branch calls reply directly with
    no cap of its own, so a fat value reaches it whole -- and an over-long frame does
    not truncate, it tears the pipe down in both directions."""
    rt = live(GetLibraryEntry='function(lt, n) return {fat = string.rep("D", 40000)} end')
    r = ask(rt, 1, "macro", "engine", "some_macro", "fat")
    assert r.status == "ERR", f"{r.status}: {r.payload[:80]}"
    assert "reply too large" in r.payload, r.payload[:120]
    from x4validate import _livepipe as lp
    assert lp.byte_len(r.payload) <= 32000


def test_an_ordinary_PROPERTY_still_comes_back_OK(lua_factory):
    """The twin: a guard that ERRed on everything would pass the test above."""
    rt = live(GetLibraryEntry='function(lt, n) return {thin = "ok"} end')
    r = ask(rt, 1, "macro", "engine", "some_macro", "thin")
    assert r.status == "OK", r.payload
    assert r.payload == "ok", r.payload


def test_a_pcall_FAILURE_is_ERR_and_keeps_the_message(lua_factory):
    """"We could not ask" must never be reported as "the engine has nothing here".
    _livecli counts ABSENT as a real answer, so a harvest that lost answers this way
    still balanced -- and the result becomes a groundtruth fixture."""
    rt = live(GetLibraryEntry='function(lt, n) error("boom from the engine") end')
    r = ask(rt, 1, "macro", "engine", "some_macro")
    assert r.status == "ERR", f"{r.status}: {r.payload}"
    assert "boom from the engine" in r.payload, r.payload


def test_a_GENUINE_absence_is_still_ABSENT(lua_factory):
    """The twin. Blanket-changing ABSENT to ERR would make a macro that genuinely does
    not exist indistinguishable from one we failed to ask about."""
    rt = live(GetLibraryEntry='function(lt, n) return nil end')
    r = ask(rt, 1, "macro", "engine", "some_macro")
    assert r.status == "ABSENT", f"{r.status}: {r.payload}"


# --- the ENUMERATION bound, on the three verbs that never reached it ---------- #
#
# `MAX_ENUMERATE` has existed since the round-6 finding, and it lived entirely inside
# `render`. `compare`, `containerprobe` and `censusprobe` do not call `render`, so the
# bound was never on any of their paths -- MEASURED offline at N=5,000: 30,008 and
# 25,012 engine calls respectively, in the ONE frame pipes.lua drains the FIFO in.
#
# Each of the three gets a DIFFERENT mechanism, and the differences are the point:
# capping a comparison is not a performance fix, it is a way of manufacturing the
# finding the comparison exists to look for.

BOUND = 20000


def _n_engine_reads(rt):
    """Count GetComponentData calls, which is what read_flags costs (~5 fields, 1
    call) and therefore what the bound is really about. Wrapping the global is the
    only honest way to assert WORK rather than OUTPUT."""
    rt.execute(
        "_G.__reads = 0 "
        "local real = _G.GetComponentData "
        "_G.GetComponentData = function(...) _G.__reads = _G.__reads + 1 "
        "  return real(...) end")
    return lambda: int(g(rt, "__reads"))


def test_compare_REFUSES_above_the_bound_rather_than_truncating(lua_factory):
    """★ The CRITICAL half. `compare` walked both lists uncapped inside one frame.

    It must REFUSE, not cap. The two lists feed a single set difference, so truncating
    either side drops objects that then read as `old_only`/`new_only` -- the exact
    method difference the verb exists to detect, manufactured out of a performance
    fix. That is the same hazard censusprobe's `#factions == 0` guard already refuses
    on, and the same contract `reply` applies to an over-budget frame.
    """
    rt = live(n_stations=0, n_ships=BOUND + 1,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "'
                             + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {}}')
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "ERR", (
        "a comparison it cannot answer soundly came back as an answer: %s"
        % r.payload[:250])
    assert "ENUM_REFUSED" in r.payload, r.payload[:250]
    assert "wide=%d" % (BOUND + 1) in r.payload, (
        "the refusal must name the numbers, or the caller cannot act on it: %s"
        % r.payload[:250])
    assert "limit=%d" % BOUND in r.payload, r.payload[:250]


def test_the_compare_refusal_COSTS_NO_GetComponentData_CALLS(lua_factory):
    """The refusal is only a fix if it happens BEFORE the expensive part.

    ⚠ RENAMED 2026-09-08. It was "..._AND_NOTHING_ELSE", which claims more than it
    checks: this counts GetComponentData calls only, and at the refusal the verb
    still allocates `ffi.new("UniverseID[?]", N)` and runs N tostring() dedupe
    inserts in gather.take -- MEASURED 9 ms at N=20,001 and 24 ms at N=60,000,
    scaling linearly. The assertion is correct and worth having; the NAME was the
    part a future reader would trust.

    A refusal printed after the walk would be a message, not a bound -- the frame is
    already gone. So this asserts the WORK, not the wording: `read_flags` is ~1
    GetComponentData call per object, and above the bound there must be none of them.
    """
    rt = live(n_stations=0, n_ships=BOUND + 1,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "'
                             + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {}}')
    reads = _n_engine_reads(rt)
    before = reads()
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "ERR", r.payload[:200]
    walked = reads() - before
    assert walked == 0, (
        "the refusal fired only after walking %d objects, so it cost the frame it was "
        "meant to save" % walked)


def test_compare_AT_the_bound_still_ANSWERS_and_DOES_the_work(lua_factory):
    """The twin, and it is the one that can go red the other way.

    Two claims in one, because either alone is satisfiable by a broken bound: at the
    limit the verb must still answer (so the guard is `>`, not `>=`, and has not
    simply swallowed the verb), AND it must actually have walked the objects (so a
    refusal that returned OK with an empty comparison could not pass this).
    """
    rt = live(n_stations=0, n_ships=BOUND,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "'
                             + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {}}')
    reads = _n_engine_reads(rt)
    before = reads()
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "OK", r.payload[:250]
    assert hdr(r)["old"] == str(BOUND), hdr(r)
    assert reads() - before >= BOUND, (
        "it answered without reading the objects, so the OK above proves nothing")


def test_containerprobe_SKIPS_ONLY_the_class_walk_and_KEEPS_the_rest(lua_factory):
    """The other CRITICAL half, and it declines a BLOCK rather than the verb.

    Q3 is one block of a probe whose other answers -- symbol reachability, the wire
    round-trip, Q2's three counts -- are cheap and already computed. Refusing the
    whole verb would throw those away for no reason.

    And it must not CAP: `class.stations_engine_control` is an independent engine
    count over the WHOLE list, so a truncated walk disagrees with it by construction
    and the block reports a class-discrimination defect that is really just the cap.
    """
    ids = "{" + ", ".join('"o%d"' % i for i in range(BOUND + 1)) + "}"
    rt = live(component_data=SECT,
              contained='{["' + LUAKEY + '"] = ' + ids + '}',
              contained_stations='{argon = {"o0"}}')
    f = cprobe(rt)
    assert "class.SKIPPED" in f, sorted(k for k in f if k.startswith("class"))
    assert str(BOUND + 1) in f["class.SKIPPED"] and str(BOUND) in f["class.SKIPPED"], (
        "the skip must name n and the limit: %s" % f["class.SKIPPED"])
    assert "class.rows_walked" not in f, (
        "it said SKIPPED and walked anyway: %s" % f.get("class.rows_walked"))
    # The cheap half of the probe is the reason this is a block-level skip.
    assert f["n.sector_via_luaid"] == str(BOUND + 1), f["n.sector_via_luaid"]
    assert f["sym.IsComponentClass"] == "function", f["sym.IsComponentClass"]
    assert "wire.roundtrip" in f, sorted(f)


def test_containerprobe_BELOW_the_bound_still_WALKS_and_does_not_say_SKIPPED(lua_factory):
    """The twin. A probe that skipped unconditionally would pass the test above, and
    Q3 is the question containerprobe exists to answer."""
    rt = live(component_data=SECT,
              contained='{["' + LUAKEY + '"] = {"A", "B", "C"}}',
              obj_class='{A = "ship", B = "ship", C = "station"}',
              contained_stations='{argon = {"C"}}')
    f = cprobe(rt)
    assert f["class.rows_walked"] == "3", f.get("class.rows_walked")
    assert "class.SKIPPED" not in f, f["class.SKIPPED"]


def test_censusprobe_DECLARES_what_it_ENUMERATED(lua_factory):
    """The MINOR half, and the mechanism is deliberately NOT a bound.

    Each entry here is ONE engine call returning a whole list, so there is nothing to
    cap without denying the query outright -- and `censusprobe -` is the operator
    asking for the galaxy on purpose. What was missing is that the cost never reached
    the wire at all: a bare `censusprobe -` is 4 + 4x#factions galaxy-wide
    enumerations in a single frame, and nothing said so.
    """
    rt = _census_rt(["a", "b"], ["a"])
    r = ask(rt, 2, "censusprobe", "ID: 4301")
    assert r.status == "OK", r.payload[:200]
    cost = [f for f in r.fields if f.startswith("COST|")]
    assert len(cost) == 1, [f.split("|")[0] for f in r.fields]
    assert "scope=container" in cost[0], cost[0]
    assert "enumerations=" in cost[0] and "objects_touched=" in cost[0], cost[0]


def test_the_declared_cost_MOVES_with_the_query(lua_factory):
    """A number that cannot change is decoration. Two runs over different populations
    must report different costs, or the row says nothing about this query.

    Asserted as the DIRECTION and the exact delta, not merely inequality -- that is
    what distinguishes a real count from a constant that happens to differ.

    ⚠ The multiplier is x2, not x1, and it is pinned here on purpose. Every
    CENSUS_CONTAINER function is enumerated with BOTH flag values, so one station is
    two objects touched: the work really is done twice, and a reader who took
    `objects_touched` for a headcount would read the cost as half what it is. I
    predicted +3 for three more stations and MEASURED +6.
    """
    def cost_of(stations_all, stations_by_owner, sector="ID: 4301"):
        rt = _census_rt(stations_all, stations_by_owner)
        r = ask(rt, 2, "censusprobe", sector)
        assert r.status == "OK", r.payload[:200]
        row = [f for f in r.fields if f.startswith("COST|")][0]
        return dict(kv.split("=", 1) for kv in row.split("|")[2].split(" ") if "=" in kv)

    small = cost_of(["a"], ["a"])
    big = cost_of(["a", "b", "c", "d"], ["a"])
    assert small["enumerations"] == big["enumerations"] == "4", (small, big)
    assert int(small["objects_touched"]) == 2, (
        "one station, enumerated under both flags: %s" % small)
    assert int(big["objects_touched"]) - int(small["objects_touched"]) == 6, (small, big)


def test_the_galaxy_scope_is_LABELLED_as_such(lua_factory):
    """The twin for `scope=`. It is the field that tells a reader whether the cost
    above was one sector or the whole galaxy, which is the entire difference between
    a cheap census and an expensive one."""
    rt = _census_rt(["a"], ["a"])
    r = ask(rt, 2, "censusprobe", "-")
    assert r.status == "OK", r.payload[:200]
    cost = [f for f in r.fields if f.startswith("COST|")][0]
    assert "scope=galaxy" in cost, cost


def test_compare_ISSUES_the_ids_it_prints_so_component_can_follow_them(lua_factory):
    """`compare` used `wire()` where `render` and `censusprobe` use `issue()`.

    `wire` canonicalises; `issue` canonicalises AND records the id in the allowlist.
    So every id `compare` PRINTED was absent from `issued_ids`, and feeding one
    straight back to `component` was refused with "most likely mistyped, or left over
    from before a UI reload". Both named causes are false: the tool printed the id one
    query earlier.

    That matters more than a wrong message. `compare` exists to name the objects the
    two enumerations disagree about -- its own comment calls those rows THE DIAGNOSIS
    -- so refusing to follow them, and blaming the operator for it, defeats the verb.
    """
    rt = live(n_stations=0, n_ships=3,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "'
                             + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {}}')
    r = ask(rt, 1, "compare", "argon", TOKEN)
    assert r.status == "OK", r.payload[:250]

    # THE ID, not the field that carries it: a row reads `old_only=100002ULL`, so
    # splitting on "|" alone yields the prefix too and `component` correctly refuses
    # a token that is not an id. My first version asserted against that and read as
    # a failure of the fix rather than of the extraction.
    ids = [tok.rsplit("=", 1)[-1]
           for f in r.fields for tok in f.split("|") if tok.endswith("ULL")]
    assert ids, (
        "compare reported no object ids at all, so this test cannot say whether the "
        "ones it prints are followable: %s" % r.payload[:250])

    follow = ask(rt, 2, "component", ids[0])
    assert follow.status == "OK", (
        "compare printed id %s and `component` then refused it -- the tool blaming the "
        "operator for an id it issued one query earlier: %s" % (ids[0], follow.payload[:250]))


def test_ONE_unconvertible_object_does_not_discard_the_WHOLE_enumeration(lua_factory):
    """`wire` RAISES on an id at or above 2^53, and that raise was contained only by
    the verb's single outer pcall -- so one bad object discarded every good row and
    every denominator with it.

    MEASURED before the fix, 5 objects one of which sits at 2^53:
        objects <sector>  ->  ERR enumeration raised: ... cannot be represented exactly
    against a control of shown=5 matched=5 enumerated=5.

    The 2^53 enforcement is correct; the CONTAINMENT was at the wrong granularity.
    `read_flags` already had the right shape -- per object, counted, announced -- and
    this now matches it. Asserted on the SURVIVORS and on the disclosure, because a
    fix that silently dropped the bad object would be the narrowing step this file
    exists to refuse.
    """
    rt = live(n_stations=0, n_ships=4,
              component_data='{classid = "ship", realclassid = "ship", sectorid = "'
                             + TOKEN + '"}',
              contained='{["' + LUAKEY + '"] = {}}')
    # one object whose id cannot be represented exactly as a lua number
    rt.execute("_G.__bad_id = 9007199254740992")
    r = ask(rt, 1, "objects", TOKEN)
    assert r.status == "OK", (
        "a single unconvertible object aborted the whole enumeration: %s"
        % r.payload[:250])
    assert "enumerated=" in r.payload, r.payload[:250]


def test_a_UI_RELOAD_RE_REGISTERS_and_the_GAME_side_is_UNKNOWN(lua_factory):
    """Each UI reload adds one load-hook listener IN THE HARNESS. Pinned, not fixed.

    The code's defence against this is "_G ITSELF DOES NOT PERSIST", which is MEASURED
    for `_G` and NOT for the engine's own UI event registry -- a claim about one
    subject wearing the evidence of another. MEASURED here: 1 -> 5 over four reloads.

    ⚠ WHAT THIS TEST IS. It pins the HARNESS's model, in which the fake registry
    persists by construction, so a green here is evidence about lupa and not about X4.
    The real-engine behaviour is UNKNOWN. If the engine's registry also persists, two
    alt-enters mean one `ping` answered three times and two stale frames queued --
    exactly the FIFO desync the reply path spends thirty lines preventing.

    ⚠ AND `sched_reads` IS NOT THE STRUCTURE TO ASSERT ON. It is a CALL LOG: the fake
    appends on every Schedule_Read, so it grows across reloads whatever the code does.
    My first version of this test asserted against it and went red over the RECORDER
    rather than the defect. The review that raised this quotes both numbers side by
    side, and only the listener table carries information.

    THE SETTLEMENT IS FIVE SECONDS IN GAME AND IS NOT DONE: alt-enter twice, send one
    `ping`, count the frames that come back. Until then this stays recorded rather
    than repaired -- changing arming behaviour on an unverified model is how a fix
    becomes the next defect.
    """
    rt = live()
    key = "Lua_Loader.Send_Priority_Ready"
    before = len(g(rt, "ui_events")[key])
    assert before >= 1, "the fixture never registered the load hook, so this asserts nothing"
    for _ in range(4):
        reload_ui(rt)
    after = len(g(rt, "ui_events")[key])
    assert after == before + 4, (
        "the harness model changed: load-hook listeners went %d -> %d over four "
        "reloads, where one per reload is what this pins. If arming was made "
        "idempotent that is good news, and this test is the thing to update -- with "
        "the in-game check finally run." % (before, after))


# --------------------------------------------------------------------------- #
# THE WRITE PATH -- `pausestate` (a read) and `pause` / `unpause` (the first writes)
# --------------------------------------------------------------------------- #
#
# The contract, each clause pinned below by its own test:
#   * a write verb lives in WRITE_VERBS, never in `verbs`, so "is this a write?" is a
#     table lookup rather than a naming convention;
#   * every write reply LEADS with an advisory row whose first token is `write=yes`,
#     enforced once in reply() rather than remembered per verb;
#   * the verb reports what the engine READS BACK, never its own intention;
#   * ownership, not toggling: `unpause` undoes only a pause THIS chunk made, which is
#     vanilla's own discipline (gamemodified.lua sets menu.paused before Pause() and
#     calls Unpause() only if it is set).

def pause_externally(rt):
    """The player's pause, or another mod's: engine state true, ownership NOT ours.

    Deliberately NOT `ask(rt, n, "pause")` -- routing through our own verb would set the
    ownership flag, and the refusal under test could never fire."""
    rt.execute("_G.game_paused = true")


def unpause_externally(rt):
    rt.execute("_G.game_paused = false")


def adv(r):
    """The advisory row as a dict, ASSERTING that it leads the payload.

    Positional and token-exact on purpose: a substring check would pass on a reply that
    merely mentions writing somewhere in its prose."""
    first = r.fields[0].split(" ") if r.fields else [""]
    assert first[0] == "write=yes", (
        f"a write reply must lead with its advisory row: {r.payload[:200]!r}")
    return dict(kv.split("=", 1) for kv in first if "=" in kv)


def test_pause_PAUSES_and_reports_what_the_engine_READS_BACK():
    rt = live()
    r = ask(rt, 1, "pause")
    assert r.status == "OK", r.payload
    a = adv(r)
    assert (a["verb"], a["reason"], a["acted"], a["before"], a["after"], a["want"],
            a["agree"], a["owner"]) == (
        "pause", "ok", "yes", "false", "true", "true", "yes", "us")
    assert g(rt, "pause_calls") == 1 and g(rt, "game_paused") is True
    assert g(rt, "pause_nargs") == 0, "Pause must be called BARE"


def test_pause_reports_the_READ_BACK_not_its_own_intention():
    """The engine accepts the call and nothing changes. A verb that reported its
    intention would say after=true here."""
    rt = live(pause_is_noop="true")
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "OK", r.payload
    assert (a["reason"], a["acted"], a["after"], a["agree"]) == (
        "disagree", "yes", "false", "no")


def test_a_pause_whose_READ_BACK_DISAGREES_claims_NO_ownership():
    rt = live(pause_is_noop="true")
    ask(rt, 1, "pause")
    pause_externally(rt)
    r = ask(rt, 2, "unpause")
    assert r.status == "ERR"
    assert adv(r)["reason"] == "not-ours"
    assert g(rt, "unpause_calls") == 0


def test_pause_REFUSES_when_the_game_is_ALREADY_paused():
    rt = live()
    pause_externally(rt)
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["before"], a["owner"], a["agree"]) == (
        "already-paused", "no", "true", "other", "unknown"), (
        "a refusal must not report agree=yes: nothing acted, so there is nothing to agree")
    assert g(rt, "pause_calls") == 0


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_a_write_verb_with_ANY_argument_is_REFUSED(verb):
    """A write verb has no argument surface at all, so nothing a caller types can change
    what it does."""
    rt = live()
    if verb == "unpause":
        assert ask(rt, 9, "pause").status == "OK"
    calls = (g(rt, "pause_calls"), g(rt, "unpause_calls"))
    r = ask(rt, 1, verb, "now")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"]) == ("args", "no")
    assert (g(rt, "pause_calls"), g(rt, "unpause_calls")) == calls


@pytest.mark.parametrize("pre", ['_G.cdef_raises_on = "IsGamePaused"', "_G.no_ffi = true"])
def test_pause_REFUSES_when_it_cannot_READ_BACK_because_ffi_did_not_load(pre):
    """A write that cannot be verified is never performed."""
    rt = live(rt=_build(pre=pre))
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["before"]) == ("unreadable", "no", "unknown")
    assert g(rt, "pause_calls") == 0


def test_a_failed_IsGamePaused_declaration_leaves_the_READ_verbs_working():
    """The pause verbs' declaration is guarded SEPARATELY. Folded into the shared cdef
    block, one bad line would take `player`, `stations`, `ships` and `objects` down too."""
    rt = live(rt=_build(pre='_G.cdef_raises_on = "IsGamePaused"'))
    r = ask(rt, 1, "player")
    assert r.status == "OK", r.payload[:200]


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_a_write_REFUSES_when_IsGamePaused_is_NOT_EXPORTED(verb):
    rt = live()
    if verb == "unpause":
        assert ask(rt, 9, "pause").status == "OK"
    rt.execute("_G.fake_C.IsGamePaused = nil")
    r = ask(rt, 1, verb)
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["before"]) == ("unreadable", "no", "unknown")
    assert (g(rt, "pause_calls"), g(rt, "unpause_calls")) == (
        (1, 0) if verb == "unpause" else (0, 0))


def test_a_write_REFUSES_when_IsGamePaused_answers_a_NON_BOOLEAN():
    """`1 == true` is false in lua, so without the type check a non-boolean answer would
    read as "not paused" and the verb would act on it."""
    rt = live()
    rt.execute("_G.fake_C.IsGamePaused = function() return 1 end")
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["before"]) == ("unreadable", "unknown")
    assert g(rt, "pause_calls") == 0


@pytest.mark.parametrize("verb,name", [("pause", "Pause"), ("unpause", "Unpause")])
def test_a_write_REFUSES_when_its_engine_global_is_ABSENT(verb, name):
    rt = live()
    if verb == "unpause":
        assert ask(rt, 9, "pause").status == "OK"
    rt.execute(f"{name} = nil")
    r = ask(rt, 1, verb)
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"]) == ("no-engine-call", "no")
    assert name in r.payload


def test_a_RAISING_engine_call_is_ERR_WITH_a_read_back():
    """It may have raised AFTER acting, so the state is read back regardless."""
    rt = live(pause_raises="true")
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["after"]) == ("raised", "yes", "false")
    assert "engine refused to pause" in r.payload


def test_unpause_UNPAUSES_a_pause_THIS_CHUNK_made():
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    r = ask(rt, 2, "unpause")
    assert r.status == "OK", r.payload
    a = adv(r)
    assert (a["verb"], a["reason"], a["acted"], a["before"], a["after"], a["want"],
            a["agree"], a["owner"]) == (
        "unpause", "ok", "yes", "true", "false", "false", "yes", "none")
    assert g(rt, "unpause_calls") == 1 and g(rt, "game_paused") is False
    assert g(rt, "unpause_nargs") == 0, "Unpause must be called BARE"


def test_unpause_REFUSES_a_pause_it_did_not_make():
    rt = live()
    pause_externally(rt)
    r = ask(rt, 1, "unpause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["owner"]) == ("not-ours", "no", "other")
    assert g(rt, "unpause_calls") == 0 and g(rt, "game_paused") is True


def test_unpause_REFUSES_when_the_game_is_NOT_paused():
    rt = live()
    r = ask(rt, 1, "unpause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["before"], a["agree"]) == (
        "not-paused", "no", "false", "unknown")
    assert g(rt, "unpause_calls") == 0


def test_a_successful_unpause_RELEASES_ownership():
    """After our pause is undone, the NEXT pause is somebody else's."""
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    assert ask(rt, 2, "unpause").status == "OK"
    pause_externally(rt)
    r = ask(rt, 3, "unpause")
    assert r.status == "ERR" and adv(r)["reason"] == "not-ours"
    assert g(rt, "game_paused") is True and g(rt, "unpause_calls") == 1


def test_after_a_UI_RELOAD_unpause_REFUSES_the_previous_chunks_pause():
    """A reload re-creates the chunk; the pause survives engine-side and the new chunk
    cannot prove it made it. Inventing ownership from `before=true` would let the
    channel undo the player's pause, which is the failure ownership exists to prevent."""
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    reload_ui(rt)
    r = ask(rt, 2, "unpause")
    assert r.status == "ERR" and adv(r)["reason"] == "not-ours"
    assert g(rt, "game_paused") is True and g(rt, "unpause_calls") == 0


def test_a_STALE_ownership_flag_is_CLEARED_when_the_engine_shows_the_pause_is_gone():
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    unpause_externally(rt)           # the player undid OUR pause by hand
    r = ask(rt, 2, "unpause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["ownerreset"]) == ("not-paused", "yes")
    pause_externally(rt)             # ...and later paused again: THEIR pause now
    r = ask(rt, 3, "unpause")
    assert r.status == "ERR" and adv(r)["reason"] == "not-ours"
    assert g(rt, "unpause_calls") == 0 and g(rt, "game_paused") is True


def test_an_unpause_whose_READ_BACK_DISAGREES_RELEASES_ownership_and_never_retries():
    """The engine still reads paused after our Unpause(). Vanilla's menus each call
    Pause() on show and Unpause() on close without checking, so the engine either
    COUNTS pauses or lets them undo each other -- UNMEASURED which. If it counts, the
    pause still holding is somebody ELSE's (an open menu), and a retry would undo it.
    So the claim is released and the channel will not unpause again: never undo a
    pause this channel cannot prove it made."""
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    rt.execute("_G.unpause_is_noop = true")
    r = ask(rt, 2, "unpause")
    a = adv(r)
    assert r.status == "OK", r.payload
    assert (a["reason"], a["acted"], a["after"], a["agree"], a["owner"]) == (
        "disagree", "yes", "true", "no", "other")
    rt.execute("_G.unpause_is_noop = false")
    r = ask(rt, 3, "unpause")
    assert r.status == "ERR" and adv(r)["reason"] == "not-ours", (
        "after a disagreeing unpause the channel must not retry")
    assert g(rt, "unpause_calls") == 1 and g(rt, "game_paused") is True


def test_a_write_verb_that_RAISES_still_answers_with_an_ADVISORY_led_frame():
    rt = live(rt=_build(extra='WRITE_VERBS.__boom = function(seq) error("kaboom") end'))
    r = ask(rt, 1, "__boom")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["verb"], a["reason"], a["acted"]) == ("__boom", "unwrapped", "unknown")
    assert "kaboom" in r.payload


def test_a_write_reply_WITHOUT_its_advisory_is_WRAPPED_and_marked_untrusted():
    """The invariant is enforced in reply(), once. A write verb that forgets its header
    still cannot produce a frame whose first field is not the advisory."""
    rt = live(rt=_build(
        extra='WRITE_VERBS.__forgetful = function(seq) reply(seq, "OK", "done") end'))
    r = ask(rt, 1, "__forgetful")
    a = adv(r)
    assert r.status == "ERR", "an unwrapped write reply must not stay OK"
    assert (a["reason"], a["acted"]) == ("unwrapped", "unknown")
    assert "done" in r.payload


@pytest.mark.parametrize("first", ["pause", "__boom"])
def test_the_write_flag_is_CLEARED_so_the_next_READ_is_not_wrapped(first):
    rt = live(rt=_build(extra='WRITE_VERBS.__boom = function(seq) error("kaboom") end'))
    ask(rt, 1, first)
    r = ask(rt, 2, "ping")
    assert r.status == "OK" and r.fields[0] == "pong", r.payload[:120]


def test_a_verb_in_BOTH_tables_is_REFUSED_and_neither_runs():
    rt = live(rt=_build(extra=(
        'verbs.pause = function(seq) _G.__read_pause_ran = true; '
        'reply(seq, "OK", "read") end')))
    r = ask(rt, 1, "pause")
    assert r.status == "ERR"
    assert g(rt, "pause_calls") == 0
    assert g(rt, "__read_pause_ran") is None


def test_the_write_verbs_live_ONLY_in_WRITE_VERBS():
    rt = _build(extra=(
        "_G.__t = {r_pause = verbs.pause ~= nil, r_unpause = verbs.unpause ~= nil, "
        "r_state = verbs.pausestate ~= nil, w_pause = WRITE_VERBS.pause ~= nil, "
        "w_unpause = WRITE_VERBS.unpause ~= nil, w_state = WRITE_VERBS.pausestate ~= nil}"))
    assert dict(g(rt, "__t").items()) == {
        "r_pause": False, "r_unpause": False, "r_state": True,
        "w_pause": True, "w_unpause": True, "w_state": False}


def test_the_lua_and_python_WRITE_VERB_lists_are_the_SAME_set():
    """Without this a verb added game-side would reach `x4live query` unrefused."""
    from x4validate import _livecli

    rt = _build(extra=(
        "_G.__w = {} for k in pairs(WRITE_VERBS) do _G.__w[#_G.__w + 1] = k end"))
    assert set(g(rt, "__w").values()) == {"pause", "unpause"}
    assert set(_livecli.WRITE_VERBS) == {"pause", "unpause"}


def test_every_write_ATTEMPT_is_logged_even_a_refused_one():
    rt = live()
    pause_externally(rt)
    ask(rt, 1, "pause")
    lines = [s for s in g(rt, "log").values() if "X4TOOLKIT_LIVE WRITE" in s]
    # BOTH lines, each pinned separately: the dispatcher's ATTEMPT line (it is the only
    # one a write verb that raises before finishing ever leaves) and the RESULT line.
    # Asserting "some WRITE line mentions pause" let either one be deleted unnoticed.
    assert any("WRITE pause seq=1" in s for s in lines), lines
    assert any("WRITE pause result: reason=already-paused" in s for s in lines), lines


def test_pausestate_READS_state_and_ownership_WITHOUT_writing():
    rt = live()
    r = ask(rt, 1, "pausestate")
    assert r.status == "OK", r.payload
    assert not r.fields[0].startswith("write=")
    assert (hdr(r)["paused"], hdr(r)["owner"]) == ("false", "none")
    assert ask(rt, 2, "pause").status == "OK"
    h = hdr(ask(rt, 3, "pausestate"))
    assert (h["paused"], h["owner"]) == ("true", "us")
    assert ask(rt, 4, "unpause").status == "OK"
    pause_externally(rt)
    h = hdr(ask(rt, 5, "pausestate"))
    assert (h["paused"], h["owner"]) == ("true", "other")
    assert (g(rt, "pause_calls"), g(rt, "unpause_calls")) == (1, 1)


def test_pausestate_is_ERR_not_a_guess_when_the_state_cannot_be_read():
    rt = live()
    rt.execute("_G.fake_C.IsGamePaused = nil")
    r = ask(rt, 1, "pausestate")
    assert r.status == "ERR", r.payload
    # NOT VACUOUS: an unknown verb is also ERR, so the refusal must name what it could
    # not read. The first version of this test passed before the verb existed.
    assert "IsGamePaused" in r.payload, r.payload


def test_a_write_whose_state_CANNOT_BE_READ_BACK_after_acting_is_UNVERIFIED():
    """The call returned, but the read-back vanished: nothing can say what happened."""
    rt = live()
    rt.execute("local real = Pause\n"
               "Pause = function(...) real(...); _G.fake_C.IsGamePaused = nil end")
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["after"], a["agree"], a["owner"]) == (
        "unverified", "yes", "unknown", "unknown", "unknown")
    assert "pausestate" in r.payload


def test_a_call_that_RAISES_AFTER_pausing_still_owns_the_pause_it_made():
    """The read-back shows the pause landed, taken from running to paused by our own
    single-threaded call, so it IS ours. Losing the claim here would strand a pause this
    channel made and could no longer undo."""
    rt = live()
    rt.execute('Pause = function() _G.game_paused = true; error("late failure") end')
    r = ask(rt, 1, "pause")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"], a["after"], a["owner"]) == ("raised", "yes", "true", "us")
    r = ask(rt, 2, "unpause")
    assert r.status == "OK" and adv(r)["agree"] == "yes", r.payload


@pytest.mark.parametrize("body,reason", [
    ('string.rep("x", 40000)', "unwrapped"),
    ('"write=yes verb=__huge reason=ok acted=yes\\t" .. string.rep("x", 40000)', "too-large"),
], ids=["no-advisory", "advisory-led"])
def test_an_OVERSIZED_write_reply_still_LEADS_with_its_advisory(body, reason):
    """The advisory wrap and the size cap both live in reply(). Neither may strip what
    the other guarantees: a write frame always leads with its advisory, and always fits."""
    rt = live(rt=_build(extra=(
        'WRITE_VERBS.__huge = function(seq) reply(seq, "OK", %s) end' % body)))
    r = ask(rt, 1, "__huge")
    a = adv(r)
    assert r.status == "ERR"
    assert (a["reason"], a["acted"]) == (reason, "unknown")
    assert len(r.payload.encode("utf-8")) <= 32000


@pytest.mark.parametrize("mode", ["raised", "unverified"])
def test_an_unpause_that_RAISED_or_went_UNVERIFIED_still_RELEASES_the_claim(mode):
    """Every unpause ATTEMPT releases ownership, not only one whose read-back disagrees:
    if we cannot confirm our pause is gone, some other hold may be what remains."""
    rt = live()
    assert ask(rt, 1, "pause").status == "OK"
    if mode == "raised":
        rt.execute('Unpause = function() error("engine refused") end')
    else:
        rt.execute("local real = Unpause\n"
                   "Unpause = function(...) _G.fake_C.IsGamePaused = nil end")
    r = ask(rt, 2, "unpause")
    a = adv(r)
    assert r.status == "ERR" and a["reason"] == mode
    assert "released" in r.payload.lower(), "the reply must say the claim was given up"
    rt.execute("_G.fake_C.IsGamePaused = function() return _G.game_paused end\n"
               "Unpause = function() _G.game_paused = false end")
    r = ask(rt, 3, "unpause")
    assert r.status == "ERR" and adv(r)["reason"] == "not-ours", r.payload
    assert "gave up" in r.payload, "not-ours must name an earlier unpause as a possible cause"


def test_the_wrap_never_SPLITS_a_multibyte_character():
    """The host decodes strictly. A 2000-byte cut landing inside a UTF-8 sequence would
    turn the whole frame into a non-answer and lose the advisory with it."""
    body = '"\\xc3\\xa9" .. string.rep("\\xc3\\xa9", 1500)'  # 3002 bytes of two-byte chars, offset 0
    rt = live(rt=_build(extra=(
        'WRITE_VERBS.__wide = function(seq) reply(seq, "OK", "x" .. %s) end' % body)))
    r = ask(rt, 1, "__wide")   # ask() decodes strictly: a split character raises here
    a = adv(r)
    assert r.status == "ERR" and a["reason"] == "unwrapped"
    assert "bytes cut" in r.payload

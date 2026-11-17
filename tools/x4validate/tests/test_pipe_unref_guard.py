"""The helper's F124 pipe unref guard, tested against its REAL code.

WHAT IT GUARDS (BLIND-SPOTS F124, root cause measured 2026-11-16): the Mod Support APIs
pipe DLL never initialises the `callback` ref of a File userdata (+0x10), and
File:close() hands that garbage to luaL_unref, which overwrites X4's Lua registry. The
helper wraps the pipes module's Connect_Pipe and writes LUA_NOREF (-2) into +0x10 of
every newly opened file on ITS OWN pipe names, behind a version gate and a structural
check that refuse (no write) on any mismatch.

TWO LAYERS, because they answer different questions:

  * the unit tests below run the guard block extracted verbatim from live_query.lua in a
    real LuaJIT (lupa) with the REAL ffi. The File objects are real userdata whose
    payload is redirected to a 0x30-byte ffi buffer, so every pointer read and the write
    are real memory operations on the exact offsets the DLL uses;
  * `test_offline_*` load the GAME's lua51_64.dll and the installed pipe DLL in a child
    process (no game) and run >= 200 open/close cycles through the same block, checking
    the registry directly. They skip when that install is not on this machine.

Every guard clause has a twin that differs from the passing case in that one clause
only, so deleting the clause turns exactly that test red (CLAUDE.md #26).
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

lupa = pytest.importorskip("lupa.luajit21", reason="lua runtime; dev-only check of the game-side mod")

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _mod_lua() -> pathlib.Path:
    for name in ("mods", "dev"):
        hits = sorted((_ROOT / name).glob("*/ui/*live_query.lua")) if (_ROOT / name).is_dir() else []
        if hits:
            return hits[0]
    roots = [str(_ROOT / n) for n in ("mods", "dev") if (_ROOT / n).is_dir()]
    if roots:
        raise AssertionError(f"searched {roots} and found no live_query.lua: broken layout")
    pytest.skip("no mods/ or dev/ root in this tree")


_BLOCK_RE = re.compile(r"^--@@ PIPE_UNREF_GUARD_BEGIN\n(.*?)^--@@ PIPE_UNREF_GUARD_END",
                       re.S | re.M)


def guard_block() -> str:
    m = _BLOCK_RE.search(_mod_lua().read_text(encoding="utf-8"))
    assert m, "live_query.lua lost its PIPE_UNREF_GUARD markers"
    return m.group(1)


SETUP = r"""
local block = ...
local chunk = assert(load(block .. "\nreturn install_pipe_unref_guard, new_pipe_unref_guard"))
install, new_guard = chunk()
local real = require("ffi")
local bufs = setmetatable({}, {__mode = "k"})
-- Real ffi, except that casting one of OUR fake files to void* yields its 0x30 buffer,
-- the way LuaJIT yields a real userdata's payload.
fake_ffi = setmetatable({cast = function(ct, v)
    if ct == "void*" and type(v) == "userdata" and bufs[v] then return real.cast("void*", bufs[v]) end
    return real.cast(ct, v)
end}, {__index = real})

function nop() end
function make_file(o)
    o = o or {}
    -- winpipe's File metatable shape: __index = itself, read/write/close/__gc. `o.mt`
    -- replaces it wholesale (a refusal twin); `o.nomt` gives a bare userdata.
    local u = newproxy(not o.nomt)
    if not o.nomt then
        local mt = getmetatable(u)
        local spec = o.mt or {read = nop, write = nop, close = nop}
        for k, v in pairs(spec) do mt[k] = v end
        if spec.__index == nil then mt.__index = mt end
        if spec.__index == false then mt.__index = nil end
    end
    local b = real.new("uint8_t[48]")
    local ptr = real.cast("void**", b)
    ptr[0] = real.cast("void*", o.handle or 0x100)
    ptr[1] = real.cast("void*", o.L or 0x200)
    real.cast("int32_t*", b + 0x10)[0] = o.ref or 12345
    ptr[3] = real.cast("void*", o.buf or 0x300)
    real.cast("int32_t*", b + 0x20)[0] = o.bufsz or 2048
    bufs[u] = b
    return u
end
function ref_of(u) return real.cast("int32_t*", bufs[u] + 0x10)[0] end

-- pipes.lua's shape: L.pipes[name].file, opened by L.Connect_Pipe when nil.
function make_module(file_opts)
    local m = {pipes = {}, calls = 0, opts = file_opts}
    m.Connect_Pipe = function(name, extra)
        m.calls = m.calls + 1
        if m.raise then error("open_pipe returned nil for " .. tostring(name), 0) end
        m.pipes[name] = m.pipes[name] or {}
        if m.pipes[name].file == nil then m.pipes[name].file = make_file(m.opts) end
        return "orig-return", extra
    end
    return m
end

logs = {}
GOOD_EXT = function() return {{id = "ws_2042901274", version = "1.95"}, {id = "other", version = "1.0"}} end
function deps(o)
    o = o or {}
    local f = fake_ffi
    if o.noffi then f = nil end
    return {ffi = f, own = {x4live = true}, extensions = o.extensions or GOOD_EXT,
            log = function(s) logs[#logs + 1] = tostring(s) end}
end
function inactive_logs()
    local n = 0
    for _, s in ipairs(logs) do if s:find("pipe unref guard INACTIVE", 1, true) then n = n + 1 end end
    return n
end
"""


@pytest.fixture
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.eval("function(src, block) local c = assert(load(src)); c(block) end")(SETUP, guard_block())
    return rt


def run(rt, code):
    return rt.execute(code)


# --------------------------------------------------------------------------- #
# the write, and who gets it
# --------------------------------------------------------------------------- #

def test_without_the_guard_the_ref_stays_garbage(lua):
    """The control: the instrument can see an unstamped file. Without it, a ref of -2
    after the guard could be the fake's own value."""
    assert run(lua, "local m = make_module(); m.Connect_Pipe('x4live'); return ref_of(m.pipes.x4live.file)") == 12345


def test_the_guard_stamps_OUR_pipe_with_LUA_NOREF(lua):
    ref, status, n = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.Connect_Pipe('x4live')
        return ref_of(m.pipes.x4live.file), g.status(), g.stamped()""")
    assert (ref, status, n) == (-2, "active", 1)


def test_a_FOREIGN_pipe_passes_through_untouched(lua):
    ref, status, n, calls = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.Connect_Pipe('some_other_mod')
        return ref_of(m.pipes.some_other_mod.file), g.status(), g.stamped(), m.calls""")
    assert (ref, status, n, calls) == (12345, "armed", 0, 1)


def test_the_wrapper_returns_what_the_original_returns(lua):
    a, b = run(lua, "local m = make_module(); install(m, deps()); return m.Connect_Pipe('x4live', 'extra')")
    assert (a, b) == ("orig-return", "extra")


def test_an_error_from_the_original_propagates_unchanged_and_nothing_is_stamped(lua):
    ok, msg, n = run(lua, """
        local m = make_module(); m.raise = true; local g = install(m, deps())
        local ok, msg = pcall(m.Connect_Pipe, 'x4live')
        return ok, msg, g.stamped()""")
    assert (ok, msg, n) == (False, "open_pipe returned nil for x4live", 0)


def test_each_NEW_file_is_stamped_once_and_an_open_one_is_not_rewritten(lua):
    n1, n2, n3, ref = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.Connect_Pipe('x4live'); local a = g.stamped()
        m.Connect_Pipe('x4live'); m.Connect_Pipe('x4live'); local b = g.stamped()
        m.pipes.x4live.file = nil            -- Disconnect_Pipe
        m.Connect_Pipe('x4live'); local c = g.stamped()
        return a, b, c, ref_of(m.pipes.x4live.file)""")
    assert (n1, n2, n3, ref) == (1, 1, 2, -2)


def test_install_is_IDEMPOTENT(lua):
    same, same_fn, calls, n = run(lua, """
        local m = make_module()
        local g1 = install(m, deps()); local w1 = m.Connect_Pipe
        local g2 = install(m, deps()); local w2 = m.Connect_Pipe
        m.Connect_Pipe('x4live')
        return g1 == g2, w1 == w2, m.calls, g1.stamped()""")
    assert (same, same_fn, calls, n) == (True, True, 1, 1)


# --------------------------------------------------------------------------- #
# the version gate: refuses with no write, logs once
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext, reason", [
    ("function() return {{id='ws_2042901274', version='1.96'}} end",
     "inactive:Mod Support APIs version 1.96 is not the measured 1.95"),
    ("function() return {{id='other', version='1.95'}} end",
     "inactive:Mod Support APIs (ws_2042901274) listed 0 times"),
    ("function() return {{id='ws_2042901274', version='1.95'}, {id='ws_2042901274', version='1.95'}} end",
     "inactive:Mod Support APIs (ws_2042901274) listed 2 times"),
    ("function() error('GetExtensionList absent', 0) end",
     "inactive:extension list unavailable: GetExtensionList absent"),
    ("function() return 'nope' end", "inactive:extension list is string"),
])
def test_the_version_gate_REFUSES_with_no_write(lua, ext, reason):
    ref, ref2, status, n, nlog = run(lua, f"""
        local m = make_module(); local g = install(m, deps({{extensions = {ext}}}))
        m.Connect_Pipe('x4live'); local r1 = ref_of(m.pipes.x4live.file)
        m.pipes.x4live.file = nil; m.Connect_Pipe('x4live')
        return r1, ref_of(m.pipes.x4live.file), g.status(), g.stamped(), inactive_logs()""")
    assert (ref, ref2, n) == (12345, 12345, 0)
    assert status == reason
    assert nlog == 1, "the INACTIVE line must be logged exactly once"


# --------------------------------------------------------------------------- #
# the structural check: one twin per clause, each differing in that clause only
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("opts, reason", [
    ("{handle = 0}", "inactive:handle at +0x00 is NULL"),
    ("{L = 0}", "inactive:lua_State at +0x08 is NULL"),
    ("{buf = 0}", "inactive:buffer at +0x18 is NULL"),
    ("{bufsz = 4096}", "inactive:bufsz at +0x20 is 4096, not 2048"),
])
def test_each_structural_clause_REFUSES_with_no_write(lua, opts, reason):
    ref, status, n, nlog = run(lua, f"""
        local m = make_module({opts}); local g = install(m, deps())
        m.Connect_Pipe('x4live')
        return ref_of(m.pipes.x4live.file), g.status(), g.stamped(), inactive_logs()""")
    assert (ref, status, n, nlog) == (12345, reason, 0, 1)


def test_once_refused_a_later_GOOD_file_is_not_written_either(lua):
    """A layout mismatch means the DLL is not the one measured: stay off for the session."""
    ref, status = run(lua, """
        local m = make_module({bufsz = 1}); local g = install(m, deps())
        m.Connect_Pipe('x4live')
        m.pipes.x4live.file = nil; m.opts = nil   -- next open has the GOOD layout
        m.Connect_Pipe('x4live')
        return ref_of(m.pipes.x4live.file), g.status()""")
    assert ref == 12345
    assert status == "inactive:bufsz at +0x20 is 1, not 2048"


def test_without_ffi_it_REFUSES(lua):
    ref, status = run(lua, """
        local m = make_module(); local g = install(m, deps({noffi = true}))
        m.Connect_Pipe('x4live')
        return ref_of(m.pipes.x4live.file), g.status()""")
    assert (ref, status) == (12345, "inactive:ffi unavailable")


def test_a_file_that_is_not_a_userdata_is_REFUSED(lua):
    status = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.pipes.x4live = {file = {}}
        m.Connect_Pipe('x4live')
        return g.status()""")
    assert status == "inactive:pipe file is a table"


# --------------------------------------------------------------------------- #
# install-time refusals and later replacement
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("module, reason", [
    ("{pipes = {}}", "inactive:Connect_Pipe absent"),
    ("{Connect_Pipe = function() end}", "inactive:pipes state table absent"),
    ("nil", "inactive:pipes module is a nil"),
])
def test_an_install_refusal_is_logged_only_by_report_and_only_once(lua, module, reason):
    before, after, again, status = run(lua, f"""
        local g = install({module}, deps())
        local before = inactive_logs()
        g.report(); local after = inactive_logs()
        g.report(); return before, after, inactive_logs(), g.status()""")
    assert (before, after, again, status) == (0, 1, 1, reason)


def test_a_REPLACED_Connect_Pipe_is_reported_without_disabling_the_guard(lua):
    """Review M1: status() used to refuse() here, so one probe switched stamping off for
    the session even when another mod had only WRAPPED ours. Now it reports and moves on:
    a wrapper on top of ours still reaches ours, and that file is still stamped."""
    s_armed, s_active, n, ref, again = run(lua, """
        local m = make_module(); local g = install(m, deps())
        local ours = m.Connect_Pipe
        m.Connect_Pipe = function(name, ...) return ours(name, ...) end   -- another mod wraps ours
        local s1 = g.status()
        m.Connect_Pipe('x4live')
        return s1, g.status(), g.stamped(), ref_of(m.pipes.x4live.file), inactive_logs()""")
    assert (s_armed, s_active, n, ref, again) == (
        "armed;wrapper-replaced", "active;wrapper-replaced", 1, -2, 0)


def test_status_is_plain_while_our_wrapper_is_still_installed(lua):
    """The twin for the replaced suffix: no replacement, no suffix."""
    assert run(lua, "local m = make_module(); local g = install(m, deps()); return g.status()") == "armed"


# --------------------------------------------------------------------------- #
# the File type check (review M2): runs before any structural read
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("opts, reason", [
    ("{nomt = true}", "inactive:pipe file metatable is a nil"),
    ("{mt = {read = nop, write = nop, close = nop, __index = false}}", "inactive:pipe file __index is a nil"),
    ("{mt = {write = nop, close = nop}}", "inactive:pipe file has no read"),
    ("{mt = {read = nop, close = nop}}", "inactive:pipe file has no write"),
    ("{mt = {read = nop, write = nop}}", "inactive:pipe file has no close"),
    ("{mt = {read = nop, write = nop, close = nop, seek = nop}}", "inactive:pipe file has a foreign method seek"),
])
def test_each_file_type_clause_REFUSES_with_no_write(lua, opts, reason):
    ref, status, n, nlog = run(lua, f"""
        local m = make_module({opts}); local g = install(m, deps())
        m.Connect_Pipe('x4live')
        return ref_of(m.pipes.x4live.file), g.status(), g.stamped(), inactive_logs()""")
    assert (ref, status, n, nlog) == (12345, reason, 0, 1)


def test_a_FOREIGN_userdata_with_read_write_close_is_REFUSED(lua):
    """A real io file has read, write and close too; its extra methods give it away,
    before any structural read of its payload."""
    status, n = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.pipes.x4live = {file = io.tmpfile()}
        m.Connect_Pipe('x4live')
        return g.status(), g.stamped()""")
    assert status.startswith("inactive:pipe file has a foreign method ") and n == 0, status


def test_without_getmetatable_it_REFUSES(lua):
    status = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.pipes.x4live = {file = make_file()}       -- made while getmetatable still exists
        local saved = getmetatable; getmetatable = nil
        local ok, err = pcall(m.Connect_Pipe, 'x4live')
        getmetatable = saved
        return g.status()""")
    assert status == "inactive:getmetatable unavailable"


def test_a_file_type_check_that_RAISES_is_a_named_refusal(lua):
    status = run(lua, """
        local m = make_module(); local g = install(m, deps())
        m.pipes.x4live = {file = make_file()}
        local saved = getmetatable; getmetatable = function() error("boom", 0) end
        pcall(m.Connect_Pipe, 'x4live')
        getmetatable = saved
        return g.status()""")
    assert status == "inactive:file type check raised: boom"


def test_a_refused_install_marks_the_table_so_a_second_install_point_reuses_it(lua):
    """Review M3: the start-menu and world install points both report(); without the
    mark the second guard would refuse and log a second INACTIVE line."""
    same, nlog = run(lua, """
        local t = {pipes = {}}
        local g1 = install(t, deps()); g1.report()
        local g2 = install(t, deps()); g2.report()
        return g1 == g2, inactive_logs()""")
    assert (same, nlog) == (True, 1)


# --------------------------------------------------------------------------- #
# OFFLINE INTEGRATION: the game's lua51_64.dll + the real pipe DLL, no game
# --------------------------------------------------------------------------- #

_PIPE_DLL_SIZE = 92160  # the build measured in F124 (PE timestamp 1602049405)

DRIVER = r'''
import ctypes, ctypes.wintypes as W, os, sys
game, mode, n, block_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
name = "x4ut_unref_%d" % os.getpid()
full = "\\\\.\\pipe\\" + name
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateNamedPipeW.restype = W.HANDLE
k32.CreateNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p]
hs = []
for _ in range(n + 2):
    h = k32.CreateNamedPipeW(full, 3, 4 | 2 | 1, 255, 65536, 65536, 0, None)
    if h in (None, W.HANDLE(-1).value):
        print("SERVE_FAILED", ctypes.get_last_error()); sys.exit(2)
    hs.append(h)
os.add_dll_directory(game)
lua = ctypes.CDLL(os.path.join(game, "lua51_64.dll"))
lua.luaL_newstate.restype = ctypes.c_void_p
lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lua.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
lua.lua_tolstring.restype = ctypes.c_char_p
lua.lua_pushstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
L = lua.luaL_newstate(); lua.luaL_openlibs(L)
script = r"""
local N, NAME, DLL, BLOCK, MODE = ...
N = tonumber(N)
local ffi = require("ffi")
local wp = assert(package.loadlib(DLL, "luaopen_winpipe"))()
local install = assert(loadstring(BLOCK .. "\nreturn install_pipe_unref_guard"))()
local reg = debug.getregistry()
reg[1] = "SLOT1"; reg[2] = "SLOT2"; reg[3] = "SLOT3"; reg[4] = "SLOT4"
-- pipes.lua's Connect_Pipe / Disconnect_Pipe shape, against the REAL winpipe.
local m = {pipes = {}}
function m.Connect_Pipe(name)
    m.pipes[name] = m.pipes[name] or {}
    if m.pipes[name].file == nil then
        if MODE == "iofile" then m.pipes[name].file = io.tmpfile()   -- a FOREIGN userdata
        else m.pipes[name].file = wp.open_pipe("\\\\.\\pipe\\" .. NAME) end
        if m.pipes[name].file == nil then error("open_pipe returned nil for " .. name) end
    end
end
local function disconnect(name)
    local f = m.pipes[name].file
    pcall(function() f:close() end)
    m.pipes[name].file = nil
end
local logs = {}
local g
if MODE ~= "noguard" then
    g = install(m, {ffi = ffi, own = {[NAME] = true},
        log = function(s) logs[#logs + 1] = s end,
        extensions = function() return {{id = "ws_2042901274", version = "1.95"}} end})
end
local use = (MODE == "foreign") and ("foreign_" .. NAME) or NAME
local function snap() local s = {} for k = 0, 4 do s[k] = tostring(reg[k]) end return s end
local base = snap()
local changed, slots = 0, 0
for i = 1, N do
    m.Connect_Pipe(use)
    disconnect(use)
    local now = snap()
    if now[0] ~= base[0] then changed = changed + 1 end
    for k = 1, 4 do if now[k] ~= base[k] then slots = slots + 1 end end
    if i % 10 == 0 then collectgarbage() end
end
return string.format("RESULT changed=%d slots=%d stamped=%s status=%s logs=%d",
    changed, slots, g and g.stamped() or "none", (g and g.status() or "none"):gsub(" ", "_"), #logs)
"""
if lua.luaL_loadstring(L, script.encode()) != 0:
    print("LOAD_ERROR", lua.lua_tolstring(L, -1, None)); sys.exit(3)
dll = os.path.join(game, "extensions", "sn_mod_support_apis", "ui_c_library_winpipe_64.txt")
for a in (str(n), name, dll, open(block_file, encoding="utf-8").read(), mode):
    lua.lua_pushstring(L, a.encode())
rc = lua.lua_pcall(L, 5, 1, 0)
print(lua.lua_tolstring(L, -1, None).decode(errors="replace") if rc == 0
      else "PCALL_ERROR " + lua.lua_tolstring(L, -1, None).decode(errors="replace"))
'''


def _game_root() -> pathlib.Path:
    if sys.platform != "win32":
        pytest.skip("the pipe DLL is a Windows DLL")
    root = os.environ.get("X4_GAME")
    if not root:
        try:
            from x4validate import _paths
            r = _paths.game_root()
        except Exception as exc:  # silent-ok: an unconfigured toolkit is a skip, named
            pytest.skip(f"game root unresolved: {exc}")
        root = str(r) if r else None
    if not root:
        pytest.skip("no X4 game root configured (set X4_GAME)")
    g = pathlib.Path(root)
    lua_dll = g / "lua51_64.dll"
    pipe_dll = g / "extensions" / "sn_mod_support_apis" / "ui_c_library_winpipe_64.txt"
    if not lua_dll.is_file() or not pipe_dll.is_file():
        pytest.skip(f"needs {lua_dll} and {pipe_dll}")
    if pipe_dll.stat().st_size != _PIPE_DLL_SIZE:
        pytest.skip(f"the installed pipe DLL is {pipe_dll.stat().st_size} bytes, not the "
                    f"measured {_PIPE_DLL_SIZE}-byte build")
    return g


def _offline(mode: str, tmp_path, n: int = 200) -> dict[str, str]:
    game = _game_root()
    block = tmp_path / "guard_block.lua"
    block.write_bytes(guard_block().encode("utf-8"))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run([sys.executable, "-I", "-c", DRIVER, str(game), mode, str(n), str(block)],
                       capture_output=True, text=True, timeout=180, creationflags=flags,
                       cwd=str(tmp_path))
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    assert p.returncode == 0 and len(line) == 1, (p.returncode, p.stdout, p.stderr)
    return dict(kv.split("=", 1) for kv in line[0].split()[1:])


def test_offline_WITHOUT_the_guard_close_corrupts_the_registry(tmp_path):
    """The falsification twin: the defect is reproducible here, so the guarded run's zero
    is a measurement and not an instrument that cannot move."""
    r = _offline("noguard", tmp_path)
    assert int(r["changed"]) > 0, r


def test_offline_WITH_the_guard_200_cycles_leave_the_registry_unchanged(tmp_path):
    r = _offline("guard", tmp_path)
    assert (r["changed"], r["slots"], r["stamped"], r["status"], r["logs"]) == (
        "0", "0", "200", "active", "0"), r


def test_offline_a_FOREIGN_userdata_on_our_pipe_name_is_refused_before_any_read(tmp_path):
    """Review M2, in the game's own Lua: an io file (read/write/close, but not winpipe's
    File) on our pipe name refuses at the File type check, nothing stamped, one log line.
    The `guard` run above is the twin: real winpipe Files pass the same check."""
    r = _offline("iofile", tmp_path, n=20)
    assert r["status"].startswith("inactive:pipe_file_has_a_foreign_method_"), r
    assert (r["stamped"], r["logs"]) == ("0", "1"), r


def test_offline_a_FOREIGN_pipe_is_left_untouched_by_the_guard(tmp_path):
    """Scope check, not a safety claim: other mods' pipes keep the upstream defect."""
    r = _offline("foreign", tmp_path)
    assert r["stamped"] == "0" and r["status"] == "armed", r
    assert int(r["changed"]) > 0, r

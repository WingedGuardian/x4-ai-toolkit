"""Game Lua is tested under LuaJIT, the runtime X4 embeds (review r4-1, r5-M2).

`lupa.LuaRuntime` is lupa's default build (Lua 5.4/5.5 here); X4 runs LuaJIT 2.1, which
differs in integer division, bit ops, `goto`, `ffi` and string formatting. Every test
that runs helper or mod Lua must take its runtime from `lupa.luajit21`.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BAD = re.compile(r"from lupa import|importorskip\(\s*[\"']lupa[\"']|^\s*import lupa\s*$|lupa\.lua5\d", re.M)


def test_every_lua_test_uses_luajit():
    files = sorted(HERE.glob("test_live*.py")) + [HERE / "test_modlua_rearm.py"]
    assert len(files) > 100, "population check: the live test files moved"
    offenders = [f.name for f in files if f.name != Path(__file__).name and BAD.search(f.read_text(encoding="utf-8"))]
    assert offenders == []

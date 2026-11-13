"""Test files read sources as UTF-8, never in the locale code page (review r5-M3).

A bare `Path.read_text()` decodes with the locale encoding (cp1252 on Windows), so a
non-ASCII byte in a Lua or MD source reads differently on Windows and Linux CI.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BARE = re.compile(r"\.read_text\(\s*\)")


def test_no_bare_read_text_in_live_tests():
    files = sorted(HERE.glob("test_live*.py")) + [HERE / "test_xrcat_maxpath.py", HERE / "test_modlua_rearm.py"]
    assert len(files) > 100, "population check: the live test files moved"
    hits = [f"{f.name}:{n}" for f in files if f.name != Path(__file__).name
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if BARE.search(line)]
    assert hits == []

"""bin/xrcat refuses a pack whose input holds a file XRCatTool 1.11 would skip.

MEASURED 2026-11-13 (XRCatTool 1.11, 18 files of absolute length 250..267 under one
input folder): every file of 259 characters or fewer was packed; every file of 260 or
more was left out of the catalog with only an "Illegal file <relative path>" line on
stderr, and the tool exited 0. A pack that silently drops files ships a broken mod,
so the wrapper refuses BEFORE running the tool and names each such path.

A fake XRCatTool stands in for the real one: the test is about the wrapper's
preflight, and "the tool never ran" is the property that matters.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "bin" / "xrcat"
WINDOWS = sys.platform == "win32"


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not available")
    return found


def _windows_spelling(path: Path) -> str:
    if WINDOWS:
        return str(path.resolve())
    return "Z:" + str(path.resolve()).replace("/", "\\")


def _u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _make_file(folder: Path, total: int, head: str = "") -> str:
    """Create folder/d/<head><pad>.xml whose absolute Windows spelling is exactly `total` UTF-16 units."""
    prefix = _windows_spelling(folder) + "\\d\\"
    pad = total - _u16(prefix) - _u16(head) - len(".xml")
    assert pad >= 1, "test folder too deep for this length"
    name = head + "y" * pad + ".xml"
    full_win = prefix + name
    assert _u16(full_win) == total
    target = folder / "d" / name
    if WINDOWS:
        os.makedirs("\\\\?\\" + str((folder / "d").resolve()), exist_ok=True)
        with open("\\\\?\\" + str(target.resolve()), "wb") as fh:
            fh.write(b"<x/>")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"<x/>")
    return full_win


def _fake_tool(tmp: Path) -> Path:
    marker = tmp / "ran.txt"
    fake = tmp / "fakexrcat.sh"
    fake.write_bytes(("#!/usr/bin/env bash\necho ran > '" + marker.as_posix() + "'\nexit 0\n").encode())
    fake.chmod(0o755)
    return fake


def _run(tmp: Path, args, **extra_env):
    fake = _fake_tool(tmp)
    env = {**os.environ, "XRCATTOOL": fake.as_posix(), **extra_env}
    return subprocess.run([_bash(), WRAPPER.as_posix(), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "content.xml").write_bytes(b"<content/>")
    return src


@pytest.mark.parametrize("length", [260, 300])
def test_pack_with_an_input_path_of_260_or_more_is_refused_before_the_tool_runs(tmp_path, length):
    src = _src(tmp_path)
    long_path = _make_file(src, length)
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode != 0, result.stdout + result.stderr
    assert not (tmp_path / "ran.txt").exists(), "XRCatTool ran despite the over-long input"
    assert long_path in result.stderr
    assert "260" in result.stderr


def test_pack_with_input_paths_of_259_runs_the_tool(tmp_path):
    # Twin: one character shorter is packed by the real tool, so the wrapper must not refuse it.
    if not WINDOWS:
        pytest.skip("the tool-runs branch needs Wine off Windows")
    src = _src(tmp_path)
    _make_file(src, 259)
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "ran.txt").exists()


def test_unpack_of_a_catalog_is_not_preflighted(tmp_path):
    # Twin: -in naming a .cat file (an unpack) is not walked.
    if not WINDOWS:
        pytest.skip("the tool-runs branch needs Wine off Windows")
    cat = tmp_path / "ext_01.cat"
    cat.write_bytes(b"")
    result = _run(tmp_path, ["-in", str(cat), "-out", str(tmp_path / "out")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "ran.txt").exists()


# Review finding r1-1: the length was counted in UTF-8 BYTES. MEASURED with the real
# tool: e-acute names still pack at 259 UTF-16 units and are skipped at 260, and a
# Cyrillic name is skipped at any length ("Illegal file k?.xml", exit 0) because the
# tool writes names in the ANSI code page (1252 here), where e-acute and the euro exist.
E_ACUTE = "\u00e9" * 20


def test_non_ascii_name_of_259_units_runs_the_tool(tmp_path):
    if not WINDOWS:
        pytest.skip("the tool-runs branch needs Wine off Windows")
    src = _src(tmp_path)
    _make_file(src, 259, E_ACUTE)
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "ran.txt").exists()


def test_non_ascii_name_of_260_units_is_refused(tmp_path):
    src = _src(tmp_path)
    long_path = _make_file(src, 260, E_ACUTE)
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode != 0 and not (tmp_path / "ran.txt").exists()
    assert "260  " in result.stderr and long_path in result.stderr


@pytest.mark.parametrize("name", ["k\u0436.xml", "s\U0001F600.xml"])
def test_short_name_beyond_latin1_is_refused(tmp_path, name):
    src = _src(tmp_path)
    (src / name).write_bytes(b"<x/>")
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode != 0 and not (tmp_path / "ran.txt").exists()
    assert "code page" in result.stderr and name in result.stderr


@pytest.mark.parametrize("name", ["a\u00e9.xml", "c\u00ff.xml"])
def test_short_latin1_name_runs_the_tool(tmp_path, name):
    # Twin of the code-page clause: Latin-1 names pack where the code page is 1252.
    if not WINDOWS:
        pytest.skip("the tool-runs branch needs Wine off Windows")
    if _acp() != 1252:
        pytest.skip("measured for code page 1252 only")
    src = _src(tmp_path)
    (src / name).write_bytes(b"<x/>")
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode == 0, result.stdout + result.stderr


# Delta review minor 4: the rule follows the machine's ANSI code page (GetACP), not an
# assumed 1252, and covers the input root itself, not only the names under it.
def _acp():
    import ctypes
    return ctypes.windll.kernel32.GetACP()


def test_euro_name_follows_the_ansi_code_page(tmp_path):
    if not WINDOWS:
        pytest.skip("Windows code page only")
    src = _src(tmp_path)
    (src / "c\u20ac.xml").write_bytes(b"<x/>")
    try:
        "c\u20ac.xml".encode("cp%d" % _acp())
        encodable = True
    except (UnicodeEncodeError, LookupError):
        encodable = False
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert (result.returncode == 0) is encodable, result.stderr


def test_input_root_with_an_unencodable_name_is_refused(tmp_path):
    src = tmp_path / "\u0436src"
    src.mkdir()
    (src / "content.xml").write_bytes(b"<x/>")
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")])
    assert result.returncode != 0 and not (tmp_path / "ran.txt").exists()
    assert "code page" in result.stderr


# Delta review 2, M4: say precisely why names cannot be checked.
@pytest.mark.parametrize("env,expected", [
    ({"X4_XRCAT_ICONV": "no-such-iconv-binary"}, "iconv is not available"),
    ({"X4_XRCAT_ACP": "65001"}, "65001 (UTF-8)"),
    ({"X4_XRCAT_ACP": "99999"}, "this iconv cannot convert to code page 99999"),
    ({"X4_XRCAT_ACP": ""}, "could not be read"),
])
def test_unverifiable_code_page_is_named_precisely(tmp_path, env, expected):
    if not WINDOWS and "X4_XRCAT_ACP" not in env:
        pytest.skip("off Windows the code page is never read")
    if "X4_XRCAT_ACP" not in env:
        env = {**env, "X4_XRCAT_ACP": "1252"}
    src = _src(tmp_path)
    (src / "aé.xml").write_bytes(b"<x/>")
    result = _run(tmp_path, ["-in", str(src), "-out", str(tmp_path / "ext_01.cat")], **env)
    assert result.returncode != 0 and expected in result.stderr, result.stderr

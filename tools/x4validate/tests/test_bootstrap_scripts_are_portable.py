"""Bootstrap scripts must parse and run on the shell users actually have.

MEASURED 2026-09-01: install.ps1 held 9 UTF-8 em-dashes and no BOM. Windows PowerShell
5.1 -- the default shell on Windows 10/11, and the one README:206 tells users to run --
reads a BOM-less .ps1 as the ANSI codepage, so every em-dash became three mojibake
characters. One of them sat inside an interpolated string at line 108 and the parser
derailed: 3 parse errors, the installer dead before its first statement. pwsh 7 parsed
it fine, which is why it survived to a 3.0 release candidate.

TWO checks, because they fail differently:
  * the ASCII assertion catches the CAUSE and runs on every platform, including the
    Linux CI leg where powershell.exe does not exist;
  * the 5.1 parser check catches ANY syntax defect, not just this one, but can only
    run where that engine is installed.
A single check would have been vacuous on one leg or blind to everything else.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent

#: PowerShell scripts a user runs BEFORE the toolkit is installed. Scoped to .ps1 on
#: purpose: the defect is PowerShell 5.1 reading a BOM-less file as the ANSI codepage.
#: bash is byte-transparent and passes UTF-8 through untouched, so the same em-dashes
#: in install.sh / setup.sh are harmless -- MEASURED, 39 non-ASCII bytes between them,
#: all in comments and echo strings, and both run fine. Applying the rule there anyway
#: would be a check that fires where nothing is wrong, which is how a suite gets
#: ignored. (The real portability risk for .sh is CRLF, pinned by .gitattributes.)
BOOTSTRAP = ["install.ps1"]


def _tracked_ps1() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "*.ps1"], cwd=ROOT,
                         capture_output=True, text=True, check=False)
    listed = [ROOT / n for n in out.stdout.split() if n]
    if listed or (ROOT / ".git").exists():
        return listed
    # 4.1 review M13: an INSTALLED toolkit has no git index, so "tracked" was always empty there and the
    # 5.1 parse check skipped with "no tracked .ps1 files" while install.ps1 sat in the folder. With no
    # .git at the root, the shipped scripts are the .ps1 files on disk (never a venv or an unpacked tree).
    skip = {".venv", "reference", "node_modules", "__pycache__", ".test-sandbox"}
    return sorted(p for p in ROOT.rglob("*.ps1") if not skip & set(p.relative_to(ROOT).parts))


def non_ascii(data: bytes) -> list:
    """(offset, byte) for every byte above 127. Extracted so the falsification twin
    below can CALL it.

    The twin used to write an em-dash to a temp file and assert the FIXTURE carried
    non-ASCII bytes -- it never touched this predicate, so replacing the real check
    with `return []` left it green. Decoration, written the same day as the rule
    against exactly that. A twin must exercise the production code path.
    """
    return [(i, data[i]) for i in range(len(data)) if data[i] > 127]


@pytest.mark.parametrize("name", BOOTSTRAP)
def test_bootstrap_script_is_pure_ascii(name):
    p = ROOT / name
    if not p.is_file():
        pytest.skip(f"{name} not present in this tree")
    bad = non_ascii(p.read_bytes())
    assert not bad, (
        f"{name} has {len(bad)} non-ASCII byte(s), first at offset {bad[0][0]}. "
        "Windows PowerShell 5.1 reads a BOM-less script as the ANSI codepage, so a "
        "UTF-8 character becomes mojibake and can break parsing. Use ASCII "
        "(e.g. '-' for an em-dash).")


def test_the_ascii_check_would_catch_a_real_em_dash(tmp_path):
    """The twin, and it now calls the PRODUCTION predicate.

    The em-dash is the exact character that broke install.ps1 under Windows PowerShell
    5.1, so this fixture is the real defect rather than a stand-in.
    """
    f = tmp_path / "sample.ps1"
    f.write_bytes(("Write-Host " + chr(39) + "a " + chr(0x2014) + " b" + chr(39)).encode("utf-8"))
    found = non_ascii(f.read_bytes())
    assert found, "non_ascii() did not flag a real em-dash -- the check is inert"
    assert found[0][1] > 127


def test_the_ascii_check_stays_silent_on_pure_ascii(tmp_path):
    """The other direction: a check that fires on everything is no check either."""
    f = tmp_path / "clean.ps1"
    f.write_bytes(b"Write-Host 'a - b'")
    assert non_ascii(f.read_bytes()) == []


@pytest.mark.skipif(shutil.which("powershell") is None,
                    reason="Windows PowerShell 5.1 not available on this platform")
@pytest.mark.parametrize("script", [pytest.param(p, id=p.name) for p in _tracked_ps1()] or
                         [pytest.param(None, id="none-tracked")])
def test_every_tracked_ps1_parses_under_windows_powershell(script):
    if script is None:
        pytest.skip("no .ps1 files to parse here: git lists none and none were found on disk")
    cmd = ("$e=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile("
           f"'{script.as_posix()}',[ref]$null,[ref]$e); "
           "if($e.Count){ $e | ForEach-Object { $_.Message }; exit 1 } else { exit 0 }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        f"{script.name} does not parse under Windows PowerShell 5.1:\n{r.stdout}")

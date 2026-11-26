"""The launcher the installers write: start an agent from the install folder, whatever folder the terminal is in.

An agent loads its guards and instructions from the folder it starts in; started elsewhere it runs with none.
`scripts/x4launcher.py` writes `<toolkit>/bin/x4agent` (+ `.cmd`) through the vendored core launcher, and both
installers run it for every method except `global`.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / "scripts" / "x4launcher.py"
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"
README = ROOT / "README.md"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # never a visible console, never DETACHED_PROCESS
FILES = ("x4agent", "x4agent.cmd", "x4agent.ps1")


def _run(args, cwd=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                          cwd=cwd, creationflags=NO_WINDOW, timeout=120, stdin=subprocess.DEVNULL)


@pytest.fixture
def tk(tmp_path):
    folder = tmp_path / "install folder"
    folder.mkdir()
    return folder


def _bin(folder):
    return sorted(p.name for p in (folder / "bin").glob("x4agent*")) if (folder / "bin").is_dir() else []


def test_it_writes_the_launcher_for_the_INSTALLED_agents_only(tk):
    r = _run(["--toolkit", str(tk), "--agents", "claude"])
    assert r.returncode == 0, r.stderr
    text = (tk / "bin" / "x4agent").read_text(encoding="utf-8")
    assert "claude" in text and "codex" not in text and "opencode" not in text
    r = _run(["--toolkit", str(tk), "--agents", "claude,codex,opencode,generic", "--shells", "bash"])
    assert r.returncode == 0, r.stderr
    text = (tk / "bin" / "x4agent").read_text(encoding="utf-8")
    assert all(a in text for a in ("claude", "codex", "opencode")) and "generic" not in text


def test_the_default_files_never_include_the_ps1_launcher(tk):
    """MEASURED 2026-11-26: PowerShell prefers a .ps1 over a .cmd of the same name and the stock policy refuses it."""
    assert _run(["--toolkit", str(tk), "--agents", "claude"]).returncode == 0
    expected = ["x4agent", "x4agent.cmd"] if os.name == "nt" else ["x4agent"]
    assert _bin(tk) == expected
    assert _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "bash,cmd,powershell"]).returncode == 0
    assert _bin(tk) == sorted(FILES)


def test_generic_alone_has_no_command_so_nothing_is_written(tk):
    r = _run(["--toolkit", str(tk), "--agents", "generic"])
    assert r.returncode == 0, r.stderr
    assert _bin(tk) == [] and "no installed agent" in r.stdout


def test_it_bakes_the_absolute_install_folder(tk):
    assert _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "bash"]).returncode == 0
    assert str(tk.resolve()) in (tk / "bin" / "x4agent").read_text(encoding="utf-8")


def test_a_foreign_file_is_refused_and_nothing_new_is_written(tk):
    (tk / "bin").mkdir()
    mine = tk / "bin" / "x4agent"
    mine.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    r = _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "cmd,bash"])
    assert r.returncode != 0
    assert "WARNING" in r.stderr and "did not write" in r.stderr and "x4agent" in r.stderr
    assert mine.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert _bin(tk) == ["x4agent"], "the .cmd must not be written when the bash file is refused"


def test_a_launcher_it_wrote_is_rewritten(tk):
    assert _run(["--toolkit", str(tk), "--agents", "claude"]).returncode == 0
    assert _run(["--toolkit", str(tk), "--agents", "claude,codex"]).returncode == 0
    assert "codex" in (tk / "bin" / "x4agent").read_text(encoding="utf-8")


def test_a_missing_install_folder_is_a_warning_and_a_nonzero_exit(tmp_path):
    r = _run(["--toolkit", str(tmp_path / "nowhere"), "--agents", "claude"])
    assert r.returncode != 0 and "WARNING" in r.stderr and "not found" in r.stderr
    assert not (tmp_path / "nowhere").exists()


def test_an_unknown_agent_or_shell_is_a_warning_and_writes_nothing(tk):
    for extra in (["--agents", "frobnicate"], ["--agents", "claude", "--shells", "zsh"]):
        r = _run(["--toolkit", str(tk)] + extra)
        assert r.returncode != 0 and "WARNING" in r.stderr
    assert _bin(tk) == []


def test_a_dry_run_writes_nothing_and_says_what_it_would_write(tk):
    r = _run(["--toolkit", str(tk), "--agents", "claude,codex", "--dry-run"])
    assert r.returncode == 0, r.stderr
    assert not (tk / "bin").exists()
    assert "would write" in r.stdout and "x4agent" in r.stdout


def test_a_dry_run_reports_a_foreign_file_it_would_refuse(tk):
    (tk / "bin").mkdir()
    (tk / "bin" / "x4agent").write_text("mine\n", encoding="utf-8")
    r = _run(["--toolkit", str(tk), "--agents", "claude", "--dry-run", "--shells", "bash"])
    assert r.returncode != 0 and "WARNING" in r.stderr
    assert (tk / "bin" / "x4agent").read_text(encoding="utf-8") == "mine\n"


def test_the_bash_launcher_started_from_another_folder_runs_in_the_install_folder(tk, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX only: the bash launcher is exercised on POSIX runners")
    assert _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "bash"]).returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run([str(tk / "bin" / "x4agent"), "--", "pwd"], cwd=elsewhere, capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert os.path.samefile(r.stdout.strip(), tk)


def test_the_cmd_launcher_started_from_another_folder_runs_in_the_install_folder(tk, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows only: cmd.exe")
    assert _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "cmd"]).returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run(["cmd", "/c", str(tk / "bin" / "x4agent.cmd"), "cd"], cwd=elsewhere, capture_output=True,
                       text=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert r.returncode == 0, r.stderr
    assert os.path.samefile(r.stdout.strip(), tk)


# --- the installers and the docs -----------------------------------------------------------------------------

def _sh_keep() -> list[str]:
    return re.search(r'^X4_KEEP_LOCAL="([^"]*)"', SH.read_text(encoding="utf-8"), re.M).group(1).split()


def _ps1_keep() -> list[str]:
    m = re.search(r"\$X4KeepLocal\s*=\s*@\((.*?)\)", PS1.read_text(encoding="utf-8"), re.S)
    return re.findall(r"'([^']+)'", m.group(1))


def test_the_default_launcher_files_are_keep_local_in_both_installers():
    """They bake this machine's absolute path: never copied in from a source tree, never removed from an install."""
    sh, ps = _sh_keep(), _ps1_keep()
    for name in FILES:
        assert "bin/" + name in sh, name
        assert "bin" + chr(92) + name in ps, name


def test_the_launcher_files_are_gitignored():
    for name in FILES:
        r = subprocess.run(["git", "check-ignore", "-q", "bin/" + name], cwd=ROOT, creationflags=NO_WINDOW,
                           capture_output=True, timeout=60)
        assert r.returncode == 0, name + " is not ignored"
    for tracked in ("bin/xrcat", "bin/unpack-reference.sh"):
        r = subprocess.run(["git", "check-ignore", "-q", tracked], cwd=ROOT, creationflags=NO_WINDOW,
                           capture_output=True, timeout=60)
        assert r.returncode == 1, tracked + " must stay tracked"


def _sh_text():
    return SH.read_text(encoding="utf-8")


def _ps1_text():
    return PS1.read_text(encoding="utf-8")


def _sh_func():
    m = re.search(r"^write_launcher\(\) \{.*?^\}$", _sh_text(), re.S | re.M)
    assert m, "install.sh has no write_launcher function"
    return m.group(0)


def _ps1_func():
    m = re.search(r"^function Write-Launcher.*?^\}$", _ps1_text(), re.S | re.M)
    assert m, "install.ps1 has no Write-Launcher function"
    return m.group(0)


def test_both_installers_run_the_step_through_their_resolved_python_ISOLATED():
    assert re.search(r'"\$\{X4_OC_PY\[@\]\}" -I "\$dest/scripts/x4launcher\.py" --toolkit "\$dest" --agents "\$agents"', _sh_func())
    func = _ps1_func()
    assert "Find-OcPython" in func and "'-I', $script, '--toolkit', $dest, '--agents', $agents" in func
    assert "'x4launcher.py'" in func


def test_both_installers_pass_only_the_LANDED_agents():
    assert "_agents_landed" in _sh_func() and "Get-AgentsLanded" in _ps1_func()


def test_a_failed_launcher_step_is_a_WARNING_and_never_INCOMPLETE():
    assert "add_failed" not in _sh_func() and "WARNING" in _sh_func()
    assert "$script:failed" not in _ps1_func() and "WARNING" in _ps1_func()


def test_the_launcher_step_writes_nothing_for_global_or_a_dry_run():
    sh, ps = _sh_func(), _ps1_func()
    assert re.search(r'\[ "\$METHOD" = global \]', sh) and re.search(r"launcher: none for --method global", sh)
    assert re.search(r"\$Method -eq 'global'", ps) and re.search(r"launcher: none for -Method global", ps)
    assert re.search(r'\[ "\$DRY_RUN" = 1 \]', sh) and "if ($DryRun)" in ps
    # the global / dry-run returns come BEFORE anything is run
    assert sh.index("global") < sh.index("-I") and sh.index("DRY_RUN") < sh.index("-I")
    assert ps.index("'global'") < ps.index("'-I'") and ps.index("$DryRun") < ps.index("'-I'")


def test_the_step_runs_once_per_installer_before_the_INCOMPLETE_and_complete_banners():
    sh, ps = _sh_text(), _ps1_text()
    assert sh.count('write_launcher "$TOOLKIT"') == 1 and ps.count("Write-Launcher $Toolkit") == 1
    assert sh.index('write_launcher "$TOOLKIT"') < sh.index("=== install INCOMPLETE")
    assert ps.index("Write-Launcher $Toolkit") < ps.index("=== install INCOMPLETE")


def test_both_banners_name_the_install_folder_and_the_launcher():
    sh_line = re.search(r'^\s*echo "Start:[^\n]*', _sh_text(), re.M)
    ps_line = re.search(r"^\s*Write-Host[^\n]*Start:[^\n]*", _ps1_text(), re.M)
    assert sh_line and ps_line
    for line in (sh_line.group(0), ps_line.group(0)):
        assert "$TOOLKIT" in line or "$Toolkit" in line
        assert "x4agent" in line and "claude|codex|opencode" in line and "run your agent there" in line


def test_the_readme_first_line_says_to_start_the_agent_from_the_install_folder():
    lines = README.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# ")
    first = next(line for line in lines[1:] if line.strip())
    low = first.lower()
    assert "install folder" in low and "launcher" in low and "bin/" in first
    assert "no guards" in low and "knowledge" in low


def test_the_readme_has_a_short_powershell_note_for_the_launcher():
    text = README.read_text(encoding="utf-8")
    assert "powershell -ExecutionPolicy Bypass -File" in text
    assert "x4agent.cmd" in text and "x4agent.ps1" in text

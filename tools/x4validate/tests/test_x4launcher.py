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


def test_both_banners_name_the_launcher_ONLY_when_one_was_written():
    """The launcher and its agents come from what the step actually wrote, never a fixed text."""
    sh_lines = re.findall(r'^\s*echo "Start:[^\n]*', _sh_text(), re.M)
    ps_lines = re.findall(r"^\s*Write-Host[^\n]*Start:[^\n]*", _ps1_text(), re.M)
    assert len(sh_lines) == 2 and len(ps_lines) == 2, (sh_lines, ps_lines)
    sh_with = next(x for x in sh_lines if "LAUNCHER" in x)
    sh_without = next(x for x in sh_lines if "LAUNCHER" not in x)
    ps_with = next(x for x in ps_lines if "Launcher" in x)
    ps_without = next(x for x in ps_lines if "Launcher" not in x)
    assert "$X4_LAUNCHER_START" in sh_with and "$X4_LAUNCHER_AGENTS" in sh_with
    assert "$script:X4LauncherStart" in ps_with and "$script:X4LauncherAgents" in ps_with
    for line in (sh_with, sh_without, ps_with, ps_without):
        assert ("$TOOLKIT" in line or "$Toolkit" in line) and "run your agent there" in line
    for line in (sh_without, ps_without):
        assert "x4agent" not in line and "bin" not in line
    # the file and agents are decided by the script's own output, so the installers must read exactly those lines
    assert "launcher: start with: " in _sh_func() and "launcher: agents: " in _sh_func()
    assert "launcher: start with: " in _ps1_func() and "launcher: agents: " in _ps1_func()


def test_the_script_names_the_cmd_file_on_windows_and_only_the_agents_it_wrote(tk):
    r = _run(["--toolkit", str(tk), "--agents", "claude,codex,generic"])
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    start = next(x for x in lines if x.startswith("launcher: start with: "))
    assert start.endswith("x4agent.cmd" if os.name == "nt" else "x4agent")
    assert "launcher: agents: claude|codex" in lines


# --- review round 1 ------------------------------------------------------------------------------------------

def _nonascii(tmp_path):
    folder = tmp_path / "M\u00fcller kit"
    folder.mkdir()
    return folder


def test_a_non_ASCII_install_path_gets_no_cmd_launcher_and_a_WARNING(tmp_path):
    """MEASURED by the reviewer: the core writes the .cmd as UTF-8 without a BOM and cmd reads it in the OEM code page,
    so `x4agent.cmd` in a `M\u00fcller kit` folder said 'install folder not found'. Refuse to write a launcher that lies."""
    folder = _nonascii(tmp_path)
    r = _run(["--toolkit", str(folder), "--agents", "claude", "--shells", "bash,cmd"])
    assert r.returncode == 0, r.stderr
    assert _bin(folder) == ["x4agent"]
    text = r.stdout + r.stderr
    assert "WARNING" in text and ".cmd" in text and "not ASCII" in text and "cd into" in text
    start = next(x for x in r.stdout.splitlines() if x.startswith("launcher: start with: "))
    assert not start.endswith(".cmd")


def test_a_non_ASCII_path_with_only_the_cmd_shell_is_a_failure_and_writes_nothing(tmp_path):
    folder = _nonascii(tmp_path)
    r = _run(["--toolkit", str(folder), "--agents", "claude", "--shells", "cmd"])
    assert r.returncode != 0 and "WARNING" in r.stderr
    assert _bin(folder) == []


def test_an_ASCII_install_path_still_gets_the_cmd_launcher(tk):
    r = _run(["--toolkit", str(tk), "--agents", "claude", "--shells", "bash,cmd"])
    assert r.returncode == 0, r.stderr
    assert _bin(tk) == ["x4agent", "x4agent.cmd"] and "not ASCII" not in r.stdout + r.stderr


def test_a_dry_run_applies_the_same_non_ASCII_rule(tmp_path):
    folder = _nonascii(tmp_path)
    r = _run(["--toolkit", str(folder), "--agents", "claude", "--shells", "bash,cmd", "--dry-run"])
    assert r.returncode == 0, r.stderr
    assert not (folder / "bin").exists()
    assert "would write" in r.stdout and "x4agent.cmd" not in r.stdout and "not ASCII" in r.stdout + r.stderr


def test_a_dry_run_against_a_missing_or_invalid_folder_FAILS(tmp_path):
    """A dry run that skips the core's validation would report 'would write' for a folder the real run refuses."""
    missing = _run(["--toolkit", str(tmp_path / "nowhere"), "--agents", "claude", "--dry-run"])
    assert missing.returncode != 0 and "WARNING" in missing.stderr and "not found" in missing.stderr
    a_file = tmp_path / "afile"
    a_file.write_text("x", encoding="utf-8")
    invalid = _run(["--toolkit", str(a_file), "--agents", "claude", "--dry-run"])
    assert invalid.returncode != 0 and "WARNING" in invalid.stderr
    assert "would write" not in missing.stdout + invalid.stdout


# --- the installer wrappers, by behaviour --------------------------------------------------------------------

def _bash_or_skip():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gitbash_launcher", ROOT / "scripts" / "gitbash.py")
    gb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gb)
    bash = gb.find_bash()
    if not bash:
        pytest.skip("needs POSIX bash or Git Bash (never the WSL stub)")
    return bash


def _stub(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path.as_posix()


def _sh_harness(tmp_path, stub_python, agents="claude, codex", method="separate", dry="0", with_script=True):
    dest = tmp_path / "dest"
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    if with_script:
        (dest / "scripts" / "x4launcher.py").write_text("", encoding="utf-8")
    func = re.search(r"^write_launcher\(\) \{.*?^\}$", _sh_text(), re.S | re.M).group(0)
    script = (
        "set -eu\n"
        "_agents_landed() { printf '%s' '" + agents + "'; }\n"
        "_oc_python() { X4_OC_PY=('" + stub_python + "'); }\n"
        "METHOD=" + method + "; DRY_RUN=" + dry + "\n"
        + func + "\n"
        'write_launcher "$1"; echo "after: rc=$?"\n'
        'echo "start=[${X4_LAUNCHER_START-}] agents=[${X4_LAUNCHER_AGENTS-}]"\n'
        "echo CONTINUED\n")
    return dest, script


def _run_wrapper(script, dest):
    return subprocess.run([_bash_or_skip(), "-c", script, "x", dest.as_posix()], capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def test_write_launcher_returns_0_and_the_install_continues_when_the_script_FAILS(tmp_path):
    """Under `set -e`, a failing script must not end the installer: it is a WARNING, never INCOMPLETE."""
    stub = _stub(tmp_path, "stubpy", 'echo "WARNING: launcher not written: boom" >&2; exit 1')
    dest, script = _sh_harness(tmp_path, stub)
    r = _run_wrapper(script, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING: the launcher was not written (exit 1" in r.stdout and "boom" in r.stdout
    assert "after: rc=0" in r.stdout and "CONTINUED" in r.stdout
    assert "start=[] agents=[]" in r.stdout


def test_write_launcher_with_no_script_warns_and_returns_0(tmp_path):
    dest, script = _sh_harness(tmp_path, "unused", with_script=False)
    r = _run_wrapper(script, dest)
    assert r.returncode == 0 and "WARNING" in r.stdout and "after: rc=0" in r.stdout and "CONTINUED" in r.stdout


def test_write_launcher_reads_the_start_file_and_agents_the_script_printed(tmp_path):
    stub = _stub(tmp_path, "okpy", r'printf "launcher: wrote /x/bin/x4agent\r\nlauncher: start with: /x/bin/x4agent\r\n'
                 r'launcher: agents: claude|codex\r\n"')
    dest, script = _sh_harness(tmp_path, stub)
    r = _run_wrapper(script, dest)
    assert r.returncode == 0, r.stderr
    assert "start=[/x/bin/x4agent] agents=[claude|codex]" in r.stdout


def test_write_launcher_with_an_EMPTY_agent_list_does_not_call_the_script(tmp_path):
    marker = tmp_path / "called"
    stub = _stub(tmp_path, "markpy", 'echo called > "' + marker.as_posix() + '"')
    dest, script = _sh_harness(tmp_path, stub, agents="")
    r = _run_wrapper(script, dest)
    assert r.returncode == 0 and not marker.exists() and "WARNING" not in r.stdout
    assert "no agent" in r.stdout and "CONTINUED" in r.stdout


def test_write_launcher_does_nothing_for_global_or_a_dry_run(tmp_path):
    marker = tmp_path / "called"
    stub = _stub(tmp_path, "markpy", 'echo called > "' + marker.as_posix() + '"')
    for kwargs in ({"method": "global"}, {"dry": "1"}):
        dest, script = _sh_harness(tmp_path, stub, **kwargs)
        r = _run_wrapper(script, dest)
        assert r.returncode == 0 and not marker.exists(), kwargs


def _ps_or_skip():
    import shutil
    if os.name != "nt":
        pytest.skip("the PowerShell installer wrapper is exercised on Windows")
    ps = shutil.which("powershell") or shutil.which("pwsh")      # Windows PowerShell 5.1 first: it is the one that drops ''
    if not ps:
        pytest.skip("no PowerShell")
    return ps


def _ps_harness(tmp_path, stub_cmd, agents):
    text = _ps1_text()
    funcs = "\n".join(re.search(r"^function " + re.escape(name) + r"\b.*?^\}$", text, re.S | re.M).group(0)
                      for name in ("Invoke-X4Native", "Write-Launcher"))
    dest = tmp_path / "psdest"
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "scripts" / "x4launcher.py").write_text("", encoding="utf-8")
    script = (
        "$Method = 'separate'; $DryRun = $false\n"
        "function Get-AgentsLanded { '" + agents + "' }\n"
        "function Find-OcPython { Write-Host 'FIND-PYTHON-CALLED'; return , @('" + str(stub_cmd) + "') }\n"
        + funcs + "\n"
        "Write-Launcher '" + str(dest) + "'\n"
        'Write-Host "start=[$($script:X4LauncherStart)] agents=[$($script:X4LauncherAgents)]"\n'
        "Write-Host 'CONTINUED'\n")
    path = tmp_path / "harness.ps1"
    path.write_text(script, encoding="utf-8")
    return path


def _ps_run(path):
    return subprocess.run([_ps_or_skip(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def test_Write_Launcher_continues_after_a_failing_script_on_PowerShell(tmp_path):
    stub = tmp_path / "failpy.cmd"
    stub.write_text("@echo WARNING: launcher not written: boom\r\n@exit /b 1\r\n", encoding="utf-8", newline="")
    r = _ps_run(_ps_harness(tmp_path, stub, "claude, codex"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING: the launcher was not written (exit 1" in r.stdout and "CONTINUED" in r.stdout
    assert "start=[] agents=[]" in r.stdout


def test_Write_Launcher_with_an_EMPTY_agent_list_does_not_call_the_script_on_PowerShell(tmp_path):
    """Windows PowerShell 5.1 drops an empty-string argument, so `--agents ''` reached argparse as `--agents` alone."""
    stub = tmp_path / "failpy.cmd"
    stub.write_text("@exit /b 1\r\n", encoding="utf-8", newline="")
    r = _ps_run(_ps_harness(tmp_path, stub, ""))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FIND-PYTHON-CALLED" not in r.stdout and "WARNING" not in r.stdout and "no agent" in r.stdout
    assert "CONTINUED" in r.stdout


def test_Write_Launcher_reads_the_start_file_and_agents_the_script_printed_on_PowerShell(tmp_path):
    stub = tmp_path / "okpy.cmd"
    stub.write_text("@echo launcher: wrote C:\\x\\bin\\x4agent\r\n@echo launcher: start with: C:\\x y\\bin\\x4agent.cmd\r\n"
                    "@echo launcher: agents: claude^|codex\r\n", encoding="utf-8", newline="")
    r = _ps_run(_ps_harness(tmp_path, stub, "claude, codex"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "start=[C:\\x y\\bin\\x4agent.cmd] agents=[claude|codex]" in r.stdout


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


def test_the_readme_says_the_bash_launcher_bakes_a_windows_path_so_use_Git_Bash_not_WSL():
    text = README.read_text(encoding="utf-8")
    assert re.search(r"bash launcher[^\n]*Windows path[^\n]*Git Bash[^\n]*not WSL", text)


def test_the_changelog_names_the_non_ASCII_path_limit_of_the_cmd_launcher():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    bullet = next(line for line in text.splitlines() if line.startswith("- **A launcher that starts your agent"))
    assert "non-ASCII" in bullet and ".cmd" in bullet

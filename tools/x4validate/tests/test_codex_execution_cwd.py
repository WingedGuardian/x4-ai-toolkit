"""Exercise real operand extraction with only downstream policy/store mocked."""
import importlib
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from _layout import require_native_codex

ROOT = Path(__file__).resolve().parents[3]
# The unknown-execution-cwd path is Codex's (its payload carries no cwd) and needs the Codex
# carrier's patch_paths beside save_policy. A module-level import from .codex/hooks broke
# COLLECTION of every install without Codex (release review T3): skip, counted, when Codex
# was not selected; a selected-but-damaged carrier still fails.
try:
    require_native_codex(ROOT)
except pytest.skip.Exception as exc:
    pytest.skip(str(exc), allow_module_level=True)
sys.path.insert(0, str(ROOT / ".codex/hooks"))
sys.path.insert(0, str(ROOT / "tools"))
import save_policy
from modding_harness import contracts, dispatch

CASES = [
    ("powershell", "Set-Content -LiteralPath 'Owned.xml.gz' -Value x", True),
    ("powershell", "Remove-Item -LiteralPath 'Owned.xml.gz'", True),
    ("powershell", "Copy-Item -LiteralPath 'C:/Source' -Destination 'Owned.xml.gz'", True),
    ("powershell", "Move-Item -LiteralPath 'Owned.xml.gz' -Destination 'C:/Elsewhere/a'", True),
    ("powershell", "git reset --hard", True),
    ("powershell", "git clean -fdx", True),
    ("powershell", "Get-Content -LiteralPath 'Owned.xml.gz'", False),
    ("powershell", "git status", False),
    ("powershell", "Set-Content -LiteralPath 'C:/Scratch/notes.txt' -Value x", False),
    ("powershell", "Set-Location -LiteralPath 'C:/Scratch'; Set-Content -LiteralPath 'notes.txt' -Value x", True),
    ("bash", "printf x > Owned.xml.gz", True),
    ("bash", "rm -- Owned.xml.gz", True),
    ("bash", "cp /source Owned.xml.gz", True),
    ("bash", "mv Owned.xml.gz /elsewhere", True),
    ("bash", "cat Owned.xml.gz", False),
    ("bash", "printf x > C:/scratch/notes.txt", False),
    ("bash", "cd /scratch && printf x > notes.txt", True),
    ("bash", "git -C C:/scratch reset --hard", False),
    ("powershell", "Rename-Item -LiteralPath 'C:/Scratch/old.txt' -NewName 'new.txt'", False),
    ("powershell", "Rename-Item -LiteralPath 'Owned.xml.gz' -NewName 'new.xml.gz'", True),
    ("powershell", "Set-Location -LiteralPath 'C:/Scratch'; [IO.File]::WriteAllText('Owned.xml.gz', 'x')", True),
    ("bash", "git -C relative reset --hard", True),
    ("bash", "git clean -ndx", False),
    ("powershell", "Set-Location -LiteralPath 'C:/Owned-Missing'; Set-Content -LiteralPath 'Owned.save' -Value x", True),
    ("bash", "cd /owned-missing; printf x > Owned.save", True),
    ("powershell", "Set-Content -LiteralPath '/Scratch/Owned.xml.gz' -Value x", True),
    ("powershell", "Set-Content -LiteralPath '\\Scratch\\Owned.xml.gz' -Value x", True),
    ("powershell", "Set-Content -LiteralPath '/c/Scratch/Owned.xml.gz' -Value x", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: /Scratch/Owned.xml.gz\n+x\n*** End Patch\nPATCH", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: /c/Scratch/Owned.xml.gz\n+x\n*** End Patch\nPATCH", True),
    ("bash", "pwsh -NoProfile -Command \"Set-Content -LiteralPath '/Scratch/Owned.xml.gz' -Value x\"", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: Owned.xml.gz\n+x\n*** End Patch\nPATCH", True),
    ("bash", "cd C:/scratch && apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: notes.txt\n+x\n*** End Patch\nPATCH", True),
]


def run_case(tmp_path, shell, command, workdir=None):
    roots = {key: str(tmp_path / key) for key in ("toolkit", "game", "reference", "profile", "saves", "mods", "extensions")}
    roots["toolkit"] = str(ROOT)
    ti = {"command": command}
    if workdir is not None:
        ti["workdir"] = workdir
    payload = {"tool_name": "Bash", "tool_input": ti, "cwd": str(tmp_path), "session_id": "owned"}
    calls = [("shell", shell, command, None, "owned")]
    def load_core(root, name):
        return importlib.import_module("modding_harness." + name)
    with patch.object(save_policy, "read_roots", return_value=roots), \
         patch.object(save_policy, "core", side_effect=load_core), \
         patch.object(save_policy, "enforce", return_value=dispatch.Outcome(contracts.Result())) as downstream:
        result = save_policy.batch(payload, calls, lambda _: {}, time.monotonic() + 20, "codex")
    return result, downstream.call_count


@pytest.mark.parametrize("shell,command,refused", CASES)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows native cwd/drive cases; portable Bash cases below")
def test_missing_execution_cwd_checks_mutations_not_reads(tmp_path, shell, command, refused):
    result, calls = run_case(tmp_path, shell, command)
    assert (result["decision"] == "deny") is refused, (command, result)
    assert calls == (0 if refused else 1)
    if refused:
        assert "execution workdir" in result["reason"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native explicit workdir case")
def test_absolute_workdir_keeps_relative_mutation_qualified(tmp_path):
    result, calls = run_case(tmp_path, "powershell", "Set-Content notes.txt x", "C:/Scratch")
    assert result["decision"] == "allow" and calls == 1


@pytest.mark.parametrize("case,refused", [("write", True), ("read", False), ("cd-write", True),
                                        ("absolute-write", False), ("copy", False), ("git", False)])
def test_portable_bash_context(tmp_path, case, refused):
    absolute = tmp_path.as_posix()
    commands = {"write": "printf x > Owned.save", "read": "cat Owned.save",
                "cd-write": "cd '" + absolute + "'; printf x > Owned.save",
                "absolute-write": "printf x > '" + absolute + "/notes.txt'",
                "copy": "cp relative-source '" + absolute + "/notes.txt'",
                "git": "git -C '" + absolute + "' reset --hard"}
    result, calls = run_case(tmp_path, "bash", commands[case])
    assert (result["decision"] == "deny") is refused, result
    assert calls == (0 if refused else 1)

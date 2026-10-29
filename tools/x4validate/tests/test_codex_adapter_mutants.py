"""Adapter mutants (spec 7.3): each plants ONE defect in a tmp copy of the rendered .codex/hooks
and must turn its probe red. The substitution must match exactly once -- a no-op mutant fails
loudly instead of passing vacuously -- and every probe is first run UNMUTATED as the control.

A mutant that stays green means a missing probe: add the probe, never weaken the mutant.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codex_testlib import HOOKS, make_sandbox, native, parse_output

PWSH = shutil.which("pwsh") or shutil.which("powershell")
# WINDOWS-ONLY since 8bdaa55, deliberately: codex-entry.ps1 now launches the adapter through
# kernel32 P/Invoke (CreateProcess with an explicit handle list, a job object to bound the
# adapter's lifetime -- READ, codex-entry.ps1 Add-Type block), which pwsh on Linux cannot load,
# so every probe here would fail for a reason that is not the mutant. Before it, pwsh on ubuntu
# ran this chain. ⚠ The POSIX entry (codex-entry.sh) has no mutant file of its own.
pytestmark = pytest.mark.skipif(not PWSH or os.name != "nt", reason="the native Windows entry chain is NOT checked here")


def chain(tree: Path, payload: dict, env: dict, shell: str | None = "bash", event: str = "pre_tool_use"):
    e = dict(env, X4_PYTHON=sys.executable)
    e.pop("X4_CODEX_SHELL", None)
    if shell:
        e["X4_CODEX_SHELL"] = shell
    t0 = time.monotonic()
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(tree / "codex-entry.ps1"), event], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=e, timeout=120)
    assert r.returncode == 0
    d, text = parse_output(r.stdout)
    return d, text or "", time.monotonic() - t0


# --- probes: (payload builder, shell, env extras, tree prep) -> the observable each returns ----- #

def p_ps_set_content(tmp, tk, env, tree):
    cmd = f"Set-Content -Path '{tk / 'reference' / 'libraries' / 'wares.xml'}' -Value 'x'"
    return chain(tree, native("bash_powershell", tmp, command=cmd), env, shell=None)[0]   # DEFAULT routing


def p_delete_reference(tmp, tk, env, tree):
    patch = "*** Begin Patch\n*** Delete File: reference/libraries/wares.xml\n*** End Patch"
    return chain(tree, native("apply_patch_delete", tk, command=patch), env)[0]


def p_move_into_reference(tmp, tk, env, tree):
    (tk / "dev" / "mymod" / "w.xml").write_text("x\n", encoding="utf-8")
    patch = ("*** Begin Patch\n*** Update File: dev/mymod/w.xml\n*** Move to: reference/libraries/w.xml\n"
             "@@\n-x\n+y\n*** End Patch")
    return chain(tree, native("apply_patch_move", tk, command=patch), env)[0]


def p_relative_to_cwd(tmp, tk, env, tree):
    patch = "*** Begin Patch\n*** Update File: libraries/wares.xml\n@@\n-ref\n+x\n*** End Patch"
    return chain(tree, native("apply_patch_update", tk / "reference", command=patch), env)[0]


def p_profile_ask(tmp, tk, env, tree):
    patch = f"*** Begin Patch\n*** Update File: {tmp / 'profile' / 'config.xml'}\n@@\n-a\n+b\n*** End Patch"
    return chain(tree, native("apply_patch_update", tk, command=patch), env)[0]


def p_crash_adapter(tmp, tk, env, tree):
    (tree / "codex_adapter.py").write_text("import sys; sys.exit(3)", encoding="utf-8")
    return chain(tree, native("bash_powershell", tmp, command="echo hi"), env)[0]


def p_write_stdin(tmp, tk, env, tree):
    d = native("bash_powershell", tmp)
    d["tool_name"], d["tool_input"] = "write_stdin", {"session_id": 1, "chars": f"rm -rf '{(tk / 'reference').as_posix()}'\n"}
    return chain(tree, d, env)[0]


def p_hung_guard(tmp, tk, env, tree):
    """A guard that hangs: the adapter's budget must bound it (the ADAPTER answers, inert) rather
    than the wrapper's kill (which also says inert, but only after the hook's whole timeout)."""
    # A busy loop IN bash, not `sleep`: on master's x4guard a hung CHILD keeps the pipes open and
    # subprocess.run's timeout then waits for it unboundedly on Windows (MEASURED here: the control
    # came back wrapper-killed; lane E replaces that call). This probe is about the ADAPTER budget.
    (tree / "protect-bash.sh").write_text("#!/bin/bash\nend=$((SECONDS + 40)); while [ $SECONDS -lt $end ]; do :; done\n",
                                          encoding="utf-8")
    e = dict(env, X4_CODEX_BUDGET_S="4", X4_GUARD_TIMEOUT_S="30", X4_WRAPPER_TIMEOUT_S="20")
    d, text, _ = chain(tree, native("bash_powershell", tmp, command="echo hi"), e)
    return d + ("/wrapper-killed" if "did not answer" in text else "/adapter-bounded")


MUTANTS = [
    # id, file, old, new, probe, control verdict
    ("shell-routing-dropped", "codex_adapter.py", 'SHELL = "powershell" if', 'SHELL = "bash" if',
     p_ps_set_content, "deny"),
    ("apply-patch-paths-dropped", "codex_adapter.py", "    for op, p in ops:\n        kind = OP_KIND.get(op)",
     "    for op, p in ops[:0]:\n        kind = OP_KIND.get(op)", p_delete_reference, "deny"),
    ("move-to-dropped", "codex_adapter.py", '"move_to": "write"', '"move_to": None', p_move_into_reference, "deny"),
    ("payload-cwd-ignored", "codex_adapter.py", "        os.chdir(cwd)", "        pass", p_relative_to_cwd, "deny"),
    ("deny-rendered-as-exit-2", "codex_adapter.py",
     '        hso = {"hookEventName": name, "permissionDecision": "deny", "permissionDecisionReason": verdict["reason"]}',
     '        sys.stderr.write(verdict["reason"]); sys.exit(2)', p_delete_reference, "deny"),
    ("ask-rendered-as-ask", "codex_adapter.py",
     '        hso = {"hookEventName": name, "permissionDecision": "deny",\n               "permissionDecisionReason": ASK_PREFIX',
     '        hso = {"hookEventName": name, "permissionDecision": "ask",\n               "permissionDecisionReason": ASK_PREFIX',
     p_profile_ask, "ask"),
    ("extra-key-in-output", "codex_adapter.py", 'json.dumps({"hookSpecificOutput": hso}',
     'json.dumps({"hookSpecificOutput": hso, "inert": True}', p_delete_reference, "deny"),
    ("wrapper-error-swallowed-to-allow", "codex-entry.ps1", "function Write-Fail([string]$cause) {",
     "function Write-Fail([string]$cause) { $script:emitted = $true; return", p_crash_adapter, "inert"),
    ("deadline-ignored", "codex_adapter.py", "x4guard.verdict_for(kind, sh, cmd, path, deadline=deadline, **options)",
     "x4guard.verdict_for(kind, sh, cmd, path, **options)",
     p_hung_guard, "inert/adapter-bounded"),
    ("write-stdin-unguarded", "codex_adapter.py", "    if tool in STDIN_TOOLS:\n",
     "    if tool in STDIN_TOOLS:\n        return []\n", p_write_stdin, "deny"),
]


@pytest.fixture
def setup(tmp_path):
    tmp, tk, env = make_sandbox(tmp_path / "sbx")
    tree = tmp_path / "root" / ".codex" / "hooks"
    shutil.copytree(HOOKS, tree)
    return tmp, tk, env, tree


@pytest.mark.parametrize("mid,fname,old,new,probe,control", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_control_then_mutant_turns_red(setup, mid, fname, old, new, probe, control):
    tmp, tk, env, tree = setup
    if mid == "shell-routing-dropped" and sys.platform != "win32":
        pytest.skip("the default shell is bash off Windows -- this mutant is a no-op there")
    pristine = tree.parent / "pristine"
    shutil.copytree(tree, pristine)
    got = probe(tmp, tk, env, tree)
    assert got == control, f"CONTROL failed for {mid}: {got} (expected {control})"
    shutil.rmtree(tree)
    shutil.copytree(pristine, tree)
    target = tree / fname
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutant {mid} anchor matched {text.count(old)} times -- re-anchor it"
    target.write_text(text.replace(old, new), encoding="utf-8")
    mutated = probe(tmp, tk, env, tree)
    assert mutated != control, f"mutant {mid} SURVIVED: probe still says {mutated}"

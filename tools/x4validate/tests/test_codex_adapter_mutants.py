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


def _detached_patch(tk, rel="dev/mymod/launch.py"):
    patch = f'*** Begin Patch\n*** Add File: {rel}\n+import subprocess\n+subprocess.Popen(["x"], creationflags=0x8)\n*** End Patch'
    return native("apply_patch_add", tk, command=patch)


def p_detached_launch(tmp, tk, env, tree):
    return chain(tree, _detached_patch(tk), env)[0]


def p_detached_after_refusal(tmp, tk, env, tree):
    """The funnel refuses (a write into reference/); the detector must not replace that refusal's reason."""
    d, text, _ = chain(tree, _detached_patch(tk, "reference/libraries/launch.py"), env)
    return d + ("/detached" if "no-detached-launch" in text else "/guard")


def p_detached_ask(tmp, tk, env, tree):
    """The funnel ASKS (a new file in the user profile); the detector must not replace that."""
    d, text, _ = chain(tree, _detached_patch(tk, (tmp / "profile" / "launch.py").as_posix()), env)
    return d + ("/detached" if "no-detached-launch" in text else "/guard")


def p_detached_inert(tmp, tk, env, tree):
    """The funnel answers INERT (a variable interpreter it cannot resolve); the detector must not replace that."""
    cmd = "& $env:PY -c 'import subprocess; subprocess.Popen(1, creationflags=0x8)'"
    d, text, _ = chain(tree, native("bash_powershell", tmp, command=cmd), env, shell="powershell")
    return d + ("/detached" if "no-detached-launch" in text else "/guard")


def p_detached_guard_off_log(tmp, tk, env, tree):
    log = tmp / "backups" / "GUARDS-OFF.log"
    log.unlink(missing_ok=True)                  # the control run's record must not stand in for the mutant run's
    d = chain(tree, _detached_patch(tk), dict(env, X4_GUARD="off"))[0]
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return d + ("/logged" if "no-detached-launch would have DENIED" in text else "/unlogged")


def p_detached_roots_recorded(tmp, tk, env, tree):
    """A hook-only install: no X4_TOOLKIT, the toolkit comes from the path config, and ONLY the roots the funnel read
    name the toolkit that holds the detector. The real read_roots must record them, or no call is ever judged."""
    from codex_testlib import copy_shared_runtime
    other = tmp / "other-toolkit"
    if not other.exists():
        copy_shared_runtime(other)
        (other / "dev" / "mymod").mkdir(parents=True)
    cfg = tmp / "paths.env"
    cfg.write_text(f"X4_TOOLKIT={other.as_posix()}\n", encoding="utf-8")
    e = dict(env, X4_CONFIG=str(cfg))
    e.pop("X4_TOOLKIT")
    return chain(tree, _detached_patch(other), e)[0]


def p_detached_module_missing(tmp, tk, env, tree):
    """No detector on disk: the call must still be judged by the funnel and allowed with a note, never refused."""
    (tk / "tools" / "modding_harness" / "detached.py").unlink(missing_ok=True)   # the mutant run repeats the probe
    return chain(tree, _detached_patch(tk), env)[0]


def p_detached_module_exits(tmp, tk, env, tree):
    """A detector that calls sys.exit while loading: a BaseException, which must still fail open."""
    (tk / "tools" / "modding_harness" / "detached.py").write_text("raise SystemExit(5)\n", encoding="utf-8")
    return chain(tree, _detached_patch(tk), env)[0]


def p_detached_guard_off(tmp, tk, env, tree):
    return chain(tree, _detached_patch(tk), dict(env, X4_GUARD="off"))[0]


def p_detached_own_file(tmp, tk, env, tree):
    return chain(tree, _detached_patch(tk, "dev/mymod/no-detached-launch.py"), env)[0]


def p_detached_core_file(tmp, tk, env, tree):
    return chain(tree, _detached_patch(tk, "dev/mymod/modding_harness/detached.py"), env)[0]


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
    # no-detached-launch (4.3.1): one row per clause of the in-process check
    ("detached-check-dropped", "codex_adapter.py",
     "    verdict = detached_verdict(verdict, lambda: detached_items(payload, shell), deadline)\n",
     "    pass\n", p_detached_launch, "deny"),
    ("detached-consulted-after-a-refusal", "codex_adapter.py",
     '    if verdict["decision"] not in ("allow", "advise"):\n        return verdict\n    try:',
     "    if False:\n        return verdict\n    try:", p_detached_after_refusal, "deny/guard"),
    ("detached-consulted-after-an-ask", "codex_adapter.py",
     '    if verdict["decision"] not in ("allow", "advise"):\n        return verdict\n    try:',
     '    if verdict["decision"] == "deny" and not verdict.get("inert"):\n        return verdict\n    try:',
     p_detached_ask, "ask/guard"),
    ("detached-consulted-after-an-inert-deny", "codex_adapter.py",
     '    if verdict["decision"] not in ("allow", "advise"):\n        return verdict\n    try:',
     '    if verdict["decision"] == "deny" and not verdict.get("inert"):\n        return verdict\n    try:',
     p_detached_inert, "inert/guard"),
    ("detached-guard-off-log-dropped", "codex_adapter.py", "        _log_guard_off(reason)\n", "        pass\n",
     p_detached_guard_off_log, "advise/logged"),
    ("detached-roots-not-recorded", "save_policy.py",
     '    global LAST_ROOTS\n    LAST_ROOTS = packet(result.stdout.decode("utf-8") + "{}")[0]\n    return LAST_ROOTS',
     '    return packet(result.stdout.decode("utf-8") + "{}")[0]', p_detached_roots_recorded, "deny"),
    ("detached-fails-closed", "codex_adapter.py",
     "    except BaseException as e:          # noqa: BLE001 -- nothing the check raises may become the wrapper's inert deny",
     "    except KeyError as e:               # noqa: BLE001 -- nothing the check raises may become the wrapper's inert deny",
     p_detached_module_missing, "advise"),
    ("detached-base-exception-escapes", "codex_adapter.py",
     "    except BaseException as e:          # noqa: BLE001 -- nothing the check raises may become the wrapper's inert deny",
     "    except Exception as e:              # noqa: BLE001 -- nothing the check raises may become the wrapper's inert deny",
     p_detached_module_exits, "advise"),
    ("detached-guard-off-ignored", "codex_adapter.py",
     '    if os.environ.get("X4_GUARD") == "off":\n        _log_guard_off(reason)',
     '    if False:\n        _log_guard_off(reason)', p_detached_guard_off, "advise"),
    ("detached-own-exempt-dropped", "codex_adapter.py",
     'DETACHED_OWN_EXEMPT = ("no-detached-launch.py", ', 'DETACHED_OWN_EXEMPT = ("zz-no-such-file.py", ',
     p_detached_own_file, "allow"),
    ("detached-core-exempt-replaced", "codex_adapter.py",
     "    return tuple(det.EXEMPT) + DETACHED_OWN_EXEMPT", "    return DETACHED_OWN_EXEMPT",
     p_detached_core_file, "allow"),
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

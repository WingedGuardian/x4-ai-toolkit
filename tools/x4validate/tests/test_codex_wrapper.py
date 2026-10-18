"""The fail-closed Codex entry wrappers: EVERY failure path prints a JSON deny and exits 0.

MEASURED on Codex 0.160.0: a hook that crashes, exits non-zero or prints anything Codex cannot
parse is "Failed" and the tool call RUNS. So the wrapper -- not the adapter -- is what makes a
broken adapter a refusal. Each fault double below replaces the adapter in a copy of the
rendered .codex/hooks; the control proves a real allow still passes through as an allow.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codex_testlib import HOOKS

POWERSHELLS = list(dict.fromkeys(p for p in (shutil.which("pwsh"), shutil.which("powershell"))
                               if p and sys.platform == "win32"))
PWSH = POWERSHELLS[0] if POWERSHELLS else None
BASH = os.environ.get("X4_BASH") or shutil.which("bash")
FAULTS = {
    "crash": "import sys; sys.exit(3)",
    "garbage": "print('hello')",
    "empty": "pass",
    "twolines": "print('X4OK '); print('X4OK ')",
    "extrakey": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"r\"},\"inert\":true}')",
    "allow_decision": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"allow\"}}')",
    "ask_decision": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"r\"}}')",
    "inner_extra": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"r\",\"inert\":true}}')",
    "badjson": "print('X4OK {not json')",
    # R2-F8: Codex's schema is case-sensitive; PowerShell's -ne / -notcontains were not
    "case_top_key": "print('X4OK {\"HookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"r\"}}')",
    "case_inner_key": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"PermissionDecision\":\"deny\",\"permissionDecisionReason\":\"r\"}}')",
    "case_decision": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"Deny\",\"permissionDecisionReason\":\"r\"}}')",
    # FX-G2 (reviewer E, minor): PowerShell's `@('deny') -cne 'deny'` is an EMPTY array, i.e.
    # falsy, so an array-valued decision passed the .ps1 check that the .sh one rejects.
    "array_decision": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":[\"deny\"],\"permissionDecisionReason\":\"r\"}}')",
    "object_decision": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":{\"deny\":1},\"permissionDecisionReason\":\"r\"}}')",
    "missing_event": "print('X4OK {\"hookSpecificOutput\":{\"permissionDecision\":\"deny\"}}')",
    "wrong_event": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"permissionDecision\":\"deny\"}}')",
    "nonstring_event": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":7,\"permissionDecision\":\"deny\"}}')",
    "object_reason": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":{}}}')",
    "array_context": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"additionalContext\":[]}}')",
    "duplicate_event": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\"}}')",
    "unpaired_unicode": "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"\\\\ud800\"}}')",
    "hang": "import time; time.sleep(600)",
    "raise_after_partial": "import sys; sys.stdout.write('X4O'); raise SystemExit(0)",
}


def wrappers():
    out = []
    for powershell in POWERSHELLS:
        out.append(("ps1", [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]))
    if BASH and "system32" not in BASH.lower():
        out.append(("sh", [BASH]))
    return out


WRAPPERS = wrappers() or [pytest.param("none", None, marks=pytest.mark.skip(reason="no PowerShell and no bash"))]


@pytest.fixture
def tree(tmp_path):
    t = tmp_path / ".codex" / "hooks"
    shutil.copytree(HOOKS, t)
    return t


def _run(launcher, script, event, payload: bytes, env=None):
    return subprocess.run(launcher + [str(script), event], input=payload, capture_output=True,
                          env=env or dict(os.environ), timeout=90)


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
@pytest.mark.parametrize("fault", sorted(FAULTS) + ["missing_adapter", "missing_python"])
def test_every_fault_yields_json_deny(tree, kind, launcher, fault):
    env = dict(os.environ, X4_WRAPPER_TIMEOUT_S="5")
    if fault == "missing_adapter":
        (tree / "codex_adapter.py").unlink()
    elif fault == "missing_python":
        env["X4_PYTHON"] = str(tree / "no-such-python.exe")
        env["X4_NO_PYTHON_FALLBACK"] = "1"
    else:
        (tree / "codex_adapter.py").write_text(FAULTS[fault], encoding="utf-8")
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = _run(launcher, script, "pre_tool_use", b'{"hook_event_name":"PreToolUse"}', env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out) == {"hookSpecificOutput"}
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "X4 GUARD INERT" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
@pytest.mark.parametrize("event,name", [("post_tool_use", "PostToolUse"), ("session_start", "SessionStart")])
def test_a_fault_on_a_non_blocking_event_is_an_advisory(tree, kind, launcher, event, name):
    (tree / "codex_adapter.py").write_text(FAULTS["crash"], encoding="utf-8")
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = _run(launcher, script, event, b"{}")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and set(out["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert out["hookSpecificOutput"]["hookEventName"] == name


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
def test_control_allow_passes_through_empty(tree, kind, launcher):
    (tree / "codex_adapter.py").write_text("print('X4OK ')", encoding="utf-8")
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = _run(launcher, script, "pre_tool_use", b"{}")
    assert r.returncode == 0 and r.stdout.strip() == b""          # the control: a real allow is NOT a deny


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
def test_control_a_real_deny_passes_through_verbatim(tree, kind, launcher):
    body = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: x"}}'
    (tree / "codex_adapter.py").write_text(f"print('X4OK ' + {body!r})", encoding="utf-8")
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = _run(launcher, script, "pre_tool_use", b"{}")
    assert r.returncode == 0 and json.loads(r.stdout) == json.loads(body)


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
def test_control_valid_unicode_reason_passes_through(tree, kind, launcher):
    body = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": "reason \U0001f642"}})
    (tree / "codex_adapter.py").write_text(f"print('X4OK ' + {body!r})", encoding="utf-8")
    result = _run(launcher, tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh"), "pre_tool_use", b"{}")
    assert result.returncode == 0 and json.loads(result.stdout) == json.loads(body)


@pytest.mark.parametrize("kind,launcher", WRAPPERS)
def test_payload_reaches_the_adapter_byte_for_byte(tree, kind, launcher, tmp_path):
    seen = tmp_path / "seen.bin"
    (tree / "codex_adapter.py").write_text(
        f"import sys; open({str(seen)!r}, 'wb').write(sys.stdin.buffer.read()); print('X4OK ')", encoding="utf-8")
    payload = '{"hook_event_name":"PreToolUse","tool_input":{"command":"echo é — x"}}'.encode("utf-8")
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = _run(launcher, script, "pre_tool_use", payload)
    assert r.returncode == 0 and seen.read_bytes() == payload


@pytest.mark.parametrize("powershell", POWERSHELLS or [pytest.param(None, marks=pytest.mark.skip(
    reason="Windows wrapper deadline requires native Windows PowerShell"))])
@pytest.mark.parametrize("failure", ["inherited-pipes", "unread-stdin"])
def test_powershell_deadline_includes_input_and_descendant_output(tree, failure, powershell):
    child_pid = tree / "owned-child-pid.txt"
    body = ("import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(6)'], "
            "stdout=sys.stdout,stderr=sys.stderr,close_fds=False); "
            f"open({str(child_pid)!r},'w').write(str(child.pid)); "
            "print('X4OK {\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\","
            "\"permissionDecisionReason\":\"adapter result\"}}')") if failure == "inherited-pipes" else (
                "import time; time.sleep(6)")
    launcher = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    env = dict(os.environ, X4_PYTHON=sys.executable, X4_NO_PYTHON_FALLBACK="1", X4_WRAPPER_TIMEOUT_S="1")
    payload = b"x" * (2 << 20) if failure == "unread-stdin" else b"{}"
    # BASELINE first, same launcher, payload and machine load, with an adapter that does
    # nothing: an absolute bound (3.5 s) was MEASURED at 3.31 s on an idle machine, so any
    # load turned PowerShell + Python startup into a failure of the deadline.
    (tree / "codex_adapter.py").write_text("pass", encoding="utf-8")
    start = time.monotonic()
    _run(launcher, tree / "codex-entry.ps1", "pre_tool_use", payload, env)
    baseline = time.monotonic() - start
    (tree / "codex_adapter.py").write_text(body, encoding="utf-8")
    start = time.monotonic()
    result = _run(launcher, tree / "codex-entry.ps1", "pre_tool_use", payload, env)
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "X4 GUARD INERT" in reason
    # The deadline is 1 s: the run may cost the no-op baseline plus that second plus slack,
    # and must never reach the adapter's 6 s sleep (that would be no deadline at all).
    assert elapsed < baseline + 1.0 + 1.5, (failure, elapsed, baseline)
    assert elapsed < 6.0, (failure, elapsed, baseline)
    if failure == "inherited-pipes":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, int(child_pid.read_text()))
        if handle:
            try:
                exit_code = wintypes.DWORD()
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                assert exit_code.value != 259, "owned pipe-holder remains alive after timeout"
            finally:
                kernel.CloseHandle(handle)


def _assert_wrapper_cancellation(tree, powershell):
    import ctypes
    from ctypes import wintypes
    ready = tree / "owned-processes.json"
    (tree / "codex_adapter.py").write_text(
        "import json,os,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])\n"
        f"open({str(ready)!r},'w').write(json.dumps([os.getpid(),child.pid]))\n"
        "time.sleep(20)\n", encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handles = []
    wrapper = subprocess.Popen([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(tree / "codex-entry.ps1"), "pre_tool_use"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, X4_PYTHON=sys.executable, X4_NO_PYTHON_FALLBACK="1"))
    try:
        wrapper.stdin.write(b"{}")
        wrapper.stdin.close()
        wrapper.stdin = None
        deadline = time.monotonic() + 8
        while not ready.is_file() and wrapper.poll() is None and time.monotonic() < deadline:
            time.sleep(.025)
        assert ready.is_file(), "owned adapter did not become ready"
        for pid in json.loads(ready.read_text()):
            handle = kernel.OpenProcess(0x100001, False, pid)  # synchronize + terminate
            assert handle, "owned process vanished before the cancellation control"
            handles.append(handle)
        wrapper.kill()  # Only the wrapper this test just started; no tree kill.
        wrapper.wait(timeout=3)
        assert all(kernel.WaitForSingleObject(handle, 2500) == 0 for handle in handles), \
            "owned adapter or descendant remains alive after wrapper cancellation"
    finally:
        # Handles identify our processes even if their numerical PIDs are reused.
        for handle in handles:
            if kernel.WaitForSingleObject(handle, 0) != 0:
                kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
        if wrapper.poll() is None:
            wrapper.kill()
        wrapper.communicate(timeout=5)


@pytest.mark.parametrize("powershell", POWERSHELLS or [pytest.param(None, marks=pytest.mark.skip(
    reason="wrapper cancellation requires native Windows PowerShell"))])
def test_wrapper_cancellation_terminates_owned_adapter_and_descendant(tree, powershell):
    _assert_wrapper_cancellation(tree, powershell)


@pytest.mark.parametrize("powershell", POWERSHELLS or [pytest.param(None, marks=pytest.mark.skip(
    reason="wrapper cancellation requires native Windows PowerShell"))])
def test_mutation_without_kill_on_close_is_detected(tree, powershell):
    script = tree / "codex-entry.ps1"
    text = script.read_text(encoding="utf-8")
    anchor = "limits.basic.flags=0x2000;"
    assert text.count(anchor) == 1
    script.write_text(text.replace(anchor, "limits.basic.flags=0;"), encoding="utf-8")
    with pytest.raises(AssertionError, match="remains alive after wrapper cancellation"):
        _assert_wrapper_cancellation(tree, powershell)

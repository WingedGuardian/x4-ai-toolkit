"""The no-detached-launch check inside the Codex and OpenCode adapters (4.3.1).

The shared funnel (x4guard.run_guard, through codex.judge/aggregate) turns a crashed guard into a DENY. This
check must FAIL OPEN, so it runs IN-PROCESS in the adapter, after the funnel's verdict, using the vendored core
detector (tools/modding_harness/detached.py). These tests call the adapters in-process
(codex.handle / opencode.handle), so they see the real funnel, the real detector and the real rendering.

Each decisive clause has a twin showing the clause decided the outcome: a fix that only denied everything, or
only allowed everything, would turn one half red.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from codex_testlib import HAS_PWSH, REPO, SANDBOX_KEYS, make_sandbox, native, parse_output
from _layout import require_repo

SRC = REPO / "agent" / "guards"
FLAG_LINE = 'subprocess.Popen(["x"], creationflags=0x8)'
OK_LINE = 'subprocess.Popen(["x"], creationflags=0x08000000)'
NOTE = "no-detached-launch NOT judged"


@pytest.fixture
def adapters(monkeypatch):
    """Use the source or shipped adapters. Both must share OpenCode's Codex core."""
    if SRC.is_dir():
        monkeypatch.syspath_prepend(str(SRC / "claude-hooks"))
        monkeypatch.syspath_prepend(str(SRC / "adapters"))
        codex_name, opencode_name = "codex", "opencode"
    else:
        require_repo(".opencode/hooks/opencode_adapter.py", why="checks the installed OpenCode adapter")
        monkeypatch.syspath_prepend(str(REPO / ".opencode" / "hooks"))
        codex_name, opencode_name = "codex_adapter", "opencode_adapter"
    saved = {n: sys.modules.pop(n, None) for n in ("codex", "opencode", "codex_adapter", "opencode_adapter")}
    try:
        codex = importlib.import_module(codex_name)
        opencode = importlib.import_module(opencode_name)
        assert opencode.core is codex
        # The backup hook is a sibling of the RENDERED adapter, not of the source one: backups are not under test here.
        monkeypatch.setattr(codex, "run_backups", lambda ops, base, deadline: None)
        yield codex, opencode
    finally:
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod


@pytest.fixture
def box(tmp_path, monkeypatch):
    """The sandbox roots, applied to THIS process (the adapters are called in-process)."""
    tmp, tk, env = make_sandbox(tmp_path)
    for k in (*SANDBOX_KEYS, "X4_CODEX_TOOL_LOG"):
        monkeypatch.setenv(k, env[k])
    for k in ("X4_BASH", "X4_CODEX_SHELL", "X4_GUARD", "X4_CODEX_BUDGET_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp)
    return tmp, tk


class Stub:
    """A stand-in for the core detector: records every call; optionally raises or stalls."""
    EXEMPT = ()

    def __init__(self, exc=None, delay=0.0, hit=None):
        self.calls, self.exc, self.delay, self.hit = [], exc, delay, hit

    def _go(self, name, args, kw):
        self.calls.append((name, args, kw))
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.hit

    def judge(self, *a, **k):
        return self._go("judge", a, k)

    def judge_patch(self, *a, **k):
        return self._go("judge_patch", a, k)


def use(monkeypatch, codex, stub):
    monkeypatch.setattr(codex, "_detached_module", lambda deadline: stub)
    return stub


def codex_run(codex, payload, shell="bash", monkeypatch=None):
    os.environ["X4_CODEX_SHELL"] = shell
    try:
        text = codex.handle("pre_tool_use", json.dumps(payload).encode("utf-8"))
    finally:
        os.environ.pop("X4_CODEX_SHELL", None)
    return parse_output(text.encode("utf-8"))


def shell_payload(tmp, cmd):
    return native("bash_powershell", tmp, command=cmd)


def patch_payload(tk, patch):
    return native("apply_patch_add", tk, command=patch)


def add_patch(rel, line):
    return f"*** Begin Patch\n*** Add File: {rel}\n+import subprocess\n+{line}\n*** End Patch"


def oc_call(tk, tool, args, shell="bash"):
    return {"v": 1, "tool": tool, "args": args, "directory": str(tk), "shell": shell, "session_id": "fixture:" + str(tk)}


def oc_run(opencode, payload):
    v = opencode.handle("pre", json.dumps(payload).encode("utf-8"))
    assert set(v) == {"v", "decision", "inert", "reason", "context"}, v
    return v


# ------------------------------------------------------------------ Codex ---------------------------------------- #

def test_codex_shell_popen_detached_is_denied_and_names_statement(adapters, box):
    codex, _ = adapters
    tmp, _tk = box
    cmd = "python -c 'import subprocess; subprocess.Popen([\"x\"], creationflags=0x8)'"
    d, reason = codex_run(codex, shell_payload(tmp, cmd), "bash")
    assert d == "deny", (d, reason)
    assert "no-detached-launch" in reason and "creationflags=0x8" in reason, reason
    assert "CREATE_NO_WINDOW" in reason, reason          # the fix is named
    ok = cmd.replace("0x8)", "0x08000000)")
    assert codex_run(codex, shell_payload(tmp, ok), "bash")[0] in ("allow", "advise")   # twin: the flag decided


@pytest.mark.skipif(not HAS_PWSH, reason="no PowerShell -- the PowerShell routing is NOT checked here")
def test_codex_powershell_env_interpreter_inline_is_denied(adapters, box):
    codex, _ = adapters
    tmp, _tk = box
    # MEASURED: with a variable interpreter the funnel itself refuses any inline code that starts a subprocess
    # (inert: the target cannot be resolved), so the statement here carries only the flag value.
    cmd = "& $env:PY -c 'opts = dict(creationflags=0x8)'"
    d, reason = codex_run(codex, shell_payload(tmp, cmd), "powershell")
    assert d == "deny" and "no-detached-launch" in reason and "creationflags=0x8" in reason, (d, reason)
    ok = cmd.replace("0x8)", "0x08000000)")
    assert codex_run(codex, shell_payload(tmp, ok), "powershell")[0] in ("allow", "advise")  # twin


def test_codex_apply_patch_adding_detached_flag_is_denied(adapters, box):
    codex, _ = adapters
    _tmp, tk = box
    d, reason = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "deny" and "no-detached-launch" in reason and "creationflags=0x8" in reason, (d, reason)


def test_codex_create_no_window_is_allowed(adapters, box):
    codex, _ = adapters
    _tmp, tk = box
    d, reason = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)))
    assert d in ("allow", "advise") and NOTE not in (reason or ""), (d, reason)


def test_codex_rg_for_flag_text_is_allowed(adapters, box):
    codex, _ = adapters
    _tmp, tk = box
    d, reason = codex_run(codex, shell_payload(tk / "dev" / "mymod", "rg creationflags=8 ."), "bash")
    assert d in ("allow", "advise") and NOTE not in (reason or ""), (d, reason)


def test_codex_shell_run_patch_is_judged(adapters, box):
    """An apply_patch run through the shell is judged on its body too (the heredoc is not an interpreter's)."""
    codex, _ = adapters
    tmp, tk = box
    body = add_patch("dev/mymod/launch.py", FLAG_LINE)
    payload = shell_payload(tmp, f"apply_patch <<'PATCH'\n{body}\nPATCH")
    payload["tool_input"]["workdir"] = str(tk)           # a known execution cwd (the funnel refuses without one)
    d, reason = codex_run(codex, payload, "bash")
    assert d == "deny" and "no-detached-launch" in reason, (d, reason)


def test_variable_command_word_search_is_refused_by_the_core_detector(adapters, box):
    """DOCUMENTED disagreement with the Claude hook: the core reads a variable command word as an interpreter."""
    codex, _ = adapters
    tmp, _tk = box
    d, reason = codex_run(codex, shell_payload(tmp, "\"$RG\" -e 'creationflags=8' src"), "bash")
    assert d == "deny" and "no-detached-launch" in reason, (d, reason)


# ------------------------------------------------------------------ OpenCode ------------------------------------- #

def test_opencode_write_and_edit_camelcase_are_judged(adapters, box):
    _, opencode = adapters
    _tmp, tk = box
    target = tk / "dev" / "mymod" / "launch.py"
    v = oc_run(opencode, oc_call(tk, "write", {"filePath": str(target), "content": f"import subprocess\n{FLAG_LINE}\n"}))
    assert v["decision"] == "deny" and not v["inert"] and "no-detached-launch" in v["reason"], v
    ok = oc_run(opencode, oc_call(tk, "write", {"filePath": str(target), "content": f"import subprocess\n{OK_LINE}\n"}))
    assert ok["decision"] in ("allow", "advise") and NOTE not in (ok["context"] or ""), ok   # twin
    target.write_text('import subprocess\nsubprocess.Popen(["x"])\n', encoding="utf-8")
    v = oc_run(opencode, oc_call(tk, "edit", {"filePath": str(target), "oldString": 'subprocess.Popen(["x"])',
                                              "newString": FLAG_LINE, "replaceAll": False}))
    assert v["decision"] == "deny" and "no-detached-launch" in v["reason"], v
    ok = oc_run(opencode, oc_call(tk, "edit", {"filePath": str(target), "oldString": 'subprocess.Popen(["x"])',
                                               "newString": OK_LINE}))
    assert ok["decision"] in ("allow", "advise") and NOTE not in (ok["context"] or ""), ok   # twin


def test_opencode_patch_tool_is_judged(adapters, box):
    _, opencode = adapters
    _tmp, tk = box
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "deny" and "no-detached-launch" in v["reason"], v
    ok = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", OK_LINE)}))
    assert ok["decision"] in ("allow", "advise") and NOTE not in (ok["context"] or ""), ok


def test_opencode_bash_is_judged_in_its_workdir(adapters, box):
    _, opencode = adapters
    tmp, tk = box
    cmd = "python -c 'import subprocess; subprocess.Popen([\"x\"], creationflags=0x8)'"
    v = oc_run(opencode, oc_call(tk, "bash", {"command": cmd, "workdir": str(tk / "dev")}))
    assert v["decision"] == "deny" and "no-detached-launch" in v["reason"], v
    ok = oc_run(opencode, oc_call(tk, "bash", {"command": cmd.replace("0x8)", "0x08000000)")}))
    assert ok["decision"] in ("allow", "advise"), ok


# ------------------------------------------------------------------ the verdict rules ---------------------------- #

def test_existing_deny_is_kept_and_detector_not_called(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub(hit="subprocess.Popen(...)"))
    d, reason = codex_run(codex, patch_payload(tk, add_patch("reference/libraries/x.py", FLAG_LINE)))
    assert d == "deny" and "no-detached-launch" not in reason, (d, reason)    # the funnel's own refusal, untouched
    ref = (tk / "reference" / "libraries" / "x.py").as_posix()
    v = oc_run(opencode, oc_call(tk, "write", {"filePath": ref, "content": f"import subprocess\n{FLAG_LINE}\n"}))
    assert v["decision"] == "deny" and "no-detached-launch" not in v["reason"], v
    assert stub.calls == [], "the detector was consulted although the funnel had already refused"
    use(monkeypatch, codex, stub)   # control: an allowed call DOES reach the stub
    codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)))
    assert stub.calls, "control: an allowed call never reached the detector, so the check above proved nothing"


@pytest.mark.parametrize("exc", [RuntimeError("boom"), RecursionError("deep"), TimeoutError("slow"), ImportError("gone"),
                                 SystemExit(3), KeyboardInterrupt(), GeneratorExit()],
                         ids=lambda e: type(e).__name__)
def test_detector_exception_fails_open_with_visible_note(adapters, box, monkeypatch, exc):
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub(exc=exc))
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and f"{NOTE}: {type(exc).__name__}" in text, (d, text)
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise" and f"{NOTE}: {type(exc).__name__}" in v["context"], v
    assert stub.calls


def test_missing_core_detached_module_fails_open_with_visible_note(adapters, box, monkeypatch, tmp_path):
    codex, opencode = adapters
    _tmp, tk = box
    empty = tmp_path / "no-toolkit"
    empty.mkdir()
    monkeypatch.setenv("X4_TOOLKIT", str(empty))      # no tools/modding_harness/detached.py anywhere on the way
    allow = {"decision": "allow", "inert": False, "reason": None, "context": None}
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(allow))
    monkeypatch.setattr(codex.save_policy, "LAST_ROOTS", {"toolkit": str(empty)}, raising=False)
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and f"{NOTE}: ImportError" in text, (d, text)
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise" and f"{NOTE}: ImportError" in v["context"], v


def test_exhausted_budget_skips_with_note(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub(hit="subprocess.Popen(...)"))
    allow = {"decision": "allow", "inert": False, "reason": None, "context": None}
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(allow))
    payload = patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE))
    v = codex.pre_tool_use(payload, "bash", time.monotonic() + 0.5)          # under one second left
    assert v["decision"] == "advise" and NOTE in v["context"], v
    assert stub.calls == [], "the detector ran with no time budget left"
    ok = codex.pre_tool_use(payload, "bash", time.monotonic() + 30)           # twin: time left, the stub is consulted
    assert ok["decision"] == "deny" and stub.calls
    # OpenCode: the budget runs out while the funnel runs, so only the note is left
    monkeypatch.setenv("X4_CODEX_BUDGET_S", "2")
    stub.calls.clear()
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: (time.sleep(1.3), dict(allow))[1])
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise" and NOTE in v["context"] and stub.calls == [], v


def test_slow_detector_is_abandoned_with_note(adapters, box, monkeypatch):
    codex, _ = adapters
    _tmp, tk = box
    use(monkeypatch, codex, Stub(delay=4.0))
    allow = {"decision": "allow", "inert": False, "reason": None, "context": None}
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(allow))
    payload = patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE))
    t0 = time.monotonic()
    v = codex.pre_tool_use(payload, "bash", time.monotonic() + 2.0)
    assert time.monotonic() - t0 < 3.5, "the adapter waited for the slow detector"
    assert v["decision"] == "advise" and f"{NOTE}: TimeoutError" in v["context"], v


def test_x4_guard_off_turns_detached_deny_into_advisory(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    monkeypatch.setenv("X4_GUARD", "off")
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and "X4 GUARDS OFF" in text and "no-detached-launch" in text and "creationflags=0x8" in text, (d, text)
    assert "DENIED" in text and "NOT enforced" in text, text
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise" and "no-detached-launch" in v["context"], v
    monkeypatch.delenv("X4_GUARD")                    # twin: the switch decided
    assert codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))[0] == "deny"


def test_own_detector_files_are_exempt(adapters, box):
    codex, _ = adapters
    _tmp, tk = box
    for name in ("no-detached-launch.py", "test_no_detached_launch.py", "verify-no-detached-launch.py",
                 "tests/test_adapter_detached_launch.py"):
        d, reason = codex_run(codex, patch_payload(tk, add_patch(f"dev/mymod/{name}", FLAG_LINE)))
        assert d in ("allow", "advise") and "no-detached-launch:" not in (reason or ""), (name, d, reason)
    d, reason = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/other.py", FLAG_LINE)))   # twin
    assert d == "deny" and "no-detached-launch" in reason, (d, reason)


def test_exempt_list_extends_the_core_default(adapters, box):
    codex, _ = adapters
    tmp, tk = box
    det = codex._detached_module(time.monotonic() + 30)
    assert set(det.EXEMPT) <= set(codex.detached_exempt(det))
    assert "no-detached-launch.py" in codex.detached_exempt(det)


def test_claude_hook_docstring_names_the_codex_and_opencode_wiring():
    require_repo(".claude/hooks/no-detached-launch.py", why="checks the shipped Claude detector")
    text = (REPO / ".claude" / "hooks" / "no-detached-launch.py").read_text(encoding="utf-8")
    assert "NOT wired for\nCodex or OpenCode" not in text and "Codex and OpenCode launches are therefore not checked" not in text
    flat = " ".join(text.split()).lower()
    assert "core detector" in flat and "modding_harness/detached.py" in flat


# ------------------------------------------------------------------ review round: every exception class ----------- #

BASE_EXCS = [SystemExit(2), KeyboardInterrupt(), GeneratorExit()]
ALLOW = {"decision": "allow", "inert": False, "reason": None, "context": None}


@pytest.mark.parametrize("exc", BASE_EXCS + [RuntimeError("boom")], ids=lambda e: type(e).__name__)
def test_an_error_while_reading_the_call_fails_open(adapters, box, monkeypatch, exc):
    """make_items() is inside the same fail-open as the judging (a SystemExit there became an inert deny)."""
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub())

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(codex, "detached_items", boom)
    monkeypatch.setattr(opencode, "detached_items", boom)
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)))
    assert d == "advise" and f"{NOTE}: {type(exc).__name__}" in text, (d, text)
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", OK_LINE)}))
    assert v["decision"] == "advise" and f"{NOTE}: {type(exc).__name__}" in v["context"], v
    assert stub.calls == []


@pytest.mark.parametrize("exc", BASE_EXCS, ids=lambda e: type(e).__name__)
def test_an_error_while_loading_the_detector_fails_open(adapters, box, monkeypatch, exc):
    codex, _ = adapters
    _tmp, tk = box

    def boom(deadline):
        raise exc
    monkeypatch.setattr(codex, "_detached_module", boom)
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and f"{NOTE}: {type(exc).__name__}" in text, (d, text)


# ------------------------------------------------------------------ review round: the funnel's other verdicts ----- #

def test_a_result_after_the_detector_deadline_is_discarded(adapters, monkeypatch):
    from types import SimpleNamespace

    codex, _ = adapters
    clock = [0]
    monkeypatch.setattr(codex, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def load(_deadline):
        clock[0] = 100
        return Stub(hit="synthetic late hit")

    monkeypatch.setattr(codex, "_detached_module", load)
    value = codex.detached_verdict(dict(ALLOW), lambda: [("shell", "bash", "echo harmless", ".")], 5)
    assert value["decision"] == "advise" and NOTE in value["context"], value


def test_an_ask_and_an_inert_deny_are_kept_and_the_detector_is_not_called(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub(hit="subprocess.Popen(...)"))
    payload = patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE))
    for v in ({"decision": "ask", "inert": False, "reason": "ASK-REASON", "context": None},
              {"decision": "deny", "inert": True, "reason": "X4 GUARD INERT: INERT-REASON", "context": None}):
        monkeypatch.setattr(codex.save_policy, "batch", lambda *a, _v=v, **k: dict(_v))
        got = codex.pre_tool_use(payload, "bash", time.monotonic() + 30)
        assert got["decision"] == v["decision"] and got["inert"] == v["inert"] and v["reason"] in got["reason"], got
    assert stub.calls == []


def test_a_real_ask_from_the_funnel_is_kept(adapters, box, monkeypatch):
    codex, _ = adapters
    tmp, tk = box
    stub = use(monkeypatch, codex, Stub(hit="subprocess.Popen(...)"))
    d, reason = codex_run(codex, patch_payload(tk, add_patch(str(tmp / "profile" / "launch.py"), FLAG_LINE)))
    assert d == "ask" and "no-detached-launch" not in reason, (d, reason)
    assert stub.calls == []


def test_a_funnel_advisory_survives_next_to_the_not_judged_note(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    use(monkeypatch, codex, Stub(exc=RuntimeError("boom")))
    advise = {"decision": "advise", "inert": False, "reason": None, "context": "FUNNEL-ADVICE-TEXT"}
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(advise))
    got = codex.pre_tool_use(patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)), "bash", time.monotonic() + 30)
    assert got["decision"] == "advise" and "FUNNEL-ADVICE-TEXT" in got["context"] and NOTE in got["context"], got
    assert got["context"].index(NOTE) < got["context"].index("FUNNEL-ADVICE-TEXT")      # the note leads
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", OK_LINE)}))
    assert v["decision"] == "advise" and "FUNNEL-ADVICE-TEXT" in v["context"] and NOTE in v["context"], v
    use(monkeypatch, codex, Stub())                 # twin: a clean call leaves the advisory exactly as it was
    got = codex.pre_tool_use(patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)), "bash", time.monotonic() + 30)
    assert got["context"] == "FUNNEL-ADVICE-TEXT", got


def test_write_stdin_lines_are_judged(adapters, box):
    codex, _ = adapters
    tmp, tk = box
    payload = native("bash_powershell", tk / "dev" / "mymod")
    payload["tool_name"] = "write_stdin"
    payload["tool_input"] = {"session_id": 1, "chars": "python -c 'opts = dict(creationflags=0x8)'\n"}
    d, reason = codex_run(codex, payload, "bash")
    assert d == "deny" and "no-detached-launch" in reason and "creationflags=0x8" in reason, (d, reason)
    payload["tool_input"] = {"session_id": 1, "chars": "python -c 'opts = dict(creationflags=0x8)'"}      # twin: never submitted
    assert codex_run(codex, payload, "bash")[0] in ("allow", "advise")


def test_opencode_exhausted_budget_skips_with_note(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    stub = use(monkeypatch, codex, Stub(hit="subprocess.Popen(...)"))
    monkeypatch.setenv("X4_CODEX_BUDGET_S", "2")
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: (time.sleep(1.3), dict(ALLOW))[1])
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise" and not v["inert"] and NOTE in v["context"] and "time budget" in v["context"], v
    assert stub.calls == []


def test_a_refused_call_does_not_consume_a_pending_profile_approval(adapters, box, monkeypatch):
    codex, _ = adapters
    _tmp, tk = box
    consumed = []
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(ALLOW))
    monkeypatch.setattr(codex.profile_approval, "prepare", lambda payload, calls, verdict, deadline: (verdict, ("pending",)))
    monkeypatch.setattr(codex.profile_approval, "consume", lambda pending: consumed.append(pending))
    payload = patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE))
    got = codex.pre_tool_use(payload, "bash", time.monotonic() + 30)
    assert got["decision"] == "deny" and "no-detached-launch" in got["reason"] and consumed == [], (got, consumed)
    got = codex.pre_tool_use(patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE)), "bash", time.monotonic() + 30)
    assert got["decision"] in ("allow", "advise") and consumed == [("pending",)]      # twin: an allowed call consumes


# ------------------------------------------------------------------ review round: GUARDS-OFF.log ------------------- #

def _off_log(tmp):
    return tmp / "backups" / "GUARDS-OFF.log"


def test_guard_off_deny_is_logged_like_the_shell_guards(adapters, box, monkeypatch):
    import re
    codex, opencode = adapters
    tmp, tk = box
    monkeypatch.setenv("X4_GUARD", "off")
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise", (d, text)
    lines = _off_log(tmp).read_text(encoding="utf-8").splitlines()
    rec = [l for l in lines if "no-detached-launch would have DENIED" in l]
    assert len(rec) == 1 and re.match(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] no-detached-launch would have DENIED: no-detached-launch: ", rec[0]), lines
    assert "creationflags=0x8" in rec[0] and "\r" not in rec[0] and len(rec[0].split("DENIED: ", 1)[1]) <= 300
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "advise"
    assert len([l for l in _off_log(tmp).read_text(encoding="utf-8").splitlines() if "no-detached-launch would have" in l]) == 2


def test_guard_off_log_is_skipped_for_a_check_and_when_the_guards_are_on(adapters, box, monkeypatch):
    codex, _ = adapters
    tmp, tk = box
    monkeypatch.setenv("X4_GUARD", "off")
    monkeypatch.setenv("X4_GUARD_CHECK", "1")          # the guards' own check protocol promises no side effects
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and "no-detached-launch" in text and not _off_log(tmp).exists(), (d, text)
    monkeypatch.delenv("X4_GUARD_CHECK")
    monkeypatch.delenv("X4_GUARD")                     # twin: guards on, a deny, nothing logged
    assert codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))[0] == "deny"
    assert not _off_log(tmp).exists()


def test_an_unwritable_guard_off_log_never_changes_the_verdict(adapters, box, monkeypatch):
    codex, _ = adapters
    tmp, tk = box
    (tmp / "blocker").write_text("a file where the log folder should be", encoding="utf-8")
    monkeypatch.setenv("X4_BACKUPS", str(tmp / "blocker"))
    monkeypatch.setenv("X4_GUARD", "off")
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(ALLOW))
    got = codex.pre_tool_use(patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)), "bash", time.monotonic() + 30)
    assert got["decision"] == "advise" and "no-detached-launch" in got["context"], got


# ------------------------------------------------------------------ review round: no second Bash start ------------- #

def test_the_detector_never_starts_the_root_reader_a_second_time(adapters, box, monkeypatch, tmp_path):
    """A hook-only install keeps its tools in another toolkit: the detector must reuse the roots the funnel read."""
    from codex_testlib import copy_shared_runtime
    codex, opencode = adapters
    tmp, tk = box
    other = tmp_path / "other-toolkit"
    copy_shared_runtime(other)
    (tk / "tools" / "modding_harness" / "detached.py").unlink()       # X4_TOOLKIT holds no detector
    real, seen = codex.save_policy.read_roots, []

    def counting(deadline):
        seen.append(1)
        roots = dict(real(deadline), toolkit=str(other))              # the roots name the toolkit that has it
        codex.save_policy.LAST_ROOTS = roots                          # what the real read_roots records
        return roots
    monkeypatch.setattr(codex.save_policy, "read_roots", counting)
    payload = patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE))
    d, reason = codex_run(codex, payload)
    assert d == "deny" and "no-detached-launch" in reason, (d, reason)    # the roots-named toolkit's detector ran
    with_detector = len(seen)
    assert with_detector >= 1
    seen.clear()
    with monkeypatch.context() as m:
        m.setattr(codex, "detached_verdict", lambda v, make, deadline: v)
        codex_run(codex, payload)
    assert with_detector == len(seen), f"the detector started the root reader {with_detector - len(seen)} extra time(s)"
    seen.clear()
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "deny" and "no-detached-launch" in v["reason"], v
    assert len(seen) <= with_detector


def test_no_roots_read_in_this_call_means_no_bash_start_and_a_note(adapters, box, monkeypatch, tmp_path):
    codex, _ = adapters
    _tmp, tk = box
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("X4_TOOLKIT", str(empty))
    monkeypatch.setattr(codex.save_policy, "LAST_ROOTS", None, raising=False)
    started = []
    monkeypatch.setattr(codex.save_policy, "read_roots", lambda deadline: started.append(1) or {"toolkit": str(tk)})
    got = codex.detached_verdict(dict(ALLOW), lambda: [("shell", "bash", "echo hi", str(tk))], time.monotonic() + 30)
    assert got["decision"] == "advise" and f"{NOTE}: ImportError" in got["context"], got
    assert started == [], "the detector started the root reader itself"


def test_roots_from_an_earlier_call_are_not_used(adapters, box, monkeypatch, tmp_path):
    codex, _ = adapters
    tmp, tk = box
    empty = tmp_path / "empty2"
    empty.mkdir()
    monkeypatch.setenv("X4_TOOLKIT", str(empty))
    monkeypatch.setattr(codex.save_policy, "batch", lambda *a, **k: dict(ALLOW))
    monkeypatch.setattr(codex.save_policy, "LAST_ROOTS", {"toolkit": str(tk)}, raising=False)       # a stale earlier call
    d, text = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert d == "advise" and f"{NOTE}: ImportError" in text, (d, text)


# ------------------------------------------------------------------ review round 2: the REAL read_roots records ---- #

def test_the_real_read_roots_records_the_roots_the_detector_then_uses(adapters, box, monkeypatch, tmp_path):
    """Only the Bash call is faked (its environment names another toolkit); read_roots itself is the real one.
    If it stopped recording LAST_ROOTS, a hook-only install would answer NOT judged on every call."""
    import subprocess as sp
    import types
    from codex_testlib import copy_shared_runtime
    codex, opencode = adapters
    _tmp, tk = box
    other = tmp_path / "toolkit-with-the-detector"
    copy_shared_runtime(other)
    (tk / "tools" / "modding_harness" / "detached.py").unlink()       # X4_TOOLKIT holds no detector
    real_run, bash_calls = sp.run, []

    def fake_run(cmd, *args, **kw):
        if any("x4_emit_guard_roots" in str(part) for part in cmd):   # the root reader's Bash call, and only that
            bash_calls.append(1)
            kw["env"] = dict(os.environ, X4_TOOLKIT=str(other))
        return real_run(cmd, *args, **kw)
    shim = types.SimpleNamespace(**vars(sp))
    shim.run = fake_run
    monkeypatch.setattr(codex.save_policy, "subprocess", shim)
    d, reason = codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", FLAG_LINE)))
    assert bash_calls, "the root reader never ran, so this test proved nothing"
    assert d == "deny" and "no-detached-launch" in reason, (d, reason)
    assert codex.save_policy.LAST_ROOTS and codex.save_policy.LAST_ROOTS["toolkit"].endswith("toolkit-with-the-detector")
    v = oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", FLAG_LINE)}))
    assert v["decision"] == "deny" and "no-detached-launch" in v["reason"], v


def test_both_adapters_reset_the_recorded_roots_at_the_start_of_a_call(adapters, box, monkeypatch):
    codex, opencode = adapters
    _tmp, tk = box
    seen = []

    def batch(*a, **k):
        seen.append(codex.save_policy.LAST_ROOTS)          # what the funnel starts from
        return dict(ALLOW)
    monkeypatch.setattr(codex.save_policy, "batch", batch)
    use(monkeypatch, codex, Stub())
    for run in (lambda: codex_run(codex, patch_payload(tk, add_patch("dev/mymod/launch.py", OK_LINE))),
                lambda: oc_run(opencode, oc_call(tk, "apply_patch", {"patchText": add_patch("dev/mymod/launch.py", OK_LINE)}))):
        monkeypatch.setattr(codex.save_policy, "LAST_ROOTS", {"toolkit": "a stale earlier call"}, raising=False)
        run()
    assert seen == [None, None], seen

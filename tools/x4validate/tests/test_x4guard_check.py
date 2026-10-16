"""x4guard check: one side-effect-free front door to the guards, for any agent.

A guard that cannot run must never read as allow -- it is an inert deny with the cause named.
"""
import importlib.util
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from codex_testlib import copy_shared_runtime, process_alive

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
X4GUARD = REPO / ".claude" / "hooks" / "x4guard.py"
HAS_PWSH = bool(shutil.which("pwsh") or shutil.which("powershell"))


@pytest.fixture
def sandbox(tmp_path):
    tk, game = tmp_path / "toolkit", tmp_path / "X4 Foundations"
    copy_shared_runtime(tk)
    for d in (tk / "dev" / "mymod", tk / "reference" / "libraries", game / "extensions",
              tmp_path / "profile" / "save", tmp_path / "mods", tmp_path / "docs", tmp_path / "backups"):
        d.mkdir(parents=True)
    (tk / "reference" / "libraries" / "wares.xml").write_text("ref\n", encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tk), X4_GAME=str(game), X4_REFERENCE=str(tk / "reference"),
               X4_PROFILE=str(tmp_path / "profile"), X4_MODS=str(tmp_path / "mods"),
               X4_EXTENSIONS=str(game / "extensions"), X4_SAVES=str(tmp_path / "profile" / "save"),
               X4_DOCUMENTS=str(tmp_path / "docs"), X4_BACKUPS=str(tmp_path / "backups"),
               X4_CONFIG="/nonexistent",
               # These tests judge VERDICTS, not speed. X4_GUARD_TIMEOUT_S is one budget per CHECK
               # (lane E), and a real delete check took 22-24 s of the default 25 under load
               # (MEASURED 2026-10-02), so the default made verdict tests flake inert. The budget
               # tests set their own value.
               X4_GUARD_TIMEOUT_S="120")
    env.pop("X4_BASH", None)
    return tmp_path, tk, env


def check(env, *args, script=X4GUARD):
    r = subprocess.run([sys.executable, str(script), "check", *args], capture_output=True, env=env, timeout=120)
    return r.returncode, (json.loads(r.stdout) if r.returncode == 0 else None), r.stderr.decode("utf-8", "replace")


def test_shell_bash_delete_in_reference_denies(sandbox):
    _, tk, env = sandbox
    rc, v, _ = check(env, "--kind", "shell", "--shell", "bash", "--command",
                     f"rm -rf '{(tk / 'reference' / 'libraries').as_posix()}'")
    assert rc == 0 and v["decision"] == "deny" and not v["inert"] and v["guards"] == ["protect-bash.sh"]


@pytest.mark.skipif(not HAS_PWSH, reason="no PowerShell to translate with -- routing NOT checked here")
def test_shell_routing_decides_the_verdict(sandbox):
    """The measured Codex hole: PowerShell text judged as bash. The flag must decide."""
    _, tk, env = sandbox
    cmd = f"Set-Content -Path '{tk / 'reference' / 'libraries' / 'wares.xml'}' -Value 'x'"
    _, as_ps, _ = check(env, "--kind", "shell", "--shell", "powershell", "--command", cmd)
    _, as_bash, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", cmd)
    assert as_ps["decision"] == "deny" and not as_ps["inert"]
    assert as_bash["decision"] != "deny"   # if this ever denies, pick a new twin: routing is no longer shown


def test_shell_echo_allows(sandbox):
    _, _, env = sandbox
    rc, v, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", "echo hello")
    assert rc == 0 and v["decision"] == "allow" and not v["inert"]


@pytest.mark.parametrize("kind", ["write", "delete"])
def test_write_and_delete_into_reference_deny(sandbox, kind):
    _, tk, env = sandbox
    rc, v, _ = check(env, "--kind", kind, "--path", str(tk / "reference" / "libraries" / "wares.xml"))
    assert rc == 0 and v["decision"] == "deny"
    # a delete is judged by protect-files AND an rm through protect-bash (final-review ruling I4)
    assert v["guards"] == (["protect-files.sh"] if kind == "write" else ["protect-files.sh", "protect-bash.sh"])


def test_write_path_with_spaces_and_backslashes(sandbox):
    tmp, _, env = sandbox
    p = str(tmp / "X4 Foundations" / "libraries" / "wares.xml").replace("/", "\\")
    _, v, _ = check(env, "--kind", "write", "--path", p)
    assert v["decision"] == "deny" and not v["inert"]    # a base-game file, backslashes and a space


def test_manifest_advises_and_profile_asks(sandbox):
    tmp, tk, env = sandbox
    _, adv, _ = check(env, "--kind", "write", "--path", str(tk / "dev" / "mymod" / "content.xml"))
    _, ask, _ = check(env, "--kind", "write", "--path", str(tmp / "profile" / "content.xml"))
    assert adv["decision"] == "advise" and ask["decision"] == "ask"


def test_check_has_no_side_effects(sandbox):
    """backup-before-edit.sh copies an EXISTING file into $X4_BACKUPS. A check must never run it.
    The target exists (the backup hook skips absent files, which would make this vacuous) and
    the check must have produced a real verdict (a check that never ran has no side effects)."""
    tmp, tk, env = sandbox
    target = tk / "dev" / "mymod" / "content.xml"
    target.write_text("<content/>\n", encoding="utf-8")
    rc, v, _ = check(env, "--kind", "write", "--path", str(target))
    assert rc == 0 and v["decision"] == "advise" and not v["inert"]
    assert list((tmp / "backups").iterdir()) == []


def test_missing_guard_is_inert_deny(sandbox, tmp_path):
    _, _, env = sandbox
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy2(X4GUARD, lone / "x4guard.py")
    rc, v, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", "echo hi", script=lone / "x4guard.py")
    assert rc == 0 and v["decision"] == "deny" and v["inert"] and "missing" in v["reason"]


def test_wsl_bash_refused(sandbox):
    _, _, env = sandbox
    env = dict(env, X4_BASH=r"C:\Windows\System32\bash.exe")
    _, v, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", "echo hi")
    assert v["decision"] == "deny" and v["inert"], v
    # The WSL-stub trap exists on Windows only (resolve_bash checks it under os.name == "nt").
    # Off Windows the same X4_BASH is a path to nothing, and THAT must be the reason given --
    # still an inert deny, never an allow (CI5, ubuntu 2026-10-05).
    if os.name == "nt":
        assert "WSL" in v["reason"], v
    else:
        assert "points at nothing" in v["reason"] and "WSL" not in v["reason"], v


def test_missing_bash_is_inert_deny(sandbox, tmp_path):
    _, _, env = sandbox
    env = dict(env, X4_BASH=str(tmp_path / "no-such-bash.exe"))
    _, v, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", "echo hi")
    assert v["decision"] == "deny" and v["inert"]


def test_unparseable_guard_output_is_inert_deny():
    sys.path.insert(0, str(X4GUARD.parent))
    try:
        import x4guard
    finally:
        sys.path.pop(0)
    with pytest.raises(ValueError):
        x4guard.parse_hook_output("{not json")
    with pytest.raises(ValueError):
        x4guard.parse_hook_output('{"hookSpecificOutput": {"permissionDecision": "maybe"}}')


def test_usage_error_is_rc2(sandbox):
    _, _, env = sandbox
    rc, _, err = check(env, "--kind", "shell", "--shell", "bash")
    assert rc == 2 and "--command" in err


# ---------------------------------------------------------------- final-review fixes (I1-I5)

def _check_in(env, cwd, *args, script=X4GUARD):
    r = subprocess.run([sys.executable, str(script), "check", *args], capture_output=True, env=env,
                       timeout=120, cwd=str(cwd))
    return json.loads(r.stdout)


def test_I1_relative_path_is_resolved_from_the_callers_cwd(sandbox):
    """Codex apply_patch paths are relative; judged unresolved, a reference/ write was ALLOWED."""
    _, tk, env = sandbox
    v = _check_in(env, tk, "--kind", "write", "--path", "reference/libraries/wares.xml")
    assert v["decision"] == "deny" and not v["inert"]


def test_F_a_relative_shell_command_is_judged_from_the_callers_cwd(sandbox):
    """Lane F: the shell check sent no `cwd`, so `rm -f reference/...` from the folder holding
    reference/ was ALLOWED while the absolute spelling denied. The caller's cwd is the payload's."""
    _, tk, env = sandbox
    v = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", "rm -f reference/libraries/wares.xml")
    assert v["decision"] == "deny" and not v["inert"]


def test_F_TWIN_the_same_relative_command_from_an_unrelated_cwd_allows(sandbox):
    tmp, _, env = sandbox
    v = _check_in(env, tmp, "--kind", "shell", "--shell", "bash", "--command",   # under no root
                  "rm -f reference/libraries/wares.xml")
    assert v["decision"] == "allow" and not v["inert"]


def _hooks_copy(dst):
    shutil.copytree(X4GUARD.parent, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst / "x4guard.py"


def test_I2_a_copy_outside_claude_hooks_refuses_without_X4_TOOLKIT(sandbox, tmp_path):
    """The SOURCE copy (agent/guards/claude-hooks) derives the toolkit root from its own folder
    and so never finds the configured roots: it allowed a hard-blocked write. It must refuse."""
    _, tk, env = sandbox
    script = _hooks_copy(tmp_path / "agent" / "guards" / "claude-hooks")
    env = {k: v for k, v in env.items() if k not in ("X4_TOOLKIT", "CLAUDE_PROJECT_DIR")}
    v = _check_in(env, tmp_path, "--kind", "write", "--path", str(tk / "reference" / "libraries" / "wares.xml"),
                  script=script)
    assert v["decision"] == "deny" and v["inert"] and ".claude/hooks" in v["reason"]


def test_I2_TWIN_a_deployed_claude_hooks_copy_is_not_refused_for_location(tmp_path):
    """The twin of the location clause only. Root RESOLUTION is not asserted here: under %TEMP%
    Git Bash spells the tree /tmp/..., the payload spells C:/.../Temp/..., and they never match
    (MEASURED 2026-10-01), so a tmp tree cannot show a real install's verdict."""
    root = tmp_path / "tk2"
    copy_shared_runtime(root)
    script = _hooks_copy(root / ".claude" / "hooks")
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_") and k != "CLAUDE_PROJECT_DIR"}
    env["X4_CONFIG"] = "/nonexistent"
    v = _check_in(env, root, "--kind", "write", "--path", str(root / "x.txt"), script=script)
    assert not v["inert"], v

@pytest.mark.skipif(not HAS_PWSH, reason="no PowerShell -- the untranslatable path is NOT checked here")
def test_I3_a_guard_that_checked_nothing_is_inert_not_a_plain_ask(sandbox):
    _, tk, env = sandbox
    env = dict(env, X4_PWSH=str(tk / "no-such-pwsh.exe"))
    v = _check_in(env, tk, "--kind", "shell", "--shell", "powershell", "--command",
                  f"Remove-Item -Force '{tk / 'reference' / 'libraries' / 'wares.xml'}'")
    assert v["decision"] == "deny" and v["inert"] and "NOTHING was checked" in v["reason"], v
    assert "X4 save dispatcher" in v["reason"], v


def test_I4_a_delete_is_at_least_as_strict_as_rm(sandbox):
    tmp, tk, env = sandbox
    target = tmp / "profile" / "save" / "quick.xml.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    rank = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}
    d = _check_in(env, tk, "--kind", "delete", "--path", str(target))
    w = _check_in(env, tk, "--kind", "write", "--path", str(target))
    s = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", f"rm -f '{target.as_posix()}'")
    s2 = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", f"rm -rf -- '{target.as_posix()}'")
    assert rank[d["decision"]] == max(rank[w["decision"]], rank[s["decision"]], rank[s2["decision"]]), (d, w, s, s2)
    assert d["guards"] == ["protect-files.sh", "protect-bash.sh"]
    assert target.exists()                      # verdict only: nothing executed


@pytest.mark.parametrize("body", ["exit 1", "printf 'not json'", "sleep 6"])
def test_I5_a_guard_that_fails_is_an_inert_deny(sandbox, tmp_path, body):
    _, tk, env = sandbox
    hooks = tmp_path / "stub" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(X4GUARD, hooks / "x4guard.py")
    (hooks / "protect-bash.sh").write_text(f"cat >/dev/null\n{body}\n", encoding="utf-8")
    env = dict(env, X4_GUARD_TIMEOUT_S="2")
    v = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", "echo hi",
                  script=hooks / "x4guard.py")
    assert v["decision"] == "deny" and v["inert"], v


@pytest.mark.skipif(not HAS_PWSH, reason="no PowerShell -- the untranslatable path is NOT checked here")
def test_guard_signals_not_checked_with_exit_2_only_under_X4_GUARD_CHECK(sandbox):
    """Structured 'could not evaluate' signal (ported from the Codex audit session's game-root
    edit): with X4_GUARD_CHECK=1 a not-checked state exits 2; the native hook instead
    emits an explicit deny with exit0, preserving the native hard-block protocol."""
    _, tk, env = sandbox
    env = dict(env, X4_PWSH=str(tk / "no-such-pwsh.exe"))
    payload = json.dumps({"tool_name": "PowerShell", "tool_input": {"command":
              f"Remove-Item -Force '{tk / 'reference' / 'libraries' / 'wares.xml'}'"}}).encode()
    bash = shutil.which("bash.exe") or shutil.which("bash")
    hook = str(X4GUARD.parent / "protect-bash.sh")
    plain = subprocess.run([bash, hook], input=payload, capture_output=True, env=env, timeout=120)
    check = subprocess.run([bash, hook], input=payload, capture_output=True, timeout=120,
                           env=dict(env, X4_GUARD_CHECK="1"))
    assert plain.returncode == 0 and b'"deny"' in plain.stdout.replace(b" ", b"")
    expected = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "Shell scope could not be completely evaluated; use literal "
                                    "targets and an available parser. This call was not allowed."}}
    assert json.loads(plain.stdout) == expected
    assert check.returncode == 2
    # The shell transport turns the dispatcher's nonzero check result into its
    # own structured refusal; exit2 still distinguishes it from a native verdict.
    expected["hookSpecificOutput"]["permissionDecisionReason"] = (
        "X4 save dispatcher did not return a complete decision; this call was not allowed.")
    assert json.loads(check.stdout) == expected


# ---------------------------------------------------------------- lane E (Plan 2) helpers

def _alive(pid: int) -> bool:
    """Is this PID a live process? Windows: STILL_ACTIVE exit code. POSIX: kill(pid, 0)."""
    return process_alive(pid)


def _wait_dead(pid: int, within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while _alive(pid) and time.monotonic() < end:
        time.sleep(0.2)
    return not _alive(pid)


def _reap(pid: int | None) -> None:
    if pid and _alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, signal.SIGKILL)


def _stub_hooks(tmp_path, bodies: dict) -> Path:
    """A deployed-looking .claude/hooks holding this x4guard.py and the given stub guards."""
    hooks = tmp_path / "stub" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(X4GUARD, hooks / "x4guard.py")
    for name, body in bodies.items():
        (hooks / name).write_bytes(body.encode("utf-8"))
    return hooks


def _grandchild(pidfile: Path, seconds: int = 60) -> str:
    """A guard whose CHILD inherits the pipes, records its own PID, and outlives any budget."""
    py = Path(sys.executable).as_posix()
    return (f"cat >/dev/null\n'{py}' -c \"import os,time;open(r'{pidfile.as_posix()}','w')"
            f".write(str(os.getpid()));time.sleep({seconds})\"\n")


def _load(script: Path):
    spec = importlib.util.spec_from_file_location(f"x4guard_under_test_{abs(hash(str(script)))}", script)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_E1_a_timeout_is_bounded_and_kills_the_guards_descendants(sandbox, tmp_path):
    """MEASURED 2026-10-02: a guard's grandchild held the pipes and subprocess.run(timeout=)
    waited for it (sleep 8 under a 2 s budget took 10.9 s). The grandchild here sleeps 60 s."""
    _, tk, env = sandbox
    pidfile = tmp_path / "grandchild.pid"
    hooks = _stub_hooks(tmp_path, {"protect-bash.sh": _grandchild(pidfile)})
    t0 = time.monotonic()
    v = _check_in(dict(env, X4_GUARD_TIMEOUT_S="3"), tk, "--kind", "shell", "--shell", "bash",
                  "--command", "echo hi", script=hooks / "x4guard.py")
    wall = time.monotonic() - t0
    assert pidfile.exists(), "the grandchild never started -- this test proved nothing"
    pid = int(pidfile.read_text())
    try:
        assert v["decision"] == "deny" and v["inert"] and "timed out" in v["reason"], v
        assert wall < 20, f"{wall:.1f}s: a 3 s budget did not bound the check"
        assert _wait_dead(pid), "the guard's grandchild survived the timeout"
    finally:
        _reap(pid)


def _msys_grandchild(pidfile: Path) -> str:
    """A guard that backgrounds an MSYS program (Git Bash's own `sleep`) and records its OS PID
    (/proc/<pid>/winpid under MSYS, the bash PID elsewhere). Under Git Bash such a process is not
    linked to the guard by Windows parent PIDs, so `taskkill /T` never reaches it."""
    return (f"cat >/dev/null\nsleep 61 & p=$!\n"
            f"cat /proc/$p/winpid > '{pidfile.as_posix()}' 2>/dev/null || echo $p > '{pidfile.as_posix()}'\n"
            "sleep 62\n")


def test_E1_an_MSYS_grandchild_dies_with_the_guard_too(sandbox, tmp_path):
    """R2-F4 (v4.0.0 review, MEASURED with scratch-R2/killprobe.py): after a timed-out check,
    a backgrounded `sleep` and the foreground `sleep` of the guard were still alive -- the
    docstring's "the guard's whole process tree is killed" was false for MSYS descendants. The
    guard now runs in a Job Object that holds every descendant."""
    _, tk, env = sandbox
    pidfile = tmp_path / "msys.pid"
    hooks = _stub_hooks(tmp_path, {"protect-bash.sh": _msys_grandchild(pidfile)})
    v = _check_in(dict(env, X4_GUARD_TIMEOUT_S="3"), tk, "--kind", "shell", "--shell", "bash",
                  "--command", "echo hi", script=hooks / "x4guard.py")
    assert pidfile.exists() and pidfile.read_text().strip(), "the grandchild never started -- this test proved nothing"
    pid = int(pidfile.read_text().strip())
    try:
        assert v["decision"] == "deny" and v["inert"] and "timed out" in v["reason"], v
        assert _wait_dead(pid), "the guard's MSYS grandchild survived the timeout"
    finally:
        _reap(pid)


def test_E1_TWIN_the_bound_holds_even_when_the_tree_kill_does_not(sandbox, tmp_path, monkeypatch):
    """Twin for the DRAIN clause: with the tree kill reduced to killing the root only, the
    grandchild keeps the pipes open -- and the check must STILL return on time."""
    _, tk, env = sandbox
    pidfile = tmp_path / "grandchild.pid"
    hooks = _stub_hooks(tmp_path, {"protect-bash.sh": _grandchild(pidfile)})
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(hooks / "x4guard.py")
    monkeypatch.setattr(g, "TIMEOUT_S", 3.0)
    monkeypatch.setattr(g, "_kill_tree", lambda proc, job=None: proc.kill())
    t0 = time.monotonic()
    v = g.verdict_for("shell", "bash", "echo hi", None)
    wall = time.monotonic() - t0
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    try:
        assert pid, "the grandchild never started -- this test proved nothing"
        assert v["decision"] == "deny" and v["inert"], v
        assert wall < 3 + g.DRAIN_GRACE_S + 6, f"{wall:.1f}s: the drain is not bounded"
        assert _alive(pid), "control: the tree kill was meant to be OFF here"
    finally:
        _reap(pid)


def _fake_runner(g, monkeypatch, costs: dict):
    """Replace the subprocess layer with a clock: each guard 'takes' costs[script] seconds and
    times out if that exceeds the timeout it was GIVEN. Records (script, timeout given)."""
    clock, seen = [1000.0], []
    monkeypatch.setattr(g, "_clock", lambda: clock[0])
    monkeypatch.setattr(g, "resolve_bash", lambda: ("bash", None))

    def run(argv, payload, env, timeout):
        name = Path(argv[1]).name
        seen.append((name, round(timeout, 3)))
        clock[0] += min(costs[name], timeout)
        return (None, b"", b"") if costs[name] >= timeout else (0, b"", b"")
    monkeypatch.setattr(g, "_run_bounded", run)
    return seen


def test_E2_the_second_delete_guard_gets_only_what_is_left(sandbox, monkeypatch):
    _, tk, env = sandbox
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(X4GUARD)
    monkeypatch.setattr(g, "TIMEOUT_S", 4.0)
    seen = _fake_runner(g, monkeypatch, {"protect-files.sh": 1.0, "protect-bash.sh": 0.5})
    v = g.verdict_for("delete", None, None, str(tk / "x.txt"))
    assert seen == [("protect-files.sh", 4.0), ("protect-bash.sh", 3.0)], seen
    assert not v["inert"], v


def test_E2_a_spent_budget_runs_no_further_guard_and_is_inert(sandbox, monkeypatch):
    _, tk, env = sandbox
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(X4GUARD)
    monkeypatch.setattr(g, "TIMEOUT_S", 4.0)
    seen = _fake_runner(g, monkeypatch, {"protect-files.sh": 99.0, "protect-bash.sh": 0.5})
    v = g.verdict_for("delete", None, None, str(tk / "x.txt"))
    assert seen == [("protect-files.sh", 4.0)], seen
    assert v["decision"] == "deny" and v["inert"], v
    assert v["guards"] == ["protect-files.sh", "protect-bash.sh"]
    assert "X4_GUARD_TIMEOUT_S" in v["reason"], v["reason"]


def test_E2_a_delete_with_both_guards_hung_spends_one_budget(sandbox, tmp_path):
    """Real processes: MEASURED 17.6 s for two hung guards under a 2 s budget before this lane."""
    _, tk, env = sandbox
    pids = [tmp_path / "a.pid", tmp_path / "b.pid"]
    hooks = _stub_hooks(tmp_path, {"protect-files.sh": _grandchild(pids[0]),
                                   "protect-bash.sh": _grandchild(pids[1])})
    t0 = time.monotonic()
    v = _check_in(dict(env, X4_GUARD_TIMEOUT_S="5"), tk, "--kind", "delete", "--path",
                  str(tmp_path / "x.txt"), script=hooks / "x4guard.py")
    wall = time.monotonic() - t0
    try:
        assert v["decision"] == "deny" and v["inert"], v
        assert wall < 9.5, f"{wall:.1f}s: two budgets were spent (one is ~5.5 s, two ~11 s)"
    finally:
        for p in pids:
            _reap(int(p.read_text()) if p.exists() else None)


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "nan", "inf"])
def test_E2_a_bad_budget_setting_is_an_inert_deny(sandbox, raw):
    _, _, env = sandbox
    rc, v, err = check(dict(env, X4_GUARD_TIMEOUT_S=raw), "--kind", "shell", "--shell", "bash",
                       "--command", "echo hi")
    assert rc == 0, err                       # "abc" was a traceback, rc 1, no JSON (MEASURED)
    # The VALUE is named, not just the variable: a 0 budget would otherwise pass via the
    # "budget already spent" path and hide a missing `t <= 0` clause (one twin per clause).
    assert v["decision"] == "deny" and v["inert"] and f"X4_GUARD_TIMEOUT_S={raw!r}" in v["reason"], v


_ASK_THEN_EXIT = ("cat >/dev/null\nprintf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
                  "\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"WHY-IT-FAILED\"}}'\nexit CODE\n")


@pytest.mark.parametrize("code", [2, 3])
def test_E3_a_failing_guard_names_its_own_reason(sandbox, tmp_path, code):
    _, tk, env = sandbox
    hooks = _stub_hooks(tmp_path, {"protect-bash.sh": _ASK_THEN_EXIT.replace("CODE", str(code))})
    v = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", "echo hi",
                  script=hooks / "x4guard.py")
    assert v["decision"] == "deny" and v["inert"], v
    assert "WHY-IT-FAILED" in v["reason"], v["reason"]          # dropped before lane E (MEASURED)
    assert f"exit {code}" in v["reason"] or f"exited {code}" in v["reason"], v["reason"]


def test_E3_TWIN_a_failing_guard_without_a_verdict_names_its_stderr(sandbox, tmp_path):
    """The unparseable-stdout clause: no hook JSON, so the cause comes from stderr -- and a
    parse failure must not escape as an exception or turn into anything but an inert deny."""
    _, tk, env = sandbox
    hooks = _stub_hooks(tmp_path, {"protect-bash.sh":
                                   "cat >/dev/null\nprintf 'not json'\necho 'boom: jq missing' >&2\nexit 1\n"})
    v = _check_in(env, tk, "--kind", "shell", "--shell", "bash", "--command", "echo hi",
                  script=hooks / "x4guard.py")
    assert v["decision"] == "deny" and v["inert"], v
    assert "exited 1" in v["reason"] and "boom: jq missing" in v["reason"], v["reason"]


def test_E4_the_delete_probe_is_a_recursive_rm(sandbox):
    """The rm rule's message echoes the probe command (MEASURED: 'confirm: rm -f ...'). Since
    2026-10-02 an X4-folder delete is an ADVISORY (user decision), so it is in the context."""
    _, tk, env = sandbox
    v = _check_in(env, tk, "--kind", "delete", "--path", str(tk / "dev" / "mymod"))
    assert v["decision"] == "advise" and not v["inert"], v
    assert "rm -rf -- '" in v["context"], v["context"]


def test_E4_TWIN_a_quote_and_a_space_in_the_path_still_reach_the_rm_rule(sandbox):
    """Quoting clause: a broken quote would come back as the 'does not PARSE' ask instead."""
    _, tk, env = sandbox
    p = tk / "dev" / "mymod" / "it's here.xml"
    p.write_text("x\n", encoding="utf-8")
    v = _check_in(env, tk, "--kind", "delete", "--path", str(p))
    assert v["decision"] == "advise" and not v["inert"], v
    assert "deletes files in an X4 directory" in v["context"], v["context"]
    assert p.exists()


def test_E5_a_delete_keeps_the_file_guards_advisory(sandbox):
    """MEASURED: the delete's verdict came from protect-bash and the manifest advisory from
    protect-files was dropped -- context null. (Since 2026-10-02 that delete ADVISES too.)"""
    _, tk, env = sandbox
    target = tk / "dev" / "mymod" / "content.xml"
    w = _check_in(env, tk, "--kind", "write", "--path", str(target))
    d = _check_in(env, tk, "--kind", "delete", "--path", str(target))
    assert w["decision"] == "advise" and w["context"], w
    assert d["decision"] == "advise" and d["context"] and w["context"] in d["context"], d
    assert "deletes files in an X4 directory" in d["context"], d


def test_E5_TWIN_contexts_join_in_guard_order_whoever_wins(monkeypatch):
    g = _load(X4GUARD)
    vs = iter([{"v": 1, "decision": "advise", "reason": None, "context": "A", "inert": False,
                "guards": ["protect-files.sh"]},
               {"v": 1, "decision": "advise", "reason": None, "context": "B", "inert": False,
                "guards": ["protect-bash.sh"]}])
    monkeypatch.setattr(g, "run_guard", lambda *a, **k: next(vs))
    v = g.verdict_for("delete", None, None, "x")
    assert v["decision"] == "advise" and v["context"] == "A\n\nB", v
    assert v["guards"] == ["protect-files.sh", "protect-bash.sh"]


def test_E2_TWIN_a_valid_budget_setting_is_honoured(sandbox):
    _, _, env = sandbox
    _, v, _ = check(dict(env, X4_GUARD_TIMEOUT_S="30.5"), "--kind", "shell", "--shell", "bash",
                    "--command", "echo hi")
    assert v["decision"] == "allow" and not v["inert"], v


# ---------------------------------------------------------------- lane E: X4_GUARD=off (decision #19)

def _hook(env, script, payload: dict):
    """Run one deployed guard the way Claude Code does (no X4_GUARD_CHECK). -> (rc, hookSpecificOutput)"""
    bash = shutil.which("bash.exe") or shutil.which("bash")
    r = subprocess.run([bash, str(X4GUARD.parent / script)], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    out = r.stdout.decode("utf-8", "replace").strip()
    return r.returncode, (json.loads(out)["hookSpecificOutput"] if out else None)


def _ref_write(tk):
    return {"tool_name": "Write", "tool_input": {"file_path": str(tk / "reference" / "libraries" / "wares.xml"),
                                                 "content": "x"}}


def _ref_rm(tk):
    return {"tool_name": "Bash", "tool_input": {"command": f"rm -rf '{(tk / 'reference' / 'libraries').as_posix()}'"}}


@pytest.mark.parametrize("script,payload", [("protect-files.sh", _ref_write), ("protect-bash.sh", _ref_rm)])
def test_X4_GUARD_off_turns_a_hard_block_into_a_logged_advisory(sandbox, script, payload):
    """Spec 5.7: a LAUNCH-time X4_GUARD=off turns the hook verdicts into advisories, and every
    overridden call is logged. Before lane E no guard read the variable (0 readers, MEASURED)."""
    tmp, tk, env = sandbox
    rc, hso = _hook(dict(env, X4_GUARD="off"), script, payload(tk))
    assert rc == 0 and hso is not None, "guards off must still SAY something, never a silent allow"
    assert "permissionDecision" not in hso, hso
    ctx = hso["additionalContext"]
    assert "X4 GUARDS OFF" in ctx and "DENIED" in ctx and "reference" in ctx, ctx
    log = tmp / "backups" / "GUARDS-OFF.log"
    assert log.is_file() and script in log.read_text(encoding="utf-8"), "the override was not logged"


@pytest.mark.parametrize("value", [None, "on", "OFF", "Off", "0", "false", " off", ""])
def test_X4_GUARD_TWIN_any_other_value_leaves_the_guards_on(sandbox, value):
    """Exactly 'off' and nothing else: a typo must never disable protection."""
    tmp, tk, env = sandbox
    env = {k: v for k, v in env.items() if k != "X4_GUARD"}
    if value is not None:
        env["X4_GUARD"] = value
    for script, payload in (("protect-files.sh", _ref_write), ("protect-bash.sh", _ref_rm)):
        _, hso = _hook(env, script, payload(tk))
        assert hso and hso.get("permissionDecision") == "deny", (script, value, hso)
    assert not (tmp / "backups" / "GUARDS-OFF.log").exists()


def test_X4_GUARD_off_check_reports_it_and_is_never_a_plain_allow(sandbox):
    """x4guard check reports GUARDS OFF explicitly -- for a would-be deny AND for a would-be allow --
    and, being a check, logs nothing (no side effects)."""
    tmp, tk, env = sandbox
    env = dict(env, X4_GUARD="off")
    _, w, _ = check(env, "--kind", "write", "--path", str(tk / "reference" / "libraries" / "wares.xml"))
    _, e, _ = check(env, "--kind", "shell", "--shell", "bash", "--command", "echo hi")
    for v in (w, e):
        assert v["decision"] == "advise" and not v["inert"], v
        assert v["context"].startswith("X4 GUARDS OFF"), v
    assert "DENIED" in w["context"] and "reference" in w["context"], w
    assert list((tmp / "backups").iterdir()) == []


def test_X4_GUARD_TWIN_check_with_the_guards_on_still_denies(sandbox):
    _, tk, env = sandbox
    _, v, _ = check(dict(env, X4_GUARD="on"), "--kind", "write", "--path",
                    str(tk / "reference" / "libraries" / "wares.xml"))
    assert v["decision"] == "deny" and not v["inert"] and "GUARDS OFF" not in json.dumps(v), v


@pytest.mark.parametrize("value,banner", [("off", True), ("on", False), (None, False)])
def test_X4_GUARD_off_puts_a_banner_on_session_start(sandbox, value, banner):
    _, tk, env = sandbox
    env = {k: v for k, v in env.items() if k != "X4_GUARD"}
    if value is not None:
        env["X4_GUARD"] = value
    bash = shutil.which("bash.exe") or shutil.which("bash")
    r = subprocess.run([bash, str(X4GUARD.parent / "session-canary.sh")], input=b"{}", capture_output=True,
                       env=env, timeout=120)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0
    assert ("GUARDS OFF" in out) is banner, out


# --- Plan 3 lane J, J3: a timeout names the budget that actually ran out -------------------- #
# The Codex adapter passes its OWN deadline (X4_CODEX_BUDGET_S) into every check; a reason that
# names X4_GUARD_TIMEOUT_S there sends the user to a knob that played no part.

def test_J3_a_CALLERS_spent_deadline_does_not_blame_X4_GUARD_TIMEOUT_S(sandbox, monkeypatch):
    _, tk, env = sandbox
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(X4GUARD)
    v = g.verdict_for("write", None, None, str(tk / "x.txt"), deadline=g._clock() - 1)
    assert v["decision"] == "deny" and v["inert"], v
    assert "X4_GUARD_TIMEOUT_S" not in v["reason"] and "caller" in v["reason"], v["reason"]


def test_J3_TWIN_a_caller_deadline_that_RUNS_OUT_mid_guard_blames_the_caller(sandbox, monkeypatch):
    _, tk, env = sandbox
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(X4GUARD)
    _fake_runner(g, monkeypatch, {"protect-files.sh": 99.0})
    v = g.verdict_for("write", None, None, str(tk / "x.txt"), deadline=g._clock() + 2.0)
    assert v["inert"] and "timed out" in v["reason"], v
    assert "X4_GUARD_TIMEOUT_S" not in v["reason"] and "caller" in v["reason"], v["reason"]


def test_J3_TWIN_x4guards_OWN_spent_budget_still_names_X4_GUARD_TIMEOUT_S(sandbox, monkeypatch):
    """The 'was already spent' message with x4guard's own budget (run_guard called without a
    caller label) -- the E2 test above pins only the 'timed out' message for the own budget."""
    _, tk, env = sandbox
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    g = _load(X4GUARD)
    v = g.run_guard("protect-files.sh", g.guard_payload("write", None, None, str(tk / "x.txt")),
                    g._clock() - 1)
    assert v["inert"] and "already spent" in v["reason"], v
    assert "X4_GUARD_TIMEOUT_S" in v["reason"] and "caller" not in v["reason"], v["reason"]



# --- Plan 3 decision J-Q1: a bare destructive git clean from the game folder is a DENY ------- #

def _jq1_check_in(env, cwd, *args):
    r = subprocess.run([sys.executable, str(X4GUARD), "check", *args], capture_output=True,
                       env=env, cwd=cwd, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout)


def test_JQ1_bare_git_clean_fdx_FROM_the_game_folder_is_denied_end_to_end(sandbox):
    """Through the real protect-bash.sh: the cwd x4guard sends is the game folder."""
    tmp, _, env = sandbox
    v = _jq1_check_in(env, tmp / "X4 Foundations", "--kind", "shell", "--shell", "bash",
                  "--command", "git clean -fdx")
    assert v["decision"] == "deny" and not v["inert"], v
    assert "git -C" in v["reason"], v["reason"]


def test_JQ1_TWIN_the_same_command_from_an_unrelated_folder_is_allowed(sandbox):
    tmp, _, env = sandbox
    (tmp / "elsewhere").mkdir()
    v = _jq1_check_in(env, tmp / "elsewhere", "--kind", "shell", "--shell", "bash",
                  "--command", "git clean -fdx")
    assert v["decision"] == "allow" and not v["inert"], v

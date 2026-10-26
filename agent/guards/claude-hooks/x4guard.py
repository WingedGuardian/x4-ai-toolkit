#!/usr/bin/env python3
"""x4guard -- one front door to the toolkit's guards (Layer 0 of the agent-portability design).

    python x4guard.py check --kind shell --shell {bash,powershell} --command CMD
    python x4guard.py check --kind {write,delete} --path P
    python x4guard.py check --kind write --path P {--content WHOLE_FILE | --command PATCH_TEXT}
    python x4guard.py conformance --profile NAME|PATH [-- ADAPTER ARGV]   (see ADAPTING.md)

`check` prints ONE JSON verdict and exits 0:
    {"v":1,"decision":"allow|advise|ask|deny","reason":...,"context":...,"inert":bool,"guards":[...]}
Exit 2 only on a usage error. (`conformance` has its own exit codes: 0 agree, 1 disagree,
2 cannot evaluate, 3 too little examined -- scripts/x4conformance.py.) `check` runs the SAME guard scripts Claude Code runs, with the payload
shape they already read, and NO side effects: backup-before-edit.sh is never run by a check.

--shell names the shell that will EXECUTE the command, not the tool that sent it. Codex, for
one, labels PowerShell commands "Bash" (MEASURED 2026-09-30); judged as bash, a PowerShell
write into reference/ was allowed.

A guard that could not run is never an allow: missing script, no bash, the WSL bash stub, a
timeout, a non-zero exit, unreadable output or an invalid X4_GUARD_TIMEOUT_S all return
decision "deny" with inert=true and the cause named.

X4_GUARD_TIMEOUT_S (default 25, a positive number of seconds) is the budget for the WHOLE
check: a delete's two guards share it. On a timeout the guard and every descendant are killed:
on Windows through a Job Object the guard starts in (MSYS descendants included, which
`taskkill /T` cannot see -- R2-F4), falling back to `taskkill /T` if no job could be made; on
POSIX through the guard's own process group, which a descendant that calls setsid() leaves.
The worst-case wall clock is that budget plus KILL_WAIT_S + DRAIN_GRACE_S. An agent that can ask its user may present an inert deny as a question; it may
not present it as an allow. Stdlib only; Python >= 3.10.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _timeout_setting() -> tuple:
    """X4_GUARD_TIMEOUT_S: ONE budget for the whole check (both guards of a delete share it).
    A bad value is a configuration error: an inert deny naming it, never a traceback."""
    raw = os.environ.get("X4_GUARD_TIMEOUT_S")
    if raw is None or not raw.strip():
        return 25.0, None
    try:
        t = float(raw)
    except ValueError:
        t = math.nan
    if not math.isfinite(t) or t <= 0:
        return None, f"X4_GUARD_TIMEOUT_S={raw!r} is not a positive number of seconds"
    return t, None


TIMEOUT_S, TIMEOUT_ERROR = _timeout_setting()
RANK = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}
#: How the guards say "I checked nothing" (or only part). They ASK in those states, which an
#: agent would put to its user as an ordinary confirmation; here it is an inert deny instead.
NOT_CHECKED = re.compile(r"X4 GUARD INERT|NO rule (?:below )?was evaluated|NEVER checked against any rule"
                         r"|could not be translated|could not be analysed")
#: After a timeout: how long taskkill may take, and how long to wait for the pipes to close once
#: the tree is dead. A check's wall clock is bounded by TIMEOUT_S + KILL_WAIT_S + DRAIN_GRACE_S
#: even if the kill fails, because the drain is never unbounded.
KILL_WAIT_S = 5
DRAIN_GRACE_S = 3
_TASKKILL = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
_CREATE_SUSPENDED = 0x00000004


def _clock() -> float:
    return time.monotonic()


class _Job:
    """A Windows Job Object holding one guard and EVERY process it starts (R2-F4, v4.0.0
    review). `taskkill /T` walks Windows parent PIDs, and an MSYS grandchild -- a backgrounded
    subshell's `sleep` under Git Bash -- is not linked to the guard that way: it survived the
    tree kill (MEASURED, review probe scratch-R2/killprobe.py: `sleep 41`/`sleep 42` alive after
    the check returned). A job is inherited by every descendant that cannot break away, and this
    one does not allow breakaway. The guard starts SUSPENDED and is resumed only after it is in
    the job, so nothing it starts can escape. Any failure here is reported by `ok` False and the
    caller keeps the taskkill path; the guard is resumed whatever happens."""
    JOB_INFO_EXTENDED = 9
    LIMIT_KILL_ON_CLOSE = 0x2000

    def __init__(self):
        self.handle = None
        self.ok = False
        try:
            import ctypes
            from ctypes import wintypes
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._ntdll = ctypes.WinDLL("ntdll")
            self._k32.CreateJobObjectW.restype = wintypes.HANDLE
            self._k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            self._k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            self._k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            self._k32.CloseHandle.argtypes = (wintypes.HANDLE,)
            self._ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
            self.handle = self._k32.CreateJobObjectW(None, None)
        except (OSError, AttributeError, ImportError):
            self.handle = None

    def adopt(self, proc: subprocess.Popen) -> None:
        """Put the SUSPENDED guard in the job, then resume it -- always resume."""
        try:
            if self.handle:
                self.ok = bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))
        except (OSError, AttributeError):
            self.ok = False
        finally:
            try:
                self._ntdll.NtResumeProcess(int(proc._handle))
            except (OSError, AttributeError):
                proc.kill()          # a guard we cannot resume must not hang the budget

    def kill(self) -> None:
        if self.handle and self.ok:
            try:
                self._k32.TerminateJobObject(self.handle, 1)
            except OSError:
                pass

    def close(self) -> None:
        if self.handle:
            try:
                self._k32.CloseHandle(self.handle)
            except OSError:
                pass
            self.handle = None


def _kill_tree(proc: subprocess.Popen, job: "_Job | None" = None) -> None:
    """Kill the guard AND its descendants. On Windows the guard's Job Object is terminated first
    (it holds every descendant, MSYS ones included -- see _Job); taskkill /T stays as the
    fallback for a job that could not be made: a killed bash.exe leaves its children running and
    holding the pipes (MEASURED 2026-10-02, 3 of 3 process shapes), and taskkill walks parent PIDs
    from the root while it is still known. The PID cannot be reused meanwhile -- Popen holds a
    handle to the process. On POSIX the guard leads its own process group (start_new_session),
    so killpg reaches every descendant that did not leave it."""
    if os.name == "nt":
        if job is not None:
            job.kill()
        try:
            subprocess.run([_TASKKILL, "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=KILL_WAIT_S)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_bounded(argv: list, payload: bytes, env: dict, timeout: float):
    """(returncode, stdout, stderr), or (None, b"", b"") on timeout. Never `subprocess.run`:
    on Windows its timeout path ends in an UNBOUNDED communicate() that waits for every pipe
    holder, so a guard's grandchild stretched a 2 s budget to 10.9 s (MEASURED). Not
    `with Popen(...)` either: its __exit__ waits."""
    job = _Job() if os.name == "nt" else None
    extra = {"creationflags": _CREATE_SUSPENDED} if job is not None and job.handle else (
        {} if os.name == "nt" else {"start_new_session": True})
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, **extra)
        if "creationflags" in extra:
            job.adopt(proc)
        try:
            out, err = proc.communicate(payload, timeout=timeout)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            _kill_tree(proc, job)
            try:
                proc.communicate(timeout=DRAIN_GRACE_S)
            except subprocess.TimeoutExpired:
                pass        # abandon: the reader threads are daemons and die with this process
            return None, b"", b""
    finally:
        if job is not None:
            job.close()     # no KILL_ON_CLOSE: a guard that finished in time keeps its children


#: Directories whose `bash.exe` is a stub (the WSL launcher, the Store alias), never a shell.
BASH_STUB_DIRS = ("system32", "syswow64", "windowsapps")
#: The documented Git for Windows default, named in every "not found" message.
GIT_BASH_DEFAULT = r"C:\Program Files\Git\bin\bash.exe"


def is_stub_bash(p) -> bool:
    parts = [q.lower() for q in Path(str(p).replace("/", "\\") if os.name == "nt" else p).parts]
    return any(d in parts for d in BASH_STUB_DIRS)


def git_bash_candidates() -> list[str]:
    """Where Git for Windows installs bash, from the environment (machine-wide, 32-bit,
    per-user): `<base>\\Git\\bin\\bash.exe` (the wrapper that sets up Git's PATH) first, then
    `<base>\\Git\\usr\\bin\\bash.exe`. install.ps1's Find-GitBash probes the same bases."""
    bases = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    la = os.environ.get("LOCALAPPDATA")
    bases.append(os.path.join(la, "Programs") if la else None)
    out = []
    for b in (b for b in bases if b):
        out += [os.path.join(b, "Git", "bin", "bash.exe"), os.path.join(b, "Git", "usr", "bin", "bash.exe")]
    return out


def resolve_bash() -> tuple[str | None, str | None]:
    """THE bash resolver (B1, install red-team 2026-10-04): the guards, the OpenCode renderer,
    x4doctor and scripts/gitbash.py all ask this one function. Git Bash, never a stub: `bash`
    alone on a stock Windows PATH is C:\\Windows\\System32\\bash.exe, which cannot run a
    Windows-path script (gates/hook_false_positives.py records this), and Git for Windows puts
    only `<Git>\\cmd` on PATH by default -- so asking PATH alone found NOTHING on a stock
    machine and the install ended INCOMPLETE.

    Order: X4_BASH (explicit; a stub or a missing file is an ERROR, never a silent fallback) >
    on Windows the Git for Windows locations > PATH, walked past any stub (`which` returns only
    the first hit, which is how the stub won). (None, reason) names the exact `setx` line."""
    explicit = os.environ.get("X4_BASH")
    if explicit:
        if os.name == "nt" and is_stub_bash(explicit):
            return None, (f"refusing the WSL/Store bash stub X4_BASH names ({explicit}): it cannot run a "
                          f"Windows-path guard. Point it at Git Bash: setx X4_BASH \"{GIT_BASH_DEFAULT}\"")
        if not Path(explicit).is_file():
            return None, f"X4_BASH points at nothing: {explicit}"
        return explicit, None
    if os.name != "nt":
        cand = shutil.which("bash")
        return (cand, None) if cand else (None, "no bash found on PATH (set X4_BASH)")
    for c in git_bash_candidates():
        if Path(c).is_file():
            return c, None
    stubs = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / "bash.exe" if d else None
        if cand is None or not cand.is_file():
            continue
        if is_stub_bash(cand):
            stubs.append(str(cand))
            continue
        return str(cand), None
    seen = f" (only the stub {stubs[0]} is on PATH)" if stubs else ""
    return None, (f"no Git Bash found{seen}: install Git for Windows, or point the toolkit at your "
                  f"bash.exe for NEW shells with: setx X4_BASH \"{GIT_BASH_DEFAULT}\" "
                  f"(adjust the path if Git is installed elsewhere)")


def is_agent_settings(path: str | None) -> bool:
    """A Claude Code settings file (`.claude/settings*.json`), whose written TEXT the adapters
    pass to the guard (settings_guard.py, user decision 2026-10-05)."""
    import re
    return bool(path) and bool(re.search(r"(^|/)\.claude/settings[^/]*\.json$",
                                         str(path).replace(chr(92), "/").lower()))


def guard_payload(kind: str, shell: str | None, command: str | None, path: str | None,
                  content: str | None = None, edit: dict | None = None, patch: bool = False,
                  execution_host: str = "") -> dict:
    if kind == "shell":
        # `cwd` at the TOP level, where Claude Code and Codex put it: a relative operand is judged
        # from the caller's directory, so `check` and the hooks agree (lane F). The Codex adapter
        # has already entered the payload's cwd, so this is the Codex session cwd too.
        return {"tool_name": "PowerShell" if shell == "powershell" else "Bash",
                "tool_input": {"command": command}, "cwd": os.getcwd(),
                "x4_execution_host": execution_host}
    if content is not None:
        # The file's WHOLE new content (a whole-file write tool, `--content`): exactly a Claude Code
        # Write, so every rule judges it as the reference hooks do (FX-G5 item 10).
        return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}
    if edit is not None:
        # ONE string replacement (an OpenCode edit: old, new, replace_all), which the settings rule
        # APPLIES to the file as it is now and judges the result (FX-G6 / reviewer K I5).
        return {"tool_name": "Write", "tool_input": {"file_path": path, "x4_edit": edit}}
    if command is not None or is_agent_settings(path):
        # The TEXT this write applies (an apply_patch, an OpenCode write/edit), for the
        # settings-file rule (settings_guard.py, user decision 2026-10-05). Not `content`: a
        # patch is not the file's resulting content, and the rule judges the two differently.
        # `x4_patch` only when the CALLER says the text is an apply_patch (FX-G6 / reviewer K M1):
        # the settings rule gives a patch's own semantics only then, never by sniffing the text.
        ti = {"file_path": path, "x4_written": command}
        if patch:
            ti["x4_patch"] = True
        return {"tool_name": "Write", "tool_input": ti}
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": ""}}


def parse_hook_output(out: str) -> tuple[str, str | None, str | None]:
    out = out.strip()
    if not out:
        return "allow", None, None
    try:
        hso = json.loads(out)["hookSpecificOutput"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"guard output is not a hook verdict: {out[:120]!r}") from e
    if not isinstance(hso, dict):
        raise ValueError(f"guard output is not a hook verdict: {out[:120]!r}")
    decision = hso.get("permissionDecision")
    if decision in ("deny", "ask"):
        return decision, hso.get("permissionDecisionReason"), None
    if decision is None and "additionalContext" in hso:
        return "advise", None, hso["additionalContext"]
    raise ValueError(f"unrecognised hook verdict: {hso!r}")


def _inert(reason: str, guards: list[str]) -> dict:
    return {"v": 1, "decision": "deny", "inert": True, "guards": guards, "context": None,
            "reason": f"X4 GUARD INERT: {reason}. NOTHING was checked; this is a refusal, "
                      "not a verdict on the command."}


def _failure_detail(out: bytes, err: bytes) -> str | None:
    """The guard's own reason when it printed a hook verdict before failing (every X4_GUARD_CHECK
    exit 2 does: the verdict is printed BEFORE x4_guard_check_inert exits), else the last 300
    characters of its stderr. Without this the cause was only "exited N" (MEASURED 2026-10-02)."""
    try:
        _, reason, context = parse_hook_output(out.decode("utf-8", "replace"))
        if reason or context:
            return reason or context
    except ValueError:
        pass
    tail = err.decode("utf-8", "replace").strip()[-300:]
    return tail or None


def _deployed() -> bool:
    """A deployed copy (.claude/hooks for Claude Code, .codex/hooks for Codex, .opencode/hooks
    for OpenCode) finds its roots through _x4-env.sh (HOOK_DIR/../..); any other copy needs
    X4_TOOLKIT."""
    return (HERE.name == "hooks" and HERE.parent.name in (".claude", ".codex", ".opencode")
            or HERE.name == "x4_harness" and HERE.parent.name == "tools")


#: Which budget a timeout message names (lane J, Plan 3): x4guard's own, or the CALLER's (the
#: Codex adapter passes its own deadline, X4_CODEX_BUDGET_S, so X4_GUARD_TIMEOUT_S played no part).
CALLER_BUDGET = "the caller's deadline (the Codex adapter's X4_CODEX_BUDGET_S)"


def _own_budget() -> str:
    return f"this check's {TIMEOUT_S:g}s budget (X4_GUARD_TIMEOUT_S)"


def run_guard(script: str, payload: dict, deadline: float | None = None,
              budget: str | None = None, *, save_qualified=None) -> dict:
    """One guard, one verdict dict. Anything that keeps it from producing a real verdict is inert.
    `deadline` (a _clock() value) is the CHECK's deadline: a second guard gets only what is left.
    `budget` names whose deadline it is in a timeout message (default: x4guard's own)."""
    guards = [script]
    if TIMEOUT_ERROR:
        return _inert(TIMEOUT_ERROR, guards)
    target = HERE / script
    if not target.is_file():
        return _inert(f"guard script missing: {script}", guards)
    if not _deployed() and not os.environ.get("X4_TOOLKIT"):
        return _inert(f"this copy is not under .claude/hooks, .codex/hooks or .opencode/hooks and X4_TOOLKIT is unset, so the guard "
                      f"cannot find the configured roots; run the deployed .claude/hooks/x4guard.py", guards)
    bash, why = resolve_bash()
    if not bash:
        return _inert(why, guards)
    if deadline is None:
        deadline = _clock() + TIMEOUT_S
    remaining = deadline - _clock()
    if remaining <= 0:
        return _inert(f"{script} was not run: {budget or _own_budget()} was already spent", guards)
    try:
        # X4_GUARD_CHECK=1: the guards' internal check protocol. Every "checked nothing" path exits
        # 2 instead of asking (a no-op for Claude Code's hooks, where the variable is unset).
        argv = [bash, str(target)]
        if save_qualified is not None:
            argv += ["--harness-policy-backend"] + (["--save-qualified"] if save_qualified else [])
        rc, out, err = _run_bounded(argv, json.dumps(payload).encode("utf-8"),
                                    dict(os.environ, X4_GUARD_CHECK="1"), remaining)
    except OSError as e:
        return _inert(f"{script} could not start: {e}", guards)
    if rc is None:
        return _inert(f"{script} timed out: {budget or _own_budget()} ran out", guards)
    if rc != 0:
        why = _failure_detail(out, err)
        head = (f"{script} reported it could not evaluate this (exit 2, X4_GUARD_CHECK)" if rc == 2
                else f"{script} exited {rc}")
        return _inert(head + (f": {why}" if why else ""), guards)
    try:
        decision, reason, context = parse_hook_output(out.decode("utf-8", "replace"))
    except ValueError as e:
        return _inert(str(e), guards)
    if decision in ("ask", "deny") and reason and NOT_CHECKED.search(reason):
        return _inert(f"{script} could not check all or part of this: {reason}", guards)
    return {"v": 1, "decision": decision, "reason": reason, "context": context,
            "inert": False, "guards": guards}


#: Leads every non-inert verdict while X4_GUARD=off, so it can never read as a plain allow.
GUARDS_OFF_NOTE = ("X4 GUARDS OFF (X4_GUARD=off at launch): every guard verdict is an advisory this "
                   "session and nothing here was enforced.")


def verdict_for(kind: str, shell: str | None, command: str | None, path: str | None,
                deadline: float | None = None, content: str | None = None,
                edit: dict | None = None, patch: bool = False, save_qualified=None,
                execution_host: str = "") -> dict:
    """The verdict, plus spec 5.7's escape hatch: with X4_GUARD exactly "off" the guards turn a
    deny/ask into an advisory naming what it would have been (they read the variable
    themselves), and this says GUARDS OFF on every verdict -- a would-be allow included. An inert
    verdict stays an inert deny: a guard that could not run judged nothing to relax."""
    v = _verdict(kind, shell, command, path, deadline, content, edit, patch, save_qualified, execution_host)
    if os.environ.get("X4_GUARD") != "off" or v["inert"]:
        return v
    v = dict(v)
    if RANK[v["decision"]] < RANK["advise"]:
        v["decision"] = "advise"
    v["context"] = GUARDS_OFF_NOTE + ("\n\n" + v["context"] if v.get("context") else "")
    return v


def _verdict(kind: str, shell: str | None, command: str | None, path: str | None,
             deadline: float | None = None, content: str | None = None,
             edit: dict | None = None, patch: bool = False, save_qualified=None,
             execution_host: str = "") -> dict:
    """A relative path is resolved from the CALLER's working directory (Codex apply_patch paths
    are relative). A delete is judged as the stricter of a write and an `rm -rf --` of that path:
    recursive, because the path may be a directory. No protect-bash rule distinguishes it from
    `rm -f` today (MEASURED 9/9 paths identical); the probe says what a delete IS, for any
    future rule keyed on recursion."""
    # A caller that judges MANY checks under one budget (the Codex adapter's apply_patch batch)
    # passes its own absolute deadline; otherwise each check gets TIMEOUT_S of its own.
    budget = None if deadline is None else CALLER_BUDGET
    if deadline is None:
        deadline = _clock() + TIMEOUT_S if TIMEOUT_S else None
    def check(script, payload, deadline, budget):
        options = {} if save_qualified is None else {"save_qualified": save_qualified}
        return run_guard(script, payload, deadline, budget, **options)
    if kind == "shell":
        return check("protect-bash.sh", guard_payload(kind, shell, command, None,
                     execution_host=execution_host), deadline, budget)
    path = os.path.abspath(path)
    parts = [check("protect-files.sh", guard_payload("write", None, command, path, content, edit, patch),
                       deadline, budget)]
    if kind in ("delete", "move"):
        quoted = path.replace("\\", "/").replace("'", "'\"'\"'")    # close, "'", reopen
        rm = "rm -rf -- '" + quoted + "'"
        parts.append(check("protect-bash.sh", guard_payload("shell", "bash", rm, None), deadline, budget))
    worst = max(parts, key=lambda v: (v["inert"], RANK[v["decision"]]))
    worst = dict(worst)
    worst["guards"] = [g for v in parts for g in v["guards"]]
    # EVERY guard's advisory, in guard order, whoever wins: a delete that ASKs (protect-bash)
    # used to drop protect-files' manifest advisory entirely (MEASURED 2026-10-02).
    ctx = [v["context"] for v in parts if v.get("context")]
    worst["context"] = "\n\n".join(ctx) or None
    return worst


def _g_engine_path() -> Path | None:
    """scripts/x4conformance.py of THIS copy's toolkit (repo .claude/hooks or the agent/ source),
    else of $X4_TOOLKIT (a deployed copy)."""
    cands = [HERE.parents[1] / "scripts", HERE.parents[2] / "scripts"] if len(HERE.parents) > 2 else []
    if os.environ.get("X4_TOOLKIT"):
        cands.append(Path(os.environ["X4_TOOLKIT"]) / "scripts")
    return next((d / "x4conformance.py" for d in cands if (d / "x4conformance.py").is_file()), None)


def _g_conformance(argv: list) -> int:
    """`x4guard conformance ...`: hand the arguments to the engine. Nothing in `check` changes."""
    path = _g_engine_path()
    if path is None:
        sys.stderr.write("x4guard conformance: scripts/x4conformance.py not found next to this copy "
                         "or under $X4_TOOLKIT; run the toolkit's own .claude/hooks/x4guard.py\n")
        return 2
    import importlib.util
    spec = importlib.util.spec_from_file_location("x4conformance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # BEFORE argparse: an argparse REMAINDER does not capture leading options, and everything
    # after `conformance` (its own flags, `--`, the adapter argv) belongs to the engine.
    if argv[:1] == ["conformance"]:
        return _g_conformance(argv[1:])
    ap = argparse.ArgumentParser(prog="x4guard", description="Ask the toolkit's guards for a verdict.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("conformance", help="replay the guard corpus through an adapter (see ADAPTING.md)")
    c = sub.add_parser("check", help="verdict on one shell command or one file path; no side effects")
    c.add_argument("--kind", required=True, choices=("shell", "write", "delete"))
    c.add_argument("--shell", choices=("bash", "powershell"),
                   help="the shell that will EXECUTE the command (not the tool that sent it)")
    c.add_argument("--command", help="shell: the command; write/delete: the TEXT written, which "
                   "the guard needs for a .claude/settings*.json (without it, such a write is denied)")
    c.add_argument("--content", help="write: the file's WHOLE new content, from a tool that writes a "
                   "complete file -- judged exactly as a Claude Code Write. Use --command instead for "
                   "a patch or any partial text")
    c.add_argument("--patch", action="store_true", help="write/delete: the --command text is an "
                   "apply_patch (its own semantics: an add-only patch replaces the file)")
    c.add_argument("--path")
    a = ap.parse_args(argv)
    if a.cmd == "conformance":            # only reachable with options before the word
        return _g_conformance(argv[argv.index("conformance") + 1:])
    if a.kind == "shell" and (not a.shell or a.command is None):
        ap.error("--kind shell needs --shell and --command")
    if a.kind != "shell" and not a.path:
        ap.error(f"--kind {a.kind} needs --path")
    if a.content is not None and (a.kind != "write" or a.command is not None):
        ap.error("--content is for --kind write only, and never with --command")
    if a.patch and (a.kind == "shell" or a.command is None):
        ap.error("--patch is for --kind write/delete with --command (the patch text)")
    v = verdict_for(a.kind, a.shell, a.command, a.path, content=a.content, patch=a.patch)
    sys.stdout.write(json.dumps(v) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

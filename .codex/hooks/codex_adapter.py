#!/usr/bin/env python3
"""The Codex hook adapter: translate a native Codex payload, ask the toolkit's guards, render the
answer in the ONLY shapes Codex honours. It decides nothing itself (spec D10).

    python codex_adapter.py <session_start|pre_tool_use|post_tool_use>   (payload on stdin)

Prints EXACTLY one line, `X4OK <codex-json-or-empty>`, and exits 0. The entry wrapper
(codex-entry.ps1 / codex-entry.sh) accepts nothing else: a crash, a second line or a missing
prefix becomes a fail-closed deny there.

Why the output shapes are so narrow (MEASURED on Codex 0.160.0 unless marked READ):
  - ONLY a parsed JSON permissionDecision "deny" blocks. exit 2 + stderr did NOT block (2/2);
    a crash, a non-zero exit or unparseable output is "Failed" and the call RUNS.
  - The output schema is additionalProperties:false (READ): one extra key fails the hook open.
  - "ask" is not honoured, so a guard's ask is rendered as a deny whose reason tells the model
    to ask the user ("NEEDS YOUR APPROVAL: ...").
So the four shapes are: deny, advise (additionalContext), allow (empty), SessionStart context.

Routing (PreToolUse):
  Bash         -> the shell guard, judged as the shell that will EXECUTE it: PowerShell on
                  Windows (Codex labels PowerShell "Bash"; MEASURED), bash elsewhere (INFERRED,
                  not device-tested). X4_CODEX_SHELL overrides. An apply_patch run through the
                  shell (MEASURED P3) is ALSO judged by its patch paths.
  apply_patch  -> parse_patch: add/update/move_to are writes, delete/move_from are deletes;
                  a patch it cannot parse is an inert deny. Relative paths resolve against the
                  payload cwd (MEASURED: patch paths are relative, cwd is the session cwd).
  write_stdin  -> each SUBMITTED line (text before a newline) judged as a shell command. No
                  PreToolUse fired for write_stdin in 0/2 measured calls: this branch is for a
                  Codex that does hook it; the gap is disclosed, not closed (DECISIONS #20).
  anything else-> allowed (as Claude Code allows tools it does not hook), name recorded.

The guards inherit the full hook environment, secrets included (MEASURED): nothing here ever
logs the environment. Stdlib only; Python >= 3.10.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "claude-hooks"):         # rendered tree, then the source tree
    if (_p / "x4guard.py").is_file() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import x4guard                     # noqa: E402  (sibling module; path set above)
import patch_paths                 # noqa: E402

BANNER = "X4 GUARDS LIVE (codex hooks v1) — shell commands and apply_patch are checked (input typed into a running shell is not)."
EVENTS = {"session_start": "SessionStart", "pre_tool_use": "PreToolUse", "post_tool_use": "PostToolUse"}
SHELL = "powershell" if sys.platform == "win32" else "bash"
OP_KIND = {"add": "write", "update": "write", "move_to": "write", "delete": "delete", "move_from": "delete"}
BACKUP_OPS = ("update", "delete", "move_from")
VALIDATE_OPS = ("add", "update", "move_to")
SHELL_TOOLS = ("Bash",)
PATCH_TOOLS = ("apply_patch",)
STDIN_TOOLS = ("write_stdin",)
RANK = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}
ASK_PREFIX = "NEEDS YOUR APPROVAL: "
ASK_SUFFIX = " Ask the user; do not retry until they agree."
INERT = "X4 GUARD INERT"


def max_chars() -> int:
    try:
        return max(200, int(os.environ.get("X4_HOOK_MAX_CHARS") or 10000))
    except ValueError:
        return 10000


def budget_s() -> float:
    try:
        return max(0.0, float(os.environ.get("X4_CODEX_BUDGET_S") or 45))
    except ValueError:
        return 45.0


def inert(reason: str, label: str = "") -> dict:
    return {"v": 1, "decision": "deny", "inert": True, "context": None, "label": label,
            "reason": f"{INERT}: {reason}. NOTHING was allowed; this is a refusal, not a verdict."}


# ------------------------------------------------------------------ translate ---------- #

_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(?:/|$)")


def _abspath(p: str, base: str) -> str:
    """Absolute path as the guards must see it. On Windows a Git Bash drive path `/c/x` means
    `C:/x`; os.path.abspath would make it `<cwd drive>:\\c\\x`, a path outside every protected
    root (MEASURED by the conformance suite: an Update File of /c/.../reference/... was ALLOWED)."""
    if sys.platform == "win32":
        p = _MSYS_DRIVE.sub(lambda m: m.group(1).upper() + ":/", p)
    return os.path.abspath(os.path.join(base, p)) if not os.path.isabs(p) else os.path.abspath(p)


def _patch_calls(text: str, base: str, parse=patch_paths.parse_patch) -> list[tuple]:
    """GuardCalls for one patch, read with `parse` -- the grammar of the agent that will apply it
    (Codex's by default; the OpenCode adapter passes parse_patch_opencode). A PatchParseError
    propagates to the caller."""
    calls = []
    ops = parse(text)
    for op, p in ops:
        kind = OP_KIND.get(op)
        if kind is None:
            continue
        ap = _abspath(p, base)
        # A Claude Code settings file: the guard judges the patch TEXT (settings_guard.py), told
        # that it IS a patch (FX-G6 / reviewer K M1) -- never left to guess from the text.
        if x4guard.is_agent_settings(ap):
            calls.append((kind, None, text, ap, f"{op} {p}", {"patch": True}))
        else:
            calls.append((kind, None, None, ap, f"{op} {p}"))
    return calls


def _patch_text(ti: dict):
    """The patch text of an apply_patch call: `command` (0.160.0 captures), else `input`. ONE
    reader for the pre, backup and post paths (R2-F9: backup and post read `command` only, so a
    patch under `input` was judged but never backed up or validated)."""
    return ti.get("command") if isinstance(ti.get("command"), str) else ti.get("input")


def translate(payload: dict, event: str, *, shell: str) -> list[tuple]:
    """[(kind, shell, command, path, label)] -- the guard calls this PreToolUse needs. Raises
    patch_paths.PatchParseError when a patch cannot be read (the caller refuses)."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = os.getcwd()
    if tool in SHELL_TOOLS:
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            raise patch_paths.PatchParseError("Bash payload carries no command string")
        calls = [("shell", shell, cmd, None, "shell command")]
        found = patch_paths.shell_patch(cmd)
        if found is not None:
            body, cd = found
            calls += _patch_calls(body, _abspath(cd, cwd) if cd else cwd)
        return calls
    if tool in PATCH_TOOLS:
        return _patch_calls(_patch_text(ti), cwd)
    if tool in STDIN_TOOLS:
        chars = ti.get("chars")
        if not isinstance(chars, str) or "\n" not in chars.replace("\r", "\n"):
            return []
        lines = chars.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:-1]   # submitted lines only
        return [("shell", shell, ln, None, "stdin line") for ln in lines if ln.strip()]
    record_unknown(tool)
    return []


def record_unknown(tool) -> None:
    """Best effort: the NAME of a tool the adapter does not judge, nothing else."""
    try:
        log = Path(os.environ.get("X4_CODEX_TOOL_LOG") or (HERE.parent / "x4-unknown-tools.log"))
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{str(tool)[:200]}\n")
    except OSError:
        pass


# ------------------------------------------------------------------ judge -------------- #

#: Path checks of one patch run concurrently: M13 (2026-10-02) measured a 3-file patch at
#: p95 5.8 s through the whole chain when the checks ran one after another.
PARALLEL = 4


def _one(call: tuple, deadline: float | None = None) -> dict:
    kind, sh, cmd, path, label, *extra = call     # extra: verdict_for options (FX-G6 / K I5, M1)
    # ONE deadline for the whole batch (MEASURED after merging lanes B+E: 400 deletes under a 45 s
    # budget ran past 180 s, because each check started its own fresh x4guard deadline). x4guard
    # refuses to START a guard once it has passed, and bounds one already running.
    try:
        v = dict(x4guard.verdict_for(kind, sh, cmd, path, deadline=deadline, **(extra[0] if extra else {})))
    except Exception as e:                # a guard that raised checked nothing
        v = inert(f"the guard raised {type(e).__name__}: {e}", label)
    v["label"] = label
    return v


def judge(calls: list[tuple], deadline: float) -> list[dict]:
    """Verdicts in call order. The shell check (if any) runs first; a real deny there ends it.
    Every guard shares ONE deadline: it is passed into each x4guard check (see _one)."""
    from concurrent.futures import ThreadPoolExecutor
    out: list[dict] = []
    shell = [c for c in calls if c[0] == "shell"]
    paths = [c for c in calls if c[0] != "shell"]
    for batch in ([[c] for c in shell] + ([paths] if paths else [])):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            out += [inert("the adapter's time budget ran out before this check", c[4]) for c in batch]
            continue
        if len(batch) == 1:
            got = [_one(batch[0], deadline)]
        else:
            with ThreadPoolExecutor(min(PARALLEL, len(batch))) as ex:
                got = list(ex.map(lambda c: _one(c, deadline), batch))
        out += got
        if any(v["decision"] == "deny" and not v.get("inert") for v in got):
            break                         # the call is refused; later checks cannot change that
    return out


def aggregate(verdicts: list[dict]) -> dict:
    """inert deny > deny > ask > advise > allow, reasons joined per path, bounded, directive first."""
    if not verdicts:
        return {"decision": "allow", "inert": False, "reason": None, "context": None}
    worst = max(verdicts, key=lambda v: (bool(v.get("inert")), RANK[v["decision"]]))
    dec, is_inert = worst["decision"], bool(worst.get("inert"))
    same = [v for v in verdicts if v["decision"] == dec and bool(v.get("inert")) == is_inert]
    if dec in ("deny", "ask"):
        items = [(v.get("label") or "", v.get("reason") or "") for v in same]
        if len(items) == 1:               # one finding: the guard's own directive, then WHICH path
            label, text = items[0]
            head, items = (f"{text} [{label}]" if label and label != "shell command" else text), []
        elif is_inert:
            head = f"{INERT}: {len(items)} of {len(verdicts)} checks could not run. NOTHING was allowed."
        elif dec == "deny":
            head = f"BLOCKED by the X4 toolkit guards: {len(items)} of {len(verdicts)} checks refused this call."
        else:
            head = f"{len(items)} of {len(verdicts)} checks need the user's confirmation."
        return {"decision": dec, "inert": is_inert, "context": None, "reason": bounded(head, items)}
    if dec == "advise":
        items = [(v.get("label") or "", v.get("context") or "") for v in same]
        head = items[0][1] if len(items) == 1 else f"X4 guards: {len(items)} advisories for this call."
        return {"decision": "advise", "inert": False, "reason": None,
                "context": bounded(head, items if len(items) > 1 else [])}
    return {"decision": "allow", "inert": False, "reason": None, "context": None}


def bounded(head: str, items: list[tuple], limit: int | None = None) -> str:
    limit = limit or max_chars()
    reserve = 120
    out = head if len(head) <= limit - reserve else head[: limit - reserve] + " [...]"
    for n, (label, text) in enumerate(items):
        line = f"\n- {label}: {text}"
        if len(out) + len(line) > limit - reserve:
            out += f"\n... and {len(items) - n} more (bounded to {limit} characters)"
            break
        out += line
    return out


def render(event: str, verdict: dict) -> str:
    name = EVENTS[event]
    dec = verdict["decision"]
    if event == "pre_tool_use" and dec == "deny":
        hso = {"hookEventName": name, "permissionDecision": "deny", "permissionDecisionReason": verdict["reason"]}
    elif event == "pre_tool_use" and dec == "ask":
        body = bounded(verdict["reason"] or "", [], max_chars() - len(ASK_PREFIX) - len(ASK_SUFFIX))
        hso = {"hookEventName": name, "permissionDecision": "deny",
               "permissionDecisionReason": ASK_PREFIX + body + ASK_SUFFIX}
    elif dec in ("advise", "deny", "ask") and (verdict.get("context") or verdict.get("reason")):
        hso = {"hookEventName": name, "additionalContext": verdict.get("context") or verdict.get("reason")}
    else:
        return ""
    return json.dumps({"hookSpecificOutput": hso}, ensure_ascii=True)   # ASCII: survives any code page


# ------------------------------------------------------------------ side hooks --------- #

def _run_hook(script: str, payload: dict, timeout: float) -> tuple[str | None, str | None]:
    """(stdout, error) of a Claude-shaped hook script."""
    bash, why = x4guard.resolve_bash()
    if not bash:
        return None, why
    target = HERE / script
    if not target.is_file():
        return None, f"{script} is missing"
    # x4guard's bounded runner, never subprocess.run(timeout=): on Windows that ends in an
    # UNBOUNDED communicate() that waits for every pipe holder (R2-F6; x4guard._run_bounded).
    try:
        rc, out, _err = x4guard._run_bounded([bash, str(target)], json.dumps(payload).encode("utf-8"),
                                             dict(os.environ), max(1, timeout))
    except OSError as e:
        return None, f"{script} could not start: {e}"
    if rc is None:
        return None, f"{script} timed out"
    if rc != 0:
        return None, f"{script} exited {rc}"
    return out.decode("utf-8", "replace"), None


def run_backups(ops: list[tuple], base: str, deadline: float) -> dict | None:
    """backup-before-edit.sh per existing update/delete/move_from path. A backup that asks (it
    could not be taken) becomes an ask; one that could not run at all becomes an ask too."""
    asks = []
    for op, p in ops:
        if op not in BACKUP_OPS:
            continue
        ap = _abspath(p, base)
        if not os.path.isfile(ap):
            continue
        out, err = _run_hook("backup-before-edit.sh",
                             {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                              "tool_input": {"file_path": ap, "old_string": "", "new_string": ""}},
                             deadline - time.monotonic())
        if err:
            asks.append({"decision": "ask", "inert": False, "label": f"backup {p}",
                         "reason": f"X4 BACKUP: no backup could be taken ({err}). Confirm only if you "
                                   f"accept this edit being unrecoverable."})
            continue
        try:
            dec, reason, _ = x4guard.parse_hook_output(out or "")
        except ValueError:
            dec, reason = "ask", "X4 BACKUP: the backup hook answered unreadably; no backup is certain."
        if dec in ("ask", "deny"):
            asks.append({"decision": "ask", "inert": False, "label": f"backup {p}", "reason": reason})
    return aggregate(asks) if asks else None


def pre_tool_use(payload: dict, shell: str, deadline: float) -> dict:
    try:
        calls = translate(payload, "pre_tool_use", shell=shell)
    except patch_paths.PatchParseError as e:
        return aggregate([inert(f"the patch could not be read ({e}), so its paths were never checked")])
    verdict = aggregate(judge(calls, deadline))
    if verdict["decision"] in ("allow", "advise"):
        ops = _ops_for_backup(payload)
        if ops:
            b = run_backups(ops[0], ops[1], deadline)
            if b is not None:
                verdict = b
    return verdict


def _ops_for_backup(payload: dict, strict=False):
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    try:
        if payload.get("tool_name") in PATCH_TOOLS:
            return patch_paths.parse_patch(_patch_text(ti)), os.getcwd()
        if payload.get("tool_name") in SHELL_TOOLS and isinstance(ti.get("command"), str):
            found = patch_paths.shell_patch(ti["command"])
            if found:
                body, cd = found
                return patch_paths.parse_patch(body), (_abspath(cd, os.getcwd()) if cd else os.getcwd())
    except patch_paths.PatchParseError:
        if strict:
            raise
        return None
    return None


def post_tool_use(payload: dict, deadline: float) -> dict:
    try:
        affected = _ops_for_backup(payload, strict=True)
    except patch_paths.PatchParseError as e:
        return {"decision": "advise", "context": f"X4 VALIDATION DID NOT RUN: the patch could not be read ({e})."}
    if affected is None:
        return {"decision": "allow"}
    ops, base = affected
    notes = []
    for op, p in ops:
        if op not in VALIDATE_OPS:
            continue
        ap = _abspath(p, base)
        out, err = _run_hook("x4validate-on-edit.sh",
                             {"hook_event_name": "PostToolUse", "tool_name": "Write",
                              "tool_input": {"file_path": ap, "content": ""}},
                             deadline - time.monotonic())
        if err:
            notes.append(f"X4 VALIDATION DID NOT RUN for {p}: {err}")
            continue
        try:
            dec, reason, ctx = x4guard.parse_hook_output(out or "")
        except ValueError:
            notes.append(f"X4 VALIDATION DID NOT RUN for {p}: unreadable validator hook output")
            continue
        if ctx or reason:
            notes.append(f"{p}: {ctx or reason}")
    if not notes:
        return {"decision": "allow"}
    return {"decision": "advise", "context": bounded(notes[0], [("", n) for n in notes[1:]])}


#: The WHOLE session_start, both hooks together: under the wrapper's 25 s session_start limit
#: (codex-entry.ps1/.sh) with room for Python's start-up and the render. Each hook used to get
#: min(20, budget) -- 2 x 20 s could outrun the wrapper, which then reports the guards NOT LIVE
#: (R2-F7).
SESSION_START_BUDGET_S = 18.0


def session_start(deadline: float) -> dict:
    parts = [BANNER]
    deadline = min(deadline, time.monotonic() + SESSION_START_BUDGET_S)
    for script in ("check-reference-version.sh", "session-canary.sh"):
        left = deadline - time.monotonic()
        if left < 1:
            parts.append(f"[x4] {script} did not run: the session-start budget was spent")
            continue
        out, err = _run_hook(script, {"hook_event_name": "SessionStart", "source": "startup"}, left)
        if err:
            parts.append(f"[x4] {script} did not run: {err}")
        elif out and out.strip():
            parts.append(out.strip())
    return {"decision": "advise", "context": bounded("\n".join(parts), [])}


# ------------------------------------------------------------------ main --------------- #

def _fail(event: str, cause: str) -> str:
    if event == "pre_tool_use":
        return render(event, aggregate([inert(cause)]))
    name = event if event in EVENTS else "post_tool_use"
    return render(name, {"decision": "advise", "context": f"X4 HOOK DID NOT RUN ({cause})."})


def handle(event: str, raw: bytes) -> str:
    if event not in EVENTS:
        return _fail("pre_tool_use", f"unknown hook event argument {event!r}")
    deadline = time.monotonic() + budget_s()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _fail(event, "unreadable Codex payload (not JSON)")
    if not isinstance(payload, dict) or not payload.get("hook_event_name"):
        return _fail(event, "unreadable Codex payload (no hook_event_name)")
    if payload["hook_event_name"] != EVENTS[event]:
        return _fail(event, f"payload event {payload['hook_event_name']!r} does not match {EVENTS[event]!r}")
    cwd = payload.get("cwd")
    try:
        os.chdir(cwd)
    except (OSError, TypeError) as e:
        return _fail(event, f"the payload cwd cannot be entered ({type(e).__name__}), so relative paths cannot be resolved")
    if event == "session_start":
        return render(event, session_start(deadline))
    if not payload.get("tool_name"):
        return _fail(event, "unreadable Codex payload (no tool_name)")
    if event == "post_tool_use":
        return render(event, post_tool_use(payload, deadline))
    shell = os.environ.get("X4_CODEX_SHELL") or SHELL
    if shell not in ("bash", "powershell"):
        return _fail(event, f"X4_CODEX_SHELL={shell!r} is not bash or powershell")
    if budget_s() < 1:
        return render(event, aggregate([inert("the adapter's time budget (X4_CODEX_BUDGET_S) is under one second")]))
    return render(event, pre_tool_use(payload, shell, deadline))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    event = argv[0] if argv else ""
    try:
        text = handle(event, sys.stdin.buffer.read())
    except BaseException as e:            # noqa: BLE001 -- every failure must still print a verdict
        text = _fail(event if event in EVENTS else "pre_tool_use", f"the adapter raised {type(e).__name__}")
    sys.stdout.buffer.write(("X4OK " + text + "\n").encode("ascii", "replace"))   # json escapes newlines
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

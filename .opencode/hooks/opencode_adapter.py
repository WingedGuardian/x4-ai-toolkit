#!/usr/bin/env python3
"""The OpenCode adapter: translate one OpenCode tool call, as the x4guard.js plugin hands it over,
into the toolkit's guard checks, and answer in the ONE shape the plugin accepts. It decides
nothing itself (spec D10): the guards decide, through the Codex adapter's judge/aggregate.

    python opencode_adapter.py <pre|post>      (payload on stdin)

stdin:  {"v":1,"tool":str,"args":object,"directory":str,"shell":"bash"|"powershell"}
stdout: EXACTLY one ASCII line and exit 0:
    X4OK {"v":1,"decision":"allow"|"advise"|"deny","inert":bool,"reason":str|null,"context":str|null}
The plugin throws on anything else, and on "deny".

BEST EFFORT, from OpenCode's docs and source (v1.18.34), NOT measured against a running OpenCode:
  - tool ids (READ tool/shell/id.ts, edit.ts, write.ts, apply_patch.ts): the shell tool is `bash`
    on EVERY OS (args.command, optional args.workdir resolved against the instance directory);
    `edit` / `write` carry args.filePath (relative = against the instance directory);
    `apply_patch` carries args.patchText (the Codex patch envelope).
  - the shell that RUNS a `bash` call is OpenCode's configured shell, else $SHELL, else on
    Windows pwsh/powershell first (READ core/shell.ts). The plugin names it in "shell".
  - OpenCode has no "ask" a plugin can return: a guard's ask is a deny whose reason tells the
    model to ask the user (the Codex rendering, NEEDS YOUR APPROVAL ...).
Routing (pre):
  bash         -> the shell guard (cwd = workdir), plus the patch paths of a shell-run apply_patch
  edit, write  -> a write of filePath
  apply_patch  -> parse_patch_opencode (OpenCode's grammar, not Codex's): add/update/move_to are writes, delete/move_from are deletes
  anything else-> allow, no guard run (read, glob, grep, webfetch, task, skill, MCP tools ...)
On allow/advise the backup hook runs for existing edit/write targets and patch update/delete
ops (Claude Code runs backup-before-edit on Edit/Write). `post` runs the validator hook on each
written path and only ever advises. Stdlib only; Python >= 3.10.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "claude-hooks"):         # rendered tree, then the source tree
    if (_p / "x4guard.py").is_file() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:                               # rendered tree: .opencode/hooks/codex_adapter.py
    import codex_adapter as core   # noqa: E402
except ImportError:                # source tree: agent/guards/adapters/codex.py
    import codex as core           # noqa: E402
import patch_paths                 # noqa: E402

EVENTS = ("pre", "post")
SHELLS = ("bash", "powershell")
SHELL_TOOL = "bash"
FILE_TOOLS = ("edit", "write")
PATCH_TOOL = "apply_patch"


class Unreadable(Exception):
    """The call cannot be translated: an inert deny (pre) or a did-not-run advisory (post)."""


def _out(decision: str, inert: bool = False, reason: str | None = None, context: str | None = None) -> dict:
    return {"v": 1, "decision": decision, "inert": inert, "reason": reason, "context": context}


def _inert(cause: str) -> dict:
    return _out("deny", True, core.inert(cause)["reason"])


def _payload(raw: bytes) -> dict:
    try:
        p = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Unreadable("unreadable OpenCode payload (not JSON)")
    if not isinstance(p, dict) or p.get("v") != 1:
        raise Unreadable("unreadable OpenCode payload (not a v1 object)")
    if not isinstance(p.get("tool"), str) or not isinstance(p.get("args"), dict):
        raise Unreadable("unreadable OpenCode payload (no tool name or args object)")
    d = p.get("directory")
    if not isinstance(d, str) or not os.path.isabs(d) or not os.path.isdir(d):
        raise Unreadable(f"the payload directory {d!r} is not an existing absolute folder, "
                         "so relative paths cannot be resolved")
    return p


def _arg(args: dict, key: str, tool: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v:
        raise Unreadable(f"the {tool} call carries no {key} string")
    return v


def translate(p: dict) -> tuple[list[tuple], list[tuple] | None, str | None]:
    """(guard calls, backup ops, shell cwd). Backup ops are (op, absolute path) pairs. Raises
    Unreadable or patch_paths.PatchParseError: the caller refuses."""
    tool, args, base = p["tool"], p["args"], p["directory"]
    if tool == SHELL_TOOL:
        shell = p.get("shell")
        if shell not in SHELLS:
            # FX-G4 / reviewer G-I1: the old advice, "set X4_OPENCODE_SHELL", made the guards
            # judge a cmd command as bash (MEASURED: `del /s /q <reference>\libraries` allowed).
            # Only OpenCode's own `shell` decides what runs the call, so that is what to change.
            raise Unreadable(f"OpenCode runs this bash call in the shell {shell!r}, which the X4 guards "
                             "cannot judge. Set OpenCode's own `shell` config to pwsh, powershell or bash "
                             "(a path to it) and restart OpenCode; X4_OPENCODE_SHELL cannot change the "
                             "shell OpenCode runs, and when it disagrees with that shell every bash call "
                             "is refused")
        cmd = args.get("command")
        if not isinstance(cmd, str):
            raise Unreadable("the bash call carries no command string")
        wd = args.get("workdir")
        cwd = core._abspath(wd, base) if isinstance(wd, str) and wd else base
        calls = [("shell", shell, cmd, None, "shell command")]
        ops = None
        found = patch_paths.shell_patch(cmd)
        if found is not None:
            # OpenCode does not intercept a shell apply_patch (only its patch/index.ts tests call
            # maybeParseApplyPatch, READ v1.18.34); if one runs, it is Codex's: Codex's grammar
            body, cd = found
            pbase = core._abspath(cd, cwd) if cd else cwd
            calls += core._patch_calls(body, pbase)
            ops = [(op, core._abspath(x, pbase)) for op, x in patch_paths.parse_patch(body)]
        return calls, ops, cwd
    if tool in FILE_TOOLS:
        raw = _arg(args, "filePath", tool)
        path = core._abspath(raw, base)
        call = ("write", None, None, path, f"{tool} {raw}")
        if core.x4guard.is_agent_settings(path):   # settings_guard.py judges the RESULT
            # FX-G6 / reviewer K I5 (MEASURED: a two-step edit built X4_GUARD=off, each step's
            # text innocent): an `edit` is one replacement the guard APPLIES to the file as it is
            # now, as for Claude Code's Edit. A `write` keeps its text reading (unchanged: the
            # text, and a file that already names the key is refused); no text, refused blind.
            if (tool == "edit" and isinstance(args.get("oldString"), str)
                    and isinstance(args.get("newString"), str)):
                call += ({"edit": {"old": args["oldString"], "new": args["newString"],
                                   "replace_all": bool(args.get("replaceAll"))}},)
            elif tool == "write" and isinstance(args.get("content"), str):
                call = ("write", None, args["content"], path, f"{tool} {raw}")
        return [call], [("update", path)], None
    if tool == PATCH_TOOL:
        # OpenCode's apply_patch runs OpenCode's OWN parser, not Codex's (R2-F1): read it that way
        text = _arg(args, "patchText", tool)
        ops = patch_paths.parse_patch_opencode(text)
        return (core._patch_calls(text, base, patch_paths.parse_patch_opencode),
                [(op, core._abspath(x, base)) for op, x in ops], None)
    return [], None, None


def pre(p: dict) -> dict:
    deadline = time.monotonic() + core.budget_s()
    try:
        calls, ops, cwd = translate(p)
    except Unreadable as e:
        return _inert(str(e))
    except patch_paths.PatchParseError as e:
        return _inert(f"the patch could not be read ({e}), so its paths were never checked")
    if core.budget_s() < 1:
        return _inert("the adapter's time budget (X4_CODEX_BUDGET_S) is under one second")
    if cwd is not None:
        try:
            os.chdir(cwd)              # the shell guard reads the CALLER's cwd for relative operands
        except OSError as e:
            return _inert(f"the bash workdir cannot be entered ({type(e).__name__})")
    try:
        v = core.save_policy.batch(p, calls,
            lambda qualified: core.aggregate(core.judge(calls, deadline, qualified)), deadline, "opencode")
    except (OSError, ValueError, TimeoutError) as exc:
        return _inert("save dispatcher could not complete: " + type(exc).__name__)
    if v["decision"] in ("allow", "advise") and ops:
        b = core.run_backups(ops, p["directory"], deadline)
        if b is not None:
            v = b
    if v["decision"] == "ask":
        body = core.bounded(v["reason"] or "", [],
                            core.max_chars() - len(core.ASK_PREFIX) - len(core.ASK_SUFFIX))
        return _out("deny", False, core.ASK_PREFIX + body + core.ASK_SUFFIX)
    if v["decision"] == "deny":
        return _out("deny", bool(v.get("inert")), v["reason"])
    if v["decision"] == "advise":
        return _out("advise", False, None, v.get("context"))
    return _out("allow")


def _written(p: dict) -> list[str]:
    tool, args, base = p["tool"], p["args"], p["directory"]
    if tool in FILE_TOOLS:
        return [core._abspath(_arg(args, "filePath", tool), base)]
    if tool == PATCH_TOOL:
        return [core._abspath(x, base) for op, x in patch_paths.parse_patch_opencode(_arg(args, "patchText", tool))
                if op in core.VALIDATE_OPS]
    return []


def post(p: dict) -> dict:
    deadline = time.monotonic() + core.budget_s()
    try:
        paths = _written(p)
    except (Unreadable, patch_paths.PatchParseError) as e:
        return _out("advise", context=f"X4 VALIDATION DID NOT RUN: {e}.")
    notes = []
    for ap in paths:
        out, err = core._run_hook("x4validate-on-edit.sh",
                                  {"hook_event_name": "PostToolUse", "tool_name": "Write",
                                   "tool_input": {"file_path": ap, "content": ""}},
                                  deadline - time.monotonic())
        if err:
            notes.append(f"X4 VALIDATION DID NOT RUN for {ap}: {err}")
            continue
        try:
            _, reason, ctx = core.x4guard.parse_hook_output(out or "")
        except ValueError:
            notes.append(f"X4 VALIDATION DID NOT RUN for {ap}: unreadable validator hook output")
            continue
        if ctx or reason:
            notes.append(ctx or reason)
    if not notes:
        return _out("allow")
    return _out("advise", context=core.bounded(notes[0], [("", n) for n in notes[1:]]))


def handle(event: str, raw: bytes) -> dict:
    if event not in EVENTS:
        return _inert(f"unknown adapter event argument {event!r}")
    try:
        p = _payload(raw)
    except Unreadable as e:
        return _inert(str(e)) if event == "pre" else _out("advise", context=f"X4 VALIDATION DID NOT RUN: {e}.")
    return pre(p) if event == "pre" else post(p)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    event = argv[0] if argv else ""
    try:
        v = handle(event, sys.stdin.buffer.read())
    except BaseException as e:            # noqa: BLE001 -- every failure must still print a verdict
        v = (_inert(f"the adapter raised {type(e).__name__}") if event != "post"
             else _out("advise", context=f"X4 VALIDATION DID NOT RUN: the adapter raised {type(e).__name__}."))
    sys.stdout.buffer.write(("X4OK " + json.dumps(v, ensure_ascii=True) + "\n").encode("ascii", "replace"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

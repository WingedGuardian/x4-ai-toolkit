#!/usr/bin/env python3
"""Agent-neutral X4 entry: stdin Action v1; structured JSON, no host exit assumptions."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools/x4_harness"))
import save_policy
import x4guard


TRANSCRIPT_REFUSED = (
    "--transcript is refused: this entry cannot verify that a named file is the host's own session "
    "transcript, so it cannot read a typed human save approval from it. Changes to more than two "
    "saves need that approval through the agent's native guard hook; or restore to a new folder "
    "with save-restore --to.")


class TranscriptRefused(ValueError):
    pass


def judge(calls, qualified, deadline, execution_host=""):
    # Save scope is qualified once, over the WHOLE action, before this call.
    # Only path-only write batches use the shared file backend.
    if qualified is not None and len(calls) > 1 and all(
            kind == "write" and shell is None and command is None
            for kind, shell, command, path, label in calls):
        verdicts = x4guard.write_verdicts([path for kind, shell, command, path, label in calls],
                                         deadline=deadline, save_qualified=qualified)
    else:
        verdicts = [x4guard.verdict_for(kind, shell, command, path, deadline=deadline, save_qualified=qualified,
                                   execution_host=execution_host)
                    for kind, shell, command, path, _label in calls]
    worst = dict(max(verdicts, key=lambda verdict: (bool(verdict.get("inert")), x4guard.RANK[verdict["decision"]])))
    notes = [verdict["context"] for verdict in verdicts if verdict.get("context")]
    if notes:
        worst["context"] = "\n".join(dict.fromkeys(notes))
    return worst


def save_lock_folders(profile):
    """The watched save folders, and each folder under a .protect-saves marker that holds saves."""
    import save_sentinel
    found = [str(f) for f in profile.save_roots]
    for top in save_sentinel.marked_folders(ROOT):
        for folder, _dirs, names in os.walk(top):
            if any(n.lower().endswith(profile.extensions) for n in names):
                found.append(folder)
    return list(dict.fromkeys(found))


def save_lock_command(args):
    """status / off / unlock for the OS save locks; `on` refuses. `unlock <save>` backs the save up into the permanent
    store first, and unlocks nothing when that backup fails. `unlock <folder>` and `off` only change permissions and
    back nothing up: they are the way back when a backup cannot be made."""
    if args.action == "on":
        return {"decision": "refused", "reason": "save locks cannot be turned on in this version: a lock binds X4 too, "
                "and a player must be able to save over a save. Nothing was changed."}
    import save_sentinel
    lock = save_policy.core(ROOT, "save_lock")
    if (args.action == "unlock") != bool(args.target):
        return {"decision": "refused", "reason": "save-lock unlock takes one save or folder; the others take none"}
    if args.forget_missing and args.action != "off":
        return {"decision": "refused", "reason": "--forget-missing goes with save-lock off"}
    deadline = time.monotonic() + 40
    roots = save_policy.read_roots(deadline)
    profile = save_policy.core(ROOT, "profiles").GameProfile("x4", save_sentinel.save_folders(roots),
                                                           save_sentinel.EXTENSIONS)
    switch = save_sentinel.lock_switch(ROOT)
    try:
        if args.action == "status":
            rows = []
            for folder in save_lock_folders(profile):
                state = lock.status_map(folder, profile.extensions)
                rows.append({"folder": folder, "folder_locked": lock.is_locked(folder),
                             "saves": len(state), "locked": sum(state.values())})
            return {"enabled": bool(lock.read_config(switch).get("enabled")), "elevated": lock.is_elevated(),
                    "folders": rows}
        if args.action == "off":
            lifted = lock.turn_off(switch, save_lock_folders(profile), profile.extensions,
                                   forget_missing=args.forget_missing)
            return {"enabled": False, "locks_lifted": lifted,
                    "note": "every save and folder checked now reads plain"
                            + (" (folders that were missing were forgotten, not checked)" if args.forget_missing else "")}
        sid = lock.user_sid()
        target = Path(args.target)
        if target.is_dir():
            paths = [target] + [p for p in target.iterdir()
                                if p.is_file() and p.name.lower().endswith(profile.extensions)]
            for p in paths:
                lock.unlock(p, sid)
            return {"unlocked": [str(p) for p in paths]}
        if not target.name.lower().endswith(profile.extensions):
            return {"decision": "refused", "reason": "not a save: " + str(target)}

        def backup(paths):
            store = save_policy.core(ROOT, "workspace").save_store(ROOT, profile)
            manifest = store.backup([profile.members(target)], {"profile": "x4", "host": "user-unlock"}, deadline)
            if not manifest:
                raise OSError("the backup recorded nothing")
        done = lock.backup_then_unlock(target, [], sid, backup)
        return {"backed_up_and_unlocked": [str(p) for p in done]}
    except lock.Unsupported as e:
        return {"decision": "unsupported", "reason": str(e)}
    except (PermissionError, OSError) as e:
        return {"decision": "refused", "reason": str(e)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--preview", action="store_true")
    check.add_argument("--transcript", default="")
    sub.add_parser("capabilities")
    sub.add_parser("preferences")
    notes = sub.add_parser("notes-preserve")
    notes.add_argument("--destination", required=True, type=Path)
    notes.add_argument("--reference", required=True, type=Path)
    upgrade_cli = save_policy.core(ROOT, "installation")
    upgrade_cli.add_arguments(sub)
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--session-id", required=True)
    sub.add_parser("save-list")
    # OS save locks (modding_harness/save_lock.py): `on` refuses (user ruling 2026-11-04: a lock binds the game too, and
    # a player must be able to save over a save). `off` and `unlock` stay, and need an elevated prompt: locks outlast
    # the toolkit and ordinary Windows tools cannot lift them.
    locks = sub.add_parser("save-lock")
    locks.add_argument("action", choices=("status", "on", "off", "unlock"))
    locks.add_argument("target", nargs="?")
    locks.add_argument("--forget-missing", action="store_true",
                       help="off: stop accounting for locked folders that are no longer where they were")
    restore = sub.add_parser("save-restore")
    restore.add_argument("transaction")
    destination = restore.add_mutually_exclusive_group()
    destination.add_argument("--to", type=Path)
    destination.add_argument("--in-place", action="store_true")
    restore.add_argument("--path", action="append", default=[])
    restore.add_argument("--host", default="generic")
    restore.add_argument("--session-id", default="")
    restore.add_argument("--transcript", default="")
    args = parser.parse_args(argv)
    contracts = save_policy.core(ROOT, "contracts")
    exit_status = 0
    try:
        if getattr(args, "transcript", ""):
            # H1 (v4.0 release review): this entry cannot verify that a caller-named file is the
            # host's own transcript, nor that the declared --host wrote it. MEASURED: a one-line
            # forged jsonl yielded a grant matching a 3-save delete. Only a native hook, which
            # receives transcript_path in the HOST's own hook input, may read typed approvals.
            raise TranscriptRefused(TRANSCRIPT_REFUSED)
        if args.command == "capabilities":
            output = {"api_version": 1, "profile": "x4", "unknown_policy": "warn and allow",
                      "surfaces": {"files": "adapter fixtures", "shell": "Bash/PowerShell backend fixtures",
                                   "mcp": "unguarded", "nested_code_mode": "unguarded"},
                      "host_enforcement": "unmeasured; qualify the actual host transport and review hooks",
                      "post_validation": "not run by this pre-operation entry",
                      "save_restore": "new directory by default; guarded in-place replacement preserves current saves"}
        elif args.command == "notes-preserve":
            deadline = time.monotonic() + 40
            digest = save_policy.core(ROOT, "durable").digest(args.reference, deadline)
            output = save_policy.core(ROOT, "upgrade").preserve_notes(args.destination, digest, deadline=deadline)
        elif args.command == "preferences":
            output = {"context": save_policy.core(ROOT, "preferences").render(ROOT),
                      "scope": "explicit My preferences section only; unclassified notes remain notes"}
        elif args.command in upgrade_cli.COMMANDS:
            import x4_upgrade_render
            output = upgrade_cli.handle(args, ROOT, local_files=("x4-paths.env", ".claude/x4-paths.env"),
                candidate_preparer=x4_upgrade_render.prepare, candidate_validator=x4_upgrade_render.validate,
                generated_paths=(".codex/hooks.json",))
        elif args.command == "coverage":
            output = save_policy.core(ROOT, "telemetry").Telemetry(ROOT).report(args.session_id)
        elif args.command == "save-lock":
            output = save_lock_command(args)
            exit_status = {"refused": 2, "unsupported": 3}.get(output.get("decision"), 0)
        elif args.command in ("save-list", "save-restore"):
            deadline = time.monotonic() + 40
            roots = save_policy.read_roots(deadline)
            profile = save_policy.resolve(ROOT, roots, [], deadline)[0]
            workspace = save_policy.core(ROOT, "workspace")
            store = workspace.save_store(ROOT, profile)
            if args.command == "save-list":
                rows, unreadable = workspace.summaries(store)
                output = {"transactions": rows, "unreadable": unreadable,
                          "blob_verification": "runs for selected saves on restore", "destination": str(store.root)}
            else:
                if not args.in_place:
                    output = store.restore(args.transaction, args.to, args.path, deadline=deadline)
                else:
                    canonical = save_policy.core(ROOT, "profiles").canonical
                    selected = {canonical(path) for path in args.path}
                    manifest = store.inspect(args.transaction)
                    groups = [group for group in manifest["groups"] if not selected or
                              any(member["canonical"] in selected for member in group["members"])]
                    members = [member for group in groups for member in group["members"]]
                    if selected - {member["canonical"] for member in members}:
                        raise ValueError("selected path is not in this transaction")
                    if any(len(group["members"]) != 1 for group in groups) or any(
                            canonical(Path(member["path"]).parent) not in {canonical(root) for root in profile.save_roots}
                            or not any(member["path"].lower().endswith(ext) for ext in profile.extensions)
                            for member in members):
                        raise ValueError("in-place recovery is limited to this profile's configured save files")
                    store.verify(groups, deadline)
                    calls = [("write", None, None, member["path"], member["path"]) for member in members]
                    payload = {"tool_name": "save-restore", "session_id": args.session_id,
                               "transcript_path": args.transcript,
                               "tool_input": {"command": json.dumps(sys.argv[1:] if argv is None else argv)}}
                    checked = save_policy.batch(payload, calls, lambda qualified: judge(calls, qualified, deadline),
                                                deadline, args.host, helper=True)
                    if checked["decision"] in ("deny", "ask"):
                        output = checked
                    else:
                        output = store.restore(args.transaction, selected=args.path, in_place=True, deadline=deadline)
                        output["pre_restore_transactions"] = checked["transactions"]
        else:
            action = save_policy.core(ROOT, "generic").parse(sys.stdin.buffer.read(1024 * 1024 + 1))
            if action.surface == "shell":
                if action.shell not in ("bash", "powershell") or not action.command:
                    raise ValueError("shell action needs the actual supported shell and command")
                calls = [("shell", action.shell, action.command, None, "shell command")]
            elif action.surface == "files":
                calls = [(operation, None, None, path, path) for path, operation in zip(action.paths, action.operations)
                         if operation != "read"]
            else:
                calls = []
            if action.cwd:
                os.chdir(action.cwd)
            payload = {"tool_name": action.tool, "session_id": action.session_id, "tool_use_id": action.tool_call_id,
                       "host_version": action.host_version, "tool_input": {"command": action.command},
                       "transcript_path": args.transcript}
            deadline = time.monotonic() + 40
            # K6: an action with no cwd says nothing about where the host will execute it, so
            # this process's own directory must never stand in for it (the vendored generic
            # parser defaults provenance to "known"). Unknown means relative mutation targets
            # are refused rather than resolved against the wrong folder.
            known_cwd = bool(action.cwd) and action.provenance.get("execution_cwd_known", True) is not False
            output = save_policy.batch(payload, calls, lambda qualified: judge(calls, qualified, deadline, action.host),
                                       deadline, action.host, args.preview, execution_cwd_known=known_cwd)
    except TranscriptRefused as exc:
        # A structured refusal like every other decision here (exit 0; adapters block on it).
        result = contracts.Result("deny", "evaluated", reason=str(exc)).as_dict()
        output = {"api_version": 1, "decision": "deny", "reason": str(exc), "result": result,
                  "transactions": []}
    except (OSError, ValueError, RecursionError, UnicodeError, TimeoutError) as exc:
        if args.command in upgrade_cli.COMMANDS:
            output, exit_status = upgrade_cli.failure(args.command, exc, "generic X4 entry failed")
        else:
            if args.command == "notes-preserve":
                exit_status = 2  # An installer must refuse its copy when preservation failed.
            output = {"api_version": 1, "result": contracts.Result("deny", "failed", reason=
                      "generic X4 entry failed: " + type(exc).__name__).as_dict(), "transactions": []}
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=True) + "\n").encode("ascii"))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())

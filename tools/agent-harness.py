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


def judge(calls, qualified, deadline):
    verdicts = [x4guard.verdict_for(kind, shell, command, path, deadline=deadline, save_qualified=qualified)
                for kind, shell, command, path, _label in calls]
    worst = dict(max(verdicts, key=lambda verdict: (bool(verdict.get("inert")), x4guard.RANK[verdict["decision"]])))
    notes = [verdict["context"] for verdict in verdicts if verdict.get("context")]
    if notes:
        worst["context"] = "\n".join(dict.fromkeys(notes))
    return worst


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
    upgrade = sub.add_parser("upgrade-preview")
    upgrade.add_argument("--candidate", required=True, type=Path)
    upgrade.add_argument("--previous", type=Path)
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--session-id", required=True)
    sub.add_parser("save-list")
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
        elif args.command == "upgrade-preview":
            load = save_policy.core(ROOT, "transport").load
            def read_manifest(path):
                with path.open("rb") as stream:
                    return load(stream.read(1024 * 1024 + 1))
            output = save_policy.core(ROOT, "upgrade").preview(ROOT, read_manifest(args.candidate),
                     read_manifest(args.previous) if args.previous else None, deadline=time.monotonic() + 20)
        elif args.command == "coverage":
            output = save_policy.core(ROOT, "telemetry").Telemetry(ROOT).report(args.session_id)
        elif args.command in ("save-list", "save-restore"):
            roots = save_policy.read_roots(time.monotonic() + 20)
            profile = save_policy.resolve(ROOT, roots, [])[0]
            workspace = save_policy.core(ROOT, "workspace")
            store = workspace.save_store(ROOT, profile)
            if args.command == "save-list":
                rows, unreadable = workspace.summaries(store)
                output = {"transactions": rows, "unreadable": unreadable,
                          "blob_verification": "runs for selected saves on restore", "destination": str(store.root)}
            else:
                if not args.in_place:
                    output = store.restore(args.transaction, args.to, args.path)
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
                    store.verify(groups)
                    calls = [("write", None, None, member["path"], member["path"]) for member in members]
                    deadline = time.monotonic() + 40
                    payload = {"tool_name": "save-restore", "session_id": args.session_id,
                               "transcript_path": args.transcript,
                               "tool_input": {"command": json.dumps(sys.argv[1:] if argv is None else argv)}}
                    checked = save_policy.batch(payload, calls, lambda qualified: judge(calls, qualified, deadline),
                                                deadline, args.host, helper=True)
                    if checked["decision"] in ("deny", "ask"):
                        output = checked
                    else:
                        output = store.restore(args.transaction, selected=args.path, in_place=True)
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
            output = save_policy.batch(payload, calls, lambda qualified: judge(calls, qualified, deadline),
                                       deadline, action.host, args.preview)
    except (OSError, ValueError, RecursionError, UnicodeError, TimeoutError) as exc:
        if args.command == "notes-preserve":
            exit_status = 2  # An installer must refuse its copy when preservation failed.
        output = {"api_version": 1, "result": contracts.Result("deny", "failed", reason=
                  "generic X4 entry failed: " + type(exc).__name__).as_dict(), "transactions": []}
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=True) + "\n").encode("ascii"))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())

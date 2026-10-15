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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--preview", action="store_true")
    sub.add_parser("capabilities")
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--session-id", required=True)
    sub.add_parser("save-list")
    restore = sub.add_parser("save-restore")
    restore.add_argument("transaction")
    restore.add_argument("--to", type=Path)
    restore.add_argument("--path", action="append", default=[])
    args = parser.parse_args(argv)
    contracts = save_policy.core(ROOT, "contracts")
    try:
        if args.command == "capabilities":
            output = {"api_version": 1, "profile": "x4", "unknown_policy": "warn and allow",
                      "surfaces": {"files": "adapter fixtures", "shell": "Bash/PowerShell backend fixtures",
                                   "mcp": "unguarded", "nested_code_mode": "unguarded"},
                      "host_enforcement": "unmeasured; qualify the actual host transport and review hooks",
                      "post_validation": "not run by this pre-operation entry",
                      "save_restore": "new directory; live in-place restore is not exposed by this entry"}
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
                output = store.restore(args.transaction, args.to, args.path)
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
                       "host_version": action.host_version, "tool_input": {"command": action.command}}
            deadline = time.monotonic() + 40
            def judge(qualified):
                verdicts = [x4guard.verdict_for(kind, shell, command, path, deadline=deadline, save_qualified=qualified)
                            for kind, shell, command, path, _label in calls]
                return max(verdicts, key=lambda verdict: (bool(verdict.get("inert")), x4guard.RANK[verdict["decision"]]))
            output = save_policy.batch(payload, calls, judge, deadline, action.host, args.preview)
    except (OSError, ValueError, RecursionError, UnicodeError, TimeoutError) as exc:
        output = {"api_version": 1, "result": contracts.Result("deny", "failed", reason=
                  "generic X4 entry failed: " + type(exc).__name__).as_dict(), "transactions": []}
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=True) + "\n").encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/bin/bash
# UserPromptSubmit (`prompt`) and SessionStart (`session-start`, the default): the session charter.
# The first prompt of a session is recorded as its origin in .agent-sessions/<session id>.md; at
# every session start, including after context compaction, that file is printed back. Its own
# hook entry, so its bounded text (4 KiB body) never competes with the canary for the preview.
# Parameter expansion for HOOK_DIR, both separators: see check-reference-version.sh.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"
MODE="${1:-session-start}"
x4_resolve_python
if [ -z "$X4_PY" ]; then
  [ "$MODE" = prompt ] || echo "SESSION CHARTER: NOT INJECTED -- Python is unavailable."
  exit 0
fi
"$X4_PY" -B "$HOOK_DIR/charter.py" "$MODE" 2>/dev/null || {
  [ "$MODE" = prompt ] || echo "SESSION CHARTER: NOT INJECTED -- the charter hook failed."
}
exit 0

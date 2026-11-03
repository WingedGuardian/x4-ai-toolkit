#!/bin/bash
# PostToolUse: the save sentinel. After a call, reports saves changed with no backup taken first --
# the gap the pre-call save guard cannot see (code that builds a save path at run time). The logic is
# save_sentinel.py over the pinned harness's sentinel. Advisory: it never denies, and a failure to run
# is disclosed as additionalContext, never silence.
# Parameter expansion for HOOK_DIR, both separators (see x4validate-on-edit.sh for why).
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"

INPUT=$(x4_hook_input)
[ -z "$INPUT" ] && exit 0      # advisory: an absent payload must not prompt

PY="$(x4_python)"              # ONE implementation, shared with every other hook
if [ -z "$PY" ]; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"SAVE SENTINEL DID NOT RUN (no Python): saves were not checked after this call."}}'
  exit 0
fi
{ x4_emit_guard_roots; printf '%s' "$INPUT"; } | "$PY" "$HOOK_DIR/save_sentinel.py"
exit 0

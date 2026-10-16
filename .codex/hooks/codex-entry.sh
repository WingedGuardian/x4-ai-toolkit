#!/bin/bash
# codex-entry.sh -- the fail-closed POSIX entry for the toolkit's Codex hooks (Linux/macOS).
#
#   bash "<root>/.codex/hooks/codex-entry.sh" <event>        (hooks.json `command`; payload on stdin)
#
# Every path prints a JSON verdict and exits 0 (MEASURED on Codex 0.160.0: a crash, a non-zero
# exit or output Codex cannot parse is "Failed" and the tool call RUNS; only a JSON deny
# blocks). The adapter's answer is passed through only when it is exactly one `X4OK` line whose
# body is a shape Codex honours; anything else becomes a deny (PreToolUse) or an advisory.
#
# POSIX support is BEST EFFORT, not device-tested (Plan 2 DECISIONS #15). Windows uses
# codex-entry.ps1: under cmd.exe, `bash` can be the WSL stub (MEASURED).
# Test-only knobs: X4_WRAPPER_TIMEOUT_S (default 50; 25 for session_start) and
# X4_NO_PYTHON_FALLBACK=1. Never logs the environment (hooks inherit secrets; MEASURED).
set -u
EV="${1:-}"
EMITTED=0
TMPD=""

emit(){ printf '%s' "$1"; EMITTED=1; }

fail(){
  local safe
  safe=$(printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9 ._:/()=,+-' '?' | cut -c1-300)
  case "$EV" in
    session_start) emit '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"X4 GUARDS NOT LIVE: the toolkit session hook failed ('"$safe"'). Tell the user the X4 guards may not be running and ask them to run x4doctor."}}' ;;
    post_tool_use) emit '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"X4 VALIDATION DID NOT RUN: the toolkit hook failed ('"$safe"')."}}' ;;
    *) emit '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"X4 GUARD INERT: the Codex hook wrapper failed ('"$safe"'). NOTHING was checked; this is a refusal, not a verdict. Ask the user to run x4doctor."}}' ;;
  esac
}

finish(){
  [ "$EMITTED" = 1 ] || fail "the wrapper ended without a verdict"
  [ -n "$TMPD" ] && [ -d "$TMPD" ] && rm -rf -- "$TMPD"
  exit 0
}
trap finish EXIT
trap 'fail "wrapper error at line $LINENO"; exit 0' ERR
trap 'fail "wrapper interrupted"; exit 0' INT TERM HUP

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { fail "cannot locate the hooks directory"; exit 0; }
ADAPTER="$HERE/codex_adapter.py"
[ -f "$ADAPTER" ] || { fail "codex_adapter.py is missing"; exit 0; }

PY=()
if [ -n "${X4_PYTHON:-}" ] && command -v "$X4_PYTHON" >/dev/null 2>&1; then PY=("$X4_PYTHON")
elif [ "${X4_NO_PYTHON_FALLBACK:-}" != 1 ]; then
  if command -v py >/dev/null 2>&1; then PY=(py -3)
  elif command -v python3 >/dev/null 2>&1; then PY=(python3)
  elif command -v python >/dev/null 2>&1; then PY=(python)
  fi
fi
[ "${#PY[@]}" -gt 0 ] || { fail "no Python found (set X4_PYTHON)"; exit 0; }

LIMIT=50; [ "$EV" = session_start ] && LIMIT=25
[ -n "${X4_WRAPPER_TIMEOUT_S:-}" ] && LIMIT="$X4_WRAPPER_TIMEOUT_S"
case "$LIMIT" in ''|*[!0-9]*) fail "X4_WRAPPER_TIMEOUT_S is not a whole number"; exit 0 ;; esac

TMPD="$(mktemp -d 2>/dev/null)" || { fail "cannot create a temp directory"; exit 0; }
cat > "$TMPD/in" || { fail "cannot read the payload"; exit 0; }

"${PY[@]}" "$ADAPTER" "$EV" < "$TMPD/in" > "$TMPD/out" 2>/dev/null &
PID=$!
ticks=$((LIMIT * 10)); n=0
while kill -0 "$PID" 2>/dev/null; do
  if [ "$n" -ge "$ticks" ]; then
    command -v pkill >/dev/null 2>&1 && { pkill -KILL -P "$PID" 2>/dev/null || true; }
    kill -KILL "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    fail "the adapter did not answer within ${LIMIT} s"; exit 0
  fi
  sleep 0.1; n=$((n + 1))
done
RC=0; wait "$PID" || RC=$?
[ "$RC" = 0 ] || { fail "the adapter exited $RC"; exit 0; }

OUT="$(tr -d '\r' < "$TMPD/out")"
[ -n "$OUT" ] || { fail "the adapter printed nothing"; exit 0; }
NL=$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')
[ "$NL" = 1 ] || { fail "the adapter printed $NL lines"; exit 0; }
case "$OUT" in X4OK*) ;; *) fail "the adapter output lacks the X4OK prefix"; exit 0 ;; esac
BODY="${OUT#X4OK}"; BODY="${BODY#"${BODY%%[![:space:]]*}"}"
[ -n "$BODY" ] || { emit ""; exit 0; }

# The shape check needs a JSON parser; the adapter just ran on this Python, so use it.
BAD="$(printf '%s' "$BODY" | "${PY[@]}" -c '
import json, sys
def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out
try:
    o = json.loads(sys.stdin.read(), object_pairs_hook=unique)
except Exception:
    print("adapter output is not JSON"); sys.exit()
if not isinstance(o, dict) or list(o) != ["hookSpecificOutput"] or not isinstance(o["hookSpecificOutput"], dict):
    print("adapter output has keys other than hookSpecificOutput"); sys.exit()
h = o["hookSpecificOutput"]
event = {"session_start": "SessionStart", "post_tool_use": "PostToolUse"}.get(sys.argv[1], "PreToolUse")
allowed = {"hookEventName", "additionalContext"}
if event == "PreToolUse":
    allowed |= {"permissionDecision", "permissionDecisionReason"}
if set(h) - allowed:
    print("hookSpecificOutput has an unknown key"); sys.exit()
if h.get("hookEventName") != event:
    print("adapter output names no matching event"); sys.exit()
if any(not isinstance(value, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in value) for value in h.values()):
    print("adapter output has a nonstring or invalid Unicode field"); sys.exit()
if "permissionDecision" in h and h["permissionDecision"] != "deny":
    print("adapter answered a permissionDecision other than deny")
' "$EV" 2>/dev/null)" || BAD="the output shape check could not run"
[ -z "$BAD" ] || { fail "$BAD"; exit 0; }
emit "$BODY"
exit 0

#!/bin/bash
# PostToolUse (Edit|Write): advisory x4validate on a mod's diff-XML edits.
# Non-blocking — surfaces unmatched sel= findings as additionalContext. Never denies.
JQ="${JQ:-jq}"
UV="${UV:-uv}"
# Parameter expansion, not `$(cd "$(dirname "$0")" && pwd)`: that was a subshell AND
# a dirname process on every call (AUDIT-2026-09-24 HK-4). The settings command passes
# an absolute path, and a relative one still resolves: nothing here changes directory.
# BOTH separators: a hook started as `bash C:\...\protect-bash.sh` has a $0 with no
# forward slash at all, and reading it as "." sourced _x4-env.sh from the CALLER's
# directory -- MEASURED: the guard then found no python and asked on every command.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"
X4V="${X4V:-$X4_TOOLKIT/tools/x4validate}"

INPUT=$(x4_hook_input)
# Advisory only, so an absent payload must NOT prompt -- it exits below.
# Recurrence is caught statically by test-hooks.sh, which fails if any
# hook reads /dev/stdin again.
# x4_field, not a bare jq call. This line read through jq ALONE, so with jq missing
# it produced an empty FP and the next line exited 0 -- which made the python fallback
# added further down (for the same reason, in the same chain) structurally
# UNREACHABLE. MEASURED 2026-09-05 with `bash -x` and JQ=/nonexistent: `FP=` then
# `exit 0`, the whole hook inert. `_x4-env.sh` ships x4_field for exactly this, and
# documents the identical bug for search-scope.sh at its own definition: teach EVERY
# step of the chain, not the last one.
#
# It also removes a second interpreter probe further down that disagreed with the
# shared one: `x4_python` REFUSES when $X4_PYTHON is set to something missing, while
# the local loop silently fell through to python3 -- "a guard that runs under an
# interpreter the operator did not choose is a guard nobody configured".
FP=$(x4_field "$INPUT" tool_input.file_path)
# NotebookEdit names its file `notebook_path` (AUDIT-2026-09-24 HK-1 re-review). Only
# XML is validated below, so today this is a no-op for a notebook -- it is read so the
# matcher and the reader agree, and a future non-.ipynb notebook path is not missed.
[ -z "$FP" ] && FP=$(x4_field "$INPUT" tool_input.notebook_path)
[ -z "$FP" ] && exit 0

# Only XML files, and never the read-only reference tree.
echo "$FP" | grep -qiE '\.xml$' || exit 0
x4_under "$FP" "$X4_REFERENCE" && exit 0

F="${FP//\\//}"                  # normalize backslashes for bash
[ -f "$F" ] || exit 0
grep -qi '<diff' "$F" 2>/dev/null || exit 0   # only diff patches

# Mod root = nearest ancestor with content.xml (so this works wherever mods live)
D=$(dirname "$F"); ROOT=""
for _ in $(seq 1 25); do
  [ -f "$D/content.xml" ] && { ROOT="$D"; break; }
  ND=$(dirname "$D"); [ "$ND" = "$D" ] && break; D="$ND"
done
[ -z "$ROOT" ] && exit 0

# TIER SELECTION.  A file at <mod>/extensions/<target>/... is a CROSS-MOD patch, and
# Tier A builds base+DLC only -- so it reports "no base game file ... can never apply"
# for EVERY such file.  MEASURED 2026-08-28 on a real cross-mod overlay: Tier A
# error_count=1, Tier B error_count=0, same correct file.  Left on Tier A this hook
# cries wolf on every edit to a cross-mod overlay and trains the reader to ignore it,
# which is worse than not running at all.
REL="${F#"$ROOT"/}"
TIER=""
case "$REL" in
  extensions/*) TIER="--tier b" ;;
esac

OUT=$(cd "$X4V" && "$UV" run --python 3.13 x4validate "$ROOT" --file "$F" $TIER --json 2>/dev/null)
VALIDATE_RC=$?
not_completed() {
  x4_advise "VALIDATION NOT COMPLETED: $1. This edit has not been validated; run x4validate manually." PostToolUse
  exit 0
}
case "$VALIDATE_RC" in
  0|1|3) ;;  # JSON completeness matters too: errors can mask degraded exit 3
  *) not_completed "validator could not run (exit $VALIDATE_RC)" ;;
esac
[ -n "$OUT" ] || not_completed "validator returned no result"
# Both the PARSE and the EMIT need a renderer. With jq missing this block used to go
# quiet -- ERRS came back empty, `${ERRS:-0}` made it 0, and a real validation failure
# produced no advisory at all. Python is already a hard prerequisite here (the line above
# runs x4validate through uv), so it is always the right fallback.
if printf '%s' '{}' | "$JQ" -e . >/dev/null 2>&1; then
  printf '%s' "$OUT" | "$JQ" -e 'type == "object" and
    (.error_count | type == "number") and (.error_count >= 0) and
    (.error_count == (.error_count | floor)) and
    (.degraded == null or (.degraded | type == "boolean")) and
    (.skipped == null or ((.skipped | type == "array") and (.skipped | all(type == "object" and
      (.what | type == "string") and (.why | type == "string"))))) and
    (.findings | type == "array") and (.findings | all(type == "object" and
      (.severity | type == "string") and (.message | type == "string") and
      (.vpath | type == "string") and (.line | type == "number") and (.line == (.line | floor))))' >/dev/null 2>&1 \
    || not_completed "validator returned unreadable or incomplete JSON"
  ERRS=$(printf '%s' "$OUT" | "$JQ" -r '.error_count // 0' 2>/dev/null)
  DEGRADED=$(printf '%s' "$OUT" | "$JQ" -r '.degraded // false' 2>/dev/null)
  SKIP_MSG=$(printf '%s' "$OUT" | "$JQ" -r '(.skipped // [])[] | "  [not checked] \(.what): \(.why)"' 2>/dev/null)
  MSG=$(printf '%s' "$OUT" | "$JQ" -r '.findings[] | "  [\(.severity)] \(.message) (\(.vpath):\(.line))"' 2>/dev/null)
else
  PY="$(x4_python)"      # ONE implementation, shared with every other hook
  if [ -n "$PY" ]; then
    ERRS=$(X4_OUT="$OUT" "$PY" -c 'import json, os, sys
try:
    d = json.loads(os.environ["X4_OUT"])
    assert isinstance(d, dict)
    n = d["error_count"]; f = d["findings"]
    assert type(n) is int and n >= 0 and isinstance(f, list) and all(isinstance(x, dict) for x in f)
    assert type(d.get("degraded", False)) is bool
    skipped = d.get("skipped", [])
    assert isinstance(skipped, list) and all(isinstance(x, dict) and isinstance(x.get("what"), str) and
                                          isinstance(x.get("why"), str) for x in skipped)
    assert all(isinstance(x.get("severity"), str) and isinstance(x.get("message"), str) and
               isinstance(x.get("vpath"), str) and type(x.get("line")) is int for x in f)
    sys.stdout.write(str(n))
except Exception: sys.exit(9)' 2>/dev/null) || not_completed "validator returned unreadable or incomplete JSON"
    DEGRADED=$(X4_OUT="$OUT" "$PY" -c 'import json, os
print("true" if json.loads(os.environ["X4_OUT"]).get("degraded", False) else "false")' 2>/dev/null)
    SKIP_MSG=$(X4_OUT="$OUT" "$PY" -c 'import json, os
print("\n".join("  [not checked] %s: %s" % (x["what"], x["why"]) for x in
                json.loads(os.environ["X4_OUT"]).get("skipped", [])))' 2>/dev/null)
    MSG=$(X4_OUT="$OUT" "$PY" -c 'import json, os, sys
try: f = json.loads(os.environ["X4_OUT"]).get("findings") or []
except Exception: f = []
sys.stdout.write("\n".join("  [%s] %s (%s:%s)" % (x.get("severity"), x.get("message"), x.get("vpath"), x.get("line")) for x in f))' 2>/dev/null)
  else
    not_completed "neither jq nor Python could read the validator result"
  fi
fi
if [ "${DEGRADED:-false}" = true ] || [ "$VALIDATE_RC" = 3 ]; then
  x4_advise "VALIDATION NOT COMPLETED: one or more requested checks could not run. Findings collected so far:
$MSG
$SKIP_MSG
Run x4validate manually to inspect the skipped checks." PostToolUse
# NO advisory for a NON-degraded skip. `--file` always lists one ("every other check
# (--file)"): MEASURED 2026-10-02, 50 of 50 real edits across 23 dev mods carried it, so a
# "partial" note would fire on every edit and teach the reader to ignore this hook.
elif [ "${ERRS:-0}" -gt 0 ]; then
  x4_advise "x4validate (advisory${TIER:+, tier B}) flagged this edit:
$MSG" PostToolUse
fi
exit 0

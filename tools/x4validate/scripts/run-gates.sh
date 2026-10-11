#!/usr/bin/env bash
# Run the contributor gates, and SAY WHAT WAS NOT RUN.
#
# WHY THIS EXISTS. Until 2026-08-28 there were 27 gates and no runner -- and a
# count in a comment goes stale the moment one is added, so the roster below is
# discovered from gates/*.py and the live count is printed, never asserted. CI cannot
# help -- ci.yml states it: the gates need a real X4 install. So they ran when
# someone remembered, which makes every gate's coverage a matter of chance rather
# than of process. A gate nobody runs is indistinguishable from a gate that passes.
#
# The contract this inherits from the tools themselves: a run that examined less
# than everything must SAY SO. Skipped gates are named, with the reason, and the
# buckets sum to the population -- never a bare "all green".
#
#   scripts/run-gates.sh            quick gates only (seconds to ~1 min each)
#   scripts/run-gates.sh --all      everything, including the ~55 min sweep
#   scripts/run-gates.sh --list     show the roster and exit
#
# Exit: 0 everything attempted PASSED and everything attempted RAN
#       1 a gate failed
#       2 nothing ran at all
#       3 every gate that ran passed, but some COULD NOT RUN -- not a clean
#         sweep. Split out because 0 said 'fine' over gates that examined
#         nothing, which is the same conflation the tools themselves refuse:
#         'could not look' is not 'nothing wrong'. A caller that genuinely
#         accepts missing fixtures can treat 3 as success; the release
#         checklist must not.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# Runtimes from gates/README.md, MEASURED on the reference machine.
SLOW="corpus_sweep perf_guard xsd_fast_parity schema_sweep noop_audit regress stress_sweep update_corpus hook_false_positives"
# Rewrites source in place; must never run beside anything that reads the tree.
MUTATING="mutation_probe"

mode="quick"
case "${1:-}" in
  --all)  mode="all" ;;
  --list) mode="list" ;;
  "")     ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

all_gates=()
for f in gates/*.py; do
  b=$(basename "$f" .py)
  case "$b" in __init__|_env) continue ;; esac
  all_gates+=("$b")
done
[ ${#all_gates[@]} -eq 0 ] && { echo "no gates found under gates/" >&2; exit 2; }

if [ "$mode" = "list" ]; then
  printf '%s\n' ${all_gates[@]+"${all_gates[@]}"}; exit 0
fi

run=(); skip_slow=(); skip_mut=()
for g in ${all_gates[@]+"${all_gates[@]}"}; do
  if [[ " $MUTATING " == *" $g "* ]]; then skip_mut+=("$g"); continue; fi
  if [ "$mode" = "quick" ] && [[ " $SLOW " == *" $g "* ]]; then skip_slow+=("$g"); continue; fi
  run+=("$g")
done

# FULL PER-GATE LOGS + MACHINE STATE (GitHub issue #3). `cross_tool` once failed in a
# release sweep and never reproduced; this runner kept only a short tail of its output and
# nothing recorded memory or load, so the failure left nothing to read. Every attempted
# gate's whole stdout+stderr now goes to <logdir>/<gate>.log, and <logdir>/system.txt gets
# free memory and load at the start and at each failure. X4_GATE_LOG_DIR overrides the
# default (a fresh temp dir); it is refused inside the game or reference tree.
#
# WHICH game/reference: the ones the TOOLS resolve -- environment, then the path config --
# asked of x4validate._paths, never only the exported variables (v4.0.0 review R4-7/R6-10:
# a root named only in x4-paths.env was not seen). The containment test is Python's too:
# `realpath -m` does not exist on macOS, where the old check failed OPEN. A resolver that
# cannot run REFUSES: a log dir that cannot be checked is not a log dir that passed.
_log_dir_inside_a_root(){ # prints "VAR (root)" when $1 is inside a resolved root; rc 2 = cannot tell
  PYTHONDONTWRITEBYTECODE=1 uv run python - "$1" <<'PY'
import os, sys
try:
    from x4validate import _paths
except Exception as exc:
    print("cannot import x4validate._paths: %s" % exc, file=sys.stderr)
    sys.exit(2)
# BOTH spellings, links RESOLVED too (FX-B2, delta review: abspath alone let a log dir that is
# a symlink/junction INTO the reference tree pass as "outside"). realpath is non-strict: a
# path that does not exist yet still resolves its existing prefix.
def _forms(p):
    return {os.path.normcase(os.path.abspath(p)).rstrip(os.sep),
            os.path.normcase(os.path.realpath(p)).rstrip(os.sep)}
ds = _forms(_paths.native(sys.argv[1]))
for name, fn in (("X4_GAME", _paths.game_root), ("X4_REFERENCE", _paths.reference)):
    try:
        root = fn()
    except Exception as exc:
        print("cannot resolve %s: %s" % (name, exc), file=sys.stderr)
        sys.exit(2)
    if root is None:
        continue
    for r in _forms(str(root)):
        if any(d == r or d.startswith(r + os.sep) for d in ds):
            print("%s (%s)" % (name, root))
            sys.exit(0)
sys.exit(1)
PY
}
if [ -n "${X4_GATE_LOG_DIR:-}" ]; then
  _hit="$(_log_dir_inside_a_root "$X4_GATE_LOG_DIR")"; _rc=$?
  if [ "$_rc" = 0 ]; then
    echo "REFUSING: X4_GATE_LOG_DIR ($X4_GATE_LOG_DIR) is inside $_hit; gate logs never go there" >&2
    exit 2
  elif [ "$_rc" != 1 ]; then
    echo "REFUSING: cannot check X4_GATE_LOG_DIR ($X4_GATE_LOG_DIR) against the game and reference trees (the resolver failed, rc $_rc); unset it to use a fresh temp dir" >&2
    exit 2
  fi
  logdir="$X4_GATE_LOG_DIR"
  mkdir -p "$logdir" || { echo "REFUSING: cannot create X4_GATE_LOG_DIR $logdir" >&2; exit 2; }
else
  logdir="$(mktemp -d "${TMPDIR:-${TEMP:-/tmp}}/x4-gates.XXXXXX")" \
    || { echo "REFUSING: cannot create a temp dir for the gate logs" >&2; exit 2; }
fi
# One spelling a reader can paste: the Windows form under Git Bash (pwd -W), else POSIX.
logdir="$(cd "$logdir" && { pwd -W 2>/dev/null || pwd; })" || { echo "REFUSING: gate log dir vanished" >&2; exit 2; }
sysstate(){ # sysstate <label>: append one machine-state block to system.txt
  {
    echo "=== $1 $(date '+%Y-%m-%dT%H:%M:%S%z')"
    ma=""
    [ -r /proc/meminfo ] && ma="$(awk '/^MemAvailable:/{print $2" kB"}' /proc/meminfo)"
    [ -z "$ma" ] && [ -r /proc/meminfo ] && ma="$(awk '/^MemFree:/{print $2" kB (MemFree; no MemAvailable)"}' /proc/meminfo)"
    echo "mem_available: ${ma:-unavailable (no /proc/meminfo)}"
    if [ -r /proc/loadavg ]; then echo "load: $(cat /proc/loadavg)"; else echo "load: unavailable (no /proc/loadavg)"; fi
    echo "cpus: $(nproc 2>/dev/null || echo unknown)"
  } >> "$logdir/system.txt"
}
sysstate start

echo "GATE RUN — mode=$mode — ${#all_gates[@]} gate(s) known"
echo "GATE LOGS: $logdir"
echo "=================================================================="
pass=(); fail=(); cannot=()
for g in ${run[@]+"${run[@]}"}; do
  # The replay gate otherwise defaults to 14 workers on this machine. Keep this
  # managed sweep within the user's eight-worker policy; other arguments stay intact.
  gate_args=()
  [ "$g" = "hook_false_positives" ] && gate_args+=(--workers=8)
  # PYTHONDONTWRITEBYTECODE: a gate run must not leave a .pyc that a later run
  # could reuse against a same-second, same-size edit (see test_no_stale_bytecode).
  PYTHONDONTWRITEBYTECODE=1 uv run python "gates/$g.py" ${gate_args[@]+"${gate_args[@]}"} > "$logdir/$g.log" 2>&1; rc=$?
  out=$(cat "$logdir/$g.log")
  case $rc in
    0) pass+=("$g");   printf '  ok      %-26s\n' "$g" ;;
    2) cannot+=("$g"); printf '  CANNOT  %-26s %s\n' "$g" "$(echo "$out" | tail -1 | cut -c1-70)" ;;
    *) fail+=("$g");   printf '  FAIL    %-26s rc=%s\n' "$g" "$rc"
       sysstate "FAIL $g rc=$rc"
       echo "$out" | tail -6 | sed 's/^/            /' ;;
  esac
done

echo "=================================================================="
printf 'attempted %d   passed %d   failed %d   could-not-run %d\n' \
       "${#run[@]}" "${#pass[@]}" "${#fail[@]}" "${#cannot[@]}"
total=$(( ${#run[@]} + ${#skip_slow[@]} + ${#skip_mut[@]} ))
printf 'NOT ATTEMPTED %d  (buckets sum to %d of %d)\n' \
       "$(( ${#skip_slow[@]} + ${#skip_mut[@]} ))" "$total" "${#all_gates[@]}"
[ ${#skip_slow[@]} -gt 0 ] && echo "  slow, use --all: ${skip_slow[*]}"
[ ${#skip_mut[@]}  -gt 0 ] && echo "  MUTATING, run alone and announce it: ${skip_mut[*]}"
[ ${#cannot[@]}    -gt 0 ] && echo "  could not run (missing baseline/fixture): ${cannot[*]}"
echo "full output of every gate, and machine state: $logdir"

[ ${#fail[@]} -gt 0 ] && exit 1
[ ${#pass[@]} -eq 0 ] && { echo "NOTHING PASSED — this is not a green run." >&2; exit 2; }
if [ ${#cannot[@]} -gt 0 ]; then
  echo >&2
  echo "NOT A CLEAN SWEEP: ${#pass[@]} passed, but ${#cannot[@]} gate(s) could not run" >&2
  echo "and therefore examined nothing. Exit 3, not 0 -- 'could not look' is not" >&2
  echo "'nothing wrong'. Supply the missing baseline/fixture, or accept 3 knowingly." >&2
  exit 3
fi
exit 0

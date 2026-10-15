#!/bin/bash
# SessionStart: has anything irreplaceable been lost since it was last committed?
#
# WHY THIS RUNS AUTOMATICALLY. On 2026-09-03 the mod registry went from 196,363 bytes
# and 258 hand-triaged entries to 46 bytes. `dev/` is a git repository and `git status`
# showed it from the moment it happened -- ` M _registry/modlist.yaml`, 8,222 deletions.
# Nobody ran it. The loss was found six hours later, by accident, while investigating
# something unrelated.
#
# The detector existed and nothing invoked it. That is the entire failure, so the fix is
# not a better detector -- it is a detector that runs without anyone remembering to run
# it. SessionStart stdout becomes session context, which is the one channel that is
# always read.
#
# Deliberately never fails the session. A refusal here would block work over a condition
# the user may already know about; the banner is what matters, and it is unmissable.
# `x4canary` itself still exits 1 on a loss and 2 when it could not check, for callers
# that want a gate (a subagent batch, CI).

# Parameter expansion, not `$(cd "$(dirname "$0")" && pwd)`: that was a subshell AND
# a dirname process on every call (AUDIT-2026-09-24 HK-4). The settings command passes
# an absolute path, and a relative one still resolves: nothing here changes directory.
# BOTH separators: a hook started as `bash C:\...\protect-bash.sh` has a $0 with no
# forward slash at all, and reading it as "." sourced _x4-env.sh from the CALLER's
# directory -- MEASURED: the guard then found no python and asked on every command.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"

# X4_GUARD=off (spec 5.7): FIRST, before the canary, because a session whose guards are off
# must be unmissable -- and the preview keeps the head.
if x4_guards_off; then
  echo "[x4 guards] *** GUARDS OFF (X4_GUARD=off at launch) *** Every guard verdict this session"
  echo "            is an ADVISORY: hard blocks (reference/, game files, saves) are NOT enforced"
  echo "            by the hooks. Each overridden call is logged to"
  echo "            ${X4_BACKUPS:-$X4_TOOLKIT/.claude/backups}/GUARDS-OFF.log."
  echo "            Unset X4_GUARD and restart the session to turn them back on."
fi

# The path config (Plan 3 lane I): a MISSING, DEPRECATED (3.x .claude/) or DOUBLED config is
# named once per session, here, and in x4doctor's roots.config -- never per tool call, and no
# guard verdict depends on it. Before the canary lookup, which can exit early. At most 4 lines:
# the config's IGNORED lines (an X4_GUARD line first, R1-F1), then its location.
x4_config_banner

# X4_GUARD IN A SETTINGS FILE'S env (user decision 2026-10-05). A settings `env` block reaches
# every hook (MEASURED, Claude Code 2.1.290), so a key there switches the guards from inside the
# project rather than at launch. Agents may not write one (settings_guard.py); this names one
# that is already there. Directive first, then the files -- paths only, never a value.
_x4_sg_files=()
for _f in "${CLAUDE_PROJECT_DIR:-}/.claude/settings.json" "${CLAUDE_PROJECT_DIR:-}/.claude/settings.local.json"           "${HOME:-}/.claude/settings.json" "${HOME:-}/.claude/settings.local.json"; do
  case "$_f" in /.claude/*) continue ;; esac
  [ -f "$_f" ] && _x4_sg_files[${#_x4_sg_files[@]}]="$_f"
done
_x4_sgpy="$(x4_python)"
if [ "${#_x4_sg_files[@]}" -gt 0 ] && [ -n "$_x4_sgpy" ]; then
  _x4_sghit="$("$_x4_sgpy" "$HOOK_DIR/settings_guard.py" --scan "${_x4_sg_files[@]}" 2>/dev/null)"
  if [ -n "$_x4_sghit" ]; then
    echo "[x4 guards] X4_GUARD is set in a Claude Code settings env block -- it reaches every hook. Tell the user; do not edit it yourself (the guards refuse). File(s):"
    printf '%s
' "$_x4_sghit" | sed 's/^/            /'
  fi
fi

# The canary lives in the toolkit, not beside the hooks: it is a tool, and it needs the
# x4validate package to resolve roots. If the toolkit is not configured there is nothing
# to say -- but say THAT, rather than printing a reassuring nothing.
CANARY=""
for c in "${X4_TOOLKIT:-}/scripts/x4canary.py" "$HOOK_DIR/../../scripts/x4canary.py"; do
  [ -f "$c" ] && { CANARY="$c"; break; }
done
if [ -z "$CANARY" ]; then
  echo "[x4 canary] NOT RUN: scripts/x4canary.py not found under \$X4_TOOLKIT (${X4_TOOLKIT:-unset})."
  echo "            Irreplaceable files are UNCHECKED this session."
  exit 0
fi

# The SHARED lookup (AUDIT-2026-09-24 HK-6). This hook resolved `python` itself, so it
# was the one hook that ignored X4_PYTHON -- and, with X4_PYTHON set to something that
# does not resolve, it quietly ran whatever python was on PATH where every other hook
# refuses. x4_python prints nothing in that case, which is reported below.
PY="$(x4_python)"
if [ -z "$PY" ]; then
  echo "[x4 canary] NOT RUN: no usable python (X4_PYTHON=${X4_PYTHON:-unset}; none on PATH otherwise). Irreplaceable files are UNCHECKED."
  exit 0
fi

OUT=$("$PY" "$CANARY" 2>&1)
RC=$?

# BOUNDED, AND DIRECTIVE FIRST. This hook writes BARE STDOUT, so it does not get
# x4_advise's cap for free -- and MEASURED 2026-09-07 with a 400-item report, the
# text reached 36,638 characters against a 10,000-character ceiling. Claude Code
# files anything above that and shows the model a ~2 KB preview, silently, with
# the exit code unchanged.
#
# The preview keeps the HEAD, so the ORDER is the fix, not just the cap: the
# recovery directive is printed BEFORE the report. Printed after it, the
# instruction is the first thing dropped -- and "do not re-run whatever wrote it"
# is the single line that stops the recoverable state being destroyed. The
# inventory is the part that can afford to be cut; the instruction is not.
#
# $CANARY is named because TWO copies of x4canary.py exist on a normal install --
# $X4_TOOLKIT/scripts/ and the one beside the hooks -- and this hook silently
# picks whichever it finds first. MEASURED on this machine: the resolved copy was
# 7,631 bytes with 2 refusal guards while the repo's was 19,630 with 5, so four
# fixed false-DATA-LOSS bugs were not reaching the running hook. Nothing said so.
case $RC in
  0)
    # NOTHING LOST -- but 'nothing lost' over a PARTIALLY RESOLVED root set is a
    # narrower claim than it reads as, and rc 0 with one root configured and one
    # not is the COMMON case rather than an edge. x4canary's own
    # `_report_unresolved` docstring names this branch as the one that discards
    # its disclosure, then fixes rc 1 and rc 2 and leaves rc 0 inside it.
    #
    # Only the NOT-CHECKED line is surfaced, and only when there is one: a clean
    # run over a fully resolved set stays silent, which is what keeps the session
    # start quiet.
    #
    # To STDOUT (AUDIT-2026-09-24 HK-6). It went to stderr, and SessionStart adds a
    # hook's STDOUT to the session context -- so the one disclosure this branch exists
    # to surface was printed where the model never reads it.
    printf '%s
' "$OUT" | grep -F 'NOT CHECKED:' || :
    ;;
  1)
    x4_bound "$(
      echo "[x4 canary] *** A TRACKED IRREPLACEABLE FILE HAS BEEN LOST ***"
      echo "            Recover it BEFORE doing anything else, and do not re-run"
      echo "            whatever wrote it:  git -C <repo> checkout -- <path>"
      echo "            (report from $CANARY)"
      echo "$OUT" | sed 's/^/            /'
    )"
    echo
    ;;
  *)
    x4_bound "$(
      echo "[x4 canary] COULD NOT CHECK (exit $RC) -- this is not a clean result."
      echo "            (report from $CANARY)"
      echo "$OUT" | sed 's/^/            /'
    )"
    echo
    ;;
esac
exit 0

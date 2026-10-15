#!/bin/bash
# PreToolUse (Grep|Glob): a content search under the game's extensions/ folder returns a
# PARTIAL answer that looks complete.
#
# WHY. X4 mods ship as packed `.cat`/`.dat` archives, loose XML, or BOTH. Grep and Glob read
# loose files only. So a search over a mod that ships a .cat does not fail and does not say
# "some content was unreadable" -- it returns the loose subset, silently, in the same shape a
# complete answer would have. MEASURED on the reference machine: of 133 installed mods, 54
# (41%) ship a .cat AND loose XML, so the misleading case is the COMMON one, not an edge.
#
# The cost is documented: two sessions of a real investigation were spent on a `No matches`
# that meant "that mod is packed", not "the string is absent". The routing table in CLAUDE.md
# already said so and did not prevent it, which is the argument for a hook rather than a
# sentence -- a rule you have to remember is not a control.
#
# DELIBERATELY NARROW, because a guard that fires on ordinary work gets bypassed:
#   * a DIRECTORY target only. Searching one named file is not a survey and cannot mislead.
#   * that directory must be the extensions root, or sit inside a mod that actually ships a
#     .cat. A loose-only mod is fully visible to Grep and must not prompt.
# MEASURED against 21 transcripts / 311 Grep calls: this fires on 4 of them.
JQ="${JQ:-jq}"
# Parameter expansion, not `$(cd "$(dirname "$0")" && pwd)`: that was a subshell AND
# a dirname process on every call (AUDIT-2026-09-24 HK-4). The settings command passes
# an absolute path, and a relative one still resolves: nothing here changes directory.
# BOTH separators: a hook started as `bash C:\...\protect-bash.sh` has a $0 with no
# forward slash at all, and reading it as "." sourced _x4-env.sh from the CALLER's
# directory -- MEASURED: the guard then found no python and asked on every command.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"

# ADVISE, never ask. A partial-answer warning is about MY instrument choice, so it
# must cost me a note and the user nothing. Renamed from ask() 2026-08-29, when
# fixing the stdin defect turned five newly-live hooks into a prompt storm.
advise() { x4_advise "$1"; exit 0; }   # shared emitter: survives a missing jq

INPUT=$(x4_hook_input)
# ADVISE, never ask -- this file's own header says so, and it escalated to a PROMPT on
# every Grep/Glob if the stdin defect ever recurred. A partial-answer warning is about
# MY instrument choice: it must cost me a note and the user nothing.
# x4validate-on-edit.sh makes the same (correct) call for the same situation.
if [ -z "$INPUT" ]; then
  x4_advise "X4 SEARCH-SCOPE INERT: this hook received NO INPUT, so it checked nothing. Your search may be reading a partial picture (packed mods are invisible to a text search). Nothing is blocked."
  exit 0
fi
# COULD WE READ THIS PAYLOAD AT ALL? `x4_field` returns empty for "field absent" AND
# for "could not parse", and its own docstring concedes it -- but the INERT branch
# three lines above is proof this hook has something to say about the second. Probing
# a field that is always present separates them. MEASURED 2026-09-05: a truncated
# payload produced 0 bytes and silence, where an EMPTY one produced 288 bytes of INERT.
# Same condition, opposite reporting.
TOOL="$(x4_field "$INPUT" 'tool_name')"
if [ -z "$TOOL" ]; then
  x4_advise "X4 SEARCH-SCOPE INERT: this payload could not be parsed, so the hook checked NOTHING. Your search may be reading a partial picture (packed mods are invisible to a text search). Nothing is blocked."
  exit 0
fi
FP=$(x4_field "$INPUT" 'tool_input.path')   # shared reader: survives a missing jq

# A literal backslash cannot be written safely through every layer that touches this
# file, so it is built from its byte value. MEASURED 2026-08-29: writing it as an
# escaped pair collapsed to a single backslash, turning this substitution into
# "delete every forward slash" -- the path became C:Program Files... and the hook
# silently never fired. Every must-fire probe went red and every must-NOT-fire probe
# went green, which is what an inert guard looks like.
BS=$(printf '\134')

# A GIT-IGNORED ROOT IS INVISIBLE, not partial (2026-10-02). Grep and Glob honour .gitignore,
# and the game root's .gitignore is a whitelist (`*`). MEASURED: Grep rooted anywhere in
# extensions/ found 0 of 133 manifests (rg --no-ignore: 133), and the packed-archive advisory
# below then supplied a WRONG explanation for the zero. Grep there cannot succeed, so it is a
# deny with the way out. Glob is only an advisory: it skipped ignored files for a `*/x`
# pattern but found them for `**/x` (MEASURED, same root), so it can succeed. A root outside
# any git work tree makes check-ignore exit 128, which is "not ignored". ~20 ms, one process.
# `-d` skips that process for a single FILE (Grep reads a named file even when it is ignored).
# It is a cost guard, not the correctness one: `git -C <file>` fails too (MEASURED by a twin).
ROOT="$FP"; [ -z "$ROOT" ] && ROOT="$(x4_field "$INPUT" 'cwd')"
R="${ROOT//"$BS"//}"
# ...BUT AN IGNORED ROOT IS NOT AN INVISIBLE TREE (v4.0 release review R1-F4). Grep (ripgrep)
# reads every file under a root it was GIVEN, unless a pattern matches the children themselves.
# MEASURED 2026-10-04: rg --files under a `reference/`-ignored folder -> 1 of 1 file; under the
# game root's `*` -> 0 of 1. `check-ignore` alone cannot tell them apart -- it calls every child
# of an ignored directory ignored (MEASURED: `build/x` -> `build/`) -- and rg is not on PATH in
# a hook (Claude Code ships it as a shell function). So the PATTERN that ignores the root
# decides (`-v`): a directory-only pattern (`build/`) leaves the children visible; one whose
# last component matches ANY name (`*`) hides them. Anything else -- a pattern naming the root
# itself (`ext*`) -- cannot hide children, so it does not fire. COST: the plain `-q` test runs
# first, in no subshell, so a root that is NOT ignored -- nearly every search -- still pays one
# process; only an ignored root pays the second (`-v`, captured).
_ign=""
if [ -n "$R" ] && [ -d "$R" ] && git -C "$R" check-ignore -q -- "$R" 2>/dev/null; then
  _ign="$(git -C "$R" check-ignore -v -- "$R" 2>/dev/null)" || _ign=""
fi
if [ -n "$_ign" ]; then
  _pat="${_ign%%$'\t'*}"
  [[ "$_pat" =~ :[0-9]+:(.*)$ ]] && _pat="${BASH_REMATCH[1]}" || _pat=""
  case "$_pat" in
    ''|*/) _ign="" ;;                              # unreadable, or directory-only: children visible
    *)
      _pat="${_pat##*/}"                           # its last component, the part a CHILD is matched by
      # shellcheck disable=SC2254 -- the pattern IS a glob, deliberately unquoted
      case "x4-any-child-name" in $_pat) ;; *) _ign="" ;; esac ;;
  esac
fi
if [ -n "$_ign" ]; then
  if [ "$TOOL" = "Grep" ]; then
    _why="BLOCKED: $ROOT is git-ignored by a pattern that matches every name under it, and Grep honours .gitignore -- it cannot see ONE file under this root and would answer 'No files found' whatever is there. Search with Bash instead: rg --no-ignore <pattern> '<root>' (add -uu to include hidden files), or give Grep a single FILE as path."
    # X4_GUARD=off relaxes this deny too, by the shared rule (R1-F5): its banner says EVERY
    # verdict is an advisory, and this one was not.
    x4_guard_relax search-scope.sh deny "$_why"
    [ "$_x4_rk" = advise ] && advise "$_x4_rr"
    if "${JQ:-jq}" -n --arg r "$_why" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}' 2>/dev/null; then exit 0; fi
    x4_resolve_python
    if [ -n "$X4_PY" ]; then
      X4_REASON="$_why" "$X4_PY" -c 'import json, os, sys
sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": os.environ["X4_REASON"]}}))'
      exit 0
    fi
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: this Grep root is git-ignored, so Grep cannot see any file under it. Use Bash: rg --no-ignore."}}'
    exit 0
  fi
  advise "UNRELIABLE ZERO: $ROOT is git-ignored, and Glob honours .gitignore. MEASURED: here a '*/name' pattern returned 'No files found' while '**/name' found the files. Never read a zero from this search as absence; confirm with ls, or Bash: rg --no-ignore --files '<root>'."
fi
[ -z "${X4_EXTENSIONS:-}" ] && exit 0

# Above extensions/: when extensions/ itself is git-ignored, Grep sees NONE of it -- loose
# mods included -- so the advisory leads with that cause. Probed only on these two branches.
ign_note() {
  local _e="${X4_EXTENSIONS//"$BS"//}"
  [ "$TOOL" = Grep ] && [ -d "$_e" ] && git -C "$_e" check-ignore -q -- "$_e" 2>/dev/null &&
    printf '%s' "Grep CANNOT SEE extensions/ AT ALL here: it is git-ignored and Grep honours .gitignore, so every mod file is invisible, loose or packed -- search mod files with Bash: rg --no-ignore. "
  return 0
}

# NO PATH AT ALL is the BROADEST search, not the narrowest. Grep/Glob without `path`
# search the cwd -- and the cwd for this project IS the game root, a strict superset
# of extensions/. This exited silently while a narrower search rooted INSIDE
# extensions/ advised, so coverage was inverted with respect to breadth. MEASURED over
# 26 transcripts / 397 Grep+Glob calls: 6 no-path and 4 above-extensions, all
# unguarded, against 15 that fired.
if [ -z "$FP" ]; then
  advise "$(ign_note)PARTIAL ANSWER: this search names no path, so it runs from the working directory -- which here contains the whole extensions/ folder. A text search reads LOOSE files only, so mods shipping .cat archives are invisible and a 'no matches' means 'not found in the loose subset', NOT 'absent'. Use _scan.iter_corpus_xml (packed-inclusive) for a corpus sweep, or confirm you want the loose-only view."
fi

# Pure-string prefilter first: the overwhelming majority of searches are nowhere near the
# game folder, and they must not pay for a filesystem probe.
nFP="$(x4_norm "$FP")"; nFP="${nFP%/}"
nEXT="$(x4_norm "$X4_EXTENSIONS")"; nEXT="${nEXT%/}"
# A search root that CONTAINS extensions/ (the game root, or any ancestor) covers a
# strict superset of it -- including every packed mod -- and was exiting silently
# while the subset advised.
case "$nEXT" in
  "$nFP"/*)
    advise "$(ign_note)PARTIAL ANSWER: this search is rooted ABOVE the extensions/ folder, so it covers every installed mod -- and a text search reads LOOSE files only. Mods shipping .cat archives are invisible to it, so a 'no matches' here means 'not found in the loose subset', NOT 'absent'. Use _scan.iter_corpus_xml (packed-inclusive) for a corpus sweep, or confirm you want the loose-only view."
    ;;
esac
case "$nFP" in
  "$nEXT"|"$nEXT"/*) ;;
  *) exit 0 ;;
esac

F="${FP//"$BS"//}"
[ -d "$F" ] || exit 0            # a single file is not a survey

if [ "$nFP" = "$nEXT" ]; then
  advise "PARTIAL ANSWER: a search rooted at the whole extensions/ folder reads LOOSE files only. Mods that ship .cat archives are invisible to it, so a 'no matches' here means 'not found in the loose subset', NOT 'absent'. Use _scan.iter_corpus_xml (packed-inclusive) for a corpus sweep, or confirm you want the loose-only view."
fi

# Which mod folder is this inside? First path component under extensions/.
REL="${nFP#"$nEXT"/}"
MOD="${REL%%/*}"
[ -z "$MOD" ] && exit 0

# Resolve the mod folder against the REAL (un-normalised) extensions root so the .cat probe
# looks at the actual directory, not a lowercased string.
#
# ONLY THE ROOT WAS UN-NORMALISED. `MOD` is carved out of `nFP`, which x4_norm has
# lowercased, so the probe path was `<real root>/packedmod` for a folder named
# `PackedMod` -- and this comment claimed the opposite. Windows is case-insensitive so
# it is inert here; on a case-sensitive filesystem (X4 ships a Linux build, and this is
# a public toolkit) the guard is silently inert for exactly those mods, which is the
# worst direction: a packed mod reads as loose-only and its "no matches" reads as
# "absent". MEASURED on the reference machine: 133 mod folders, 54 ship a .cat, 10 have
# an uppercase letter, 3 of the 54 (5.6%) are both.
#
# The direct path is tried FIRST so the common case costs nothing extra; the scan only
# runs when it misses, which on Windows is never. `nocasematch` is used rather than
# `nocaseglob` because it compares STRINGS -- so its behaviour is provable on this
# machine and portable, where a glob test here would only ever exercise Windows'
# case-insensitive filesystem and could not go red. The right-hand side is quoted, so a
# folder name containing a glob metacharacter is compared literally.
EXTDIR="${X4_EXTENSIONS//"$BS"//}"
MODDIR="$EXTDIR/$MOD"
if [ ! -d "$MODDIR" ]; then
  MODDIR=""
  _ncm="$(shopt -p nocasematch)"          # restore whatever the caller had
  shopt -s nocasematch
  for d in "$EXTDIR"/*/; do
    n="${d%/}"; n="${n##*/}"
    if [[ "$n" == "$MOD" ]]; then MODDIR="$EXTDIR/$n"; break; fi
  done
  eval "$_ncm"
fi
[ -n "$MODDIR" ] || exit 0
for c in "$MODDIR"/*.cat; do
  if [ -e "$c" ]; then
    advise "PARTIAL ANSWER: '$MOD' ships packed .cat archives as well as loose files, and this search reads LOOSE files only. A zero result here is 'not in the loose subset', NOT 'absent'. Use _scan.iter_mod_xml_bytes / _scan.iter_corpus_xml to include packed content, or confirm you want the loose-only view."
  fi
done
exit 0

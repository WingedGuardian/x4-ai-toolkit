#!/usr/bin/env bash
# Unpack YOUR OWN X4 base game + DLCs into a local reference/ tree for x4validate and
# "vanilla as frame of reference". Text only (.xml/.xsd/.lua/.xpl) — skips meshes/textures/
# audio, so the tree stays ~0.6 GB instead of ~29 GB. Cross-platform (uses bin/xrcat).
#
# reference/ is read-only base-game data: never edit it, never redistribute it.
# Configure paths via x4-paths.env in the toolkit root (X4_GAME, X4_REFERENCE) or env vars.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# WHICH TOOLKIT (B2, install red-team 2026-10-04). This script acts for the toolkit it LIVES
# IN (or --toolkit DIR). An inherited $X4_TOOLKIT naming another toolkit made a second copy's
# x4refguard target the first copy's REAL reference tree; here it would also unpack into the
# other toolkit's X4_REFERENCE. So a foreign $X4_TOOLKIT gets one line naming both roots and
# a REFUSAL unless --toolkit names the toolkit explicitly (both installers pass it: FX-B2,
# where the delta review found that neither did).
ACT_TK="$(cd "$HERE/.." && pwd)"
EXPLICIT_TK=0
#: FX-B2: what the CALLER's environment exported, captured before the loader below exports the
#: config's values -- an inherited X4_REFERENCE outranks the config, and is checked against it.
_X4_INHERITED_REF="${X4_REFERENCE:-}"
EXPLICIT_REF=""
while [ $# -gt 0 ]; do
  case "$1" in
    --toolkit)
      [ $# -ge 2 ] && ACT_TK="$(cd "$2" 2>/dev/null && pwd)" \
        || { echo "ERROR: --toolkit needs an existing directory (got '${2:-}')" >&2; exit 2; }
      EXPLICIT_TK=1; shift 2 ;;
    --reference)
      [ $# -ge 2 ] && [ -n "$2" ] \
        || { echo "ERROR: --reference needs a directory" >&2; exit 2; }
      EXPLICIT_REF="$2"; shift 2 ;;
    -h|--help)
      echo "usage: bash bin/unpack-reference.sh [--toolkit DIR] [--reference DIR]"
      echo "  Unpacks the base game + DLC text into X4_REFERENCE, then protects it (x4refguard)."
      echo "  --toolkit DIR    act for this toolkit's x4-paths.env (default: the toolkit this"
      echo "                   script lives in; REQUIRED when \$X4_TOOLKIT names a different one)"
      echo "  --reference DIR  unpack into DIR, chosen explicitly (REQUIRED when an exported"
      echo "                   X4_REFERENCE names a different tree from the toolkit's config)"
      exit 0 ;;
    *) echo "ERROR: unknown argument '$1' (see --help)" >&2; exit 2 ;;
  esac
done
_x4_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
if [ -n "${X4_TOOLKIT:-}" ]; then
  _x4_env_tk="$(cd "$X4_TOOLKIT" 2>/dev/null && pwd || printf '%s' "$X4_TOOLKIT")"
  if [ "$(_x4_lc "$_x4_env_tk")" != "$(_x4_lc "$ACT_TK")" ]; then
    echo "x4 toolkit: acting for $ACT_TK; \$X4_TOOLKIT names a different toolkit, $X4_TOOLKIT, which is NOT used here." >&2
    if [ "$EXPLICIT_TK" != 1 ]; then
      echo "REFUSED: the unpack writes the reference tree and protects it, and \$X4_TOOLKIT names $X4_TOOLKIT while this script lives in $ACT_TK. Nothing was changed. To act for THIS toolkit: bash \"$HERE/unpack-reference.sh\" --toolkit \"$ACT_TK\"" >&2
      exit 2
    fi
  fi
fi
export X4_TOOLKIT="$ACT_TK"

# X4_GAME, X4_REFERENCE, X4_TOOLKIT -- from whichever guard copy is installed. An
# `install --agent codex` has .codex/hooks/ and no .claude/hooks/ (a byte-identical copy
# of the same file), so naming one location killed --unpack in every Codex-only install --
# and an OpenCode-only install has only .opencode/hooks/ (Plan 3 lane I).
if [ -f "$HERE/../.claude/hooks/_x4-env.sh" ]; then
  . "$HERE/../.claude/hooks/_x4-env.sh"
elif [ -f "$HERE/../.codex/hooks/_x4-env.sh" ]; then
  . "$HERE/../.codex/hooks/_x4-env.sh"
elif [ -f "$HERE/../.opencode/hooks/_x4-env.sh" ]; then
  . "$HERE/../.opencode/hooks/_x4-env.sh"
elif [ -f "$HERE/../tools/x4_harness/_x4-env.sh" ]; then
  . "$HERE/../tools/x4_harness/_x4-env.sh"
else
  echo "ERROR: no _x4-env.sh in .claude/hooks/, .codex/hooks/, .opencode/hooks/ or tools/x4_harness/ beside $HERE -- re-run the installer." >&2
  exit 2
fi

# AN INHERITED X4_REFERENCE (FX-B2, delta review of F160). The loader lets the environment win,
# so a shell that had exported another toolkit's X4_REFERENCE unpacked into -- and protected --
# THAT tree, even with --toolkit naming this one. Compare it with what the acting toolkit's
# config alone says (the same loader, X4_REFERENCE unset), and refuse a difference unless
# --reference chose the tree explicitly. Only when a config file is read: with none, the
# environment is the only configuration there is.
_x4_canon() {    # a path in one spelling: its nearest EXISTING ancestor via cd+pwd, plus the rest
  local p="$1" rest="" base win=0
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) win=1; p="${p//\\//}" ;; esac
  # FX-B4 (reviewer I): RELATIVE to the working directory, as Python resolves it. A bare name
  # (no `/`) walked up to nothing and came out as `/<name>`, so `cd <toolkit>;
  # X4_CONFIG=x4-paths.env bash bin/unpack-reference.sh` was refused as a foreign config.
  case "$p" in
    /*) ;;
    [A-Za-z]:/*) [ "$win" = 1 ] || p="$PWD/$p" ;;
    *) p="$PWD/$p" ;;
  esac
  while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p="${p%/}"; done
  while [ -n "$p" ] && [ ! -d "$p" ]; do
    base="${p##*/}"; rest="/$base$rest"
    case "$p" in */*) p="${p%/*}" ;; *) p="" ;; esac
  done
  if [ -n "$p" ]; then p="$(cd "$p/" 2>/dev/null && pwd -P)"; fi
  # ONE spelling on Windows: Git Bash's `pwd -P` answers /tmp/... for a folder under %TEMP%
  # reached as C:/..., and /c/... for the same folder reached as /c/... (MEASURED, FX-B4).
  if [ "$win" = 1 ] && [ -n "$p" ] && command -v cygpath >/dev/null 2>&1; then
    p="$(cygpath -m "$p" 2>/dev/null || printf '%s' "$p")"
  fi
  p="${p%/}$rest"
  if [ "$win" = 1 ]; then p="$(_x4_lc "$p")"; fi
  printf '%s' "$p"
}
_x4_samedir() { [ "$(_x4_canon "$1")" = "$(_x4_canon "$2")" ]; }   # case-folded on Windows only

# AN INHERITED X4_CONFIG (FX-B3, delta review reviewer F). The check below compares against
# "this toolkit's config" -- but the loader took THAT file from $X4_CONFIG, which the shell
# inherits like any other variable: naming another toolkit's config made the unpack write (and
# protect) THAT toolkit's tree, and naming a missing file read no config at all, so nothing
# was compared. Refuse.
#
# NO FLAG LIFTS IT (FX-B4, reviewer I; as x4lock's config_lifted_by_flags=False). --reference
# did, but the foreign config still supplied X4_GAME and X4_APPMANIFEST: the chosen tree was
# filled from THE OTHER game and this toolkit's .claude/.reference-buildid recorded THAT
# game's build. The unpack also reads X4_XRCAT, X4_PYTHON, X4_REFGUARD_SCRIPT,
# X4_UNPACK_FLOOR and X4_FORCE_UNPACK from whatever config is loaded, with no flag for any.
if [ -n "${X4_CONFIG:-}" ]; then
  _x4_cfg_in=0
  # FX-B4: judge the file the loader READ (`_x4_cfg`, its normalized spelling of X4_CONFIG),
  # not the raw string -- `-f "x4-paths.env/"` said "does not exist" for a file Python reads.
  if [ -n "${_x4_cfg:-}" ]; then
    case "$(_x4_canon "$_x4_cfg")" in "$(_x4_canon "$ACT_TK")"/*) _x4_cfg_in=1 ;; esac
  fi
  if [ "$_x4_cfg_in" != 1 ]; then
    if [ -n "${_x4_cfg:-}" ]; then _x4_why="names a config OUTSIDE this toolkit"; else _x4_why="names a file that does not exist (so NO config is read)"; fi
    echo "REFUSED: the unpack writes the reference tree and protects it, and \$X4_CONFIG ($X4_CONFIG) $_x4_why. Nothing was changed." >&2
    echo "  acting toolkit: $ACT_TK" >&2
    echo "  No flag lifts this: the config would still supply the game, its build id and more." >&2
    echo "  Unset X4_CONFIG in this shell (or point it at this toolkit's x4-paths.env), then:" >&2
    echo "    bash \"$HERE/unpack-reference.sh\" --toolkit \"$ACT_TK\"" >&2
    exit 2
  fi
fi
if [ -n "$EXPLICIT_REF" ]; then
  X4_REFERENCE="$EXPLICIT_REF"; export X4_REFERENCE
elif [ -n "$_X4_INHERITED_REF" ] && [ -n "${_x4_cfg:-}" ]; then
  _X4_LOADER=""
  for _l in "$HERE/../.claude/hooks/_x4-env.sh" "$HERE/../.codex/hooks/_x4-env.sh" "$HERE/../.opencode/hooks/_x4-env.sh" "$HERE/../tools/x4_harness/_x4-env.sh"; do
    [ -f "$_l" ] && { _X4_LOADER="$_l"; break; }
  done
  _X4_CFG_REF="$(unset X4_REFERENCE; . "$_X4_LOADER" >/dev/null 2>&1; printf '%s' "${X4_REFERENCE:-}")"
  if [ -n "$_X4_CFG_REF" ] && ! _x4_samedir "$_X4_INHERITED_REF" "$_X4_CFG_REF"; then
    echo "REFUSED: the unpack writes the reference tree and protects it, and the environment and" >&2
    echo "  this toolkit's config ($_x4_cfg) name DIFFERENT trees. Nothing was changed." >&2
    echo "    \$X4_REFERENCE in the environment -> $_X4_INHERITED_REF" >&2
    echo "    this toolkit's config            -> $_X4_CFG_REF" >&2
    # FX-B3 (reviewer F M1): absolute, and with --toolkit -- the relative form ran only from
    # the toolkit root, and without --toolkit a foreign X4_TOOLKIT refused it again.
    echo "  Choose one explicitly: bash \"$HERE/unpack-reference.sh\" --toolkit \"$ACT_TK\" --reference \"$_X4_CFG_REF\"  (the config's)" >&2
    echo "                     or: bash \"$HERE/unpack-reference.sh\" --toolkit \"$ACT_TK\" --reference \"$_X4_INHERITED_REF\"  (the environment's)" >&2
    echo "  -- or unset X4_REFERENCE in this shell." >&2
    exit 2
  fi
fi

# X4_XRCAT is a TEST SEAM (like X4_UNPACK_FLOOR): it changes only which binary extracts,
# so the unpack -> sentinel -> Layer 2 flow can be tested end to end without a game.
# X4_REFGUARD_SCRIPT likewise changes only which guard script runs.
XRCAT="${X4_XRCAT:-$HERE/xrcat}"
REFGUARD="${X4_REFGUARD_SCRIPT:-$HERE/../scripts/x4refguard.py}"
TKDIR="$(cd "$HERE/.." && pwd)"
x4_resolve_python                            # sets X4_PY (empty if none)

# The Layer-2 state of an existing reference tree, as one word, or nothing if it could
# not be read. Never fatal: a missing python or an unreadable ACL is reported where it
# matters (after the unpack), not turned into a refusal here.
x4_refguard_state() {
  [ -n "$X4_PY" ] || return 0
  X4_REFERENCE="$REF" "$X4_PY" "$REFGUARD" status --json --toolkit "$ACT_TK" --reference "$REF" 2>/dev/null \
    | grep -oE '"state": "[a-z]+"' | head -1 | sed -E 's/.*"([a-z]+)"$/\1/' || true
}
# FX-B4 (reviewer I): every hint below is a command that runs from ANY directory, for THIS
# toolkit -- an absolute script path plus --toolkit (and the tree, --reference), as the B3
# refusals above already were. `python scripts/x4refguard.py ... (from DIR)` ran only from the
# toolkit root, and with no --toolkit a foreign $X4_TOOLKIT refused it again.
x4_guard_cmd() {   # SUBCOMMAND [ARGS...] -> the x4refguard command line, on stdout
  local py="${X4_PY:-python}"
  case "$py" in *" "*) py="\"$py\"" ;; esac
  printf '%s "%s" %s --toolkit "%s" --reference "%s"' "$py" "$TKDIR/scripts/x4refguard.py" "$*" "$ACT_TK" "$REF_ABS"
}
x4_lift_steps() {
  echo "  To re-unpack (e.g. after a game update), as the USER -- never an agent on its own:" >&2
  echo "    1. $(x4_guard_cmd remove)      (lifts the OS protection)" >&2
  echo "    2. rm \"$REF_ABS/.unpacked-and-locked\"" >&2
  echo "    3. bash \"$HERE/unpack-reference.sh\" --toolkit \"$ACT_TK\" --reference \"$REF_ABS\"" >&2
}
INCLUDE='\.(xml|xsd|lua|xpl)$'               # text/markup only — keeps the tree small

# Exit 2 == "this toolkit is not configured", everywhere in the toolkit. Kept
# distinct from 1 ("it ran and something was wrong") so a caller can tell "set
# X4_GAME" from "the unpack failed" — they need opposite responses.
[ -n "${X4_GAME:-}" ]  || { echo "ERROR: X4_GAME not set (path to 'X4 Foundations' with 01.cat..09.cat). Set it in x4-paths.env (toolkit root)." >&2; exit 2; }
[ -d "$X4_GAME" ]      || { echo "ERROR: game dir not found: $X4_GAME" >&2; exit 2; }
[ -n "${X4_REFERENCE:-}" ] || { echo "ERROR: X4_REFERENCE not set (where to write the unpacked tree). Set it in x4-paths.env (toolkit root)." >&2; exit 2; }
REF="$X4_REFERENCE"
case "$REF" in /*|[A-Za-z]:[/\\]*) REF_ABS="$REF" ;; *) REF_ABS="$PWD/$REF" ;; esac   # for the hints

# THE LOCK, CHECKED. The sentinel was only ever WRITTEN here (line ~60) and read
# nowhere in this script -- so its own text, "reference/ is read-only; remove this
# file manually to re-unpack", was false for the one path that actually unpacks.
#
# The lock did exist, but only in protect-bash.sh, which guards commands CLAUDE runs
# through its Bash tool. It cannot see an XRCatTool invocation made by this script in
# a child process, and it does not run at all for a user typing
# `bash bin/unpack-reference.sh` or `bash install.sh --unpack`. So the protection
# covered the assistant's path and not the user's.
#
# MEASURED 2026-09-02: an `install.sh --unpack` re-unpacked 510,711 files / 27 GB over
# an already-locked reference tree without a word. The content was identical (same
# .cat files) so nothing was corrupted, but every mtime in the tree changed -- and a
# real re-unpack after a game update would silently overwrite a tree someone had
# deliberately pinned.
if [ -f "$REF/.unpacked-and-locked" ] && [ "${X4_FORCE_UNPACK:-0}" != "1" ]; then
  echo "REFUSING: $REF is locked by .unpacked-and-locked, so it has already been" >&2
  echo "  unpacked and is treated as read-only base-game data." >&2
  x4_lift_steps
  echo "  (X4_FORCE_UNPACK=1 skips step 2 for one run; it never skips step 1.)" >&2
  exit 2
fi

# LAYER 2 IS NEVER LIFTED HERE. A forced re-unpack into a tree that still carries the OS
# protection would fail file by file, part-way through; and lifting it is the user's
# explicit act (D8), not a side effect of a flag. So refuse up front and name the lift.
if [ -d "$REF" ]; then
  L2STATE="$(x4_refguard_state)"
  case "$L2STATE" in
    protected|partial|foreign)
      echo "REFUSING: $REF carries the Layer-2 OS protection (x4refguard state: $L2STATE)," >&2
      echo "  so nothing can be written into it, and this script never lifts it itself." >&2
      x4_lift_steps
      exit 2 ;;
    # v4.0.0 review R4-10: `error` means the protection state could NOT be read -- not that
    # there is none. Proceeding wrote into a tree that may be protected, failing part-way.
    error)
      echo "REFUSING: the Layer-2 OS protection state of $REF could not be read (x4refguard" >&2
      echo "  state: error), so this script cannot tell whether it may write there. See:" >&2
      echo "    $(x4_guard_cmd status)" >&2
      exit 2 ;;
    # The states that mean "nothing is in the way": proceed.
    absent|unconfigured|unsupported) ;;
    # FX-B2 (delta review): an EMPTY or unknown answer (no python, a crash, output that does
    # not parse) proceeded as though the tree were unprotected. It is a non-answer, not "absent".
    *)
      if [ -z "$X4_PY" ]; then
        echo "REFUSING: no python (>= 3.10) was found to read the Layer-2 OS protection state of" >&2
        echo "  $REF, so this script cannot tell whether it may write there. Install python," >&2
        echo "  then re-run (it also applies the protection after the unpack)." >&2
      else
        echo "REFUSING: the Layer-2 OS protection state of $REF could not be read (x4refguard" >&2
        echo "  answered '${L2STATE:-nothing}', not a known state), so this script cannot tell whether" >&2
        echo "  it may write there. See:" >&2
        echo "    $(x4_guard_cmd status)" >&2
      fi
      exit 2 ;;
  esac
fi

echo "Game:      $X4_GAME"
echo "Reference: $REF"
mkdir -p "$REF"

# --- base game (later cat overrides earlier; 09 wins) ---
echo "[1/2] base 01..09.cat"
base=()
for n in 01 02 03 04 05 06 07 08 09; do
  [ -f "$X4_GAME/$n.cat" ] && base+=( -in "$X4_GAME/$n.cat" )
done
[ ${#base[@]} -gt 0 ] || { echo "ERROR: no 01.cat..09.cat found in $X4_GAME" >&2; exit 1; }
"$XRCAT" "${base[@]}" -out "$REF" -include "$INCLUDE"

# --- DLCs: each into reference/extensions/ego_dlc_* (matches X4's index path prefixes) ---
echo "[2/2] DLCs"
for d in "$X4_GAME"/extensions/ego_dlc_*/; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"
  cats=()
  for c in "$d"ext_*.cat; do
    case "$c" in *_sig.cat) continue;; esac
    [ -f "$c" ] && cats+=( -in "$c" )
  done
  [ ${#cats[@]} -eq 0 ] && continue
  mkdir -p "$REF/extensions/$name"
  echo "  $name (${#cats[@]} cats)"
  "$XRCAT" "${cats[@]}" -out "$REF/extensions/$name" -include "$INCLUDE"
done

# --- record the build id reference/ was unpacked from (stale-reference SessionStart hook) ---
#
# TWO PLACES, AND THE ONE THAT MATTERS IS THE SENTINEL. The detached
# `.claude/.reference-buildid` has to be kept in step by hand, and MEASURED
# 2026-09-06 it had not been: two copies disagreed (23524486 vs 23660954) while the
# tree was current, so the SessionStart hook announced a stale reference/ at every
# session start and recommended a ~60 GB re-unpack.
#
# check-reference-version.sh was changed to read the SENTINEL first, because that file
# lives INSIDE the tree it describes and therefore cannot drift from it. The v3.1.0
# release reviewer then found the other half: this script wrote the sentinel with a
# bare `touch`, i.e. ZERO BYTES, so there was no build id in it to read and the hook
# fell straight back to the detached file it was written to stop trusting. It worked
# on the developer machine only because that sentinel had been hand-written.
# THE SENTINEL IS A CLAIM THAT THE UNPACK SUCCEEDED, SO COUNT BEFORE WRITING IT.
# It was written unconditionally and the file count was computed AFTERWARDS, for
# display only. So a failed or partial extraction still marked the tree "unpacked
# and locked" -- and the check at the top of this script then REFUSES every later
# attempt, so the tool that produced the broken tree is the one that will not let
# you repair it. Recovery needs a manual `rm` the user has no reason to suspect.
#
# The floor is MEASURED, not chosen: a real text-only unpack of base 01-09 plus 8
# DLC produced 510,711 files on this machine (2026-09-08). 1000 is ~0.2% of that,
# so it catches a catastrophic failure and cannot false-refuse a legitimately
# smaller install -- deliberately insensitive, because a wrong refusal here costs
# a 27 GB re-run.
UNPACK_FLOOR="${X4_UNPACK_FLOOR:-1000}"
NFILES=$(find "$REF" -type f 2>/dev/null | wc -l)
if [ "$NFILES" -lt "$UNPACK_FLOOR" ]; then
  echo "REFUSING to write the lock sentinel: only $NFILES file(s) are in $REF," >&2
  echo "  and a successful unpack of base+DLC yields orders of magnitude more." >&2
  echo "  Writing it would mark a BROKEN tree as complete, and this script then" >&2
  echo "  refuses every later attempt to fix it." >&2
  echo "  Check XRCatTool output above. Override with X4_UNPACK_FLOOR=<n> if you" >&2
  echo "  genuinely mean to lock a tree this small." >&2
  exit 2
fi

BUILDID=""
if [ -n "${X4_APPMANIFEST:-}" ] && [ -f "$X4_APPMANIFEST" ]; then
  # NOT `grep buildid | tail -1`: that read the LAST of several and returned the public
  # beta branch's build (23524486) instead of the installed one (23660954). See
  # x4_acf_buildid in _x4-env.sh.
  BUILDID=$(x4_acf_buildid "$X4_APPMANIFEST" || true)
fi
if [ -n "$BUILDID" ]; then
  mkdir -p "$X4_TOOLKIT/.claude"
  printf '%s
' "$BUILDID" > "$X4_TOOLKIT/.claude/.reference-buildid"
fi

# lock against accidental re-unpacks (remove this sentinel to re-unpack, e.g. after a
# game update). The TEXT is load-bearing: check-reference-version.sh parses the build
# id out of it, and the wording matches what a hand-written sentinel already carried.
#
# The tail names the LIFT first: once Layer 2 is on, `rm` of this file fails with
# Access denied, so "remove this file" alone would be an escape hatch that does not open.
# The `buildid N` prefix is unchanged -- the parser takes the FIRST buildid[^0-9]*[0-9]+.
SENTINEL_TAIL="reference/ is read-only and delete-protected; to re-unpack run python scripts/x4refguard.py remove, then remove this file."
if [ -n "$BUILDID" ]; then
  printf 'Re-unpacked from X4 (steam buildid %s) on %s. %s\n' \
    "$BUILDID" "$(date +%Y-%m-%d)" "$SENTINEL_TAIL" > "$REF/.unpacked-and-locked"
else
  # No manifest, so no build id to record. Say so IN the sentinel rather than leaving
  # an empty file that reads as "unpacked from nothing" -- the hook then falls back to
  # the detached marker and says which source it used.
  printf 'Re-unpacked on %s; the Steam build id could not be determined (no appmanifest). %s\n' \
    "$(date +%Y-%m-%d)" "$SENTINEL_TAIL" > "$REF/.unpacked-and-locked"
fi

echo "DONE: $NFILES files, $(du -sh "$REF" | cut -f1)"

# --- Layer 2: the OS-level protection (scripts/x4refguard.py) ---
#
# Applied only AFTER the sentinel, i.e. only over a tree this script just counted as a
# complete unpack. Its failure does not undo the unpack -- the files stay -- but it is
# never silent: "unpacked" and "protected" are different claims, and exit 1 says the
# second one is not true. Exit 3 is a platform with no mechanism: disclosed, not failed.
if [ -z "$X4_PY" ]; then
  echo "Layer 2: FAILED -- no python found to run x4refguard.py, so reference/ is NOT OS-protected." >&2
  echo "  The unpack itself is complete. Install python, then: $(x4_guard_cmd apply --yes)" >&2
  exit 1
fi
# --yes (B3): x4refguard asks before an apply, and refuses when nobody can answer. The user
# ran THIS script, whose stated job (header, README) includes protecting the tree it has just
# unpacked and counted above -- and its `--unpack` caller is non-interactive -- so the
# confirmation is given here, for exactly this tree (X4_REFERENCE is pinned to $REF).
set +e
X4_REFERENCE="$REF" "$X4_PY" "$REFGUARD" apply --yes --toolkit "$ACT_TK" --reference "$REF"
L2RC=$?
set -e
case "$L2RC" in
  0) echo "Layer 2: reference/ protected at the OS level (python scripts/x4refguard.py status)" ;;
  3) echo "Layer 2: not available on this OS (disclosed gap; see README)" ;;
  *) echo "Layer 2: FAILED (x4refguard.py apply exit $L2RC) -- reference/ is NOT confirmed protected." >&2
     echo "  The unpack itself is complete and stays on disk. Fix the cause above, then:" >&2
     echo "  $(x4_guard_cmd apply --yes)" >&2
     exit 1 ;;
esac

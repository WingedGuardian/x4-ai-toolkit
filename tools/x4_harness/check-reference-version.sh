#!/bin/bash
# SessionStart: warn if the game build differs from the build reference/ was unpacked from.
# Plain stdout becomes session context for SessionStart hooks. Cross-platform Steam detection.
# Parameter expansion, not `$(cd "$(dirname "$0")" && pwd)`: that was a subshell AND
# a dirname process on every call (AUDIT-2026-09-24 HK-4). The settings command passes
# an absolute path, and a relative one still resolves: nothing here changes directory.
# BOTH separators: a hook started as `bash C:\...\protect-bash.sh` has a $0 with no
# forward slash at all, and reading it as "." sourced _x4-env.sh from the CALLER's
# directory -- MEASURED: the guard then found no python and asked on every command.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"

# WHICH marker is authoritative, and it is not the obvious one.
#
# `reference/.unpacked-and-locked` is written BY the unpack, INTO the tree it
# describes, and names the build in its text. It therefore cannot drift from its
# subject: copy the tree, the marker comes with it; delete the marker, the tree is
# unlocked and gets re-unpacked. `$X4_TOOLKIT/.claude/.reference-buildid` is a
# DETACHED file that has to be updated by hand as a separate step, which is exactly
# the step that gets missed.
#
# MEASURED 2026-09-06: it had been missed. The two detached copies disagreed --
# <toolkit>/.claude said 23524486 and the game-root one said 23660954 -- while the
# sentinel and the live game both said 23660954. This hook read the stale one, so it
# announced a stale reference/ AT EVERY SESSION START while reference/ was current,
# and the "fix" it recommended is a ~60 GB re-unpack. A banner that is always wrong
# trains you to ignore the banner, which is the failure mode _freshness.py is written
# to prevent; here it was costing a real re-unpack recommendation every session.
#
# Confirmed the tree really had not moved: 1 of 510,711 files under reference/ has an
# mtime after the schema baseline, and it IS the sentinel -- XRCatTool preserves the
# catalogs' timestamps, so a re-run of bin/unpack-reference.sh rewrites the marker
# and changes nothing else. An mtime on that file is evidence about the SCRIPT.
#
# So: sentinel first, detached file only as a fallback for a tree that predates it.
SENTINEL="$X4_REFERENCE/.unpacked-and-locked"
STORE="$X4_TOOLKIT/.claude/.reference-buildid"
ACF="${X4_APPMANIFEST:-}"                      # may already be derived from X4_GAME in _x4-env.sh
if [ -z "$ACF" ] || [ ! -f "$ACF" ]; then
  for c in \
    "$HOME/.steam/steam/steamapps/appmanifest_392160.acf" \
    "$HOME/.local/share/Steam/steamapps/appmanifest_392160.acf" \
    "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/appmanifest_392160.acf" \
    "$HOME/Library/Application Support/Steam/steamapps/appmanifest_392160.acf" \
    "/c/Program Files (x86)/Steam/steamapps/appmanifest_392160.acf" \
    "/mnt/c/Program Files (x86)/Steam/steamapps/appmanifest_392160.acf"; do
    [ -f "$c" ] && { ACF="$c"; break; }
  done
fi
[ -f "$ACF" ] || exit 0

CUR=$(x4_acf_buildid "$ACF")          # the INSTALLED build, never a beta branch's (_x4-env.sh)
# A manifest this parser cannot read is a NON-ANSWER, not a match. The parser needs a line
# holding only `{` or `}` to track depth; a VDF written as `"AppState" {` returns nothing,
# and exiting 0 here printed NOTHING -- byte-identical to "the builds agree", in the hook
# whose whole job is to warn. Say which state it is. (Steam does not write that shape today:
# MEASURED, the live manifest parses.)
if [ -z "$CUR" ]; then
  [ -f "$SENTINEL" ] || [ -f "$STORE" ] || exit 0
  echo "[x4 stale-reference] CANNOT DETERMINE the installed build: $ACF is not in a shape x4_acf_buildid can read, so the reference/ freshness check did NOT run. Compare the buildid under AppState in that file with the one in reference/.unpacked-and-locked yourself before trusting line numbers."
  exit 0
fi
STORED=""
SRC=""
if [ -f "$SENTINEL" ]; then
  # "... (steam buildid 23660954) on 2026-06-22 ..." -- take the number after the word.
  STORED=$(grep -oE 'buildid[^0-9]*[0-9]+' "$SENTINEL" | head -1 | grep -oE '[0-9]+' | tail -1)
  [ -n "$STORED" ] && SRC="reference/.unpacked-and-locked"
fi
if [ -z "$STORED" ] && [ -f "$STORE" ]; then
  STORED=$(tr -d '[:space:]' < "$STORE")
  [ -n "$STORED" ] && SRC=".claude/.reference-buildid"
fi
# FX-Z (FX-B4 lead): every step is an ABSOLUTE command with --toolkit and --reference. The
# relative forms ran only from the toolkit folder, and the bare ones refuse under an exported
# X4_REFERENCE/X4_TOOLKIT that differs from the config -- in the very shell this hook judged.
if [ -n "$STORED" ] && [ "$STORED" != "$CUR" ]; then
  _tk="--toolkit \"$X4_TOOLKIT\" --reference \"$X4_REFERENCE\""
  echo "[x4 stale-reference] reference/ ($X4_REFERENCE) was unpacked from build $STORED (per $SRC) but the game is now build $CUR. Re-unpack before trusting line numbers in deep fixes -- the USER lifts the OS protection: python \"$X4_TOOLKIT/scripts/x4refguard.py\" remove $_tk ; removes \"$X4_REFERENCE/.unpacked-and-locked\" ; then runs: bash \"$X4_TOOLKIT/bin/unpack-reference.sh\" $_tk -- and afterward updates \"$X4_TOOLKIT/.claude/.reference-buildid\" so the detached copy stops disagreeing."
fi
exit 0

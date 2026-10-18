#!/bin/bash
# Protect X4 mod files from unintended edits. Cross-platform & config-driven.
# Locations come from x4-paths.env / env vars (see _x4-env.sh); when those are
# unset the legacy path-name patterns act as a backstop, so it still protects out of the box.
# - Hard blocks: reference/ (read-only base game), .cat/.dat, the game installation
# - Confirmation: user profile, live extensions/ (deploy target)
# - Advisory only: content.xml (manifests) -- the user turned the prompt off 2026-08-29
JQ="${JQ:-jq}"
# Parameter expansion, not `$(cd "$(dirname "$0")" && pwd)`: that was a subshell AND
# a dirname process on every call (AUDIT-2026-09-24 HK-4). The settings command passes
# an absolute path, and a relative one still resolves: nothing here changes directory.
# BOTH separators: a hook started as `bash C:\...\protect-bash.sh` has a $0 with no
# forward slash at all, and reading it as "." sourced _x4-env.sh from the CALLER's
# directory -- MEASURED: the guard then found no python and asked on every command.
case "$0" in */*|*\\*) HOOK_DIR="${0%[/\\]*}" ;; *) HOOK_DIR=. ;; esac
. "$HOOK_DIR/_x4-env.sh"

INPUT=$(x4_hook_input)
x4_require_input "$INPUT" "X4 GUARD INERT: this hook received NO INPUT, so it checked nothing. Allowing silently is how five hooks sat dead for weeks while their suites passed. Confirm only if you know why the payload is missing."

# Is jq actually usable? A BROKEN jq made every verdict below print nothing, and empty
# stdout from a hook means ALLOW -- so the guard failed open, silently, exactly like
# F79. protect-bash.sh gained this fallback in bccffc1; this file never did.
x4_resolve_python; PY="$X4_PY"   # shared: refuses a misconfigured X4_PYTHON

_harness_save_qualified=0
if [ -n "$PY" ] && [ "${1:-}" != --harness-policy-backend ]; then
  x4_save_dispatch files
  exit 0
fi
[ "${2:-}" = --save-qualified ] && _harness_save_qualified=1

emit() {   # emit <deny|ask|advise> <reason>
  # X4_GUARD=off (spec 5.7): a deny or an ask becomes an advisory that names what it would
  # have been, and is logged -- except an INABILITY (_X4_UNRELAXED), which still asks
  # (R1-F2). See x4_guard_relax in _x4-env.sh.
  x4_guard_relax protect-files.sh "$1" "$2"
  set -- "$_x4_rk" "$_x4_rr"
  if [ "$JQ_OK" = 1 ]; then
    if [ "$1" = "advise" ]; then
      "$JQ" -n --arg r "$2" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$r}}'
    else
      "$JQ" -n --arg k "$1" --arg r "$2" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$k,permissionDecisionReason:$r}}'
    fi
  elif [ -n "$PY" ]; then
    X4_KIND="$1" X4_REASON="$2" "$PY" -c 'import json, os, sys
k = os.environ["X4_KIND"]; r = os.environ["X4_REASON"]
h = {"hookEventName": "PreToolUse"}
if k == "advise":
    h["additionalContext"] = r
else:
    h["permissionDecision"] = k; h["permissionDecisionReason"] = r
sys.stdout.buffer.write(json.dumps({"hookSpecificOutput": h}).encode("utf-8"))'
  else
    # Neither emitter available. Hand-rolled JSON is the last resort, and the reason is
    # deliberately literal: a verdict that cannot be rendered must still be a verdict.
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"X4 GUARD: neither jq nor python is available, so this hook could not evaluate its rules. Confirm only if you know the edit is safe."}}'
  fi
}

# ONE jq process: the read itself is the health check (AUDIT-2026-09-24 HK-4). A
# separate `jq -e .` probe first was a second process on every Edit/Write. jq exits
# non-zero when it is missing, broken, OR handed an unreadable payload -- and in every
# one of those cases the python reader below runs and tells the last two apart, so an
# UNREADABLE payload still reaches the refusal (MEASURED 2026-09-05: it once did not).
# JQ_OK records whether jq worked, for emit().
JQ_OK=0
if FILE_PATH=$(printf '%s' "$INPUT" | "$JQ" -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null); then
  JQ_OK=1; FP_OK=1
elif [ -n "$PY" ]; then
  FILE_PATH=$(X4_IN="$INPUT" "$PY" -c 'import json, os, sys
try:
    sys.stdout.write((json.loads(os.environ["X4_IN"]).get("tool_input") or {}).get("file_path") or (json.loads(os.environ["X4_IN"]).get("tool_input") or {}).get("notebook_path") or "")
except Exception:
    sys.exit(9)')
  FP_OK=$?
  [ "$FP_OK" -eq 0 ] && FP_OK=1 || FP_OK=0
else
  FP_OK=0
fi

# An EMPTY path and an UNREADABLE payload are different facts, and conflating them is
# how a guard reports success over nothing. Only the first is an allow.
if [ "$FP_OK" != 1 ]; then
  VERDICT=1; _X4_UNRELAXED=1     # an inability: never relaxed below ask (R1-F2)
  emit ask "X4 GUARD: could not read the file path from this payload (no working jq or python), so NO rule below was evaluated. This is not a clean pass. Confirm only if you know the edit is safe."
  x4_guard_check_inert
  exit 0
fi
[ -z "$FILE_PATH" ] && exit 0
# The path AS WINDOWS OPENS IT, before any rule reads it (FX-G2 item 3): `x4-paths.env.`,
# `x4-paths.env::$DATA`, `<ref>./libraries/x`, `C:/PROGRA~2/.../X4FOUN~1/...` each ARE the
# protected file, and every NAME test below (the config name, `X4 Foundations/`, `.cat`) reads
# the path as written. MEASURED: 11 of 17 such writes were allowed. See x4_winpath.
x4_winpath "$FILE_PATH"; FILE_PATH="$_X4_WP"

deny() { VERDICT=1; emit deny "$1"; exit 0; }
# advise <reason> -- ALLOW, and explain to CLAUDE. Added 2026-08-29 when the user
# turned off the content.xml confirmation: the reminder is still worth having, the
# INTERRUPTION is not. A prompt spends the user's attention; an advisory spends mine.
#
# ACCUMULATES, and does NOT exit (2026-08-30, F84). An advisory is an ALLOW THAT
# CARRIES A NOTE, not a decision, so it must never make the rules below it
# unreachable. Here that had teeth: the manifest advisory sits ABOVE the profile
# confirmation, the deployed-extensions confirmation AND the game-install hard
# block, so editing a DEPLOYED mod manifest was advised and never confirmed.
VERDICT=""
ADVICE=""
# Flush on EVERY exit path. A tail-only flush is silently skipped by the
# whitelist `exit 0`s in the middle of this file -- MEASURED 2026-08-30: it turned
# the manifest advisory into a plain allow. Nothing is emitted if a terminal
# verdict already spoke, or if no advisory accumulated.
flush_advice() {
  [ -n "$VERDICT" ] && return 0
  [ -n "$ADVICE" ] || return 0
  emit advise "$ADVICE"
}
trap flush_advice EXIT
advise() { if [ -n "$ADVICE" ]; then ADVICE="$ADVICE
$1"; else ADVICE="$1"; fi; }
ask()  { VERDICT=1; emit ask "$1"; exit 0; }

# === HARD BLOCK — read-only reference data (unpacked base game, never edit) ===
# X4_REFERENCE defaults to $X4_TOOLKIT/reference, so this covers the default layout too.
x4_under "$FILE_PATH" "$X4_REFERENCE" && deny "BLOCKED: reference/ is read-only unpacked base game data — never edit. Make a diff patch in your mod instead."

# === HARD BLOCK — the toolkit's PATH CONFIG (v4.0 release review R1-F1 / R1-P1, ruling 3) ===
# Every guard reads its roots from x4-paths.env. An agent that could write it could point
# reference/ somewhere else or drop the game root, and the guards would protect a different
# tree, silently. COST: a pure-shell NAME test first (case-insensitive), so every other path
# pays no subprocess; only a file NAMED like a config pays the x4_under checks.
_x4_fn="${FILE_PATH##*[/\\]}"
# `shopt -q`, not `$(shopt -p ...)`: a builtin test, so the restore costs no subshell (HK-4).
_x4_nm=0; shopt -q nocasematch && _x4_nm=1
shopt -s nocasematch
_x4_cfgname=0
[[ "$_x4_fn" == "x4-paths.env" || ( -n "${X4_CONFIG:-}" && "$_x4_fn" == "${X4_CONFIG##*[/\\]}" ) ]] && _x4_cfgname=1
[ "$_x4_nm" = 1 ] || shopt -u nocasematch
if [ "$_x4_cfgname" = 1 ]; then
  x4_cfg_candidates
  while IFS= read -r _cf; do
    [ -n "$_cf" ] && x4_under "$FILE_PATH" "$_cf" && { x4_cfg_why "$FILE_PATH"; deny "$_x4_cfg_why"; }
  done <<EOF
$_x4_cfg_paths
EOF
fi

# === HARD BLOCK — X4_GUARD in a Claude Code SETTINGS file's env (user decision 2026-10-05) ===
# A settings `env` block reaches every hook process (MEASURED, Claude Code 2.1.290), so an agent
# writing `"env": {"X4_GUARD": "off"}` there turns every guard off at the next launch. The
# RESULTING content is judged by settings_guard.py (the one implementation, shared with the
# adapters and session-canary.sh); every other settings edit stays allowed. COST: a pure-shell
# name test; only a file named settings*.json pays the python call.
case "$FILE_PATH" in
  *[Ss][Ee][Tt][Tt][Ii][Nn][Gg][Ss]*.[Jj][Ss][Oo][Nn])
    if [ -n "$PY" ]; then
      _x4_sg="$(printf '%s' "$INPUT" | "$PY" "$HOOK_DIR/settings_guard.py" --path "$FILE_PATH" 2>/dev/null)"
      _x4_sgrc=$?
      [ "$_x4_sgrc" = 0 ] || _x4_sg="BLOCKED: the settings-file check could not run (exit $_x4_sgrc), so this write to $FILE_PATH was not judged. Retry; if it persists, the user should edit the file."
      [ -n "$_x4_sg" ] && deny "$_x4_sg"
    else
      case "$(printf '%s' "$FILE_PATH" | tr 'A-Z\\' 'a-z/')" in
        */.claude/settings*.json) deny "BLOCKED: no Python was found, so the guard cannot check what this write leaves in $FILE_PATH, a Claude Code settings file whose env reaches every hook. Set X4_PYTHON (or put python on PATH) and retry." ;;
      esac
    fi ;;
esac

# === HARD BLOCK — CAT/DAT archive files (use bin/xrcat / XRCatTool only) ===
# Scoped 2026-08-29: only where X4 keeps archives. `.dat` is a generic extension --
# this denied editing another game's save in Documents, which is not ours to block.
if echo "$FILE_PATH" | grep -qiE '\.(cat|dat)$'; then
  for _r in "${X4_GAME:-}" "${X4_EXTENSIONS:-}" "${X4_MODS:-}" "${X4_TOOLKIT:-}"; do
    x4_under "$FILE_PATH" "$_r" && deny "BLOCKED: cannot write .cat/.dat archives directly. Use bin/xrcat (XRCatTool) to pack/unpack."
  done
fi

# === CONFIRMATION — content.xml (mod manifests; changing breaks mod loading) ===
# Deliberately ABOVE the workspace whitelist: a manifest edit always confirms, even inside
# dev/ or X4_MODS. (Before this it was below the whitelist, so mod manifests in the working
# dirs were silently allowed — contradicting the documented safety rule.)
# The profile's own manifest is the mod enable/disable list and the Steam Workshop
# toggle, and the profile CONFIRMATION below owns it. That used to need a guard here,
# because this advisory exited and would have bypassed the prompt the user kept; since
# advisories accumulate (F84) the confirmation below is reached on its own and wins.
if true; then
  echo "$FILE_PATH" | grep -qiE '(^|[/\\])content\.xml$' && advise "MOD MANIFEST: $FILE_PATH controls what this mod loads, its id, version and dependencies. A wrong id makes every dependent mod report MISSING, and `save=\"1\"` bakes the mod into save files. Allowed without confirmation (user decision 2026-08-29) -- so check the change yourself rather than expecting a prompt."
fi

# === ADVISORY — editing the DEPLOYED .claude/ instead of its source (2026-09-13) ===
# When the toolkit lives OUTSIDE the game root, the game root's .claude/ is a deployed
# copy of $X4_TOOLKIT/.claude/. MEASURED 2026-09-13: five files there had drifted from the
# source -- two skills written straight into the deployment and never shipped, one skill
# newer there, two agents with personal paths hand-edited in. Every one was an Edit that
# landed in the wrong copy. ABOVE the whitelist on purpose: the `.claude/(hooks|skills|..)`
# whitelist below exits 0, and an advisory placed under it would be unreachable (F84).
# ADVISORY, never a deny: a deliberate local edit is legitimate; promote only once a
# false-positive rate has been measured. In the in-game layout the game root IS the
# toolkit, so its .claude/ is the source and this stays silent.
# COST GATE, added 2026-09-20 after the release review MEASURED this block at +182 ms on
# EVERY Edit/Write, for every path: it canonicalised both roots (a memo miss each time)
# and called x4_under up to five times -- seven subprocesses per edit, on a hook whose own
# header records an 11.3x latency regression as the class it was rewritten to remove. The
# case below is pure shell: no subprocess, and it skips every path that is not inside some
# .claude/ at all, which is almost all of them.
case "$FILE_PATH" in
  *.claude/*|*.claude\\*) _x4_maybe_deployed=1 ;;
  *) _x4_maybe_deployed= ;;
esac
if [ -n "$_x4_maybe_deployed" ] && [ -n "${X4_GAME:-}" ] && [ -n "${X4_TOOLKIT:-}" ]; then
  x4_canon_memo "$X4_GAME";    _dg="${_X4_CANON_RESULT%/}"
  x4_canon_memo "$X4_TOOLKIT"; _dt="${_X4_CANON_RESULT%/}"
  if [ "$_dg" != "$_dt" ] && x4_under "$FILE_PATH" "$X4_GAME/.claude"; then
    for _dsub in skills agents hooks commands settings.json; do
      if x4_under "$FILE_PATH" "$X4_GAME/.claude/$_dsub"; then
        advise "DEPLOYED COPY: $FILE_PATH is inside the game root's .claude/, which is a DEPLOYMENT of $X4_TOOLKIT/.claude/ -- not its source. An edit made only here never ships and drifts (five files had, 2026-09-13). Make the change under $X4_TOOLKIT/.claude/ (create the file there if it is new), then run: uv run python $X4_TOOLKIT/tools/x4validate/scripts/deploy-claude-dir.py --apply. gates/deploy_parity.py verifies the two agree."
        break
      fi
    done
  fi
fi

# === ADVISORY — a write to a file x4lock has LOCKED (2026-10-03, lane N) ===
# MEASURED in a live Codex run: KNOWLEDGEBASE.md was allowed HERE, then the write failed on
# the read-only attribute x4lock sets, and nothing said why or how to proceed. The lock is
# kept (user decision N2); the agent is told the unlock -> edit -> relock steps instead.
# ADVISORY only, never ask/deny: the filesystem already refuses the write, so a prompt
# would protect nothing. COST: `-f`/`-w` are shell builtins, so a writable or absent file
# -- every ordinary edit -- costs no subprocess. Only a READ-ONLY file pays one python
# call, which asks x4lock ITSELF whether it manages it, so there is one manifest, not two.
# `-w` honours the Windows read-only attribute under Git Bash (MEASURED 2026-10-03, with an
# unlocked control, in C:/, C:\ and /c/ spellings). ABOVE the whitelist: its `exit 0`s
# would make an advisory placed under it unreachable (F84); flush_advice still emits it.
if [ -f "$FILE_PATH" ] && [ ! -w "$FILE_PATH" ] && [ -n "$PY" ]; then
  _lk=""
  for _c in "${X4_TOOLKIT:-}/scripts/x4lock.py" "$HOOK_DIR/../../scripts/x4lock.py"; do
    [ -f "$_c" ] && { _lk="$_c"; break; }
  done
  if [ -n "$_lk" ]; then
    "$PY" "$_lk" protected "$FILE_PATH" >/dev/null 2>&1; _lkrc=$?
    if [ "$_lkrc" -ne 1 ]; then
      _lkcmd="python \"$_lk\" unlock \"$FILE_PATH\""
      _lkmsg="LOCKED BY x4lock: $FILE_PATH is read-only, so this write will FAIL until it is unlocked. Run: $_lkcmd -- then make the edit -- then relock: python \"$_lk\" lock"
      [ "$_lkrc" -eq 0 ] || _lkmsg="READ-ONLY: $FILE_PATH is read-only, so this write will fail; x4lock could not say whether it locked it (exit $_lkrc). If it did: $_lkcmd, edit, then python \"$_lk\" lock"
      advise "$_lkmsg"
    fi
  fi
fi

# === WHITELIST — the toolkit's own working dirs & docs (editable in every install mode) ===
# Every NAME test below reads the NORMALISED path (lowercase, forward slashes, `..`
# RESOLVED), never the raw one. AUDIT-2026-09-24 HK-3, MEASURED: the `.claude/hooks/`
# name whitelist read the RAW path, so `<game>/.claude/hooks/../../libraries/wares.xml`
# -- a base-game file -- matched the whitelist and exited 0 before the game-install HARD
# BLOCK was reached. A whitelist decided on text the filesystem does not resolve that
# way is a whitelist for a different file.
_NP="$(x4_norm "$FILE_PATH")"
# H1 (v4.0 release review): a HOST SESSION TRANSCRIPT is written by the host alone. The save
# guard reads the user's typed save approvals from it, so an agent write there could forge
# one -- and `*.claude/projects/*` is whitelisted below for the memory notes beside it.
case "$_NP" in
  */.claude/projects/*.jsonl|*/.codex/sessions/*.jsonl)
    deny "BLOCKED: $FILE_PATH is a host session transcript. The host alone writes it, and the save guard reads the user's typed save approvals from it -- an agent edit could forge one. Leave it to the host." ;;
esac
# The project's instruction and notes files, at the PROJECT ROOT ONLY: CLAUDE.md (and
# Claude's .claude/CLAUDE.md), AGENTS.md (Codex's CLAUDE.md, 2026-09-30), KNOWLEDGEBASE.md,
# and the agent's own notes -- X4-NOTES.md and the 4.0 migration's X4-NOTES.pre-4.0.md (lane
# N, user decision N1: the instructions send agents there, and the game-install block below
# -- and KNOWLEDGEBASE.local.md, the user's personal notes and preferences (D1, v4.0 release
# review: core.md, AGENTS.md and the skills send agents there, and this block denied it in
# the in-game layout, X4_GAME == X4_TOOLKIT)
# denied it, MEASURED in a live Codex run 2026-10-03). The NAME alone is not enough: the same
# name anywhere deeper in the game tree stays denied (or asked, in a deployed mod), so the
# RESOLVED path must equal a root plus the name. The instruction files were a NAME-ANYWHERE
# whitelist until the v4.0 release review (R1-F6/R6-09; pre-arc F150 for CLAUDE.md and
# KNOWLEDGEBASE.md): `<game>/extensions/<mod>/AGENTS.md` and `<game>/libraries/CLAUDE.md`
# walked past every rule below. MEASURED 2026-10-04 before narrowing: 4 of 60,228 files under
# the reference machine's game root carry one of those names -- the 3 at its root and 1 under
# .claude/backups/ (whitelisted below) -- and 0 under extensions/.
# The roots are X4_GAME and X4_TOOLKIT -- the two this hook already resolves in every channel
# (the x4guard write payload carries no cwd, and CLAUDE_PROJECT_DIR is Claude-only). X4_GAME
# is the root that is denied; X4_TOOLKIT covers the in-game layout with X4_GAME unset, where
# only the `X4 Foundations/` name backstop protects it. The case is pure shell, so every
# other path pays nothing.
case "$_NP" in
  */x4-notes.md|*/x4-notes.pre-4.0.md|*/claude.md|*/agents.md|*/knowledgebase.md|*/knowledgebase.local.md)
    _nrel="${_NP##*/}"
    case "$_NP" in */.claude/claude.md) _nrel=".claude/claude.md" ;; esac
    x4_canon_memo "$FILE_PATH"; _nfc="$_X4_CANON_RESULT"
    for _nr in "${X4_GAME:-}" "${X4_TOOLKIT:-}"; do
      [ -n "$_nr" ] || continue
      x4_canon_memo "$_nr"
      [ "$_nfc" = "${_X4_CANON_RESULT%/}/$_nrel" ] && exit 0
    done ;;
esac
# dev/ and dist/ are the documented mod workspace; they MUST be whitelisted before the
# game-install block below, because in the "in-game" install method X4_TOOLKIT *is* the game
# folder — without this, editing your own mod source is hard-denied in the default layout.
#
# The toolkit root is canonicalised ONCE and each subdirectory appended to it, instead of
# canonicalising all 13 `<toolkit>/<sub>` roots separately -- that was a sed (and a
# subshell) per root on every Edit/Write, most of this hook's latency (AUDIT-2026-09-24
# HK-4). The two agree unless a subdirectory is itself a SYMLINK, which realpath would
# resolve elsewhere; for that one case the full x4_under still runs.
x4_canon_memo "$X4_TOOLKIT"; _tkc="${_X4_CANON_RESULT%/}"
x4_canon_memo "$FILE_PATH";  _fpc="$_X4_CANON_RESULT"
for sub in .claude/hooks .claude/skills .claude/agents .claude/commands .claude/plans .claude/backups .claude/memory .claude/projects tools bin scripts dev dist; do
  if [ -L "$X4_TOOLKIT/${sub%%/*}" ] || [ -L "$X4_TOOLKIT/$sub" ]; then
    x4_under "$FILE_PATH" "$X4_TOOLKIT/$sub" && exit 0
    continue
  fi
  case "$_fpc" in "$_tkc/$sub"|"$_tkc/$sub"/*) exit 0 ;; esac
done
# Mod sources may live outside the toolkit entirely (X4_MODS); same reasoning.
x4_under "$FILE_PATH" "${X4_MODS:-}" && exit 0
case "$_NP" in
  *.claude/hooks/*|*.claude/skills/*|*.claude/agents/*|*.claude/commands/*|\
  *.claude/plans/*|*.claude/backups/*|*.claude/memory/*|*.claude/projects/*) exit 0 ;;
esac

# NOTE: the "ask" rules below run BEFORE the game-install block, so the deploy target
# (extensions/), mod manifests and the profile get a confirmation even when they live inside
# (or are symlinked into) the game folder — x4_under resolves symlinks via realpath.
# (content.xml is handled earlier, above the whitelist, so the advisory reaches Claude
# even for a manifest inside dev/ or X4_MODS.)

# === CONFIRMATION — user profile files (saves, config, active mod list) ===
x4_under "$FILE_PATH" "$X4_PROFILE" && [ "$_harness_save_qualified" != 1 ] && ask "EDITING USER PROFILE FILE: $FILE_PATH — changes affect live game config/saves. Confirm?"
# Saves first: the message must name what is actually at risk.
x4_under "$FILE_PATH" "${X4_SAVES:-}" && [ "$_harness_save_qualified" != 1 ] && ask "EDITING A SAVE GAME: $FILE_PATH -- saves are not reproducible and there is no undo. Confirm?"
# A profile path the X4_PROFILE variable does not name still confirms -- BEFORE the
# Documents advisory below, or it would only be advised (the profile lives in Documents).
echo "$FILE_PATH" | grep -qiE 'Egosoft[/\\]X4[/\\]' && [ "$_harness_save_qualified" != 1 ] && ask "EDITING USER PROFILE FILE: $FILE_PATH — changes affect live game config/saves. Confirm?"
# ...and the rest of Documents: game settings, other games, personal files. ADVISORY since
# 2026-10-02 (user: an ask that is always approved protects nothing). advise() accumulates
# and never exits, so every rule below still applies.
x4_under "$FILE_PATH" "${X4_DOCUMENTS:-}" && advise "EDITING A FILE IN YOUR DOCUMENTS FOLDER: $FILE_PATH -- outside the toolkit and the game. Keep it to what the task needs."

# === CONFIRMATION — live extensions/ (deploy target, overwritten on every deploy) ===
# This is mod territory, NOT base-game content — so it's an "ask", even though it usually sits
# inside the game folder (often as a symlink). Must precede the game-install block below.
# Whether the deployed copy is EDITABLE depends on whether a source copy exists elsewhere.
# If a separate mods root is configured and it is NOT inside the game folder, the source
# lives there, every deploy overwrites this copy, and editing it is simply a mistake -- so
# it is a hard block naming where to go instead. When mods live only inside the game folder
# (the common single-location setup) there IS no other copy, and denying would block all
# normal work -- so it stays a confirmation.
x4_mods_are_separate() {
  [ -n "${X4_MODS:-}" ] && [ -n "${X4_GAME:-}" ] || return 1
  x4_under "$X4_MODS" "$X4_GAME" && return 1
  return 0
}
if x4_under "$FILE_PATH" "$X4_EXTENSIONS"; then
  x4_mods_are_separate     && deny "BLOCKED: $FILE_PATH is a DEPLOYED copy; the live extensions/ folder is overwritten on every deploy. Edit the source under $X4_MODS and redeploy."
  ask "EDITING DEPLOYED MOD: $FILE_PATH -- the live extensions/ folder is overwritten on each deploy; edit the source instead. Confirm?"
fi

# === HARD BLOCK — game installation files (base-game content that isn't the toolkit) ===
# The reason ROUTES the agent (lane N, user decision N3): "work in your mod folder" alone
# left a live Codex run with nowhere to put its notes (2026-10-03).
_GR="${X4_GAME:-$X4_TOOLKIT}"; _GR="${_GR%[/\\]}"
_GAME_DENY="BLOCKED: $FILE_PATH is in the game installation. Notes -> $_GR/X4-NOTES.md. Personal notes and preferences -> $_GR/KNOWLEDGEBASE.local.md. Game/engine facts -> $_GR/KNOWLEDGEBASE.md (x4lock keeps it read-only: python \"$X4_TOOLKIT/scripts/x4lock.py\" unlock \"$_GR/KNOWLEDGEBASE.md\", edit, then relock with x4lock.py lock). Mod files -> your mod folder (dev/ or X4_MODS), then deploy."
if [ -n "${X4_GAME:-}" ] && x4_under "$FILE_PATH" "$X4_GAME"; then
  deny "$_GAME_DENY"
fi
echo "$FILE_PATH" | grep -qiE 'X4 Foundations[/\\]' && deny "$_GAME_DENY"

exit 0

#!/bin/bash
# Protect against destructive bash commands. Cross-platform & config-driven.
#
# STRUCTURE: hook_facts.py does ONE parse pass over the command and answers every
# rule's question; this file holds the POLICY -- which verdict, and the prose that
# explains it. Nothing here re-parses shell text.
#
# WHY, and it is measured. Each rule used to hand-roll its own quote-aware parsing in
# bash. On 2026-08-31, clean machine, a 201-char command cost 13,585 ms per Bash call
# against 1,205 ms before the rules were re-scoped -- 11.3x -- because resolve_var
# re-derived its assignment table per TOKEN inside per-SEGMENT loops, and writes_under
# and searches_rooted_at were re-invoked 5 and 4 times, each re-tokenising from scratch.
# PreToolUse blocks the tool call, so that was pure latency on every command.
#
# Every gap a code review found also lived in that duplicated parsing: `mv -t`, `>|`,
# wrapper verbs (time/nice/env/sudo/xargs), `grep -r -e`, rg being recursive by default,
# and a heredoc marker inside a quoted string opening a skip region. Fixing them one
# predicate at a time meant writing the same parser eight more times. The parse pass is
# unit-tested directly, and every test is proven able to FAIL by a planted mutation with
# every predicate probed in both directions. The counts are deliberately not written
# here -- they rot, and a stale number in a comment is worse than none:
#     python .claude/hooks/test_hook_facts.py     (the unit tests)
#     python scripts/verify-hook-tests.py         (mutations + predicate coverage)
#     python scripts/fuzz-guard.py                (syntax-bypass fuzzing)
#
# TWO FRONT-ENDS, ONE RULE SET (AUDIT-2026-09-24 HK-1). This hook is also the PowerShell
# tool's guard. hook_facts.py hands a PowerShell payload to ps_translate.ps1, which parses
# it with PowerShell's own parser and returns the equivalent POSIX-shell command; every
# rule below then judges that. A PowerShell command that does not PARSE is DENIED with the
# parser's message (hook_facts rc 5); one that cannot be translated for any other reason --
# no PowerShell, a failure, the budget -- is ASKED (rc 4).
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

# emit KIND REASON -- render one verdict as JSON.
#
# jq is used when it works, and PYTHON renders it when jq does not. That fallback is
# not belt-and-braces: this hook no longer parses with jq, only EMITS with it, so a
# broken jq turned every deny into empty stdout -- which the harness and the client
# both read as ALLOW. Silence is allow, and that is the exact defect that left five
# hooks dead for five weeks. Python is already required to reach this line, so the
# fallback is always available.
#
# The reason travels through the ENVIRONMENT, never the command line: a message
# containing quotes, newlines and backslashes is routine here, and every attempt to
# escape one through an interpreter argument in this workspace has collapsed.
# The reason is BOUNDED here, for the same reason x4_advise bounds: Claude Code
# files model-facing output above 10,000 characters and shows only a preview.
# Seven rules below interpolate the whole command into their reason, and MEASURED
# with a long path this emitted a 20,126-character permissionDecisionReason --
# twice the ceiling, on the highest-stakes model-facing channel there is.
#
# The VERDICT is never at risk: a deny is honoured at any reason length (measured
# at 200,000). What was at risk is the reason, which is the part that says WHY --
# and a filed reason is a preview of itself.
emit() {
  # X4_GUARD=off (spec 5.7): a deny or an ask becomes an advisory that names what it would
  # have been, and is logged -- except an INABILITY (_X4_UNRELAXED), which still asks
  # (R1-F2). See x4_guard_relax in _x4-env.sh.
  x4_guard_relax protect-bash.sh "$1" "$2"
  set -- "$_x4_rk" "$(x4_bound "$_x4_rr")"
  if jq_works; then
    if [ "$1" = "advise" ]; then
      "$JQ" -n --arg r "$2" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$r}}'
    else
      "$JQ" -n --arg k "$1" --arg r "$2" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$k,permissionDecisionReason:$r}}'
    fi
  else
    X4_KIND="$1" X4_REASON="$2" "$PY" -c 'import json, os, sys
k = os.environ["X4_KIND"]; r = os.environ["X4_REASON"]
h = {"hookEventName": "PreToolUse"}
if k == "advise":
    h["additionalContext"] = r
else:
    h["permissionDecision"] = k; h["permissionDecisionReason"] = r
sys.stdout.buffer.write(json.dumps({"hookSpecificOutput": h}).encode("utf-8"))'
  fi
}

# Under X4_GUARD=off a rule's deny is relaxed -- but an INABILITY ask recorded earlier in this
# call (_X4_UNRELAXED) still asks, and this deny's (relaxed) reason rides along (R1-F2).
deny() {
  VERDICT=deny
  if [ -n "${_X4_UNRELAXED:-}" ] && [ -n "$ASKS" ] && x4_guards_off; then
    emit ask "$ASKS
$(x4_guard_overridden protect-bash.sh deny "$1")"
  else
    emit deny "$1"
  fi
  exit 0
}
# Records and RETURNS -- see the precedence note below. A deny further down must
# still be able to win.
ask()  { VERDICT=ask; if [ -n "$ASKS" ]; then ASKS="$ASKS
$1"; else ASKS="$1"; fi; }
# advise <reason> -- ALLOW, and explain to CLAUDE why the command is questionable.
# The user's attention is the scarce resource: a prompt spends theirs, a deny or an
# advisory spends mine. Anything that is merely MY hygiene must never reach them.
#
# ACCUMULATES, and does NOT exit. An advisory is an ALLOW THAT CARRIES A NOTE, not a
# decision -- so it must never make the rules below it unreachable. MEASURED
# 2026-08-30 (F84): with the six advisory/ask rules off, 298 of the 1,846 commands
# they catch are REFUSED by a rule further down -- 130 timeout-above-the-cap, 64
# shared-/tmp, 35 durable truncating-open, 27 exit-status-after-a-pipeline, 26
# profile-manifest-by-name, 11 stage-everything. Every one measured genuine, every
# one silently suppressed by a note.
#
# ⚠ AND `ask` ACCUMULATES TOO, from 2026-09-05. That same analysis was never run on
# ask-versus-deny, and the answer is the same: an `ask` that EXITS makes every deny
# below it unreachable, so precedence was decided by POSITION IN THE FILE rather than
# by severity. MEASURED: `git add -A` denies, but `rm "<mods>/t.txt" && git add -A`
# only ASKED; and 300 trivial `$( )` substitutions turned the game-delete HARD BLOCK
# into an ask, because the carrier-truncation refusal sits above it. The blocking fact
# was computed correctly in every case and then discarded by ordering alone.
#
# Precedence is now by VALUE, not by line number: deny > ask > advise. `deny` still
# exits -- it is final and nothing outranks it -- while `ask` records its reason and
# lets the remaining rules run, so a later deny can still win. Multiple asks
# concatenate exactly as advisories do.
VERDICT=""
ADVICE=""
ASKS=""
# Flush on EVERY exit path. A tail-only flush is silently skipped by the whitelist
# `exit 0`s in the middle of this file -- MEASURED 2026-08-30: it turned the manifest
# advisory into a plain allow.
flush_advice() {
  # A deny has already emitted and is final.
  [ "$VERDICT" = deny ] && return 0
  if [ -n "$ASKS" ]; then emit ask "$ASKS"; return 0; fi
  [ -n "$ADVICE" ] || return 0
  emit advise "$ADVICE"
}
trap flush_advice EXIT
advise() { if [ -n "$ADVICE" ]; then ADVICE="$ADVICE
$1"; else ADVICE="$1"; fi; }

# --- the single parse pass ---------------------------------------------------
# A guard that cannot see its input must not stay silent: silence IS allow, and that
# is exactly how every hook here sat dead for five weeks. So a missing interpreter, a
# crash, or an unparseable payload ASKS -- it never falls through.
x4_resolve_python; PY="$X4_PY"   # shared with every other hook (see _x4-env.sh)
# Does jq actually WORK? Presence on PATH is not the question -- the probe that caught
# this pointed JQ at a missing binary. Tested before any verdict is rendered, rather than
# discovered on the verdict path where a failure is silent.
#
# LAZILY, on the first verdict (AUDIT-2026-09-24 HK-4). Only emit() consumes it, and most
# calls are a silent allow that renders nothing -- so probing up front spent a jq process
# on every call to answer a question almost none of them asked. A failing probe still
# routes the verdict through python, exactly as before.
JQ_OK=""
jq_works() {
  if [ -z "$JQ_OK" ]; then
    JQ_OK=0
    printf '%s' '{}' | "$JQ" -e . >/dev/null 2>&1 && JQ_OK=1
  fi
  [ "$JQ_OK" = 1 ]
}

if [ -z "$PY" ]; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"X4 GUARD INERT: no Python interpreter was found, so protect-bash.sh could not analyse this command and checked NOTHING. Set X4_PYTHON, or put python on PATH. Confirm only if you know why it is missing."}}'
  x4_guard_check_inert
  exit 0
fi

# The roots go over STDIN, ahead of the payload -- NOT through the environment.
#
# Two reasons, both measured. (1) _x4-env.sh only exports what came from x4-paths.env
# (via `set -a`); X4_DOCUMENTS is computed in a loop and X4_SAVES/X4_REFERENCE come from
# `: ${VAR:=...}`, so none of those are exported and the child would see them EMPTY --
# `under()` is false for an empty root, so those rules would be silently dead.
# (2) Worse, and the reason the environment cannot be used at all: MSYS/Git-Bash
# TRANSLATES a POSIX-looking value when passing an environment variable to a NATIVE
# Windows process. Exporting "/tmp/x/docs" arrived as
# "C:/Users/.../AppData/Local/Temp/x/docs", while the command text still said
# "/tmp/x/docs" -- so no path rule could ever match. The README tells users they may
# write roots as "C:\..." OR "/c/...", so this is a real installation, not a test-only
# concern. A byte stream on stdin is not translated.
# THE PROFILE WITH X4_PROFILE UNSET (v4.0 release review R1-F3). The profile rules ask through
# this root alone, so an unconfigured machine fell through to the Documents rule -- an
# ADVISORY since 2026-10-02 -- and a save delete that v3.3.1 asked about was only advised.
# protect-files.sh keeps an `Egosoft[/\\]X4[/\\]` NAME backstop for the same case; this is
# its Bash twin as a ROOT: the folder that holds every X4 profile, from Documents (Windows)
# or ~/.config (Linux). Only when X4_PROFILE is unset; a configured profile is unchanged.
_x4_prof_root="${X4_PROFILE:-}"
if [ -z "$_x4_prof_root" ]; then
  for _pr in "${X4_DOCUMENTS:+$X4_DOCUMENTS/Egosoft/X4}" "${HOME:+$HOME/.config/EgoSoft/X4}"; do
    [ -n "$_pr" ] && [ -d "$_pr" ] && { _x4_prof_root="$_pr"; break; }
  done
fi
# emit_roots [REFERENCE] -- an argument REPLACES the reference root, for the config pass below.
# Every other root stays, so a root VARIABLE resolves there too (FX-G2 item 1).
emit_roots() {
  printf 'game\t%s\n'       "$X4_GAME"
  printf 'profile\t%s\n'    "$_x4_prof_root"
  printf 'reference\t%s\n'  "${1:-$X4_REFERENCE}"
  printf 'config\t%s\n'     "${X4_CONFIG:-}"
  printf 'toolkit\t%s\n'    "$X4_TOOLKIT"
  printf 'mods\t%s\n'       "$X4_MODS"
  printf 'documents\t%s\n'  "${X4_DOCUMENTS:-}"
  printf 'saves\t%s\n'      "${X4_SAVES:-}"
  printf -- '--X4-ROOTS-END--'
}

_harness_save_qualified=0
if [ "${1:-}" != --harness-policy-backend ]; then
  trap - EXIT
  x4_save_dispatch shell
  exit 0
fi
_harness_facts_stdin=0
for _a in "${@:2}"; do
  case "$_a" in
    --save-qualified) _harness_save_qualified=1 ;;
    --facts-stdin) _harness_facts_stdin=1 ;;
  esac
done

if [ "$_harness_facts_stdin" = 1 ]; then
  # K5 (v4.0 release review): the save bridge already ran hook_facts once for its evidence
  # and sends `<rc>\n<payload json>\n<hook_facts stdout>` -- the same bytes this file would
  # get from a second analyser process (which re-ran the PowerShell translation and doubled
  # the hook's latency). json.dumps never emits a raw newline, so the payload is one line.
  PARSE_RC="${INPUT%%$'\n'*}"
  _rest="${INPUT#*$'\n'}"
  case "$_rest" in
    *$'\n'*) INPUT="${_rest%%$'\n'*}"; FACTS_RAW="${_rest#*$'\n'}" ;;
    *) INPUT="$_rest"; FACTS_RAW="" ;;
  esac
  case "$PARSE_RC" in ''|*[!0-9]*) PARSE_RC=99 ;; esac   # malformed: the generic refusal below
else
  FACTS_RAW=$( { emit_roots; printf '%s' "$INPUT"; } | "$PY" "$HOOK_DIR/hook_facts.py" 2>/dev/null)
  PARSE_RC=$?
fi
# rc 5: the PowerShell TOOL command does not PARSE as PowerShell. That is a syntax error in
# the command I wrote, with the parser's own message to act on -- hygiene, so a DENY with
# an actionable reason rather than a prompt spent on the user (v3.3.0 release review,
# finding 7). A command that parses but cannot be RESOLVED still asks (below).
if [ "$PARSE_RC" = 5 ]; then
  _X4_UNRELAXED=1     # an inability: X4_GUARD=off never relaxes it below ask (R1-F2)
  deny "This PowerShell command does not parse, so the guard could evaluate NO rule against it -- and PowerShell would reject it too: ${FACTS_RAW:-no reason given}. Fix the syntax and re-run."
fi
# rc 4: a PowerShell TOOL command could not be translated (no PowerShell was found to
# parse it, it failed, or the budget ran out), and the reason is on stdout. ASK,
# as an unparseable Bash command does below -- nothing was analysed, so this is neither
# a clean pass nor evidence for a deny.
if [ "$PARSE_RC" = 4 ]; then
  _X4_UNRELAXED=1; VERDICT=ask
  emit ask "X4 GUARD: this PowerShell command could not be analysed, so NO rule was evaluated against it: ${FACTS_RAW:-no reason given}. The guard reads PowerShell through PowerShell's own parser (pwsh, else powershell; X4_PWSH overrides). Fix the syntax, or confirm only if you know the command is safe."
  x4_guard_check_inert
  exit 0
fi
if [ "$PARSE_RC" != 0 ]; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"X4 GUARD INERT: the command analyser exited non-zero, so this command was NOT checked. That is the stdin defect one layer in: allowing silently would look identical to deciding it is fine. Confirm only if you know why."}}'
  x4_guard_check_inert
  exit 0
fi

# Split on the sentinel with parameter expansion -- no eval, no subprocess. The command
# is emitted LAST and raw because it may be multi-line (heredocs are routine here) and
# any escaping scheme would change what the messages below print.
SENT=$'\n__X4_COMMAND__\n'
# AN EMPTY COMMAND ENDS THE STREAM WITH THE SENTINEL, and `$( )` strips the newline after
# it -- so the split below never matched, and COMMAND held the whole FACT DUMP, which
# every rule that quotes the command then printed. Worse, the empty case is exactly a
# PowerShell translation whose every part was UNRESOLVED (`iex $cmd`, `Remove-Item
# @args`), and the `[ -z ]` allow below would have waved it through the moment the split
# worked (AUDIT-2026-09-24 HK-1 re-review). So the empty case is decided here, explicitly.
case "$FACTS_RAW" in
  *$'\n__X4_COMMAND__') FACTS_RAW="$FACTS_RAW"$'\n' ;;
esac
FACT_LINES="${FACTS_RAW%%$SENT*}"
COMMAND="${FACTS_RAW#*$SENT}"
if [ -z "$COMMAND" ]; then
  case $'\n'"${FACT_LINES//$'\r'/}"$'\n' in
    *$'\ncarrier_untranslated\t1\n'*)
      _X4_UNRELAXED=1; VERDICT=ask
      emit ask "X4 GUARD: nothing in this command could be translated into something the guard can check -- every write/delete in it names a target it cannot resolve (a splat that is not a literal hashtable, Invoke-Expression of computed text, a .Delete()-style method on an unidentified object), so it could not be analysed and NO rule was evaluated. Write the target literally, or confirm only if you know what it touches."
      x4_guard_check_inert
      exit 0 ;;
  esac
  exit 0
fi

# Facts are matched against a NEWLINE-DELIMITED STRING, not an associative array.
#
# `declare -A` is bash 4+, and macOS ships bash 3.2 as /bin/bash. There the declare
# fails, `F[key]=v` becomes an ARITHMETIC index into an ordinary array, every predicate
# collapses into one slot, and the hook ALLOWS EVERYTHING while looking healthy -- a
# guard silently dead on an entire platform, which is the exact defect this file exists
# to prevent. Nothing here can catch that at runtime, because bash 3.2 cannot be run on
# the machine that wrote it, so scripts/test-hooks.sh carries a STATIC check instead.
#
# CRs are stripped first. hook_facts.py writes BYTES so none should arrive, but a CR is
# invisible and turns every "1" into "1\r"; the same trap is already recorded for jq.exe.
FACT_LINES="${FACT_LINES//$'\r'/}"
FACTS_NL="
$FACT_LINES
"
# on() KEY -> true when the parse pass says that predicate holds. Bounded by newlines on
# BOTH sides so a key cannot match a longer key's line, nor a value match a longer value.
on() {
  case "$FACTS_NL" in
    *"
$1	1
"*) return 0 ;;
  esac
  return 1
}

# The timeout value, for the message that quotes it back. Pure parameter expansion; the
# TAB after the name is what stops `timeout` matching `timeout_over_cap`.
_t="${FACTS_NL#*
timeout	}"
TIMEOUT_MS="${_t%%
*}"

# === REFUSE — the command did not PARSE, so no rule below means anything ===
# FIRST, deliberately. Every rule here is quote-aware, so one unbalanced quote turns the
# rest of the command into text no rule can see -- and a rule that sees nothing returns
# false, which is indistinguishable from "this is fine".
#
# BASH ITSELF IS THE JUDGE. `-n` parses and executes nothing, costs ~14 ms against a
# ~186 ms budget, and needs no dependency: this hook already IS bash.
#
# The hand-rolled scanner this replaces was wrong in BOTH directions, MEASURED over
# 13,203 historical commands against bash: 13 FALSE POSITIVES (command substitution
# inside double quotes resets the quoting context, which a flat scanner cannot model)
# and 6 FALSE NEGATIVES. Three parser defects shipped from hand-rolling shell
# tokenisation in one release; this is the one place where the real parser is available
# for the asking, so it is asked.
#
# ONE SHORTCUT, and it cannot change a verdict (AUDIT-2026-09-24 HK-4): a command made of
# plain words -- letters, digits and `_./:@%+=,-`, separated by blanks or newlines -- with
# no reserved word among them has nothing that CAN fail to parse: no quote, bracket,
# operator, expansion or compound command. `git status` and `uv run pytest -q` skip a
# process; anything else still asks bash.
#   (Whitespace other than blank/newline -- CR, VT, FF -- is an ordinary word character
#   to bash's parser, so letting [[:space:]] through cannot hide a syntax error. The
#   word loop cannot glob: no pattern character survives the first test.)
_x4_plain=0
_x4_rest="${COMMAND//[A-Za-z0-9_.\/:@%+=,-]/}"
if [ -z "${_x4_rest//[[:space:]]/}" ]; then
  _x4_plain=1
  for _w in $COMMAND; do
    case "$_w" in
      if|then|else|elif|fi|case|esac|for|select|while|until|do|done|function|in|time|coproc)
        _x4_plain=0; break ;;
    esac
  done
fi
if [ "$_x4_plain" != 1 ] && ! bash -n -c "$COMMAND" 2>/dev/null; then
  # DENY, not ask (user, 2026-10-02: an ask they always approve protects nothing). This is
  # Claude's own syntax error -- bash -n rejecting it means bash would not run it either --
  # so the reason must be something Claude can act on. Check-mode signal FIRST: under
  # X4_GUARD_CHECK this still exits 2 ("checked nothing"), which x4guard reports as inert.
  x4_guard_check_inert
  _X4_UNRELAXED=1
  deny "This command does not PARSE (bash -n rejects it), so bash would not run it and the guard could evaluate NO rule against it. Fix the quoting and re-run -- a Windows path ending in a backslash inside double quotes, or a heredoc whose body contains its own terminator line, are the usual causes."
fi

# === CONFIRMATION — the analysis could not be completed ===
# The parse pass follows command CARRIERS: a shell's -c argument, eval, trap, a shell
# heredoc body, and command substitutions. That walk is bounded, because it runs on the
# blocking path and an unbounded walk over an adversarial string is a hang. MEASURED
# 2026-09-02: a 128 KB command with unique text at every nesting level produced 9,841
# command strings and 4.1 s before the bound existed.
#
# When the bound is reached, some command text reached NO rule. Allowing silently would
# be a step that narrows its data and reports success -- the shape behind every tool
# defect found in this workspace. Unreachable by ordinary work: over 13,503 real
# historical commands the largest walk produced 25 of the 250 allowed.
# A COMMAND NAME THAT ARRIVES THROUGH SUBSTITUTION IS AN UNKNOWN VERB, and an unknown
# verb reaches NO rule -- unlike an unknown OPERAND, which still reaches the
# conservative branch. MEASURED 2026-09-08: `$(echo rm) -rf "<game>"` returned ALLOW
# where the plain spelling denies, walking past all three hard blocks.
#
# DENY rather than ask, per this file's own policy: there is a correct alternative I
# can simply take -- write the command name literally -- so it should spend my
# attention and not the user's. Scoped to the VERB of a segment, never to the
# presence of `$(` anywhere in the command, which would deny ordinary substitution
# in an argument.
if on verb_unresolved; then
  _X4_UNRELAXED=1
  deny "A command name here arrives through substitution (\$(...) or backticks), so the guard cannot tell what command this is and NO rule -- including the hard blocks on the game install -- was evaluated for it. Write the command name literally and re-run."
fi

# F10 (v4.0): the same unknown verb through a VARIABLE (`$R -rf <game>`, `& $R Remove-Item
# <save>`, cmd's `%R% del <x>`), given a protected path. The guard cannot say whether it
# deletes; the readings of what it may run (eval, a wrapper, git) are judged by the rules
# below with the plain command's own verdict, and this asks for the rest. An ASK, like the
# unknown PowerShell cmdlet, and not relaxed by X4_GUARD=off: it is an inability.
if on program_dynamic_protected; then
  _X4_UNRELAXED=1
  ask "A command name here comes from a variable or expansion (\$R, \"\$R\", %R%, !R!, & \$R), and the command is given a protected path (the game, reference/, the profile or a save) -- so the guard cannot tell whether it deletes or overwrites it. Write the command name literally, or confirm only if you know what the variable holds."
fi

# A POWERSHELL TOOL command reaches this file already TRANSLATED by PowerShell's own
# parser into the equivalent POSIX-shell command (hook_facts.powershell_to_sh), so every
# rule below judges it exactly as it judges Bash (AUDIT-2026-09-24 HK-1). The messages
# quote the translation -- say so, or a reader sees `rm -rf` they never typed.
if on from_powershell; then
  COMMAND="[PowerShell, as the guard read it] $COMMAND"
fi

# A `cmd /c` or `powershell -c` carrier whose text could not be translated (HK-2), or a
# PowerShell write/delete whose target the translator could not resolve (HK-1 follow-up):
# that part reached no rule. Same verdict as an unparseable command -- and a deny
# elsewhere in the command still wins, because `ask` accumulates.
if on carrier_untranslated; then
  _X4_UNRELAXED=1
  ask "Part of this command could not be analysed, so it was NEVER checked against any rule: nested PowerShell that does not parse (or no PowerShell was found to parse it), or a PowerShell write/delete whose TARGET the guard cannot resolve -- a splat that is not a literal hashtable, a .Delete()/.MoveTo()-style method on an object it cannot identify, Invoke-Expression of computed text. Write the target literally, or confirm only if you know what it touches."
  x4_guard_check_inert
fi

if on carriers_truncated; then
  # DENY, not ask (2026-10-02, same reasoning as the parse failure above): Claude can split it.
  x4_guard_check_inert
  _X4_UNRELAXED=1
  deny "This command nests so many substitutions/wrappers that the guard stopped expanding them, so part of it was NEVER checked against any rule. Split it into simpler commands (or a script file) and re-run."
fi

# C7 (v4.0 release review): too many possible directories from `cd` targets with unset-variable
# operators (${V:+x}...) -- the guard no longer knows where later relative paths land. Same verdict.
if on cd_lanes_truncated; then
  x4_guard_check_inert
  _X4_UNRELAXED=1
  deny "This command's cd targets have so many possible values (\${VAR:+...} / \${VAR-...} with unset variables) that the guard cannot tell which directory later relative paths are in, so it was NEVER fully checked. Use literal cd targets, or split it into simpler commands, and re-run."
fi

# === HARD BLOCK — delete the game installation ===
# The target must be a real path in the tree, OR bear the game's name while not being
# an archive. MEASURED 2026-08-30: 7 of 8 refusals here were a .zip merely NAMED after
# the game in an unrelated folder -- the extension exclusion, not dropping the name
# test, is what removed those. The name test is also the ONLY protection an installer
# with no configured paths has, which is why it is here rather than only on the ask.
on rm_hits_game && deny "BLOCKED: cannot delete the X4 game installation directory."

# === HARD BLOCK — delete the reference folder (read-only base game data) ===
on rm_targets_reference && deny "BLOCKED: reference/ is the read-only unpacked base game data (re-unpack only via bin/unpack-reference.sh)."

# === HARD BLOCK — WRITE into the reference folder (read-only base game data) ===
# The delete case above has been blocked for months; a WRITE had no rule at all, so a
# truncating redirect, a `cp` or a `sed -i` into reference/ was ALLOW -- while the file
# channel (protect-files.sh) hard-blocks the identical write for Edit/Write, and
# CLAUDE.md lists it under "Hard blocked". Two channels, one tree, opposite verdicts.
on writes_reference && deny "BLOCKED: reference/ is the read-only unpacked base game data — never write into it (re-unpack only via bin/unpack-reference.sh). Work in a mod folder under extensions/ instead."

# === HARD BLOCK — WRITE or DELETE the toolkit's PATH CONFIG (v4.0 release review R1-F1/R1-P1) ===
# Every guard reads its roots from x4-paths.env; an agent that could rewrite it could move or
# drop what they protect. The SAME parse pass judges it: hook_facts runs once more with the
# config file IN PLACE OF the `reference` root -- every other root kept, so `$X4_TOOLKIT/...`
# and `cd "$X4_TOOLKIT"` still resolve (FX-G2: sending the config ALONE left every root-variable
# spelling of the write allowed) -- and its write/delete facts for that root answer
# "does this command write or delete the config?" -- a redirect (truncating or appending), cp,
# mv, tee, sed -i, rm. COST: only a command whose TEXT names the config (or X4_CONFIG) pays
# that second pass; every other command pays one pure-shell case. ACCEPTED RESIDUAL: a name
# built so its text never appears (`x4-pa""ths.env`, a variable from outside the command) or
# a write from inside an interpreter (python -c) is not seen -- this stops accidents, not
# intent, like the reference/ OS-deny rule below.
_x4_cfgm=0
case "$COMMAND" in *[xX]4-[pP][aA][tT][hH][sS]*|*X4_CONFIG*) _x4_cfgm=1 ;; esac
if [ "$_x4_cfgm" = 0 ] && [ -n "${X4_CONFIG:-}" ]; then
  case "$COMMAND" in *"${X4_CONFIG##*[/\\]}"*) _x4_cfgm=1 ;; esac
fi
if [ "$_x4_cfgm" = 1 ]; then
  x4_cfg_candidates
  while IFS= read -r _cf; do
    [ -n "$_cf" ] || continue
    _cfacts=$( { emit_roots "$_cf"; printf '%s' "$INPUT"; } \
               | "$PY" "$HOOK_DIR/hook_facts.py" 2>/dev/null)
    _cfacts="${_cfacts%%$SENT*}"
    case $'\n'"${_cfacts//$'\r'/}"$'\n' in
      *$'\nwrites_reference\t1\n'*|*$'\nrm_targets_reference\t1\n'*)
        x4_cfg_why "$_cf"; deny "$_x4_cfg_why Command: $COMMAND" ;;
    esac
  done <<EOF
$_x4_cfg_paths
EOF
fi

# === DENY — a SHELL write to a Claude Code settings file (user decision 2026-10-05) ===
# A settings `env` block reaches every hook (MEASURED, Claude Code 2.1.290), so `"X4_GUARD":
# "off"` written there switches the guards off at the next launch. A shell write's resulting
# content cannot be seen, so it is refused outright -- a DENY with a remedy, never an ask (the
# user's no-prompt rule) -- and the file-edit tools, whose content settings_guard.py judges,
# stay open for every other settings edit. ACCEPTED RESIDUAL: a write from inside an opaque
# interpreter (`python -c "open(...)"`) is not seen.
on writes_host_transcript && deny "BLOCKED: a shell command may not write a host session transcript (.claude/projects/**.jsonl, .codex/sessions/**.jsonl). The host alone writes it, and the save guard reads the user's typed save approvals from it -- an agent-written line could forge one. Command: $COMMAND"
on writes_agent_settings && deny "BLOCKED: a shell command may not write a Claude Code settings file (.claude/settings*.json): its env block reaches every hook, and the guard cannot see what a shell write leaves there -- X4_GUARD in it would switch the X4 guards off. Make the edit with your file-edit tool (Write / Edit / apply_patch), whose content the guard checks; X4_GUARD itself is the user's to set, at launch. Command: $COMMAND"

# === HARD BLOCK — re-unpack into a locked reference/ ===
# Sentinel-gated: once reference/.unpacked-and-locked exists, block accidental re-unpacks.
# The FILESYSTEM test stays here; the parse pass never touches the disk.
if on xrcat_reunpack && [ -f "$X4_REFERENCE/.unpacked-and-locked" ]; then
  deny "BLOCKED: reference/ is locked (reference/.unpacked-and-locked exists). Re-unpacking would overwrite the read-only base. If you really mean to re-unpack: lift the OS deny first (python scripts/x4refguard.py remove -- the user's step), then remove the sentinel."
fi

# === CONFIRM — lifting the OS deny on reference/ ===
# reference/ carries an inherited delete+write DENY (Plan 2 lane D). D8: only the USER lifts
# a protection, so `x4refguard.py remove` and a raw `icacls /remove|/reset` aimed at the
# reference root ASK. Accepted residual (lane D Q3): an opaque interpreter (`python -c`
# calling icacls) is not seen -- this stops accidents, not intent.
on lifts_reference_deny && ask "This LIFTS the OS delete/write protection on reference/ (the read-only unpacked base game). Lifting a protection is the user's step -- confirm only if they asked for it: $COMMAND"

# === CONFIRM — rm targeting the game, profile, reference, mods, or toolkit ===
# ADVISORY since 2026-10-02 (user): 54 hook prompts in 5 weeks, 54 approved, 0 refused --
# an ask that is always approved protects nothing and trains approval. The game install
# and reference/ are HARD blocks above. The X4 PROFILE and saves ask below, EXCEPT when the
# save bridge (save_policy.py) passed --save-qualified: every change in the command is one
# or two existing saves it backed up and verified first (user policy 2026-10-18). Three or
# more, or a wildcard, need typed approval; anything the backup cannot cover still asks.
on rm_in_profile && [ "$_harness_save_qualified" != 1 ] && ask "DELETING IN YOUR X4 PROFILE (mod list, config, saves) -- not reproducible. Confirm: $COMMAND"
on rm_in_x4_dir && advise "This deletes files in an X4 directory (game extensions, mod sources or the toolkit). Make sure it is what you meant -- check git status afterwards, and redeploy if you removed a deployed mod by mistake. Command: $COMMAND"

# === CONFIRM — destructive git inside an X4 directory ===
# git IGNORES the read-only attribute. MEASURED 2026-09-04: `git checkout HEAD~1 -- f`
# overwrote a LOCKED file and left it unlocked afterwards, and `git clean -fdx` deleted
# one. So scripts/x4lock.py -- which stops 11 of 14 write primitives -- stops none of
# these, and this is the only layer that sees them.
#
# ASK, not deny: discarding local changes is an ordinary thing to do, and the whole
# point of the hook policy is that `ask` is for what is genuinely the user's decision.
# Scoped to the segment's own cwd, so `git clean -fdx` in an unknown directory reaches
# no rule rather than firing on unrelated work.
#
# DENY for the BARE form (Plan 3 decision J-Q1, 2026-10-02): `git clean` with -x/-X/-d, or
# `reset --hard`, run with no folder named while the SESSION is in an X4 dir. In the game
# folder `.gitignore` is `*`, so every untracked file there is ignored and `git clean -fdx`
# deletes the installation's own files. A deny reaches the agent, never the user; naming
# the folder explicitly (`git -C`) still reaches the ask below. `git stash --all`/`-a`
# (v4.0.0 review P2) is the same wipe -- it stashes the ignored files and DELETES them.
on git_wipe_from_session_dir && deny "BLOCKED: git clean -x/-X/-d, git reset --hard or git stash -a/--all with no folder named, run from an X4 directory (the game folder, profile, mods, reference or the toolkit). In the game folder git ignores every untracked file, so this would delete the installation's own files, which have no history and no other copy. If you really mean it, name the folder explicitly: git -C \"<folder>\" clean ... (or stash ...) -- that asks the user. Command: $COMMAND"
on git_wipes_x4_dir && ask "git clean, reset --hard or stash -u/-a in an X4 directory DELETES untracked files too, which have no history and no other copy -- and git ignores the read-only protection x4lock applies. Confirm: $COMMAND"
on git_discards_x4_files && advise "This discards working-tree changes to files in an X4 directory, and git IGNORES the read-only protection x4lock applies (measured: git checkout over a locked file overwrote it AND left it unlocked). The named paths are tracked, so the content is recoverable -- but re-lock afterwards with: python scripts/x4lock.py lock"

# === CONFIRM - deleting a SAVE GAME ===
# Named separately from the rule above so the prompt says what is at risk. Reached only when
# the save bridge could NOT back up everything this command changes first (see above).
on rm_saves && [ "$_harness_save_qualified" != 1 ] && ask "DELETING A SAVE GAME, and the guard could not back up everything this command changes first (an unresolved target, a command it cannot see into, another profile file, or no save harness). Saves are not reproducible. Confirm: $COMMAND"

# === CONFIRM - deleting or overwriting anything else under Documents ===
# Game settings, other games' data, personal files. Not ours, not reproducible.
# MEASURED 2026-08-30: 193 of 196 hits were a command that merely NAMED a path there.
# This was the only rule left spending the USER's attention on a false positive, so it
# tests the write TARGET, not the whole string.
# ADVISORY since 2026-10-02 (user; 13 prompts, 0 refused). The X4 PROFILE keeps asking.
on writes_profile && [ "$_harness_save_qualified" != 1 ] && ask "WRITING OR DELETING IN YOUR X4 PROFILE (mod list, config, saves): a bad content.xml or save=\"1\" can damage saves. Confirm: $COMMAND"
on writes_documents && advise "This writes or deletes under the Documents folder -- outside the toolkit and the game (game settings, other games, personal files). Keep it to what the task needs."

# === CONFIRM — mv/cp into the game or profile dirs ===
# MEASURED: 49 of 86 hits were a copy OUT of the game, or a mention. The DESTINATION
# decides -- including `mv -t <dir>` and a `tee` behind a wrapper, both of which the
# bash version missed because it required the verb to be the segment's first word.
on copy_into_game_or_profile \
  && advise "Copying into a game or profile directory. That is the documented DEPLOY path, so it is allowed -- but use dev/_tools/deploy.py rather than a hand-rolled cp: it refuses a wrong destination, deletes orphans one named file at a time, and re-reads the destination to prove every file is byte-identical."

# === CONFIRM — output redirect into game or profile dirs ===
# MEASURED 2026-08-30: 1,269 of 1,320 hits were `2>/dev/null` plus a game path mentioned
# anywhere -- 13.4% of every command in the corpus carrying a note about a write it was
# not making. Now the TRUNCATING redirect target must resolve under the tree. An append
# cannot truncate, so it is not this rule's business.
on redirect_truncate_into_game_or_profile \
  && advise "Redirecting output into a game or profile directory. Allowed, but a truncating > has no backup: if the target is a durable record, write to a temp file and move it into place."

# === DENY — sed -i on game or profile files ===
# DENY is the verdict (user decision 2026-09-25, AUDIT-2026-09-24 HK-6); the reason used
# to end "confirm: ...", which on a deny offers the reader nothing to confirm. There is a
# correct alternative to take instead, so it names that.
on sed_i_in_game_or_profile && deny "BLOCKED: in-place sed edit (sed -i) of a file in the game or profile directory. A shell edit gets NO backup and bypasses the file-path guard. Use your file-edit tool instead (Edit, apply_patch): it is backed up, checked by protect-files.sh, and fails loudly on a non-unique match. Command: $COMMAND"

# === CONFIRM — direct reference to .cat/.dat archives ===
# DROPPED 2026-08-29: this fired on any command whose TEXT mentioned a .cat -- including
# an `echo` that merely discussed one. Writing a .cat is already covered by
# protect-files.sh, which checks the actual TARGET PATH rather than the command text.


# =============================================================================
# The rules below were developed in a live modding workspace and ported here on
# 2026-08-29. Each carries the MEASURED cost of the failure it prevents -- that
# is deliberate, because a guard whose reason has been trimmed away is the first
# one someone deletes. Paths resolve through _x4-env.sh, never as literals.
# =============================================================================

# === ASK — `git add -A` / `git add .` in a workspace shared by concurrent sessions ===
# MEASURED 2026-08-27: a second session had four untracked WIP files in the same tree
# (`_livecli.py`, `_livedump.py` + tests). They inflated the suite 836 -> 877 and
# `git add -A` would have committed someone else's half-finished work. It did not, but
# only by timing. Stage explicit paths instead.
#
# A heredoc BODY is data, not commands: this rule blocked the session's own work on the
# staging phrase appearing inside a payload being written.
on git_add_all && deny "GIT ADD -A / . IN A SHARED WORKSPACE: this stages EVERY untracked file, including another session's work-in-progress. MEASURED 2026-08-27: 4 untracked files from a concurrent session sat in this tree. Prefer explicit paths (git add <file> ...). Proceed?"


# === DURABLE RECORDS — a truncating write via Bash has NO backup and NO guard ===
# backup-before-edit.sh and protect-files.sh are wired to Edit|Write ONLY. A file edited
# through Bash (a `>` redirect, or python's open(p,"w")) gets neither. And open(p,"w")
# TRUNCATES AT OPEN, so an exception mid-write leaves the file EMPTY, not unchanged —
# the traceback looks like "nothing happened" while the content is gone.
# Measured 2026-08-22: that is exactly how a memory file was wiped to 0 bytes.
# Appends (`>>`) are deliberately NOT blocked — they cannot truncate.
if on durable_truncating_redirect; then
  deny "BLOCKED: truncating redirect onto a durable record.
A '>' replaces the file, and a shell write gets NO backup (backup-before-edit.sh covers file-edit tools only).
Use instead:
  - your file-edit tool for a surgical change (Edit, apply_patch: backed up, and it fails loudly if the match is not unique)
  - your file-write tool for a full replacement (Write, apply_patch: backed up)
  - '>>' to append — it cannot truncate
Command: $COMMAND"
fi

# Heuristic: a python truncating open in a command that also names a durable record. The
# path is usually in a variable, so it will not appear inside open() itself.
if on durable_python_open_w; then
  deny "python open(...,'w') in a command that names a durable record (memory / KNOWLEDGEBASE / CLAUDE.md / BLIND-SPOTS).
open() TRUNCATES AT OPEN — if the write then raises, the file is left EMPTY. This wiped a memory file on 2026-08-22.
Prefer your file-edit/-write tools (Edit, Write, apply_patch: backed up), or write to a temp and rename. If you proceed, VERIFY the size afterwards.
Command: $COMMAND"
fi

# === DENY — bare system python invoking TOOLKIT code ===
# instrument_hygiene.py's own shape `bare-python-on-project-code`: MEASURED at
# 0.63% of ~23,000 historical Bash commands (up from 0.03%), and that gate itself
# rates it "viable as a PreToolUse rule". On this machine bare `python`/`python3`/
# `py` IS the system Python 3.10, with none of the project's dependencies
# installed and a syntax it does not even parse (nested f-strings) -- so it fails
# with ModuleNotFoundError or SyntaxError, which reads exactly like a real test
# failure, not a wrong interpreter. `uv run ... python`, an absolute interpreter
# path, a .venv spelling, and a flag-only or `-c`-inline invocation are all
# UNAFFECTED -- see hook_facts.py's _is_bare_python_word / _python_script_target.
#
# SCOPED NARROWER than the routing table's own "tools/, scripts/, gates/,
# .claude/hooks/" wording -- MEASURED, not guessed: classifying every historical
# hit (not a sample) found bare python on `.claude/hooks/*.py` and the
# toolkit-ROOT `scripts/*.py` genuinely works on this machine's real bare
# Python 3.10, because both are stdlib-only by convention (and `.claude/hooks/`
# is invoked bare BY THE HOOK INFRASTRUCTURE ITSELF). Fixing that cut the fire
# rate from 7.70% to 0.53% of the same corpus. See hook_facts.py's `_PROJECT_DIR`
# for exactly what counts now, and the commit that added this rule for the full
# TP/FP classification.
if on bare_python_on_project_code; then
  deny "BARE PYTHON ON TOOLKIT CODE: the system interpreter has no project dependencies, so this reports ModuleNotFoundError / SyntaxError -- which reads exactly like a real test failure, not a wrong interpreter.
Use instead:
  cd tools/x4validate && uv run --frozen python <script.py | -m pytest | -m x4validate>
  uv run --no-project python <path>          # a standalone script with no project deps
Command: $COMMAND"
fi

# === TOOL ROUTING — block recursive text search over the 60 GB reference tree ===
# Purpose-built tools answer these far faster and with a denominator. See CLAUDE.md
# "Discovery vs. Proof" routing table. This blocks the reflex, not the capability:
# a scoped grep at a specific subdirectory still works -- the SEARCH PATH must BE the
# root. rg and ag count with no flag at all: they recurse by default, which the
# flag-gated bash version missed entirely.
if on search_rooted_reference; then
  deny "WRONG TOOL: recursive text search that traverses the whole reference\\ tree (~60 GB).
This fires whether the search is rooted AT reference\\ or at any directory ABOVE it --
an ancestor walk reaches the 60 GB and everything beside it, so it is the worse case.
Route the question first (the project instructions -- CLAUDE.md / AGENTS.md -- section 'Discovery vs. Proof'):
  - 'what values does attribute X take / who references X?' -> BaseX: cd tools\\basex && python ask.py ...
    (fast, and gives a DENOMINATOR — which a bare grep count never does)
  - 'what is the LIVE value and who set it?'                -> uv run x4effective
  - 'does a file with this NAME exist?'                     -> a file-NAME search (Glob, rg --files, find) -- NOT grep: grep searches CONTENTS
  - 'find this text in ONE known area'                      -> a content search (Grep, rg) scoped to that subdirectory
If you truly need a full-tree scan, scope it to a subpath so it is deliberate rather than reflexive."
fi

# === MEASUREMENT-INSTRUMENT TRAP — searching the profile content.xml by NAME ===
# The profile content.xml keys EVERY entry by the mod's MANIFEST ID, never by folder
# name and never by display name. MEASURED 2026-08-23: of 123 on-disk mods, 60 match by
# manifest id and only 9 by folder name.
#
# The failure this catches actually happened, 2026-08-23: `grep -i xspvro` on the
# profile returned nothing -- because the entry is `ws_3691358137` -- and that zero was
# one keystroke from being written into permanent record as "xspvro has no ban record,
# so a Steam re-download would come back ENABLED". It would have destroyed a CORRECT
# memory and reported a real safety protection as absent.
if on profile_search_by_name; then
  deny "CHECK THE INSTRUMENT: the profile content.xml is keyed by MANIFEST ID, not by mod name.
A name-shaped search finding nothing proves NOTHING -- it is the wrong query.
MEASURED: 60 of 123 on-disk mods match by manifest id, only 9 by folder name.
Real case (2026-08-23): 'grep -i xspvro' -> 0 hits, but the entry is 'ws_3691358137'.

Route it instead:
  - 'is this mod active / installed?'  -> _registry.mods(\"active\"|\"installed\") -- never the raw file
  - 'what is this mod's id?'           -> read the mod's OWN extensions/<folder>/content.xml @id
  - and remember: the profile is a DECISION LOG, not an inventory. MEASURED 2026-08-23:
    348 entries, 287 FOSSILS (82.5%), and 54 of 115 installed mods absent from it entirely.
    ABSENT != DISABLED -- X4 adds an unseen folder as ENABLED. See #30 in the x4-xml-patching skill.

If you already have the manifest id and are grepping for THAT, proceed."
fi


# ============================================================================
# MEASUREMENT-INSTRUMENT TRAPS  (added 2026-08-22)
# ----------------------------------------------------------------------------
# MEASURED across three sessions: 10 of 10 checking-step bugs were the CHECKER,
# not the finding. Three of them, in one session, were plain shell artefacts.
# CLAUDE.md #22 records the rule; prose alone has already failed to stop a
# repeat here (#20 was in always-in-context text and still recurred twice in
# one session), so the two textually-detectable ones get a tripwire.

# --- `$?` read after a pipeline -------------------------------------------
# `cmd | head; echo $?` reports HEAD's exit code, not cmd's. This turned a
# stale-index refusal (real exit 5) into an apparent "exit 0" and was nearly
# filed as a tool defect. A pipe inside a process substitution runs in a subshell,
# so its status never becomes $? -- that exclusion is in the parse pass.
if on dollarq_after_pipe; then
  deny "\$? AFTER A PIPELINE reports the LAST command's exit code, not the one you mean.
  cmd | head; echo \$?      -> that is HEAD's exit code
Measured 2026-08-22: this reported a stale-index refusal (real exit 5) as 'exit 0',
and it was nearly written up as a tool defect (check the checker first: #22, the x4-toolkit-dev skill).
Use instead:
  cmd > out 2>&1; rc=\$?     # capture FIRST, format afterwards
  \${PIPESTATUS[0]}          # if you must keep the pipeline
Proceed only if the \$? genuinely belongs to a non-piped command.
Command: $COMMAND"
fi

# --- measurement output into shared /tmp ----------------------------------
# /tmp is shared across CONCURRENT sessions. Near-miss: 5 of 8 gate outputs
# read back were a parallel session's, 4 hours stale, and were nearly reported
# as this session's results. Reads and cleanup are fine; only writes fire.
if on write_to_tmp; then
  deny "WRITING MEASUREMENT OUTPUT TO /tmp — it is shared across concurrent sessions.
A parallel session's stale files were once nearly reported as this session's results.
Use the session scratchpad instead (see the 'Scratchpad Directory' section of the
system prompt), and never glob a pattern that can match another run's files.
Command: $COMMAND"
fi

# --- recursive search rooted AT the workspace / game root -----------------
# Distinct from the reference\ rule above, which only covers reference\.
# MEASURED 2026-08-22: `grep -rn` over the whole workspace root was killed at 300 s and
# the ripgrep tool timed out at 20 s -- tools\basex\basex\data\ alone was 3.7 GB of
# binary database pages. The path must BE the root, so a scoped search into any
# subdirectory is unaffected -- including `cd <root> && grep -rn x subdir/`, which this
# rule's own message recommends and which the bash version nonetheless blocked.
if on search_rooted_workspace; then
  deny "WRONG SCOPE: recursive text search rooted at the whole workspace / game root.
It does not finish — MEASURED 2026-08-22: grep -r killed at 300 s, ripgrep timed out at
20 s, because tools\\basex\\basex\\data\\ alone is GBs of binary database pages.
Name the directory you actually mean:
  tools\\x4validate  ·  dev  ·  tools\\basex (excluding basex/data)
Or route the question (the project instructions -- CLAUDE.md / AGENTS.md -- section 'Discovery vs. Proof'):
  values / who-references-X  -> BaseX ask.py (gives a DENOMINATOR)
  the LIVE value + who set it -> uv run x4effective
  does a FILE by this name exist -> a file-NAME search (Glob, rg --files, find), not grep
Command: $COMMAND"
fi

# --- timeout ABOVE the harness cap ----------------------------------------
# The Bash tool caps `timeout` at 600000 ms and SILENTLY CLAMPS anything larger.
# Passing 900000 does not buy 15 minutes: the command is killed at exactly 10:00
# with exit 143/124, and nothing says it was clamped -- so it reads like the
# command hanging rather than the harness stopping it.
# MEASURED 2026-08-22: this cost four separate 10-minute losses in ONE session
# (corpus_sweep, a 19-gate loop, a 5-gate loop, perf_guard), every time from
# passing a number that was assumed to raise the ceiling and never did.
if on timeout_over_cap; then
  deny "TIMEOUT ABOVE THE CAP: you passed ${TIMEOUT_MS}ms. The Bash and PowerShell tools cap a
foreground call at 600000ms (10 min) and a background one at 7200000ms (2 h).
A larger value is clamped -- a foreground command is KILLED at exactly 10:00 (exit 143),
which looks like a hang and is not one.
Needing more than 10 minutes IS the signal to background it, not to raise the number:
  run_in_background: true      <- up to 2 h (default 30 min); you are re-invoked when it finishes (do NOT poll)
Beyond that, split the work."
fi

# --- a known LONG job in the foreground ------------------------------------
# Measured runtimes in this workspace: gates/corpus_sweep.py ~2100s,
# gates/perf_guard.py (and --record) ~600s+, build-effective.sh ~100-200s,
# `x4effective build` and build-corpus.sh minutes.
# MEASURED 2026-08-30: 125 of this rule's 144 hits merely NAMED a job rather than
# running one. It denied this session's own analysis script (the name sat inside a
# regex literal) and the write of the plan that proposed fixing it. Both the job name
# and an invoker must now appear in the SAME segment, with quoted strings blanked so a
# name inside a literal is a mention.
if on longjob_foreground; then
  deny "LONG JOB IN THE FOREGROUND — this is a known multi-minute command and the Bash
tool hard-caps a foreground call at 600000ms (10 min).
MEASURED: corpus_sweep ~2100s · perf_guard ~600s+ · build-effective.sh ~100-200s.
Prefer  run_in_background: true  — up to 2 h (default 30 min), and it re-invokes you on completion.
Proceed in the foreground only if you have scoped it down (e.g. --limit=N).
Command: $COMMAND"
fi

exit 0

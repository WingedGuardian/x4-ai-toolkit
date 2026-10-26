#!/bin/bash
# Regression test for protect-bash.sh.   Run:  bash .claude/hooks/test-protect-bash.sh
#
# WHY THIS EXISTS. A PreToolUse hook sits in front of EVERY Bash call, so its two
# failure modes are both expensive and both silent-ish:
#   over-block  -> legitimate work becomes impossible, and the fix is not obvious
#   under-block -> the guard is decorative and you trust it anyway
# The must-NOT-fire cases below are therefore the important half, not the padding.
#
# It has already earned its keep twice:
#   1. The first draft of the `$?`-after-a-pipeline rule FALSE-FIRED on
#      `cat >> f.md <<'EOF' | a | b | EOF; echo $?` -- a markdown table inside a
#      heredoc, a shape used constantly in this workspace. Fixed by stripping
#      heredoc BODIES before looking for a pipeline.
#   2. The first version of THIS harness reported 7 spurious failures, because a
#      hook that correctly stays silent produces NO stdout, and `jq '... // "allow"'`
#      over empty input yields empty rather than the default. The harness was wrong,
#      not the hook -- see KNOWLEDGEBASE 2026-08-22b, "check the checker first".

HOOK="$(dirname "$0")/protect-bash.sh"
JQ="${JQ:-jq}"
. "$(dirname "$0")/_x4-env.sh"
S=0   # probes skipped because their location is not configured

# A path-dependent probe SKIPS, loudly, when its location is unset. Silently
# passing would make an unconfigured machine report a green suite over rules
# that were never exercised -- a green that could not have gone red.
need() { if [ -z "$1" ]; then printf '  skip %-26s %s not configured
' "$2" "$3"; S=$((S+1)); return 1; fi; return 0; }
F=0

probe() {  # want_decision  label  command
  local want="$1" label="$2" cmd="$3" out dec rc
  out=$(printf '%s' "$cmd" | "$JQ" -R -s '{tool_input:{command:.}}' | bash "$HOOK" 2>&1); rc=$?
  if [ -z "$out" ]; then
    dec="allow"          # no output == no decision == the tool proceeds
  else
    # An advisory carries additionalContext and NO permissionDecision (2026-10-02: X4-folder
    # deletes became advisories); only output with neither is MALFORMED.
    dec=$(printf '%s' "$out" | "$JQ" -r '.hookSpecificOutput | if .permissionDecision then .permissionDecision elif .additionalContext then "advise" else "MALFORMED" end' 2>/dev/null) \
      || dec="PARSE_ERROR"
  fi
  if [ "$dec" = "$want" ] && [ "$rc" = "0" ]; then
    printf '  ok   %-26s %s\n' "$label" "$dec"
  else
    printf ' FAIL  %-26s want=%s got=%s rc=%s\n' "$label" "$want" "$dec" "$rc"; F=$((F+1))
  fi
}

probe_rt() {  # want_decision  label  command  timeout  run_in_background
  # The runtime rules read `.tool_input.timeout` and `.tool_input.run_in_background`,
  # which `probe` above never sets. Kept as a separate helper so the existing
  # cases keep exercising the exact JSON shape they always have.
  local want="$1" label="$2" cmd="$3" to="$4" bg="$5" out dec rc
  out=$(printf '%s' "$cmd" \
        | "$JQ" -R -s --argjson to "$to" --argjson bg "$bg" \
                '{tool_input:{command:., timeout:$to, run_in_background:$bg}}' \
        | bash "$HOOK" 2>&1); rc=$?
  if [ -z "$out" ]; then dec="allow"
  else dec=$(printf '%s' "$out" | "$JQ" -r '.hookSpecificOutput.permissionDecision // "MALFORMED"' 2>/dev/null) || dec="PARSE_ERROR"
  fi
  if [ "$dec" = "$want" ] && [ "$rc" = "0" ]; then
    printf '  ok   %-26s %s\n' "$label" "$dec"
  else
    printf ' FAIL  %-26s want=%s got=%s rc=%s\n' "$label" "$want" "$dec" "$rc"; F=$((F+1))
  fi
}

bash -n "$HOOK" || { echo "FATAL: $HOOK does not parse"; exit 1; }

echo "--- guards must FIRE ---"
probe deny  "pipe-then-exitcode"   'uv run python x.py 2>&1 | tail -5; echo "exit=$?"'
probe deny  "pipe-plain-dollarq"   'cmd | head -3 && echo $?'
# NARROWED 2026-08-29. The rule tested '$? appears somewhere' AND 'a pipeline
# appears somewhere' as independent predicates over the whole command -- the same
# shape as the delete rules fixed the same day. A peer session hit the false
# positive on a real command where the pipeline sat three segments before a $?
# that belonged to an unpiped command. $? refers to the command IMMEDIATELY
# before it, so only the same segment or the one just prior can be the referent.
probe allow "pipe-far-from-dollarq"   'cd /a && grep -o x f | head -1; cd /b && uv run python t.py; rc=$?'
probe allow "dollarq-with-no-pipeline" 'uv run pytest -q; rc=$?'
probe allow "pipeline-after-the-dollarq" 'uv run python x.py; rc=$?; echo done | tee log.txt'
# A pipe inside a PROCESS SUBSTITUTION runs in a subshell, so its status never
# becomes $?. This denied a real `diff <(grep|sort) <(grep|sort); echo $?` where
# the $? correctly belonged to diff.
probe allow "pipe-in-process-substitution" 'diff <(grep a f | sort) <(grep b g | sort) > /dev/null; echo $?'
# $? EXPANDS inside double quotes, so blanking them to find pipes also hid the
# thing being detected. `echo "exit=$?"` is the commonest form of the mistake.
probe deny  "dollarq-inside-double-quotes" 'cmd | tail -5 && echo "exit=$?"'
probe deny  "tmp-write"            'uv run python gates/g.py > /tmp/g.txt 2>&1'
need "$X4_TOOLKIT" "unscoped-search" X4_TOOLKIT && probe deny "unscoped-search"      "grep -rn foo \"$X4_TOOLKIT\""
# FIXED 2026-08-29, reported by a concurrent session on a real command. The flag
# regex scanned the WHOLE command including the quoted search PATTERN, and a
# hyphenated pattern such as "IN-SECTOR vs OUT-OF-SECTOR" contains `-SECTOR` --
# a dash followed by letters ending in R, which reads as -r. Combined with a `cd`
# into the game root it denied a grep of ONE named 128 KB file. A search STRING is
# data, never flags.
need "$X4_GAME" "grep-one-file-hyphenated-pattern" X4_GAME && probe allow "grep-one-file-hyphenated-pattern" "cd \"$X4_GAME\" && grep -n \"IN-SECTOR vs OUT-OF-SECTOR\" CLAUDE.md"
need "$X4_GAME" "grep-two-named-files" X4_GAME && probe allow "grep-two-named-files" "grep -c \"pre-flight\" \"$X4_GAME/CLAUDE.md\" \"$X4_GAME/KNOWLEDGEBASE.md\""
# ...and the case the rule exists for must still fire, from the same cwd.
need "$X4_GAME" "recursive-from-game-root-cwd" X4_GAME && probe deny "recursive-from-game-root-cwd" "cd \"$X4_GAME\" && grep -rn foo ."
probe deny "durable-truncate"     'echo hi > KNOWLEDGEBASE.md'
need "$X4_REFERENCE" "ref-recursive-grep" X4_REFERENCE && probe deny "ref-recursive-grep"   "grep -rn x \"$X4_REFERENCE\""
# DROPPED 2026-08-29: merely NAMING a .cat is harmless -- this fired on an `echo`
# that discussed one. Writing a .cat is covered by protect-files.sh, which
# checks the TARGET PATH. Probe kept and inverted so a re-add is not silent.
probe allow  "cat-dat-reference"    'XRCatTool -in 01.cat -out ref'
need "$X4_MODS" "rm-in-modding" X4_MODS && probe advise "rm-in-modding"        "rm -rf \"$X4_MODS/dev/foo\""
# Lifting the OS deny on reference/ is the USER's step (Plan 2 D8/#16): an agent's lift ASKS.
need "$X4_REFERENCE" "icacls-reset-reference" X4_REFERENCE && probe ask "icacls-reset-reference" "icacls \"$X4_REFERENCE\" /reset /T /C"
# FX-H2 / reviewer L: Git Bash's doubled-slash switch, and icacls's /inheritance* prefix class.
need "$X4_REFERENCE" "icacls-dblslash-reset" X4_REFERENCE && probe ask "icacls-dblslash-reset" "icacls \"$X4_REFERENCE\" //reset //T //C"
need "$X4_REFERENCE" "icacls-inherit-prefix" X4_REFERENCE && probe ask "icacls-inherit-prefix" "icacls \"$X4_REFERENCE\" /inheritancezzz:r"
probe ask   "x4refguard-remove"    'python scripts/x4refguard.py remove'
# F331 follow-up: a link whose TARGET is a protected root is refused; an unreadable target asks.
need "$X4_REFERENCE" "link-into-reference" X4_REFERENCE && probe deny "link-into-reference" "ln -s \"$X4_REFERENCE\" lnk"
probe ask   "link-target-unreadable" 'ln -s "$X4_NO_SUCH_LINK_SRC" lnk'

# --- runtime rules: the harness caps a foreground call at 600000 ms ---------
# Four separate 10-minute losses in one session came from passing a LARGER value
# and assuming it raised the ceiling. It never did; the job was killed at 10:00.
probe_rt deny  "timeout-over-cap"      'echo hi' 900000 false
probe_rt deny  "timeout-just-over"     'echo hi' 600001 false
probe_rt deny  "timeout-bg-over-2h"    'echo hi' 7200001 true
probe_rt deny   "longjob-foreground"    'uv run python gates/corpus_sweep.py' 0 false
probe_rt deny   "longjob-x4eff-build"   'uv run x4effective build' 0 false

echo "--- guards must STAY QUIET (the important half) ---"
need "$X4_TOOLKIT" "scoped-search" X4_TOOLKIT && probe allow "scoped-search"       "grep -rn foo \"$X4_TOOLKIT/tools/x4validate\""
probe allow "quoted-pipe"         'grep -E "a|b" f.txt; echo $?'
probe allow "pipestatus"          'cmd | head; echo ${PIPESTATUS[0]}'
probe allow "tmp-read"            'cat /tmp/g.txt'
probe allow "tmp-cleanup"         'rm -f /tmp/g.txt'
probe allow "heredoc-md-table"    "$(printf 'cat >> f.md <<%sEOF%s\n| a | b |\nEOF\necho $?' "'" "'")"
probe allow "heredoc-piped-body"  "$(printf 'git commit -F- <<%sMSG%s\ntable | with | pipes\nMSG\necho done' "'" "'")"
probe allow "no-dollarq-pipe"     'cmd | head -5'
probe allow "rc-capture-correct"  'cmd > out 2>&1; rc=$?'
probe allow "or-operator"         'a || b; echo $?'
probe allow "append-durable"      'cat x >> KNOWLEDGEBASE.md'
probe allow "plain-ls"            'ls -la'
probe allow "x4refguard-status"   'python scripts/x4refguard.py status'
probe allow "link-elsewhere"      'ln -s /c/work/src /c/work/lnk'
need "$X4_REFERENCE" "icacls-read-reference" X4_REFERENCE && probe allow "icacls-read-reference" "icacls \"$X4_REFERENCE\""
need "$X4_REFERENCE" "icacls-dblslash-save" X4_REFERENCE && probe allow "icacls-dblslash-save" "icacls \"$X4_REFERENCE\" //save acl.txt //T"
need "$X4_REFERENCE" "icacls-inh-rejected" X4_REFERENCE && probe allow "icacls-inh-rejected" "icacls \"$X4_REFERENCE\" /inheritanc:r"
probe allow "pytest"              'cd tools/x4validate && uv run python -m pytest -q'
probe allow "git-status"          'git status --porcelain'
probe allow "empty-command"       ''
probe_rt allow "timeout-at-cap"        'echo hi' 600000 false
probe_rt allow "timeout-normal"        'echo hi' 120000 false
# A BACKGROUND call's cap is 7200000 ms (the Bash tool's own description, READ 2026-10-02).
# This denied a 25-minute background job with the foreground cap's message.
probe_rt allow "timeout-bg-25min"      'echo hi' 1500000 true
probe_rt allow "timeout-bg-at-2h"      'echo hi' 7200000 true
probe_rt allow "longjob-backgrounded"  'uv run python gates/corpus_sweep.py' 0 true
probe_rt allow "longjob-name-mentioned" 'grep -n corpus_sweep gates/README.md' 0 false
probe_rt allow "longjob-cat-the-file"  'cat gates/perf_guard.py' 0 false

echo
echo "--- git add -A / . in a shared workspace (must ASK) ---"
probe deny   "git-add-A"          'git add -A'
probe deny   "git-add-all-long"   'git add --all'
probe deny   "git-add-dot"        'cd /tmp/repo && git add .'
probe deny   "git-add-A-chained"  'cd x && git add -A && git commit -m x'

echo "--- explicit paths and lookalikes (must NOT fire) ---"
probe allow "git-add-explicit"   'git add tests/test_one.py x4validate/_check.py'
probe allow "git-add-dashed-path" 'git add -- tests/test_one.py'
probe allow "git-add-patch"      'git add -p x4validate/_check.py'
probe allow "git-add-dotfile"    'git add .gitattributes'
probe allow "git-add-dotdir"     'git add .claude/hooks/protect-bash.sh'
probe allow "not-git-add"        'uv add --dev pytest'

echo
echo "--- bare system python on toolkit code (must DENY) ---"
probe deny "bare-python-dash-m-pytest"   'cd tools/x4validate && python -m pytest -q tests/'
probe deny "bare-python3-gates-script"   'python3 gates/claims_audit.py'
probe deny "bare-py-tools-x4validate"    'py tools/x4validate/gates/claims_audit.py'

echo "--- ...but these must NOT fire (the important half) ---"
probe allow "uv-run-frozen-pytest"       'uv run --frozen python -m pytest -q'
probe allow "uv-run-python-gates"        'uv run python gates/claims_audit.py'
probe allow "venv-python-gates"          '.venv/Scripts/python gates/claims_audit.py'
probe allow "bare-python-version"        'python --version'
probe allow "bare-python-pytest-elsewhere" 'cd /c/work/otherproject && python -m pytest -q'
probe allow "bare-python-dash-c"         'python -c "print(1)"'
probe allow "bare-python-scratch-script" 'python /c/scratch/fu-hook/probe.py'
# MEASURED near-misses, not oversights -- see hook_facts.py's _PROJECT_DIR comment.
# Both genuinely run fine under this machine's real bare Python 3.10 (stdlib
# only, or -- for .claude/hooks/ -- invoked bare BY THE HOOK INFRASTRUCTURE ITSELF).
probe allow "toolkit-root-script-x4lock" 'cd /c/toolkit && python scripts/x4lock.py status'
probe allow "dot-claude-hooks-test-file" 'python .claude/hooks/test_hook_facts.py'

echo "--- profile content.xml searched by NAME (must ASK) ---"
need "$X4_PROFILE" "profile-grep-by-name" X4_PROFILE && probe deny   "profile-grep-by-name"   "grep -i somemod \"$X4_PROFILE/content.xml\""
need "$X4_PROFILE" "profile-rg-by-name" X4_PROFILE && probe deny   "profile-rg-by-name"     "rg somemod \"$X4_PROFILE/content.xml\""
probe deny   "profile-grep-envvar"    'grep -n MoreRooms "$X4_PROFILE/content.xml"'

echo "--- ...but these must NOT fire (the important half) ---"
# already holding the manifest id -- that is the CORRECT query, do not nag
need "$X4_PROFILE" "profile-grep-by-ws-id" X4_PROFILE && probe allow "profile-grep-by-ws-id"  "grep -n ws_1234567890 \"$X4_PROFILE/content.xml\""
# a MOD's own content.xml is a manifest, not the profile decision log
probe allow "mod-own-content-xml"    'grep -i id extensions/some_mod/content.xml'
# reading it whole is fine -- the trap is concluding from a NAME search
need "$X4_PROFILE" "profile-cat" X4_PROFILE && probe allow "profile-cat"            "cat \"$X4_PROFILE/content.xml\""
# searching SOURCE for the string content.xml is unrelated
probe allow "source-grep-contentxml" 'grep -rn "content.xml" tools/x4validate/x4validate'

echo
echo "--- fail-open on junk input (a hook must never wedge the Bash tool) ---"
for junk in 'not json' '' '{"tool_input":{}}' '{}'; do
  printf '%s' "$junk" | bash "$HOOK" >/dev/null 2>&1
  rc=$?
  if [ "$rc" = "0" ]; then printf '  ok   junk rc=0  %s\n' "${junk:-<empty>}"
  else printf ' FAIL  junk rc=%s  %s\n' "$rc" "${junk:-<empty>}"; F=$((F+1)); fi
done

echo
# The SKIP count is part of the verdict, not a footnote. Without it a run where
# every path-dependent probe skipped would print the same cheerful line as a run
# that exercised them -- a green that could not have gone red. A suite that
# examined less than everything must say so.
if [ "$S" != "0" ]; then
  echo "NOTE: $S probe(s) SKIPPED because their location is not configured."
  echo "      Those rules were NOT exercised. Configure x4-paths.env to cover them."
fi
echo
echo "--- a SUBSTITUTED command name (must DENY; must not touch substituted ARGS) ---"
# An unknown OPERAND still reaches the conservative branch; an unknown VERB reached
# NO rule, so `$(echo rm) -rf <game>` was ALLOW where the plain spelling denied --
# past all three hard blocks. Priced over 28,989 real commands before shipping: the
# rule as written matches 4 of them (0.014%); keyed on any unresolved verb it would
# have matched 679, mostly $JQ / $UV where the variable holds a PATH.
if need "${X4_GAME:-}" "subst-verb" "X4_GAME"; then
  probe deny  "subst-verb-game"      "\$(echo rm) -rf '$X4_GAME'"
  probe deny  "backtick-verb-game"   "\`echo rm\` -rf '$X4_GAME'"
fi
# THE MUST-NOT-FIRE HALF, and the reason the rule is keyed on the VERB position
# rather than on `$(` appearing anywhere: substitution in an ARGUMENT is ordinary.
probe allow "subst-arg-pwd"        'ls -la $(pwd)'
probe allow "subst-arg-date"       'echo $(date)'
probe allow "subst-arg-cd"         'cd $(git rev-parse --show-toplevel) && ls'
probe allow "var-verb-path"        'JQ=/x/jq.exe; $JQ -n 1'

if [ "$F" = "0" ]; then echo "All hook guards behave. ($F failures, $S skipped)"; else echo "HOOK REGRESSIONS: $F ($S skipped)"; fi
exit $F

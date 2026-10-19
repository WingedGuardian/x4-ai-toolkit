#!/bin/bash
# Smoke-test every safety hook by feeding it synthetic tool-call JSON and asserting the
# decision it returns. Run this after ANY change to .claude/hooks/.
#
#   bash scripts/test-hooks.sh
#
# Why this exists: the hooks are the toolkit's safety net, but nothing exercised them, so
# several were silently inert for entire releases — a `(?!\.claude)` PCRE lookahead that can
# never match under `grep -E` (game-install block), a hook anchored to one developer's folder
# (auto-validate), and auto-backups scattering into the cwd. All three passed code review and
# shipped. Assertions catch that; reading does not.
#
# Requires: bash, jq. Uses only temp dirs — touches nothing real.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$REPO/.claude/hooks"

command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 2; }

# The sandbox must NOT live under /tmp. One of the rules under test refuses writes into
# shared /tmp, so a sandbox rooted there makes every unrelated write probe trip it -- the
# probe for "an append cannot truncate" would fail for a reason that has nothing to do
# with appends. That went unnoticed until 2026-08-31 only because the /tmp rule's regex
# accepted a double quote and not a single one, and these probes quote with singles: the
# harness was passing because of a gap in the rule it was standing next to.
_SBX="${X4_TEST_SANDBOX:-$REPO/.test-sandbox}"
mkdir -p "$_SBX" || { echo "cannot create sandbox base $_SBX"; exit 2; }
SBX_TMP="$(mktemp -d "$_SBX/hooks.XXXXXX")"
# Test writes must never follow a caller's configured backup store. Individual
# backup-failure probes below override this only with their own fixture paths.
unset X4_BACKUPS
# The sandbox itself is removed too, not just its child. Leaving it behind made the
# hygiene check at the end of this file report the suite's OWN sandbox as a stray.
trap 'rm -rf "$SBX_TMP"; rmdir "$_SBX" 2>/dev/null' EXIT
# X4_DECIDE_DUMP=<file> (lane B conformance): append one JSONL record per decide() -- the
# payload, the hook, the env it ran under, the live verdict -- and KEEP the sandbox, so
# tests/test_codex_conformance.py can replay every case through the Codex adapter against
# the same roots. Unset (the default), nothing below changes.
[ -n "${X4_DECIDE_DUMP:-}" ] && trap 'printf "%s\n" "$SBX_TMP" > "$X4_DECIDE_DUMP.sandbox"' EXIT

# Snapshot the caller's directory AFTER the sandbox exists, so the check at the end
# compares against a real baseline.
#
# MEASURED 2026-09-01, and it is the reason this ordering is spelled out: taking the
# snapshot FIRST made the suite fail on every FRESH CHECKOUT -- 133 passed, 2 failed,
# "the suite left new paths in the caller directory: .test-sandbox" -- while passing in
# a tree where an earlier run had already created that directory. It is gitignored, so
# `git status` stayed clean and nothing signalled it. CI checks out fresh every time, so
# the first run is the only run: this would have turned the new CI gate red on the first
# push. A contaminated baseline, in the check written to detect contamination.
#
# The version before THAT compared ./x with $REPO/x while running FROM $REPO -- the same
# path -- so it could never fire at all. Third time is the charm; both earlier failures
# are recorded because the shape repeats.
_CWD_BEFORE="$(ls -A 2>/dev/null | sort)"
case "$SBX_TMP" in
  /tmp/*|/var/tmp/*) echo "REFUSING: sandbox landed under /tmp ($SBX_TMP); the shared-/tmp rule
       would fire on unrelated write probes and the failures would be misattributed."; exit 2 ;;
esac
pass=0; fail=0; skipped=0
ok(){ pass=$((pass+1)); printf '  OK   %s\n' "$1"; }
# A SKIP is neither a pass nor a failure: it is a probe that COULD NOT RUN here.
# It is counted and named, so a green can never quietly mean "nothing was exercised",
# and it is reconciled against EXPECT below alongside the passes and failures.
skip(){ skipped=$((skipped+1)); printf '  SKIP %s\n' "$1"; }
no(){ fail=$((fail+1)); printf '  FAIL %s\n' "$1"; }

# decide <expected> <hook> <json> <label>
decide(){
  local exp="$1" hook="$2" json="$3" label="$4" out got rc errf="$SBX_TMP/decide.stderr"
  case "${X4_TOOLKIT:-}" in "$SBX_TMP"/*)
    if [ ! -f "$X4_TOOLKIT/tools/modding_harness/__init__.py" ]; then
      mkdir -p "$X4_TOOLKIT/tools"
      cp -R "$REPO/tools/modding_harness" "$X4_TOOLKIT/tools/"
    fi ;;
  esac
  if ! printf '%s' "$json" | jq -e . >/dev/null 2>&1; then
    no "$label -- MALFORMED PROBE PAYLOAD: not valid JSON, so this probe exercises the
        parser-contract guard rather than its own rule"
    return
  fi
  # rc and stderr are DIAGNOSTIC ONLY (lane J, Plan 3 flake hunt): they go on a FAIL line and
  # change no verdict, count or dump record. An empty stdout still reads as allow, so a hook
  # that CRASHED looks like one that allowed -- the bracket on the FAIL line tells them apart.
  out=$(printf '%s' "$json" | bash "$HOOKS/$hook" 2>"$errf"); rc=$?
  if [ -z "$out" ]; then got="allow"
  else
    got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null)
    # An advisory is an allow that CARRIES A REASON FOR CLAUDE. Distinguishing the two
    # matters: "allow" and "advise" are the same decision and a different intent, and a
    # hook that silently stopped advising would otherwise still read as passing.
    if [ "$got" = "allow" ]        && [ -n "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)" ]; then
      got="advise"
    fi
  fi
  [ -n "${X4_DECIDE_DUMP:-}" ] && jq -cn --arg e "$exp" --arg g "$got" --arg h "$hook" --argjson j "$json" \
      --arg l "$label" --arg cwd "$(pwd)" --arg out "$out" \
      '{exp:$e,got:$g,hook:$h,payload:$j,label:$l,cwd:$cwd,out:$out,
        env:(env|with_entries(select((.key|test("^(X4_|CLAUDE_PROJECT_DIR$|GIT_CEILING_DIRECTORIES$|HOME$)"))
                                     and (.key|test("KEY|TOKEN|SECRET|PASS")|not))))}' \
      >> "$X4_DECIDE_DUMP" 2>/dev/null
  [ "$got" = "$exp" ] && ok "$label ($got)" \
    || no "$label — expected $exp, got $got [hook rc=$rc; stderr: $(head -c 300 "$errf" 2>/dev/null | tr '\n' ' ')]"
}
fj(){ printf '{"tool_name":"Edit","tool_input":{"file_path":%s}}' "$(printf '%s' "$1" | jq -Rs .)"; }
# jq -Rs does the JSON escaping. The first version interpolated the command RAW,
# so any probe containing a double quote emitted INVALID JSON -- and since the
# parser-contract guard landed, the hook correctly answers `ask` to that. A probe
# written with quotes was therefore testing the parser guard, not its own rule.
cj(){ printf '{"tool_name":"Bash","tool_input":{"command":%s}}' "$(printf '%s' "$1" | jq -Rs .)"; }
pj(){ printf '{"tool_name":"Grep","tool_input":{"pattern":"x","path":%s}}' "$(printf '%s' "$1" | jq -Rs .)"; }

run_layout(){ # run_layout <name> <toolkit> <game>
  local name="$1" TK="$2" GAME="$3"
  mkdir -p "$TK/.claude/hooks" "$TK/dev/mymod" "$TK/reference" "$GAME/extensions/deployed" \
           "$SBX_TMP/profile" "$SBX_TMP/mods/other"
  export X4_TOOLKIT="$TK" X4_GAME="$GAME" X4_REFERENCE="$TK/reference" \
         X4_PROFILE="$SBX_TMP/profile" X4_MODS="$SBX_TMP/mods" X4_EXTENSIONS="$GAME/extensions" \
         X4_CONFIG=/nonexistent CLAUDE_PROJECT_DIR="$TK" \
         X4_SAVES="$SBX_TMP/profile/save" X4_DOCUMENTS="$SBX_TMP/docs"
  mkdir -p "$SBX_TMP/profile/save" "$SBX_TMP/docs/My Games/SomeGame" "$SBX_TMP/docs/Other Game"
  echo; echo "=== protect-files.sh — $name layout ==="
  decide allow protect-files.sh "$(fj "$TK/dev/mymod/libraries/wares.xml")" "mod source in dev/"
  decide allow protect-files.sh "$(fj "$SBX_TMP/mods/other/wares.xml")"         "mod source in \$X4_MODS"
  decide allow protect-files.sh "$(fj "$TK/.claude/hooks/x.sh")"            "toolkit .claude/"
  decide allow protect-files.sh "$(fj "$TK/CLAUDE.md")"                     "CLAUDE.md"
  # AGENTS.md is Codex's CLAUDE.md, and sits beside it in the game root. It was denied as
  # a game-install file (2026-09-30) while CLAUDE.md was allowed. The twin keeps the
  # exception to the exact NAME: a looser `*agents.md` pattern would let this one through.
  decide allow protect-files.sh "$(fj "$GAME/AGENTS.md")"                   "AGENTS.md in the game root"
  decide deny  protect-files.sh "$(fj "$GAME/notagents.md")"                "a NAME merely ending in agents.md is still a game file"
  # ...and the exception is the project ROOT, not the name anywhere (v4.0 release review
  # R1-F6/R6-09, pre-arc F150): the same names deeper in the game tree are game files.
  decide deny  protect-files.sh "$(fj "$GAME/libraries/AGENTS.md")"         "an AGENTS.md deeper in the game is a game file"
  decide deny  protect-files.sh "$(fj "$GAME/libraries/CLAUDE.md")"         "a CLAUDE.md deeper in the game is a game file"
  decide deny  protect-files.sh "$(fj "$GAME/libraries/KNOWLEDGEBASE.md")"  "a KNOWLEDGEBASE.md deeper in the game is a game file"
  decide allow protect-files.sh "$(fj "$GAME/KNOWLEDGEBASE.md")"            "KNOWLEDGEBASE.md at the game root"
  decide allow protect-files.sh "$(fj "$GAME/.claude/CLAUDE.md")"           "Claude's .claude/CLAUDE.md at the game root"
  decide deny  protect-files.sh "$(fj "$TK/reference/libraries/wares.xml")" "reference/ is read-only"
  # A BACKSLASH path, which the harness could not carry until 2026-08-30: fj
  # interpolated raw, so a Windows path was invalid JSON (\U is not a valid
  # JSON escape) and the probe silently exercised the parser-contract guard.
  decide deny  protect-files.sh "$(fj "$(printf '%s' "$TK/reference/libraries/wares.xml" | tr / '\\')")" \
    "reference/ is read-only, named with BACKSLASHES"
  decide deny  protect-files.sh "$(fj "$GAME/01.cat")"                      ".cat archive"
  decide deny  protect-files.sh "$(fj "$GAME/01.dat")"                      ".dat archive"
  decide deny  protect-files.sh "$(fj "$GAME/libraries/wares.xml")"         "base game file"
  # AUDIT-2026-09-24 HK-3: the `.claude/hooks/` NAME whitelist read the raw path, so a
  # `..` walking back out of it reached the base game with an ALLOW.
  decide deny  protect-files.sh "$(fj "$GAME/.claude/hooks/../../libraries/wares.xml")" \
    "a .. out of a whitelisted .claude/hooks/ still reaches the game block"
  decide advise protect-files.sh "$(fj "$TK/dev/mymod/content.xml")"        "content.xml manifest"
  # The PROFILE's own content.xml is the mod enable/disable list and the Steam
  # Workshop toggle. Turning the manifest prompt into an advisory (2026-08-29)
  # nearly bypassed the profile confirmation for it: the advisory exits 0, so it
  # short-circuited a rule the user had explicitly kept. It must still ASK.
  decide ask   protect-files.sh "$(fj "$SBX_TMP/profile/content.xml")"          "profile content.xml still confirms"
  # ...and a DEPLOYED mod's manifest must still reach the extensions rule below the
  # advisory. MEASURED 2026-08-30 (F84): an advisory that exits makes every rule under
  # it unreachable, so this manifest was advised and never confirmed.
  decide deny  protect-files.sh "$(fj "$GAME/extensions/deployed/content.xml")"  "a deployed mod manifest reaches the deployed-copy rule"
  decide ask   protect-files.sh "$(fj "$SBX_TMP/profile/config.xml")"           "user profile file"
  # Saves, game settings and everything else under Documents (user request
  # 2026-08-30). MEASURED first: over 11,133 historical commands this fires on 7
  # MORE than the existing profile rules already did, and on zero more edits.
  decide allow protect-files.sh "$(fj "$SBX_TMP/profile/save/save_001.xml.gz")"  "a new save game needs no preservation"
  decide advise protect-files.sh "$(fj "$SBX_TMP/docs/My Games/SomeGame/x.ini")"  "under My Games"
  # `.dat` is a GENERIC extension. The X4-archive rule denied another game's save
  # outright until it was scoped to X4 locations -- a guard blocking a file that was
  # never ours. It must ask here, and still deny inside the game folder.
  decide advise protect-files.sh "$(fj "$SBX_TMP/docs/Other Game/saved.dat")"     "another game's .dat is not an X4 archive"
  decide deny  protect-files.sh "$(fj "$GAME/01.dat")"                       ".dat INSIDE the game still denies"
  # X4_MODS is outside $GAME in both layouts, so the source lives elsewhere -> hard block.
  decide deny  protect-files.sh "$(fj "$GAME/extensions/deployed/x.xml")"   "deployed extensions/ (source elsewhere)"
  # The DEPLOYED-copy advisory (2026-09-13). In the separate layout the game root's
  # .claude/ is a deployment of the toolkit's, so an edit there is advised toward the
  # source; in the in-game layout they are the same directory and it must stay silent.
  if [ "$name" = "separate" ]; then
    decide advise protect-files.sh "$(fj "$GAME/.claude/skills/x4-debug/SKILL.md")" "a DEPLOYED skill edit advises toward the source"
    decide advise protect-files.sh "$(fj "$GAME/.claude/agents/mod-research.md")"   "a DEPLOYED agent edit advises"
    decide advise protect-files.sh "$(fj "$GAME/.claude/hooks/protect-bash.sh")"    "a DEPLOYED hook edit advises"
    decide allow  protect-files.sh "$(fj "$TK/.claude/skills/x4-debug/SKILL.md")"   "the SOURCE skill does not advise"
  else
    decide allow  protect-files.sh "$(fj "$GAME/.claude/skills/x4-debug/SKILL.md")" "in-game layout: .claude/ IS the source, no advisory"
  fi
}

# The in-game layout is the interesting one: toolkit IS the game folder, so mod sources sit
# inside it and must not be caught by the game-installation block.
run_layout "in-game"  "$SBX_TMP/game/X4 Foundations" "$SBX_TMP/game/X4 Foundations"
run_layout "separate" "$SBX_TMP/sep/toolkit"         "$SBX_TMP/sep/X4 Foundations"

# The OTHER branch of the same rule, and it must be exercised or the deny above is the
# only reachable outcome and the ask is dead code. Mods living INSIDE the game folder is
# the common single-location setup: there is no separate source, so denying would block
# every normal edit.
echo; echo "=== protect-files.sh -- deployed edit when mods live INSIDE the game ==="
GAME="$SBX_TMP/game/X4 Foundations"
mkdir -p "$GAME/extensions/deployed"
# The hook runs as a CHILD process, so a bare `VAR=x decide ...` prefix does not reach
# it -- the values must be EXPORTED. That is why this saves and restores instead of
# using a subshell: `decide` increments the pass/fail counters, which a subshell would
# discard, and the run would go quiet again in a different way.
_sm="$X4_MODS"; _se="$X4_EXTENSIONS"; _sg="$X4_GAME"
# THREE reachable branches for a write under extensions/, one probe each -- otherwise
# the deny above is the only outcome ever exercised and the rest is dead code.
export X4_EXTENSIONS="$GAME/extensions" X4_GAME="$GAME"
#  (a) no mods root configured at all: nothing says where the source is, so CONFIRM.
unset X4_MODS
decide ask protect-files.sh "$(fj "$GAME/extensions/deployed/x.xml")" "deployed edit, no mods root configured"
#      ...and a MANIFEST must reach it too: the manifest advisory sits above this rule
#      and used to exit, so it never did (F84).
decide ask protect-files.sh "$(fj "$GAME/extensions/deployed/content.xml")" "a deployed manifest still confirms"
#  (b) mods root IS the extensions folder: the deployed copy IS the source, so this is
#      ordinary work and must not prompt at all.
export X4_MODS="$GAME/extensions"
decide allow protect-files.sh "$(fj "$GAME/extensions/deployed/x.xml")" "deployed edit IS the source"
export X4_MODS="$_sm" X4_EXTENSIONS="$_se" X4_GAME="$_sg"


# =============================================================================
# search-scope.sh -- a content search that returns a PARTIAL answer looking complete
# =============================================================================
# Grep and Glob read LOOSE files only. A mod that ships a .cat archive is invisible to
# them, so a zero result means "not in the loose subset", not "absent" -- in the same
# shape a complete answer would have. Measured on the reference machine: 54 of 133
# installed mods ship BOTH, so the misleading case is the common one.
echo; echo "=== search-scope.sh ==="
# The sandbox lives in $REPO/.test-sandbox, which THIS repo's .gitignore excludes -- and
# search-scope.sh now denies a Grep rooted in a git-ignored folder (correctly: Grep could
# see nothing there). Stop git's repo discovery at the sandbox base so each fixture is
# judged on its OWN repo (the igame fixture below has one; the plain game has none).
_sgc="${GIT_CEILING_DIRECTORIES-}"; export GIT_CEILING_DIRECTORIES="$_SBX"
GAME="$SBX_TMP/game/X4 Foundations"
# One line, deliberately. This held the two characters \n, which OUTSIDE QUOTES is a
# literal 'n' -- so mkdir -p took it as an argument and created a directory called `n`
# in the CALLER's cwd on every run. An empty directory is invisible to `git status`,
# so it accumulated unnoticed. The stray-path assertion below now makes it loud.
mkdir -p "$GAME/extensions/packedmod" "$GAME/extensions/loosemod/md" "$X4_TOOLKIT/dev/mymod"
: > "$GAME/extensions/packedmod/ext_01.cat"
: > "$GAME/extensions/packedmod/readme.txt"
: > "$GAME/extensions/loosemod/md/a.xml"
export X4_EXTENSIONS="$GAME/extensions" X4_GAME="$GAME"
decide advise  search-scope.sh "$(pj "$GAME/extensions")"              "search rooted at extensions/"
decide advise  search-scope.sh "$(pj "$GAME/extensions/packedmod")"    "search inside a .cat-shipping mod"
# The must-NOT-fire half is not decoration: when this hook was first written a
# collapsed backslash made it inert, every must-fire probe went red and every
# must-NOT-fire probe went green. Silence is indistinguishable from allow, so the
# fire cases above are what give these their meaning.
decide allow search-scope.sh "$(pj "$GAME/extensions/loosemod")"     "loose-only mod is fully visible"
decide allow search-scope.sh "$(pj "$GAME/extensions/packedmod/readme.txt")" "a single FILE is not a survey"
decide allow search-scope.sh "$(pj "$X4_TOOLKIT/dev/mymod")"          "the mod workspace"
# NO PATH IS THE BROADEST SEARCH, NOT THE NARROWEST. This asserted `allow`, which
# pinned the defect: a Grep with no path runs from the working directory -- which here
# contains the whole extensions/ folder -- so it covers a strict SUPERSET of the case
# two lines up that DOES advise. Coverage was inverted with respect to breadth.
# MEASURED over 26 transcripts / 397 Grep+Glob calls: 6 no-path and 4 rooted above
# extensions/, all unguarded, against 15 that fired.
decide advise search-scope.sh '{"tool_name":"Grep","tool_input":{"pattern":"x"}}' "no path at all is the BROADEST search"
decide advise search-scope.sh "$(pj "$GAME")"                          "rooted ABOVE extensions/ covers every mod"
# MIXED CASE. `MOD` is carved out of the NORMALISED (lowercased) path, so the .cat probe
# was built as `<real root>/mixedcasepacked` for a folder named `MixedCasePacked` --
# while the comment above it claimed the probe looked at the actual directory. Windows
# resolves that anyway, so this probe passes here through the fast path; on a
# case-sensitive filesystem it is the one that goes RED without the fallback, and X4
# ships a Linux build. Measured on the reference machine: 3 of 54 .cat-shipping mods
# (5.6%) have an uppercase letter, i.e. were silently unguarded there.
mkdir -p "$GAME/extensions/MixedCasePacked" "$GAME/extensions/MixedCaseLoose/md"
: > "$GAME/extensions/MixedCasePacked/ext_01.cat"
: > "$GAME/extensions/MixedCaseLoose/md/a.xml"
decide advise search-scope.sh "$(pj "$GAME/extensions/MixedCasePacked")" "mixed-case .cat mod is still found"
decide allow  search-scope.sh "$(pj "$GAME/extensions/MixedCaseLoose")"  "mixed-case LOOSE mod must NOT advise"
# GIT-IGNORED ROOT. Grep and Glob honour .gitignore, so a root git ignores is INVISIBLE to
# them -- and the reference machine's game root carries a whitelist .gitignore (`*`).
# MEASURED 2026-10-02: Grep rooted anywhere in extensions/ found 0 of 133 manifests
# (rg --no-ignore: 133), and the packed-archive advisory supplied the WRONG explanation.
# The plain game above is NOT a git repo, which is why its probes are unaffected.
IGAME="$SBX_TMP/igame/X4 Foundations"
mkdir -p "$IGAME/extensions/loosemod/md" "$IGAME/tracked"
: > "$IGAME/extensions/loosemod/md/a.xml"; : > "$IGAME/tracked/a.md"
printf '%s\n' '*' '!.gitignore' '!tracked/' '!tracked/**' > "$IGAME/.gitignore"
git -C "$IGAME" init -q
_se3="$X4_EXTENSIONS"; _sg3="$X4_GAME"
export X4_EXTENSIONS="$IGAME/extensions" X4_GAME="$IGAME"
gj(){ printf '{"tool_name":"Glob","tool_input":{"pattern":"*/a.xml","path":%s}}' "$(printf '%s' "$1" | jq -Rs .)"; }
nj(){ printf '{"tool_name":"Grep","tool_input":{"pattern":"x"},"cwd":%s}' "$(printf '%s' "$1" | jq -Rs .)"; }
decide deny   search-scope.sh "$(pj "$IGAME/extensions/loosemod")" "Grep rooted in a git-IGNORED folder sees nothing"
decide deny   search-scope.sh "$(pj "$IGAME/extensions")"          "Grep rooted at an ignored extensions/ sees nothing"
decide deny   search-scope.sh "$(nj "$IGAME/extensions/loosemod")" "no path: the CWD is the root, and it is ignored"
decide advise search-scope.sh "$(gj "$IGAME/extensions/loosemod")" "Glob in an ignored folder: a zero is unreliable"
decide allow  search-scope.sh "$(pj "$IGAME/tracked")"             "an UN-ignored folder of the same repo is visible"
decide allow  search-scope.sh "$(pj "$IGAME/extensions/loosemod/md/a.xml")" "a single FILE in an ignored folder IS readable (Grep reads a named file)"
# Rooted ABOVE an ignored extensions/: still an advisory (tracked files there are visible),
# but it must name the real cause. `decide` sees only the category, so read the reason.
_out=$(pj "$IGAME" | bash "$HOOKS/search-scope.sh" 2>/dev/null)
case "$_out" in
  *git-ignored*) ok "rooted above an ignored extensions/: the advisory names .gitignore" ;;
  *)             no "rooted above an ignored extensions/: the advisory blames packing, not .gitignore" ;;
esac
export X4_EXTENSIONS="$_se3" X4_GAME="$_sg3"
if [ -n "$_sgc" ]; then export GIT_CEILING_DIRECTORIES="$_sgc"; else unset GIT_CEILING_DIRECTORIES; fi
# (the unparseable-payload case is NOT a probe here: this harness refuses a probe
#  whose payload is not valid JSON, and it is right to -- such a probe would
#  exercise the harness rather than the hook. Measured directly instead:
#  truncated payload -> 299 bytes of INERT advisory, was 0 bytes and silent.)

echo; echo "=== protect-bash.sh ==="
decide deny  protect-bash.sh "$(cj "rm -rf '$X4_GAME'")"      "rm the game directory"
# --- Block B: two rules measured ~97% GENUINE, but with the same FP shape -------
# Each blocked this session's own work on its trigger word inside a quoted fixture.
# Overriding a rule that measures as working needs its own evidence, so these are
# probed and committed separately from the six.
_GA2="git add ""-A"
decide deny  protect-bash.sh "$(cj "$_GA2")"   "staging everything still denies"
decide allow protect-bash.sh "$(cj "echo 'do not run $_GA2 here' >> notes.md")"   "the phrase inside a quoted string is a mention"
decide allow protect-bash.sh "$(cj "grep -n '$_GA2' scripts/test-hooks.sh")"   "searching for the phrase is a mention"
decide deny  protect-bash.sh "$(cj "grep -i xspvro '$X4_PROFILE/content.xml'")"   "searching the profile manifest by NAME still denies"
decide allow protect-bash.sh "$(cj "echo 'the profile content.xml is keyed by id' >> notes.md")"   "describing the manifest is not searching it"
# A HEREDOC BODY IS DATA, NOT COMMANDS. Both rules blocked this session's own work on
# text inside one -- a script whose payload happened to contain the phrase at a command
# position, and one that merely mentioned a search, the profile var and the word content.
# The `$?` rule already strips heredoc bodies; both now use the same helper.
decide allow protect-bash.sh "$(cj "python - <<'PY'
s = \"cd x && git add -A > log\"
PY")"   "the staging phrase inside a heredoc body is data"
decide allow protect-bash.sh "$(cj "python - <<'PY'
guard = 'grep -qiE content.xml against \$X4_PROFILE'
PY")"   "a manifest search described inside a heredoc body is data"
decide deny  protect-bash.sh "$(cj "rm -rf '$X4_REFERENCE'")" "rm reference/"
# C1 (v4.0 release review): an ABSENT delete target is not a save the bridge can preserve -- it
# was how `rm <saves>/save_00{0,1,2}.xml.gz` lifted the ask with 0 backups. It keeps the ask now.
decide ask    protect-bash.sh "$(cj "rm -f '$SBX_TMP/profile/save/save_001.xml.gz'")" "C1: deleting an absent named save keeps the profile ask"
# An unresolved operand naming the save folder is the bridge's "unresolved protected operand"
# REFUSAL (deny), stricter than the ask -- either way nothing is lifted.
decide deny   protect-bash.sh "$(cj "rm -f $SBX_TMP/profile/save/save_00{1,2}.xml.gz")" "C1: a brace-expanded save delete is refused, never lifted"
# C6: a redirect / output-flag target is judged at EVERY value of an unset-variable operator.
decide deny   protect-bash.sh "$(cj "echo x > \"\${NOPE:+x}$X4_REFERENCE/libraries/wares.xml\"")" "C6: > into reference/ behind \${NOPE:+x}"
decide deny   protect-bash.sh "$(cj "echo x >> \"\${NOPE+x}$X4_REFERENCE/libraries/wares.xml\"")" "C6: >> into reference/ behind \${NOPE+x}"
decide deny   protect-bash.sh "$(cj "curl -o \"\${NOPE:+x}$X4_REFERENCE/libraries/wares.xml\" http://127.0.0.1:9/x")" "C6: curl -o into reference/ behind \${NOPE:+x}"
# P1: an assignment prefix holding a spaced process substitution is ONE word.
decide deny   protect-bash.sh "$(cj "X=<(echo a) rm -rf '$X4_REFERENCE'")" "P1: X=<(echo a) prefix keeps the reference hard block"
decide deny   protect-bash.sh "$(cj "env X=<(echo a) rm -rf '$X4_REFERENCE'")" "P1: env X=<(echo a) prefix keeps the reference hard block"
decide deny   protect-bash.sh "$(cj "env X=\$(echo a rm) -rf '$X4_REFERENCE'")" "P1: env word-splits an unquoted expansion into the command name"
decide advise protect-bash.sh "$(cj "echo x > '$SBX_TMP/docs/notes.txt'")"            "writing into Documents"
# ...but only when Documents is the TARGET. MEASURED 2026-08-30: 193 of 196 hits
# were a command that merely NAMED a path there -- `(rm|mv|cp|tee|>) anywhere` AND
# `a Documents path anywhere`, two independent tests over the whole string. This is
# the only rule left that spends the USER's attention on a false positive.
decide allow protect-bash.sh "$(cj "P='$SBX_TMP/docs/Egosoft'; ls -la \"\$P\" 2>/dev/null")"   "reading a Documents path is not writing to it"
decide allow protect-bash.sh "$(cj "cp '$SBX_TMP/docs/notes.txt' '$SBX_TMP/copy.txt'")"   "copying OUT of Documents"
decide allow protect-bash.sh "$(cj "grep -n x '$SBX_TMP/docs/notes.txt' > '$SBX_TMP/out.txt'")"   "reading from Documents, writing elsewhere"
decide advise protect-bash.sh "$(cj "cp '$SBX_TMP/a.txt' '$SBX_TMP/docs/b.txt'")"   "copying INTO Documents"
decide advise protect-bash.sh "$(cj "rm -f '$SBX_TMP/docs/notes.txt'")"   "deleting inside Documents"
decide advise protect-bash.sh "$(cj "tee '$SBX_TMP/docs/log.txt' < '$SBX_TMP/a.txt'")"   "tee INTO Documents"
decide advise protect-bash.sh "$(cj "D='$SBX_TMP/docs'; echo x > \"\$D/n.txt\"")"   "a variable Documents destination still confirms"
decide advise protect-bash.sh "$(cj "rm -rf '$X4_MODS/other'")" "rm inside mod sources"
# 2026-10-02 (user): 54 hook prompts in 5 weeks, 0 refused -- X4-folder deletes and
# Documents writes became ADVISORIES. The X4 PROFILE keeps asking (standing rule: a bad
# content.xml or save="1" can damage saves); saves keep asking; game/reference stay hard.
decide ask   protect-bash.sh "$(cj "echo x > '$SBX_TMP/profile/config.xml'")"   "writing into the X4 PROFILE still confirms"
decide ask   protect-bash.sh "$(cj "rm -f '$SBX_TMP/profile/config.xml'")"   "deleting in the X4 PROFILE still confirms"
decide ask   protect-bash.sh "$(cj "cp '$SBX_TMP/a.txt' '$SBX_TMP/profile/b.xml'")"   "copying INTO the X4 profile still confirms"
# --- rule 2: the redirect advisory must test the TARGET, not the whole string ----
# MEASURED 2026-08-30: 1,269 of its 1,320 hits were `2>/dev/null` plus a game path
# mentioned anywhere -- 13.4% of every command in the corpus carrying a spurious note.
decide allow protect-bash.sh "$(cj "find '$X4_GAME/extensions' -maxdepth 2 -type d 2>/dev/null")"   "stderr suppression is not a write into the game"
decide allow protect-bash.sh "$(cj "cat '$X4_GAME/CLAUDE.md' > '$SBX_TMP/copy.md'")"   "reading from the game, writing elsewhere"
decide advise protect-bash.sh "$(cj "echo x > '$X4_GAME/notes.txt'")"   "a truncating write into the game still advises"
decide allow protect-bash.sh "$(cj "echo x >> '$X4_GAME/notes.txt'")"   "an APPEND cannot truncate, so it does not advise"
# --- rule 6: cp/mv must test the DESTINATION -----------------------------------
# MEASURED: 49 of 86 hits were a copy OUT of the game, or a mention.
decide advise protect-bash.sh "$(cj "cp -r '$SBX_TMP/mods/other' '$X4_GAME/extensions/other'")"   "a deploy INTO the game still advises"
decide allow protect-bash.sh "$(cj "cp '$X4_GAME/CLAUDE.md' '$SBX_TMP/backup.md'")"   "copying OUT of the game is not a deploy"
# --- rule 7: the game-delete backstop must name the install ROOT ---------------
# MEASURED: 7 of 8 hits were a .zip merely NAMED after the game, in another folder.
decide allow protect-bash.sh "$(cj "rm -f '$SBX_TMP/X4 Foundations Toolkit v1.zip'")"   "a zip named after the game is a file, not the install"
decide deny  protect-bash.sh "$(cj "rm -rf '$X4_GAME'")"   "deleting the install itself still denies"
# The BOUNDARY of the hard block, probed on both sides. It used to cover anything UNDER
# the game folder. MEASURED 2026-08-31 over a 1,000-command replay of real history: all 4
# hits of the rule were `rm -rf "$DST"` with DST resolving to extensions/<one mod> -- the
# documented deploy path, which deploy.py performs itself. It only started firing there
# because variable resolution got BETTER; the predicate was never edited.
#
# The original objection to narrowing was that `rm -rf <game>/extensions` would fall
# through to a confirmation. It does not: extensions/ WHOLESALE is still a hard block.
# Only a path INSIDE it falls to the confirm, which is the verdict CLAUDE.md assigns to
# deleting in an X4 directory, and it is recoverable by redeploying from dev/.
decide deny  protect-bash.sh "$(cj "rm -rf '$X4_GAME/extensions'")"   "extensions/ WHOLESALE is still a hard block"
decide advise protect-bash.sh "$(cj "rm -rf '$X4_GAME/extensions/deployed'")"   "ONE deployed mod confirms, it is not a hard block"
decide advise protect-bash.sh "$(cj "rm -f '$X4_GAME/extensions/deployed/ext_01.cat'")"   "one file inside a deployed mod confirms"
# ...and with the destination in a VARIABLE, which is the form that exposed this and the
# form no probe used before.
decide advise protect-bash.sh "$(cj "DST='$X4_GAME/extensions/deployed'; rm -rf \"\$DST\"")"   "a variable deploy destination confirms, not blocks"
# --- rule 3: the long-job rule must see an INVOCATION, not a mention -----------
# MEASURED 2026-08-30: 125 of its 144 hits merely NAMED a job. It denied this session's
# own analysis script (the name sat in a regex literal) and the write of the PLAN that
# proposed fixing it -- a plan file being the purest possible mention-not-invocation.
_J="corpus_sweep"
decide deny  protect-bash.sh "$(cj "uv run python gates/$_J.py")"   "invoking a long job in the foreground still denies"
decide allow protect-bash.sh "$(cj "python -c \"print('$_J')\"")"   "the job name inside a quoted string is a mention"
decide allow protect-bash.sh "$(cj "grep -n $_J gates/README.md")"   "searching for the job name is a mention"
decide allow protect-bash.sh "$(cj "echo 'see gates/$_J.py for details' >> notes.md")"   "writing the name into a document is a mention"
decide advise protect-bash.sh "$(cj "G='$X4_GAME'; echo x > \"\$G/notes.txt\"")"   "a variable game destination still advises"
# The FALSE POSITIVES these rules produced the moment they first ran (2026-08-29).
# Until this week no rule in protect-bash.sh had ever executed in production, so
# none of their false-positive rates were known. The delete rules tested 'an rm
# appears SOMEWHERE' AND 'the game path appears SOMEWHERE' as two independent
# checks over the whole command -- so deleting a temp file was blocked because the
# command also mentioned the game directory in a variable. They now test only the
# segments that actually invoke a delete.
decide allow protect-bash.sh "$(cj "L='$X4_GAME/.claude'; rm -f /tmp/scratch.log")" "temp delete, game dir in a variable"
decide allow protect-bash.sh "$(cj "echo 'working on $X4_GAME'; rm -f /tmp/x")"      "temp delete, game dir in an echo"

# --- shared /tmp: the rule had NO probe, and its regex accepted only a DOUBLE quote ---
# So a single-quoted target slipped past, and the hook suite's own sandbox depended on
# that gap. Both quote styles are asserted here so closing it cannot silently reopen.
decide deny  protect-bash.sh "$(cj 'uv run x4validate > "/tmp/out.log"')"  "measurement output to /tmp, double-quoted"
decide deny  protect-bash.sh "$(cj "uv run x4validate > '/tmp/out.log'")"  "measurement output to /tmp, SINGLE-quoted"
decide deny  protect-bash.sh "$(cj 'uv run x4validate > /tmp/out.log')"    "measurement output to /tmp, unquoted"
# Reads and cleanup are fine; only writes fire. Without these the rule could be widened
# to "mentions /tmp" and nothing would notice.
decide allow protect-bash.sh "$(cj 'cat /tmp/out.log')"                    "READING from /tmp is fine"
decide allow protect-bash.sh "$(cj 'ls -la /tmp/')"                        "listing /tmp is fine"
decide allow protect-bash.sh "$(cj "ls -la '$X4_GAME/extensions'")"                 "no delete at all, game dir named"
decide deny  protect-bash.sh "$(cj "cd /tmp && rm -rf '$X4_GAME/extensions'")"     "delete of the game dir, chained"
decide allow  protect-bash.sh "$(cj "ls '$X4_GAME/01.cat'")"   "command naming a .cat"   # DROPPED: naming a .cat is harmless; protect-files.sh checks the TARGET
decide allow protect-bash.sh "$(cj "ls -la .")"               "harmless ls"
decide allow protect-bash.sh "$(cj "git status")"             "git status"

echo; echo "=== backup-before-edit.sh ==="
TK="$X4_TOOLKIT"; SUBJ="$TK/dev/mymod/libraries/wares.xml"
mkdir -p "$(dirname "$SUBJ")"; echo '<diff/>' > "$SUBJ"
printf '%s' "$(fj "$SUBJ")" | bash "$HOOKS/backup-before-edit.sh" >/dev/null 2>&1
[ "$(ls "$TK/.claude/backups/" 2>/dev/null | grep -c 'wares.xml')" -ge 1 ] \
  && ok "backup created" || no "no backup created"
grep -q "wares.xml" "$TK/.claude/backups/AUDIT_LOG.txt" 2>/dev/null \
  && ok "audit log appended" || no "no audit log entry"
# AUDIT-2026-09-24 HK-3: the skip for .claude/hooks/ read the RAW path, so a file
# spelled through it with `..` was edited with no backup. Named via the skipped dir.
SUBJ3="$TK/dev/mymod/libraries/hk3dotdot.xml"; echo '<diff/>' > "$SUBJ3"
printf '%s' "$(fj "$TK/.claude/hooks/../../dev/mymod/libraries/hk3dotdot.xml")" \
  | bash "$HOOKS/backup-before-edit.sh" >/dev/null 2>&1
[ "$(ls "$TK/.claude/backups/" 2>/dev/null | grep -c 'hk3dotdot.xml')" -ge 1 ] \
  && ok "a .. through .claude/hooks/ is still backed up" \
  || no "a file named through .claude/hooks/.. was NOT backed up"
# AUDIT-2026-09-24 HK-5: a source path longer than a filename may be flattened into a
# name cp cannot create. Deep ON PURPOSE, so this probe does not depend on how long the
# sandbox path happens to be -- that dependence is what made "backup created" a flake.
_LP="$TK/dev/$(printf 'm%.0s' $(seq 1 90))/$(printf 'n%.0s' $(seq 1 90))"
mkdir -p "$_LP" && echo '<diff/>' > "$_LP/hk5longpath.xml"
_lp_out="$(printf '%s' "$(fj "$_LP/hk5longpath.xml")" | bash "$HOOKS/backup-before-edit.sh" 2>/dev/null)"
if [ -z "$_lp_out" ] && [ "$(ls "$TK/.claude/backups/" 2>/dev/null | grep -c 'hk5longpath.xml')" -ge 1 ]; then
  ok "a path too long to flatten into one filename is still backed up"
else
  no "long-path source: no backup (or the hook asked): ${_lp_out:0:120}"
fi
# Must stay anchored to the toolkit even with CLAUDE_PROJECT_DIR unset.
mkdir -p "$SBX_TMP/elsewhere"
( unset CLAUDE_PROJECT_DIR X4_TOOLKIT; export X4_BACKUPS="$TK/.claude/backups"; cd "$SBX_TMP/elsewhere" \
  && printf '%s' "$(fj "$SUBJ")" | bash "$HOOKS/backup-before-edit.sh" >/dev/null 2>&1 )
[ -d "$SBX_TMP/elsewhere/.claude/backups" ] \
  && no "backups scattered into the cwd when CLAUDE_PROJECT_DIR is unset" \
  || ok "backups stay anchored to the toolkit"

echo; echo "=== check-reference-version.sh ==="
printf '"AppState"\n{\n\t"buildid"\t\t"99999999"\n}\n' > "$SBX_TMP/acf"
echo "11111111" > "$TK/.claude/.reference-buildid"
_crv_out="$(X4_APPMANIFEST="$SBX_TMP/acf" bash "$HOOKS/check-reference-version.sh" 2>/dev/null)"
case "$_crv_out" in *stale-reference*) ok "warns on build mismatch" ;; *) no "no stale-reference warning" ;; esac
# FX-Z (FX-B4 lead): the re-unpack steps were RELATIVE (`python scripts/x4refguard.py remove`,
# `bin/unpack-reference.sh`) -- runnable only from the toolkit folder, and the bare forms
# refuse under an exported X4_REFERENCE/X4_TOOLKIT that differs from the config. Each step now
# names its absolute path, --toolkit and --reference. One clause per command.
case "$_crv_out" in *"\"$X4_TOOLKIT/scripts/x4refguard.py\" remove --toolkit \"$X4_TOOLKIT\" --reference \"$X4_REFERENCE\""*)
  ok "stale-reference: the x4refguard remove step is absolute, with --toolkit and --reference" ;;
  *) no "stale-reference: the x4refguard step is not the absolute command: ${_crv_out:0:300}" ;; esac
case "$_crv_out" in *"bash \"$X4_TOOLKIT/bin/unpack-reference.sh\" --toolkit \"$X4_TOOLKIT\" --reference \"$X4_REFERENCE\""*)
  ok "stale-reference: the unpack step is absolute, with --toolkit and --reference" ;;
  *) no "stale-reference: the unpack step is not the absolute command: ${_crv_out:0:300}" ;; esac
case "$_crv_out" in *"$X4_REFERENCE/.unpacked-and-locked"*"$X4_TOOLKIT/.claude/.reference-buildid"*)
  ok "stale-reference: the sentinel and the detached build-id file are named by absolute path" ;;
  *) no "stale-reference: a file in the steps is not absolute: ${_crv_out:0:300}" ;; esac
echo "99999999" > "$TK/.claude/.reference-buildid"
[ -z "$(X4_APPMANIFEST="$SBX_TMP/acf" bash "$HOOKS/check-reference-version.sh" 2>/dev/null)" ] \
  && ok "silent when builds match" || no "warned when builds match"
# A real manifest carries MORE THAN ONE "buildid": the installed build directly under
# AppState, and one per beta branch under PrivateDepots. MEASURED 2026-09-14 on this
# machine: AppState 23660954, public_beta 23524486. bin/unpack-reference.sh took the LAST
# match, so a re-unpack would have stamped the BETA's build into the sentinel; this hook
# took the FIRST, which is right only while Steam writes AppState's key above the
# branches. The fixture above has one buildid, so neither could go red. Both now read
# through x4_acf_buildid, which takes the key at depth 1 whatever the order.
_acf_real="$SBX_TMP/acf-real"; _acf_hostile="$SBX_TMP/acf-hostile"
printf '"AppState"\n{\n\t"appid"\t\t"392160"\n\t"buildid"\t\t"23660954"\n\t"PrivateDepots"\n\t{\n\t\t"branches"\n\t\t{\n\t\t\t"public_beta"\n\t\t\t{\n\t\t\t\t"buildid"\t\t"23524486"\n\t\t\t}\n\t\t}\n\t}\n}\n' > "$_acf_real"
printf '"AppState"\n{\n\t"PrivateDepots"\n\t{\n\t\t"branches"\n\t\t{\n\t\t\t"public_beta"\n\t\t\t{\n\t\t\t\t"buildid"\t\t"23524486"\n\t\t\t}\n\t\t}\n\t}\n\t"buildid"\t\t"23660954"\n}\n' > "$_acf_hostile"
[ "$( . "$HOOKS/_x4-env.sh"; x4_acf_buildid "$_acf_real" 2>/dev/null )" = "23660954" ] \
  && ok "x4_acf_buildid reads the INSTALLED build, not a beta branch's" \
  || no "x4_acf_buildid did not return the AppState build 23660954"
echo "23660954" > "$TK/.claude/.reference-buildid"
[ -z "$(X4_APPMANIFEST="$_acf_hostile" bash "$HOOKS/check-reference-version.sh" 2>/dev/null)" ] \
  && ok "a beta branch listed ABOVE the installed build does not fake a stale reference" \
  || no "warned stale because a beta branch's buildid came first"
[ -z "$(X4_APPMANIFEST="$_acf_real" bash "$HOOKS/check-reference-version.sh" 2>/dev/null)" ] \
  && ok "silent on a real-shaped manifest whose builds match" \
  || no "warned on a real-shaped manifest whose builds match"
# The helper strips a trailing CR. Steam writes LF (measured 2026-09-14, 0 CRLF in 455
# lines), but a manifest copied through a CRLF-converting tool would otherwise match no
# brace line, never reach depth 1, and return nothing -- a silent non-answer.
# awk rather than `sed 's/$/\r/'`, whose `\r` is a GNU extension (review 2026-09-14);
# BINMODE=3 so Git Bash's gawk writes the CR as given instead of translating line ends.
awk -v BINMODE=3 '{ sub(/\r$/, ""); printf "%s\r\n", $0 }' "$_acf_real" > "$SBX_TMP/acf-crlf"
[ "$( . "$HOOKS/_x4-env.sh"; x4_acf_buildid "$SBX_TMP/acf-crlf" 2>/dev/null )" = "23660954" ] \
  && ok "x4_acf_buildid reads a CRLF manifest too" \
  || no "x4_acf_buildid returned nothing for a CRLF manifest"

echo
# A probe line that fails to PARSE increments neither counter -- it vanishes, and the
# run still prints a cheerful total. That happened while adding the probe above: bash
# reported "n: command not found" and the suite still said "33 passed, 0 failed".
# So the total is asserted against a number that must be updated deliberately.
# Anything that appeared in the caller's directory during the run is a leak. An empty

# --- an UNRESOLVABLE VERB reaches no rule at all -----------------------------------
#
# MEASURED 2026-09-08 against the live hook, before the fix:
#     rm -rf "<game>"           deny
#     $(echo rm) -rf "<game>"   ALLOW    <- past all three hard blocks
#     `echo rm` -rf "<game>"    ALLOW
#
# An unknown OPERAND still reaches the conservative branch; an unknown VERB reaches
# nothing, which is why this is worse than the variable spelling resolve_verb was
# written for. The guard fuzzer could not find it: its VERB axis has four mutators and
# every one is a SPELLING of a literal name -- none is a SUBSTITUTION. A bug history
# is not a grammar (CLAUDE.md #36).
#
# PRICED before shipping, over every Bash command in 110 real session transcripts:
# 0 of 28,963 carry a substituted verb, so this denies nothing anyone has ever run.
decide deny protect-bash.sh "$(cj "\$(echo rm) -rf \"$GAME\"")" \
  "a command-substitution VERB is refused, not silently unmatched"
decide deny protect-bash.sh "$(cj "\`echo rm\` -rf \"$GAME\"")" \
  "the backtick spelling of the same bypass is refused too"

# THE MUST-NOT-FIRE SIDE, and it is the whole reason this is keyed on the VERB rather
# than on `$(` appearing anywhere: substitution in an ARGUMENT is ordinary work.
decide allow protect-bash.sh "$(cj 'ls -la $(pwd)')" \
  "substitution in an ARGUMENT is untouched"
decide allow protect-bash.sh "$(cj 'echo $(date)')" \
  "a substituted argument to a harmless verb is untouched"
decide allow protect-bash.sh "$(cj 'cd $(git rev-parse --show-toplevel) && ls')" \
  "a substituted argument in a cd is untouched"


EXPECT=312

# =============================================================================
# PATH DIALECT -- a verdict must not depend on HOW the path was written
# =============================================================================
# MEASURED 2026-08-30: x4_norm is `tr 'A-Z\' 'a-z/'` -- lowercase and backslash to
# slash, and nothing else. So Git Bash's "/c/Users/..." and Windows' "C:/Users/..."
# never compare equal, and a rule rooted purely on a configured PATH misses one of
# the two dialects entirely. Rules carrying a NAME backstop (the game, the profile)
# survived; Documents, reference/ and the workspace root did not.
# 2,553 historical commands use the MSYS form. No probe in this suite had ever used
# a drive-lettered root at all, so nothing here could have caught it.
echo; echo "=== path dialect ==="
_sd="$X4_DOCUMENTS"; _sr="$X4_REFERENCE"
export X4_DOCUMENTS="C:/fixture/Documents"
decide advise protect-bash.sh "$(cj 'echo x > "C:/fixture/Documents/n.txt"')"   "Documents write, root Windows / command Windows"
decide advise protect-bash.sh "$(cj 'echo x > "/c/fixture/Documents/n.txt"')"   "Documents write, root Windows / command MSYS"
export X4_DOCUMENTS="/c/fixture/Documents"
decide advise protect-bash.sh "$(cj 'echo x > "C:/fixture/Documents/n.txt"')"   "Documents write, root MSYS / command Windows"
decide allow protect-bash.sh "$(cj 'echo x > "C:/elsewhere/n.txt"')"   "an unrelated path is still allowed in either dialect"
export X4_DOCUMENTS="$_sd"
export X4_REFERENCE="C:/fixture/reference"
decide deny  protect-bash.sh "$(cj 'grep -rn foo "/c/fixture/reference"')"   "recursive search at reference root, MSYS form"
# --- rules 4 and 5: a search rule must test the SEARCH PATH, not the whole string
# MEASURED 2026-08-30: 67 of 80 reference refusals and 12 of 20 workspace refusals were a
# search SCOPED to a subdirectory, or not recursive at all. The reference rule's own
# comment already promised a scoped search would be allowed; it never was.
decide allow protect-bash.sh "$(cj 'grep -rn foo "C:/fixture/reference/aiscripts"')"   "a subdirectory-scoped search is allowed, as its own message promises"
decide allow protect-bash.sh "$(cj 'cd "C:/fixture/reference" && grep -rn foo aiscripts/')"   "cd to the root, then scope to a subdirectory"
decide allow protect-bash.sh "$(cj 'cd "C:/fixture/reference" && grep -n foo one.xml')"   "a NON-recursive search is not this rule"
decide deny  protect-bash.sh "$(cj 'cd "C:/fixture/reference" && grep -rn foo .')"   "cd to the root then search dot still denies"
export X4_REFERENCE="$_sr"

# =============================================================================
# THE STDIN CONTRACT -- a hook that receives NOTHING must not read as ALLOW
# =============================================================================
# MEASURED 2026-08-29, and it had made every hook here inert: `cat /dev/stdin`
# returns ZERO BYTES in the Claude Code hook environment while a bare `cat`
# returns the payload (7 of 7 probes: 0 vs 641-2840 bytes). The failure is
# invisible by construction -- a hook that reads nothing falls through its first
# guard clause and exits 0, which is byte-identical to deciding "this is fine".
#
# THIS SUITE PASSED THROUGHOUT, because it pipes stdin explicitly and /dev/stdin
# resolves fine that way. So the probes below deliberately close stdin instead:
# they reproduce the PRODUCTION condition, which piping never can.
echo; echo "=== stdin contract ==="
# The property is NOTHING MAY BE SILENT -- not "everything must prompt". A hook that
# emits nothing has decided "this is fine", which is how five hooks sat dead for weeks.
#
# But the verdict differs by KIND, and conflating them cost a real contradiction: the
# two guards that can REFUSE must ask, while search-scope.sh is advisory-only and its own
# header says "ADVISE, never ask -- it must cost me a note and the user nothing". It was
# escalating to a PROMPT on every Grep/Glob if the stdin defect ever recurred.
# x4validate-on-edit.sh already makes the advisory call for the same situation.
#
# Safety is not weakened: a systemic stdin failure is still reported by protect-bash and
# protect-files, which is where a refusal actually matters.
for h in protect-bash.sh protect-files.sh backup-before-edit.sh; do
  out=$(bash "$HOOKS/$h" </dev/null 2>/dev/null)
  got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null)
  [ -z "$got" ] && got="allow"
  if [ "$got" = "ask" ]; then ok "$h asks when it received no input"
  else no "$h -- empty stdin read as '$got'; silence IS allow"; fi
done
for h in search-scope.sh; do
  out=$(bash "$HOOKS/$h" </dev/null 2>/dev/null)
  if [ -n "$(printf '%s' "$out" | tr -d '[:space:]')" ]; then
    ok "$h (advisory) still SAYS something when it received no input"
  else no "$h -- empty stdin produced NO OUTPUT at all; silence IS allow"; fi
done
# ...and the refusal itself must not depend on the tool that may have failed.
# MEASURED 2026-08-30: x4_require_input emitted its `ask` THROUGH jq, so with jq
# unavailable an empty payload was reported by nothing at all -- allow again.
for h in protect-bash.sh protect-files.sh backup-before-edit.sh; do
  out=$(JQ=no_such_jq_binary bash "$HOOKS/$h" </dev/null 2>/dev/null)
  got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null)
  [ -z "$got" ] && got="allow"
  if [ "$got" = "ask" ]; then ok "$h asks on empty input even with jq unavailable"
  else no "$h -- empty input + broken jq read as '$got'; the refusal ran through the broken tool"; fi
done
for h in search-scope.sh; do
  out=$(JQ=no_such_jq_binary bash "$HOOKS/$h" </dev/null 2>/dev/null)
  if [ -n "$(printf '%s' "$out" | tr -d '[:space:]')" ]; then
    ok "$h (advisory) still SAYS something with jq unavailable"
  else no "$h -- empty input + broken jq produced NO OUTPUT; the note ran through the broken tool"; fi
done
# The static half. A runtime probe only covers the hooks it lists; this covers any
# hook, including one added later, and it is the cheaper of the two to keep true.
if grep -nE '^[^#]*cat /dev/stdin' "$HOOKS"/*.sh >/dev/null 2>&1; then
  no "a hook reads 'cat /dev/stdin' again -- that returns EMPTY in the real hook environment"
else
  ok "no hook reads 'cat /dev/stdin'"
fi
# --- STATIC: no hook may read a DEAD payload env var -------------------------
# Claude Code hands a hook its payload as JSON on STDIN. CLAUDE_TOOL_INPUT,
# CLAUDE_TOOL_USE_RESULT and CLAUDE_SESSION_ID are a LEGACY contract that current
# CC does not set, so a hook reading one sees an EMPTY value, falls through its
# first guard clause and exits 0 -- byte-identical to deciding "this is fine".
# That is F79's exact shape one interface over: a guard that FAILS OPEN and
# reports success. Reported 2026-09-06 by an outside project (Genesis) that had
# FOUR guards dead this way for weeks while their tests passed, because the tests
# fed the dead env var too -- the tests and the code agreed with each other and
# both disagreed with reality.
#
# MEASURED here 2026-09-07: zero hits, so this pins a property that currently
# HOLDS rather than fixing a live defect. The mutation gate cannot reach this
# class -- a dead input read means the hook never blocks, so there is no
# behaviour for a mutant to weaken -- which is why it is static and why it lives
# here rather than in test_hook_facts.py (verify-hook-tests.py copies only
# hook_facts.py and test_hook_facts.py into a temp dir, so a __file__-relative
# glob there would sweep an EMPTY population and report green).
#
# TEST files are scanned deliberately: a hit in test code not mirrored by a
# real-payload test is exactly where Genesis's bug hid.
_DEAD_INPUT_VARS='CLAUDE_TOOL_INPUT|CLAUDE_TOOL_USE_RESULT|CLAUDE_SESSION_ID'
_dead_input_reads(){
  grep -nE "^[^#]*($_DEAD_INPUT_VARS)" "$1"/*.sh "$1"/*.py 2>/dev/null
}
_dead_input_scanned(){ ls "$1"/*.sh "$1"/*.py 2>/dev/null | wc -l; }

_ds="$(_dead_input_scanned "$HOOKS")"
if [ "$_ds" -lt 8 ]; then
  no "the dead-payload-env scan covered only $_ds file(s) -- a guard over nothing passes forever"
elif [ -n "$(_dead_input_reads "$HOOKS")" ]; then
  no "a hook reads a DEAD payload env var -- CC does not set these, so the read is empty and the hook FAILS OPEN: $(_dead_input_reads "$HOOKS")"
else
  ok "no hook reads a dead payload env var ($_ds files scanned)"
fi

# The twins. A static grep that cannot be shown to FIRE is decoration, and the
# negative twin alone proves nothing -- an ABSENT detector also matches nothing.
_deadenv_fixture(){
  _d="$SBX_TMP/deadenv_$1"; mkdir -p "$_d"; printf '%s\n' "$2" > "$_d/probe.sh"; printf '%s' "$_d"
}
_dp="$(_deadenv_fixture pos 'INPUT="$CLAUDE_TOOL_INPUT"')"
if [ -n "$(_dead_input_reads "$_dp" 2>/dev/null)" ]; then
  ok "the dead-payload-env detector FIRES on a planted hook"
else
  no "the dead-payload-env detector did NOT fire on a planted hook -- it cannot go red"
fi
_dn="$(_deadenv_fixture neg '# CLAUDE_TOOL_INPUT is dead -- never read it')"
if [ -z "$(_dead_input_reads "$_dn" 2>/dev/null)" ]; then
  ok "a COMMENT naming the dead env var does not trip the detector"
else
  no "the detector matched a COMMENT -- prose must not satisfy the rule"
fi


# A hook whose PARSER fails must not read as ALLOW either. MEASURED 2026-08-30
# (code-review probe): with JQ pointing at a missing binary, protect-bash.sh
# exited 0 with EMPTY stdout -- the jq call failed, $COMMAND came back empty, and
# `[ -z "$COMMAND" ] && exit 0` treated "could not parse" as "nothing to check".
# Same shape as the stdin contract, one layer in.
AP=$(printf '\047')
Q=$(printf '\042')
# ONE backslash. `printf '\\\\'` emits TWO (single quotes keep both pairs) and the
# probe below then builds a validly-escaped command instead of a malformed one --
# which is how this case silently passed as 'allow' while claiming to test a refusal.
BSL=$(printf '\\')
X4_GAME_BS="${X4_GAME//\//\\}"
echo; echo "=== the command must PARSE, and bash is the judge ==="
# A guard whose helpers are all quote-aware fails OPEN on one unbalanced quote: the rest
# of the command reads as quoted text, no rule sees it, and every rule returns false --
# which is indistinguishable from "this is fine". MEASURED 2026-09-01 against c400a05:
# one apostrophe in an English comment turned 5 of 5 refusals into a silent allow.
#
# The hand-rolled scanner that first fixed it was wrong BOTH ways over 13,203 real
# commands -- 13 false positives (command substitution inside double quotes resets the
# quoting context) and 6 false negatives. `bash -n` parses, executes nothing, costs
# ~14 ms, and needs no dependency this hook does not already have.
#
# The four ALLOW cases are the ones the hand-rolled version got wrong; they are the
# point of the exercise, not padding.
decide allow protect-bash.sh "$(cj "echo hello")"                      "a balanced command parses"
decide allow protect-bash.sh "$(cj "# it${AP}s fine
echo hi")"                                                             "an apostrophe in a COMMENT is prose, not a quote"
decide allow protect-bash.sh "$(cj "cat > f <<${AP}X${AP}
it${AP}s data
X")"                                                                   "an apostrophe in a HEREDOC BODY is prose"
decide allow protect-bash.sh "$(cj "echo ${Q}a \$(grep -o ${AP}x|y${AP} f) b${Q}")"   "nested quotes inside \$( ) still parse"
decide deny  protect-bash.sh "$(cj "echo ${AP}unterminated")"          "a genuinely unbalanced quote is REFUSED, not ignored"
decide deny  protect-bash.sh "$(cj "ls ${Q}C:${BSL}Users${BSL}x${BSL}${Q} 2>/dev/null")"  "a Windows path ending in a backslash is REFUSED"

# The most natural way a Windows user writes a path must still reach every rule: inside
# double quotes bash keeps a backslash literal unless it precedes $ ` " \ or newline.
# Unescaping unconditionally deleted the separators, so the game-delete HARD BLOCK fired
# NOTHING on a backslash path -- c400a05 denied it, the parse pass allowed it.
decide deny  protect-bash.sh "$(cj "rm -rf ${Q}${X4_GAME_BS}${Q}")"    "a BACKSLASH game path still hits the hard block"

echo; echo "=== parser contract ==="
# --- STATIC: no personal identifier may reach a tracked file -----------------
# This repo already SHIPPED scripts/scan-identifiers.py and it was never wired to
# anything, so nothing made it run. MEASURED 2026-08-31: a hook test fixture carried the
# author's Windows username in four paths plus their 8-digit game-profile id, committed
# (never pushed). Fed the pre-fix content the scanner fires on all four lines -- so the
# guard was correct the whole time and the only gap was that running it depended on
# remembering to. That is the third instance in one day of a correct tool going unrun.
#
# Its own selftest runs first: a scan that cannot be shown to fail is not evidence that
# the tree is clean, it is evidence that something printed the word "clean".
# ...and a selftest that could not RUN has not FAILED: with the caller's own TMP too
# long to spawn safely the scanner refuses before the selftest (rc 2), and that was
# reported here as "selftest FAILED". Classified by the shared helper, same rule as
# the scan below: a failure needs the selftest's own summary as evidence.
. "$REPO/scripts/_scan-classify.sh"
_st_out="$(python "$REPO/scripts/scan-identifiers.py" --selftest 2>&1)"
_st_rc=$?
case "$(classify_selftest_result "$_st_rc" "$_st_out")" in
  pass) ok "the identifier scanner's own selftest passes" ;;
  fail) no "scan-identifiers.py --selftest FAILED; its verdict on the tree means nothing" ;;
  *) skip "the identifier scanner's selftest COULD NOT RUN (rc $_st_rc): $(printf '%s' "$_st_out" | grep -m1 . | cut -c1-200)" ;;
esac
# rc 1 (found something) and rc 2 (COULD NOT RUN) are different facts, and reporting the
# second as the first is the error this whole suite exists to refuse. MEASURED
# 2026-09-01: in a `git archive` extract there is no .git, the scanner correctly returns
# 2 -- "cannot run the identifier scan" -- and this probe announced "a personal
# identifier reached a tracked file". Exactly the verify-a-release-tarball case.
# The classification lives in one shared helper (scripts/_scan-classify.sh) so
# this call site and its test cannot silently disagree. rc 1 alone is NOT proof of
# a leak: a hung scan-identifiers.py (the TMP-length bug SBX_TMP above works
# around) killed with taskkill also exits 1 with no output at all, and that must
# not read as "a personal identifier reached a tracked file". See the helper's
# header for the measured taskkill case.
_scan_out="$(python "$REPO/scripts/scan-identifiers.py" 2>&1)"
_scan_rc=$?
case "$(classify_scan_result "$_scan_rc" "$_scan_out")" in
  clean) ok "no personal identifier in any tracked file" ;;
  leak)  no "a personal identifier reached a tracked file -- run scripts/scan-identifiers.py" ;;
  *) skip "the identifier scan did not confirm a finding (rc $_scan_rc, e.g. no .git in a tarball, a scanner crash, or a killed/hung process) -- not a clean result, but not a finding either" ;;
esac

# --- STATIC: no hook may use a bash-4-only feature ---------------------------
# macOS ships bash 3.2 as /bin/bash. `declare -A` there is not a syntax error that stops
# the script -- it fails, then `F[key]=v` becomes an ARITHMETIC index into an ordinary
# array, every predicate collapses into one slot, and the hook ALLOWS EVERYTHING while
# looking healthy. This machine cannot run bash 3.2, so a runtime probe is impossible and
# a static check is the only reachable one. Comments are excluded so the entry
# explaining the trap does not trip it.
_b4=$(grep -nE '^[^#]*(declare[[:space:]]+-A|\$\{[A-Za-z_][A-Za-z0-9_]*(,,|\^\^)\}|mapfile|readarray)' \
        "$HOOKS"/*.sh 2>/dev/null)
if [ -z "$_b4" ]; then ok "no hook uses a bash-4-only feature (macOS /bin/bash is 3.2)"
else no "a hook uses a bash-4-only feature -- on macOS the guard would die SILENTLY: $_b4"; fi

# CONTRACT CHANGED, and it is now STRONGER. protect-bash.sh no longer PARSES with jq --
# hook_facts.py does that -- so jq is only the EMITTER. That made a broken jq far more
# dangerous than before: every deny became empty stdout, which the client reads as
# ALLOW. So there is now a Python emitter fallback, and the assertion is that a broken
# jq still produces the CORRECT VERDICT rather than degrading to a prompt.
# The probe also needs a command that actually triggers a rule: the old one used a
# delete of "/" which matches no configured root, so it could only ever have tested the
# refusal path, never the verdict path.
out=$(printf '%s' "$(cj 'git add -A')" | JQ=no_such_jq_binary bash "$HOOKS/protect-bash.sh" 2>/dev/null)
got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null)
[ -z "$got" ] && got="allow"
if [ "$got" = "deny" ]; then ok "a broken jq still emits the verdict (python fallback)"
else no "protect-bash.sh -- with jq broken the deny came out as '$got'; silence IS allow"; fi

# And a missing PYTHON must ASK: the parse pass is the only thing that inspects the
# command now, so without it the hook has checked nothing at all.
# X4_PYTHON points at a binary that does NOT run. Emptying PATH instead would break
# `dirname` and `cat` as well, so the hook would fail before reaching the python check
# and the probe would be testing nothing -- a green with no reachable failure branch.
out=$(printf '%s' "$(cj 'git add -A')" | X4_PYTHON="$SBX_TMP/no_such_python" bash "$HOOKS/protect-bash.sh" 2>/dev/null)
got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null)
[ -z "$got" ] && got="allow"
if [ "$got" = "ask" ]; then ok "protect-bash.sh asks when no Python is available"
else no "protect-bash.sh -- with no Python the command read as '$got'; unchecked IS allow"; fi

# =============================================================================
# AN ADVISORY MUST NOT SUPPRESS A LATER REFUSAL
# =============================================================================
# An `advise` is an ALLOW that carries a note; a `deny` is a refusal. Emitting the
# note and EXITING made every rule below the advisory unreachable.
# MEASURED 2026-08-30 by neutralising the six advisory/ask rules and replaying the
# 1,846 commands they catch: 298 are refused by a rule further down -- 130
# TIMEOUT-above-the-cap, 64 /tmp, 35 durable open(,'w'), 27 dollar-question-mark
# after a pipeline, 26 profile-content.xml-by-name, 11 stage-everything. Each was
# measured GENUINE, and each was silently suppressed by a note.
echo; echo "=== an advisory never masks a later verdict ==="
_GA="git ""add -A"
decide deny   protect-bash.sh "$(cj "cd $GAME && $_GA > $GAME/build.log")"   "a redirect advisory does not hide the stage-everything refusal"
decide deny   protect-bash.sh "$(cj "grep -rn foo $X4_REFERENCE > $GAME/out.txt")"   "a redirect advisory does not hide the reference-tree refusal"
# NB the fixture roots live under /tmp, so a redirect INTO the fixture game dir is
# also a write to /tmp and is refused for that -- correctly. These two use a
# relative redirect target so the advisories can be observed in isolation.
decide advise protect-bash.sh "$(cj "cp a '$GAME/b'")"   "one advisory still advises"
decide advise protect-bash.sh "$(cj "cp a '$GAME/b' > log.txt")"   "two advisories still advise"
# ...and BOTH texts must survive into the single note, not just the last one.
# Uses a FAKE game root outside /tmp: the fixture roots live under /tmp, so a real
# redirect into the fixture game dir is also a shared-/tmp write and is refused for
# that instead -- correctly, but it would stop this probe reaching the advisories.
_sg2="$X4_GAME"; export X4_GAME="C:/fixture/game"
_out=$(printf '%s' "$(cj 'cp a "C:/fixture/game/b" > "C:/fixture/game/log.txt"')" | bash "$HOOKS/protect-bash.sh" 2>/dev/null)
_n=$(printf '%s' "$_out" | jq -r '.hookSpecificOutput.additionalContext // ""' | grep -c .)
if [ "$_n" = "2" ]; then ok "both advisories are carried in ONE note"
else no "advisory accumulation -- expected 2 lines in additionalContext, got $_n"; fi
export X4_GAME="$_sg2"
decide deny   protect-bash.sh "$(cj "rm -rf '$GAME'")"   "a hard block still wins over everything"

# --- The 10,000-character model-output cap -----------------------------------
# MEASURED 2026-09-07 on CC 2.1.263, this machine. Claude Code FILES a hook's
# model-facing output above a threshold and shows the model a ~2 KB preview:
# no error, exit code unchanged, and the failure is indistinguishable from
# success. Four arms per channel, head+tail sentinel, discriminating on whether
# the TAIL survives:
#
#   bare stdout        500 -> BOTH   10,000 -> BOTH   10,001 -> HEAD   30,000 -> HEAD
#   additionalContext  500 -> BOTH    9,950 -> BOTH   10,001 -> HEAD   30,000 -> HEAD
#
# The 9,950 arm is the load-bearing one: its total stdout was 10,032 characters
# and it still arrived whole, so the cap is on the CONTENT the model receives and
# the JSON envelope does NOT count against it. Budgeting "10,000 minus envelope"
# would be needlessly tight and would rot as the envelope changes.
#
# The number has already MOVED once (high-20s K on 2.1.218 -> 10,000 on 2.1.246,
# still 10,000 on 2.1.263), so re-deriving it is a named step on a CC bump, not a
# guess. Re-derive with the probe shape above and a small-size control arm.
_cap_ctx(){ printf '%s' "$1" | "${JQ:-jq}" -r '.hookSpecificOutput.additionalContext'; }
# THE CONSTANT, NOT A COPY OF ITS VALUE. Six assertions below hardcoded 10000
# while _x4-env.sh says of that number: "This is the ONLY place the number is
# written down; a second copy would drift, and a wrong constant here fails
# silently in the safe-looking direction."
#
# The failure direction is the unsafe one: if a CC bump LOWERS the real cap and
# X4_HOOK_MAX_CHARS is re-derived per the documented procedure, every bounded
# payload comes out ABOVE the new cap and is silently filed -- while all six
# assertions still pass at <= 10000, green over the exact failure the feature
# exists to prevent.
#
# Derived in ONE subshell rather than by sourcing _x4-env.sh here: every probe
# in this file sources it inside `( ... )`, so the variable never reaches this
# scope -- the first version of this fix used it bare and died on `unbound
# variable`, which is the difference between reading that it is in scope and
# checking.
CAP="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; printf %s "$X4_HOOK_MAX_CHARS" ) )"
# REFUSE rather than compare against an empty string: `[ N -le "" ]` is a syntax
# error in some shells and a silent pass in others, and either way six probes
# would stop asserting what they claim to.
case "$CAP" in ""|*[!0-9]*) printf 'FATAL: could not derive X4_HOOK_MAX_CHARS (%s); the six cap probes below would assert nothing.
' "$CAP" >&2; exit 2 ;; esac

_cap_env(){ ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; x4_advise "$1" PreToolUse ); }

_long="$(printf 'y%.0s' $(seq 1 25000))"
_short="a short advisory"

_got="$(_cap_ctx "$(_cap_env "$_long")")"
if [ "${#_got}" -le "$CAP" ]; then
  ok "a 25,000-char advisory is bounded to the cap (got ${#_got})"
else
  no "a 25,000-char advisory came back at ${#_got} chars -- above the cap, so CC files it and the model sees a preview"
fi

case "$_got" in
  *TRUNCATED*) ok "the bounded advisory ANNOUNCES that it was truncated" ;;
  *) no "the advisory was cut with no notice -- a narrowing step must announce it" ;;
esac

_gots="$(_cap_ctx "$(_cap_env "$_short")")"
if [ "$_gots" = "$_short" ]; then
  ok "a short advisory is passed through UNCHANGED (the cap does not fire spuriously)"
else
  no "a short advisory was altered: $_gots"
fi

# Both renderers must bound. The python fallback exists precisely for a machine
# with no jq, and a cap that lives in only one of them is a cap that is absent
# exactly when the fallback is doing the work.
_gotp="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; JQ=no_such_jq_binary x4_advise "$_long" PreToolUse ) \
          | "${JQ:-jq}" -r '.hookSpecificOutput.additionalContext' )"
if [ "${#_gotp}" -le "$CAP" ]; then
  ok "the PYTHON renderer bounds too (got ${#_gotp})"
else
  no "with jq unavailable the advisory came back at ${#_gotp} chars -- the fallback does not bound"
fi

if [ "$_got" = "$_gotp" ]; then
  ok "both renderers produce the SAME bounded text (one home for the number)"
else
  no "the jq and python renderers DISAGREE on the bound -- the constant has drifted between them"
fi

# --- session-canary.sh feeds a BARE-STDOUT channel with a hard ceiling --------
# This hook does not go through x4_advise, so the cap does not reach it for free.
# Driven with a stub canary rather than the real one: the real repos are clean,
# and a test that can only run when something is actually lost is a test that
# never runs.
_sc_tk="$SBX_TMP/sc_toolkit"; mkdir -p "$_sc_tk/scripts"
cat > "$_sc_tk/scripts/x4canary.py" <<'CANARY_STUB'
import sys
sys.stderr.write("STUBHEAD\n")
for i in range(400):
    sys.stderr.write("   repo :: some/reasonably/long/path/to/file_%04d.md: "
                     "EMPTIED (400 bytes -> 0)\n" % i)
sys.stderr.write("STUBTAIL\n")
raise SystemExit(1)
CANARY_STUB
_sc_out="$(X4_TOOLKIT="$_sc_tk" bash "$HOOKS/session-canary.sh" </dev/null 2>/dev/null)"

case "$_sc_out" in
  *x4canary.py*) ok "session-canary names WHICH x4canary it resolved" ;;
  *) no "session-canary does not say which x4canary it ran -- two copies exist and the divergence is invisible" ;;
esac

if [ "${#_sc_out}" -le "$CAP" ]; then
  ok "a huge canary report is bounded by the hook (${#_sc_out} chars)"
else
  no "the canary report reached ${#_sc_out} chars -- above the cap, so CC files the DATA LOSS warning and the session sees a preview"
fi

case "$_sc_out" in
  *TRUNCATED*) ok "the bounded canary report ANNOUNCES the truncation" ;;
  *) no "the canary report was cut with no notice" ;;
esac

# --- the cap must not be defeatable ------------------------------------------
# Three ways it was, all found by review and all reproduced before fixing.
_bnd(){ ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; x4_bound "$1" ); }

# (a) A python that RESOLVES but FAILS. x4_len returned 0 regardless of the arm
# status, so the length came back empty, x4_bound took its non-numeric guard and
# passed the text through WHOLE -- the fail-open shape the function exists to
# close, inside the function that closes it.
_stub="$SBX_TMP/pystub"; mkdir -p "$_stub"
printf '%s\n' '#!/bin/bash' 'exit 9' > "$_stub/python"; chmod +x "$_stub/python"
_big="$(printf 'z%.0s' $(seq 1 25000))"
_got="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; X4_PYTHON="$_stub/python" x4_bound "$_big" ) )"
if [ "${#_got}" -le "$CAP" ]; then
  ok "a python that resolves but FAILS still bounds (${#_got} chars)"
else
  no "with a failing python the cap is ABSENT: ${#_got} chars passed through unbounded"
fi

# (b) A cap BELOW the notice reserve made _keep negative, and a negative slice
# keeps almost everything -- so the "bounded" result came back LARGER than the
# cap it was asked for. The knob is documented as overridable and the constant is
# expected to be re-derived on a CC bump, so a lower ceiling is the realistic path in.
_small="$(printf 'w%.0s' $(seq 1 5000))"
_gs="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; X4_HOOK_MAX_CHARS=200 x4_bound "$_small" ) )"
if [ "${#_gs}" -le 200 ]; then
  ok "a cap below the notice reserve still bounds (${#_gs} <= 200)"
else
  no "cap=200 returned ${#_gs} chars -- a negative slice kept almost everything"
fi

# (c) THE VERDICT EMITTER. x4_advise was bounded and emit() was not, so deny/ask
# reasons -- the highest-stakes model-facing channel there is -- stayed unbounded
# while interpolating the whole command. The verdict itself is honoured at any
# length (measured: a deny blocks at 200,000 chars), but the REASON is what tells
# the reader WHY, and a filed reason is a preview.
#
# The probe must PARSE. A first attempt used a path with enough slashes to be
# rejected by `bash -n`, so the parse-refusal rule fired with its own fixed
# reason and the test passed on 266 characters -- a vacuous pass that looked
# like a bound working.
# Uses the SUITE fixture root, not a real profile path: run_layout exports
# X4_SAVES at a sandbox location, so a real path reaches no rule here and the
# probe would refuse (correctly) instead of exercising the bound.
_savep="${X4_PROFILE:-$SBX_TMP/profile}/$(printf 'bbbbbbbbbb/%.0s' $(seq 1 900))config.ini"
_vr="$(printf '{"tool_name":"Bash","tool_input":{"command":%s}}' "$(printf 'rm -f %s' "$_savep" | jq -Rs .)" \
       | bash "$HOOKS/protect-bash.sh" 2>/dev/null | jq -r '.hookSpecificOutput.permissionDecisionReason // ""')"
if [ "${#_vr}" -lt 200 ]; then
  no "the verdict probe produced only ${#_vr} chars -- it did not reach a command-interpolating rule, so this proves nothing"
elif [ "${#_vr}" -le "$CAP" ]; then
  ok "a verdict reason is bounded too (${#_vr} chars)"
else
  no "a verdict reason came back at ${#_vr} chars -- emit() never routes through x4_bound"
fi

# --- the cap must not COST anything on the common path -----------------------
# MEASURED by review: x4_bound spawned python unconditionally, so every verdict
# paid it even for a 57-character reason -- +117 ms, +32% per verdict. That is a
# smaller instance of the 11.3x latency regression this file's own header records
# as the class it was rewritten to remove, reintroduced by the fix for it.
#
# Probed by MARKER FILE rather than by timing: "did it spawn" is a fact, a
# duration is a measurement, and a timing assertion in a suite is a flake waiting
# to happen.
_sp="$SBX_TMP/spawnprobe"; mkdir -p "$_sp"
printf '%s\n' '#!/bin/bash' "touch \"$_sp/SPAWNED\"" 'exit 0' > "$_sp/python"; chmod +x "$_sp/python"

rm -f "$_sp/SPAWNED"
_short_out="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; X4_PYTHON="$_sp/python" x4_bound "a short advisory" ) )"
if [ -e "$_sp/SPAWNED" ]; then
  no "x4_bound spawned an interpreter for a 16-character payload -- every verdict pays that"
else
  ok "a payload obviously under the cap costs no interpreter spawn"
fi
if [ "$_short_out" = "a short advisory" ]; then
  ok "and the short payload is returned unchanged"
else
  no "the short-circuit altered a short payload: $_short_out"
fi

# The twin: the short-circuit must NOT swallow a payload that really is over the
# cap. A pre-check that skips too much is the same defect pointing the other way.
rm -f "$_sp/SPAWNED"
_big2="$(printf 'k%.0s' $(seq 1 25000))"
_big_out="$( ( HOOK_DIR="$HOOKS"; . "$HOOKS/_x4-env.sh"; x4_bound "$_big2" ) )"
if [ "${#_big_out}" -le "$CAP" ]; then
  ok "an over-cap payload is still bounded (${#_big_out} chars)"
else
  no "the short-circuit let an over-cap payload through: ${#_big_out} chars"
fi


# =============================================================================
# THE PATH CONFIG IS DATA (v4.0 release review R1-F1 / R1-P1)
# =============================================================================
# _x4-env.sh SOURCED x4-paths.env, and an agent can write it: a line `exit 0` made every hook
# exit before it spoke (empty stdout = ALLOW), and `X4_GUARD=off` in it relaxed every deny
# under a banner that said "at launch". It is parsed now, and agents may not write it.
echo; echo "=== the path config is DATA, never run ==="
_cfg_saved="$(export -p | grep -E '^declare -x (X4_[A-Z0-9_]*|CLAUDE_PROJECT_DIR)=')"
# X4_DECIDE_DUMP is the HARNESS's own switch, not a root: unsetting it with the rest silently
# kept every probe in this section out of the Codex conformance replay (reviewer E8, MEASURED:
# 16 config-write denies never reached the dump).
for _v in $(export -p | sed -nE 's/^declare -x (X4_[A-Z0-9_]*|CLAUDE_PROJECT_DIR)=.*/\1/p'); do
  [ "$_v" = X4_DECIDE_DUMP ] || unset "$_v"
done
_ct="$SBX_TMP/cfgtk"; mkdir -p "$_ct/reference/libraries" "$_ct/ref2/libraries" "$_ct/.claude"
# Lines AFTER an `exit 0` are still read: ref2 is configured below it and must be protected.
# Its OWN toolkit folder, whose config is never rewritten (FX-G5): the conformance replay re-runs
# each row against the kept sandbox, and rewriting a shared config below made these two rows
# replay as ALLOW (MEASURED: DRIFT #153/#154 in test_codex_conformance).
_ce="$SBX_TMP/cfgtk-exit0"; mkdir -p "$_ce/ref2/libraries"
export X4_TOOLKIT="$_ce" CLAUDE_PROJECT_DIR="$_ce"
printf '%s\n' '# cfg' 'exit 0' "X4_REFERENCE=\"$_ce/ref2\"" > "$_ce/x4-paths.env"
decide deny protect-files.sh "$(fj "$_ce/ref2/libraries/w.xml")" "an 'exit 0' line in the config no longer silences the guards"
decide deny protect-bash.sh  "$(cj "echo x > '$_ce/ref2/libraries/w.xml'")" "an 'exit 0' line: protect-bash still reads the roots after it"
export X4_TOOLKIT="$_ct" CLAUDE_PROJECT_DIR="$_ct"
# X4_GUARD=off in the FILE is ignored ...
printf '%s\n' 'X4_GUARD=off' > "$_ct/x4-paths.env"
decide deny protect-files.sh "$(fj "$_ct/reference/libraries/w.xml")" "X4_GUARD=off in the config does NOT switch the guards off"
_ban="$(bash "$HOOKS/session-canary.sh" 2>/dev/null)"
case "$_ban" in
  *"X4_GUARD in "*IGNORED*) ok "the session banner names the ignored config X4_GUARD" ;;
  *) no "the session banner does not name the ignored config X4_GUARD: ${_ban:0:200}" ;;
esac
# ... while the LAUNCH environment's still does (the twin: a loader that dropped X4_GUARD
# everywhere would pass the probe above).
export X4_GUARD=off
decide advise protect-files.sh "$(fj "$_ct/reference/libraries/w.xml")" "TWIN: X4_GUARD=off at LAUNCH still relaxes the deny"
unset X4_GUARD
: > "$_ct/x4-paths.env"
# Agents may not write the config, in either location, by Edit/Write or by a shell command.
decide deny  protect-files.sh "$(fj "$_ct/x4-paths.env")"          "Write the path config: denied"
decide deny  protect-files.sh "$(fj "$_ct/.claude/x4-paths.env")"  "Write the 3.x path config: denied"
decide deny  protect-files.sh "$(fj "$(printf '%s' "$_ct/X4-PATHS.ENV" | tr / '\\')")" "Write the path config, other case + backslashes: denied"
decide allow protect-files.sh "$(fj "$_ct/x4-paths.env.example")"  "TWIN: the .example beside it is not the config"
decide allow protect-files.sh "$(fj "$_ct/dev/x4-paths.env")"      "TWIN: a same-named file elsewhere is not the config"
decide deny  protect-bash.sh "$(cj "echo X4_GUARD=off >> '$_ct/x4-paths.env'")"  "append to the path config: denied"
decide deny  protect-bash.sh "$(cj "cd '$_ct' && cp x4-paths.env.example x4-paths.env")" "cp onto the path config (relative): denied"
decide deny  protect-bash.sh "$(cj "rm -f '$_ct/.claude/x4-paths.env'")" "delete the 3.x path config: denied"
decide allow protect-bash.sh "$(cj "cat '$_ct/x4-paths.env'")"     "TWIN: READING the path config is allowed"
decide allow protect-bash.sh "$(cj "echo x >> '$_ct/notes-x4-paths.txt'")" "TWIN: a write to a file merely NAMED like it is allowed"
# FX-G2 item 1 (v4.0.0 delta review): the config pass sent the config ALONE, so a ROOT VARIABLE
# was never substituted -- every spelling below was ALLOWED (and the rm only advised).
decide deny  protect-bash.sh "$(cj "echo X4_REFERENCE=/x > \"\$X4_TOOLKIT/x4-paths.env\"")" "redirect via \$X4_TOOLKIT onto the config: denied"
decide deny  protect-bash.sh "$(cj "cp /dev/null \"\${X4_TOOLKIT}/x4-paths.env\"")" "cp via \${X4_TOOLKIT} onto the config: denied"
decide deny  protect-bash.sh "$(cj "cd \"\$X4_TOOLKIT\" && echo a > x4-paths.env")" "cd \$X4_TOOLKIT, then a relative redirect onto the config: denied"
decide deny  protect-bash.sh "$(cj "rm \"\$X4_TOOLKIT/x4-paths.env\"")" "rm via \$X4_TOOLKIT of the config: denied (was an advisory)"
decide allow protect-bash.sh "$(cj "echo a > \"\$X4_TOOLKIT/x4-paths.env.example\"")" "TWIN: via the variable, the .example beside it is not the config"
export X4_CONFIG="$_ct/elsewhere.env"; : > "$X4_CONFIG"
decide deny  protect-files.sh "$(fj "$X4_CONFIG")"                 "Write an explicit X4_CONFIG file: denied"
decide deny  protect-bash.sh "$(cj "echo x > '$X4_CONFIG'")"       "redirect into an explicit X4_CONFIG file: denied"
decide deny  protect-bash.sh "$(cj "echo x > \"\$X4_CONFIG\"")"    "redirect into \$X4_CONFIG, by the variable: denied (FX-G2)"
unset X4_CONFIG X4_TOOLKIT CLAUDE_PROJECT_DIR
eval "$_cfg_saved"

# =============================================================================
# WINDOWS PATH ALIASES (FX-G2 item 3, v4.0.0 delta review)
# =============================================================================
# Windows opens `x.`, `x `, `x::$DATA` and `dir./x` as the real file, and an 8.3 short name as
# its long name. MEASURED 2026-10-05: Python wrote INTO x4-paths.env through the first three,
# and 11 of 17 protect-files probes (19 of 28 overall) were ALLOWED.
echo; echo "=== Windows path aliases: trailing dot/space, stream suffix, 8.3 short name ==="
_wa_saved="$(export -p | grep -E '^declare -x (X4_[A-Z0-9_]*|CLAUDE_PROJECT_DIR)=')"
_wt="$SBX_TMP/aliastk"; mkdir -p "$_wt/reference/libraries" "$_wt/A Long Game Folder Name/extensions"
: > "$_wt/x4-paths.env"
export X4_TOOLKIT="$_wt" X4_REFERENCE="$_wt/reference" X4_GAME="$_wt/A Long Game Folder Name" \
       X4_CONFIG="$_wt/x4-paths.env" CLAUDE_PROJECT_DIR="$_wt"
decide deny  protect-files.sh "$(fj "$_wt/x4-paths.env.")"                  "Write the config + a trailing dot: denied"
decide deny  protect-files.sh "$(fj "$_wt/x4-paths.env ")"                  "Write the config + a trailing space: denied"
decide deny  protect-files.sh "$(fj "$_wt/x4-paths.env::\$DATA")"           "Write the config's ::\$DATA stream: denied"
decide deny  protect-files.sh "$(fj "$_wt/reference./libraries/w.xml")"     "Write reference./ (a trailing dot on the DIR): denied"
decide deny  protect-files.sh "$(fj "$_wt/reference::\$INDEX_ALLOCATION/libraries/w.xml")" "Write via the dir's ::\$INDEX_ALLOCATION: denied"
decide deny  protect-files.sh "$(fj "$X4_GAME./libraries/a.xml")"           "Write the game dir + a trailing dot: denied"
decide allow protect-files.sh "$(fj "$_wt/reference..old/w.xml")"           "TWIN: dots INSIDE a name are another folder"
decide allow protect-files.sh "$(fj "$_wt/x4-paths.env.example")"           "TWIN: the .example is still not the config"
decide deny  protect-bash.sh  "$(cj "echo x > '$_wt/reference./libraries/w.xml'")" "Bash: a redirect into reference./: denied"
decide deny  protect-bash.sh  "$(cj "echo x > '$_wt/x4-paths.env.'")"       "Bash: a redirect onto the config + a dot: denied"
decide allow protect-bash.sh  "$(cj "echo x > '$_wt/reference..old/w.xml'")" "TWIN: Bash, dots INSIDE a name"
_ws="$(cygpath -m -s "$X4_GAME" 2>/dev/null)"
case "$_ws" in
  *~[0-9]*)
    decide deny protect-files.sh "$(fj "$_ws/libraries/a.xml")"  "Write the game dir by its 8.3 SHORT name: denied"
    decide deny protect-bash.sh  "$(cj "rm -rf '$_ws'")"         "Bash: delete the game dir by its 8.3 SHORT name: denied" ;;
  *) skip "8.3 short name (no cygpath, or 8.3 names disabled on this volume): files"
     skip "8.3 short name (no cygpath, or 8.3 names disabled on this volume): bash" ;;
esac
unset X4_CONFIG X4_TOOLKIT X4_REFERENCE X4_GAME CLAUDE_PROJECT_DIR
eval "$_wa_saved"


# =============================================================================
# X4_GUARD=off relaxes VERDICTS, never an INABILITY (v4.0 release review R1-F2)
# =============================================================================
# The CHANGELOG promises "a guard that could not check still asks" under X4_GUARD=off; every
# inability verdict went through the same emitter as a rule's deny and became an advisory.
echo; echo "=== X4_GUARD=off: a guard that could not check still asks ==="
_ig_game="${X4_GAME-}"; export X4_GUARD=off X4_GAME="$SBX_TMP/g/X4 Foundations"; mkdir -p "$X4_GAME"
decide ask protect-bash.sh "$(cj 'echo "unterminated')"   "guards off: a command bash -n rejects still ASKS"
decide ask protect-bash.sh "$(cj "\$(echo rm) -rf '$X4_GAME'")"     "guards off: a command name built by substitution still ASKS"
_psj='{"tool_name":"PowerShell","tool_input":{"command":"Remove-Item x"}}'
export X4_PWSH="$SBX_TMP/no-such-pwsh"
decide deny protect-bash.sh "$_psj" "guards off: a failed PowerShell scope evaluation still REFUSES"
unset X4_PWSH
_pf_out="$(printf '%s' '{"tool_name":"Edit","tool_input":' | JQ=no-such-jq bash "$HOOKS/protect-files.sh" 2>/dev/null)"
case "$_pf_out" in
  *'"permissionDecision":"deny"'*|*'"permissionDecision": "deny"'*) ok "guards off: protect-files with an unreadable payload still REFUSES" ;;
  *) no "guards off: protect-files with an unreadable payload did not refuse: ${_pf_out:0:200}" ;;
esac
# TWIN: an ordinary rule's deny IS relaxed -- the switch still works for verdicts.
decide advise protect-bash.sh "$(cj "rm -rf '$X4_GAME'")" "TWIN: guards off: a rule's deny is still an advisory"
unset X4_GUARD; if [ -n "$_ig_game" ]; then export X4_GAME="$_ig_game"; else unset X4_GAME; fi

# =============================================================================
# search-scope.sh: an ignored ROOT is not an invisible TREE (v4.0 release review R1-F4, R1-F5)
# =============================================================================
# Grep (ripgrep) given a root that git ignores as a DIRECTORY (`reference/`, `build/`) still
# reads every file under it -- only a pattern that matches the CHILDREN themselves (the game
# root's `*`) hides them. MEASURED 2026-10-04: rg --files under `reference/` -> 1 of 1 files,
# under `*` -> 0 of 1. The deny fired on both, so the toolkit's own reference/libraries could
# not be searched with Grep in the default layout.
echo; echo "=== search-scope.sh: a dir/-ignored root is still searchable ==="
_ss_gc="${GIT_CEILING_DIRECTORIES-}"; export GIT_CEILING_DIRECTORIES="$_SBX"
_ss_ext="${X4_EXTENSIONS-}"; _ss_game="${X4_GAME-}"
DTK="$SBX_TMP/dtk"; mkdir -p "$DTK/reference/libraries" "$DTK/build/sub"
: > "$DTK/reference/libraries/a.xml"; : > "$DTK/build/sub/b.txt"
printf '%s\n' 'reference/' 'build/' > "$DTK/.gitignore"; git -C "$DTK" init -q
SG="$SBX_TMP/sgame/X4 Foundations"; mkdir -p "$SG/extensions/loosemod/md"
: > "$SG/extensions/loosemod/md/a.xml"; printf '%s\n' '*' '!.gitignore' > "$SG/.gitignore"; git -C "$SG" init -q
export X4_GAME="$SG" X4_EXTENSIONS="$SG/extensions"
ssj(){ printf '{"tool_name":"%s","tool_input":{"pattern":"x","path":%s}}' "$1" "$(printf '%s' "$2" | jq -Rs .)"; }
decide allow  search-scope.sh "$(ssj Grep "$DTK/reference/libraries")" "Grep under a dir/-ignored reference/ sees its files: no deny"
decide allow  search-scope.sh "$(ssj Grep "$DTK/build")"               "Grep AT a dir/-ignored root sees its files: no deny"
decide allow  search-scope.sh "$(ssj Glob "$DTK/build")"               "Glob AT a dir/-ignored root: no unreliable-zero advisory"
mkdir -p "$DTK/extra1/d"; : > "$DTK/extra1/d/c.txt"; printf '%s\n' 'extra*' >> "$DTK/.gitignore"
decide allow  search-scope.sh "$(ssj Grep "$DTK/extra1")"              "a pattern naming the ROOT (extra*) does not hide its children: no deny"
decide deny   search-scope.sh "$(ssj Grep "$SG/extensions/loosemod")"  "TWIN: a *-ignored root's children are invisible: still denied"
decide advise search-scope.sh "$(ssj Glob "$SG/extensions/loosemod")"  "TWIN: Glob in a *-ignored root: still advised"
export X4_GUARD=off
decide advise search-scope.sh "$(ssj Grep "$SG/extensions/loosemod")"  "guards off: search-scope's deny is an advisory too (R1-F5)"
unset X4_GUARD
if [ -n "$_ss_ext" ]; then export X4_EXTENSIONS="$_ss_ext"; else unset X4_EXTENSIONS; fi
if [ -n "$_ss_game" ]; then export X4_GAME="$_ss_game"; else unset X4_GAME; fi
if [ -n "$_ss_gc" ]; then export GIT_CEILING_DIRECTORIES="$_ss_gc"; else unset GIT_CEILING_DIRECTORIES; fi


# =============================================================================
# The X4 PROFILE with X4_PROFILE unset (v4.0 release review R1-F3)
# =============================================================================
# A delete or write in the profile asks -- but only through the configured X4_PROFILE root.
# Unconfigured, it fell to the Documents rule, which became an ADVISORY (2026-10-02): v3.3.1
# asked, v4.0 advised. protect-files.sh keeps an `Egosoft/X4/` NAME backstop for this; the
# Bash side now gets the same tree as a root, from Documents (Windows) or ~/.config (Linux).
echo; echo "=== protect-bash.sh: the X4 profile with X4_PROFILE unset ==="
_pp_prof="${X4_PROFILE-}"; _pp_docs="${X4_DOCUMENTS-}"; _pp_saves="${X4_SAVES-}"; _pp_home="$HOME"
unset X4_PROFILE X4_SAVES
export X4_DOCUMENTS="$SBX_TMP/pdocs"; mkdir -p "$X4_DOCUMENTS/Egosoft/X4/123/save" "$X4_DOCUMENTS/Other Game"
printf 'save fixture' > "$X4_DOCUMENTS/Egosoft/X4/123/save/s1.xml.gz"
decide advise protect-bash.sh "$(cj "rm -f '$X4_DOCUMENTS/Egosoft/X4/123/save/s1.xml.gz'")" "X4_PROFILE unset: a named save is preserved before allowance"
decide ask    protect-bash.sh "$(cj "echo x > '$X4_DOCUMENTS/Egosoft/X4/123/content.xml'")" "X4_PROFILE unset: writing the profile content.xml still ASKS"
decide advise protect-bash.sh "$(cj "rm -f '$X4_DOCUMENTS/Other Game/a.txt'")"            "TWIN: another game's file in Documents is only advised"
export X4_DOCUMENTS="$SBX_TMP/no-docs-here" HOME="$SBX_TMP/phome"; mkdir -p "$HOME/.config/EgoSoft/X4/123"
decide ask    protect-bash.sh "$(cj "rm -f '$HOME/.config/EgoSoft/X4/123/content.xml'")"   "X4_PROFILE unset, Linux layout: ~/.config/EgoSoft/X4 still ASKS"
export HOME="$_pp_home"
if [ -n "$_pp_docs" ]; then export X4_DOCUMENTS="$_pp_docs"; else unset X4_DOCUMENTS; fi
[ -n "$_pp_prof" ] && export X4_PROFILE="$_pp_prof"
[ -n "$_pp_saves" ] && export X4_SAVES="$_pp_saves"


# =============================================================================
# Refusals name AGENT-NEUTRAL remedies (v4.0 release review R3-1)
# =============================================================================
# The same protect-bash.sh judges Codex and OpenCode, and its reasons told every agent to "use
# the Edit tool" and to "see CLAUDE.md #22" -- a tool Codex lacks and an anchor that no longer
# exists in CLAUDE.md. Read the REASONS of four refusals that carried them.
echo; echo "=== refusals name agent-neutral remedies ==="
# PER REFUSAL (reviewer E, minor): the four reasons used to be joined and matched once, so one
# refusal that rendered nothing -- or lost its remedy -- hid behind the others' text. Each now
# must be a DENY, carry no Claude-only remedy, and name its OWN agent-neutral remedy.
_r31_one(){ # _r31_one <command> <the remedy its reason must name> <label>
  local out
  out="$(X4_GAME="$SBX_TMP/game/X4 Foundations" X4_REFERENCE="$SBX_TMP/r31ref" bash -c 'printf "%s" "$1" | bash "$2"' _ "$(cj "$1")" "$HOOKS/protect-bash.sh" 2>/dev/null)"
  case "$out" in
    *'"deny"'*) ;;
    *) no "$3: not a deny (or did not render): ${out:0:160}"; return ;;
  esac
  case "$out" in
    *"the Edit tool"*|*"the Write tool"*|*"the Glob tool"*|*"the Grep tool"*|*"CLAUDE.md #"*)
      no "$3: names a Claude-only remedy or a dead CLAUDE.md anchor" ;;
    *"$2"*) ok "$3: names an agent-neutral remedy ($2)" ;;
    *) no "$3: lost its remedy '$2': ${out:0:200}" ;;
  esac
}
_r31_one "sed -i s/a/b/ '$SBX_TMP/game/X4 Foundations/libraries/w.xml'" "apply_patch" "sed -i in the game"
_r31_one "echo x > KNOWLEDGEBASE.md" "apply_patch" "truncating a durable record"
_r31_one "uv run pytest | tail -3; echo \$?" "PIPESTATUS" "\$? after a pipeline"
_r31_one "grep -rn wares '$SBX_TMP/r31ref'" "Discovery vs. Proof" "a recursive search of reference/"


# =============================================================================
# backup-before-edit.sh: the FAILURE paths keep the trail and stderr honest (release review P4)
# =============================================================================
# The could-not-create-the-backup-dir path wrote its audit line to ${AUDIT_LOG:-<dir>/../AUDIT_LOG.txt}:
# a file OUTSIDE the trail (and any inherited AUDIT_LOG variable could redirect it anywhere),
# and every other audit append let a write error reach stderr -- stderr beside a verdict.
echo; echo "=== backup-before-edit.sh: failure paths ==="
_bk_saved="${X4_BACKUPS-}"
_bkd="$SBX_TMP/bkfail"; mkdir -p "$_bkd/src"; echo '<diff/>' > "$_bkd/src/f.xml"
: > "$_bkd/notadir"                                   # a FILE where the backup dir should go
export X4_BACKUPS="$_bkd/notadir/backups"
_bo="$(printf '%s' "$(fj "$_bkd/src/f.xml")" | AUDIT_LOG="$_bkd/redirected.txt" bash "$HOOKS/backup-before-edit.sh" 2>"$_bkd/err1")"
case "$_bo" in
  *'"ask"'*"no audit line"*) ok "backup dir cannot be created: ASKS and says no audit line was written" ;;
  *) no "backup dir cannot be created: expected an ask naming the missing audit line: ${_bo:0:160}" ;;
esac
if [ -s "$_bkd/err1" ] || [ -e "$_bkd/redirected.txt" ] || [ -e "$_bkd/notadir/AUDIT_LOG.txt" ]; then
  no "backup dir failure leaked: stderr=$(head -c 120 "$_bkd/err1") redirected=$([ -e "$_bkd/redirected.txt" ] && echo yes)"
else
  ok "backup dir failure: nothing on stderr, no stray audit file, an inherited AUDIT_LOG is ignored"
fi
export X4_BACKUPS="$_bkd/bk2"; mkdir -p "$X4_BACKUPS/AUDIT_LOG.txt"   # the trail cannot be appended
_bo="$(printf '%s' "$(fj "$_bkd/src/f.xml")" | bash "$HOOKS/backup-before-edit.sh" 2>"$_bkd/err2")"
if [ -s "$_bkd/err2" ]; then
  no "an unwritable audit log leaked to stderr: $(head -c 120 "$_bkd/err2")"
else
  case "$_bo" in
    *"AUDIT"*) ok "an unwritable audit log: no stderr, and the note says the trail was not appended" ;;
    *) no "an unwritable audit log was silent: ${_bo:0:160}" ;;
  esac
fi
if [ -n "$_bk_saved" ]; then export X4_BACKUPS="$_bk_saved"; else unset X4_BACKUPS; fi

# =============================================================================
# X4_GUARD IN A CLAUDE CODE SETTINGS FILE (user decision 2026-10-05: "Block X4_GUARD there")
# =============================================================================
# A settings `env` block reaches every hook (MEASURED, Claude Code 2.1.290). The file tools are
# judged on the RESULTING content; a shell write cannot be seen, so it is refused outright.
echo; echo "=== X4_GUARD in a settings env: refused in every channel, reported at startup ==="
_st="$SBX_TMP/settk"; mkdir -p "$_st/.claude"; printf '%s' '{"env":{"OTHER":"1"}}' > "$_st/.claude/settings.json"
_sj(){ printf '{"tool_name":"Write","tool_input":{"file_path":%s,"content":%s}}' \
         "$(printf '%s' "$_st/.claude/settings.json" | jq -Rs .)" "$(printf '%s' "$1" | jq -Rs .)"; }
decide deny  protect-files.sh "$(_sj '{"env":{"X4_GUARD":"off"}}')"   "Write settings.json with X4_GUARD in env: denied"
decide allow protect-files.sh "$(_sj '{"env":{"OTHER":"2"}}')"         "TWIN: Write settings.json without X4_GUARD: allowed"
decide deny  protect-bash.sh  "$(cj "echo '{}' > '$_st/.claude/settings.local.json'")" "Bash: a redirect into settings.local.json: denied"
decide allow protect-bash.sh  "$(cj "cat '$_st/.claude/settings.json'")" "TWIN: Bash: reading settings.json: allowed"
printf '%s' '{"env":{"X4_GUARD":"off"}}' > "$_st/.claude/settings.json"
_sc="$(CLAUDE_PROJECT_DIR="$_st" HOME="$SBX_TMP/nohome" bash "$HOOKS/session-canary.sh" 2>/dev/null)"
case "$_sc" in
  "[x4 guards] X4_GUARD is set in a Claude Code settings env block"*|*$'\n'"[x4 guards] X4_GUARD is set in a Claude Code settings env block"*)
    ok "session-canary names the settings file that sets X4_GUARD" ;;
  *) no "session-canary did not report X4_GUARD in the settings env: ${_sc:0:200}" ;;
esac

# =============================================================================
# FX-G4 (v4.0.0 delta review, reviewers G and H): each was ALLOWED (or wrongly ASKED) before
# =============================================================================
echo; echo "=== FX-G4: parser and rule bypasses, end to end ==="
_g4save="$(declare -p X4_GAME X4_REFERENCE X4_TOOLKIT CLAUDE_PROJECT_DIR 2>/dev/null)"
_g4tk="$SBX_TMP/g4tk"; _g4g="$SBX_TMP/g4game/X4 Foundations"; _g4r="$_g4tk/reference"
mkdir -p "$_g4r/libraries" "$_g4g/libraries" "$_g4tk/.claude"
export X4_GAME="$_g4g" X4_REFERENCE="$_g4r" X4_TOOLKIT="$_g4tk" CLAUDE_PROJECT_DIR="$_g4tk"
_NL=$'\n'; _RMG="rm -rf \"$_g4g\""
decide deny   protect-bash.sh "$(cj "((x=1<<EOF))${_NL}${_RMG}")"                    "H5: a shift in (( )) opens no heredoc: the next line is judged"
decide deny   protect-bash.sh "$(cj "x=\`cat <<EOF${_NL}hi${_NL}EOF\`${_NL}${_RMG}")"  "H5: EOF + backtick ends the heredoc"
decide allow  protect-bash.sh "$(cj "cat <<EOF${_NL}${_RMG}${_NL}EOF")"                "TWIN H5: a real heredoc body is still data"
decide deny   protect-bash.sh "$(cj "cmd //k <<EOF${_NL}rd /s /q \"$_g4r\"${_NL}EOF")"  "H1: cmd /k reads its program from stdin"
decide allow  protect-bash.sh "$(cj "echo rd /s /q \"$_g4r\" | cmd //c sort")"           "TWIN H1: cmd /c sort takes no stdin program"
decide deny   protect-bash.sh "$(cj 'rm -rf "${X4_GAME:-/x}"')"                          "H2: a root variable under :- is its root"
decide allow  protect-bash.sh "$(cj 'rm -rf "${G4_NOT_A_ROOT:-/tmp/g4zz}"')"             "TWIN H2: another variable under :- is not"
decide ask    protect-bash.sh "$(cj "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=clean.requireForce GIT_CONFIG_VALUE_0=false git -C \"$_g4tk\" clean -dx")" \
  "H6: git config from the environment forces a clean in the toolkit"
decide deny   protect-bash.sh "$(cj "echo ${_RMG} | bash")"                              "H7: unquoted echo piped into bash is its program"
decide deny   protect-bash.sh "$(cj "bash <(echo ${_RMG})")"                             "H8: a process substitution run as a script"
decide deny   protect-bash.sh "$(cj "x=\$(printf rm); \$x -rf \"$_g4r\"")"               "G-OUT: a verb from a variable holding a substitution"
decide advise protect-bash.sh "$(cj "\$(echo rm) -rf \"$_g4g/libraries\"")"              "F183: a substituted verb below the game root advises"
decide allow  protect-bash.sh "$(cj 'python scripts/x4refguard.py apply --toolkit "$X4_TOOLKIT"')" "H9: a variable in an OPTION value is not the action"
decide ask    protect-bash.sh "$(cj 'python scripts/x4refguard.py "$ACT"')"                "TWIN H9: an unresolved ACTION still asks"
# FX-G5 / reviewer J2: each was ALLOWED before
decide deny   protect-bash.sh "$(cj "A=\$(echo a b) ${_RMG}")"                            "J2-3: an unquoted multi-word prefix substitution hides no verb"
decide deny   protect-bash.sh "$(cj "x=\$(printf rm); A=\$(true) \$x -rf \"$_g4r\"")"    "J2-3: a prefix assignment hides no substituted verb"
decide deny   protect-bash.sh "$(cj "bash <(echo -n ${_RMG})")"                           "J2-2: an echo -n INSIDE is not bash -n"
decide deny   protect-bash.sh "$(cj "bash -o pipefail <(echo ${_RMG})")"                  "J2-2: an option's value is not the script"
decide allow  protect-bash.sh "$(cj "bash -o pipefail -n <(echo ${_RMG})")"               "TWIN J2-2: the shell's own -n still checks syntax only"
decide deny   protect-bash.sh "$(cj 'rm -rf "${PATH:+$X4_GAME}"')"                        "J2-9: the alternate of an unassigned variable is judged"
decide ask    protect-bash.sh "$(cj "git -c include.path=/w/c.cfg -C \"$_g4tk\" clean -dx")" "J2-5: -c include.path forces a clean"
decide allow  protect-bash.sh "$(cj "git -c core.x=1 -C \"$_g4tk\" clean -dx")"          "TWIN J2-5: other -c leaves an unforced clean alone"
# FX-G6 / reviewer K: each was ALLOWED at 47d06cd (C1, C2 regressions of FX-G5; I2-I4 pre-arc)
decide deny   protect-bash.sh "$(cj "rm -rf \"\${G6_NOPE:+zz}$_g4r\"")"                     "K-C1: the EMPTY branch of an unassigned :+ is judged"
decide deny   protect-bash.sh "$(cj "rm -rf \"\${G6_NOPE+zz}$_g4r\"")"                      "K-C1: ...and of an unassigned +"
decide deny   protect-bash.sh "$(cj "rm -rf \"\${G6_NOPE:+./junk}$_g4g\"")"                 "K-C1: ...a relative alternate before the game root"
decide allow  protect-bash.sh "$(cj 'rm -rf "${TMPDIR:+$TMPDIR/}g6build"')"                 "TWIN K-C1: an ordinary alternate stays harmless"
decide deny   protect-bash.sh "$(cj "bash - <(echo ${_RMG})")"                             "K-C2: a lone - ends the options; the procsub is the script"
decide deny   protect-bash.sh "$(cj "sh -x - <(echo ${_RMG})")"                            "K-C2: ...after an option too"
decide deny   protect-bash.sh "$(cj "bash -n +n <(echo ${_RMG})")"                         "K-I3: +n turns -n back off"
decide allow  protect-bash.sh "$(cj "bash +n -n <(echo ${_RMG})")"                         "TWIN K-I3: the LAST n-toggle is -n: a syntax check"
decide deny   protect-bash.sh "$(cj "A=\${G6_X:-echo a} ${_RMG}")"                          "K-I4: a \${...} with a blank is one word"
decide deny   protect-bash.sh "$(cj "A=\$(echo a; echo b) ${_RMG}")"                        "K-I4: a substitution the segmenter cut hides no verb"
decide deny   protect-bash.sh "$(cj "env -C \$(echo /tmp ) ${_RMG}")"                       "K-I4: a wrapper option's VALUE is one word"
decide allow  protect-bash.sh "$(cj "A=\$(echo a; echo b) ls \"$_g4g\"")"                   "TWIN K-I4: a harmless command behind the cut stays harmless"
decide ask    protect-bash.sh "$(cj "git -C \"$_g4tk\" config \"clean.require\"\"Force\" false && git -C \"$_g4tk\" clean -dx")" \
  "K-I2: ANY writing git config forces a following clean"
decide ask    protect-bash.sh "$(cj "HOME=/w git -C \"$_g4tk\" clean -dx")"                 "K-I2: a moved HOME forces a clean"
decide allow  protect-bash.sh "$(cj "git -C \"$_g4tk\" config --get user.name && git -C \"$_g4tk\" clean -dx")" \
  "TWIN K-I2: a READ of git config does not"
decide ask    protect-bash.sh "$(cj "takeown /f \"$_g4r\" /r")"                           "H-M5: takeown of reference asks"
decide deny   protect-bash.sh "$(cj "gio trash \"file://${_g4g// /%20}\"")"               "H-M3: a percent-encoded file:// URL is decoded"
_hk(){ printf '{"tool_name":"Write","tool_input":{"file_path":%s,"content":%s}}' \
         "$(printf '%s' "$_g4tk/.claude/settings.json" | jq -Rs .)" \
         "$(printf '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"%s bash .claude/hooks/protect-bash.sh"}]}]}}' "$1" | jq -Rs .)"; }
decide deny   protect-files.sh "$(_hk 'X4_GUARD=off')"                                    "H4a: X4_GUARD in a hooks COMMAND of a settings file"
decide allow  protect-files.sh "$(_hk 'FOO=1')"                                           "TWIN H4a: an ordinary hooks command"
unset X4_GAME X4_REFERENCE X4_TOOLKIT CLAUDE_PROJECT_DIR; eval "$_g4save"

# THE CALLER-DIRECTORY CHECK RUNS LAST (reviewer E, minor): it sat above every section added
# since, so a probe below it that leaked a path into the caller's directory went unseen.
# (Its history, and why the baseline is taken AFTER the sandbox exists, is at _CWD_BEFORE.)
# directory is invisible to `git status`, which is how a stray `n/` accumulated.
_CWD_AFTER="$(ls -A 2>/dev/null | sort)"
_NEW="$(comm -13 <(printf "%s" "$_CWD_BEFORE") <(printf "%s" "$_CWD_AFTER") | tr "
" " ")"
if [ -n "$(printf "%s" "$_NEW" | tr -d "[:space:]")" ]; then
  no "the suite left new paths in the caller directory: $_NEW"
else
  ok "the suite left nothing behind in the caller directory"
fi

echo "RESULT: $pass passed, $fail failed, $skipped skipped"
if [ $((pass + fail + skipped)) -ne "$EXPECT" ]; then
  echo "FAIL: $((pass + fail + skipped)) probes ran ($skipped skipped), expected $EXPECT -- a probe was DROPPED, not passed."
  echo "      (If you added or removed one deliberately, update EXPECT.)"
  exit 1
fi
[ "$fail" -eq 0 ]

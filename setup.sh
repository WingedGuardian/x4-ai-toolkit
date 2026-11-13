#!/bin/bash
# X4 AI Assistant Toolkit — setup (cross-platform: Linux / macOS / Windows Git Bash)
# Checks prerequisites, wires up the bundled x4validate, and seeds local config. Idempotent.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
# --config-only: seed the path config and stop (no prerequisites, no dependency sync). For
# tests and for a user who only wants the config; the installers do not pass it.
_I_CONFIG_ONLY=0
[ "${1:-}" = "--config-only" ] && _I_CONFIG_ONLY=1
# --save-lock is refused with a note (section 6); --no-save-lock is accepted and does nothing.
_I_SAVE_LOCK=""
for _a in "$@"; do
  case "$_a" in --save-lock) _I_SAVE_LOCK=yes ;; --no-save-lock) _I_SAVE_LOCK=no ;; esac
done
echo "=== X4 AI Assistant Toolkit setup ==="
echo "Toolkit root: $ROOT"
echo

ok()   { echo "  [ok]   $*"; }
warn() { echo "  [warn] $*"; }
# A setup that cannot FAIL cannot be checked by its caller. Recorded here and reported
# at the end, so every prerequisite is still reported in one pass rather than the run
# stopping at the first miss.
MISSING=""
fail() { echo "  [FAIL] $*"; MISSING="$MISSING
  - $*"; }

# The path config (Plan 3 lane I). 4.x reads <toolkit>/x4-paths.env; a 3.x one at
# .claude/x4-paths.env is still read, deprecated. NEVER create the 4.x file beside a 3.x one:
# the new location outranks the old, so a fresh copy of the (blank) example would SHADOW the
# real config with empty values. Point at the one-command migration instead.
_i_seed_config() {
  CFG="$ROOT/x4-paths.env"
  CFG_OLD="$ROOT/.claude/x4-paths.env"
  CFG_EX="$ROOT/x4-paths.env.example"
  # R2 cosmetic: "left as it is" read oddly about a file the installer wrote seconds earlier;
  # both installers set X4_SETUP_CONFIG_WRITTEN=1 when they call this script.
  if [ -f "$CFG" ] && [ "${X4_SETUP_CONFIG_WRITTEN:-0}" = 1 ]; then
    ok "x4-paths.env written by the installer just now ($CFG); edit it to change a path"
  elif [ -f "$CFG" ]; then ok "x4-paths.env in place ($CFG) -- left as it is; edit it to change a path"
  elif [ -f "$CFG_OLD" ]; then
    warn "DEPRECATED location: $CFG_OLD is the 3.x path config (still read). 4.0 reads $CFG."
    warn "  Move it: x4config.py migrate --apply, i.e. python \"$ROOT/scripts/x4config.py\" migrate --apply"
  elif [ -f "$CFG_EX" ]; then
    if cp "$CFG_EX" "$CFG" 2>/dev/null && [ -f "$CFG" ]; then
      ok "created x4-paths.env from example (gitignored) — edit it to point at your game/profile"
    else
      fail "could not create x4-paths.env from the example (is $CFG a directory?)"
    fi
  else warn "no x4-paths.env.example to copy"; fi
}
if [ "$_I_CONFIG_ONLY" = 1 ]; then
  _i_seed_config
  [ -z "$MISSING" ] || { echo "setup --config-only FAILED:$MISSING"; exit 1; }
  exit 0
fi

# --- detect OS for install hints ------------------------------------------
case "$(uname -s 2>/dev/null)" in
  Linux*)                 OS=linux;   PKG="your package manager (e.g. sudo pacman -S jq / sudo apt install jq / sudo dnf install jq)";;
  Darwin*)                OS=macos;   PKG="brew install jq";;
  MINGW*|MSYS*|CYGWIN*)   OS=windows; PKG="winget install jqlang.jq";;
  *)                      OS=unknown; PKG="your package manager";;
esac
echo "Detected OS: $OS"
echo

# --- 1. prerequisites ------------------------------------------------------
echo "1) Checking prerequisites..."
# jq is WARN, not FAIL: the hooks carry a documented python fallback for emitting a
# verdict, so jq-missing-but-python-present is degraded, not broken.
if command -v jq >/dev/null 2>&1; then ok "jq found"; else
  warn "jq not found — install with: $PKG  (then restart your shell)"; fi

if command -v uv >/dev/null 2>&1; then ok "uv found"; else
  fail "uv not found — x4validate cannot run at all. Install from https://docs.astral.sh/uv/"; fi

# A bare interpreter, checked SEPARATELY from uv. protect-bash.sh analyses every command
# with .claude/hooks/hook_facts.py, so without one the Bash guard asks on every command
# instead of checking it. uv can be present while `python` is not on PATH, so testing uv
# alone would report a healthy setup for a machine whose main guard cannot run.
_py=""
for _c in python python3 py; do command -v "$_c" >/dev/null 2>&1 && { _py="$_c"; break; }; done
if [ -n "$_py" ]; then ok "python found ($_py) — the Bash guard can analyse commands"; else
  fail "no python/python3/py on PATH — protect-bash.sh cannot analyse commands and will ASK on every one. Install Python 3, or set X4_PYTHON to an interpreter."; fi

# Java is OPTIONAL and scoped to the BaseX corpus search (tools/basex). The floor is
# MEASURED, not chosen: BaseX 13.0's classes are bytecode major 65 (Java 21), so an older JVM
# refuses to load them outright (UnsupportedClassVersionError) rather than running
# slowly. Nothing else in this toolkit needs a JVM.
if command -v java >/dev/null 2>&1; then
  JV=$(java -version 2>&1 | head -1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p')
  # the pre-9 form is `java version "1.8.0_402"`, where the major is the SECOND field
  if [ "$JV" = "1" ]; then JV=$(java -version 2>&1 | head -1 | sed -n 's/.*version "1\.\([0-9][0-9]*\).*/\1/p'); fi
  if [ -n "$JV" ] && [ "$JV" -ge 21 ] 2>/dev/null; then
    ok "java $JV found (optional — powers the BaseX corpus search)"
  else
    warn "java found but reports version '${JV:-unparsed}' — BaseX 13.0 needs 21+. Optional: only tools/basex uses it."
  fi
else
  warn "java not found — OPTIONAL, needed only for the BaseX corpus search (tools/basex, Java 21+). Everything else works without a JVM."
fi

if [ "$OS" != "windows" ]; then
  if command -v wine >/dev/null 2>&1; then ok "wine found (needed to run XRCatTool on $OS)"; else
    warn "wine not found — needed to run Egosoft's XRCatTool on $OS (bin/xrcat). Install it via $PKG-equivalent."; fi
fi

# --- 2. x4validate ---------------------------------------------------------
echo
echo "2) Setting up bundled x4validate..."
X4V="$ROOT/tools/x4validate"
if [ -d "$X4V" ] && command -v uv >/dev/null 2>&1; then
  ( cd "$X4V" && uv sync >/dev/null 2>&1 ) && ok "x4validate dependencies synced (uv)" \
    || fail "uv sync failed — x4validate will not run. Try it manually:  cd tools/x4validate && uv sync"
  # R2-g (second install red-team): `&&` is rejected by Windows PowerShell 5.1, so each shell
  # gets its own line. An INSTALLED toolkit skips the tests that need the repo-only agent/
  # source (counted, with a REPO-ONLY reason): a skip there is expected, not a failure.
  echo "     test it:  Git Bash / Linux / macOS:  cd tools/x4validate && uv run pytest -q"
  echo "               PowerShell:                cd tools/x4validate; uv run pytest -q"
  echo "               passing = 0 failed and 0 errors; skips are expected (each names its reason)"
  echo "     run it:   cd tools/x4validate, then:  uv run x4validate <your_mod>"
else
  fail "x4validate NOT wired up (need uv + tools/x4validate)"
fi

# --- 3. local settings + path config --------------------------------------
echo
echo "3) Local settings & path config..."
LOCAL="$ROOT/.claude/settings.local.json"
EXAMPLE="$ROOT/.claude/settings.local.json.example"
if [ ! -d "$ROOT/.claude" ]; then ok "no Claude target here, so no .claude/settings.local.json is needed"
elif [ -f "$LOCAL" ]; then ok ".claude/settings.local.json in place -- left as it is"
# VERIFY THE ARTIFACT, not the intention. `cp ...; ok "created ..."` uses `;` not
# `&&`, so a cp that failed -- or that succeeded INTO A DIRECTORY of that name --
# still printed [ok] and contributed nothing to MISSING. MEASURED 2026-09-05 with
# $LOCAL as a directory: both [ok] lines printed, "=== setup complete ===", exit 0,
# and neither file existed. Line 76 in this same file already checks its artifact.
elif [ -f "$EXAMPLE" ]; then
  if cp "$EXAMPLE" "$LOCAL" 2>/dev/null && [ -f "$LOCAL" ]; then
    ok "created .claude/settings.local.json from its example (your per-machine Claude settings; gitignored)"
  else
    fail "could not create settings.local.json from the example (is $LOCAL a directory?)"
  fi
else warn "no settings.local.json.example to copy"; fi

_i_seed_config
echo "     For a guided install (game folder / separate dir / global multi-repo) run:  bash install.sh   (or install.ps1 on Windows)"

# --- 4. reference tree (your own game data — never redistributed) ----------
echo
echo "4) Base-game reference (you unpack your OWN copy):"
# R2 cosmetic: this said "Set X4_GAME" after the installer had set it, and named a Git Bash
# command only. Read the config it just seeded; give the PowerShell form too (bare `bash`
# there is usually the WSL stub, which cannot run it).
#: FX-B2 (delta review): THE SAME GRAMMAR as every other reader -- the guards' own loader,
#: parsing x4-paths.env alone (X4_CONFIG pinned to it, the key unset so the environment cannot
#: answer). The sed it replaces ignored `export KEY=` lines, so a config written that way read
#: as "X4_GAME is blank". The sed stays only for a root with no guard copy, and takes `export`.
_i_loader=""
for _i_l in .claude .codex .opencode; do
  [ -f "$ROOT/$_i_l/hooks/_x4-env.sh" ] && { _i_loader="$ROOT/$_i_l/hooks/_x4-env.sh"; break; }
done
_i_cfg_val() {   # KEY -> the value in x4-paths.env (empty when unset)
  [ -f "$ROOT/x4-paths.env" ] || return 0
  if [ -n "$_i_loader" ]; then
    ( unset "$1"; X4_TOOLKIT="$ROOT"; X4_CONFIG="$ROOT/x4-paths.env"; export X4_TOOLKIT X4_CONFIG
      . "$_i_loader" >/dev/null 2>&1; printf '%s' "${!1:-}" )
    return 0
  fi
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=[[:space:]]*[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\2/p" "$ROOT/x4-paths.env" | tail -n 1
}
_I_GAME="$(_i_cfg_val X4_GAME)"
_I_REF="$(_i_cfg_val X4_REFERENCE)"; _I_REF="${_I_REF:-$ROOT/reference}"
_I_PSBASH='& "C:\Program Files\Git\bin\bash.exe"'
if [ -n "${X4_BASH:-}" ]; then _I_PSBASH="& \"$X4_BASH\""
elif [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then
  _I_PSBASH="& \"$(cygpath -w "$(command -v bash)")\""
fi
if [ -f "$_I_REF/.unpacked-and-locked" ]; then
  echo "   reference/ is already unpacked ($_I_REF)."
else
  if [ -n "$_I_GAME" ]; then
    echo "   X4_GAME is set in x4-paths.env. Build reference/ from the toolkit root with:"
  else
    echo "   Set X4_GAME in x4-paths.env (toolkit root), then build reference/ from the toolkit root with:"
  fi
  echo "     Git Bash / Linux / macOS:  bash bin/unpack-reference.sh"
  [ "$OS" = windows ] && echo "     PowerShell:                $_I_PSBASH bin/unpack-reference.sh"
  echo "   (text-only unpack via XRCatTool → reference/, ~0.6 GB; gitignored, never redistributed.)"
fi

# --- 5. Nexus API (optional, your own key) ---------------------------------
echo
echo "5) Nexus mod triage (optional): x4modlist uses the official Nexus API with YOUR OWN free key."
echo "   Get one at https://www.nexusmods.com/users/myaccount?tab=api then set X4_NEXUS_KEY:"
if [ "$OS" = "windows" ]; then
  echo "       setx X4_NEXUS_KEY \"<your-key>\"          (or add it to x4-paths.env)"
else
  echo "       export X4_NEXUS_KEY=\"<your-key>\"        (add to your shell rc, or to x4-paths.env)"
fi

# --- 6. Save locks: cannot be turned on in this version (user ruling 2026-11-04) ----
# A lock binds X4 too, and a player must be able to save over a save. Someone holding locks from a pre-release
# build is told how to lift them.
echo
_sl_switch="$ROOT/.modding-agent-harness/save-lock.json"
if [ -f "$_sl_switch" ] && grep -q '"enabled": true' "$_sl_switch" 2>/dev/null; then
  warn "save locks: ON, from an earlier build. They stop X4 saving over a locked save. From an administrator prompt: python \"$ROOT/tools/agent-harness.py\" save-lock off"
elif [ "$_I_SAVE_LOCK" = yes ]; then
  warn "--save-lock: save locks cannot be turned on in this version, because a locked save could not be saved over in game. Nothing was changed."
fi

echo
if [ -n "$MISSING" ]; then
  echo "=== setup INCOMPLETE ==="
  echo "These are required and are missing or failed:$MISSING"
  echo
  echo "Fix the above and re-run:  bash setup.sh"
  exit 1
fi
echo "=== setup complete ==="
echo "Open your AI agent (Claude Code, Codex, ...) in this folder and start modding. See README.md for examples."
exit 0

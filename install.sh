#!/usr/bin/env bash
# X4 AI Assistant Toolkit installer — Linux / macOS / Windows (Git Bash).
# Three install methods, all with fully configurable paths (nothing hardcoded):
#
#   in-game   Copy the toolkit INTO your X4 game folder (the upstream model). One workspace.
#   separate  Keep the toolkit in its OWN folder, pointed at the game via config.
#   global    Install the skills/agents into ~/.claude and write the X4_* paths into your
#             global Claude settings, so they work across MANY mod repos (multi-project).
#
# Every location is auto-detected where possible and overridable by flag/env. The chosen
# paths are written to <toolkit>/x4-paths.env (the single source of truth the hooks, bin/
# scripts and tools read). A 3.x config at <toolkit>/.claude/x4-paths.env is MOVED there.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repo / toolkit source
#: R2-f (second install red-team): the --over-existing refusal ended in `...`. It now prints
#: the user's OWN command line back, plus the flag that was missing.
X4_ORIG_ARGS=("$@")

# --- defaults (overridable by flags / env) ---------------------------------
METHOD=""; ASSUME_YES=0; DO_UNPACK=0; OVER_EXISTING=0; DRY_RUN=0; NO_ENV=0; CODEX_DOC_MAX=""
#: Which agent targets to install (user decision #10, 2026-10-02: default `all`). The
#: Codex files are inert without Codex, and x4doctor reports which targets are live.
AGENT="all"
#: Did a HUMAN name the destination, or did we find it by scanning? Recorded at
#: parse time: an env var is a deliberate act, a Steam-folder scan is not.
#: EXCEPT X4_TOOLKIT (FX-B2, delta review): it is INHERITED by every shell a working toolkit
#: has touched -- it names the user's LIVE toolkit -- so adopting it as a "named" destination
#: meant a second install's refusal offered `--over-existing` against that live toolkit, and
#: `--method global` copied ITS skills. Only `--toolkit` names the toolkit destination; an
#: inherited value is a default, said out loud (TOOLKIT_FROM_ENV) and never paired with an
#: --over-existing suggestion.
#: AND X4_GAME (FX-B3, reviewer F M6): the same class. `--method global` writes X4_GAME into
#: ~/.claude/settings.json env, so every agent session inherits it, and an in-game install
#: under `--yes` adopted it as a NAMED destination -- the user's live game root. Only --game
#: names it; an inherited value is a default, said out loud (GAME_FROM_ENV).
GAME_NAMED=detected
GAME_FROM_ENV=$([ -n "${X4_GAME:-}" ] && echo 1 || echo 0)
TOOLKIT_NAMED=detected
TOOLKIT_FROM_ENV=$([ -n "${X4_TOOLKIT:-}" ] && echo 1 || echo 0)
#: The path variables this run INHERITED (not flags). Each one used is printed before any
#: write (FX-B2: an inherited X4_REFERENCE was copied into a new install's config silently).
X4_INHERITED_PATHS=""
for _v in X4_GAME X4_PROFILE X4_MODS X4_REFERENCE X4_EXTENSIONS XRCATTOOL; do
  [ -n "${!_v:-}" ] && X4_INHERITED_PATHS="$X4_INHERITED_PATHS $_v"
done
GAME="${X4_GAME:-}"; PROFILE="${X4_PROFILE:-}"; TOOLKIT="${X4_TOOLKIT:-}"
X4_H_INHERITED_TOOLKIT="${X4_TOOLKIT:-}"   # what this shell already exports (POSIX "different" check)
MODS="${X4_MODS:-}"; REFERENCE="${X4_REFERENCE:-}"; EXTENSIONS="${X4_EXTENSIONS:-}"
XRCAT="${XRCATTOOL:-}"

usage() {
  # The comment block ends at line 12. '2,16p' printed four lines of live shell
  # source into the middle of the help text.
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

Usage: bash install.sh --method in-game|separate|global [options]
  --game DIR         X4 install (folder with 01.cat..09.cat)   [auto-detected]
  --profile DIR      user profile (saves/config/debug log)     [auto-detected]
  --toolkit DIR      where the toolkit lives (separate/global) [repo dir / game dir]
  --mods DIR         your mod source repos root
  --reference DIR    unpacked base game (default <toolkit>/reference)
  --extensions DIR   live deploy target (default <game>/extensions)
  --xrcattool PATH   XRCatTool.exe location
  --agent LIST       claude | codex | generic | opencode | all | auto   [all]
                     (a comma list picks several: claude,codex)
                     which agent's instructions, guards and skills to install;
                     auto = the agents found on PATH or already in the destination
                     (none found: all, and it says so)
  --unpack          also unpack reference/ now (needs --game + XRCatTool [+wine])
  --over-existing    REQUIRED to install over an existing installation
  --dry-run          print the destination and the item list; write nothing
  --codex-doc-max-bytes N
                     write project_doc_max_bytes = N (32768..1048576) into the
                     project's .codex/config.toml (needs the Codex target). Codex
                     reads the root AGENTS.md and every nested one into ONE
                     32,768-byte budget, so an AGENTS.md of your own lower in the
                     tree can cut the toolkit's off. Opt-in; never overwrites.
  --no-env           do NOT set X4_TOOLKIT for your user (by default it is set when
                     unset; a DIFFERENT existing value is reported and left alone)
  --yes              don't prompt; accept detected/blank values (never a
                     detected DESTINATION -- name that with --game/--toolkit)
  -h, --help         this help

Test seams (for the installer test suite only, not for users):
  X4_INSTALL_ENV_REGKEY   registry key used instead of HKCU\Environment
                          (refused unless under HKCU\Software\X4ToolkitTests\)
  X4_INSTALL_DETECT_PATH  the PATH --agent auto walks instead of $PATH
USAGE
}

# Every value-taking flag goes through need2 first. Under `set -u` a bare `--game`
# died with "install.sh: line 44: $2: unbound variable" -- a bash-internal diagnostic
# naming a line number, with no usage hint. install.ps1 gets this free from param().
need2() {
  [ "$2" -ge 2 ] || { echo "ERROR: $1 requires a value" >&2; echo >&2; usage >&2; exit 2; }
}
#: A flag replaced an inherited value: it is no longer "from the environment".
_x4_unherit() { X4_INHERITED_PATHS=" ${X4_INHERITED_PATHS# } "; X4_INHERITED_PATHS="${X4_INHERITED_PATHS/ $1 / }"; X4_INHERITED_PATHS="${X4_INHERITED_PATHS% }"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --method) need2 "$1" $#; METHOD="$2"; shift 2;;
    --game) need2 "$1" $#; GAME="$2"; GAME_NAMED=named; GAME_FROM_ENV=0; _x4_unherit X4_GAME; shift 2;;
    --profile) need2 "$1" $#; PROFILE="$2"; _x4_unherit X4_PROFILE; shift 2;;
    --toolkit) need2 "$1" $#; TOOLKIT="$2"; TOOLKIT_NAMED=named; TOOLKIT_FROM_ENV=0; shift 2;;
    --mods) need2 "$1" $#; MODS="$2"; _x4_unherit X4_MODS; shift 2;;
    --reference) need2 "$1" $#; REFERENCE="$2"; _x4_unherit X4_REFERENCE; shift 2;;
    --extensions) need2 "$1" $#; EXTENSIONS="$2"; _x4_unherit X4_EXTENSIONS; shift 2;;
    --xrcattool) need2 "$1" $#; XRCAT="$2"; _x4_unherit XRCATTOOL; shift 2;;
    --agent) need2 "$1" $#; AGENT="$2"; shift 2;;
    --over-existing) OVER_EXISTING=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    --no-env) NO_ENV=1; shift;;
    --codex-doc-max-bytes) need2 "$1" $#; CODEX_DOC_MAX="$2"; shift 2;;
    --unpack) DO_UNPACK=1; shift;;
    --yes|-y) ASSUME_YES=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage; exit 2;;
  esac
done

case "$(uname -s 2>/dev/null)" in
  Linux*) OS=linux;; Darwin*) OS=macos;; MINGW*|MSYS*|CYGWIN*|Windows*) OS=windows;; *) OS=unknown;;
esac
# R2 cosmetic: under Git Bash $SRC is MSYS-style (/c/Users/...), which no Windows tool accepts.
SRC_SHOW="$SRC"
if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then
  SRC_SHOW="$(cygpath -m "$SRC" 2>/dev/null || printf '%s' "$SRC")"
fi
echo "X4 AI Assistant Toolkit installer — OS: $OS, source: $SRC_SHOW"

#: One argument, quoted for bash only when it needs it (R2-f).
_r2_shquote() {
  case "$1" in
    ''|*[!A-Za-z0-9_./:=,+@%-]*) printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")" ;;
    *) printf '%s' "$1" ;;
  esac
}
#: The command that re-runs THIS install with EXTRA appended (R2-f).
_r2_rerun_cmd() {   # EXTRA...
  local out="bash $(_r2_shquote "$0")" a
  for a in "${X4_ORIG_ARGS[@]}" "$@"; do out="$out $(_r2_shquote "$a")"; done
  printf '%s' "$out"
}

# --- helpers ---------------------------------------------------------------
ask() {  # ask VAR "prompt" "default"
  local cur="${!1}" def="$3"
  [ -n "$cur" ] && def="$cur"
  if [ "$ASSUME_YES" = 1 ]; then printf -v "$1" '%s' "$def"; return; fi
  local ans; read -r -p "$2 [${def:-blank}]: " ans || true
  printf -v "$1" '%s' "${ans:-$def}"
}

steam_roots() {
  case "$OS" in
    linux)   printf '%s\n' "$HOME/.steam/steam" "$HOME/.local/share/Steam" "$HOME/.steam/root" \
               "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam";;
    macos)   printf '%s\n' "$HOME/Library/Application Support/Steam";;
    windows) printf '%s\n' "/c/Program Files (x86)/Steam" "/c/Program Files/Steam";;
  esac
}

detect_game() {
  [ -n "$GAME" ] && return 0
  local root vdf lib
  # Read line-by-line, NOT `for root in $(steam_roots)`. Unquoted command
  # substitution word-splits on spaces, which shreds every path this function
  # emits on Windows ("/c/Program Files (x86)/Steam" -> three fragments) and on
  # macOS ("$HOME/Library/Application Support/Steam"). Only Linux's paths happen
  # to be space-free, so auto-detection silently worked on exactly one platform
  # — including the libraryfolders.vdf fallback, which the loop never reached.
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    [ -d "$root/steamapps/common/X4 Foundations" ] && { GAME="$root/steamapps/common/X4 Foundations"; return 0; }
    vdf="$root/steamapps/libraryfolders.vdf"
    [ -f "$vdf" ] || continue
    while IFS= read -r lib; do
      [ -d "$lib/steamapps/common/X4 Foundations" ] && { GAME="$lib/steamapps/common/X4 Foundations"; return 0; }
    done < <(grep -oE '"path"[[:space:]]*"[^"]+"' "$vdf" | sed -E 's/.*"path"[[:space:]]*"([^"]+)"/\1/')
  done < <(steam_roots)
  return 0
}

detect_profile() {
  [ -n "$PROFILE" ] && return 0
  local base newest="" d
  case "$OS" in
    windows) base="${USERPROFILE:-$HOME}/Documents/Egosoft/X4";;
    *)       base="$HOME/.config/EgoSoft/X4";;   # Linux/macOS vary; override with --profile if different
  esac
  [ -d "$base" ] || return 0
  for d in "$base"/*/; do
    [ -d "$d" ] || continue
    if [ -z "$newest" ] || [ "$d" -nt "$newest" ]; then newest="$d"; fi
  done
  PROFILE="${newest%/}"
  return 0
}

detect_xrcat() {
  [ -n "$XRCAT" ] && return 0
  local c
  for c in "$SRC/tools/XRCatTool/XRCatTool.exe" "$SRC/XTools/XRCatTool.exe" \
           "${GAME:+$GAME/../XRCatTool.exe}"; do
    [ -f "$c" ] && { XRCAT="$c"; return 0; }
  done
  return 0
}

MISSING=""
#: Paths never copied between toolkits, pruned from the destination BOTH before and
#: after the copy. One list, named once: two passes over two hand-written lists is how
#: they drift, and the whole defect here was one of the passes not running.
#: Machine-local paths that must not travel. `.gitignore` is already the correct
#: enumeration of 'must not leave this machine' and nothing derived this from it,
#: so the copy carried 217 files of `.claude/backups/`, every __pycache__ and
#: .pytest_cache under the copy set, and `.perf-baseline.json` -- whose own
#: .gitignore entry says MACHINE-LOCAL by design because wall-clock differs per
#: machine, and which the installer then copied to another machine for
#: perf_guard to compare against a stranger's timings.
X4_COPY_PRUNE="tools/x4validate/.venv tools/x4validate/.pytest_cache tools/basex/.pytest_cache tools/x4validate/.mutation-probe-pristine .claude/hooks/__pycache__ .claude/hooks/.pytest_cache scripts/__pycache__ tools/.pytest_cache tools/basex/__pycache__ tools/basex/basex/.basex tools/x4validate/.perf-baseline.json tools/x4validate/.obtainability-baseline.json tools/x4validate/.claude-md-budget-baseline.json tools/x4validate/.toolkit-usage-baseline.json tools/x4validate/.hook-false-positive-baseline.json tools/x4validate/.instrument-hygiene-baseline.json tools/x4validate/.schema-sweep-baseline.json tools/x4validate/gates/__pycache__ tools/x4validate/scripts/__pycache__ tools/x4validate/tests/__pycache__ tools/x4validate/x4validate/__pycache__"

#: Per-machine files that must NEVER travel from the source: they hold THIS
#: machine paths and secrets, and the destination copy is the user own.
#:
#: Distinct from X4_COPY_PRUNE, and the difference is the whole point: a pruned
#: path is also DELETED from the destination, which is right for a stale .venv and
#: catastrophic for a config. These are skipped on the way IN and left alone on
#: the way OUT.
#:
#: This replaces a backup-then-overwrite-then-restore round trip on the same two
#: files. That round trip had two defects and both are removed rather than fixed:
#: it wrote a file it never intended to change (so `x4lock`, which the README
#: tells users to run, made the whole install fail with a bare `cp: Permission
#: denied` on item 1 of 16), and its restore sat 53 lines after its backup with
#: no trap between them, so any failure in the copy loop skipped the restore and
#: left an orphaned .bak. Not copying a file cannot fail to restore it.
#:
#: The BaseX build products share these semantics exactly -- this machine's, the
#: destination's own, never copied in and NEVER deleted out -- so they live here and
#: not in X4_COPY_PRUNE, whose second meaning is `rm -rf` in the destination. Filed
#: there, an upgrade would erase the user's 2+ GB of built databases, the same
#: dual-meaning trap `.claude/backups` fell into. A checkout accumulates them (release
#: review 2026-09-26: data/ alone was 2.3 GB) and a git source never copies them
#: anyway (see _tracked_copy_set); this is the walk's defence in depth.
#: `.claude/.reference-buildid` too (FX-G5 / reviewer J2 item 7): THIS machine's finished-unpack
#: marker, which x4doctor reads as evidence of a MOVED tree -- copied in, a fresh install read
#: "moved"; pruned, an upgrade would erase the destination's own.
X4_KEEP_LOCAL="x4-paths.env x4-housekeep.json .claude/x4-paths.env .claude/settings.local.json .claude/backups .claude/.reference-buildid .codex/hooks.json .codex/config.toml .opencode/opencode.jsonc tools/basex/basex/data tools/basex/basex/coverage-x4raw.json tools/basex/basex/coverage-x4eff.json tools/basex/_eff tools/basex/stage-manifest.json"

#: THE TRACKED SET, when the source is a git checkout.
#:
#: The two lists above are a hand-kept second copy of `.gitignore`, and they drift:
#: installing from a maintainer checkout carried 33 files git does not track,
#: 2.3 GB of them BaseX databases. `.gitignore` IS the enumeration of what must not
#: leave the machine, so when the source is a work tree the copy set is
#: `git ls-files` -- filtered through the same two lists, so a tracked template
#: still ships and nothing keep-local travels even if someone tracks it.
#:
#: TWO clauses, each with its own falsification twin in the installer suite
#: (and each mutated away separately there, because either one alone shadows the
#: other on the obvious fixtures):
#:   * $SRC is the TOP of its work tree. A toolkit sitting inside SOMEONE ELSE's
#:     repo -- a zip extracted into a game root that is itself a repo -- is
#:     governed by that repo's idea of what is tracked, not the toolkit's.
#:   * the tracked set COVERS every copy item present on disk. A repo that
#:     tracks CLAUDE.md and .claude/ but not tools/ -- the shape of a game-root
#:     repo -- would otherwise install no tools at all, silently. This also
#:     covers `git init` with nothing added.
#: Anything else walks the disk exactly as before -- a release zip holds only tracked
#: files anyway -- and X4_TRACKED_WHY says why, for a source that IS a checkout.
#: Fills X4_TRACKED (relative paths) and X4_TRACKED_ABSENT (tracked but deleted from
#: the working tree); cached, because three callers need it.
X4_TRACKED_STATE=""; X4_TRACKED=(); X4_TRACKED_ABSENT=0; X4_TRACKED_WHY=""
_tracked_copy_set() {
  [ -n "$X4_TRACKED_STATE" ] && { [ "$X4_TRACKED_STATE" = yes ]; return; }
  X4_TRACKED_STATE=no
  command -v git >/dev/null 2>&1 || return 1
  local top p junk skip item first all=() heads=" "
  top="$(git -C "$SRC" rev-parse --show-toplevel 2>/dev/null)" || return 1
  if [ -z "$top" ] || ! same_dir "$top" "$SRC"; then
    X4_TRACKED_WHY="the source sits inside another repository ($top)"
    return 1
  fi
  while IFS= read -r -d '' p; do all+=("$p"); heads="$heads${p%%/*} "; done < <(git -C "$SRC" ls-files -z 2>/dev/null)
  for item in $X4_ITEMS; do
    [ -e "$SRC/$item" ] || continue
    case "$heads" in *" $item "*) : ;; *)
      X4_TRACKED_WHY="git tracks nothing under '$item', which is on disk"
      return 1 ;;
    esac
  done
  for p in "${all[@]}"; do
    first="${p%%/*}"; skip=1
    for item in $X4_ITEMS; do [ "$item" = "$first" ] && skip=0; done
    for junk in $X4_COPY_PRUNE $X4_KEEP_LOCAL; do
      case "$p" in "$junk"|"$junk"/*) skip=1 ;; esac
    done
    for junk in $X4_KEEP_LOCAL; do
      case "$p" in "$junk".example) : ;; "$junk".*) skip=1 ;; esac
    done
    [ "$skip" = 1 ] && continue
    if [ -f "$SRC/$p" ]; then X4_TRACKED+=("$p"); else X4_TRACKED_ABSENT=$((X4_TRACKED_ABSENT + 1)); fi
  done
  X4_TRACKED_STATE=yes
  return 0
}

#: Copy the tracked set into DEST, one `cp` per run of files sharing a directory:
#: a cp per file costs a process each, which is seconds under Git Bash.
copy_tracked_set() {   # copy_tracked_set DEST
  local dest="$1" p dir cur="" batch=()
  for p in "${X4_TRACKED[@]}"; do
    dir="."; case "$p" in */*) dir="${p%/*}" ;; esac
    if [ "$dir" != "$cur" ] && [ "${#batch[@]}" -gt 0 ]; then
      mkdir -p "$dest/$cur"; cp -- "${batch[@]}" "$dest/$cur/"; batch=()
    fi
    cur="$dir"; batch+=("$SRC/$p")
  done
  if [ "${#batch[@]}" -gt 0 ]; then mkdir -p "$dest/$cur"; cp -- "${batch[@]}" "$dest/$cur/"; fi
  return 0
}

#: Copy $SRC/REL to DEST/REL, never descending into a pruned relative path.
#:
#: The virtualenv used to be copied in full (1,407 files, 38 MB) and deleted on
#: arrival, while the CHANGELOG said it no longer travels. Worse than wasteful:
#: under `set -e` a failure among those copies aborts BEFORE both the prune and
#: the config restore, so the most expensive thing copied was also the thing most
#: likely to break the install -- and nothing wanted it copied.
#:
#: `cp -r` has no portable exclude. rsync is absent from Git Bash and from a stock
#: macOS, and tar's --exclude differs between GNU tar and bsdtar, so the walk is
#: done here -- and ONLY where needed: a directory containing no pruned path is
#: still copied in one cp, so only tools/ is ever descended into.
#:
#: `cp -r "$src/." "$dest/$rel/"`, not `cp -r "$src" "$dest/"`: the second copies a
#: directory INTO an existing directory of the same name on an upgrade, which is
#: how you get .claude/.claude. This form merges, and is idempotent.
copy_item() {   # copy_item REL DEST
  # `src` is assigned on its OWN line. `local a="$1" b="$SRC/$a"` does not work: bash
  # expands every word of the `local` command before the builtin assigns any of them,
  # so `$a` is still unset there -- and under `set -u` that is a hard "unbound
  # variable", which is how the upgrade case failed the moment this was added.
  local rel="$1" dest="$2" junk needs_walk=0 child src
  src="$SRC/$rel"
  for junk in $X4_COPY_PRUNE $X4_KEEP_LOCAL; do
    [ "$junk" = "$rel" ] && return 0
    case "$junk" in "$rel"/*) needs_walk=1 ;; esac
  done
  # AND EVERY SIBLING OF A KEEP-LOCAL FILE, not just the exact name. `.bak-<stamp>`
  # and `.tmp<pid>` sit beside the config, carry X4_NEXUS_KEY by construction, and
  # travelled to the destination because this matched two literal strings.
  #
  # ★ The same arc widened .gitignore from those two names to `x4-paths.env.*` and
  # explained why -- and left THIS matching exactly. The ignore rule and the copy
  # rule describe one set; only one of them had been told. When you generalise a
  # rule, find its twin.
  #
  # `.example` is re-included for the same reason .gitignore negates it: the
  # templates SHIP, and a blanket prefix skip stops the installer installing its
  # own example files.
  for junk in $X4_KEEP_LOCAL; do
    case "$rel" in
      "$junk".example) : ;;
      "$junk".*)       return 0 ;;
    esac
  done
  if [ ! -d "$src" ] || [ "$needs_walk" = 0 ]; then
    mkdir -p "$dest/$(dirname "$rel")"
    if [ -d "$src" ]; then
      mkdir -p "$dest/$rel"
      cp -r "$src/." "$dest/$rel/"
    else
      cp "$src" "$dest/$rel"
    fi
    return 0
  fi
  mkdir -p "$dest/$rel"
  for child in "$src"/* "$src"/.[!.]* "$src"/..?*; do
    [ -e "$child" ] || continue
    copy_item "$rel/$(basename "$child")" "$dest"
  done
}

copy_toolkit() {
  refuse_if_dry_run "copying the toolkit into" "$1"  # copy_toolkit DEST  — copy tracked toolkit files (never game data / local config)
  precheck_project_settings "$1"
  local dest="$1"; mkdir -p "$dest/.claude"
  # An UNEDITED KB some release shipped (scripts/shipped-instruction-hashes.txt) is ours to
  # replace: preserving it saved a KNOWLEDGEBASE.pre-4.0-<sha>.md of the toolkit's own text on
  # every upgrade (release review H6). Only a KB that is neither the source's nor a shipped one
  # can hold the user's notes.
  if [ -f "$dest/KNOWLEDGEBASE.md" ] && ! cmp -s "$dest/KNOWLEDGEBASE.md" "$SRC/KNOWLEDGEBASE.md" \
     && ! _h_is_shipped "$dest" KNOWLEDGEBASE.md; then
    if ! _oc_python; then
      echo "REFUSING: preserving the existing knowledgebase needs Python >= 3.10; no toolkit files were copied." >&2
      return 1
    fi
    "${X4_OC_PY[@]}" "$SRC/tools/agent-harness.py" notes-preserve --destination "$dest" --reference "$SRC/KNOWLEDGEBASE.md" || {
      echo "REFUSING: legacy notes preservation failed; no toolkit files were copied." >&2; return 1;
    }
  fi
  local item

  # BEFORE the copy loop, and that ordering IS the fix. `cp -r "$SRC/.claude"`
  # below overwrites these two, so a backup taken AFTERWARDS preserves the SOURCE
  # machine's file and reports it as the user's. MEASURED 2026-09-02 against a
  # source that had itself been set up -- which is what any working toolkit folder
  # looks like: the destination's X4_NEXUS_KEY was gone from every file, the .bak
  # held the source's values, and the run printed "kept your existing".
  # A reassurance that fires exactly when the thing it names has been destroyed is
  # worse than saying nothing at all.
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local keep
  # `mods` carries the game extension x4live needs (README: "copy that folder into
  # {game}/extensions/"). Omitting it shipped a documented instruction pointing at a
  # directory the installer never created.
  # PRUNE THE DESTINATION FIRST. This list also runs after the loop, and running it
  # ONLY after was the defect: uv hardlinks package files from a shared cache, so once
  # both sides have been synced the same file has the same INODE in each and `cp -r`
  # refuses with "are the same file". MEASURED 2026-09-02 on the documented upgrade
  # path: hundreds of those, exit 1, a half-copied destination -- and because `set -e`
  # kills the script at the cp, neither the prune below nor the config restore after it
  # ever ran. A cleanup that only runs after the copy cannot make the copy possible.
  local junk
  for junk in $X4_COPY_PRUNE; do
    rm -rf "$dest/$junk" 2>/dev/null || true
  done
  local tracked=0
  _tracked_copy_set && tracked=1
  if [ "$tracked" = 1 ]; then
    # ANNOUNCED: a step that narrows the data says so.
    echo "  source is a git checkout: copying the ${#X4_TRACKED[@]} file(s) git tracks in the copy set;"
    echo "  untracked and ignored files (built databases, caches, scratch output) stay behind."
  elif [ -n "$X4_TRACKED_WHY" ]; then
    echo "  [note] not copying git's tracked set: $X4_TRACKED_WHY -- copying what is on disk."
    if [ "$X4_TRACKED_ABSENT" -gt 0 ]; then
      echo "  [note] $X4_TRACKED_ABSENT tracked file(s) are deleted in the source working tree, so not copied"
    fi
  fi
  for item in $X4_ITEMS; do
    # NAMED, never silently skipped -- that silence is how the absent mods/ folder
    # survived a whole release.
    [ -e "$SRC/$item" ] || { MISSING="$MISSING $item"; continue; }
    [ "$tracked" = 1 ] || copy_item "$item" "$dest"
  done
  if [ "$tracked" = 1 ]; then copy_tracked_set "$dest"; fi
  # A VIRTUALENV MUST NOT TRAVEL. uv hardlinks package files from a shared cache,
  # so once the source and the destination have each been synced the same file has
  # the SAME INODE in both -- and `cp -r` then fails with "are the same file", 1,334
  # times, part way through, leaving a half-copied destination. `set -e` kills the
  # script there, before the restore below, so there is no backup either.
  # pyvenv.cfg and the Scripts shims also embed absolute paths, so a copied venv
  # points back at the source. setup.sh recreates it anyway.
  # ...and again afterwards, so the SOURCE machine's virtualenv does not linger here.
  # pyvenv.cfg and the Scripts shims embed absolute paths, so a copied venv points back
  # at the machine it came from. setup.sh recreates it.
  for junk in $X4_COPY_PRUNE; do
    rm -rf "$dest/$junk" 2>/dev/null || true
  done
  # THE BACKUP/RESTORE ROUND TRIP IS GONE, not repaired. These two files are now
  # in X4_KEEP_LOCAL, so the copy never touches them -- and a file that is never
  # overwritten needs no backup and no restore.
  #
  # Removing it fixes two defects at once rather than handling them. The restore
  # sat 53 lines after the backup with NO trap between, so any failure in the copy
  # loop skipped it and left an orphaned .bak; and the restore itself WROTE to the
  # config, so a config the user had locked (as README instructs) failed the
  # install -- at the restore, after the copy had already succeeded.
  #
  # MEASURED 2026-09-07: with the copy skipped but the round trip still present,
  # the upgrade still died with `cp: cannot create regular file .../x4-paths.env:
  # Permission denied` -- the restore, not the copy. Half the fix was no fix.
  [ -n "$MISSING" ] && echo "  [note] not in the source, so not copied:$MISSING"
  return 0
}

#: The key=value lines write_paths_env OWNS, as it would write them now.
#: Factored out so the precondition below and the writer cannot disagree about
#: what "would change" means -- two renderings of one rule is the defect this
#: installer already carries a comment about elsewhere.
#: HOISTED to file scope. It used to be defined INSIDE write_paths_env, which
#: means it does not exist until that function has been entered -- so the
#: precondition check above, which runs BEFORE any writing, called a function
#: that was not there yet. Bash reports `command not found`, the substitution
#: yields an empty string, every rendered value comes out blank, and the
#: comparison therefore ALWAYS differs: the check refused every upgrade,
#: including the ones it should have waved through. A helper two callers need
#: cannot live inside one of them.
_esc_env_value() {
  local v="$1"
  v="${v%/}"                       # trailing separator, either dialect
  v="${v%\\}"
  v="${v//\\/\\\\}"                # backslashes FIRST, or we double the ones added below
  v="${v//\"/\\\"}"
  v="${v//\$/\\\$}"
  v="${v//\`/\\\`}"
  printf '%s' "$v"
}

_owned_lines_new() {   # _owned_lines_new TOOLKIT_DIR
  local t="$1"
  echo "X4_TOOLKIT=\"$(_esc_env_value "$t")\""
  [ -n "$GAME" ]    && echo "X4_GAME=\"$(_esc_env_value "$GAME")\""
  echo "X4_REFERENCE=\"$(_esc_env_value "${REFERENCE:-$t/reference}")\""
  [ -n "$PROFILE" ] && echo "X4_PROFILE=\"$(_esc_env_value "$PROFILE")\""
  [ -n "$PROFILE" ] && echo "X4_DEBUGLOG=\"$(_esc_env_value "$PROFILE")/debug.txt\""
  [ -n "$MODS" ]    && echo "X4_MODS=\"$(_esc_env_value "$MODS")\""
  # CONDITIONAL, matching install.ps1. Emitted unconditionally, with neither
  # --game nor --extensions this wrote `X4_EXTENSIONS=""` while PowerShell omitted
  # the key -- two configs from one input, reachable via --method global with no
  # game detected. An empty value is a claim; an absent key is not.
  [ -n "${EXTENSIONS:-${GAME:+$GAME/extensions}}" ] && echo "X4_EXTENSIONS=\"$(_esc_env_value "${EXTENSIONS:-${GAME:+$GAME/extensions}}")\""
  [ -n "$XRCAT" ]   && echo "XRCATTOOL=\"$(_esc_env_value "$XRCAT")\""
  return 0
}

#: The same keys as they stand in the file today. Comments and carried keys are
#: excluded deliberately: carried keys come FROM the file so they can never
#: differ, and a timestamp header changes on every run, which would make every
#: upgrade look like a change and defeat the whole check.
CR=$(printf '\r')   # one carriage return, built once
_owned_lines_old() {   # _owned_lines_old CONFIG_FILE
  local f="$1" line key
  # EXISTS-BUT-NOT-A-READABLE-FILE is its own answer. `[ -f ]` is false for a
  # DIRECTORY standing where the config belongs, so the old form returned "no
  # config" for a path that plainly is one -- and the caller then compared an
  # empty result against the rendered lines and read it as "the paths changed".
  # Absent, unreadable and different are three states, not two.
  if [ -e "$f" ] && [ ! -f "$f" ]; then
    echo "__X4_UNREADABLE__"
    return 0
  fi
  [ -f "$f" ] || return 0
  # READABLE, or say so. A config held open by an editor, an AV scanner or a sync
  # client gives `Device or resource busy` here, and an unreadable file used to
  # produce raw shell errors and an empty result -- which the caller then compared
  # against the rendered lines and read as "everything changed". Cannot-read is
  # not the same as differs, and it must not be silently promoted to it.
  if ! [ -r "$f" ] || ! head -c 1 "$f" >/dev/null 2>&1; then
    echo "__X4_UNREADABLE__"
    return 0
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    # STRIP THE CR. `read -r` keeps it and `_owned_lines_new` renders without one,
    # so a config saved by any Windows editor compared UNEQUAL forever: bash
    # refused the documented locked upgrade with a remedy that cannot help, while
    # PowerShell's Get-Content strips CR and reported "already matches". One
    # input, opposite verdicts -- and the test written for that path could not see
    # it, because the installer it had just run wrote LF.
    line="${line%$CR}"
    # THE KEY IS TRIMMED. An INDENTED owned key was not recognised as owned, so it
    # fell through to the carry-over block and was emitted AFTER the authoritative
    # line -- and bash sources the last assignment. MEASURED: `  X4_GAME="/OLD"`
    # re-installed with `--game <new>` yielded the OLD root, rc 0, silent. The
    # config's own header tells the user to edit it freely, so an indented key is a
    # supported edit. This precondition shares the extraction with the carry-over
    # loop below, so it was blind to the same line and could not stop the run.
    case "$line" in '#'*|'') continue ;; *=*) key="${line%%=*}"; key="${key#"${key%%[![:space:]]*}"}" ;; *) continue ;; esac
    case " X4_TOOLKIT X4_GAME X4_REFERENCE X4_PROFILE X4_DEBUGLOG X4_MODS X4_EXTENSIONS XRCATTOOL " in
      *" $key "*) echo "$line" ;;
    esac
  done < "$f"
  return 0
}

#: PRECONDITION, checked before ANY file is written and in dry-run too.
#:
#: `x4lock` marks the path config read-only and README tells users to run it, so
#: the documented upgrade path met a bare `cp: Permission denied` on item 1 of 16
#: -- after the copy had already begun, with no statement of what had landed.
#: MEASURED 2026-09-07 on a real install and reduced to a fixture.
#:
#: Two outcomes, and the split is the point. An upgrade that does not need to
#: CHANGE the config never writes it, so the lock is irrelevant and the install
#: proceeds. An upgrade that genuinely must change it REFUSES, up front, naming
#: the unlock command -- because an installer that silently unlocked would defeat
#: the mechanism the user deliberately turned on.
#: THE COPY SET, named ONCE. It was written out twice -- in the copy loop and in
#: the dry-run listing -- and a third caller now needs it (the locked-target
#: precheck below). Two statements of one list is the shape this file already
#: warns about elsewhere; three would be asking for the listing and the copy to
#: disagree about what an install actually writes.
#:
#: COMMON to every agent target. Each agent's own files are named once more, in its
#: X4_AGENT_ITEMS_<name> set below, and never here: an item in both lists is an item
#: two answers can disagree about.
X4_COPY_ITEMS="tools bin scripts mods KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md.example README.md CHANGELOG.md LICENSE setup.sh install.sh install.ps1 SETUP_PROMPT.txt ADAPTING.md RETENTION.md x4-housekeep.json.example .gitignore .gitattributes x4-paths.env.example"

#: PER-AGENT sets (audit F8: the installers shipped no AGENTS.md at all). `agent/` -- the
#: neutral source the generator reads -- is in NO set: an installed toolkit is
#: runtime-only (user decision #9), and the release zip still carries it for anyone who
#: wants to regenerate. install.ps1 holds the same sets as $X4AgentItems, and
#: test_installers_agree parses both.
X4_AGENT_ITEMS_claude=".claude CLAUDE.md"
X4_AGENT_ITEMS_codex="AGENTS.md .codex .agents"
X4_AGENT_ITEMS_generic="AGENTS.md .agents"
X4_AGENT_ITEMS_opencode="AGENTS.md .opencode"
#: `all` (the default) installs EVERY name here, OpenCode included (user decision L-Q1,
#: 2026-10-02). OpenCode is BEST EFFORT -- from its docs and source, not measured.
X4_AGENT_NAMES="claude codex generic opencode"

#: --agent auto (lane H): how each agent is DETECTED. One row per agent; an agent with no
#: row is never detected, so a new agent adds rows here and touches no code. DETECT = a
#: program name on PATH; MARK = a path in the destination. Never a bare `.claude/`: every
#: 3.x install created one (x4-paths.env lived there), whatever the agent. install.ps1 holds the
#: same tables; test_installers_agree parses both.
X4_AGENT_DETECT_claude="claude"
X4_AGENT_MARK_claude="CLAUDE.md .claude/settings.json"
X4_AGENT_DETECT_codex="codex"
X4_AGENT_MARK_codex=".codex"
X4_AGENT_DETECT_opencode="opencode"
X4_AGENT_MARK_opencode=".opencode opencode.json opencode.jsonc"
#: On Windows a program is found as NAME or NAME + one of these. Git Bash's `command -v`
#: and PowerShell's `Get-Command` disagree about which codex shim they find (MEASURED), so
#: neither is used: both installers walk PATH themselves with this one list.
X4_DETECT_EXTS=".exe .cmd .bat .ps1"

#: The token the generated Codex/generic skills carry (gen-agent-trees.py's TOKEN), and
#: what it is rendered to in the copy THIS install writes (user decision #2). Per OS, not
#: per installer: Codex runs its shell commands through PowerShell on Windows, where
#: `$X4_TOOLKIT` expands to an EMPTY string (MEASURED, lane A) -- so install.sh under Git
#: Bash on Windows renders the PowerShell form too.
X4_TOOLKIT_TOKEN='{{TOOLKIT}}'
X4_TOOLKIT_RENDER_windows='$env:X4_TOOLKIT'
X4_TOOLKIT_RENDER_posix='$X4_TOOLKIT'
X4_TOKEN_DIRS=".agents .opencode"

#: The frozen Codex hook definitions (lane B), rendered for THIS destination's absolute
#: root. The result is per-machine config (X4_KEEP_LOCAL): it never travels.
X4_CODEX_HOOKS_TMPL="agent/targets/codex/hooks.json.tmpl"

# --- resolve --agent, BEFORE anything is written ------------------------------------
# WHITESPACE SEPARATES TOO, as in install.ps1 (FX-B2: `-Agent "claude codex"` was accepted and
# `--agent "claude codex"` refused). install.ps1 needs it -- `-Agent claude,codex` typed inside
# PowerShell binds an ARRAY, joined with spaces -- so the two agree on its split rule,
# `\s*,\s*|\s+`: an empty item (",," or a stray edge space) is still refused below.
case "$AGENT" in
  *[[:space:]]*) AGENT="$(printf '%s' "$AGENT" | sed -E 's/[[:space:]]*,[[:space:]]*/,/g; s/[[:space:]]+/,/g')" ;;
esac
case "$AGENT" in
  all) X4_AGENTS="$X4_AGENT_NAMES" ;;
  auto) X4_AGENTS="" ;;   # resolved per destination by resolve_auto_agents, inside the arms
  claude|codex|generic|opencode) X4_AGENTS="$AGENT" ;;
  *,*)
    # C1 (install red-team 2026-10-04): a COMMA LIST (`claude,codex`), each item validated
    # before anything is written; duplicates collapse; `all`/`auto` stand alone.
    X4_AGENTS=""
    _rest="$AGENT,"
    while [ -n "$_rest" ]; do
      _it="${_rest%%,*}"; _rest="${_rest#*,}"
      case "$_it" in
        "") echo "REFUSING: --agent '$AGENT' has an empty item. Supported: $X4_AGENT_NAMES (comma-separated), all, auto. Nothing has been changed." >&2
            exit 2 ;;
        all|auto) echo "REFUSING: --agent '$_it' cannot be combined with other agents in a list ('$AGENT'). Nothing has been changed." >&2
            exit 2 ;;
        claude|codex|generic|opencode)
          case " $X4_AGENTS " in *" $_it "*) ;; *) X4_AGENTS="${X4_AGENTS:+$X4_AGENTS }$_it" ;; esac ;;
        *) echo "REFUSING: unknown agent '$_it' in --agent '$AGENT'. Supported: $X4_AGENT_NAMES (comma-separated), all, auto. Nothing has been changed." >&2
           exit 2 ;;
      esac
    done
    _picked=" $X4_AGENTS "; X4_AGENTS=""    # canonical order, as install.ps1
    for _it in $X4_AGENT_NAMES; do
      case "$_picked" in *" $_it "*) X4_AGENTS="${X4_AGENTS:+$X4_AGENTS }$_it" ;; esac
    done
    ;;
  *)
    echo "REFUSING: unknown --agent '$AGENT'. Supported: $X4_AGENT_NAMES all. Nothing has been changed." >&2
    exit 2 ;;
esac
#: THE RESOLVED COPY SET: common items plus each selected agent's, once each. Every
#: consumer -- the copy, the tracked-set filter, the dry-run listing and the locked-target
#: precheck -- reads THIS, so they cannot disagree about what an install writes.
_h_resolve_items() {
  local _a _i _set
  X4_ITEMS="$X4_COPY_ITEMS"
  for _a in $X4_AGENTS; do
    eval "_set=\$X4_AGENT_ITEMS_$_a"
    for _i in $_set; do
      case " $X4_ITEMS " in *" $_i "*) ;; *) X4_ITEMS="$X4_ITEMS $_i" ;; esac
    done
  done
}
_h_resolve_items

#: Print where NAME is found on the detect PATH, or fail. X4_INSTALL_DETECT_PATH is a TEST
#: SEAM (the harness points it at a sandbox); unset, the real PATH is walked.
_h_on_path() {   # NAME
  local n="$1" p d e dirs
  p="${X4_INSTALL_DETECT_PATH:-$PATH}"
  # A Windows-form seam reaches Git Bash unconverted (MEASURED: `C:/x` stays `C:/x`), and
  # splitting it on ':' would cut the drive letter off.
  if [ "$OS" = windows ] && [ -n "${X4_INSTALL_DETECT_PATH:-}" ] && command -v cygpath >/dev/null 2>&1; then
    p="$(cygpath -u -p "$p")"
  fi
  IFS=: read -r -a dirs <<< "$p"
  for d in "${dirs[@]}"; do
    [ -n "$d" ] || continue
    if [ "$OS" = windows ]; then
      for e in "" $X4_DETECT_EXTS; do
        [ -f "$d/$n$e" ] && { printf '%s' "$d/$n$e"; return 0; }
      done
    else
      [ -f "$d/$n" ] && [ -x "$d/$n" ] && { printf '%s' "$d/$n"; return 0; }
    fi
  done
  return 1
}

#: --agent auto: select exactly the agents detected for DEST, each with its reason; none
#: detected selects all (user decision H-Q1) and SAYS so. Then the copy set is re-derived.
resolve_auto_agents() {   # DEST
  [ "$AGENT" = auto ] || return 0
  local dest="$1" a n m found why det="" says="" _dn _mk
  for a in $X4_AGENT_NAMES; do
    why=""
    eval "_dn=\${X4_AGENT_DETECT_$a:-}"
    eval "_mk=\${X4_AGENT_MARK_$a:-}"
    for n in $_dn; do
      if found="$(_h_on_path "$n")"; then why="on PATH: $found"; break; fi
    done
    if [ -z "$why" ]; then
      for m in $_mk; do
        if [ -d "$dest/$m" ]; then why="destination has $m/"; break; fi
        if [ -e "$dest/$m" ]; then why="destination has $m"; break; fi
      done
    fi
    [ -n "$why" ] || continue
    det="$det${det:+ }$a"
    says="$says${says:+, }$a ($why)"
  done
  if [ -n "$det" ]; then
    X4_AGENTS="$det"
    echo "  --agent auto: $says"
  else
    X4_AGENTS="$X4_AGENT_NAMES"
    echo "  --agent auto: no agent detected (none on PATH, no marker in the destination);"
    echo "                installing all: $X4_AGENT_NAMES"
  fi
  _h_resolve_items
}

#: --codex-doc-max-bytes (lane H): the NUMBER is checked here, before anything is written;
#: that it needs the Codex target is checked per arm, once --agent auto has resolved.
X4_CODEX_DOC_MIN=32768
X4_CODEX_DOC_MAX=1048576
if [ -n "$CODEX_DOC_MAX" ]; then
  case "$CODEX_DOC_MAX" in
    *[!0-9]*|0*) _h_bad=1 ;;
    *) _h_bad=0; { [ "${#CODEX_DOC_MAX}" -le 7 ] && [ "$CODEX_DOC_MAX" -ge "$X4_CODEX_DOC_MIN" ] \
         && [ "$CODEX_DOC_MAX" -le "$X4_CODEX_DOC_MAX" ]; } || _h_bad=1 ;;
  esac
  if [ "$_h_bad" = 1 ]; then
    echo "REFUSING: --codex-doc-max-bytes '$CODEX_DOC_MAX' is not a whole number from $X4_CODEX_DOC_MIN to $X4_CODEX_DOC_MAX." >&2
    echo "  Nothing has been changed." >&2
    exit 2
  fi
fi

#: Failures that make the install INCOMPLETE, accumulated and reported at the end.
#: Defined HERE, above every writer that records into it.
FAILED=""
add_failed() { FAILED="$FAILED${FAILED:+, }$1"; }

_item_selected() { case " $X4_ITEMS " in *" $1 "*) return 0 ;; esac; return 1; }

#: The selected agents whose every item is present in the source -- what can actually
#: land. A source without the generated Codex tree installs no Codex target, and the
#: summary must not claim one.
_agents_landed() {
  local a i ok out="" _set
  for a in $X4_AGENTS; do
    ok=1
    eval "_set=\$X4_AGENT_ITEMS_$a"
    for i in $_set; do [ -e "$SRC/$i" ] || ok=0; done
    [ "$ok" = 1 ] && out="$out${out:+, }$a"
  done
  printf '%s' "$out"
}

# --- AGENTS.md: never overwrite one the toolkit did not write ------------------------
#
# 0 of 23 tagged releases before 4.0 shipped AGENTS.md (MEASURED 2026-10-02, per tag),
# so an AGENTS.md in a destination today was written by its user, and "differs from the
# one we ship" is exactly "not ours". It is MOVED ASIDE, never overwritten -- unless its
# hash is one a release SHIPPED (scripts/shipped-instruction-hashes.txt, generated from
# every release tag by gen-shipped-hashes.py), which makes it ours to replace.
_same_text() {   # _same_text A B -- equal once CRLF is ignored
  [ "$(tr -d '\r' < "$1")" = "$(tr -d '\r' < "$2")" ]
}

# --- shipped-version hashes (lane H) ---------------------------------------------------
#: The data file both installers read. Absent (a synthetic or truncated source) means
#: "nothing is known to be ours", so every differing file is KEPT -- the safe direction.
X4_SHIPPED_HASHES="scripts/shipped-instruction-hashes.txt"

#: SHA-256 of stdin as lowercase hex; fails when no tool exists or the output is not a hash.
_h_sha256() {
  local out
  if command -v sha256sum >/dev/null 2>&1; then out="$(sha256sum)" || return 1
  elif command -v shasum >/dev/null 2>&1; then out="$(shasum -a 256)" || return 1
  elif command -v openssl >/dev/null 2>&1; then out="$(openssl dgst -sha256 -r)" || return 1
  else return 1; fi
  out="${out%% *}"
  out="$(printf '%s' "$out" | tr 'A-F' 'a-f')"
  case "$out" in *[!0-9a-f]*|'') return 1 ;; esac
  [ "${#out}" -eq 64 ] || return 1
  printf '%s' "$out"
}

#: THE CANONICAL HASH (one definition, three implementations: gen-shipped-hashes.py,
#: this, Get-HCanonicalSha256): drop a leading UTF-8 BOM, delete every CR, strip trailing
#: LFs (the command substitution does that), SHA-256. GNU sed MEASURED under Git Bash;
#: BSD sed on macOS is NOT measured -- a failure there only over-keeps, never overwrites.
_h_canonical_sha256() {   # FILE -> hex, or fails
  [ -f "$1" ] && [ -r "$1" ] || return 1
  printf '%s' "$(LC_ALL=C sed $'1s/^\xef\xbb\xbf//' "$1" | tr -d '\r')" | _h_sha256
}

#: Is (HASH, NAME) a row of the shipped list? rc 0 yes, 1 no, 2 there is no list.
_h_known_hash() {   # HASH NAME
  local f="$SRC/$X4_SHIPPED_HASHES" h n rest
  [ -f "$f" ] || return 2
  while IFS=' ' read -r h n rest || [ -n "$h" ]; do
    h="${h%$CR}"; n="${n%$CR}"
    case "$h" in '#'*|'') continue ;; esac
    [ "$h" = "$1" ] && [ "$n" = "$2" ] && return 0
  done < "$f"
  return 1
}

#: Is DEST/NAME a file the toolkit SHIPPED -- the source's copy, or any release's?
#: rc 0 yes; 1 no (it is the user's). Never fails open: no hash tool means "not known".
_h_is_shipped() {   # DEST NAME
  local h s
  h="$(_h_canonical_sha256 "$1/$2")" || return 1
  if s="$(_h_canonical_sha256 "$SRC/$2")" && [ "$h" = "$s" ]; then return 0; fi
  _h_known_hash "$h" "$2"
}

# A blanket --over-existing does not establish ownership of personal policies,
# permissions or post hooks. Refuse before moving instructions or copying bytes.
precheck_project_settings() {   # DEST (copy branch only)
  _item_selected .claude || return 0
  local dest="$1" file="$1/.claude/settings.json"
  if [ -L "$dest/.claude" ] || [ -L "$file" ]; then
    echo "REFUSING: project settings traverse a link; review this destination separately. Nothing has been changed." >&2
    return 1
  fi
  [ -e "$file" ] || return 0
  if [ -f "$file" ] && _h_is_shipped "$dest" .claude/settings.json; then return 0; fi
  echo "REFUSING: custom project settings need a reviewed upgrade; --over-existing cannot replace them." >&2
  echo "  Use tools/agent-harness.py upgrade-stage to retain and review your settings, handlers and permissions." >&2
  echo "  Nothing has been changed." >&2
  return 1
}

#: The first free name BASE.md / BASE.<stamp>.md / BASE.<stamp>-N.md in DEST.
_h_aside_name() {   # DEST BASE
  local d="$1" n="$2.md" stamp i=0
  [ -e "$d/$n" ] || { printf '%s' "$n"; return 0; }
  stamp="$(date +%Y%m%d-%H%M%S)"
  n="$2.$stamp.md"
  while [ -e "$d/$n" ]; do i=$((i + 1)); n="$2.$stamp-$i.md"; done
  printf '%s' "$n"
}

#: 3.x -> 4.0. The name DEST's CLAUDE.md would be KEPT as, or fails when it is ours (the
#: source's, or a shipped release's) or no Claude copy happens. Shared by the dry-run
#: listing and the mover, so they cannot differ. No installer ever REWROTE a CLAUDE.md
#: (READ, every tag), so a differing hash is the user's edit, not an installer's.
_h_claude_md_move_target() {   # DEST
  local dest="$1"
  _item_selected CLAUDE.md || return 1
  [ -f "$SRC/CLAUDE.md" ] && [ -f "$dest/CLAUDE.md" ] || return 1
  _h_is_shipped "$dest" CLAUDE.md && return 1
  _h_aside_name "$dest" "X4-NOTES.pre-4.0"
}

#: Why the decision above is narrower than it looks, when it is: SAID, never silent.
_h_hash_caveat() {
  if [ ! -f "$SRC/$X4_SHIPPED_HASHES" ]; then
    echo "  [note] this source has no $X4_SHIPPED_HASHES, so no shipped version is known:"
    echo "         any CLAUDE.md that differs from this one is treated as yours and kept."
  elif ! printf '' | _h_sha256 >/dev/null 2>&1; then
    echo "  [note] no SHA-256 tool (sha256sum / shasum / openssl) was found, so shipped versions"
    echo "         cannot be recognised: any differing CLAUDE.md is treated as yours and kept."
  fi
  return 0
}

#: R2-e (second install red-team): the kept copy is the WHOLE old CLAUDE.md, and the user
#: wants their EDITS. The release that shipped the original is named by the destination's
#: CHANGELOG.md (read BEFORE the copy replaces it): its first `## vX` heading.
X4_RELEASES_URL="https://github.com/WingedGuardian/x4-ai-toolkit/releases"
X4_RAW_URL="https://raw.githubusercontent.com/WingedGuardian/x4-ai-toolkit"
_r2_prev_version() {   # DEST -> vX.Y.Z, or fails
  local v
  [ -f "$1/CHANGELOG.md" ] || return 1
  v="$(sed -n 's/^## \(v[0-9][0-9.]*[0-9]\).*/\1/p' "$1/CHANGELOG.md" | head -n 1)"
  [ -n "$v" ] || return 1
  printf '%s' "$v"
}
_r2_your_edits_hint() {   # DEST KEPT-NAME VERSION-or-empty
  echo "         To see only YOUR edits, compare it with the CLAUDE.md the toolkit shipped:"
  if [ -n "$3" ]; then
    echo "           $X4_RAW_URL/$3/CLAUDE.md   ($3: your previous install, per its CHANGELOG.md)"
  else
    echo "           the CLAUDE.md of the release you had installed: $X4_RELEASES_URL"
  fi
  echo "           save it, then:  git diff --no-index \"<saved CLAUDE.md>\" \"$1/$2\""
}

preserve_user_claude_md() {   # DEST -- AFTER every precheck, BEFORE the copy
  local dest="$1" to prev
  to="$(_h_claude_md_move_target "$dest")" || return 0
  prev="$(_r2_prev_version "$dest" || true)"
  refuse_if_dry_run "keeping your CLAUDE.md as X4-NOTES.pre-4.0 in" "$dest"
  if ! mv -- "$dest/CLAUDE.md" "$dest/$to"; then
    echo "ERROR: could not move $dest/CLAUDE.md aside to $to. Nothing else has been changed." >&2
    exit 1
  fi
  echo "  [note] your CLAUDE.md is not one this toolkit ever shipped, so it was KEPT (not lost) as:"
  echo "           $dest/$to"
  echo "         That copy is NO LONGER LOADED by any agent: the 4.0 CLAUDE.md replaced it."
  echo "         To bring your notes back, merge what you want into KNOWLEDGEBASE.local.md in"
  echo "         the same folder -- every agent's instructions read it (and a legacy X4-NOTES.md),"
  echo "         and the toolkit never overwrites it."
  _r2_your_edits_hint "$dest" "$to" "$prev"
  _h_hash_caveat
}

#: The 3.x example config `.claude/x4-paths.env.example` (install red-team 2026-10-04: an
#: upgrade left it beside 4.0's root example). Prints `remove` when it is an UNEDITED shipped
#: copy (a `.claude/x4-paths.env.example` row of the shipped list), `keep` when it differs from
#: every one, nothing when there is none. Never deletes a file a user may have edited.
_old_example_action() {   # DEST
  [ -f "$1/.claude/x4-paths.env.example" ] && [ -f "$SRC/x4-paths.env.example" ] || return 0
  if _h_is_shipped "$1" ".claude/x4-paths.env.example"; then printf remove; else printf keep; fi
}
retire_old_paths_example() {   # DEST -- AFTER the copy
  local dest="$1" old="$1/.claude/x4-paths.env.example"
  case "$(_old_example_action "$dest")" in
    remove)
      if rm -f -- "$old" && [ ! -e "$old" ]; then
        echo "  [note] removed the 3.x .claude/x4-paths.env.example (an unedited shipped copy); 4.0's"
        echo "         example is x4-paths.env.example at the toolkit root."
      else
        echo "  [warn] could not remove the stale 3.x $old (it is unedited, so deleting it is safe)."
      fi ;;
    keep)
      echo "  [note] kept $dest/.claude/x4-paths.env.example: it differs from every example a"
      echo "         release shipped (your edits?). 4.0 never reads it; delete it when you are done." ;;
  esac
}

_agents_md_aside_name() {   # DEST -> the first name that does not exist yet
  local d="$1" n="AGENTS.pre-4.0.md" stamp i=0
  [ -e "$d/$n" ] || { printf '%s' "$n"; return 0; }
  stamp="$(date +%Y%m%d-%H%M%S)"
  n="AGENTS.pre-4.0.$stamp.md"
  while [ -e "$d/$n" ]; do i=$((i + 1)); n="AGENTS.pre-4.0.$stamp-$i.md"; done
  printf '%s' "$n"
}

#: Prints the name the destination's AGENTS.md would be moved to, or fails when no
#: move is needed. Shared by the dry-run listing and the mover, so they cannot differ.
_agents_md_move_target() {   # DEST
  local dest="$1"
  _item_selected AGENTS.md || return 1
  [ -f "$SRC/AGENTS.md" ] && [ -f "$dest/AGENTS.md" ] || return 1
  _same_text "$dest/AGENTS.md" "$SRC/AGENTS.md" && return 1
  local h
  if h="$(_h_canonical_sha256 "$dest/AGENTS.md")" && _h_known_hash "$h" AGENTS.md; then return 1; fi
  _agents_md_aside_name "$dest"
}

preserve_user_agents_md() {   # DEST -- AFTER the locked-target precheck, BEFORE the copy
  local dest="$1" to
  to="$(_agents_md_move_target "$dest")" || return 0
  refuse_if_dry_run "moving your AGENTS.md aside in" "$dest"
  if ! mv -- "$dest/AGENTS.md" "$dest/$to"; then
    echo "ERROR: could not move $dest/AGENTS.md aside to $to. Nothing else has been changed." >&2
    exit 1
  fi
  echo "  [note] your AGENTS.md differs from the one this toolkit ships, so it was KEPT as:"
  echo "           $dest/$to"
}

# --- {{TOOLKIT}} in the Codex / generic skills ----------------------------------------
#: PER TARGET SHELL, not only per OS (v4.0.0 review R4-6). Codex runs PowerShell on Windows;
#: OpenCode runs bash when X4_OPENCODE_SHELL=bash -- the setting its plugin reads too -- and
#: bash expands `$env:X4_TOOLKIT` to ':X4_TOOLKIT'. No single spelling expands in both shells
#: (MEASURED: PowerShell splits `"C:/a b"/tools` into two arguments, so not even a quoted
#: absolute path works). Only the EXPLICIT setting decides, never $SHELL: the installer's own
#: shell is not OpenCode's, and the two installers must render alike. install.ps1 holds the
#: same rule (Get-ToolkitRenderValue).
_toolkit_render_value() {   # [TARGET DIR]
  if [ "$OS" = windows ] && ! { [ "${1:-}" = .opencode ] && [ "${X4_OPENCODE_SHELL:-}" = bash ]; }; then
    printf '%s' "$X4_TOOLKIT_RENDER_windows"
  else printf '%s' "$X4_TOOLKIT_RENDER_posix"; fi
}

#: Rewrite the token in the files THIS install copied -- derived from the SOURCE, never
#: from a destination glob, so a user's own file under .agents/ is never touched (the
#: rule install_global_claude learned the hard way). Verified per file afterwards.
render_toolkit_token() {   # DEST
  local dest="$1" d f rel to n=0 said=""
  refuse_if_dry_run "rendering the skill token in" "$dest"
  for d in $X4_TOKEN_DIRS; do
    _item_selected "$d" || continue
    [ -d "$SRC/$d" ] || continue
    to="$(_toolkit_render_value "$d")"
    said="$said${said:+, }$d/ as $to"
    while IFS= read -r f; do
      rel="${f#"$SRC/"}"
      [ -f "$dest/$rel" ] || continue
      grep -qF "$X4_TOOLKIT_TOKEN" "$dest/$rel" 2>/dev/null || continue
      sed -i.x4tok "s#$X4_TOOLKIT_TOKEN#$to#g" "$dest/$rel" && rm -f "$dest/$rel.x4tok"
      if grep -qF "$X4_TOOLKIT_TOKEN" "$dest/$rel" 2>/dev/null; then
        add_failed "$rel (the $X4_TOOLKIT_TOKEN token could not be rendered)"
      else
        n=$((n + 1))
      fi
    done < <(find "$SRC/$d" -type f 2>/dev/null)
  done
  [ "$n" -gt 0 ] && echo "  rendered $X4_TOOLKIT_TOKEN in $n skill file(s): $said"
  return 0
}

#: In place (the source IS the destination) nothing is copied, so nothing is rendered:
#: a toolkit checkout holds the generator's output, and rewriting it would make
#: `gen-agent-trees.py --check` fail there. Said, never silent.
note_in_place_token() {
  local d
  for d in $X4_TOKEN_DIRS; do
    _item_selected "$d" || continue
    if [ -d "$SRC/$d" ] && grep -rqF "$X4_TOOLKIT_TOKEN" "$SRC/$d" 2>/dev/null; then
      echo "  [note] installing in place: $d/ keeps the $X4_TOOLKIT_TOKEN token unrendered."
      echo "         An agent reading those skills should read it as $(_toolkit_render_value "$d")."
    fi
  done
  return 0
}

# --- the Codex hook definitions ------------------------------------------------------
#: Literal replace-all. Not ${s//pat/rep}: under bash 5.2's patsub_replacement an `&`
#: in a path becomes the matched text, and backslash handling in the replacement
#: differs between the bash 3.2 macOS ships and 5.x. Prefix/suffix removal has neither.
_replace_all() {   # TEXT TOKEN VALUE -> stdout
  local s="$1" tok="$2" val="$3" out=""
  while :; do
    case "$s" in
      *"$tok"*) out="$out${s%%"$tok"*}$val"; s="${s#*"$tok"}" ;;
      *) break ;;
    esac
  done
  printf '%s' "$out$s"
}

_abs_dir() {   # an EXISTING dir -> absolute, forward slashes (C:/... on Windows)
  local d
  d="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then d="$(cygpath -m "$d")"; fi
  printf '%s' "$d"
}

#: The template with {{ROOT}} (forward slashes) and {{ROOT_WIN}} (backslashes) filled,
#: each JSON-escaped -- the contract of lane B's render_codex_hooks_json, which the
#: installer cannot import (the generator needs ruamel.yaml) and so re-implements.
#: test_install_over_existing pins all three renderings to one parsed answer.
render_codex_hooks_json() {   # DEST -> stdout (no trailing newline)
  local root win t bs
  bs="$(printf '%b' '\134')"
  root="$(_abs_dir "$1")" || return 1
  _codex_root_safe "$root" || return 1
  t="$(tr -d '\r' < "$SRC/$X4_CODEX_HOOKS_TMPL")" || return 1
  win="$(_replace_all "$root" / "$bs")"
  root="$(_replace_all "$root" "$bs" "$bs$bs")"
  win="$(_replace_all "$win" "$bs" "$bs$bs")"
  t="$(_replace_all "$t" '{{ROOT_WIN}}' "$win")"
  t="$(_replace_all "$t" '{{ROOT}}' "$root")"
  case "$t" in *'{{ROOT'*) return 1 ;; esac
  printf '%s' "$t"
}

_codex_selected() { _item_selected .codex && [ -d "$SRC/.codex" ]; }

_codex_root_safe() {
  case "$1" in *'"'*|*"$CR"*|*$'\n'*|*'%'*|*'!'*|*'$'*|*'`'*) return 1 ;; esac
  return 0
}

_codex_matches_template() {   # DEST EXPECTED TEXT
  local f="$1/.codex/hooks.json"
  [ -f "$f" ] || return 1
  [ "$(tr -d '\r' < "$f")" = "$2" ] && return 0
  [ -f "$SRC/tools/x4_harness/x4_upgrade_render.py" ] || return 1
  _oc_python || return 1
  "${X4_OC_PY[@]}" -B "$SRC/tools/x4_harness/x4_upgrade_render.py" check-codex \
    --root "$1" --template "$SRC/$X4_CODEX_HOOKS_TMPL" >/dev/null 2>&1
}

#: Is DEST's .codex/hooks.json an UNEDITED rendering of a template some release SHIPPED?
#: Un-render it -- this root's two JSON-escaped spellings back to {{ROOT_WIN}} / {{ROOT}},
#: the inverse of render_codex_hooks_json -- and look the canonical hash up in the shipped
#: list under the template's name, the rule .claude/settings.json already follows. Without it
#: the first template change after a release refused every unedited upgrade (release review
#: H4). install.ps1 mirrors it in Test-CodexShippedRendering.
_codex_is_shipped_rendering() {   # DEST
  local f="$1/.codex/hooks.json" root win t bs h
  [ -f "$f" ] || return 1
  bs="$(printf '%b' '\134')"
  root="$(_abs_dir "$1")" || return 1
  _codex_root_safe "$root" || return 1
  win="$(_replace_all "$root" / "$bs")"
  root="$(_replace_all "$root" "$bs" "$bs$bs")"
  win="$(_replace_all "$win" "$bs" "$bs$bs")"
  t="$(LC_ALL=C sed $'1s/^\xef\xbb\xbf//' "$f" | tr -d '\r')" || return 1
  t="$(_replace_all "$t" "$win" '{{ROOT_WIN}}')"
  t="$(_replace_all "$t" "$root" '{{ROOT}}')"
  h="$(printf '%s' "$t" | _h_sha256)" || return 1
  _h_known_hash "$h" "$X4_CODEX_HOOKS_TMPL"
}

#: _x4lock_steps DEST [FILE] -- the "unlock, re-run, lock" steps, aimed at the DESTINATION's own
#: x4lock (FX-B2, delta review). They named `scripts/x4lock.py` relative to the cwd -- the SOURCE's
#: copy, whose manifest never holds a separate install's files: `unlock` answered "not in the
#: protected manifest", exit 0, and the re-run refused again, a loop. No FILE: --all.
_x4lock_steps() {
  local d="$1" dn what
  dn="$d"
  command -v cygpath >/dev/null 2>&1 && dn="$(cygpath -m "$d" 2>/dev/null || printf '%s' "$d")"
  if [ -n "${2:-}" ]; then what="\"$2\""; else what="--all"; fi
  if [ -f "$d/scripts/x4lock.py" ]; then
    echo "      python \"$dn/scripts/x4lock.py\" unlock $what --toolkit \"$dn\"" >&2
    echo "      <re-run this command>"                                              >&2
    echo "      python \"$dn/scripts/x4lock.py\" lock --toolkit \"$dn\""        >&2
  else
    echo "      (no x4lock in $dn: clear the read-only attribute on the file(s) above by hand --" >&2
    echo "       attrib -R on Windows, chmod u+w elsewhere -- then re-run this command)"       >&2
  fi
}

#: PRECONDITION, before any write: a READ-ONLY hooks.json (x4lock protects it) that this
#: install would CHANGE refuses up front. Unchanged, the lock never applies.
precheck_codex_hooks_json() {   # DEST
  local dest="$1" f="$1/.codex/hooks.json" new
  _codex_selected || return 0
  if ! _codex_root_safe "$dest"; then
    echo "REFUSING: Codex hooks cannot safely quote this destination. Choose a root without shell expansion characters, quotes or line breaks. Nothing has been changed." >&2
    exit 1
  fi
  [ -e "$f" ] || return 0
  [ -f "$SRC/$X4_CODEX_HOOKS_TMPL" ] || return 0     # the writer reports INCOMPLETE
  new="$(render_codex_hooks_json "$dest")" || return 0
  _codex_matches_template "$dest" "$new" && return 0
  if [ -w "$f" ]; then
    _codex_is_shipped_rendering "$dest" && return 0   # an older SHIPPED rendering: ours to replace
    echo "REFUSING: custom Codex hook definitions need a reviewed upgrade; --over-existing cannot replace them." >&2
    echo "  Use tools/agent-harness.py upgrade-stage. Nothing has been changed." >&2
    exit 1
  fi
  echo                                                                          >&2
  echo "REFUSING: the Codex hook definitions must change, and the file is READ-ONLY." >&2
  echo "      $f"                                                               >&2
  echo "  Review custom Codex hook definitions with upgrade-stage before unlocking or replacing them." >&2
  echo "  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:" >&2
  _x4lock_steps "$dest" "$f"
  exit 1
}

# --- the OpenCode permission deny rules (lane L, BEST EFFORT) ------------------------
#: OpenCode's PRIMARY guard layer is a per-machine .opencode/opencode.jsonc holding explicit
#: deny rules for THIS install's roots. The toolkit's own renderer writes it (it sources the
#: guards' loader, so the roots are the ones the guards see); it never overwrites a file
#: without its banner, and every failure is NAMED as INCOMPLETE, never silent.
X4_OPENCODE_NOTE="OpenCode:  BEST EFFORT, CLI only, from docs, not measured. The OpenCode desktop app is NOT supported (its plugin hooks never fire, anomalyco/opencode#38604)."
_opencode_selected() { _item_selected .opencode && [ -d "$SRC/.opencode" ]; }

#: The first Python >= 3.10 (X4_PYTHON, then py -3 on Windows, python3, python; with
#: X4_NO_PYTHON_FALLBACK=1 only X4_PYTHON) -- the plugin's own order. Sets the ARRAY
#: X4_OC_PY (a path may hold spaces or a drive colon); fails when there is none.
_oc_py_ok() { "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
_oc_python() {
  local c
  X4_OC_PY=()
  if [ -n "${X4_PYTHON:-}" ] && _oc_py_ok "$X4_PYTHON"; then X4_OC_PY=("$X4_PYTHON"); return 0; fi
  [ "${X4_NO_PYTHON_FALLBACK:-}" = 1 ] && return 1
  if [ "$OS" = windows ] && _oc_py_ok py -3; then X4_OC_PY=(py -3); return 0; fi
  for c in python3 python; do
    if _oc_py_ok "$c"; then X4_OC_PY=("$c"); return 0; fi
  done
  return 1
}

# --- R6-04 (v4.0.0 review): the OS protection on reference/ is PRINTED, never applied -----
#: Layer 2 (scripts/x4refguard.py) is an ACL / attribute change, so an installer never applies
#: it on the user's behalf. bin/unpack-reference.sh applies it to a tree it has just unpacked
#: (setup.sh only PRINTS the unpack command); an install or upgrade over an EXISTING reference/
#: applied nothing and said nothing. So: when x4refguard reports reference/ present and not
#: protected, say so and name the step. install.ps1's Write-RefguardStep prints the same lines.
#: C4 (install red-team 2026-10-04): the summary ALWAYS states the protection, in every state --
#: silence read as "handled". FX-B2: a state x4refguard could not give (no parseable answer) is
#: said as such, never as "no reference/ tree yet".
#: R2-b (second install red-team): a HUMAN reads this, so plain `apply` -- it shows the folder
#: and a count and ASKS (B3). `--yes` is for a non-interactive caller (an agent, after you agreed).
X4_REFGUARD_STEP_CMD='python scripts/x4refguard.py apply'
print_refguard_step() {   # TOOLKIT
  local tk="$1" out state="" sentinel=""
  [ -f "$tk/scripts/x4refguard.py" ] || return 0
  if _oc_python; then
    out="$(cd "$tk" && X4_TOOLKIT="$tk" "${X4_OC_PY[@]}" scripts/x4refguard.py status --json 2>/dev/null)" || true
    state="$(printf '%s' "$out" | sed -n 's/.*"state": "\([a-z]*\)".*/\1/p' | head -n 1)"
    sentinel="$(printf '%s' "$out" | sed -n 's/.*"sentinel": \([a-z]*\).*/\1/p' | head -n 1)"
    # FX-B2: python ran and gave no parseable state: a NON-ANSWER, never "no reference/ tree yet".
    [ -n "$state" ] || state=noanswer
  elif [ -d "${REFERENCE:-$tk/reference}" ]; then
    state="unknown (no Python >= 3.10 to ask x4refguard)"
  fi
  echo
  case "$state" in
    protected)
      echo "Reference: reference/ carries the OS-level deny-delete protection (x4refguard: protected)." ;;
    ""|unconfigured)
      echo "Reference: no reference/ tree yet, so nothing is OS-protected. The protection is not applied by the installer:"
      echo "           bin/unpack-reference.sh applies it to the tree it unpacks (see Next below)." ;;
    unsupported)
      echo "Reference: this platform has no OS-level protection mechanism (a disclosed gap; not applied by the installer)." ;;
    noanswer)
      echo "Reference: the OS-level protection state could NOT be read (x4refguard gave no answer). Check it, in"
      echo "           $tk:  python scripts/x4refguard.py status" ;;
    *)
      # R2-a: apply REFUSES a tree without the sentinel, so naming it here was a dead end.
      if [ "$sentinel" = false ]; then
        echo "Reference: reference/ exists but has no .unpacked-and-locked sentinel, i.e. it is not a finished unpack,"
        echo "           so it is not OS-protected and cannot be yet (see Next below)."
        return 0
      fi
      [ "$state" = absent ] && state="not applied"
      echo "Reference: reference/ exists and is NOT OS-protected (x4refguard: $state)."
      echo "           It is not applied by the installer (a permission change is yours to make). To add the"
      echo "           OS-level deny-delete layer (any process, hooks or not), run in $tk:"
      echo "             $X4_REFGUARD_STEP_CMD"
      echo "           (it shows the folder and a file count, then asks you; --yes answers for you, e.g. when"
      echo "           an agent runs it after you agreed)" ;;
  esac
}

#: A READ-ONLY (x4lock'd) .opencode/opencode.jsonc that would have to change refuses UP FRONT,
#: like the Codex hooks.json above (v4.0.0 review R4-5: it had no precheck, so an upgrade copied
#: the whole toolkit and THEN failed at the render). "Would have to change" is the renderer's
#: own `check`, run from the SOURCE (the destination's copy may be older) against the
#: destination's roots. Cannot ask (no python, no renderer): the writer reports it.
precheck_opencode_config() {   # DEST
  local dest="$1" f="$1/.opencode/opencode.jsonc"
  _opencode_selected || return 0
  [ -e "$f" ] || return 0
  [ -w "$f" ] && return 0
  [ -f "$SRC/.opencode/hooks/opencode_config.py" ] || return 0
  _oc_python || return 0
  X4_TOOLKIT="$dest" "${X4_OC_PY[@]}" "$SRC/.opencode/hooks/opencode_config.py" check --root "$dest" >/dev/null 2>&1 && return 0
  echo                                                                          >&2
  echo "REFUSING: the OpenCode deny rules must change, and the file is READ-ONLY." >&2
  echo "      $f"                                                               >&2
  echo "  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:" >&2
  _x4lock_steps "$dest" "$f"
  exit 1
}

write_opencode_config() {   # DEST
  local dest="$1" out rc
  _opencode_selected || return 0
  refuse_if_dry_run "rendering the OpenCode deny rules into" "$dest/.opencode/opencode.jsonc"
  if [ ! -f "$dest/.opencode/hooks/opencode_config.py" ]; then
    add_failed ".opencode/opencode.jsonc (the permission deny layer was NOT installed: .opencode/hooks/opencode_config.py is missing)"
    return 0
  fi
  if ! _oc_python; then
    add_failed ".opencode/opencode.jsonc (the permission deny layer was NOT installed: no Python >= 3.10; set X4_PYTHON and run: python .opencode/hooks/opencode_config.py write --root \"$dest\")"
    return 0
  fi
  # X4_TOOLKIT=DEST: the roots of THIS install, as its guards will see them once
  # X4_TOOLKIT points here -- never a previous toolkit's that the caller still exports.
  rc=0   # `|| rc=$?`, never a bare `rc=$?`: under `set -e` a failing assignment EXITS the installer
  out="$(X4_TOOLKIT="$dest" "${X4_OC_PY[@]}" "$dest/.opencode/hooks/opencode_config.py" write --root "$dest" 2>&1)" || rc=$?
  if [ "$rc" != 0 ]; then
    add_failed ".opencode/opencode.jsonc (the permission deny layer was NOT installed: $(printf '%s' "$out" | tr '\r\n' '  ' | cut -c1-300); fix it, then run: python .opencode/hooks/opencode_config.py write --root \"$dest\")"
    return 0
  fi
  echo "  $out"
}

X4_CODEX_HOOKS_WRITTEN=0
write_codex_hooks_json() {   # DEST
  local dest="$1" f="$1/.codex/hooks.json" new tmp
  _codex_selected || return 0
  refuse_if_dry_run "rendering the Codex hook definitions into" "$f"
  if [ ! -f "$SRC/$X4_CODEX_HOOKS_TMPL" ]; then
    add_failed ".codex/hooks.json (the source has no $X4_CODEX_HOOKS_TMPL, so Codex's hooks were NOT installed)"
    return 0
  fi
  if ! new="$(render_codex_hooks_json "$dest")"; then
    add_failed ".codex/hooks.json (could not render it for $dest: a path containing a quote or a newline cannot be written into it)"
    return 0
  fi
  if _codex_matches_template "$dest" "$new"; then
    echo "  [note] $f already matches; left untouched, so its Codex review still holds"
    return 0
  fi
  mkdir -p "$dest/.codex"
  tmp="$f.tmp$$"
  if ! { printf '%s\n' "$new" > "$tmp" && mv -f "$tmp" "$f"; }; then
    rm -f "$tmp"
    add_failed ".codex/hooks.json (could not write $f)"
    return 0
  fi
  X4_CODEX_HOOKS_WRITTEN=1
  echo "  wrote $f"
}

# --- Codex project_doc_max_bytes (lane H, opt-in) -------------------------------------
X4_CODEX_DOC_WRITTEN=""
_h_codex_config() { printf '%s' "$1/.codex/config.toml"; }

#: The ROOT-table project_doc_max_bytes value in FILE (keys before the first [table]).
_h_codex_doc_value() {   # FILE -> value, or fails when absent
  local line t k v
  [ -f "$1" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$CR}"
    t="${line#"${line%%[![:space:]]*}"}"            # leading whitespace off
    case "$t" in '['*) return 1 ;; esac              # the first [table] ends the root keys
    k="${t%%=*}"
    [ "$k" != "$t" ] || continue                      # no '=' on this line
    k="${k%"${k##*[![:space:]]}"}"                    # trailing whitespace off the key
    [ "$k" = project_doc_max_bytes ] || continue
    v="${t#*=}"; v="${v%%#*}"; v="$(printf '%s' "$v" | tr -d '[:space:]')"
    printf '%s' "$v"; return 0
  done < "$1"
  return 1
}

#: A Codex-configuring flag on an install that does not select Codex is REFUSED (rc 2).
require_codex_for_doc_cap() {
  [ -n "$CODEX_DOC_MAX" ] || return 0
  _codex_selected && return 0
  echo "REFUSING: --codex-doc-max-bytes configures Codex, and this install does not select Codex" >&2
  echo "  (agents: ${X4_AGENTS:-none}). Nothing has been changed." >&2
  exit 2
}

#: PRECONDITION, before any write: a READ-ONLY config.toml this flag would CHANGE refuses.
precheck_codex_doc_max_bytes() {   # DEST
  local f old
  [ -n "$CODEX_DOC_MAX" ] || return 0
  f="$(_h_codex_config "$1")"
  [ -e "$f" ] || return 0
  [ -w "$f" ] && return 0
  old="$(_h_codex_doc_value "$f")" && return 0          # present: never changed, so no write
  echo                                                                          >&2
  echo "REFUSING: --codex-doc-max-bytes must add a line to a READ-ONLY file."    >&2
  echo "      $f"                                                               >&2
  echo "  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:" >&2
  _x4lock_steps "$1" "$f"
  exit 1
}

write_codex_doc_max_bytes() {   # DEST -- after the dispatch
  local f old tmp line
  [ -n "$CODEX_DOC_MAX" ] || return 0
  f="$(_h_codex_config "$1")"
  refuse_if_dry_run "writing project_doc_max_bytes into" "$f"
  line="project_doc_max_bytes = $CODEX_DOC_MAX  # X4 toolkit installer (--codex-doc-max-bytes)"
  if old="$(_h_codex_doc_value "$f")"; then
    if [ "$old" = "$CODEX_DOC_MAX" ]; then
      echo "  [note] $f already sets project_doc_max_bytes = $old; left untouched"
    else
      echo "  [WARNING] $f already sets project_doc_max_bytes = $old, not $CODEX_DOC_MAX. Left unchanged."
    fi
    return 0
  fi
  mkdir -p "$1/.codex"
  tmp="$f.tmp$$"
  # ROOT keys must precede every [table], so the line goes FIRST; the rest is kept as is.
  if ! { printf '%s\n' "$line" > "$tmp" && { [ ! -f "$f" ] || cat "$f" >> "$tmp"; } && mv -f "$tmp" "$f"; }; then
    rm -f "$tmp"
    add_failed ".codex/config.toml (could not write project_doc_max_bytes into $f)"
    return 0
  fi
  X4_CODEX_DOC_WRITTEN="$CODEX_DOC_MAX"
  echo "  wrote project_doc_max_bytes = $CODEX_DOC_MAX into $f"
}

#: Destination files the copy would overwrite that CANNOT be written.
#:
#: precheck_config guarded ONE file. x4lock's manifest under a game root covers
#: about twenty-six -- CLAUDE.md, KNOWLEDGEBASE.md, .claude/settings.json, the
#: hooks, the skills, the agents -- and for `--method in-game` the game root IS
#: the destination, so all of them sit inside the copy set. Guarding the env file
#: alone moved the failure from one filename to another.
#:
#: MEASURED 2026-09-07 with one locked hook in the destination: install.sh died
#: mid-copy on a bare `cp: Permission denied`, half-copied, with no unlock hint;
#: install.ps1 returned 0 and OVERWROTE it, because `Copy-Item -Force` clears the
#: read-only attribute. One installer destroyed what the lock protected and the
#: other left a mess, and nothing decided which was correct. Refusing before
#: either writes anything is the only answer that is right for both.
_destination_problem() {   # DEST REL locked|redirected -> prints a problematic path
  local dest="$1" rel="$2" mode="$3" path="$1" part
  if [ "$mode" = locked ]; then
    path="$dest/$rel"
    if [ -e "$path" ] && [ ! -w "$path" ]; then printf '%s\n' "$path"; fi
    return 0
  fi
  while [ -n "$rel" ]; do
    part="${rel%%/*}"
    path="$path/$part"
    if [ -L "$path" ]; then printf '%s\n' "$path"; return 0; fi
    case "$rel" in */*) rel="${rel#*/}" ;; *) break ;; esac
  done
  return 0
}

_locked_targets() {   # DEST [locked|redirected], using the same copy set for both
  local dest="$1" mode="${2:-locked}" item rel f junk skip
  # THE SAME SET THE COPY WRITES. From a git checkout that is the tracked set; a
  # locked destination file standing where an UNTRACKED source file sits is never
  # written, and refusing over it would block an install for nothing.
  if _tracked_copy_set; then
    for rel in "${X4_TRACKED[@]}"; do
      _destination_problem "$dest" "$rel" "$mode"
    done
    return 0
  fi
  for item in $X4_ITEMS; do
    [ -e "$SRC/$item" ] || continue
    if [ -d "$SRC/$item" ]; then
      ( cd "$SRC/$item" 2>/dev/null && find . -type f -print 2>/dev/null ) | while IFS= read -r rel; do
        rel="${rel#./}"
        skip=0
        for junk in $X4_COPY_PRUNE $X4_KEEP_LOCAL; do
          case "$item/$rel" in "$junk"|"$junk"/*) skip=1 ;; esac
        done
        [ "$skip" = 1 ] && continue
        _destination_problem "$dest" "$item/$rel" "$mode"
      done
    else
      _destination_problem "$dest" "$item" "$mode"
    fi
  done
  return 0
}

#: PRECONDITION over the whole copy set, before anything is written, dry run too.
precheck_locked_targets() {   # precheck_locked_targets DEST
  local dest="$1" blocked n
  blocked="$(_locked_targets "$dest" redirected)"
  if [ -n "$blocked" ]; then
    echo 'REFUSING: destination links redirect toolkit writes. Nothing has been changed.' >&2
    printf '%s\n' "$blocked" | LC_ALL=C sort -u | head -8 >&2
    echo 'Review the linked paths and select an independent destination before installing.' >&2
    exit 1
  fi
  blocked="$(_locked_targets "$dest")"
  [ -z "$blocked" ] && return 0
  n="$(printf '%s\n' "$blocked" | grep -c .)"
  echo                                                                          >&2
  echo "REFUSING: $n file(s) this install would overwrite are READ-ONLY."        >&2
  printf '%s\n' "$blocked" | head -8 | sed 's/^/      /'                        >&2
  [ "$n" -gt 8 ] && echo "      ... and $((n - 8)) more NOT LISTED"              >&2
  echo                                                                          >&2
  echo "  This is x4lock doing its job -- README tells you to run it, and it"    >&2
  echo "  cannot tell an installer from any other process that writes here."     >&2
  echo "  Nothing has been changed."                                            >&2
  echo                                                                          >&2
  echo "  Unlock, re-run this installer, then lock again:"                       >&2
  _x4lock_steps "$dest"
  exit 1
}

#: _i_assign_lines FILE -> the comparable content of a path config (Plan 3 lane I): its
#: KEY=value lines with CR and indent stripped, comments and blanks dropped, sorted. A comment
#: difference AGREES and a quoting difference DIFFERS. The same rule as
#: `_paths._assignments`; install.ps1 mirrors it in Get-IAssignLines.
_i_assign_lines() {
  tr -d '\r' < "$1" 2>/dev/null | sed 's/^[[:space:]]*//' | grep -v '^#' | grep '=' | LC_ALL=C sort || true
}

#: _i_differing_keys A B -> the KEY NAMES whose assignments differ; never a value (the file
#: carries X4_NEXUS_KEY).
_i_differing_keys() {
  { _i_assign_lines "$1"; _i_assign_lines "$2"; } | LC_ALL=C sort | uniq -u | cut -d= -f1 \
    | sed 's/^export[[:space:]][[:space:]]*//' | LC_ALL=C sort -u | tr '\n' ' ' || true
}

_i_backup_file() {
  # mktemp exclusively reserves this operation's file. Never copy over a
  # timestamp-named predecessor, including two installs in the same second.
  local src="$1" bak
  bak="$(mktemp "$src.bak-$(date +%Y%m%d-%H%M%S).XXXXXX")" || return 1
  if ! cp "$src" "$bak" || ! cmp -s "$src" "$bak"; then
    rm -f -- "$bak"
    return 1
  fi
  printf '%s\n' "$bak"
}

#: MOVE a lone 3.x config to the root, retaining its read-only attribute. When
#: both configs agree, RENAME the 3.x one to a free .bak-<stamp> name (no clobber,
#: read-only kept). Differing copies are refused by precheck_config before any write.
_i_migrate_paths_env() {
  local new="$1/x4-paths.env" old="$1/.claude/x4-paths.env" bak
  [ -f "$old" ] || return 0
  if [ ! -e "$new" ]; then
    mv "$old" "$new" || { echo "ERROR: could not move $old -> $new; it is still where it was." >&2; return 1; }
    echo "  [migrated] $old -> $new (the 3.x location; 4.0 reads the toolkit root). To undo: move it back."
    return 0
  fi
  if [ "$(_i_assign_lines "$new")" = "$(_i_assign_lines "$old")" ]; then
    # A RENAME, as install.ps1's no-clobber [IO.File]::Move: the same file, so a locked
    # (read-only) copy stays locked -- a copy plus `rm` gave a new, writable file and
    # deleted the original (release review H3). `mv -n` never replaces an existing name;
    # whether it moved is judged by the result, since a refused -n is rc 0 on older mv.
    local _try
    for _try in 1 2 3 4 5 6 7 8; do
      bak="$(mktemp -u "$old.bak-$(date +%Y%m%d-%H%M%S).XXXXXX")" || break
      [ -e "$bak" ] && continue
      mv -n -- "$old" "$bak" 2>/dev/null || true
      if [ ! -e "$old" ] && [ -f "$bak" ]; then
        echo "  [migrated] the 3.x $old agreed with $new; renamed it to $bak (to undo: rename it back)"
        return 0
      fi
    done
    echo "ERROR: could not rename the agreeing 3.x $old to a free .bak-<stamp> name; it is still there." >&2
    return 1
  fi
  echo "ERROR: $new and the 3.x $old DIFFER on: $(_i_differing_keys "$new" "$old")-- refusing to choose." >&2
  return 1
}

precheck_config() {   # precheck_config TOOLKIT_DIR
  local t="$1" f="$1/x4-paths.env" old="$1/.claude/x4-paths.env"
  # TWO CONFIGS THAT DIFFER (Plan 3 lane I): refused before anything is written, dry run too.
  # The 4.x file outranks the 3.x one, so an older guard copy reading .claude/ would protect a
  # different tree; which values are right is the user's call, never the installer's.
  if [ -f "$f" ] && [ -f "$old" ] && [ "$(_i_assign_lines "$f")" != "$(_i_assign_lines "$old")" ]; then
    echo                                                                        >&2
    echo "REFUSING: two path configs that DIFFER:"                              >&2
    echo "      $f"                                                             >&2
    echo "      $old   (the 3.x location)"                                      >&2
    echo "  They differ on: $(_i_differing_keys "$f" "$old")"                   >&2
    echo "  Nothing has been changed. Keep the values you want in the first, delete" >&2
    echo "  or rename the second, then re-run. To see them side by side:"       >&2
    # --root, and the SOURCE's copy: without --root `status` reports X4_TOOLKIT's (or the
    # cwd's) config -- possibly another toolkit (v4.0.0 review R4-11).
    echo "      python \"$SRC/scripts/x4config.py\" status --root \"$t\""          >&2
    exit 1
  fi
  if [ "$DRY_RUN" = 1 ] && [ -f "$old" ]; then
    if [ -e "$f" ]; then echo "  --dry-run: would RENAME the agreeing 3.x $old to $old.bak-<stamp>"
    else echo "  --dry-run: would MOVE $old -> $f (the 3.x location; 4.0 reads the toolkit root)"; fi
  fi
  [ -e "$f" ] || f="$old"                      # judged where it IS: the move keeps its bytes
  [ -e "$f" ] || return 0                      # nothing there to protect
  local _now; _now="$(_owned_lines_old "$f")"
  if [ "$_now" = "__X4_UNREADABLE__" ]; then
    echo "REFUSING: $f exists but cannot be READ, so this run cannot tell whether" >&2
    echo "      your paths would change. Something is holding it open -- an editor," >&2
    echo "      an AV scanner, or a sync client. Nothing has been changed." >&2
    exit 1
  fi
  [ "$_now" = "$(_owned_lines_new "$t")" ] && return 0
  [ -w "$f" ] && return 0
  echo                                                                        >&2
  echo "REFUSING: your path config must change, and it is READ-ONLY."         >&2
  echo "      $f"                                                             >&2
  echo                                                                        >&2
  echo "  This is x4lock doing its job -- README tells you to run it, and it"  >&2
  echo "  cannot tell an installer from any other process that writes here."  >&2
  echo "  Nothing has been changed."                                          >&2
  echo                                                                        >&2
  echo "  Unlock, re-run this installer, then lock again:"                    >&2
  _x4lock_steps "$t"
  echo                                                                        >&2
  echo "  (An upgrade that does NOT change your paths does not need this: it" >&2
  echo "   leaves the config untouched and the lock never applies.)"          >&2
  exit 1
}

write_paths_env() {  # write_paths_env TOOLKIT_DIR
  local t="$1" f="$1/x4-paths.env"
  # THE GATE IS THE FIRST STATEMENT, and it has to stay there.
  #
  # The "nothing to change" fast path below was inserted ABOVE it, which made
  # --dry-run perform a REAL install whenever `same_dir` was true: no copy runs,
  # so this is the only writer reached, the fast path returned before the gate,
  # and execution fell through to `bash setup.sh` at top level -- which synced
  # dependencies for real. MEASURED on all four arms, rc 0, "install complete",
  # no dry-run banner. Proven a regression against the same fixture at c6fe0f0^.
  #
  # An early return added above a gate is invisible to a check that COUNTS call
  # sites, which is what install.ps1's own comment here already warned about --
  # and the same arc then moved the gate below a return anyway.
  refuse_if_dry_run "writing the path config into" "$f"
  # A 3.x config MOVES to the root first (Plan 3 lane I), AFTER the gate, so the fast path
  # below then judges it in its new place -- an unchanged locked config is left untouched.
  _i_migrate_paths_env "$1" || return 1
  # NOTHING TO CHANGE, NOTHING TO WRITE. An upgrade that resolves the same
  # paths used to rewrite this file anyway -- which meant a config the user
  # had locked (as README instructs) failed the whole install for a write
  # that would have changed nothing, and a config with hand-written COMMENTS
  # lost them on every run, because only key=value lines are carried over.
  if [ -f "$f" ] && [ "$(_owned_lines_old "$f")" = "$(_owned_lines_new "$t")" ]; then
    echo "  [note] $f already matches these paths; left untouched"
    return 0
  fi
  mkdir -p "$1"

  # BACKED UP HERE, not at the call sites. `copy_toolkit` backs this file up and puts it
  # back, which covers --method in-game and --method separate; --method global never
  # calls copy_toolkit and went straight to this function, so it rewrote the file with no
  # backup at all.
  #
  # MEASURED 2026-09-02 on this repository, by accident: a `--method global` smoke run
  # with `--toolkit <the repo>` replaced the checkout's own config with sandbox paths and
  # left no .bak beside it. Five cold tests then failed, because the reference path
  # resolved to a directory that does not exist and the skip guarding them did not fire.
  #
  # Third instance of "installing destroys the user's config" in this release. The first
  # two were fixed by adding a backup at one call site each; putting it here means a
  # fourth caller cannot be added without one.
  if [ -f "$f" ]; then
    local _backup
    if _backup="$(_i_backup_file "$f")"; then
      echo "  [note] kept your previous x4-paths.env as $_backup"
    else
      # A backup that silently did not happen is worse than none, because the message
      # above would have said it did.
      # States what is TRUE NOW, not a prediction. This used to say "it is about to
      # be rewritten" and then the run died before rewriting anything -- a warning
      # that was false in the only case that printed it.
      echo "ERROR: could not back up $f. Refusing to rewrite it." >&2
      return 1
    fi
  fi

  # A trailing separator is not cosmetic here. The value lands inside a bash
  # double-quoted string, so a path ending in a backslash writes a line whose quote
  # never closes; `set -a; . "$cfg"` aborts on it and EVERY X4_* comes out unset while
  # the installer reports success. Windows tab-completion appends that backslash, and a
  # drive root is one.
  # KEYS THIS FUNCTION DOES NOT OWN ARE CARRIED OVER. setup.sh tells the user to keep
  # X4_NEXUS_KEY in this file, and the file's own header says "edit freely" -- yet every
  # upgrade rebuilt it from scratch, so both were silently reverted and the key survived
  # only in a .bak the user had no reason to open.
  local owned=" X4_TOOLKIT X4_GAME X4_REFERENCE X4_PROFILE X4_DEBUGLOG X4_MODS X4_EXTENSIONS XRCATTOOL "
  local carried="" line key
  if [ -f "$f" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        '#'*|'') continue ;;
        *=*)     key="${line%%=*}"; key="${key#"${key%%[![:space:]]*}"}" ;;   # trimmed: see write_paths_env
        *)       continue ;;
      esac
      case "$owned" in
        *" $key "*) continue ;;
      esac
      carried="$carried$line
"
    done < "$f"
  fi

  # RENDER TO A TEMP, VERIFY THE TEMP, THEN REPLACE. The redirect used to target
  # the LIVE config, and `>` truncates before the first byte is written -- so a
  # render that failed part way left the user with a half-written config, and the
  # verification below then reported the failure about a file it was already too
  # late to protect. "Refusing to report success" was honest and the config was
  # gone regardless, recoverable only from a .bak the user had to go and find.
  #
  # This is the third instance of one shape today: `_effective._write_db` leaked
  # its temp because the cleanup only existed on one exit path, `_registry.save`
  # already had the answer, and this had the answer inverted -- it wrote to the
  # live path and checked afterwards. Build beside, prove, then move.
  tmp="$f.tmp$$"
  {
    echo "# Written by install.sh ($(date -u +%Y-%m-%dT%H:%MZ)) - edit freely. All paths overridable."
    # ONE RENDERING. These eight lines were re-emitted here by hand while
    # _owned_lines_new claimed in its own docstring to exist so the
    # precondition and the writer could not disagree -- a property asserted
    # and not held, and the X4_EXTENSIONS divergence above is what it hid.
    _owned_lines_new "$t"
    if [ -n "$carried" ]; then
      echo "# --- carried over from your previous x4-paths.env ---"
      printf '%s' "$carried"
    fi
  } > "$tmp" || { rm -f "$tmp"; echo "ERROR: could not write $tmp; your existing config is untouched." >&2; return 1; }

  # VERIFY THE ARTIFACT, never the exit code -- and verify it BEFORE it is live.
  # Parse without execution. Carried assignments are user data, and sourcing
  # them here would execute substitutions/operators that the runtime data loader refuses.
  if ! "$BASH" -n -- "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"
    echo "ERROR: the config this run would write has invalid assignment syntax." >&2
    echo "       It was parsed without executing any carried settings." >&2
    echo "       NOTHING was changed: your existing $f is untouched." >&2
    return 1
  fi
  # PRECHECK_CONFIG IS NOW THE SOLE GUARD ON A LOCKED CONFIG, and that is worth
  # stating rather than leaving to be rediscovered. This writer used to open the LIVE
  # file with `>`, so the OS refused a read-only config even if precheck_config were
  # wrong -- a second, independent answer to the same question. 912cf8e replaced that
  # with build-to-temp then move, which is the right shape for atomicity and removed
  # the backstop as a side effect: MEASURED, both `mv -f` under MSYS and PowerShell's
  # `Move-Item -Force` succeed OVER a read-only destination.
  # 
  # No live defect: precheck_config and the fast path above decide "would this
  # change?" with the same `_owned_lines_old` vs `_owned_lines_new` pair, so a run
  # reaching here has either been allowed or has nothing to write. Any future drift
  # between them is a SILENT overwrite of a locked config rather than a permission
  # error -- which is what the locked-config PAIR in test_install_over_existing.py
  # actually pins: unchanged-and-locked must be allowed with no write, and
  # changed-and-locked must refuse up front.
  mv -f "$tmp" "$f" || { rm -f "$tmp"; echo "ERROR: could not install $f; your existing config is untouched." >&2; return 1; }
  echo "  wrote $f"
  if [ -n "$carried" ]; then
    echo "  [note] carried over $(printf '%s' "$carried" | grep -c .) setting(s) you had added"
  fi
  return 0
}

require_jq_for_global() {
  command -v jq >/dev/null 2>&1 && return 0
  local home_claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  echo "ERROR: --method global needs jq to merge the X4_* env into $home_claude/settings.json." >&2
  echo "       Install jq (https://jqlang.github.io/jq/), or use --method separate/in-game," >&2
  echo "       which do not need it. Nothing has been changed." >&2
  exit 1
}

#: Read-only targets in the GLOBAL destination, checked BEFORE any write.
#:
#: Two defects, and the second was in both installers. (1) This tested `-w` on the
#: skill DIRECTORY, so a read-only SKILL.md inside a writable directory was
#: invisible: the run then reached the copy, `cp` failed with Permission denied, and
#: it ended "1 item(s) could not be copied ... a partial install is not recoverable"
#: -- the outcome the guard exists to prevent. install.ps1 recursed per file and
#: refused correctly, so it was a genuine divergence. (2) It lived INSIDE
#: install_global_claude, which the dispatch calls AFTER write_paths_env, so a
#: refused run had already rewritten the path config. install.ps1's own comment
#: states the rule: "refusing after the path config has already been rewritten is a
#: partial write, which is the shape of the bug rather than a fix" -- which is why
#: Assert-GlobalOverExisting sits ahead of the writer, and this now does too.
precheck_global_locked() {
  local home_claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  local blocked="" _t _f
  for s in "$TOOLKIT/.claude/skills/"x4-*; do
    [ -e "$s" ] || continue
    _t="$home_claude/skills/$(basename "$s")"
    [ -d "$_t" ] || continue
    # EVERY FILE, not the directory: a directory stays writable while the file
    # inside it is read-only, which is exactly the case that got through.
    while IFS= read -r _f; do
      [ -n "$_f" ] || continue
      [ -w "$_f" ] || blocked="$blocked$_f
"
    done <<EOF
$(find "$_t" -type f 2>/dev/null)
EOF
  done
  for a in "$TOOLKIT/.claude/agents/"*.md; do
    [ -e "$a" ] || continue
    _t="$home_claude/agents/$(basename "$a")"
    [ -e "$_t" ] && [ ! -w "$_t" ] && blocked="$blocked$_t
"
  done
  if [ -n "$blocked" ]; then
    echo "REFUSING: file(s) in $home_claude are READ-ONLY and this would overwrite them." >&2
    printf '%s' "$blocked" | head -8 | sed 's/^/      /' >&2
    echo "      Unlock them, or move them aside, and re-run. Nothing has been changed." >&2
    exit 1
  fi
}

install_global_claude() {  # copy skills/agents to ~/.claude and write X4_* env into settings.json
  local home_claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  refuse_if_dry_run "installing skills and agents into" "$home_claude"
  # jq is checked FIRST, before anything is copied. This method cannot complete without
  # it (the settings.json merge below is jq-only), and by the time that merge runs the
  # skills/agents have already been copied AND rewritten to reference $X4_TOOLKIT -- so
  # failing late leaves a half-install whose every skill resolves to an undefined
  # variable. setup.sh treats a missing jq as a warning because it can proceed; this
  # cannot.
  require_jq_for_global   # ONE implementation; the arm calls it before any write

  # LOCKED TARGETS IN THIS DESTINATION TOO. precheck_locked_targets covers the
  # 16 copy items against $TOOLKIT; this arm writes to a SECOND destination that
  # neither precheck knew about, and the invariant is the same one 18b220d
  # states: refusing before either installer writes is the only answer correct
  # for both. MEASURED before this: install.ps1 CLOBBERED a read-only skill via
  # Copy-Item -Force and returned 0; install.sh skipped it and ALSO returned 0.
  mkdir -p "$home_claude/skills" "$home_claude/agents"
  local s a copied=0 failed_copies=0
  # `cp … && copied=…` is an && LIST, which `set -e` does NOT trip -- so a failed
  # copy was invisible and the run still printed "install complete" at rc 0. This
  # file already carries a comment about that exact construct for the jq merge,
  # three functions below. Counted explicitly instead.
  for s in "$TOOLKIT/.claude/skills/"x4-*; do
    [ -e "$s" ] || continue
    if cp -r "$s" "$home_claude/skills/"; then copied=$((copied + 1)); else failed_copies=$((failed_copies + 1)); fi
  done
  for a in "$TOOLKIT/.claude/agents/"*.md; do
    [ -e "$a" ] || continue
    if cp "$a" "$home_claude/agents/"; then copied=$((copied + 1)); else failed_copies=$((failed_copies + 1)); fi
  done
  if [ "$failed_copies" -gt 0 ]; then
    echo "ERROR: $failed_copies item(s) could not be copied into $home_claude." >&2
    echo "       This method keeps no backup, so a partial install is not recoverable." >&2
    exit 1
  fi
  # "Copied nothing" must never print as "installed". MEASURED 2026-09-02: with the x4
  # skills removed this printed `installed x4 skills + agents` and exited 0.
  # install.ps1:173-178 has had this refusal; bash had none.
  if [ "$copied" -eq 0 ]; then
    echo "ERROR: copied NOTHING from $TOOLKIT (.claude/skills/x4-* and .claude/agents/*.md)." >&2
    echo "       Nothing was installed into $home_claude -- this is a FAILURE, not a no-op." >&2
    echo "       Check that --toolkit points at a real toolkit checkout." >&2
    exit 1
  fi
  # global skills/agents run from any repo → resolve the validator via $X4_TOOLKIT, not
  # $CLAUDE_PROJECT_DIR. Rewrite ONLY the files this installer just copied — a user's
  # pre-existing skills/agents may use $CLAUDE_PROJECT_DIR on purpose and must not be
  # touched, so both lists are derived from the TOOLKIT's own contents, never from a
  # destination glob (a pre-existing user skill named x4-* must not match).
  # ...AND THE FILE LIST MUST BE TOOLKIT-DERIVED TOO, not just the directory list.
  # This emitted DESTINATION DIRECTORIES and then ran `grep -rl` inside them. With
  # --over-existing that directory holds the toolkit's files MERGED with the user's,
  # so a note the user kept in ~/.claude/skills/x4-balance/ was rewritten in place by
  # the sed below and `rm -f "$f.bak"` deleted the only copy: rc 0, nothing printed.
  # The rule two lines up was already right; it was applied one level too shallow --
  # toolkit-derived for directories, destination-derived for files. install.ps1 built
  # its list from $srcRoot and was never exposed, which is what named the fix.
  {
    for s in "$TOOLKIT/.claude/skills/"x4-*; do
      [ -e "$s" ] || continue
      # Every file THIS TOOLKIT SHIPS in that skill, mapped to where it was copied.
      # A user's own file has no counterpart under $TOOLKIT, so it cannot be named.
      find "$s" -type f 2>/dev/null | while read -r src; do
        echo "$home_claude/skills/${src#"$TOOLKIT/.claude/skills/"}"
      done
    done
    for a in "$TOOLKIT/.claude/agents/"*.md; do
      [ -e "$a" ] && echo "$home_claude/agents/$(basename "$a")"
    done
    # STATUS-NEUTRAL terminator, and it is load-bearing. A `{ ... }` group's exit
    # status is its LAST command: with no agents/*.md match the `for` above ends on a
    # false `[ -e ]` and returns 1, `pipefail` promotes that to the pipeline, and
    # `set -e` kills the installer HERE -- after the copy, before the jq merge, with
    # no message. An unmatched glob is a normal state, not a failure.
    true
  } | while read -r f; do
    # `|| continue` on both tests is LOAD-BEARING: grep exits 1 when it matches
    # nothing, `set -o pipefail` promotes that to the pipeline's status, and `set -e`
    # then killed the whole installer. MEASURED 2026-09-01: 2 of the 7 shipped skills
    # (x4-debug, x4-probe) contain no $CLAUDE_PROJECT_DIR, so `--method global` exited
    # 1 on this very tree -- after copying skills and agents, and before writing any
    # env. A skill that needs no rewrite is the NORMAL case, not an error.
    [ -f "$f" ] || continue
    grep -q 'CLAUDE_PROJECT_DIR' "$f" 2>/dev/null || continue
    sed -i.bak 's#\$CLAUDE_PROJECT_DIR#$X4_TOOLKIT#g' "$f" && rm -f "$f.bak"
  done
  echo "  installed $copied x4 skill/agent item(s) into $home_claude"
  # STATED, because it is the difference between this layout and the other two, and
  # nothing else in the run mentions it. The hooks are registered in the repo's
  # .claude/settings.json, which this method does not copy, and each is addressed as
  # $CLAUDE_PROJECT_DIR/.claude/hooks/... -- which globally resolves to whatever repo
  # the user has open, not to the toolkit.
  echo
  echo "  NOTE: this layout installs the SKILLS AND AGENTS ONLY."
  echo "        The safety guards -- the command and file hooks, and the automatic"
  echo "        backup before every edit -- are NOT installed by --method global."
  echo "        They are per-project: they live in a repo's .claude/settings.json and"
  echo "        resolve their paths from that project. Use --method in-game or"
  echo "        --method separate in a mod repo to get them there."
  # merge env into settings.json (jq); create if absent
  local sj="$home_claude/settings.json"
  # BACKED UP FIRST. This rewrites the user's GLOBAL Claude settings, and it was the
  # one write in either installer with no backup at all -- copy_toolkit and
  # write_paths_env both take one, and install.ps1 now does too. A settings.json
  # carries hand-added keys (this machine's has many), and `/model` is documented as
  # dropping them, so an unbacked rewrite here is not recoverable from anywhere.
  if [ -f "$sj" ]; then
    if ! _i_backup_file "$sj" >/dev/null; then
      echo "ERROR: could not back up $sj. Refusing to rewrite it." >&2
      exit 1
    fi
  else
    echo '{}' > "$sj"
  fi
  local tmp; tmp="$(mktemp)"
  jq --arg tk "$TOOLKIT" --arg g "$GAME" --arg ref "${REFERENCE:-$TOOLKIT/reference}" \
     --arg p "$PROFILE" --arg m "$MODS" --arg ext "${EXTENSIONS:-${GAME:+$GAME/extensions}}" --arg xc "$XRCAT" '
     .env = ((.env // {})
       + {X4_TOOLKIT:$tk, X4_REFERENCE:$ref}
       + (if $g  != "" then {X4_GAME:$g}       else {} end)
       + (if $p  != "" then {X4_PROFILE:$p, X4_DEBUGLOG:($p+"/debug.txt")} else {} end)
       + (if $m  != "" then {X4_MODS:$m}       else {} end)
       + (if $ext!= "" then {X4_EXTENSIONS:$ext} else {} end)
       + (if $xc != "" then {XRCATTOOL:$xc}    else {} end))' "$sj" > "$tmp" && mv "$tmp" "$sj"
  # NEVER announce success from a line that cannot have failed. `A && B` does not trip
  # `set -e` when A fails (A is not the command following the final &&), so a failing jq
  # skipped the mv and this echo still printed -- exit 0, "merged X4_* env into ...",
  # and settings.json left as `{}`. Verify the ARTIFACT, not the exit code of the shell
  # that wrapped it.
  if ! jq -e '.env.X4_TOOLKIT' "$sj" >/dev/null 2>&1; then
    echo "ERROR: the X4_* env merge did not land in $sj." >&2
    echo "       Skills and agents were copied, but they resolve paths via \$X4_TOOLKIT," >&2
    echo "       which is now undefined. Set it by hand, or re-run once jq works." >&2
    exit 1
  fi
  echo "  merged X4_* env into $sj"
}

# --- writing somewhere needs DIRECTION, not inference -----------------------
#
# MEASURED 2026-09-02: `install.sh --method in-game --yes` with no --game wrote 1,642
# files into a real Steam install and exited 0. Every X4_* variable had been cleared
# first and none of it mattered -- steam_roots() is hardcoded, so detection found the
# game directly, and --yes accepted that destination without ever printing it. Seven
# personal files were overwritten, including a 145 KB CLAUDE.md and a 631 KB
# KNOWLEDGEBASE.md. They were recovered from a Volume Shadow Copy: luck, not design.
#
# The rule (user-set, and stronger than "prompt first"): a new install must not proceed
# over an EXISTING install without strict user DIRECTION -- a flag naming the intent --
# not merely an approval the user can click through. And a destination the user never
# named is not a destination at all when nobody is there to read the prompt.

#: Does this directory already hold a toolkit? Any one marker is enough; they are the
#: things an install would overwrite.
looks_installed() {
  local d="$1"
  [ -e "$d/.claude/hooks/protect-bash.sh" ] && return 0
  [ -e "$d/tools/x4validate" ]              && return 0
  [ -e "$d/CLAUDE.md" ]                     && return 0
  [ -e "$d/KNOWLEDGEBASE.md" ]              && return 0
  return 1
}

#: refuse_if_dry_run WHAT DEST -- the ONE dry-run gate, called by every writer.
#:
#: It used to live in `announce_target`, which is only called when a COPY would happen --
#: so `--method global` never reached it and did a complete install, and so did an
#: in-place `separate`. MEASURED 2026-09-02: `--method global --dry-run` wrote the config,
#: copied 9 items into the global Claude home over the user's own skills with no backup,
#: merged settings.json and printed "install complete".
#:
#: Placed on the WRITERS instead: every write in this script goes through copy_toolkit,
#: write_paths_env or install_global_claude, so an arm added later cannot write without
#: passing it. The flag is documented three times with no qualification, and a user
#: reaching for it is reaching for safety.
refuse_if_dry_run() {
  [ "$DRY_RUN" = 1 ] || return 0
  echo
  echo "  --dry-run: NOT $1"
  echo "      $2"
  announce_dry_run_extras "$TOOLKIT"
  echo
  echo "=== dry run complete: nothing was changed ==="
  exit 0
}

#: require_direction DEST WHAT_NAMED_IT
#: Called immediately before the first write of every method.
require_direction() {
  local dest="$1" named="$2"

  # (a) Nobody named it AND nobody is watching. `--yes` is documented as "accept
  #     detected values", which is fine for a profile path and is not fine for the
  #     directory about to be written to in bulk.
  # ...or nobody is watching for any other reason. `--yes` was the only trigger, so a
  # CLOSED STDIN walked straight through: `ask()` is `read ... || true`, the read fails
  # at EOF, the `|| true` swallows it, and the detected default is accepted in silence.
  # The rule this branch states is "a destination the user never named is not a
  # destination at all when nobody is there to read the prompt" -- and no tty is exactly
  # that. The Bash tool this toolkit is driven by has no tty.
  if [ "$named" = "detected" ] && { [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; }; then
    if [ "$ASSUME_YES" = 1 ]; then
      echo "REFUSING: --yes with an auto-detected destination." >&2
    else
      echo "REFUSING: an auto-detected destination with no terminal to confirm on." >&2
    fi
    echo "  Detected: $dest" >&2
    if [ "$TOOLKIT_FROM_ENV" = 1 ] && [ "$METHOD" != in-game ]; then
      echo "  Nothing named that path -- it came from \$X4_TOOLKIT, INHERITED from your environment" >&2
      echo "  (usually your live toolkit), and nobody will see a prompt before the write starts." >&2
      echo "  Name the destination explicitly:  --toolkit \"<the folder you mean>\"" >&2
    elif [ "$GAME_FROM_ENV" = 1 ] && [ "$METHOD" = in-game ]; then
      echo "  Nothing named that path -- it came from \$X4_GAME, INHERITED from your environment" >&2
      echo "  (a --method global install puts it in every agent session), and nobody will see a" >&2
      echo "  prompt before the write starts. Name it explicitly:  --game \"<the game folder you mean>\"" >&2
    else
      echo "  Nothing named that path -- it came from scanning the usual Steam locations," >&2
      echo "  and nobody will see a prompt before the write starts." >&2
      echo "  Name it explicitly:  --game \"$dest\"   (or --toolkit for separate/global)" >&2
    fi
    exit 2
  fi

  # (b) Something is already installed there. A flag is the direction; a prompt is not.
  if looks_installed "$dest" && [ "$OVER_EXISTING" != 1 ]; then
    echo "REFUSING: there is already an installation at the destination." >&2
    echo "  Destination: $dest" >&2
    echo "  Found:" >&2
    for m in .claude/hooks/protect-bash.sh tools/x4validate CLAUDE.md KNOWLEDGEBASE.md; do
      [ -e "$dest/$m" ] && echo "      $m" >&2
    done
    echo >&2
    echo "  Installing over it REPLACES those files. An edited CLAUDE.md or AGENTS.md is" >&2
    echo "  KEPT beside it (X4-NOTES.pre-4.0.md / AGENTS.pre-4.0.md); your own" >&2
    echo "  KNOWLEDGEBASE.md and customised skills are replaced. x4-paths.env and" >&2
    echo "  settings.local.json are preserved." >&2
    echo >&2
    if [ "$named" != named ]; then
      # FX-B2: NOT the rerun line. This destination was never named by a flag (an inherited
      # X4_TOOLKIT, a scan, or a prompt's default), and "your command plus --over-existing"
      # would overwrite whatever installation lives there -- typically the user's live one.
      echo "  This destination was not named on the command line, so no upgrade command is offered." >&2
      echo "  Name the folder you mean with --toolkit DIR (--game DIR for in-game); add" >&2
      echo "  --over-existing only if you mean to UPGRADE the installation at that path." >&2
      exit 2
    fi
    echo "  To upgrade it anyway, say so explicitly -- your own command, plus --over-existing:" >&2
    echo "      $(_r2_rerun_cmd --over-existing)" >&2
    exit 2
  fi
}

#: announce_target DEST -- say what is about to happen, before it happens.
#: Says WHERE, and nothing else. It used to also perform the dry-run exit with a
#: listing of the items a copy would move -- two unrelated jobs, and because the
#: second only makes sense where a copy happens, the first was wired into the COPY
#: branch too. So `global`, and a `separate` install landing in place, reached the
#: config writer without ever saying where they were writing, while the CHANGELOG
#: claimed the destination is printed 'in every mode'.
announce_target() {
  echo
  echo "  About to write the toolkit into:"
  echo "      $1"
}

#: The dry-run listing, for the arms where a COPY would actually happen. The gate
#: itself does not depend on this: refuse_if_dry_run sits inside all three writers,
#: so an arm that copies nothing still stops before it writes.
#: THE REST OF THE PREVIEW (v4.0.0 review R4-4). The copy plan listed the copied items and
#: stopped, so three writes the real run makes -- the Codex hooks.json render, the OpenCode
#: deny-rule render, the .codex/config.toml prepend -- were never named; and the in-place and
#: global arms reach no copy, so they never printed the X4_TOOLKIT line either. ONE function,
#: called by the copy plan AND by the dry-run gate every writer passes through. install.ps1's
#: Show-DryRunExtras prints the same lines.
X4_DRY_EXTRAS_SHOWN=0
announce_dry_run_extras() {   # DEST
  local dest="$1" f old
  [ "$X4_DRY_EXTRAS_SHOWN" = 1 ] && return 0
  X4_DRY_EXTRAS_SHOWN=1
  [ -n "$dest" ] || return 0
  if [ "$METHOD" != global ] && _codex_selected; then
    echo "  .codex/hooks.json would be (re)rendered for this folder (Codex skips a changed hook until you re-approve it in /hooks)"
  fi
  if [ "$METHOD" != global ] && _opencode_selected; then
    echo "  .opencode/opencode.jsonc (the OpenCode deny rules) would be rendered for this folder's roots"
  fi
  if [ -n "$CODEX_DOC_MAX" ]; then
    f="$(_h_codex_config "$dest")"
    if old="$(_h_codex_doc_value "$f")"; then
      echo "  $f already sets project_doc_max_bytes = $old: it would be left unchanged"
    else
      echo "  project_doc_max_bytes = $CODEX_DOC_MAX would be prepended to $f"
    fi
  fi
  _h_userenv_preview "$dest"
}

announce_copy_plan() {
  [ "$DRY_RUN" = 1 ] || return 0
  echo "  --dry-run: nothing will be written. Items that would be copied:"
  local item to
  for item in $X4_ITEMS; do
    [ -e "$SRC/$item" ] && echo "      $item"
  done
  if _tracked_copy_set; then
    echo "  (the source is a git checkout: only the ${#X4_TRACKED[@]} file(s) git tracks would be copied)"
  fi
  echo "  agents that would be installed: $(_agents_landed)"
  # SAID, in the dry run, exactly as the real run would do it: the mover and this line
  # read one function, so the preview cannot name a different file.
  if to="$(_agents_md_move_target "$1")"; then
    echo "  your AGENTS.md differs from the shipped one: it would be KEPT as $to, not overwritten"
  fi
  if to="$(_h_claude_md_move_target "$1")"; then
    echo "  your CLAUDE.md is not one this toolkit shipped: it would be KEPT as $to, not overwritten"
    _h_hash_caveat
  fi
  case "$(_old_example_action "$1")" in
    remove) echo "  the 3.x .claude/x4-paths.env.example (an unedited shipped copy) would be removed" ;;
    keep)   echo "  the 3.x .claude/x4-paths.env.example differs from every shipped one: it would be KEPT" ;;
  esac
  announce_dry_run_extras "$TOOLKIT"
  echo
  echo "=== dry run complete: nothing was changed ==="
  exit 0
}

#: Strip a trailing path separator, which otherwise makes a STRING comparison lie.
#: `--toolkit "$SRC/"` is not equal to `$SRC`, so the `SRC != TOOLKIT` test said
#: COPY and `cp -r "$SRC/.claude" "$SRC//"` copied a directory into itself. Under
#: `set -e` the script died there, BEFORE the FAILED accounting exists -- so there
#: was no INCOMPLETE banner and no config written, and the exit code was the only
#: sign. MEASURED 2026-09-03 in a sandbox: rc 1, "are the same file", zero config.
#:
#: Both separators, because this runs under Git Bash where a Windows path gets
#: pasted in. A drive root and `/` KEEP their separator: stripping it would turn an
#: absolute path into a relative one. No backslash appears in a case PATTERN here --
#: the first draft had one and the tool boundary collapsed it into a syntax error.
#: Are these the SAME directory? Asked canonically, never as a string.
#:
#: `$SRC` is MSYS-style (`/tmp/...`) because it comes from `cd $(dirname) && pwd`
#: under Git Bash, while `--toolkit` is whatever the user pasted -- and every path
#: this project documents is Windows-style (`C:/...`). So the plain string test
#: compared two spellings of one directory and said COPY. `cp -r` then reported
#: "are the same file" and `set -e` killed the script BEFORE the FAILED accounting
#: exists: no INCOMPLETE banner, no failed: line, no config. MEASURED in a sandbox.
#:
#: `cd ... && pwd -P` settles dialect, trailing separators, . / .. and symlinks at
#: once. A destination that does not exist YET cannot be the source, so a failed cd
#: means "different" -- which is the right answer for a fresh install.
same_dir() {
  local a b
  a=$(cd "$1" 2>/dev/null && pwd -P) || return 1
  b=$(cd "$2" 2>/dev/null && pwd -P) || return 1
  [ "$a" = "$b" ]
}

strip_trailing_sep() {
  local p="$1" bs last
  bs=$(printf '%b' '\134')            # one backslash, from its octal value
  while [ ${#p} -gt 1 ]; do
    last="${p: -1}"
    [ "$last" = "/" ] || [ "$last" = "$bs" ] || break
    case "$p" in
      ?:?) break ;;                   # a drive root
    esac
    p="${p%?}"
  done
  printf '%s' "$p"
}

# --- X4_TOOLKIT in the user environment (lane H; user decision 1) ----------------------
#: Set when unset; a DIFFERENT existing value is REPORTED and LEFT (never overwritten);
#: --no-env touches nothing. "Same" is canonical: separators, case (Windows), trailing
#: separator. Windows writes through scripts/x4-userenv.ps1 -- the ONE writer both
#: installers call -- and POSIX appends one marked block to the shell's startup file.
X4_USERENV_PS1="scripts/x4-userenv.ps1"
X4_H_ENV_STATE=""; X4_H_ENV_MSG=""; X4_H_TAB="$(printf '\t')"

#: The value as it is written: Windows form with backslashes, or POSIX `pwd -P`.
_h_native_path() {   # DIR
  local d
  d="$(cd "$1" 2>/dev/null && pwd -P)" || d="$1"
  if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then d="$(cygpath -w "$d")"; fi
  printf '%s' "$d"
}

#: Canonical spelling for comparison. Windows: one separator, no trailing one, lower case.
_h_canon_path() {   # PATH
  local v="$1" bs
  bs="$(printf '%b' '\134')"
  if [ "$OS" = windows ]; then
    command -v cygpath >/dev/null 2>&1 && v="$(cygpath -w "$v" 2>/dev/null || printf '%s' "$v")"
    v="$(_replace_all "$v" / "$bs")"
    while [ "${#v}" -gt 3 ] && [ "${v%"$bs"}" != "$v" ]; do v="${v%"$bs"}"; done
    printf '%s' "$v" | tr '[:upper:]' '[:lower:]'
  else
    if [ -d "$v" ]; then v="$(cd "$v" && pwd -P)"; fi
    while [ "${#v}" -gt 1 ] && [ "${v%/}" != "$v" ]; do v="${v%/}"; done
    printf '%s' "$v"
  fi
}

_h_same_path() { [ "$(_h_canon_path "$1")" = "$(_h_canon_path "$2")" ]; }

#: The shell startup file a new `claude` / `codex` would inherit from, or fails (unknown
#: shell: fish needs `set -Ux`, and we do not edit configs whose syntax we do not own).
_h_profile_file() {
  case "$(basename "${SHELL:-}")" in
    zsh)  printf '%s' "${ZDOTDIR:-$HOME}/.zshenv" ;;          # read by EVERY zsh, interactive or not
    bash) if [ "$OS" = macos ]; then printf '%s' "$HOME/.bash_profile"   # Terminal.app: login shells
          else printf '%s' "$HOME/.bashrc"; fi ;;
    *)    return 1 ;;
  esac
}

#: The manual command for THIS OS, for the user to run themselves.
_h_manual_env_cmd() {   # VALUE
  if [ "$OS" = windows ]; then printf 'setx X4_TOOLKIT "%s"' "$1"
  else printf "echo 'export X4_TOOLKIT=\"%s\"' >> %s" "$1" "$(_h_profile_file 2>/dev/null || echo '~/.bashrc')"; fi
}

#: Run scripts/x4-userenv.ps1 through Windows PowerShell; stdout is the value (CR dropped).
_h_win_userenv() {   # get | set VALUE
  local ps script out
  ps="$(command -v powershell.exe 2>/dev/null || command -v pwsh.exe 2>/dev/null)" || return 3
  script="$SRC/$X4_USERENV_PS1"
  [ -f "$script" ] || return 4
  command -v cygpath >/dev/null 2>&1 && script="$(cygpath -w "$script")"
  out="$("$ps" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$script" "$@" -Utf8Out)" || return 1
  printf '%s' "${out%$CR}"
}

#: The last `export X4_TOOLKIT=` value in FILE, quotes removed; fails when there is none.
_h_profile_value() {   # FILE
  local line v=""
  [ -f "$1" ] || return 1
  line="$(grep -E '^[[:space:]]*export[[:space:]]+X4_TOOLKIT=' "$1" 2>/dev/null | tail -n 1)" || true
  [ -n "$line" ] || return 1
  v="${line#*X4_TOOLKIT=}"; v="${v%$CR}"
  case "$v" in \'*\') v="${v#\'}"; v="${v%\'}" ;; \"*\") v="${v#\"}"; v="${v%\"}" ;; esac
  printf '%s' "$v"
}

#: EVERY startup file an X4_TOOLKIT export could already live in (v4.0.0 review R4-3: only
#: the one this installer would write was read, so an export in another one -- a login
#: shell's ~/.profile, Git Bash's ~/.bashrc on Windows -- was invisible, and a SECOND,
#: conflicting value was written). Windows: Git Bash's own files.
_h_profile_files() {
  if [ "$OS" = windows ]; then
    printf '%s\n' "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"
  else
    printf '%s\n' "${ZDOTDIR:-$HOME}/.zshenv" "${ZDOTDIR:-$HOME}/.zprofile" "${ZDOTDIR:-$HOME}/.zshrc" \
      "$HOME/.profile" "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.bashrc"
  fi
}

#: The X4_TOOLKIT in the `env` block of the global Claude settings.json (`--method global`
#: writes it there), or nothing. R4-3 / FX-B2: one more place an existing value lives. Read
#: ONCE, before the dispatch (X4_H_SETTINGS_TOOLKIT below): the global arm rewrites that file,
#: and reading it afterwards would find this install's own value and call it "already set".
_h_settings_toolkit() {
  local f="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json" v=""
  [ -f "$f" ] || return 0
  if command -v jq >/dev/null 2>&1; then
    v="$(jq -r '.env.X4_TOOLKIT // empty' "$f" 2>/dev/null)" || v=""
  else
    v="$(grep -o '"X4_TOOLKIT"[[:space:]]*:[[:space:]]*"[^"]*"' "$f" 2>/dev/null | head -n 1 |
         sed -e 's/^.*:[[:space:]]*"//' -e 's/"$//' -e 's/\\\\/\\/g')" || v=""
  fi
  printf '%s' "${v%$CR}"
}

#: THE DECISION, one function read by the dry-run line and the writer. Prints one line:
#: `set` | `same` | `different<TAB>FOUND` | `skip<TAB>WHY` | `fail<TAB>WHY`, where FOUND is
#: EVERY existing value, `VALUE (WHERE)` joined by `; ` -- all of them, not the first.
#: Nothing is written beside a value that differs: which one a terminal then sees depends on
#: how it was started.
_h_userenv_plan() {   # TOOLKIT
  local v old f rc=0 tab found="" diff=0 pf
  tab="$(printf '\t')"
  v="$(_h_native_path "$1")"
  _h_found() {   # VALUE WHERE
    [ -n "$1" ] || return 0
    found="$found${found:+; }$1 ($2)"
    _h_same_path "$1" "$v" || diff=1
  }
  if [ "$OS" = windows ]; then
    old="$(_h_win_userenv get)" || rc=$?
    case "$rc" in
      0) ;;
      3) printf 'fail%sno powershell.exe on PATH' "$tab"; return 0 ;;
      4) printf 'fail%sthe source has no %s' "$tab" "$X4_USERENV_PS1"; return 0 ;;
      *) printf 'fail%s%s get failed' "$tab" "$X4_USERENV_PS1"; return 0 ;;
    esac
    _h_found "$old" "your user environment"
    old="$(_h_win_userenv get-machine)" || { printf 'fail%s%s get-machine failed' "$tab" "$X4_USERENV_PS1"; return 0; }
    _h_found "$old" "the machine environment"
  else
    if ! f="$(_h_profile_file)"; then
      printf 'skip%syour shell (%s) is not bash or zsh, so no startup file is edited' "$tab" "${SHELL:-unset}"
      return 0
    fi
  fi
  while IFS= read -r pf; do
    [ -n "$pf" ] || continue
    old="$(_h_profile_value "$pf")" || old=""
    _h_found "$old" "$pf"
  done <<EOF_PROFILES
$(_h_profile_files)
EOF_PROFILES
  # FX-B2 (R4-3 completed): the global settings.json env block, and on Windows this PROCESS's
  # environment -- each counted only when it DIFFERS: an equal value there is not where the
  # user-level variable lives, and must not turn "set" into "already set" with nothing written.
  if [ -n "${X4_H_SETTINGS_TOOLKIT:-}" ] && ! _h_same_path "$X4_H_SETTINGS_TOOLKIT" "$v"; then
    _h_found "$X4_H_SETTINGS_TOOLKIT" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json env"
  fi
  if [ -z "$found" ]; then
    if [ "$OS" != windows ]; then
      _h_found "$X4_H_INHERITED_TOOLKIT" "this shell's environment"
    elif [ -n "$X4_H_INHERITED_TOOLKIT" ] && ! _h_same_path "$X4_H_INHERITED_TOOLKIT" "$v"; then
      _h_found "$X4_H_INHERITED_TOOLKIT" "this process's environment"
    fi
  fi
  if [ -z "$found" ]; then printf 'set'
  elif [ "$diff" = 0 ]; then printf 'same'
  else printf 'different%s%s' "$tab" "$found"; fi
}

#: The dry-run preview, from the same decision.
_h_userenv_preview() {   # TOOLKIT
  local plan v
  if [ "$NO_ENV" = 1 ]; then echo "  --no-env: X4_TOOLKIT would not be touched"; return 0; fi
  plan="$(_h_userenv_plan "$1")"; v="$(_h_native_path "$1")"
  case "$plan" in
    set)        echo "  X4_TOOLKIT would be set for your user: $v" ;;
    same)       echo "  X4_TOOLKIT is already set to this toolkit: nothing would change" ;;
    different*) echo "  X4_TOOLKIT is set to ${plan#*"$X4_H_TAB"}, not this toolkit: it would be left unchanged" ;;
    *)          echo "  X4_TOOLKIT would not be set: ${plan#*"$X4_H_TAB"}" ;;
  esac
}

set_user_toolkit_env() {   # TOOLKIT -- after the dispatch, before setup.sh
  refuse_if_dry_run "setting X4_TOOLKIT for your user to" "$1"
  local plan v old f why cmd
  v="$(_h_native_path "$1")"
  cmd="$(_h_manual_env_cmd "$v")"
  plan="$(_h_userenv_plan "$1")"
  case "$plan" in
    same)
      X4_H_ENV_STATE=same; X4_H_ENV_MSG="X4_TOOLKIT already set to this toolkit ($v)" ;;
    different*)
      old="${plan#*"$X4_H_TAB"}"
      X4_H_ENV_STATE=different
      X4_H_ENV_MSG="[WARNING] X4_TOOLKIT is set to $old, not this toolkit $v. Left unchanged. To point it here: $cmd" ;;
    skip*)
      X4_H_ENV_STATE=skip; X4_H_ENV_MSG="${plan#*"$X4_H_TAB"}" ;;
    fail*)
      why="${plan#*"$X4_H_TAB"}"
      X4_H_ENV_STATE=fail; add_failed "X4_TOOLKIT (could not set: $why; run: $cmd)" ;;
    set)
      if [ "$OS" = windows ]; then
        if _h_win_userenv set "$v" >/dev/null; then
          X4_H_ENV_STATE=set; X4_H_ENV_MSG="X4_TOOLKIT set for your user: $v (new terminals only)"
        else
          X4_H_ENV_STATE=fail; add_failed "X4_TOOLKIT (could not set: $X4_USERENV_PS1 set failed; run: $cmd)"
        fi
      else
        f="$(_h_profile_file)"
        case "$v" in *"'"*|*"
"*)
          X4_H_ENV_STATE=fail
          add_failed "X4_TOOLKIT (could not set: the path contains a quote or a newline; run: $cmd)"
          return 0 ;;
        esac
        # APPENDED, never temp-then-move: a dotfile is often a SYMLINK (dotfile managers),
        # and a move would replace the link with a copy and drop its mode. `>>` cannot
        # truncate, and the block is written only when the file holds no X4_TOOLKIT line.
        if printf '\n# >>> X4 toolkit: X4_TOOLKIT (written by install.sh; delete this block to undo) >>>\nexport X4_TOOLKIT='"'"'%s'"'"'\n# <<< X4 toolkit <<<\n' "$v" >> "$f"; then
          X4_H_ENV_STATE=set; X4_H_ENV_MSG="X4_TOOLKIT set for your user in $f: $v (new terminals only)"
        else
          X4_H_ENV_STATE=fail; add_failed "X4_TOOLKIT (could not set: $f is not writable; run: $cmd)"
        fi
      fi ;;
  esac
  [ -n "$X4_H_ENV_MSG" ] && echo "  $X4_H_ENV_MSG"
  return 0
}

# --- choose method ---------------------------------------------------------
if [ -z "$METHOD" ]; then
  echo; echo "Install method:"; echo "  1) in-game    2) separate    3) global (multi-repo)"
  if [ "$ASSUME_YES" = 1 ]; then METHOD=separate; else
    read -r -p "Choose [1/2/3]: " m || true
    case "$m" in 1) METHOD=in-game;; 3) METHOD=global;; *) METHOD=separate;; esac
  fi
fi
echo "Method: $METHOD"
X4_H_SETTINGS_TOOLKIT="$(_h_settings_toolkit)"   # BEFORE any write (see _h_settings_toolkit)
# FX-B2: what this run took from the ENVIRONMENT rather than a flag, before anything is written
# -- they go into the new install's config. Paths only (no secret is among these keys).
for _v in $X4_INHERITED_PATHS; do
  echo "  [note] $_v from your environment (not a flag): ${!_v} -- this install's config will use it; pass the flag to choose another."
done
if [ "$TOOLKIT_FROM_ENV" = 1 ] && [ "$METHOD" != in-game ]; then
  echo "  [note] no --toolkit given: the destination below comes from \$X4_TOOLKIT, inherited from your"
  echo "         environment ($X4_TOOLKIT). An inherited value is NOT a named destination."
fi

detect_game; detect_profile; detect_xrcat
ask GAME    "X4 game folder (01.cat..09.cat)" "$GAME"
ask PROFILE "X4 user profile folder"          "$PROFILE"
ask XRCAT   "XRCatTool.exe path"              "$XRCAT"

# Normalised HERE and again after the in-arm ask below, because a destination
# can arrive either way and a single up-front pass would miss the interactive
# one.
GAME="$(strip_trailing_sep "$GAME")"
PROFILE="$(strip_trailing_sep "$PROFILE")"
TOOLKIT="$(strip_trailing_sep "$TOOLKIT")"

case "$METHOD" in
  in-game)
    [ -n "$GAME" ] || { echo "ERROR: in-game needs --game"; exit 1; }
    TOOLKIT="$GAME"
    announce_target "$TOOLKIT"
    resolve_auto_agents "$TOOLKIT"   # BEFORE anything reads X4_ITEMS
    require_codex_for_doc_cap
    # ORDER, and each position is load-bearing for a different reason:
    #   require_direction     FIRST, and only when a copy will happen. Both
    #                         prechecks can exit 1 telling the user to unlock and
    #                         re-run -- against a destination they never named,
    #                         when Steam detection picked it. The refusal they
    #                         should see is "you did not name this". Neither
    #                         precheck writes, so this was a wrong MESSAGE rather
    #                         than a wrong outcome.
    #   precheck_config       OUTSIDE the copy guard: the config is written on
    #                         BOTH branches, so an in-place upgrade skipped the
    #                         only check in front of it and the orphaned .bak
    #                         came back.
    #   precheck_locked_targets INSIDE: with no copy there is nothing to
    #                         overwrite, and checking anyway refuses an in-place
    #                         run for no reason.
    if ! same_dir "$SRC" "$TOOLKIT"; then
      require_direction "$TOOLKIT" "$GAME_NAMED"
    fi
    precheck_config "$TOOLKIT"
    precheck_codex_hooks_json "$TOOLKIT"   # written on BOTH branches, so OUTSIDE the guard
    precheck_opencode_config "$TOOLKIT"    # likewise
    precheck_codex_doc_max_bytes "$TOOLKIT"
    if ! same_dir "$SRC" "$TOOLKIT"; then
      precheck_project_settings "$TOOLKIT"
      precheck_locked_targets "$TOOLKIT"
      announce_copy_plan "$TOOLKIT"
      preserve_user_agents_md "$TOOLKIT"   # after every precheck, before the copy
      preserve_user_claude_md "$TOOLKIT"   # 3.x -> 4.0: same position, same shape
      copy_toolkit "$TOOLKIT"
      retire_old_paths_example "$TOOLKIT"   # after the copy: the 4.0 example is in place
      render_toolkit_token "$TOOLKIT"
    else
      note_in_place_token
    fi
    write_paths_env "$TOOLKIT"
    write_codex_hooks_json "$TOOLKIT"
    write_opencode_config "$TOOLKIT"
    ;;
  separate)
    [ -n "$TOOLKIT" ] || TOOLKIT="$SRC"
    ask TOOLKIT "Toolkit folder" "$TOOLKIT"
    TOOLKIT="$(strip_trailing_sep "$TOOLKIT")"
    announce_target "$TOOLKIT"
    resolve_auto_agents "$TOOLKIT"   # BEFORE anything reads X4_ITEMS
    require_codex_for_doc_cap
    # ORDER, and each position is load-bearing for a different reason:
    #   require_direction     FIRST, and only when a copy will happen. Both
    #                         prechecks can exit 1 telling the user to unlock and
    #                         re-run -- against a destination they never named,
    #                         when Steam detection picked it. The refusal they
    #                         should see is "you did not name this". Neither
    #                         precheck writes, so this was a wrong MESSAGE rather
    #                         than a wrong outcome.
    #   precheck_config       OUTSIDE the copy guard: the config is written on
    #                         BOTH branches, so an in-place upgrade skipped the
    #                         only check in front of it and the orphaned .bak
    #                         came back.
    #   precheck_locked_targets INSIDE: with no copy there is nothing to
    #                         overwrite, and checking anyway refuses an in-place
    #                         run for no reason.
    if ! same_dir "$SRC" "$TOOLKIT"; then
      require_direction "$TOOLKIT" "$TOOLKIT_NAMED"
    fi
    precheck_config "$TOOLKIT"
    precheck_codex_hooks_json "$TOOLKIT"   # written on BOTH branches, so OUTSIDE the guard
    precheck_opencode_config "$TOOLKIT"    # likewise
    precheck_codex_doc_max_bytes "$TOOLKIT"
    if ! same_dir "$SRC" "$TOOLKIT"; then
      precheck_project_settings "$TOOLKIT"
      precheck_locked_targets "$TOOLKIT"
      announce_copy_plan "$TOOLKIT"
      preserve_user_agents_md "$TOOLKIT"   # after every precheck, before the copy
      preserve_user_claude_md "$TOOLKIT"   # 3.x -> 4.0: same position, same shape
      copy_toolkit "$TOOLKIT"
      retire_old_paths_example "$TOOLKIT"   # after the copy: the 4.0 example is in place
      render_toolkit_token "$TOOLKIT"
    else
      note_in_place_token
    fi
    write_paths_env "$TOOLKIT"
    write_codex_hooks_json "$TOOLKIT"
    write_opencode_config "$TOOLKIT"
    ;;
  global)
    # THE GLOBAL LAYOUT IS CLAUDE-ONLY: it installs skills and agents into the Claude
    # home and nothing else. An explicit non-Claude target is REFUSED rather than half
    # installed; the default (`all`) proceeds and says what it leaves out. Codex's own
    # global skills are a follow-up, not a layout to fake.
    if [ -n "$CODEX_DOC_MAX" ]; then
      echo "REFUSING: --codex-doc-max-bytes configures Codex, and --method global is a Claude-only layout." >&2
      echo "  Nothing has been changed." >&2
      exit 2
    fi
    case "$AGENT" in
      all|auto) ;;
      *)    # codex, generic, opencode -- or a list naming any of them (C1)
        if [ "$X4_AGENTS" != claude ]; then
          echo "REFUSING: --method global is a Claude-only layout; it cannot install --agent $AGENT." >&2
          echo "  Use --method in-game or --method separate for Codex, OpenCode and generic agents." >&2
          echo "  Nothing has been changed." >&2
          exit 2
        fi ;;
    esac
    if [ "$AGENT" = all ] || [ "$AGENT" = auto ]; then
      echo "  [note] --method global is a Claude-only layout: only the Claude target is installed."
      echo "         Codex, OpenCode and generic agents need --method in-game or --method separate."
    fi
    X4_AGENTS="claude"
    _h_resolve_items
    [ -n "$TOOLKIT" ] || TOOLKIT="$SRC"
    # FX-B2: global copies the skills and agents of the toolkit at $TOOLKIT. An INHERITED
    # X4_TOOLKIT naming another toolkit silently installed THAT toolkit's skills.
    if [ "$TOOLKIT_FROM_ENV" = 1 ] && ! same_dir "$SRC" "$TOOLKIT"; then
      echo "REFUSING: --method global installs the skills of the toolkit at $TOOLKIT, which came from" >&2
      echo "  \$X4_TOOLKIT inherited from your environment, not from a flag. Nothing has been changed." >&2
      echo "  Name it:  --toolkit \"$SRC_SHOW\"   (this toolkit)   or   --toolkit \"$TOOLKIT\"   (that one)" >&2
      exit 2
    fi
    announce_target "$TOOLKIT"
    # THE GLOBAL DESTINATION WAS NEVER GATED. `require_direction` is called for
    # in-game and separate and never here, and `looks_installed` was never asked about
    # $HOME/.claude -- so `--over-existing` could not gate a method that copies skills
    # and agents in and rewrites settings.json.
    #
    # NOT routed through require_direction: that refusal is about a destination that
    # came from SCANNING (the Steam locations), and ~/.claude is a fixed, well-known
    # path, so its reasoning does not transfer. What does transfer is the
    # over-existing rule, and the backup below.
    _hc="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
    # AGENTS TOO. This gate enumerated skills only, while the global install copies
    # every <toolkit>/.claude/agents/*.md over the destination -- so a user's own
    # edited ~/.claude/agents/mod-research.md was replaced with no prompt, no
    # --over-existing, no backup, and rc 0. Raised by the v3.1.0 release reviewer,
    # who measured it against install.ps1; install.sh had the identical gap, so this
    # is NOT one of the "fixed in bash, absent in PowerShell" class -- both halves
    # gated half their destination.
    #
    # Only files this install would actually WRITE are named: an unrelated agent of
    # the user's own is not at risk and must not be listed as though it were.
    _hits=""
    _NL='
'
    # ENUMERATED FROM THE TOOLKIT, then checked against the destination -- the shape
    # the agents leg below already uses, and the property the comment above already
    # claims. This globbed the DESTINATION, so a user's own `x4-mycustom/` was listed
    # as a file the install "would REPLACE" and the run refused until they passed
    # --over-existing, against a directory this installer never touches. The comment
    # was right and the code was one enumeration away from it.
    if [ -d "$_hc/skills" ] && [ -d "$TOOLKIT/.claude/skills" ]; then
      for _s in "$TOOLKIT/.claude/skills/"x4-*; do
        [ -e "$_s" ] || continue
        [ -e "$_hc/skills/$(basename "$_s")" ] &&
          _hits="$_hits      skills/$(basename "$_s")$_NL"
      done
    fi
    if [ -d "$_hc/agents" ] && [ -d "$TOOLKIT/.claude/agents" ]; then
      for _a in "$TOOLKIT/.claude/agents/"*.md; do
        [ -e "$_a" ] || continue
        [ -e "$_hc/agents/$(basename "$_a")" ] &&
          _hits="$_hits      agents/$(basename "$_a")$_NL"
      done
    fi
    if [ -n "$_hits" ] && [ "$OVER_EXISTING" != 1 ]; then
      echo "REFUSING: $_hc already carries files this install would REPLACE." >&2
      printf '%s' "$_hits" >&2
      echo "  Re-run with --over-existing to replace them, after checking you have not" >&2
      echo "  edited them in place. This method also rewrites $_hc/settings.json:" >&2
      echo "      $(_r2_rerun_cmd --over-existing)" >&2
      exit 2
    fi
    # BEFORE the first write. `install_global_claude` checks jq at its own top and
    # exits 1 saying "Nothing has been changed." -- but `write_paths_env` has already
    # rewritten x4-paths.env by then, so on a machine without jq the user was told
    # nothing changed while their live config had been regenerated. The message was
    # true of the function and false of the run.
    require_jq_for_global
    precheck_global_locked       # BEFORE the config write, not inside the copier
    precheck_config "$TOOLKIT"   # --method global writes the config and never copied
    write_paths_env "$TOOLKIT"
    install_global_claude
    ;;
  *) echo "ERROR: unknown method '$METHOD' (in-game|separate|global)"; exit 2;;
esac

# Opt-in Codex doc cap: ONE call, after every arm (global refused it up front).
write_codex_doc_max_bytes "$TOOLKIT"
# X4_TOOLKIT for the user: ONE call, after every arm, so no arm can skip or repeat it.
[ "$NO_ENV" = 1 ] || set_user_toolkit_env "$TOOLKIT"

# wire x4validate + prereqs in the target toolkit
# `|| true` swallowed a failed setup.sh entirely, and this script had no INCOMPLETE
# branch at all -- so a half-finished install printed exactly the same success text as
# a good one. install.ps1 has accumulated $failed and exited 1 for a while; this is the
# same accounting on the bash side.
# ACCUMULATES. A single assignment could only ever name one failure, and the unpack
# below was outside the accounting entirely: under `set -e` a failing unpack killed the
# script with NO summary -- neither banner, no `failed:` line, no next steps.
# install.ps1 has used an array with three contributors for a while; this is parity.
# (FAILED and add_failed are defined above the dispatch: the Codex writers inside it
#  record into the same accounting.)

# A BACKSTOP, and today it is UNREACHED -- stated rather than implied. Every
# dispatch arm ends in a writer whose first statement is `refuse_if_dry_run`,
# which exits 0, so control does not arrive here on any path measured (all three
# arms, both installers: this branch printed zero times). It is kept because the
# writers' gate covering these two is an accident of ORDERING rather than a
# property: setup.sh and bin/unpack-reference.sh are invoked at TOP LEVEL,
# outside all three writers, so any future arm not ending in a writer would run
# them under --dry-run. Dead today, correct tomorrow -- and nobody should read it
# as the thing currently doing the work. refuse_if_dry_run's
# own docstring claimed "every write goes through" those three; it does not, and
# these two are the counter-example. A dry run that syncs dependencies or unpacks
# 60 GB of game archives is not a preview.
if [ "$DRY_RUN" = 1 ]; then
  echo "  --dry-run: NOT running setup.sh"
else
( cd "$TOOLKIT" && CLAUDE_PROJECT_DIR="$TOOLKIT" X4_SETUP_CONFIG_WRITTEN=1 bash setup.sh ) || add_failed "setup.sh"
fi

if [ "$DO_UNPACK" = 1 ] && [ "$DRY_RUN" = 1 ]; then
  echo "  --dry-run: NOT unpacking reference/"
elif [ "$DO_UNPACK" = 1 ]; then
  echo "Unpacking reference/ ..."
  # --toolkit (FX-B2): the unpack acts for the toolkit it LIVES in and refuses an inherited
  # X4_TOOLKIT naming another one unless told -- and this run INSTALLED $TOOLKIT.
  # --reference (FX-B3, reviewer F M1): the tree THIS run configured. Without it a shell
  # exporting a different X4_REFERENCE made `--reference R2 --unpack` refuse (the unpack saw
  # the inherited value disagree with the config) and the install end INCOMPLETE. The
  # unpack also reads THIS run's config and game: an inherited X4_CONFIG would have chosen
  # another config, and an inherited X4_GAME outranks the --game this run was given.
  ( cd "$TOOLKIT" && export X4_CONFIG="" && { [ -z "$GAME" ] || export X4_GAME="$GAME"; } \
      && CLAUDE_PROJECT_DIR="$TOOLKIT" bash bin/unpack-reference.sh --toolkit "$TOOLKIT" \
           --reference "${REFERENCE:-$TOOLKIT/reference}" ) \
    || add_failed "bin/unpack-reference.sh"
fi

echo
# An INCOMPLETE install must not print the success text. install.ps1 has accumulated
# $failed and exited 1 for a while; install.sh swallowed a failed setup.sh with
# `|| true` and had no such branch at all, so a half-finished install and a good one
# were indistinguishable from the output.
if [ -n "${FAILED:-}" ]; then
  echo
  echo "=== install INCOMPLETE ($METHOD) ==="
  echo "  failed: $FAILED"
  echo "Toolkit:   $TOOLKIT"
  echo "Fix the above and re-run, or complete that step by hand."
  exit 1
fi

echo "=== install complete ($METHOD) ==="
echo "Toolkit:   $TOOLKIT"
echo "Agents:    $(_agents_landed)"
echo "Config:    $TOOLKIT/x4-paths.env  (edit any path here)"
# CODEX RUNS NO HOOK IT HAS NOT REVIEWED, and it says nothing when it skips one
# (MEASURED, spike 2026-09-30). The installer must never approve hooks or trust the
# project on the user's behalf (spec section 8), so it says what to do instead.
if [ "$METHOD" != global ] && _codex_selected; then
  echo
  echo "Codex:     the guards in .codex/hooks.json are INERT until you review them in Codex."
  echo "           1. run  codex  in $TOOLKIT  and trust the folder when asked"
  echo "           2. type  /hooks  and approve each X4 hook"
  echo "           3. verify:  python scripts/x4doctor.py --root \"$TOOLKIT\""
  if [ "$X4_CODEX_HOOKS_WRITTEN" = 1 ]; then
    echo "           (the definitions were just (re)written: any earlier review no longer holds)"
  fi
  if [ -n "$X4_CODEX_DOC_WRITTEN" ]; then
    echo "           project_doc_max_bytes = $X4_CODEX_DOC_WRITTEN is in .codex/config.toml; it takes"
    echo "           effect once you trust this folder in Codex."
  fi
fi
if [ "$METHOD" != global ] && _opencode_selected; then
  echo
  echo "$X4_OPENCODE_NOTE"
  echo "           start  opencode  in $TOOLKIT  (its config and plugin are found from there), then"
  echo "           verify:  python scripts/x4doctor.py --root \"$TOOLKIT\" --agent opencode"
fi
[ "$METHOD" = global ] && echo "Global:    skills/agents + X4_* env added to your ~/.claude — works from any mod repo."
print_refguard_step "$TOOLKIT"
# C5 (install red-team 2026-10-04): the Next line asked to set X4_GAME even when it was set.
if [ -z "$GAME" ]; then
  echo "Next:      X4_GAME is blank -- set it in $TOOLKIT/x4-paths.env, then  (cd \"$TOOLKIT\" && bash bin/unpack-reference.sh)  to build reference/."
elif [ -f "${REFERENCE:-$TOOLKIT/reference}/.unpacked-and-locked" ]; then
  echo "Next:      reference/ is already unpacked; open your agent in \"$TOOLKIT\" and paste SETUP_PROMPT.txt."
elif [ -d "${REFERENCE:-$TOOLKIT/reference}" ] && [ -n "$(ls -A "${REFERENCE:-$TOOLKIT/reference}" 2>/dev/null)" ]; then
  # R2 cosmetic: reference/ exists, but without .unpacked-and-locked nothing says it is finished.
  echo "Next:      reference/ exists but has no .unpacked-and-locked (not a finished unpack): (cd \"$TOOLKIT\" && bash bin/unpack-reference.sh)  to unpack it; if it IS a complete unpack you made yourself, python scripts/x4refguard.py apply  prints how to mark it."
else
  echo "Next:      (cd \"$TOOLKIT\" && bash bin/unpack-reference.sh)  to build reference/ from your game."
fi
echo
case "$X4_H_ENV_STATE" in
  set|same)
    echo "X4_TOOLKIT: ${X4_H_ENV_MSG}" ;;
  *)
    # --no-env, a different existing value, or a shell we do not edit: the user decides.
    echo "IMPORTANT — set X4_TOOLKIT in your user environment so the tools find the config"
    echo "above from ANY directory (they are often run from the game folder, which may hold"
    echo "no x4-paths.env)."
    if [ "$NO_ENV" = 1 ]; then echo "  --no-env: this installer did not touch it. To set it yourself:"
    elif [ "$X4_H_ENV_STATE" = different ]; then echo "  It names another toolkit (the [WARNING] above). To point it here instead:"
    elif [ "$X4_H_ENV_STATE" = skip ]; then echo "  $X4_H_ENV_MSG. To set it yourself:"
    else echo "  It was not set (see 'failed:' above). To set it yourself:"; fi
    echo "           $(_h_manual_env_cmd "$(_h_native_path "$TOOLKIT")")        (takes effect in NEW shells)" ;;
esac
echo "Verify:    (cd \"$TOOLKIT/tools/x4validate\" && uv run x4validate --paths)"

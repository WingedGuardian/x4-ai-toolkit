#!/bin/bash
# Shared, cross-platform path/config resolver for the X4 toolkit hooks & scripts.
# SOURCE this (do not execute). Single source of truth for the configurable X4 locations
# so nothing is hardcoded to one OS or one user's folder layout.
#
# Resolution order for each value:  existing env var  >  `x4-paths.env` (see below)  >  default.
#
# WHICH config file (Plan 3 lane I): $X4_CONFIG if set (naming no file reads NONE), else
# <toolkit>/x4-paths.env, else the 3.x <toolkit>/.claude/x4-paths.env (deprecated, still
# read for all of 4.x). The same rule as `_paths._locate_config()`, pinned to it by
# tools/x4validate/tests/test_config_precedence_agrees.py.
#
# THE ENVIRONMENT WINS, matching CLAUDE.md ("env var > x4-paths.env > default") and
# the Python half (`_paths._layers()` returns [env, file, fallback]).
#
# It did not, until 2026-09-03. install.sh writes bare KEY="value" lines and this
# file sourced them with `set -a`, so a plain assignment overwrote whatever had been
# exported -- and the note here was rewritten to match that rather than to fix it.
# A comment corrected to agree with a defect is not a decision.
#
# The divergence was the dangerous half: both halves resolve paths for the SAME
# machine, so exporting X4_GAME pointed x4validate at one install while the guards
# protecting the game folder read another. Protection and work aimed at different
# trees, silently.
# All locations are overridable; see x4-paths.env.example (toolkit root) for the keys.

# Toolkit root (where this toolkit lives).
# Prefer $CLAUDE_PROJECT_DIR; otherwise derive it from the hook's OWN location
# (<toolkit>/.claude/hooks/ -> <toolkit>), because falling back to $(pwd) makes every
# path resolve against whatever directory the shell happened to be in — which silently
# scattered auto-backups outside the toolkit whenever the var was unset.
# WAS IT REALLY IN THE ENVIRONMENT? The block below DERIVES a value when it is not,
# and the snapshot/restore further down exists so a real environment variable outranks
# the config file. A derived fallback is not an environment variable -- but it is
# indistinguishable from one by the time the snapshot is taken, so the config file's
# own X4_TOOLKIT was ALWAYS discarded.
#
# MEASURED 2026-09-05 with a config saying X4_TOOLKIT=<tkA> and no X4_* exported:
#     python  _paths.reference()  ->  <tkA>/reference   (honours the file)
#     bash    X4_REFERENCE        ->  <tkB>/reference   (ignored it)
# and a Write into <tkA>/reference was a SILENT ALLOW past the reference/ HARD BLOCK,
# while <tkB>/reference denied. X4_TOOLKIT is the key every other path derives from,
# and install.sh writes it into that file on every method -- so the guards were reading
# a different tree from the validator. That is the exact split this file's header says
# it exists to prevent.
_X4_TK_FROM_ENV=0
[ -n "${X4_TOOLKIT:-}" ] && _X4_TK_FROM_ENV=1
if [ -z "${X4_TOOLKIT:-}" ]; then
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    X4_TOOLKIT="$CLAUDE_PROJECT_DIR"
  elif [ -n "${HOOK_DIR:-}" ] && [ -d "$HOOK_DIR/../.." ]; then
    X4_TOOLKIT="$(cd "$HOOK_DIR/../.." && pwd)"
  else
    X4_TOOLKIT="$(pwd)"
  fi
fi

# --- the path config is DATA, never code (v4.0 release review R1-F1 / R1-P1) -------------
# ONE grammar, the same as `_paths.parse_env_report` (tests/test_config_precedence_agrees.py
# runs both over the same files):
#   * blank lines and `#` lines are skipped; a trailing CR (CRLF file) and a leading UTF-8
#     BOM are dropped from every line, for every key;
#   * `[export ]KEY=value`, KEY = X4_[A-Z0-9_]+ or XRCATTOOL, blanks around `=` allowed;
#   * the value: '...' is literal; "..." honours \\ \" \$ \` and expands $NAME / ${NAME}
#     (NAME = [A-Z0-9_]+); unquoted text expands too, keeps backslashes, and ends at a ` #`
#     comment; adjacent segments concatenate; an unterminated quote falls back to the whole
#     value with its outer quotes stripped (`_paths._unquote_legacy`);
#   * $NAME is the file's own earlier value, else the shell's; an unknown name is empty.
#     That is a STRING substitution: nothing is ever run.
# A line that does not fit is IGNORED and recorded in `_x4_cfg_ignored` as LINE:REASON --
#   shape (not KEY=value: `exit 0`, `source x`), key (not an X4 key: PATH=, JQ=),
#   subst ($( ) or a backtick), operator (an unquoted ; & | < >), guard (X4_GUARD or
#   X4_GUARD_CHECK: the guards are switched off only by the LAUNCH environment, never by a
#   file an agent could write).
# Line numbers and reasons only, never a value: the file may hold X4_NEXUS_KEY.
# An EMPTY value configures nothing. An exported (non-empty) value always wins -- for EVERY
# key, as in Python's `_layers()`; the sourcing loader protected only a named twelve -- except
# a DERIVED X4_TOOLKIT, which the file may replace (see _X4_TK_FROM_ENV above).
# No subshell and no process anywhere below: this runs on every hook call (HK-4).
#: ASCII name characters, spelled out: a bracket RANGE such as [A-Z] follows the locale's
#: collation in a bash pattern and can admit lowercase letters.
_X4_UC=ABCDEFGHIJKLMNOPQRSTUVWXYZ
_X4_NMC="${_X4_UC}0123456789_"
_X4_IDC="${_X4_NMC}abcdefghijklmnopqrstuvwxyz"
# FX-G2 item 8: the six ASCII whitespace characters, and every character Python's str.isspace()
# adds beyond them, as UTF-8. A config line holding one of the latter is refused (`shape`) here
# and in _paths.parse_env_report alike: `X4_GAME<NBSP>=/g` WAS a configuration to Python and a
# refused line to bash (whose [:space:] is ASCII in the C locale and locale-dependent otherwise).
_X4_AWS=$' \t\v\f\r'
_X4_ODDSP=($'\x1c' $'\x1d' $'\x1e' $'\x1f' $'\xc2\x85' $'\xc2\xa0' $'\xe1\x9a\x80'
  $'\xe2\x80\x80' $'\xe2\x80\x81' $'\xe2\x80\x82' $'\xe2\x80\x83' $'\xe2\x80\x84' $'\xe2\x80\x85'
  $'\xe2\x80\x86' $'\xe2\x80\x87' $'\xe2\x80\x88' $'\xe2\x80\x89' $'\xe2\x80\x8a' $'\xe2\x80\xa8'
  $'\xe2\x80\xa9' $'\xe2\x80\xaf' $'\xe2\x81\x9f' $'\xe3\x80\x80')

_x4_cfg_lookup() {   # NAME -> _x4_lv: the file's latest value for NAME, else the shell's
  local j=${#_X4_FK[@]}
  while [ "$j" -gt 0 ]; do
    j=$((j - 1))
    if [ "${_X4_FK[$j]}" = "$1" ]; then _x4_lv="${_X4_FV[$j]}"; return 0; fi
  done
  case "$1" in [0123456789]*) _x4_lv=""; return 0 ;; esac   # not a variable name
  _x4_lv="${!1:-}"
}

_x4_cfg_dollar() {   # TEXT-AFTER-$ -> _x4_dt (replacement), _x4_dn (chars consumed incl. $)
  local r="$1" nm t
  _x4_dt='$'; _x4_dn=1
  case "$r" in
    '('*) _x4_bad=subst ;;
    '{'*)
      t="${r#?}"; nm="${t%%\}*}"
      [ "$nm" != "$t" ] || return 0                       # no closing brace: a literal $
      case "$nm" in ''|*[!$_X4_NMC]*) return 0 ;; esac
      _x4_cfg_lookup "$nm"; _x4_dt="$_x4_lv"; _x4_dn=$(( ${#nm} + 3 )) ;;
    [$_X4_NMC]*)
      nm="${r%%[!$_X4_NMC]*}"
      _x4_cfg_lookup "$nm"; _x4_dt="$_x4_lv"; _x4_dn=$(( ${#nm} + 1 )) ;;
  esac
  return 0
}

_x4_cfg_legacy() {   # an UNTERMINATED quote: the whole value, outer quotes stripped, $NAME expanded
  local v="$1" out="" i=0 c
  case "$v" in *'$('*|*'`'*) _x4_bad=subst; return 0 ;; esac
  v="${v%% #*}"
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  if [ "${#v}" -ge 2 ]; then
    case "$v" in \"*\"|\'*\') v="${v:1:$((${#v} - 2))}" ;; esac
  fi
  while [ "$i" -lt "${#v}" ]; do
    c="${v:$i:1}"
    if [ "$c" = '$' ]; then
      _x4_cfg_dollar "${v:$((i + 1))}"; out="$out$_x4_dt"; i=$((i + _x4_dn))
    else
      out="$out$c"; i=$((i + 1))
    fi
  done
  _x4_v="$out"
}

_x4_cfg_value() {    # RAW-VALUE -> _x4_v, or _x4_bad=subst|operator (and _x4_v unset)
  local s="$1" i=0 j n c out="" ws="" seg
  _x4_bad=""; _x4_v=""
  s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"
  # Fast paths: what the installers write ("..." with nothing to escape or expand), and a
  # plain unquoted word. Everything else takes the full scan below.
  case "$s" in
    \"*\")
      seg="${s:1:$((${#s} - 2))}"
      case "$seg" in *[\"\\\$\`]*) ;; *) _x4_v="$seg"; return 0 ;; esac ;;
    \'*\')
      seg="${s:1:$((${#s} - 2))}"
      case "$seg" in *\'*) ;; *) _x4_v="$seg"; return 0 ;; esac ;;
    *[[:space:]\"\'\$\`\;\&\|\<\>]*) ;;
    *) _x4_v="$s"; return 0 ;;
  esac
  n=${#s}
  while [ "$i" -lt "$n" ]; do
    c="${s:$i:1}"
    case "$c" in
      \')
        seg="${s:$((i + 1))}"
        case "$seg" in *\'*) ;; *) _x4_cfg_legacy "$s"; return 0 ;; esac
        seg="${seg%%\'*}"
        out="$out$ws$seg"; ws=""; i=$((i + ${#seg} + 2)) ;;
      \")
        j=$((i + 1)); seg=""
        while :; do
          if [ "$j" -ge "$n" ]; then _x4_cfg_legacy "$s"; return 0; fi
          c="${s:$j:1}"
          case "$c" in
            \") break ;;
            \\)
              case "${s:$((j + 1)):1}" in
                \\|\"|\$|\`) seg="$seg${s:$((j + 1)):1}"; j=$((j + 2)) ;;
                *) seg="$seg$c"; j=$((j + 1)) ;;
              esac ;;
            \$)
              _x4_cfg_dollar "${s:$((j + 1))}"
              [ -n "$_x4_bad" ] && return 0
              seg="$seg$_x4_dt"; j=$((j + _x4_dn)) ;;
            \`) _x4_bad=subst; return 0 ;;
            *) seg="$seg$c"; j=$((j + 1)) ;;
          esac
        done
        out="$out$ws$seg"; ws=""; i=$((j + 1)) ;;
      \#)
        if [ "$i" -gt 0 ]; then
          case "${s:$((i - 1)):1}" in [[:space:]]) break ;; esac
        fi
        out="$out$ws$c"; ws=""; i=$((i + 1)) ;;
      [[:space:]]) ws="$ws$c"; i=$((i + 1)) ;;
      \$)
        _x4_cfg_dollar "${s:$((i + 1))}"
        [ -n "$_x4_bad" ] && return 0
        out="$out$ws$_x4_dt"; ws=""; i=$((i + _x4_dn)) ;;
      \`) _x4_bad=subst; return 0 ;;
      \;|\&|\||\<|\>) _x4_bad=operator; return 0 ;;
      *) out="$out$ws$c"; ws=""; i=$((i + 1)) ;;
    esac
  done
  _x4_v="$out"
}

# _x4_cfg_read FILE -- parse FILE and EXPORT what it configures (the environment winning).
# Sets _x4_cfg_ignored ("LINE:REASON ..."), read by x4_config_banner and x4doctor.
_x4_cfg_read() {
  local line l k n=0 j known
  _X4_FK=(); _X4_FV=(); _x4_cfg_ignored=""
  local pinned=" "
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line="${line%$'\r'}"
    [ "$n" = 1 ] && line="${line#$'\357\273\277'}"
    l="${line#"${line%%[!$_X4_AWS]*}"}"
    case "$l" in ''|\#*) continue ;; esac
    _x4_odd=0
    for _x4_sp in "${_X4_ODDSP[@]}"; do case "$line" in *"$_x4_sp"*) _x4_odd=1; break ;; esac; done
    if [ "$_x4_odd" = 1 ]; then _x4_cfg_ignored="$_x4_cfg_ignored $n:shape"; continue; fi
    case "$l" in export[[:space:]]*) l="${l#export}"; l="${l#"${l%%[![:space:]]*}"}" ;; esac
    case "$l" in *=*) ;; *) _x4_cfg_ignored="$_x4_cfg_ignored $n:shape"; continue ;; esac
    k="${l%%=*}"; k="${k%"${k##*[![:space:]]}"}"
    case "$k" in
      ''|[0123456789]*|*[!$_X4_IDC]*) _x4_cfg_ignored="$_x4_cfg_ignored $n:shape"; continue ;;
      XRCATTOOL) ;;
      X4_?*) case "${k#X4_}" in *[!$_X4_NMC]*) _x4_cfg_ignored="$_x4_cfg_ignored $n:key"; continue ;; esac ;;
      *) _x4_cfg_ignored="$_x4_cfg_ignored $n:key"; continue ;;
    esac
    case "$k" in X4_GUARD|X4_GUARD_CHECK) _x4_cfg_ignored="$_x4_cfg_ignored $n:guard"; continue ;; esac
    _x4_cfg_value "${l#*=}"
    if [ -n "$_x4_bad" ]; then _x4_cfg_ignored="$_x4_cfg_ignored $n:$_x4_bad"; continue; fi
    [ -n "$_x4_v" ] || continue
    known=0; j=${#_X4_FK[@]}
    while [ "$j" -gt 0 ]; do j=$((j - 1)); [ "${_X4_FK[$j]}" = "$k" ] && { known=1; break; }; done
    _X4_FK[${#_X4_FK[@]}]="$k"; _X4_FV[${#_X4_FV[@]}]="$_x4_v"
    case "$pinned" in *" $k "*) continue ;; esac
    if [ "$known" = 0 ] && [ -n "${!k:-}" ] && { [ "$k" != X4_TOOLKIT ] || [ "$_X4_TK_FROM_ENV" = 1 ]; }; then
      pinned="$pinned$k "             # exported before the file was read: the environment wins
      continue
    fi
    export "$k=$_x4_v"
  done < "$1"
  _x4_cfg_ignored="${_x4_cfg_ignored# }"
  unset _X4_FK _X4_FV _x4_v _x4_bad _x4_lv _x4_dt _x4_dn _x4_odd _x4_sp
  return 0
}

# _x4_lexnorm PATH -> REPLY: `.` and `..` components resolved LEXICALLY and repeated `/`
# collapsed, as Python's os.path.normpath does (FX-G5 / J2 item 4). A leading `/` or `//` is
# kept, a `..` never climbs above the start of a relative path's own text (it is kept, as
# normpath keeps it) nor past a root or a drive (`C:`). String operations only.
_x4_lexnorm() {
  local p="$1" lead="" part rest
  local -a out=()
  case "$p" in //*) [ "${p:2:1}" = "/" ] && lead="/" || lead="//" ;; /*) lead="/" ;; esac
  rest="$p/"
  while [ -n "$rest" ]; do
    part="${rest%%/*}"; rest="${rest#*/}"
    case "$part" in
      ""|.) ;;
      ..) if [ "${#out[@]}" -gt 0 ] && [ "${out[-1]}" != ".." ]; then
            case "${out[-1]}" in [A-Za-z]:) ;; *) unset 'out[-1]' ;; esac
          elif [ -z "$lead" ]; then out+=(..); fi ;;
      *) out+=("$part") ;;
    esac
  done
  local IFS=/
  REPLY="$lead${out[*]}"
  [ -n "$REPLY" ] || REPLY="."
  case "$REPLY" in [A-Za-z]:) REPLY="$REPLY/" ;; esac   # `C:/..` is the drive root, as ntpath
}

# Load the user's path config if present (KEY=VALUE lines).
# WHICH FILE (Plan 3 lane I). ONE rule, mirrored by _paths._locate_config and pinned to it by
# tests/test_config_precedence_agrees.py: $X4_CONFIG (explicit; naming no file reads NONE) >
# <toolkit>/x4-paths.env > <toolkit>/.claude/x4-paths.env (3.x, deprecated). Records the state
# for session-canary.sh and x4doctor and PRINTS NOTHING: this runs on every tool call, and
# stderr beside an empty verdict is a refusal to gates/hook_false_positives.py.
_x4_cfg=""; _x4_cfg_src=none; _x4_cfg_tk="$X4_TOOLKIT"
if [ -n "${X4_CONFIG:-}" ]; then
  # FX-B4 (reviewer I): the SPELLING the Python half opens. pathlib drops trailing separators
  # everywhere, and Windows drops a final component's trailing dots and spaces -- so
  # `x4-paths.env/` and (Windows) `x4-paths.env.` were READ by Python and "missing" here,
  # and the guards fell back to the DEFAULT reference root. When only the
  # normalized spelling is a file, X4_CONFIG takes that spelling, so every guard's name check
  # sees the file actually read. String operations only: this runs on every tool call.
  if [ ! -f "$X4_CONFIG" ]; then
    _x4_cn="$X4_CONFIG"
    case "${OSTYPE:-}" in msys*|cygwin*)
      _x4_cn="${_x4_cn//\\//}"
      # FX-G5 / reviewer J2 item 4: `<file>::$DATA` is the file's own data stream to Win32
      # (Python opens it); the suffix is dropped, case-insensitively, as _paths does.
      case "$_x4_cn" in *::[\$][Dd][Aa][Tt][Aa]) _x4_cn="${_x4_cn%::*}" ;; esac ;;
    esac
    # ...and `.` / `..` components are resolved LEXICALLY (Python's normpath), so
    # `x4-paths.env/.` and `sub/../x4-paths.env` name the file Python reads.
    _x4_lexnorm "$_x4_cn"; _x4_cn="$REPLY"
    while [ "${#_x4_cn}" -gt 1 ] && [ "${_x4_cn%/}" != "$_x4_cn" ]; do _x4_cn="${_x4_cn%/}"; done
    case "${OSTYPE:-}" in msys*|cygwin*)
      case "${_x4_cn##*/}" in
        .|..) ;;
        *) while case "$_x4_cn" in *[.\ ]) true ;; *) false ;; esac; do _x4_cn="${_x4_cn%?}"; done ;;
      esac ;;
    esac
    if [ -n "$_x4_cn" ] && [ "$_x4_cn" != "$X4_CONFIG" ] && [ -f "$_x4_cn" ]; then X4_CONFIG="$_x4_cn"; fi
    unset _x4_cn
  fi
  if [ -f "$X4_CONFIG" ]; then _x4_cfg="$X4_CONFIG"; _x4_cfg_src=explicit; else _x4_cfg_src=explicit-missing; fi
elif [ -f "$X4_TOOLKIT/x4-paths.env" ]; then
  _x4_cfg="$X4_TOOLKIT/x4-paths.env"; _x4_cfg_src=new
  [ -f "$X4_TOOLKIT/.claude/x4-paths.env" ] && _x4_cfg_src=both
elif [ -f "$X4_TOOLKIT/.claude/x4-paths.env" ]; then
  _x4_cfg="$X4_TOOLKIT/.claude/x4-paths.env"; _x4_cfg_src=legacy
fi
_x4_cfg_ignored=""
if [ -n "$_x4_cfg" ]; then
  # PARSED, NEVER SOURCED (v4.0 release review R1-F1 / R1-P1). This file used to run the
  # config as shell code (`set -a; . "$cfg"`), and the agent can write the config: a line
  # `exit 0` ended every hook before it spoke (silence is ALLOW), and `X4_GUARD=off` in it
  # turned every deny into an advisory -- under a banner saying "set at launch". The parse
  # is _x4_cfg_read below, the same rule as the Python half (`_paths.parse_env_report`),
  # pinned to it by tests/test_config_precedence_agrees.py. The environment still wins.
  _x4_cfg_read "$_x4_cfg"
fi

# x4_cfg_candidates -> _x4_cfg_paths: every file that IS (or would be) this toolkit's path
# config: the one read, an explicit $X4_CONFIG, and both standard locations under the toolkit
# root before AND after the file named one. Newline-separated, no duplicates. An agent may not
# write any of them (R1-F1 ruling 3) -- see protect-files.sh / protect-bash.sh.
# x4_cfg_why <path> -> _x4_cfg_why, the refusal both guards give. No subshell: a global.
x4_cfg_candidates() {
  local c
  _x4_cfg_paths=""
  for c in "${_x4_cfg:-}" "${X4_CONFIG:-}" "$_x4_cfg_tk/x4-paths.env" "$_x4_cfg_tk/.claude/x4-paths.env" \
           "${X4_TOOLKIT:-}/x4-paths.env" "${X4_TOOLKIT:-}/.claude/x4-paths.env"; do
    case "$c" in ''|/x4-paths.env|/.claude/x4-paths.env) continue ;; esac
    case "
$_x4_cfg_paths
" in *"
$c
"*) continue ;; esac
    _x4_cfg_paths="${_x4_cfg_paths:+$_x4_cfg_paths
}$c"
  done
}
x4_cfg_why() {
  _x4_cfg_why="BLOCKED: $1 is the toolkit's PATH CONFIG -- every guard reads its roots from it, so an agent edit could move or drop what they protect. Agents may not write or delete it, by a file edit or a shell command (v4.0). Ask the user to edit it by hand or re-run the installer; to see what is read: python \"$_x4_cfg_tk/scripts/x4config.py\" status --root \"$_x4_cfg_tk\""
}

# Fill only what config/env did not set. (Game/profile/mods/etc. have no safe default — may be empty.)
# The reference default is RECORDED (`_x4_ref_defaulted`), never silent: session-canary.sh and
# x4doctor read it to name a machine whose guards ASSUME <toolkit>/reference (Plan 3 lane I).
# Same assignment as the `: "${X4_REFERENCE:=...}"` it replaces.
_x4_ref_defaulted=0
if [ -z "${X4_REFERENCE:-}" ]; then X4_REFERENCE="$X4_TOOLKIT/reference"; _x4_ref_defaulted=1; fi

# x4_config_banner -> the SessionStart lines naming a missing, deprecated or doubled path config,
# on stdout (callers redirect), or NOTHING when the config is in order. Paths and KEY names only,
# never a value: the file carries X4_NEXUS_KEY. Called once per session (session-canary.sh), never
# per tool call -- every guard verdict is unchanged by the config's location or absence.
x4_config_banner() {
  # IGNORED LINES FIRST (R1-F1): a config the guards read as data but the user wrote as shell
  # code -- and above all an X4_GUARD line, which the user may believe has switched the guards
  # off. Line numbers and reasons only, never a value.
  if [ -n "${_x4_cfg_ignored:-}" ]; then
    case " $_x4_cfg_ignored" in
      *:guard*)
        printf '%s\n' "[x4 config] X4_GUARD in $_x4_cfg is IGNORED: the guards are switched off only by the environment the agent is LAUNCHED from (X4_GUARD=off), never by a file an agent can write. They are ON unless that says otherwise." ;;
    esac
    printf '%s\n' "[x4 config] $_x4_cfg is read as DATA, never run, and these lines were IGNORED (line:reason): $_x4_cfg_ignored. Reasons: shape = not KEY=value; key = not an X4_* key; subst = \$( ) or a backtick; operator = an unquoted ; & | < >; guard = X4_GUARD/X4_GUARD_CHECK. Fix them by hand; check with: python \"$_x4_cfg_tk/scripts/x4doctor.py\""
  fi
  case "$_x4_cfg_src" in
    none|explicit-missing)
      [ "$_x4_ref_defaulted" = 1 ] || return 0
      if [ "$_x4_cfg_src" = explicit-missing ]; then
        printf '%s\n' "[x4 config] NO PATH CONFIG: X4_CONFIG names $X4_CONFIG, which does not exist, so no config file is read, and X4_REFERENCE is not exported."
      else
        printf '%s\n' "[x4 config] NO PATH CONFIG: neither $_x4_cfg_tk/x4-paths.env nor the 3.x $_x4_cfg_tk/.claude/x4-paths.env exists, and X4_REFERENCE is not exported."
      fi
      printf '%s\n' "[x4 config] The guards ASSUME reference/ is $X4_REFERENCE (hard-blocked there) and know the game and profile only by folder NAME. Fix: re-run the installer, or copy $_x4_cfg_tk/x4-paths.env.example to $_x4_cfg_tk/x4-paths.env. Check: python \"$_x4_cfg_tk/scripts/x4doctor.py\"" ;;
    legacy)
      printf '%s\n' "[x4 config] DEPRECATED location: reading $_x4_cfg. 4.0 reads $_x4_cfg_tk/x4-paths.env. Move it: x4config.py migrate --apply, i.e. python \"$_x4_cfg_tk/scripts/x4config.py\" migrate --apply --root \"$_x4_cfg_tk\"" ;;
    both)
      printf '%s\n' "[x4 config] TWO path configs: reading $_x4_cfg; the 3.x $_x4_cfg_tk/.claude/x4-paths.env is IGNORED. If they differ, older guard copies protect a different tree. Resolve: python \"$_x4_cfg_tk/scripts/x4config.py\" status --root \"$_x4_cfg_tk\"" ;;
  esac
  return 0
}

# Derive the Steam app manifest from the game dir when possible (…/steamapps/common/X4 Foundations).
if [ -z "${X4_APPMANIFEST:-}" ] && [ -n "${X4_GAME:-}" ]; then
  # Two parent directories by parameter expansion: `dirname "$(dirname ...)"` cost two
  # processes and two subshells on EVERY hook call (AUDIT-2026-09-24 HK-4). Backslashes
  # are folded first, trailing separators dropped, so `C:\...\X4 Foundations\` and
  # `/c/.../X4 Foundations` both land on steamapps/.
  _sa="${X4_GAME//\\//}"
  while [ "${_sa%/}" != "$_sa" ]; do _sa="${_sa%/}"; done
  _sa="${_sa%/*}"; _sa="${_sa%/*}"
  [ -f "$_sa/appmanifest_392160.acf" ] && X4_APPMANIFEST="$_sa/appmanifest_392160.acf"
fi

# x4_acf_buildid FILE -> the INSTALLED build id from a Steam app manifest; prints nothing
# and returns 1 when there is none. THE ONE READER -- tests/test_acf_buildid_has_one_parser.py.
#
# A real manifest carries more than one "buildid": the installed build directly under
# AppState, plus one per beta branch under PrivateDepots/branches. MEASURED 2026-09-14 on
# the reference machine: AppState 23660954, public_beta 23524486. A plain grep answers
# with whichever comes first or last. bin/unpack-reference.sh took the LAST, so a
# re-unpack would have stamped the beta's build into the lock sentinel and the
# SessionStart hook would then have called a current reference/ stale. So: the key at
# brace depth 1, whatever order Steam writes the keys in.
x4_acf_buildid() {
  [ -f "${1:-}" ] || return 1
  # BINMODE=3: Git Bash's gawk strips CR on input by default and Linux/macOS awk does not
  # (MEASURED: a CRLF line reads length 1 vs 2), so without it the CR handling below would
  # be exercised on one platform only. Other awks ignore the unused variable.
  awk -v BINMODE=3 '
    { line = $0; sub(/\r$/, "", line) }
    line ~ /^[ \t]*\{[ \t]*$/ { depth++; next }
    line ~ /^[ \t]*\}[ \t]*$/ { depth--; next }
    depth == 1 && tolower(line) ~ /^[ \t]*"buildid"[ \t]+"[0-9]+"[ \t]*$/ {
      n = line; gsub(/[^0-9]/, "", n); print n; found = 1; exit
    }
    END { exit found ? 0 : 1 }
  ' "$1"
}

# --- path helpers: case-insensitive + backslash-insensitive (Windows/Git-Bash/macOS/Linux) ---
# x4_norm PATH_OR_COMMAND -> lowercase, backslashes to slashes, and the DRIVE DIALECT
# unified. MEASURED 2026-08-30: without the last step, Git Bash's "/c/Users/..." and
# Windows' "C:/Users/..." never compare equal, so any guard rooted purely on a
# configured PATH missed one of the two dialects -- the same Documents write asked in
# one form and was ALLOWED in the other. 2,553 historical commands use the MSYS form,
# and no probe in the suite had ever used a drive-lettered root, so nothing could have
# caught it. Rules carrying a NAME backstop (the game, the profile) were unaffected.
#
# Windows-to-MSYS is the safe direction: "c:/" is unambiguous, since a colon is illegal
# elsewhere in a Windows path, whereas "/c/" also occurs mid-path. The guard on the
# preceding character is what keeps "https://" from matching as a drive named "s".
#
# ONE subprocess, not two: sed's `y` transliterates, which is all `tr` was doing. This
# helper is the hot path of protect-files.sh -- MEASURED 2026-08-31 at 145 ms per call
# and ~22 calls per Edit/Write, i.e. ~6 s on every file edit, because x4_under
# canonicalises BOTH of its arguments on all 11 of its call sites. Process spawn is the
# whole cost on Windows; halving the spawns halves the bill.
#
# Behaviour is UNCHANGED and that was proven, not assumed: 43 of 43 path shapes agree
# with the two-process form, including the live configured roots, and the differential
# harness was shown able to detect a deliberately-wrong implementation. x4_norm is shared
# by every guard, and F93 is the entry about a shared helper quietly re-scoping the rules
# above it -- so this may cost less, and must not decide differently.
x4_norm() {
  printf '%s' "$1" | x4_norm_stream
}
# Shared sed program for single paths and line-safe bulk streams.
x4_norm_stream() {
  # Lowercase, backslash -> slash, drive dialect unified, repeated separators
  # collapsed, THEN dot segments resolved.
  #
  # The separator pass is the twin of the dot pass below and was added for the same
  # reason: `<root>//reference/x` names the same file as `<root>/reference/x` on
  # both POSIX and Windows, but compared equal to nothing and walked past the
  # reference/ HARD BLOCK. MEASURED 2026-09-03 in BOTH channels. A LEADING `//`
  # survives -- that is a UNC share, a different location -- which is what the
  # `(.)` guard in the rule is for.
  #
  # The dot-segment pass is not cosmetic. x4_canon only feeds POSIX-absolute paths to
  # realpath, so a WINDOWS-dialect path kept its `..` and compared unequal to the root:
  # MEASURED 2026-09-01, C:/<toolkit>/other/../reference/libraries/w.xml was NOT under
  # reference/, and protect-files.sh returned EMPTY -- a silent allow past a HARD BLOCK
  # -- while the identical /c/... form was correctly caught. hook_facts.norm() has
  # always collapsed them (posixpath.normpath); this is the same rule on the bash side,
  # so two implementations of one path fact stop disagreeing.
  #
  # Still ONE subprocess: the loops live inside the same sed program.
  #
  # The EXTENDED-LENGTH prefix goes first, and the order is the fix rather than a
  # detail. Windows accepts \\?\C:\... (and the \\.\ device form) for any path, the
  # Write tool passes it through, and Python opens it -- but it normalises to
  # //?/c:/... which is under no configured root, so the reference HARD BLOCK simply
  # did not fire. Stripping it AFTER the drive-dialect rule below would not help:
  # that rule rewrites <sep>c:/ to <sep>/c/, turning //?/c:/users into //?//c/users,
  # which still matches nothing.
  #
  # \\?\UNC\server\share is the same prefix over a network path and unwraps to
  # //server/share.
  #
  # WINDOWS ALIASES (FX-G2 item 3), the three lines after `tslash`: per component, an NTFS
  # stream suffix is dropped (`x::$DATA` and `x:alt` are x, `dir::$INDEX_ALLOCATION` is dir)
  # and so are trailing dots and spaces (`reference./` is reference). MEASURED 2026-10-05:
  # Python wrote INTO x4-paths.env through `x4-paths.env.`, `x4-paths.env ` and
  # `x4-paths.env::$DATA`, and every guard compared them equal to nothing. Twin of
  # hook_facts._win_alias -- the same conservative superset, on every platform.
  sed -E 'y/ABCDEFGHIJKLMNOPQRSTUVWXYZ\\/abcdefghijklmnopqrstuvwxyz\//
s#^//[?.]/unc/#//#
s#^//[?.]/##
s#(^|[^a-z0-9])([a-z]):/#\1/\2/#g
:slash
s#(.)//+#\1/#g
tslash
s#([^/]):[^/]+(/|$)#\1\2#g
s#([^/]):$#\1#
s#([^/]*[^/. ])[. ]+(/|$)#\1\2#g
:dot
s#/[.]/#/#g
tdot
s#/[.]$#/#
:dotdot
s#/[^/]+/[.][.](/|$)#/#
tdotdot
s#(.)/$#\1#'
}
# x4_winpath PATH -> _X4_WP: PATH as WINDOWS opens it, CASE KEPT (FX-G2 item 3) -- NTFS stream
# suffixes and trailing dots/spaces dropped per component (as x4_norm does), and an 8.3 SHORT
# name (`C:/PROGRA~2/.../X4FOUN~1`, MEASURED on this machine's game root) resolved to its long
# form via `cygpath -m -l` on the longest EXISTING prefix (it answers only for a path that
# exists). For the NAME tests in protect-files.sh, which read the path as written. COST: pure
# shell unless the path ends a component in a dot/space, carries a colon past the drive, or a
# `~<digit>`; then one sed, plus one cygpath for a short name (MEASURED ~17 ms). No cygpath
# (Linux/macOS: no 8.3 names) -> the short name is kept, i.e. today's verdict.
x4_winpath() {
  _X4_WP="$1"
  local need=0 r="${1#*[A-Za-z]:[/\\]}" d rest="" nd l
  case "$1" in *[.\ ]|*[.\ ]/*|*[.\ ]\\*|*~[0-9]*) need=1 ;; esac
  case "$r" in *:*) need=1 ;; esac
  [ "$need" = 1 ] || return 0
  _X4_WP="$(printf '%s' "$1" | sed -E 's#([^/\\]):[^/\\]+([/\\]|$)#\1\2#g
s#([^/\\]):$#\1#
s#([^/\\]*[^/\\. ])[. ]+([/\\]|$)#\1\2#g')" || { _X4_CANON_ERROR=1; return 1; }
  case "$_X4_WP" in *~[0-9]*) ;; *) return 0 ;; esac
  command -v cygpath >/dev/null 2>&1 || return 0
  d="$_X4_WP"
  while [ -n "$d" ] && [ ! -e "$d" ]; do
    nd="${d%[/\\]*}"; [ "$nd" = "$d" ] && return 0
    rest="/${d##*[/\\]}$rest"; d="$nd"
  done
  case "$d" in *~[0-9]*) ;; *) return 0 ;; esac
  l="$(cygpath -m -l -- "$d" 2>/dev/null)" && [ -n "$l" ] && _X4_WP="$l$rest"
  return 0
}
# x4_canon PATH -> resolve symlinks + .. (so e.g. a game-dir 'extensions' symlink and its real
# target compare equal). Uses realpath -m when available (no need for the file to exist);
# falls back to the raw path otherwise. Then normalized for case/slash-insensitive compare.
x4_canon() {
  x4_winpath "$1" || return 1
  local p="$_X4_WP"
  # STRIPPED BEFORE realpath, not after. `realpath -m` rewrites `//./X` to `//X`, which
  # x4_norm then renders `///c/...` -- under no root -- so the ordering fix written
  # inside x4_norm was defeated by its only caller. MEASURED 2026-09-02:
  #
  #   IN  //./C:/X/ref/a.xml    x4_norm  /c/x/ref/a.xml
  #                             x4_canon ///c/x/ref/a.xml
  #
  # The `//?/` form survived only because realpath happens to leave it alone.
  # The BACKSLASH device forms too (F331 review, MEASURED): x4guard's os.path.abspath turns
  # `//?/C:/x` into the backslash form, which x4_norm rendered `/?c` -- under no root, so a
  # write through a junction into reference/ was allowed. Stripped into the drive-form branch.
  case "$p" in
    '\\'[?.]'\'[Uu][Nn][Cc]'\'*) p="//${p:8}" ;;
    '\\'[?.]'\'*)            p="${p:4}" ;;
    //[?.]/[Uu][Nn][Cc]/*) p="//${p#//?/???/}" ;;
    //[?.]/*)              p="${p#//?/}" ;;
  esac
  # Only canonicalize POSIX-absolute paths (Linux/macOS, and Git-Bash "/c/..."). A Windows
  # "C:\..." path must NOT be fed to realpath (no leading "/" -> treated as relative -> mangled);
  # it is normalized as a string, and only under Git Bash resolved through a link (F331, below).
  # $p, NOT $1. Reading the original here discarded the strip above -- the branch tested
  # the unstripped path and realpath was handed it too, so the device form still came out
  # as ///c/... and a write into reference/ was still allowed.
  case "$p" in
    /*) command -v realpath >/dev/null 2>&1 && p="$(realpath -m -- "$p" 2>/dev/null || printf '%s' "$p")" ;;
    [A-Za-z]:[/\\]*|[A-Za-z]:)
      # F331: a WINDOWS-form path -- what Write/Edit send on Windows -- never reached realpath,
      # so `<toolkit>\dev\x.xml` with `dev` a JUNCTION into reference/ was judged as the toolkit
      # file it is spelled as: ALLOW, single and batch alike. Any user can make a junction (no
      # privilege), and no guard refuses making one. See x4_win_follow. Both sides of every
      # x4_under come through here, so a configured root behind a link resolves the same way.
      case "${OSTYPE:-}" in
        msys*|cygwin*)
          p="$(x4_norm "$p")" || return 1
          x4_win_has_link "$p" || { printf '%s' "$p"; return 0; }
          x4_win_follow "$p"; return ;;
      esac ;;
  esac
  x4_norm "$p"
}
# F331 helpers, Git Bash only (OSTYPE msys, or cygwin from Git Bash 5.3 -- MEASURED 2026-10-26).
# x4_win_has_link N: N is an x4_norm'd drive path (/c/...). True when an EXISTING component of it
# is a link (`-L` is true for a junction under Git Bash, MEASURED). Builtins only, so the common
# case -- no link anywhere -- costs no process and returns exactly the lexical form as before.
# `..` was already resolved LEXICALLY by x4_norm, which is how Windows itself opens the path.
# _X4_NOLINK memoises directories already seen to exist and not be links, so a batch of paths
# sharing a tree stats each shared prefix once (MEASURED: without it, 3589 paths ran 21 s -> 36 s
# under a 2%-CPU cap). An associative array is safe here: only Git Bash (bash >= 4) runs this,
# never macOS's bash 3.2.
case "${OSTYPE:-}" in msys*|cygwin*) declare -A _X4_NOLINK=() 2>/dev/null ;; esac
x4_win_has_link() {
  local cur="$1" rest="" c
  # Climb to the deepest prefix already known link-free; only what lies below it is probed.
  while [ -n "$cur" ] && [ -z "${_X4_NOLINK[$cur]:-}" ]; do
    rest="${cur##*/}${rest:+/$rest}"; cur="${cur%/*}"
  done
  while [ -n "$rest" ]; do
    c="${rest%%/*}"
    if [ "$c" = "$rest" ]; then rest=""; else rest="${rest#*/}"; fi
    cur="$cur/$c"
    [ -n "${_X4_NOLINK[$cur]:-}" ] && continue
    [ -L "$cur" ] && return 0
    [ -e "$cur" ] || return 1     # nothing under a missing component can be a link
    _X4_NOLINK[$cur]=1
  done
  return 1
}
# x4_win_follow N -> N resolved through its links, normalised. realpath alone is not enough: it
# reports a followed link's target in MOUNT form (`C:/Users/<u>/AppData/Local/Temp/x` came back
# `/tmp/x`, MEASURED) while a link-free root stays /c/..., so cygpath -m maps it back to the drive
# form first. FAIL CLOSED: any failure returns non-zero, which every caller refuses on -- never
# the unresolved spelling's verdict.
x4_win_follow() {
  local r w
  r="$(realpath -m -- "$1" 2>/dev/null)" && [ -n "$r" ] || return 1
  w="$(cygpath -m -- "$r" 2>/dev/null)" && [ -n "$w" ] || return 1
  x4_norm "$w"
}
# x4_under FILE DIR -> 0 (true) if FILE is inside DIR or equals it; false if DIR empty.
#
# The FIRST argument is memoised. protect-files.sh calls this 11 times and passes the
# SAME file path every time, so the identical canonicalisation was recomputed 10 times
# for nothing -- a subprocess each. A one-entry cache is enough precisely because the
# repetition is in argument one; the roots differ per call and caching them would buy
# little and cost correctness questions.
#
# Cache CORRECTNESS: keyed on the exact input string, and x4_canon is a pure function of
# it (realpath is read-only and the filesystem does not move mid-hook), so a hit returns
# what a recomputation would. Plain variables, not an associative array -- macOS ships
# bash 3.2, where `declare -A` fails silently and takes the guard with it.
#
# Returns through a GLOBAL, not stdout. A memo read with `x="$(memo ...)"` caches
# NOTHING: command substitution forks a subshell, the array writes land in the child and
# die with it. MEASURED 2026-08-31 -- 0 cache entries after two calls -- and the first
# version of this cost MORE than no cache at all, because every call was a miss plus a
# scan of a permanently empty array. The interleaved benchmark showed the memo slower
# than the un-memoised form, which read as noise and was not.
#
# Indexed arrays, not `declare -A`: macOS ships bash 3.2, where the associative form
# fails and takes the guard with it.
_X4_CK=()                 # cache keys
_X4_CV=()                 # cache values
_X4_CANON_RESULT=""       # the out-parameter
_X4_CANON_ERROR=""        # bulk callers refuse a failed canonicalization
x4_canon_memo() {
  local i=0 n=${#_X4_CK[@]}
  while [ "$i" -lt "$n" ]; do
    if [ "${_X4_CK[$i]}" = "$1" ]; then _X4_CANON_RESULT="${_X4_CV[$i]}"; return 0; fi
    i=$((i+1))
  done
  if ! _X4_CANON_RESULT="$(x4_canon "$1")"; then _X4_CANON_ERROR=1; fi
  _X4_CK[$n]="$1"; _X4_CV[$n]="$_X4_CANON_RESULT"
}
x4_under() {
  [ -n "$2" ] || return 1
  local f d
  x4_canon_memo "$1"; f="$_X4_CANON_RESULT"
  x4_canon_memo "$2"; d="${_X4_CANON_RESULT%/}"
  case "$f" in "$d"/*|"$d") return 0;; *) return 1;; esac
}

# --- user documents ----------------------------------------------------------
# Everything a person keeps outside the toolkit: game settings, saves, other games'
# data. On the reference machine Documents holds Elder Scrolls Online, Paradox
# Interactive, My Games and a backup archive alongside the X4 profile -- none of it
# reproducible, none of it ours.
#
# MEASURED 2026-08-29 before adding the rules that use this: over 11,133 historical
# commands, guarding all of Documents fires on 7 MORE commands (0.06%) than the
# existing X4-profile rules already did, and on ZERO more Edit/Write calls. Cheap.
#
# Empty when it cannot be resolved, and every rule below is guarded on non-empty --
# so an unconfigured machine gets no rule rather than a rule against "".
if [ -z "${X4_DOCUMENTS:-}" ]; then
  for _d in "${USERPROFILE:-}/Documents" "$HOME/Documents" "$HOME/My Documents"; do
    [ -n "${_d#/Documents}" ] && [ -d "$_d" ] && { X4_DOCUMENTS="$_d"; break; }
  done
fi
# The save folder, named separately because deleting one is unrecoverable and the
# message should say so rather than talking about "an X4 directory".
: "${X4_SAVES:=${X4_PROFILE:+$X4_PROFILE/save}}"

# --- hook payload -------------------------------------------------------------
# Read the hook's JSON payload from stdin.
#
# ⚠ MEASURED 2026-08-29, and it had made EVERY HOOK HERE INERT: `cat /dev/stdin`
# returns ZERO BYTES in the Claude Code hook environment, while a bare `cat`
# returns the payload. Seven consecutive probes: 0 bytes via /dev/stdin,
# 641-2840 bytes via bare cat, PreToolUse and PostToolUse alike.
#
# All five hooks used the former. The failure is invisible by construction: a hook
# that reads nothing falls through its first guard clause and exits 0, which is
# byte-identical to deciding "this is fine". Independent confirmation: no
# AUDIT_LOG.txt existed anywhere, and the only one that did contained 17 entries,
# all of them the test suite's synthetic /tmp fixture -- not one real edit in five
# weeks, while CLAUDE.md stated every edit was backed up.
#
# The suites passed throughout, because a suite pipes stdin explicitly and
# /dev/stdin resolves fine there. Green in the harness, dead in production.
x4_hook_input() { cat; }

# x4_require_input <payload> <reason> [event]
# A guard that cannot see its input cannot vouch for it, so it must not stay
# silent -- silence IS allow, and that is exactly how the defect above survived.
#
# The refusal must not depend on the tool that may have failed. MEASURED
# 2026-08-30: this emitted its `ask` THROUGH jq, so with jq unavailable an empty
# payload was reported by nothing at all -- allow again, one layer in. If jq
# cannot render the reason, a static literal goes out instead (no interpolation:
# a reason with a quote in it would need escaping we no longer have).
# x4_python -> print the interpreter to use, or nothing.
#
# ONE implementation. The three hooks that need Python each grew their own, and they
# DISAGREED on the case that matters: with X4_PYTHON set to something that does not
# resolve, protect-bash.sh refused (correctly -- an explicitly configured interpreter
# that is missing is an error, not a cue to quietly pick a different one), while
# protect-files.sh and backup-before-edit.sh fell through to python3/python/py. They
# also probed in opposite orders. A guard that runs under an interpreter the operator
# did not choose is a guard nobody configured.
#
# x4_resolve_python sets X4_PY instead of printing, so a caller needs no `$( )` subshell
# -- a process on Windows, on every hook call (AUDIT-2026-09-24 HK-4). x4_python is the
# printing form of the SAME function, kept for callers that want a value.
x4_resolve_python() {
  X4_PY=""
  if [ -n "${X4_PYTHON:-}" ]; then
    command -v "$X4_PYTHON" >/dev/null 2>&1 && X4_PY="$X4_PYTHON"
    return 0                      # set but unresolvable -> NOTHING, deliberately
  fi
  for _c in python python3 py; do
    if command -v "$_c" >/dev/null 2>&1; then X4_PY="$_c"; return 0; fi
  done
  return 0
}
x4_python() { x4_resolve_python; printf '%s' "$X4_PY"; }

# x4_field <payload> <dotted path, e.g. tool_input.path>
# Read one field from a hook payload without depending on jq alone.
#
# Fixing only the EMIT was a half fix: search-scope.sh READS its path with jq too, so
# with jq missing the read returned empty, the next line exited 0, and the shared
# emitter was never reached. MEASURED 2026-09-01 -- working jq: 420 bytes of advisory;
# broken jq: 0 bytes, still silent, after the emitter had supposedly been fixed. Teach
# EVERY step of the chain, not the last one.
#
# Returns empty for "absent" AND for "unreadable" -- fine for the ADVISORY hooks that
# use it, which have nothing to say either way. The two hooks that emit VERDICTS
# (protect-files, backup-before-edit) deliberately keep their own readers, because they
# must tell those two cases apart and ASK on the second.
x4_field() {
  # ONE jq process, not a health probe plus a read (AUDIT-2026-09-24 HK-4). A jq that is
  # missing or broken exits non-zero, and so does one handed an unreadable payload; both
  # fall through to python, which answers the same question -- so the result for every
  # case is what the probe-first form returned.
  local _jv
  if _jv="$(printf '%s' "$1" | "${JQ:-jq}" -r ".$2 // empty" 2>/dev/null)"; then
    printf '%s' "$_jv"
    return 0
  fi
  x4_resolve_python
  local _py="$X4_PY"
  [ -n "$_py" ] || return 0
  X4_IN="$1" X4_PATH="$2" "$_py" -c 'import json, os, sys
cur = json.loads(os.environ["X4_IN"])
for k in os.environ["X4_PATH"].split("."):
    if not isinstance(cur, dict):
        cur = None
        break
    cur = cur.get(k)
sys.stdout.write("" if cur is None else str(cur))' 2>/dev/null
}

# x4_advise <reason> [event]
# Emit an ADVISORY (an allow that carries a note to the model) without depending on jq
# alone. MEASURED 2026-09-01: search-scope.sh and x4validate-on-edit.sh emitted straight
# through `"$JQ"`, so with jq unavailable they produced 0 bytes and the advisory was
# simply lost -- silently, since 0 bytes is also how "nothing to say" looks. The cost is
# a lost note rather than a lost refusal, which is exactly why it could sit unnoticed.
#
# ONE implementation, so a third advisory hook cannot reintroduce the gap: the two
# guards that emit VERDICTS carry their own emitter because they also need deny/ask.
# x4_bound <text>  -- cap model-facing text, and SAY SO when it bites.
#
# MEASURED 2026-09-07, CC 2.1.263, this machine: Claude Code FILES a hook's
# model-facing output above 10,000 CHARACTERS and shows the model a ~2 KB
# preview. No error, exit code unchanged -- the failure is indistinguishable
# from success, which is the whole reason this exists. Four arms per channel,
# head+tail sentinel, discriminating on whether the TAIL survives:
#
#   bare stdout        500 BOTH · 10,000 BOTH · 10,001 HEAD · 30,000 HEAD
#   additionalContext  500 BOTH ·  9,950 BOTH · 10,001 HEAD · 30,000 HEAD
#
# The 9,950 arm is load-bearing: its raw stdout was 10,032 characters and it
# still arrived whole. So the cap is on the CONTENT the model receives and the
# JSON envelope does NOT count -- budget the reason at 10,000 flat, never
# "10,000 minus envelope".
#
# ⚠ THE NUMBER MOVES BETWEEN CC VERSIONS (high-20s K on 2.1.218 -> 10,000 on
# 2.1.246 -> still 10,000 on 2.1.263). Re-derive it on a CC bump with the probe
# above, keeping a small-size control arm so a broken probe cannot read as a
# clean pass. This is the ONLY place the number is written down; a second copy
# would drift, and a wrong constant here fails silently in the safe-looking
# direction.
#
# ONE bound, applied BEFORE either renderer runs. Doing it inside jq and again
# inside the python fallback would be two implementations of one rule, and the
# fallback is what runs on a machine with no jq -- exactly when nobody is
# looking. Renderer parity is then structural, not a coincidence two code paths
# have to keep re-earning.
X4_HOOK_MAX_CHARS="${X4_HOOK_MAX_CHARS:-10000}"
#: Room for the notice itself, so the bounded text INCLUDING its disclosure
#: still fits under the cap. A cap that overflows by the width of its own
#: warning is the joke version of this function. MEASURED: the notice is
#: 200 characters at 5-digit totals, so 300 leaves headroom for wider
#: numbers; the suite asserts the bounded result is <= the cap regardless.
_X4_BOUND_RESERVE=300

# Each arm is CHECKED, and a failing arm falls through to the next. x4_len used
# to `return 0` after the python arm whatever that arm did, so a python that
# RESOLVES but FAILS (a Windows Store stub, an X4_PYTHON pointing at a wrapper
# that errors) printed nothing, the length came back empty, and x4_bound took its
# non-numeric guard and passed the text through WHOLE. A fail-open inside the
# function written to close a fail-open, with the jq and bash arms unreachable
# because the first arm always claimed success. MEASURED with a stub exiting 9:
# 25,000 characters in, 25,000 out.
x4_len(){
  x4_resolve_python; _py="$X4_PY"
  if [ -n "$_py" ]; then
    _n="$(X4_BND="$1" "$_py" -c 'import os,sys; sys.stdout.buffer.write(str(len(os.environ["X4_BND"])).encode())' 2>/dev/null)"
    case "$_n" in ''|*[!0-9]*) : ;; *) printf '%s' "$_n"; return 0 ;; esac
  fi
  if printf '%s' '{}' | "${JQ:-jq}" -e . >/dev/null 2>&1; then
    _n="$("${JQ:-jq}" -rn --arg s "$1" '$s|length' 2>/dev/null)"
    case "$_n" in ''|*[!0-9]*) : ;; *) printf '%s' "$_n"; return 0 ;; esac
  fi
  printf '%s' "${#1}"          # bytes in the C locale; last resort, and it undercounts nothing
}

# sys.stdout.BUFFER, not sys.stdout. On Windows the text-mode stdout re-translates
# LF into CRLF on the way out, so a payload that already carried CRLF came back as
# CR CR LF and the slice GREW by one character per line AFTER the bound had been
# computed: 9,700 characters in, 9,807 out, capped result 10,007 against a 10,000
# ceiling. The suite caught it and my own spot-check did NOT, because text-mode
# open() collapses CRLF on the way back in and reported an honest-looking 9,900.
# Read bytes, write bytes.
#
# Each arm is CHECKED here too, for the same reason as x4_len above.
x4_head(){
  x4_resolve_python; _py="$X4_PY"
  if [ -n "$_py" ]; then
    if _o="$(X4_BND="$1" X4_BNDN="$2" "$_py" -c 'import os,sys; sys.stdout.buffer.write(os.environ["X4_BND"][:int(os.environ["X4_BNDN"])].encode("utf-8"))' 2>/dev/null)"; then
      printf '%s' "$_o"; return 0
    fi
  fi
  if printf '%s' '{}' | "${JQ:-jq}" -e . >/dev/null 2>&1; then
    if _o="$("${JQ:-jq}" -rn --arg s "$1" --argjson n "$2" '$s[0:$n]' 2>/dev/null)"; then
      printf '%s' "$_o"; return 0
    fi
  fi
  printf '%s' "${1:0:$2}"
}

x4_bound(){
  # FAST PATH. MEASURED by review: spawning an interpreter unconditionally cost
  # +117 ms per verdict (+32%) even for a 57-character reason -- a smaller
  # instance of the 11.3x latency regression this file's header records as the
  # class it was rewritten to remove, reintroduced by the fix for it.
  #
  # ${#1} is BYTES in the C locale and CHARACTERS in a UTF-8 one. Both are >= the
  # codepoint count, so a payload whose ${#1} already fits is under the cap on
  # either reading and the exact count is not needed. The skip can therefore only
  # err toward MEASURING, never toward letting something through unbounded --
  # which is the only direction that would matter.
  [ "${#1}" -le "$X4_HOOK_MAX_CHARS" ] && { printf '%s' "$1"; return 0; }
  _t="$(x4_len "$1")"
  # An unreadable length is NOT a licence to pass the text through: every arm of
  # x4_len is checked now, so reaching here means all three failed, and the
  # honest response is to bound blind rather than emit unbounded.
  case "$_t" in ''|*[!0-9]*) _t="" ;; esac
  if [ -n "$_t" ] && [ "$_t" -le "$X4_HOOK_MAX_CHARS" ]; then printf '%s' "$1"; return 0; fi
  _keep=$((X4_HOOK_MAX_CHARS - _X4_BOUND_RESERVE))
  if [ "$_keep" -lt 1 ]; then
    # A cap SMALLER than the notice made _keep negative, and a negative slice
    # keeps almost everything -- so the "bounded" result came back LARGER than
    # the cap it was asked for. MEASURED at X4_HOOK_MAX_CHARS=200 on a 5,000
    # character payload: 5,097 characters out, 25x the cap. The knob is
    # documented as overridable and the constant is expected to be re-derived on
    # a CC bump, so a lower ceiling is the realistic way in. Below the notice
    # width the notice itself has to shrink.
    _keep=$((X4_HOOK_MAX_CHARS / 2))
    [ "$_keep" -lt 1 ] && _keep=1
    printf '%s\n[TRUNCATED %s/%s]' "$(x4_head "$1" "$_keep")" "$_keep" "${_t:-?}"
    return 0
  fi
  printf '%s\n[TRUNCATED: showing %s of %s characters. Claude Code files hook output above %s and shows the model only a preview, so the rest is dropped HERE, deliberately, rather than vanishing silently.]' \
    "$(x4_head "$1" "$_keep")" "$_keep" "${_t:-an unreadable number of}" "$X4_HOOK_MAX_CHARS"
}

x4_advise() {
  set -- "$(x4_bound "$1")" "${2:-PreToolUse}"     # ONE bound, before either renderer
  if printf '%s' '{}' | "${JQ:-jq}" -e . >/dev/null 2>&1; then
    "${JQ:-jq}" -n --arg r "$1" --arg e "${2:-PreToolUse}" \
      '{hookSpecificOutput:{hookEventName:$e,additionalContext:$r}}'
    return 0
  fi
  x4_resolve_python; local _py="$X4_PY"
  if [ -n "$_py" ]; then
    X4_REASON="$1" X4_EVENT="${2:-PreToolUse}" "$_py" -c 'import json, os, sys
sys.stdout.buffer.write(json.dumps({"hookSpecificOutput": {
  "hookEventName": os.environ.get("X4_EVENT") or "PreToolUse",
  "additionalContext": os.environ["X4_REASON"]}}).encode("utf-8"))'
    return 0
  fi
  # Neither renderer. An advisory is a note, not a decision, so a static literal that
  # says the note was lost beats emitting nothing and pretending there was none.
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"X4 ADVISORY LOST: this hook had something to tell you but neither jq nor python is available to render it. Install jq, or set X4_PYTHON."}}'
}

x4_guard_check_inert() {
  # Machine-readable failure signal only for x4guard checks. Ordinary hooks keep
  # their existing verdicts and exit codes; check callers must not interpret an
  # inability to evaluate as a completed policy decision.
  [ "${X4_GUARD_CHECK:-}" = 1 ] && exit 2
  return 0
}

# X4_GUARD=off -- the user's LAUNCH-time escape hatch (spec 5.7, user decision #19). Read from
# the ENVIRONMENT only: _x4_cfg_read never sets X4_GUARD (or X4_GUARD_CHECK) from the path
# config, which an agent could write (R1-F1), and x4doctor reads it the same way. Exactly
# "off": any other value (OFF, 0, false, " off") leaves every guard ON, because a typo must
# never be what disables protection. Only a VERDICT is relaxed: a guard that could not check
# still says so, and Codex .rules and the OS deny on reference\ do not read this at all.
x4_guards_off() { [ "${X4_GUARD:-}" = off ]; }

# x4_guard_overridden <hook> <deny|ask> <reason> -- print the advisory that replaces a deny or
# an ask while the guards are off, and append the override to GUARDS-OFF.log beside the
# backups. A check (X4_GUARD_CHECK=1) logs nothing: x4guard check promises no side effects.
x4_guard_overridden() {
  case "$2" in deny) _x4_was="DENIED" ;; *) _x4_was="ASKED the user about" ;; esac
  if [ "${X4_GUARD_CHECK:-}" != 1 ]; then
    _x4_log="${X4_BACKUPS:-$X4_TOOLKIT/.claude/backups}/GUARDS-OFF.log"
    { mkdir -p "${_x4_log%/*}" &&
      printf '[%s] %s would have %s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$_x4_was" \
        "$(printf '%s' "$3" | tr '\r\n' '  ' | cut -c1-300)" >> "$_x4_log"; } 2>/dev/null || :
  fi
  printf 'X4 GUARDS OFF (X4_GUARD=off at launch): %s would have %s this call; it was NOT enforced. Its reason: %s' \
    "$1" "$_x4_was" "$3"
}

# x4_guard_relax <hook> <deny|ask|advise> <reason> -> _x4_rk, _x4_rr: the verdict to EMIT.
# Unchanged with the guards on, and for an advisory. Under X4_GUARD=off a rule's deny or ask
# becomes an advisory (logged) -- but NOT an INABILITY: a caller that could not check the call
# sets _X4_UNRELAXED=1 first, and that stays an ASK (v4.0 release review R1-F2; the CHANGELOG
# promises "a guard that could not check still asks"). One function, so every guard relaxes by
# the same rule -- search-scope.sh's deny ignored the switch while its own banner said
# "every verdict is an advisory" (R1-F5).
x4_guard_relax() {
  _x4_rk="$2"; _x4_rr="$3"
  { [ "$2" != advise ] && x4_guards_off; } || return 0
  if [ -n "${_X4_UNRELAXED:-}" ]; then
    _x4_rk=ask
    _x4_rr="X4 GUARDS ARE OFF (X4_GUARD=off at launch), but this guard COULD NOT CHECK this call -- the switch relaxes verdicts, never that, so it still asks: $3"
  else
    _x4_rk=advise
    _x4_rr="$(x4_guard_overridden "$1" "$2" "$3")"
  fi
  return 0
}

x4_require_input() {
  [ -n "$1" ] && return 0
  "${JQ:-jq}" -n --arg r "$2" --arg e "${3:-PreToolUse}" \
    '{hookSpecificOutput:{hookEventName:$e,permissionDecision:"ask",permissionDecisionReason:$r}}' 2>/dev/null \
  || printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"X4 GUARD INERT: this hook received NO INPUT and could not run jq to report it, so it checked nothing. Confirm only if you know why both are missing."}}'
  x4_guard_check_inert
  exit 0
}

# Protection-root packet shared with the synchronous save dispatcher. Values
# travel on stdin: MSYS rewrites path-looking environment values for native Python.
x4_emit_guard_roots() {
  local profile="${X4_PROFILE:-}" candidate
  if [ -z "$profile" ]; then
    for candidate in "${X4_DOCUMENTS:+$X4_DOCUMENTS/Egosoft/X4}" "${HOME:+$HOME/.config/EgoSoft/X4}"; do
      [ -n "$candidate" ] && [ -d "$candidate" ] && { profile="$candidate"; break; }
    done
  fi
  printf 'game\t%s\nprofile\t%s\nreference\t%s\nconfig\t%s\ntoolkit\t%s\nmods\t%s\ndocuments\t%s\nsaves\t%s\n--X4-ROOTS-END--' \
    "$X4_GAME" "$profile" "$X4_REFERENCE" "${X4_CONFIG:-}" "$X4_TOOLKIT" "$X4_MODS" "${X4_DOCUMENTS:-}" "${X4_SAVES:-}"
}

x4_save_dispatch() {
  local answer rc
  answer=$( { x4_emit_guard_roots; printf '%s' "$INPUT"; } | "$PY" "$HOOK_DIR/save_policy.py" "$1" "$0")
  rc=$?
  case "$rc:$answer" in
    '0:X4SAVE '*) printf '%s' "${answer#X4SAVE }" ;;
    *) case "$rc:$answer" in
         # K10: under X4_GUARD_CHECK=1 a refusal that checked nothing exits 2. Its static
         # reason (e.g. "PowerShell command does not parse") is the actionable part: pass it
         # through -- only a deny or an ask, and still "checked nothing" (exit 2 below).
         '2:X4SAVE {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", '*|\
         '2:X4SAVE {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", '*)
           printf '%s' "${answer#X4SAVE }" ;;
         *) printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"X4 save dispatcher did not return a complete decision; this call was not allowed."}}' ;;
       esac
       x4_guard_check_inert
       return 0 ;;
  esac
}

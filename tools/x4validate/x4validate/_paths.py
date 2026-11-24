r"""One place that answers "where is the game / profile / reference / registry?".

**The defect this exists to fix (shipped in v2.0).** `install.sh` / `install.ps1`
write `.claude/x4-paths.env` using the names `X4_GAME`, `X4_EXTENSIONS`,
`X4_PROFILE`, `X4_MODS`, `X4_REFERENCE`, `X4_TOOLKIT`. The Python package read a
*different* set — `X4_GAME_EXTENSIONS`, `X4_PROFILE_CONTENT`,
`X4_PROFILE_EXTENSIONS`, `X4_WORKSHOP_CONTENT`, `X4_REGISTRY` — and **the overlap
was exactly one name, `X4_REFERENCE`.** Nothing bridged them: `x4-paths.env` is read
by `.claude/hooks/_x4-env.sh` and `bin/`, never by Python. So a user on the
`separate` or `global` install layout ran the installer, saw it succeed, and then had
`--tier b`, `x4compat`, `x4stats`, `x4similar`, `x4xref`, `x4modlist` and
`x4effective` silently fall back to CWD-relative paths. It was invisible on the
development machine only because the hardcoded defaults there happened to be right.

**Resolution order** for every location, first hit wins:

  1. the INSTALLER's env var name (what `x4-paths.env` and the docs use)
  2. the LEGACY env var name (what the Python used to read) — nothing that works
     today may break
  3. the config FILE (the common case, since a plain shell exports none of the
     above), chosen by ONE rule that `_x4-env.sh` mirrors exactly (Plan 3 lane I;
     `tests/test_config_precedence_agrees.py` pins the two together):
     `$X4_CONFIG` (explicit -- naming no file means NO file is read) >
     `<toolkit>/x4-paths.env` > `<toolkit>/.claude/x4-paths.env` (the 3.x location,
     still read for all of 4.x, with a one-line deprecation notice) > none.
     `<toolkit>` is the toolkit this module LIVES IN (B2, install red-team 2026-10-04:
     an inherited `$X4_TOOLKIT` naming another copy made a second toolkit's
     `x4refguard apply` target the first one's reference tree), or `--toolkit` when a
     command was given one (`use_toolkit`). `$X4_TOOLKIT` naming a different directory
     earns one stderr line naming both roots and is otherwise NOT used. Only code living
     outside the `<root>/tools/x4validate/x4validate/` layout falls back to `$X4_TOOLKIT`,
     and only when that is unset too does the search walk up from the CWD, new before
     legacy at each level.
     The bash loader (`_x4-env.sh`, in the DEPLOYED guard copies) still reads
     `$X4_TOOLKIT`: a guard lives in a game root and legitimately reaches a separate
     toolkit through it. The two agree whenever `$X4_TOOLKIT` names the toolkit whose
     tools run -- the installed shape -- which tests/test_config_precedence_agrees.py
     pins.
  4. a derivation from an already-resolved location (`$X4_GAME/extensions`,
     `$X4_PROFILE/content.xml`, ...)
  5. `_LOCAL_FALLBACK` — development-machine defaults, empty in the public tree

The file is PARSED, never sourced: running a shell to read config is an arbitrary
code path we do not need, and it would not work on Windows without a shell anyway.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from . import _mutation

#: Steam Workshop id for X4: Foundations, used to derive the workshop content dir.
STEAM_APPID = "392160"

#: Last-resort overrides, and **deliberately empty**.
#:
#: This is the documented seam for a local default, so that a contributor who needs
#: one has an obvious single place to put it instead of scattering
#: `os.environ.get(..., r"C:\Users\...")` through the package — which is exactly how
#: six personal paths ended up hardcoded in the shipped v2.0.
#:
#: It stays empty even on the development machine. A dev-only fallback is dead code
#: until the day it silently rescues a broken config and hides it, and the loud
#: failures are better: an unresolved reference tree is a hard error ("validation is
#: meaningless without it"), and an unresolved extensions dir makes Tier B a degraded
#: skip that explicitly reports "this result is NOT a pass". A fallback here would
#: paper over precisely the misconfiguration the tool is built to shout about.
_LOCAL_FALLBACK: dict[str, str] = {}

#: `[export ]KEY = value`, KEY any shell name. Which KEYS count is decided after the match,
#: so a line naming another key is REPORTED (`key`), not silently skipped.
_LINE = re.compile(r"""^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*?)\s*$""")
_OUR_KEY = re.compile(r"^(?:X4_[A-Z0-9_]+|XRCATTOOL)$")
#: Whitespace to the shell: the six ASCII characters bash's [:space:] holds in the C locale.
_ASCII_WS = " \t\n\r\x0b\x0c"
#: Every character str.isspace() calls whitespace BEYOND those six. A config line holding one is
#: refused (`shape`) by both loaders, never read two ways (FX-G2 item 8; _X4_ODDSP in _x4-env.sh).
_ODD_SPACE = re.compile("[\x1c-\x1f\x85\xa0  -     　]")
#: The guard switches. Read from the LAUNCH environment only (v4.0 release review R1-F1): a
#: config line naming one is ignored and reported, in both loaders.
GUARD_KEYS = frozenset({"X4_GUARD", "X4_GUARD_CHECK"})
_NAME = re.compile(r"\{(?P<b>[A-Z0-9_]+)\}|(?P<p>[A-Z0-9_]+)")
_OPERATORS = frozenset(";&|<>")


class _Refused(Exception):
    """A value the grammar refuses (`subst` | `operator`): the whole LINE is ignored."""


def _dollar(rest: str, lookup) -> tuple[str, int]:
    """`$` followed by *rest* -> (replacement, characters consumed including the `$`).

    `$NAME` / `${NAME}` (NAME = [A-Z0-9_]+) expand through *lookup*; `$(` refuses the line;
    anything else is a literal `$`. A string substitution -- nothing is ever run."""
    if rest.startswith("("):
        raise _Refused("subst")
    m = _NAME.match(rest)
    if not m:
        return "$", 1
    name = m.group("b") or m.group("p")
    return lookup(name), 1 + m.end()


def _unquote(v: str, lookup=lambda n: "") -> str:
    r"""A shell-style value -- the SAME grammar as `_x4_cfg_value` in `_x4-env.sh`.

    '...' is literal. "..." honours the escapes \\ \" \$ \` (any other backslash is kept)
    and expands $NAME / ${NAME}. Unquoted text expands too, keeps its backslashes (a
    hand-written `C:\\Games` stays a Windows path) and ends at a ` #` comment -- a `#`
    mid-word is data. Adjacent segments CONCATENATE, as in the shell: `"a"b` is `ab`
    (review of RG-5). Unquoted whitespace is kept inside the value and trimmed at its ends.
    An UNTERMINATED quote falls back to `_unquote_legacy`. `$( )`, a backtick, or an unquoted
    ; & | < > raise `_Refused`: the line is shell code, and code is never run (R1-P1).

    The comment was cut first, for both quoting styles, until AUDIT-2026-09-24 RG-5, so
    `X4_MODS="C:/My Mods #2/x4"` became `"C:/My Mods` with a dangling quote.
    """
    s = v.strip()
    out: list[str] = []
    ws = ""                                       # unquoted whitespace not yet known to be inner
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            end = s.find("'", i + 1)
            if end < 0:
                return _unquote_legacy(v, lookup)
            out.append(ws + s[i + 1:end]); ws = ""
            i = end + 1
        elif c == '"':
            j, seg = i + 1, []
            while True:
                if j >= n:
                    return _unquote_legacy(v, lookup)
                d = s[j]
                if d == '"':
                    break
                if d == "\\":
                    nxt = s[j + 1:j + 2]
                    if nxt in ("\\", '"', "$", "`") and nxt:
                        seg.append(nxt); j += 2
                    else:
                        seg.append(d); j += 1
                elif d == "$":
                    t, k = _dollar(s[j + 1:], lookup)
                    seg.append(t); j += k
                elif d == "`":
                    raise _Refused("subst")
                else:
                    seg.append(d); j += 1
            out.append(ws + "".join(seg)); ws = ""
            i = j + 1
        elif c == "#" and i > 0 and s[i - 1].isspace():
            break                                 # a comment: the value ended before it
        elif c.isspace():
            ws += c; i += 1
        elif c == "$":
            t, k = _dollar(s[i + 1:], lookup)
            out.append(ws + t); ws = ""
            i += k
        elif c == "`":
            raise _Refused("subst")
        elif c in _OPERATORS:
            raise _Refused("operator")
        else:
            out.append(ws + c); ws = ""
            i += 1
    return "".join(out)


def _unquote_legacy(v: str, lookup=lambda n: "") -> str:
    """The pre-concatenation reading, kept ONLY for a value with an unterminated quote:
    the value up to ` #`, outer quotes stripped, then $NAME expanded over the whole."""
    if "$(" in v or "`" in v:
        raise _Refused("subst")
    v = v.split(" #", 1)[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    out, i = [], 0
    while i < len(v):
        if v[i] == "$":
            t, k = _dollar(v[i + 1:], lookup)
            out.append(t); i += k
        else:
            out.append(v[i]); i += 1
    return "".join(out)


def parse_env_report(path: Path) -> tuple[dict[str, str], list[tuple[int, str]]]:
    """`(values, ignored)` for an x4-paths.env, PARSED and never executed.

    THE SAME GRAMMAR as `_x4_cfg_read` in `.claude/hooks/_x4-env.sh` (the guards' loader,
    which SOURCED the file as shell code until the v4.0 release review: R1-F1 / R1-P1), and
    tests/test_config_precedence_agrees.py runs both over the same files. *ignored* lists
    `(line number, reason)` for every line that is neither blank nor a comment and not a
    configuration -- reasons `shape` (not KEY=value), `key` (not an X4 key), `subst`
    (`$( )` / backtick), `operator` (an unquoted ; & | < >), `guard` (X4_GUARD /
    X4_GUARD_CHECK: launch environment only). Line numbers and reasons, never a value:
    the file may hold X4_NEXUS_KEY.

    `$NAME` expands against keys already seen in the file, then the real environment --
    what a shell sourcing it top-to-bottom would do. Unknown names expand to empty rather
    than staying a literal `$X4_TOOLKIT` that would later become a bogus directory name.
    An empty value configures nothing. A CR (CRLF file) and a leading BOM are dropped.
    """
    out: dict[str, str] = {}
    ignored: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        # silent-ok: an unreadable config file is simply "no config" — the caller
        # falls through to the next resolution layer, and `describe()` reports
        # which file (if any) was actually used.
        return out, ignored

    def lookup(name: str) -> str:
        return out.get(name, os.environ.get(name, ""))

    for n, raw in enumerate(text.split("\n"), 1):
        raw = raw[:-1] if raw.endswith("\r") else raw
        # ASCII whitespace only, here and below (FX-G2 item 8): `\s` and str.isspace() are
        # UNICODE, bash's [:space:] is not, and `X4_GAME<NBSP>=/g` was a configuration here and
        # a refused line to the guards -- one file, two trees. MEASURED by the delta review.
        if not raw.strip(_ASCII_WS) or raw.lstrip(_ASCII_WS).startswith("#"):
            continue
        if _ODD_SPACE.search(raw):
            ignored.append((n, "shape"))
            continue
        m = _LINE.match(raw)
        if not m:
            ignored.append((n, "shape"))
            continue
        key = m.group("key")
        if not _OUR_KEY.match(key):
            ignored.append((n, "key"))
            continue
        if key in GUARD_KEYS:
            ignored.append((n, "guard"))
            continue
        try:
            val = _unquote(m.group("val"), lookup)
        except _Refused as why:
            ignored.append((n, str(why)))
            continue
        if val:
            out[key] = val
    return out, ignored


def parse_env_file(path: Path) -> dict[str, str]:
    """`KEY="value"` pairs from an x4-paths.env -- `parse_env_report` without the report."""
    return parse_env_report(path)[0]


#: The config file's name, the same at the 4.x location (the toolkit root) and the
#: 3.x one (`<toolkit>/.claude/`).
CONFIG_NAME = "x4-paths.env"
#: The 3.x location, relative to a toolkit root. Read for all of 4.x, deprecated.
LEGACY_CONFIG = Path(".claude", CONFIG_NAME)

#: Configs already given their one deprecation notice in this process.
_NOTICED: set[str] = set()


# --- WHICH toolkit this process acts for (B2, install red-team 2026-10-04) ---------------
#
# THE INCIDENT. A second toolkit copy's `x4refguard.py apply`, run in a shell that had
# inherited X4_TOOLKIT naming the user's REAL toolkit, imported its own `_paths` -- which
# located the config through $X4_TOOLKIT and so resolved the OTHER toolkit's reference tree.
# A script now acts for the toolkit it LIVES IN; $X4_TOOLKIT naming a different directory
# earns one notice naming both, and system-changing commands refuse unless the caller passes
# --toolkit (`use_toolkit`). The deployed GUARD copies are a different case and keep reading
# $X4_TOOLKIT: they live in a game root and legitimately reach a separate toolkit.

def _self_toolkit_of(module_file: Path) -> Path | None:
    """`<root>` when *module_file* sits at `<root>/tools/x4validate/x4validate/<file>`, the
    layout every toolkit copy has (a checkout, an installed copy). None for any other
    layout, e.g. the package installed into a site-packages -- which then has no "own"
    toolkit, and $X4_TOOLKIT stays the only answer, exactly as before."""
    pkg = Path(module_file).resolve().parent
    proj = pkg.parent
    if pkg.name == "x4validate" and proj.name == "x4validate" and proj.parent.name == "tools":
        return proj.parent.parent
    return None


#: The toolkit this module lives in (see `_self_toolkit_of`). A module attribute so an
#: in-process test can model "installed outside any toolkit" without moving files.
_SELF: Path | None = _self_toolkit_of(Path(__file__))
#: The toolkit named by an explicit --toolkit (`use_toolkit`), else None.
_EXPLICIT: Path | None = None


def self_toolkit() -> Path | None:
    """The toolkit this code lives in, or None outside the toolkit layout."""
    return _SELF


def use_toolkit(root) -> None:
    """--toolkit: act for *root*, explicitly. The one way a system-changing command may act
    while $X4_TOOLKIT names a different toolkit."""
    global _EXPLICIT
    _EXPLICIT = Path(native(str(root)))
    reload()


def explicit_toolkit() -> Path | None:
    """The --toolkit given to this process, or None."""
    return _EXPLICIT


def _env_toolkit() -> Path | None:
    v = os.environ.get("X4_TOOLKIT")
    return Path(native(v)) if v else None


def toolkit_root() -> Path | None:
    """The toolkit this process acts for: --toolkit > the toolkit this code lives in >
    $X4_TOOLKIT (only when the code lives outside the toolkit layout) > None."""
    if _EXPLICIT is not None:
        return _EXPLICIT
    if _SELF is not None:
        return _SELF
    return _env_toolkit()


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def toolkit_conflict() -> tuple[Path, Path] | None:
    """`(acting toolkit, the different one $X4_TOOLKIT names)`, or None when they agree,
    when X4_TOOLKIT is unset, or when there is no acting toolkit to differ from."""
    env, acting = _env_toolkit(), toolkit_root()
    if env is None or acting is None or _same_dir(env, acting):
        return None
    return acting, env


def toolkit_notice() -> None:
    """ONE stderr line per process when $X4_TOOLKIT names another toolkit: both ROOTS, never
    a config value."""
    c = toolkit_conflict()
    if c is None or "toolkit-conflict" in _NOTICED:
        return
    _NOTICED.add("toolkit-conflict")
    acting, env = c
    why = "--toolkit" if _EXPLICIT is not None else "the toolkit this tool lives in"
    # FX-B2: "NOT used here" is true of X4_TOOLKIT alone. Any other exported root still
    # outranks the config, so the line names those instead of offering false comfort.
    still = sorted(k for keys in ROOT_KEYS.values() for k in keys if os.environ.get(k))
    tail = (f" Other exported roots ARE still read and outrank the config: {', '.join(still)}."
            if still else "")
    print(f"x4 toolkit: acting for {acting} ({why}); $X4_TOOLKIT names a different toolkit, "
          f"{env}, which is NOT used here -- run that toolkit's own copy to act for it.{tail}",
          file=sys.stderr)


def foreign_toolkit_refusal(action: str) -> str | None:
    """The refusal for a SYSTEM-CHANGING *action* while $X4_TOOLKIT names a different
    toolkit and no --toolkit was given, else None. Callers print it and exit 2."""
    c = toolkit_conflict()
    if c is None or _EXPLICIT is not None:
        return None
    acting, env = c
    return (f"REFUSED: `{action}` changes your system, and $X4_TOOLKIT names {env} while "
            f"this tool lives in {acting}. Nothing was changed. To act for THIS toolkit, "
            f"re-run with --toolkit \"{acting}\"; to act for the other one, run its own copy "
            f"(or fix X4_TOOLKIT).")


#: `<file>::$DATA` -- the file's own unnamed data stream to Win32 (any case).
_DATA_STREAM = re.compile(r"::\$data$", re.IGNORECASE)


def config_spelling(value: str) -> Path:
    """The file an explicit `$X4_CONFIG` names, spelled as `_x4-env.sh` spells it (FX-G5 /
    reviewer J2 item 4): a Windows `::$DATA` suffix dropped, then `.` and `..` components
    resolved LEXICALLY (normpath) -- the bash loader found no file for `x4-paths.env/.`,
    `sub/../x4-paths.env/.` or `x4-paths.env::$DATA` while this module read one."""
    v = native(value)
    if _IS_WINDOWS:
        v = _DATA_STREAM.sub("", v)
    return Path(os.path.normpath(v)) if v else Path(v)


def config_names_stream(value: str) -> bool:
    """Does an explicit `$X4_CONFIG` name a Windows stream OTHER than exactly one trailing
    `::$DATA` -- `x4-paths.env::$DATA/.`, `::$DATA::$DATA`, `::$DATA ` (FX-G6 / reviewer K M2)?
    Windows opens those as the file, and `_x4-env.sh` finds none: such a spelling names NO
    config file, in both loaders. Windows only; elsewhere `::` is an ordinary filename byte."""
    return _IS_WINDOWS and "::" in _DATA_STREAM.sub("", native(value).replace(chr(92), "/"))


def _locate_config() -> tuple[Path | None, str, Path | None]:
    """`(file read or None, state, the OTHER location)` by the module docstring's rule.

    state is one of `explicit | explicit-missing | new | both | legacy | none`;
    `config_state()` refines `both`. The other location is the 3.x copy for `both`
    and the 4.x path a `legacy` config should move to; None otherwise.
    """
    explicit = os.environ.get("X4_CONFIG")
    if explicit:                                  # empty counts as unset, like ${X4_CONFIG:-}
        p = config_spelling(explicit)
        if p.is_file() and not config_names_stream(explicit):
            return p, "explicit", None
        return None, "explicit-missing", None
    toolkit = toolkit_root()                      # B2: the toolkit this code lives in
    if toolkit is not None:
        roots = [toolkit]
    else:
        here = Path.cwd().resolve()
        roots = [here, *here.parents]
    for d in roots:
        new, old = d / CONFIG_NAME, d / LEGACY_CONFIG
        if new.is_file():
            return (new, "both", old) if old.is_file() else (new, "new", None)
        if old.is_file():
            return old, "legacy", new
    return None, "none", None


def _find_env_file() -> Path | None:
    """The config file actually read (see `_locate_config`), or None."""
    return _locate_config()[0]


def _assignments(p: Path) -> list[str] | None:
    """The comparable content of a config: its `KEY=value` lines, CR and indent
    stripped, blank and comment lines dropped, sorted. A comment difference therefore
    AGREES and a quoting difference DIFFERS -- conservative on purpose. None = the file
    could not be read: a NON-ANSWER, which `_differing_keys` never lets compare equal."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:  # silent-ok: not silent -- None reaches _differing_keys, which reports "<unreadable config>"
        return None
    out = []
    for raw in text.split("\n"):
        line = raw.replace("\r", "").lstrip()
        if line and not line.startswith("#") and "=" in line:
            out.append(line)
    return sorted(out)


def _key_of(line: str) -> str:
    k = line.split("=", 1)[0].strip()
    return k[len("export "):].strip() if k.startswith("export ") else k


def _differing_keys(a: Path, b: Path) -> list[str]:
    """KEY NAMES whose assignments differ between two configs -- never a value: the
    file carries `X4_NEXUS_KEY`."""
    la, lb = _assignments(a), _assignments(b)
    if la is None or lb is None:
        return ["<unreadable config>"]
    sa, sb = set(la), set(lb)
    return sorted({_key_of(l) for l in sa ^ sb})


def config_state() -> str:
    """`explicit | explicit-missing | new | both-agree | both-differ | legacy | none`."""
    found, state, other = _locate_config()
    if state == "both":
        return "both-differ" if _differing_keys(found, other) else "both-agree"
    return state


def config_file_in(root: Path) -> Path:
    """THE config file of the toolkit at *root*: the 4.x one if it exists, else the 3.x
    one if THAT exists, else the 4.x path (the one to demand)."""
    root = Path(root)
    new, old = root / CONFIG_NAME, root / LEGACY_CONFIG
    if new.is_file():
        return new
    return old if old.is_file() else new


def _migrate_cmd(toolkit: Path) -> str:
    """The one migration command every notice names: its short form first (what a
    reader searches for), then the exact, quoted line to run."""
    return f'x4config.py migrate --apply, i.e. python "{toolkit / "scripts" / "x4config.py"}" migrate --apply'


def _notice(env_file: Path | None) -> None:
    """ONE stderr line per process for a deprecated (`legacy`) or doubled (`both`)
    config. Paths and KEY NAMES only, never a value. The bash loader prints nothing
    per call (a hook's stderr beside an empty verdict is a refusal); this is Python,
    where a CLI's stderr is read by a person."""
    explicit = os.environ.get("X4_CONFIG")
    if env_file is None and explicit:
        # v4.0.0 review R5-6: an X4_CONFIG naming no file means NO config is read, and every
        # path silently fell back (reference -> <toolkit>/reference); only --paths said so.
        key = "explicit-missing:" + explicit
        if key not in _NOTICED:
            _NOTICED.add(key)
            print(f"x4 config: X4_CONFIG names {native(explicit)}, which does not exist -- NO "
                  f"config file is read, so every path comes from the environment or a "
                  f"default. Fix X4_CONFIG, or unset it to use <toolkit>/x4-paths.env.",
                  file=sys.stderr)
        return
    if env_file is None or explicit:
        return
    key = str(env_file)
    if key in _NOTICED:
        return
    if env_file.parent.name == ".claude" and env_file.name == CONFIG_NAME:
        tk = env_file.parent.parent
        msg = (f"x4 config: {env_file} is the 3.x location and is deprecated; 4.0 reads "
               f"{tk / CONFIG_NAME}. Move it: {_migrate_cmd(tk)} (or re-run the installer).")
    elif env_file.name == CONFIG_NAME and (env_file.parent / LEGACY_CONFIG).is_file():
        tk, old = env_file.parent, env_file.parent / LEGACY_CONFIG
        keys = _differing_keys(env_file, old)
        differ = (f" They DIFFER on: {', '.join(keys)} -- older guard copies may protect a "
                  f"different tree." if keys else " They agree.")
        msg = (f"x4 config: reading {env_file}; the deprecated 3.x copy {old} is IGNORED."
               f"{differ} Resolve: x4config.py status, i.e. python \"{tk / 'scripts' / 'x4config.py'}\" "
               f"status --root \"{tk}\"")
    else:
        return
    _NOTICED.add(key)
    print(msg, file=sys.stderr)


def migrate_legacy_config(root: Path, apply: bool = False) -> tuple[str, str]:
    """Move a toolkit's 3.x `.claude/x4-paths.env` to `<root>/x4-paths.env`.

    A MOVE, never a copy (a copy leaves two files that drift apart on the first edit).
    A rename works on an x4lock-locked (read-only) file and the lock travels with it
    (MEASURED, Plan 3 lane I M6), so no unlock is needed. Dry unless *apply*.

    `(action, message)`, action one of:
      none              -- no 3.x file
      would-move/moved  -- only the 3.x file: it becomes the 4.x one, byte for byte
      would-retire-old/retired-old -- both, AGREEING: the 3.x one is renamed to
                           `.claude/x4-paths.env.bak-<stamp>` (gitignored)
      refused           -- both, DIFFERING: nothing changes; the message names both
                           paths and the differing KEY names, never a value
    """
    root = Path(root)
    new, old = root / CONFIG_NAME, root / LEGACY_CONFIG
    if not old.is_file():
        return "none", f"nothing to migrate: no 3.x config at {old}"
    if not new.exists():
        if not apply:
            return "would-move", f"would move {old} -> {new}"
        os.rename(old, new)                       # target checked absent: never overwrites
        return "moved", (f"[migrated] moved {old} -> {new} (the 3.x location; 4.0 reads the "
                         f"toolkit root). To undo (e.g. to go back to 3.x): move it back.")
    if not new.is_file():
        return "refused", f"REFUSING: {new} exists and is not a file; {old} left in place."
    keys = _differing_keys(new, old)
    if keys:
        return "refused", (f"REFUSING: {new} and the 3.x {old} both exist and DIFFER on: "
                           f"{', '.join(keys)}. Nothing has been changed. Keep the values you "
                           f"want in {new}, then delete or rename {old}.")
    import time
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = old.with_name(f"{CONFIG_NAME}.bak-{stamp}")
    n = 1
    while bak.exists():
        bak = old.with_name(f"{CONFIG_NAME}.bak-{stamp}-{n}")
        n += 1
    if not apply:
        return "would-retire-old", f"would rename the agreeing 3.x copy {old} -> {bak}"
    os.rename(old, bak)
    return "retired-old", (f"[migrated] the 3.x {old} agreed with {new}; renamed it to {bak}. "
                           f"To undo: rename it back.")


@lru_cache(maxsize=1)
def _file_layer() -> tuple[dict[str, str], Path | None]:
    """The parsed `x4-paths.env`. Cached because it costs a directory walk + read.

    Only the FILE is cached. The environment layer is rebuilt on every lookup, so
    a test (or a long-lived process) that mutates `os.environ` is picked up without
    having to remember `reload()` — a cache that silently ignores a freshly-set env
    var is precisely the kind of quiet misconfiguration this module exists to end.
    """
    toolkit_notice()
    env_file = _find_env_file()
    _notice(env_file)
    values, ignored = parse_env_report(env_file) if env_file else ({}, [])
    _IGNORED[:] = ignored
    _ignored_notice(env_file, ignored)
    return values, env_file


#: The `(line, reason)` pairs the config file last read had IGNORED (see `config_ignored`).
_IGNORED: list[tuple[int, str]] = []

#: What each ignore reason means, in the words the bash banner uses.
IGNORE_REASONS = {
    "shape": "not KEY=value",
    "key": "not an X4_* key",
    "subst": "$( ) or a backtick (shell code is never run)",
    "operator": "an unquoted ; & | < > (quote the value)",
    "guard": "X4_GUARD/X4_GUARD_CHECK (launch environment only)",
}


def config_ignored() -> list[tuple[int, str]]:
    """`(line number, reason)` for every line of the config file in use that was IGNORED.
    Line numbers and reasons only -- never a value: the file may hold X4_NEXUS_KEY."""
    _file_layer()
    return list(_IGNORED)


def describe_ignored(ignored) -> str:
    """`3:operator, 4:subst` -- the compact form the notice, --paths and x4doctor print."""
    return ", ".join(f"{n}:{why}" for n, why in ignored)


def _ignored_notice(env_file: Path | None, ignored) -> None:
    """ONE stderr line per process per file when the config has IGNORED lines (FX-B2, reviewer
    C I1): a line the grammar refuses -- an unquoted `X4_REFERENCE=<dir>/ref&x`, a
    `X4_MODS=$(...)` -- was dropped SILENTLY by every Python tool, which then fell back to a
    default (`<toolkit>/reference`), exit 0, nothing on stderr. Line:reason, never a value."""
    if env_file is None or not ignored:
        return
    key = "ignored:" + str(env_file)
    if key in _NOTICED:
        return
    _NOTICED.add(key)
    reasons = sorted({why for _n, why in ignored})
    print(f"x4 config: {env_file} -- these lines were IGNORED (line:reason): "
          f"{describe_ignored(ignored)}. "
          + "; ".join(f"{r} = {IGNORE_REASONS.get(r, r)}" for r in reasons)
          + ". Every path they would set falls back to its default. Fix them by hand; check "
            "with: x4validate --paths, or python scripts/x4doctor.py",
          file=sys.stderr)


def reload() -> None:
    """Forget the located/parsed config file. Needed only if that FILE changes."""
    _file_layer.cache_clear()


def _layers() -> list[dict[str, str]]:
    """Sources in descending priority: real env, config file, dev-machine fallback.

    Resolution walks these **layer by layer**, and within a layer tries every alias
    and derivation before dropping to the next. Flattening them into one dict is
    wrong: a `_LOCAL_FALLBACK` value for `X4_GAME` would then outrank a real
    `$X4_GAME_EXTENSIONS` the user actually exported.
    """
    file_layer, _ = _file_layer()
    if _CONFIG_ONLY:
        # What the acting toolkit's CONFIG says, the inherited environment ignored (see
        # `env_root_conflicts`). X4_TOOLKIT stays: it is the acting toolkit, not a config value.
        tk = toolkit_root()
        return [{"X4_TOOLKIT": str(tk)} if tk is not None else {}, file_layer, _LOCAL_FALLBACK]
    env = {k: v for k, v in os.environ.items()
           if (k.startswith("X4_") or k == "XRCATTOOL") and v}
    # B2: an exported X4_TOOLKIT naming ANOTHER toolkit must not answer by derivation either
    # (`<X4_TOOLKIT>/reference` was the second route to the other toolkit's tree).
    if env.get("X4_TOOLKIT") and toolkit_conflict() is not None:
        env["X4_TOOLKIT"] = str(toolkit_root())
    # An explicit --reference (`use_root`) sits above the environment, and only when given:
    # without one the order is the documented [env, file, fallback].
    explicit = [dict(_EXPLICIT_ROOTS)] if _EXPLICIT_ROOTS else []
    return [*explicit, env, file_layer, _LOCAL_FALLBACK]


# --- an INHERITED root vs the acting toolkit's config (FX-B2, delta review of F160) -----------
#
# THE INCIDENT, second route. F160 stopped an inherited $X4_TOOLKIT from choosing which config
# is read -- but the ENVIRONMENT layer still outranks that config, so a shell that had inherited
# X4_REFERENCE=<A>/reference made `x4refguard apply --toolkit B --yes` protect A's tree and
# `remove --toolkit B --yes` lift A's protection (exit 0, MEASURED), with no notice at all when
# X4_TOOLKIT itself was not inherited. A system-changing command therefore compares each root
# it acts on, resolved WITH the environment, against the same root resolved from the acting
# toolkit's config alone, and refuses on a difference unless the root was chosen explicitly
# (`use_root`, e.g. x4refguard --reference). A read-only command gets a notice instead.

#: Accessor -> the variables that can set it (every alias and every derivation source).
ROOT_KEYS: dict[str, tuple[str, ...]] = {
    "reference": ("X4_REFERENCE",),
    "game_root": ("X4_GAME", "X4_GAME_ROOT", "X4_EXTENSIONS", "X4_GAME_EXTENSIONS"),
    "registry": ("X4_REGISTRY", "X4_MODS"),
}
#: Roots chosen by an explicit command-line flag (`use_root`): the top layer, never a conflict.
_EXPLICIT_ROOTS: dict[str, str] = {}
#: Set only inside `_config_answer`: `_layers` then drops the inherited environment.
_CONFIG_ONLY = False


def use_root(key: str, value) -> None:
    """An explicit flag (e.g. `--reference DIR`) chose *key*: it outranks the environment and
    the config, and `env_root_conflicts` no longer reports the root it sets."""
    _EXPLICIT_ROOTS[key] = native(str(value))


def _config_answer(fn):
    """*fn*() as the acting toolkit's config alone answers it (no inherited X4_* variable)."""
    global _CONFIG_ONLY
    _CONFIG_ONLY = True
    try:
        return fn()
    finally:
        _CONFIG_ONLY = False


def env_root_conflicts(*names: str) -> list[tuple[str, tuple[str, ...], Path, Path]]:
    """`(accessor, the exported variables, answer WITH the environment, the config's answer)`
    for each accessor in *names* (keys of `ROOT_KEYS`) whose answer an EXPORTED variable
    changes away from what the acting toolkit's config file says.

    Only when a config file IS read: with none, the environment is the only configuration
    there is, and nothing disagrees with it. A config silent on the key still has an answer
    (`<toolkit>/reference`) and is compared. A root chosen by `use_root` is never reported."""
    if _file_layer()[1] is None:
        return []
    out = []
    for name in names:
        keys = ROOT_KEYS[name]
        if any(k in _EXPLICIT_ROOTS for k in keys):
            continue
        exported = tuple(k for k in keys if os.environ.get(k))
        if not exported:
            continue
        fn = globals()[name]
        eff, cfg = fn(), _config_answer(fn)
        if eff is None or cfg is None or _same_dir(eff, cfg):
            continue
        out.append((name, exported, eff, cfg))
    return out


# --- an INHERITED X4_CONFIG vs the acting toolkit (FX-B3, delta review reviewer F) ------------
#
# `env_root_conflicts` compares the environment against "the acting toolkit's config" -- but
# WHICH file that is was chosen by $X4_CONFIG (`_locate_config`, explicit branch), which the
# shell inherits like any other variable. With X4_CONFIG naming toolkit A's config,
# `x4refguard apply --toolkit B --yes` targeted A's reference, `remove` reported on A's tree
# (exit 0, no warning) and `x4lock status --toolkit B` listed A's x4-paths.env, never B's
# (MEASURED). An X4_CONFIG naming a MISSING file disabled the comparison outright (no config
# read -> nothing to disagree with), with only the missing-file notice.

def _inside(p: Path, root: Path) -> bool:
    try:
        rp = os.path.normcase(str(Path(p).resolve()))
        rr = os.path.normcase(str(Path(root).resolve()))
    except OSError:
        rp = os.path.normcase(os.path.abspath(str(p)))
        rr = os.path.normcase(os.path.abspath(str(root)))
    return rp == rr or rp.startswith(rr.rstrip(os.sep) + os.sep)


def config_conflict() -> tuple[Path, Path, bool] | None:
    """`(acting toolkit, the file $X4_CONFIG names, whether it exists)` when $X4_CONFIG is set
    AND names a missing file or one OUTSIDE the acting toolkit, else None.

    Only when the acting toolkit is EXPLICIT -- `--toolkit`, or the toolkit this code lives
    in. Code outside the toolkit layout acts for `$X4_TOOLKIT`, itself inherited, so there is
    no independent toolkit for X4_CONFIG to disagree with (and that case keeps its old,
    documented behaviour)."""
    explicit = os.environ.get("X4_CONFIG")
    if not explicit:
        return None
    acting = _EXPLICIT if _EXPLICIT is not None else _SELF
    if acting is None:
        return None
    p = config_spelling(explicit)
    exists = p.is_file() and not config_names_stream(explicit)
    if exists and _inside(p, acting):
        return None
    return acting, p, exists


def _config_conflict_text(action: str, flags: dict[str, str], names: tuple[str, ...],
                          lifted_by_flags: bool) -> str | None:
    c = config_conflict()
    if c is None:
        return None
    if lifted_by_flags and names and all(
            any(k in _EXPLICIT_ROOTS for k in ROOT_KEYS[n]) for n in names):
        return None                     # every root acted on was chosen by a flag
    acting, p, exists = c
    what = ("names a file that does not exist, so NO config is read and every root falls "
            "back to the environment or a default" if not exists else
            "names a config OUTSIDE this toolkit, so that config -- not this toolkit's -- "
            "decides what is acted on")
    lines = [f"REFUSED: `{action}` changes your system, and $X4_CONFIG ({p}) {what}. "
             f"Nothing was changed.",
             f"  acting toolkit: {acting}  (its config: {config_file_in(acting)})",
             f"  Unset X4_CONFIG in this shell (or point it at {config_file_in(acting)}) to "
             f"act on this toolkit's config."]
    chosen = [flags[n] for n in names if n in flags]
    if lifted_by_flags and chosen:
        lines.append(f"  Or choose the root explicitly: {' and '.join(chosen)} DIR.")
    return "\n".join(lines)


def config_notice() -> None:
    """ONE stderr line per process for a read-only command while $X4_CONFIG names a config
    outside the acting toolkit (or a missing file): what follows is THAT config's answer."""
    c = config_conflict()
    if c is None or "config-conflict" in _NOTICED:
        return
    _NOTICED.add("config-conflict")
    _NOTICED.add("explicit-missing:" + os.environ["X4_CONFIG"])   # this line says it: ONE line
    acting, p, exists = c
    state = "which does not exist" if not exists else "outside this toolkit"
    print(f"x4 config: acting for {acting}, but $X4_CONFIG names {p} ({state}); the answers "
          f"below follow $X4_CONFIG, not {config_file_in(acting)}. Commands that change your "
          f"system refuse until X4_CONFIG is unset.", file=sys.stderr)


def env_root_refusal(action: str, flags: dict[str, str], *names: str,
                     config_lifted_by_flags: bool = True) -> str | None:
    """The refusal for a SYSTEM-CHANGING *action* while an exported variable moves one of
    *names* away from the acting toolkit's config, else None. *flags* maps an accessor to the
    command-line flag that chooses it explicitly. Paths and variable NAMES, never a secret.

    FX-B3: first, an inherited $X4_CONFIG naming a config outside the acting toolkit (or a
    missing file) refuses -- unless every root in *names* was chosen by its flag and
    *config_lifted_by_flags* (False for a command that acts on more than those roots)."""
    text = _config_conflict_text(action, flags, names, config_lifted_by_flags)
    if text:
        return text
    found = env_root_conflicts(*names)
    if not found:
        return None
    lines = [f"REFUSED: `{action}` changes your system, and the environment and this toolkit's "
             f"config ({_file_layer()[1]}) name DIFFERENT roots. Nothing was changed."]
    for name, keys, eff, cfg in found:
        flag = flags.get(name, "")
        lines.append(f"  {name}: ${'/$'.join(keys)} in the environment -> {eff}")
        lines.append(f"  {name}: this toolkit's config -> {cfg}")
        if flag:
            lines.append(f"  Choose one explicitly: {flag} \"{cfg}\"  (the config's)  or  "
                         f"{flag} \"{eff}\"  (the environment's) -- or unset "
                         f"{' / '.join(keys)} in this shell.")
        else:
            lines.append(f"  Unset {' / '.join(keys)} in this shell (or make it match the "
                         f"config) to act on the config's root.")
    return "\n".join(lines)


def env_root_notice(*names: str) -> None:
    """ONE stderr line per root per process for a read-only command: the environment moves
    a root away from the config, and the answer below follows the environment."""
    config_notice()
    for name, keys, eff, cfg in env_root_conflicts(*names):
        key = "env-root:" + name
        if key in _NOTICED:
            continue
        _NOTICED.add(key)
        print(f"x4 config: {name} follows ${'/$'.join(keys)} from the environment ({eff}); "
              f"this toolkit's config says {cfg}. Commands that change your system refuse "
              f"until one is chosen.", file=sys.stderr)


#: WSL first — `/mnt/c/x` also matches the MSYS shape as drive "m" + "nt/c/x".
_RE_WSL = re.compile(r"^/mnt/([a-zA-Z])/(.*)$")
_RE_MSYS = re.compile(r"^/([a-zA-Z])/(.*)$")

#: The platform seam. Read this rather than `os.name` directly, so a test can
#: steer the behaviour without patching the SHARED `os` module — which `pathlib`
#: also dispatches its flavour on. MEASURED 2026-08-24 (ubuntu CI): tests doing
#: `monkeypatch.setattr(_paths.os, "name", "nt")` made the next `Path(...)` raise
#: `UnsupportedOperation: cannot instantiate 'WindowsPath' on your system`. One
#: failed; two passed by luck, depending on whether a `Path` was constructed
#: while the patch was live. Reaching around a module into a global is not
#: patching a seam, it is editing the interpreter.
_IS_WINDOWS = os.name == "nt"


def native(value: str) -> str:
    r"""Translate a POSIX drive path into one Python can actually open on Windows.

    `install.sh` detects Steam at `/c/Program Files (x86)/Steam` under Git Bash
    (install.sh's `steam_roots`), and `x4-paths.env.example` explicitly
    promises that either `C:\...` or `/c/...` is acceptable. The shell half honours
    that; Python does not — `Path("/c/Program Files")` becomes `\c\Program Files`,
    which does not exist. So the FIRST command the README gives a Windows user
    produced a config file the Python silently could not use: a successful install
    pointing at nothing, which is precisely the failure v2.01 exists to end.

    Only applied on Windows. On Linux `/c/...` and `/mnt/c/...` are legitimate
    absolute paths and must be left exactly as written.
    """
    if not _IS_WINDOWS:
        return value
    for rx in (_RE_WSL, _RE_MSYS):
        if m := rx.match(value):
            return f"{m.group(1).upper()}:/{m.group(2)}"
    return value


def _pick(layer: dict[str, str], *names: str) -> str | None:
    """First of *names* this layer defines, translated to a usable native path.

    The single choke point every alias and every derivation goes through, so the
    translation cannot be forgotten for one location — which is the shape of bug
    this module exists to prevent.
    """
    for n in names:
        if layer.get(n):
            return native(layer[n])
    return None


def _resolve(fn) -> Path | None:
    """First layer that can answer *fn* wins."""
    for layer in _layers():
        got = fn(layer)
        if got is not None:
            return got
    return None


class Unconfigured(RuntimeError):
    """A required location could not be resolved from any layer.

    Deliberately NOT used for every missing setting. It means *"you never told me
    where this is, and I refuse to guess"* — the reference tree being the case
    that produced it. A setting whose absence is recoverable must NOT raise this:
    `_nexus.nexus_key()` raises `NexusError` instead, because every caller catches
    it and degrades to local facts, and an optional key promoted to a hard refusal
    would break offline work.
    """


class Locked(PermissionError):
    """A protected file was not written because `scripts/x4lock.py` locked it.

    A sibling of `Unconfigured`: both mean *"this did not happen, on purpose"*, and
    both must reach the user as a decision rather than a traceback. It subclasses
    `PermissionError` so a caller that already handles one still works, and so the
    underlying OS error is not disguised as something else.
    """


def refuses_unconfigured(fn):
    """Wrap a CLI `main` so `Unconfigured` becomes **rc=2**, not a traceback.

    rc=2 is "this toolkit is not set up". It has to be distinguishable from rc=1,
    which several CLIs use for "the thing you asked about has findings" — a caller
    that cannot tell those apart is told to fix the wrong thing. Applied to every
    entry point in `pyproject.toml`, which `tests/test_unconfigured_refusal.py`
    asserts mechanically rather than trusting anyone to remember.

    IT ALSO ANNOUNCES A MUTATION WINDOW, and that second duty lives here for
    one reason: this is the only place already GUARANTEED to wrap every entry
    point. While `gates/mutation_probe.py` runs, the source tree is
    deliberately broken and every tool answers from it -- with `git status`
    looking normal, because the mutated file is TRACKED. Reads get a banner,
    never a refusal: a wrong answer can be re-taken, whereas breaking an
    unrelated session for the ~70s a probe takes cannot be undone. WRITES
    refuse instead, at the two stamping sites. See `_mutation`.
    """
    @functools.wraps(fn)
    def wrapper(argv=None, **seams):
        # `seams`: keyword-only injection points a CLI offers its tests (x4run); none for the rest.
        warning = _mutation.banner()
        if warning:
            print(warning, file=sys.stderr)
        try:
            return fn(argv, **seams)
        except Unconfigured as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Locked as exc:
            # Same reasoning as TreeMutating below: a guard that fires is a DECISION,
            # so it gets rc 2 ("cannot run") and a plain message. Left unhandled it
            # produced a raw traceback and rc 1 -- indistinguishable from "your data
            # has findings" -- which is the defect this wrapper already exists to
            # prevent, arriving a third time. Found by running it, not by reading it.
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except _mutation.TreeMutating as exc:
            # rc 2 = "cannot run", NOT rc 1 = "the thing you asked about has
            # findings". Caught here so a refusal reads as a decision rather
            # than a crash -- an unhandled raise gave a raw traceback and rc 1,
            # which is the same defect F39/F47 fixed elsewhere and which this
            # module's own docstring warns about. Found by running it, not by
            # reading it.
            print(f"error: {exc}", file=sys.stderr)
            return 2
    wrapper._refuses_unconfigured = True
    return wrapper


def _pick_raw(layer: dict[str, str], *names: str) -> str | None:
    """First of *names* this layer defines, EXACTLY as written.

    The counterpart to `_pick`, which path-translates what it returns. A setting
    that is not a path must not be rewritten: `native()` turns `/c/deadbeef` into
    `C:/deadbeef`, and silently corrupting a credential produces an
    authentication failure with nothing pointing back at this module.
    """
    for n in names:
        if layer.get(n):
            return layer[n]
    return None


def value(*names: str) -> str | None:
    """A NON-path setting, resolved through the same layers as every location.

    Use this instead of `os.environ.get`. The config file is a LAYER, and the
    documentation tells users they may put settings there — `setup.sh` says so
    for `X4_NEXUS_KEY` in as many words. Two consumers read the environment
    directly and therefore could not see a value in `.claude/x4-paths.env`,
    so following our own instructions produced "not set".
    """
    return _resolve(lambda layer: _pick_raw(layer, *names))


def path_value(*names: str) -> Path | None:
    """A PATH setting, resolved through the layers and translated for this OS.

    The path-shaped sibling of `value()`. Exists so that a caller needing a
    configurable location (`$X4_EFFECTIVE_DB`) uses the SAME door as everything
    else — `gates/_env.py` open-coded this resolution while `_effective` read
    `os.environ` at import time, and the two could disagree about which store
    was configured.
    """
    return _resolve(lambda layer: Path(v) if (v := _pick(layer, *names)) else None)


def game_root() -> Path | None:
    """The folder holding 01.cat..09.cat and extensions/."""
    def _in(layer):
        if v := _pick(layer, "X4_GAME", "X4_GAME_ROOT"):
            return Path(v)
        if ext := _pick(layer, "X4_EXTENSIONS", "X4_GAME_EXTENSIONS"):
            return Path(ext).parent
        return None
    return _resolve(_in)


def game_extensions() -> Path | None:
    def _in(layer):
        if v := _pick(layer, "X4_EXTENSIONS", "X4_GAME_EXTENSIONS"):
            return Path(v)
        if g := _pick(layer, "X4_GAME", "X4_GAME_ROOT"):
            return Path(g) / "extensions"
        return None
    return _resolve(_in)


def reference() -> Path | None:
    """The unpacked base+DLC tree.

    X4_TOOLKIT IS A LAST-RESORT SOURCE HERE, asked only after every layer the
    user controls has been asked for an explicit X4_REFERENCE.

    Layer order alone was not enough. `install.sh` writes X4_REFERENCE into
    .claude/x4-paths.env and then tells every Windows user to
    `setx X4_TOOLKIT "$TOOLKIT"` -- so the ENV layer answers by DERIVATION while
    the FILE layer answers EXPLICITLY, and the derived answer won. MEASURED
    end-to-end: a toolkit whose config named a real reference tree validated
    against `<toolkit>/reference` instead and reported "reference tree not
    found -- unpack the base game first", while `--paths` called the config file
    found and in use. That is the installer's own documented setup, not a
    corner case.

    SCOPED DELIBERATELY TO THIS ONE DERIVATION. The same shadowing shape exists
    for all 9 accessors that derive (MEASURED: 9 of 9), but the general rule
    "explicit beats derived" also demotes an ENV-exported X4_GAME_EXTENSIONS
    below a config-file X4_GAME -- inverting the documented layer priority for a
    knob `test_game_root_follows_the_documented_extensions_env_var` exists to
    pin. X4_TOOLKIT is different in kind: it names where the TOOLKIT lives, so
    `<toolkit>/reference` is a convenience default, not a statement about where
    the reference tree is. X4_GAME -> extensions is a first-class relationship
    and keeps its precedence. Widening this is a decision, not a bug fix.

    The FALLBACK layer is excluded from the explicit pass, for the reason
    `_layers` gives: a dev-machine default must never outrank something the
    user really exported.
    """
    *real_layers, _fallback = _layers()
    for layer in real_layers:
        if v := _pick(layer, "X4_REFERENCE"):
            return Path(v)

    # Nothing explicit anywhere the user controls: previous behaviour, unchanged.
    def _in(layer):
        if v := _pick(layer, "X4_REFERENCE"):
            return Path(v)
        if t := _pick(layer, "X4_TOOLKIT"):
            return Path(t) / "reference"
        return None
    return _resolve(_in)


def profile() -> Path | None:
    return _resolve(lambda layer: Path(v) if (v := _pick(layer, "X4_PROFILE")) else None)


def profile_content() -> Path | None:
    def _in(layer):
        if v := _pick(layer, "X4_PROFILE_CONTENT"):
            return Path(v)
        if p := _pick(layer, "X4_PROFILE"):
            return Path(p) / "content.xml"
        return None
    return _resolve(_in)


def profile_extensions() -> Path | None:
    def _in(layer):
        if v := _pick(layer, "X4_PROFILE_EXTENSIONS"):
            return Path(v)
        if p := _pick(layer, "X4_PROFILE"):
            return Path(p) / "extensions"
        return None
    return _resolve(_in)


def workshop_content() -> Path | None:
    """`steamapps/workshop/content/392160`, derived from the game dir when unset.

    A default Steam layout puts the game at `steamapps/common/<name>`, so the
    workshop tree is two levels up. Only offered when that shape actually holds —
    guessing on a non-Steam or relocated install would invent a path that silently
    scans nothing and reports "no mods here".
    """
    def _in(layer):
        if v := _pick(layer, "X4_WORKSHOP_CONTENT"):
            return Path(v)
        g = _pick(layer, "X4_GAME", "X4_GAME_ROOT")
        gp = Path(g) if g else None
        if gp is None and (ext := _pick(layer, "X4_EXTENSIONS", "X4_GAME_EXTENSIONS")):
            gp = Path(ext).parent
        if gp is not None and gp.parent.name.lower() == "common":
            return gp.parent.parent / "workshop" / "content" / STEAM_APPID
        return None
    return _resolve(_in)


def mods() -> Path | None:
    """Where the user's mod source folders live (one per mod) — `$X4_MODS`.

    `registry()` already derived from this, but had no direct accessor, so the
    `gates/` harnesses hardcoded a developer's own `dev\\` path instead. That is
    how a personal path survives into a public repo.
    """
    return _resolve(lambda layer: Path(v) if (v := _pick(layer, "X4_MODS")) else None)


def registry() -> Path | None:
    def _in(layer):
        if v := _pick(layer, "X4_REGISTRY"):
            return Path(v)
        if m := _pick(layer, "X4_MODS"):
            return Path(m) / "_registry" / "modlist.yaml"
        return None
    return _resolve(_in)


def debug_log() -> Path | None:
    def _in(layer):
        if v := _pick(layer, "X4_DEBUGLOG"):
            return Path(v)
        if p := _pick(layer, "X4_PROFILE"):
            return Path(p) / "debug.txt"
        return None
    return _resolve(_in)

def savegames() -> Path | None:
    """The savegame directory, or None.

    A save is an artifact the engine WROTE, so it answers questions no manifest
    can: which extensions are baked into it (`save` absent or `="1"` — CLAUDE.md
    #33), and what content it references that the live tree no longer defines.

    Same two-layer shape as `debug_log`: an explicit override, else derived from
    the profile. Returns None rather than guessing — a save reader pointed at the
    wrong directory reports "no saves" and that is indistinguishable from a clean
    result unless the caller refuses instead.
    """
    def _in(layer):
        if v := _pick(layer, "X4_SAVES"):
            return Path(v)
        if p := _pick(layer, "X4_PROFILE"):
            return Path(p) / "save"
        return None
    return _resolve(_in)


def describe() -> list[str]:
    """Human-readable resolution report, for `--paths` and for bug reports.

    Silent misconfiguration is the whole failure mode here, so there has to be a
    way to ask the tool where it thinks everything is.
    """
    file_layer, env_file = _file_layer()
    label = ""
    if env_file is not None:
        if os.environ.get("X4_CONFIG"):
            label = "   (explicit $X4_CONFIG)"
        elif env_file.parent.name == ".claude":
            label = "   (3.x location, DEPRECATED -- scripts/x4config.py migrate --apply)"
    elif os.environ.get("X4_CONFIG"):
        label = f"   ($X4_CONFIG names {os.environ['X4_CONFIG']}, which does not exist)"
    lines = [f"config file: {env_file or '(none found — set $X4_TOOLKIT or run install)'}{label}"]
    ignored = config_ignored()
    if ignored:
        reasons = sorted({why for _n, why in ignored})
        lines.append(f"  IGNORED config lines (line:reason): {describe_ignored(ignored)}   ("
                     + "; ".join(f"{r} = {IGNORE_REASONS.get(r, r)}" for r in reasons)
                     + " -- what they would set falls back to a default)")
    env = {k: v for k, v in os.environ.items() if k.startswith("X4_") and v}
    ref_defaulted = not any(layer.get("X4_REFERENCE")
                            for layer in (env, file_layer, _LOCAL_FALLBACK))
    for name, fn in (("game", game_root), ("extensions", game_extensions),
                     ("reference", reference), ("profile", profile),
                     ("profile content.xml", profile_content),
                     ("profile extensions", profile_extensions),
                     ("workshop", workshop_content), ("mods", mods),
                     ("registry", registry), ("debug log", debug_log),
                     ("savegames", savegames)):
        p = fn()
        mark = "" if p is None else ("" if p.exists() else "   (does not exist)")
        if name == "reference" and p is not None and ref_defaulted:
            mark += "   (DEFAULT: <toolkit>/reference -- no X4_REFERENCE configured)"
        lines.append(f"  {name:<20} {p or '(unresolved)'}{mark}")
    return lines

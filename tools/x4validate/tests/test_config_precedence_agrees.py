"""The two halves of the toolkit must agree on which config source wins.

There were THREE statements of precedence and one disagreed:

    CLAUDE.md          "in this order: **env var > x4-paths.env > default**"
    _paths._layers()   [env, file_layer, _LOCAL_FALLBACK]        -- env wins
    _x4-env.sh         `set -a; . "$cfg"; set +a`                -- the FILE won

Two of three agreed, and the third is the one every hook runs on every tool call.

Why an inconsistency here is a safety problem rather than an untidiness: both halves
resolve paths for the SAME machine. Exporting `X4_GAME` pointed x4validate at one
install while the guards protecting the game folder read another -- so the protection
and the work could be aimed at different trees, with nothing saying so.

Nothing pinned the two together, which is exactly how they drifted. This file is that
pin, and it checks all three statements rather than two: a test that compared only the
two implementations would have stayed green while both drifted away from the promise
made to the user.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
from _layout import common_hook_dir, runtime_instructions
ENV_SH = common_hook_dir(ROOT) / "_x4-env.sh"
CLAUDE_MD = ROOT / "CLAUDE.md"


def _bash() -> str | None:
    """Git Bash, via the repo's own resolver.

    NOT `shutil.which("bash")`. On any Windows machine with WSL enabled -- which
    includes every Docker Desktop install -- that returns the stub at
    `C:\\Windows\\System32\\bash.exe`, and the first draft of this test failed with
      WSL (9 - Relay) ERROR: CreateProcessCommon:640: execvpe(/bin/bash) failed
    which is a broken test, not a broken precedence. `scripts/gitbash.py` exists for
    exactly this and has its own suite.
    """
    src = ROOT / "scripts" / "gitbash.py"
    if not src.is_file():
        return None
    spec = importlib.util.spec_from_file_location("gitbash_for_precedence", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_bash()


def test_the_python_half_puts_the_environment_FIRST():
    from x4validate import _paths

    layers = _paths._layers()
    assert len(layers) >= 2, layers
    # The env layer is built from os.environ, so a variable set here must appear in
    # layer 0 and nowhere earlier. Asserting the ORDER, not just membership: the bug
    # was a precedence inversion, which membership cannot see.
    key = "X4_PRECEDENCE_PROBE"
    os.environ[key] = "from-the-env"
    try:
        layers = _paths._layers()
        assert layers[0].get(key) == "from-the-env", (
            "the first layer is not the real environment")
    finally:
        os.environ.pop(key, None)


def test_the_documented_order_still_says_the_environment_wins():
    """If someone changes the promise, this test is where they find out that two
    implementations are pinned to it."""
    # Plan 2: AGENTS.md carries the same shared core, so it makes the same promise.
    docs = runtime_instructions(ROOT)
    for doc in docs:
        text = doc.read_text(encoding="utf-8", errors="replace")
        assert "env var > `x4-paths.env` > default" in text, (
            f"{doc.name} no longer documents env > file > default; the two implementations "
            "below are pinned to that order")


def test_the_instructions_name_the_ROOT_config_and_not_the_3x_one():
    """Plan 3 lane I: every agent's instructions name `x4-paths.env` at the toolkit root."""
    docs = runtime_instructions(ROOT)
    for doc in docs:
        text = doc.read_text(encoding="utf-8", errors="replace")
        assert "x4-paths.env" in text, doc.name
        assert ".claude/x4-paths.env" not in text and ".claude" + chr(92) + "x4-paths.env" not in text, doc.name


def test_the_bash_half_lets_the_environment_win():
    """Runs the real `_x4-env.sh`, because the defect was in what it DID, not in what
    it said -- its own header described the inverted behaviour accurately."""
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash found (the WSL stub does not count) -- NOT CHECKED")
    with tempfile.TemporaryDirectory() as td:
        tk = pathlib.Path(td)
        (tk / ".claude").mkdir()
        (tk / ".claude" / "x4-paths.env").write_text(
            'X4_GAME="/from/the/FILE"\nX4_PROFILE="/profile/from/FILE"\n',
            encoding="utf-8", newline="\n")
        # HERMETIC (FX-G2 item 9): the caller's X4_* -- above all X4_CONFIG, which names the
        # file to read and so bypasses the one written here -- must not leak in. MEASURED: with
        # X4_CONFIG set in the environment this test FAILED for a reason unrelated to its claim.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "XRCATTOOL")}
        env["X4_TOOLKIT"] = str(tk)
        env["X4_GAME"] = "/from/the/ENV"
        r = subprocess.run(
            [bash, "-c",
             '. "$1"; printf "%s\\n%s\\n" "$X4_GAME" "$X4_PROFILE"',
             "_", str(ENV_SH)],
            capture_output=True, text=True, env=env)
        assert r.returncode == 0, (r.returncode, r.stderr[-400:])
        game, profile = (r.stdout.splitlines() + ["", ""])[:2]

    assert game == "/from/the/ENV", (
        f"the FILE overrode an exported X4_GAME (got {game!r}); "
        "that is the precedence inversion this file exists to catch")
    # ...and the file must still supply what the environment does not. A fix that
    # simply stopped reading the file would pass the assertion above.
    assert profile == "/profile/from/FILE", (
        f"the file no longer supplies an unset key (got {profile!r})")


# --- Plan 3 lane I: the location MATRIX, both loaders, one table ------------------------
#
# Each row is the falsification twin of ONE clause of the selection rule:
#   new_only -> "new is read"            old_only   -> "legacy still read"
#   both_*   -> "new outranks legacy"    both_differ -> the ORDER (a swap reads /xgame/old)
#   neither  -> "nothing is invented"    explicit*  -> "$X4_CONFIG decides, even when absent"
#   env_wins -> "an exported value beats every file"
import json
import sys

NEW, OLD = "x4-paths.env", ".claude/x4-paths.env"
MATRIX = [
    # id,                files {rel: X4_GAME},                         X4_CONFIG,      exported X4_GAME, file read,       state,              X4_GAME
    ("new_only",         {NEW: "/xgame/new"},                          None,           None,     NEW,             "new",              "/xgame/new"),
    ("old_only",         {OLD: "/xgame/old"},                          None,           None,     OLD,             "legacy",           "/xgame/old"),
    ("both_agree",       {NEW: "/xgame/same", OLD: "/xgame/same"},     None,           None,     NEW,             "both",             "/xgame/same"),
    ("both_differ",      {NEW: "/xgame/new", OLD: "/xgame/old"},       None,           None,     NEW,             "both",             "/xgame/new"),
    ("neither",          {},                                           None,           None,     None,            "none",             ""),
    ("explicit",         {NEW: "/xgame/new", "elsewhere.env": "/xgame/x"}, "elsewhere.env", None, "elsewhere.env", "explicit",        "/xgame/x"),
    ("explicit_missing", {NEW: "/xgame/new"},                          "absent.env",   None,     None,            "explicit-missing", ""),
    ("env_wins",         {NEW: "/xgame/new"},                          None,           "/xgame/env", NEW,         "new",              "/xgame/env"),
]
IDS = [r[0] for r in MATRIX]
PKG = ROOT / "tools" / "x4validate"


def _n(v):
    return "" if v in (None, "") else str(v).replace("\\", "/").rstrip("/").lower()


def _box(tmp: pathlib.Path, row):
    _id, files, xcfg, xgame, *_ = row
    tk = tmp / "tk"
    tk.mkdir()
    for rel, game in files.items():
        p = tk / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'X4_GAME="{game}"\n', encoding="utf-8", newline="\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR")}
    env["X4_TOOLKIT"] = str(tk)
    if xcfg:
        env["X4_CONFIG"] = str(tk / xcfg)
    if xgame:
        env["X4_GAME"] = xgame
    return tk, env


def _bash_view(tk, env):
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash found (the WSL stub does not count) -- NOT CHECKED")
    r = subprocess.run([bash, "-c", '. "$1"; printf "%s\\n" "$_x4_cfg" "$_x4_cfg_src" '
                        '"${X4_GAME:-}" "$X4_REFERENCE" "$_x4_ref_defaulted"', "_", str(ENV_SH)],
                       capture_output=True, text=True, env=env, cwd=str(tk))
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stderr == "", "the loader must print NOTHING per call (M5): " + r.stderr[-300:]
    f, s, g, ref, d = (r.stdout.splitlines() + [""] * 5)[:5]
    return {"file": f, "state": s, "game": g, "reference": ref, "defaulted": d}


_PY = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from x4validate import _paths
from pathlib import Path
if len(sys.argv) > 2:   # where the tools LIVE (B2): "" = outside the toolkit layout
    _paths._SELF = Path(sys.argv[2]) if sys.argv[2] else None
f = _paths._find_env_file(); g = _paths.game_root()
print(json.dumps({"file": None if f is None else str(f), "state": _paths.config_state(),
                  "game": "" if g is None else g.as_posix(), "reference": str(_paths.reference())}))
'''


def _py_view(tk, env):
    # The installed shape: the tools live in the toolkit X4_TOOLKIT names (B2 -- a tool acts
    # for the toolkit it lives in; a FOREIGN X4_TOOLKIT is test_toolkit_binding.py's subject).
    r = subprocess.run([sys.executable, "-c", _PY, str(PKG), str(tk)], capture_output=True,
                       text=True, env=env, cwd=str(tk))
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _expect(tk, row, view, collapse=False):
    _id, _f, _x, _g, want_file, want_state, want_game = row
    state = view["state"]
    if collapse and state in ("both-agree", "both-differ"):
        state = "both"
    assert _n(view["file"]) == _n(tk / want_file if want_file else None), (_id, view)
    assert state == want_state, (_id, view)
    assert _n(view["game"]) == _n(want_game), (_id, view)


@pytest.mark.parametrize("row", MATRIX, ids=IDS)
def test_bash_loader_matrix(tmp_path, row):
    tk, env = _box(tmp_path, row)
    v = _bash_view(tk, env)
    _expect(tk, row, v)
    # the reference default is RECORDED, never silent: defaulted only where no file gave one
    assert v["defaulted"] == "1" and _n(v["reference"]) == _n(tk / "reference"), (row[0], v)


@pytest.mark.parametrize("row", MATRIX, ids=IDS)
def test_python_loader_matrix(tmp_path, row):
    tk, env = _box(tmp_path, row)
    _expect(tk, row, _py_view(tk, env), collapse=True)


@pytest.mark.parametrize("row", MATRIX, ids=IDS)
def test_config_precedence_agrees(tmp_path, row):
    """Bash and Python, same box, same env: same file, same state, same game, same reference."""
    tk, env = _box(tmp_path, row)
    b, p = _bash_view(tk, env), _py_view(tk, env)
    ps = "both" if p["state"] in ("both-agree", "both-differ") else p["state"]
    assert (_n(b["file"]), b["state"], _n(b["game"]), _n(b["reference"])) == \
           (_n(p["file"]), ps, _n(p["game"]), _n(p["reference"])), (row[0], b, p)


def test_TWIN_an_exported_reference_is_not_DEFAULTED(tmp_path):
    """The `_x4_ref_defaulted` flag is what the banner and x4doctor read: it must go to 0
    the moment anything real names the reference."""
    tk, env = _box(tmp_path, MATRIX[4])                   # neither
    env["X4_REFERENCE"] = str(tmp_path / "realref")
    v = _bash_view(tk, env)
    assert v["defaulted"] == "0" and _n(v["reference"]) == _n(tmp_path / "realref")


def test_a_CRLF_config_gives_the_bash_loader_NO_carriage_return(tmp_path):
    """A config saved by any Windows editor is CRLF, and `. file` under a POSIX bash KEEPS the
    CR: MEASURED 2026-10-04 in ubuntu:24.04, `X4_REFERENCE="/a/b"<CR>` sourced to `/a/b<CR>`
    (Git Bash strips it, so this machine never saw it). Every guard then compares paths against
    a root that ends in a CR, which no real path does. CI run 37172347642 surfaced it only
    because the OpenCode renderer refused the value ("X4_REFERENCE contains a line break").
    Bash and Python must read the same CRLF file to the same values, with no CR in either."""
    tk, env = _box(tmp_path, MATRIX[4])                   # neither: the file is written below
    keys = {"X4_GAME": "/g/game", "X4_REFERENCE": "/g/ref", "X4_PROFILE": "/g/profile",
            "X4_MODS": "/g/mods"}
    body = "# a comment\n" + "".join(f'{k}="{v}"\n' for k, v in keys.items()) + "X4_SAVES=/g/saves\n"
    (tk / NEW).write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash found (the WSL stub does not count) -- NOT CHECKED")
    names = [*keys, "X4_SAVES"]
    r = subprocess.run([bash, "-c", '. "$1"; shift; for k in "$@"; do eval "v=\\${$k}"; printf "%s\\0" "$v"; done',
                        "_", str(ENV_SH), *names], capture_output=True, env=env, cwd=str(tk))
    assert r.returncode == 0, r.stderr[-400:]
    got = dict(zip(names, r.stdout.decode("utf-8").split("\0")))
    assert got == {**keys, "X4_SAVES": "/g/saves"}, got


# --- R5-7 (v4.0.0 review): X4_TOOLKIT UNSET -- two anchors, documented and pinned ----------
#
# With X4_TOOLKIT unset a guard anchors on where its own copy lives (CLAUDE_PROJECT_DIR, else
# <hooks>/../..). Since B2 (install red-team 2026-10-04) a Python tool anchors on where IT
# lives -- the toolkit holding tools/x4validate -- so in an installed toolkit the two agree
# from ANY working directory, which closed R5-7's divergence for that shape. The CWD walk is
# left only for code outside the toolkit layout (`_SELF` None), pinned below.

def _unset_box(tmp_path):
    tk = tmp_path / "tk"
    (tk / "sub" / "deeper").mkdir(parents=True)
    (tk / NEW).write_text('X4_GAME="/xgame/project"\n', encoding="utf-8", newline="\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR")}
    return tk, env


def _bash_in(tk, env, cwd):
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash found (the WSL stub does not count) -- NOT CHECKED")
    r = subprocess.run([bash, "-c", '. "$1"; printf "%s\n" "$_x4_cfg" "${X4_GAME:-}"', "_", str(ENV_SH)],
                       capture_output=True, text=True, env=dict(env, CLAUDE_PROJECT_DIR=str(tk)),
                       cwd=str(cwd))
    assert r.returncode == 0, r.stderr[-400:]
    f, g = (r.stdout.splitlines() + ["", ""])[:2]
    return f, g


def _py_in(env, cwd, self_root=""):
    r = subprocess.run([sys.executable, "-c", _PY, str(PKG), str(self_root)], capture_output=True,
                       text=True, env=env, cwd=str(cwd))
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_UNSET_toolkit_a_tool_run_IN_the_project_agrees_with_the_guards(tmp_path):
    tk, env = _unset_box(tmp_path)
    bf, bg = _bash_in(tk, env, tk)
    p = _py_in(env, tk / "sub" / "deeper")          # outside the layout: the walk climbs
    assert _n(bf) == _n(p["file"]) == _n(tk / NEW), (bf, p)
    assert _n(bg) == _n(p["game"]) == _n("/xgame/project"), (bg, p)


def test_UNSET_toolkit_an_INSTALLED_tool_run_ELSEWHERE_still_agrees_B2(tmp_path):
    """B2: a tool living in the toolkit reads THAT toolkit's config from any working
    directory -- the guard and the tool agree even run from elsewhere."""
    tk, env = _unset_box(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bf, _ = _bash_in(tk, env, outside)
    p = _py_in(env, outside, self_root=tk)
    assert _n(bf) == _n(p["file"]) == _n(tk / NEW), (bf, p)


def test_UNSET_toolkit_a_tool_run_ELSEWHERE_reads_what_its_walk_finds_DOCUMENTED(tmp_path):
    """Code OUTSIDE the toolkit layout (no own toolkit) keeps the documented divergence: the
    guard still reads its project's config; the tool, run outside the project, does not see
    it. Set X4_TOOLKIT (the installers do) to remove it."""
    tk, env = _unset_box(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bf, _ = _bash_in(tk, env, outside)
    p = _py_in(env, outside)
    assert _n(bf) == _n(tk / NEW)
    assert p["file"] is None and p["state"] == "none", p
    assert "walk" in (PKG / "x4validate" / "_paths.py").read_text(encoding="utf-8").split('"""')[1]
# --- v4.0 release review R1-F1 / R1-P1: the config is PARSED, by ONE grammar, in BOTH loaders --
#
# The bash loader SOURCED the file (`set -a; . "$cfg"`), so the config was shell CODE to the
# guards and DATA to Python -- and the agent can write it: a line `exit 0` ended every hook
# before it spoke (silence is ALLOW), `X4_GUARD=off` in it relaxed every deny. Each row below
# is one clause of the grammar both loaders now share (`_x4_cfg_read` / `parse_env_report`).
# A row lists the file's lines, the values it must configure (absent = must NOT be set), and
# the ignored (line, reason) pairs. FXSBASE is exported as "/base" for every row.
BS = chr(92)
PARSE_KEYS = ("X4_GAME", "X4_MODS", "X4_PROFILE", "X4_DEBUGLOG", "X4_SAVES", "XRCATTOOL",
              "X4_GUARD", "X4_GUARD_CHECK", "X4_APPMANIFEST", "X4_BACKUPS")
PARSER = [
    # id, lines, want values, want ignored
    ("double_quotes",   ['X4_GAME="/a b/c"'],                          {"X4_GAME": "/a b/c"}, []),
    ("single_quotes_literal", ["X4_GAME='/a $FXSBASE/c'"],             {"X4_GAME": "/a $FXSBASE/c"}, []),
    ("export",          ['export X4_GAME="/e"'],                       {"X4_GAME": "/e"}, []),
    ("blanks_around_eq", ['X4_GAME = "/sp"'],                          {"X4_GAME": "/sp"}, []),
    ("comments",        ["# X4_GAME=/no", "X4_GAME=/m # note", "  # indented"], {"X4_GAME": "/m"}, []),
    ("hash_mid_word",   ["X4_GAME=/a#b"],                              {"X4_GAME": "/a#b"}, []),
    ("quoted_hash",     ['X4_GAME="/My Mods #2/x" # c'],               {"X4_GAME": "/My Mods #2/x"}, []),
    ("concatenation",   ["X4_GAME='/x'\"/y\"z"],                       {"X4_GAME": "/x/yz"}, []),
    ("expand_env",      ['X4_GAME="$FXSBASE/g"', "X4_MODS=${FXSBASE}/m"],
                        {"X4_GAME": "/base/g", "X4_MODS": "/base/m"}, []),
    ("expand_file_key", ['X4_GAME="/g"', 'X4_MODS="$X4_GAME/m"'],      {"X4_GAME": "/g", "X4_MODS": "/g/m"}, []),
    ("unknown_name_empty", ['X4_GAME="$FXS_NOT_SET/g"'],               {"X4_GAME": "/g"}, []),
    ("escaped_dollar_literal", ['X4_GAME="/a' + BS + '$FXSBASE"'],     {"X4_GAME": "/a$FXSBASE"}, []),
    ("braced_default_literal", ['X4_GAME="${FXSBASE:-/x}"'],           {"X4_GAME": "${FXSBASE:-/x}"}, []),
    # what both installers write: \\ \" \$ \` escaped inside double quotes
    ("installer_escapes", ['X4_GAME="C:' + BS * 2 + "Users" + BS * 2 + "me " + BS + '"q' + BS
                           + '" ' + BS + "$x " + BS + "`b" + BS + '`"'],
                        {"X4_GAME": "C:" + BS + "Users" + BS + 'me "q" $x `b`'}, []),
    ("unquoted_backslashes_kept", ["X4_GAME=C:" + BS + "Games" + BS + "X4"],
                        {"X4_GAME": "C:" + BS + "Games" + BS + "X4"}, []),
    # a hand-typed trailing separator: the quote is escaped, so it is UNTERMINATED -> legacy
    ("unterminated_legacy", ['X4_GAME="C:' + BS + "Games" + BS + 'X4' + BS + '"'],
                        {"X4_GAME": "C:" + BS + "Games" + BS + "X4" + BS}, []),
    ("empty_configures_nothing", ['X4_GAME=""'],                       {}, []),
    ("crlf",            ["X4_GAME=\"/c\"\r", "X4_MODS=/m\r"],          {"X4_GAME": "/c", "X4_MODS": "/m"}, []),
    ("bom",             ["﻿X4_GAME=/bom"],                        {"X4_GAME": "/bom"}, []),
    # --- shell CODE: ignored, reported, and NEVER run ------------------------------------
    ("exit_0_line",     ["exit 0", "X4_GAME=/after"],                  {"X4_GAME": "/after"}, [(1, "shape")]),
    ("command_line",    ["touch MARK", "source /x"],                   {}, [(1, "shape"), (2, "shape")]),
    ("subst_dollar",    ['X4_GAME="$(touch MARK)/x"', "X4_MODS=/m"],   {"X4_MODS": "/m"}, [(1, "subst")]),
    ("subst_backtick",  ["X4_GAME=`touch MARK`"],                      {}, [(1, "subst")]),
    ("subst_unterminated", ['X4_GAME="$(touch MARK)'],                 {}, [(1, "subst")]),
    ("operator",        ["X4_GAME=/a; touch MARK", "X4_MODS=/a&b"],    {}, [(1, "operator"), (2, "operator")]),
    ("single_quoted_code_is_data", ["X4_GAME='$(touch MARK); x'"],     {"X4_GAME": "$(touch MARK); x"}, []),
    ("not_our_key",     ["PATH=/evil", "JQ=/evil", "X4_lower=/x"],     {}, [(1, "key"), (2, "key"), (3, "key")]),
    ("guard_keys",      ["X4_GUARD=off", "X4_GUARD_CHECK=1", "X4_GAME=/g"],
                        {"X4_GAME": "/g"}, [(1, "guard"), (2, "guard")]),
    ("xrcattool_key",   ['XRCATTOOL="/x/XRCatTool.exe"'],              {"XRCATTOOL": "/x/XRCatTool.exe"}, []),
    ("any_x4_key",      ["X4_APPMANIFEST=/a.acf"],                     {"X4_APPMANIFEST": "/a.acf"}, []),
]
PARSER_IDS = [r[0] for r in PARSER]


def _parse_box(tmp: pathlib.Path, lines):
    tk = tmp / "tk"
    tk.mkdir()
    (tk / NEW).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR")}
    env["X4_TOOLKIT"] = str(tk)
    env["FXSBASE"] = "/base"
    env.pop("FXS_NOT_SET", None)
    return tk, env


def _bash_parse(tk, env):
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash found (the WSL stub does not count) -- NOT CHECKED")
    # NUL-separated: VALUE, then whether it is EXPORTED (a child process must see it), per key;
    # then the ignored list and the PATH check last.
    script = ('. "$1"; shift; for k in "$@"; do eval "v=\\${$k-}"; printf "%s\\0" "$v"; '
              'case "$(declare -p "$k" 2>/dev/null)" in "declare -x"*) printf "x\\0";; *) printf "%s\\0" -;; esac; done; '
              'printf "%s\\0" "$_x4_cfg_ignored"; case "$PATH" in /evil*) printf "HIJACKED";; *) printf "ok";; esac')
    r = subprocess.run([bash, "-c", script, "_", str(ENV_SH), *PARSE_KEYS],
                       capture_output=True, env=env, cwd=str(tk))
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stderr == b"", "the loader must print NOTHING per call (M5): " + r.stderr[-300:].decode()
    parts = r.stdout.decode("utf-8").split("\0")
    vals, exported = {}, {}
    for i, k in enumerate(PARSE_KEYS):
        v, x = parts[2 * i], parts[2 * i + 1]
        if v:
            vals[k] = v
            exported[k] = x == "x"
    ignored = [(int(a), b) for a, b in (t.split(":") for t in parts[2 * len(PARSE_KEYS)].split())]
    return vals, exported, ignored, parts[-1]


def _py_parse(tk, env):
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from x4validate import _paths; "
            "v, ig = _paths.parse_env_report(_paths.Path(sys.argv[2])); "
            "print(json.dumps({'v': v, 'ig': ig}))")
    r = subprocess.run([sys.executable, "-c", code, str(PKG), str(tk / NEW)],
                       capture_output=True, text=True, env=env, cwd=str(tk))
    assert r.returncode == 0, r.stderr[-400:]
    d = json.loads(r.stdout.strip().splitlines()[-1])
    return d["v"], [tuple(x) for x in d["ig"]]


@pytest.mark.parametrize("row", PARSER, ids=PARSER_IDS)
def test_both_loaders_parse_the_config_by_one_grammar(tmp_path, row):
    _id, lines, want, want_ignored = row
    tk, env = _parse_box(tmp_path, lines)
    bvals, bexp, bign, path_check = _bash_parse(tk, env)
    pvals, pign = _py_parse(tk, env)
    pvals = {k: v for k, v in pvals.items() if k in PARSE_KEYS}
    assert bvals == want, ("bash", _id, bvals)
    assert pvals == want, ("python", _id, pvals)
    assert bign == want_ignored, ("bash ignored", _id, bign)
    assert pign == want_ignored, ("python ignored", _id, pign)
    assert all(bexp.values()), ("a configured value must be EXPORTED, as `set -a` did", _id, bexp)
    assert path_check == "ok", "a config line changed the hook's PATH"
    assert not (tk / "MARK").exists(), f"{_id}: a config line was EXECUTED"


def test_an_exported_value_still_wins_for_a_key_the_old_loader_did_not_protect(tmp_path):
    """The sourcing loader restored only twelve named keys after `. "$cfg"`; any other X4_*
    key (X4_BACKUPS, X4_PYTHON ...) was won by the FILE, while Python's env layer won for all.
    One rule now: the environment wins for every key, in both loaders."""
    tk, env = _parse_box(tmp_path, ['X4_BACKUPS="/from/file"'])
    env["X4_BACKUPS"] = "/from/env"
    bvals, *_ = _bash_parse(tk, env)
    assert bvals.get("X4_BACKUPS") == "/from/env", bvals


def test_TWIN_a_config_X4_GUARD_never_reaches_the_guards_but_the_launch_one_does(tmp_path):
    """R1-F1, both halves: the file's X4_GUARD=off is ignored; the LAUNCH environment's is
    honoured. Without the second assertion, a loader that dropped X4_GUARD everywhere passes."""
    tk, env = _parse_box(tmp_path, ["X4_GUARD=off"])
    bvals, _x, bign, _p = _bash_parse(tk, env)
    assert "X4_GUARD" not in bvals and bign == [(1, "guard")], (bvals, bign)
    env["X4_GUARD"] = "off"
    bvals, *_ = _bash_parse(tk, env)
    assert bvals.get("X4_GUARD") == "off", bvals


# --- FX-B4 (reviewer I): ONE spelling rule for $X4_CONFIG ----------------------------------
#
# Python opens what pathlib + the OS make of the string: trailing separators dropped
# everywhere, a final component's trailing dots and spaces dropped on Windows, a relative path
# taken against the working directory. The bash loader tested the raw string with `-f`, so
# `x4-paths.env/` and (Windows) `x4-paths.env.` were READ by every Python tool and "missing"
# for every guard -- which then fell back to the DEFAULT reference root. Each row is one
# spelling of the SAME file; `reads` is what both must answer (so a both-read-nothing pass
# is impossible for the rows that name the file).
_WIN = os.name == "nt"
SPELLINGS = [
    # id,                 spelling (relative to cwd = the toolkit, or {tk}-absolute), reads, windows-only
    ("plain",             "{tk}/x4-paths.env",            True,  False),
    ("relative-bare",     "x4-paths.env",                 True,  False),
    ("relative-dot",      "./x4-paths.env",               True,  False),
    ("relative-up",       "../tk/x4-paths.env",           True,  False),
    ("dotdot-inside",     "{tk}/sub/../x4-paths.env",     True,  False),
    ("trailing-slash",    "{tk}/x4-paths.env/",           True,  False),
    ("trailing-slashes",  "x4-paths.env//",               True,  False),
    ("trailing-dot",      "{tk}/x4-paths.env.",           True,  True),
    ("trailing-dots-rel", "x4-paths.env..",               True,  True),
    ("trailing-space",    "{tk}/x4-paths.env ",           True,  True),
    ("trailing-backslash", "{tk}" + chr(92) + "x4-paths.env" + chr(92), True, True),
    ("upper-case",        "{TK}/X4-PATHS.ENV",            True,  True),
    ("missing",           "{tk}/nope.env",                False, False),
    ("missing-dot",       "{tk}/nope.env.",               False, False),
    ("a-directory",       "{tk}/sub/",                    False, False),
    ("dot-only",          ".",                            False, False),
    # FX-G5 / reviewer J2 item 4 (MEASURED: bash read nothing, Python read the file): `.` and
    # `..` components are normalised LEXICALLY, and a `::$DATA` suffix (Windows: the file's own
    # data stream) dropped, the same way in both halves.
    ("dot-component-trail", "x4-paths.env/.",             True,  False),
    ("dot-components",    "./x4-paths.env/./",            True,  False),
    ("dotdot-then-dot",   "sub/../x4-paths.env/.",        True,  False),
    ("abs-trail-dot",     "{tk}/x4-paths.env/.",          True,  False),
    ("data-stream",       "x4-paths.env::$DATA",          True,  True),
    ("data-stream-case",  "{tk}/x4-paths.env::$data",     True,  True),
    # twins: the same moves never invent a file
    ("missing-dot-comp",  "sub/../nope.env/.",            False, False),
    ("dotdot-escapes",    "sub/../../x4-paths.env/.",     False, False),
    ("data-stream-missing", "nope.env::$DATA",            False, True),
    # FX-G6 / reviewer K M2 (MEASURED: bash read nothing, Python read the file): a `::` stream
    # suffix other than exactly ONE trailing `::$DATA` names no config file -- in both halves.
    ("data-stream-then-dot", "x4-paths.env::$DATA/.",     False, True),
    ("data-stream-twice", "x4-paths.env::$DATA::$DATA",   False, True),
    ("data-stream-space", "x4-paths.env::$DATA ",         False, True),
    ("data-stream-abs-dot", "{tk}/x4-paths.env::$DATA/.", False, True),
    # twins: exactly one trailing `::$DATA`, after `.`/`..` and a trailing dot-space, still reads
    ("dot-then-data-stream", "x4-paths.env/.::$DATA",     True,  True),
    ("dotdot-data-stream", "{tk}/sub/../x4-paths.env::$DATA", True, True),
    ("dot-space-data-stream", "x4-paths.env. ::$DATA",    True,  True),
]


def test_FXG5_python_names_the_file_by_the_SAME_spelling_bash_takes():
    """The agreement rows above compare WHETHER a file is read; this pins WHICH spelling the
    Python half reports (x4doctor and the foreign-config check compare it to a toolkit)."""
    from x4validate import _paths

    sp = _paths.config_spelling
    assert sp("sub/../x4-paths.env/.") == pathlib.Path("x4-paths.env")
    assert sp("./x4-paths.env/./") == pathlib.Path("x4-paths.env")
    if _WIN:
        assert sp("x4-paths.env::$DATA") == pathlib.Path("x4-paths.env")
        assert sp("C:/t/x4-paths.env::$Data") == pathlib.Path("C:/t/x4-paths.env")
        assert sp("x4-paths.env:other") == pathlib.Path("x4-paths.env:other")   # twin: a NAMED stream
        # FX-G6 / reviewer K M2: any further `::` names no config; one trailing `::$DATA` does
        ns = _paths.config_names_stream
        assert ns("x4-paths.env::$DATA/.") and ns("a::$DATA::$DATA") and ns("a::$DATA ")
        assert not ns("x4-paths.env::$DATA") and not ns("C:/t/x4-paths.env") and not ns("a:other")


@pytest.mark.parametrize("row", SPELLINGS, ids=[r[0] for r in SPELLINGS])
def test_FXB4_both_loaders_read_the_SAME_file_for_every_X4_CONFIG_spelling(tmp_path, row):
    _id, spelling, reads, win_only = row
    if win_only and not _WIN:
        pytest.skip("a Windows path spelling (Win32 drops trailing dots/spaces; case-folds)")
    tk = tmp_path / "tk"
    (tk / "sub").mkdir(parents=True)
    (tk / NEW).write_text('X4_GAME="/xgame/spelled"\n', encoding="utf-8", newline="\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR")}
    env["X4_TOOLKIT"] = str(tk)
    env["X4_CONFIG"] = spelling.format(tk=str(tk), TK=str(tk).upper())
    bf, bg = _bash_in(tk, env, tk)
    p = _py_in(env, tk, self_root=tk)
    assert (bool(bf), _n(bg)) == (p["file"] is not None, _n(p["game"])), (_id, bf, bg, p)
    assert bool(bf) is reads, (_id, "bash", bf)
    assert _n(bg) == (_n("/xgame/spelled") if reads else ""), (_id, bg)

"""Every Python a hook script starts runs ISOLATED (`-I`): no module from the cwd or PYTHON* env.

MEASURED (4.2.0 final guard review, 2026-11-14): with jq unavailable the hooks print their
decision through `"$PY" -c 'import json ...'`. `python -c` puts the CURRENT DIRECTORY first on
sys.path, and a hook's cwd is the project folder, which agents can write. With JQ hidden and a
planted json.py that exits 0, protect-files.sh's deny became 0 bytes, rc 0 -- which the host reads
as ALLOW. PYTHONPATH (a sitecustomize.py on it) reaches every interpreter the same way, including
the one a script path starts. `-I` drops the cwd/script dir from sys.path and ignores every PYTHON*
variable and the user site; a script that imports a sibling inserts its OWN directory explicitly.

Runtime half: the real deployed hooks with jq hidden, a plant in the cwd or on PYTHONPATH, and a
control proving that plant DOES take over a non-isolated interpreter (else the case is a skip, never
a pass). Twin: the same hooks with no plant give the same deny. Source half: a non-isolated Python
invocation in any hook script is red, over a census that must be non-empty.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_testlib import HOOKS, REPO, make_sandbox
from test_codex_wrapper import wrappers

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hooks are NOT checked here")

#: Each plant records that it ran, then ends the process SILENTLY with rc 0: the shape that turns a
#: deny into 0 bytes. sitecustomize/usercustomize do it at interpreter start, json.py at import.
_BODY = ("import os\n"
         "open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'PLANT-RAN'), 'a').write('x')\n"
         "os._exit(0)\n")
PLANTS = ("json.py", "sitecustomize.py", "usercustomize.py")
#: The BASE interpreter, not the venv's: a venv disables the user site, so usercustomize could never
#: act and its case would only ever skip. The hooks need nothing but the stdlib.
PY = getattr(sys, "_base_executable", None) or sys.executable
VECTORS = ("cwd", "pythonpath")


def _plant(tmp_path, name, vector, env):
    d = tmp_path / "plant"
    d.mkdir()
    (d / name).write_text(_BODY, encoding="utf-8")
    cwd = tmp_path / "work"
    cwd.mkdir()
    if vector == "cwd":
        cwd = d
    else:
        env["PYTHONPATH"] = str(d)
    return d / "PLANT-RAN", cwd


def _require_effective(env, cwd, marker):
    """The CONTROL: the plant takes over a NON-isolated interpreter here, or this case proves nothing."""
    subprocess.run([env["X4_PYTHON"], "-c", "import json"], cwd=cwd, env=env, capture_output=True, timeout=60)
    if not marker.exists():
        pytest.skip("this plant cannot act on a non-isolated %s here (e.g. no user site in a venv)"
                    % env["X4_PYTHON"])
    marker.unlink()


def _decision(stdout: bytes):
    out = stdout.decode("utf-8", "replace").strip()
    if not out:
        return "allow", ""
    h = json.loads(out)["hookSpecificOutput"]
    return h.get("permissionDecision") or "advise", h.get("permissionDecisionReason") or ""


def _cases(tk):
    return [
        ("protect-files.sh", {"tool_name": "Write", "tool_input": {
            "file_path": str(tk / "reference" / "libraries" / "wares.xml"), "content": "x"}}, "reference/ is read-only"),
        ("protect-bash.sh", {"tool_name": "Bash", "tool_input": {"command": "git add -A"}}, "GIT ADD -A"),
    ]


def _env(tmp_path):
    _tmp, tk, env = make_sandbox(tmp_path)
    env.update(JQ=str(tmp_path / "no-such-jq"), X4_PYTHON=PY, X4_HOUSEKEEP_SWEEP="off")
    for k in ("PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING"):
        env.pop(k, None)
    return tk, env


NAME = "é漢字.xml"          # é漢字.xml: Latin-1 AND outside every ANSI code page


@needs_bash
@pytest.mark.parametrize("utf8", [None, "1"])
def test_a_non_ascii_path_is_judged_and_backed_up_with_jq_hidden(tmp_path, utf8):
    """F380 review F1, MEASURED: -I ignores PYTHONUTF8, so the jq-less PATH READERS (which wrote with
    text-mode sys.stdout) failed on a CJK path -- "could not read the file path ... NO rule was
    evaluated" and "NO BACKUP was taken". Both with and without PYTHONUTF8 they must now read it."""
    tk, env = _env(tmp_path)
    if utf8:
        env["PYTHONUTF8"] = utf8
    ref, dev = tk / "reference" / "libraries" / NAME, tk / "dev" / "mymod" / NAME
    dev.write_text("old", encoding="utf-8")

    def run(hook, path):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}}
        r = subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           cwd=tmp_path, capture_output=True, env=env, timeout=120)
        assert r.returncode == 0, (hook, r.stderr)
        return _decision(r.stdout)

    d, why = run("protect-files.sh", ref)
    assert d == "deny" and "reference/ is read-only" in why, (d, why)
    d, why = run("protect-files.sh", dev)
    assert d in ("allow", "advise"), (d, why)
    before = {p for p in (tmp_path / "backups").rglob("*") if p.is_file()}
    d, why = run("backup-before-edit.sh", dev)
    assert d in ("allow", "advise"), (d, why)
    taken = [p for p in (tmp_path / "backups").rglob("*") if p.is_file() and p not in before]
    assert any("漢字" in p.name and p.read_text(encoding="utf-8") == "old" for p in taken), taken


@needs_bash
@pytest.mark.parametrize("utf8,want", [(None, ["-I"]), ("1", ["-I", "-X", "utf8"]), ("0", ["-I", "-X", "utf8=0"]),
                                       ("yes", ["-I"])])
def test_PYTHONUTF8_is_carried_as_its_option(tmp_path, utf8, want):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONDONTWRITEBYTECODE")}
    if utf8:
        env["PYTHONUTF8"] = utf8
    r = subprocess.run([BASH, "-c", '. "$1/_x4-env.sh"; printf "%s\\n" "${X4_PYI[@]}"', "x", str(HOOKS)],
                       capture_output=True, env=dict(env, HOOK_DIR=str(HOOKS)), timeout=60)
    assert r.stdout.decode().split() == want, r


@needs_bash
@pytest.mark.parametrize("vector", VECTORS)
@pytest.mark.parametrize("plant", PLANTS)
def test_a_planted_module_cannot_swallow_a_deny_with_jq_hidden(tmp_path, plant, vector):
    tk, env = _env(tmp_path)
    marker, cwd = _plant(tmp_path, plant, vector, env)
    _require_effective(env, cwd, marker)
    for hook, payload, why in _cases(tk):
        r = subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload).encode(), cwd=cwd,
                           capture_output=True, env=env, timeout=120)
        d, reason = _decision(r.stdout)
        assert r.returncode == 0 and d == "deny" and why in reason, (hook, plant, vector, r.stdout, r.stderr)
        assert not marker.exists(), (hook, plant, vector, "the planted module RAN inside the hook")


@needs_bash
def test_TWIN_no_plant_gives_the_same_deny(tmp_path):
    tk, env = _env(tmp_path)
    for hook, payload, why in _cases(tk):
        r = subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload).encode(), cwd=tmp_path,
                           capture_output=True, env=env, timeout=120)
        d, reason = _decision(r.stdout)
        assert r.returncode == 0 and d == "deny" and why in reason, (hook, r.stdout, r.stderr)


DENY = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: x"}}'


@pytest.mark.parametrize("kind,launcher", wrappers())
@pytest.mark.parametrize("vector", VECTORS)
@pytest.mark.parametrize("plant", PLANTS)
def test_the_codex_wrapper_python_is_isolated(tmp_path, kind, launcher, plant, vector):
    """The wrapper starts the adapter by PATH and re-parses its JSON with `-c`: both are isolated."""
    tree = tmp_path / ".codex" / "hooks"
    shutil.copytree(HOOKS, tree)
    (tree / "codex_adapter.py").write_text(f"print('X4OK ' + {DENY!r})", encoding="utf-8")
    env = dict(os.environ, X4_PYTHON=PY, X4_NO_PYTHON_FALLBACK="1", X4_WRAPPER_TIMEOUT_S="30")
    env.pop("PYTHONPATH", None)
    marker, cwd = _plant(tmp_path, plant, vector, env)
    _require_effective(env, cwd, marker)
    script = tree / ("codex-entry.ps1" if kind == "ps1" else "codex-entry.sh")
    r = subprocess.run(launcher + [str(script), "pre_tool_use"], input=b"{}", cwd=cwd, capture_output=True,
                       env=env, timeout=90)
    assert r.returncode == 0 and json.loads(r.stdout) == json.loads(DENY), (kind, plant, vector, r.stdout)
    assert not marker.exists(), (kind, plant, vector, "the planted module RAN inside the wrapper's python")


# --- source half ------------------------------------------------------------------------------------
GUARDS = REPO / "agent" / "guards"
#: Where a COMMAND starts: line start, after `$(`, `(` (not an array's `=(`), `{`, `|`, `&`, `;`, `!`,
#: a backtick; then any keywords and wrappers (`if`, `then`, `exec`, `timeout 5`, `env`, ...) and env
#: assignments.
_LEAD = (r'(?:^|\$\(|(?<!=)\(|[{|;&!`])\s*'
         r'(?:(?:if|then|else|elif|do|while|until|exec|command|builtin|nohup|time|!)\s+'
         r'|timeout\s+(?:-\S+\s+)*\S+\s+|env\s+(?:-\S+\s+)*'
         # an env assignment; its value never holds `$(` -- `OUT=$("$PY" ...` is a substitution,
         # and a value that swallowed it hid the interpreter inside (MEASURED: 2 canary sites)
         r'|[A-Za-z_][A-Za-z0-9_]*=(?:"(?:[^"$]|\$(?!\())*"|\'[^\']*\'|(?:[^\s"\'$]|\$(?!\())*)\s+)*')
#: An interpreter in a variable whose NAME says Python ($PY, $_py, $X4_PY, ${PY[@]}, $_x4_hkpy,
#: $X4_PYTHON, $python, quoted or not), or a bare one (python, python3, python3.12, py, uv run python).
_VAR = r'"?\$\{?([A-Za-z_][A-Za-z0-9_]*)(?:\[@\])?\}?"?'
_BARE_WORD = r'(?:uv\s+run\s+(?:-\S+\s+(?:\d\S*\s+)?)*)?(?:python(?:3(?:\.\d+)?)?w?|py)'
_INVOKE = re.compile(_LEAD + r'(?:' + _VAR + r'|(' + _BARE_WORD + r'))(?=\s|$)')
_ISO_TOKENS = ('${X4_PYI[@]}', '${PYI[@]}')
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]+)['\"]?")


def _quote_walk(line: str, state):
    """(state at the END of `line`, index where code resumes if `line` STARTS inside a quote, else 0).
    The state is a stack of frames: ("c", paren depth, open `case`s) for code, "'" or '"' for a
    string; `$(` inside a double-quoted string opens a code frame, and a `case` pattern's `)` does
    not close it. Coarse on purpose; a line it misreads shows up as a census change."""
    stack = list(state) if state else [("c", 0, 0)]
    code = lambda: isinstance(stack[-1], tuple)  # noqa: E731
    i, resume = 0, (0 if code() else None)
    while i < len(line):
        ch = line[i]
        top = stack[-1]
        if isinstance(top, tuple):
            word = re.match(r"(case|esac)\b", line[i:]) if (i == 0 or not re.match(r"\w", line[i - 1])) else None
            if ch == "#" and (i == 0 or line[i - 1] in " \t;("):
                break                                     # a comment: the rest of the line is prose
            if word:
                stack[-1] = ("c", top[1], top[2] + (1 if word.group(1) == "case" else -1))
                i += 4
                continue
            if ch == "\\":
                i += 1
            elif ch in "'\"":
                stack.append(ch)
            elif ch == "(":
                stack[-1] = ("c", top[1] + 1, top[2])
            elif ch == ")":
                if top[1]:
                    stack[-1] = ("c", top[1] - 1, top[2])
                elif top[2] <= 0 and len(stack) > 1:
                    stack.pop()                           # the `)` that ends a `$(` inside a string
        elif top == "'":
            if ch == "'":
                stack.pop()
        else:
            if ch == "\\":
                i += 1
            elif ch == '"':
                stack.pop()
            elif line.startswith("$(", i):
                stack.append(("c", 0, 0))
                i += 1
        i += 1
        if resume is None and code():
            resume = i
    return tuple(stack), resume


def _code_lines(path):
    """Code lines, without comments, the BODY of a here-document, or the part of a line still INSIDE
    a multi-line quoted string (a deny message's prose, a `-c '...'` Python body): not commands."""
    tag, state = None, None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if tag is not None:
            if s == tag:
                tag = None
            continue
        state, resume = _quote_walk(line, state)
        if resume is None:
            continue                                      # the whole line is inside the string
        line = line[resume:]
        s = line.strip()
        m = _HEREDOC.search(line)
        if m and not re.search(r"<<<", line):
            tag = m.group(1)
        if not s or s.startswith("#"):
            continue
        yield n, line


def _shell_scripts():
    found = sorted(p for p in GUARDS.rglob("*.sh") if not p.name.startswith("test-"))
    assert len(found) >= 10, found
    return found


def _isolated(rest: str) -> bool:
    """Walk the interpreter's OPTIONS (they end at -c, -m, `-` or the first non-option): isolated
    iff an isolation token or a short-option cluster holding `I` comes first. -X/-W take a value."""
    toks = re.findall(r'"[^"]*"|\'[^\']*\'?|\S+', rest)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.strip('"') in _ISO_TOKENS:
            return True
        if re.fullmatch(r"-3(?:\.\d+)?(?:-\d+)?|-V:\S+", t):       # the py launcher's version switch
            i += 1
            continue
        if not t.startswith("-") or t == "-" or t.startswith("--"):
            return False
        for j, ch in enumerate(t[1:]):
            if ch == "I":
                return True
            if ch in "cm":
                return False
            if ch in "XW":
                if j == len(t) - 2:
                    i += 1                                        # its value is the next word
                break
        i += 1
    return False


def _blank_quotes(line: str) -> str:
    """Empty every same-line quoted string: a bare interpreter NAMED in prose is not a run."""
    return re.sub(r'"[^"]*"|\'[^\']*\'', '""', line)


def _sites(line):
    """(text, isolated) for every interpreter START on the line."""
    out = []
    for m in _INVOKE.finditer(line):                   # variables: the quotes are part of the shape
        name = m.group(1)
        if name and re.search(r"(?i)py", name) and name not in ("X4_PYI", "PYI"):
            out.append((m.group(0).strip(), _isolated(line[m.end():])))
    blank = _blank_quotes(line)
    for m in _INVOKE.finditer(blank):                  # bare words: never inside a string
        if m.group(2):
            out.append((m.group(0).strip(), _isolated(blank[m.end():])))
    return out


def test_every_python_a_hook_script_starts_is_isolated():
    census, bad = 0, []
    for p in _shell_scripts():
        for n, line in _code_lines(p):
            for text, ok in _sites(line):
                census += 1
                if not ok:
                    bad.append(f"{p.relative_to(REPO)}:{n}: {line.strip()[:120]}")
    # EXACT, not a floor (#23): two sites hidden by a too-greedy assignment value read as 30 >= 30.
    # A new interpreter start in a hook script changes this number on purpose.
    assert census == 32, census
    assert not bad, "non-isolated python in a hook script:\n" + "\n".join(bad)


def _flagged(line):
    sites = _sites(line)
    return None if not sites else any(not ok for _t, ok in sites)


@pytest.mark.parametrize("line,red", [
    ('  X4_IN="$x" "$PY" -c \'import json\'', True),
    ('  X4_IN="$x" "$PY" "${X4_PYI[@]}" -c \'import json\'', False),
    ('  X4_REASON="$1" X4_EVENT="${2:-PreToolUse}" "$_py" -c \'x\'', True),
    ('BAD="$(printf x | "${PY[@]}" -c \'', True),
    ('"${PY[@]}" "$ADAPTER" "$EV"', True),
    ('"${PY[@]}" "${PYI[@]}" "$ADAPTER" "$EV"', False),
    ('    _h="$("$_x4_sgpy" "$HOOK_DIR/settings_guard.py")"', True),
    ('  _x4_sghit="$("$_x4_sgpy" -B "$HOOK_DIR/s.py" --scan "${f[@]}" 2>/dev/null)"', True),
    ('OUT=$("$PY" "$CANARY" 2>&1)', True),
    ('OUT=$("$PY" "${X4_PYI[@]}" "$CANARY" 2>&1)', False),
    ('  ( "$_x4_hkpy" -B "$HK" session & )', True),
    ('  ( "$_x4_hkpy" "${X4_PYI[@]}" -B "$HK" session & )', False),
    ('"$X4_PY" - <<\'EOF\'', True),
    ('"$X4_PY" -B "$HOOK_DIR/charter.py"', True),
    ('"$X4_PY" -IB "$HOOK_DIR/charter.py"', False),
    # review F2, each missed shape
    ('if "$PY" -c \'import sys\'; then', True),
    ('then "$PY" x.py', True),
    ('! "$PY" x.py', True),
    ('exec "$PY" x.py', True),
    ('timeout 5 "$PY" x.py', True),
    ('"$X4_PYTHON" x.py', True),
    ('"$_x4_py" -c x', True),
    ('$python x.py', True),
    ('python3.12 x.py', True),
    ('uv run python x.py', True),
    ('uv run --frozen python x.py', True),
    ('cd a && python3 y.py', True),
    ('out=$(python -c \'x\')', True),
    ('a | py -3 x.py', True),
    ('"$PY" -X utf8 x.py', True),
    ('"$PY" -c \'-I\'', True),
    # ... and each wrongly flagged one, plus isolated twins of the shapes above
    ('py -3 -I x.py', False),
    ('"$PY" -X utf8 -I x.py', False),
    ('"$PY" -Xutf8 -I x.py', False),
    ('if "$PY" -I -c \'import sys\'; then', False),
    ('timeout 5 "$PY" -I x.py', False),
    ('uv run --frozen python -I x.py', False),
    ('python3.12 -I x.py', False),
])
def test_the_check_goes_red_on_a_non_isolated_site(line, red):
    """Falsification twins for the census: each shape it must catch, and its isolated twin."""
    assert _flagged(line) is red, (line, _sites(line))


@pytest.mark.parametrize("line", [
    'if [ -n "$PY" ]; then',                              # a test of the variable, not a run
    'PY="$X4_PY"',
    'then PY=(py -3)',                                    # an array assignment
    'deny "BLOCKED: lift it first (python scripts/x.py remove"',   # prose inside a string
    'echo "run: python x.py"',
    '_lkcmd="python \\"$_lk\\" unlock"',
])
def test_the_check_ignores_what_is_not_a_run(line):
    assert _flagged(line) is None, (line, _sites(line))


def test_only_a_heredoc_BODY_is_skipped(tmp_path):
    s = tmp_path / "s.sh"
    s.write_text("cat <<'EOF'\n  python run.py\nEOF\n\"$PY\" - <<EOF\nprint(1)\nEOF\n  python3 after.py\n",
                 encoding="utf-8")
    assert [n for n, _ in _code_lines(s)] == [1, 4, 7]


def test_only_a_line_that_STARTS_inside_a_quoted_string_is_skipped(tmp_path):
    s = tmp_path / "s.sh"
    s.write_text('deny "first line\n  - prose && python ask.py\nend"; python3 after.py\n'
                 'X=1 "$PY" x.py\n  "$PY" -c \'import os\nprint(1)\'\n'
                 'v="$(\n  "$PY" -I y.py "$(printf \'%s\' "$a")"\n)"; python z.py\n', encoding="utf-8")
    lines = dict(_code_lines(s))
    assert sorted(lines) == [1, 3, 4, 5, 7, 8, 9], lines  # 2 and 6 are wholly inside a string
    assert not _sites(lines[1])
    assert [ok for _t, ok in _sites(lines[3])] == [False]  # the code AFTER the string closes is checked
    assert [ok for _t, ok in _sites(lines[4])] == [False]
    assert [ok for _t, ok in _sites(lines[8])] == [True]   # code inside a multi-line "$( )" is code
    assert [ok for _t, ok in _sites(lines[9])] == [False]  # and the string it sat in closes


def test_the_powershell_entry_starts_python_isolated():
    """The .ps1 wrapper builds one argument list; the runtime test above covers it where pwsh exists.
    NOT covered: the OpenCode plugin's spawn lives in the PINNED tools/modding_harness (hash-locked,
    upstream-owned), so it is a BLIND-SPOTS row, not an edit here."""
    ps1 = (GUARDS / "adapters" / "codex-entry.ps1").read_text(encoding="utf-8")
    lines = re.findall(r"\$argList = (.+)", ps1)
    assert len(lines) == 1 and "$script:PyIsolation" in lines[0], lines
    assert re.search(r"\$script:PyIsolation = @\(if \(\$env:PYTHONDONTWRITEBYTECODE\) \{ '-IB' \} else \{ '-I' \}\)", ps1)
    assert "$script:PyIsolation += @('-X', 'utf8')" in ps1


_OWN_DIR = re.compile(r"sys\.path\.insert\(0, (?:str\(Path\(__file__\)\.resolve\(\)\.parent\)"
                      r"|str\(HERE\)|str\(_p\)|os\.path\.dirname\(os\.path\.abspath\(__file__\)\)"
                      r"|str\(ROOT / \"tools/x4_harness\"\))\)")   # generic.py: the dir its siblings ship in


def _hook_scripts():
    """Every hook-tree .py run as a SCRIPT, with whether it reaches a sibling module: an import of
    one (AST, lazy imports included), or the save_policy bridge loaded by path, which imports more."""
    import ast
    dirs = (GUARDS / "claude-hooks", GUARDS / "adapters")
    siblings = {p.stem for d in dirs for p in d.glob("*.py")} | {"codex_adapter", "opencode_adapter"}
    out = []
    for d in dirs:
        for p in sorted(d.glob("*.py")):
            src = p.read_text(encoding="utf-8")
            if p.name.startswith("test_") or '__name__ == "__main__"' not in src:
                continue
            names = set()
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Import):
                    names |= {a.name for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names.add(n.module)
            out.append((p, bool(names & siblings) or "'save_policy.py'" in src, src))
    assert len(out) >= 8, [p.name for p, *_ in out]
    return out


def test_every_hook_script_that_reaches_a_sibling_inserts_its_own_directory():
    """Under -I the script's directory is NOT on sys.path; a script must put it there itself."""
    reach = [(p, src) for p, needs, src in _hook_scripts() if needs]
    names = {p.name for p, _ in reach}
    assert {"preferences.py", "charter.py", "save_policy.py", "save_sentinel.py", "hook_facts.py",
            "codex.py"} <= names, names                  # the census still sees the known ones
    missing = [p.name for p, src in reach if not _OWN_DIR.search(src)]
    assert not missing, missing

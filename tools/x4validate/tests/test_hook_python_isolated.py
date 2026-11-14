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
    env.pop("PYTHONPATH", None)
    return tk, env


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
#: An interpreter held in a shell variable: "$PY", "$_py", "$X4_PY", "${PY[@]}", "$_x4_hkpy", ...
_VAR = r'"\$\{?(?:PY|_py|X4_PY|_x4_[a-z]+py)(?:\[@\])?\}?"'
#: ... at a COMMAND position: line start, after `$(`, `(`, `{`, `|`, `&&`, `;`, then any env assignments.
_POS = r'(?:^\s*|\$\(\s*|[|;&({]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|\'[^\']*\'|\S*)\s+)*'
_INVOKE = re.compile(_POS + "(" + _VAR + r")\s")
_ISOLATED = re.compile(r'(?:"\$X4_PYI"|"\$\{PYI\[@\]\}"|-I[A-Za-z]*)\s')
#: A bare interpreter at a command position. Narrower than _POS (no `(`, `&&`): those positions occur
#: in the guards' own prose ("lift the deny first (python scripts/...") and in `PY=(py -3)`.
_BARE = re.compile(r'(?:^\s*|\$\(\s*|\|\s*)(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|\S*)\s+)*'
                   r'(?:python3?|py(?:\s+-3)?)\s+(?!-I)')
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]+)['\"]?")


def _code_lines(path):
    """Code lines, without comments and without the BODY of a here-document (prose, not commands)."""
    tag = None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if tag is not None:
            if s == tag:
                tag = None
            continue
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


def test_every_python_a_hook_script_starts_is_isolated():
    census, bad = 0, []
    for p in _shell_scripts():
        for n, line in _code_lines(p):
            for m in _INVOKE.finditer(line):
                census += 1
                if not _ISOLATED.match(line, m.end()):
                    bad.append(f"{p.relative_to(REPO)}:{n}: {line.strip()[:120]}")
            if _BARE.search(line) and not re.search(r'\b(?:echo|printf)\b', line):
                bad.append(f"{p.relative_to(REPO)}:{n}: bare: {line.strip()[:120]}")
    assert census >= 30, census          # the shape still matches what the scripts write
    assert not bad, "non-isolated python in a hook script:\n" + "\n".join(bad)


def _flagged(line):
    return any(not _ISOLATED.match(line, m.end()) for m in _INVOKE.finditer(line))


def test_the_check_goes_red_on_a_non_isolated_site():
    """Falsification twins for the census regex: each shape it must catch, and its isolated twin."""
    for line, red in (('  X4_IN="$x" "$PY" -c \'import json\'', True),
                      ('  X4_IN="$x" "$PY" "$X4_PYI" -c \'import json\'', False),
                      ('  X4_REASON="$1" X4_EVENT="${2:-PreToolUse}" "$_py" -c \'x\'', True),
                      ('BAD="$(printf x | "${PY[@]}" -c \'', True),
                      ('"${PY[@]}" "$ADAPTER" "$EV"', True),
                      ('"${PY[@]}" "${PYI[@]}" "$ADAPTER" "$EV"', False),
                      ('    _h="$("$_x4_sgpy" "$HOOK_DIR/settings_guard.py")"', True),
                      ('  ( "$_x4_hkpy" -B "$HK" session & )', True),
                      ('"$X4_PY" - <<\'EOF\'', True),
                      ('"$X4_PY" -B "$HOOK_DIR/charter.py"', True),
                      ('"$X4_PY" -IB "$HOOK_DIR/charter.py"', False),
                      ('if [ -n "$PY" ]; then', False),          # a test of the variable, not a run
                      ('PY="$X4_PY"', False)):
        assert _flagged(line) is red, line
    assert _BARE.search("out=$(python -c 'x')") and _BARE.search("  python3 x.py") \
        and _BARE.search("a | py -3 x.py")
    assert not _BARE.search("deny \"BLOCKED: lift it first (python scripts/x.py remove\"") \
        and not _BARE.search("then PY=(py -3)")
    assert not _BARE.search("  python -I x.py")


def test_only_a_heredoc_BODY_is_skipped(tmp_path):
    s = tmp_path / "s.sh"
    s.write_text("cat <<'EOF'\n  python run.py\nEOF\n\"$PY\" - <<EOF\nprint(1)\nEOF\n  python3 after.py\n",
                 encoding="utf-8")
    assert [n for n, _ in _code_lines(s)] == [1, 4, 7]


def test_the_powershell_entry_starts_python_isolated():
    """The .ps1 wrapper builds one argument list; the runtime test above covers it where pwsh exists.
    NOT covered: the OpenCode plugin's spawn lives in the PINNED tools/modding_harness (hash-locked,
    upstream-owned), so it is a BLIND-SPOTS row, not an edit here."""
    ps1 = (GUARDS / "adapters" / "codex-entry.ps1").read_text(encoding="utf-8")
    lines = re.findall(r"\$argList = (.+)", ps1)
    assert len(lines) == 1 and "$script:PyIsolation" in lines[0], lines
    assert re.search(r"\$script:PyIsolation = if \(\$env:PYTHONDONTWRITEBYTECODE\) \{ '-IB' \} else \{ '-I' \}", ps1)


@pytest.mark.parametrize("name", ["preferences.py", "charter.py", "save_policy.py", "save_sentinel.py"])
def test_a_hook_script_that_imports_a_sibling_inserts_its_own_directory(name):
    """Under -I the script's directory is NOT on sys.path; the script must put it there itself."""
    src = (GUARDS / "claude-hooks" / name).read_text(encoding="utf-8")
    assert re.search(r"sys\.path\.insert\(0, str\(Path\(__file__\)\.resolve\(\)\.parent\)\)", src), name

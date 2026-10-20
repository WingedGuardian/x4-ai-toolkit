"""The toolkit's temp dirs: every prefix the code creates is one the session-start sweep knows, and the
session-start hook shows the sweep's note without launching a real sweep from inside a test.

MEASURED 2026-10-18 on one machine: 1,184 `x4qa_registry_*` and 767 `x4qa_out_*` dirs left by qa_sweep, which
never removed its sandboxes. RETENTION.md, "The toolkit cleans up after itself".
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK_SRC = REPO / "agent" / "guards" / "claude-hooks"
_spec = importlib.util.spec_from_file_location("x4housekeep_hygiene", REPO / "scripts" / "x4housekeep.py")
hk = importlib.util.module_from_spec(_spec)
sys.modules["x4housekeep_hygiene"] = hk
_spec.loader.exec_module(hk)

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hook is NOT checked here")


def _created_prefixes():
    found = {}
    pat = re.compile(r"""(?:mkdtemp|TemporaryDirectory)\(\s*prefix=["']([^"']+)["']\s*\)""")
    roots = [REPO / "tools" / "x4validate", REPO / "scripts", HOOK_SRC]
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            if ".venv" in p.parts or "fixtures" in p.parts or p.name == Path(__file__).name:
                continue
            for m in pat.finditer(p.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(m.group(1), str(p.relative_to(REPO)))
    return found


def test_every_temp_prefix_the_toolkit_creates_is_one_the_sweep_knows():
    created = _created_prefixes()
    assert "x4qa_registry_" in created and "x4-pytest-batch-" in created, "the prefix scan found nothing: fix it"
    known = tuple(hk.DEFAULTS["temp_prefixes"])
    missing = {k: v for k, v in created.items() if not k.startswith(known)}
    assert not missing, ("a leftover of these is never swept; add each to x4housekeep.DEFAULTS['temp_prefixes']:\n"
                         + "\n".join(f"  {k}  ({v})" for k, v in sorted(missing.items())))


def test_qa_sweep_removes_its_sandboxes_at_exit(tmp_path):
    code = ("import sys, tempfile; sys.path.insert(0, sys.argv[1]); tempfile.tempdir = sys.argv[2]\n"
            "from gates import qa_sweep\n"
            "print(qa_sweep._sandbox_dir()); print(qa_sweep._sandbox_registry())\n")
    temp = tmp_path / "temp"
    for d in ("temp", "ext", "ref"):
        (tmp_path / d).mkdir()
    env = dict(os.environ, X4_TOOLKIT="", X4_EXTENSIONS=str(tmp_path / "ext"), X4_REFERENCE=str(tmp_path / "ref"))
    r = subprocess.run([sys.executable, "-c", code, str(REPO / "tools" / "x4validate"), str(temp)], env=env,
                       capture_output=True, text=True, timeout=120, cwd=str(REPO / "tools" / "x4validate"))
    assert r.returncode == 0, r.stderr
    lines = [Path(line.strip()) for line in r.stdout.splitlines() if line.strip().startswith(str(temp))]
    assert len(lines) == 2, r.stdout
    made = [lines[0], lines[1].parent]                               # the registry sandbox is the file's folder
    assert not any(p.exists() for p in made), "qa_sweep left its sandbox dirs behind"
    assert list(temp.iterdir()) == []


def _hook_tree(tmp_path):
    hooks = tmp_path / "proj" / ".claude" / "hooks"
    shutil.copytree(HOOK_SRC, hooks)
    return hooks


def _run_hook(hooks, env):
    return subprocess.run([BASH, str(hooks / "session-canary.sh")], input=b"{}", capture_output=True,
                          env=env, timeout=120).stdout.decode("utf-8", "replace")


@needs_bash
def test_session_start_shows_the_disk_note(tmp_path):
    hooks = _hook_tree(tmp_path)
    env = dict(os.environ, X4_TOOLKIT=str(tmp_path / "none"), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"))
    assert "[x4 disk]" not in _run_hook(hooks, env)                 # twin: no note yet
    (hooks.parent / "housekeep-session.txt").write_text("[x4 disk] removed 3 leftover temp dir(s)\n",
                                                         encoding="utf-8")
    assert "[x4 disk] removed 3 leftover temp dir(s)" in _run_hook(hooks, env)


@needs_bash
def test_session_start_launches_the_sweep_only_outside_tests(tmp_path):
    hooks = _hook_tree(tmp_path)
    tk = tmp_path / "tk"
    (tk / "scripts").mkdir(parents=True)
    (tk / "scripts" / "x4housekeep.py").write_text(
        "import sys\nopen(sys.argv[-1] + '.launched', 'w').write(' '.join(sys.argv[1:]))\n", encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tk), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"))
    env.pop("X4_HOUSEKEEP_SWEEP", None)
    _run_hook(hooks, env)
    time.sleep(2)
    note = hooks.parent / "housekeep-session.txt"
    assert not Path(str(note) + ".launched").exists(), "under pytest the hook must not sweep the real temp dir"
    env["X4_SWEEP_IN_TESTS"] = "1"                                   # twin: what a real session does
    _run_hook(hooks, env)
    launched = Path(str(note) + ".launched")
    for _ in range(50):
        if launched.exists():
            break
        time.sleep(0.2)
    args = launched.read_text(encoding="utf-8").split()
    assert args[0] == "session" and Path(args[1]).name == "housekeep-session.txt"


_MARKER = "import sys\nopen(sys.argv[-1] + '.launched', 'w').write(' '.join(sys.argv[1:]))\n"


def _launched(hooks, wait):
    flag = Path(str(hooks.parent / "housekeep-session.txt") + ".launched")
    for _ in range(int(wait / 0.2)):
        if flag.exists():
            return True
        time.sleep(0.2)
    return flag.exists()


@needs_bash
def test_session_start_never_runs_the_script_beside_the_hooks(tmp_path):
    """Only $X4_TOOLKIT's x4housekeep. The fallback to <hooks>/../../scripts let every hook test with a fixture
    X4_TOOLKIT run the REPO's script on the real temp dir and the real Recycle Bin (2026-10-20): under unittest or a
    shell harness PYTEST_CURRENT_TEST does not exist, so X4_SWEEP_IN_TESTS=1 below reproduces that condition."""
    hooks = _hook_tree(tmp_path)
    (tmp_path / "proj" / "scripts").mkdir()
    (tmp_path / "proj" / "scripts" / "x4housekeep.py").write_text(_MARKER, encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tmp_path / "fixture-tk"), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"),
               X4_SWEEP_IN_TESTS="1")
    env.pop("X4_HOUSEKEEP_SWEEP", None)
    _run_hook(hooks, env)
    assert not _launched(hooks, 2), "the script beside the hooks was launched"
    env["X4_TOOLKIT"] = str(tmp_path / "proj")                       # twin: the same script, named by X4_TOOLKIT
    _run_hook(hooks, env)
    assert _launched(hooks, 10)


@needs_bash
def test_session_start_never_sweeps_when_the_harness_says_off(tmp_path):
    hooks = _hook_tree(tmp_path)
    (tmp_path / "proj" / "scripts").mkdir()
    (tmp_path / "proj" / "scripts" / "x4housekeep.py").write_text(_MARKER, encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tmp_path / "proj"), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"),
               X4_SWEEP_IN_TESTS="1", X4_HOUSEKEEP_SWEEP="off")
    _run_hook(hooks, env)
    assert not _launched(hooks, 2), "X4_HOUSEKEEP_SWEEP=off did not stop the launch"
    env.pop("X4_HOUSEKEEP_SWEEP")                                    # twin: without the opt-out it launches
    _run_hook(hooks, env)
    assert _launched(hooks, 10)


def test_every_hook_harness_sets_the_sweep_opt_out():
    """Tripwire (a substring READ, not a measurement): each non-pytest runner of session-canary.sh carries the opt-out."""
    for rel in ("agent/guards/claude-hooks/test_audit0924_hooks.py", "scripts/test-hooks.sh"):
        assert "X4_HOUSEKEEP_SWEEP" in (REPO / rel).read_text(encoding="utf-8"), rel

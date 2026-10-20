r"""`scripts/x4doctor.py` -- per-agent guard health that can never say OK about nothing.

WHY. Codex skips a hook it has not reviewed SILENTLY, a WSL bash stub makes a guard fail
open, and a Store `python` stub resolves and then cannot run -- each of these leaves an
install that LOOKS guarded. x4doctor answers "are the guards live HERE?" per agent target,
and its contract is the point of this file: every row is OK, FAIL, UNKNOWN or N/A; a run
in which nothing answered is exit 2, never 0; a check that raises is UNKNOWN with the
exception named, never a dropped row.

Every FAIL clause has a twin that must be OK, so a check that denies everything cannot
read as a working check (CLAUDE.md #26).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCTOR = REPO / "scripts" / "x4doctor.py"
_spec = importlib.util.spec_from_file_location("x4doctor", DOCTOR)
doc = importlib.util.module_from_spec(_spec)
sys.modules["x4doctor"] = doc
_spec.loader.exec_module(doc)


def _run(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(DOCTOR), *args], capture_output=True, text=True,
                          timeout=300, env=env, cwd=cwd)


# --- Task 5: the result model and the exit contract ----------------------------------- #

def test_no_agent_target_at_root_is_exit_2_never_ok(tmp_path):
    r = _run("--root", str(tmp_path))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "OK" not in r.stdout.split()


def test_summarise_all_UNKNOWN_is_not_ok():
    assert doc.exit_code([doc.Check("a", "claude", doc.UNKNOWN, "x")]) == 2
    rows = [doc.Check("a", "claude", doc.OK, ""), doc.Check("b", "claude", doc.UNKNOWN, "x")]
    assert doc.exit_code(rows) == 3


def test_summarise_zero_answered_is_2():
    assert doc.exit_code([]) == 2
    assert doc.exit_code([doc.Check("a", "codex", doc.NA, "not installed")]) == 2


def test_any_FAIL_wins_over_OK():
    rows = [doc.Check("a", "claude", doc.OK, ""), doc.Check("b", "claude", doc.FAIL, "")]
    assert doc.exit_code(rows) == 1


def test_C2_only_user_steps_pending_is_exit_4():
    rows = [doc.Check("a", "claude", doc.OK, ""), doc.Check("b", "codex", doc.TODO, "trust it")]
    assert doc.exit_code(rows) == 4


def test_C2_a_TODO_never_masks_a_FAIL():
    rows = [doc.Check("a", "codex", doc.TODO, ""), doc.Check("b", "claude", doc.FAIL, "")]
    assert doc.exit_code(rows) == 1


def test_C2_an_UNKNOWN_outranks_a_TODO():
    rows = [doc.Check("a", "codex", doc.TODO, ""), doc.Check("b", "claude", doc.UNKNOWN, "")]
    assert doc.exit_code(rows) == 3


def test_C2_a_TODO_counts_as_answered():
    assert doc.exit_code([doc.Check("a", "codex", doc.TODO, "")]) == 4


def test_C2_exit_4_is_documented_in_help():
    r = _run("--help")
    assert "4" in r.stdout and "TODO" in r.stdout, r.stdout


def test_TWIN_all_OK_is_0():
    assert doc.exit_code([doc.Check("a", "claude", doc.OK, ""), doc.Check("b", "codex", doc.NA, "")]) == 0


def test_a_check_that_RAISES_becomes_UNKNOWN_with_the_exception_named():
    def boom(ctx):
        raise RuntimeError("kaput")
    row = doc.run_check("boom", "claude", boom, ctx=None)
    assert row.status == doc.UNKNOWN and "kaput" in row.detail and "RuntimeError" in row.detail


def test_a_check_returning_an_unknown_STATUS_is_UNKNOWN_not_trusted():
    row = doc.run_check("odd", "claude", lambda ctx: ("GREEN", "looks fine"), ctx=None)
    assert row.status == doc.UNKNOWN and "GREEN" in row.detail


def test_json_output_is_one_parseable_document(tmp_path):
    r = _run("--root", str(tmp_path), "--json")
    d = json.loads(r.stdout)
    assert d["v"] == 1 and isinstance(d["checks"], list) and d["root"]
    assert set(d["targets"]) == {"claude", "codex", "generic", "opencode"}
    assert d["exit"] == r.returncode == 2


def test_detect_targets(tmp_path):
    assert doc.detect_targets(tmp_path) == {"claude": False, "codex": False, "generic": False,
                                            "opencode": False}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".codex" / "hooks").mkdir(parents=True)
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    assert doc.detect_targets(tmp_path) == {"claude": True, "codex": True, "generic": True,
                                            "opencode": False}


def test_TWIN_a_users_own_AGENTS_md_alone_is_NOT_the_generic_target(tmp_path):
    """MEASURED on the author's game root: a hand-written AGENTS.md read as 'generic
    installed'. The toolkit's generic target is its skills payload; the file is reported
    by the instructions row instead."""
    (tmp_path / "AGENTS.md").write_text("my notes\n", encoding="utf-8")
    assert doc.detect_targets(tmp_path)["generic"] is False


def test_TWIN_a_bare_claude_dir_holding_only_the_path_config_is_NOT_the_claude_target(tmp_path):
    """A 3.x install wrote .claude/x4-paths.env for every agent -- a Codex-only one included --
    and 4.x still reads it there (deprecated), so a bare .claude/ is still no Claude signal."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "x4-paths.env").write_text("X4_TOOLKIT=x\n", encoding="utf-8")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("#\n", encoding="utf-8")
    assert doc.detect_targets(tmp_path) == {"claude": False, "codex": False, "generic": False,
                                            "opencode": False}


def test_the_verdict_line_comes_FIRST(tmp_path):
    """CLAUDE.md #38: a truncated report keeps its head, so the verdict goes there."""
    r = _run("--root", str(tmp_path))
    assert r.stdout.splitlines()[0].startswith("x4doctor:"), r.stdout[:300]


def test_an_unknown_agent_filter_is_a_usage_error(tmp_path):
    r = _run("--root", str(tmp_path), "--agent", "nonsense")
    assert r.returncode == 2 and "unknown --agent" in r.stdout, r.stdout


def test_TWIN_opencode_is_a_known_agent_filter(tmp_path):
    """Plan 3 lane L: OpenCode is a target (best effort). Nothing installed here, so exit 2
    still -- but for 'nothing answered', never for a usage error."""
    r = _run("--root", str(tmp_path), "--agent", "opencode")
    assert "unknown --agent" not in r.stdout, r.stdout


def test_the_doctor_is_stdlib_only():
    """It must run when uv or the venv is the thing that is broken."""
    import ast
    tree = ast.parse(DOCTOR.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    assert mods <= stdlib, sorted(mods - stdlib)


# --- Task 6: toolchain and roots, as the GUARDS resolve them ------------------------- #
#
# The fixture copies the repo's generated .claude/hooks into a sandbox root so the REAL
# _x4-env.sh and x4guard.py run, with a path config pointing at sandbox dirs.

_LEAKY = ("X4_TOOLKIT", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_MODS", "X4_PYTHON",
          "X4_CONFIG", "X4_BASH", "CLAUDE_PROJECT_DIR", "X4_GUARD", "JQ", "CODEX_HOME")


class Sandbox:
    def __init__(self, root, game, ref, codex_home):
        self.root, self.game, self.ref, self.codex_home = root, game, ref, codex_home

    def ctx(self, **kw):
        # The sandbox models an INSTALLED toolkit at the root, so the doctor acts for it (B2:
        # a doctor acts for the toolkit it lives in; this one lives in the checkout).
        kw.setdefault("toolkit", self.root)
        return doc.Ctx(root=self.root, **kw)

    def rows(self, fn):
        return {r.id: r for r in fn(self.ctx())}


def _env_file(path: Path, **kv):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join('%s="%s"\n' % (k, Path(v).as_posix()) for k, v in kv.items()),
                    encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "root"
    game = tmp_path / "game"
    ref = root / "reference"
    for d in (game / "extensions", ref / "libraries"):
        d.mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text("<wares/>\n", encoding="utf-8")
    # A FINISHED unpack (FX-B3): the sentinel is what x4refguard apply demands, and the
    # layer2 rows below test the apply hint. The no-sentinel case has tests of its own.
    (ref / ".unpacked-and-locked").write_text("buildid 1\n", encoding="utf-8")
    shutil.copytree(REPO / ".claude" / "hooks", root / ".claude" / "hooks",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copy2(REPO / ".claude" / "settings.json", root / ".claude" / "settings.json")
    from codex_testlib import copy_shared_runtime
    copy_shared_runtime(root)
    _env_file(root / "x4-paths.env", X4_TOOLKIT=root, X4_GAME=game, X4_REFERENCE=ref)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return Sandbox(root, game, ref, codex_home)


def _write_failing_exe(path: Path) -> None:
    """Resolves (bash `command -v` finds it) and cannot run: exits 9, prints nothing --
    the Store-stub shape."""
    path.write_bytes(b"#!/bin/sh\nexit 9\n")
    path.chmod(0o755)


def _stub_first(tmp_path, monkeypatch):
    stub = tmp_path / "Windows" / "System32"
    stub.mkdir(parents=True)
    (stub / "bash.exe").write_bytes(b"MZ")              # resolves; cannot run anything
    monkeypatch.setenv("PATH", str(stub) + os.pathsep + os.environ["PATH"])
    return stub


# R2-B2 (second install red-team, 2026-10-04): bash.path FAILED on every stock Windows PATH
# (System32's WSL stub comes first) although no installed hook ever starts bash from that
# PATH: Claude Code runs its hook commands in Git Bash (whose own PATH puts /usr/bin first --
# MEASURED: this machine's PowerShell PATH has the stub first and its `bash "..."` hooks run),
# Codex on Windows uses `commandWindows` (PowerShell), and every guard resolves Git Bash itself
# (x4guard.resolve_bash). The row now judges what the HOOKS would run. FAIL needs one of:
# a hook that WOULD start bash from the Windows PATH, or no Git Bash the resolver can find.

@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_a_stub_first_on_PATH_is_OK_when_no_hook_starts_bash_from_PATH(sandbox, tmp_path,
                                                                            monkeypatch):
    _stub_first(tmp_path, monkeypatch)
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["bash.path"].status == doc.OK, rows["bash.path"]
    d = rows["bash.path"].detail
    assert "System32" in d and "informational" in d, d     # the stub is still NAMED


def _codex_hooks(root: Path, windows: str | None):
    h = {"type": "command", "command": 'bash "x/.codex/hooks/codex-entry.sh" pre_tool_use'}
    if windows is not None:
        h["commandWindows"] = windows
    (root / ".codex" / "hooks").mkdir(parents=True, exist_ok=True)
    (root / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [h]}]}}), encoding="utf-8")


_ALL_TARGETS = {"claude": True, "codex": True, "generic": False, "opencode": False}


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_a_codex_hook_with_NO_windows_command_and_a_stub_first_is_FAIL(sandbox, tmp_path,
                                                                           monkeypatch):
    """Codex runs `command` through cmd.exe when there is no `commandWindows`: bare `bash`
    is then the stub, the hook fails, and Codex runs the tool call (fails OPEN)."""
    _stub_first(tmp_path, monkeypatch)
    _codex_hooks(sandbox.root, None)
    row = {r.id: r for r in doc.check_toolchain(sandbox.ctx(targets=_ALL_TARGETS))}["bash.path"]
    assert row.status == doc.FAIL, row
    assert "System32" in row.detail and "codex" in row.detail.lower(), row.detail
    assert "install" in row.detail, row.detail          # the fix: re-run the installer


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_TWIN_the_same_codex_hook_WITH_a_powershell_windows_command_is_OK(sandbox, tmp_path,
                                                                             monkeypatch):
    _stub_first(tmp_path, monkeypatch)
    _codex_hooks(sandbox.root, "pwsh -NoProfile -File x/codex-entry.ps1 pre_tool_use")
    row = {r.id: r for r in doc.check_toolchain(sandbox.ctx(targets=_ALL_TARGETS))}["bash.path"]
    assert row.status == doc.OK, row


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_TWIN_a_bare_bash_hook_with_GIT_BASH_first_on_PATH_is_OK(sandbox, tmp_path,
                                                                    monkeypatch):
    """The other clause: the same bare-bash hook is fine when PATH's first bash is not a stub."""
    gb = doc.guard_bash(sandbox.ctx())[0]
    assert gb and not doc._is_stub(gb), gb
    monkeypatch.setenv("PATH", str(Path(gb).parent) + os.pathsep + os.environ["PATH"])
    _codex_hooks(sandbox.root, None)
    row = {r.id: r for r in doc.check_toolchain(sandbox.ctx(targets=_ALL_TARGETS))}["bash.path"]
    assert row.status == doc.OK, row


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_a_codex_windows_command_that_IS_bare_bash_is_FAIL(sandbox, tmp_path, monkeypatch):
    _stub_first(tmp_path, monkeypatch)
    _codex_hooks(sandbox.root, 'bash "x/codex-entry.sh" pre_tool_use')
    row = {r.id: r for r in doc.check_toolchain(sandbox.ctx(targets=_ALL_TARGETS))}["bash.path"]
    assert row.status == doc.FAIL, row


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_a_claude_hook_run_by_POWERSHELL_that_starts_bare_bash_is_FAIL(sandbox, tmp_path,
                                                                           monkeypatch):
    _stub_first(tmp_path, monkeypatch)
    s = sandbox.root / ".claude" / "settings.json"
    d = json.loads(s.read_text(encoding="utf-8"))
    ev = next(iter(d["hooks"]))
    d["hooks"][ev][0]["hooks"][0]["shell"] = "powershell"
    s.write_text(json.dumps(d), encoding="utf-8")
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["bash.path"].status == doc.FAIL, rows["bash.path"]
    assert "claude" in rows["bash.path"].detail.lower(), rows["bash.path"].detail


@pytest.mark.skipif(os.name != "nt", reason="the WSL-stub trap is Windows-only")
def test_R2B2_a_stub_first_and_NO_git_bash_resolvable_is_FAIL_with_the_setx_line(sandbox, tmp_path,
                                                                                monkeypatch):
    _stub_first(tmp_path, monkeypatch)
    monkeypatch.setenv("X4_BASH", str(tmp_path / "no-such-bash.exe"))
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["bash.path"].status == doc.FAIL, rows["bash.path"]
    assert "setx X4_BASH" in rows["bash.path"].detail, rows["bash.path"].detail


def test_TWIN_the_real_bash_is_OK(sandbox):
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["bash.guards"].status == doc.OK, rows["bash.guards"]
    if os.name == "nt":
        assert rows["bash.path"].status in (doc.OK, doc.FAIL)   # the developer's PATH decides
    else:
        assert rows["bash.path"].status == doc.NA


def test_a_python_that_RESOLVES_but_FAILS_is_FAIL_not_OK(sandbox, tmp_path, monkeypatch):
    bad = tmp_path / "badpy"
    bad.mkdir()
    _write_failing_exe(bad / "python")
    monkeypatch.setenv("X4_PYTHON", (bad / "python").as_posix())
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["python.guards"].status == doc.FAIL, rows["python.guards"]
    assert "does not run" in rows["python.guards"].detail


def test_an_UNRESOLVABLE_X4_PYTHON_is_FAIL_naming_the_variable(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("X4_PYTHON", (tmp_path / "no-such-python").as_posix())
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["python.guards"].status == doc.FAIL and "X4_PYTHON" in rows["python.guards"].detail


def test_TWIN_a_working_python_is_OK(sandbox):
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["python.guards"].status == doc.OK, rows["python.guards"]


def test_a_MISSING_jq_with_python_is_OK_degraded_and_says_so(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("JQ", (tmp_path / "no-such-jq").as_posix())
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["jq"].status == doc.OK and "fall back" in rows["jq"].detail, rows["jq"]


def test_a_MISSING_jq_AND_python_is_FAIL(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("JQ", (tmp_path / "no-such-jq").as_posix())
    monkeypatch.setenv("X4_PYTHON", (tmp_path / "no-such-python").as_posix())
    rows = sandbox.rows(doc.check_toolchain)
    assert rows["jq"].status == doc.FAIL, rows["jq"]


def _as_3x_guard(sandbox) -> None:
    """Make the sandbox's guard copy a 3.x one: it reads only `.claude/x4-paths.env`."""
    f = sandbox.root / ".claude" / "hooks" / "_x4-env.sh"
    src = f.read_text(encoding="utf-8")
    new_branch = 'elif [ -f "$X4_TOOLKIT/x4-paths.env" ]; then'
    assert src.count(new_branch) == 1
    f.write_text(src.replace(new_branch, "elif false; then"), encoding="utf-8", newline="\n")


def test_bash_and_python_DISAGREEING_on_reference_is_FAIL(sandbox, tmp_path):
    """An OLDER guard copy reads the 3.x `.claude/x4-paths.env` while the tools read the 4.x
    root file: the two then protect and read different trees. (Until Plan 3 lane I the split
    came from X4_CONFIG, which only bash honoured -- see the twin below.)"""
    other = tmp_path / "other-ref"
    other.mkdir()
    _env_file(sandbox.root / ".claude" / "x4-paths.env", X4_TOOLKIT=sandbox.root,
              X4_GAME=sandbox.game, X4_REFERENCE=other)
    _as_3x_guard(sandbox)
    rows = sandbox.rows(doc.check_roots)
    r = rows["roots.agree"]
    assert r.status == doc.FAIL, r
    assert "other-ref" in r.detail and "reference" in r.detail


def test_TWIN_X4_CONFIG_now_moves_BOTH_sides(sandbox, tmp_path, monkeypatch):
    """Plan 3 lane I: Python honours X4_CONFIG too, so it no longer splits the two."""
    other = tmp_path / "other-ref"
    other.mkdir()
    _env_file(tmp_path / "elsewhere.env", X4_TOOLKIT=sandbox.root, X4_GAME=sandbox.game,
              X4_REFERENCE=other)
    monkeypatch.setenv("X4_CONFIG", (tmp_path / "elsewhere.env").as_posix())
    r = sandbox.rows(doc.check_roots)["roots.agree"]
    assert r.status == doc.OK and "other-ref" not in r.detail.split("guards read")[0], r


def test_TWIN_agreeing_roots_are_OK(sandbox):
    rows = sandbox.rows(doc.check_roots)
    assert rows["roots.agree"].status == doc.OK, rows["roots.agree"]
    assert rows["roots.reference"].status == doc.OK, rows["roots.reference"]
    assert rows["roots.game"].status == doc.OK, rows["roots.game"]


def test_an_UNSET_game_root_is_FAIL(sandbox):
    _env_file(sandbox.root / "x4-paths.env", X4_TOOLKIT=sandbox.root,
              X4_REFERENCE=sandbox.ref)
    rows = sandbox.rows(doc.check_roots)
    assert rows["roots.game"].status == doc.FAIL, rows["roots.game"]


def test_a_reference_not_unpacked_YET_is_the_users_STEP_not_FAIL_nor_OK(sandbox, tmp_path):
    """FX-B3: was UNKNOWN (exit 3, "can't tell"). A fresh install before its unpack step is a
    KNOWN state: TODO (exit 4), naming the unpack command."""
    # FX-B4: a FRESH install has no finished unpack anywhere -- the fixture's own finished tree
    # at <root>/reference would now (correctly) read as a MOVED tree.
    shutil.rmtree(sandbox.ref)
    _env_file(sandbox.root / "x4-paths.env", X4_TOOLKIT=sandbox.root,
              X4_GAME=sandbox.game, X4_REFERENCE=tmp_path / "not-yet")
    rows = sandbox.rows(doc.check_roots)
    assert rows["roots.reference"].status == doc.TODO, rows["roots.reference"]
    assert "unpack-reference.sh" in rows["roots.reference"].detail, rows["roots.reference"]


def test_the_roots_rows_NAME_the_config_file_each_side_read(sandbox):
    rows = sandbox.rows(doc.check_roots)
    assert "x4-paths.env" in rows["roots.agree"].detail


def test_the_guards_config_variable_still_exists():
    """x4doctor reads `_x4_cfg` (an internal name) to say which config the guards read.
    If _x4-env.sh renames it, this goes red instead of the doctor going quiet."""
    from _layout import require_repo
    require_repo("agent/guards/claude-hooks/_x4-env.sh", why="the guard loader SOURCE")
    src = (REPO / "agent" / "guards" / "claude-hooks" / "_x4-env.sh").read_text(encoding="utf-8")
    assert "_x4_cfg=" in src and "x4_resolve_python()" in src and "X4_PY=" in src
    assert "_x4_cfg_src=" in src and "_x4_ref_defaulted=" in src


def test_a_GENERIC_only_root_has_no_guard_toolchain_and_says_so(tmp_path, monkeypatch):
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    rows = {r.id: r for r in doc.check_toolchain(doc.Ctx(root=tmp_path, toolkit=tmp_path))}
    assert {r.status for r in rows.values()} == {doc.NA}, rows


def test_two_spellings_of_ONE_git_install_agree(monkeypatch):
    """MEASURED on the author's machine: x4guard resolves Git/usr/bin/bash.exe (PATH),
    scripts/gitbash.py resolves Git/bin/bash.exe (its launcher). One install, one bash.
    The launcher relationship is a fact about Git for WINDOWS, so the host is pinned to
    Windows here (CI ubuntu ran this unpinned and failed, run 37091872873); the twin below
    pins the other side of the `_on_windows()` clause."""
    monkeypatch.setattr(doc, "_on_windows", lambda: True)
    assert doc.same_bash("C:/Program Files/Git/usr/bin/bash.exe", "C:/Program Files/Git/bin/bash.exe")
    assert doc.same_bash("/usr/bin/bash", "/usr/bin/bash")


def test_TWIN_off_Windows_the_launcher_spelling_is_two_paths(monkeypatch):
    """`/usr/bin/bash` and `/bin/bash` are NOT one install in general on POSIX."""
    monkeypatch.setattr(doc, "_on_windows", lambda: False)
    assert not doc.same_bash("C:/Program Files/Git/usr/bin/bash.exe", "C:/Program Files/Git/bin/bash.exe")
    assert not doc.same_bash("/usr/bin/bash", "/bin/bash")
    assert doc.same_bash("/usr/bin/bash", "/usr/bin/bash")


def test_TWIN_two_DIFFERENT_git_installs_do_not_agree():
    assert not doc.same_bash("C:/Program Files/Git/usr/bin/bash.exe", "D:/PortableGit/bin/bash.exe")
    assert not doc.same_bash("/usr/bin/bash", "/opt/homebrew/bin/bash")


# --- Task 7: deployed-vs-source parity per agent target ------------------------------- #

GEN_BANNER = "<!-- GENERATED from agent/ -->"


def _tree(base: Path, files: dict) -> Path:
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return base


def _pair(tmp_path, monkeypatch, src: dict, dst: dict) -> doc.Ctx:
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)
    tk = _tree(tmp_path / "tk", src)
    root = _tree(tmp_path / "root", dst)
    monkeypatch.setenv("X4_TOOLKIT", str(tk))
    return doc.Ctx(root=root, toolkit=tk)


_CLAUDE = {".claude/settings.json": "{}\n", ".claude/hooks/a.sh": "echo a\n",
           "CLAUDE.md": "# C\n" + GEN_BANNER + "\n"}


def _prow(ctx, cid):
    rows = {r.id: r for r in doc.check_parity(ctx)}
    return rows[cid]


def test_parity_identical_is_OK_with_a_count(tmp_path, monkeypatch):
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, _CLAUDE), "parity.claude")
    assert r.status == doc.OK and "2 file" in r.detail, r


def test_parity_DRIFT_is_FAIL_naming_the_file(tmp_path, monkeypatch):
    dst = dict(_CLAUDE, **{".claude/hooks/a.sh": "echo EDITED\n"})
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, dst), "parity.claude")
    assert r.status == doc.FAIL and "hooks/a.sh" in r.detail, r


def test_parity_a_CRLF_only_difference_is_not_drift(tmp_path, monkeypatch):
    dst = dict(_CLAUDE, **{".claude/hooks/a.sh": b"echo a\r\n"})
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, dst), "parity.claude")
    assert r.status == doc.OK, r


def test_parity_with_NO_source_is_UNKNOWN(tmp_path, monkeypatch):
    ctx = _pair(tmp_path, monkeypatch, _CLAUDE, _CLAUDE)
    monkeypatch.delenv("X4_TOOLKIT")
    ctx = doc.Ctx(root=ctx.root, toolkit=None)      # a doctor outside any toolkit, no env
    r = _prow(ctx, "parity.claude")
    assert r.status == doc.UNKNOWN and "X4_TOOLKIT" in r.detail, r


#: What makes a root an INSTALLED toolkit: the runtime the installers copy, no agent/ source.
_RUNTIME = {"scripts/x4doctor.py": "#\n", "tools/x4validate/x4validate/_paths.py": "#\n"}


def test_same_tree_without_source_is_NA_never_UNKNOWN(tmp_path, monkeypatch):
    """R4-2 (v4.0.0 review): an installed toolkit IS the toolkit, and runtime-only (decision
    #9). Parity is a DEPLOY-workflow check (deploy-claude-dir.py), and nothing was deployed
    into an installed root -- so the row is N/A with that reason. As UNKNOWN it made a healthy
    fresh install exit 3 forever: x4doctor could never say OK on any installed root."""
    files = dict(_CLAUDE, **_RUNTIME)
    ctx = _pair(tmp_path, monkeypatch, files, files)
    monkeypatch.setenv("X4_TOOLKIT", str(ctx.root))
    r = _prow(doc.Ctx(root=ctx.root, toolkit=ctx.root), "parity.claude")
    assert r.status == doc.NA and "deploy" in r.detail and "agent/" in r.detail, r


def test_an_installed_root_with_X4_TOOLKIT_UNSET_is_NA_too(tmp_path, monkeypatch):
    """`install --no-env` leaves X4_TOOLKIT unset; the root still IS the installed toolkit."""
    files = dict(_CLAUDE, **_RUNTIME)
    ctx = _pair(tmp_path, monkeypatch, files, files)
    monkeypatch.delenv("X4_TOOLKIT")
    r = _prow(doc.Ctx(root=ctx.root, toolkit=None), "parity.claude")
    assert r.status == doc.NA and "deploy" in r.detail, r


def _git_commit_all(root):
    import subprocess
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "fixture"]):
        r = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True)
        assert r.returncode == 0, (argv, r.stderr)
    return root


_SOURCE = {"agent/core.md": "#\n", "tools/x4validate/scripts/gen-agent-trees.py": "#\n"}


def test_an_IN_PLACE_install_of_the_release_zip_is_the_INSTALLED_layout(tmp_path, monkeypatch):
    """FX-H2 / F269, user decision 2026-10-10: the release zip carries agent/ and the generator
    but no .git, and the README installs it IN PLACE. That is an installed toolkit (N/A), not a
    source checkout whose generator must be run."""
    files = dict(_CLAUDE, **_RUNTIME, **_SOURCE)
    ctx = _pair(tmp_path, monkeypatch, files, files)
    monkeypatch.setenv("X4_TOOLKIT", str(ctx.root))
    r = _prow(doc.Ctx(root=ctx.root, toolkit=ctx.root), "parity.claude")
    assert r.status == doc.NA and "deploy" in r.detail, r


def test_TWIN_a_toolkit_root_WITH_an_agent_source_is_still_checked(tmp_path, monkeypatch):
    """A source CHECKOUT (a .git whose HEAD tracks agent/, + the generator) keeps the
    gen-agent-trees --check path: without its venv that is UNKNOWN, never the installed N/A."""
    files = dict(_CLAUDE, **_RUNTIME, **_SOURCE)
    ctx = _pair(tmp_path, monkeypatch, files, files)
    _git_commit_all(ctx.root)
    monkeypatch.setenv("X4_TOOLKIT", str(ctx.root))
    r = _prow(doc.Ctx(root=ctx.root, toolkit=ctx.root), "parity.claude")
    assert r.status == doc.UNKNOWN and "venv" in r.detail, r


def test_a_codex_target_with_no_deploy_workflow_is_NA_with_the_reason(tmp_path, monkeypatch):
    """R4-2: deploy_parity defines only the Claude deployment; .codex/ reaches a root only
    through the installer (deploy-claude-dir.py deploys only the defined targets). With no TargetSpec there
    is no deploy relationship to check -- N/A naming why, never an UNKNOWN that blocks exit 0."""
    files = dict(_CLAUDE, **{".codex/hooks/x.sh": "#\n", "AGENTS.md": GEN_BANNER + "\n"})
    ctx = _pair(tmp_path, monkeypatch, files, files)
    monkeypatch.setattr(doc, "_parity_targets", lambda mod: {"claude": mod.TARGETS["claude"]})
    r = _prow(ctx, "parity.codex")
    assert r.status == doc.NA and "deploy" in r.detail and ".codex" in r.detail, r


def test_TWIN_a_codex_TargetSpec_when_one_exists_is_compared(tmp_path, monkeypatch):
    files = dict(_CLAUDE, **{".codex/hooks/x.sh": "#\n", "AGENTS.md": GEN_BANNER + "\n"})
    ctx = _pair(tmp_path, monkeypatch, files, files)

    def with_codex(mod):
        c = mod.TARGETS["claude"]
        return {"claude": c, "codex": mod.TargetSpec("codex", ".codex", (), ("hooks",), ())}
    monkeypatch.setattr(doc, "_parity_targets", with_codex)
    r = _prow(ctx, "parity.codex")
    assert r.status == doc.OK and "1 file" in r.detail, r


def test_an_absent_target_is_NA(tmp_path, monkeypatch):
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, _CLAUDE), "parity.codex")
    assert r.status == doc.NA, r


def test_a_claude_target_WITHOUT_CLAUDE_md_is_FAIL(tmp_path, monkeypatch):
    """Claude Code reads AGENTS.md only when there is no CLAUDE.md (MEASURED, lane A):
    the Claude target without its file runs on the wrong instructions."""
    dst = {k: v for k, v in _CLAUDE.items() if k != "CLAUDE.md"}
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, dst), "instructions.claude")
    assert r.status == doc.FAIL, r


def test_TWIN_a_claude_target_WITH_CLAUDE_md_is_OK(tmp_path, monkeypatch):
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, _CLAUDE), "instructions.claude")
    assert r.status == doc.OK, r


def test_a_codex_target_reading_a_HAND_WRITTEN_AGENTS_md_is_FAIL(tmp_path, monkeypatch):
    files = dict(_CLAUDE, **{".codex/hooks/x.sh": "#\n", "AGENTS.md": "# my own notes\n"})
    r = _prow(_pair(tmp_path, monkeypatch, files, files), "instructions.codex")
    assert r.status == doc.FAIL and "GENERATED" in r.detail, r


def test_TWIN_a_codex_target_reading_the_GENERATED_AGENTS_md_is_OK(tmp_path, monkeypatch):
    files = dict(_CLAUDE, **{".codex/hooks/x.sh": "#\n", "AGENTS.md": "# A\n" + GEN_BANNER + "\n"})
    r = _prow(_pair(tmp_path, monkeypatch, files, files), "instructions.codex")
    assert r.status == doc.OK, r


def test_a_hand_written_AGENTS_md_with_NO_codex_target_is_NOTED_not_failed(tmp_path, monkeypatch):
    """The author's game root today: a personal AGENTS.md, Codex not installed. Any Codex
    session there reads it, so it is NAMED -- but it is the user's file, not a failure."""
    dst = dict(_CLAUDE, **{"AGENTS.md": "# my own notes\n"})
    r = _prow(_pair(tmp_path, monkeypatch, _CLAUDE, dst), "instructions.agents_md")
    assert r.status == doc.OK and "hand-written" in r.detail, r


def test_a_source_whose_comparer_PREDATES_TargetSpec_falls_back_to_the_doctors_own(tmp_path, monkeypatch):
    """MEASURED 2026-10-02: X4_TOOLKIT named a checkout whose deploy_parity.py had no
    TARGETS yet, and every row read 'no TargetSpec for claude' -- true of that file, false
    of the question. The comparer is code; the SOURCE tree is still X4_TOOLKIT's."""
    files = dict(_CLAUDE, **{"tools/x4validate/gates/deploy_parity.py": "# an old gate, no TARGETS\n"})
    ctx = _pair(tmp_path, monkeypatch, files, _CLAUDE)
    r = _prow(ctx, "parity.claude")
    assert r.status == doc.OK and "2 file" in r.detail, r


# --- roots.config: the guards read NO config file -------------------------------------- #

def test_roots_config_FAILS_with_no_config_and_no_exported_reference(sandbox):
    """MEASURED 2026-10-02 on the author's machine, check-only: with X4_TOOLKIT unset, the
    game root's guards derive the toolkit from CLAUDE_PROJECT_DIR, find no x4-paths.env
    there, default the reference to <game>/reference -- and ALLOW a write and a delete
    into the configured reference (4 of 4 deny controls became allow)."""
    (sandbox.root / "x4-paths.env").unlink()
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.FAIL, r
    d = r.detail.replace("\\", "/")
    assert "/x4-paths.env" in d and ".claude/x4-paths.env" in d and "ASSUME" in d, r


def test_TWIN_the_guards_reading_their_config_is_OK(sandbox):
    rows = sandbox.rows(doc.check_roots)
    assert rows["roots.config"].status == doc.OK, rows["roots.config"]


def test_roots_config_OK_names_the_new_file(sandbox):
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.OK and "x4-paths.env" in r.detail, r
    assert ".claude" not in r.detail and "DEPRECATED" not in r.detail, r


def test_roots_config_legacy_is_OK_but_says_DEPRECATED(sandbox):
    (sandbox.root / "x4-paths.env").rename(sandbox.root / ".claude" / "x4-paths.env")
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.OK and "DEPRECATED" in r.detail and "x4config.py migrate" in r.detail, r


def test_roots_config_FAILS_when_two_copies_DIFFER(sandbox, tmp_path):
    _env_file(sandbox.root / ".claude" / "x4-paths.env", X4_TOOLKIT=sandbox.root,
              X4_GAME=sandbox.game, X4_REFERENCE=tmp_path / "other-ref")
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.FAIL and "X4_REFERENCE" in r.detail, r
    assert "other-ref" not in r.detail, "a VALUE was printed: " + r.detail


def test_roots_config_two_AGREEING_copies_are_OK(sandbox):
    (sandbox.root / ".claude" / "x4-paths.env").write_bytes((sandbox.root / "x4-paths.env").read_bytes())
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.OK and "x4config.py" in r.detail, r


#: FX-B2 (reviewer C I1): a config line the grammar REFUSES was dropped SILENTLY. One line
#: per reason, each a twin of the others: the row must FAIL naming line:reason, never the value.
_IGNORED_LINES = [
    ("operator", 'X4_MODS=C:/mods/a&b'),
    ("subst", 'X4_MODS=$(echo SECRETVALUE)'),
    ("shape", 'this is not an assignment SECRETVALUE'),
]


@pytest.mark.parametrize("reason,line", _IGNORED_LINES, ids=[r for r, _l in _IGNORED_LINES])
def test_roots_config_FAILS_on_a_line_every_loader_IGNORES(sandbox, reason, line):
    cfg = sandbox.root / "x4-paths.env"
    cfg.write_text(cfg.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    n = len(cfg.read_text(encoding="utf-8").splitlines())
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.FAIL and ("%d:%s" % (n, reason)) in r.detail, r
    assert "SECRETVALUE" not in r.detail and "a&b" not in r.detail, "a VALUE was printed: " + r.detail


@pytest.mark.parametrize("line", ['X4_MODS="C:/mods/a&b"', "OTHER_TOOL_SETTING=1"])
def test_TWIN_roots_config_a_QUOTED_operator_or_a_foreign_key_is_not_a_FAIL(sandbox, line):
    cfg = sandbox.root / "x4-paths.env"
    cfg.write_text(cfg.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == doc.OK, r


def test_roots_config_env_only_is_OK(sandbox):
    (sandbox.root / "x4-paths.env").unlink()
    env = {k: v for k, v in os.environ.items()}
    env.update(X4_REFERENCE=sandbox.ref.as_posix(), X4_GAME=sandbox.game.as_posix())
    rows = {r.id: r for r in doc.check_roots(sandbox.ctx(env=env))}
    r = rows["roots.config"]
    assert r.status == doc.OK and "exported environment" in r.detail, r


@pytest.mark.parametrize("exists", [True, False])
def test_roots_config_reads_an_OLD_guard_copy_without_CFGSRC(sandbox, monkeypatch, tmp_path, exists):
    """A 3.x deployed guard prints CFG (always the path it LOOKED for) and no CFGSRC: the
    pre-4.0 logic still gives it a verdict."""
    cfg = tmp_path / "old.env"
    if exists:
        cfg.write_text("X4_GAME=x\n", encoding="utf-8")
    probe = {"CFG": cfg.as_posix(), "CFGSRC": "", "PY": "python", "REFERENCE": "", "GAME": ""}
    monkeypatch.setattr(doc, "guard_probe", lambda ctx: (probe, ""))
    r = sandbox.rows(doc.check_roots)["roots.config"]
    assert r.status == (doc.OK if exists else doc.FAIL), r


# --- Task 8: guard self-test -- controls that MUST deny and controls that MUST allow -- #

def _sel(sandbox, target="claude"):
    rows = {r.id: r for r in doc.check_guards(sandbox.ctx())}
    return rows["guards.selftest." + target]


def _replace_guard(sandbox, name: str, body: str) -> None:
    (sandbox.root / ".claude" / "hooks" / name).write_bytes(body.encode("utf-8"))


def test_selftest_all_controls_hold_is_OK(sandbox):
    r = _sel(sandbox)
    assert r.status == doc.OK, r
    assert "6 control" in r.detail


def test_a_guard_that_ALLOWS_the_reference_write_is_FAIL(sandbox):
    _replace_guard(sandbox, "protect-files.sh", "#!/bin/bash\nexit 0\n")      # allows everything
    r = _sel(sandbox)
    assert r.status == doc.FAIL and "deny.write.ref" in r.detail, r
    assert "LET THROUGH" in r.detail


def test_a_guard_that_DENIES_everything_is_FAIL(sandbox):
    _replace_guard(sandbox, "protect-bash.sh",
                   "#!/bin/bash\nprintf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
                   "\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"x\"}}'\n")
    r = _sel(sandbox)
    assert r.status == doc.FAIL and "allow.bash.echo" in r.detail, r


def test_an_INERT_guard_is_FAIL_not_ok(sandbox, monkeypatch, tmp_path):
    monkeypatch.setenv("X4_PYTHON", (tmp_path / "no-such-python").as_posix())
    r = _sel(sandbox)
    assert r.status == doc.FAIL and "inert" in r.detail.lower(), r


def test_no_reference_resolved_makes_the_deny_controls_UNKNOWN(sandbox, monkeypatch):
    real = doc.guard_probe

    def no_ref(ctx):
        vals, why = real(ctx)
        return (dict(vals, REFERENCE=""), why) if vals else (vals, why)
    monkeypatch.setattr(doc, "guard_probe", no_ref)
    r = _sel(sandbox)
    assert r.status == doc.UNKNOWN and "deny" in r.detail, r


def test_the_selftest_never_CREATES_the_probe_path(sandbox):
    _sel(sandbox)
    assert not (sandbox.ref / doc.PROBE_NAME).exists()


def test_a_codex_guard_copy_is_tested_with_its_OWN_x4guard(sandbox):
    shutil.copytree(sandbox.root / ".claude" / "hooks", sandbox.root / ".codex" / "hooks")
    rows = {r.id: r for r in doc.check_guards(sandbox.ctx())}
    assert "guards.selftest.codex" in rows
    assert ".codex" in rows["guards.selftest.codex"].detail.replace(chr(92), "/")


# --- Task 9: per-agent liveness ------------------------------------------------------- #

_HOOKS_JSON = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "timeout": 30, "command": "bash s.sh"}]}],
    "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "timeout": 60, "command": "bash p.sh",
                                                "commandWindows": "pwsh -File p.ps1"}]}],
}}


@pytest.fixture
def codex_root(tmp_path, monkeypatch):
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "proj"
    (root / ".codex" / "hooks").mkdir(parents=True)
    (root / ".codex" / "rules").mkdir()
    (root / ".codex" / "rules" / "x4.rules").write_text("# rules\n", encoding="utf-8")
    (root / ".codex" / "hooks.json").write_text(json.dumps(_HOOKS_JSON), encoding="utf-8")
    (root / "AGENTS.md").write_text(GEN_BANNER + "\n", encoding="utf-8")
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(doc, "_codex_cmd", lambda: None)
    return root


def _codex(root):
    return {r.id: r for r in doc.check_codex(doc.Ctx(root=root, toolkit=root))}


def _cfg(root: Path, *, trusted=True, entries=None, extra="") -> None:
    exp = doc.expected_hooks(root / ".codex" / "hooks.json")
    lines = []
    if trusted:
        lines += ["[projects.'%s']" % str(root.resolve()).lower(), 'trust_level = "trusted"', ""]
    for key, val in (entries if entries is not None else exp).items():
        lines += ["[hooks.state.'%s']" % key]
        if val == "disabled":
            lines += ['trusted_hash = "%s"' % exp[key], "enabled = false"]
        else:
            lines += ['trusted_hash = "%s"' % val]
        lines += [""]
    home = Path(os.environ["CODEX_HOME"])
    (home / "config.toml").write_text("\n".join(lines) + extra, encoding="utf-8")


def test_no_codex_config_is_UNKNOWN_never_ok(codex_root):
    rows = _codex(codex_root)
    assert rows["codex.config"].status == doc.UNKNOWN
    assert rows["codex.trusted"].status == doc.UNKNOWN and rows["codex.reviewed"].status == doc.UNKNOWN


def test_untrusted_project_is_TODO_the_users_step(codex_root):
    """C2 (install red-team 2026-10-04): trusting the folder is the USER's step on a correct
    fresh install, never a defect -- TODO, which can never mask a real FAIL."""
    _cfg(codex_root, trusted=False)
    r = _codex(codex_root)["codex.trusted"]
    assert r.status == doc.TODO and "trust" in r.detail, r


def test_TWIN_trusted_lowercased_key_is_OK(codex_root):
    _cfg(codex_root)
    assert _codex(codex_root)["codex.trusted"].status == doc.OK


def test_trust_inherited_from_an_ANCESTOR_is_UNKNOWN(codex_root):
    home = Path(os.environ["CODEX_HOME"])
    (home / "config.toml").write_text(
        "[projects.'%s']\ntrust_level = \"trusted\"\n" % str(codex_root.parent.resolve()).lower(), encoding="utf-8")
    assert _codex(codex_root)["codex.trusted"].status == doc.UNKNOWN


def test_all_hooks_reviewed_is_OK(codex_root):
    _cfg(codex_root)
    r = _codex(codex_root)["codex.reviewed"]
    assert r.status == doc.OK and "2 of 2" in r.detail, r


def test_an_UNREVIEWED_hook_is_FAIL_naming_the_silent_skip(codex_root):
    exp = doc.expected_hooks(codex_root / ".codex" / "hooks.json")
    first = sorted(exp)[0]
    _cfg(codex_root, entries={first: exp[first]})
    r = _codex(codex_root)["codex.reviewed"]
    assert r.status == doc.TODO and "SILENTLY" in r.detail and "/hooks" in r.detail, r


def test_a_DISABLED_hook_is_FAIL(codex_root):
    exp = doc.expected_hooks(codex_root / ".codex" / "hooks.json")
    k = sorted(exp)
    _cfg(codex_root, entries={k[0]: exp[k[0]], k[1]: "disabled"})
    r = _codex(codex_root)["codex.reviewed"]
    assert r.status == doc.FAIL and "disabled" in r.detail, r


def test_a_definition_CHANGED_since_review_is_FAIL_when_the_scheme_is_confirmed(codex_root):
    exp = doc.expected_hooks(codex_root / ".codex" / "hooks.json")
    k = sorted(exp)
    _cfg(codex_root, entries={k[0]: exp[k[0]], k[1]: "sha256:" + "0" * 64})
    r = _codex(codex_root)["codex.reviewed"]
    assert r.status == doc.FAIL and "changed" in r.detail, r


def test_hash_unverifiable_is_UNKNOWN_not_OK_nor_FAIL(codex_root):
    """No entry matches our hash at all: the likelier story is a Codex that changed its
    scheme, not every definition changing at once -- so UNKNOWN, never 'modified'."""
    exp = doc.expected_hooks(codex_root / ".codex" / "hooks.json")
    _cfg(codex_root, entries={k: "sha256:" + "1" * 64 for k in exp})
    r = _codex(codex_root)["codex.reviewed"]
    assert r.status == doc.UNKNOWN and "scheme" in r.detail, r


@pytest.mark.parametrize("states,status,detail", [
    (("untrusted", "trusted", "trusted"), doc.TODO, "NOT REVIEWED"),
    (("untrusted", "disabled", "trusted"), doc.FAIL, "disabled"),
    (("untrusted", "mismatch", "trusted"), doc.FAIL, "changed"),
    (("untrusted", "mismatch", "untrusted"), doc.UNKNOWN, "scheme"),
    (("untrusted", "mismatch", "disabled"), doc.FAIL, "disabled"),
])
def test_pending_review_cannot_mask_disabled_or_unverifiable_hooks(codex_root, states, status, detail):
    path = codex_root / ".codex/hooks.json"
    definitions = json.loads(path.read_text(encoding="utf-8"))
    definitions["hooks"]["PostToolUse"] = [{"hooks": [{"type": "command", "command": "bash after.sh"}]}]
    path.write_text(json.dumps(definitions), encoding="utf-8")
    expected = doc.expected_hooks(path)
    assert len(expected) == len(states)
    entries = {key: (expected[key] if state == "trusted" else
                     "disabled" if state == "disabled" else "sha256:" + "0" * 64)
               for key, state in zip(sorted(expected), states) if state != "untrusted"}
    _cfg(codex_root, entries=entries)
    result = _codex(codex_root)["codex.reviewed"]
    assert result.status == status and detail in result.detail, result


def test_the_hash_moves_with_every_field_codex_hashes():
    base = {"type": "command", "command": "bash x.sh", "timeout": 30}
    h0 = doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, base, windows=False)
    for k, v in (("command", "bash y.sh"), ("timeout", 31), ("async", True), ("statusMessage", "s"),
                 ("additionalContextLimit", 100)):
        assert doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, dict(base, **{k: v}), windows=False) != h0, k
    assert doc.codex_hook_hash("pre_tool_use", {"matcher": "Bash"}, base, windows=False) != h0
    assert doc.codex_hook_hash("post_tool_use", {"matcher": ".*"}, base, windows=False) != h0
    # normalised defaults do NOT move it
    assert doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, dict(base, timeout=None), windows=False) == \
        doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, dict(base, timeout=600), windows=False)
    assert doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, dict(base, additionalContextLimit=2500),
                               windows=False) == h0
    # Windows hashes commandWindows when present
    w = dict(base, commandWindows="pwsh -File x.ps1")
    assert doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, w, windows=True) != \
        doc.codex_hook_hash("pre_tool_use", {"matcher": ".*"}, w, windows=False)


def test_the_310_reader_and_tomllib_AGREE(codex_root, monkeypatch):
    pytest.importorskip("tomllib")
    _cfg(codex_root, extra='\n[plugins."x@y"]\nenabled = true\n[features]\nfoo = 1\n')
    text = (Path(os.environ["CODEX_HOME"]) / "config.toml").read_text(encoding="utf-8")
    full = doc._toml_tables(text)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    narrow = doc._toml_tables(text)
    assert full == narrow and len(narrow) == 3, (full, narrow)


def test_the_310_reader_REFUSES_a_line_it_does_not_understand(monkeypatch):
    monkeypatch.setitem(sys.modules, "tomllib", None)
    with pytest.raises(doc.TomlUnreadable):
        doc._toml_tables("[hooks.state.'k']\ntrusted_hash = \"sha256:1\"\nweird = [1, 2]\n")


def test_a_codex_target_with_NO_hooks_json_is_FAIL(codex_root):
    (codex_root / ".codex" / "hooks.json").unlink()
    _cfg_path = Path(os.environ["CODEX_HOME"]) / "config.toml"
    _cfg_path.write_text("", encoding="utf-8")
    r = _codex(codex_root)["codex.hooks"]
    assert r.status == doc.FAIL and "hooks.json" in r.detail


def test_codex_rules_that_FAIL_to_parse_are_FAIL(codex_root, monkeypatch, tmp_path):
    fake = tmp_path / "fake_codex.py"
    fake.write_text("import sys\nprint('failed to parse policy', file=sys.stderr)\nsys.exit(1)\n", encoding="utf-8")
    monkeypatch.setattr(doc, "_codex_cmd", lambda: [sys.executable, str(fake)])
    assert _codex(codex_root)["codex.rules"].status == doc.FAIL


def test_TWIN_codex_rules_that_parse_are_OK(codex_root, monkeypatch, tmp_path):
    fake = tmp_path / "fake_codex.py"
    fake.write_text("print('{\"decision\": \"allow\"}')\n", encoding="utf-8")
    monkeypatch.setattr(doc, "_codex_cmd", lambda: [sys.executable, str(fake)])
    assert _codex(codex_root)["codex.rules"].status == doc.OK


def test_codex_rules_without_codex_on_PATH_are_UNKNOWN(codex_root):
    assert _codex(codex_root)["codex.rules"].status == doc.UNKNOWN


def test_the_codex_rows_are_NA_when_codex_is_not_installed(sandbox):
    assert {r.status for r in doc.check_codex(sandbox.ctx())} == {doc.NA}


# claude
def test_TWIN_the_shipped_claude_wiring_is_OK(sandbox):
    rows = {r.id: r for r in doc.check_claude(sandbox.ctx())}
    assert rows["claude.wiring"].status == doc.OK, rows["claude.wiring"]
    assert rows["claude.enabled"].status == doc.OK, rows["claude.enabled"]


def test_a_missing_PowerShell_matcher_is_FAIL(sandbox):
    p = sandbox.root / ".claude" / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    s["hooks"]["PreToolUse"] = [g for g in s["hooks"]["PreToolUse"] if g.get("matcher") != "PowerShell"]
    p.write_text(json.dumps(s), encoding="utf-8")
    r = {r.id: r for r in doc.check_claude(sandbox.ctx())}["claude.wiring"]
    assert r.status == doc.FAIL and "PowerShell" in r.detail, r


def test_a_wired_script_that_is_MISSING_is_FAIL(sandbox):
    (sandbox.root / ".claude" / "hooks" / "protect-files.sh").unlink()
    r = {r.id: r for r in doc.check_claude(sandbox.ctx())}["claude.wiring"]
    assert r.status == doc.FAIL and "protect-files.sh" in r.detail, r


def test_disableAllHooks_in_the_LOCAL_settings_is_FAIL(sandbox):
    """READ (code.claude.com hooks-guide): `"disableAllHooks": true` switches every hook off,
    in user, project, local or managed settings."""
    (sandbox.root / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
    r = {r.id: r for r in doc.check_claude(sandbox.ctx())}["claude.enabled"]
    assert r.status == doc.FAIL and "disableAllHooks" in r.detail, r


def test_disableAllHooks_in_the_USER_settings_is_FAIL(sandbox, tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / "settings.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    r = {r.id: r for r in doc.check_claude(sandbox.ctx())}["claude.enabled"]
    assert r.status == doc.FAIL, r


def test_TWIN_a_project_false_OVERRIDES_a_user_true(sandbox, tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / "settings.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    (sandbox.root / ".claude" / "settings.local.json").write_text('{"disableAllHooks": false}', encoding="utf-8")
    r = {r.id: r for r in doc.check_claude(sandbox.ctx())}["claude.enabled"]
    assert r.status == doc.OK, r


# all targets
def test_X4_GUARD_off_is_FAIL(sandbox, monkeypatch):
    monkeypatch.setenv("X4_GUARD", "off")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["guard.escape"]
    assert r.status == doc.FAIL and "OFF" in r.detail, r


def test_TWIN_X4_GUARD_unset_is_OK(sandbox):
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["guard.escape"]
    assert r.status == doc.OK, r


@pytest.mark.parametrize("line", ["X4_GUARD=off", "export X4_GUARD='off'", "  X4_GUARD = on"])
def test_an_X4_GUARD_line_in_the_CONFIG_is_FAIL_and_named_ignored(sandbox, line):
    """R1-F1 / FX-S ruling 4: X4_GUARD is honoured ONLY from the launch environment; the
    config is a file an agent can write. A line there is ignored by the guards -- and
    reported as a FAIL, because someone tried to switch the guards off from a file."""
    cfg = sandbox.root / "x4-paths.env"
    cfg.write_text(cfg.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["guard.escape"]
    assert r.status == doc.FAIL and "ignored" in r.detail and str(cfg) in r.detail, r
    assert "off" not in r.detail.replace("OFF", ""), "a config VALUE was printed: %s" % r


def test_TWIN_a_config_merely_MENTIONING_X4_GUARD_in_a_comment_is_OK(sandbox):
    cfg = sandbox.root / "x4-paths.env"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "# X4_GUARD=off is ignored here\n",
                   encoding="utf-8")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["guard.escape"]
    assert r.status == doc.OK, r


def test_the_ENV_value_stays_authoritative_beside_a_config_line(sandbox, monkeypatch):
    monkeypatch.setenv("X4_GUARD", "off")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["guard.escape"]
    assert r.status == doc.FAIL and "OFF" in r.detail, r


def test_layer2_without_the_query_is_UNKNOWN(sandbox, monkeypatch):
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: None)
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == doc.UNKNOWN, r


def _refguard_stub(state):
    return type("M", (), {"report": staticmethod(
        lambda full=False, path=None: {"state": state, "detail": "stub " + state})})()


@pytest.mark.parametrize("state,codex,want", [
    ("protected", False, "OK"), ("absent", False, "TODO"), ("partial", False, "FAIL"),
    ("absent", True, "TODO"),
    ("partial", True, "FAIL"), ("unsupported", True, "UNKNOWN"), ("error", True, "UNKNOWN"),
    ("foreign", True, "UNKNOWN"), ("unconfigured", True, "UNKNOWN"), ("protected", True, "OK")])
def test_layer2_states(sandbox, monkeypatch, state, codex, want):
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub(state))
    if codex:
        shutil.copytree(sandbox.root / ".claude" / "hooks", sandbox.root / ".codex" / "hooks")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == want, r


def test_layer2_an_unprotected_OPENCODE_root_is_FAIL_too(sandbox, monkeypatch):
    """R6-15: 'hookless' named codex|generic and left OpenCode out, so an OpenCode root with
    no OS-level protection read OK 'the Claude hooks cover deletes' -- with no Claude target."""
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub("absent"))
    (sandbox.root / ".opencode" / "plugins").mkdir(parents=True)
    (sandbox.root / ".opencode" / "plugins" / "x4guard.js").write_text("//\n", encoding="utf-8")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == doc.TODO and "OpenCode" in r.detail, r


def test_layer2_asks_the_REAL_x4refguard_and_FAILS_an_unprotected_codex_root(sandbox):
    """Plan 3 live probe, 2026-10-03: x4doctor asked `x4lock.deny_delete_state`, which no
    module ever had -- Layer 2 shipped as scripts/x4refguard.py -- so this row was UNKNOWN on
    every machine, and the stubbed tests above (which invented the same function) agreed with
    it. NO stub here: the sandbox reference/ carries no protection, a Codex agent has no
    hook-level delete guard, so the real query must say FAIL."""
    shutil.copytree(sandbox.root / ".claude" / "hooks", sandbox.root / ".codex" / "hooks")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == doc.TODO and 'x4refguard.py" apply ' in r.detail, r   # C2: the user's step
    # FX-B2: the command is ABSOLUTE -- `scripts/...` relative to an unknown cwd named nothing
    m = re.search(r'python "([^"]+x4refguard\.py)" apply', r.detail)
    assert m and Path(m.group(1)).is_absolute() and Path(m.group(1)).is_file(), r
    # R2-b: a HUMAN reads this row, so plain `apply` (it asks); --yes only as an explanation
    assert "apply --yes" not in r.detail, r


def test_layer2_REAL_unprotected_claude_only_root_is_the_users_STEP_not_OK(sandbox):
    """FX-B2 (delta review): a Claude-only root read OK whatever the state, "the Claude hooks
    cover deletes". Unprotected is the user's pending step on EVERY root (TODO); the hooks
    sentence appears only where they are wired (the sandbox copies settings.json)."""
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == doc.TODO and "YOUR STEP" in r.detail, r
    assert "Claude hooks (wired" in r.detail, r


def test_TWIN_layer2_claude_root_WITHOUT_wired_hooks_does_not_claim_them(sandbox, monkeypatch):
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub("absent"))
    (sandbox.root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    r = {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]
    assert r.status == doc.TODO and "No Claude delete hook is wired" in r.detail, r
    assert "Claude hooks (wired" not in r.detail, r


def _lock_row(ctx):
    return {r.id: r for r in doc.check_common(ctx)}["x4lock"]


def _fake_lock(monkeypatch, states: dict, missing: int = 0):
    monkeypatch.setattr(doc, "_x4lock_answer", lambda ctx: ({"states": states, "missing": missing}, ""))


def test_x4lock_unlocked_files_are_OK_informational_never_UNKNOWN(sandbox, monkeypatch):
    """R4-2: locking is the user's CHOICE, so an unlocked manifest is an ANSWER, not a
    non-answer. As UNKNOWN it kept every fresh install at exit 3."""
    _fake_lock(monkeypatch, {"unlocked": 1})
    r = _lock_row(sandbox.ctx())
    assert r.status == doc.OK and "1 unlocked" in r.detail and "informational" in r.detail, r


def test_TWIN_x4lock_all_locked_is_OK(sandbox, monkeypatch):
    _fake_lock(monkeypatch, {"locked": 1})
    r = _lock_row(sandbox.ctx())
    assert r.status == doc.OK and "informational" not in r.detail, r


def test_x4lock_a_MISSING_protected_file_is_UNKNOWN(sandbox, monkeypatch):
    _fake_lock(monkeypatch, {"unlocked": 1}, missing=2)
    r = _lock_row(sandbox.ctx())
    assert r.status == doc.UNKNOWN and "2 missing" in r.detail, r


def _as_in_game_install(sandbox):
    """The sandbox root as an in-game install: the game folder IS the toolkit.

    CI2 (CI on da93d2d, ubuntu): with no scripts/ here the doctor fell back to the CHECKOUT's
    x4lock.py, whose manifest also demands the checkout's own x4-paths.env -- per-machine,
    gitignored, so ABSENT in CI ("1 missing" became "2"), and present on a developer machine
    (or waived in a linked worktree, which then demands the MAIN checkout's). The scenario now
    carries its own x4lock and resolver, as an installed toolkit does: nothing of the
    developer's checkout is read."""
    for name, text in (("CLAUDE.md", "# c\n"), ("KNOWLEDGEBASE.md", "# kb\n")):
        (sandbox.root / name).write_text(text, encoding="utf-8")
    (sandbox.root / "scripts").mkdir(exist_ok=True)
    for name in ("x4lock.py", "toolkit_layout.py"):      # scripts/ ships whole; x4lock loads the helper
        shutil.copy2(REPO / "scripts" / name, sandbox.root / "scripts" / name)
    shutil.copytree(REPO / "tools" / "x4validate" / "x4validate",
                    sandbox.root / "tools" / "x4validate" / "x4validate",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    _env_file(sandbox.root / "x4-paths.env", X4_TOOLKIT=sandbox.root, X4_GAME=sandbox.root,
              X4_REFERENCE=sandbox.ref)


def test_x4lock_row_asks_the_REAL_x4lock_with_the_roots_environment(sandbox):
    """R7-8: the row was only ever stub-tested. NO stub here: the real scripts/x4lock.py, run
    the way an agent runs it from the root (X4_TOOLKIT = the root), must find the root's
    guards and instructions -- more than the lone config file it found when it inherited the
    DOCTOR's cwd and environment instead (MEASURED on a scratch install: '1 protected')."""
    _as_in_game_install(sandbox)
    ctx = sandbox.ctx(env=dict(os.environ, X4_TOOLKIT=str(sandbox.root)))
    r = _lock_row(ctx)
    assert r.status == doc.OK and "0 missing" in r.detail, r
    # it asked the SANDBOX's x4lock, never the checkout's (CI2)
    got, where = doc._x4lock_answer(ctx)
    assert Path(where).resolve() == (sandbox.root / "scripts" / "x4lock.py").resolve(), where
    n = int(r.detail.split(" protected")[0])
    hooks = len(list((sandbox.root / ".claude" / "hooks").glob("*.sh")))
    assert n >= hooks + 3, r          # the hooks, CLAUDE.md, KNOWLEDGEBASE.md, settings.json


def test_TWIN_x4lock_REAL_a_deleted_CLAUDE_md_is_MISSING(sandbox):
    _as_in_game_install(sandbox)
    (sandbox.root / "CLAUDE.md").unlink()
    ctx = sandbox.ctx(env=dict(os.environ, X4_TOOLKIT=str(sandbox.root)))
    r = _lock_row(ctx)
    assert r.status == doc.UNKNOWN and "1 missing" in r.detail, r


def test_the_doctor_never_WRITES_the_codex_config(codex_root):
    _cfg(codex_root)
    p = Path(os.environ["CODEX_HOME"]) / "config.toml"
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    doc.collect(doc.Ctx(root=codex_root, toolkit=codex_root))
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


# --- one trust model: the doctor's must equal lane B's and Codex's own ------------------ #

_HASH_CASES = [
    ("pre_tool_use", {"matcher": ".*"}, {"type": "command", "command": "bash a.sh", "timeout": 60}),
    ("pre_tool_use", {"matcher": ".*"}, {"type": "command", "command": "bash a.sh", "timeout": 60,
                                         "commandWindows": "pwsh -File a.ps1"}),
    ("session_start", {}, {"type": "command", "command": "bash s.sh", "timeout": 30}),
    ("post_tool_use", {"matcher": "apply_patch"}, {"type": "command", "command": "bash p.sh"}),
]


def test_the_doctor_and_lane_Bs_codex_trust_compute_the_SAME_hash():
    src = REPO / "agent" / "guards" / "adapters" / "codex_trust.py"
    from _layout import require_repo
    # codex_trust.py landed in Plan 2: in a checkout its absence is a FAILURE, not a skip.
    require_repo("agent/guards/adapters/codex_trust.py", root=REPO,
                 why="the doctor's hash is compared with the adapter source's")
    spec = importlib.util.spec_from_file_location("codex_trust_for_doctor", src)
    ct = importlib.util.module_from_spec(spec)
    sys.modules["codex_trust_for_doctor"] = ct
    spec.loader.exec_module(ct)
    for event, group, handler in _HASH_CASES:
        assert doc.codex_hook_hash(event, group, handler) == ct.hook_hash(event, group, handler), (event, handler)


def test_the_doctor_reproduces_CODEXS_OWN_hash_vectors():
    """The oracle is Codex (lane B Task 3 records `hooks/list` current_hash values for
    neutral definitions), never another reimplementation (#14)."""
    vec = Path(__file__).parent / "fixtures" / "codex" / "0.160.0" / "trust_vectors.json"
    if not vec.is_file():
        pytest.skip("lane B's Codex-produced trust vectors have not landed")
    vectors = json.loads(vec.read_text(encoding="utf-8"))
    assert len(vectors) >= 4
    for v in vectors:
        assert doc.codex_hook_hash(v["event_key"], v["group"], v["handler"],
                                   windows=v.get("windows")) == v["codex_hash"], v.get("label")


def test_a_TARGET_whose_guard_copy_is_MISSING_is_FAIL_not_NA(codex_root):
    """MEASURED on the review scratch project: hooks.json present, .codex/hooks/ absent --
    the self-test read N/A 'not installed'. Hooks that point at absent scripts fail, and
    Codex then runs the command."""
    rows = {r.id: r for r in doc.check_guards(doc.Ctx(root=codex_root, toolkit=codex_root))}
    assert rows["guards.selftest.codex"].status == doc.FAIL, rows["guards.selftest.codex"]
    assert rows["guards.selftest.claude"].status == doc.NA


def test_TWIN_a_target_that_is_not_installed_is_NA(sandbox):
    rows = {r.id: r for r in doc.check_guards(sandbox.ctx())}
    assert rows["guards.selftest.codex"].status == doc.NA


# --- Plan 3 lane L: the OpenCode target (BEST EFFORT, from docs, not measured) --------- #

OC_HOOKS = REPO / ".opencode" / "hooks"


@pytest.fixture
def oc_root(tmp_path, monkeypatch):
    """An OpenCode-only install: the generated .opencode/ tree, AGENTS.md, the path config,
    and the deny rules rendered by the toolkit's own renderer."""
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "root"
    from codex_testlib import copy_shared_runtime
    copy_shared_runtime(root)
    game = tmp_path / "game"
    ref = root / "reference"
    for d in (game / "extensions", ref / "libraries"):
        d.mkdir(parents=True)
    for sub in ("hooks", "plugins"):
        shutil.copytree(REPO / ".opencode" / sub, root / ".opencode" / sub,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copy2(REPO / ".opencode" / "X4-OPENCODE.md", root / ".opencode" / "X4-OPENCODE.md")
    shutil.copy2(REPO / "AGENTS.md", root / "AGENTS.md")
    _env_file(root / "x4-paths.env", X4_TOOLKIT=root, X4_GAME=game, X4_REFERENCE=ref)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    r = subprocess.run([sys.executable, str(root / ".opencode" / "hooks" / "opencode_config.py"),
                        "write", "--root", str(root)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return root


def _oc_rows(root, fn, **kw):
    # FX-B2 (reviewer C): a Ctx without toolkit= acts for the toolkit the doctor LIVES in --
    # the checkout -- and read its REAL config. Every scenario names its scratch toolkit.
    kw.setdefault("toolkit", root)
    return {r.id: r for r in fn(doc.Ctx(root=root, **kw))}


def test_opencode_is_detected_by_its_plugin(tmp_path):
    (tmp_path / ".opencode" / "plugins").mkdir(parents=True)
    (tmp_path / ".opencode" / "plugins" / "x4guard.js").write_text("//\n", encoding="utf-8")
    assert doc.detect_targets(tmp_path)["opencode"] is True


def test_TWIN_a_users_own_opencode_dir_is_NOT_the_opencode_target(tmp_path):
    """OpenCode itself writes .opencode/ (its .gitignore, package.json); a user may keep their
    own plugins there. The toolkit's target is OUR plugin."""
    (tmp_path / ".opencode" / "plugins").mkdir(parents=True)
    (tmp_path / ".opencode" / "opencode.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".opencode" / "plugins" / "mine.js").write_text("//\n", encoding="utf-8")
    assert doc.detect_targets(tmp_path)["opencode"] is False


def test_a_healthy_opencode_install_is_OK_on_every_opencode_row(oc_root):
    rows = _oc_rows(oc_root, doc.check_opencode)
    for rid in ("opencode.plugin", "opencode.config", "opencode.userconfig", "opencode.cli"):
        assert rows[rid].status == doc.OK, rows[rid]
    assert "desktop app" in rows["opencode.cli"].detail.lower()


def test_opencode_rows_are_NA_when_the_target_is_absent(sandbox):
    rows = sandbox.rows(doc.check_opencode)
    assert rows and all(r.status == doc.NA for r in rows.values()), rows


@pytest.mark.parametrize("gone", ["opencode_adapter.py", "codex_adapter.py", "x4guard.py"])
def test_a_missing_adapter_or_guard_is_an_opencode_plugin_FAIL(oc_root, gone):
    (oc_root / ".opencode" / "hooks" / gone).unlink()
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.plugin"]
    assert r.status == doc.FAIL and gone in r.detail, r


def test_a_missing_config_is_an_opencode_config_FAIL(oc_root):
    (oc_root / ".opencode" / "opencode.jsonc").unlink()
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.config"]
    assert r.status == doc.FAIL and "opencode_config.py write" in r.detail, r


def test_a_STALE_config_is_an_opencode_config_FAIL(oc_root, tmp_path):
    moved = tmp_path / "moved-ref"
    moved.mkdir()
    _env_file(oc_root / "x4-paths.env", X4_TOOLKIT=oc_root, X4_REFERENCE=moved)
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.config"]
    assert r.status == doc.FAIL and "stale" in r.detail, r
    # C5 (install red-team 2026-10-04): never a bare "stale" -- say what differs and why
    assert "rule(s)" in r.detail and "moved" in r.detail, r


def test_C5_a_foreign_X4_TOOLKIT_is_NAMED_as_the_cause_of_a_stale_config(oc_root, tmp_path):
    other = tmp_path / "other-toolkit"
    other.mkdir()
    _env_file(other / "x4-paths.env", X4_TOOLKIT=other, X4_REFERENCE=tmp_path / "other-ref")
    (tmp_path / "other-ref").mkdir()
    env = dict(os.environ, X4_TOOLKIT=str(other))
    r = _oc_rows(oc_root, doc.check_opencode, env=env)["opencode.config"]
    assert r.status == doc.FAIL and "X4_TOOLKIT=" in r.detail and str(other) in r.detail, r


def test_a_config_that_cannot_be_rendered_is_UNKNOWN(oc_root, tmp_path):
    env = dict(os.environ, X4_REFERENCE=str(tmp_path / "a*b"))
    r = _oc_rows(oc_root, doc.check_opencode, env=env)["opencode.config"]
    assert r.status == doc.UNKNOWN, r


@pytest.mark.parametrize("where,body", [
    ("project", {"permission": {"edit": "ask"}}),
    ("project", {"permission": "ask"}),
    ("dotdir", {"permission": {"bash": "ask"}}),
    ("global", {"permission": {"edit": "ask"}}),
])
def test_a_STRING_permission_in_the_users_config_is_flagged(oc_root, tmp_path, where, body):
    """READ (config.ts + remeda mergeDeep): a string in an earlier config is REPLACED by our
    deny object, so the user's own "ask" silently stops applying."""
    p = {"project": oc_root / "opencode.json", "dotdir": oc_root / ".opencode" / "opencode.json",
         "global": tmp_path / "xdg" / "opencode" / "opencode.jsonc"}[where]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("// mine\n" + json.dumps(body), encoding="utf-8")
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.userconfig"]
    assert r.status == doc.UNKNOWN and "REPLACED" in r.detail and str(p.name) in r.detail, r


def test_TWIN_an_object_permission_in_the_users_config_is_fine(oc_root):
    (oc_root / "opencode.json").write_text(json.dumps({"permission": {"edit": {"*": "ask"}}}),
                                           encoding="utf-8")
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.userconfig"]
    assert r.status == doc.OK, r


def test_an_unreadable_user_config_is_UNKNOWN(oc_root):
    (oc_root / "opencode.json").write_text("{ not json", encoding="utf-8")
    r = _oc_rows(oc_root, doc.check_opencode)["opencode.userconfig"]
    assert r.status == doc.UNKNOWN and "opencode.json" in r.detail, r


def test_the_opencode_guard_copy_is_self_tested_with_its_OWN_x4guard(oc_root):
    rows = _oc_rows(oc_root, doc.check_guards)
    r = rows["guards.selftest.opencode"]
    assert r.status == doc.OK and ".opencode" in r.detail.replace(chr(92), "/"), r


def test_opencode_instructions_need_AGENTS_md_and_the_addendum(oc_root):
    assert _oc_rows(oc_root, doc.check_parity)["instructions.opencode"].status == doc.OK
    (oc_root / ".opencode" / "X4-OPENCODE.md").unlink()
    r = _oc_rows(oc_root, doc.check_parity)["instructions.opencode"]
    assert r.status == doc.FAIL and "X4-OPENCODE.md" in r.detail, r

# --- the doctor's X4_GUARD-line detector and the loaders' parser are ONE rule (v4.0.0 merge) ---
# x4doctor stays stdlib-only, so it cannot import _paths (relative imports); it keeps its own
# matcher, and this pins it to the parser line by line, so the two cannot drift.
_BOM = chr(0xFEFF)
_GUARD_SHAPES = ["X4_GUARD=off", "export X4_GUARD=off", "  X4_GUARD = off", _BOM + "X4_GUARD=off",
                 "X4_GUARD_CHECK=1", "X4_GUARD=", "# X4_GUARD=off", "X4_GUARDS=off", "X4_GAME=/g",
                 "echo X4_GUARD=off", "X4_GUARD=off" + chr(13)]


@pytest.mark.parametrize("line", _GUARD_SHAPES, ids=lambda s: repr(s))
def test_doctor_guard_line_agrees_with_the_loader_parser(tmp_path, line):
    from x4validate import _paths
    f = tmp_path / "x4-paths.env"
    f.write_bytes((line + chr(10)).encode("utf-8"))
    _vals, ignored = _paths.parse_env_report(f)
    parser_says = any(reason == "guard" for _n, reason in ignored)
    doctor_says = f in doc.config_guard_lines_in([f])
    assert doctor_says == parser_says, (line, doctor_says, parser_says)



# --- B2 (install red-team 2026-10-04): the doctor acts for the toolkit it LIVES IN -------- #

def test_B2_the_default_toolkit_is_the_doctors_own_not_X4_TOOLKIT(tmp_path):
    ctx = doc.Ctx(root=tmp_path, env={"X4_TOOLKIT": str(tmp_path / "other")})
    assert ctx.toolkit == REPO, ctx.toolkit
    assert str(REPO) in ctx.toolkit_note and str(tmp_path / "other") in ctx.toolkit_note


def test_B2_TWIN_no_note_when_X4_TOOLKIT_names_the_doctors_own(tmp_path):
    ctx = doc.Ctx(root=tmp_path, env={"X4_TOOLKIT": str(REPO)})
    assert ctx.toolkit == REPO and ctx.toolkit_note == ""


def test_B2_INCIDENT_a_second_installed_copy_never_compares_against_the_first(tmp_path):
    """The red-team shape: B (an installed toolkit, runtime only) diagnosed by B's own doctor
    while X4_TOOLKIT names A, a source tree that differs. Parity must not compare B to A."""
    a = _tree(tmp_path / "A", dict(_CLAUDE, **{".claude/hooks/a.sh": "echo A-DIFFERS\n",
                                                "agent/core.md": "#\n"}))
    b = _tree(tmp_path / "B", dict(_CLAUDE, **{"tools/x4validate/x4validate/_paths.py": "#\n"}))
    (b / "scripts").mkdir()
    for name in ("x4doctor.py", "toolkit_layout.py"):    # scripts/ ships whole; the doctor loads the helper
        shutil.copy2(REPO / "scripts" / name, b / "scripts" / name)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env.update(X4_TOOLKIT=str(a), X4_CONFIG="", X4_REFERENCE="", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(b / "scripts" / "x4doctor.py"), "--root", str(b),
                        "--agent", "claude", "--json"], capture_output=True, text=True, env=env,
                       timeout=300)
    got = json.loads(r.stdout)
    rows = {c["id"]: c for c in got["checks"]}
    assert rows["parity.claude"]["status"] == "N/A", rows["parity.claude"]
    assert str(a) in got.get("toolkit_note", "") and str(b) in got.get("toolkit_note", ""), got.get("toolkit_note")


# --- M-a (v4.0.0 delta review): a .git git cannot answer for is UNKNOWN, never N/A -------- #

@pytest.mark.parametrize("commit,want", [(False, "UNKNOWN"), (True, "N/A")])
def test_Ma_parity_on_a_root_whose_git_cannot_answer_is_UNKNOWN(tmp_path, commit, want):
    """An installed-looking root WITH the generator and a .git: git failing (no commit yet) is
    UNKNOWN, not "this root IS an installed toolkit". TWIN: a repo git CAN read, whose HEAD has
    no agent/ (an in-game install inside a game repo), stays N/A."""
    b = _tree(tmp_path / "B", dict(_CLAUDE, **{"tools/x4validate/x4validate/_paths.py": "#\n",
                                                "tools/x4validate/scripts/gen-agent-trees.py": "#\n"}))
    (b / "scripts").mkdir()
    for name in ("x4doctor.py", "toolkit_layout.py"):
        shutil.copy2(REPO / "scripts" / name, b / "scripts" / name)
    subprocess.run(["git", "init", "-q", str(b)], check=True, capture_output=True)
    if commit:
        (b / "readme.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(b), "add", "readme.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(b), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "i"],
                       check=True, capture_output=True)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env.update(X4_CONFIG="", X4_REFERENCE="", PYTHONDONTWRITEBYTECODE="1")
    env.pop("X4_TOOLKIT", None)
    r = subprocess.run([sys.executable, str(b / "scripts" / "x4doctor.py"), "--root", str(b),
                        "--agent", "claude", "--json"], capture_output=True, text=True, env=env,
                       timeout=300)
    row = {c["id"]: c for c in json.loads(r.stdout)["checks"]}["parity.claude"]
    assert row["status"] == want, row
    if want == "UNKNOWN":
        assert "git could not read" in row["detail"], row



@pytest.mark.parametrize("commit", [False, True])
def test_Ma_parity_WITHOUT_the_generator_does_not_overclaim_an_install(tmp_path, commit):
    """Release review F7: with NO generator, parity is N/A either way (it cannot run). But
    when git could not answer, the old text still said "this root IS an installed
    toolkit" -- a claim nothing measured. It must say the status could not be determined,
    and why. TWIN (commit=True): git answers "no agent/ in HEAD" -- an install, and the
    install wording stays."""
    b = _tree(tmp_path / "B", dict(_CLAUDE, **{"tools/x4validate/x4validate/_paths.py": "#\n"}))
    (b / "scripts").mkdir()
    for name in ("x4doctor.py", "toolkit_layout.py"):
        shutil.copy2(REPO / "scripts" / name, b / "scripts" / name)
    subprocess.run(["git", "init", "-q", str(b)], check=True, capture_output=True)
    if commit:
        (b / "readme.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(b), "add", "readme.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(b), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "i"],
                       check=True, capture_output=True)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env.update(X4_CONFIG="", X4_REFERENCE="", PYTHONDONTWRITEBYTECODE="1")
    env.pop("X4_TOOLKIT", None)
    r = subprocess.run([sys.executable, str(b / "scripts" / "x4doctor.py"), "--root", str(b),
                        "--agent", "claude", "--json"], capture_output=True, text=True, env=env,
                       timeout=300)
    row = {c["id"]: c for c in json.loads(r.stdout)["checks"]}["parity.claude"]
    assert row["status"] == "N/A", row
    if commit:
        assert "IS an installed toolkit" in row["detail"], row
    else:
        assert "IS an installed toolkit" not in row["detail"], row
        assert "could not be determined" in row["detail"], row
        assert "git could not read" in row["detail"], row
        assert "gen-agent-trees.py" in row["detail"], row

# --- cosmetic (install red-team 2026-10-04): the targets line of a claude+codex install ---- #

def test_targets_line_does_not_list_generic_for_a_codex_install(tmp_path):
    """A Codex install ships .agents/skills, which is also the generic payload, so an
    `--agent auto` claude+codex install read "targets: claude, codex, generic"."""
    ctx = doc.Ctx(root=tmp_path, toolkit=None, env={},
                  targets={"claude": True, "codex": True, "generic": True, "opencode": False})
    line = [l for l in doc.render_text(ctx, [], 0, 0.0).splitlines() if l.startswith("  targets:")][0]
    assert line.split("targets:")[1].split("(")[0].strip() == "claude, codex", line


def test_TWIN_a_generic_only_install_still_lists_generic(tmp_path):
    ctx = doc.Ctx(root=tmp_path, toolkit=None, env={},
                  targets={"claude": False, "codex": False, "generic": True, "opencode": False})
    line = [l for l in doc.render_text(ctx, [], 0, 0.0).splitlines() if l.startswith("  targets:")][0]
    assert "generic" in line, line


# ------------- FX-B3 (reviewer F M2/M3): the layer2 hint is a command that WORKS in its case

def _layer2_row(sandbox):
    return {r.id: r for r in doc.check_common(sandbox.ctx())}["layer2.reference"]


def _unpack_path_in(detail):
    m = re.search(r'bash "([^"]+unpack-reference\.sh)" --toolkit "([^"]+)"', detail)
    return m and Path(m.group(1))


def test_FXB3_layer2_WITH_the_sentinel_says_apply(sandbox):
    """The REAL x4refguard on a finished, unprotected unpack: apply is the step, and works."""
    r = _layer2_row(sandbox)
    assert r.status == doc.TODO and 'x4refguard.py" apply ' in r.detail, r
    assert "unpack-reference.sh" not in r.detail, r


def test_FXB3_layer2_WITHOUT_the_sentinel_says_UNPACK_not_apply(sandbox):
    """apply REFUSES a tree with no .unpacked-and-locked; the row told the user to run it."""
    (sandbox.ref / ".unpacked-and-locked").unlink()
    r = _layer2_row(sandbox)
    assert r.status == doc.TODO and "not a finished unpack" in r.detail, r
    assert "-- run: bash" in r.detail and "would refuse" in r.detail, r
    p = _unpack_path_in(r.detail)
    assert p is not None and p.is_absolute() and p.is_file(), r


def test_FXB3_layer2_PARTIAL_without_the_sentinel_says_remove_then_unpack(sandbox, monkeypatch):
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub("partial"))
    (sandbox.ref / ".unpacked-and-locked").unlink()
    r = _layer2_row(sandbox)
    assert r.status == doc.FAIL and re.search(r'" remove --toolkit "[^"]+" --reference "[^"]+", then bash',
                                              r.detail), r


def test_FXB3_layer2_NO_reference_yet_is_a_TODO_with_the_unpack_command(sandbox):
    """x4refguard reports a missing tree as "unconfigured"; the row said UNKNOWN (exit 3) on
    every fresh install before its unpack. Now the user's step (TODO, exit 4)."""
    shutil.rmtree(sandbox.ref)
    r = _layer2_row(sandbox)
    assert r.status == doc.TODO and "not unpacked yet" in r.detail, r
    assert _unpack_path_in(r.detail) is not None, r


def test_FXB3_TWIN_unconfigured_with_the_tree_PRESENT_stays_UNKNOWN(sandbox, monkeypatch):
    """Clause twin: only a MISSING tree is the known pre-unpack state; an "unconfigured"
    answer about a folder that exists is a real can't-tell."""
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub("unconfigured"))
    r = _layer2_row(sandbox)
    assert r.status == doc.UNKNOWN, r


# ------------- FX-B4 #4 (reviewer I): the unpack hint names the tree it judged; a MOVED tree is
#               not "not unpacked"

def _roots_ref_row(sandbox, **kw):
    return {r.id: r for r in doc.check_roots(sandbox.ctx(**kw))}["roots.reference"]


def _fresh(sandbox, tmp_path, name="not-yet"):
    """A fresh install: the config names a tree that is not there, and none exists anywhere.
    The root gets its own bin/unpack-reference.sh, as an installed toolkit has, so the hint's
    --toolkit is the sandbox and not this checkout."""
    shutil.rmtree(sandbox.ref)
    (sandbox.root / "bin").mkdir(exist_ok=True)
    shutil.copy2(REPO / "bin" / "unpack-reference.sh", sandbox.root / "bin" / "unpack-reference.sh")
    _env_file(sandbox.root / "x4-paths.env", X4_TOOLKIT=sandbox.root, X4_GAME=sandbox.game,
              X4_REFERENCE=tmp_path / name)
    return tmp_path / name


def _reference_arg(detail):
    m = re.search(r'unpack-reference\.sh" --toolkit "[^"]+" --reference "([^"]+)"', detail)
    return m and m.group(1)


def test_FXB4_the_unpack_hint_names_the_tree_the_doctor_JUDGED(sandbox, tmp_path):
    """It printed `bash ".../unpack-reference.sh" --toolkit TK` -- with an exported
    X4_REFERENCE differing from the config, that command REFUSES (FX-B2). The doctor judged
    the guards' tree, so the command names it."""
    want = _fresh(sandbox, tmp_path)
    for row in (_roots_ref_row(sandbox), _layer2_row(sandbox)):
        assert row.status == doc.TODO, row
        got = _reference_arg(row.detail)
        assert got and os.path.normcase(str(Path(got))) == os.path.normcase(str(want)), row


def test_FXB4_an_inherited_FOREIGN_X4_CONFIG_hint_says_clear_it_FIRST(sandbox, tmp_path):
    """The unpack refuses a foreign X4_CONFIG whatever the flags (FX-B4 #3): a hint that does
    not say so names a command that cannot run in this shell."""
    want = _fresh(sandbox, tmp_path)
    foreign = tmp_path / "elsewhere" / "x4-paths.env"
    _env_file(foreign, X4_TOOLKIT=sandbox.root, X4_GAME=sandbox.game, X4_REFERENCE=want)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env["X4_CONFIG"] = str(foreign)
    row = _roots_ref_row(sandbox, env=env)
    assert row.status == doc.TODO, row
    assert "X4_CONFIG" in row.detail and "unset X4_CONFIG" in row.detail, row
    assert row.detail.index("X4_CONFIG") < row.detail.index("unpack-reference.sh"), row


def test_FXB4_TWIN_X4_CONFIG_naming_the_root_s_OWN_config_needs_no_clearing(sandbox, tmp_path):
    _fresh(sandbox, tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env["X4_CONFIG"] = str(sandbox.root / "x4-paths.env")
    row = _roots_ref_row(sandbox, env=env)
    assert row.status == doc.TODO and "unset X4_CONFIG" not in row.detail, row


@pytest.mark.parametrize("suffix", ["/.", "/./", ""] + (["::$DATA", "::$data"] if os.name == "nt" else []))
def test_FXG5_the_root_s_OWN_config_in_ANY_spelling_needs_no_clearing(sandbox, tmp_path, suffix):
    """FX-G5 / reviewer J2 item 4: the doctor judges the file the loaders READ -- `.`/`..`
    lexically, `::$DATA` dropped -- not the raw string (`x4-paths.env/.` read as missing)."""
    _fresh(sandbox, tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env["X4_CONFIG"] = str(sandbox.root / "sub" / ".." / "x4-paths.env") + suffix
    row = _roots_ref_row(sandbox, env=env)
    assert row.status == doc.TODO and "unset X4_CONFIG" not in row.detail, (suffix, row)
    assert doc._config_spelling(env["X4_CONFIG"]) == Path(os.path.normpath(sandbox.root / "x4-paths.env"))


@pytest.mark.skipif(os.name != "nt", reason="a Windows stream spelling -- elsewhere `::` is a filename byte")
@pytest.mark.parametrize("suffix", ["::$DATA/.", "::$DATA::$DATA", "::$DATA "])
def test_FXG6_a_further_stream_suffix_names_NO_config(sandbox, tmp_path, suffix):
    """FX-G6 / reviewer K M2 (MEASURED: Windows opens these as the file; the bash loader finds
    none): any `::` beyond exactly one trailing `::$DATA` names no config, so the doctor says
    to clear X4_CONFIG, as for a missing file -- the same answer as both loaders."""
    _fresh(sandbox, tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env["X4_CONFIG"] = str(sandbox.root / "x4-paths.env") + suffix
    row = _roots_ref_row(sandbox, env=env)
    assert "unset X4_CONFIG" in row.detail, (suffix, row)
    assert doc._config_stream(env["X4_CONFIG"])
    # twin: exactly one trailing `::$DATA` still names the file
    assert not doc._config_stream(str(sandbox.root / "x4-paths.env") + "::$DATA")


def test_FXG5_TWIN_a_missing_config_in_the_same_spelling_still_says_clear_it(sandbox, tmp_path):
    _fresh(sandbox, tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env["X4_CONFIG"] = str(sandbox.root / "nope.env") + "/."
    row = _roots_ref_row(sandbox, env=env)
    assert "unset X4_CONFIG" in row.detail, row


def test_FXB4_a_MOVED_tree_is_UNKNOWN_not_a_fresh_unpack__buildid_marker(sandbox, tmp_path):
    """x4refguard says "does not exist (not unpacked yet, or renamed or moved)" -- it cannot
    tell. The doctor said "not unpacked" (TODO, run the unpack): for a tree the user MOVED, a
    27 GB re-unpack into the old place. The unpack writes .claude/.reference-buildid only after
    a counted, finished unpack, so its presence says one was made."""
    _fresh(sandbox, tmp_path)
    (sandbox.root / ".claude" / ".reference-buildid").write_text("123\n", encoding="utf-8")
    for row in (_roots_ref_row(sandbox), _layer2_row(sandbox)):
        assert row.status == doc.UNKNOWN, row
        assert "renamed or moved" in row.detail and ".reference-buildid" in row.detail, row


def test_FXB4_a_MOVED_tree_is_UNKNOWN__a_finished_unpack_at_the_DEFAULT_place(sandbox, tmp_path):
    """Second clause: the config names a missing tree while <toolkit>/reference holds a
    finished unpack (its sentinel) -- the tree is somewhere, just not where the config says."""
    _fresh(sandbox, tmp_path)
    (sandbox.root / "reference").mkdir()
    (sandbox.root / "reference" / ".unpacked-and-locked").write_text("buildid 1\n", encoding="utf-8")
    for row in (_roots_ref_row(sandbox), _layer2_row(sandbox)):
        assert row.status == doc.UNKNOWN, row
        assert "renamed or moved" in row.detail and ".unpacked-and-locked" in row.detail, row


def test_FXB4_TWIN_an_empty_default_folder_is_no_evidence_of_a_move(sandbox, tmp_path):
    """Clause twin: a <toolkit>/reference WITHOUT the sentinel is not a finished unpack."""
    _fresh(sandbox, tmp_path)
    (sandbox.root / "reference").mkdir()
    for row in (_roots_ref_row(sandbox), _layer2_row(sandbox)):
        assert row.status == doc.TODO, row


# ------------- FX-Z (FX-B4 lead): the `x4refguard apply` hint carries --toolkit and --reference,
#               as the unpack hint does -- an exported X4_REFERENCE or a foreign X4_CONFIG made
#               the bare `apply` REFUSE (FX-B2/FX-B3), so the row named a command that could not run

_REFGUARD_HINT = re.compile(r'python "([^"]+x4refguard\.py)" (apply|remove)((?: --[a-z]+ "[^"]+")*)')


def _mini_toolkit(sandbox):
    """The sandbox root as an INSTALLED toolkit carrying its own x4refguard + x4validate, so
    the hint's script acts for the sandbox's config and never for this checkout's."""
    (sandbox.root / "scripts").mkdir(exist_ok=True)
    for name in ("x4refguard.py", "gitbash.py"):
        shutil.copy2(REPO / "scripts" / name, sandbox.root / "scripts" / name)
    shutil.copytree(REPO / "tools" / "x4validate" / "x4validate",
                    sandbox.root / "tools" / "x4validate" / "x4validate",
                    ignore=shutil.ignore_patterns("__pycache__"))


def _alt_tree(tmp_path):
    alt = tmp_path / "alt-ref"
    (alt / "libraries").mkdir(parents=True)
    (alt / "libraries" / "wares.xml").write_text("<wares/>\n", encoding="utf-8")
    (alt / ".unpacked-and-locked").write_text("buildid 1\n", encoding="utf-8")
    return alt


def _run_hint(detail, env, cwd):
    """Run the row's x4refguard command as printed. No terminal (stdin is an empty pipe) and no --yes,
    so x4refguard can only REFUSE before changing anything -- the question is WHICH refusal:
    the root-conflict one (the hint cannot run) or "not confirmed" after naming its target."""
    m = _REFGUARD_HINT.search(detail)
    assert m, detail
    flags = re.findall(r'(--[a-z]+) "([^"]+)"', m.group(3))
    argv = [sys.executable, m.group(1), m.group(2)] + [x for kv in flags for x in kv]
    r = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=120,
                       input="")   # a PIPE: NUL reads as a terminal on Windows
    return dict(flags), r


def _clean_env(**kv):
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
    env.update({k: str(v) for k, v in kv.items()})
    return env


def _names_target(stderr, tree):
    m = re.search(r"x4refguard apply -- target: (.+)", stderr)
    return bool(m) and os.path.normcase(str(Path(m.group(1).strip()).resolve())) == \
        os.path.normcase(str(Path(tree).resolve()))


def _own_hint_tree(sandbox, tmp_path, monkeypatch, tree):
    """Make only this scratch target meet the real Windows ownership precondition.

    The hint still runs without confirmation and must never apply an ACL deny.
    Ownership repair uses the same sandboxed fixture as the reference-guard tests.
    """
    if os.name != "nt":
        return
    import tempfile
    from refguard_owner import own_or_skip

    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert tree.resolve().is_relative_to(tmp_path.resolve())
    guard = doc._x4refguard_module(sandbox.ctx())
    assert guard is not None
    monkeypatch.setenv(guard.SANDBOX_ENV, str(tmp_path))
    own_or_skip(tree, guard)


def test_FXZ_the_apply_hint_RUNS_under_an_exported_X4_REFERENCE(sandbox, tmp_path, monkeypatch):
    """MEASURED RED at 4d92732: the row printed `python ".../x4refguard.py" apply`, and that
    command, run in the shell the doctor judged, refused "name DIFFERENT roots" (FX-B2) -- the
    doctor judged the exported tree, the bare apply acts on the config's."""
    _mini_toolkit(sandbox)
    alt = _alt_tree(tmp_path)
    _own_hint_tree(sandbox, tmp_path, monkeypatch, alt)
    env = _clean_env(X4_REFERENCE=alt, CODEX_HOME=sandbox.codex_home)
    row = {r.id: r for r in doc.check_common(sandbox.ctx(env=env))}["layer2.reference"]
    assert row.status == doc.TODO, row
    flags, r = _run_hint(row.detail, env, tmp_path)
    assert "DIFFERENT roots" not in r.stderr and "X4_CONFIG" not in r.stderr, r.stderr
    assert _names_target(r.stderr, alt), r.stderr          # it acts on the tree it JUDGED
    assert "REFUSED: not confirmed" in r.stderr and r.returncode == 2, r.stderr   # changed nothing
    assert os.path.normcase(str(Path(flags["--toolkit"]).resolve())) == \
        os.path.normcase(str(sandbox.root.resolve())), flags


def test_FXZ_the_apply_hint_RUNS_under_a_FOREIGN_X4_CONFIG(sandbox, tmp_path, monkeypatch):
    """Second clause: an inherited X4_CONFIG outside the toolkit refuses every mutating call
    (FX-B3) unless the root is chosen by its flag; --reference lifts it for x4refguard."""
    _mini_toolkit(sandbox)
    alt = _alt_tree(tmp_path)
    _own_hint_tree(sandbox, tmp_path, monkeypatch, alt)
    foreign = tmp_path / "elsewhere" / "x4-paths.env"
    _env_file(foreign, X4_TOOLKIT=sandbox.root, X4_GAME=sandbox.game, X4_REFERENCE=alt)
    env = _clean_env(X4_CONFIG=foreign, CODEX_HOME=sandbox.codex_home)
    row = {r.id: r for r in doc.check_common(sandbox.ctx(env=env))}["layer2.reference"]
    assert row.status == doc.TODO, row
    _flags, r = _run_hint(row.detail, env, tmp_path)
    assert "REFUSED: `x4refguard apply`" not in r.stderr, r.stderr
    assert _names_target(r.stderr, alt), r.stderr


def test_FXZ_TWIN_the_apply_hint_with_NOTHING_exported_acts_on_the_config_tree(sandbox, tmp_path, monkeypatch):
    """Control: the flags must not move the target when nothing in the environment does."""
    _mini_toolkit(sandbox)
    _own_hint_tree(sandbox, tmp_path, monkeypatch, sandbox.ref)
    env = _clean_env(CODEX_HOME=sandbox.codex_home)
    row = {r.id: r for r in doc.check_common(sandbox.ctx(env=env))}["layer2.reference"]
    _flags, r = _run_hint(row.detail, env, tmp_path)
    assert _names_target(r.stderr, sandbox.ref), r.stderr


def test_FXZ_TWIN_a_hint_WITHOUT_the_flags_REFUSES_under_the_same_export(sandbox, tmp_path):
    """The instrument can go red: the same run with the flags stripped is the 4d92732 hint,
    and it must hit the root-conflict refusal -- else the tests above prove nothing."""
    _mini_toolkit(sandbox)
    alt = _alt_tree(tmp_path)
    env = _clean_env(X4_REFERENCE=alt, CODEX_HOME=sandbox.codex_home)
    row = {r.id: r for r in doc.check_common(sandbox.ctx(env=env))}["layer2.reference"]
    m = _REFGUARD_HINT.search(row.detail)
    assert m, row
    r = subprocess.run([sys.executable, m.group(1), m.group(2)], env=env, cwd=tmp_path,
                       capture_output=True, text=True, timeout=120, input="")   # a PIPE: NUL reads as a terminal on Windows
    assert r.returncode == 2 and "DIFFERENT roots" in r.stderr, r.stderr


def test_FXZ_the_PARTIAL_remove_hint_carries_the_same_flags(sandbox, monkeypatch):
    monkeypatch.setattr(doc, "_x4refguard_module", lambda ctx: _refguard_stub("partial"))
    (sandbox.ref / ".unpacked-and-locked").unlink()
    r = _layer2_row(sandbox)
    m = _REFGUARD_HINT.search(r.detail)
    assert r.status == doc.FAIL and m and m.group(2) == "remove", r
    assert "--toolkit" in m.group(3) and "--reference" in m.group(3), r

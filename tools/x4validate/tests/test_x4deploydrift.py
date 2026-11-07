"""`scripts/x4deploydrift.py` -- the session-start warning that a root's deployed agent trees
(`.claude/`, `.codex/`, `.agents/`) no longer match what the toolkit would deploy.

WHY. Nothing compared the deployed copy with the toolkit between deploys: `deploy-claude-dir.py`
checks itself after applying, and `gates/deploy_parity.py` and x4doctor run only when someone runs
them. So a toolkit update that was never deployed left every session in the game root on the old
guards, and nothing said so. The comparison is `deploy_parity.compare_trees` -- the deploy
script's own notion of what is deployed -- never a second implementation.

Contract pinned here: in sync is SILENT; drift is a few lines, directive first, naming the counts
(differ / missing / extra) and the fix command; anything that stops the check is ONE line saying
it could not check, never silence. A cached answer is reused only while every compared file's
size and mtime are unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _layout import claude_hooks_source, require_repo

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "x4deploydrift.py"


def _load():
    spec = importlib.util.spec_from_file_location("x4deploydrift_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FILES = {
    "settings.json": '{"hooks": {}}\n',
    "hooks/guard.sh": "#!/bin/bash\necho guard\n",
    "skills/x4-a/SKILL.md": "cd $CLAUDE_PROJECT_DIR/tools/x4validate\n",
    "agents/helper.md": "helper\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))


def _pair(tmp_path, game_files=None, n_extra_skills=0):
    """A toolkit with a `.claude/` tree and a game root deployed from it (in sync)."""
    tk, root = tmp_path / "tk", tmp_path / "game"
    files = dict(FILES)
    for i in range(n_extra_skills):
        files[f"skills/x4-gen{i}/SKILL.md"] = f"skill {i}\n" * 20
    _write(tk / ".claude", files)
    _write(root / ".claude", files if game_files is None else game_files)
    return tk, root


def _run(tk, root, state=None, env_extra=None):
    cmd = [sys.executable, "-B", str(SCRIPT), str(root), "--toolkit", str(tk)]
    if state is not None:
        cmd += ["--state", str(state)]
    env = dict(os.environ, **(env_extra or {}))
    r = subprocess.run(cmd, capture_output=True, timeout=120, env=env)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def test_in_sync_is_silent(tmp_path):
    tk, root = _pair(tmp_path)
    rc, out, err = _run(tk, root)
    assert (rc, out) == (0, ""), err


def test_one_edited_file_warns_and_names_it(tmp_path):
    tk, root = _pair(tmp_path)
    (root / ".claude" / "hooks" / "guard.sh").write_text("#!/bin/bash\necho edited\n", encoding="utf-8")
    rc, out, _ = _run(tk, root)
    assert rc == 0
    first = out.splitlines()[0]
    assert first.startswith("[x4 deploy]") and "1 differ" in first and "0 missing" in first \
        and "0 extra" in first
    assert "deploy-claude-dir.py --target all --apply" in out
    assert ".claude/hooks/guard.sh" in out


def test_a_missing_file_warns(tmp_path):
    files = dict(FILES)
    del files["agents/helper.md"]
    tk, root = _pair(tmp_path, game_files=files)
    _, out, _ = _run(tk, root)
    assert "0 differ, 1 missing, 0 extra" in out and ".claude/agents/helper.md" in out


def test_an_extra_file_warns(tmp_path):
    tk, root = _pair(tmp_path, game_files=dict(FILES, **{"skills/local/SKILL.md": "mine\n"}))
    _, out, _ = _run(tk, root)
    assert "0 differ, 0 missing, 1 extra" in out and ".claude/skills/local/SKILL.md" in out


def test_the_installer_rewrite_and_line_endings_are_not_drift(tmp_path):
    game = dict(FILES)
    game["skills/x4-a/SKILL.md"] = "cd $X4_TOOLKIT/tools/x4validate\n"
    tk, root = _pair(tmp_path, game_files=game)
    (root / ".claude" / "hooks" / "guard.sh").write_bytes(b"#!/bin/bash\r\necho guard\r\n")
    assert _run(tk, root)[:2] == (0, "")


def test_an_unconfigured_toolkit_says_it_could_not_check(tmp_path):
    _, root = _pair(tmp_path)
    rc, out, _ = _run(tmp_path / "no-toolkit-here", root)
    assert rc == 0 and len(out.splitlines()) == 1 and "could not check" in out


def test_a_root_with_no_deployed_tree_says_it_could_not_check(tmp_path):
    tk, _ = _pair(tmp_path)
    empty = tmp_path / "empty-root"
    empty.mkdir()
    rc, out, _ = _run(tk, empty)
    assert rc == 0 and len(out.splitlines()) == 1 and "could not check" in out


def test_TWIN_a_root_with_an_empty_claude_dir_is_drift_not_silence(tmp_path):
    """Every toolkit file missing from the deployed tree is the loudest drift, never 'in sync'."""
    tk, _ = _pair(tmp_path)
    root = tmp_path / "bare"
    (root / ".claude").mkdir(parents=True)
    _, out, _ = _run(tk, root)
    assert f"0 differ, {len(FILES)} missing, 0 extra" in out


def test_the_toolkit_itself_is_not_a_deployed_copy(tmp_path):
    """The toolkit's own session compares a tree to itself: no deploy relationship, nothing to say."""
    tk, _ = _pair(tmp_path)
    assert _run(tk, tk)[:2] == (0, "")


def test_another_toolkit_tree_is_not_a_deployed_copy(tmp_path):
    """A worktree of the toolkit has its own generated .claude/: comparing it with the main checkout
    would call every unmerged branch change 'drift'. Twin: the same root without the toolkit marker warns."""
    tk, root = _pair(tmp_path)
    (root / ".claude" / "agents" / "helper.md").write_text("branch change\n", encoding="utf-8")
    assert "1 differ" in _run(tk, root)[1], "the control: a plain root with the same edit warns"
    _write(root, {"tools/x4validate/gates/deploy_parity.py": "# a toolkit tree\n"})
    assert _run(tk, root)[:2] == (0, "")


def test_an_AGENTS_md_root_with_no_codex_tree_is_drift(tmp_path):
    """AGENTS.md steers Codex: a root that has it and no .codex/ is the unguarded state (F11)."""
    tk, root = _pair(tmp_path)
    _write(tk, {".codex/hooks/guard.sh": "x\n"})
    (root / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
    _, out, _ = _run(tk, root)
    assert out.startswith("[x4 deploy]") and ".codex/hooks/guard.sh" in out


def test_the_warning_stays_short_however_much_drifted(tmp_path):
    """#38: hook output above ~10,000 characters is silently filed. A few lines, whatever the count."""
    tk, root = _pair(tmp_path, n_extra_skills=500)
    shutil.rmtree(root / ".claude" / "skills")
    _, out, _ = _run(tk, root)
    assert "501 missing" in out
    assert len(out.splitlines()) <= 3 and len(out) < 1200


def test_an_exception_inside_the_check_is_one_could_not_check_line(tmp_path, monkeypatch, capsys):
    mod = _load()
    tk, root = _pair(tmp_path)

    def boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(mod, "_compare", boom)
    assert mod.main([str(root), "--toolkit", str(tk)]) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1 and "could not check" in out and "PermissionError" in out


# ------------------------------------------------------------- the cache and its timing --------------- #

def test_the_state_records_its_own_elapsed_time(tmp_path):
    """So a real post-reboot (cold disk) run is measured with nobody remembering to time it."""
    tk, root = _pair(tmp_path)
    state = tmp_path / "state"
    _run(tk, root, state=state)
    data = json.loads((state / "deploy-drift.json").read_text(encoding="utf-8"))
    t = data["timings"][-1]
    assert isinstance(t["ms"], (int, float)) and t["cached"] is False and t["rows"] == len(FILES)


def test_a_cache_hit_reads_no_file_content(tmp_path, monkeypatch, capsys):
    mod = _load()
    tk, root = _pair(tmp_path, n_extra_skills=60)
    state = tmp_path / "state"
    assert mod.main([str(root), "--toolkit", str(tk), "--state", str(state)]) == 0
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    t0 = time.perf_counter()
    assert mod.main([str(root), "--toolkit", str(tk), "--state", str(state)]) == 0
    elapsed = time.perf_counter() - t0
    assert capsys.readouterr().out == ""
    assert [p for p in reads if ".claude" in p.parts] == []
    data = json.loads((state / "deploy-drift.json").read_text(encoding="utf-8"))
    assert data["timings"][-1]["cached"] is True
    assert elapsed < 1.0, f"a cache hit over {len(FILES) + 60} files took {elapsed:.3f} s"


def test_a_cached_drift_still_warns(tmp_path):
    tk, root = _pair(tmp_path)
    (root / ".claude" / "agents" / "helper.md").write_text("changed\n", encoding="utf-8")
    state = tmp_path / "state"
    first = _run(tk, root, state=state)[1]
    second = _run(tk, root, state=state)[1]
    assert first and first == second


def test_an_edit_after_a_cached_in_sync_run_is_seen(tmp_path):
    tk, root = _pair(tmp_path)
    state = tmp_path / "state"
    assert _run(tk, root, state=state)[1] == ""
    p = root / ".claude" / "agents" / "helper.md"
    p.write_text("HELPER\n", encoding="utf-8")     # the same SIZE: only the mtime moves
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert "1 differ" in _run(tk, root, state=state)[1]


def test_a_toolkit_update_after_a_cached_run_is_seen(tmp_path):
    tk, root = _pair(tmp_path)
    state = tmp_path / "state"
    assert _run(tk, root, state=state)[1] == ""
    _write(tk / ".claude", {"skills/x4-new/SKILL.md": "new\n"})
    assert "1 missing" in _run(tk, root, state=state)[1]


def test_an_unreadable_state_file_is_ignored_not_fatal(tmp_path):
    tk, root = _pair(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "deploy-drift.json").write_text("[not, json", encoding="utf-8")
    assert _run(tk, root, state=state)[:2] == (0, "")


def test_a_full_run_is_fast(tmp_path):
    """The session-start budget. A whole process (interpreter start included) over ~110 files,
    uncached; generous for a slow CI runner, while the real number is logged by every run."""
    tk, root = _pair(tmp_path, n_extra_skills=106)
    t0 = time.perf_counter()
    rc, out, err = _run(tk, root)
    assert (rc, out) == (0, ""), err
    assert time.perf_counter() - t0 < 5.0


# ------------------------------------------------------------- the hook --------------- #

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hook is NOT checked here")

STUB = ("import sys\nprint('[x4 deploy] STUB ' + ' | '.join(sys.argv[1:]))\n")


def _hook_tree(tmp_path, stub=True):
    require_repo("agent/guards/claude-hooks", why="copies the hook source")
    hooks = tmp_path / "proj" / ".claude" / "hooks"
    shutil.copytree(claude_hooks_source(REPO, "the session hooks under test"), hooks)
    tk = tmp_path / "tk"
    (tk / "scripts").mkdir(parents=True)
    if stub:
        (tk / "scripts" / "x4deploydrift.py").write_text(STUB, encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tk), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"),
               X4_HOUSEKEEP_SWEEP="off", X4_FRESH_AUTO="off")
    return hooks, env


def _hook(hooks, env):
    return subprocess.run([BASH, str(hooks / "session-canary.sh")], input=b"{}", capture_output=True,
                          env=env, timeout=120).stdout.decode("utf-8", "replace")


@needs_bash
def test_the_hook_runs_the_toolkits_check_on_its_own_root(tmp_path):
    hooks, env = _hook_tree(tmp_path)
    out = _hook(hooks, env)
    line = next(x for x in out.splitlines() if "STUB" in x)
    args = line.split("STUB ", 1)[1].split(" | ")
    assert Path(args[0]).resolve() == (tmp_path / "proj").resolve()
    assert "--state" in args and Path(args[args.index("--state") + 1]).resolve() == hooks.parent.resolve()


@needs_bash
def test_the_hook_says_it_could_not_check_without_the_toolkits_script(tmp_path):
    hooks, env = _hook_tree(tmp_path, stub=False)
    out = _hook(hooks, env)
    hits = [x for x in out.splitlines() if x.startswith("[x4 deploy]")]
    assert len(hits) == 1 and "could not check" in hits[0]


@needs_bash
def test_the_hook_honours_its_off_switch(tmp_path):
    hooks, env = _hook_tree(tmp_path)
    env["X4_DEPLOY_DRIFT"] = "off"
    assert "[x4 deploy]" not in _hook(hooks, env)

"""`bin/unpack-reference.sh` must run in an install that has NO `.claude/hooks/`.

`install --agent codex` installs the guards as `.codex/hooks/` (a full copy, user decision
#4) and no `.claude/hooks/` at all. The unpack script sourced `.claude/hooks/_x4-env.sh`
unconditionally, so `--unpack` -- and the README's "then run bin/unpack-reference.sh" --
died on a missing file in every Codex-only install (READ 2026-10-02, lane C).

The probe stops at the script's first configuration check (X4_GAME unset -> exit 2 with
its own message), which is only reachable AFTER the env file was sourced -- so nothing is
unpacked and no XRCatTool is needed.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from _layout import require_repo  # noqa: E402  (R2-B1: repo-only content skips, counted)

REPO = Path(__file__).resolve().parents[3]


def _bash():
    spec = importlib.util.spec_from_file_location("gitbash_for_unpack", REPO / "scripts" / "gitbash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_bash()


def _root(tmp_path: Path, guards: tuple[str, ...]) -> Path:
    if guards:
        require_repo("agent/guards/claude-hooks/_x4-env.sh", why="the guard loader SOURCE")
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(REPO / "bin" / "unpack-reference.sh", root / "bin" / "unpack-reference.sh")
    for g in guards:
        (root / g).mkdir(parents=True)
        shutil.copy2(REPO / "agent" / "guards" / "claude-hooks" / "_x4-env.sh", root / g / "_x4-env.sh")
    (root / "x4-paths.env").write_text('X4_REFERENCE="%s"\n' % (root / "reference").as_posix(),
                                       encoding="utf-8")                # the 4.x location
    return root


def _unpack(root: Path, monkeypatch):
    bash = _bash()
    if bash is None:
        pytest.skip("no Git Bash / bash on this machine")
    for k in ("X4_GAME", "X4_TOOLKIT", "X4_CONFIG", "CLAUDE_PROJECT_DIR", "X4_REFERENCE"):
        monkeypatch.delenv(k, raising=False)
    return subprocess.run([bash, (root / "bin" / "unpack-reference.sh").as_posix()],
                          capture_output=True, text=True, timeout=120, cwd=str(root))


@pytest.mark.parametrize("guards", [(".codex/hooks",), (".claude/hooks",), (".claude/hooks", ".codex/hooks"),
                                    (".opencode/hooks",), ("tools/x4_harness",)])
def test_the_env_is_sourced_from_WHICHEVER_guard_copy_is_installed(guards, tmp_path, monkeypatch):
    r = _unpack(_root(tmp_path, guards), monkeypatch)
    assert r.returncode == 2 and "X4_GAME not set" in r.stderr, (r.returncode, r.stdout, r.stderr)


def test_TWIN_with_NO_guard_copy_it_refuses_naming_both_places(tmp_path, monkeypatch):
    r = _unpack(_root(tmp_path, ()), monkeypatch)
    assert r.returncode != 0 and "X4_GAME not set" not in r.stderr
    assert ".claude/hooks" in r.stderr and ".codex/hooks" in r.stderr, r.stderr
    assert ".opencode/hooks" in r.stderr, r.stderr

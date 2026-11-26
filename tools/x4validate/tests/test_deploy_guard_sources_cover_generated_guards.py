"""GUARD-ADJACENT (4.3.1): the deployer's GUARD_SOURCES must cover every generated guard path the
pre-push gate watches.

`deploy-claude-dir.py --apply` refuses when a path in GUARD_SOURCES has uncommitted edits, and the
hook tests run on the commit it checked. Until 4.3.1 that list omitted `.opencode/hooks`, the
OpenCode plugin and `tools/agent-harness.py`, all three generated guard code that
`scripts/prepush-gate.py` already counts as a guard path (`GUARD_PATHS`), so a dirty edit to them
did not stop a deploy.

Both lists are DERIVED from their source files, never retyped here:
  * GUARD_PATHS  -- read from prepush-gate.py (parsed, not imported: another change may be editing it);
  * OWNED        -- the generator's own list of generated paths (guard code + docs/skills);
  * GUARD_SOURCES -- read from deploy-claude-dir.py.
A "generated guard path" is an OWNED path that GUARD_PATHS covers and that is code or config, not
documentation or a skill/agent definition (a `.md` file, or a path under `skills/` or `agents/`).
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path, PurePosixPath

import pytest

from _layout import ROOT, require_repo

PREPUSH = ROOT / "scripts" / "prepush-gate.py"
DEPLOY = ROOT / "tools" / "x4validate" / "scripts" / "deploy-claude-dir.py"
GEN = ROOT / "tools" / "x4validate" / "scripts" / "gen-agent-trees.py"


def _literal(path: Path, name: str):
    """The tuple literal assigned to module-level `name`, read from the source (no import)."""
    tree = ast.parse(path.read_bytes().decode("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} is not a module-level literal in {path.name}")


def _owned() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("gen_for_guard_sources_test", GEN)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return tuple(mod.OWNED)


def _norm(p: str) -> str:
    return p.rstrip("/")


def _covered_by(path: str, root: str) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root + "/")


def generated_guard_paths(owned, guard_paths) -> list[str]:
    """OWNED paths the pre-push gate treats as guard code, minus documentation and skill/agent definitions."""
    out = []
    for o in owned:
        parts = PurePosixPath(_norm(o)).parts
        if o.endswith(".md") or "skills" in parts or "agents" in parts:
            continue
        if any(_covered_by(o, g) for g in guard_paths):
            out.append(o)
    return out


def uncovered(owned, guard_paths, sources) -> list[str]:
    """Generated guard paths no GUARD_SOURCES entry contains."""
    return [g for g in generated_guard_paths(owned, guard_paths)
            if not any(_covered_by(g, s) for s in sources)]


@pytest.fixture(scope="module")
def lists():
    require_repo("scripts/prepush-gate.py", "agent", why="repo-only maintainer gate and generator")
    return _owned(), _literal(PREPUSH, "GUARD_PATHS"), _literal(DEPLOY, "GUARD_SOURCES")


def test_population_is_not_empty_and_names_the_three_gaps(lists):
    owned, paths, _ = lists
    gen = generated_guard_paths(owned, paths)
    assert len(gen) >= 8, gen   # claude hooks+settings, codex hooks+rules, harness x2, opencode hooks+plugin
    for needed in (".opencode/hooks/", ".opencode/plugins/x4guard.js", "tools/agent-harness.py"):
        assert needed in gen, f"{needed} must be a generated guard path (derived): {gen}"


def test_every_generated_guard_path_is_a_guard_source(lists):
    owned, paths, sources = lists
    assert uncovered(owned, paths, sources) == []


@pytest.mark.parametrize("gone", [".opencode/hooks", ".opencode/plugins/x4guard.js", "tools/agent-harness.py"])
def test_twin_removing_a_source_is_caught(lists, gone):
    owned, paths, sources = lists
    assert gone in sources, f"{gone} should be listed in GUARD_SOURCES"
    without = tuple(s for s in sources if s != gone)
    missing = uncovered(owned, paths, without)
    assert missing and all(_covered_by(m, gone) for m in missing), missing


def test_twin_a_new_unlisted_generated_guard_is_caught(lists):
    owned, paths, sources = lists
    now = set(uncovered(owned, paths, sources))
    with_new = set(uncovered(owned + ("tools/new_guard/",), paths + ("tools/",), sources))
    assert with_new - now == {"tools/new_guard/"}

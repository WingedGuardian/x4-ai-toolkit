"""The DEPLOYED guard scripts are not agent-writable, in any tree (4.2.0 review 4).

MEASURED (review 4): protect-files.sh whitelisted `*.claude/hooks/*` for writes -- a v1.0 rule from
when the game root's `.claude/` was where the toolkit itself was developed -- so an agent could
Write over `.claude/hooks/protect-files.sh`, the simplest guard bypass there is. Since the guards
moved under `agent/` (2026-09-30), every deployed copy (`.claude/hooks/`, `.codex/hooks/`,
`.opencode/hooks/`, the OpenCode plugin `.opencode/plugins/x4guard.js`) is produced by the generator
or an installer through the shell, never by an agent file edit; the source under `agent/` stays
editable. Every write is judged single AND batch, and the two must agree.

Final guard review (2026-11-14), both MEASURED: (1) the rule read only the path as written, so a
junction (`proj/jl -> .claude/hooks`, `proj/dotc -> .claude`) walked past it -- the RESOLVED path is
judged too; (2) it refused a user's OWN hook in any `.claude/hooks/` (`~/.claude/hooks/my-hook.sh`).
Now a write in a hook tree is refused when its NAME is one the generator ships into a hook tree, or
when the hook folder already holds a file the toolkit ships (a helper planted next to the guards).
"""
import importlib.util
import os
import re
import subprocess
import time

import pytest

from _layout import claude_hooks_source, require_repo
from codex_testlib import REPO
from test_file_guard_batch import guard  # noqa: F401  (the shared real-backend fixture)

HOOK_TREES = (".claude/hooks/", ".codex/hooks/", ".opencode/hooks/")


_SHIPPED = []


def _shipped_names():
    """Every file NAME the generator writes into a deployed hook tree (its own output, not a list).

    Called by the tests that need it, never at import: an installed toolkit ships no `agent/`
    source tree, and a module-level call failed its whole COLLECTION (4.2.0 pre-push gate, R2B1),
    taking every other test here with it. There the two tests that need the census skip, counted."""
    require_repo("agent", "tools/x4validate/scripts/gen-agent-trees.py",
                 why="the hook-name census is the generator's own output")
    if _SHIPPED:
        return _SHIPPED
    src = REPO / "tools/x4validate/scripts/gen-agent-trees.py"
    spec = importlib.util.spec_from_file_location("gen_agent_trees_hook_names", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.generate(REPO)
    names = {rel.rsplit("/", 1)[1] for rel in out if rel.startswith(HOOK_TREES)}
    assert len(names) > 20, names        # a census, not an empty set that passes everything
    _SHIPPED.extend(sorted(names))
    return _SHIPPED

DENIED = [   # by NAME: no file exists anywhere
    "proj/.claude/hooks/protect-files.sh",
    "proj/.claude/hooks/settings_guard.py",
    "proj/.Claude/Hooks/protect-bash.sh",              # any case
    "proj/.claude/skills/../hooks/protect-files.sh",   # reached through `..`
    "proj/.codex/hooks/codex-entry.sh",
    "proj/.codex/hooks/codex_adapter.py",
    "proj/.opencode/hooks/opencode_adapter.py",
    "proj/.opencode/plugins/x4guard.js",
]
ALLOWED = [
    "proj/.claude/skills/x4-debug/SKILL.md",           # the rest of .claude/ keeps its whitelist
    "proj/.claude/plans/plan.md",
    "proj/.claude/hooks/../skills/x/SKILL.md",         # `..` out of hooks/ is not a hook
    "proj/agent/guards/claude-hooks/protect-files.sh",  # the SOURCE
    "proj/.claude/hooksx/a.sh",                        # a lookalike, no directory boundary
    "proj/hooks/protect-files.sh",
    "proj/.opencode/plugins/my-plugin.js",
    "home/.claude/hooks/my-own-hook.sh",               # a user's own hook, no toolkit files there
    "home/.codex/hooks/my-own-hook.py",
]


def _verdicts(g, paths):
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    assert len(bulk) == len(paths)
    out = []
    for path, batch in zip(paths, bulk):
        single = g.verdict_for("write", None, None, path, save_qualified=False)
        assert single == batch, (path, single, batch)
        assert not single["inert"], single
        out.append(single)
    return out


def _denied(v):
    return v["decision"] == "deny" and "agent/" in v["reason"]


@pytest.mark.parametrize("rel", DENIED)
def test_a_deployed_guard_file_write_is_denied(guard, tmp_path, rel):
    g, _r = guard
    (v,) = _verdicts(g, [str(tmp_path / rel)])
    assert _denied(v), (rel, v)


@pytest.mark.parametrize("rel", ALLOWED)
def test_TWIN_the_rest_is_allowed(guard, tmp_path, rel):
    g, _r = guard
    (v,) = _verdicts(g, [str(tmp_path / rel)])
    assert v["decision"] == "allow", (rel, v)


def test_inside_the_mods_root_and_the_toolkit_too(guard):
    """The mods-root and toolkit whitelists come later and must not reach a deployed guard file."""
    g, r = guard
    paths = [str(r["X4_MODS"] / ".claude/hooks/protect-files.sh"),
             str(r["X4_TOOLKIT"] / ".claude/hooks/protect-files.sh"),
             str(r["X4_TOOLKIT"] / ".codex/hooks/protect-files.sh")]
    assert [v["decision"] for v in _verdicts(g, paths)] == ["deny"] * 3
    twin = [str(r["X4_MODS"] / "mymod/content.xml.bak"), str(r["X4_TOOLKIT"] / "agent/guards/claude-hooks/x.sh")]
    assert [v["decision"] for v in _verdicts(g, twin)] == ["allow"] * 2


# --- (2) the NAME rule is the generator's own output, every name of it ---------------------------

def test_every_shipped_name_is_denied_in_an_empty_user_hook_folder(guard, tmp_path):
    g, _r = guard
    for tree in HOOK_TREES:
        paths = [str(tmp_path / "home" / tree / n) for n in _shipped_names()]
        bad = [(p, v) for p, v in zip(paths, _verdicts(g, paths)) if not _denied(v)]
        assert not bad, bad


def test_the_guard_name_list_is_exactly_the_generator_output():
    """Pins the shell list both ways: a name the generator stops shipping must leave it too."""
    text = (claude_hooks_source(why="the guard under test") / "protect-files.sh").read_text(encoding="utf-8")
    m = re.search(r'^_X4_SHIPPED_HOOK_NAMES="([^"]*)"', text, re.M)
    assert m, "protect-files.sh lost its _X4_SHIPPED_HOOK_NAMES line"
    assert sorted(m.group(1).split()) == _shipped_names()


# --- (2) the FOLDER rule: anything next to toolkit guard files -------------------------------------

@pytest.mark.parametrize("tree", HOOK_TREES)
@pytest.mark.parametrize("marker", ["protect-files.sh", "x4guard.py"])
def test_a_planted_helper_next_to_the_guards_is_denied(guard, tmp_path, tree, marker):
    g, _r = guard
    hooks = tmp_path / "proj" / tree
    hooks.mkdir(parents=True)
    (hooks / marker).write_text("x", encoding="utf-8")
    paths = [str(hooks / "new-support-file.py"), str(hooks / "__pycache__/x4guard.cpython-312.pyc")]
    assert all(_denied(v) for v in _verdicts(g, paths)), paths


def test_TWIN_a_user_hook_folder_without_toolkit_files_is_theirs(guard, tmp_path):
    g, _r = guard
    hooks = tmp_path / "home/.claude/hooks"
    hooks.mkdir(parents=True)
    (hooks / "their-other-hook.sh").write_text("x", encoding="utf-8")
    paths = [str(hooks / "my-own-hook.sh"), str(hooks / "their-other-hook.sh"),
             str(hooks / "sub/helper.py"), str(hooks / "__pycache__/mine.cpython-312.pyc")]
    assert [v["decision"] for v in _verdicts(g, paths)] == ["allow"] * 4


# --- (1) a link to a hook tree is judged on where it RESOLVES --------------------------------------

def _link(kind, link, target):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junctions are Windows-only; the symlink case covers POSIX")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"could not create a junction: {r.stderr.strip()}")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("native Windows symlink privilege unavailable; the junction case covers Windows")
        raise


def _linked_project(kind, tmp_path):
    proj = tmp_path / "lp"
    hooks = proj / ".claude/hooks"
    hooks.mkdir(parents=True)
    (proj / ".claude/skills").mkdir()
    (hooks / "protect-files.sh").write_text("x", encoding="utf-8")
    _link(kind, proj / "jl", hooks)
    _link(kind, proj / ".claude/skills/jl", hooks)
    _link(kind, proj / "dotc", proj / ".claude")
    return proj


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_a_link_into_a_guard_tree_is_denied(guard, tmp_path, kind):
    g, _r = guard
    proj = _linked_project(kind, tmp_path)
    paths = [str(proj / "jl/protect-files.sh"), str(proj / "jl/planted.py"),
             str(proj / ".claude/skills/jl/protect-files.sh"), str(proj / ".claude/skills/jl/planted.py"),
             str(proj / "dotc/hooks/protect-files.sh"), str(proj / "dotc/hooks/planted.py")]
    bad = [(p, v["decision"]) for p, v in zip(paths, _verdicts(g, paths)) if not _denied(v)]
    assert not bad, bad


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_TWIN_a_link_elsewhere_and_into_a_user_hook_folder_is_allowed(guard, tmp_path, kind):
    g, _r = guard
    proj = tmp_path / "lp"
    (proj / "plain").mkdir(parents=True)
    user_hooks = tmp_path / "home/.claude/hooks"
    user_hooks.mkdir(parents=True)
    _link(kind, proj / "jl", proj / "plain")
    _link(kind, proj / "ul", user_hooks)
    paths = [str(proj / "jl/notes.md"), str(proj / "ul/my-own-hook.sh")]
    assert [v["decision"] for v in _verdicts(g, paths)] == ["allow"] * 2


def test_the_msys_spelling_of_a_junction_is_denied(guard, tmp_path, monkeypatch):
    """Claude Code hands protect-files.sh the path as written; x4guard's abspath would turn `/c/...`
    into `C:\\c\\...` on Windows (a different file), so it is bypassed to reach the shell rule."""
    if os.name != "nt":
        pytest.skip("the /c/... drive spelling exists only on Windows")
    g, _r = guard
    monkeypatch.setattr(g.os.path, "abspath", lambda p: p)
    proj = _linked_project("junction", tmp_path)
    p = str(proj / "jl/protect-files.sh").replace("\\", "/")
    msys = "/" + p[0].lower() + p[2:]
    (v,) = _verdicts(g, [msys])
    assert _denied(v), (msys, v)

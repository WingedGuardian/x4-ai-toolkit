"""The DEPLOYED guard scripts are not agent-writable, in any tree (4.2.0 review 4).

MEASURED (review 4): protect-files.sh whitelisted `*.claude/hooks/*` for writes -- a v1.0 rule from
when the game root's `.claude/` was where the toolkit itself was developed -- so an agent could
Write over `.claude/hooks/protect-files.sh`, the simplest guard bypass there is. Since the guards
moved under `agent/` (2026-09-30), every deployed copy (`.claude/hooks/`, `.codex/hooks/`,
`.opencode/hooks/`, the OpenCode plugin `.opencode/plugins/x4guard.js`) is produced by the generator
or an installer through the shell, never by an agent file edit; the source under `agent/` stays
editable. Every write is judged single AND batch, and the two must agree.
"""
import time

import pytest

from test_file_guard_batch import guard  # noqa: F401  (the shared real-backend fixture)

DENIED = [
    "proj/.claude/hooks/protect-files.sh",
    "proj/.claude/hooks/settings_guard.py",
    "proj/.claude/hooks/new-support-file.py",          # a support file, new or not
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


@pytest.mark.parametrize("rel", DENIED)
def test_a_deployed_guard_file_write_is_denied(guard, tmp_path, rel):
    g, _r = guard
    (v,) = _verdicts(g, [str(tmp_path / rel)])
    assert v["decision"] == "deny" and "agent/" in v["reason"], (rel, v)


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

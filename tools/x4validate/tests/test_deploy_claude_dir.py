"""`scripts/deploy-claude-dir.py` -- repo `.claude/` into a game root, one direction.

Every case runs against a SCRATCH git repo and a scratch destination, never the real game
root. One test per clause, because each guard shadows the ones behind it.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
SRC = PKG / "scripts" / "deploy-claude-dir.py"


def _load():
    import sys
    spec = importlib.util.spec_from_file_location("deploy_claude_dir_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod     # @dataclass needs its module registered (see the script)
    spec.loader.exec_module(mod)
    return mod


dep = _load()


def _git(root: Path, *args: str) -> None:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".claude" / "skills" / "a").mkdir(parents=True)
    try:
        _git(root, "init", "-q")
    except (OSError, AssertionError):
        pytest.skip("git unavailable -- the deploy's history check cannot be exercised")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    return root


def _commit(repo: Path, rel: str, content: bytes, msg: str = "c") -> None:
    p = repo / ".claude" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    _git(repo, "add", "--", f".claude/{rel}")
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def dest(tmp_path):
    """A destination WITHOUT tools/x4validate beside it -- the game-root layout."""
    d = tmp_path / "game" / ".claude"
    d.mkdir(parents=True)
    return d


def _kinds(actions):
    return {a.name: a.kind for a in actions}


def test_a_dry_run_writes_nothing(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    rc = dep.main(["--repo", str(repo), "--dest", str(dest)])
    assert rc == 0
    assert not (dest / "skills" / "a" / "SKILL.md").exists()


def test_apply_creates_a_missing_file(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "skills" / "a" / "SKILL.md").read_bytes() == b"v1\n"


def test_a_STALE_deploy_that_matches_an_older_commit_is_updated(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    _commit(repo, "skills/a/SKILL.md", b"v2\n")
    (dest / "skills" / "a").mkdir(parents=True)
    (dest / "skills" / "a" / "SKILL.md").write_bytes(b"v1\n")
    assert _kinds(dep.plan(repo, dest))["skills/a/SKILL.md"] == dep.UPDATE


def test_a_NEVER_PORTED_local_edit_is_REFUSED_and_left_untouched(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    (dest / "skills" / "a").mkdir(parents=True)
    (dest / "skills" / "a" / "SKILL.md").write_bytes(b"v1\nlocal rung nobody ported\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 1
    assert (dest / "skills" / "a" / "SKILL.md").read_bytes() == b"v1\nlocal rung nobody ported\n"


def test_force_overrides_the_refusal_for_that_one_file(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    (dest / "skills" / "a").mkdir(parents=True)
    (dest / "skills" / "a" / "SKILL.md").write_bytes(b"hand-edited\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply",
                     "--force", "skills/a/SKILL.md"]) == 0
    assert (dest / "skills" / "a" / "SKILL.md").read_bytes() == b"v1\n"


def test_force_naming_a_file_outside_the_deployment_is_a_refusal(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--force", "nope.md"]) == 2


def test_the_rewrite_is_applied_when_the_toolkit_is_NOT_beside_the_destination(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"cd $CLAUDE_PROJECT_DIR/tools/x4validate\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "skills" / "a" / "SKILL.md").read_bytes() == b"cd $X4_TOOLKIT/tools/x4validate\n"


def test_HOOKS_and_SETTINGS_are_never_rewritten(repo, dest):
    """The bug the first real dry run found: an unscoped rewrite planned to point the game
    root's settings.json at the REPO's hooks. install.sh rewrites skills and agents only."""
    _commit(repo, "settings.json", b'"bash $CLAUDE_PROJECT_DIR/.claude/hooks/h.sh"\n')
    _commit(repo, "hooks/h.sh", b". $CLAUDE_PROJECT_DIR/.claude/hooks/_env.sh\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "settings.json").read_bytes() == b'"bash $CLAUDE_PROJECT_DIR/.claude/hooks/h.sh"\n'
    assert (dest / "hooks" / "h.sh").read_bytes() == b". $CLAUDE_PROJECT_DIR/.claude/hooks/_env.sh\n"


def test_an_identical_unrewritten_HOOK_is_left_alone(repo, dest):
    """The game root's hooks today: byte-identical to the repo. Must be 'identical', not
    an update that rewrites them."""
    _commit(repo, "hooks/h.sh", b". $CLAUDE_PROJECT_DIR/x\n")
    (dest / "hooks").mkdir(parents=True)
    (dest / "hooks" / "h.sh").write_bytes(b". $CLAUDE_PROJECT_DIR/x\n")
    assert _kinds(dep.plan(repo, dest))["hooks/h.sh"] == dep.SKIP


def test_a_hand_REWRITTEN_hook_is_refused_not_recognised(repo, dest):
    """The installer never produces a rewritten hook, so one is a local edit."""
    _commit(repo, "hooks/h.sh", b". $CLAUDE_PROJECT_DIR/x\n")
    (dest / "hooks").mkdir(parents=True)
    (dest / "hooks" / "h.sh").write_bytes(b". $X4_TOOLKIT/x\n")
    assert _kinds(dep.plan(repo, dest))["hooks/h.sh"] == dep.REFUSE


def test_the_rewrite_is_NOT_applied_when_the_toolkit_IS_beside_the_destination(repo, tmp_path):
    d = tmp_path / "inplace" / ".claude"
    d.mkdir(parents=True)
    (tmp_path / "inplace" / "tools" / "x4validate").mkdir(parents=True)
    _commit(repo, "skills/a/SKILL.md", b"cd $CLAUDE_PROJECT_DIR/tools/x4validate\n")
    assert dep.main(["--repo", str(repo), "--dest", str(d), "--apply"]) == 0
    assert (d / "skills" / "a" / "SKILL.md").read_bytes() == b"cd $CLAUDE_PROJECT_DIR/tools/x4validate\n"


def test_a_deployed_UNREWRITTEN_copy_is_a_known_version_and_gets_rewritten(repo, dest):
    """The game root's five skills today: identical to the repo, never rewritten."""
    _commit(repo, "skills/a/SKILL.md", b"cd $CLAUDE_PROJECT_DIR/x\n")
    (dest / "skills" / "a").mkdir(parents=True)
    (dest / "skills" / "a" / "SKILL.md").write_bytes(b"cd $CLAUDE_PROJECT_DIR/x\n")
    assert _kinds(dep.plan(repo, dest))["skills/a/SKILL.md"] == dep.UPDATE


def test_a_CRLF_destination_stays_CRLF(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"one\ntwo\n")
    _commit(repo, "skills/a/SKILL.md", b"one\ntwo\nthree\n")
    (dest / "skills" / "a").mkdir(parents=True)
    (dest / "skills" / "a" / "SKILL.md").write_bytes(b"one\r\ntwo\r\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "skills" / "a" / "SKILL.md").read_bytes() == b"one\r\ntwo\r\nthree\r\n"


def test_a_read_only_destination_is_written_and_RE_LOCKED(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    _commit(repo, "skills/a/SKILL.md", b"v2\n")
    f = dest / "skills" / "a" / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"v1\n")
    os.chmod(f, stat.S_IREAD)
    try:
        assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
        assert f.read_bytes() == b"v2\n"
        assert not (f.stat().st_mode & stat.S_IWRITE), "the lock was not restored"
    finally:
        os.chmod(f, stat.S_IREAD | stat.S_IWRITE)


def test_a_file_only_in_the_destination_is_REPORTED_never_deleted(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    (dest / "skills" / "local").mkdir(parents=True)
    (dest / "skills" / "local" / "SKILL.md").write_bytes(b"mine\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "skills" / "local" / "SKILL.md").read_bytes() == b"mine\n"
    assert _kinds(dep.plan(repo, dest))["skills/local/SKILL.md"] == dep.REPORT


def test_per_machine_files_are_never_touched(repo, dest):
    """settings.local.json is per-machine and never in the plan. (The 3.x x4-paths.env is
    the one exception, and it is MOVED, never edited: the rows below.)"""
    _commit(repo, "settings.json", b"{}\n")
    (dest / "settings.local.json").write_bytes(b"{\"mine\": 1}\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "settings.local.json").read_bytes() == b"{\"mine\": 1}\n"
    assert "settings.local.json" not in _kinds(dep.plan(repo, dest))


# --- Plan 3 lane I: a game root's 3.x path config is MOVED to the root ---------------- #

def test_deploy_plans_a_MIGRATE_for_a_3x_config_and_never_an_update(repo, dest):
    _commit(repo, "settings.json", b"{}\n")
    body = b'X4_GAME="here"\nX4_NEXUS_KEY="secret"\n'
    (dest / "x4-paths.env").write_bytes(body)
    acts = [a for a in dep.plan(repo, dest) if "x4-paths.env" in a.name]
    assert [a.kind for a in acts] == [dep.MIGRATE], acts
    assert b"secret" not in acts[0].reason.encode()
    assert dep.main(["--repo", str(repo), "--dest", str(dest)]) == 0        # dry run
    assert (dest / "x4-paths.env").read_bytes() == body
    assert not (dest.parent / "x4-paths.env").exists()
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest.parent / "x4-paths.env").read_bytes() == body
    assert not (dest / "x4-paths.env").exists()


def test_deploy_REPORTS_two_differing_configs_and_touches_neither(repo, dest):
    _commit(repo, "settings.json", b"{}\n")
    (dest / "x4-paths.env").write_bytes(b'X4_GAME="old"\n')
    (dest.parent / "x4-paths.env").write_bytes(b'X4_GAME="new"\n')
    acts = [a for a in dep.plan(repo, dest) if "x4-paths.env" in a.name]
    assert [a.kind for a in acts] == [dep.CONFLICT] and "X4_GAME" in acts[0].reason, acts
    # v4.0.0 review R4-9: REPORTED and still rc 0, so a scripted deploy read "done" while two
    # configs that protect DIFFERENT trees stood side by side. Non-zero, dry run and apply.
    assert dep.main(["--repo", str(repo), "--dest", str(dest)]) == 1
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 1
    assert (dest / "x4-paths.env").read_bytes() == b'X4_GAME="old"\n'
    assert (dest.parent / "x4-paths.env").read_bytes() == b'X4_GAME="new"\n'


def test_deploy_retires_an_AGREEING_3x_copy(repo, dest):
    _commit(repo, "settings.json", b"{}\n")
    (dest / "x4-paths.env").write_bytes(b'X4_GAME="same"\n')
    (dest.parent / "x4-paths.env").write_bytes(b'X4_GAME="same"\n')
    acts = [a for a in dep.plan(repo, dest) if "x4-paths.env" in a.name]
    assert [a.kind for a in acts] == [dep.MIGRATE], acts
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert not (dest / "x4-paths.env").exists()
    assert len(list(dest.glob("x4-paths.env.bak-*"))) == 1


def test_deploy_with_no_3x_config_plans_nothing_for_it(repo, dest):
    _commit(repo, "settings.json", b"{}\n")
    (dest.parent / "x4-paths.env").write_bytes(b'X4_GAME="new"\n')
    assert not [a for a in dep.plan(repo, dest) if "x4-paths.env" in a.name]


def test_after_apply_the_parity_gate_agrees(repo, dest):
    _commit(repo, "skills/a/SKILL.md", b"cd $CLAUDE_PROJECT_DIR/x\n")
    _commit(repo, "hooks/h.sh", b"echo hi\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    rows = dep.parity.compare_trees(repo / ".claude", dest)
    assert rows and all(r.at_parity for r in rows), [(r.name, r.state) for r in rows]


def test_the_destination_being_the_repo_is_a_refusal(repo):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    assert dep.main(["--repo", str(repo), "--dest", str(repo / ".claude")]) == 2


def test_a_plan_WRITE_disagreement_after_apply_is_rc_1(repo, dest, monkeypatch):
    """The shape of the plan()/target_bytes() split found once already: printed, rc 0."""
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    monkeypatch.setattr(dep, "target_bytes", lambda r, d, n, *rest: b"not what the plan said\n")
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 1


def test_a_CREATED_file_in_the_lock_manifest_is_locked(repo, dest, monkeypatch):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    f = dest / "skills" / "a" / "SKILL.md"
    monkeypatch.setattr(dep.x4lock, "manifest", lambda: [f])
    try:
        assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
        assert not (f.stat().st_mode & stat.S_IWRITE), "a created, manifest-covered file was left writable"
    finally:
        if f.exists():
            os.chmod(f, stat.S_IREAD | stat.S_IWRITE)


def test_a_CREATED_file_outside_the_manifest_stays_writable(repo, dest, monkeypatch):
    _commit(repo, "skills/a/SKILL.md", b"v1\n")
    monkeypatch.setattr(dep.x4lock, "manifest", lambda: [])
    assert dep.main(["--repo", str(repo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "skills" / "a" / "SKILL.md").stat().st_mode & stat.S_IWRITE


# --- --target codex (F11, 2026-10-20) ------------------------------------------------- #
# MEASURED 2026-10-20: the script deployed .claude/ only, so a Codex session in the game root
# it served ran with no guard hooks. --target codex deploys .codex/hooks, .codex/rules,
# .agents/skills and a RENDERED .codex/hooks.json, by the same safety rules.

import json  # noqa: E402

_RENDERER = dep.REPO_ROOT / dep.parity.CODEX_RENDERER
_TMPL_V1 = json.dumps({"hooks": {"SessionStart": [{"hooks": [{
    "type": "command", "command": 'bash "{{ROOT}}/.codex/hooks/codex-entry.sh" session_start'}]}]}},
    indent=2) + "\n"
_TMPL_V2 = _TMPL_V1.replace("session_start", "session_start --v2")
_TOKEN_VALUE = "$env:X4_TOOLKIT" if os.name == "nt" else "$X4_TOOLKIT"


def _commit_at(repo: Path, rel: str, content: bytes, msg: str = "c") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    _git(repo, "add", "--", rel)
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def crepo(repo):
    """A source tree carrying the Codex target, its template and the real renderer."""
    _commit_at(repo, dep.parity.CODEX_RENDERER, _RENDERER.read_bytes())
    _commit_at(repo, dep.parity.CODEX_TEMPLATE, _TMPL_V1.encode())
    _commit_at(repo, ".codex/hooks/h.sh", b"echo hi\n")
    _commit_at(repo, ".codex/rules/x4.rules", b"rule\n")
    _commit_at(repo, ".agents/skills/a/SKILL.md", b"cd {{TOOLKIT}}/tools\n")
    _commit(repo, "hooks/c.sh", b"x\n")
    return repo


def _codex(repo, dest, *extra):
    return dep.main(["--repo", str(repo), "--dest", str(dest), "--target", "codex", *extra])


def test_codex_dry_run_writes_nothing(crepo, dest):
    assert _codex(crepo, dest) == 0
    assert not (dest.parent / ".codex").exists() and not (dest.parent / ".agents").exists()


def test_codex_apply_creates_hooks_rules_skills_and_a_RENDERED_hooks_json(crepo, dest, capsys):
    root = dest.parent
    assert _codex(crepo, dest, "--apply") == 0
    assert (root / ".codex/hooks/h.sh").read_bytes() == b"echo hi\n"
    assert (root / ".codex/rules/x4.rules").read_bytes() == b"rule\n"
    assert (root / ".agents/skills/a/SKILL.md").read_bytes() == \
        ("cd " + _TOKEN_VALUE + "/tools\n").encode()
    hooks = json.loads((root / ".codex/hooks.json").read_text(encoding="utf-8"))
    cmd = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert str(root.resolve()).replace(chr(92), "/") in cmd and "{{" not in cmd
    out = capsys.readouterr().out
    assert "/hooks" in out, "a NEW hooks.json must say Codex needs it approved"


def test_TWIN_default_target_is_still_claude_only(crepo, dest):
    assert dep.main(["--repo", str(crepo), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "hooks" / "c.sh").is_file()
    assert not (dest.parent / ".codex").exists()


def test_target_all_deploys_both(crepo, dest):
    assert dep.main(["--repo", str(crepo), "--dest", str(dest), "--target", "all", "--apply"]) == 0
    assert (dest / "hooks" / "c.sh").is_file() and (dest.parent / ".codex/hooks/h.sh").is_file()


def test_an_UNCHANGED_hooks_json_is_left_BYTE_IDENTICAL(crepo, dest, capsys):
    """Codex skips a CHANGED hooks.json until the user re-approves it in /hooks, so an
    equal-but-reformatted one (canonical JSON) is never rewritten."""
    root = dest.parent
    data = json.loads(dep.parity.render_rendered(crepo, dep.parity.CODEX_TEMPLATE, root))
    body = json.dumps(data, indent=8).replace("\n", "\r\n").encode()
    (root / ".codex").mkdir()
    (root / ".codex/hooks.json").write_bytes(body)
    kinds = _kinds(dep.plan(crepo, root, spec=dep.parity.CODEX))
    assert kinds[".codex/hooks.json"] == dep.SKIP
    assert _codex(crepo, dest, "--apply") == 0
    assert (root / ".codex/hooks.json").read_bytes() == body
    assert "re-approve" not in capsys.readouterr().out


def test_TWIN_an_OLDER_shipped_hooks_json_is_UPDATED_and_says_so(crepo, dest, capsys):
    root = dest.parent
    old = dep.parity.render_rendered(crepo, dep.parity.CODEX_TEMPLATE, root)
    _commit_at(crepo, dep.parity.CODEX_TEMPLATE, _TMPL_V2.encode())
    (root / ".codex").mkdir()
    (root / ".codex/hooks.json").write_text(old, encoding="utf-8")
    assert _kinds(dep.plan(crepo, root, spec=dep.parity.CODEX))[".codex/hooks.json"] == dep.UPDATE
    assert _codex(crepo, dest, "--apply") == 0
    assert "--v2" in (root / ".codex/hooks.json").read_text(encoding="utf-8")
    assert "re-approve" in capsys.readouterr().out


def test_TWIN_a_CUSTOM_hooks_json_is_REFUSED_and_left_untouched(crepo, dest):
    root = dest.parent
    (root / ".codex").mkdir()
    (root / ".codex/hooks.json").write_bytes(b'{"hooks": {"Mine": []}}\n')
    assert _codex(crepo, dest, "--apply") == 1
    assert (root / ".codex/hooks.json").read_bytes() == b'{"hooks": {"Mine": []}}\n'


def test_codex_per_machine_config_and_AGENTS_md_are_never_touched(crepo, dest, capsys):
    root = dest.parent
    (root / ".codex").mkdir()
    (root / ".codex/config.toml").write_bytes(b"project_doc_max_bytes = 65536\n")
    (root / "AGENTS.md").write_bytes(b"# mine\n")
    assert _codex(crepo, dest, "--apply") == 0
    assert (root / ".codex/config.toml").read_bytes() == b"project_doc_max_bytes = 65536\n"
    assert (root / "AGENTS.md").read_bytes() == b"# mine\n"
    names = _kinds(dep.plan(crepo, root, spec=dep.parity.CODEX))
    assert ".codex/config.toml" not in names and "AGENTS.md" not in names
    assert "AGENTS.md" in capsys.readouterr().out, "the AGENTS.md state must be reported"


def test_a_deployed_UNRENDERED_codex_skill_is_UPDATED_not_skipped(crepo, dest):
    root = dest.parent
    (root / ".agents/skills/a").mkdir(parents=True)
    (root / ".agents/skills/a/SKILL.md").write_bytes(b"cd {{TOOLKIT}}/tools\n")
    assert _kinds(dep.plan(crepo, root, spec=dep.parity.CODEX))[".agents/skills/a/SKILL.md"] == dep.UPDATE


def test_TWIN_a_hand_edited_codex_hook_is_REFUSED(crepo, dest):
    root = dest.parent
    (root / ".codex/hooks").mkdir(parents=True)
    (root / ".codex/hooks/h.sh").write_bytes(b"echo local edit\n")
    assert _kinds(dep.plan(crepo, root, spec=dep.parity.CODEX))[".codex/hooks/h.sh"] == dep.REFUSE


def test_after_codex_apply_the_parity_gate_agrees(crepo, dest):
    root = dest.parent
    assert _codex(crepo, dest, "--apply") == 0
    rows = dep.parity.compare_trees(crepo, root, dep.parity.CODEX)
    assert rows and all(r.at_parity for r in rows), [(r.name, r.state) for r in rows]


def test_codex_into_the_repo_itself_is_a_refusal(crepo):
    assert dep.main(["--repo", str(crepo), "--root", str(crepo), "--target", "codex"]) == 2


def test_the_ACTION_line_is_printed_only_when_hooks_json_was_really_written(crepo, dest, capsys):
    """Review LOW-3: 'ACTION FOR YOU: .codex/hooks.json was written' printed whenever the plan
    held a create/update, even when the write FAILED. A directory in its place makes it fail."""
    (dest.parent / ".codex" / "hooks.json").mkdir(parents=True)
    assert _codex(crepo, dest, "--apply") == 1
    out = capsys.readouterr()
    assert "ACTION FOR YOU" not in out.out
    assert ".codex/hooks.json" in out.err


def test_TWIN_the_ACTION_line_is_printed_after_a_real_write(crepo, dest, capsys):
    assert _codex(crepo, dest, "--apply") == 0
    assert "ACTION FOR YOU" in capsys.readouterr().out


def test_a_named_dest_that_does_not_exist_is_REFUSED(repo, tmp_path, capsys):
    """Review LOW-4: before F11 a missing --dest was created from nothing (rc 0)."""
    _commit(repo, "skills/a/SKILL.md", b"v1"+bytes([10]))
    d = tmp_path / "typo" / ".claude"
    assert dep.main(["--repo", str(repo), "--dest", str(d), "--apply"]) == 2
    assert not d.exists() and "--dest" in capsys.readouterr().err

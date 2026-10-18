"""Ordinary upgrades must not discard user configuration before review."""
import json
import subprocess

import pytest

from _layout import require_repo
from test_install_over_existing import ROOT, _fresh, _install, _ok


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("method", ["separate", "in-game"])
@pytest.mark.parametrize("customization", ["permissions", "pre_handler", "post_handler", "matcher", "timeout"])
def test_custom_project_settings_refuse_before_any_destination_write(tmp_path, installer, method, customization):
    require_repo(".claude/settings.json", why="an install without Claude ships no settings to customise")
    dest = _fresh(tmp_path)
    if method == "in-game":
        dest = tmp_path / "game"
    settings = json.loads((ROOT / ".claude/settings.json").read_bytes())
    if customization == "permissions":
        settings["permissions"] = {"deny": ["personal restriction"]}
    elif customization in ("matcher", "timeout"):
        group = settings["hooks"]["PreToolUse"][0]
        if customization == "matcher":
            group["matcher"] = "Write"
        else:
            group["hooks"][0]["timeout"] = 1
    else:
        event = "PreToolUse" if customization == "pre_handler" else "PostToolUse"
        settings.setdefault("hooks", {}).setdefault(event, []).append(
            {"matcher": ".*", "hooks": [{"type": "command", "command": "personal policy", "timeout": 15}]})
    target = dest / ".claude/settings.json"
    target.parent.mkdir()
    target.write_text(json.dumps(settings))
    (dest / "AGENTS.md").write_bytes(b"user instructions before copy")
    (dest / "CLAUDE.md").write_bytes(b"user claude instructions before copy")
    before = {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob("*") if p.is_file()}
    result = _install(installer, tmp_path, dest, "--agent", "all", method=method)
    assert result.returncode != 0, _ok(result)
    assert "custom project settings" in (result.stdout + result.stderr).lower(), _ok(result)
    assert "upgrade-stage" in result.stdout + result.stderr, _ok(result)
    assert {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_exact_shipped_three_x_settings_can_upgrade(tmp_path, installer):
    require_repo(".git", ".claude/settings.json", why="reads the v3.3.1 tag's settings from git history")
    dest = _fresh(tmp_path)
    target = dest / ".claude/settings.json"
    target.parent.mkdir()
    tagged = subprocess.check_output(["git", "-C", str(ROOT), "show", "v3.3.1:.claude/settings.json"])
    target.write_bytes(tagged)
    result = _install(installer, tmp_path, dest, "--agent", "claude")
    assert result.returncode == 0, _ok(result)
    assert json.loads(target.read_bytes()) == json.loads((ROOT / ".claude/settings.json").read_bytes())


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_generic_install_does_not_adopt_or_replace_claude_custom_settings(tmp_path, installer):
    dest = _fresh(tmp_path)
    target = dest / ".claude/settings.json"
    target.parent.mkdir()
    target.write_bytes(b'{"permissions":{"deny":["personal"]}}')
    result = _install(installer, tmp_path, dest, "--agent", "generic")
    assert result.returncode == 0, _ok(result)
    assert target.read_bytes() == b'{"permissions":{"deny":["personal"]}}'


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("customization", ["windows_command", "pre_handler", "post_handler"])
def test_custom_codex_definitions_refuse_before_any_destination_write(tmp_path, installer, customization):
    from test_gen_agent_trees import load
    dest = _fresh(tmp_path)
    settings = json.loads(load().render_codex_hooks_json(dest))
    if customization == "windows_command":
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"] = "personal windows policy"
    else:
        event = "PreToolUse" if customization == "pre_handler" else "PostToolUse"
        settings["hooks"].setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": "personal policy"}]})
    target = dest / ".codex/hooks.json"
    target.parent.mkdir()
    target.write_text(json.dumps(settings))
    (dest / "AGENTS.md").write_bytes(b"private instructions before upgrade")
    before = {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob("*") if p.is_file()}
    result = _install(installer, tmp_path, dest, "--agent", "codex")
    assert result.returncode != 0, _ok(result)
    assert "custom codex hook definitions" in (result.stdout + result.stderr).lower(), _ok(result)
    assert {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_semantically_unchanged_codex_definitions_remain_byte_exact_and_readonly(tmp_path, installer):
    from test_gen_agent_trees import load
    dest = _fresh(tmp_path)
    settings = json.loads(load().render_codex_hooks_json(dest))
    target = dest / ".codex/hooks.json"
    target.parent.mkdir()
    raw = (json.dumps(settings, sort_keys=True) + "\n").encode("utf-8")
    target.write_bytes(raw)
    target.chmod(0o444)
    mode = target.stat().st_mode
    try:
        result = _install(installer, tmp_path, dest, "--agent", "codex")
        assert result.returncode == 0, _ok(result)
        assert target.read_bytes() == raw and target.stat().st_mode == mode
    finally:
        target.chmod(0o644)

"""An agent may not CHANGE the protected-root keys in a Claude Code settings file (4.2).

A settings `env` block reaches every hook process, and the environment outranks x4-paths.env, so
`"env": {"X4_REFERENCE": "<elsewhere>"}` (or X4_GAME, X4_CONFIG, ..., or X4_STAGING_DIRS naming a
game folder) written by an agent would move or widen what the guards protect at the next launch.
The rule is a DELTA: the global install mode writes X4_TOOLKIT, X4_GAME, X4_REFERENCE, X4_PROFILE,
X4_MODS and X4_EXTENSIONS into settings.json itself, so an edit that leaves those exactly as they
are stays allowed; one that adds, removes or changes one (in an `env` block at any depth, or as a
`KEY=` assignment in a hook command) is denied. Reading a key (`$X4_TOOLKIT/...`) is not setting it.
Every case has a twin that must stay allowed.
"""
from __future__ import annotations

import importlib.util
import json

import pytest

from _layout import claude_hooks_source
from codex_testlib import native, run_adapter
from test_settings_guard import _hook, needs_bash, sandbox  # noqa: F401  (shared fixture)


def _sg():
    path = claude_hooks_source(why="the settings guard") / "settings_guard.py"
    spec = importlib.util.spec_from_file_location("settings_guard_roots", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SG = _sg()
ROOT_KEYS = ["X4_STAGING_DIRS", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_SAVES", "X4_MODS",
             "X4_CONFIG", "X4_TOOLKIT", "X4_EXTENSIONS", "X4_DOCUMENTS"]
INSTALLED = {"env": {"X4_TOOLKIT": "/tk", "X4_GAME": "/game", "X4_REFERENCE": "/tk/reference",
                     "OTHER": "1"},
             "permissions": {"allow": []}}


@pytest.fixture
def settings(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps(INSTALLED, indent=2), encoding="utf-8")
    return p


def _write(path, doc):
    return SG.judge({"file_path": str(path), "content": json.dumps(doc, indent=2)})


@pytest.mark.parametrize("key", ROOT_KEYS)
def test_adding_a_root_key_to_env_is_denied(settings, key):
    doc = json.loads(json.dumps(INSTALLED))
    if key in doc["env"]:
        doc["env"][key] = "/somewhere/else"
    else:
        doc["env"][key] = "/somewhere"
    why = _write(settings, doc)
    assert why and key in why, why


def test_TWIN_keeping_the_installed_keys_and_editing_elsewhere_is_allowed(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["permissions"]["allow"] = ["Bash(ls)"]
    doc["env"]["OTHER"] = "2"
    assert _write(settings, doc) == ""


def test_removing_a_root_key_is_denied(settings):
    doc = json.loads(json.dumps(INSTALLED))
    del doc["env"]["X4_REFERENCE"]
    assert "X4_REFERENCE" in _write(settings, doc)


def test_a_nested_env_block_counts(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["hooks"] = {"PreToolUse": [{"env": {"X4_STAGING_DIRS": "libraries"}}]}
    assert "X4_STAGING_DIRS" in _write(settings, doc)


def test_a_hook_command_assignment_is_denied_and_a_read_is_allowed(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["hooks"] = {"PreToolUse": [{"hooks": [{"type": "command",
                                               "command": "X4_REFERENCE=/tmp bash x.sh"}]}]}
    assert "X4_REFERENCE" in _write(settings, doc)
    doc["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = 'bash "$X4_TOOLKIT/.claude/hooks/x.sh"'
    assert _write(settings, doc) == ""


def test_TWIN_a_permissions_rule_naming_a_key_sets_nothing(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["permissions"]["allow"] = ["Bash(export X4_GAME=/x)"]
    assert _write(settings, doc) == ""


def test_claude_edit_is_judged_on_the_result(settings):
    text = settings.read_text(encoding="utf-8")
    bad = {"file_path": str(settings), "old_string": '"/game"', "new_string": '"/elsewhere"'}
    ok = {"file_path": str(settings), "old_string": '"OTHER": "1"', "new_string": '"OTHER": "3"'}
    assert '"/game"' in text
    assert "X4_GAME" in SG.judge(bad)
    assert SG.judge(ok) == ""


def test_edit_that_does_not_apply_is_judged_on_its_new_text(settings):
    bad = {"file_path": str(settings), "old_string": "nowhere", "new_string": '"X4_CONFIG": "/x"'}
    ok = {"file_path": str(settings), "old_string": "nowhere", "new_string": '"OTHER": "4"'}
    assert "X4_CONFIG" in SG.judge(bad)
    assert SG.judge(ok) == ""


def test_opencode_edit_adding_a_key_is_denied(settings):
    bad = {"file_path": str(settings), "x4_edit": {"old": '"OTHER": "1"',
                                                   "new": '"OTHER": "1", "X4_SAVES": "/s"'}}
    ok = {"file_path": str(settings), "x4_edit": {"old": '"OTHER": "1"', "new": '"OTHER": "5"'}}
    assert "X4_SAVES" in SG.judge(bad)
    assert SG.judge(ok) == ""


def test_patch_lines_that_change_a_key_are_denied_context_lines_are_not(settings):
    upd = ("*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n   \"env\": {\n"
           "     \"X4_TOOLKIT\": \"/tk\",\n"
           "-    \"X4_GAME\": \"/game\",\n+    \"X4_GAME\": \"/other\",\n*** End Patch\n")
    ctx = ("*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n     \"X4_REFERENCE\": \"/tk/reference\",\n"
           "-    \"OTHER\": \"1\"\n+    \"OTHER\": \"6\"\n*** End Patch\n")
    assert "X4_GAME" in SG.judge({"file_path": str(settings), "x4_written": upd, "x4_patch": True})
    assert SG.judge({"file_path": str(settings), "x4_written": ctx, "x4_patch": True}) == ""


def test_add_only_patch_is_compared_with_the_file(settings):
    def add(doc):
        body = "".join("+" + ln + "\n" for ln in json.dumps(doc, indent=2).splitlines())
        return "*** Begin Patch\n*** Add File: x\n" + body + "*** End Patch\n"
    same = json.loads(json.dumps(INSTALLED)); same["env"]["OTHER"] = "7"
    moved = json.loads(json.dumps(INSTALLED)); moved["env"]["X4_TOOLKIT"] = "/evil"
    assert SG.judge({"file_path": str(settings), "x4_written": add(same), "x4_patch": True}) == ""
    assert "X4_TOOLKIT" in SG.judge({"file_path": str(settings), "x4_written": add(moved),
                                     "x4_patch": True})


def test_a_json_escape_spelling_is_denied(settings):
    doc = json.loads(json.dumps(INSTALLED))
    raw = json.dumps(doc).replace('"OTHER"', '"X4_\\u0047AME_DIR": "/y", "\\u0058\\u0034_SAVES": "/x", "OTHER"')
    json.loads(raw)                                   # valid JSON: not the undecidable-text refusal
    assert "X4_SAVES" in SG.judge({"file_path": str(settings), "content": raw})


def test_TWIN_not_a_settings_file(tmp_path):
    p = tmp_path / "notes" / "settings.json"
    assert SG.judge({"file_path": str(p), "content": json.dumps({"env": {"X4_GAME": "/x"}})}) == ""


# --- end to end: the rendered hook and the Codex apply_patch adapter --------------------------
@needs_bash
def test_hook_write_adding_a_root_key_is_denied_and_twin_allowed(sandbox):
    _tmp, _tk, env, s = sandbox
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps({"env": {"X4_STAGING_DIRS": "libraries"}})}})
    assert d == "deny" and "X4_STAGING_DIRS" in why, why
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps({"env": {"OTHER": "2"}})}})
    assert d == "allow"


def test_codex_patch_adding_a_root_key_is_denied_and_twin_allowed(sandbox):
    _tmp, tk, env, s = sandbox
    old = s.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(old) if '"OTHER"' in ln)
    def patch(new_line):
        return ("*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n"
                f"-{old[i]}\n+{new_line}\n*** End Patch\n")
    d, why = run_adapter(env, native("apply_patch_update", tk,
                                     command=patch('    "OTHER": "1", "X4_REFERENCE": "/x"')))
    assert d == "deny", why
    assert run_adapter(env, native("apply_patch_update", tk, command=patch('    "OTHER": "2"')))[0] == "allow"


# --- second review (4.2.0) -------------------------------------------------------------------
def _seed(path, doc_or_text):
    text = doc_or_text if isinstance(doc_or_text, str) else json.dumps(doc_or_text, indent=2)
    path.write_text(text, encoding="utf-8")


def _patch(*hunks, name=".claude/settings.json"):
    body = "".join("@@\n" + "".join(h) for h in hunks)
    return f"*** Begin Patch\n*** Update File: {name}\n{body}*** End Patch\n"


def test_I1_a_root_key_name_as_a_dict_key_ANYWHERE_counts(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["stash"] = {"X4_GAME": "/evil"}                 # step 1 of the two-step bypass
    assert "X4_GAME" in _write(settings, doc)


def test_I1_a_patch_renaming_a_parent_is_judged_on_the_RESULTING_file(settings):
    seeded = json.loads(json.dumps(INSTALLED))
    seeded["stash"] = {"X4_GAME": "/evil"}              # as if step 1 had landed before this rule
    _seed(settings, seeded)
    lines = settings.read_text(encoding="utf-8").splitlines()
    env_i = lines.index('  "env": {')
    stash_i = lines.index('  "stash": {')
    bad = _patch(["-  \"env\": {\n", "+  \"env0\": {\n"], ["-  \"stash\": {\n", "+  \"env\": {\n"])
    assert lines[env_i] and lines[stash_i]
    why = SG.judge({"file_path": str(settings), "x4_written": bad, "x4_patch": True})
    assert "X4_GAME" in why, why


def test_I1_TWIN_a_patch_that_applies_and_changes_nothing_protected_is_allowed(settings):
    ok = _patch(["   \"env\": {\n", "     \"X4_TOOLKIT\": \"/tk\",\n", "     \"X4_GAME\": \"/game\",\n",
                 "     \"X4_REFERENCE\": \"/tk/reference\",\n", "-    \"OTHER\": \"1\"\n", "+    \"OTHER\": \"9\"\n"])
    assert SG.judge({"file_path": str(settings), "x4_written": ok, "x4_patch": True}) == ""


def test_I1_a_patch_that_cannot_be_reconstructed_is_refused_when_the_file_holds_a_protected_key(settings):
    fuzzy = _patch(["   \"no such context line\": {\n", "-    \"OTHER\": \"1\"\n", "+    \"OTHER\": \"9\"\n"])
    assert SG.judge({"file_path": str(settings), "x4_written": fuzzy, "x4_patch": True})
    _seed(settings, {"env": {"OTHER": "1"}})            # TWIN: nothing protected to lose
    assert SG.judge({"file_path": str(settings), "x4_written": fuzzy, "x4_patch": True}) == ""


@pytest.mark.parametrize("key", ["X4_REFERENCE=C:/elsewhere", "FOO=bar", "X4_GAMEX", "x4_staging_dirs_2"])
def test_I2_env_keys_with_equals_or_a_root_key_prefix_are_refused(settings, key):
    doc = json.loads(json.dumps(INSTALLED))
    doc["env"][key] = ""
    assert _write(settings, doc), key


STARTUP = ["BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PATH", "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME"]


@pytest.mark.parametrize("key", STARTUP)
def test_M2_shell_and_python_startup_variables_are_refused(settings, key):
    doc = json.loads(json.dumps(INSTALLED))
    doc["env"][key] = "/somewhere/x.sh"
    assert key in _write(settings, doc)


def test_M2_TWIN_an_existing_PATH_left_unchanged_is_allowed(settings):
    seeded = json.loads(json.dumps(INSTALLED)); seeded["env"]["PATH"] = "/usr/bin"
    _seed(settings, seeded)
    seeded["env"]["OTHER"] = "2"
    assert _write(settings, seeded) == ""


def test_M2_disableAllHooks_true_is_refused_false_and_unchanged_are_allowed(settings):
    doc = json.loads(json.dumps(INSTALLED))
    doc["disableAllHooks"] = True
    assert "disableAllHooks" in _write(settings, doc)
    doc["disableAllHooks"] = False
    assert _write(settings, doc) == ""
    _seed(settings, dict(INSTALLED, disableAllHooks=True))   # already set by the user
    assert _write(settings, dict(INSTALLED, disableAllHooks=True, other=1)) == ""


HOOKS = {"PreToolUse": [{"matcher": "Edit|Write", "hooks": [
    {"type": "command", "command": "bash .claude/hooks/protect-files.sh"},
    {"type": "command", "command": "bash my-own.sh"}]}]}


def test_M2_removing_or_emptying_hooks_is_refused_adding_one_is_allowed(settings):
    _seed(settings, dict(INSTALLED, hooks=HOOKS))
    assert "hook" in _write(settings, dict(INSTALLED)).lower()
    assert "hook" in _write(settings, dict(INSTALLED, hooks={})).lower()
    more = json.loads(json.dumps(HOOKS))
    more["PreToolUse"][0]["hooks"].append({"type": "command", "command": "bash another.sh"})
    assert _write(settings, dict(INSTALLED, hooks=more)) == ""
    mine_gone = json.loads(json.dumps(HOOKS))
    del mine_gone["PreToolUse"][0]["hooks"][1]           # a hook the toolkit does not own
    assert _write(settings, dict(INSTALLED, hooks=mine_gone)) == ""
    guard_changed = json.loads(json.dumps(HOOKS))
    guard_changed["PreToolUse"][0]["hooks"][0]["command"] += " > /dev/null"
    assert "hook" in _write(settings, dict(INSTALLED, hooks=guard_changed)).lower()


def test_M4_an_unparseable_current_file_naming_a_root_key_cannot_be_rewritten_without_it(settings):
    _seed(settings, '{\n  // jsonc comment\n  "env": {"X4_GAME": "/game"}\n}\n')
    assert _write(settings, {"env": {"OTHER": "1"}})
    _seed(settings, '{\n  // jsonc comment\n  "env": {"OTHER": "0"}\n}\n')     # TWIN
    assert _write(settings, {"env": {"OTHER": "1"}}) == ""


@pytest.mark.parametrize("cmd", [
    "$env:X4_GAME = 'C:/x'; bash x.sh",
    "[Environment]::SetEnvironmentVariable('X4_GAME','C:/x'); bash x.sh",
    "set -x X4_GAME C:/x; bash x.sh",
    "printf -v X4_GAME %s C:/x; export X4_GAME; bash x.sh",
    "read X4_GAME <<< C:/x; bash x.sh",
    "export X4_GAME; bash x.sh",
    "Set-Item env:X4_GAME 'C:/x'; bash x.sh",
])
def test_M1_cheap_assignment_spellings_in_a_hook_command_are_refused(settings, cmd):
    doc = json.loads(json.dumps(INSTALLED))
    doc["hooks"] = {"PreToolUse": [{"hooks": [{"type": "command", "command": cmd}]}]}
    assert "X4_GAME" in _write(settings, doc), cmd


@pytest.mark.parametrize("cmd", [
    'bash "$X4_TOOLKIT/.claude/hooks/x.sh"',
    'pwsh -File "$env:X4_TOOLKIT\\x.ps1"',
    "echo $X4_GAME; bash x.sh",
])
def test_M1_TWIN_reading_a_key_in_a_hook_command_is_allowed(settings, cmd):
    doc = json.loads(json.dumps(INSTALLED))
    doc["hooks"] = {"PreToolUse": [{"hooks": [{"type": "command", "command": cmd}]}]}
    assert _write(settings, doc) == "", cmd


@needs_bash
def test_I1_codex_two_step_end_to_end(sandbox):
    _tmp, tk, env, s = sandbox
    d0 = json.loads(s.read_text(encoding="utf-8"))
    d0["stash"] = {"X4_GAME": "/evil"}
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(d0, indent=2)}})
    assert d == "deny" and "X4_GAME" in why, why
    s.write_text(json.dumps(d0, indent=2), encoding="utf-8")     # as if step 1 had landed earlier
    patch = ("*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n"
             "-  \"env\": {\n+  \"env0\": {\n@@\n-  \"stash\": {\n+  \"env\": {\n*** End Patch\n")
    d, why = run_adapter(env, native("apply_patch_update", tk, command=patch))
    assert d == "deny", why

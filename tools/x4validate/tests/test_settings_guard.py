"""X4_GUARD may not be written into a Claude Code settings file's `env` (user decision 2026-10-05).

MEASURED on Claude Code 2.1.290: a project `.claude/settings.json` `env` block reaches every hook
process, so `"env": {"X4_GUARD": "off"}` written by an agent switches every guard to advisory at
the next launch. One channel per test, each with a twin (a settings edit that does NOT touch
X4_GUARD is allowed): the file tools (protect-files.sh), the shell (protect-bash.sh -- unit-tested
in test_hook_facts.py, E2E in scripts/test-hooks.sh), the Codex apply_patch adapter, `x4guard
check`, and the session-canary report. Driven through the RENDERED .codex/hooks tree.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_testlib import HOOKS, make_sandbox, native, run_adapter

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hooks are NOT checked here")

ON = {"env": {"X4_GUARD": "off"}, "permissions": {"allow": []}}
OFF = {"env": {"OTHER": "1"}, "permissions": {"allow": []}}
NL = chr(10)


@pytest.fixture
def sandbox(tmp_path):
    tmp, tk, env = make_sandbox(tmp_path)
    (tk / ".claude").mkdir(exist_ok=True)
    s = tk / ".claude" / "settings.json"
    s.write_text(json.dumps(OFF, indent=2), encoding="utf-8")
    return tmp, tk, env, s


def _hook(env, hook, payload):
    r = subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload).encode(),
                       capture_output=True, env=env, timeout=120)
    out = r.stdout.decode("utf-8", "replace").strip()
    if not out:
        return "allow", ""
    h = json.loads(out)["hookSpecificOutput"]
    return h.get("permissionDecision") or "advise", h.get("permissionDecisionReason") or ""


# --- the file tools (Claude Code Write / Edit / MultiEdit) -----------------------------------
@needs_bash
def test_write_that_sets_X4_GUARD_is_denied_and_twin_allowed(sandbox):
    _tmp, _tk, env, s = sandbox
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(ON)}})
    assert d == "deny" and "X4_GUARD" in why and "at the next launch" in why
    for key in ("X4_GUARD_CHECK", "x4_guard"):                     # the other key; any case
        d2, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
            "file_path": str(s), "content": json.dumps({"env": {key: "1"}})}})
        assert d2 == "deny", key
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(OFF)}})
    assert d == "allow"


@needs_bash
def test_edit_is_judged_on_the_RESULTING_file(sandbox):
    _tmp, _tk, env, s = sandbox
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": '"OTHER": "1"', "new_string": '"X4_GUARD": "off"'}})
    assert d == "deny"
    # TWIN: an edit of another key in the same env block
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": '"OTHER": "1"', "new_string": '"OTHER": "2"'}})
    assert d == "allow"
    # MultiEdit: the key arrives in the SECOND edit
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "MultiEdit", "tool_input": {
        "file_path": str(s), "edits": [{"old_string": '"OTHER": "1"', "new_string": '"A": "1"'},
                                       {"old_string": '"A": "1"', "new_string": '"X4_GUARD": "off"'}]}})
    assert d == "deny"


_HOOKCMD = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
    {"type": "command", "command": "bash .claude/hooks/protect-bash.sh"}]}]}}


@needs_bash
def test_X4_GUARD_ANYWHERE_in_the_file_is_denied_not_only_in_env(sandbox):
    """FX-G4 / reviewer H4a, MEASURED: `X4_GUARD=off bash .../protect-bash.sh` as a hooks COMMAND
    switched that hook off and was allowed -- only `env` was read. USER DECISION: block X4_GUARD
    there. Twins: the same hooks edit without the key, and a write of an ordinary hook command."""
    _tmp, _tk, env, s = sandbox
    for key in ("X4_GUARD=off", "X4_GUARD_CHECK=", "x4_guard=off"):
        bad = json.loads(json.dumps(_HOOKCMD))
        bad["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = key + " bash .claude/hooks/protect-bash.sh"
        d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
            "file_path": str(s), "content": json.dumps(bad)}})
        assert d == "deny" and "X4_GUARD" in why, (key, d, why)
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(_HOOKCMD)}})
    assert d == "allow"
    # an Edit putting it into a hook command string
    s.write_text(json.dumps(_HOOKCMD, indent=2), encoding="utf-8")
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": '"bash .claude', "new_string": '"X4_GUARD=off bash .claude'}})
    assert d == "deny"
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": '"bash .claude', "new_string": '"FOO=1 bash .claude'}})
    assert d == "allow"


@needs_bash
def test_a_PERMISSIONS_rule_mentioning_X4_GUARD_is_allowed_the_same_text_elsewhere_is_not(sandbox):
    """USER DECISION 2026-10-06: the top-level `permissions` block is exempt -- a rule string that
    mentions X4_GUARD sets nothing. The same text in `env` or a hooks command is still denied."""
    _tmp, _tk, env, s = sandbox
    text = "Bash(export X4_GUARD=off)"
    for doc, want in (({"permissions": {"deny": [text], "allow": [], "ask": [text]}}, "allow"),
                      ({"env": {"NOTE": text}}, "deny"),
                      ({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": text}]}]}}, "deny"),
                      ({"permissions": {"deny": [text]}, "env": {"X4_GUARD": "off"}}, "deny")):
        d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
            "file_path": str(s), "content": json.dumps(doc)}})
        assert d == want, (doc, d)


@needs_bash
def test_only_the_TOP_LEVEL_permissions_block_is_exempt(sandbox):
    """FX-G5 / reviewer J2 item 6: the exemption is `o is doc` -- a `permissions` key NESTED
    anywhere else is ordinary text, and X4_GUARD in it is still denied. Twin: the top level."""
    _tmp, _tk, env, s = sandbox
    text = "Bash(export X4_GUARD=off)"
    for doc, want in (({"hooks": {"permissions": {"deny": [text]}}}, "deny"),
                      ({"x": [{"permissions": text}]}, "deny"),
                      ({"permissions": {"deny": [text]}}, "allow")):
        d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
            "file_path": str(s), "content": json.dumps(doc)}})
        assert d == want, (doc, d)


#: Lane F7 (v4.0 release review), MEASURED in Git Bash: each SETS X4_GUARD=off, none names it.
DYNAMIC_NAMES = ['env X4_GUA""RD=off bash .claude/hooks/protect-bash.sh',
                 'export X4_GUA""RD=off; bash .claude/hooks/protect-bash.sh',
                 "env X4_$'GUARD'=off bash .claude/hooks/protect-bash.sh",
                 "V=GUARD; env X4_$V=off bash .claude/hooks/protect-bash.sh",
                 "env ${P}GUARD=off bash .claude/hooks/protect-bash.sh",
                 "env X4_GUA" + chr(92) + "RD=off bash .claude/hooks/protect-bash.sh"]
BENIGN_COMMANDS = ["bash .claude/hooks/protect-bash.sh", "FOO=1 bash .claude/hooks/x.sh",
                   'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/x.sh"', "echo $(date)=x",
                   "jq '.a=1' f.json", 'X="$HOME" bash x.sh', "curl https://h/x?a=b"]


@needs_bash
@pytest.mark.parametrize("command", DYNAMIC_NAMES)
def test_F7_a_hook_command_assigning_through_a_non_literal_NAME_is_denied(sandbox, command):
    """The class, not the spellings: a NAME that is quoted, escaped or built from a variable is
    decided at run time, so the literal-text match cannot see it -- refused whatever it spells."""
    _tmp, _tk, env, s = sandbox
    doc = json.loads(json.dumps(_HOOKCMD))
    doc["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = command
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(doc)}})
    assert d == "deny" and "X4_GUARD" in why, (command, d, why)


@needs_bash
@pytest.mark.parametrize("command", BENIGN_COMMANDS)
def test_F7_TWIN_ordinary_hook_commands_are_allowed(sandbox, command):
    _tmp, _tk, env, s = sandbox
    doc = json.loads(json.dumps(_HOOKCMD))
    doc["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = command
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(doc)}})
    assert d == "allow", (command, d, why)


@needs_bash
def test_an_edit_whose_old_string_does_not_apply_is_judged_on_its_new_text(sandbox):
    """FX-G4 / reviewer H-M1: an Edit whose old_string is not in the file as read here was ALLOWED
    whatever it wrote -- the file the tool sees may differ. Twin: the same miss without the key."""
    _tmp, _tk, env, s = sandbox
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": "NOT-IN-FILE", "new_string": '"X4_GUARD": "off"'}})
    assert d == "deny"
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": "NOT-IN-FILE", "new_string": '"OTHER": "3"'}})
    assert d == "allow"


@needs_bash
def test_an_unreadable_result_that_could_spell_it_is_denied(sandbox):
    """Cannot tell -> DENY with an actionable reason (the user's no-prompt rule), never ask."""
    _tmp, _tk, env, s = sandbox
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": '{"env": {"X4_GUARD": "off"'}})
    assert d == "deny" and "cannot tell" in why
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": '{"env": {"X4\\u005fGUARD": "off"}}'}})   # a JSON escape
    assert d == "deny"


@needs_bash
def test_TWIN_a_file_merely_named_settings_elsewhere_is_not_this_rule(sandbox):
    _tmp, tk, env, _s = sandbox
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(tk / "dev" / "mymod" / "settings.json"), "content": json.dumps(ON)}})
    assert d == "allow"


# --- the shell ---------------------------------------------------------------------------------
@needs_bash
def test_a_shell_write_is_denied_and_a_read_is_allowed(sandbox):
    _tmp, _tk, env, s = sandbox
    p = str(s).replace("\\", "/")
    d, why = _hook(env, "protect-bash.sh", {"tool_name": "Bash", "tool_input": {
        "command": f"jq '.env.X = 1' a.json > '{p}'"}})
    assert d == "deny" and "file-edit tool" in why
    d, _ = _hook(env, "protect-bash.sh", {"tool_name": "Bash", "tool_input": {"command": f"cat '{p}'"}})
    assert d == "allow"


# --- the Codex apply_patch adapter --------------------------------------------------------------
def test_codex_patch_adding_X4_GUARD_is_denied_and_twin_allowed(sandbox):
    _tmp, tk, env, _s = sandbox
    bad = ('*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n-    "OTHER": "1"\n'
           '+    "X4_GUARD": "off"\n*** End Patch')
    d, why = run_adapter(env, native("apply_patch_update", tk, command=bad))
    assert d == "deny" and "X4_GUARD" in (why or "")
    ok = ('*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n-    "OTHER": "1"\n'
          '+    "OTHER": "2"\n*** End Patch')
    assert run_adapter(env, native("apply_patch_update", tk, command=ok))[0] == "allow"


def test_codex_patch_on_a_file_that_ALREADY_sets_it_is_denied(sandbox):
    """A patch's result cannot be proven to remove an existing key: deny, actionable."""
    _tmp, tk, env, s = sandbox
    s.write_text(json.dumps(ON, indent=2), encoding="utf-8")
    ok = ('*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n-    "allow": []\n'
          '+    "allow": ["Bash"]\n*** End Patch')
    assert run_adapter(env, native("apply_patch_update", tk, command=ok))[0] == "deny"


def test_codex_ADD_FILE_over_a_file_that_sets_it_is_judged_on_what_it_writes(sandbox):
    """FX-G5 item 10 (MEASURED: conformance DISAGREE #204): an add-only patch REPLACES the file
    (or fails), so what the file held before cannot survive it -- only the added text is
    judged. Twins: the same Add with the key is denied; an Update, or an Add beside an Update,
    still cannot be proven to remove the existing key."""
    _tmp, tk, env, s = sandbox
    s.write_text(json.dumps(ON, indent=2), encoding="utf-8")
    add = lambda body: "*** Begin Patch\n*** Add File: .claude/settings.json\n+" + body + "\n*** End Patch"
    assert run_adapter(env, native("apply_patch_add", tk, command=add(json.dumps(OFF))))[0] == "allow"
    assert run_adapter(env, native("apply_patch_add", tk, command=add(json.dumps(ON))))[0] == "deny"
    both = ("*** Begin Patch\n*** Add File: .claude/settings.json\n+" + json.dumps(OFF)
            + "\n*** Update File: .claude/settings.json\n@@\n-x\n+y\n*** End Patch")
    assert run_adapter(env, native("apply_patch_add", tk, command=both))[0] == "deny"


# --- x4guard check (any other agent) ------------------------------------------------------------
def _check(env, *args):
    r = subprocess.run([sys.executable, str(HOOKS / "x4guard.py"), "check", *args],
                       capture_output=True, text=True, env=env, timeout=120)
    return json.loads(r.stdout)["decision"]


def test_x4guard_check_needs_the_written_text_for_a_settings_file(sandbox):
    _tmp, _tk, env, s = sandbox
    assert _check(env, "--kind", "write", "--path", str(s)) == "deny"            # text not given
    assert _check(env, "--kind", "write", "--path", str(s), "--command", json.dumps(ON)) == "deny"
    assert _check(env, "--kind", "write", "--path", str(s), "--command", json.dumps(OFF)) == "allow"


def test_x4guard_check_content_is_judged_as_the_whole_resulting_file(sandbox):
    """FX-G5 item 10 (MEASURED: the toy adapter's conformance DISAGREE #204/#224): a whole-file
    write tool passes `--content`, judged exactly as a Claude Code Write -- so a file that sets
    the key now may be REPLACED by one that does not. `--command` (a patch / partial text)
    keeps the conservative reading. Both at once is a usage error, never a guess."""
    _tmp, _tk, env, s = sandbox
    s.write_text(json.dumps(ON), encoding="utf-8")
    assert _check(env, "--kind", "write", "--path", str(s), "--content", json.dumps(OFF)) == "allow"
    assert _check(env, "--kind", "write", "--path", str(s), "--content", json.dumps(ON)) == "deny"
    assert _check(env, "--kind", "write", "--path", str(s), "--command", json.dumps(OFF)) == "deny"
    r = subprocess.run([sys.executable, str(HOOKS / "x4guard.py"), "check", "--kind", "write", "--path",
                        str(s), "--content", "{}", "--command", "{}"], capture_output=True, env=env, timeout=120)
    assert r.returncode == 2
    r = subprocess.run([sys.executable, str(HOOKS / "x4guard.py"), "check", "--kind", "delete", "--path",
                        str(s), "--content", "{}"], capture_output=True, env=env, timeout=120)
    assert r.returncode == 2


def test_patch_semantics_only_when_the_caller_SAYS_it_is_a_patch(sandbox):
    """FX-G6 / reviewer K M1: the add-only reading (the old file cannot survive) was chosen by
    SNIFFING the text for `*** Begin Patch` / `*** Add File:`, so any written text shaped like
    one -- an OpenCode write, a `--command` from another agent -- escaped the check of what the
    file already holds. Now only an explicit patch (`--patch`, the Codex adapter's patch calls)."""
    _tmp, _tk, env, s = sandbox
    s.write_text(json.dumps(ON), encoding="utf-8")
    add = NL.join(["*** Begin Patch", "*** Add File: .claude/settings.json", "+" + json.dumps(OFF), "*** End Patch"])
    assert _check(env, "--kind", "write", "--path", str(s), "--command", add) == "deny"
    # twin: the same text, declared a patch, keeps its add-only reading
    assert _check(env, "--kind", "write", "--path", str(s), "--command", add, "--patch") == "allow"
    r = subprocess.run([sys.executable, str(HOOKS / "x4guard.py"), "check", "--kind", "write", "--path",
                        str(s), "--patch"], capture_output=True, env=env, timeout=120)
    assert r.returncode == 2                                     # --patch needs the text


# --- session-canary -----------------------------------------------------------------------------
@needs_bash
def test_the_canary_names_a_settings_file_that_sets_it(sandbox):
    tmp, tk, env, s = sandbox
    env = dict(env, CLAUDE_PROJECT_DIR=str(tk), HOME=str(tmp / "home"))
    run = lambda: subprocess.run([BASH, str(HOOKS / "session-canary.sh")], capture_output=True,
                                 env=env, timeout=120).stdout.decode("utf-8", "replace")
    assert "settings env block" not in run()                                   # twin: clean file
    s.write_text(json.dumps(ON), encoding="utf-8")
    out = run()
    assert "X4_GUARD is set in a Claude Code settings env block" in out
    assert "settings.json" in out and "off" not in out.split("File(s):", 1)[1]   # paths, no value

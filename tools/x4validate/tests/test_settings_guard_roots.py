"""What an agent may not change in a Claude Code settings file (4.2.0): the CLASS rules.

A settings file's `env` block reaches every hook process (and outranks x4-paths.env), and its hooks
ARE the guards. Three review rounds found the lists leaking (one more variable, one more hook field,
one more path spelling), so the rules are classes, each a NO-CHANGE rule judged on the resulting
file:
  * `env`: no key of any `env` object (at any depth outside the top-level `permissions`) may be
    added, removed or changed -- the user edits env by hand;
  * a dict key holding `=` or starting with a protected-root key's name, at any depth, with the path
    above it (renaming its parent to `env` makes it live);
  * every hook that runs one of the toolkit's guard scripts, as a WHOLE: its event, its group's
    matcher and other fields, and the hook object itself (type, command, timeout, async, ...);
  * `disableAllHooks` set true.
The install's own keys stay put, so an edit that leaves all of this alone is allowed; a user's own
hooks stay theirs. An apply_patch is rebuilt in memory from the ONE header the adapter resolves to
the file (a patch touching it through two headers is refused); a file that is not plain JSON and
holds protected settings cannot be re-judged, so the agent is told to ask the user. Every refusal
has a twin that stays allowed.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

from _layout import claude_hooks_source
from codex_testlib import native, run_adapter
from test_settings_guard import _hook, needs_bash, sandbox  # noqa: F401  (shared fixture)


def _sg():
    path = claude_hooks_source(why="the settings guard", module_level=True) / "settings_guard.py"
    spec = importlib.util.spec_from_file_location("settings_guard_roots", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SG = _sg()
ROOT_KEYS = ["X4_STAGING_DIRS", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_SAVES", "X4_MODS",
             "X4_CONFIG", "X4_TOOLKIT", "X4_EXTENSIONS", "X4_DOCUMENTS"]
GUARD = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/{}"'
HOOKS = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": GUARD.format("protect-bash.sh")}]},
        {"matcher": "PowerShell", "hooks": [{"type": "command", "command": GUARD.format("protect-bash.sh")}]},
        {"matcher": "Edit|Write|NotebookEdit", "hooks": [
            {"type": "command", "command": GUARD.format("protect-files.sh")},
            {"type": "command", "command": GUARD.format("backup-before-edit.sh")}]},
        {"matcher": "Read", "hooks": [{"type": "command", "command": "bash my-own.sh"}]},
    ],
    "SessionStart": [{"hooks": [{"type": "command", "command": GUARD.format("session-canary.sh")}]}],
}
INSTALLED = {"env": {"X4_TOOLKIT": "/tk", "X4_GAME": "/game", "X4_REFERENCE": "/tk/reference",
                     "OTHER": "1"},
             "permissions": {"allow": []},
             "hooks": HOOKS}


def _copy(d):
    return json.loads(json.dumps(d))


@pytest.fixture
def settings(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps(INSTALLED, indent=2), encoding="utf-8")
    return p


def _seed(path, doc_or_text):
    text = doc_or_text if isinstance(doc_or_text, str) else json.dumps(doc_or_text, indent=2)
    path.write_text(text, encoding="utf-8")


def _write(path, doc):
    return SG.judge({"file_path": str(path), "content": json.dumps(doc, indent=2)})


def _patch_judge(path, patch, targets):
    # The guard rebuilds a patch with the adapter's own reader (patch_paths.py beside it, or the
    # source tree's adapters/). An install's .claude/hooks ships no reader -- Claude Code sends no
    # apply_patch -- so there every patch is refused unread (fails closed); skip, counted.
    here = os.path.dirname(SG.__file__)
    if not any(os.path.isfile(os.path.join(here, *r)) for r in (("patch_paths.py",),
                                                                ("..", "adapters", "patch_paths.py"))):
        pytest.skip("this hook tree ships no patch reader (an install's .claude/hooks): patches are refused unread")
    return SG.judge({"file_path": str(path), "x4_written": patch, "x4_patch": True,
                     "x4_patch_targets": targets})


def _patch(*hunks, name=".claude/settings.json"):
    body = "".join("@@\n" + "".join(h) for h in hunks)
    return f"*** Begin Patch\n*** Update File: {name}\n{body}*** End Patch\n"


# --- TWINS: what stays allowed ----------------------------------------------------------------
def test_TWIN_an_edit_leaving_env_and_guard_hooks_alone_is_allowed(settings):
    doc = _copy(INSTALLED)
    doc["permissions"]["allow"] = ["Bash(ls)"]
    doc["env"] = dict(reversed(list(doc["env"].items())))          # key order is not a change
    doc["hooks"]["PreToolUse"].reverse()                           # group order is not a change
    assert _write(settings, doc) == ""


def test_TWIN_a_users_own_hooks_stay_editable(settings):
    doc = _copy(INSTALLED)
    doc["hooks"]["PreToolUse"][3]["matcher"] = "Read|Grep"
    doc["hooks"]["PreToolUse"][3]["hooks"][0]["timeout"] = 5
    doc["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "PATH=/x notify-send done"}]}]
    assert _write(settings, doc) == ""                              # review 3 M2: own hook, own env


def test_TWIN_not_a_settings_file(tmp_path):
    p = tmp_path / "notes" / "settings.json"
    assert SG.judge({"file_path": str(p), "content": json.dumps({"env": {"X4_GAME": "/x"}})}) == ""


# --- F3: the env block, as a class -------------------------------------------------------------
@pytest.mark.parametrize("key", ROOT_KEYS + [
    "X4_PYTHON", "X4_BACKUPS", "X4_HOOK_MAX_CHARS", "PATH", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS",
    "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONWARNINGS",
    "CLAUDE_CODE_SHELL_PREFIX", "CLAUDE_CODE_GIT_BASH_PATH", "BASH_FUNC_x%%", "APPDATA", "HOME",
    "ANYTHING_ELSE", "X4_REFERENCE=C:/elsewhere", "path"])
def test_F3_adding_ANY_env_key_is_refused(settings, key):
    doc = _copy(INSTALLED)
    doc["env"][key] = "/somewhere"
    why = _write(settings, doc)
    assert "env" in why and "by hand" in why, (key, why)


def test_F3_changing_or_removing_an_env_key_is_refused(settings):
    for change in ("change", "remove", "drop-block"):
        doc = _copy(INSTALLED)
        if change == "change":
            doc["env"]["OTHER"] = "2"
        elif change == "remove":
            del doc["env"]["X4_REFERENCE"]
        else:
            del doc["env"]
        assert "env" in _write(settings, doc), change


def test_F3_a_nested_env_block_counts(settings):
    doc = _copy(INSTALLED)
    doc["hooks"]["PreToolUse"][3]["env"] = {"X4_STAGING_DIRS": "libraries"}
    assert _write(settings, doc)


def test_I1_a_root_key_name_as_a_dict_key_anywhere_counts(settings):
    doc = _copy(INSTALLED)
    doc["stash"] = {"X4_GAME": "/evil"}
    assert "X4_GAME" in _write(settings, doc)


def test_I1_renaming_a_parent_to_env_is_refused(settings):
    seeded = _copy(INSTALLED)
    seeded["stash"] = {"X4_GAME": "/evil"}                         # as if it had landed earlier
    _seed(settings, seeded)
    doc = _copy(seeded)
    doc["env0"] = doc.pop("env")
    doc["env"] = doc.pop("stash")
    assert _write(settings, doc)


def test_M2_disableAllHooks_true_is_refused_false_and_unchanged_are_allowed(settings):
    doc = _copy(INSTALLED)
    doc["disableAllHooks"] = True
    assert "disableAllHooks" in _write(settings, doc)
    doc["disableAllHooks"] = False
    assert _write(settings, doc) == ""
    _seed(settings, dict(INSTALLED, disableAllHooks=True))         # already set by the user
    assert _write(settings, dict(INSTALLED, disableAllHooks=True, other=1)) == ""


# --- F1: every guard hook as a whole -----------------------------------------------------------
def _mutate(fn):
    doc = _copy(INSTALLED)
    fn(doc["hooks"])
    return doc


F1_CASES = {
    "matcher Edit|Write|NotebookEdit -> NoSuchTool": lambda h: h["PreToolUse"][2].__setitem__("matcher", "NoSuchTool"),
    "matcher Bash -> NoSuchTool": lambda h: h["PreToolUse"][0].__setitem__("matcher", "NoSuchTool"),
    "group moved PreToolUse -> PostToolUse": lambda h: h.setdefault("PostToolUse", []).append(h["PreToolUse"].pop(2)),
    "async true": lambda h: h["PreToolUse"][2]["hooks"][0].__setitem__("async", True),
    "timeout 0.001": lambda h: h["PreToolUse"][2]["hooks"][0].__setitem__("timeout", 0.001),
    "type prompt": lambda h: h["PreToolUse"][2]["hooks"][0].__setitem__("type", "prompt"),
    "Bash group deleted (same command stays under PowerShell)": lambda h: h["PreToolUse"].pop(0),
    "guard hook removed from its group": lambda h: h["PreToolUse"][2]["hooks"].pop(0),
    "command suffix silences it": lambda h: h["PreToolUse"][2]["hooks"][0].__setitem__(
        "command", GUARD.format("protect-files.sh") + " > /dev/null"),
    "a guard command gains an env prefix": lambda h: h["PreToolUse"][2]["hooks"][0].__setitem__(
        "command", "X4_REFERENCE=/x " + GUARD.format("protect-files.sh")),
    "a new extra field on the group": lambda h: h["PreToolUse"][0].__setitem__("disabled", True),
    "hooks emptied": lambda h: h.clear(),
}


@pytest.mark.parametrize("case", list(F1_CASES))
def test_F1_a_guard_hook_is_protected_as_a_whole(settings, case):
    why = _write(settings, _mutate(F1_CASES[case]))
    assert "hook" in why.lower(), (case, why)


def test_F1_removing_one_of_two_IDENTICAL_guard_hooks_is_refused(settings):
    """The rule counts: two identical groups are two hooks, and one going is a removal."""
    seeded = _copy(INSTALLED)
    seeded["hooks"]["PreToolUse"].append(_copy(seeded["hooks"]["PreToolUse"][0]))
    _seed(settings, seeded)
    assert _write(settings, seeded) == ""                           # TWIN: unchanged
    assert "hook" in _write(settings, INSTALLED).lower()            # one Bash group gone


def test_F1_TWIN_adding_a_hook_even_one_running_a_guard_script_is_allowed(settings):
    """The most restrictive PreToolUse decision wins, so an added hook cannot lift a guard's deny.
    What it RUNS is not judged (one returning `updatedInput` rewrites the call): F221, OPEN."""
    doc = _copy(INSTALLED)
    doc["hooks"]["PreToolUse"][0]["hooks"].append(
        {"type": "command", "command": GUARD.format("protect-bash.sh"), "async": True})
    doc["hooks"]["PostToolUse"] = [{"hooks": [{"type": "command", "command": GUARD.format("x.sh")}]}]
    assert _write(settings, doc) == ""


def test_F1_removing_the_whole_hooks_block_is_refused(settings):
    doc = _copy(INSTALLED)
    del doc["hooks"]
    assert "hook" in _write(settings, doc).lower()


# --- the edit channels -------------------------------------------------------------------------
def test_claude_edit_is_judged_on_the_result(settings):
    bad = {"file_path": str(settings), "old_string": '"/game"', "new_string": '"/elsewhere"'}
    ok = {"file_path": str(settings), "old_string": '"allow": []', "new_string": '"allow": ["Bash(ls)"]'}
    assert SG.judge(bad)
    assert SG.judge(ok) == ""


def test_edit_that_does_not_apply_is_judged_on_its_new_text_when_nothing_is_protected(settings):
    """A file holding no protected setting: the new text alone can add one, so it is judged."""
    _seed(settings, {"permissions": {"allow": []}, "model": "opus"})
    bad = {"file_path": str(settings), "old_string": "nowhere", "new_string": '"X4_CONFIG": "/x"'}
    ok = {"file_path": str(settings), "old_string": "nowhere", "new_string": '"allow": ["Bash(ls)"]'}
    assert SG.judge(bad)
    assert SG.judge(ok) == ""


# --- an edit that does not apply EXACTLY, to a file holding protected settings (review 4) -------
# MEASURED (review 4): Claude Code's Edit matches curly quotes to straight ones and OpenCode's edit
# has whitespace-fuzzy replacers, so an old text that is not in the file can still land -- and was
# judged by its new text alone: `"matcher": "NoSuchTool"` or `"async": true` name nothing protected.
# Same rule as the patch channel: refused, and the agent is told to Write the whole file instead.
NONAPPLYING = {
    "Claude Edit, curly-quoted old_string -> matcher NoSuchTool": {
        "old_string": "“matcher”: “Bash”", "new_string": '"matcher": "NoSuchTool"'},
    "OpenCode edit, whitespace-fuzzy old -> matcher NoSuchTool": {
        "x4_edit": {"old": '"matcher":"Bash"', "new": '"matcher":"NoSuchTool"'}},
    "OpenCode edit, whitespace-fuzzy old -> async true": {
        "x4_edit": {"old": '"type":"command",', "new": '"type":"command","async":true,'}},
    "MultiEdit, one edit not applying": {"edits": [
        {"old_string": '"allow": []', "new_string": '"allow": ["Bash(ls)"]'},
        {"old_string": "“matcher”: “Bash”", "new_string": '"matcher": "NoSuchTool"'}]},
}


@pytest.mark.parametrize("case", list(NONAPPLYING))
def test_R4_a_nonapplying_edit_of_a_file_holding_guard_hooks_is_refused(settings, case):
    why = SG.judge(dict(NONAPPLYING[case], file_path=str(settings)))
    assert why and "Write" in why and "exactly" in why, (case, why)


@pytest.mark.parametrize("case", list(NONAPPLYING))
def test_R4_TWIN_the_same_edit_of_a_file_holding_nothing_protected_is_allowed(settings, case):
    _seed(settings, {"permissions": {"allow": []}, "model": "opus"})
    assert SG.judge(dict(NONAPPLYING[case], file_path=str(settings))) == "", case


def test_R4_TWIN_the_exact_form_of_an_innocent_edit_is_still_allowed(settings):
    ok = {"file_path": str(settings), "x4_edit": {"old": '"allow": []', "new": '"allow": ["Bash(ls)"]'}}
    assert SG.judge(ok) == ""
    ok = {"file_path": str(settings), "old_string": '"allow": []', "new_string": '"allow": ["Bash(ls)"]'}
    assert SG.judge(ok) == ""


def test_R4_TWIN_a_whole_file_Write_making_the_same_innocent_change_is_allowed(settings):
    doc = _copy(INSTALLED)
    doc["permissions"]["allow"] = ["Bash(ls)"]
    assert _write(settings, doc) == ""


# --- strict parsing: what Claude Code's JSON.parse rejects, and shapes it does not read (review 4)
@pytest.mark.parametrize("const", ["NaN", "Infinity", "-Infinity"])
def test_R4_a_NaN_or_Infinity_value_is_refused(settings, const):
    """Python's json accepts them; JSON.parse does not, so the whole file -- guard hooks too -- stops
    loading. MEASURED (review 4): appended to a guard-hooked file, both were allowed."""
    good = json.dumps(INSTALLED, indent=2)
    why = SG.judge({"file_path": str(settings), "content": good[:-1] + f', "x": {const}}}'})
    assert why and "plain JSON" in why, (const, why)
    assert SG.judge({"file_path": str(settings), "content": good[:-1] + ', "x": 1}'}) == ""   # TWIN


def test_R4_TWIN_NaN_in_a_file_holding_nothing_protected_is_not_this_rules_business(settings):
    _seed(settings, {"model": "opus"})
    assert SG.judge({"file_path": str(settings), "content": '{"model": "opus", "x": NaN}'}) == ""


def test_R4_an_events_list_replaced_by_one_object_is_a_change_to_its_guard_hooks(settings):
    doc = _copy(INSTALLED)
    doc["hooks"]["SessionStart"] = doc["hooks"]["SessionStart"][0]
    assert "hook" in _write(settings, doc).lower()


def test_R4_a_groups_hooks_list_replaced_by_one_object_is_a_change_to_its_guard_hook(settings):
    doc = _copy(INSTALLED)
    doc["hooks"]["PreToolUse"][0]["hooks"] = doc["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "hook" in _write(settings, doc).lower()


def test_R4_TWIN_a_file_already_in_that_shape_may_be_edited_elsewhere(settings):
    seeded = _copy(INSTALLED)
    seeded["hooks"]["SessionStart"] = seeded["hooks"]["SessionStart"][0]
    _seed(settings, seeded)
    doc = _copy(seeded)
    doc["permissions"]["allow"] = ["Bash(ls)"]
    assert _write(settings, doc) == ""


def test_opencode_edit_is_judged_on_the_result(settings):
    bad = {"file_path": str(settings), "x4_edit": {"old": '"OTHER": "1"', "new": '"OTHER": "5"'}}
    ok = {"file_path": str(settings), "x4_edit": {"old": '"allow": []', "new": '"allow": ["Bash(ls)"]'}}
    assert SG.judge(bad)
    assert SG.judge(ok) == ""


def test_a_json_escape_spelling_is_refused(settings):
    raw = json.dumps(INSTALLED).replace('"OTHER"', '"\\u0058\\u0034_SAVES": "/x", "OTHER"')
    json.loads(raw)
    assert SG.judge({"file_path": str(settings), "content": raw})


# --- patches: rebuilt from the ONE header the adapter resolves (F2) -----------------------------
TARGET = [["update", ".claude/settings.json"]]


def test_patch_changing_env_is_refused_and_one_changing_permissions_is_allowed(settings):
    bad = _patch(['     "X4_TOOLKIT": "/tk",\n', '-    "X4_GAME": "/game",\n', '+    "X4_GAME": "/other",\n'])
    ok = _patch(['   "permissions": {\n', '-    "allow": []\n', '+    "allow": ["Bash(ls)"]\n'])
    assert _patch_judge(settings, bad, TARGET)
    assert _patch_judge(settings, ok, TARGET) == ""


def test_patch_renaming_a_parent_is_judged_on_the_RESULTING_file(settings):
    seeded = _copy(INSTALLED)
    seeded["stash"] = {"X4_GAME": "/evil"}
    _seed(settings, seeded)
    bad = _patch(['-  "env": {\n', '+  "env0": {\n'], ['-  "stash": {\n', '+  "env": {\n'])
    assert _patch_judge(settings, bad, TARGET)


def test_patch_without_resolved_targets_is_refused_when_the_file_holds_protected_settings(settings):
    ok = _patch(['   "permissions": {\n', '-    "allow": []\n', '+    "allow": ["Bash(ls)"]\n'])
    assert SG.judge({"file_path": str(settings), "x4_written": ok, "x4_patch": True})
    _seed(settings, {"permissions": {"allow": []}})                 # TWIN: nothing protected
    assert SG.judge({"file_path": str(settings), "x4_written": ok, "x4_patch": True}) == ""


def test_F2_two_headers_for_the_same_file_are_refused(settings):
    ok = _patch(['   "permissions": {\n', '-    "allow": []\n', '+    "allow": ["Bash(ls)"]\n'])
    targets = [["update", ".claude/settings.json"], ["update", "./.claude/settings.json"]]
    assert "more than one" in _patch_judge(settings, ok, targets)


def test_patch_that_cannot_be_rebuilt_exactly_is_refused_when_the_file_holds_protected_settings(settings):
    fuzzy = _patch(['   "no such context line": {\n', '-    "allow": []\n', '+    "allow": ["x"]\n'])
    assert _patch_judge(settings, fuzzy, TARGET)
    _seed(settings, {"permissions": {"allow": []}})                 # TWIN
    assert _patch_judge(settings, fuzzy, TARGET) == ""


def test_add_and_delete_of_the_file_are_judged(settings):
    def add(doc):
        body = "".join("+" + ln + "\n" for ln in json.dumps(doc, indent=2).splitlines())
        return "*** Begin Patch\n*** Add File: .claude/settings.json\n" + body + "*** End Patch\n"
    same = _copy(INSTALLED); same["permissions"]["allow"] = ["Bash(ls)"]
    moved = _copy(INSTALLED); moved["env"]["X4_TOOLKIT"] = "/evil"
    assert _patch_judge(settings, add(same), [["add", ".claude/settings.json"]]) == ""
    assert _patch_judge(settings, add(moved), [["add", ".claude/settings.json"]])
    delete = "*** Begin Patch\n*** Delete File: .claude/settings.json\n*** End Patch\n"
    assert _patch_judge(settings, delete, [["delete", ".claude/settings.json"]])


# --- a file that is not plain JSON (review 3 M2) ------------------------------------------------
def test_M4_jsonc_holding_protected_settings_tells_the_agent_to_ask_the_user(settings):
    _seed(settings, '{\n  // jsonc comment\n  "hooks": {},\n  "permissions": {"allow": []}\n}\n')
    why = _write(settings, {"permissions": {"allow": ["Bash(ls)"]}})
    assert "ask the user" in why.lower() and "write the whole" not in why.lower(), why
    _seed(settings, '{\n  // jsonc comment\n  "permissions": {"allow": []}\n}\n')     # TWIN
    assert _write(settings, {"permissions": {"allow": ["Bash(ls)"]}}) == ""


# --- end to end: the rendered hook and both adapter channels ------------------------------------
@needs_bash
def test_hook_write_changing_env_is_refused_and_twin_allowed(sandbox):
    _tmp, _tk, env, s = sandbox
    cur = json.loads(s.read_text(encoding="utf-8"))
    bad = _copy(cur); bad["env"]["X4_STAGING_DIRS"] = "libraries"
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(bad)}})
    assert d == "deny" and "env" in why, why
    ok = _copy(cur); ok["permissions"]["allow"] = ["Bash(ls)"]
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": json.dumps(ok)}})
    assert d == "allow"


@needs_bash
def test_R4_hook_curly_quote_edit_and_NaN_write_of_a_guard_hooked_file_are_refused(sandbox):
    """End to end through the rendered protect-files.sh (review 4 MEASURED both allowed there)."""
    _tmp, _tk, env, s = sandbox
    _seed(s, INSTALLED)
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Edit", "tool_input": {
        "file_path": str(s), "old_string": "“matcher”: “Bash”",
        "new_string": '"matcher": "NoSuchTool"'}})
    assert d == "deny" and "Write" in why, why
    text = s.read_text(encoding="utf-8")
    d, why = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {
        "file_path": str(s), "content": text.rstrip()[:-1] + ', "x": NaN}'}})
    assert d == "deny" and "plain JSON" in why, why
    d, _ = _hook(env, "protect-files.sh", {"tool_name": "Write", "tool_input": {   # TWIN
        "file_path": str(s), "content": text.rstrip()[:-1] + ', "x": 1}'}})
    assert d == "allow"


@pytest.mark.parametrize("header", [".claude/settings.json", "./.claude/./settings.json",
                                    ".claude//settings.json", ".claude/../.claude/settings.json"])
def test_F2_codex_patch_through_any_spelling_of_the_path(sandbox, header):
    _tmp, tk, env, s = sandbox
    lines = s.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if '"OTHER"' in ln)
    bad = (f"*** Begin Patch\n*** Update File: {header}\n@@\n-{lines[i]}\n"
           f"+{lines[i].replace(chr(34) + '1' + chr(34), chr(34) + '2' + chr(34))}\n*** End Patch\n")
    d, why = run_adapter(env, native("apply_patch_update", tk, command=bad))
    assert d == "deny", (header, why)
    j = next(n for n, ln in enumerate(lines) if '"allow"' in ln)
    ok = (f"*** Begin Patch\n*** Update File: {header}\n@@\n-{lines[j]}\n"
          f"+{lines[j].replace('[]', '[' + chr(34) + 'Bash(ls)' + chr(34) + ']')}\n*** End Patch\n")
    d, why = run_adapter(env, native("apply_patch_update", tk, command=ok))
    assert d == "allow", (header, why)


def test_F2_codex_patch_through_two_spellings_is_refused(sandbox):
    _tmp, tk, env, s = sandbox
    lines = s.read_text(encoding="utf-8").splitlines()
    j = next(n for n, ln in enumerate(lines) if '"allow"' in ln)
    two = ("*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n"
           f" {lines[j]}\n*** Update File: ./.claude/settings.json\n@@\n {lines[j]}\n*** End Patch\n")
    d, why = run_adapter(env, native("apply_patch_update", tk, command=two))
    assert d == "deny", why

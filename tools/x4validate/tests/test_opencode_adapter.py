"""The OpenCode adapter (Plan 3 lane L Task 3): an OpenCode tool call, as the plugin hands it over,
judged by the SAME guards as Codex and Claude Code. Driven through the RENDERED
.opencode/hooks/opencode_adapter.py.

The payload shapes (tool ids `bash`/`edit`/`write`/`apply_patch`; args `command`, `workdir`,
`filePath`, `patchText`) are READ from OpenCode's source (v1.18.34), never captured from a running
OpenCode (user decision: no local install). Every routing clause has a test; each decisive one a
twin showing the clause decided it.
"""
import json
import os
import subprocess
import sys

import pytest

from codex_testlib import FAKE_FINDING, REPO, fake_validator, make_sandbox

ADAPTER = REPO / ".opencode" / "hooks" / "opencode_adapter.py"


@pytest.fixture
def sandbox(tmp_path):
    return make_sandbox(tmp_path)


def run(env, payload=None, event="pre", raw: bytes | None = None, cwd=None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    r = subprocess.run([sys.executable, str(ADAPTER), event], input=data, capture_output=True,
                       env=env, timeout=180, cwd=cwd or str(REPO / "tools" / "x4validate"))
    lines = r.stdout.decode("ascii").splitlines()
    assert r.returncode == 0 and len(lines) == 1 and lines[0].startswith("X4OK "), (r.returncode, r.stdout, r.stderr)
    v = json.loads(lines[0][len("X4OK "):])
    assert set(v) == {"v", "decision", "inert", "reason", "context"} and v["v"] == 1, v
    assert v["decision"] in ("allow", "advise", "deny"), v      # the plugin knows no other word
    return v


def call(tk, tool, args, shell="bash", directory=None):
    return {"v": 1, "tool": tool, "args": args, "directory": str(directory or tk), "shell": shell,
            "session_id": "fixture:" + str(tk)}


def test_rendered_adapter_exists():
    assert ADAPTER.is_file(), "regenerate: cd tools/x4validate && uv run python scripts/gen-agent-trees.py"


def _ref_cases(tk):
    ref = (tk / "reference").as_posix()
    return [
        ("edit", {"filePath": f"{ref}/libraries/wares.xml", "oldString": "ref", "newString": "x"}),
        ("write", {"filePath": f"{ref}/x.xml", "content": ""}),
        ("apply_patch", {"patchText": f"*** Begin Patch\n*** Add File: {ref}/n.xml\n+x\n*** End Patch\n"}),
        # the MOVE DESTINATION: OpenCode's permission layer checks only the source (R6), the guards both
        ("apply_patch", {"patchText": "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n"
                                      f"*** Move to: {ref}/a.xml\n@@\n-a\n+b\n*** End Patch\n"}),
        ("bash", {"command": f"rm -rf '{ref}/libraries'"}),
    ]


@pytest.mark.parametrize("n", range(5))
def test_a_write_into_reference_is_DENIED(sandbox, n):
    tmp, tk, env = sandbox
    (tk / "dev" / "mymod" / "a.xml").write_text("a\n", encoding="utf-8")
    tool, args = _ref_cases(tk)[n]
    v = run(env, call(tk, tool, args))
    assert v["decision"] == "deny" and not v["inert"], v


def test_a_patch_is_read_with_OPENCODES_grammar(sandbox):
    """R2-F1: OpenCode's parser is not Codex's. `*** Delete File:x` (no space) is a delete to
    OpenCode and a parse error to Codex: read with Codex's grammar it would be an inert refusal,
    with OpenCode's it is judged by its path -- a real deny in reference, an allow in dev."""
    tmp, tk, env = sandbox
    ref = (tk / "reference").as_posix()
    (tk / "dev" / "mymod" / "a.xml").write_text("a\n", encoding="utf-8")
    v = run(env, call(tk, "apply_patch", {"patchText": f"*** Begin Patch\n*** Delete File:{ref}/libraries/wares.xml\n*** End Patch\n"}))
    assert v["decision"] == "deny" and not v["inert"], v
    v = run(env, call(tk, "apply_patch", {"patchText": "*** Begin Patch\n*** Delete File:dev/mymod/a.xml\n*** End Patch\n"}))
    assert v["decision"] in ("allow", "advise") and not v["inert"], v


def test_TWIN_an_indented_header_OpenCode_skips_is_not_judged(sandbox):
    """The other direction: Codex obeys an indented Delete, OpenCode skips the line (headers only
    at column 0, READ from patch/index.ts and checked against it under node). Read with Codex's
    grammar this would be a deny for a delete OpenCode never performs."""
    tmp, tk, env = sandbox
    ref = (tk / "reference").as_posix()
    v = run(env, call(tk, "apply_patch", {"patchText": "*** Begin Patch\n*** Add File: dev/mymod/h.xml\n+x\n"
                                                       f"  *** Delete File: {ref}/libraries/wares.xml\n*** End Patch\n"}))
    assert v["decision"] in ("allow", "advise") and not v["inert"], v


@pytest.mark.parametrize("tool,args", [
    ("edit", {"filePath": "dev/mymod/a.xml", "oldString": "a", "newString": "b"}),
    ("write", {"filePath": "dev/mymod/new.xml", "content": "<x/>"}),
    ("apply_patch", {"patchText": "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n@@\n-a\n+b\n*** End Patch\n"}),
    ("bash", {"command": "rm -rf dev/mymod/tmp"}),
])
def test_TWIN_a_write_in_dev_is_allowed(sandbox, tool, args):
    tmp, tk, env = sandbox
    (tk / "dev" / "mymod" / "a.xml").write_text("a\n", encoding="utf-8")
    v = run(env, call(tk, tool, args))
    assert v["decision"] in ("allow", "advise") and not v["inert"], v


def test_a_relative_filePath_resolves_against_the_payload_directory(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "edit", {"filePath": "../reference/libraries/wares.xml", "oldString": "r",
                                   "newString": "x"}, directory=tk / "dev"))
    assert v["decision"] == "deny" and not v["inert"], v


def test_TWIN_the_same_relative_filePath_from_elsewhere_is_allowed(sandbox):
    tmp, tk, env = sandbox
    (tk / "dev" / "mymod" / "a.xml").write_text("a\n", encoding="utf-8")
    v = run(env, call(tk, "edit", {"filePath": "../mymod/a.xml", "oldString": "a", "newString": "b"},
                      directory=tk / "dev" / "mymod"))
    assert v["decision"] in ("allow", "advise"), v


def test_bash_workdir_is_the_cwd_for_a_relative_operand(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "bash", {"command": "rm -rf libraries", "workdir": str(tk / "reference")}))
    assert v["decision"] == "deny" and not v["inert"], v


def test_TWIN_bash_workdir_in_dev_allows_the_same_command(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "bash", {"command": "rm -rf libraries", "workdir": str(tk / "dev")}))
    assert v["decision"] in ("allow", "advise"), v


def test_a_relative_bash_workdir_resolves_against_the_payload_directory(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "bash", {"command": "rm -rf libraries", "workdir": "reference"}))
    assert v["decision"] == "deny" and not v["inert"], v


def test_an_ask_is_a_deny_that_says_ask_the_user(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "write", {"filePath": str(tmp / "profile" / "content.xml"), "content": "x"}))
    assert v["decision"] == "deny" and not v["inert"], v
    assert v["reason"].startswith("NEEDS YOUR APPROVAL: ")
    assert v["reason"].endswith("Ask the user; do not retry until they agree.")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"v": 2, "tool": "edit", "args": {}}',
                                 b'{"v": 1, "tool": "edit", "args": "x", "directory": ".", "shell": "bash"}'])
def test_an_unreadable_payload_is_an_INERT_deny(sandbox, raw):
    tmp, tk, env = sandbox
    v = run(env, raw=raw)
    assert v["decision"] == "deny" and v["inert"] and "X4 GUARD INERT" in v["reason"], v


@pytest.mark.parametrize("tool,args", [
    ("apply_patch", {"patchText": "*** Begin Patch\n*** Nope: x\n*** End Patch\n"}),
    ("apply_patch", {}),
    ("edit", {"oldString": "a"}),
    ("bash", {"command": 5}),
])
def test_an_unparseable_patch_or_missing_arg_is_an_INERT_deny(sandbox, tool, args):
    tmp, tk, env = sandbox
    v = run(env, call(tk, tool, args))
    assert v["decision"] == "deny" and v["inert"], v


def test_a_directory_that_does_not_exist_is_an_INERT_deny(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "edit", {"filePath": "a.xml", "oldString": "a", "newString": "b"},
                      directory=tmp / "nowhere"))
    assert v["decision"] == "deny" and v["inert"], v


@pytest.mark.parametrize("shell", ["cmd", "", None])
def test_an_unknown_shell_is_an_INERT_deny(sandbox, shell):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "bash", {"command": "echo hi"}, shell=shell))
    assert v["decision"] == "deny" and v["inert"], v


def test_TWIN_powershell_is_a_known_shell(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "bash", {"command": "Get-ChildItem"}, shell="powershell"))
    assert not v["inert"], v


@pytest.mark.parametrize("tool", ["read", "glob", "grep", "webfetch", "task", "skill"])
def test_tools_that_write_nothing_are_allowed_without_a_guard_run(sandbox, tool):
    tmp, tk, env = sandbox
    # X4_BASH pointing nowhere: any guard run would be an inert deny, so an allow proves none ran
    v = run(dict(env, X4_BASH=str(tmp / "no-bash.exe")), call(tk, tool, {"filePath": str(tk / "reference" / "x")}))
    assert v["decision"] == "advise" and "WARN AND ALLOW" in v["context"] and not v["inert"], v


def test_TWIN_a_judged_tool_with_no_bash_is_inert(sandbox):
    tmp, tk, env = sandbox
    v = run(dict(env, X4_BASH=str(tmp / "no-bash.exe")), call(tk, "edit", {
        "filePath": "dev/mymod/a.xml", "oldString": "a", "newString": "b"}))
    assert v["decision"] == "deny" and v["inert"], v


def test_backup_taken_before_an_allowed_edit_of_an_existing_file(sandbox):
    tmp, tk, env = sandbox
    (tk / "dev" / "mymod" / "a.xml").write_text("<a/>\n", encoding="utf-8")
    v = run(env, call(tk, "edit", {"filePath": "dev/mymod/a.xml", "oldString": "a", "newString": "b"}))
    assert v["decision"] in ("allow", "advise"), v
    assert any((tmp / "backups").rglob("*")), "an allowed edit must be backed up first"


def test_TWIN_no_backup_for_a_denied_edit(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "edit", {"filePath": "reference/libraries/wares.xml", "oldString": "r", "newString": "x"}))
    assert v["decision"] == "deny"
    assert not any((tmp / "backups").rglob("*"))


@pytest.mark.parametrize("tool,args", [
    ("edit", {"filePath": "dev/mymod/a.xml", "oldString": "a", "newString": "b"}),
    ("apply_patch", {"patchText": "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n@@\n-a\n+b\n*** End Patch\n"}),
])
def test_post_carries_the_validators_finding(sandbox, tool, args):
    """R7-2: this accepted `allow` and the sandbox mod had no content.xml, so the validator never
    ran. Now it runs (a stand-in validator, see fake_validator) and its finding must reach the
    model. Catches: post() not calling the validator, dropping its context, or resolving the
    path against the wrong directory."""
    tmp, tk, env = sandbox
    env = fake_validator(tmp, env, tk)
    (tk / "dev" / "mymod" / "a.xml").write_text("<diff/>\n", encoding="utf-8")
    v = run(env, call(tk, tool, args), event="post")
    assert v["decision"] == "advise" and not v["inert"] and FAKE_FINDING in (v["context"] or ""), v
    v = run(env, call(tk, tool, args, directory=tmp), event="post")     # twin: no such file there
    assert v["decision"] == "allow", v


def test_post_on_an_unparseable_patch_says_validation_did_not_run(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "apply_patch", {"patchText": "*** Begin Patch\n*** Nope: x\n*** End Patch\n"}), event="post")
    assert v["decision"] == "advise" and v["context"].startswith("X4 VALIDATION DID NOT RUN"), v


@pytest.mark.parametrize("base", ["workdir", "cd"])
def test_shell_patch_post_validation_keeps_its_working_directory(sandbox, base):
    tmp, tk, env = sandbox
    env = fake_validator(tmp, env, tk)
    (tk / "dev/mymod/a.xml").write_text("<diff/>\n", encoding="utf-8")
    command = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: dev/mymod/a.xml\n+<diff/>\n*** End Patch\nPATCH"
    args = {"command": command, "workdir": str(tk if base == "workdir" else tmp)}
    if base == "cd":
        args["command"] = f"cd '{tk.as_posix()}'; " + command
    result = run(env, call(tk, "bash", args, directory=tmp), event="post")
    assert result["decision"] == "advise" and FAKE_FINDING in (result["context"] or ""), result
    args["command"] = "echo hello"
    assert run(env, call(tk, "bash", args, directory=tmp), event="post")["decision"] == "allow"


def test_malformed_shell_patch_post_discloses_missing_validation(sandbox):
    tmp, tk, env = sandbox
    command = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Nope: a.xml\n*** End Patch\nPATCH"
    result = run(env, call(tk, "bash", {"command": command}), event="post")
    assert result["decision"] == "advise" and "VALIDATION DID NOT RUN" in (result["context"] or ""), result


def test_post_never_denies_even_on_garbage(sandbox):
    tmp, tk, env = sandbox
    v = run(env, raw=b"garbage", event="post")
    assert v["decision"] == "advise" and "DID NOT RUN" in v["context"], v


def test_an_unknown_event_is_an_INERT_deny(sandbox):
    tmp, tk, env = sandbox
    v = run(env, call(tk, "edit", {"filePath": "dev/mymod/a.xml", "oldString": "a", "newString": "b"}), event="nope")
    assert v["decision"] == "deny" and v["inert"], v


def test_X4_GUARD_off_turns_a_deny_into_an_advise_naming_it(sandbox):
    tmp, tk, env = sandbox
    v = run(dict(env, X4_GUARD="off"), call(tk, "edit", {
        "filePath": "reference/libraries/wares.xml", "oldString": "r", "newString": "x"}))
    assert v["decision"] == "advise" and "X4 GUARDS OFF" in v["context"], v


def test_the_output_is_one_ascii_line_even_for_non_ascii_paths(sandbox):
    tmp, tk, env = sandbox
    d = tk / "reference" / "café"
    d.mkdir()
    v = run(env, call(tk, "write", {"filePath": str(d / "x.xml"), "content": ""}))
    assert v["decision"] == "deny"


def test_the_plugin_judges_every_built_in_tool_that_writes():
    """R7-13 (v4.0.0 review): JUDGED was four tools with no record of why four. READ at OpenCode
    v1.18.34 (docs/superpowers/measurements/2026-10-02-opencode-read.md, R15): of the built-in
    tools only bash, edit, write and apply_patch write a file or run a command. MCP and custom
    tools are not judged and README discloses it. A new writing tool upstream means re-reading
    the registry and changing BOTH this set and R15."""
    import re
    from _layout import require_repo
    require_repo("agent/targets/opencode/x4guard.js", "docs/superpowers",
                 why="the plugin SOURCE and the measurement record")
    wrapper = (REPO / "agent/targets/opencode/x4guard.js").read_text(encoding="utf-8")
    assert '../../tools/modding_harness/opencode_plugin.mjs' in wrapper
    js = (REPO / "tools/modding_harness/opencode_plugin.mjs").read_text(encoding="utf-8")
    judged = set(re.findall(r'"(\w+)"', re.search(r"const JUDGED = new Set\(\[([^\]]*)\]\)", js).group(1)))
    assert judged == {"bash", "edit", "write", "apply_patch"}, judged
    doc = (REPO / "docs" / "superpowers" / "measurements" / "2026-10-02-opencode-read.md").read_text(encoding="utf-8")
    assert re.search(r"^\| R15 \|.*exactly bash, edit, write, apply_patch", doc, re.M)


# --- FX-G6 / reviewer K I5: an `edit` of a settings file is APPLIED and its RESULT judged ---------
# MEASURED (reviewer K): only the edit's new text was judged, so two innocent edits -- add
# `"X4_GUA": "x"`, then replace `A": "x"` with `ARD": "off"` -- built X4_GUARD=off.
def _settings(tk, text):
    s = tk / ".claude" / "settings.json"
    s.parent.mkdir(exist_ok=True)
    s.write_text(text, encoding="utf-8")
    return s


def test_a_two_step_edit_that_BUILDS_X4_GUARD_is_denied(sandbox):
    tmp, tk, env = sandbox
    s = _settings(tk, '{"env": {"X4_GUA": "x"}}')
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": 'A": "x"', "newString": 'ARD": "off"'}))
    assert v["decision"] == "deny" and not v["inert"] and "X4_GUARD" in v["reason"], v
    # ...replace_all too (the FIRST `GUA` is a value; only replacing every one builds the key)
    _settings(tk, '{"env": {"A": "GUA", "X4_GUA": "x"}}')
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": "GUA", "newString": "GUARD",
                                   "replaceAll": True}))
    assert v["decision"] == "deny", v
    # ...and an edit that does not apply here whose text could finish the key
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": "nowhere", "newString": 'RD": "off"'}))
    assert v["decision"] == "deny", v


# --- one falsification twin per clause ---
def test_TWIN_an_edit_whose_result_has_no_key_is_allowed(sandbox):
    tmp, tk, env = sandbox
    s = _settings(tk, '{"env": {"X4_GUA": "x"}}')
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": '"x"', "newString": '"y"'}))
    assert v["decision"] in ("allow", "advise") and not v["inert"], v
    # an edit that does not apply here, with a text that cannot form the key wherever it lands
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": "nowhere", "newString": '"y"'}))
    assert v["decision"] in ("allow", "advise"), v
    # unchanged: a write is judged on its text, and a file that already names the key refuses
    v = run(env, call(tk, "write", {"filePath": str(s), "content": '{"env": {"A": "1"}}'}))
    assert v["decision"] in ("allow", "advise"), v
    # ...even an edit that would REMOVE it (refused before FX-G6 too: never a deny turned allow)
    _settings(tk, '{"env": {"X4_GUARD": "off"}}')
    v = run(env, call(tk, "edit", {"filePath": str(s), "oldString": '"X4_GUARD": "off"', "newString": '"A": "1"'}))
    assert v["decision"] == "deny", v

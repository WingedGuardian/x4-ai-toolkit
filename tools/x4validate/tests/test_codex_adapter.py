"""The Codex hook adapter: translate a native Codex payload, ask the guards, render ONLY the JSON
shapes Codex honours (C4). Driven through the RENDERED .codex/hooks/codex_adapter.py.

Every routing branch has a test; each decisive one has a twin that shows the branch decided it.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codex_testlib import UNKNOWN_EXECUTION_CWD, ADAPTER, FAKE_FINDING, FIX, HAS_PWSH, REPO, fake_validator, make_sandbox, native, process_alive, run_adapter as run


@pytest.fixture
def sandbox(tmp_path):
    return make_sandbox(tmp_path)


def test_rendered_adapter_exists():
    assert ADAPTER.is_file(), "regenerate: cd tools/x4validate && uv run python scripts/gen-agent-trees.py"


@pytest.mark.skipif(not HAS_PWSH, reason="no PowerShell -- routing NOT checked here")
def test_bash_labelled_powershell_is_routed(sandbox):
    tmp, tk, env = sandbox
    cmd = f"Set-Content -Path '{tk / 'reference' / 'libraries' / 'wares.xml'}' -Value 'x'"
    assert run(env, native("bash_powershell", tmp, command=cmd), shell="powershell")[0] == "deny"
    assert run(env, native("bash_powershell", tmp, command=cmd), shell="bash")[0] != "deny"  # twin: routing decides


def test_harmless_shell_command_allows(sandbox):
    tmp, _, env = sandbox
    assert run(env, native("bash_powershell", tmp, command="echo hello"), shell="bash") == ("allow", None)


def test_relative_patch_path_resolves_against_payload_cwd(sandbox):
    tmp, tk, env = sandbox
    patch = "*** Begin Patch\n*** Update File: reference/libraries/wares.xml\n@@\n-ref\n+x\n*** End Patch"
    assert run(env, native("apply_patch_update", tk, command=patch))[0] == "deny"
    assert run(env, native("apply_patch_update", tk / "dev", command=patch))[0] == "allow"  # same text, other cwd


def test_multi_file_patch_worst_verdict_wins_and_names_path(sandbox):
    tmp, tk, env = sandbox
    patch = ("*** Begin Patch\n*** Add File: dev/mymod/a.xml\n+<a/>\n"
             "*** Delete File: reference/libraries/wares.xml\n*** End Patch")
    d, reason = run(env, native("apply_patch_multi", tk, command=patch))
    assert d == "deny" and "wares.xml" in reason


def test_add_file_into_reference_denies_and_twin_in_dev_allows(sandbox):
    tmp, tk, env = sandbox
    for rel, want in (("reference/libraries/new.xml", "deny"), ("dev/mymod/new.xml", "allow")):
        patch = f"*** Begin Patch\n*** Add File: {rel}\n+<a/>\n*** End Patch"
        assert run(env, native("apply_patch_add", tk, command=patch))[0] == want, rel


def test_move_to_checks_both_ends(sandbox):
    tmp, tk, env = sandbox
    out_of_ref = ("*** Begin Patch\n*** Update File: reference/libraries/wares.xml\n*** Move to: dev/mymod/w.xml\n"
                  "@@\n-ref\n+x\n*** End Patch")
    into_ref = ("*** Begin Patch\n*** Update File: dev/mymod/w.xml\n*** Move to: reference/libraries/w.xml\n"
                "@@\n-ref\n+x\n*** End Patch")
    (tk / "dev" / "mymod" / "w.xml").write_text("ref\n", encoding="utf-8")
    assert run(env, native("apply_patch_move", tk, command=out_of_ref))[0] == "deny"   # the source end
    assert run(env, native("apply_patch_move", tk, command=into_ref))[0] == "deny"     # the destination end


def test_delete_file_patch_denies_in_reference(sandbox):
    tmp, tk, env = sandbox
    patch = "*** Begin Patch\n*** Delete File: reference/libraries/wares.xml\n*** End Patch"
    assert run(env, native("apply_patch_delete", tk, command=patch))[0] == "deny"


@pytest.mark.parametrize("pad", ["  ", "\t"])
def test_an_indented_delete_after_an_add_is_judged(sandbox, pad):
    """R2-F1 (MEASURED against codex --codex-run-as-apply-patch): Codex trims a line before asking
    whether it is a header, so this indented Delete after an Add deletes the file. The guard read
    it as Add content and ALLOWED it."""
    tmp, tk, env = sandbox
    patch = f"*** Begin Patch\n*** Add File: dev/mymod/h.txt\n+hi\n{pad}*** Delete File: reference/libraries/wares.xml\n*** End Patch"
    assert run(env, native("apply_patch_add", tk, command=patch))[0] == "deny"
    ok = patch.replace("reference/libraries/wares.xml", "dev/mymod/old.xml")
    assert run(env, native("apply_patch_add", tk, command=ok))[0] in ("allow", "advise")   # twin


def test_shell_heredoc_patch_is_judged_by_its_paths(sandbox):
    """P3 (MEASURED): apply_patch through the shell arrives as Bash and IS applied."""
    tmp, tk, env = sandbox
    body = "*** Begin Patch\n*** Add File: reference/libraries/x.xml\n+<a/>\n*** End Patch"
    cmd = f"apply_patch <<'PATCH'\n{body}\nPATCH"
    refused = run(env, native("bash_shell_heredoc_patch", tk, command=cmd), shell="bash")
    assert refused == ("inert", UNKNOWN_EXECUTION_CWD)
    ok = cmd.replace("reference/libraries/x.xml", "dev/mymod/x.xml")
    refused, reason = run(env, native("bash_shell_heredoc_patch", tk, command=ok), shell="bash")
    assert (refused, reason) == ("inert", UNKNOWN_EXECUTION_CWD)
    # Explicit execution cwd is a synthetic contract twin, not native capture evidence.
    for command, expected in ((cmd, "deny"), (ok, "allow")):
        payload = native("bash_shell_heredoc_patch", tk, command=command)
        payload["tool_input"]["workdir"] = str(tk)
        verdict, reason = run(env, payload, shell="bash")
        assert verdict == expected
        if expected == "deny":
            assert "reference" in reason.casefold() and "INERT" not in reason
            # T2: the policy's refusal, never an engine failure wearing its verdict.
            assert not any(f in reason for f in ("policy engine failed", "X4 save dispatcher",
                                                 "GUARD INERT", "preservation/policy failed")), reason


def test_profile_ask_becomes_deny_with_approval_text(sandbox):
    tmp, tk, env = sandbox
    patch = f"*** Begin Patch\n*** Update File: {tmp / 'profile' / 'content.xml'}\n@@\n-a\n+b\n*** End Patch"
    d, reason = run(env, native("apply_patch_update", tk, command=patch))
    assert d == "ask" and reason.startswith("NEEDS YOUR APPROVAL:")
    assert reason.rstrip().endswith("Ask the user; do not retry until they agree.")


def test_manifest_advise_becomes_additional_context(sandbox):
    tmp, tk, env = sandbox
    patch = "*** Begin Patch\n*** Update File: dev/mymod/content.xml\n@@\n-a\n+b\n*** End Patch"
    d, ctx = run(env, native("apply_patch_update", tk, command=patch))
    assert d == "advise" and ctx


def test_unparseable_patch_is_inert_deny(sandbox):
    tmp, tk, env = sandbox
    d, reason = run(env, native("apply_patch_update", tk, command="*** Begin Patch\n*** Frobnicate: x\n*** End Patch"))
    assert d == "inert" and "X4 GUARD INERT" in reason


def test_unreadable_payload_is_inert_deny(sandbox):
    _, _, env = sandbox
    r = subprocess.run([sys.executable, str(ADAPTER), "pre_tool_use"], input=b"{not json", capture_output=True, env=env)
    assert b"permissionDecision\":\"deny" in r.stdout.replace(b" ", b"") and b"INERT" in r.stdout


def test_event_mismatch_is_inert_deny(sandbox):
    tmp, _, env = sandbox
    d = native("bash_powershell", tmp, command="echo hi")
    assert run(env, d, event="post_tool_use")[0] == "advise"          # wrong event on a non-blocking hook
    d["hook_event_name"] = "PostToolUse"
    assert run(env, d, event="pre_tool_use")[0] == "inert"


def test_missing_cwd_is_inert_deny(sandbox):
    tmp, _, env = sandbox
    assert run(env, native("bash_powershell", tmp / "no-such-dir", command="echo hi"), shell="bash")[0] == "inert"


def test_unknown_tool_allowed_and_recorded(sandbox):
    tmp, _, env = sandbox
    d = native("spawn_agent", tmp)
    decision, context = run(env, d)
    assert decision == "advise" and "WARN AND ALLOW" in context
    log = tmp / "unknown-tools.log"
    assert log.is_file() and "collaborationspawn_agent" in log.read_text(encoding="utf-8")


def test_write_stdin_with_newline_is_judged_as_shell(sandbox):
    """No PreToolUse fired for write_stdin in 0/2 measured calls (DECISIONS #20: disclose only).
    If a future Codex does hook it, a submitted line is judged as a shell command."""
    tmp, tk, env = sandbox
    d = native("bash_powershell", tmp)
    d["tool_name"], d["tool_input"] = "write_stdin", {"session_id": 1, "chars": f"rm -rf '{(tk / 'reference').as_posix()}'\n"}
    assert run(env, d, shell="bash")[0] == "deny"
    d["tool_input"]["chars"] = f"rm -rf '{(tk / 'reference').as_posix()}'"     # twin: not submitted yet
    assert run(env, d, shell="bash")[0] == "allow"


def test_reason_is_bounded_directive_first(sandbox):
    tmp, tk, env = sandbox
    many = "".join(f"*** Delete File: reference/libraries/f{i:04d}.xml\n" for i in range(400))
    d, reason = run(env, native("apply_patch_delete", tk, command="*** Begin Patch\n" + many + "*** End Patch"))
    assert d in ("deny", "inert") and len(reason) <= 10_000 and reason.lstrip().startswith(("BLOCKED", "X4"))


def test_the_adapter_budget_bounds_a_whole_batch(sandbox):
    """MEASURED after merging lanes B+E: 400 deletes ran past the 180 s harness timeout under a
    45 s adapter budget -- the budget was checked only BETWEEN batches, and every x4guard check
    inside the one path batch started its OWN fresh deadline. One deadline must bound them all."""
    import time as _t
    tmp, tk, env = sandbox
    many = "".join(f"*** Delete File: reference/libraries/f{i:04d}.xml\n" for i in range(400))
    t0 = _t.monotonic()
    d, _reason = run(dict(env, X4_CODEX_BUDGET_S="5"),
                     native("apply_patch_delete", tk, command="*** Begin Patch\n" + many + "*** End Patch"))
    took = _t.monotonic() - t0
    assert d in ("deny", "inert"), d
    assert took < 40, f"a 5 s budget took {took:.1f} s"


def test_backup_taken_for_an_allowed_update(sandbox):
    tmp, tk, env = sandbox
    f = tk / "dev" / "mymod" / "a.xml"
    f.write_text("<a/>\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n@@\n-<a/>\n+<b/>\n*** End Patch"
    assert run(env, native("apply_patch_update", tk, command=patch))[0] == "allow"
    assert any((tmp / "backups").rglob("*")), "an allowed update must be backed up first"


def _as_input(d: dict) -> dict:
    """The same call with the patch under `input` instead of `command` -- the other key translate()
    has always read (R2-F9)."""
    d["tool_input"]["input"] = d["tool_input"].pop("command")
    return d


def test_a_patch_under_input_is_backed_up_like_one_under_command(sandbox):
    """R2-F9 (v4.0.0 review): translate() read `command` or `input`, the backup and post paths
    `command` only -- a patch under `input` was judged, never backed up, never validated."""
    tmp, tk, env = sandbox
    f = tk / "dev" / "mymod" / "a.xml"
    f.write_text("<a/>\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n@@\n-<a/>\n+<b/>\n*** End Patch"
    assert run(env, _as_input(native("apply_patch_update", tk, command=patch)))[0] == "allow"
    assert any((tmp / "backups").rglob("*")), "the `input` patch was not backed up"


def test_a_patch_under_input_is_validated_like_one_under_command(sandbox):
    tmp, tk, env = sandbox
    env = fake_validator(tmp, env, tk)
    (tk / "dev" / "mymod" / "a.xml").write_text("<diff/>\n", encoding="utf-8")
    d = _as_input(native("post_tool_use_apply_patch", tk,
                         command="*** Begin Patch\n*** Add File: dev/mymod/a.xml\n+<diff/>\n*** End Patch"))
    kind, ctx = run(env, d, event="post_tool_use")
    assert kind == "advise" and FAKE_FINDING in (ctx or ""), (kind, ctx)


def test_a_hook_whose_grandchild_holds_the_pipes_is_still_bounded(sandbox, tmp_path):
    """R2-F6 (v4.0.0 review): _run_hook used subprocess.run(timeout=), whose timeout path on
    Windows waits for EVERY pipe holder -- a backup hook's grandchild stretched the call to its own
    lifetime. It now uses x4guard's bounded runner."""
    tmp, tk, env = sandbox
    tree = tmp_path / "root" / ".codex" / "hooks"
    shutil.copytree(ADAPTER.parent, tree)
    py = Path(sys.executable).as_posix()
    pidfile = (tmp_path / "grandchild.pid").as_posix()
    (tree / "backup-before-edit.sh").write_bytes(
        (f"cat >/dev/null\n'{py}' -c \"import os, time; open('{pidfile}', 'w').write(str(os.getpid())); "
         f"time.sleep(60)\" &\nsleep 60\n").encode("utf-8"))
    (tk / "dev" / "mymod" / "a.xml").write_text("<a/>\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: dev/mymod/a.xml\n@@\n-<a/>\n+<b/>\n*** End Patch"
    t0 = time.monotonic()
    kind, text = run(dict(env, X4_CODEX_BUDGET_S="12"), native("apply_patch_update", tk, command=patch),
                     adapter=tree / "codex_adapter.py")
    wall = time.monotonic() - t0
    assert kind in ("ask", "deny") and "timed out" in (text or ""), (kind, text)
    assert wall < 40, f"{wall:.1f}s: a 12 s budget did not bound the backup hook"
    # v4.0.0 delta review C: bounding the CALL is not the same as reaping the TREE -- the python
    # grandchild could outlive the test by its full 60 s. MEASURED 2026-10-05 (Windows, outside
    # rb's job object): no survivor. Pinned here: the grandchild must have started (else this
    # proves nothing) and must be gone; a survivor is killed before the test fails.
    pf = Path(pidfile)
    assert pf.is_file(), "the grandchild never started -- the bound above was not exercised"
    pid = int(pf.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if _alive(pid):
        _terminate(pid)
        pytest.fail(f"the hook's grandchild (pid {pid}) outlived the bounded call: orphaned")


def _alive(pid: int) -> bool:
    return process_alive(pid)


def _terminate(pid: int) -> None:
    import os
    import signal
    try:
        os.kill(pid, signal.SIGTERM)                     # TerminateProcess on Windows
    except OSError:
        pass


def test_TWIN_the_liveness_probe_sees_a_live_process_and_a_dead_one():
    """The orphan check above is only as good as `_alive`: it must say True for a running
    process and False once that process is gone."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _alive(p.pid) is True
    finally:
        p.kill()
        p.wait(timeout=10)
    assert _alive(p.pid) is False


def test_session_start_fits_inside_the_wrappers_limit(monkeypatch):
    """R2-F7 (v4.0.0 review): each session hook got min(20 s, budget) -- two of them could take
    40 s, and the wrappers kill session_start at 25 s and report the guards NOT LIVE. The two
    hooks now SHARE one budget below that limit."""
    src = {n: (ADAPTER.parent / n).read_text(encoding="utf-8") for n in ("codex-entry.ps1", "codex-entry.sh")}
    limits = {int(re.search(r"session_start'\) \{ \$limit = (\d+)", src["codex-entry.ps1"]).group(1)),
              int(re.search(r'session_start \]\s*&&\s*LIMIT=(\d+)', src["codex-entry.sh"]).group(1))}
    assert len(limits) == 1, limits
    spec = importlib.util.spec_from_file_location("codex_adapter_f7", ADAPTER)
    ad = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ad)
    clock, given = [100.0], []

    def slow_hook(script, payload, timeout):           # each hook burns ALL the time it is given
        given.append(timeout)
        clock[0] += timeout
        return None, f"{script} timed out"
    monkeypatch.setattr(ad, "_run_hook", slow_hook)
    monkeypatch.setattr(ad.time, "monotonic", lambda: clock[0])
    ad.session_start(clock[0] + ad.budget_s())
    spent = clock[0] - 100.0
    assert spent <= ad.SESSION_START_BUDGET_S < min(limits) - 5, (given, spent, limits)


def test_TWIN_no_backup_for_a_denied_patch(sandbox):
    tmp, tk, env = sandbox
    patch = "*** Begin Patch\n*** Update File: reference/libraries/wares.xml\n@@\n-ref\n+x\n*** End Patch"
    assert run(env, native("apply_patch_update", tk, command=patch))[0] == "deny"
    assert not any((tmp / "backups").rglob("*"))


def test_post_tool_use_carries_the_validators_finding(sandbox):
    """R7-2: this accepted `allow` and the sandbox mod had no content.xml, so the validator never
    ran and the test passed whatever the adapter did. Now the validator path runs (a stand-in
    validator, see fake_validator) and its finding must reach Codex. Catches: post_tool_use not
    calling the validator, dropping its context, or resolving the patch path against the wrong
    directory (the hook then finds no file and stays silent)."""
    tmp, tk, env = sandbox
    env = fake_validator(tmp, env, tk)
    (tk / "dev" / "mymod" / "a.xml").write_text("<diff><add sel=\"/x\"/></diff>\n", encoding="utf-8")
    d = native("post_tool_use_apply_patch", tk,
               command="*** Begin Patch\n*** Add File: dev/mymod/a.xml\n+<diff/>\n*** End Patch")
    kind, ctx = run(env, d, event="post_tool_use")
    assert kind == "advise" and FAKE_FINDING in (ctx or ""), (kind, ctx)
    # twin: the same patch from a cwd where dev/mymod/a.xml does not exist -> nothing to validate
    d["cwd"] = str(tmp)
    assert run(env, d, event="post_tool_use")[0] == "allow"


def test_post_tool_use_unparseable_patch_says_validation_did_not_run(sandbox):
    tmp, tk, env = sandbox
    d = native("post_tool_use_apply_patch", tk, command="*** Begin Patch\n*** Nope: x\n*** End Patch")
    kind, ctx = run(env, d, event="post_tool_use")
    assert kind == "advise" and ctx.startswith("X4 VALIDATION DID NOT RUN")


def test_shell_carried_patch_keeps_its_cd_and_reaches_post_validation(sandbox):
    template = json.loads((REPO / "agent/targets/codex/hooks.json.tmpl").read_text(encoding="utf-8"))
    assert any(re.fullmatch(group["matcher"], "Bash") for group in template["hooks"]["PostToolUse"])
    tmp, tk, env = sandbox
    env = fake_validator(tmp, env, tk)
    (tk / "dev/mymod/a.xml").write_text("<diff/>\n", encoding="utf-8")
    command = (f"cd '{tk.as_posix()}'; apply_patch <<'PATCH'\n"
               "*** Begin Patch\n*** Add File: dev/mymod/a.xml\n+<diff/>\n*** End Patch\nPATCH")
    payload = native("post_tool_use_bash", tmp, command=command)
    kind, context = run(env, payload, event="post_tool_use")
    assert kind == "advise" and FAKE_FINDING in (context or ""), (kind, context)
    payload["tool_input"]["command"] = "echo hello"
    assert run(env, payload, event="post_tool_use")[0] == "allow"


def test_malformed_shell_carried_patch_discloses_missing_validation(sandbox):
    tmp, tk, env = sandbox
    command = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Nope: a.xml\n*** End Patch\nPATCH"
    payload = native("post_tool_use_bash", tk, command=command)
    kind, context = run(env, payload, event="post_tool_use")
    assert kind == "advise" and "VALIDATION DID NOT RUN" in context


def test_session_start_banner(sandbox):
    tmp, _, env = sandbox
    d = json.loads((FIX / "session_start.json").read_text(encoding="utf-8").replace("<CWD>", "x"))
    d["cwd"] = str(tmp)
    kind, ctx = run(env, d, event="session_start")
    assert kind == "advise" and ctx.startswith("X4 GUARDS LIVE (codex hooks v1)")


def test_deadline_exhausted_is_inert(sandbox):
    tmp, tk, env = sandbox
    env = dict(env, X4_CODEX_BUDGET_S="0")
    assert run(env, native("bash_powershell", tmp, command="echo hi"), shell="bash")[0] == "inert"


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash drive paths (/c/...) exist on Windows only")
def test_msys_drive_path_is_the_windows_path(sandbox):
    """Found by the conformance suite: abspath('/c/...') on Windows is '<drive>:/c/...', outside
    every protected root, so an Update File of the reference via /c/... was ALLOWED."""
    tmp, tk, env = sandbox
    p = tk.as_posix()
    msys = "/" + p[0].lower() + p[2:] + "/reference/libraries/wares.xml"
    patch = f"*** Begin Patch\n*** Update File: {msys}\n@@\n-ref\n+x\n*** End Patch"
    assert run(env, native("apply_patch_update", tk, command=patch))[0] == "deny"

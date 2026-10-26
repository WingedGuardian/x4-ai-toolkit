"""x4conformance engine, pure parts: no bash, no dump. Every exit-code clause has a twin."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))
from modding_harness.transport import UNKNOWN_EXECUTION_CWD
spec = importlib.util.spec_from_file_location("x4conformance", REPO / "scripts" / "x4conformance.py")
xc = importlib.util.module_from_spec(spec); spec.loader.exec_module(xc)

CODEX = xc.load_profile("codex", REPO)
CLAUDE = xc.load_profile("claude", REPO)


def _old_codex_patch(op, path, content=""):          # verbatim from test_codex_conformance._patch
    if op == "add":
        body = "".join("+" + ln + "\n" for ln in (content.split("\n") if content else [""]))
        return f"*** Begin Patch\n*** Add File: {path}\n{body}*** End Patch"
    return f"*** Begin Patch\n*** Update File: {path}\n@@\n-x\n+y\n*** End Patch"


@pytest.mark.parametrize("content", ["", "a", "a\nb", "a\n", "{{COMMAND}}"])
def test_codex_write_template_reproduces_the_old_patch_builder(content):
    t = CODEX["cases"]["write"]["set"]["/tool_input/command"]
    assert xc.fill(t, {"PATH": "p q.xml", "CONTENT": content}) == _old_codex_patch("add", "p q.xml", content)


def test_codex_edit_template_reproduces_the_old_patch_builder():
    t = CODEX["cases"]["edit"]["set"]["/tool_input/command"]
    assert xc.fill(t, {"PATH": "p q.xml"}) == _old_codex_patch("update", "p q.xml")


def test_fill_never_re_expands_a_token_inside_a_value():
    assert xc.fill("{{COMMAND}}", {"COMMAND": "echo {{PATH}}", "PATH": "X"}) == "echo {{PATH}}"


def test_an_unknown_template_token_is_a_profile_error():
    with pytest.raises(xc.ProfileError):
        xc.fill("{{NOPE}}", {})


def test_TWIN_a_known_token_without_a_value_is_also_a_profile_error():
    with pytest.raises(xc.ProfileError):
        xc.fill("{{PATH}}", {})


def _profile(**over):
    p = json.loads(json.dumps(CODEX)); p.update(over); return p


def test_a_kind_neither_mapped_nor_unsupported_is_refused(tmp_path):
    p = _profile(); del p["cases"]["shell-powershell"]
    f = tmp_path / "p.json"; f.write_text(json.dumps(p), encoding="utf-8")
    with pytest.raises(xc.ProfileError, match="shell-powershell"):
        xc.load_profile(str(f), REPO)


def test_TWIN_a_kind_declared_unsupported_loads_and_is_a_gap(tmp_path):
    p = _profile(); del p["cases"]["shell-powershell"]; p["unsupported"] = {"shell-powershell": "no PS"}
    f = tmp_path / "p.json"; f.write_text(json.dumps(p), encoding="utf-8")
    assert xc.load_profile(str(f), REPO)["unsupported"] == {"shell-powershell": "no PS"}


def test_a_missing_profile_file_is_a_profile_error(tmp_path):
    with pytest.raises(xc.ProfileError):
        xc.load_profile(str(tmp_path / "nope.json"), REPO)


def _shipped_hook_script(key):
    """The script the SHIPPED Codex PreToolUse hook runs under `command` (POSIX) or
    `commandWindows` -- what a real host executes, which conformance must replay."""
    tmpl = json.loads((REPO / "agent/targets/codex/hooks.json.tmpl").read_text(encoding="utf-8"))
    (hook,) = [h for g in tmpl["hooks"]["PreToolUse"] for h in g["hooks"]]
    return next(w for w in hook[key].replace("\\", "/").replace('"', " ").split() if "codex-entry." in w).rsplit("/", 1)[1]


@pytest.mark.parametrize("posix,key", [(True, "command"), (False, "commandWindows")])
def test_codex_conformance_runs_the_entry_the_host_runs_on_each_os(posix, key):
    """ubuntu CI run 39187589960: the profile ran codex-entry.ps1 under pwsh on Linux, a path no
    host takes (hooks.json gives Linux codex-entry.sh), and since 8bdaa55 the .ps1's kernel32
    P/Invoke made every case INERT there. Each OS replays its own production entry."""
    argv = xc.profile_command(CODEX, posix=posix)
    script = next(a for a in argv if "codex-entry." in a).rsplit("/", 1)[1]
    assert script == _shipped_hook_script(key)


def test_TWIN_a_profile_without_command_posix_uses_command_everywhere():
    assert xc.profile_command(CLAUDE, posix=True) == xc.profile_command(CLAUDE, posix=False) == CLAUDE["command"]


@pytest.mark.parametrize("bad", [[], "bash x", [1]])
def test_a_malformed_command_posix_is_a_profile_error(tmp_path, bad):
    p = _profile(); p["command_posix"] = bad
    f = tmp_path / "p.json"; f.write_text(json.dumps(p), encoding="utf-8")
    with pytest.raises(xc.ProfileError, match="command_posix"):
        xc.load_profile(str(f), REPO)


# --- decode: Codex shapes (the C4 contract), Claude shapes, text rules --------------------- #

def _hso(**k):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **k}}).encode()


@pytest.mark.parametrize("out,expect", [
    (b"", "allow"),
    (_hso(permissionDecision="deny", permissionDecisionReason="BLOCKED x"), "deny"),
    (_hso(permissionDecision="deny", permissionDecisionReason="NEEDS YOUR APPROVAL: x"), "ask"),
    (_hso(permissionDecision="deny", permissionDecisionReason="X4 GUARD INERT: y"), "inert"),
    (_hso(additionalContext="note"), "advise"),
    (_hso(permissionDecision="ask", permissionDecisionReason="x"), "unreadable"),    # Codex fails open on ask
    (_hso(permissionDecision="allow", permissionDecisionReason="x"), "unreadable"),
    (json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "r",
                                        "extra": 1}}).encode(), "unreadable"),        # extra key = fail-open
    (json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "r"},
                 "extra": 1}).encode(), "unreadable"),                                # extra TOP key too
    (b"not json", "unreadable"),
])
def test_codex_decode(out, expect):
    assert xc.decode(CODEX["output"], out, 0)[0] == expect


def test_codex_a_nonzero_exit_is_unreadable_never_a_verdict():
    assert xc.decode(CODEX["output"], _hso(permissionDecision="deny", permissionDecisionReason="r"), 2)[0] == "unreadable"


@pytest.mark.parametrize("out,rc,expect", [
    (_hso(permissionDecision="ask", permissionDecisionReason="x"), 0, "ask"),          # Claude honours ask
    (_hso(permissionDecision="ask", permissionDecisionReason="NO rule was evaluated"), 2, "inert"),
    (_hso(permissionDecision="deny", permissionDecisionReason="x"), 0, "deny"),
    (_hso(additionalContext="note"), 0, "advise"),
    (b"", 0, "allow"),
    (b"", 1, "unreadable"),
])
def test_claude_decode(out, rc, expect):
    assert xc.decode(CLAUDE["output"], out, rc)[0] == expect


TEXT_OUT = {"format": "text", "exit_codes": {"0": "decode"}, "empty": "allow",
            "rules": [{"regex": "^B (?P<text>NEEDS YOUR APPROVAL:.*)$", "decision": "ask"},
                      {"regex": "^B (?P<text>.*)$", "decision": "deny"},
                      {"regex": "^N (?P<text>.*)$", "decision": "advise"}]}


@pytest.mark.parametrize("out,expect", [
    (b"", "allow"), (b"B no", "deny"), (b"B NEEDS YOUR APPROVAL: q", "ask"), (b"N hi", "advise"),
    (b"B X4 GUARD INERT: z", "inert"), (b"garbage", "unreadable"),
])
def test_text_decode(out, expect):
    assert xc.decode(TEXT_OUT, out, 0)[0] == expect


def test_an_undeclared_empty_output_is_unreadable_never_an_allow():
    o = dict(TEXT_OUT); del o["empty"]
    assert xc.decode(o, b"", 0)[0] == "unreadable"


# --- classify + summarise ------------------------------------------------------------------ #

@pytest.mark.parametrize("payload,kind", [
    ({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "shell-bash"),
    ({"tool_name": "PowerShell", "tool_input": {"command": "dir"}}, "shell-powershell"),
    ({"tool_name": "Edit", "tool_input": {"file_path": "a"}}, "edit"),
    ({"tool_name": "Write", "tool_input": {"file_path": "a", "content": ""}}, "write"),
    ({"tool_name": "Bash", "tool_input": {"command": ""}}, "no_native_analogue"),
    ({"tool_name": "Grep", "tool_input": {"pattern": "x"}}, "no_native_analogue"),
    ({"tool_name": "Bash", "tool_input": "notadict"}, "no_native_analogue"),
])
def test_classify(payload, kind):
    assert xc.classify({"payload": payload}) == kind


# A Windows path DIALECT off Windows (CI run 37172347642, ubuntu): `\home\u\tk\reference\x`
# is ONE relative filename on POSIX -- a write lands in the cwd, never in reference/ -- so the
# adapter's ALLOW is the truth there while the hook (which folds backslashes everywhere) says
# deny. Such a row is counted in its own bucket off Windows, never replayed and never dropped.
@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_a_BACKSLASH_path_off_windows_is_its_own_kind(tool):
    row = {"payload": {"tool_name": tool, "tool_input": {"file_path": "C:" + chr(92) + "tk" + chr(92) + "x"}}}
    assert xc.classify(row, windows=False) == "windows_path_dialect"


@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_TWIN_the_same_path_ON_windows_is_replayed(tool):
    row = {"payload": {"tool_name": tool, "tool_input": {"file_path": "C:" + chr(92) + "tk" + chr(92) + "x"}}}
    assert xc.classify(row, windows=True) == tool.lower()


def test_TWIN_a_forward_slash_path_off_windows_is_replayed():
    assert xc.classify({"payload": {"tool_name": "Edit", "tool_input": {"file_path": "/tk/x"}}},
                       windows=False) == "edit"


def test_TWIN_a_backslash_in_a_SHELL_command_off_windows_is_still_replayed():
    row = {"payload": {"tool_name": "Bash", "tool_input": {"command": "echo a" + chr(92) + "b"}}}
    assert xc.classify(row, windows=False) == "shell-bash"


def test_the_dialect_rows_get_their_OWN_bucket_and_the_buckets_sum():
    rows = [{"kind": "edit"}, {"kind": "windows_path_dialect"}, {"kind": "no_native_analogue"},
            {"kind": "shell-bash"}]
    b = xc.bucket_counts(rows, n_replayed=2, profile=CODEX)
    assert b == {"replayed": 2, "windows_path_dialect": 1, "no_native_analogue": 1}, b


def test_the_neutral_run_dir_is_NOT_the_system_temp():
    """On POSIX the system temp is /tmp, and the guards DENY a write into /tmp (the shared-/tmp
    measurement rule) -- so a "neutral" run dir there judged every relative case by that rule
    instead, and the ignores_workdir mutant survived on ubuntu (CI run 37172347642)."""
    import tempfile
    d = xc.neutral_run_dir(REPO)
    try:
        assert d.is_dir()
        assert not d.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()), d
        assert d.resolve().is_relative_to((REPO / ".test-sandbox").resolve()), d
    finally:
        d.rmdir()


def _res(n_agree, n_disagree=0):
    r = [{"label": f"a{i}", "kind": "shell-bash", "reference": "deny", "adapter": "deny"} for i in range(n_agree)]
    r += [{"label": f"d{i}", "kind": "write", "reference": "deny", "adapter": "allow"} for i in range(n_disagree)]
    return r


def test_zero_replayed_refuses_with_3():
    rc, text = xc.summarise([], n_total=12, buckets={"no_native_analogue": 12}, gaps={}, min_cases=1)
    assert rc == xc.RC_NOTHING and "0 replayed" in text


def test_TWIN_one_agreeing_case_with_a_floor_of_one_passes():
    assert xc.summarise(_res(1), n_total=1, buckets={"replayed": 1}, gaps={}, min_cases=1)[0] == xc.RC_OK


def test_below_the_floor_refuses_with_3():
    assert xc.summarise(_res(5), n_total=5, buckets={"replayed": 5}, gaps={}, min_cases=80)[0] == xc.RC_NOTHING


def test_one_disagreement_is_1_and_names_the_item():
    rc, text = xc.summarise(_res(90, 1), n_total=91, buckets={"replayed": 91}, gaps={}, min_cases=80)
    assert rc == xc.RC_DISAGREE and "d0" in text


@pytest.mark.parametrize("reference,adapter,text,rc", [
    ("advise", "ask", "NEEDS YOUR APPROVAL: " + xc.BACKUP_ONLY + ": a read-only check", xc.RC_OK),
    ("allow", "ask", xc.BACKUP_ONLY, xc.RC_OK),
    # Twins: only the STRICTER direction, and only with the read-only check's own reason.
    ("advise", "ask", "some other ask", xc.RC_DISAGREE),
    ("ask", "allow", xc.BACKUP_ONLY, xc.RC_DISAGREE),
    ("deny", "ask", xc.BACKUP_ONLY, xc.RC_DISAGREE),
])
def test_K3_a_read_only_check_may_ask_where_the_native_hook_backs_a_save_up(reference, adapter, text, rc):
    """K3: `x4guard check` cannot preserve a save, so it asks where the native hook backs up and
    allows. An adapter built on it diverges there, stricter, and only there."""
    rows = _res(1) + [{"label": "s0", "kind": "shell-bash", "reference": reference, "adapter": adapter,
                       "text": text}]
    assert xc.summarise(rows, n_total=2, buckets={"replayed": 2}, gaps={}, min_cases=1)[0] == rc


@pytest.mark.parametrize("reference,host,adapter,rc", [
    ("deny", "allow", "allow", xc.RC_OK),          # the guards WITH the host say what the adapter says
    ("deny", "advise", "advise", xc.RC_OK),
    # Twins: the host-aware verdict must EQUAL the adapter's; no host verdict, no excuse.
    ("deny", "deny", "allow", xc.RC_DISAGREE),
    ("deny", "advise", "allow", xc.RC_DISAGREE),
    ("deny", None, "allow", xc.RC_DISAGREE),
    ("deny", "inert", "inert", xc.RC_DISAGREE),
    ("deny", "error", "error", xc.RC_DISAGREE),
])
def test_a825d86_a_host_contract_difference_agrees_only_when_the_hosted_guards_agree(reference, host, adapter, rc):
    row = {"label": "h0", "kind": "shell-bash", "reference": reference, "adapter": adapter, "text": None}
    if host is not None:
        row["reference_host"] = host
    got, text = xc.summarise(_res(1) + [row], n_total=2, buckets={"replayed": 2}, gaps={}, min_cases=1)
    assert got == rc, text
    assert ("HOST CONTRACT h0" in text) == (rc == xc.RC_OK)


def test_the_codex_profile_declares_its_execution_host_and_the_claude_one_does_not():
    assert CODEX["execution_host"] == "codex"
    assert "execution_host" not in CLAUDE


@pytest.mark.parametrize("bad", ["", 1, ["codex"]])
def test_a_malformed_execution_host_is_a_profile_error(tmp_path, bad):
    raw = json.loads((REPO / "scripts/conformance-profiles/codex.json").read_text(encoding="utf-8"))
    raw["execution_host"] = bad
    raw["cases"] = {k: dict(v, payload=str((REPO / "scripts/conformance-profiles" / v["payload"]).resolve()))
                    for k, v in raw["cases"].items()}
    p = tmp_path / "p.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(xc.ProfileError, match="execution_host"):
        xc.load_profile(str(p), REPO)
    raw["execution_host"] = "codex"                    # twin: the same profile, well-formed, loads
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert xc.load_profile(str(p), REPO)["execution_host"] == "codex"


def _fake_replay(monkeypatch, tmp_path, adapter="allow"):
    seen = []

    def ref(row, root):
        seen.append(row["payload"])
        return ("allow" if row["payload"].get("x4_execution_host") == "codex" else "deny"), ""
    monkeypatch.setattr(xc, "reference_verdict_detail", ref)
    monkeypatch.setattr(xc, "adapter_verdict", lambda *a, **k: (adapter, None))
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    monkeypatch.setattr(xc, "neutral_run_dir", lambda root: run)
    return seen


@pytest.mark.parametrize("kind", ["shell-bash", "shell-powershell"])
def test_replay_rejudges_a_differing_shell_case_with_the_profile_host_at_the_payload_top_level(
        monkeypatch, tmp_path, kind):
    seen = _fake_replay(monkeypatch, tmp_path)
    row = {"label": "x", "kind": kind, "hook": "protect-bash.sh",
           "payload": {"tool_name": "Bash", "tool_input": {"command": "bash build-effective.sh"}}}
    (r,) = xc.replay([row], CODEX, None, REPO, workers=1)
    assert r["reference"] == "deny" and r["reference_host"] == "allow" and xc.host_contract_divergence(r)
    assert seen[-1]["x4_execution_host"] == "codex" and "x4_execution_host" not in seen[-1]["tool_input"]
    assert "x4_execution_host" not in row["payload"]          # the dump row itself is untouched


def test_TWIN_replay_never_rejudges_without_a_host_a_difference_or_a_shell(monkeypatch, tmp_path):
    shell = {"label": "x", "kind": "shell-bash", "hook": "protect-bash.sh",
             "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}}}
    _fake_replay(monkeypatch, tmp_path)
    assert "reference_host" not in xc.replay([shell], CLAUDE, None, REPO, workers=1)[0]   # no host
    _fake_replay(monkeypatch, tmp_path, adapter="deny")
    assert "reference_host" not in xc.replay([shell], CODEX, None, REPO, workers=1)[0]    # they agree
    edit = {"label": "e", "kind": "edit", "hook": "protect-files.sh",
            "payload": {"tool_name": "Edit", "tool_input": {"file_path": "a.xml"}}}
    _fake_replay(monkeypatch, tmp_path)
    assert "reference_host" not in xc.replay([edit], CODEX, None, REPO, workers=1)[0]     # not a shell


def test_buckets_that_do_not_sum_to_the_total_are_an_engine_error():
    with pytest.raises(AssertionError):
        xc.summarise(_res(3), n_total=5, buckets={"replayed": 3}, gaps={}, min_cases=1)


def _inert(n):
    return [{"label": f"i{i}", "kind": "shell-bash", "reference": "inert", "adapter": "inert"} for i in range(n)]


def test_inert_agreeing_with_inert_is_not_a_pass():
    """R2-F3 (v4.0.0 review): with a broken X4_PYTHON every guard answered "checked nothing" and
    so did the adapter -- 3 of 3 "agree", exit 0. Inert on both sides examined nothing."""
    rc, text = xc.summarise(_inert(3), n_total=3, buckets={"replayed": 3}, gaps={}, min_cases=1)
    assert rc == xc.RC_NOTHING and "inert" in text.lower()


def test_the_floor_counts_only_CHECKED_cases():
    rc, _ = xc.summarise(_res(79) + _inert(10), n_total=89, buckets={"replayed": 89}, gaps={}, min_cases=80)
    assert rc == xc.RC_NOTHING


def test_TWIN_a_few_inert_cases_beside_enough_checked_ones_pass():
    assert xc.summarise(_res(80) + _inert(10), n_total=90, buckets={"replayed": 90}, gaps={},
                        min_cases=80)[0] == xc.RC_OK
    assert xc.summarise(_res(1) + _inert(2), n_total=3, buckets={"replayed": 3}, gaps={},
                        min_cases=1)[0] == xc.RC_OK


def test_a_gap_is_printed_on_a_passing_run():
    rc, text = xc.summarise(_res(90), n_total=90, buckets={"replayed": 90}, gaps={"shell-powershell": "no PS"},
                            min_cases=80)
    assert rc == xc.RC_OK and "GAP" in text and "shell-powershell" in text


@pytest.mark.parametrize("ref", ["error", "unreadable"])
def test_a_case_with_NO_reference_verdict_cannot_pass(ref):
    """FX-G3 (v4.0.0 delta review): a reference hook that could not run was read as an allow, and
    summarise returned 0. A case with no reference verdict is an ERROR whatever the adapter says --
    including when the adapter AGREES with the error string."""
    broken = {"label": "x0", "kind": "shell-bash", "reference": ref, "reference_why": "exited 127",
              "adapter": "allow"}
    rc, text = xc.summarise(_res(90) + [broken], n_total=91, buckets={"replayed": 91}, gaps={}, min_cases=80)
    assert rc == xc.RC_ERROR and "x0" in text and "exited 127" in text
    rc, _ = xc.summarise(_res(90) + [dict(broken, adapter=ref)], n_total=91, buckets={"replayed": 91},
                         gaps={}, min_cases=80)
    assert rc == xc.RC_ERROR


def test_ONE_bash_resolver_the_engine_asks_gitbash(monkeypatch, tmp_path):
    """FX-G3: x4conformance carried its own X4_BASH > which() resolver beside gitbash.py's. The
    shared resolver REFUSES an X4_BASH that points at nothing; the old private one returned it."""
    import gitbash
    assert xc.find_bash is gitbash.find_bash
    monkeypatch.setenv("X4_BASH", str(tmp_path / "no-such-bash.exe"))
    assert xc._bash() is None


def test_TWIN_an_X4_BASH_that_exists_is_used(monkeypatch, tmp_path):
    fake = tmp_path / "bash.exe"; fake.write_bytes(b"")
    monkeypatch.setenv("X4_BASH", str(fake))
    assert xc._bash() == str(fake)


def test_TWIN_the_same_case_WITH_a_reference_verdict_passes():
    ok = {"label": "x0", "kind": "shell-bash", "reference": "allow", "reference_why": "", "adapter": "allow"}
    assert xc.summarise(_res(90) + [ok], n_total=91, buckets={"replayed": 91}, gaps={},
                        min_cases=80)[0] == xc.RC_OK


def test_shared_context_refusal_decodes_as_inert_not_policy_denial():
    raw = _hso(permissionDecision="deny", permissionDecisionReason=UNKNOWN_EXECUTION_CWD)
    assert xc.decode(CODEX["output"], raw, 0) == ("inert", UNKNOWN_EXECUTION_CWD)
    assert json.loads(raw)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("reason", [
    "prefix " + UNKNOWN_EXECUTION_CWD,
    UNKNOWN_EXECUTION_CWD + " extra",
    UNKNOWN_EXECUTION_CWD + "\n",
    UNKNOWN_EXECUTION_CWD.replace("session directory", "execution directory"),
    "GUARD INERT: unrelated failure",
    "BLOCKED: path includes X4 GUARD INERT: text",
    "prefix X4 GUARD INERT: y",
    "BLOCKED: protected reference path",
])
def test_TWIN_inexact_or_embedded_inert_text_remains_policy_denial(reason):
    raw = _hso(permissionDecision="deny", permissionDecisionReason=reason)
    assert xc.decode(CODEX["output"], raw, 0) == ("deny", reason)


@pytest.mark.parametrize("kind", ["shell-bash", "shell-powershell"])
def test_codex_policy_parity_supplies_context_without_changing_capture(tmp_path, kind):
    case = CODEX["cases"][kind]
    fixture = Path(CODEX["_dir"]) / case["payload"]
    before = fixture.read_bytes()
    assert "workdir" not in json.loads(before)["tool_input"]
    row = {"kind": kind, "payload": {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}}
    payload = xc.build_payload(row, CODEX, str(tmp_path))
    assert payload["cwd"] == payload["tool_input"].get("workdir") == str(tmp_path)
    assert payload["tool_input"]["command"] == "echo hello"
    assert fixture.read_bytes() == before
    assert CODEX["run_cwd"] == "neutral"


def test_native_payload_passthrough_does_not_invent_execution_context(tmp_path):
    raw = {"tool_name": "Bash", "tool_input": {"command": "rm notes.txt"}, "cwd": str(tmp_path)}
    built = xc.build_payload({"native": raw}, CODEX, str(tmp_path))
    assert built == raw and built is not raw
    assert "workdir" not in built["tool_input"]


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_extra_policy_parity_supplies_its_own_context(monkeypatch, tmp_path, shell):
    import test_codex_conformance as conformance
    seen = []
    def observe(row, *_args):
        seen.append(row)
        return "allow", ""
    monkeypatch.setattr(conformance.xc, "adapter_verdict", observe)
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    case = {"id": "owned-extra-context", "fixture": "bash_powershell", "command": "echo hello", "shell": shell, "cwd": "{TK}"}
    assert conformance.extra_codex(case, tmp_path, toolkit, {}) == "allow"
    assert len(seen) == 1
    payload = seen[0]["native"]
    assert payload["cwd"] == payload["tool_input"].get("workdir") == str(toolkit)


def test_policy_parity_refuses_a_population_that_checked_no_policy(monkeypatch):
    import test_codex_conformance as conformance
    rows = [{"label": str(n), "got": "inert", "payload": {
        "tool_name": "Bash", "tool_input": {"command": "echo hello"}}} for n in range(80)]
    results = [{"label": str(n), "kind": "shell-bash", "reference": "inert",
                "adapter": "inert", "text": "checked nothing"} for n in range(80)]
    monkeypatch.setattr(conformance.xc, "replay", lambda *_args, **_kwargs: results)
    with pytest.raises(AssertionError, match="cases evaluated game policy"):
        conformance.test_every_replayable_case_agrees((rows, None))

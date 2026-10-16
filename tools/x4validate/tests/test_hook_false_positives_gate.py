"""gates/hook_false_positives.py -- the pure parts of the corpus replay.

The subprocess replay itself takes ~30 minutes and is not tested here. What IS
tested is everything that turned out to be wrong about the first version
(BLIND-SPOTS F82 follow-up, 2026-08-30):

- the artifact carried capped EXAMPLES and no COUNTS, so "1,450" and "25" were
  indistinguishable in the file;
- the measurement OVERWROTE its own baseline, so nothing could be diffed;
- the live hook was REDEPLOYED mid-run and the run reported a number anyway;
- a rule whose reason embeds $COMMAND produced one key per command.
"""

from __future__ import annotations

import ast
import re

import pytest

from conftest import import_gate

hfp = import_gate("hook_false_positives")


# ---- parse_verdict: the hook's stdout -> (verdict, reason) -------------------

def test_empty_stdout_is_allow():
    assert hfp.parse_verdict("") == ("allow", "")


def test_additional_context_is_advise():
    # jq emits the ESCAPED two-character form, so the fixture must be raw.
    out = r'{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"Redirecting output\nsecond line"}}'
    assert hfp.parse_verdict(out) == ("advise", "Redirecting output")


def test_permission_decision_wins_over_context():
    out = '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"BLOCKED: x"}}'
    assert hfp.parse_verdict(out) == ("deny", "BLOCKED: x")


def test_malformed_stdout_is_not_silently_allow():
    # An unparseable verdict is a broken hook, not permission. The old code
    # returned "allow" here -- the F79 shape (nothing read == fine).
    with pytest.raises(hfp.HookOutputError):
        hfp.parse_verdict("not json")


# ---- rule_key: one key per RULE, however the reason embeds the command --------

def test_rule_key_strips_embedded_command_after_confirm():
    a = hfp.rule_key("Deleting files in an X4 directory — confirm: cd /a && rm -rf b")
    b = hfp.rule_key("Deleting files in an X4 directory — confirm: rm -f other")
    assert a == b == "Deleting files in an X4 directory — confirm:"


def test_rule_key_strips_capital_confirm_variant():
    a = hfp.rule_key("WRITING OR DELETING UNDER YOUR DOCUMENTS FOLDER: this is outside. Confirm: cp x y")
    b = hfp.rule_key("WRITING OR DELETING UNDER YOUR DOCUMENTS FOLDER: this is outside. Confirm: rm z")
    assert a == b


def test_rule_key_is_not_truncated():
    long = "LONG JOB IN THE FOREGROUND — this is a known multi-minute command and the Bash tool hard-caps"
    assert hfp.rule_key(long) == long


# ---- resolved_paths: allow-listed X4_* only; a secret can never land in the artifact

def test_resolved_paths_keeps_only_allowlisted_vars():
    text = "X4_GAME=C:/g\nX4_NEXUS_KEY=abc123\nX4_DOCUMENTS=C:/d\nPATH=/x\n"
    got = hfp.resolved_paths(text)
    assert got == {"X4_GAME": "C:/g", "X4_DOCUMENTS": "C:/d"}
    assert "abc123" not in repr(got)


# ---- compare: per-rule drift over the INTERSECTION of commands ----------------

def _art(verdicts, **extra):
    return {"verdicts": verdicts, **extra}


def test_compare_reports_drift_only_on_shared_commands():
    base = _art({"h1": ["deny", "R1"], "h2": ["allow", ""], "h3": ["deny", "R2"]})
    now = _art({"h1": ["allow", ""], "h2": ["allow", ""], "h4": ["deny", "R2"]})
    rep = hfp.compare(base, now)
    assert rep["shared"] == 2
    assert rep["only_in_baseline"] == 1 and rep["only_in_current"] == 1
    # R1 fired on h1 before and not now -> drift on a shared command.
    assert rep["by_rule"]["R1"] == {"baseline": 1, "current": 0, "delta": -1}
    # R2's hits are on h3 (baseline only) and h4 (current only): NOT shared, so
    # it must not appear as drift -- corpus growth is not a rule change.
    assert "R2" not in rep["by_rule"]
    assert rep["drift"] is True


def test_compare_no_drift_when_shared_verdicts_identical():
    base = _art({"h1": ["deny", "R1"]})
    now = _art({"h1": ["deny", "R1"], "h9": ["deny", "R1"]})
    rep = hfp.compare(base, now)
    assert rep["drift"] is False and rep["by_rule"] == {}


# ---- stability: a hook that changed under the run voids the run --------------

def test_assert_stable_raises_when_hook_bytes_changed():
    with pytest.raises(hfp.UnstableInstrument):
        hfp.assert_stable({"protect-bash.sh": "aaa"}, {"protect-bash.sh": "bbb"})


def test_assert_stable_passes_when_identical():
    hfp.assert_stable({"protect-bash.sh": "aaa"}, {"protect-bash.sh": "aaa"})


# ---- summarize: counts are UNCAPPED and sum to the verdict totals -------------

def test_summarize_counts_every_hit_not_just_examples():
    verdicts = {f"h{i}": ["deny", "R1"] for i in range(40)}
    verdicts["a"] = ["allow", ""]
    s = hfp.summarize(verdicts, example_cap=25)
    assert s["counts"] == {"allow": 1, "deny": 40}
    assert s["counts_by_rule"] == {"R1": 40}
    assert len(s["examples_by_rule"]["R1"]) == 25
    assert sum(s["counts_by_rule"].values()) == s["counts"]["deny"]


# ---- extract_commands: the replay must send what the hook READS -------------
# The first version sent only `command`. The LONG JOB rule reads
# run_in_background and the timeout-cap rule reads timeout, so every
# historically-backgrounded long job replayed as FOREGROUND and the timeout rule
# could never fire -- a wrong-population measurement (CLAUDE.md gotcha #20).

def _rec(cmd, **inp):
    return {"message": {"content": [{"type": "tool_use", "name": "Bash",
                                     "input": {"command": cmd, **inp}}]}}


def test_extract_commands_keeps_background_and_timeout():
    items = hfp.extract_commands([_rec("sleep 1", run_in_background=True, timeout=900000)])
    assert items == [{"command": "sleep 1", "run_in_background": True, "timeout": 900000}]


def test_extract_commands_dedupes_on_all_three_fields():
    recs = [_rec("x"), _rec("x"), _rec("x", run_in_background=True)]
    items = hfp.extract_commands(recs)
    assert [i["run_in_background"] for i in items] == [False, True]


def test_extract_commands_skips_non_bash_and_empty():
    recs = [_rec(""), {"message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
            {"message": {"content": "plain text"}}]
    assert hfp.extract_commands(recs) == []


def test_payload_carries_all_three_fields():
    p = hfp.payload({"command": "ls", "run_in_background": True, "timeout": 5})
    got = __import__("json").loads(p)
    assert got == {"tool_name": "Bash",
                   "tool_input": {"command": "ls", "run_in_background": True, "timeout": 5}}


def test_rule_key_collapses_the_timeout_value():
    # The cap rule embeds the number it saw, which split one rule into a key per
    # value ("900000ms", "1200000ms") the first time the field reached the hook.
    a = hfp.rule_key("TIMEOUT ABOVE THE CAP: you passed 900000ms, but the Bash tool's maximum is 600000ms.")
    b = hfp.rule_key("TIMEOUT ABOVE THE CAP: you passed 1200000ms, but the Bash tool's maximum is 600000ms.")
    assert a == b and "900000" not in a


# ---- code review 2026-08-30: allow-producing failure paths and partial baselines

def test_interpret_treats_nonzero_exit_as_broken_instrument():
    # MEASURED by the reviewer: JQ=no_such_binary -> rc 0, EMPTY stdout, stderr noise,
    # because protect-bash.sh exits 0 on an empty $COMMAND after jq fails. The old
    # decide() discarded rc and stderr and read that as "allow".
    with pytest.raises(hfp.HookOutputError):
        hfp.interpret(1, "", "")


def test_interpret_empty_verdict_with_stderr_noise_is_not_allow():
    with pytest.raises(hfp.HookOutputError):
        hfp.interpret(0, "", "bash: jq: command not found")


def test_interpret_verdict_with_benign_stderr_still_counts_and_flags_noise():
    out = '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"X"}}'
    assert hfp.interpret(0, out, "warning: NUL byte") == ("deny", "X", True)


def test_interpret_clean_allow():
    assert hfp.interpret(0, "", "") == ("allow", "", False)


def test_parse_verdict_rejects_json_without_the_hook_envelope():
    for bad in ('{"foo": 1}', "[]", "null"):
        with pytest.raises(hfp.HookOutputError):
            hfp.parse_verdict(bad)


def test_partial_baseline_is_refused_in_compare():
    base = {"verdicts": {"h1": ["allow", ""]}, "replayed": 1, "distinct_total": 500}
    with pytest.raises(hfp.PartialBaseline):
        hfp.check_baseline(base)


def test_old_format_baseline_is_refused_not_a_keyerror():
    with pytest.raises(hfp.PartialBaseline):
        hfp.check_baseline({"distinct": 10852, "counts": {}, "by_rule": {}})


def test_complete_baseline_passes():
    hfp.check_baseline({"verdicts": {"h1": ["allow", ""]}, "replayed": 1, "distinct_total": 1})


def test_extract_commands_passes_raw_values_through():
    # The hook compares the LITERAL "true"; bool("false") would have made a string
    # "false" background-true, a population mismatch the hook would never produce.
    items = hfp.extract_commands([_rec("x", run_in_background="false", timeout="900000")])
    assert items[0]["run_in_background"] == "false" and items[0]["timeout"] == "900000"


def test_item_hash_depends_on_all_three_fields():
    a = {"command": "x", "run_in_background": False, "timeout": 0}
    b = {**a, "run_in_background": True}
    c = {**a, "timeout": 5}
    assert len({hfp.item_hash(a), hfp.item_hash(b), hfp.item_hash(c)}) == 3


def test_summarize_empty():
    assert hfp.summarize({}) == {"counts": {}, "counts_by_rule": {}, "examples_by_rule": {}}


def test_compare_reports_a_rule_swap_as_moved_commands():
    base = _art({"h1": ["deny", "R1"], "h2": ["deny", "R2"]})
    now = _art({"h1": ["deny", "R2"], "h2": ["deny", "R1"]})
    rep = hfp.compare(base, now)
    assert rep["changed"] == 2 and rep["drift"] is True
    assert rep["moved"] == {("R1", "R2"): 1, ("R2", "R1"): 1}


def _cfgs(tk):
    return {"x4-paths.env": tk / "x4-paths.env", ".claude/x4-paths.env": tk / ".claude" / "x4-paths.env"}


def _hook_fixture(root):
    for name in ("protect-bash.sh", "_x4-env.sh", "hook_facts.py", "ps_translate.ps1", "save_policy.py", "x4guard.py"):
        (root / name).write_text("original", encoding="utf-8")


_FINGERPRINT_CORE = ("__init__.py", "approval.py", "contracts.py", "dispatch.py", "durable.py", "locking.py",
                     "preservation.py", "profiles.py", "telemetry.py", "workspace.py")


def _runtime_fixture(root):
    hooks = root / ".claude/hooks"
    hooks.mkdir(parents=True)
    _hook_fixture(hooks)
    package = root / "tools/modding_harness"
    package.mkdir(parents=True)
    for name in _FINGERPRINT_CORE:
        (package / name).write_text("original", encoding="utf-8")
    (root / "tools/modding-harness.lock.json").write_text("owned-lock", encoding="utf-8")
    return hooks


@pytest.mark.parametrize("relative", [".claude/hooks/save_policy.py", ".claude/hooks/x4guard.py",
    "tools/modding-harness.lock.json", *["tools/modding_harness/" + name for name in _FINGERPRINT_CORE]])
@pytest.mark.parametrize("change", ["edit", "remove"])
def test_save_runtime_dependency_changes_void_the_replay(tmp_path, relative, change):
    hooks = _runtime_fixture(tmp_path)
    before = hfp.hash_hooks(hooks)
    target = tmp_path / relative
    if change == "edit":
        target.write_text("changed", encoding="utf-8")
    else:
        target.unlink()
    with pytest.raises(hfp.UnstableInstrument):
        hfp.assert_stable(before, hfp.hash_hooks(hooks))


def test_an_added_runtime_module_voids_the_replay(tmp_path):
    hooks = _runtime_fixture(tmp_path)
    before = hfp.hash_hooks(hooks)
    (tmp_path / "tools/modding_harness/new_dependency.py").write_text("new", encoding="utf-8")
    with pytest.raises(hfp.UnstableInstrument):
        hfp.assert_stable(before, hfp.hash_hooks(hooks))


def test_resolved_foreign_toolkit_runtime_is_the_one_fingerprinted(tmp_path):
    local, foreign = tmp_path / "local", tmp_path / "foreign"
    hooks = _runtime_fixture(local)
    _runtime_fixture(foreign)
    before = hfp.hash_hooks(hooks, toolkit=foreign)
    hfp.assert_complete_runtime(before)
    (local / "tools/modding_harness/contracts.py").write_text("unused", encoding="utf-8")
    hfp.assert_stable(before, hfp.hash_hooks(hooks, toolkit=foreign))
    (foreign / "tools/modding_harness/contracts.py").write_text("used", encoding="utf-8")
    with pytest.raises(hfp.UnstableInstrument):
        hfp.assert_stable(before, hfp.hash_hooks(hooks, toolkit=foreign))


@pytest.mark.parametrize("fault", ["missing", "unreadable"])
def test_incomplete_runtime_fingerprint_refuses_qualification(tmp_path, monkeypatch, fault):
    hooks = _runtime_fixture(tmp_path)
    target = tmp_path / "tools/modding_harness/contracts.py"
    if fault == "missing":
        target.unlink()
    else:
        original = type(target).read_bytes
        def read(path):
            if path == target:
                raise PermissionError("owned fingerprint fault")
            return original(path)
        monkeypatch.setattr(type(target), "read_bytes", read)
    with pytest.raises(hfp.UnstableInstrument, match="contracts.py"):
        hfp.assert_complete_runtime(hfp.hash_hooks(hooks))


def test_gate_refuses_missing_runtime_before_controls_or_corpus(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    _runtime_fixture(tmp_path)
    (tmp_path / "tools/modding_harness/contracts.py").unlink()
    monkeypatch.setattr(hfp._env, "extensions", lambda: tmp_path / "extensions")
    calls = []
    def resolved_only(argv, **kwargs):
        calls.append(argv)
        assert argv[1] == "-c", "no control or corpus hook should run on an incomplete runtime"
        return SimpleNamespace(stdout="X4_TOOLKIT=" + tmp_path.as_posix() + "\n")
    monkeypatch.setattr(hfp.subprocess, "run", resolved_only)
    monkeypatch.setattr(hfp, "collect_corpus", lambda: pytest.fail("must refuse before reading a corpus"))
    assert hfp.run(["--out=" + str(tmp_path / "unused.json")]) == 2
    assert len(calls) == 1 and "contracts.py" in capsys.readouterr().err
    assert not (tmp_path / "unused.json").exists()


@pytest.mark.parametrize("changed", ["protect-bash.sh", "_x4-env.sh",
                                     "hook_facts.py", "ps_translate.ps1"])
def test_changing_each_verdict_dependency_voids_the_replay(tmp_path, changed):
    _hook_fixture(tmp_path)
    before = hfp.hash_hooks(tmp_path)
    hfp.assert_stable(before, hfp.hash_hooks(tmp_path))
    (tmp_path / changed).write_text("changed", encoding="utf-8")
    with pytest.raises(hfp.UnstableInstrument, match=re.escape(changed)):
        hfp.assert_stable(before, hfp.hash_hooks(tmp_path))


def test_hash_hooks_includes_paths_env_with_absent_sentinel(tmp_path):
    _hook_fixture(tmp_path)
    h = hfp.hash_hooks(tmp_path, paths_envs=_cfgs(tmp_path))
    assert h["x4-paths.env"] == "absent" and h[".claude/x4-paths.env"] == "absent"
    (tmp_path / "x4-paths.env").write_text("X4_GAME=/g")
    assert hfp.hash_hooks(tmp_path, paths_envs=_cfgs(tmp_path))["x4-paths.env"] != "absent"


def test_a_LEGACY_config_changing_mid_run_voids_it_too(tmp_path):
    """Plan 3 lane I: the loader reads the root file, else the 3.x one -- either moving
    changes what the hooks resolve, so the run must void if EITHER moves."""
    _hook_fixture(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "x4-paths.env").write_text("X4_GAME=/g")
    before = hfp.hash_hooks(tmp_path, paths_envs=_cfgs(tmp_path))
    (tmp_path / ".claude" / "x4-paths.env").write_text("X4_GAME=/other")
    same, _ = hfp.hook_same(before, hfp.hash_hooks(tmp_path, paths_envs=_cfgs(tmp_path)))
    assert same is False


# ---- the artifact must never carry a secret VALUE, whatever the corpus said ----
# The allow-list covers env VALUES written on purpose; examples carry the first
# 120 chars of historical COMMANDS, which the list cannot vet. So the artifact is
# scanned for the VALUE of every secret-shaped variable before it is written.

def test_secret_scan_finds_a_secret_value_in_the_blob():
    env = {"X4_NEXUS_KEY": "abcd1234SECRET", "PATH": "/x", "MY_TOKEN": "tok9"}
    hits = hfp.secret_scan('{"examples": ["curl -H apikey:abcd1234SECRET"]}', env)
    assert hits == ["X4_NEXUS_KEY"]


def test_secret_scan_ignores_names_and_short_values():
    env = {"X4_NEXUS_KEY": "abcd1234SECRET", "EMPTY_KEY": "", "SHORT_TOKEN": "ab"}
    assert hfp.secret_scan("mentions X4_NEXUS_KEY by name only; ab", env) == []


# ---- "hook DIFFERENT" must mean the FILES differ, not the key set ------------
# MEASURED 2026-08-30: a baseline recorded before x4-paths.env was hashed (2 keys)
# compared against a 3-key current dict read as "hook DIFFERENT" while both real
# files were byte-identical. Compare on the keys both sides have; name the rest.

def test_hook_same_on_common_keys_ignores_a_key_only_one_side_has():
    base = {"protect-bash.sh": "a", "_x4-env.sh": "b"}
    now = {"protect-bash.sh": "a", "_x4-env.sh": "b", "x4-paths.env": "absent"}
    same, extra = hfp.hook_same(base, now)
    assert same is True and extra == ["x4-paths.env"]


def test_hook_same_detects_a_real_change():
    same, _ = hfp.hook_same({"protect-bash.sh": "a"}, {"protect-bash.sh": "b"})
    assert same is False


# ---- select_subset: the targeted replay that makes per-rule iteration cheap ----
# A full compare is ~35 min alone and was killed at two hours when other jobs
# competed with it. Only commands a rule actually caught can change when that
# rule's predicate changes -- plus a control drawn from allows, because a fix that
# turns an allow into anything is a regression, not a delta.

def _base(rows):
    return {"verdicts": {h: list(v) for h, v in rows.items()},
            "replayed": len(rows), "distinct_total": len(rows)}


def test_select_subset_picks_only_the_named_rule():
    b = _base({"h1": ("deny", "WRONG TOOL: recursive"), "h2": ("deny", "LONG JOB"),
               "h3": ("advise", "WRONG TOOL: recursive"), "h4": ("allow", "")})
    targets, controls = hfp.select_subset(b, "WRONG TOOL", n_control=0, seed=1)
    assert sorted(targets) == ["h1", "h3"] and controls == []


def test_select_subset_controls_come_only_from_allows_and_are_deterministic():
    b = _base({f"a{i}": ("allow", "") for i in range(50)} | {"d1": ("deny", "R")})
    _, c1 = hfp.select_subset(b, "R", n_control=5, seed=7)
    _, c2 = hfp.select_subset(b, "R", n_control=5, seed=7)
    _, c3 = hfp.select_subset(b, "R", n_control=5, seed=8)
    assert c1 == c2 and len(c1) == 5 and all(h.startswith("a") for h in c1)
    assert c1 != c3


def test_select_subset_caps_controls_at_what_exists():
    b = _base({"a1": ("allow", ""), "d1": ("deny", "R")})
    _, c = hfp.select_subset(b, "R", n_control=99, seed=1)
    assert c == ["a1"]


def test_select_subset_refuses_a_prefix_that_matches_nothing():
    # A typo would otherwise replay only the controls and report a clean zero --
    # a narrowing step that reports success, which is what this register is about.
    b = _base({"d1": ("deny", "WRONG TOOL: recursive"), "a1": ("allow", "")})
    with pytest.raises(hfp.NoSuchRule):
        hfp.select_subset(b, "LONG JOB", n_control=1, seed=1)


# --- G1-3: the targeted delta must compare over the REPLAYED set ------------------
#
# `--rule` is the mode used to verify a guard fix. It compared `still` -- countable
# only over commands STILL IN THE CORPUS -- against the FULL recorded baseline, so
# every command that aged out since the baseline was reported as a fix. The bias
# points at "it worked", which is the one direction a verification tool must not lean.

def _now(*pairs):
    """hash -> (verdict, rule), the shape the replay builds."""
    return {h: (v, "some rule") for h, v in pairs}


def test_an_AGED_OUT_baseline_hit_is_not_counted_as_FIXED():
    """★ G1-3. Two targets recorded, one gone from the corpus, the survivor still
    fires. Nothing was fixed, and the delta must say so."""
    d = hfp.rule_delta(["a", "b"], [], _now(("a", "deny")))
    assert d.replayed == 1 and d.still == 1
    assert d.gone == 1, "the aged-out hit must be reported, not absorbed"
    assert d.still - d.replayed == 0, (
        "the delta is over the REPLAYED set; against the recorded population this "
        "read -1 and claimed a fix that never happened")


def test_a_REAL_fix_still_reads_as_one():
    """The twin. Comparing over the intersection must not hide a genuine improvement --
    otherwise the fix trades a false positive for a false negative."""
    d = hfp.rule_delta(["a", "b"], [], _now(("a", "allow"), ("b", "deny")))
    assert d.replayed == 2 and d.still == 1 and d.gone == 0
    assert d.still - d.replayed == -1, "a command that stopped firing IS the signal"


def test_an_AGED_OUT_CONTROL_does_not_count_as_STILL_ALLOWING():
    """The same defect in the loosening check, which the audit did not name.

    A control absent from the corpus is not in `moved_controls`, so the old
    `len(cset) - len(moved)` form counted it as passing -- and could print 400/400
    having replayed none of them. This is the check that catches a guard fix which
    newly DENIES real work; it must not pass vacuously.
    """
    d = hfp.rule_delta([], ["c1", "c2"], _now(("c1", "allow")))
    assert d.replayed_controls == 1 and d.gone_controls == 1
    assert d.moved_controls == []
    assert d.replayed_controls - len(d.moved_controls) == 1, (
        "1 of 1 replayed, not 2 of 2 -- the second control was never exercised")


def test_a_control_that_STOPPED_allowing_is_reported():
    """The control's own twin: a real loosening regression must still be caught."""
    d = hfp.rule_delta([], ["c1"], _now(("c1", "deny")))
    assert d.moved_controls == ["c1"]


# --- G1-4: unparseable transcript lines are counted, not silently dropped ---------

def test_an_unparseable_transcript_line_is_COUNTED(tmp_path, monkeypatch):
    """`_records` skipped malformed lines with no count, so a corpus that quietly
    shrank was indistinguishable from one that was always this size. And
    `errors="replace"` means one corrupt byte can turn a VALID record into an
    unparseable one, so the count is not hypothetical."""
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "a.jsonl").write_text(
        '{"ok": 1}' + chr(10) + "{not json" + chr(10) + '{"ok": 2}' + chr(10),
        encoding="utf-8")
    import toolkit_usage
    monkeypatch.setattr(toolkit_usage, "transcript_dir", lambda: d)

    got = list(hfp._records())
    assert len(got) == 2, "the two well-formed records must still come through"
    assert hfp.dropped_lines == {"a.jsonl": 1}, hfp.dropped_lines
    assert "1 unparseable transcript line" in hfp.dropped_note()


def test_a_CLEAN_transcript_reports_NOTHING(tmp_path, monkeypatch):
    """The twin. A clean run must not print a '0 dropped' line -- noise in a report is
    how a real one stops being read."""
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "a.jsonl").write_text('{"ok": 1}' + chr(10), encoding="utf-8")
    import toolkit_usage
    monkeypatch.setattr(toolkit_usage, "transcript_dir", lambda: d)
    hfp.dropped_lines.clear()
    list(hfp._records())
    assert hfp.dropped_lines == {}
    assert hfp.dropped_note() == ""


# ---- HOOK_TIMEOUT: a hook that never returns is not a verdict either ---------
# MEASURED 2026-09-06: a full-corpus replay stalled at 15,000 of 17,202 with no
# diagnostic and had to be killed -- 14 workers, one wedged call, nothing able to
# say WHICH command. The root cause is fixed at source (hook_facts._MAX_RESOLVED,
# BLIND-SPOTS F102), so this path should now be unreachable; it is tested because
# it is the only thing that can turn the NEXT invisible wedge into a named command.

def test_the_timeout_is_far_above_a_real_hook_invocation():
    """A ceiling that ordinary work can reach is a flake, not a guard. The hook
    decides an ordinary command in about 0.25s, so 30s is ~120x headroom."""
    assert hfp.HOOK_TIMEOUT >= 10, "too tight to distinguish slow from wedged"
    assert hfp.HOOK_TIMEOUT <= 120, "so loose that a wedge still stalls the run"


def test_the_timeout_failure_is_a_HookOutputError_which_forces_a_REFUSAL():
    """Structural, not textual (gotcha #37): HookOutputError is what the gate's own
    refusal path keys on, so the timeout must raise THAT type and not a bare
    RuntimeError that some caller might swallow."""
    assert issubclass(hfp.HookOutputError, RuntimeError)
    src = __import__("inspect").getsource(hfp.run)
    tree = ast.parse(src.lstrip())
    handlers = [h for node in ast.walk(tree) if isinstance(node, ast.Try)
                for h in node.handlers]
    timeout_handlers = [
        h for h in handlers
        if h.type is not None and "TimeoutExpired" in ast.dump(h.type)]
    assert timeout_handlers, "no handler for a hook that never returns"
    for h in timeout_handlers:
        raises = [n for n in ast.walk(h) if isinstance(n, ast.Raise)]
        assert raises, "the timeout handler must RAISE, never fall through to a verdict"


# --- R5-9 / R5-13 (v4.0.0 review): which config the hash covers, under an honest label ----

def test_config_hash_labels_cannot_collide_with_the_3x_artifacts_label(tmp_path):
    """In v3.3.1 the label `x4-paths.env` hashed `<tk>/.claude/x4-paths.env`; 4.0 pointed the
    SAME label at the root file, so an old baseline and a new run compared two different files
    under one key. Each label now names its location, so hook_same never compares them."""
    tk = tmp_path / "tk"
    got = hfp.config_files_to_hash(tk, {})
    assert set(got) == {"x4-paths.env (4.x root)", ".claude/x4-paths.env (3.x)"}, got
    assert got["x4-paths.env (4.x root)"] == tk / "x4-paths.env"
    assert "x4-paths.env" not in got


def test_an_X4_CONFIG_file_is_hashed_too(tmp_path):
    """The hooks read X4_CONFIG's file when it is set; a run whose X4_CONFIG file changed
    mid-run is as void as one whose hook changed (R5-9: it was never hashed)."""
    cfg = tmp_path / "elsewhere.env"
    got = hfp.config_files_to_hash(tmp_path / "tk", {"X4_CONFIG": str(cfg)})
    assert got.get("X4_CONFIG") == cfg, got


def test_edge_sweep_blanks_X4_CONFIG_with_the_other_path_vars():
    """R5-9: blanking every _paths variable but X4_CONFIG left the 'unconfigured' sweep
    reading whatever file X4_CONFIG named."""
    from conftest import import_gate as _ig
    es = _ig("edge_sweep")
    assert "X4_CONFIG" in es._PATH_VARS


def test_edge_sweep_empty_config_overrides_self_discovery_with_blank_twin(tmp_path, monkeypatch):
    from pathlib import Path
    from conftest import import_gate as _ig
    from x4validate import _paths
    es = _ig("edge_sweep")
    toolkit = tmp_path / "configured-toolkit"
    toolkit.mkdir()
    own = toolkit / "x4-paths.env"
    own.write_text("X4_GAME=/fictional/game\n", encoding="utf-8")
    away = tmp_path / "away"
    away.mkdir()
    monkeypatch.setattr(_paths, "_SELF", toolkit)
    monkeypatch.setattr(_paths, "_EXPLICIT", None)
    env = es.unconfigured_environment(away)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fixture = Path(env["X4_CONFIG"])
    assert fixture.is_file() and fixture.read_bytes() == b""
    assert all(env[k] == "" for k in es._PATH_VARS if k != "X4_CONFIG")
    assert _paths._find_env_file() == fixture
    assert _paths.parse_env_report(fixture) == ({}, [])
    # Blanking X4_CONFIG recreates the leak; automatic self discovery is valid
    # production behavior and must remain available outside this fixture.
    monkeypatch.setenv("X4_CONFIG", "")
    assert _paths._find_env_file() == own

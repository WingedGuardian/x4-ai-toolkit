"""Framework audit F2/F3/F4 (docs/AUDIT-framework-2026-10-01.md), against disposable trees.

Ported from the framework-audit session's rolled-back 78841ba and re-verified test-first.
F3 deliberately differs from that commit: it also advised "VALIDATION PARTIAL" whenever the
validator listed a skip, and MEASURED 2026-10-02 over 50 real edits in 23 dev mods, 50 of 50
carry a routine `--file` skip ("every other check (--file)"), so that advisory would fire on
every edit. Only a validator that could not run, or a DEGRADED result, is disclosed.
"""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[3]
from _layout import common_hook_dir
HOOKS = common_hook_dir(REPO)
sys.path.insert(0, str(REPO / "scripts"))
from gitbash import find_bash


@pytest.fixture
def hook_env(tmp_path):
    env = dict(os.environ, X4_TOOLKIT=str(tmp_path), X4_CONFIG=str(tmp_path / "absent.env"),
               X4_PYTHON=sys.executable, X4_REFERENCE=str(tmp_path / "reference"),
               X4_BACKUPS=str(tmp_path / "backups"))
    env.pop("X4_GUARD_CHECK", None)
    return env


def run_hook(name, target, env):
    bash = find_bash()
    assert bash, "the shared Git Bash resolver found no usable shell"
    return subprocess.run([bash, str(HOOKS / name)],
                          input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(target)}}),
                          capture_output=True, text=True, env=env, timeout=60)


# ------------------------------------------------------------------ F2: backups
def test_F2_same_second_and_concurrent_backups_preserve_every_snapshot(tmp_path, hook_env):
    """Seconds-resolution names collided: the second backup overwrote the first, so the
    ORIGINAL text was lost from the trail. `date` is pinned so every call shares one second."""
    target = tmp_path / "record.xml"
    env = dict(hook_env)
    env["BASH_FUNC_date%%"] = "() { printf '%s\\n' '20000101_000000'; }"
    target.write_text("original", encoding="utf-8")
    first = run_hook("backup-before-edit.sh", target, env)
    target.write_text("intermediate", encoding="utf-8")
    second = run_hook("backup-before-edit.sh", target, env)
    assert first.returncode == second.returncode == 0 and not first.stdout and not second.stdout
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        calls = list(pool.map(lambda _: run_hook("backup-before-edit.sh", target, env), range(8)))
    assert all(r.returncode == 0 and not r.stdout for r in calls)
    snapshots = [p for p in (tmp_path / "backups").iterdir() if p.name != "AUDIT_LOG.txt"]
    assert len(snapshots) == 10
    assert {p.name.split("__", 1)[0] for p in snapshots} == {"20000101_000000"}
    assert sorted(p.read_text(encoding="utf-8") for p in snapshots) == ["intermediate"] * 9 + ["original"]
    log = (tmp_path / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert log.count("(backup:") == 10
    assert all(p.name in log for p in snapshots)


# ------------------------------------------------------------------ F3: validation
def _mod_with_patch(tmp_path):
    mod = tmp_path / "mod"
    (mod / "libraries").mkdir(parents=True)
    (mod / "content.xml").write_text('<content id="decoy"/>', encoding="utf-8")
    target = mod / "libraries" / "wares.xml"
    target.write_text('<diff><remove sel="/wares/missing"/></diff>', encoding="utf-8")
    return target


def _fake_uv(tmp_path, body):
    uv = tmp_path / "fake-uv"
    uv.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    uv.chmod(0o755)
    return uv


def _env(hook_env, tmp_path, uv, reader):
    env = dict(hook_env, UV=str(uv), X4V=str(tmp_path))
    if reader == "python":
        env["JQ"] = str(tmp_path / "missing-jq")
    return env


def _advice(r):
    assert r.returncode == 0
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("reader", ["jq", "python"])
@pytest.mark.parametrize("mode", ["missing", "crash", "invalid", "wrong_shape", "invalid_finding"])
def test_F3_a_validator_that_could_not_run_is_disclosed_not_silent(tmp_path, hook_env, mode, reader):
    """With UV pointing at nothing the hook exited 0 with empty output -- identical to a clean
    validation. Each failure shape must say the edit was NOT validated."""
    target = _mod_with_patch(tmp_path)
    body = {"crash": "exit 42", "invalid": "printf 'not-json'", "wrong_shape": "printf '{}'",
            "invalid_finding": "printf '%s' '{\"error_count\":1,\"findings\":[{}]}'"}
    uv = tmp_path / "fake-uv" if mode == "missing" else _fake_uv(tmp_path, body[mode])
    r = run_hook("x4validate-on-edit.sh", target, _env(hook_env, tmp_path, uv, reader))
    assert "VALIDATION NOT COMPLETED" in _advice(r)


@pytest.mark.parametrize("reader", ["jq", "python"])
def test_F3_a_DEGRADED_result_keeps_its_findings_and_says_it_is_incomplete(tmp_path, hook_env, reader):
    target = _mod_with_patch(tmp_path)
    data = json.dumps({"error_count": 1, "degraded": True,
                       "skipped": [{"what": "selector check", "why": "decoy input unavailable", "degraded": True}],
                       "findings": [{"severity": "error", "message": "CONTROL", "vpath": "patch.xml", "line": 1}]})
    uv = _fake_uv(tmp_path, "printf '%s' '" + data + "'\nexit 3")
    message = _advice(run_hook("x4validate-on-edit.sh", target, _env(hook_env, tmp_path, uv, reader)))
    assert "VALIDATION NOT COMPLETED" in message
    assert "CONTROL" in message and "decoy input unavailable" in message


@pytest.mark.parametrize("reader", ["jq", "python"])
def test_F3_TWIN_a_ROUTINE_skip_on_a_clean_edit_stays_quiet(tmp_path, hook_env, reader):
    """The measured flood guard: `--file` always lists a non-degraded skip. A clean run with
    that skip must stay silent, and an erroring one must read as findings, not as incomplete."""
    target = _mod_with_patch(tmp_path)
    routine = [{"what": "every other check (--file)", "why": "--file runs sel-resolution only",
                "degraded": False}]
    for errors in (0, 1):
        data = json.dumps({"error_count": errors, "degraded": False, "skipped": routine,
                           "findings": [] if not errors else
                           [{"severity": "error", "message": "CONTROL", "vpath": "patch.xml", "line": 1}]})
        uv = _fake_uv(tmp_path, "printf '%s' '" + data + "'\nexit " + str(errors))
        r = run_hook("x4validate-on-edit.sh", target, _env(hook_env, tmp_path, uv, reader))
        assert r.returncode == 0
        if errors:
            message = _advice(r)
            assert "CONTROL" in message and "NOT COMPLETED" not in message
        else:
            assert r.stdout == "", r.stdout


# ------------------------------------------------------------------ F4: lock freshness
def test_F4_CI_checks_the_lockfile_is_fresh_not_only_frozen():
    """`uv sync --frozen` consumes uv.lock without checking it against pyproject.toml
    (MEASURED by the audit: frozen sync rc 0 on a stale lock, `uv lock --check` rc 1)."""
    from _layout import require_repo
    require_repo(".github/workflows/ci.yml", why="CI configuration is not installed")
    ci = YAML(typ="safe").load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [s for job in ci["jobs"].values() for s in job.get("steps", [])]
    checks = [s for s in steps if "uv lock --check" in str(s.get("run", ""))]
    assert checks, "no CI step runs `uv lock --check`"
    assert all(s.get("working-directory") == "tools/x4validate" for s in checks), checks


# ------------------------------------------------------- I1: one red check skips nothing
#: A step's `if:` without a status function means `success() && ...`, so ONE early red
#: check (the hook audit, say) silently skipped the pytest suite, the skip ceilings and
#: both cold legs. Each job therefore names the steps that are SETUP (their failure makes
#: every later step meaningless, so they keep the default) and every later step must run
#: on `!cancelled()` gated on that setup -- or on a named earlier step it really needs.
_SETUP_MARKER = {"tests": "setup", "scrub": "checkout", "hook-mutation": "checkout"}


def _ungated_checks(ci) -> list[str]:
    """Names of check steps that an EARLIER failing check would skip."""
    bad = []
    for job_name, marker in _SETUP_MARKER.items():
        steps = ci["jobs"][job_name]["steps"]
        ids = [s.get("id") for s in steps]
        if marker not in ids:
            bad.append(f"{job_name}: no setup step with id '{marker}'")
            continue
        cut = ids.index(marker)
        for s in steps[:cut + 1]:
            if "if" in s:
                bad.append(f"{job_name}: setup step {s.get('name', s.get('uses'))!r} is conditional")
        seen = {marker}
        for s in steps[cut + 1:]:
            cond = str(s.get("if", ""))
            name = s.get("name", s.get("uses"))
            gate = re.findall(r"steps\.([A-Za-z0-9_-]+)\.outcome == 'success'", cond)
            if "!cancelled()" not in cond or not gate or not set(gate) <= seen:
                bad.append(f"{job_name}: {name!r} if={cond!r}")
            if s.get("id"):
                seen.add(s["id"])
    return bad


def test_I1_every_CI_check_runs_even_after_an_EARLIER_check_failed():
    from _layout import require_repo
    require_repo(".github/workflows/ci.yml", why="CI configuration is not installed")
    ci = YAML(typ="safe").load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert _ungated_checks(ci) == []


def test_I1_TWIN_the_gate_check_can_fail():
    """One twin per clause: no !cancelled(), no setup gate, a gate on a LATER/unknown id,
    a conditional setup step, a missing marker."""
    def wf(tests_steps, scrub_steps=None):
        return {"jobs": {"tests": {"steps": tests_steps},
                         "scrub": {"steps": scrub_steps or [{"uses": "co", "id": "checkout"}]},
                         "hook-mutation": {"steps": [{"uses": "co", "id": "checkout"}]}}}
    ok = "${{ !cancelled() && steps.setup.outcome == 'success' }}"
    setup = [{"uses": "co"}, {"name": "setup", "id": "setup", "run": "true"}]
    assert _ungated_checks(wf(setup + [{"name": "a", "if": ok}])) == []
    assert _ungated_checks(wf(setup + [{"name": "a"}]))
    assert _ungated_checks(wf(setup + [{"name": "a", "if": "${{ steps.setup.outcome == 'success' }}"}]))
    assert _ungated_checks(wf(setup + [{"name": "a", "if": "${{ !cancelled() }}"}]))
    assert _ungated_checks(wf(setup + [{"name": "a", "if": "${{ !cancelled() && steps.later.outcome == 'success' }}"},
                                       {"name": "b", "id": "later", "if": ok}]))
    assert _ungated_checks(wf([{"uses": "co", "if": "x"}, {"name": "s", "id": "setup"}]))
    assert _ungated_checks(wf([{"uses": "co"}]))

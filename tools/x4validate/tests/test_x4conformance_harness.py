"""x4conformance harness: the dump, the reference verdict, the neutral extras, and the identity
control that proves the engine itself agrees with the guards (#22: check the checker first)."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("x4conformance", REPO / "scripts" / "x4conformance.py")
xc = importlib.util.module_from_spec(spec); spec.loader.exec_module(xc)

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")),
                                reason="needs Git Bash and jq -- conformance harness NOT checked here")


def test_the_dump_is_big_enough_and_every_row_is_classified(conformance_dump):
    rows, _ = conformance_dump
    assert len(rows) >= 100
    kinds = [xc.classify(r) for r in rows]
    assert set(kinds) <= set(xc.KINDS) | {"no_native_analogue", xc.WINDOWS_PATH_DIALECT}
    assert sum(k != "no_native_analogue" for k in kinds) >= 80


def test_the_dump_sandbox_is_under_the_toolkit_never_tmp(conformance_dump):
    _, sbx = conformance_dump
    assert (REPO / ".test-sandbox") in sbx.parents


def test_the_neutral_run_dir_is_one_the_guards_have_NO_opinion_about(conformance_dump):
    """`run_cwd: neutral` is only neutral if a harmless relative write from it is ALLOWED. On
    ubuntu the old run dir sat in /tmp, which protect-bash denies writes into, so a mutant adapter
    that ignored the case's workdir still got the case's deny -- for the wrong reason (CI run
    37172347642). The row's own env, so the guards see the same configuration as a replay."""
    rows, _ = conformance_dump
    env_row = next(r for r in rows if (r.get("env") or {}).get("X4_TOOLKIT"))
    d = xc.neutral_run_dir(REPO)
    try:
        row = {"hook": "protect-bash.sh", "env": env_row["env"], "cwd": str(d),
               "payload": {"tool_name": "Bash", "tool_input": {"command": "echo x > libraries/w.xml"},
                           "cwd": str(d)}}
        assert xc.reference_verdict(row, REPO) == "allow", d
        # the control: the same relative write from the sandbox's reference/ IS denied
        ref = Path(xc._native_path(env_row["env"]["X4_TOOLKIT"])) / "reference"
        twin = dict(row, cwd=str(ref), payload=dict(row["payload"], cwd=str(ref)))
        assert xc.reference_verdict(twin, REPO) == "deny", ref
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_neutral_extras_add_powershell_cases_and_their_reference_controls_hold(conformance_dump):
    rows, _ = conformance_dump
    extras = xc.extra_rows(REPO, rows)
    assert sum(xc.classify(r) == "shell-powershell" for r in extras) >= 3
    for r in extras:                       # the reference control: the guard still says what the case expects
        assert xc.reference_verdict(r, REPO) == r["expect"], r["id"]


def test_identity_control_claude_profile_agrees_on_every_row(conformance_dump):
    rows, _ = conformance_dump
    rows = rows + xc.extra_rows(REPO, rows)
    prof = xc.load_profile("claude", REPO)
    res = xc.replay(rows, prof, None, REPO, workers=4)
    assert len(res) >= 80
    # The claude profile runs the hooks under X4_GUARD_CHECK=1, and since K3 (fc87208) that
    # read-only check ASKS where the native hook backs a save up and advises -- the one
    # divergence summarise() accepts (0830f4b). This test kept its own stricter comparison, so
    # #197 failed on both OS in CI run 39187589960. Same predicate as the engine, never a copy.
    bad = [r for r in res if r["reference"] != r["adapter"] and not xc._backup_only_divergence(r)]
    assert not bad, bad[:10]               # PER ITEM


def test_cleanup_removes_only_the_recorded_sandbox(tmp_path):
    base = tmp_path / "base"; keep = tmp_path / "keep"
    (base / "hooks.abc").mkdir(parents=True); keep.mkdir()
    xc.remove_sandbox(base / "hooks.abc", base)
    assert not (base / "hooks.abc").exists() and keep.exists()
    with pytest.raises(ValueError):
        xc.remove_sandbox(keep, base)      # TWIN: outside the base it refuses, deletes nothing
    assert keep.exists()

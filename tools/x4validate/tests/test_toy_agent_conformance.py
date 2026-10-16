"""The toy agent (spec 7.10): an adapter written only from ADAPTING.md passes `x4guard conformance`,
and the conformance it passes can fail: each mutant of the adapter turns it red on a non-empty case
set whose unmutated control is green.

The adapter (fixtures/toy_agent/toy_adapter.py) was written by a cold subagent given only
ADAPTING.md, TOY-AGENT.md, toy_agent.py and payloads/ (2026-10-03, recorded in
docs/superpowers/measurements/2026-10-03-adapting-cold-test.md). It is committed UNEDITED; its
profile differs from the cold one only in the adapter's path ({TOOLKIT}-relative).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
X4GUARD = REPO / ".claude" / "hooks" / "x4guard.py"
TOY = REPO / "tools" / "x4validate" / "tests" / "fixtures" / "toy_agent"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")),
                                reason="needs Git Bash and jq -- toy conformance NOT checked here")


def _engine():
    spec = importlib.util.spec_from_file_location("x4conformance", REPO / "scripts" / "x4conformance.py")
    xc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xc)
    return xc


def conformance(cases: Path, adapter: Path, *extra):
    return subprocess.run([sys.executable, str(X4GUARD), "conformance", "--profile", str(TOY / "profile.json"),
                           "--cases", str(cases), "--workers", "4", *extra, "--", sys.executable, str(adapter)],
                          capture_output=True, text=True, timeout=3600)


def _write(rows, path):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _recorded_inert(xc, row):
    """True when the row's RECORDED guard verdict says the guards could not check it."""
    try:
        hso = json.loads(row.get("out") or "{}").get("hookSpecificOutput") or {}
    except ValueError:
        return False
    return bool(xc.NOT_CHECKED.search(hso.get("permissionDecisionReason") or ""))


MUTANT_CASE_FLOORS = {"always_allow": 12, "ask_as_plain_deny": 12, "crash_exits_nonzero": 12,
                     "powershell_as_bash": 5, "ignores_workdir": 1}


def _checked_mutant_cases(xc, pool, pick, minimum):
    """Select a full checked control set using the guards' live read-only check protocol."""
    chosen = []
    for row in pool:
        if not pick(row) or _recorded_inert(xc, row) or not _replayable_here(xc, row):
            continue
        reference, why = xc.reference_verdict_detail(row, REPO)
        assert reference not in ("error", "unreadable"), f"guard qualification failed: {why}"
        if reference == "inert":
            print("unchecked candidate excluded:", row.get("label") or row.get("id") or row.get("n"))
            continue
        chosen.append(row)
        if len(chosen) == minimum:
            return chosen
    raise AssertionError(f"only {len(chosen)} checked cases can express this defect; "
                         f"need {minimum}, never lower the floor or drop the mutant")


def test_live_unchecked_candidate_is_replaced_while_retaining_twelve_checked_cases():
    xc = _engine()
    from types import SimpleNamespace
    measured = SimpleNamespace(NOT_CHECKED=xc.NOT_CHECKED, WINDOWS_PATH_DIALECT=xc.WINDOWS_PATH_DIALECT,
        reference_verdict_detail=lambda row, root: ("inert" if row["n"] == 0 else "ask", ""))
    rows = [{"n": n, "kind": "shell-bash", "got": "ask"} for n in range(13)]
    assert [r["n"] for r in _checked_mutant_cases(measured, rows, lambda row: True, 12)] == list(range(1, 13))
    with pytest.raises(AssertionError, match="need 12"):
        _checked_mutant_cases(measured, rows[:12], lambda row: True, 12)


def test_live_guard_error_refuses_selection_instead_of_shrinking_the_sample():
    xc = _engine()
    from types import SimpleNamespace
    measured = SimpleNamespace(NOT_CHECKED=xc.NOT_CHECKED, WINDOWS_PATH_DIALECT=xc.WINDOWS_PATH_DIALECT,
        reference_verdict_detail=lambda row, root: ("error", "owned nonanswer"))
    with pytest.raises(AssertionError, match="qualification failed"):
        _checked_mutant_cases(measured, [{"kind": "shell-bash"}], lambda row: True, 12)


@pytest.mark.parametrize("reason,inert", [
    ("X4 save dispatcher failed: ValueError; this call was not allowed.", True),
    ("X4 save dispatcher did not return a complete decision; this call was not allowed.", True),
    ("X4 GUARD INERT: no runtime", True),
    ("Permanent save deletion is not allowed.", False),
    ("This operation needs approval.", False),
])
def test_recorded_dispatcher_nonanswers_are_excluded_without_lowering_the_floor(reason, inert):
    xc = _engine()
    row = {"out": json.dumps({"hookSpecificOutput": {
        "permissionDecision": "deny", "permissionDecisionReason": reason}})}
    assert _recorded_inert(xc, row) is inert
    assert xc._hook_decision(row["out"]) == ("inert" if inert else "deny")


def test_the_toy_adapter_passes_full_conformance(conformance_dump, tmp_path):
    rows, _ = conformance_dump
    r = conformance(_write(rows, tmp_path / "all.jsonl"), TOY / "toy_adapter.py")
    assert r.returncode == 0, r.stdout[-4000:]
    assert "GAP" not in r.stdout                       # the toy maps all four kinds
    assert "extras: 6" in r.stdout                     # the neutral PowerShell/space/drive cases ran too


#: name -> ([(exact source text, replacement), ...], row selector). Each `old` is the COLD adapter's
#: real text and must occur exactly once; a mutant needing a fault to express (a crash) injects it.
MUTANTS = {
    "always_allow": ([("    deny(reason)\n", "    return\n")],
                     lambda r: r.get("got") == "deny"),
    "ask_as_plain_deny": ([("NEEDS YOUR APPROVAL: ", "")],
                          lambda r: r.get("got") == "ask"),
    "powershell_as_bash": ([('"--shell", sh,', '"--shell", "bash",')],
                           lambda r: r.get("kind") == "shell-powershell"),
    "ignores_workdir": ([('cwd = payload.get("workdir") or os.getcwd()', "cwd = os.getcwd()")],
                        lambda r: r.get("relative")),
    "crash_exits_nonzero": ([('    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))',
                              '    payload = json.loads(sys.stdin.buffer.read().decode("utf-8")); 1 / 0'),
                             ('deny("X4 GUARD INERT: toy adapter error (%s)" % type(e).__name__)', "raise")],
                            lambda r: r.get("got") == "deny"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_adapter_mutant_turns_conformance_red(name, conformance_dump, tmp_path):
    xc = _engine()
    edits, pick = MUTANTS[name]
    rows, _ = conformance_dump
    pool = rows + xc.extra_rows(REPO, rows)
    if name == "powershell_as_bash":
        # The historical fifth PS row was an unchecked dispatcher failure. Replace it with a distinct
        # checked append operation; do not lower the original five-case floor or edit the cold adapter.
        source = next(row for row in pool if row.get("id") == "powershell-set-content-into-reference")
        replacement = json.loads(json.dumps(source))
        replacement.update(id="powershell-add-content-literal-reference", label="mutant-control:PS literal append")
        command = source["payload"]["tool_input"]["command"].replace("Set-Content -Path", "Add-Content -LiteralPath")
        replacement["payload"]["tool_input"]["command"] = command
        pool.append(replacement)
    assert set(MUTANT_CASE_FLOORS) == set(MUTANTS)
    # Recorded prose is a compatibility filter. The live check protocol qualifies the actual control set;
    # unreadable guards fail selection, and unchecked rows require replacement before setting the floor.
    chosen = _checked_mutant_cases(xc, pool, pick, MUTANT_CASE_FLOORS[name])
    cases = _write(chosen, tmp_path / "c.jsonl")
    src = (TOY / "toy_adapter.py").read_text(encoding="utf-8")
    mutated = src
    for old, new in edits:
        assert src.count(old) == 1, f"{name}: anchor {old!r} matches {src.count(old)} times"
        mutated = mutated.replace(old, new)
    mutant = tmp_path / "toy_adapter.py"; mutant.write_text(mutated, encoding="utf-8")
    floor = ("--no-extras", "--min-cases", str(MUTANT_CASE_FLOORS[name]))
    control = conformance(cases, TOY / "toy_adapter.py", *floor)
    assert control.returncode == 0, control.stdout[-3000:]          # control first
    red = conformance(cases, mutant, *floor)
    assert red.returncode == 1, (name, red.returncode, red.stdout[-3000:])
    assert "below --min-cases" not in red.stdout, red.stdout[-3000:]   # red for the defect


def _replayable_here(xc, row, windows=None) -> bool:
    """False for a row the engine would bucket WINDOWS_PATH_DIALECT on this platform."""
    kind = row.get("kind") or xc.classify(row, windows=windows)
    return kind != xc.WINDOWS_PATH_DIALECT


def test_TWIN_a_backslash_path_row_is_replayable_on_Windows_only():
    xc = _engine()
    row = {"payload": {"tool_name": "Write", "tool_input": {"file_path": "C:\\tk\\reference\\x"}}}
    assert _replayable_here(xc, row, windows=True) is True
    assert _replayable_here(xc, row, windows=False) is False
    fwd = {"payload": {"tool_name": "Write", "tool_input": {"file_path": "/tk/reference/x"}}}
    assert _replayable_here(xc, fwd, windows=False) is True


def test_live_canary_through_the_toy_agent_blocks_and_its_control_writes(conformance_dump, tmp_path):
    """ADAPTING.md 5(b): the agent itself, not the adapter alone, refuses the decoy write. The
    sandbox layout with the toolkit OUTSIDE the game folder: inside it, dev/ is game install too."""
    rows, _ = conformance_dump
    env_row = next(r for r in rows if r["env"].get("X4_TOOLKIT") and r["env"].get("X4_GAME")
                   and not Path(r["env"]["X4_TOOLKIT"]).resolve().is_relative_to(Path(r["env"]["X4_GAME"]).resolve()))
    tk = Path(env_row["env"]["X4_TOOLKIT"])
    script = tmp_path / "calls.jsonl"
    decoy, ctrl = tk / "reference" / "canary-decoy.xml", tk / "dev" / "mymod" / "canary-ok.xml"
    _write([{"tool": "write_file", "args": {"target": str(decoy), "text": "x"}, "workdir": str(tk)},
            {"tool": "write_file", "args": {"target": str(ctrl), "text": "x"}, "workdir": str(tk)}], script)
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_") or "KEY" in k}
    env.update(env_row["env"], X4_GUARD_PY=str(X4GUARD))
    r = subprocess.run([sys.executable, str(TOY / "toy_agent.py"), "--root", str(tk), "--script", str(script),
                        "--hook", f'"{sys.executable}" "{TOY / "toy_adapter.py"}"'],
                       capture_output=True, text=True, env=env, timeout=600)
    try:
        assert not decoy.exists() and "TOY-BLOCK" in r.stdout, r.stdout
        assert ctrl.exists(), r.stdout                               # the control: writes do happen
    finally:
        ctrl.unlink(missing_ok=True)
        decoy.unlink(missing_ok=True)


def test_the_fail_mode_measurement_reproduces_the_toys_fail_open(tmp_path):
    """ADAPTING.md 1: the procedure finds what TOY-AGENT.md says -- exit 2 does NOT block the toy."""
    hook = tmp_path / "exit2.py"; hook.write_text("import sys; sys.exit(2)\n", encoding="utf-8")
    deny = tmp_path / "deny.py"; deny.write_text("print('TOY-BLOCK control')\n", encoding="utf-8")
    target = tmp_path / "decoy.txt"
    script = _write([{"tool": "write_file", "args": {"target": str(target), "text": "x"}, "workdir": str(tmp_path)}],
                    tmp_path / "s.jsonl")

    def go(h):
        target.unlink(missing_ok=True)
        subprocess.run([sys.executable, str(TOY / "toy_agent.py"), "--root", str(tmp_path), "--script", str(script),
                        "--hook", f'"{sys.executable}" "{h}"'], capture_output=True, timeout=60)
        return target.exists()
    assert go(deny) is False          # deny control blocks
    assert go(hook) is True           # exit 2 runs the call: fails open, as documented

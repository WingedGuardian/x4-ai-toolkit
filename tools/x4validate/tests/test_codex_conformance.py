"""Cross-agent conformance (spec 7.2): every guard case in scripts/test-hooks.sh, replayed through
the Claude hooks AND through the full Codex chain (codex-entry wrapper -> adapter -> guards) using
fixture-derived Codex envelopes with supplied execution context, must reach the SAME policy verdict.

The mechanism is the x4conformance engine (`x4guard conformance`, scripts/x4conformance.py) with
the Codex profile (scripts/conformance-profiles/codex.json) -- ONE mechanism, the same one any
other agent's adapter is held to (ADAPTING.md). Both verdicts are taken NOW, in the same kept
sandbox and env; the expected verdict is never hard-coded -- it is whatever the Claude hook says
(the guards are the policy; this suite checks the Codex translation keeps it). Per item, never a
total.

  Claude verdict: the hook script itself on the Claude-shaped payload (independent of x4guard),
                  re-run under X4_GUARD_CHECK=1 to tell "checked nothing" (inert) apart.
  Codex verdict:  the profile's fixture-derived payloads -- Bash -> captured bash_powershell fixture,
                  X4_CODEX_SHELL=bash (POSIX stand-in, M9); PowerShell -> same fixture,
                  X4_CODEX_SHELL=powershell, both with explicit synthetic tool_input.workdir;
                  Edit -> an apply_patch Update File; Write -> an
                  apply_patch Add File carrying the content -- decoded by the profile's output
                  rules (a deny "NEEDS YOUR APPROVAL:" -> ask; "X4 GUARD INERT" -> inert).
                  Grep/Glob/NotebookEdit, and payloads Codex cannot produce (no path, no
                  command), are counted as no_native_analogue.

Supplied shell workdir makes policy-parity inputs comparable; it does not claim that a live
Codex hook supplies workdir. Captured envelopes remain unchanged. Observed missing-workdir
refusals are exercised separately, including test_codex_execution_cwd.py.

The dump's sandbox lives under <repo>/.test-sandbox (the conftest `conformance_dump` fixture),
never the system temp folder: test-hooks.sh refuses a sandbox under /tmp, which is where pytest
puts tmp_path on Linux.

Slow (~130 cases through PowerShell + Python + bash): runs in parallel, 6 workers.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from codex_testlib import UNKNOWN_EXECUTION_CWD, FIX, REPO, XC as xc, make_sandbox, native

CODEX = xc.load_profile("codex", REPO)
PWSH = shutil.which("pwsh") or shutil.which("powershell")
BASH = os.environ.get("X4_BASH") or shutil.which("bash")
RANK = {"allow": 0, "advise": 1, "ask": 2, "deny": 3, "inert": 4}
EXTRAS = FIX.parent / "conformance_extra.yaml"
WORKERS = 6

pytestmark = pytest.mark.skipif(not (PWSH and BASH and shutil.which("jq")),
                                reason="needs PowerShell, Git Bash and jq -- conformance NOT checked here")


# ------------------------------------------------------------------ tests ------------- #

#: The four Claude tools with a native Codex form, and the field each must carry (an oracle written
#: from the payloads, independent of xc.classify).
NATIVE = {"Bash": "command", "PowerShell": "command", "Edit": "file_path", "Write": "file_path"}


def _bucket_problems(rows, classify) -> list:
    """Where the ENGINE's buckets (xc.bucket_counts over `classify`) disagree with a per-row tally
    taken straight from the payloads. R7-6: the old test summed a partition it had just made
    itself, so it could not fail."""
    want = {"replayed": 0}
    for r in rows:
        p = r.get("payload") or {}
        ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        field = NATIVE.get(p.get("tool_name"))
        v = ti.get(field) if field else None
        if not (isinstance(v, str) and v):
            key = "no_native_analogue"
        elif field == "file_path" and os.name != "nt" and chr(92) in v:
            key = xc.WINDOWS_PATH_DIALECT
        else:
            key = "replayed"
        want[key] = want.get(key, 0) + 1
    rows = [dict(r, kind=classify(r)) for r in rows]
    got = xc.bucket_counts(rows, sum(r["kind"] in CODEX["cases"] for r in rows), CODEX)
    problems = [] if got == want else [f"engine buckets {got} != per-row tally {want}"]
    if sum(got.values()) != len(rows):
        problems.append(f"engine buckets {got} do not sum to {len(rows)} rows")
    return problems


def test_buckets_sum(conformance_dump):
    rows, _ = conformance_dump
    assert _bucket_problems(rows, xc.classify) == []
    n = sum(xc.classify(r) in CODEX["cases"] for r in rows)
    assert n >= 80, n
    print("conformance buckets:", xc.bucket_counts(rows, n, CODEX))


def test_TWIN_the_bucket_check_can_fail():
    """A classify that loses Write rows must be caught, or the check above is decoration."""
    rows = [{"payload": {"tool_name": "Write", "tool_input": {"file_path": "a.xml", "content": ""}}},
            {"payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}}},
            {"payload": {"tool_name": "Grep", "tool_input": {"pattern": "x"}}}]
    assert _bucket_problems(rows, xc.classify) == []

    def broken(r):
        return "no_native_analogue" if r["payload"]["tool_name"] == "Write" else xc.classify(r)
    assert _bucket_problems(rows, broken)


def test_live_claude_verdicts_matched_the_harness(conformance_dump):
    """The dump's `got` IS the Claude verdict at decide() time; the harness passed, so got == exp
    for every row. A row where they differ means the dump is not what the harness judged."""
    rows, _ = conformance_dump
    bad = [(r["label"], r["exp"], r["got"]) for r in rows if r["exp"] != r["got"]]
    assert not bad, bad


def test_every_replayable_case_agrees(conformance_dump):
    rows, _ = conformance_dump
    todo = [r for r in rows if xc.classify(r) in CODEX["cases"]]
    results = xc.replay(todo, CODEX, None, REPO, workers=WORKERS)
    assert len(results) == len(todo) >= 80
    checked = sum(r["reference"] not in ("inert", "error", "unreadable") for r in results)
    assert checked >= 80, f"only {checked} cases evaluated game policy"
    drift = [(res["label"], row["got"], res["reference"]) for row, res in zip(todo, results)
             if res["reference"] != row["got"]
             and not (res["reference"] == "inert" and row["got"] in ("ask", "deny"))]   # inert = normalised ask/deny
    diffs = [(r["label"], r["kind"], r["reference"], r["adapter"], r["text"]) for r in results
             if r["reference"] != r["adapter"]]
    print(f"replayed {len(results)}; replay drift vs the live harness verdict: {len(drift)}")
    assert not diffs, "\n".join(map(str, diffs))     # PER ITEM, never a total
    # R7-3: the guards re-run NOW must still say what the harness recorded at decide() time; a
    # drift means the reference verdict is not the one the harness judged (it was only printed).
    assert not drift, "\n".join(f"DRIFT {d}" for d in drift)


# ------------------------------------------------------------------ extras ------------ #

def _extra_cases():
    return YAML(typ="safe").load(EXTRAS.read_text(encoding="utf-8"))


@pytest.fixture
def sandbox():
    """Under <repo>/.test-sandbox, never tmp_path: on Linux that is /tmp/pytest-of-*, where the
    /tmp measurement-output rule denies a write for its own reason (ubuntu CI run 39269462240),
    so an allow-expecting row would test that rule instead of the workdir contract."""
    import tempfile
    base = REPO / ".test-sandbox"
    base.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="codex-conf-", dir=base))
    try:
        yield make_sandbox(run)
    finally:
        # Same-run scratch this fixture created; nothing else is ever removed here.
        shutil.rmtree(run, ignore_errors=True)


def _fill(s: str, tmp: Path, tk: Path) -> str:
    return (s.replace("{TK}", str(tk)).replace("{TKP}", tk.as_posix()).replace("{TMP}", str(tmp))
             .replace("{TKMSYS}", "/" + tk.as_posix()[0].lower() + tk.as_posix()[2:]))


def extra_claude(case, tmp, tk, env) -> str:
    worst = "allow"
    for call in case["claude"]:
        if "path" in call:
            payload = {"tool_name": call.get("tool", "Write"),
                       "tool_input": {"file_path": _fill(call["path"], tmp, tk), "content": ""}}
            hook = "protect-files.sh"
        else:
            # `cwd` as Claude Code sends it: the same directory the Codex payload carries (lane F).
            payload = {"tool_name": call.get("tool", "Bash"), "tool_input": {"command": _fill(call["command"], tmp, tk)},
                       "cwd": _fill(case.get("cwd", "{TK}"), tmp, tk)}
            hook = "protect-bash.sh"
        d = xc.reference_verdict({"hook": hook, "payload": payload, "env": env, "cwd": str(tk)}, REPO)
        worst = max(worst, d, key=RANK.get)
    return worst


def extra_codex(case, tmp, tk, env) -> str:
    cwd = _fill(case.get("cwd", "{TK}"), tmp, tk)
    payload = native(case["fixture"], cwd, command=_fill(case["command"], tmp, tk))
    # Synthetic policy-parity context only. Do not add this in native(), whose
    # fixture-derived envelopes also exercise the observed missing-workdir contract.
    payload["tool_input"]["workdir"] = cwd
    kind = "shell-powershell" if case.get("shell", "bash") == "powershell" else "shell-bash"
    run_dir = tmp / "adapter-cwd"
    run_dir.mkdir(exist_ok=True)
    row = {"native": payload, "kind": kind, "env": env, "cwd": cwd, "label": case["id"]}
    return xc.adapter_verdict(row, CODEX, None, REPO, run_dir)[0]


@pytest.mark.parametrize("case", _extra_cases(), ids=lambda c: c["id"])
def test_codex_specific_extras(case, sandbox):
    if "{TKMSYS}" in str(case) and os.name != "nt":
        # MEASURED on ubuntu CI (run 37091872873): off Windows there is no drive letter, so the
        # Git Bash /c/... form of a path does not exist and this case judges a path that is not
        # the toolkit at all (both sides said allow). The dialect is Windows-only, so is the case.
        pytest.skip("the Git Bash /c/... drive dialect exists only on Windows -- NOT checked here")
    tmp, tk, env = sandbox
    for f in case.get("files", []):
        p = Path(_fill(f, tmp, tk))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    x = extra_codex(case, tmp, tk, env)
    c = extra_claude(case, tmp, tk, env)
    assert x == c, (case["id"], "codex", x, "claude", c)
    if "expect" in case:
        assert x == case["expect"], (case["id"], "expected", case["expect"], "both said", x)


@pytest.mark.parametrize("shell,command", [
    ("bash", "printf x > wares.xml"),
    ("powershell", "Set-Content -LiteralPath wares.xml -Value x"),
])
def test_supplied_execution_workdir_wins_over_both_unrelated_directories(sandbox, shell, command):
    """Actual wrapper/adapter/guards, synthetic cwd contract; never execute mutations."""
    tmp, toolkit, env = sandbox
    process_cwd = tmp / "neutral-wrapper-process"
    session_cwd = tmp / "neutral-session"
    process_cwd.mkdir()
    session_cwd.mkdir()
    reference = toolkit / "reference/libraries"
    reference_before = (reference / "wares.xml").read_bytes()
    assert reference_before in (b"ref\n", b"ref\r\n")
    development = toolkit / "dev/mymod"
    (development / "wares.xml").write_bytes(b"owned development fixture\n")
    kind = "shell-powershell" if shell == "powershell" else "shell-bash"
    for directory, expected in ((reference, "deny"), (development, "allow")):
        assert len({process_cwd.resolve(), session_cwd.resolve(), directory.resolve()}) == 3
        payload = native("bash_powershell", session_cwd, command=command, workdir=str(directory))
        row = {"label": "owned-distinct-context", "kind": kind, "native": payload,
               "cwd": str(session_cwd), "env": env}
        verdict, reason = xc.adapter_verdict(row, CODEX, None, REPO, process_cwd)
        assert verdict == expected, (directory, verdict, reason)
        if expected == "deny":
            assert "reference" in reason.casefold() and "INERT" not in reason
    # The unchanged fixture-derived envelope cannot establish this context.
    payload = native("bash_powershell", session_cwd, command=command)
    row = {"label": "owned-missing-context", "kind": kind, "native": payload,
           "cwd": str(session_cwd), "env": env}
    assert xc.adapter_verdict(row, CODEX, None, REPO, process_cwd) == ("inert", UNKNOWN_EXECUTION_CWD)
    assert (reference / "wares.xml").read_bytes() == reference_before
    assert (development / "wares.xml").read_bytes() == b"owned development fixture\n"

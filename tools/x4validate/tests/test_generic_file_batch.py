"""End-to-end Action v1 checks against a freshly generated, isolated candidate."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _layout import installed_layout, require_repo
from codex_testlib import REPO, assert_policy_refusal


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "candidate"
    if installed_layout() and not (REPO / "agent").is_dir():
        # D-C2: an install has no `agent/` for the generator to read, but it ships the files the
        # generator renders from it (tools/agent-harness.py, tools/x4_harness/), and those are the
        # ones a user's agent runs. In a checkout they are generated afresh, as before.
        require_repo("tools/agent-harness.py", "tools/x4_harness", why="the generic harness")
        (root / "tools").mkdir(parents=True)
        shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
        shutil.copytree(REPO / "tools/x4_harness", root / "tools/x4_harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
    else:
        spec = importlib.util.spec_from_file_location(
            "batch_candidate_generator", REPO / "tools/x4validate/scripts/gen-agent-trees.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        for relative, content in generator.generate(REPO).items():
            if relative == "tools/agent-harness.py" or relative.startswith("tools/x4_harness/"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
    shutil.copytree(REPO / "tools/modding_harness", root / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/modding-harness.lock.json", root / "tools/modding-harness.lock.json")
    config = root / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    for name, value in {
        "X4_TOOLKIT": root, "X4_GAME": tmp_path / "game", "X4_MODS": root / "dev",
        "X4_REFERENCE": tmp_path / "reference", "X4_PROFILE": tmp_path / "profile",
        "X4_SAVES": tmp_path / "profile/save", "X4_DOCUMENTS": tmp_path / "Documents",
        "X4_EXTENSIONS": tmp_path / "game/extensions", "X4_CONFIG": tmp_path / "no-config",
    }.items():
        env[name] = str(value)
    env["X4_HOUSEKEEP_SWEEP"] = "off"
    def check(paths, operations=None):
        action = {"api_version": 1, "host": "test-candidate", "tool": "copy",
                  "surface": "files", "cwd": str(root), "session_id": "candidate",
                  "tool_call_id": "batch", "paths": [str(p) for p in paths],
                  "operations": operations or ["write"] * len(paths)}
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), "check"],
                                input=json.dumps(action).encode(), env=env, capture_output=True,
                                timeout=49, cwd=root)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout), time.monotonic() - started
    return root, env, check


def test_complete_128_path_action(candidate):
    root, env, check = candidate
    paths = [root / f"dev/ror/assets/ships/ship-{i}/macros/ship.xml" for i in range(128)]
    reply, elapsed = check(paths)
    assert reply["result"]["decision"] == "allow" and not reply["inert"], reply


@pytest.mark.parametrize("forbidden", [None, 0, 1794, 3588])
def test_complete_3589_path_action(candidate, forbidden):
    root, env, check = candidate
    paths = [root / f"dev/ror/assets/ships/ship-{i}/macros/ship.xml" for i in range(3589)]
    if forbidden is not None:
        paths[forbidden] = Path(env["X4_REFERENCE"]) / "forbidden.xml"
    reply, elapsed = check(paths)
    assert elapsed < 40, (elapsed, reply)
    assert reply["result"]["evaluation"] == "evaluated" and not reply["inert"], reply
    assert reply["result"]["decision"] == ("allow" if forbidden is None else "deny"), reply
    if forbidden is not None:                                           # M10: the policy's deny, not a failure
        assert_policy_refusal("deny", reply["result"]["reason"], "reference/ is read-only")
    assert reply["result"]["preservation"] == "not_required" and not reply["transactions"]
    assert not (root / "dev").exists(), "a guard check must not execute declared writes"


def test_one_two_save_preservation_and_bulk_refusal(candidate):
    root, env, check = candidate
    saves = Path(env["X4_SAVES"])
    saves.mkdir(parents=True)
    members = [saves / f"test-{i}.xml" for i in range(3)]
    for i, member in enumerate(members):
        member.write_bytes(f"fixture save {i}".encode())
    one, _ = check(members[:1])
    two, _ = check(members[:2])
    for reply in (one, two):
        assert reply["result"]["decision"] == "allow", reply
        assert reply["result"]["preservation"] == "verified" and reply["transactions"], reply
    three, _ = check(members)
    assert three["result"]["decision"] in ("ask", "deny") and not three["transactions"], three
    # M10: the refusal must be the POLICY's (3 saves need a typed human approval), not an engine
    # failure that fails closed and so also reads "deny"
    assert three["result"]["evaluation"] == "evaluated" and not three["inert"], three
    assert_policy_refusal(three["result"]["decision"], three["result"]["reason"], "human approval required")
    assert [p.read_bytes() for p in members] == [f"fixture save {i}".encode() for i in range(3)]


def test_mixed_actions_keep_existing_policy(candidate):
    root, env, check = candidate
    allowed = root / "dev/one.xml"
    forbidden = Path(env["X4_REFERENCE"]) / "forbidden.xml"
    reply, _ = check([allowed, forbidden], ["write", "delete"])
    assert reply["result"]["decision"] == "deny" and not reply["inert"], reply
    assert reply["result"]["evaluation"] == "evaluated", reply          # M10: judged, not failed closed
    assert_policy_refusal(reply["result"]["decision"], reply["result"]["reason"], "reference/ is read-only")


def test_a_refusal_check_goes_red_on_an_engine_failure_and_on_a_missing_reason():
    """M10 twin: the helper the deny-expecting tests lean on can fail. A deny wearing an engine
    failure's reason, a deny with the wrong reason, and a non-refusal are each rejected."""
    assert_policy_refusal("deny", "BLOCKED: reference/ is read-only", "read-only")
    for decision, reason in (("deny", "X4 GUARD INERT: could not check read-only"),
                             ("deny", "something unrelated"), ("allow", "reference/ is read-only")):
        with pytest.raises(AssertionError):
            assert_policy_refusal(decision, reason, "read-only")
    with pytest.raises(AssertionError):
        assert_policy_refusal("deny", "anything")                      # no needle named: refuses to be vacuous

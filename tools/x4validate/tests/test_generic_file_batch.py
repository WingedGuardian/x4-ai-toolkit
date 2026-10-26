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

from codex_testlib import REPO


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "candidate"
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
    assert [p.read_bytes() for p in members] == [f"fixture save {i}".encode() for i in range(3)]


def test_mixed_actions_keep_existing_policy(candidate):
    root, env, check = candidate
    allowed = root / "dev/one.xml"
    forbidden = Path(env["X4_REFERENCE"]) / "forbidden.xml"
    reply, _ = check([allowed, forbidden], ["write", "delete"])
    assert reply["result"]["decision"] == "deny" and not reply["inert"], reply

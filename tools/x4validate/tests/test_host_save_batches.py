"""Actual rendered adapters, preserving one request as a single authorization scope."""
import importlib.util
import json
import sys
from pathlib import Path

from codex_testlib import REPO, make_sandbox, native, run_adapter
from test_opencode_adapter import call as open_call, run as open_run


def prepare(tmp_path):
    tmp, root, env = make_sandbox(tmp_path)
    cfg = root / ".modding-agent-harness/config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"save_reserve_bytes": 0}))
    files = [Path(env["X4_SAVES"]) / (str(i) + ".xml.gz") for i in range(3)]
    for file in files:
        file.write_bytes(file.name.encode())
    return tmp, root, env, files


def patch(files):
    return "*** Begin Patch\n" + "".join("*** Update File: " + file.as_posix() +
        "\n@@\n-old\n+new\n" for file in files) + "*** End Patch"


def transactions(root):
    return list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))


def test_codex_batches_two_preserves_then_three_refuses_and_typed_grant_succeeds(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = native("apply_patch_multi", root, command=patch(files[:2]))
    assert run_adapter(env, payload)[0] in ("allow", "advise")
    records = transactions(root)
    assert len(records) == 1 and len(json.loads(records[0].read_text())["groups"]) == 2
    payload["tool_input"]["command"] = patch(files)
    assert run_adapter(env, payload)[0] == "deny"
    assert len(transactions(root)) == 1
    # Fixture provenance matches the qualified reader contract. This does not
    # claim a current running Codex TUI was intercepted.
    spec = importlib.util.spec_from_file_location("host_save_profile", REPO / "agent/guards/claude-hooks/save_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    line = policy.core(root, "approval").example("human-session", "write", files)
    transcript = tmp / "human.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"originator": "codex-tui", "id": "human-session"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": line}]}}}]))
    payload.update(session_id="human-session", transcript_path=str(transcript))
    assert run_adapter(env, payload)[0] in ("allow", "advise")
    assert len(transactions(root)) == 2
    assert all(file.read_bytes() == file.name.encode() for file in files)


def test_opencode_single_save_preserves_and_bulk_has_no_invented_human_reader(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = open_call(root, "edit", {"filePath": str(files[0]), "oldString": "old", "newString": "new"})
    result = open_run(env, payload)
    assert result["decision"] in ("allow", "advise"), result
    assert len(transactions(root)) == 1
    payload = open_call(root, "apply_patch", {"patchText": patch(files)})
    result = open_run(env, payload)
    assert result["decision"] == "deny" and "SAVE_ACCESS_APPROVAL" in result["reason"], result
    assert len(transactions(root)) == 1


def test_a_backend_denial_never_preserves_or_gets_overruled(tmp_path):
    tmp, root, env, files = prepare(tmp_path)
    payload = native("apply_patch_multi", root, command=patch([files[0], root / "reference/libraries/wares.xml"]))
    assert run_adapter(env, payload)[0] == "deny"
    assert not transactions(root)

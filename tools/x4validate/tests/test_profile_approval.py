"""Exact grants; fixtures are synthetic and never evidence of host enforcement."""
import importlib.util
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from codex_testlib import REPO, make_sandbox, native, run_adapter

SOURCE = REPO / "agent/guards/claude-hooks"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(REPO / "agent/guards/adapters"))
spec = importlib.util.spec_from_file_location("profile_approval_test", SOURCE / "profile_approval.py")
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)


@pytest.fixture
def case(tmp_path, monkeypatch):
    _, tk, env = make_sandbox(tmp_path)
    path = tmp_path / "profile/content.xml"
    path.write_text('<content enabled="false"/>\n', encoding="utf-8")
    patch = f'*** Begin Patch\n*** Update File: {path}\n@@\n-<content enabled="false"/>\n+<content enabled="true"/>\n*** End Patch'
    transcript = tmp_path / "native.jsonl"
    payload = {"tool_name": "apply_patch", "tool_input": {"command": patch},
               "session_id": "session-one", "transcript_path": str(transcript)}
    calls = [("write", None, None, str(path), f"update {path}")]
    verdict = {"decision": "ask", "inert": False,
               "reason": f"EDITING USER PROFILE FILE: {path} — changes affect live game config/saves. Confirm? [update {path}]"}
    roots = {"toolkit": str(tk), "profile": str(path.parent), "saves": str(path.parent / "save")}
    monkeypatch.setattr(pa.save_policy, "read_roots", lambda deadline: roots)
    scope = pa.request(payload, calls, verdict, roots)
    return payload, calls, verdict, roots, scope, transcript, env


def transcript_for(case, text, *, origin="codex-tui", item="UserMessage", thread="session-one", meta=False, identity="human-one", age=0):
    transcript = case[5]
    rows = [{"type": "session_meta", "payload": {"id": "session-one", "originator": origin}},
            {"type": "event_msg", "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
             "isCompactSummary": meta, "payload": {"thread_id": thread,
             "type": "item_completed", "item": {"type": item, "id": identity, "content": [{"type": "text", "text": text}]}}}]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def prepare(case):
    return pa.prepare(*case[:3], time.monotonic() + 10)


def approve(case):
    transcript_for(case, pa.KEY + " " + pa.token(case[4]))


def test_no_grant_produces_reviewable_request(case):
    transcript_for(case, "yes")
    verdict, pending = prepare(case)
    assert verdict["decision"] == "ask" and pending is None
    assert pa.KEY + " " + pa.token(case[4]) in verdict["reason"]
    assert case[0]["tool_input"]["command"] in verdict["reason"]


def test_exact_grant_once(case):
    approve(case)
    verdict, pending = prepare(case)
    assert verdict["decision"] == "allow" and pending
    assert pa.consume(pending) is None
    assert pa.consume(pending)["decision"] == "deny"
    assert prepare(case)[0]["decision"] == "ask"


@pytest.mark.parametrize("dimension", ["session_id", "path", "before_sha256", "patch_sha256", "operation"])
def test_scope_drift_changes_token(case, dimension):
    scope = case[4]
    assert pa.token({**scope, dimension: "different"}) != pa.token(scope)


@pytest.mark.parametrize("kwargs", [{"origin": "exec"}, {"item": "AgentMessage"},
                                    {"thread": "foreign"}, {"meta": True},
                                    {"identity": ""}, {"age": 86401}, {"age": -60}])
def test_unqualified_provenance_never_grants(case, kwargs):
    transcript_for(case, pa.KEY + " " + pa.token(case[4]), **kwargs)
    assert prepare(case)[1] is None


def test_async_native_user_reply(case):
    text = '<send_user_message_question_reply>\n' + json.dumps([{
        "answer": "Approve this exact edit once", "question": "Review\n" + pa.KEY + " " + pa.token(case[4])}]) + '\n</send_user_message_question_reply>'
    transcript_for(case, text)
    assert prepare(case)[1]
    transcript_for(case, text, item="ToolMessage")
    assert prepare(case)[1] is None


def test_fresh_human_reply_can_authorize_retry(case):
    approve(case)
    assert pa.consume(prepare(case)[1]) is None
    assert prepare(case)[1] is None
    transcript_for(case, pa.KEY + " " + pa.token(case[4]), identity="human-two")
    pending = prepare(case)[1]
    assert pending and pa.consume(pending) is None


def test_changed_bytes_before_and_after_prepare_refuse(case):
    approve(case)
    pending = prepare(case)[1]
    Path(case[4]["path"]).write_text("changed")
    assert prepare(case)[1] is None
    assert pa.consume(pending)["decision"] == "deny"
    assert not pending[1].exists()


def test_changed_patch_refuses(case):
    approve(case)
    case[0]["tool_input"]["command"] += "\n"
    assert prepare(case)[1] is None


def test_concurrent_consumption_has_one_winner(case):
    approve(case)
    pending = prepare(case)[1]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(pa.consume, [pending, pending]))
    assert sum(result is None for result in results) == 1


@pytest.mark.parametrize("change", ["deny", "inert", "other-ask", "shell", "multi", "save"])
def test_never_overrides_other_policies(case, change):
    payload, calls, verdict, roots = case[:4]
    approve(case)
    if change == "deny": verdict["decision"] = "deny"
    if change == "inert": verdict["inert"] = True
    if change == "other-ask": verdict["reason"] = "A different policy requires confirmation"
    if change == "shell": payload["tool_name"] = "Bash"
    if change == "multi": calls.append(calls[0])
    if change == "save": roots["saves"] = roots["profile"]
    assert prepare(case)[1] is None


def test_receipt_failure_refuses(case):
    approve(case)
    pending = prepare(case)[1]
    pending[1].parent.parent.mkdir(parents=True, exist_ok=True)
    pending[1].parent.write_text("not a directory")
    assert pa.consume(pending)["decision"] == "deny"


def test_rendered_adapter_real_guard_path(case, tmp_path):
    payload, _calls, _verdict, _roots, _scope, transcript, env = case
    approve(case)
    p = native("apply_patch_update", tmp_path, command=payload["tool_input"]["command"])
    p.update(session_id="session-one", transcript_path=str(transcript))
    # Actual rendered adapter -> actual shell guard -> transcript reader -> backup.
    decision, reason = run_adapter(env, p)
    assert decision == "allow", reason
    decision, reason = run_adapter(env, p)
    # The test transport labels an ASK_PREFIX native deny as semantic 'ask'.
    assert decision == "ask" and "consumed" in reason


def test_backup_failure_never_consumes(case, monkeypatch):
    spec = importlib.util.spec_from_file_location("profile_test_adapter", REPO / "agent/guards/adapters/codex.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    approve(case)
    monkeypatch.setattr(adapter, "profile_approval", pa)
    monkeypatch.setattr(adapter, "translate", lambda *a, **kw: case[1])
    monkeypatch.setattr(adapter.save_policy, "batch", lambda *a, **kw: case[2])
    monkeypatch.setattr(adapter, "run_backups", lambda *a: {"decision": "ask", "reason": "backup failed"})
    pending = prepare(case)[1]
    result = adapter.pre_tool_use(case[0], "powershell", time.monotonic() + 10)
    assert result["decision"] == "ask" and not pending[1].exists()

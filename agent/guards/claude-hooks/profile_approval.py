"""One-use Codex grants for a single existing, non-save profile XML edit.

Only the native hook's transcript is evidence. No CLI grant flag, agent-authored
receipt or generic yes qualifies. This is an evidence protocol, not an OS security
boundary against a process that can forge the host's transcript/state files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import patch_paths
import save_policy

KEY = "PROFILE_EDIT_APPROVAL"
MAX_FILE = 16 * 1024 * 1024
MAX_TRANSCRIPT = 512 * 1024 * 1024
MAX_AGE = 24 * 60 * 60


def canonical(path):
    return os.path.normcase(str(Path(path).resolve(strict=True)))


def fingerprint(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("profile file exceeds the approval byte budget")
    return hashlib.sha256(data).hexdigest()


def contains(parent, child):
    return child == parent or parent in child.parents


def request(payload, calls, verdict, roots):
    """Qualify exactly the terminal profile ask, never another ask or hard deny."""
    if (verdict.get("decision") != "ask" or verdict.get("inert")
            or payload.get("tool_name") != "apply_patch" or len(calls) != 1):
        return None
    ti = payload.get("tool_input", {})
    patch = ti.get("command") if isinstance(ti.get("command"), str) else ti.get("input")
    if not isinstance(patch, str) or len(patch) > 8192:
        return None
    ops = patch_paths.parse_patch(patch)
    if len(ops) != 1 or ops[0][0] != "update":
        return None
    kind, _shell, _cmd, target, label, *_extra = calls[0]
    if kind != "write" or not target:
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 256:
        return None
    path = Path(target).resolve(strict=True)
    profile = Path(roots["profile"]).resolve(strict=True)
    if (not path.is_file() or path.suffix.lower() != ".xml"
            or not contains(profile, path) or path == profile):
        return None
    if any(contains(folder.resolve(), path) for folder in save_policy.folders(roots)):
        return None
    # Match the sole guard's exact terminal rule. Other refusals are never lifted.
    reason = f"EDITING USER PROFILE FILE: {target} — changes affect live game config/saves. Confirm?"
    if verdict.get("reason") not in (reason, f"{reason} [{label}]"):
        return None
    return {"v": 1, "session_id": session, "operation": "update",
            "path": canonical(path), "before_sha256": fingerprint(path),
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()}


def token(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def human_granted(transcript, session, digest, root, deadline):
    """Reuse the qualified native-host reader; summaries/tool output grant nothing."""
    if not transcript or Path(transcript).stat().st_size > MAX_TRANSCRIPT:
        return False
    reader = save_policy.core(root, "approval")
    wanted = f"{KEY} {digest}"
    evidence = {}
    with open(transcript, "rb") as stream:
        def bounded_lines():
            for raw in stream:
                if time.monotonic() >= deadline:
                    raise TimeoutError("profile approval reader deadline expired")
                # The shared reader already verifies session metadata; also exclude
                # explicit foreign-thread events in otherwise valid transcripts.
                row = json.loads(raw)
                if isinstance(row, dict) and isinstance(row.get("payload"), dict):
                    if row["payload"].get("thread_id", session) != session:
                        continue
                evidence.clear()
                evidence.update(row if isinstance(row, dict) else {})
                yield raw
        for text in reader.human_texts(bounded_lines(), "codex", session):
            item = evidence.get("payload", {}).get("item", {})
            identity = item.get("id")
            if not isinstance(identity, str) or not identity:
                continue
            try:
                stamp = datetime.fromisoformat(evidence["timestamp"].replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
                if not 0 <= age <= MAX_AGE:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            receipt = receipt_for(root, digest, identity)
            if receipt.exists():
                continue
            if text.strip() == wanted:
                return identity, receipt
            # Async selections arrive as native UserMessage events, not tool output.
            match = re.fullmatch(r"<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>", text.strip(), re.S)
            if match:
                replies = json.loads(match[1])
                if isinstance(replies, list):
                    for reply in replies:
                        if (isinstance(reply, dict) and reply.get("answer") == "Approve this exact edit once"
                                and wanted in str(reply.get("question", "")).splitlines()):
                            return identity, receipt
    return False


def receipt_for(root, digest, identity):
    key = hashlib.sha256((digest + "\0" + identity).encode()).hexdigest()
    return Path(root) / ".claude" / "profile-approvals" / (key + ".used.json")


def prepare(payload, calls, verdict, deadline):
    """Return (verdict, pending consumption). Errors retain the original refusal."""
    if verdict.get("decision") != "ask" or verdict.get("inert"):
        return verdict, None
    try:
        roots = save_policy.read_roots(deadline)
        scope = request(payload, calls, verdict, roots)
        if scope is None:
            return verdict, None
        digest = token(scope)
        root = Path(roots["toolkit"])
        granted = human_granted(payload.get("transcript_path", ""), scope["session_id"], digest, root, deadline)
        if not granted:
            return {**verdict, "reason": (f"Approve this exact profile update once: {scope['path']}\n"
                f"Before SHA256: {scope['before_sha256']}\nPatch SHA256: {scope['patch_sha256']}\n"
                f"{KEY} {digest}\nReview the patch below, then reply with the approval line above. "
                "A fresh human grant is required (24-hour expiry; consumed grants cannot be replayed). "
                "A generic yes cannot bind this edit.\n" + (payload["tool_input"].get("command")
                    if isinstance(payload["tool_input"].get("command"), str)
                    else payload["tool_input"]["input"]))}, None
        identity, receipt = granted
        return {"decision": "allow", "inert": False, "reason": None, "context": None}, ({**scope, "human_message_id": identity}, receipt)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        return {**verdict, "reason": f"Profile approval could not be verified ({type(exc).__name__}); edit remains blocked."}, None


def consume(pending):
    """Call only after every guard and backup succeeds; reserve before tool execution.

    A failed tool/host crash burns its grant deliberately. Exclusive creation makes
    concurrent attempts fail closed. It does not lock the target against foreign
    writers between this hook and the actual tool; that host-level TOCTOU remains.
    """
    scope, receipt = pending
    try:
        if canonical(scope["path"]) != scope["path"] or fingerprint(scope["path"]) != scope["before_sha256"]:
            raise ValueError("profile changed after approval")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump({"scope": scope, "consumed_at": time.time()}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError) as exc:
        return {"decision": "deny", "inert": False, "context": None,
                "reason": f"Profile approval was not consumed safely ({type(exc).__name__}); edit remains blocked."}
    return None

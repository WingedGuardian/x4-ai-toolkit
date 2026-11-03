"""Session charter and ledger through the pinned shared core.

`prompt` (UserPromptSubmit) records a session's first prompt as its origin and prints the ledger's per-prompt
inventory; `session-start` (SessionStart, every source including `compact`) prints the charter and the open ledger.
Never fails the session.

Cost: `prompt` runs before every message. When the charter already exists and the session has no ledger, there is
nothing to record or print, so it returns before loading anything (the shared core import is most of the cost).
A hook-only game-root deployment keeps tools/ in its configured external toolkit (X4_TOOLKIT), as save_policy._batch
does: the core is loaded from there, while the charter stays in the hook's own project.
"""
from pathlib import Path
import json
import os
import re
import sys

sys.dont_write_bytecode = True

_SESSION = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")


def _nothing_to_do(workspace, raw):
    try:
        sid = json.loads(raw.decode("utf-8-sig") or "{}").get("session_id")
    except Exception:                         # noqa: BLE001 -- let the core report it
        return False
    if not (isinstance(sid, str) and _SESSION.fullmatch(sid)):
        return False
    folder = Path(workspace) / ".agent-sessions"
    return (folder / (sid + ".md")).is_file() and not (folder / (sid + ".ledger.jsonl")).exists()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else "session-start"
    workspace = Path(__file__).resolve().parents[2]
    raw = sys.stdin.buffer.read(1 << 20)
    if mode == "prompt" and _nothing_to_do(workspace, raw):
        return 0
    try:
        import io
        import save_policy
        core_root = workspace if (workspace / "tools/modding_harness").is_dir() else Path(
            os.environ.get("X4_TOOLKIT") or workspace)
        core = save_policy.core(core_root, "charter")
        sys.stdin = io.TextIOWrapper(io.BytesIO(raw))
        return core.main([mode, "--workspace", str(workspace)])
    except Exception:                         # noqa: BLE001 -- a missing or broken core must not fail the session
        if mode != "prompt":
            sys.stdout.buffer.write(b"SESSION CHARTER: NOT INJECTED -- the pinned shared core could not be loaded.\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

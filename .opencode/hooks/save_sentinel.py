"""X4 PostToolUse save sentinel: a save changed with no backup first, whatever changed it.

The pre-call save guard (save_policy.py) judges what a call NAMES. A save changed by code that builds its path at
run time is invisible to it. This hook closes that gap after the fact: it hands the save folders to the pinned
harness's sentinel (modding_harness/sentinel.py), which compares them with the previous look and reports every
.xml.gz/.xml save modified or removed without a committed backup in the permanent store since then. It lists and
stats only and never blocks (a post-call hook cannot undo the call).

Watched: save_policy.folders(roots) (the configured saves folder and every profile account's save folder), the
same under Documents as Windows reports it (a OneDrive- or policy-redirected Documents the environment-based
discovery in _x4-env.sh does not see), and any folder under the toolkit marked with a `.protect-saves` file.
While X4 runs it may be the writer, so the report then goes to the agent only.

Input: the roots packet then the hook payload (the same stdin save_policy.py reads). Output: a PostToolUse JSON
answer, or nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import save_policy  # noqa: E402

MARKER = ".protect-saves"
MARKED_DEPTH = 4
MARKED_REFRESH = 3600
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}
GAMES = ("X4.exe",)
EXTENSIONS = (".xml.gz", ".xml")
ELEVATED_REPEAT = 12 * 3600   # seconds between "this agent runs elevated" warnings


def _known_documents():
    if os.name != "nt" or os.environ.get("X4_NO_KNOWN_FOLDERS") == "1":
        return []
    try:
        import ctypes
        import uuid
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return []
        try:
            return [Path(out.value)]
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError):
        return []


def save_folders(roots):
    found = list(save_policy.folders(roots))
    for docs in _known_documents():
        profile = docs / "Egosoft" / "X4"
        if profile.is_dir():
            found.append(profile / "save")
            found.extend(p / "save" for p in profile.iterdir() if p.is_dir())
    unique = {}
    for f in found:
        if Path(f).is_dir():
            unique.setdefault(os.path.normcase(os.path.abspath(f)), Path(f))
    return tuple(unique.values())


def marked_folders(root, depth=MARKED_DEPTH):
    found, stack = [], [(str(root), 0)]
    while stack:
        folder, level = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            continue
        if any(e.name == MARKER and e.is_file() for e in entries):
            found.append(folder)
            continue
        if level < depth:
            stack.extend((e.path, level + 1) for e in entries
                         if e.name not in SKIP and e.is_dir(follow_symlinks=False))
    return sorted(found)


def game_running():
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any('"' + g.lower() + '"' in out for g in GAMES)


class _NoStore:
    root = Path(os.devnull)


def report(roots, now=None):
    """(text, loud): what to tell the agent, and whether the user should see it too."""
    now = time.time() if now is None else now
    root = save_policy.native(roots["toolkit"])
    sentinel = save_policy.core(root, "sentinel")
    profiles = save_policy.core(root, "profiles")
    workspace = save_policy.core(root, "workspace")
    state_path = Path(root) / ".modding-agent-harness" / "save-sentinel.json"
    state = sentinel.load_state(state_path)
    if state.get("marked") is None or now - state.get("marked_time", 0) > MARKED_REFRESH:
        state["marked"] = marked_folders(root)
        state["marked_time"] = now
    profile = profiles.GameProfile("x4", save_folders(roots), EXTENSIONS)
    try:
        store = workspace.save_store(root, profile)
    except (OSError, ValueError):
        store = _NoStore()          # no readable store: every change is reported, none is "covered"
    before = dict(state.get("files") or {})
    result = sentinel.check(profile, store, state, watch=state["marked"], now=now)
    notes, lock_loud = lock_notes(root, state, before, profile, now)
    sentinel.save_state(state_path, state)
    text = sentinel.describe(result)
    loud = False
    if result.unbacked:
        running = game_running()
        text += ("\nX4 is running, so it may be the game itself saving." if running else
                 "\nThe save guard did not back these up. If a command or script you ran did this, tell the user "
                 "now; a removed file may be in the Recycle Bin.")
        loud = not running
    return "\n".join(t for t in [text] + notes if t), loud or lock_loud


def lock_switch(root):
    """The optional save-lock switch (modding_harness/save_lock.py). It lives in the neutral harness state, which
    save_policy already refuses to let an agent change: only the user switches locking off."""
    return Path(root) / ".modding-agent-harness" / "save-lock.json"


def lock_notes(root, state, before, profile, now):
    """(notes, raise to the user?): lock new or changed saves once settled while the switch is on, and say so
    loudly when locking was switched off or the agent runs elevated (the locks do not hold it)."""
    save_lock = save_policy.core(root, "save_lock")
    enabled = bool(save_lock.read_config(lock_switch(root)).get("enabled"))
    notes, loud = [], False
    if state.get("lock_enabled") and not enabled:
        notes.append("SAVE LOCK: save locking was switched OFF ({} changed or removed). If the user did not do this, "
                     "an agent or script did; it is switched back on with "
                     "`python tools/agent-harness.py save-lock on`.".format(lock_switch(root)))
        loud = True
    state["lock_enabled"] = enabled
    if not enabled or not save_lock.supported():
        return notes, loud
    files = state.get("files") or {}
    folders = list(dict.fromkeys([str(r) for r in profile.save_roots]
                                 + [os.path.dirname(p) for _, _, p in files.values()]))
    try:
        failed = save_lock.auto_lock(state, before, files, folders, now=now, extensions=profile.extensions)
    except (OSError, subprocess.SubprocessError, save_lock.Unsupported) as exc:
        failed = ["(locking unavailable: {})".format(exc)]
    if failed:
        notes.append("SAVE LOCK: could not lock {} save file(s): {}".format(len(failed), ", ".join(failed[:5])))
    if save_lock.is_elevated():
        if "elevated_warned" not in state or now - state["elevated_warned"] > ELEVATED_REPEAT:
            state["elevated_warned"] = now
            notes.append("SAVE LOCK: this agent session runs as administrator, so the save locks do NOT protect the "
                         "saves from it. Restart the agent from a normal (non-admin) terminal.")
            loud = True
    else:
        state.pop("elevated_warned", None)
    return notes, loud


def render(text, loud):
    if not text:
        return ""
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    if loud:
        out["systemMessage"] = text
    return json.dumps(out)


def main():
    try:
        roots, _payload = save_policy.packet(sys.stdin.buffer.read().decode("utf-8"))
        answer = render(*report(roots))
    except Exception as e:          # advisory: disclose that nothing was checked, never fail the call
        answer = render("SAVE SENTINEL DID NOT RUN ({}): saves were not checked after this call."
                        .format(type(e).__name__), False)
    if answer:
        sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())

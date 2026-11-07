"""X4 PostToolUse save sentinel: a save changed with no backup first, whatever changed it.

The pre-call save guard (save_policy.py) judges what a call NAMES. A save changed by code that builds its path at
run time is invisible to it. This hook closes that gap after the fact: it hands the save folders to the pinned
harness's sentinel (modding_harness/sentinel.py), which compares them with the previous look and reports every
.xml.gz/.xml save modified or removed without a committed backup in the permanent store since then. It lists and
stats only and never blocks (a post-call hook cannot undo the call).

Watched: save_policy.folders(roots) (the configured saves folder and every profile account's save folder), the
same under Documents as Windows reports it (a OneDrive- or policy-redirected Documents the environment-based
discovery in _x4-env.sh does not see), and any folder under the toolkit marked with a `.protect-saves` file.

Who wrote it. A change dated inside a game session goes to the agent only; any other raises the user too. A session is
known two ways: X4 is running at this look, or a look since the last one shows the game's exit. An exit takes all of:
    - <profile>/uidata.xml moved since the previous look (X4 truncates it at launch and rewrites it at exit; its stamp
      ends the window);
    - another file of the exit set (pipelinecache.bin, config.xml, userdata.xml, debug.txt) stamped within EXIT_SPREAD
      seconds of it, so touching uidata.xml alone is no session;
    - the profile not rewritten as a whole: fewer than REWRITTEN_FLOOR, or under half, of its other top-level files moved.
And however the session is known, more than MOST_A_SESSION_CHANGES changes to one save folder in one look are not the
game's. A change is judged by its own timestamp (a removal by its folder's), so an agent changing a save while the game
is closed still alarms, even right after a play session. A folder only the toolkit watches is never the game's.

Measured by stat on this machine's profiles, 2026-11-03, no game run. The last exit on each of 4 profiles (a play
profile, and 3 small ones that look like test runs): pipelinecache.bin 1 to 7 s after uidata.xml, debug.txt under 6 s
before it, in all 4. The play profile's other files: 5 of 33 moved in the hour before that exit, 16 of 33 in the 30 days
before it. A real whole-profile copy: 15 of 15 others and 14 of 14 saves within a minute, uidata.xml and userdata.xml
together. That EVERY exit writes one of the set is inferred from those samples, not proved.

What fools the quiet path. Things read as the game's: a change by something else while X4 runs, or stamped between the
previous look and a real exit, including one whose stamp was set back into that window (a post-call hook cannot tell two
writers apart inside one window, and this guards against accidents, not a determined attacker); a script that rewrites
uidata.xml, a second file of the exit set and at most MOST_A_SESSION_CHANGES saves while leaving most of the profile
alone; a copy of a profile with fewer than REWRITTEN_FLOOR other files and few saves.
What alarms although it was the game, once, with no effect on any save: a crash (no exit stamp), a session X4 left
without touching any of the exit set, more than MOST_A_SESSION_CHANGES saves overwritten or deleted between two looks,
and a session on a profile where half of the other files have also moved since the last look, as after months with no
agent running or on a nearly new profile.

Input: the roots packet then the hook payload (the same stdin save_policy.py reads). Output: a PostToolUse JSON
answer, or nothing.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from collections import Counter
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
ELEVATED_REPEAT = 12 * 3600   # seconds between repeats of the warning about a switch that says on
SESSION_FILE = "uidata.xml"       # rewritten by X4 on every exit, in the profile folder above its save folder
EXIT_COMPANIONS = ("pipelinecache.bin", "config.xml", "userdata.xml", "debug.txt")    # the rest of an exit: any one will do
EXIT_NAMES = {SESSION_FILE, *EXIT_COMPANIONS}
EXIT_SPREAD = 30                  # seconds between uidata.xml and a companion; measured under 7 at real exits
REWRITTEN_FLOOR = 5               # other top-level files that must move, with at least half of them, to read as a copy
MOST_A_SESSION_CHANGES = 10       # one folder, one look: three autosaves, a quicksave and a few overwritten saves
SHOWN = 15
FAILED_REPEAT = 3600              # seconds between telling the user the sentinel itself is failing


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


def _is_dir(path):
    """Absent is False. A folder that cannot be examined is True: dropping it would read its saves as removed, while
    keeping it lets the listing fail and report it as unread, with its saves carried as unknown."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    except OSError:
        return True


def save_folders(roots):
    return save_folders_and_gaps(roots)[0]


def save_folders_and_gaps(roots):
    """(save folders, gaps): `gaps` are the X4 profile folders whose accounts could not be LISTED here. Their saves are
    UNKNOWN for this look, never removed -- whatever the core's own listing of that folder says (4.1.1: a failed
    `iterdir` while the core's `scandir` of the same folder worked read every save below it as removed)."""
    found, gaps = list(save_policy.folders(roots)), []
    for docs in _known_documents():
        profile = docs / "Egosoft" / "X4"
        if _is_dir(profile):
            found.append(profile / "save")
            try:
                found.extend(p / "save" for p in profile.iterdir() if _is_dir(p))
            except OSError:
                found.append(profile)   # its accounts cannot be named: a failed listing reports this folder as unread
                gaps.append(profile)
    unique = {}
    for f in found:
        if _is_dir(f):
            unique.setdefault(os.path.normcase(os.path.abspath(f)), Path(f))
    return tuple(unique.values()), tuple(gaps)


def keep_unlisted(result, state, previous, gaps):
    """The look's report with every change under a folder in `gaps` withdrawn: such a save keeps its previous row in
    the record (unknown, not removed) and the folder is named as unread, as the core does for a folder it could not
    list itself."""
    if not gaps or not isinstance(previous, dict):
        return result
    tops = [os.path.join(os.path.abspath(g), "") for g in gaps]

    def under(path):
        return any(os.path.abspath(path).startswith(top) for top in tops)
    files = state.get("files")
    if isinstance(files, dict):
        for key, row in previous.items():
            if key not in files and isinstance(row, list) and len(row) > 2 and under(row[2]):
                files[key] = row
    unread = tuple(result.unread) + tuple(str(g) for g in gaps if str(g) not in result.unread)
    return result._replace(unbacked=tuple(c for c in result.unbacked if not under(c[1])),
                           backed_up=tuple(c for c in result.backed_up if not under(c[1])), unread=unread)


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


def _on_windows():
    return os.name == "nt"


def game_running():
    if not _on_windows():
        return False
    try:
        out = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, errors="replace",
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any('"' + g.lower() + '"' in out for g in GAMES)


def _stamp(path, default=None):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return default


def _left_exit_marks(profile, stamp):
    """Did another file of the exit set move with uidata.xml's `stamp`?"""
    return any(abs(_stamp(os.path.join(profile, name), stamp + EXIT_SPREAD + 1) - stamp) <= EXIT_SPREAD
               for name in EXIT_COMPANIONS)


def _profile_rewritten(profile, since):
    """Did most of the profile move since `since`? An exit leaves the other files alone; a restore or a sync that does
    not keep times rewrites them all. A profile that cannot be listed cannot be cleared, so it counts."""
    try:
        with os.scandir(profile) as entries:
            others = [e for e in entries if e.is_file() and e.name not in EXIT_NAMES]
            moved = sum(1 for e in others if e.stat().st_mtime >= since)
    except OSError:
        return True
    return moved >= REWRITTEN_FLOOR and moved * 2 >= len(others)


def session_end(save_folder, since):
    """When X4 last left the profile above `save_folder`, if that was since `since`. None: no exit shows (see the module
    docstring for what one takes)."""
    profile = os.path.dirname(save_folder)
    stamp = _stamp(os.path.join(profile, SESSION_FILE))
    if stamp is None or stamp < since:
        return None
    if not _left_exit_marks(profile, stamp):
        return None
    if _profile_rewritten(profile, since):
        return None
    return stamp


def _folder_key(path):
    return os.path.normcase(os.path.abspath(os.path.dirname(path)))


def games_doing(sentinel, changes, game_folders, since):
    """The (kind, path) changes dated inside a game session since `since` (see the module docstring)."""
    running = None
    quiet = []
    counts = Counter(_folder_key(path) for _kind, path in changes)
    for kind, path in changes:
        folder = os.path.dirname(path)
        if _folder_key(path) not in game_folders:
            continue                    # a folder only the toolkit watches: the game does not write there
        if counts[_folder_key(path)] > MOST_A_SESSION_CHANGES:
            continue                    # a bulk change is never a game session's
        if running is None:
            running = game_running()
        end = None if running else session_end(folder, since)
        if (running or end is not None) and sentinel.dated_within(kind, path, since, end):
            quiet.append((kind, path))
    return quiet


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
    state = sentinel.load_state(state_path, now=now)
    if state.get("busy"):           # the record is locked just now: nothing to compare with, and it stays the baseline
        return sentinel.describe(sentinel.check(None, None, state)), False
    if state.get("marked") is None or now - state.get("marked_time", 0) > MARKED_REFRESH:
        state["marked"] = marked_folders(root)
        state["marked_time"] = now
    folders, gaps = save_folders_and_gaps(roots)
    profile = profiles.GameProfile("x4", folders, EXTENSIONS)
    try:
        store = workspace.save_store(root, profile)
    except (OSError, ValueError):
        store = _NoStore()          # no readable store: every change is reported, none is "covered"
    since = state.get("time", now)
    previous = dict(state["files"]) if isinstance(state.get("files"), dict) else None
    result = sentinel.check(profile, store, state, watch=state["marked"], now=now)
    result = keep_unlisted(result, state, previous, gaps)
    notes, lock_loud = lock_notes(root, state, now)
    game_folders = {os.path.normcase(os.path.abspath(f)) for f in profile.save_roots}
    quiet = games_doing(sentinel, result.unbacked, game_folders, since)
    raised = tuple(c for c in result.unbacked if c not in quiet)
    # A folder that stays unreadable or too large is said once, not after every tool call.
    standing = sorted(result.truncated + result.unread)
    if state.get("noted") == standing:
        result = result._replace(truncated=(), unread=())
    state["noted"] = standing
    text = sentinel.describe(result._replace(unbacked=raised))
    if raised:
        text += ("\nThe save guard did not back these up. If a command or script you ran did this, tell the user "
                 "now; a removed file may be in the Recycle Bin.")
        if any(_folder_key(path) in game_folders for _kind, path in raised):
            text += ("\nIf X4 was played since the last look and nothing else touched these saves, this can be a false "
                     "alarm: a crash, or an exit that left no second sign of a session, reads as not the game's.")
    if quiet:
        lines = ["SAVE SENTINEL: {} save file(s) changed while X4 was running or before it last exited, so it may be "
                 "the game itself saving:".format(len(quiet))]
        lines += ["  {}: {}".format(kind, path) for kind, path in quiet[:SHOWN]]
        if len(quiet) > SHOWN:
            lines.append("  ... and {} more".format(len(quiet) - SHOWN))
        text = "\n".join(t for t in [text] + lines if t)
    # The record comes last: written before something that can still fail, it would lose the finding for good.
    try:
        sentinel.save_state(state_path, state)
    except OSError as exc:          # this look's findings still count; the old record stays the baseline, so at worst
        notes.append("SAVE SENTINEL: this look could not be recorded ({}), so the next look may repeat it."    # a repeat
                     .format(type(exc).__name__))
    return "\n".join(t for t in [text] + notes if t), bool(raised) or lock_loud or result.state_lost


def lock_switch(root):
    """The save-lock switch (modding_harness/save_lock.py); locks cannot be on in 4.1.0. It lives in the neutral harness state, which
    save_policy already refuses to let an agent change: only the user switches locking off."""
    return Path(root) / ".modding-agent-harness" / "save-lock.json"


def lock_notes(root, state, now):
    """(notes, raise to the user?). Nothing here locks: a lock binds the game too, and a player must be able to save over
    a save (owner ruling 2026-11-04). A switch that says on, from a pre-release build or a script, earns a warning.
    A switch turned off is still raised once."""
    save_lock = save_policy.core(root, "save_lock")
    enabled = bool(save_lock.read_config(lock_switch(root)).get("enabled"))
    notes, loud = [], False
    if state.get("lock_enabled") and not enabled:
        notes.append("SAVE LOCK: save locking was switched OFF ({} changed or removed). If the user did not do this, "
                     "an agent or script did: tell them now.".format(lock_switch(root)))
        loud = True
    state["lock_enabled"] = enabled
    if not enabled or not save_lock.supported():
        state.pop("lock_on_warned", None)
        return notes, loud
    if "lock_on_warned" not in state or now - state["lock_on_warned"] > ELEVATED_REPEAT:
        state["lock_on_warned"] = now
        notes.append("SAVE LOCK: the switch {} says on, but save locks cannot be on in this version, so nothing new is "
                     "locked. Any save already locked still cannot be saved over in game. Tell the user: from an "
                     "administrator prompt, python \"{}\" save-lock off".format(
                         lock_switch(root), Path(root) / "tools" / "agent-harness.py"))
        loud = True
    return notes, loud


def render(text, loud):
    if not text:
        return ""
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    if loud:
        out["systemMessage"] = text
    return json.dumps(out)


def failed_look(exc, roots, now=None):
    """(text, loud) for a look that failed. The agent hears every time and the user once per FAILED_REPEAT, because the
    user was told any save change is reported. With no roots there is nowhere to remember it, so only the agent hears."""
    now = time.time() if now is None else now
    text = "SAVE SENTINEL DID NOT RUN ({}): saves were not checked after this call.".format(type(exc).__name__)
    if not roots:
        return text, False
    try:
        mark = Path(save_policy.native(roots["toolkit"])) / ".modding-agent-harness" / ".save-sentinel.failed"
    except Exception:               # noqa: BLE001 -- an unusable packet: the agent still hears
        return text, False
    try:
        if now - mark.stat().st_mtime < FAILED_REPEAT:
            return text, False
    except OSError:
        pass                        # silent-ok: no mark yet means tell the user now
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_bytes(b"")
        os.utime(mark, (now, now))
    except OSError:
        pass                        # silent-ok: this report does not depend on the mark
    return text, True


def main():
    roots = None
    try:
        roots, _payload = save_policy.packet(sys.stdin.buffer.read().decode("utf-8"))
        answer = render(*report(roots))
    except Exception as e:          # advisory: disclose that nothing was checked, never fail the call
        answer = render(*failed_look(e, roots))
    if answer:
        sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())

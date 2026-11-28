"""Session-start helper for the toolkit's self-update: `note` and `check`. Stdlib only: the hook runs it with a bare python.

    python scripts/x4update_session.py note     print what a session start should say about updates (cache only)
    python scripts/x4update_session.py check    ask GitHub (at most once a day by itself) and cache the answer

session-canary.sh runs `note` first (it prints before the session's context is built, and never touches the network),
then starts `check` in the background the way it starts the x4fresh launcher: a plain background child of the hook,
never DETACHED_PROCESS, which would give every console program it starts a window of its own. `check` never delays
a session, and the next session's `note` reads what it cached.

What `note` prints, in this order:
  1. a failed or unfinished update (`guarded_update.session_note`: `update-failed.json` / `update-in-flight.json`).
     ALWAYS, with every update-check switch off, every session until the user clears it: an update that broke the guards
     must be loud, and "checks are off" is no reason to hide it.
  2. "a newer release is out", once per version, from the cache the last `check` wrote (`release.note`). Silent offline,
     silent for a git checkout (there is no installed release to compare with), silent with a check switch off.

Turn the checking off: X4_UPDATE_CHECK=off, X4_HOUSEKEEP_SWEEP=off, MODDING_HARNESS_UPDATE_CHECK=off, or
`{"v": 1, "check": false}` in `.modding-agent-harness/updates.json`. `check` also never runs under a test harness
(PYTEST_CURRENT_TEST or HARNESS_FIXTURE_ROOT), when there is no installed release to compare with, or when this is not
the toolkit's own entry (`tools/agent-harness.py` beside it). Neither command ever raises: an update check must not
cost the session its banner. The root is always the toolkit this file sits in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
TOOLKIT = Path(__file__).resolve().parent.parent
HARNESS = TOOLKIT / "tools" / "x4_harness"


def _x4_update():
    if str(HARNESS) not in sys.path:
        sys.path.insert(0, str(HARNESS))
    import x4_update
    return x4_update


def _guarded_update(root):
    """`guarded_update.session_note(root)`: the failed/in-flight marker line, or None."""
    _x4_update()                                # puts tools/x4_harness on the path
    import save_policy
    return save_policy.core(TOOLKIT, "guarded_update").session_note(root)    # the core is THIS toolkit's, `root` its subject


def note(root=TOOLKIT, environ=None):
    """The text a session start prints about updates (cache only, no network); "" when there is nothing to say."""
    environ = os.environ if environ is None else environ
    lines = []
    try:
        marker = _guarded_update(root)
        if marker:
            lines.append(str(marker))
    except Exception as exc:                    # silent-ok: the line below says so; the banner must still print
        lines.append("TOOLKIT UPDATE STATE UNKNOWN: could not look for a failed update ({}). Tell the user before "
                     "updating.".format(type(exc).__name__))
    try:
        x4u = _x4_update()
        if not x4u.off(environ):
            line = x4u.release.note(root, x4u.PROFILE, environ=environ)
            if line:
                lines.append(line)
    except Exception:
        pass                                    # silent-ok: an unreadable cache is a missing "newer release" line only
    return "\n".join(lines) + "\n" if lines else ""


def background_check(root=TOOLKIT, environ=None, fetch=None, now=None):
    """Ask GitHub if everything allows it; returns why not ("test", "off", "no-release", "not-own-entry") or the
    check's own status ("checked", "unreachable", ...). The core asks at most once a day. Never raises."""
    environ = os.environ if environ is None else environ
    try:
        if environ.get("PYTEST_CURRENT_TEST") or environ.get("HARNESS_FIXTURE_ROOT"):
            return "test"
        x4u = _x4_update()
        if x4u.off(environ) or not x4u.release.enabled(root, environ):
            return "off"
        if not (Path(root) / "tools" / "agent-harness.py").is_file():
            return "not-own-entry"
        if x4u.installed_version(root) is None:
            return "no-release"
        state = x4u.release.check(root, x4u.PROFILE, fetch=fetch or x4u.release.http_get, now=now, environ=environ)
        return str(state.get("status"))
    except Exception as exc:
        return "failed: " + type(exc).__name__


#: A hung `note` (a stuck read of a network drive, a wedged import) must never hold the session's banner, and a hung
#: `check` must not live on. Seconds; X4_UPDATE_NOTE_TIMEOUT_S overrides `note`'s (the tests' lever).
NOTE_TIMEOUT_S = 10.0
CHECK_TIMEOUT_S = 120.0


def _arm_watchdog(mode):
    """Give up (exit 0; for `note`, say so in one line) when this process is still running after its time limit."""
    import threading
    limit = CHECK_TIMEOUT_S
    if mode == "note":
        limit = NOTE_TIMEOUT_S
        try:
            limit = float(os.environ.get("X4_UPDATE_NOTE_TIMEOUT_S", limit))
        except ValueError:
            pass

    def give_up():
        if mode == "note":
            sys.stdout.write("[x4 update] the update note timed out and was skipped.\n")
            sys.stdout.flush()
        os._exit(0)
    timer = threading.Timer(limit, give_up)
    timer.daemon = True
    timer.start()


def main(argv):
    if len(argv) != 1 or argv[0] not in ("note", "check"):
        print(__doc__, file=sys.stderr)
        return 2
    if __name__ == "__main__":
        _arm_watchdog(argv[0])
    try:
        if argv[0] == "note":
            text = note()
            if text:
                sys.stdout.write(text)
        else:
            background_check()
    except Exception:
        pass                                    # silent-ok: never cost the session its banner
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

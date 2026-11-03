"""Session-start launcher for `x4fresh rebuild --auto`. Stdlib only: the hook runs it with a bare python.

The hook starts this DETACHED (session-canary.sh, beside the housekeep sweep) and returns at once, so the
hook's time budget never depends on a build. This script then runs the real thing inside the toolkit's
own environment, at BELOW-NORMAL priority, and waits for it -- it is already off the session's path.

    python scripts/x4fresh_session.py <note file>

<note file> is where x4fresh writes the outcome; the next session start prints it first. It is empty only
when everything this machine can build is fresh: this script writes "did not complete" before starting, so a
killed run or a failure before x4fresh reports can never leave an empty note reading as fine. No uv on PATH,
and any non-zero exit with no report from x4fresh, are said there too.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent.parent
PKG = TOOLKIT / "tools" / "x4validate"
BELOW_NORMAL = 0x00004000
NO_WINDOW = 0x08000000
# x4validate._freshcli.NOTE_PENDING starts with this (pinned by tests/test_x4fresh_session.py). It is also how
# a report is told from no report: only the pending text starts this way.
PENDING = "x4fresh: NOT CHECKED: the session-start check started"


def command(note: Path, uv: str) -> list[str]:
    return [uv, "run", "--frozen", "--directory", str(PKG), "x4fresh", "rebuild", "--auto", "--note", str(note)]


def _write(note: Path, text: str) -> None:
    note.parent.mkdir(parents=True, exist_ok=True)
    tmp = note.with_name(f"{note.name}.{os.getpid()}.launcher.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, note)


def _no_report(note: Path) -> bool:
    try:
        return note.read_text(encoding="utf-8", errors="replace").startswith(PENDING)
    except OSError:
        return True  # silent-ok: an unreadable note reports nothing, so the caller replaces it with the reason


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    # Absolute BEFORE handing it on: `uv run --directory` changes the working directory, and a relative
    # note would land inside tools/x4validate (review 2026-11-01, finding 11).
    note = Path(argv[0]).resolve()
    if os.environ.get("X4_FRESH_AUTO", "").lower() == "off":
        return 0
    uv = shutil.which("uv")
    if not uv:
        _write(note, "[x4 fresh] derived stores were NOT checked at session start: uv is not on PATH. "
                     "Run `x4fresh status` from tools/x4validate.\n")
        return 2
    # Worst case while another run holds the lock: its report lands, this exits at the lock, and the note
    # reads "did not complete" until the next session start. A false alarm, never a false all-clear.
    _write(note, PENDING + " but has not reported: it is still running, or it died. "
                           "Run `x4fresh status` from tools/x4validate.\n")
    kwargs = {"creationflags": BELOW_NORMAL | NO_WINDOW} if os.name == "nt" else {"preexec_fn": lambda: os.nice(10)}
    try:
        done = subprocess.run(command(note, uv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, encoding="utf-8", errors="replace", **kwargs)
    except OSError as exc:
        _write(note, f"[x4 fresh] derived stores were NOT checked at session start: x4fresh could not start "
                     f"({type(exc).__name__}). Run `x4fresh status` from tools/x4validate.\n")
        return 2
    if done.returncode != 0 and _no_report(note):
        tail = (done.stderr or "").strip()[-300:]
        _write(note, f"x4fresh: NOT CHECKED: the session-start check exited rc {done.returncode} with no report"
                     + (f": {tail}" if tail else "") + ". Run `x4fresh status` from tools/x4validate.\n")
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

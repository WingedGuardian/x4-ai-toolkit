"""Session-start launcher for `x4fresh rebuild --auto`. Stdlib only: the hook runs it with a bare python.

The hook starts this DETACHED (session-canary.sh, beside the housekeep sweep) and returns at once, so the
hook's time budget never depends on a build. This script then runs the real thing inside the toolkit's
own environment, at BELOW-NORMAL priority, and waits for it -- it is already off the session's path.

    python scripts/x4fresh_session.py <note file>

<note file> is where x4fresh writes the outcome (empty when everything is fresh); the next session start
prints it first. No uv on PATH is reported there too, rather than failing invisibly.
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


def command(note: Path, uv: str) -> list[str]:
    return [uv, "run", "--frozen", "--directory", str(PKG), "x4fresh", "rebuild", "--auto", "--note", str(note)]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    note = Path(argv[0])
    if os.environ.get("X4_FRESH_AUTO", "").lower() == "off":
        return 0
    uv = shutil.which("uv")
    if not uv:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("[x4 fresh] derived stores were NOT checked at session start: uv is not on PATH. "
                        "Run `x4fresh status` from tools/x4validate.\n", encoding="utf-8")
        return 2
    kwargs = {"creationflags": BELOW_NORMAL | NO_WINDOW} if os.name == "nt" else {"preexec_fn": lambda: os.nice(10)}
    return subprocess.run(command(note, uv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **kwargs).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

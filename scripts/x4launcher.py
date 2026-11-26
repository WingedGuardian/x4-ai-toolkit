#!/usr/bin/env python3
r"""Write the launcher that starts a coding agent from the install folder.

WHY. An agent loads its guards and instructions from the folder it is started in. Started from any other
folder it runs with NO guards and none of the toolkit's knowledge, and nothing inside the toolkit can notice
that. The one lever that works for every agent is to change into the install folder first, so the installer
writes a small script that does exactly that and then starts the chosen agent. The script itself comes from
the vendored core (tools/modding_harness/launcher.py); this file only says which agents and where.

USAGE (run it isolated, `python -I`, so no folder in the way can shadow an import)
    python -I scripts/x4launcher.py --toolkit DIR --agents claude,codex,opencode [--dry-run] [--shells bash,cmd]

  --toolkit DIR   the install folder the agent must be started in (baked into the launcher as an absolute path)
  --agents LIST   the agent targets installed there. `generic` is accepted and ignored: it has no command to start.
  --shells LIST   which launcher files to write, from bash, cmd, powershell. Default: bash and cmd on Windows,
                  bash elsewhere. The .ps1 is opt-in: MEASURED 2026-11-26 on Windows PowerShell 5.1 and
                  PowerShell 7, with a `.ps1` and a `.cmd` of the same name in one folder, a bare name picks the
                  `.ps1` (twice of twice), and under the stock execution policy (Restricted) that refuses to
                  run, where the `.cmd` ran every time. So a default `.ps1` would shadow the working launcher.
  --dry-run       print what would be written and write nothing.

Writes `<DIR>/bin/x4agent`, `x4agent.cmd` (and `x4agent.ps1` when asked for). The files bake an absolute path,
so they are per-machine (gitignored; the installers' keep-local lists). It refuses to overwrite a file it did not
write. Exit 0 when written (or nothing to write); exit 1, with a WARNING naming the reason, on any failure. The
installers treat that as a warning: a missing launcher never makes an install INCOMPLETE.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

NAME = "x4agent"
#: The agent targets that have a command of their own, and the command. `generic` has none.
COMMANDS = {"claude": "claude", "codex": "codex", "opencode": "opencode"}
SUFFIX = {"bash": "", "powershell": ".ps1", "cmd": ".cmd"}


def _core():
    """The vendored core launcher, from the tree THIS script lives in (never from the folder being installed)."""
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from modding_harness import launcher
    return launcher


def default_shells():
    return ("bash", "cmd") if os.name == "nt" else ("bash",)


def agent_table(names):
    """`{name: command}` for the listed agent targets that have a command; unknown names are an error."""
    table = {}
    for raw in names:
        name = raw.strip()
        if not name or name == "generic":
            continue
        if name not in COMMANDS:
            raise ValueError("unknown agent %r (known: %s)" % (name, ", ".join(COMMANDS)))
        table[name] = COMMANDS[name]
    return table


def _foreign(core, path):
    """True when `path` exists and is not a file the core launcher wrote (read-only twin of the core's refusal)."""
    if not (path.exists() or path.is_symlink()):
        return False
    try:
        with open(path, "rb") as stream:
            return core.MARKER.encode() not in stream.read(1024)
    except OSError:
        return True


def run(toolkit, agents, shells, dry_run, out=print):
    core = _core()
    table = agent_table(agents)
    if not table:
        out("launcher: no installed agent has a command to start, so no launcher was written.")
        return 0
    root = Path(toolkit)
    bin_dir = root / "bin"
    if dry_run:
        for shell in shells:
            core.render(root, shell, table, name=NAME)          # validates the folder, names and quoting
            target = bin_dir / (NAME + SUFFIX[shell])
            if _foreign(core, target):
                raise FileExistsError("refusing to overwrite a file this launcher did not write: %s" % (target,))
            out("launcher: would write %s" % (target,))
        out("launcher: --dry-run, nothing written.")
        return 0
    written = core.write(root, bin_dir, table, name=NAME, shells=tuple(shells))
    for path in written:
        out("launcher: wrote %s" % (path,))
    out("launcher: start an agent from anywhere with  %s <%s>  (it changes into %s first)"
        % (bin_dir / (NAME + (".cmd" if "cmd" in shells and os.name == "nt" else "")), "|".join(table), root))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the launcher that starts a coding agent from the install folder.")
    parser.add_argument("--toolkit", required=True, help="the install folder")
    parser.add_argument("--agents", required=True, help="comma-separated installed agent targets, e.g. claude,codex,opencode")
    parser.add_argument("--shells", default="", help="comma-separated: bash, cmd, powershell (default: %s)"
                        % ",".join(default_shells()))
    parser.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    args = parser.parse_args(argv)
    shells = tuple(s.strip() for s in args.shells.split(",") if s.strip()) or default_shells()
    try:
        bad = [s for s in shells if s not in SUFFIX]
        if bad:
            raise ValueError("unknown shell %s (use bash, cmd or powershell)" % ", ".join(map(repr, bad)))
        return run(args.toolkit, [a for a in args.agents.split(",")], shells, args.dry_run)
    except (ValueError, OSError, ImportError) as error:
        print("WARNING: launcher not written: %s" % (error,), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

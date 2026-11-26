"""The X4 toolkit's release profile for the shared core's self-update (`modding_harness.release`).

Users find the toolkit on Nexus or GitHub and install it the first time; later releases come from the GitHub
releases, through the agent. The core does the checking and the verified download; this file names the repository and
the zip, reads the installed version, and says which switches turn the check off. It lives in `tools/x4_harness/`
(generated from `agent/guards/adapters/`, like `patch_paths.py`), never in a hook folder.

The zip's name is the one `scripts/build-release.sh` makes (`NAME_PREFIX` + `-v<version>.zip`); a test parses the
script, so the two cannot drift.

Turn the check off: `X4_UPDATE_CHECK=off`, `X4_HOUSEKEEP_SWEEP=off` (the switch every helper a session start launches
honours, and the one test harnesses set), `MODDING_HARNESS_UPDATE_CHECK=off`, or `{"v": 1, "check": false}` in
`.modding-agent-harness/updates.json`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import save_policy

ROOT = Path(__file__).resolve().parents[2]
release = save_policy.core(ROOT, "release")

REPO = "WingedGuardian/x4-ai-toolkit"
ASSET = "X4.Foundations.AI.Assistant.Toolkit-v{version}.zip"
_RELEASE_HEADING = re.compile(r"##[ \t]+(v\d+(?:\.\d+){1,3})(?=[ \t]|$)")


def installed_version(root):
    """The release this install came from, or None when there is nothing to compare against.

    A git checkout is None whatever else is in it: a development tree is updated with git, and a stray BUILD-INFO
    (gitignored, so easily left behind by a copy) must not make it look like a release, because the daily check would
    offer updates and the guarded update would aim at a git working tree. Otherwise BUILD-INFO's `version=`; else the
    installed CHANGELOG's FIRST `## ` heading when it reads `vX.Y.Z` (a release installed before 4.3.1 has no
    BUILD-INFO, and a dry-run build's has an empty `version=`). None for a `## Unreleased` first heading."""
    if (Path(root) / ".git").exists():
        return None
    version = release.build_info(root).get("version")
    if release.parse_version(version):
        return version
    try:
        with (Path(root) / "CHANGELOG.md").open("rb") as stream:
            head = stream.read(64 * 1024).decode("utf-8", "replace")
    except OSError:
        return None
    for line in head.splitlines():
        if line.startswith("## "):
            match = _RELEASE_HEADING.match(line)
            return match.group(1)[1:] if match else None
    return None


PROFILE = release.ReleaseProfile(REPO, ASSET, "The X4 toolkit", installed_version)


def off(env):
    """The X4 switches that turn update checks off, for the session note AND `update-check` itself (any case, as the
    hook reads them): X4_UPDATE_CHECK=off, X4_HOUSEKEEP_SWEEP=off, MODDING_HARNESS_UPDATE_CHECK=off (also 0, false, no).
    The updates.json setting is the core's own (`release.enabled`)."""
    return (str(env.get("X4_UPDATE_CHECK", "")).strip().lower() == "off"
            or str(env.get("X4_HOUSEKEEP_SWEEP", "")).strip().lower() == "off"
            or str(env.get("MODDING_HARNESS_UPDATE_CHECK", "")).strip().lower() in ("off", "0", "false", "no"))


def handle(args, root, environ=None):
    """One parsed `update-check` / `update-fetch` command (the harness entry's dispatch). An off switch answers
    `update-check` with status "off" and no request; the core's `handle` does everything else."""
    environ = os.environ if environ is None else environ
    installed_root = Path(args.installed_root or root)
    if args.command == "update-check" and off(environ):
        return {"installed": installed_version(installed_root), "latest": None, "status": "off",
                "update_available": False, "checked_at": None, "error": None}
    return release.handle(args, root, PROFILE)

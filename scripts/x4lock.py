#!/usr/bin/env python3
"""Make the irreplaceable files unwritable by accident, and say so honestly.

WHY THIS EXISTS. Four times in twelve days this workspace's own tooling destroyed a
live, irreplaceable file: a memory file and a 375-line module truncated to 0 bytes by
text-mode writes that raised mid-encode, the game-root CLAUDE.md and KNOWLEDGEBASE.md
overwritten by a reviewer's installer probe, and a 258-entry mod registry replaced by a
46-byte fresh one. None was a decision. Each was a command that ran, resolved to a live
path, and met nothing in the way.

Prose did not stop any of them -- 150 KB of instructions were in context every time. The
two locations on this machine that have a MECHANISM have never been lost: `reference/`
(a sentinel the tools refuse past) and the user profile (git plus a confirming hook).
This gives the rest of the irreplaceable set protection at the only layer that can see a
write from inside another process: the filesystem.

WHAT STOPS A WRITE (measured here, Windows 11, 14 primitives, each with a control
proving the same primitive DOES change the file when it is unlocked):

    primitive                                     read-only attribute
    python open("w") / write_text / write_bytes   blocked
    python os.truncate / os.replace               blocked   (os.replace: WINDOWS ONLY)
    python shutil.copy2 / os.remove               blocked   (os.remove:  WINDOWS ONLY)
    bash > / cp / tee                             blocked
    pwsh Set-Content                              blocked
    bash rm -f                                    NOT blocked
    pwsh Copy-Item -Force / Remove-Item -Force    NOT blocked

11 of 14 ON WINDOWS. The three that get through are DELETES and force-overwrites:
POSIX `unlink` is authorised by write permission on the DIRECTORY, not the file, and
`-Force` clears the attribute before writing.

WARNING: ON LINUX/macOS IT IS 9 OF 14. `os.remove` IS unlink and `os.replace` IS
rename-over, so the directory-permission rule stated above for `rm -f` applies to BOTH
python primitives too -- this table listed them as blocked because it was measured on
Windows, where the read-only attribute does deny both. MEASURED on CI (ubuntu, run
33994180317): `test_a_locked_file_survives[os_remove]` and `[os_replace]` DID NOT RAISE.
The test now pins the weaker POSIX guarantee instead of skipping it, so the two rows
above cannot drift back to "universal" unnoticed. Those are covered a layer up -- the Bash guard asks
before a delete inside an X4 directory, and `x4canary` notices a file that shrank.

The number that decided the design: ALL FOUR real incidents are in the blocked set.
Every one was a python text write or an installer's `cp`; not one was `rm -f` or
`-Force`. This stops what has actually happened, not what is imaginable.

⚠ An earlier version of this table said `rm -f` was blocked. It was measured with the
`bash` on Windows PATH, which is the WSL stub: it cannot open a `C:/...` path, so its
failure READ AS the lock working. Always pair such a probe with a control on an
unlocked file -- the control is what turned that row from a pass into a non-answer.

WHY THERE IS NO ACL HERE, THOUGH AN ACL WOULD CLOSE THAT GAP. The first version of this
tool also applied `icacls /deny <user>:(W,D,WDAC,WO)`, which does block `-Force`. It was
withdrawn after it locked this machine out of two of its own files:

  * `W` in icacls shorthand is FILE_GENERIC_WRITE, which INCLUDES SYNCHRONIZE. Every
    synchronous open needs SYNCHRONIZE, so a "write deny" denies READING as well. The
    files became completely inaccessible, one of them a hook that runs on every edit.
  * The deny includes WDAC, so it removes the permission needed to remove itself. On a
    file owned by BUILTIN\\Administrators rather than the user -- which is ordinary under
    Program Files -- `icacls /remove:d`, `Set-Acl`, `takeown` and even rename all fail
    unelevated. Repair required an administrator.

A protection whose failure mode is "you can no longer read your own file, and cannot
undo it without elevation" is worse than the accident it prevents. The read-only bit
cannot deny a read, cannot be un-removable, and works the same on every platform.

The lock is meant to stop an ACCIDENT, not a determined process. Any code that really
means it can clear the bit -- but it has to MEAN it, in a separate visible step, which
is exactly what none of the four incidents did.

USAGE
    python scripts/x4lock.py status            what is protected, and is it locked
    python scripts/x4lock.py lock              lock every file in the manifest
    python scripts/x4lock.py unlock <path>     unlock ONE file (the choke point)
    python scripts/x4lock.py unlock --all      unlock everything
    python scripts/x4lock.py protected <path>  exit 0 if it is in the manifest (for the guards)

    Every subcommand takes --toolkit DIR. Default: the toolkit this script LIVES IN (B2,
    install red-team 2026-10-04); lock/unlock REFUSE (exit 2) while $X4_TOOLKIT names a
    different toolkit unless --toolkit is given -- and while an exported X4_GAME/X4_MODS/...
    names a different root from that toolkit's config, unless --game / --registry chooses one
    (FX-B2). Both roots are printed.

The manifest is derived from the configured roots, never hard-coded to one machine, and
is extended by `X4_PROTECTED` (an os.pathsep-separated list) for anything site-specific.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "tools" / "x4validate"))

try:
    from x4validate import _paths
except ImportError:                     # pragma: no cover - packaging accident
    _paths = None

#: Files whose loss cannot be undone by re-running anything. Deliberately NOT whole
#: directories: `.claude/backups/`, `settings.local.json` and Claude Code's own lock
#: files are written during normal operation, and a lock that breaks routine work gets
#: switched off -- which protects nothing.
_GAME_RELATIVE = (
    "KNOWLEDGEBASE.md",
)

#: Files DEMANDED only when their agent target is installed (reported MISSING if absent).
#: The marker is the target's own GUARD directory, so a Claude-only root is never reported
#: as missing a Codex file -- 0 of 23 releases before 4.0 shipped AGENTS.md -- and an
#: `install --agent codex` root is never reported as missing CLAUDE.md. Not the bare
#: `.claude/` or `.codex/`: a 3.x install wrote its path config to `.claude/x4-paths.env`
#: (4.x still reads it there, deprecated), and Codex itself reads a project `.codex/config.toml`, so either directory can exist
#: with that target absent. A root with NO marker keeps the pre-4.0 Claude demand (see
#: `_demanded_targets`), so deleting `.claude/` wholesale never makes CLAUDE.md's absence
#: silent.
_TARGET_DEMANDS = {
    ".claude/hooks": ("CLAUDE.md", ".claude/settings.json"),
    ".codex/hooks": ("AGENTS.md", ".codex/hooks.json"),
    # OpenCode (Plan 3 lane L, best effort): its guard copy is the marker, never a bare
    # .opencode/ -- OpenCode writes that folder itself and a user may keep plugins there.
    ".opencode/hooks": ("AGENTS.md", ".opencode/plugins/x4guard.js"),
    "tools/x4_harness": ("AGENTS.md", "tools/x4_harness/x4guard.py", "tools/x4_harness/_x4-env.sh"),
}

#: The guards themselves. A protection that can silently disable itself is not one.
#: These are edited rarely and deliberately, so the unlock step costs nothing.
_GAME_GLOBS = (
    ".claude/hooks/*.sh",
    ".claude/hooks/*.py",
    ".claude/skills/*/SKILL.md",
    # A skill is more than its SKILL.md: the generated x4-cli-reference skill keeps its
    # per-CLI help in reference/*.md, and those were deployable but unlockable (2026-09-13).
    ".claude/skills/*/reference/*.md",
    ".claude/agents/*.md",
    # Lock-if-present: AGENTS.md is user content in every pre-4.0 root, and is demanded
    # only where `.codex/` is installed (_TARGET_DEMANDS).
    "AGENTS.md",
    # The Codex target is a full guard copy (user decision #4) plus its frozen hook
    # definitions and execpolicy rules: the same reasoning as `.claude/hooks/`.
    ".codex/hooks.json",
    ".codex/rules/*.rules",
    ".codex/hooks/*.sh",
    ".codex/hooks/*.py",
    ".codex/hooks/*.ps1",
    # Skills for Codex and generic agents, locked exactly like Claude's (decision #11).
    ".agents/skills/*/SKILL.md",
    ".agents/skills/*/reference/*.md",
    # The OpenCode target (Plan 3 lane L): a full guard copy, OUR plugin by name (a user's
    # own plugin beside it is theirs), the rendered deny rules, the addendum and the skills.
    ".opencode/hooks/*.sh",
    ".opencode/hooks/*.py",
    ".opencode/hooks/*.ps1",
    ".opencode/plugins/x4guard.js",
    ".opencode/opencode.jsonc",
    ".opencode/X4-OPENCODE.md",
    ".opencode/skills/*/SKILL.md",
    ".opencode/skills/*/reference/*.md",
    "tools/x4_harness/*.py",
    "tools/x4_harness/*.sh",
    "tools/x4_harness/*.tmpl",
    "tools/modding_harness/*.py",
    "tools/modding_harness/*.mjs",
    "tools/modding-harness.lock.json",
)


def _cfg(name: str):
    """Resolve one configured root, and REFUSE to invent an answer for a name that
    does not exist.

    The first draft was `getattr(_paths, name, lambda: None)()`. It asked for `game`
    and `toolkit`, neither of which is a function in `_paths`, so it silently produced
    a two-file manifest and reported it as a complete one -- a step that narrows the
    data and reports success anyway, which is the bug class this tool exists to end.
    A typo must be a crash, never a smaller answer.
    """
    if _paths is None:
        return None
    fn = getattr(_paths, name, None)
    if not callable(fn):
        raise AttributeError(
            "x4lock asked _paths for %r, which does not exist. Refusing to build a "
            "manifest from a name that resolves to nothing." % name)
    return fn()


class Unresolvable(RuntimeError):
    """The manifest cannot be built, so no answer about it means anything."""


def _worktree_common_dir(root: Path) -> Path | None:
    """The shared git directory when `root` is a LINKED git worktree, else None.

    git lays a linked worktree out with `.git` as a FILE whose `gitdir:` names an admin
    directory `<common>/worktrees/<name>`, and that admin directory holds a `commondir`
    file pointing back at `<common>` (MEASURED: `../..` in this repository's worktrees).
    The main checkout has a `.git` DIRECTORY. A SUBMODULE also has a `.git` file, but its
    `.git/modules/<path>` directory has no `commondir` -- which is why that file, and not
    a folder named `worktrees`, is the test: a submodule added at `vendor/worktrees/x` has
    one in its path. Anything unreadable or unrecognised answers None: a false MISSING is
    visible, a false waiver is silent.
    """
    marker = root / ".git"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("gitdir:"):
            continue
        value = line[len("gitdir:"):].strip()
        if not value:
            return None
        gitdir = Path(value)
        if not gitdir.is_absolute():
            gitdir = root / gitdir
        try:
            common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):     # ValueError: undecodable bytes, an embedded NUL
            return None
        return (gitdir / common).resolve() if common else None
    return None


def _same_file(a: Path, b: Path) -> bool:
    """The comparison `_dedup` uses, so the note and the manifest cannot disagree. A path
    that cannot be resolved compares by its text rather than raising."""
    try:
        return str(a.resolve()).lower() == str(b.resolve()).lower()
    except OSError:
        return str(a).lower() == str(b).lower()


def _config_in(root: Path) -> Path:
    """THE path config of the toolkit at *root* (Plan 3 lane I): the 4.x `x4-paths.env` if it
    exists, else the 3.x `.claude/x4-paths.env` if THAT exists, else the 4.x path -- the one
    to demand. `_paths.config_file_in` is the one implementation."""
    if _paths is not None:
        return _paths.config_file_in(root)
    return Path(root) / "x4-paths.env"


def _config_files_in(root: Path) -> list[Path]:
    """`_config_in(root)`, plus a 3.x copy that still EXISTS beside a 4.x one: it is ignored
    by every loader but still carries keys (X4_NEXUS_KEY), so it stays locked until it is
    retired (`scripts/x4config.py migrate --apply`). Lock-if-present: never demanded."""
    out = [_config_in(root)]
    legacy = Path(root) / ".claude" / "x4-paths.env"
    if legacy.is_file() and not _same_file(legacy, out[0]):
        out.append(legacy)
    return out


def _local_env_waived() -> Path | None:
    """This checkout's own path config when it is ABSENT from a linked worktree
    AND something can stand in for it -- the one case where its absence is by design, not
    a loss (F119). Else None.

    Fails closed: with nothing to demand instead (a common dir not named `.git`, and no
    $X4_TOOLKIT), the local file is demanded as before. A false MISSING is visible; a
    waiver with nothing in its place is silent.
    """
    local_env = _config_in(_HERE.parent)
    if (not local_env.is_file() and _worktree_common_dir(_HERE.parent) is not None
            and _waiver_replacements()):
        return local_env
    return None


def _waiver_replacements() -> list[Path]:
    """The config files demanded INSTEAD when this worktree's own is waived: the main
    checkout's, derived from git's worktree metadata, and $X4_TOOLKIT's.

    `_paths._find_env_file` cannot stand in for them. It returns only a file that EXISTS
    -- from $X4_TOOLKIT, else by walking up from the working directory -- so a deleted
    copy is simply not found, and that deletion would be reported by nobody.
    """
    out: list[Path] = []
    common = _worktree_common_dir(_HERE.parent)
    # Only a common dir named `.git` has a known checkout: its parent. A bare repository, a
    # submodule's `.git/modules/<name>` and a `--separate-git-dir` layout derive nothing --
    # and then the waiver applies only if $X4_TOOLKIT names a copy (see _local_env_waived).
    if common is not None and common.name == ".git":
        out.extend(_config_files_in(common.parent))
    if os.environ.get("X4_TOOLKIT"):
        out.extend(_config_files_in(Path(os.environ["X4_TOOLKIT"])))
    return out


def _candidates() -> list[Path]:
    """Every path the manifest is DERIVED from, existing or not.

    Separate from `manifest()` because an EXPECTED-BUT-ABSENT file is a finding, not
    an empty slot. MEASURED 2026-09-04: deleting a locked file simply shrank the
    manifest and `status` printed "2 protected file(s): 2 locked", exit 0 -- while
    this module's own table names `rm -f` / `Remove-Item -Force` as the primitives
    the read-only bit does NOT stop. Deletion is the documented residual risk and it
    was rendering as a clean sweep.
    """
    if _paths is None:
        # A 1-of-26 manifest reported as healthy is worse than a crash: it is the
        # narrowing-step-that-reports-success shape this whole tool exists to stop.
        raise Unresolvable(
            "x4validate._paths could not be imported, so the game root, the registry "
            "and the configured toolkit's x4-paths.env are ALL unknown. The manifest "
            "would hold only this checkout's own env file -- 1 of 26 on this machine "
            "-- and reporting that as complete is the failure this tool exists to "
            "prevent.")
    out: list[Path] = []

    game = _cfg("game_root")
    game = Path(game) if game else None
    roots = _agent_roots(game)
    marked = [r for r in roots if _markers_in(r)]
    for root in roots:
        if root in marked:
            demanded = _markers_in(root)
        elif not marked and game is not None and root is game:
            demanded = _demanded_targets(root)       # the pre-4.0 Claude fallback
        else:
            demanded = None                         # lock-if-present only
        if demanded is None:
            out.extend(root / rel for rel in _GAME_RELATIVE if (root / rel).is_file())
        else:
            out.extend(root / rel for rel in _GAME_RELATIVE)
            for marker in demanded:
                out.extend(root / rel for rel in _TARGET_DEMANDS[marker])
        for pat in _GAME_GLOBS:
            out.extend(sorted(root.glob(pat)))

    # DEMANDED once its folder exists. The registry is created by the first x4modlist run,
    # and before that its folder does not exist either: demanding it made every fresh install
    # read MISSING (v4.0.0 review, MEASURED on scratch installs). A deleted registry whose
    # folder remains -- the incident shape, a file replaced or removed -- is still MISSING;
    # removing the whole `_registry/` folder is the residual gap, and it is named here.
    reg = _cfg("registry")
    if reg and (Path(reg).is_file() or Path(reg).parent.is_dir()):
        out.append(Path(reg))

    # Every `x4-paths.env` in play: the one this checkout carries, and the one the
    # configured toolkit root carries. They are usually different files, and the
    # second is what every hook actually reads.
    #
    # F119: a LINKED git worktree never has its own -- the file is gitignored per-machine
    # config, and CLAUDE.md mandates a worktree per concurrent session -- so its absence
    # there is not MISSING. The waiver costs nothing: the copies it stands in for are
    # demanded explicitly instead (see `_waiver_replacements`).
    if _local_env_waived() is None:
        out.extend(_config_files_in(_HERE.parent))
    else:
        out.extend(_waiver_replacements())
    # FX-B3: an explicit --toolkit's OWN config is in play too. `status --toolkit B` under an
    # inherited X4_CONFIG naming A's listed A's file and never B's.
    if _paths is not None and _paths.explicit_toolkit() is not None:
        out.extend(_config_files_in(_paths.explicit_toolkit()))
    env_file = _paths._find_env_file() if _paths is not None else None
    if env_file:
        out.append(Path(env_file))

    for extra in (os.environ.get("X4_PROTECTED") or "").split(os.pathsep):
        if extra.strip():
            out.append(Path(extra.strip()))

    return out


def _toolkit_layout():
    """scripts/toolkit_layout.py, loaded from THIS file's folder (never `_HERE`, which tests
    repoint): the one answer to "source checkout or install?" shared with x4doctor."""
    import importlib.util
    path = Path(__file__).resolve().parent / "toolkit_layout.py"
    spec = importlib.util.spec_from_file_location("x4lock_toolkit_layout", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LAYOUT = _toolkit_layout()


def _is_installed_toolkit(root: Path) -> bool:
    """An INSTALLED toolkit: the runtime the installers copy, and not this toolkit's own SOURCE
    CHECKOUT. A checkout's .claude/ etc. are GENERATED, and a read-only bit there would break
    gen-agent-trees.py and `git checkout` -- so a checkout is never an agent root here.
    FX-H2 / F269: a checkout is a root .git whose HEAD tracks agent/ (toolkit_layout.py), not
    "has agent/": the release zip HAS agent/, and installed in place its guards went unlocked.
    M-a (v4.0.0 delta review): a .git git cannot answer for ("unknown") is treated as an
    INSTALL -- an unlocked guard is the failure this tool exists to prevent, a read-only bit
    on a checkout is visible and undone by `unlock` -- and the reason is printed, never silent."""
    if not (root / "tools" / "x4validate" / "x4validate" / "_paths.py").is_file():
        return False
    state, why = _LAYOUT.checkout_status(root)
    if state == "unknown" and str(root) not in _UNKNOWN_SAID:
        _UNKNOWN_SAID.add(str(root))
        print("NOTE: cannot tell whether %s is a source checkout or an install (%s); "
              "treating it as an INSTALL, so its guards are locked. If it is a checkout, "
              "fix git and run `unlock`." % (root, why), file=sys.stderr)
    return state != "checkout"


#: roots whose "unknown" layout has already been reported this run
_UNKNOWN_SAID: set[str] = set()


def _agent_roots(game: Path | None) -> list[Path]:
    """The folders an agent runs in, whose guards and instructions are locked: the game root,
    and -- for `install --method separate`, where the guards live in the toolkit folder and
    none in the game folder -- the INSTALLED toolkit this script ships in (v4.0.0 review: a
    separate install's guards were never locked). Deduplicated: in-game, they are one."""
    roots = [game] if game is not None else []
    tk = _HERE.parent
    if _is_installed_toolkit(tk) and not any(_same_file(tk, r) for r in roots):
        roots.append(tk)
    return roots


def _claude_definition(path: Path) -> bool:
    """A remaining toolkit hook definition, not an unrelated Claude env setting."""
    import json
    try:
        with path.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return True  # Unknown config cannot waive the older native demand.
        hooks = json.loads(raw).get("hooks", {})
        names = ("protect-bash.sh", "protect-files.sh", "x4guard.py", "x4validate-on-edit.sh",
                 "session-canary.sh", "check-reference-version.sh", "backup-before-edit.sh")
        for groups in hooks.values():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    if isinstance(command, str) and any(name in command for name in names):
                        return True
        return False
    except (OSError, ValueError, AttributeError, TypeError):
        return True


def _markers_in(root: Path) -> list[str]:
    """The agent-target markers present in `root` (none: an empty list)."""
    native = [m for m in _TARGET_DEMANDS if m != "tools/x4_harness" and (root / m).is_dir()]
    # A remaining named definition still demands that native target; the
    # neutral carrier must not hide a removed native guard directory.
    for marker, signal in ((".codex/hooks", ".codex/hooks.json"),
                           (".opencode/hooks", ".opencode/plugins/x4guard.js")):
        if marker not in native and (root / signal).is_file():
            native.append(marker)
    claude = root / ".claude/settings.json"
    if ".claude/hooks" not in native and claude.is_file() and _claude_definition(claude):
        native.append(".claude/hooks")
    if native:
        return native
    if (root / "tools/x4_harness").is_dir() and (root / ".agents").is_dir():
        return ["tools/x4_harness"]
    return []


def _demanded_targets(game: Path) -> list[str]:
    """The agent-target markers whose files `game` must have.

    Every marker directory that exists. With none present, `.claude/hooks` -- the only layout
    any release before 4.0 installed -- so a root that lost `.claude/` entirely still
    reports CLAUDE.md MISSING rather than demanding nothing. A false MISSING is visible;
    a false waiver is silent.
    """
    present = _markers_in(game)
    return present or [".claude/hooks"]


def _dedup(paths, want_file: bool) -> list[Path]:
    seen: dict[str, Path] = {}
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if p.is_file() is want_file and key not in seen:
            seen[key] = p
    return [seen[k] for k in sorted(seen)]


def manifest() -> list[Path]:
    """Every protected file that currently EXISTS, deduplicated and sorted.

    Contract unchanged: only lockable files, so a caller can iterate and chmod. What
    is absent is reported by `missing()` instead of vanishing.
    """
    return _dedup(_candidates(), want_file=True)


def missing() -> list[Path]:
    """Named protected paths that are NOT files -- deleted, renamed, or never there.

    A directory is excluded by `_GAME_GLOBS` yielding only existing files, so every
    entry here is a file the manifest expected and did not find.
    """
    return _dedup(_candidates(), want_file=False)


# ----------------------------------------------------------------- the mechanism

def state(p: Path) -> str:
    if not p.is_file():
        return "missing"
    return "unlocked" if (p.stat().st_mode & stat.S_IWRITE) else "locked"


def is_locked(p) -> bool:
    """For callers (hooks, tests) that need the state rather than the report."""
    return state(Path(p)) == "locked"


def _apply(p: Path, locked: bool) -> tuple[bool, str]:
    """Set the bit, then VERIFY by re-reading it. Never reports a lock it did not
    confirm -- `chmod` can succeed against a filesystem that does not honour the
    attribute at all, and a protection reported but absent is the worst outcome."""
    try:
        mode = p.stat().st_mode
        p.chmod(mode & ~stat.S_IWRITE if locked else mode | stat.S_IWRITE)
    except OSError as exc:
        return False, type(exc).__name__ + ": " + str(exc)
    want = "locked" if locked else "unlocked"
    got = state(p)
    if got != want:
        return False, "verification says " + got + ", not " + want
    return True, ""


# ------------------------------------------------- Layer 2 (reference/, informational)

_layer2_module = None


def _load_refguard():
    """scripts/x4refguard.py, loaded from THIS file's directory (not `_HERE`, which tests
    repoint at fake checkouts)."""
    global _layer2_module
    if _layer2_module is None:
        import importlib.util
        path = Path(__file__).resolve().parent / "x4refguard.py"
        spec = importlib.util.spec_from_file_location("x4refguard", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _layer2_module = mod
    return _layer2_module


def _layer2() -> dict:
    """The reference/ OS-protection state (x4refguard's report). May raise."""
    return _load_refguard().report(full=False)


def _print_layer2() -> None:
    """ONE informational line. It never changes x4lock's exit code -- x4doctor is the
    verdict surface for Layer 2 -- and a failure to read it is UNKNOWN, never `absent`."""
    try:
        r = _layer2()
        print("  reference deny-delete: %s -- %s" % (r.get("state"), r.get("detail", "")))
    except Exception as exc:                # noqa: BLE001 - informational line only
        print("  reference deny-delete: UNKNOWN (%s: %s)" % (type(exc).__name__, exc))


# ------------------------------------------------------------------------ commands

def cmd_status(_args) -> int:
    items = manifest()
    gone = missing()
    if not items:
        print("NOTHING PROTECTED: no roots are configured, so the manifest is empty.",
              file=sys.stderr)
        print("       Set X4_GAME / X4_MODS (see `x4validate --paths`), or list files "
              "in X4_PROTECTED.", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for p in items:
        s = state(p)
        counts[s] = counts.get(s, 0) + 1
        print("  %-9s %s" % (s, p))
    print()
    print("%d protected file(s): %s" % (
        len(items), ", ".join("%s %s" % (v, k) for k, v in sorted(counts.items()))))
    _print_layer2()
    waived = _local_env_waived()
    if waived is not None:
        # ANNOUNCED, never silent: a narrowed check says what it narrowed, and names what
        # it checks instead.
        instead = _waiver_replacements()
        points_here = any(_same_file(p, waived) for p in instead)
        others: list[Path] = []
        for p in instead:
            if not _same_file(p, waived) and not any(_same_file(p, q) for q in others):
                others.append(p)
        print("  note: this checkout is a linked git worktree, so its own %s is per-machine "
              "config it never has and is not counted MISSING; checked instead: %s%s" % (
                  waived,
                  ", ".join(str(p.resolve()) for p in others) or "this worktree's own copy",
                  " -- X4_TOOLKIT points at this worktree itself, so its absent config is "
                  "still reported MISSING" if points_here else ""))
    if gone:
        # NAMED, never merely dropped. A protected file that is GONE is the outcome
        # the read-only bit cannot prevent -- this module's own table lists `rm -f`
        # and `Remove-Item -Force` as defeating it -- so rendering its absence as a
        # smaller clean total is the one report this tool must never produce.
        print()
        print("*** %d PROTECTED FILE(S) MISSING -- expected, and not found:" % len(gone),
              file=sys.stderr)
        for gp in gone:
            print("      %s" % gp, file=sys.stderr)
        print("    A file that is gone cannot be locked, and its absence is not a "
              "smaller manifest.", file=sys.stderr)
        print("    Moved or renamed? update the roots or X4_PROTECTED. Otherwise, "
              "restore it.", file=sys.stderr)
    return 1 if (counts.get("unlocked", 0) or gone) else 0


def _run(items: list[Path], locked: bool) -> int:
    verb = "locked" if locked else "unlocked"
    done = failed = 0
    for p in items:
        ok, msg = _apply(p, locked)
        if ok:
            done += 1
            print("  %-8s %s" % (verb, p))
        else:
            failed += 1
            print("  FAILED   %s  -- %s" % (p, msg), file=sys.stderr)
    print()
    print("%d %s, %d failed, of %d" % (done, verb, failed, len(items)))
    if failed:
        print("A file this could not change is NOT in the state reported for it. Fix "
              "it, or take it out of the manifest -- never leave it counted as done.",
              file=sys.stderr)
    return 1 if failed else 0


def cmd_lock(_args) -> int:
    items = manifest()
    if not items:
        print("REFUSING: the manifest is empty, so 'locked' would mean nothing.",
              file=sys.stderr)
        return 2
    return _run(items, True)


def cmd_unlock(args) -> int:
    if args.all:
        items = manifest()
        if not items:
            print("REFUSING: the manifest is empty.", file=sys.stderr)
            return 2
        return _run(items, False)
    if not args.path:
        print("unlock needs a path, or --all. One file at a time is the point: that is "
              "the moment a human decides.", file=sys.stderr)
        return 2
    p = Path(args.path)
    if not p.is_file():
        print("no such file: " + str(p), file=sys.stderr)
        return 2
    known = {str(q.resolve()).lower() for q in manifest()}
    try:
        if str(p.resolve()).lower() not in known:
            print("note: %s is not in the protected manifest (nothing to unlock)." % p)
            return 0
    except OSError:
        pass
    return _run([p], False)


def cmd_protected(args) -> int:
    """Exit 0 when PATH is in the manifest, 1 when it is not; prints nothing.

    protect-files.sh asks this when a write targets a READ-ONLY file, to tell x4lock's
    lock apart from any other read-only file -- by THIS manifest, so the guard never
    carries a second list that could drift from it (lane N, 2026-10-03). An unresolvable
    manifest is exit 2 (see main), never a "no".
    """
    try:
        key = str(Path(args.path).resolve()).lower()
    except OSError:
        return 1
    return 0 if key in {str(q.resolve()).lower() for q in manifest()} else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="x4lock",
        description="Lock the irreplaceable files against accidental overwrite by any "
                    "process.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="list protected files and whether each is locked")
    sub.add_parser("lock", help="lock every file in the manifest")
    un = sub.add_parser("unlock", help="unlock ONE file (or --all)")
    un.add_argument("path", nargs="?")
    un.add_argument("--all", action="store_true")
    pr = sub.add_parser("protected", help="exit 0 if PATH is in the manifest, 1 if not (for the guards)")
    pr.add_argument("path")
    for name in ("lock", "unlock"):
        # FX-B2: the explicit choice when an exported root and the config disagree.
        sub.choices[name].add_argument("--game", metavar="DIR",
                                       help="the game root to act on, chosen explicitly")
        sub.choices[name].add_argument("--registry", metavar="FILE",
                                       help="the mod registry to act on, chosen explicitly")
    for p in sub.choices.values():
        p.add_argument("--toolkit", metavar="DIR",
                       help="act for this toolkit's configuration. Default: the toolkit this "
                            "script lives in; REQUIRED for lock/unlock when $X4_TOOLKIT names "
                            "a different one")
    args = ap.parse_args(argv)
    # B2 (install red-team 2026-10-04): act for the toolkit this script LIVES IN; a lock or an
    # unlock while $X4_TOOLKIT names another toolkit needs that choice made explicitly.
    if _paths is not None:
        if getattr(args, "toolkit", None):
            if not Path(args.toolkit).is_dir():
                print("REFUSING: --toolkit %s is not a directory" % args.toolkit, file=sys.stderr)
                return 2
            _paths.use_toolkit(args.toolkit)
        else:
            _paths.toolkit_notice()
            if args.cmd in ("lock", "unlock"):
                refusal = _paths.foreign_toolkit_refusal("x4lock " + args.cmd)
                if refusal:
                    print(refusal, file=sys.stderr)
                    return 2
        if getattr(args, "game", None):
            _paths.use_root("X4_GAME", args.game)
        if getattr(args, "registry", None):
            _paths.use_root("X4_REGISTRY", args.registry)
        if args.cmd in ("lock", "unlock"):
            # FX-B2: an INHERITED X4_GAME / X4_MODS outranks the acting toolkit's config, so a
            # lock could act on another install's files. A difference refuses, naming both.
            # FX-B3: an inherited X4_CONFIG naming another toolkit's config (or a missing
            # file) refuses too -- and no --game/--registry lifts it: the manifest also locks
            # the config file in play, which X4_CONFIG alone chooses.
            refusal = _paths.env_root_refusal("x4lock " + args.cmd,
                                              {"game_root": "--game", "registry": "--registry"},
                                              "game_root", "registry",
                                              config_lifted_by_flags=False)
            if refusal:
                print(refusal, file=sys.stderr)
                return 2
        elif args.cmd == "status":      # not `protected`: a guard calls it, and reads stderr
            _paths.env_root_notice("game_root", "registry")
    try:
        if args.cmd == "protected":
            return cmd_protected(args)
        if args.cmd == "lock":
            return cmd_lock(args)
        if args.cmd == "unlock":
            return cmd_unlock(args)
        if args.cmd == "status":
            return cmd_status(args)
    except Unresolvable as exc:
        # "could not look" is never "nothing to protect".
        print("REFUSING: %s" % exc, file=sys.stderr)
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

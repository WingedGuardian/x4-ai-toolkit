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

THE GUARDED UPDATE (4.3.1 task 6c; second half of this file). `policy(root)` is the core's `UpdatePolicy`, built from
the INSTALLED toolkit only, never from the downloaded release. Its guard paths are DERIVED, not hand-listed: the
deployer's `GUARD_SOURCES` and the generator's guard-shaped `OWNED` entries, read from the install's own copies of
`tools/x4validate/scripts/deploy-claude-dir.py` and `gen-agent-trees.py` by parsing the assignments (never importing
them). Both ship in every install (install.sh copies `tools` whole; neither is export-ignored), and an install has no
agent/ and no pre-push gate, so these two are the derivable lists an install carries. Either one missing or not a plain
literal makes the policy raise, so staging refuses (fail closed). `handle` adds a pre-check: the installed canary must
pass before staging, and the installed setup and canary before applying, or the command refuses with "fix x4doctor
first". Without it an install that is already unhealthy would apply, fail, roll back, fail the old canary too and lock
itself with update-failed.json.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
import save_policy

ROOT = Path(__file__).resolve().parents[2]
release = save_policy.core(ROOT, "release")
guarded = save_policy.core(ROOT, "guarded_update")
update_review = save_policy.core(ROOT, "update_review")

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


# --- the guarded update (4.3.1 task 6c) ---------------------------------------------------------------------------

DEPLOYER = "tools/x4validate/scripts/deploy-claude-dir.py"
GENERATOR = "tools/x4validate/scripts/gen-agent-trees.py"
#: Guard paths neither derivation source names. `.codex/hooks.json` and `.opencode/opencode.jsonc` are the hook
#: settings rendered per install; agent/targets/ and agent/rules/ are the per-agent hook definitions and the Codex
#: rules source a release zip carries. The rest are the pre-push gate's guard files that ship: scripts/prepush-gate.py
#: is export-ignored, so an install cannot read its GUARD_PATHS, and a checkout test binds this tuple to that list.
#: (.claude/settings.local.json.example is the seed setup.sh copies into the live settings.local.json: permission rules.)
EXTRA_GUARD = (".codex/hooks.json", ".opencode/opencode.jsonc", "agent/targets/", "agent/rules/",
               ".claude/settings.local.json.example",
               "scripts/verify-hook-tests.py", "scripts/x4canary.py", "scripts/x4deploydrift.py",
               "scripts/x4conformance.py", "tools/modding-harness.lock.json",
               "tools/x4validate/scripts/next-blind-spot-id.py")
#: What defines setup and the canary (and where the guard list comes from). Only the ones present on the install count.
POLICY_FILES = ("setup.sh", "scripts/x4doctor.py", "scripts/gitbash.py", "tools/agent-harness.py",
                "tools/x4_harness/x4_update.py", "tools/x4_harness/x4guard.py", DEPLOYER, GENERATOR)
#: The core's `stage_update` takes no `local_files`, only notes, so the per-machine path config (passed to the
#: upgrade-* commands as local_files) is named here. As a notes file it is observed, never overwritten and never
#: selectable as `candidate`: the protection the upgrade-* path gives it.
NOTES_FILES = ("X4-NOTES.md", "x4-paths.env", ".claude/x4-paths.env")
GENERATED_PATHS = (".codex/hooks.json",)
CANARY = ("scripts/x4doctor.py", "--update-canary")
#: setup.sh roots itself at $CLAUDE_PROJECT_DIR when that is set, and an agent session sets it to ITS project, which is
#: not always the install. The core runs every step with the install as its working directory, so the variable is
#: dropped and setup.sh falls back to that. `$BASH` is the bash running this line, never a PATH lookup.
SETUP_SCRIPT = 'unset CLAUDE_PROJECT_DIR; exec "$BASH" ./setup.sh'
MAX_SOURCE = 4 * 1024 * 1024
_OWNED_GUARD_NAMES = (".opencode/plugins/x4guard.js", "tools/x4_harness", "tools/agent-harness.py")


def _refuse(message):
    return guarded.UpdateRefused(message + "; nothing was changed")


def _literal(root, rel, name):
    """The tuple of strings `name` holds in the install's copy of `rel`, read from its assignments (never imported).

    One top-level `name = (...)` (or `name: T = (...)`), then any number of top-level `name += (...)`, each a plain
    literal tuple of strings. Anything else that stores to the name (a second `=`, a computed value, an assignment in a
    function or a branch, a `global` declaration) makes the answer unknowable from the text, so it refuses."""
    path = Path(root) / rel
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE + 1)
    except OSError as exc:
        raise _refuse("the guard paths cannot be derived: {} cannot be read ({})".format(rel, type(exc).__name__))
    if len(raw) > MAX_SOURCE:
        raise _refuse("the guard paths cannot be derived: {} is larger than {} bytes".format(rel, MAX_SOURCE))
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=rel)
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise _refuse("the guard paths cannot be derived: {} does not parse ({})".format(rel, type(exc).__name__))
    allowed, value = set(), None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        where = "{}:{}".format(rel, node.lineno)
        if (len(targets) != 1 or node.value is None
                or (isinstance(node, ast.AugAssign) and not isinstance(node.op, ast.Add))):
            raise _refuse("the guard paths cannot be derived: {} is not a plain assignment of {}".format(where, name))
        allowed.add(id(targets[0]))
        try:
            literal = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise _refuse("the guard paths cannot be derived: {} assigns {} a value that is not a literal".format(
                where, name))
        if not isinstance(literal, tuple) or not all(isinstance(entry, str) and entry for entry in literal):
            raise _refuse("the guard paths cannot be derived: {} assigns {} something other than a tuple of path "
                          "strings".format(where, name))
        if isinstance(node, ast.AugAssign):
            if value is None:
                raise _refuse("the guard paths cannot be derived: {} extends {} before assigning it".format(where, name))
            value += literal
        elif value is not None:
            raise _refuse("the guard paths cannot be derived: {} assigns {} a second time".format(where, name))
        else:
            value = literal
    for node in ast.walk(tree):
        stored = isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load)
        declared = isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names
        if (stored and id(node) not in allowed) or declared:
            raise _refuse("the guard paths cannot be derived: {}:{} changes {} somewhere other than a top-level "
                          "assignment".format(rel, getattr(node, "lineno", "?"), name))
    if not value:
        raise _refuse("the guard paths cannot be derived: {} assigns no {} (or an empty one)".format(rel, name))
    return value


def owned_is_guard(entry):
    """Is a generator OWNED entry guard code: a hook tree, a rules tree, a hook settings file, the OpenCode guard plugin,
    or the harness (tools/x4_harness/, tools/agent-harness.py)? The instruction files and skill/subagent trees are not."""
    bare = entry.rstrip("/")
    parts = PurePosixPath(bare).parts
    return bool(parts) and ("hooks" in parts or "rules" in parts or parts[-1] == "settings.json"
                            or bare in _OWNED_GUARD_NAMES)


def _globs(root, entries):
    """Each entry as the core's fnmatch globs: a directory is itself and everything under it (`x`, `x/*`), a file is
    itself. An entry this install lacks (agent/ is not installed) is a file when its name has a suffix, else both."""
    out = []
    for entry in entries:
        bare = entry.rstrip("/")
        path = Path(root) / bare
        if entry.endswith("/") or path.is_dir():
            forms = (bare, bare + "/*")
        elif path.is_file() or PurePosixPath(bare).suffix:
            forms = (bare,)
        else:
            forms = (bare, bare + "/*")
        out += [form for form in forms if form not in out]
    return tuple(out)


def _rendered_shell(root, folder):
    """'powershell' or 'bash', as the installer rendered the toolkit token into this install's `folder` skills, or None
    when no installed skill says. Both spellings at once is ambiguous and refuses: the review would render one wrongly.
    (MEASURED 2026-11-27: no skill source under agent/skills spells either one literally; only the token renders them.)"""
    found = {}
    base = Path(root) / folder
    if base.is_dir():
        for path in sorted(base.rglob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                data = stream.read(1024 * 1024)
            for token, shell in ((b"$env:X4_TOOLKIT", "powershell"), (b"$X4_TOOLKIT", "bash")):
                if token in data:
                    found.setdefault(shell, path.relative_to(root).as_posix())
    if len(found) > 1:
        raise _refuse("the installed {} skills name the toolkit both ways ({} and {}), so which shell they were rendered "
                      "for cannot be told".format(folder, found["powershell"], found["bash"]))
    return next(iter(found), None)


def render_config(root):
    """The render configuration for this install (x4_upgrade_render's {"v", "agent_shell", "opencode_shell"}), written
    beside the update downloads, outside the install. Each shell is read from the installed skills, so a review renders
    the new skills the way this install's were; with no rendered skill, the installers' own rule (install.sh
    `_toolkit_render_value`): PowerShell on Windows, bash elsewhere, and bash for OpenCode when X4_OPENCODE_SHELL=bash."""
    root = Path(root).resolve()
    windows = os.name == "nt"
    agent = _rendered_shell(root, ".agents/skills") or ("powershell" if windows else "bash")
    opencode = _rendered_shell(root, ".opencode/skills") or (
        "powershell" if windows and os.environ.get("X4_OPENCODE_SHELL") != "bash" else "bash")
    home = release.updates_home(root)
    home.mkdir(parents=True, exist_ok=True)
    path = home / "render-config.json"
    path.write_bytes((json.dumps({"v": 1, "agent_shell": agent, "opencode_shell": opencode}, sort_keys=True)
                      + "\n").encode("utf-8"))
    return path


def policy(root):
    """The core's `UpdatePolicy` for the install at `root`, from that install's own files. Raises `UpdateRefused` when a
    derivation source cannot be read as a plain literal, when the generator names no guard entry, or when there is no
    Git Bash to run setup.sh with."""
    import x4guard
    root = Path(root).resolve()
    sources = _literal(root, DEPLOYER, "GUARD_SOURCES")
    owned = [entry for entry in _literal(root, GENERATOR, "OWNED") if owned_is_guard(entry)]
    if not owned:
        raise _refuse("the guard paths cannot be derived: {} OWNED names no hook, rules, settings or harness "
                      "entry".format(GENERATOR))
    bash, why = x4guard.resolve_bash()
    if not bash:
        raise _refuse("no Git Bash to run setup.sh with: {}".format(why))
    try:
        return update_review.UpdatePolicy(
            guard_paths=_globs(root, (*sources, *owned, *EXTRA_GUARD)),
            policy_files=tuple(name for name in POLICY_FILES if (root / name).is_file()),
            setup=(bash, "-c", SETUP_SCRIPT), canary=(sys.executable, "-I", *CANARY), render_config=render_config)
    except ValueError as exc:
        raise _refuse("the guard paths derived from {} and {} are not usable: {}".format(DEPLOYER, GENERATOR, exc))


def _lines(data):
    if data is None:
        return []
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return text.splitlines()[-60:]


def precheck(root, pol, steps, run=None):
    """Run the INSTALLED `steps` ("setup", "canary") on the install as it is; refuse at the first that fails.

    After a rollback the core runs the installed setup and canary again and writes update-failed.json when either
    fails, so an install that already fails one would lock itself on any failed update. This refuses instead, before
    anything is staged or replaced: "fix x4doctor first" naming the FAIL rows, or "fix setup.sh first"."""
    import subprocess
    runner = guarded.run if run is None else run
    for label in steps:
        argv = pol.setup if label == "setup" else pol.canary
        try:
            done = runner(argv, root, guarded.TIMEOUT)
            ok, status = done.returncode == 0, "exit {}".format(done.returncode)
            out = _lines(done.stdout) + _lines(done.stderr)
        except subprocess.TimeoutExpired as exc:
            ok, status, out = False, "timed out after {} s".format(exc.timeout), _lines(exc.output) + _lines(exc.stderr)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            ok, status, out = False, "could not start: {}: {}".format(type(exc).__name__, exc), []
        if ok:
            continue
        rows = [line.strip() for line in out if line.split()[:1] == ["FAIL"]]
        detail = "; ".join(rows) if rows else (" | ".join(out[-5:]) or "(no output)")
        if label == "canary":
            raise guarded.UpdateRefused(
                "fix x4doctor first: this install fails its update canary as it is ({}): {}. Run `python "
                "scripts/x4doctor.py` in {} and fix every FAIL row, then try again. An install that is already unhealthy "
                "is never updated, because a failed update could then not prove its rollback; the update changed "
                "nothing".format(status, detail, root))
        raise guarded.UpdateRefused(
            "fix setup.sh first: the installed setup fails on this install as it is ({}): {}. Run `bash setup.sh` in {} "
            "and fix what it reports, then try again; the update changed nothing".format(status, detail, root))


def guarded_handle(args, root, *, candidate_preparer=None, candidate_validator=None, run=None):
    """One parsed `update-stage` / `update-apply` / `update-clear-failed` (the harness entry's dispatch), through the
    core's `guarded_update.handle` with this install's policy, notes files and renderer, after the pre-check.

    `update-clear-failed` builds no policy: the markers must be clearable even when the guard sources are what broke.
    The pre-check is skipped while a failed or in-flight marker exists, because the core refuses those first."""
    installed = Path(args.installed_root or root).resolve()
    if args.command == "update-clear-failed":
        return guarded.handle(args, root, PROFILE, None, notes_files=NOTES_FILES)
    if args.command == "update-stage" and installed_version(installed) is None:
        raise _refuse("there is no installed release to update here (a git checkout, or an install with neither "
                      "BUILD-INFO nor a release heading in CHANGELOG.md); a checkout is updated with git")
    pol = policy(installed)
    if not any(os.path.lexists(installed / name) for name in (guarded.FAILED_MARKER, guarded.IN_FLIGHT)):
        precheck(installed, pol, ("canary",) if args.command == "update-stage" else ("setup", "canary"), run=run)
    # new_policy: the candidate's setup and canary are the same commands at the same paths, which after apply run the
    # NEW setup.sh and x4doctor.py. The core's "old canary" step after a policy-file change runs that same path, so it
    # is a second run of the new canary, not of the old bytes; the old canary does run, on the restored files, after a
    # rollback.
    return guarded.handle(args, root, PROFILE, pol, notes_files=NOTES_FILES, candidate_preparer=candidate_preparer,
                          candidate_validator=candidate_validator, generated_paths=GENERATED_PATHS, new_policy=pol)

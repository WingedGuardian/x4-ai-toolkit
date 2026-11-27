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
literal makes the policy raise, so staging refuses (fail closed). The installed canary must pass before staging and
applying. Setup may write files, so only the core runs it, after authorizing and publishing the update or after
restoring the previous version during rollback.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
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
               "scripts/verify-hook-tests.py", "scripts/x4canary.py", "scripts/x4deploydrift.py", "scripts/x4refguard.py",
               "scripts/x4conformance.py", "tools/modding-harness.lock.json",
               "tools/x4validate/scripts/next-blind-spot-id.py",
               # run by session-canary.sh; it prints the failed/in-flight update warning (6c review F6)
               "scripts/x4update_session.py")
#: What defines setup and the canary (and where the guard list comes from). Only the ones present on the install count.
POLICY_FILES = ("setup.sh", "scripts/x4doctor.py", "scripts/gitbash.py", "tools/agent-harness.py",
                "tools/x4_harness/x4_update.py", "tools/x4_harness/x4guard.py", DEPLOYER, GENERATOR)
#: The core's `stage_update` takes no `local_files`, only notes, so the per-machine path config (passed to the
#: upgrade-* commands as local_files) is named here. As a notes file it is observed, never overwritten and never
#: selectable as `candidate`: the protection the upgrade-* path gives it.
NOTES_FILES = ("X4-NOTES.md", "x4-paths.env", ".claude/x4-paths.env")
GENERATED_PATHS = (".codex/hooks.json",)
CANARY = ("scripts/x4doctor.py", "--update-canary")
#: The update's setup step, run by Git Bash with the install as its working directory and this Python as $1.
#: - setup.sh roots itself at $CLAUDE_PROJECT_DIR when that is set, and an agent session sets it to ITS project, which
#:   is not always the install: the variable is dropped, so setup.sh falls back to the working directory.
#: - UV_FROZEN=1: setup.sh's `uv sync` must never rewrite uv.lock (a published file: a rollback would then refuse it).
#: - X4_SETUP_KEEP_LOCAL=1: leave existing and intentionally absent local settings and path config alone.
#: - OpenCode's deny rules (.opencode/opencode.jsonc) are per-machine and never in a release, so nothing else on the
#:   update path renders them, and a release that changes them would leave them stale (x4doctor's opencode.config FAILs).
#:   For an install that HAS OpenCode (its plugin, x4doctor's own target test) they are re-rendered the way install.sh's
#:   write_opencode_config does: X4_TOOLKIT=<root> python .opencode/hooks/opencode_config.py write --root <root>. After
#:   a rollback the installed setup runs again, so the restored renderer writes them back.
#: `$BASH` is the bash running this script, never a PATH lookup; `pwd -W` is the Windows spelling of the root in Git Bash.
SETUP_SCRIPT = ('unset CLAUDE_PROJECT_DIR; export UV_FROZEN=1 X4_SETUP_KEEP_LOCAL=1\n'
                '"$BASH" ./setup.sh || exit $?\n'
                'if [ -f .opencode/plugins/x4guard.js ]; then\n'
                '  _root="$(pwd -W 2>/dev/null || pwd)"\n'
                '  X4_TOOLKIT="$_root" "$1" -B .opencode/hooks/opencode_config.py write --root "$_root" || exit $?\n'
                'fi\n')
#: The first line of every apply report: Codex runs a changed hook definition only after the user re-approves it.
CODEX_LINE = ("Codex users: re-approve the changed hooks with /hooks in Codex now. Codex silently skips every changed "
              "hook until you do, so its guards are off for those hooks.")
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
    """Each entry as the core's fnmatch globs: a directory is everything under it (`x/*`; fnmatch's `*` crosses `/`),
    a file is itself. An entry `root` lacks is a file when its name has a suffix, else a directory."""
    out = []
    for entry in entries:
        bare = entry.rstrip("/")
        path = Path(root) / bare
        if entry.endswith("/") or path.is_dir() or not (path.is_file() or PurePosixPath(bare).suffix):
            form = bare + "/*"
        else:
            form = bare
        if form not in out:
            out.append(form)
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
            setup=(bash, "-c", SETUP_SCRIPT, "x4-update-setup", sys.executable),
            canary=(sys.executable, "-I", *CANARY), render_config=render_config)
    except ValueError as exc:
        raise _refuse("the guard paths derived from {} and {} are not usable: {}".format(DEPLOYER, GENERATOR, exc))


def _lines(data):
    if data is None:
        return []
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return text.splitlines()[-60:]


def precheck(root, pol, run=None):
    """Verify that the installed canary can certify the previous version if a rollback is needed."""
    import subprocess
    runner = guarded.run if run is None else run
    try:
        done = runner(pol.canary, root, guarded.TIMEOUT)
        if done.returncode == 0:
            return
        status = "exit {}".format(done.returncode)
        out = _lines(done.stdout) + _lines(done.stderr)
    except subprocess.TimeoutExpired as exc:
        status = "timed out after {} s".format(exc.timeout)
        out = _lines(exc.output) + _lines(exc.stderr)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        status, out = "could not start: {}: {}".format(type(exc).__name__, exc), []
    rows = [line.strip() for line in out if line.split()[:1] == ["FAIL"]]
    detail = "; ".join(rows) if rows else (" | ".join(out[-5:]) or "(no output)")
    raise _refuse(
        "fix x4doctor first: this install fails its update canary as it is ({}): {}. Run "
        "`python scripts/x4doctor.py` in {} and fix every FAIL row, then try again".format(status, detail, root))


#: install.sh's own lists, read from the RELEASE's install.sh (what its installer would copy). install.ps1 holds the
#: same lists and test_installers_agree ties the two.
_SH_LISTS = ("X4_COPY_ITEMS", "X4_AGENT_NAMES", "X4_COPY_PRUNE", "X4_KEEP_LOCAL")


def _sh_list(text, name):
    found = re.findall(r'^{}="([^"$`\\]*)"[ \t]*$'.format(re.escape(name)), text, re.M)
    if len(found) != 1:
        raise _refuse("the release's install.sh names {} {} times, not once, so what an install holds cannot be "
                      "read from it".format(name, len(found)))
    items = found[0].split()
    for item in items:
        if item.startswith("/") or "\\" in item or ".." in item.split("/") or re.match(r"[A-Za-z]:", item):
            raise _refuse("the release's install.sh lists an unsafe path in {}: {!r}".format(name, item))
    return items


def install_selection(source):
    """(common items, {agent: items}, never-copied paths) from the release's install.sh, as its installer reads them:
    X4_COPY_ITEMS, X4_AGENT_ITEMS_<name> for each X4_AGENT_NAMES entry, and X4_COPY_PRUNE + X4_KEEP_LOCAL."""
    path = Path(source) / "install.sh"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE + 1)
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse("the release has no readable install.sh ({}), so what an install holds cannot be "
                      "derived".format(type(exc).__name__))
    if len(raw) > MAX_SOURCE:
        raise _refuse("the release's install.sh is larger than {} bytes".format(MAX_SOURCE))
    text = text.replace("\r\n", "\n")
    items = _sh_list(text, "X4_COPY_ITEMS")
    agents = {name: _sh_list(text, "X4_AGENT_ITEMS_" + name) for name in _sh_list(text, "X4_AGENT_NAMES")}
    skip = _sh_list(text, "X4_COPY_PRUNE") + _sh_list(text, "X4_KEEP_LOCAL")
    if not items or not agents:
        raise _refuse("the release's install.sh lists no common or agent items")
    return items, agents, skip


def installed_agents(root):
    """The agent targets this install has, by x4doctor's own test (`detect_targets`, read from the install's
    scripts/x4doctor.py): the same answer the canary uses to decide which targets it judges."""
    import importlib.util
    path = Path(root) / "scripts" / "x4doctor.py"
    if not path.is_file():
        raise _refuse("scripts/x4doctor.py is missing, so the agents this install holds cannot be told")
    name = "x4doctor_for_update_" + str(abs(hash(str(path))))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        found = module.detect_targets(Path(root))
    except Exception as exc:
        raise _refuse("scripts/x4doctor.py could not tell which agents this install holds ({}: {})".format(
            type(exc).__name__, exc))
    finally:
        sys.modules.pop(name, None)
    return sorted(agent for agent, present in found.items() if present)


def _under(name, prefixes):
    return any(name == prefix or name.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


def selection(root, source):
    """(wanted items, never-copied paths, agents): what install.sh would install into THIS install from `source`, its
    common items plus the items of the agents the install already has. agent/, docs/ and .github/ are in no list."""
    items, agent_items, skip = install_selection(source)
    agents = installed_agents(root)
    unknown = [agent for agent in agents if agent not in agent_items]
    if unknown:
        raise _refuse("this install has agent target(s) the release's installer does not know: {}".format(
            ", ".join(unknown)))
    wanted = list(dict.fromkeys([*items, *(item for agent in agents for item in agent_items[agent])]))
    return wanted, skip, agents


def select_files(inventory, wanted, skip):
    """The part of a {path: sha256} inventory install.sh would copy: under a wanted item, not under a never-copied path."""
    return {name: digest for name, digest in inventory.items() if _under(name, wanted) and not _under(name, skip)}


def review_policy(root, source, wanted=None):
    """The installed policy, its guard globs WIDENED by the release's own deployer and generator (a hook folder that is
    new in the release is then guard code too) and narrowed to the items an update can write here (`wanted`), so a
    glob for a path this install never receives does not print a "matched no path" warning. Only the INSTALLED
    sources can refuse: a release whose lists cannot be read widens nothing."""
    import dataclasses
    pol = policy(root)
    globs = list(pol.guard_paths)
    try:
        sources = _literal(source, DEPLOYER, "GUARD_SOURCES")
        owned = [entry for entry in _literal(source, GENERATOR, "OWNED") if owned_is_guard(entry)]
        globs += [glob for glob in _globs(source, (*sources, *owned, *EXTRA_GUARD)) if glob not in globs]
    except guarded.UpdateRefused:
        pass
    if wanted is not None:
        tops = {item.split("/")[0].casefold() for item in wanted}
        globs = [glob for glob in globs if glob.split("/")[0].casefold() in tops]
    return dataclasses.replace(pol, guard_paths=tuple(globs) or pol.guard_paths)


def stage(root, *, candidate_preparer=None, http=None):
    """`guarded_update.stage_update`, with the candidate cut down to what install.sh would install here (6c review F1,
    F2): the core stages the whole zip, so a Claude-only install would gain the OpenCode plugin (x4doctor then judges
    an OpenCode target with no deny rules and the canary FAILs: every update rolls back), and every install would gain
    agent/, docs/ and .github/ as dozens of new "guard" diffs. The renderer cannot drop files (stage_review_bundle
    accepts bytes only for inventoried or generated names), so the inventories are filtered before staging.

    Everything else is the core's stage_update, step for step, and it writes the same meta.json; apply_update reads
    only the bundle and meta.json, so the core applies an X4-staged bundle unchanged."""
    root = Path(root).resolve()
    if os.path.lexists(root / guarded.FAILED_MARKER):
        raise guarded.UpdateRefused("an earlier update failed and left {}; read it, fix the cause and clear it before "
                                    "staging another update".format(guarded.FAILED_MARKER))
    guarded._refuse_in_flight(root)
    installation = save_policy.core(ROOT, "installation")
    upgrade = save_policy.core(ROOT, "upgrade")
    try:
        notes = installation.notes_set(NOTES_FILES)
        fetched = release.fetch(root, PROFILE, **({} if http is None else {"http": http}))
        source, bundle = Path(fetched["source"]), Path(fetched["bundle"])
        guarded._discard_earlier_review(bundle.parent)
        wanted, skip, agents = selection(root, source)
        candidate = guarded._read_json(fetched["candidate"])
        previous = guarded._read_json(fetched["previous"]) if fetched["previous"] else None
        kept = select_files(candidate["files"], wanted, skip)
        dropped = sorted(set(candidate["files"]) - set(kept))
        pol = review_policy(root, source, wanted)
        transform = None
        if candidate_preparer is not None:
            config = guarded._read_json(pol.render_config(root))
            transform = lambda installed, staged, files: candidate_preparer(installed, staged, files, config)
        upgrade.stage_review_bundle(
            root, source, bundle, {"v": 1, "files": kept},
            None if previous is None else {"v": 1, "files": select_files(previous["files"], wanted, skip)},
            local_files=tuple(sorted(notes)), candidate_transform=transform,
            generated_paths=GENERATED_PATHS if transform is not None else (),
            # The core's default is 60 s; staging copies every selected file twice (candidate and rollback), about
            # 2,500 copies for an all-agent install, which ran past 60 s once under test load (6c review rerun).
            deadline=time.monotonic() + guarded.TIMEOUT)
        import hashlib
        review_sha256 = hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest()
        text, requires_accept = update_review.render_review(bundle=bundle, installed_root=root, source=source,
                                                            policy=pol, review_sha256=review_sha256)
        meta = {"requires_guard_accept": requires_accept, "review_sha256": review_sha256,
                "from_version": str(fetched["installed"]), "to_version": str(fetched["version"])}
        guarded._write_meta(bundle.parent / guarded.META, meta)
    except guarded.UpdateRefused:
        raise
    except ValueError as exc:
        raise guarded.UpdateRefused(str(exc)) from exc
    groups = {}
    for name in dropped:
        top = name.split("/")[0]
        groups[top] = groups.get(top, 0) + 1
    note = ("Not part of this install, so not written: {} file(s) of the release ({}). This install holds: {} "
            "(the installer's own lists, from the release's install.sh).".format(
                len(dropped), ", ".join("{} {}".format(top, n) for top, n in sorted(groups.items())) or "none",
                ", ".join(agents) or "no agent target"))
    return {"bundle": str(bundle), "review_sha256": review_sha256, "requires_guard_accept": requires_accept,
            "from_version": meta["from_version"], "to_version": meta["to_version"], "application_authorized": False,
            "agents": agents, "not_installed": len(dropped), "review": note + "\n\n" + text}


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
        precheck(installed, pol, run=run)
    if args.command == "update-stage":
        return stage(installed, candidate_preparer=candidate_preparer)
    # The apply re-derives the guard decision from complete.json with this policy, so it must be the one the review was
    # rendered with: the same widening from the release unpacked beside the bundle, the same narrowing to this install.
    source = Path(args.bundle).resolve().parent / guarded.SOURCE
    if not source.is_dir():
        raise _refuse("the release the review was staged from is gone ({}); stage the update again".format(source))
    wanted, _skip, _agents = selection(installed, source)
    pol = review_policy(installed, source, wanted)
    # new_policy: the candidate's setup and canary are the same commands at the same paths, which after apply run the
    # NEW setup.sh and x4doctor.py. The core's "old canary" step after a policy-file change runs that same path, so it
    # is a second run of the new canary, not of the old bytes; the old canary does run, on the restored files, after a
    # rollback.
    result = guarded.handle(args, root, PROFILE, pol, notes_files=NOTES_FILES, candidate_preparer=candidate_preparer,
                            candidate_validator=candidate_validator, generated_paths=GENERATED_PATHS, new_policy=pol)
    if isinstance(result, dict) and isinstance(result.get("report"), str):
        result["report"] = CODEX_LINE + "\n" + result["report"]
    return result

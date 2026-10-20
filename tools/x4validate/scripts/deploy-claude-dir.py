#!/usr/bin/env python
"""Deploy the repo's `.claude/` into the configured game root. One direction, dry-run first.

WHY THIS EXISTS. The repo is the source of every hook, skill, agent and setting; a game
root's `.claude/` is a deployed copy. Editing both by hand is how they drifted (five files,
three directions, measured 2026-09-13 -- see `gates/deploy_parity.py`). This is the
"synchronous write" as ONE command: edit in the repo, then run this.

    uv run python scripts/deploy-claude-dir.py                 # dry run: what would change
    uv run python scripts/deploy-claude-dir.py --apply         # do it
    uv run python scripts/deploy-claude-dir.py --apply --force skills/x/SKILL.md
    uv run python scripts/deploy-claude-dir.py --target codex      # the Codex tree (dry run)
    uv run python scripts/deploy-claude-dir.py --target all --apply

THE CODEX TARGET (F11, 2026-10-20). MEASURED that day: this script deployed `.claude/` only,
so the game root it served had AGENTS.md (which steers Codex) and no `.codex/` -- every Codex
session there ran with NO guard hooks. `--target codex` deploys what install.sh's
X4_AGENT_ITEMS_codex would: `.codex/hooks/`, `.codex/rules/`, `.agents/skills/` (the
`{{TOOLKIT}}` token rendered per OS, as the installer does: `$env:X4_TOOLKIT` on Windows) and
`.codex/hooks.json` RENDERED for the destination root from the shipped template. hooks.json
equal as canonical JSON is left byte-identical, because Codex SKIPS a changed hooks.json until
the user re-approves it in /hooks; when this deploy writes it, the output says so. NOT
deployed: `.codex/config.toml` (per-machine) and AGENTS.md (owned by the game-root repository,
which may append its own section) -- the output reports whether AGENTS.md still holds the
toolkit's current text and its size against Codex's 32,768-byte default budget.

WHAT IT WILL NOT DO:

* OVERWRITE AN EDIT THAT WAS NEVER PORTED BACK. A destination file is replaced only if its
  content matches the repo's current file or ANY version of it the repo ever committed
  (either spelling of the installer rewrite). Anything else is a change somebody made in
  the deployed copy, and replacing it would destroy work -- `x4-probe` in the game root
  carried a whole rung of procedure the repo never had. It is REFUSED with the reason;
  port it to the repo first, or `--force <name>` that one file.
* DELETE. A file only in the game root is reported, never removed.
* TOUCH a per-machine file. The population is `deploy_parity.population()` -- the
  deployment as the game-root repository whitelists it -- so `x4-paths.env`,
  `settings.local.json` and `backups/` are outside it by construction. ONE exception: a
  3.x path config at `<game>/.claude/x4-paths.env` is MOVED (bytes and x4lock lock kept)
  to `<game>/x4-paths.env`, where 4.x reads it -- never edited. Two copies that DIFFER are
  a CONFLICT: neither is touched and the run exits 1 (Plan 3 lane I;
  `_paths.migrate_legacy_config`).

HOW IT WRITES, each measured as a failure mode somewhere in this workspace:

* the installer rewrite (`$CLAUDE_PROJECT_DIR` -> `$X4_TOOLKIT`) is applied when the
  destination does NOT contain `tools/x4validate` -- install.sh's rule, and the reason the
  game root's five path-carrying skills pointed at a directory that does not exist there;
* the destination file's line endings are kept (a CRLF file stays CRLF): flipping every
  line ending for a one-line change is an unreviewable diff;
* an x4lock'd (read-only) file is unlocked, written, re-read and RE-LOCKED, with the lock
  verified through `x4lock._apply`, which never reports a lock it did not confirm;
* content is encoded before any write, so a failed encode cannot truncate.

Exit: 0 nothing refused (dry run) / everything applied - 1 at least one file refused or
failed, named - 2 could not run (no game root, or the destination IS the repo).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PKG = Path(__file__).resolve().parents[1]          # tools/x4validate
REPO_ROOT = PKG.parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # REGISTER BEFORE EXECUTING. `@dataclass` under `from __future__ import annotations`
    # looks its own module up in sys.modules while the class is being built; an
    # unregistered module is None there and the import dies with an AttributeError.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


parity = _load("deploy_parity_for_deploy", PKG / "gates" / "deploy_parity.py")
x4lock = _load("x4lock_for_deploy", REPO_ROOT / "scripts" / "x4lock.py")

CREATE, UPDATE, SKIP, REFUSE, REPORT = "create", "update", "identical", "REFUSED", "only in game root"
#: A game root's 3.x `.claude/x4-paths.env` moved to the root (Plan 3 lane I).
MIGRATE = "migrate (3.x config)"
#: Two path configs that DIFFER: neither is touched, and the run exits 1 (v4.0.0 review R4-9:
#: it was a REPORT with rc 0, so a scripted deploy read "done" beside two configs that protect
#: different trees).
CONFLICT = "CONFLICT (two configs)"


@dataclass
class Action:
    name: str
    kind: str
    reason: str = ""


def _norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def _rewrite(b: bytes) -> bytes:
    return b.replace(parity.REWRITE_FROM, parity.REWRITE_TO)


def _is_crlf(b: bytes) -> bool:
    return b.count(b"\r\n") * 2 > b.count(b"\n")


def needs_rewrite(dest_claude: Path) -> bool:
    """install.sh's rule: the toolkit is NOT inside the directory the skills run from."""
    return not (dest_claude.parent / "tools" / "x4validate").is_dir()


def committed_versions(repo_root: Path, rel: str) -> list[bytes]:
    """Every committed content of `rel` (repo-root-relative), normalised. [] without git --
    which only makes the refusal STRICTER, never looser."""
    try:
        revs = subprocess.run(["git", "-C", str(repo_root), "rev-list", "HEAD", "--", rel],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []  # silent-ok: no git means fewer known versions, so MORE refusals
    out: list[bytes] = []
    for rev in revs.stdout.split()[:500]:
        blob = subprocess.run(["git", "-C", str(repo_root), "show", f"{rev}:{rel}"],
                              capture_output=True, timeout=60)
        if blob.returncode == 0:
            out.append(_norm(blob.stdout))
    return out


def rewrites(dest_claude: Path, name: str) -> bool:
    """Only skills and agents, and only when the toolkit is not beside the destination --
    exactly install.sh. Hooks and settings.json keep `$CLAUDE_PROJECT_DIR`, which is what
    points a session at its OWN deployed hooks (see `deploy_parity.REWRITE_SCOPE`)."""
    return needs_rewrite(dest_claude) and parity.in_rewrite_scope(name)


def codex_token_value() -> bytes:
    """install.sh's `_toolkit_render_value` for `.agents/`: Codex runs PowerShell on Windows,
    where `$X4_TOOLKIT` expands to EMPTY (MEASURED, Plan 2 lane A)."""
    return parity.TOOLKIT_RENDER_WINDOWS if os.name == "nt" else parity.TOOLKIT_RENDER_POSIX


def _rewrite_to(spec, dest: Path, name: str) -> bytes | None:
    """The token value `name` is rendered to in `dest`, or None for no rewrite. Claude: the
    installer rewrite, only when the toolkit is not beside the destination. Codex: ALWAYS
    (install.sh renders `.agents/` on every copy; only a source checkout keeps the token)."""
    if not parity.in_rewrite_scope(name, spec):
        return None
    if spec.name == "claude":
        return parity.REWRITE_TO if needs_rewrite(dest) else None
    return codex_token_value()


def _git_rel(spec, name: str) -> str:
    return PurePosixPath(spec.root_rel, name).as_posix()


def plan(repo_root: Path, dest_claude: Path, force: set[str] | frozenset = frozenset(),
         spec=None) -> list[Action]:
    """`dest_claude` is the spec's deployed directory: `<root>/.claude` for claude, the
    root itself for codex (whose names carry `.codex/` and `.agents/`)."""
    spec = spec or parity.CLAUDE
    repo_claude = repo_root / spec.root_rel
    names = sorted(parity.population(repo_claude, spec) | parity.population(dest_claude, spec))
    actions: list[Action] = []
    for n in names:
        src, dst = repo_claude / n, dest_claude / n
        if not src.is_file():
            actions.append(Action(n, REPORT, "not in the repo: port it there, or remove it by hand"))
            continue
        want = _norm(src.read_bytes())
        scoped = parity.in_rewrite_scope(n, spec)
        to = _rewrite_to(spec, dest_claude, n)
        target = want.replace(spec.rewrite_from, to) if to else want
        if not dst.is_file():
            actions.append(Action(n, CREATE))
            continue
        have = _norm(dst.read_bytes())
        if have == target:
            actions.append(Action(n, SKIP))
            continue
        # A rewritten spelling is only a KNOWN version where the installer would have
        # produced it; a rewritten hook is a hand edit, and must be refused like one.
        def spellings(b: bytes) -> set[bytes]:
            return {b, *(b.replace(spec.rewrite_from, t) for t in spec.rewrite_to)} if scoped else {b}
        known = spellings(want)
        for c in committed_versions(repo_root, _git_rel(spec, n)):
            known |= spellings(c)
        if have in known:
            actions.append(Action(n, UPDATE))
        elif n in force:
            actions.append(Action(n, UPDATE, "--force: a never-ported local edit is overwritten"))
        else:
            actions.append(Action(
                n, REFUSE,
                "the deployed copy matches no version the repo has EVER committed -- an edit "
                "made in the game root that was never ported. Port it to the repo first, or "
                f"pass --force {n}"))
    for n, tmpl in spec.rendered:
        actions.append(_plan_rendered(repo_root, dest_claude, n, tmpl, force))
    if spec.name == "claude":
        actions.extend(_plan_config(dest_claude))
    return actions


#: The reason a rendered Codex hooks.json carries when this deploy would write it. Codex runs
#: a project hook only once its CURRENT definition is trusted (codex_trust.py): a new or
#: changed hooks.json is SKIPPED, silently, until the user approves it in Codex's /hooks.
REAPPROVE = ("NEW/CHANGED hook definitions: Codex skips them until you approve them in /hooks "
             "(re-approve after every change); until then Codex here runs UNGUARDED")


def _plan_rendered(repo_root: Path, dest_root: Path, name: str, tmpl: str,
                   force: set[str] | frozenset) -> Action:
    """A file whose content is the source template rendered for THIS root (.codex/hooks.json).
    install.sh's rules: equal as canonical JSON is left BYTE-IDENTICAL (so Codex's review of
    it still holds); a rendering of any template the repo ever committed is ours to replace;
    anything else is a custom definition and is refused."""
    try:
        want = parity.render_rendered(repo_root, tmpl, dest_root)
    except Exception as exc:  # noqa: BLE001 -- refused and NAMED, never written half-rendered
        return Action(name, REFUSE, f"cannot render {tmpl} for this root: {type(exc).__name__}: {exc}")
    dst = dest_root / name
    if not dst.is_file():
        return Action(name, CREATE, REAPPROVE)
    have = parity.canonical_json(dst.read_bytes())
    if have is not None and have == parity.canonical_json(want.encode("utf-8")):
        return Action(name, SKIP)
    for rel in (tmpl, "agent/targets/codex/hooks.json.tmpl"):
        for old in committed_versions(repo_root, rel):
            try:
                text = parity.render_template_text(old.decode("utf-8-sig"), dest_root, repo_root)
            except Exception:  # noqa: BLE001  # silent-ok: an unrenderable OLD template is simply not a known version (stricter)
                continue
            if have is not None and have == parity.canonical_json(text.encode("utf-8")):
                return Action(name, UPDATE, REAPPROVE)
    if name in force:
        return Action(name, UPDATE, "--force: custom hook definitions are overwritten; " + REAPPROVE)
    return Action(name, REFUSE, "custom Codex hook definitions (a rendering of no template the repo "
                                f"ever committed): review them, or pass --force {name}")


def _plan_config(dest_claude: Path) -> list[Action]:
    """The 3.x path config beside the deployed tree: MIGRATE it, REPORT a conflict, or
    nothing. Asked of the one implementation, dry."""
    action, msg = parity._paths.migrate_legacy_config(dest_claude.parent, apply=False)
    name = parity._paths.CONFIG_NAME
    if action in ("would-move", "would-retire-old"):
        return [Action(name, MIGRATE, msg)]
    if action == "refused":
        return [Action(name, CONFLICT, msg)]
    return []


def target_bytes(repo_root: Path, dest_claude: Path, name: str, spec=None) -> bytes:
    spec = spec or parity.CLAUDE
    rendered = dict(spec.rendered)
    if name in rendered:
        data = parity.render_rendered(repo_root, rendered[name], dest_claude).encode("utf-8")
    else:
        data = _norm((repo_root / spec.root_rel / name).read_bytes())
        # The SAME predicate `plan()` uses. The first version scoped the rewrite in plan()
        # and not here, so the plan said "hooks are not rewritten" while the write rewrote
        # them -- caught by test_HOOKS_and_SETTINGS_are_never_rewritten and, independently,
        # by the parity check this script runs after applying ("0 of 2 at parity").
        to = _rewrite_to(spec, dest_claude, name)
        if to:
            data = data.replace(spec.rewrite_from, to)
    dst = dest_claude / name
    if dst.is_file() and _is_crlf(dst.read_bytes()):
        data = data.replace(b"\n", b"\r\n")
    return data


def _lock_manifest() -> set[str] | None:
    """x4lock's manifest as resolved paths, or None when it cannot be built."""
    try:
        return {str(p.resolve()).lower() for p in x4lock.manifest()}
    except Exception:  # silent-ok: an unbuildable manifest is REPORTED by the caller, not hidden
        return None


def apply(repo_root: Path, dest_claude: Path, actions: list[Action], spec=None) -> list[str]:
    """Write every create/update. Returns failure messages; [] means all landed.

    A CREATED file is locked afterwards when x4lock's manifest covers it. The first version
    preserved each file's prior lock state, which is always "unlocked" for a file that did
    not exist -- so a new skill's files stayed writable until someone remembered to run
    `x4lock.py lock` (review, 2026-09-13).
    """
    failures: list[str] = []
    created: list[Path] = []
    for a in actions:
        if a.kind == MIGRATE:
            got, msg = parity._paths.migrate_legacy_config(dest_claude.parent, apply=True)
            if got not in ("moved", "retired-old"):
                failures.append(f"{a.name}: the 3.x config was NOT migrated ({got}): {msg}")
            else:
                print(f"  {msg}")
            continue
        if a.kind not in (CREATE, UPDATE):
            continue
        dst = dest_claude / a.name
        if a.kind == CREATE:
            created.append(dst)
        data = target_bytes(repo_root, dest_claude, a.name, spec)   # encoded before any write
        was_locked = dst.is_file() and x4lock.state(dst) == "locked"
        if was_locked:
            ok, msg = x4lock._apply(dst, locked=False)
            if not ok:
                failures.append(f"{a.name}: could not unlock ({msg}); NOT written")
                continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        except OSError as exc:
            failures.append(f"{a.name}: write failed ({exc})")
        finally:
            if was_locked:
                ok, msg = x4lock._apply(dst, locked=True)
                if not ok:
                    failures.append(f"{a.name}: WRITTEN BUT NOT RE-LOCKED ({msg})")
        if dst.is_file() and dst.read_bytes() != data:
            failures.append(f"{a.name}: re-read does not match what was written")
    if created:
        wanted = _lock_manifest()
        if wanted is None:
            failures.append("x4lock manifest could not be built: created file(s) left UNLOCKED "
                            "-- run scripts/x4lock.py lock")
        else:
            for dst in created:
                if dst.is_file() and str(dst.resolve()).lower() in wanted:
                    ok, msg = x4lock._apply(dst, locked=True)
                    if not ok:
                        failures.append(f"{dst.name}: created but NOT LOCKED ({msg})")
    return failures


def _agents_md_note(repo_root: Path, root: Path) -> str:
    """AGENTS.md is outside the codex deployment (the game-root repository owns it, and it may
    carry that workspace's own section). Say whether it still holds the toolkit's CURRENT
    generated text and how its size compares with Codex's default instruction budget."""
    p, src = root / "AGENTS.md", repo_root / "AGENTS.md"
    if not p.is_file():
        return ("AGENTS.md: none in this root -- Codex here gets no toolkit instructions "
                "(copy the toolkit's AGENTS.md; this deploy never writes it)")
    have = p.read_bytes()
    current = src.is_file() and _norm(src.read_bytes()).rstrip(b"\n") in _norm(have)
    size = len(have)
    over = (f"; OVER Codex's default {CODEX_DOC_BUDGET}-byte budget: the end is cut off unless "
            ".codex/config.toml raises project_doc_max_bytes") if size > CODEX_DOC_BUDGET else ""
    state = ("contains the toolkit's current AGENTS.md" if current else
             "does NOT contain the toolkit's current AGENTS.md -- update it by hand (keep any "
             "section of your own)")
    return f"AGENTS.md: not deployed by this script; it {state}. {size} bytes{over}"


#: Codex reads the root AGENTS.md and every nested one into ONE budget of this many bytes by
#: default (install.sh --codex-doc-max-bytes help text).
CODEX_DOC_BUDGET = 32768


def _deploy_one(repo_root: Path, dest: Path, spec, args) -> tuple[int, list[str]]:
    """Plan (and with --apply, write) one target. Returns (rc, names in the plan)."""
    actions = plan(repo_root, dest, set(args.force), spec=spec)
    mode = "APPLY" if args.apply else "DRY RUN (nothing written; --apply to write)"
    if spec.name == "claude":
        print(f"DEPLOY .claude/ -- {mode}")
        print(f"  from {repo_root / '.claude'}")
        print(f"  to   {dest}   (installer rewrite: {'yes' if needs_rewrite(dest) else 'no'})")
    else:
        print(f"DEPLOY .codex/ + .agents/ (Codex) -- {mode}")
        print(f"  from {repo_root}")
        print(f"  to   {dest}   (skill token rendered as {codex_token_value().decode()}; "
              ".codex/hooks.json rendered for this root)")
    width = max(len(k) for k in (CREATE, UPDATE, SKIP, REFUSE, REPORT, MIGRATE, CONFLICT))
    for a in actions:
        if a.kind == SKIP:
            continue
        print(f"  {a.kind:<{width}}  {a.name}" + (f"  -- {a.reason}" if a.reason else ""))
    counts = {k: sum(1 for a in actions if a.kind == k)
              for k in (CREATE, UPDATE, SKIP, REFUSE, REPORT, MIGRATE, CONFLICT)}
    print("  " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if spec.name == "codex":
        print("  " + _agents_md_note(repo_root, dest))
        print("  .codex/config.toml is per-machine and never touched.")

    refused = [a for a in actions if a.kind == REFUSE]
    conflicts = [a for a in actions if a.kind == CONFLICT]
    names = [a.name for a in actions]
    if conflicts:
        print(f"  {len(conflicts)} path-config CONFLICT(S) -- two configs that DIFFER; neither "
              f"was touched. Resolve: python \"{REPO_ROOT / 'scripts' / 'x4config.py'}\" status "
              f"--root \"{dest.parent}\"", file=sys.stderr)
    if not args.apply:
        return (1 if (refused or conflicts) else 0), names

    failures = apply(repo_root, dest, actions, spec)
    for f in failures:
        print(f"  FAILED  {f}", file=sys.stderr)
    rows = parity.compare_trees(repo_root / spec.root_rel, dest, spec)
    off = [r for r in rows if not r.at_parity]
    print(f"  parity after apply: {len(rows) - len(off)} of {len(rows)} file(s) at parity")
    for r in off:
        print(f"    {parity.describe(r)}")
    if refused:
        print(f"  {len(refused)} file(s) REFUSED -- see above; nothing was written to them",
              file=sys.stderr)
    # The deploy's OWN files must be at parity afterwards. Printing "N of M" and returning
    # 0 regardless is how a plan/write disagreement (found once already) would pass at
    # runtime; files only in the game root are reported above and are not ours to fix.
    own = {a.name for a in actions if a.kind in (CREATE, UPDATE, SKIP)}
    broken = [r for r in off if r.name in own]
    if broken:
        print(f"  PARITY BROKEN after apply for {len(broken)} file(s) this deploy wrote or "
              "checked -- the plan and the write disagree", file=sys.stderr)
    # Only when the write LANDED (review LOW-3): the file on disk is at parity after apply.
    # Not "no failure line": a re-lock failure is a failure line on a file that WAS written.
    written = [a.name for a in actions if a.kind in (CREATE, UPDATE) and REAPPROVE in a.reason
               and not any(r.name == a.name for r in off)]
    if spec.name == "codex" and written:
        print("  ACTION FOR YOU: .codex/hooks.json was written. Open Codex in this folder, run "
              "/hooks and approve the X4 hooks; then check: python scripts/x4doctor.py "
              f"--root \"{dest}\" --agent codex")
    return (1 if (refused or failures or broken or conflicts) else 0), names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deploy-claude-dir",
                                 description="Deploy the repo's agent trees into the game root.")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--force", action="append", default=[], metavar="NAME",
                    help="overwrite this one never-ported file (repeatable)")
    ap.add_argument("--target", choices=("claude", "codex", "all"), default="claude",
                    help="which agent tree: claude (.claude/, the default), codex (.codex/hooks, "
                         ".codex/rules, .agents/skills and a rendered .codex/hooks.json), or all")
    ap.add_argument("--dest", help="a .claude/ directory to deploy into (default: the "
                                   "configured game root's); its parent is the codex root")
    ap.add_argument("--root", help="the root to deploy into (default: the configured game "
                                   "root); the claude tree goes to <root>/.claude")
    ap.add_argument("--repo", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    repo_root = Path(args.repo) if args.repo else REPO_ROOT
    if args.root:
        root = Path(args.root)
    elif args.dest:
        root = Path(args.dest).parent
    else:
        g = parity._paths.game_root()
        root = Path(g) if g is not None else None
    targets = ("claude", "codex") if args.target == "all" else (args.target,)

    jobs: list[tuple] = []
    for t in targets:
        if t == "claude":
            dest = Path(args.dest) if args.dest else (root / ".claude" if root else None)
            if args.dest and not dest.is_dir():
                # The 4.0 code created a named --dest from nothing (rc 0); it is refused now,
                # so a typo cannot plant a fresh .claude/ somewhere unintended.
                print(f"REFUSING: --dest {dest} is not an existing directory. Nothing was "
                      "written.", file=sys.stderr)
                return 2
            if dest is None or not dest.is_dir():
                print("REFUSING: no game root configured (or it has no .claude/). Nothing to "
                      "deploy into; pass --dest to name one.", file=sys.stderr)
                return 2
            if dest.resolve() == (repo_root / ".claude").resolve():
                print(f"REFUSING: the destination IS the repo's .claude/ ({dest}).", file=sys.stderr)
                return 2
        else:
            dest = root
            if dest is None or not dest.is_dir():
                print("REFUSING: no game root configured (or it does not exist). Nothing to "
                      "deploy into; pass --root to name one.", file=sys.stderr)
                return 2
            if dest.resolve() == repo_root.resolve():
                print(f"REFUSING: the destination IS the repo ({dest}).", file=sys.stderr)
                return 2
        jobs.append((parity.TARGETS[t], dest))

    planned = {a.name for spec, dest in jobs for a in plan(repo_root, dest, set(args.force), spec=spec)}
    unknown_force = sorted(set(args.force) - planned)
    if unknown_force:
        print(f"REFUSING: --force names file(s) not in the deployment: {unknown_force}",
              file=sys.stderr)
        return 2

    rc = 0
    for spec, dest in jobs:
        one, _ = _deploy_one(repo_root, dest, spec, args)
        rc = max(rc, one)
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

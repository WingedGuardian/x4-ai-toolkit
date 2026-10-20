#!/usr/bin/env python
"""Does the game root's `.claude/` match the repo's? Any difference fails.

WHY THIS EXISTS. The repo is the SOURCE of every hook, skill, agent and setting; the
`.claude/` directory in a configured game root is a DEPLOYED copy, and it is the one this
machine's sessions actually load. MEASURED 2026-09-13: 25 files there vs 23 here, five
drifted, in three different directions --

  * two skills written straight into the game root and never shipped (`x4-live`,
    `x4-xml-patching`, the latter being the most-used skill in the workspace);
  * one skill NEWER in the game root (`x4-probe`, +27 lines, a whole rung of procedure);
  * two agents hand-edited in the game root to hard-code a personal absolute path,
    working around a rewrite the deployment never applied.

Every one came from writing ONCE in the wrong place. A rule to write twice fails the same
way -- the forgotten second write IS the drift -- so this is a check that refuses.

LIFTED FROM dev's `published_surface_drift.py`, keeping its two hard-won properties and
dropping the defect that let this drift live:

  * LINE ENDINGS ARE NOT DRIFT (raw bytes once made every skill "differ", which buried
    the real findings);
  * REPORT THE DIRECTION, NEVER A WINNER. "Newer" copies have destroyed cross-platform
    work before. This gate names lines-only-here and lines-only-there; the deploy script
    (`scripts/deploy-claude-dir.py`) is what acts, and it refuses to overwrite an edit
    that was never ported back.
  * ⛔ NO ACCEPT-BASELINE. The dev gate had one, and an accepted drift is a drift nobody
    reads again. Any non-identical row is rc 1.

THE POPULATION is what the game-root repository's own `.gitignore` whitelists as the
deployment: `settings.json`, `settings.local.json.example`, and everything under `hooks/`,
`skills/`, `agents/`, `commands/`. Per-machine files (install.sh's `X4_KEEP_LOCAL`:
`x4-paths.env`, `settings.local.json`, `backups/`) are outside it by construction, and
caches and editor backups are excluded explicitly.

THE INSTALLER'S REWRITE IS NOT DRIFT. `install.sh` rewrites `$CLAUDE_PROJECT_DIR` to
`$X4_TOOLKIT` when the toolkit does not live inside the directory the skills run from. A
deployed file equal to the repo file after that rewrite is the same file. The rewrite
goes ONE way: a repo file carrying `$X4_TOOLKIT` is drift, because it would break every
in-game install.

THE CODEX TARGET (F11, 2026-10-20) is compared too, whenever the game root holds `.codex/`,
`.agents/` or AGENTS.md (`CODEX_MARKS`); with none of them it is N/A, said in one line.
AGENTS.md with no `.codex/` is NOT N/A: AGENTS.md steers Codex, so that root is instructed
and unguarded -- the measured state that made this target exist -- and the gate says
`CODEX UNGUARDED` and fails. See `TARGETS["codex"]` for its population.

Run:  uv run python gates/deploy_parity.py
Exit: 0 every file identical - 1 drift, each file named with its direction - 2 could not
      compare (no game root configured, a root with neither .claude/ nor any Codex file, or
      nothing to compare; never "at parity"). A root with no .claude/ but a Codex file (a
      Codex-only setup) is checked for the Codex tree, the claude section reading N/A.
"""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x4validate import _paths  # noqa: E402

#: `<repo>/tools/x4validate/gates/this.py` -> `<repo>`. From __file__, never $X4_TOOLKIT.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The deployment, as the game-root repository's `.gitignore` whitelists it.
#: `x4-paths.env.example` left in Plan 3 lane I: it lives at the toolkit root now, beside
#: the `x4-paths.env` 4.x reads, so a game root's `.claude/` no longer carries it.
TOP_FILES = ("settings.json", "settings.local.json.example")
SUBTREES = ("hooks", "skills", "agents", "commands")

#: Never part of a deployment, wherever they appear.
_EXCLUDED_DIRS = {"__pycache__", ".pytest_cache"}
_EXCLUDED_SUFFIXES = (".pyc", ".bak")

REWRITE_FROM = b"$CLAUDE_PROJECT_DIR"
REWRITE_TO = b"$X4_TOOLKIT"

#: The ONLY subtrees the installer rewrites -- install.sh loops over `skills/x4-*` and
#: `agents/*.md` and nothing else. `settings.json` and the hooks keep
#: `$CLAUDE_PROJECT_DIR` on purpose: it resolves to the directory the session is
#: running in, which is where the deployed hooks live. MEASURED 2026-09-13 by the
#: deploy script's first dry run: an unscoped rewrite planned to point the game root's
#: settings.json at the REPO's hooks, i.e. to swap which guards every session runs.
REWRITE_SCOPE = ("skills/", "agents/")



@dataclass(frozen=True)
class TargetSpec:
    """One agent's deployed tree: where it lives under a root, what its population is,
    and which subtrees the installer rewrites (from one token to any of its renderings).

    `claude` and `codex` are defined, each with a deploy workflow that writes it
    (`deploy-claude-dir.py --target claude|codex`). `.opencode/` reaches a root only
    through the installer and has no entry; x4doctor reads this table and reports a
    target with no entry as N/A, naming that reason. Add an entry only together with a
    deploy workflow that writes that tree.

    `rendered`: (deployed name, source template) pairs whose deployed form is the template
    rendered for the DESTINATION root -- compared as canonical JSON, never as bytes.
    `rewrite_required`: a deployed file in rewrite scope that still carries `rewrite_from`
    is NOT at parity (the Codex skills' token is meaningless outside a source checkout).
    """
    name: str
    root_rel: str
    top_files: tuple[str, ...]
    subtrees: tuple[str, ...]
    rewrite_scope: tuple[str, ...]
    rewrite_from: bytes = REWRITE_FROM
    rewrite_to: tuple[bytes, ...] = (REWRITE_TO,)
    rendered: tuple[tuple[str, str], ...] = ()
    rewrite_required: bool = False


#: The Codex target (F11, 2026-10-20). MEASURED that day: a game root deployed with this
#: script had `.claude/` and AGENTS.md but no `.codex/` -- every Codex session there ran with
#: NO guard hook, and x4doctor said only "the codex target is not installed here".
#: The population is install.sh's X4_AGENT_ITEMS_codex (`AGENTS.md .codex .agents`) minus:
#:   * `.codex/hooks.json` and `.codex/config.toml` -- per-machine (X4_KEEP_LOCAL). hooks.json
#:     is still COMPARED and DEPLOYED, as a rendering of the template for the destination
#:     root (`rendered`), because its whole content is derivable; config.toml never is;
#:   * AGENTS.md -- a game root's AGENTS.md is owned by the game-root repository and may
#:     carry that workspace's own section after the generated part (READ 2026-10-20: the
#:     dev game root's does), exactly as its CLAUDE.md is outside the claude population.
#: The installer renders `{{TOOLKIT}}` in `.agents/` per OS (`$env:X4_TOOLKIT` on Windows,
#: where Codex runs PowerShell; `$X4_TOOLKIT` elsewhere): either rendering is parity.
CODEX_TEMPLATE = "tools/x4_harness/codex-hooks.json.tmpl"
CODEX_RENDERER = "tools/x4_harness/x4_upgrade_render.py"
CODEX_HOOKS_JSON = ".codex/hooks.json"
TOOLKIT_TOKEN = b"{{TOOLKIT}}"
TOOLKIT_RENDER_WINDOWS = b"$env:X4_TOOLKIT"
TOOLKIT_RENDER_POSIX = b"$X4_TOOLKIT"

TARGETS: dict[str, TargetSpec] = {
    "claude": TargetSpec("claude", ".claude", TOP_FILES, SUBTREES, REWRITE_SCOPE),
    "codex": TargetSpec("codex", ".", (), (".codex/hooks", ".codex/rules", ".agents/skills"),
                        (".agents/",), TOOLKIT_TOKEN,
                        (TOOLKIT_RENDER_WINDOWS, TOOLKIT_RENDER_POSIX),
                        rendered=((CODEX_HOOKS_JSON, CODEX_TEMPLATE),),
                        rewrite_required=True),
}
CLAUDE = TARGETS["claude"]
CODEX = TARGETS["codex"]

#: What makes the Codex target IN SCOPE for a root. AGENTS.md is the file that steers Codex,
#: so AGENTS.md with no `.codex/` is the unguarded state itself -- reported, never N/A.
CODEX_MARKS = (".codex", ".agents", "AGENTS.md")


def codex_marks(root: Path) -> list[str]:
    return [m for m in CODEX_MARKS if (root / m).exists()]


def _load_renderer(source_dir: Path):
    """The toolkit's own Codex renderer (`render_codex`), from the SOURCE tree compared --
    one implementation for the installer's check-codex, this gate and the deploy."""
    import importlib.util
    path = source_dir / CODEX_RENDERER
    spec = importlib.util.spec_from_file_location("x4_upgrade_render_for_parity", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    mod = importlib.util.module_from_spec(spec)
    saved = sys.dont_write_bytecode      # the renderer sets it on import; keep ours
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


def render_template_text(template: str, dest_root: Path, source_dir: Path = REPO_ROOT) -> str:
    """`template` rendered for `dest_root` by the source's renderer, ending in one newline
    (install.sh writes `printf '%s\\n'`)."""
    mod = _load_renderer(source_dir)
    return mod.render_codex(template, Path(dest_root).resolve()).rstrip("\n") + "\n"


def render_rendered(source_dir: Path, template_rel: str, dest_root: Path) -> str:
    raw = (source_dir / template_rel).read_bytes().decode("utf-8-sig")
    return render_template_text(raw, dest_root, source_dir)


def canonical_json(raw: bytes) -> str | None:
    """The installer's check-codex comparison: parsed, keys sorted. None = not JSON."""
    import json
    try:
        return json.dumps(json.loads(raw.decode("utf-8-sig")), sort_keys=True,
                          ensure_ascii=True, allow_nan=False)
    except (ValueError, UnicodeDecodeError):
        return None  # silent-ok: None IS the channel -- a non-JSON file never compares equal, so it reads as drift


def in_rewrite_scope(name: str, spec: TargetSpec = CLAUDE) -> bool:
    return bool(spec.rewrite_scope) and name.startswith(spec.rewrite_scope)

IDENTICAL = "identical"
IDENTICAL_REWRITTEN = "identical (after the installer rewrite)"
IDENTICAL_RENDERED = "identical (rendered for this root)"
UNRENDERED = "drift (the installer token is still unrendered)"
UNRENDERABLE = "cannot render the source template"
DRIFT = "drift"
ONLY_REPO = "only in repo"
ONLY_GAME = "only in game root"


@dataclass(frozen=True)
class Row:
    name: str
    state: str
    game_only: int = 0
    repo_only: int = 0

    @property
    def at_parity(self) -> bool:
        return self.state in (IDENTICAL, IDENTICAL_REWRITTEN, IDENTICAL_RENDERED)


def repo_claude_dir() -> Path:
    return REPO_ROOT / ".claude"


def game_claude_dir() -> Path | None:
    root = _paths.game_root()
    if root is None:
        return None
    d = Path(root) / ".claude"
    return d if d.is_dir() else None


def _norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def population(claude: Path, spec: TargetSpec = CLAUDE) -> set[str]:
    """Deployment-relative posix paths that exist under one deployed agent directory
    (`.claude/` unless `spec` names another target)."""
    out: set[str] = set()
    for name in spec.top_files:
        if (claude / name).is_file():
            out.add(name)
    for sub in spec.subtrees:
        base = claude / sub
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(claude)
            if any(part in _EXCLUDED_DIRS for part in rel.parts):
                continue
            if p.name.endswith(_EXCLUDED_SUFFIXES):
                continue
            out.add(rel.as_posix())
    return out


def compare_file(repo_file: Path, game_file: Path, name: str,
                 spec: TargetSpec = CLAUDE) -> Row:
    if not game_file.is_file():
        return Row(name, ONLY_REPO)
    if not repo_file.is_file():
        return Row(name, ONLY_GAME)
    repo, game = _norm(repo_file.read_bytes()), _norm(game_file.read_bytes())
    if repo == game:
        if (spec.rewrite_required and in_rewrite_scope(name, spec)
                and spec.rewrite_from in game):
            return Row(name, UNRENDERED)
        return Row(name, IDENTICAL)
    if in_rewrite_scope(name, spec) and any(
            repo.replace(spec.rewrite_from, to) == game for to in spec.rewrite_to):
        return Row(name, IDENTICAL_REWRITTEN)
    return _drift(name, repo, game)


def _drift(name: str, repo: bytes, game: bytes) -> Row:
    r = repo.decode("utf-8", "replace").split("\n")
    g = game.decode("utf-8", "replace").split("\n")
    diff = list(difflib.unified_diff(r, g, n=0, lineterm=""))
    game_only = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
    repo_only = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
    return Row(name, DRIFT, game_only, repo_only)


def compare_rendered(source_dir: Path, template_rel: str, game_dir: Path, name: str) -> Row:
    """A file whose deployed form is the source template rendered for the GAME root.
    Equal as canonical JSON is parity (the installer's own check-codex rule), so a
    deployed file that differs only in formatting is left alone -- Codex re-asks for
    review of any CHANGED hooks.json."""
    game_file = game_dir / name
    try:
        want = render_rendered(source_dir, template_rel, game_dir)
    except Exception as exc:  # noqa: BLE001 -- a render failure is a ROW, never parity
        return Row(name, f"{UNRENDERABLE} ({type(exc).__name__}: {exc})")
    if not game_file.is_file():
        return Row(name, ONLY_REPO)
    have = game_file.read_bytes()
    a, b = canonical_json(have), canonical_json(want.encode("utf-8"))
    if a is not None and a == b:
        return Row(name, IDENTICAL_RENDERED)
    return _drift(name, want.encode("utf-8"), _norm(have))


def compare_trees(repo_claude: Path, game_claude: Path,
                  spec: TargetSpec = CLAUDE) -> list[Row]:
    names = sorted(population(repo_claude, spec) | population(game_claude, spec))
    rows = [compare_file(repo_claude / n, game_claude / n, n, spec) for n in names]
    rows += [compare_rendered(repo_claude, tmpl, game_claude, n) for n, tmpl in spec.rendered]
    return sorted(rows, key=lambda r: r.name)


def describe(row: Row) -> str:
    if row.state == DRIFT:
        return (f"{row.name}: {row.game_only} line(s) only in the game root, "
                f"{row.repo_only} only in the repo")
    return f"{row.name}: {row.state}"


def game_root_dir() -> Path | None:
    """The configured game root, whether or not it holds a .claude/."""
    root = _paths.game_root()
    return Path(root) if root is not None and Path(root).is_dir() else None


def main() -> int:
    repo = repo_claude_dir()
    game = game_claude_dir()
    if not repo.is_dir():
        print(f"REFUSING: no repo .claude/ at {repo}", file=sys.stderr)
        return 2
    # Review LOW-2: a root with no .claude/ (Codex only) is still checked for the Codex tree;
    # the claude section says N/A. A root with NEITHER tree is a refusal, never "at parity".
    root = game.parent if game is not None else game_root_dir()
    if root is None or (game is None and not codex_marks(root)):
        print("REFUSING: no game root configured, or it has neither .claude/ nor any Codex "
              "file (.codex/, .agents/, AGENTS.md), so there is no deployed copy to compare. "
              "This is not 'at parity'.", file=sys.stderr)
        return 2
    if game is not None and game.resolve() == repo.resolve():
        print(f"REFUSING: the game root's .claude/ IS the repo's ({repo}); comparing a "
              "tree to itself proves nothing.", file=sys.stderr)
        return 2
    if root.resolve() == repo.parent.resolve():
        print(f"REFUSING: the game root IS the repo ({root}); comparing a tree to itself "
              "proves nothing.", file=sys.stderr)
        return 2
    rows = compare_trees(repo, game) if game is not None else None
    if rows is not None and not rows:
        print("REFUSING: nothing to compare -- both deployment populations are empty.",
              file=sys.stderr)
        return 2

    codex_unguarded(root)
    print("DEPLOY PARITY -- repo .claude/ (source) vs game root .claude/ (deployed)")
    print(f"  repo:      {repo}")
    print(f"  game root: {root}")
    if rows is None:
        print("  claude: N/A -- the game root has no .claude/ (Claude Code is not set up there)")
        bad = False
    else:
        bad = _report(rows, "")
    bad |= _codex(repo.parent, root)
    if not bad:
        print("  at parity.")
    return 1 if bad else 0


def _report(rows: list[Row], target_flag: str) -> bool:
    """Print one target's comparison; True when anything is off parity."""
    off = [r for r in rows if not r.at_parity]
    rewritten = sum(1 for r in rows if r.state in (IDENTICAL_REWRITTEN, IDENTICAL_RENDERED))
    print(f"  {len(rows)} file(s) compared on normalised content: "
          f"{len(rows) - len(off)} at parity ({rewritten} via the installer rewrite or "
          f"render), {len(off)} not")
    if not off:
        return False
    cmd = "scripts/deploy-claude-dir.py" + target_flag + " --apply"
    print("", file=sys.stderr)
    print(f"DRIFT -- {len(off)} file(s). The gate names no winner; read both "
          "directions, then:", file=sys.stderr)
    print("  * a change that belongs in the toolkit: make it in the REPO, then run "
          f"{cmd}", file=sys.stderr)
    print(f"  * a repo change not yet deployed: run {cmd}", file=sys.stderr)
    for r in off:
        print(f"    {describe(r)}", file=sys.stderr)
    return True


def codex_unguarded(game_root: Path) -> None:
    """The directive, printed FIRST (#38: a reader of a cut-off output keeps the head)."""
    marks = codex_marks(game_root)
    if marks and not (game_root / ".codex").is_dir():
        print(f"CODEX UNGUARDED: {game_root} has {', '.join(marks)} but no .codex/, so a Codex "
              "session there runs with NO guard hooks. Deploy them: "
              "scripts/deploy-claude-dir.py --target codex --apply", file=sys.stderr)


def _codex(repo_root: Path, game_root: Path) -> bool:
    """The Codex target. N/A only where nothing marks Codex in the root; AGENTS.md without
    `.codex/` is the 2026-10-20 state -- instructed, unguarded -- and is reported first."""
    marks = codex_marks(game_root)
    if not marks:
        print("  codex: N/A -- no .codex/, .agents/ or AGENTS.md in the game root (Codex is "
              "not set up there)")
        return False
    print("DEPLOY PARITY -- repo .codex/ + .agents/ (source) vs game root (deployed, Codex)")
    return _report(compare_trees(repo_root, game_root, CODEX), " --target codex")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

#!/usr/bin/env python
"""Session start: do this root's deployed agent trees still match what the toolkit would deploy?

    python scripts/x4deploydrift.py <root> [--toolkit DIR] [--state DIR]

WHY THIS EXISTS. `.claude/`, `.codex/` and `.agents/` in a game root are DEPLOYED copies
(`tools/x4validate/scripts/deploy-claude-dir.py`), and they are what every session there loads.
Between deploys nothing compared them with the toolkit: the deploy script checks itself after
applying, and `gates/deploy_parity.py` and x4doctor run only when someone runs them. So a toolkit
update that was never deployed left every session on the old guards, saying nothing. The
session-start hook (session-canary.sh) runs this, so the comparison happens without anyone
remembering to run it.

THE COMPARISON IS NOT REIMPLEMENTED. It is `deploy_parity.compare_trees` -- the population, the
installer rewrite, the per-root Codex render and the line-ending rule the deploy script itself
uses -- loaded from THIS toolkit, with line counting off (the warning names files, not diffs).

OUTPUT, for a hook whose text becomes session context (CLAUDE.md #38: keep it short, directive
first). In sync: NOTHING. Drift: two lines -- the counts (differ / missing / extra) and the fix
command, then up to six file names. Anything that stops the check (no toolkit, no comparer, no
deployed tree, an exception): ONE line saying it could not check, never silence, which would read
as "in sync". The toolkit's own checkout compared to itself is no deploy relationship: silent.
Always exit 0 -- this advises, it never blocks a session.

COST. MEASURED 2026-11-06 on the dev machine against a real game root, 109 rows over both trees,
bare python 3.10 (63 ms to start an empty interpreter): the first run from a fresh worktree
0.65 s inside the script; warm and uncached 0.17 s for the whole process; warm and cached 0.05 s
inside the script, 0.14 s for the whole process. None of these is a post-reboot cold disk. So with
`--state` the answer is cached against the size and mtime of every compared file (both sides, the Codex
template and renderer, and the comparer itself) and reused for at most a day: an unchanged tree
costs directory listings and stats, no reads. An edit that keeps a file's size AND mtime is not
seen until the cache expires -- the price of not reading 100+ files at every session start.
Every run appends its own elapsed time to the state file (`timings`, last 50), so a real
cold-disk number is measured whenever one happens.

Stdlib only: the hook runs it with a bare python.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent.parent
GATE_REL = Path("tools") / "x4validate" / "gates" / "deploy_parity.py"
STATE_NAME = "deploy-drift.json"
CACHE_MAX_AGE_S = 24 * 3600
KEEP_TIMINGS = 50
SHOW = 6
TAG = "[x4 deploy]"
FIX = ('cd "{tk}/tools/x4validate" && uv run python scripts/deploy-claude-dir.py --target all --apply'
       " (dry run first: drop --apply; it refuses game-root edits that were never ported and never"
       " deletes extra files)")


class CannotCheck(Exception):
    """The check could not run; the message is the reason, said in one line."""


def _load_gate(gate: Path):
    if not gate.is_file():
        raise CannotCheck(f"the comparer {gate.as_posix()} is missing")
    spec = importlib.util.spec_from_file_location("x4deploydrift_parity", gate)
    if spec is None or spec.loader is None:
        raise CannotCheck(f"the comparer {gate.as_posix()} cannot be loaded")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # @dataclass under __future__ annotations looks itself up
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True    # never leave __pycache__ in the toolkit from a hook
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


def _targets(dp, tk: Path, root: Path) -> list[tuple[str, Path, Path, object]]:
    """(display prefix, source dir, deployed dir, spec) for each tree this root deploys."""
    out = []
    if (root / ".claude").is_dir():
        out.append((".claude/", tk / ".claude", root / ".claude", dp.CLAUDE))
    if dp.codex_marks(root):
        out.append(("", tk, root, dp.CODEX))
    return out


def _stat(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _signature(dp, gate: Path, targets) -> str:
    """Every input the answer depends on, by size and mtime: never by content."""
    parts: list = [str(gate), _stat(gate), _stat(Path(__file__))]
    for prefix, src, dst, spec in targets:
        names = sorted(dp.population(src, spec) | dp.population(dst, spec))
        parts.append([prefix, str(src), str(dst), len(names)])
        parts.extend([n, _stat(src / n), _stat(dst / n)] for n in names)
        for name, tmpl in spec.rendered:
            parts.append([name, _stat(dst / name), _stat(src / tmpl),
                          _stat(src / dp.CODEX_RENDERER)])
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def _compare(dp, targets) -> tuple[list[tuple[str, str]], int]:
    """[(display name, state)] for every row OFF parity, and the total row count."""
    off, total = [], 0
    for prefix, src, dst, spec in targets:
        rows = dp.compare_trees(src, dst, spec, count_lines=False)
        total += len(rows)
        off += [(prefix + r.name, r.state) for r in rows if not r.at_parity]
    return off, total


def _report(dp, off, total: int, tk: Path) -> list[str]:
    if not off:
        return []
    missing = [n for n, s in off if s == dp.ONLY_REPO]
    extra = [n for n, s in off if s == dp.ONLY_GAME]
    differ = [n for n, s in off if s not in (dp.ONLY_REPO, dp.ONLY_GAME)]
    lines = [f"{TAG} DEPLOYED AGENT FILES DO NOT MATCH THE TOOLKIT: {len(differ)} differ, "
             f"{len(missing)} missing, {len(extra)} extra (of {total}). Sessions here run the deployed "
             f"copies. Fix: " + FIX.format(tk=tk.as_posix())]
    named = ([f"{n} (differs)" for n in differ] + [f"{n} (missing)" for n in missing]
             + [f"{n} (extra)" for n in extra])
    more = f" ... +{len(named) - SHOW} more" if len(named) > SHOW else ""
    lines.append("            " + ", ".join(named[:SHOW]) + more)
    return lines


def _read_state(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # silent-ok: no usable cache means a full comparison, never a wrong answer
    return data if isinstance(data, dict) else {}


def _write_state(path: Path | None, data: dict) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(json.dumps(data, indent=1).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        pass  # silent-ok: an unwritable cache costs the next session a full comparison, nothing else


def check(root: Path, tk: Path, state: Path | None) -> list[str]:
    t0 = time.perf_counter()
    if not tk.is_dir():
        raise CannotCheck(f"the toolkit {tk.as_posix()} does not exist (X4_TOOLKIT not configured?)")
    if root.resolve() == tk.resolve() or (root / GATE_REL).is_file():
        # The source checkout itself, or another toolkit tree (a worktree of it, a toolkit installed inside
        # this root): its .claude/ is generated from its own agent/ (gen-agent-trees --check), not deployed.
        return []
    gate = tk / GATE_REL if (tk / GATE_REL).is_file() else TOOLKIT / GATE_REL
    dp = _load_gate(gate)
    targets = _targets(dp, tk, root)
    if not targets:
        raise CannotCheck(f"{root.as_posix()} has no deployed .claude/, .codex/, .agents/ or AGENTS.md")
    data = _read_state(state)
    sig = _signature(dp, gate, targets)
    cache = data.get("cache")
    cached = (isinstance(cache, dict) and cache.get("key") == sig
              and isinstance(cache.get("at"), (int, float))
              and 0 <= time.time() - cache["at"] < CACHE_MAX_AGE_S
              and isinstance(cache.get("lines"), list)
              and all(isinstance(x, str) for x in cache["lines"])
              and isinstance(cache.get("rows"), int)
              and isinstance(cache.get("off"), int))
    if cached:
        lines, total, n_off = cache["lines"], cache["rows"], cache["off"]
    else:
        off, total = _compare(dp, targets)
        if total == 0:
            raise CannotCheck("nothing to compare: both deployed populations are empty")
        lines, n_off = _report(dp, off, total, tk), len(off)
        data["cache"] = {"key": sig, "at": time.time(), "lines": lines, "rows": total, "off": n_off}
    timings = data.get("timings") if isinstance(data.get("timings"), list) else []
    timings.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "cached": bool(cached), "rows": total, "off": n_off})
    data["timings"] = timings[-KEEP_TIMINGS:]
    _write_state(state, data)
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="the root whose deployed trees are checked")
    ap.add_argument("--toolkit", type=Path, default=TOOLKIT, help="the source toolkit (default: this one)")
    ap.add_argument("--state", type=Path, default=None,
                    help=f"directory for the cache and timing log ({STATE_NAME}); none = no cache")
    args = ap.parse_args(argv)
    state = args.state / STATE_NAME if args.state is not None else None
    try:
        lines = check(args.root, args.toolkit, state)
    except CannotCheck as exc:
        lines = [f"{TAG} could not check whether the deployed agent files match the toolkit: {exc}."]
    except Exception as exc:  # noqa: BLE001 -- any failure is said in one line, never silence
        msg = str(exc).replace("\n", " ")[:200]
        lines = [f"{TAG} could not check whether the deployed agent files match the toolkit: "
                 f"{type(exc).__name__}: {msg}"]
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ORACLE v4 — every mod the engine rejected ops from, packed included.

Uses the tool's OWN iterator (_check.iter_diff_files) so it exercises the same code
path x4validate does. Denominator is every DISTINCT (vpath, sel) cardinality failure in
the log whose mod is INSTALLED here -- keyed, not per line, because the engine logs
most failures twice (gotcha #19); raw line counts are tracked separately as `lines`.
Lines for mods that are not installed are counted and named separately
(`absent_lines`), never absorbed into the ratio.
"""
from collections import Counter, defaultdict
from pathlib import Path

import sys

import _env

from x4validate import _check, _debuglog, _merge, _registry

EXT = _env.extensions()
LOG = _env.oracle_log()

entries = [e for e in _debuglog.parse_debug(LOG) if e.cardinality]
by_mod = defaultdict(list)
for e in entries:
    by_mod[e.folder].append(e)
print(f"cardinality failures in log: {len(entries)} across {len(by_mod)} mod(s)\n")

# WHERE IS A LOGGED FOLDER? In ANY install root (F285, LOW): the log names a folder, not
# a root, and a mod in the profile root is installed all the same. The lowest root rank
# wins (the game root), matching the engine's own preference.
_where: dict[str, Path] = {}
for _m in sorted(_registry.mods("installed"), key=lambda m: m.get("root_rank", 0)):
    _where.setdefault(_m["folder"].lower(), Path(_m["path"]))
_nowhere: list[str] = []   # logged, found in no root: the log is of a different modlist

tot = Counter()
rows = []
scope: set[str] = set()   # judged mods + every mod loading before one (FX-H10)
for folder in sorted(by_mod, key=lambda f: -len(by_mod[f])):
    mod_dir = _where.get(folder.lower()) or EXT / folder
    if not mod_dir.is_dir():
        _nowhere.append(folder)
        # COUNTED, not merely shown. The row is printed but this `continue` used to
        # skip every `tot[...] +=` below, so an uninstalled mod's lines left the
        # DENOMINATOR entirely while the docstring says the denominator is the full
        # set of cardinality failures in the log. The agreement percentage was then
        # computed over a population quietly smaller than the one described -- a
        # denominator taken from what happened to be present.
        tot["absent_lines"] = tot.get("absent_lines", 0) + len(by_mod[folder])
        rows.append((folder, len(by_mod[folder]), "-", "-", "-", "NOT INSTALLED"))
        continue

    # engine verdicts, keyed (vpath, sel) with the extension resolved via the VFS
    try:
        vfs = {k.lower() for k in _cat_keys(mod_dir)} if False else None
    except Exception:
        vfs = None
    engine = {}
    for e in by_mod[folder]:
        vpath = e.vpath
        for cand in _debuglog.xml_candidates(e.vpath):
            if (mod_dir / cand).is_file():
                vpath = cand
                break
        else:
            # packed: resolve against the catalog VFS
            from x4validate import _cat
            keys = {k.lower(): k for k in _cat.mod_vfs(mod_dir, packed_only=True)}  # packed-ok: loose checked first
            for cand in _debuglog.xml_candidates(e.vpath):
                if cand.lower() in keys:
                    vpath = keys[cand.lower()]
                    break
        engine[(vpath, e.sel)] = e.cardinality

    overlays, _notes = _check.tier_b_overlays(mod_dir)
    scope.add(folder.lower())
    scope.update(Path(o).name.lower() for o in overlays)
    cfg = _merge.Config(overlays=tuple(overlays))
    ours = {}
    for vpath, diff_root in _check.iter_diff_files(mod_dir):
        res = _merge.build_effective(vpath, cfg)
        if not res.base_found:
            for op in diff_root:
                if isinstance(op.tag, str) and op.get("sel"):
                    ours[(vpath, op.get("sel"))] = "no-base"
            continue
        for a in _merge.apply_diff(res.tree, diff_root):
            if a.sel:
                ours[(vpath, a.sel)] = ("ambiguous" if a.ambiguous
                                        else "ok" if a.ok else "nomatch")

    agree = sum(1 for k in engine if ours.get(k) not in ("ok", None))
    fok = sum(1 for k in engine if ours.get(k) == "ok")
    unseen = sum(1 for k in engine if k not in ours)
    packed = any(mod_dir.glob("*.cat"))
    rows.append((folder, len(by_mod[folder]), len(engine), agree, fok, unseen,
                 "packed" if packed else "loose"))
    tot["lines"] += len(by_mod[folder])
    tot["ops"] += len(engine)
    tot["agree"] += agree
    tot["false_ok"] += fok
    tot["unseen"] += unseen

print(f"{'mod':<32}{'lines':>6}{'ops':>6}{'agree':>7}{'FALSE OK':>10}{'unseen':>8}  kind")
print("-" * 78)
for r in rows:
    if len(r) == 6:
        print(f"{r[0]:<32}{r[1]:>6}{'':>6}{'':>7}{'':>10}{'':>8}  {r[5]}")
        continue
    f, lines, ops, agree, fok, unseen, kind = r
    flag = "  <== " if fok else ""
    print(f"{f:<32}{lines:>6}{ops:>6}{agree:>7}{fok:>10}{unseen:>8}  {kind}{flag}")

print("-" * 78)
print(f"{'TOTAL':<32}{tot['lines']:>6}{tot['ops']:>6}{tot['agree']:>7}"
      f"{tot['false_ok']:>10}{tot['unseen']:>8}")
# DOES THE LOG DESCRIBE THE CURRENT MODLIST? Decided before any verdict (FX-H10): each
# op is replayed on the tree of what is installed NOW, so a rejection logged by a launch
# of a different modlist can neither convict nor clear it. SCOPED to the mods judged
# above plus their patch-time overlays (every mod loading before one of them): a newer
# manifest anywhere else changes no tree replayed here. The profile and removed-folder
# signals stay global -- neither can be placed in the load order.
_stale = _env.modlist_staleness(LOG, scope).reasons()
if _nowhere:
    # A logged mod that is installed in NO root was removed since the launch (F285): the
    # engine rejected ops of a mod this tree no longer has, so the log is of another modlist.
    _stale.append(f"{len(_nowhere)} logged mod(s) with rejected ops are installed in no "
                  f"root now: {', '.join(sorted(_nowhere))}")
if _stale:
    print(f"\nREFUSING: {LOG.name} does not describe the current modlist -- "
          + "; ".join(_stale) + ". " + _env.STALE_ADVICE, file=sys.stderr)
    raise SystemExit(2)

# A VERDICT, not just a table. This script computed `false_ok`, printed it with a
# `<==` marker beside the offending row, and ended -- and `run-gates.sh` judges a gate
# purely on its exit code, so it printed `ok oracle` whatever the oracle found. The
# README meanwhile states the gate enforces "234/234 ops agree, 0 FALSE OK".
#
# NOTHING EXAMINED IS REFUSED, NOT PASSED. Over an empty log this printed a table of
# zeroes and exited 0, which reads identically to a clean run. That is the
# could-not-check-reported-as-checked-and-clean shape this toolkit exists to refuse:
# rc 2 is the NON-ANSWER, rc 1 is "there are findings".
if not tot["ops"]:
    print("\nREFUSING: no ops were examined, so there is nothing to agree or disagree "
          "with.\n  This is a NON-ANSWER, not a clean run. Point X4_ORACLE_LOG at a "
          "real engine log.", file=sys.stderr)
    raise SystemExit(2)

print(f"\nagreement: {tot['agree']}/{tot['ops']} = "
      f"{100*tot['agree']//tot['ops']}%   FALSE OK: {tot['false_ok']}   "
      f"unclassified: {tot['unseen']}")
if tot.get("absent_lines"):
    print(f"  NB {tot['absent_lines']} log line(s) belong to mods that are NOT "
          f"INSTALLED here and are outside every number above -- named in the table, "
          f"and excluded from the denominator rather than silently absorbed into it.")
if tot["false_ok"]:
    print(f"\nFAIL: {tot['false_ok']} op(s) the validator called OK and the ENGINE "
          f"did not.\n  A false OK is the one error class this project has no other "
          f"detector for.", file=sys.stderr)
    raise SystemExit(1)

# UNSEEN GATES TOO. The verdict above enforced only half the contract CLAUDE.md
# states ("oracle: 234/234 ops, 0 false OK") -- and the half it dropped is the
# NARROWING half. `unseen` counts ops the ENGINE acted on that our iterator never
# produced at all: an op that stops being examined moves out of `agree` and into
# `unseen`, lowers agreement below 234/234, and used to exit 0. That is precisely
# the shape every defect in this codebase takes -- a step that narrows the data and
# reports success anyway -- and it was the one shape this oracle could not fail on.
#
# UNVERIFIED ON THE MACHINE THAT WROTE THIS: $X4_ORACLE_LOG is unset here, so the
# gate SKIPs (rc 2) and `unseen`'s value could not be measured. If your first
# configured run fails here, read the named ops before assuming the gate is wrong --
# either the iterator really has stopped seeing them, or there is a legitimate
# exclusion, and that belongs in the code with a reason attached rather than in a
# silent zero.
if tot["unseen"]:
    print(f"\nFAIL: {tot['unseen']} op(s) the ENGINE acted on that the validator "
          f"never examined.\n  Not a disagreement -- an ABSENCE. An op that stops "
          f"being enumerated leaves `agree` quietly, so agreement is only a "
          f"denominator you can trust while this is zero.", file=sys.stderr)
    # NAME THEM. The comment above says "read the named ops"; the first cut of this
    # branch printed only the count, so the reader had to patch the gate to learn
    # which mods it failed on. A refusal that cannot say what it refused is half a
    # refusal. (Delta review, 2026-09-05.)
    for r in rows:
        if len(r) == 7 and r[5]:
            print(f"    {r[0]}: {r[5]} unseen op(s)  [{r[6]}]", file=sys.stderr)
    raise SystemExit(1)

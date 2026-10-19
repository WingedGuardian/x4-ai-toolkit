"""x4effective CLI: argument parsing and rendering for :mod:`x4validate._effective`.

Split out of `_effective.py` for F69. `_effective.py` is an `ENGINE_SOURCE`, so
the freshness fingerprint hashes its BYTES. That meant editing an error message
invalidated the effective store AND BaseX `x4eff` exactly as a merge-semantics
change would, while the stale banner asserted *"the SAME inputs would now merge
differently"*. MEASURED 2026-08-27: two edits in one session moved the hash, the
second a pure DOCSTRING (10 lines added, 0 executable).

The population is what was wrong, not the hash. Making the hash cleverer -- an
AST or token-level digest -- would move it to the UNSAFE side, where one missed
merge change reintroduces the 2026-08-13 defect that recorded vanilla engine
values as VRO's (140 of 194 rows, 72%). Nothing here can change a merged value.
"""

from __future__ import annotations

import json as _json
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from lxml import etree

from x4validate import __version__, _effective, _merge, _paths, _registry
from x4validate._provenance import Recorder
from x4validate._effective import (
    BUILDABLE_KINDS,
    LIBRARY_REGISTRIES,
    _ADVISORY,
    _connect,
    _count_line,
    active_mods,
    build,
    build_touch_map,
    effective_db,
    ordered_overlays,
    store_freshness,
    touchers_for,
)


def _base_defines_entity(cfg, kind: str, vpath: str, name: str) -> bool:
    """Does the base+DLC tree at *vpath* (no mods) define the entity *kind*/*name*?

    Registry kinds are matched on their key attribute (`LIBRARY_REGISTRIES`), macros
    and components on `@name`. A kind this does not know is answered by file
    existence alone -- the pre-AN-4 behaviour -- because guessing its identity rule
    would be a second, unreviewed definition of the store's key.
    """
    tree = _merge.build_effective(vpath, _merge.replace(cfg, overlays=())).tree
    if tree is None:
        return False
    if kind in LIBRARY_REGISTRIES:
        _vp, child, _klass, key = LIBRARY_REGISTRIES[kind]
        return any(el.get(key) == name for el in tree.findall(child))
    if kind in ("macro", "component"):
        els = [tree] if tree.tag == kind else tree.iter(kind)
        return any(el.get("name") == name for el in els)
    return True


def _winner_not_origin_note(vpath: str, chain_json: str | None, cfg=None,
                            kind: str | None = None,
                            name: str | None = None) -> str | None:
    """Disclaimer for a chain that shows a WINNER and reads like an ORIGIN (F64).

    A root ``<replace sel="//macros">`` swaps the whole document, so base
    contributes NO chain entry -- and that is VRO's dominant idiom (848
    root-replaces, CLAUDE.md #10). A single-entry chain therefore reads as
    "this mod introduced this" when it usually means "this mod re-supplied what
    vanilla already had".

    PEER-MEASURED: of 35,423 single-op root-replace attributes, 23,182 (65.4%)
    also exist in vanilla with the chain hiding it; only 39 vpaths are genuinely
    mod-added. `bullet_arg_m_ion_01_mk1_macro` is the sharpest case -- vanilla 10,
    live 10, chain ``[vro]``: identical value, sole credit, no hint a base file
    exists. It cost a real design conclusion ("VRO added Kha'ak shield
    disruption" -- false).

    SCOPE IS EXISTENCE, NOT THE VANILLA VALUE, on purpose. Reporting the value
    would mean re-deriving the property flatten, and a second implementation of a
    normaliser is exactly what made an independent check of this very defect
    report 2.6% where the answer was 65.4%. `base_has` answers existence, handles
    the DLC-prefix trap, and is not a second door.

    Returns None -- deliberately silent -- when there is nothing to disclaim:
    a pure-base value, a chain that already names base, or a vpath base+DLC do
    not ship (those 39 are genuinely mod-added and must not be slandered as
    re-supplies). A note that fires on the common case trains you to ignore it.
    """
    if not vpath or not chain_json:
        return None
    try:
        chain = json.loads(chain_json)
    except (ValueError, TypeError):  # silent-ok: a malformed chain is a STORE defect,
        # surfaced by the build and by claims_audit; this is an advisory annotation and
        # must not invent a second error channel for it.
        return None
    if len(chain) != 1 or not chain[0] or chain[0][0] == "base":
        return None
    try:
        cfg = cfg if cfg is not None else _merge.Config()
        if not _effective.base_has(cfg, vpath):
            return None
        # THE FILE IS NOT THE ENTITY (AUDIT-2026-09-24 AN-4). Base shipping
        # libraries/wares.xml says nothing about a ware a mod ADDED to it, and the
        # note then told the user a genuinely introduced ware had merely "won".
        # Given the entity, ask whether base+DLC actually DEFINE it.
        if kind and name and not _base_defines_entity(cfg, kind, vpath, name):
            return None
    except Exception:  # silent-ok: no resolvable reference tree means the question
        # 'does base ship this?' is UNANSWERABLE here, and an absent advisory note is a
        # non-answer while a wrong one would be a false claim. The caller's real output
        # (the chain itself) is unaffected, so there is no work silently not done.
        return None
    return (f"    (base+DLC ALSO supply {vpath} -- this chain shows which source "
            f"WON, not which introduced the value)")


def _fmt_chain(chain_json: str | None) -> str:
    if not chain_json:
        return "base"
    return " → ".join(f"{s} {op}" + (f":{ln}" if ln else "")
                      for s, op, ln in json.loads(chain_json))


# --- CLI ----------------------------------------------------------------------

@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Failure means the default codec
        # stays; it affects how output LOOKS, never what was examined.
    p = argparse.ArgumentParser(
        prog="x4effective",
        description="Browse the effective merged values of every X4 entity, with provenance.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    p.add_argument("--db", default=None,
                   help="effective store to read or build (default: $X4_EFFECTIVE_DB, or derived "
                        "from $X4_MODS / $X4_REGISTRY)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="(re)build the effective store")
    b.add_argument("--reference", default=str(_merge.REFERENCE),
                   help="unpacked base+DLC tree to build from (default: the configured reference tree)")
    # Derived, never re-typed: this default was a hardcoded "ware,macro,job" and
    # adding a kind to BUILDABLE_KINDS silently did not build it. Two sources of
    # truth for one list is the same defect class as five copies of the DLC walk.
    b.add_argument("--kinds", default=",".join(BUILDABLE_KINDS),
                   help="comma-separated entity kinds to build (default: %(default)s)")

    lsp = sub.add_parser("ls", help="list entities of a kind")
    lsp.add_argument("kind", help="entity kind as stored, e.g. ware, macro, job")
    lsp.add_argument("--class", dest="klass", default=None,
                     help="only entities of this class (the store's class column)")
    lsp.add_argument("--filter", default=None, help="substring match on name")
    lsp.add_argument("--modified-only", action="store_true",
                     help="only entities a MOD changed -- the entity, any of its "
                          "attributes, or a removal in its own file; base/DLC-only rows "
                          "are hidden")
    lsp.add_argument("--limit", type=int, default=200,
                     help="rows to print; the total is still counted (default: %(default)s)")

    sh = sub.add_parser("show", help="all props + provenance for one entity")
    sh.add_argument("kind", help="entity kind as stored, e.g. ware, macro, job")
    sh.add_argument("name", help="entity name (its id)")

    at = sub.add_parser("attr", help="one prop across all entities of a class")
    at.add_argument("kind", help="entity kind as stored, e.g. ware, macro, job")
    at.add_argument("prop", help="property key as the store spells it, e.g. hull.max "
                                 "(macro keys carry no properties. prefix)")
    at.add_argument("--class", dest="klass", default=None,
                    help="only entities of this class (the store's class column)")
    at.add_argument("--sort", choices=["num", "name"], default="name",
                    help="order by numeric value or by entity name (default: %(default)s)")
    at.add_argument("--limit", type=int, default=200,
                    help="rows to print; the total is still counted (default: %(default)s)")

    ws = sub.add_parser("who-sets", help="provenance chain for an entity['s prop]")
    ws.add_argument("kind", help="entity kind as stored, e.g. ware, macro, job")
    ws.add_argument("name", help="entity name (its id)")
    ws.add_argument("prop", nargs="?",
                    help="one property; omit it for the entity-level provenance chain")

    dm = sub.add_parser("diff-mod", help="every value a mod wins")
    dm.add_argument("folder", help="the mod's folder name, as it appears as a provenance origin")
    dm.add_argument("--limit", type=int, default=500,
                    help="rows to print; the total is still counted (default: %(default)s)")

    du = sub.add_parser("dump", help="live effective XML for ANY vpath (incl. md/, aiscripts/)")
    du.add_argument("vpath", help="path inside the game tree, e.g. libraries/wares.xml")
    du.add_argument("--reference", default=str(_merge.REFERENCE),
                    help="unpacked base+DLC tree to build from (default: the configured reference tree)")
    du.add_argument("--chain", action="store_true", help="also print the file-level source chain")

    sq = sub.add_parser("sql", help="read-only SELECT against the store")
    sq.add_argument("query", help="one read-only SELECT or WITH statement (anything else is refused)")

    sub.add_parser("coverage", help="what this store DOES and does not index "
                                    "(so a negative can carry its denominator)")

    args = p.parse_args(argv)
    db = Path(args.db) if args.db else _registry.require(
        effective_db(), "the effective-store location",
        "set X4_EFFECTIVE_DB or X4_MODS (or X4_REGISTRY), or pass --db")

    if args.cmd == "build":
        cfg = _merge.Config(reference=Path(args.reference))
        kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
        try:
            build(cfg, db, kinds=kinds, progress=lambda s: print(f"  {s}", file=sys.stderr))
        except (ValueError, OSError) as exc:
            # A bad --kinds and a lost install race are both ordinary operator
            # errors with an actionable message; a traceback would bury it.
            print(f"x4effective build: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "dump":
        return _cmd_dump(args)

    # v4.0.0 review R5-2: the store-failure label ("unreadable ... Rebuild") belongs to
    # STORE failures only -- opening it, its schema and freshness metadata, and SQLite errors
    # while reading it. It used to wrap every OSError/ValueError a command raised, so
    # `x4effective ls ware | head` (BrokenPipeError IS an OSError) told the user to rebuild a
    # healthy store, and a command bug read as store damage.
    con = None
    try:
        try:
            con = _connect(db)
            return _read_store(con, args)
        except (sqlite3.Error, OSError, StoreMetadataError) as exc:
            if isinstance(exc, BrokenPipeError):
                raise
            if con is not None and not isinstance(exc, (sqlite3.Error, StoreMetadataError)):
                raise          # an OSError from a COMMAND, after the store opened fine
            print(f'effective store unreadable or incompatible: {db}: {exc}\n'
                  'Rebuild: uv run x4effective build', file=sys.stderr)
            return 2
        except ValueError as exc:
            if con is not None:
                raise          # a command's own ValueError is a bug, never store damage
            print(f'effective store unreadable or incompatible: {db}: {exc}\n'
                  'Rebuild: uv run x4effective build', file=sys.stderr)
            return 2
    except BrokenPipeError:
        # The reader went away (`| head`): what was printed was printed. Python's own
        # recipe: point stdout at devnull so the interpreter's exit flush cannot raise again.
        _quiet_stdout()
        return 0
    finally:
        if con is not None:
            con.close()


class StoreMetadataError(ValueError):
    """The store's freshness metadata is damaged: a STORE failure, unlike a command's own
    ValueError."""


def _quiet_stdout() -> None:
    import os
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):  # silent-ok: best effort after a closed pipe
        pass


def _read_store(con, args) -> int:
    # Printed on EVERY read command until the store is rebuilt. A stale store is
    # not an absence and not a non-answer -- it is an answer about a world that
    # has moved on, and these are the commands a modlist decision leans on.
    try:
        _stale = store_freshness(con)
    except StoreMetadataError:
        raise
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        # A SQLite file may have a valid schema but damaged JSON freshness data.
        # Convert only this metadata boundary; SQL/user-command errors keep their
        # existing contracts in the dispatcher below.
        raise StoreMetadataError(f'invalid freshness metadata: {exc}') from exc
    if not _stale.fresh:
        print(_stale.banner("the effective store"), file=sys.stderr)
        print("!! Rebuild:  uv run x4effective build", file=sys.stderr)

    if args.cmd == "ls":
        return _cmd_ls(con, args)
    if args.cmd == "show":
        return _cmd_show(con, args)
    if args.cmd == "attr":
        return _cmd_attr(con, args)
    if args.cmd == "who-sets":
        return _cmd_who_sets(con, args)
    if args.cmd == "diff-mod":
        return _cmd_diff_mod(con, args)
    if args.cmd == "sql":
        return _cmd_sql(con, args)
    if args.cmd == "coverage":
        return _cmd_coverage(con, args)
    return 2


def _known_kinds(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT kind FROM entities ORDER BY kind").fetchall()]


def _reject_unknown_kind(con, kind: str) -> bool:
    """True (and prints) if *kind* is not a stored kind.

    Without this an unknown kind reads as a confident empty answer: `ls ship`
    printed "0 ship(s)" — which looks like "this game has no ships" rather than
    "there is no such kind". Same false-negative shape as a validator reporting OK
    because it examined nothing.

    ⚠ The hint below is for a PARTIAL build. A full build now stores kind='ship'
    (MEASURED 2026-08-27: 514 entities), plus 'station' and 'module', so for those
    three this branch is unreachable — but `--kinds` can build a subset, which is
    exactly when the hint earns its place, so it stays. What had rotted was this
    docstring's claim that ships are stored ONLY as kind='macro': a ship's balance
    props do live on kind='macro' (hull.max: 1,973 values there, 0 on kind='ship'),
    but the kind itself exists, so the guard passes the name through and the
    unknown-PROP guard is what has to catch it.
    """
    kinds = _known_kinds(con)
    if kind in kinds:
        return False
    print(f"unknown kind {kind!r} — stored kinds are: {', '.join(kinds) or '(store is empty; run build)'}",
          file=sys.stderr)
    if kind in {"ship", "engine", "shield", "turret", "weapon", "station", "module"}:
        print(f"       ships/equipment are stored as kind 'macro' "
              f"(try: ls macro --filter {kind}) and as kind 'ware'", file=sys.stderr)
    return True


def _reject_unknown_prop(con, kind: str, prop: str) -> bool:
    """True (and prints) if no entity of *kind* carries *prop*.

    The companion to _reject_unknown_kind, and it was missing from the one command
    that needed it most. `attr` printed "0 value(s) for properties.hull.max" and
    exited 0 -- a confident zero over a key that cannot exist, which reads as "no
    mod sets this" rather than "you spelled it the way the store does not".

    The predictable wrong guess is the <properties> wrapper: for MACRO entities the
    store strips it (`hull.max`), because emitting both was F9. A parallel session
    hand-rolled a flatten that kept it, matched only the keys living OUTSIDE
    <properties>, and reported 2.6% where the truth was 65.4% -- plausible and
    self-consistent, so nothing looked wrong.

    The strip is NOT universal: MEASURED 6,842 rows keep a `properties.` prefix,
    all of them kind='mapdataset' (`properties.area.sunlight`), with 0 duplicate
    pairs. So the suggestion is looked up IN THE STORE rather than derived from a
    rule -- a blanket strip would be wrong for every one of those rows.
    """
    hit = con.execute("SELECT COUNT(*) FROM attrs a JOIN entities e ON e.id=a.entity_id "
                      "WHERE e.kind=? AND a.prop=?", (kind, prop)).fetchone()[0]
    if hit:
        return False
    total = con.execute("SELECT COUNT(DISTINCT a.prop) FROM attrs a "
                        "JOIN entities e ON e.id=a.entity_id WHERE e.kind=?",
                        (kind,)).fetchone()[0]
    print(f"no {kind} carries prop {prop!r} - that kind has {total} distinct prop(s)",
          file=sys.stderr)
    for alt in _prop_suggestions(con, kind, prop):
        n = con.execute("SELECT COUNT(*) FROM attrs a JOIN entities e ON e.id=a.entity_id "
                        "WHERE e.kind=? AND a.prop=?", (kind, alt)).fetchone()[0]
        print(f"       did you mean {alt!r}? ({n} value(s))", file=sys.stderr)
    # A prop can be real and the KIND can be real while the pair matches nothing:
    # `attr ship hull.max` is 0 rows because hull.max is a macro prop. Rejecting
    # without saying where it DOES live leaves the caller as stuck as the zero did.
    elsewhere = con.execute(
        "SELECT e.kind, COUNT(*) FROM attrs a JOIN entities e ON e.id=a.entity_id "
        "WHERE a.prop=? AND e.kind != ? GROUP BY e.kind ORDER BY 2 DESC LIMIT 3",
        (prop, kind)).fetchall()
    for row in elsewhere:
        print(f"       {prop!r} is carried by kind {row[0]!r} ({row[1]} value(s))",
              file=sys.stderr)
    return True


def _prop_suggestions(con, kind: str, prop: str, limit: int = 3) -> list[str]:
    """Stored props for *kind* a caller plausibly meant. Store-derived, never a rule."""
    out: list[str] = []
    cand = (prop[len("properties."):] if prop.startswith("properties.")
            else "properties." + prop)
    r = con.execute("SELECT 1 FROM attrs a JOIN entities e ON e.id=a.entity_id "
                    "WHERE e.kind=? AND a.prop=? LIMIT 1", (kind, cand)).fetchone()
    if r is not None:
        out.append(cand)
    # last-segment match catches the other half of the guesses (`max` for `hull.max`)
    tail = prop.rsplit(".", 1)[-1]
    for (p,) in con.execute(
            "SELECT DISTINCT a.prop FROM attrs a JOIN entities e ON e.id=a.entity_id "
            "WHERE e.kind=? AND (a.prop = ? OR a.prop LIKE ?) ORDER BY a.prop LIMIT ?",
            (kind, tail, "%." + tail, limit)):
        if p not in out:
            out.append(p)
    return out[:limit]


#: SQL predicate: does the provenance chain in column *col* name a MOD? "A mod" is
#: the store's own `mods` table -- the active extensions the build merged, which by
#: construction never includes a DLC (`_registry.mods` skips them) -- never a folder-
#: name prefix test. Base and DLC sources are therefore not "modified".
def _chain_names_a_mod(col: str) -> str:
    return (f"({col} IS NOT NULL AND EXISTS (SELECT 1 FROM json_each({col}) j "
            f"WHERE json_extract(j.value, '$[0]') IN (SELECT folder FROM mods)))")


#: An entity is MODIFIED when a mod appears in the provenance of the entity itself,
#: of ANY of its attributes, or of a removal recorded against its file when that file
#: holds only this entity (a removal in a shared file such as wares.xml cannot be
#: tied to one entity from the store -- `_removal_note` says the same).
#: AUDIT-2026-09-24 AN-3: this used to be `entities.chain IS NOT NULL`, which hid
#: every attribute-only mod change (1,449 entities on the audit store) and listed
#: every DLC-only entity as modified.
_MODIFIED_SQL = (
    "(" + _chain_names_a_mod("entities.chain")
    + " OR EXISTS (SELECT 1 FROM attrs a WHERE a.entity_id = entities.id AND "
    + _chain_names_a_mod("a.chain") + ")"
    + " OR (EXISTS (SELECT 1 FROM removed r WHERE r.vpath = entities.vpath AND "
      "r.source IN (SELECT folder FROM mods))"
      " AND (SELECT count(*) FROM entities e2 WHERE e2.vpath = entities.vpath) = 1))")


def _modifying_mods(con, ent_id: int, ent_chain: str | None, vpath: str) -> list[str]:
    """The mods in one entity's provenance (entity, attributes, sole-entity removals),
    the entity's own chain first, then by how many values each one last set."""
    mods = {r[0] for r in con.execute("SELECT folder FROM mods")}
    order: dict[str, int] = {}
    for s, *_ in json.loads(ent_chain or "[]"):
        if s in mods:
            order.setdefault(s, 10 ** 9)
    for (chain,) in con.execute("SELECT chain FROM attrs WHERE entity_id=? AND chain IS "
                                "NOT NULL", (ent_id,)):
        for s in {e[0] for e in json.loads(chain)} & mods:
            order[s] = order.get(s, 0) + 1
    if con.execute("SELECT count(*) FROM entities WHERE vpath=?", (vpath,)).fetchone()[0] == 1:
        for (s,) in con.execute("SELECT DISTINCT source FROM removed WHERE vpath=?", (vpath,)):
            if s in mods:
                order.setdefault(s, 0)
    return sorted(order, key=lambda s: (-order[s], s))


def _sql_limit(limit: int) -> int:
    """`--limit` as a SQLite `LIMIT ?` parameter.

    `_count_line`'s own TRUNCATED message tells the user "use --limit N (or 0) for
    all" -- but SQLite's `LIMIT 0` returns ZERO rows, not unlimited. SQLite treats a
    NEGATIVE limit as "no limit" (this is a bound parameter, not literal SQL text,
    so -1 is safe to pass straight through), so 0 is translated to -1 here and
    every other value passes through unchanged.
    """
    return -1 if limit == 0 else limit


def _cmd_ls(con, args) -> int:
    if _reject_unknown_kind(con, args.kind):
        return 2
    q = "SELECT id, name, klass, vpath, origin, chain FROM entities WHERE kind=?"
    params: list = [args.kind]
    if args.klass:
        q += " AND klass=?"
        params.append(args.klass)
    if args.filter:
        q += " AND name LIKE ?"
        params.append(f"%{args.filter}%")
    if args.modified_only:
        q += " AND " + _MODIFIED_SQL
    total = con.execute(q.replace("SELECT id, name, klass, vpath, origin, chain",
                                  "SELECT count(*)"), params).fetchone()[0]
    q += " ORDER BY name LIMIT ?"
    params.append(_sql_limit(args.limit))
    rows = con.execute(q, params).fetchall()
    for r in rows:
        # The marker names the MODS in this entity's provenance -- not the entity's
        # own origin, which is `base` for an attribute-only change and a DLC for a
        # DLC-only entity (AN-3). No marker = no mod touched it.
        who = _modifying_mods(con, r["id"], r["chain"], r["vpath"])
        mod = "" if not who else "  ← " + ", ".join(who[:3]) + (
            f" +{len(who) - 3}" if len(who) > 3 else "")
        print(f"{r['name']:<44} {r['klass']:<16}{mod}")
    print(f"\n{_count_line(len(rows), total, f'{args.kind}(s)')}  ·  {_ADVISORY}")
    return 0


def scope_note() -> str:
    """What this store DOES and does NOT index, in one line.

    A query that misses must never read like "no mod changes this". MEASURED
    2026-08-12: 3,349 of 7,995 corpus macros are outside the store -- the galaxy
    map (~1,371: zones, sectors, clusters, highways) and characters/npc (~1,810)
    -- while balance-relevant classes are 99.0% covered. Neither number is
    guessable from a bare "not found".
    """
    return (f"scope: {len(LIBRARY_REGISTRIES)} registry kinds from libraries/*.xml "
            f"({', '.join(sorted(LIBRARY_REGISTRIES))}), plus macro and component "
            f"per-file from assets/**. "
            f"NOT indexed: galaxy-map macros (zone/sector/cluster/highway); character & npc "
            f"MACROS (distinct from the `character` registry, which IS indexed); the structured "
            f"library documents god/parameters/loadoutrules/camerasettings; a component's "
            f"geometry (source/layers — indexing it measured 148x the whole store); and Lua "
            f"(never analysed by any tool). "
            f"See docs/BLIND-SPOTS.md for the measured denominators.")


def _cmd_coverage(con, args) -> int:
    """Answer 'what can this tool NOT see?' as a command, not as archaeology.

    Mirrors BaseX's coverage-<db>.json: the point is that scope is queryable, so a
    negative can carry its denominator instead of being taken on trust.
    """
    meta = dict(con.execute("SELECT key, value FROM meta"))
    print("x4effective coverage")
    print(f"  store           : {meta.get('active_mods', '?')} active mods indexed")
    ents = con.execute("SELECT kind, count(*) FROM entities GROUP BY kind ORDER BY 2 DESC").fetchall()
    total = sum(r[1] for r in ents)
    print(f"  entities        : {total}")
    for kind, n in ents:
        print(f"      {kind:<12} {n}")
    print(f"  attributes      : "
          f"{con.execute('SELECT count(*) FROM attrs').fetchone()[0]}")
    origins = con.execute("SELECT count(DISTINCT origin) FROM entities").fetchone()[0]
    print(f"  distinct origins: {origins}")

    # THE DROPPED OVERLAYS, WHICH NOTHING READ. `_write_db` has persisted
    # `dropped_overlays` since 0bb435f, on the stated grounds that build-time progress
    # "scrolls past and is gone" and "a reader can now ask the store itself whether it
    # was built over work that did not happen". MEASURED by the v3.1.0 release
    # reviewer: no shipped reader asked. The key was written, and read only by its own
    # test -- and THIS command, whose docstring is "answer 'what can this tool NOT
    # see?' as a command, not as archaeology", was silent about it.
    #
    # That silence is the expensive kind: an overlay the merge could not READ leaves
    # the store recording origin='base' -- "still vanilla" -- in the same grammar it
    # uses for a verified base value, so `who-sets` answers confidently and wrongly.
    dropped = int(meta.get("dropped_overlays") or 0)
    print(f"  dropped overlays: {dropped}"
          + ("" if dropped else "   (none -- every overlay was readable)"))
    if dropped:
        samples = (meta.get("dropped_overlay_samples") or "").strip()
        print("      ⚠ THIS STORE WAS BUILT OVER WORK THAT DID NOT HAPPEN. Those "
              "overlays could not be")
        print("        read, so any value they would have set reads as its BASE value "
              "here, with")
        print("        origin='base' -- indistinguishable from a verified vanilla "
              "value.")
        if samples:
            # Stored as JSON; rendered ONE PER LINE. A raw list repr on one line is
            # technically the same information and unreadable, which is how a
            # disclosure gets skipped.
            try:
                items = _json.loads(samples)
            except ValueError:
                items = [samples]
            for item in items[:10]:
                print(f"        - {str(item).split(': ', 1)[0]}")
            if len(items) > 10:
                print(f"        ... and {len(items) - 10} more")
    print()
    print(f"  {scope_note()}")
    return 0


def _one_entity(con, kind: str, name: str):
    """The entity for (kind, name) -- and a NAMED warning when there is more than one.

    `entities` has no UNIQUE constraint on (kind, name), and `fetchone()` with no
    ORDER BY took whichever row SQLite happened to return. MEASURED 2026-09-04 on the
    live store: 63 duplicated (kind, name) pairs of 23,159 entities -- 34 of them
    kind='macro', and ALL 34 disagree on vpath. `bullet_ter_turret_m_laser_01_mk1_macro`
    resolves to two rows with 24 and 18 properties; `cluster_sm3_background_macro` has
    SIX rows across six vpaths and two origins, five with chain NULL and one
    full-override -- so `who-sets` answered "ego_dlc_timelines full-override" on a
    coin toss.

    Ordered by id so the choice is at least deterministic, and the alternatives are
    printed, because a value silently chosen from several is the narrowing step this
    whole tool exists to announce.
    """
    rows = con.execute("SELECT * FROM entities WHERE kind=? AND name=? ORDER BY id",
                       (kind, name)).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        print(f"NOTE: {len(rows)} entities share ({kind}, {name!r}); showing the "
              f"lowest id. They are NOT interchangeable:", file=sys.stderr)
        # vpath ALONE cannot tell rows apart when they share a file: since issue #9
        # every same-named wildcard <icon> (`upgrade_*`: 8 on base + 8 DLC) is its own
        # entity in libraries/icons.xml. The class (an icon's texture) and the origin
        # are what distinguish them.
        keys = rows[0].keys()
        for r in rows:
            vp = r["vpath"] if "vpath" in keys else "?"
            klass = f"  class={r['klass']}" if "klass" in keys and r["klass"] else ""
            origin = f"  origin={r['origin']}" if "origin" in keys else ""
            print(f"      id={r['id']}  {vp}{klass}{origin}", file=sys.stderr)
    return rows[0]


def _cmd_show(con, args) -> int:
    ent = _one_entity(con, args.kind, args.name)
    if ent is None:
        if _reject_unknown_kind(con, args.kind):
            return 2
        print(f"no {args.kind} named {args.name!r} in the store — this is NOT proof "
              f"that nothing defines or changes it.", file=sys.stderr)
        print(f"  {scope_note()}", file=sys.stderr)
        return 1
    print(f"{args.kind} {ent['name']}  (class={ent['klass']})  vpath={ent['vpath']}")
    print(f"  entity origin: {_fmt_chain(ent['chain'])}")
    rows = con.execute(
        "SELECT prop, value, origin, chain FROM attrs WHERE entity_id=? ORDER BY prop",
        (ent["id"],)).fetchall()
    for r in rows:
        prov = "" if r["chain"] is None else f"   [{_fmt_chain(r['chain'])}]"
        print(f"  {r['prop']:<32} = {r['value']}{prov}")
    print(f"\n{len(rows)} properties  ·  {_ADVISORY}")
    return 0


def _cmd_attr(con, args) -> int:
    # Both arguments can be individually plausible while the PAIR matches nothing.
    # Without these, `attr zzznotakind hull.max` printed "0 value(s)" and exited 0.
    if _reject_unknown_kind(con, args.kind):
        return 2
    if _reject_unknown_prop(con, args.kind, args.prop):
        return 1
    q = ("SELECT e.name, e.klass, a.value, a.value_num, a.origin, a.chain "
         "FROM attrs a JOIN entities e ON e.id=a.entity_id "
         "WHERE e.kind=? AND a.prop=?")
    params: list = [args.kind, args.prop]
    if args.klass:
        q += " AND e.klass=?"
        params.append(args.klass)
    total = con.execute(
        q.replace("SELECT e.name, e.klass, a.value, a.value_num, a.origin, a.chain",
                  "SELECT count(*)"), params).fetchone()[0]
    order = "a.value_num" if args.sort == "num" else "e.name"
    q += f" ORDER BY {order} LIMIT ?"
    params.append(_sql_limit(args.limit))
    rows = con.execute(q, params).fetchall()
    for r in rows:
        mod = "" if r["chain"] is None else f"  ← {r['origin']}"
        print(f"{r['name']:<44} {r['value']:>14}{mod}")
    print(f"\n{_count_line(len(rows), total, f'value(s) for {args.prop}')}  ·  {_ADVISORY}")
    return 0


def _cmd_who_sets(con, args) -> int:
    ent = _one_entity(con, args.kind, args.name)
    if ent is None:
        if _reject_unknown_kind(con, args.kind):
            return 2
        print(f"no {args.kind} named {args.name!r}", file=sys.stderr)
        return 1
    if args.prop:
        r = con.execute("SELECT chain FROM attrs WHERE entity_id=? AND prop=?",
                        (ent["id"], args.prop)).fetchone()
        if r is None:
            print(f"no prop {args.prop!r} on {args.name}", file=sys.stderr)
            return 1
        print(f"{args.name}.{args.prop}: {_fmt_chain(r['chain'])}")
        _note = _winner_not_origin_note(ent["vpath"], r["chain"],
                                        kind=args.kind, name=ent["name"])
        if _note:
            print(_note)
    else:
        print(f"{args.name} (entity): {_fmt_chain(ent['chain'])}")
        _note = _winner_not_origin_note(ent["vpath"], ent["chain"],
                                        kind=args.kind, name=ent["name"])
        if _note:
            print(_note)
        _print_changed_props(con, ent, args)
    return 0


def _print_changed_props(con, ent, args) -> None:
    """Which of this entity's properties something other than base last set, by origin.

    The entity line above is the ENTITY's origin only. Asked "who set energycells?" it
    answered `base` while `show` revealed price.min and price.max set by a mod (cold E2E
    agent, 2026-09-14) -- a narrowing that did not announce itself. It is named in terms
    of ORIGIN, never "a mod": MEASURED over the real store the same day, 123,414 of the
    296,608 attributes with a non-base origin (42%) were last set by a DLC. `origin` is
    'base' exactly where `chain` is NULL (294,940 + 296,608 = all 591,548 rows).
    """
    rows = con.execute("SELECT prop, origin FROM attrs WHERE entity_id=? ORDER BY prop",
                       (ent["id"],)).fetchall()
    changed = [r for r in rows if r["origin"] != "base"]
    removals = _removal_note(con, ent)
    if not changed:
        print(f"  0 of {len(rows)} stored properties set by anything but base")
        print(removals or "  -- every stored property is its base value, and no removal is "
                          "recorded against its file.")
        return
    by_origin: dict[str, list[str]] = {}
    for r in changed:
        by_origin.setdefault(r["origin"], []).append(r["prop"])
    ranked = sorted(by_origin.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    print(f"  {len(changed)} of {len(rows)} properties were last set by something other "
          f"than base -- the line above is the ENTITY's origin only:")
    for origin, props in ranked[:5]:
        print(f"    {origin:<40} {len(props):>4}   e.g. {props[0]}")
    if len(ranked) > 5:
        print(f"    ... and {len(ranked) - 5} more origin(s)")
    print(f"  per property: `x4effective show {args.kind} {args.name}`, or "
          f"`x4effective who-sets {args.kind} {args.name} <prop>`")
    if removals:
        print(removals)


def _removal_note(con, ent) -> str | None:
    """Removals recorded against this entity's FILE, or None when there are none.

    A removed value is not a property: it is written to the `removed` table, not `attrs`. So
    counting properties alone called an entity "every property is still its base value"
    when a mod had removed one of its attributes (review, 2026-09-14). Rows are keyed by
    vpath and a positional node path, so they tie to ONE entity only when that file holds
    one. MEASURED on the real store: 108 vpaths carry removals -- 98 hold one entity, 9 hold
    several (shared libraries such as wares.xml), 1 holds none.
    """
    n, sources = con.execute("SELECT count(*), group_concat(DISTINCT source) FROM removed "
                             "WHERE vpath=?", (ent["vpath"],)).fetchone()
    if not n:
        return None
    who = ", ".join(sorted(set((sources or "").split(","))))
    shared = con.execute("SELECT count(*) FROM entities WHERE vpath=?",
                         (ent["vpath"],)).fetchone()[0]
    if shared <= 1:
        return (f"  and {n} removal(s) against this entity's file by {who} -- a removed value "
                f"is not stored as a property, so the count above cannot show it.")
    return (f"  and {n} removal(s) recorded against its file ({ent['vpath']}, shared by {shared} "
            f"entities) by {who} -- not attributable to one entity from the store.")


def _reject_unknown_origin(con, folder: str) -> bool:
    """True (and prints) if *folder* is not a stored origin.

    `diff-mod` was the one read command taking an identifier with NO guard, while its
    two siblings each carry a paragraph explaining that a confident zero over a name
    that CANNOT exist is the defect. MEASURED 2026-09-04 against the live store:
    `x4effective diff-mod zzznotamodatall` printed "0 value(s) won by
    zzznotamodatall", rc 0 -- which reads as "this mod overrides nothing" rather than
    "there is no such mod". The comment two lines below calls that count "the
    headline number a balance discussion turns on".

    Origins are trivially enumerable (base 294,940 / vro 62,673 / ... on this store),
    so there is no excuse for guessing at one.
    """
    have = [r[0] for r in con.execute(
        "SELECT DISTINCT origin FROM attrs ORDER BY origin").fetchall() if r[0]]
    if folder in have:
        return False
    print(f"{folder!r} is not a stored origin, so '0 values won' would be a "
          f"statement about the NAME, not about the mod.", file=sys.stderr)
    near = [o for o in have if folder.lower() in o.lower() or o.lower() in folder.lower()]
    if near:
        print("  did you mean: " + ", ".join(sorted(near)[:8]), file=sys.stderr)
    else:
        print(f"  {len(have)} stored origin(s); the largest are: "
              + ", ".join(sorted(have)[:8]), file=sys.stderr)
    return True


def _cmd_diff_mod(con, args) -> int:
    if _reject_unknown_origin(con, args.folder):
        return 2
    total = con.execute("SELECT COUNT(*) FROM attrs a JOIN entities e ON e.id=a.entity_id "
                        "WHERE a.origin=?", (args.folder,)).fetchone()[0]
    rows = con.execute(
        "SELECT e.kind, e.name, a.prop, a.value FROM attrs a "
        "JOIN entities e ON e.id=a.entity_id WHERE a.origin=? "
        "ORDER BY e.kind, e.name, a.prop LIMIT ?", (args.folder, _sql_limit(args.limit))).fetchall()
    for r in rows:
        print(f"{r['kind']:<6} {r['name']:<40} {r['prop']:<28} = {r['value']}")
    # "N value(s) won by X" is the headline number a balance discussion turns on;
    # printing the LIMIT as if it were the total makes that number wrong, not
    # merely the list incomplete.
    print(f"\n{_count_line(len(rows), total, f'value(s) won by {args.folder}')}  ·  {_ADVISORY}")
    return 0


def _cmd_sql(con, args) -> int:
    q = args.query.strip()
    if not q.lower().startswith(("select", "with")):
        print("only SELECT/WITH queries allowed", file=sys.stderr)
        return 2
    try:
        rows = con.execute(q).fetchall()
    except sqlite3.Error as exc:
        if getattr(exc, 'sqlite_errorcode', 0) & 255 in (
                sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB,
                sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN):
            raise
        print(f"sql error: {exc}", file=sys.stderr)
        return 1
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r))
    return 0


def logical_vpath(vpath: str, mod_folders: set[str],
                  dlc_folders: set[str]) -> str | None:
    """LOGICAL (game) vpath for a PHYSICAL ``extensions/<mod>/<rel>`` disk path.

    Returns None -- meaning "leave it alone" -- when *vpath* is already logical,
    names a DLC, names a folder that is not an installed mod, or is double-nested.

    **Why a DLC is excluded rather than handled.** For a DLC,
    ``extensions/ego_dlc_split/md/story_split.xml`` IS the game vpath: the engine
    builds that document under that name, and `_effective.build_touch_map`
    deliberately does not rewrite it. For a MOD the identical shape is a path on
    disk with no engine meaning -- the document the engine builds is
    ``md/morerooms.xml``. That asymmetry is the engine's, not ours.

    **Double nesting mirrors ``build_touch_map`` (F305).** ``extensions/<modA>/
    extensions/<DLC>/<rel>`` -- modA ships a file at a DLC vpath and this patches
    modA's copy -- maps to ``extensions/<DLC>/<rel>``, the document the store keys it
    under (engine-opened, MEASURED 2026-10-19; see `_modfiles.logical_vpath`). With a
    MOD, or a folder that is not a known DLC, in the inner position it is still
    refused: that shape is unmeasured, and resolving it here would let the CLI answer
    a question the merge model declines to answer.

    *dlc_folders* is asked of ``Config.dlc_dirs()``, never guessed from an
    ``ego_dlc_`` name prefix: a prefix test cannot notice a DLC being added, and
    `tests/test_dlc_enumeration.py` bans the form because this workspace wrote
    that guess six times. The guard caught this function on its first run.

    MEASURED 2026-08-27 (F71): 1,713 of 3,257 touched vpaths are mod-owned, so a
    missing physical form rendered a *wrong form* as a confident *absence*.
    """
    parts = [p for p in vpath.replace("\\", "/").split("/") if p]
    if len(parts) < 3 or parts[0].lower() != "extensions":
        return None
    folder = parts[1].lower()
    if folder in {d.lower() for d in dlc_folders}:
        return None
    if folder not in {f.lower() for f in mod_folders}:
        return None
    if parts[2].lower() == "extensions":
        if len(parts) < 5 or parts[3].lower() not in {d.lower() for d in dlc_folders}:
            return None
    return "/".join(parts[2:])


def _cmd_dump(args) -> int:
    cfg = _merge.Config(reference=Path(args.reference))
    mods = active_mods()
    # Not in the tree below, correctly -- and NAMED, or a value only that mod sets
    # would read as vanilla with nothing said (F139).
    left_out = _registry.dropped_note(mods)
    if left_out:
        print(f"NOT in this tree (the engine does not load it): {left_out}",
              file=sys.stderr)
    ordered = ordered_overlays(mods)
    model = _registry.model_note(mods)          # AFTER ordering: its records ride on mods
    if model:
        print(f"load-order model of this tree: {model}", file=sys.stderr)
    folder_to_path = {m["folder"]: p for m, p in ordered}
    touch = build_touch_map(ordered)
    ov = touchers_for(args.vpath, touch, folder_to_path)
    rec = Recorder()
    res = _merge.build_effective(args.vpath, cfg, extra_overlays=ov, recorder=rec)

    # A miss may be the wrong FORM rather than an absence. Retry the logical vpath
    # -- but only AFTER the literal one has genuinely failed, so every path that
    # resolves today keeps resolving by exactly the route it does now.
    vpath, note_folder, note_alt = args.vpath, None, None
    if res.tree is None:
        alt = logical_vpath(args.vpath, {m["folder"] for m in mods},
                            {d.name for d in cfg.dlc_dirs()})
        if alt is not None:
            alt_rec = Recorder()
            alt_res = _merge.build_effective(
                alt, cfg, extra_overlays=touchers_for(alt, touch, folder_to_path),
                recorder=alt_rec)
            if alt_res.tree is not None:
                note_folder = [p for p in args.vpath.replace("\\", "/").split("/") if p][1]
                note_alt = alt
                vpath, res, rec = alt, alt_res, alt_rec

    if res.tree is None:
        print(f"no effective content for {args.vpath}", file=sys.stderr)
        return 1
    # Printed whether or not --chain: resolving a reinterpreted path SILENTLY would
    # trade one confident-wrong answer for another.
    if note_alt is not None:
        print(f"<!-- note: '{note_folder}' is an installed MOD, not a DLC, so that is "
              f"a disk path, not a game vpath;")
        print(f"     interpreted as logical vpath '{note_alt}' -->")
    if args.chain:
        print(f"<!-- sources: {', '.join(res.sources)} -->")
        if "base" not in res.sources and _effective.base_has(cfg, vpath):
            print(f"<!-- note: base+DLC ALSO supply {vpath}; "
                  f"sources show which WON, not which introduced it -->")
    # THE SKIP CHANNEL HAS A READER HERE TOO. `MergeResult.skipped` says in its own
    # docstring what its absence costs: "without this channel a malformed overlay is
    # indistinguishable from an absent one, and the resulting tree looks complete when
    # it is not". `_cmd_coverage` gained that reader in-arc (6c7b8ac); this sibling did
    # not, so a dropped overlay printed as a clean tree -- and with --chain the
    # `sources:` line OMITS it, which is worse than silence because it looks like an
    # enumeration.
    #
    # Disclosed unconditionally, not behind --chain: a caller who did not ask for the
    # chain is exactly the caller who will read this tree as whole.
    if res.skipped:
        print("<!-- INCOMPLETE: " + str(len(res.skipped)) + " overlay(s) could not be"
              " read and are NOT in this tree.")
        for item in res.skipped:
            print("       " + str(item))
        print("     The engine cannot read them either, so what it loads differs from"
              " what this shows. -->")
    print(etree.tostring(res.tree, pretty_print=True, encoding="unicode"))
    # rc 3 == DEGRADED, the toolkit contract: a tree was produced, but not over the
    # whole population. Never 0 -- "could not check part of it" is not a pass.
    return 3 if res.skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())

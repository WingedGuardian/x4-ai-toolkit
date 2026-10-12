#!/usr/bin/env python
r"""Techniques the other 19 gates do not apply at all.

Written after noticing that the previous round stopped because I asked the
stopping question, not because the search was exhausted — and that the question
itself found three more issues. So: what has NOTHING checked yet?

  1. SENSITIVITY. `tool_properties` proves x4diff is self-consistent (identity,
     antisymmetry). Both hold TRIVIALLY for a tool that under-reports — a diff
     that always answered "no changes" passes them. Nothing ever verified it
     DETECTS a known planted delta, or that the counts are the RIGHT numbers.

  2. CROSS-TOOL AGREEMENT. Every audit so far compares a tool to itself or to a
     fresh parse. Two different tools answering the same question differently is
     a whole failure class nothing looks for: x4compat names a winner for a
     contested file; x4effective records an origin for entities in that file.
     They must be the same mod -- but ONLY for the kinds where `winner` means
     "supplies the live value". It does not mean that for SUBTREE (the winner is
     the WIPER) or NAME-CLASH (deliberately empty), so the assertion is PER KIND.
     Until 2026-08-13 this checked FULL-OVERRIDE alone -- 14 of 445 collisions,
     3.1%, with HARD/SUBTREE/UNION-KEY never verified against the store at all.

  3. BUILDER IDEMPOTENCE. `determinism_audit` covers x4compat's output ordering.
     The BUILDERS were never checked: build the same store twice from unchanged
     inputs and the contents must be identical. A builder that varies is one
     whose every downstream answer is unreproducible.

  4. READ-ONLY HARDENING. `qa_sweep` proves `x4effective sql` rejects DELETE.
     That is one verb. ATTACH, PRAGMA, and multi-statement payloads are the ways
     a "read-only" SQL surface actually gets subverted.

Run:  uv run python gates/cross_tool.py
Exit: 0 all checks hold, 1 any violation, 2 a section could not run (no/stale
      store, or a collision kind whose every row was uncheckable -- 0 of N is a
      non-answer, not agreement).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402
from x4validate import _compat, _merge, _registry  # noqa: E402

# NOTE: `EXT = _env.extensions()` used to live here, resolved at IMPORT.
# `_env` reports "no install" by raising SystemExit, so importing this module
# required a configured machine -- and tests/test_cross_tool_semantics.py
# imports it for pure mapping helpers that touch no path at all. On a fresh
# clone its 15 tests were never collected, shown as ONE skip (F42). The single
# real use now calls _env.extensions() where it is needed.

failures: list[str] = []
#: sections that could not RUN -- a missing or stale store. Kept apart from `failures`:
#: a section that examined nothing has found no defect, and must not pass either.
cannot: list[str] = []


def note(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'  ok ' if ok else ' FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(f"{label}: {detail}")


def not_run(label: str, detail: str) -> None:
    print(f"  CANNOT {label}  {detail}")
    cannot.append(f"{label}: {detail}")


def run(tool: str, *argv: str) -> tuple[int, str]:
    p = subprocess.run(["uv", "run", tool, *argv], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1800)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ------------------------------------------------------------- 1. sensitivity

def _mod(root: Path, wares: list[tuple[str, int]], extra: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "content.xml").write_text('<content id="sens" version="100"/>\n', encoding="utf-8")
    lib = root / "libraries"
    lib.mkdir(exist_ok=True)
    body = "".join(f'<ware id="{w}"><price average="{v}"/></ware>\n' for w, v in wares)
    (lib / "wares.xml").write_text(f"<wares>\n{body}</wares>\n", encoding="utf-8")
    for rel, text in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def check_sensitivity(tmp: Path) -> None:
    print("\n1. x4diff SENSITIVITY — does it detect a KNOWN planted delta?")
    base = [(f"w{i}", i * 100) for i in range(1, 6)]

    cases = [
        ("1 attr changed", [(w, 999 if w == "w1" else v) for w, v in base], None, None, (1, 0, 0, 1)),
        ("3 attrs changed", [(w, v + 1 if w in {"w1", "w2", "w3"} else v) for w, v in base],
         None, None, (1, 0, 0, 3)),
        ("1 file added", base, {"libraries/jobs.xml": "<jobs/>\n"}, None, (0, 1, 0, 0)),
        ("no change at all", base, None, None, (0, 0, 0, 0)),
    ]
    for label, wares, extra_new, extra_old, want in cases:
        a = _mod(tmp / f"a_{label.replace(' ', '_')}", base, extra_old)
        b = _mod(tmp / f"b_{label.replace(' ', '_')}", wares, extra_new)
        rc, out = run("x4diff", str(a), str(b))
        import re
        m = re.search(r"changed files:\s*(\d+)\s+added:\s*(\d+)\s+removed:\s*(\d+)", out)
        n = re.search(r"total attr changes:\s*(\d+)", out)
        got = ((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(n.group(1)) if n else -1) if m else None)
        note(got == want, f"{label}", f"got={got} want={want}")


# --------------------------------------------------- 2. cross-tool agreement

#: `Collision.winner` answers a DIFFERENT question per kind, so one blanket
#: assertion is wrong. See KB 2026-08-13d, BLIND-SPOTS F25/F26, CLAUDE.md #18.
#:
#:   FULL-OVERRIDE / UNION-KEY  winner supplies the ENTITY  -> entities.origin
#:   HARD                       winner owns the VALUE       -> attrs.origin
#:   SUBTREE                    winner is the WIPER, not the owner of the final
#:                              value; assert the VICTIM is gone (scoped to w0)
#:   NAME-CLASH                 winner deliberately '' (index/macros.xml decides,
#:                              not load order) -> assert it IS empty
#:   SOFT                       benign coexistence; nothing to assert
#:
#: MEASURED 2026-08-13 over the 115-mod install, and every number is the whole
#: population, not a sample: FULL-OVERRIDE 14/14, HARD 40/40, UNION-KEY 2/2,
#: SUBTREE 148/148, NAME-CLASH 20/20 -- 0 disagreements. Before the per-kind
#: split, HARD reported 6 FALSE disagreements purely from reading
#: entities.origin where the fact lives in attrs.origin.

_PRED = re.compile(r"\[[^\]]*\]")


def _vpath_forms(vpath: str) -> tuple[str, str]:
    """Literal and nesting-stripped spellings of one logical file.

    `build_touch_map` rewrites a mod-on-mod `extensions/<owner>/<rel>` patch to
    the owner's own `<rel>`, so a collision reported at the literal path must be
    looked up both ways or it silently finds nothing. Omitting this left 6 of 148
    SUBTREE rows unresolvable, which very nearly got written up as an x4compat
    blind spot instead of what it was -- a lookup bug in the checker.
    """
    return vpath.lower(), _compat._strip_nesting(vpath)


def _origins(con, vpath: str, column: str) -> set[str]:
    """Distinct origins at *vpath*, from `entities` or `attrs`."""
    for form in _vpath_forms(vpath):
        if column == "entities":
            rows = con.execute(
                "SELECT DISTINCT origin FROM entities WHERE lower(vpath) = ?",
                (form,)).fetchall()
        else:
            rows = con.execute(
                "SELECT DISTINCT a.origin FROM attrs a JOIN entities e "
                "ON a.entity_id = e.id WHERE lower(e.vpath) = ?", (form,)).fetchall()
        if rows:
            return {r[0] for r in rows}
    return set()


def _removal_sources(con, vpath: str, target: str | None = None) -> set[str]:
    """Mods the store records as REMOVING something at *vpath* -- or, given *target*
    (a compat getpath), only the removals of THAT node or of an ANCESTOR of it.

    Per node, because "the named mod removed SOMETHING in this file" is not agreement
    (AUDIT-2026-09-24 review of AN-5): wares.xml carries removals by many mods, and a
    remover of a different ware would otherwise confirm any claim.
    """
    for form in _vpath_forms(vpath):
        rows = con.execute("SELECT source, node_path FROM removed WHERE lower(vpath) = ?",
                           (form,)).fetchall()
        if rows:
            if target is None:
                return {src for src, _ in rows}
            return {src for src, node in rows
                    if node == target or target.startswith(node + "/")}
    return set()


def _replayed_removers(con, vpath: str, tree, target: str) -> set[str] | None:
    """Mods whose REMOVAL took out the node x4compat's *target* names (or an ancestor
    of it), found by REPLAYING the store's removal records over the base tree.

    Why not `_removal_sources(target=...)`: the store records each removal at the path
    the node had AT THAT INSTANT (`_merge._path_of`), after every earlier removal had
    already shifted its later siblings; x4compat's targets are positions in the
    UNMODIFIED base tree. MEASURED 2026-09-26 on the real install: one mod's five
    ware removals were recorded as ware[1703], [1704], [1695], [1696], [1697] --
    base wares 1703, 1705, 1695, 1697, 1699 -- so an exact-path lookup mis-attributed
    two of five. Replaying the records in application order (the store's row order)
    over a copy of the base tree turns each instant path back into a base node.

    Stated limits: records sourced by a DLC (`ego_dlc_*`) are skipped, because the
    tree here is base + DLC already; an INSERT by a mod into the middle of a sibling
    list is not in the table, so a later removal's position among those siblings is
    replayed without that shift. None = *target* does not resolve to exactly one
    element (or one attribute of one) in *tree*.
    """
    import copy

    if tree is None:
        return None
    root = tree.getroot() if hasattr(tree, "getroot") and not hasattr(tree, "tag") \
        else tree
    want_attr = None
    head, sep, last = target.rpartition("/")
    path = target
    if sep and last.startswith("@"):
        path, want_attr = head, last[1:]
    try:
        hits = root.getroottree().xpath(path)
    except Exception:                                       # silent-ok: None = unresolved
        return None
    if not isinstance(hits, list) or len(hits) != 1 or not isinstance(
            getattr(hits[0], "tag", None), str):
        return None
    node = hits[0]
    work = copy.deepcopy(root)
    # lxml hands out a proxy per C node and reuses it only while one is alive, so every
    # proxy of the copy is HELD for the replay -- otherwise id() keys go stale.
    keep_alive = list(work.iter())
    orig_of = {id(w): o for o, w in zip(root.iter(), keep_alive)}
    rows = []
    for form in _vpath_forms(vpath):
        rows = con.execute("SELECT node_path, source FROM removed WHERE lower(vpath) = ? "
                           "ORDER BY rowid", (form,)).fetchall()
        if rows:
            break
    removers: set[str] = set()
    for node_path, source in rows:
        if str(source).lower().startswith("ego_dlc"):
            continue
        rpath, rattr = node_path, None
        h, s, lst = node_path.rpartition("/")
        if s and lst.startswith("@"):
            rpath, rattr = h, lst[1:]
        try:
            got = work.getroottree().xpath(rpath)
        except Exception:                                   # silent-ok: not replayable
            continue
        if not isinstance(got, list) or len(got) != 1 or not isinstance(
                getattr(got[0], "tag", None), str):
            continue
        orig = orig_of.get(id(got[0]))
        if rattr is not None:
            got[0].attrib.pop(rattr, None)
            if orig is node and rattr == want_attr:
                removers.add(source)
            continue
        if got[0].getparent() is not None:
            got[0].getparent().remove(got[0])
        if orig is not None and (orig is node or any(a is orig for a in node.iterancestors())):
            removers.add(source)
    del keep_alive
    return removers


def _file_is_tracked(con, vpath: str) -> bool:
    """Does the store index ANY entity at *vpath*? The store merges (and so records
    removals for) registry, macro and component files only; md/, aiscripts/, t/ and
    index/ are never merged into it, so their removals are never recorded."""
    return any(con.execute("SELECT 1 FROM entities WHERE lower(vpath) = ? LIMIT 1",
                           (f,)).fetchone() for f in _vpath_forms(vpath))


#: Why a HARD row could not be compared. UNTRACKED_FILE and WHOLE_DOCUMENT are EXPLAINED
#: by what the file is, and leave the denominator; the rest are the CHECKER failing to map
#: a row, and count against the coverage floor below. ENTITY_REMOVED is not a miss at all:
#: such a row IS compared, against the store's removals (`_replayed_removers`).
UNTRACKED_FILE = "file holds no store entity (md/aiscripts/t/index...)"
WHOLE_DOCUMENT = "target is above every entity (a document-level node)"
ENTITY_REMOVED = "entity exists in the base tree but not in the store (removed by the merge)"
UNRESOLVED = "target did not resolve to exactly one node in the base tree"
UNMAPPED_KIND = "entity kind has no known flattening (not macro / registry)"
NO_STORE_KEY = "node resolved, but the store keys no property for it"
PROP_NOT_STORED = "store keys the node, but holds no attribute under it"
EXPLAINED = (UNTRACKED_FILE, WHOLE_DOCUMENT)

#: THE COVERAGE FLOOR for HARD rows: unexplained (checker-side) misses may be at most this
#: share of the rows the checker was supposed to be able to map. Principle: a check that
#: cannot place most of its own population is not answering -- a 1/10 blind share is the
#: point at which the verdict stops being about the population (it is the same 10% the
#: registry audits in this repo treat as "a sample, not a census"). MEASURED 2026-09-25 on
#: the real install: see the README row -- set BEFORE the measurement, not tuned to it.
MAX_UNEXPLAINED_SHARE = 0.10

_PROBE = "x4ct_probe_attr"


class _NoProv:
    """A Recorder stand-in: flatten_with_prov only asks it for attribute chains."""

    def attr_chain(self, el, attr):
        return []


def _store_key(entity_el, kind: str, node) -> str | None:
    """The store's property key for *node* inside *entity_el*, computed by the STORE'S
    OWN flattener, never re-implemented here: the node is marked with a probe attribute in
    a copy of the entity, the copy is flattened exactly as `_effective.extract_macros` /
    `_extract_registry` flatten it, and the probe's row names the key. That keeps the
    positional discriminator (`production[<ident>]`, `[#n]` for a repeated ident, a
    zero-based index for ident-less siblings) identical to what the store wrote. "" = the
    entity element itself. None = the flattener emits no key for it (too deep, or a
    subtree the extractor does not walk)."""
    import copy

    from x4validate import _effective

    if node is entity_el:
        return ""
    path = []
    cur = node
    while cur is not entity_el:
        parent = cur.getparent()
        path.append(list(parent).index(cur))
        cur = parent
    clone = copy.deepcopy(entity_el)
    target = clone
    for i in reversed(path):
        target = target[i]
    target.set(_PROBE, _PROBE)
    rec = _NoProv()
    saved = list(_effective.truncated_props)       # flatten appends to a module global
    try:
        if kind == "macro":
            props = clone.find("properties")
            if target is props:
                return ""                           # <properties> itself = the entity
            rows = _effective.flatten_with_prov(
                clone, rec, no_recurse=(props,) if props is not None else ())
            if props is not None:
                rows += _effective.flatten_with_prov(props, rec, child_scope=props)
        else:
            rows = _effective.flatten_with_prov(clone, rec)
    finally:
        _effective.truncated_props[:] = saved
    suffix = "." + _PROBE
    for prop, value, _n, _c in rows:
        if value == _PROBE and prop.endswith(suffix):
            return prop[: -len(suffix)]
    return None


def _entity_of(con, form: str, node) -> tuple[str, str, object] | str:
    """(kind, name, element) of the nearest stored entity at or above *node*, or a reason."""
    from x4validate import _effective

    specs = {kind: (child_tag, key_attr)
             for kind, (_vp, child_tag, _k, key_attr) in _effective.LIBRARY_REGISTRIES.items()}
    stored = {(k, n) for k, n in con.execute(
        "SELECT DISTINCT kind, name FROM entities WHERE lower(vpath) = ?", (form,))}
    kinds = {k for k, _ in stored}
    for el in [node, *node.iterancestors()]:
        if not isinstance(el.tag, str):
            continue
        for kind in kinds:
            if kind == "macro":
                tag, key = "macro", "name"
            elif kind in specs:
                tag, key = specs[kind]
            else:
                continue
            if el.tag == tag and el.get(key):
                if (kind, el.get(key)) in stored:
                    return kind, el.get(key), el
                return ENTITY_REMOVED
    if any(k not in specs and k != "macro" for k in kinds):
        return UNMAPPED_KIND
    return WHOLE_DOCUMENT


def _hard_scope(con, c, tree_for) -> tuple[str, str, str | None, str | None] | str:
    """Map a HARD collision to (store vpath, entity, node key, attr) -- the COLLIDED node,
    not the file (AUDIT-2026-09-24 GT-5) -- or the reason it cannot be (a constant above).

    The full target -- every positional step, not just its tags -- is resolved in the
    effective base tree (x4compat emits lxml getpath targets such as
    `/wares/ware[1691]/production[2]/@time`), and the resulting NODE is mapped to the
    store's key for that node by `_store_key`. Dropping the positions made
    `production[2]` a `production` prefix matching every sibling, so a wrong winner
    agreed via another sibling's origin (review of fe8065b, reproduced)."""
    form = next((f for f in _vpath_forms(c.vpath) if con.execute(
        "SELECT 1 FROM entities WHERE lower(vpath) = ? LIMIT 1", (f,)).fetchone()), None)
    if form is None:
        return UNTRACKED_FILE
    target, attr = c.target, None
    head, sep, last = target.rpartition("/")
    if sep and last.startswith("@"):
        target, attr = head, last[1:]
    tree = tree_for(c.vpath)
    if tree is None or not target:
        return UNRESOLVED
    try:
        hits = tree.getroottree().xpath(target) if hasattr(tree, "getroottree") \
            else tree.xpath(target)
    except Exception:                                   # silent-ok: returned as UNRESOLVED
        return UNRESOLVED
    if not isinstance(hits, list) or len(hits) != 1 or not isinstance(
            getattr(hits[0], "tag", None), str):
        return UNRESOLVED
    ent = _entity_of(con, form, hits[0])
    if isinstance(ent, str):
        return ent
    kind, name, el = ent
    key = _store_key(el, kind, hits[0])
    if key is None:
        return NO_STORE_KEY
    return form, name, key, attr


def _scoped_origins(con, form: str, entity: str, key: str, attr: str | None) -> set[str]:
    """Origins of *entity*'s stored attrs for the node keyed *key* ("" = the entity):
    exactly `<key>.<attr>` (or `@attr`) for an attribute target, else every attr of that
    node and its descendants (`<key>.` prefix -- never `<key>[`, which is a SIBLING)."""
    rows = con.execute(
        "SELECT a.prop, a.origin FROM attrs a JOIN entities e ON a.entity_id = e.id "
        "WHERE lower(e.vpath) = ? AND e.name = ?", (form, entity)).fetchall()
    if attr is not None:
        want = f"{key}.{attr}" if key else f"@{attr}"
        return {o for pr, o in rows if pr == want}
    if not key:
        return {o for _, o in rows}
    return {o for pr, o in rows if pr.startswith(key + ".")}


def _subtree_scope(w0: str) -> tuple[str, str | None]:
    """Map a SUBTREE target to ('file'|'node'|'unmapped', prop_prefix).

    A wipe is NODE-scoped, not file-scoped: a mod replacing
    `/macros/macro/properties/explosiondamage` wipes ONE node, and the victim
    legitimately keeps its other attributes in the same document. Asserting
    file-wide absence for every row produced 6 false alarms out of 148.

    MEASURED: only three w0 shapes occur on this install and NONE carries a
    predicate -- `/macros` (140), `/macros/macro/properties/<node>` (6),
    `/macros/macro` (2). The first and third are whole-document / whole-entity
    replaces, where file-wide absence IS the correct assertion.
    """
    if _PRED.search(w0):
        return "unmapped", None
    parts = [p for p in w0.split("/") if p]
    if parts in (["macros"], ["macros", "macro"]):
        return "file", None
    if "properties" in parts:
        tail = parts[parts.index("properties") + 1:]
        return ("node", ".".join(tail)) if tail else ("file", None)
    return "unmapped", None


def _victim_attrs(con, vpath: str, victim: str, prop: str | None) -> int | None:
    """How many attrs *victim* still owns at *vpath* (under *prop* if given).

    None = the file holds no store-tracked entity, which is an ABSENCE of
    coverage rather than a pass -- counted and printed separately.
    """
    for form in _vpath_forms(vpath):
        if not con.execute("SELECT COUNT(*) FROM entities WHERE lower(vpath) = ?",
                           (form,)).fetchone()[0]:
            continue
        if prop is None:
            return con.execute(
                "SELECT COUNT(*) FROM attrs a JOIN entities e ON a.entity_id = e.id "
                "WHERE lower(e.vpath) = ? AND a.origin = ?", (form, victim)).fetchone()[0]
        return con.execute(
            "SELECT COUNT(*) FROM attrs a JOIN entities e ON a.entity_id = e.id "
            "WHERE lower(e.vpath) = ? AND a.origin = ? AND (a.prop = ? OR a.prop LIKE ?)",
            (form, victim, prop, prop + ".%")).fetchone()[0]
    return None


def check_cross_tool_agreement() -> None:
    print("\n2. CROSS-TOOL AGREEMENT - x4compat's winner vs x4effective's origin")
    try:
        db = _env.effective_db()
    except SystemExit:
        not_run("cross-tool agreement", "no effective store (run `x4effective build`)")
        return
    # A STALE store answers for an older engine or modlist, so agreement with it proves
    # nothing about today's x4compat, and disagreement is not a defect (AUDIT-2026-09-24
    # GT-6). The refusal text goes to stderr; this line is what the summary counts.
    if _env.stale_store_refusal(db, "cross_tool") is not None:
        not_run("cross-tool agreement", "the effective store is stale -- rebuild and re-run")
        return
    report = _compat.analyze(_env.extensions(), config=_merge.Config())
    if not report.collisions:
        note(False, "found collisions to cross-check", "none on this install")
        return
    by_kind: dict[str, list] = {}
    for c in report.collisions:
        by_kind.setdefault(c.kind, []).append(c)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    config = _merge.Config()
    # The mod set `_compat.analyze` reads, built by the same call: a nested
    # `extensions/<owner>/<rel>` has its base in the OWNER mod, found by folder here.
    active = _registry.mods("active", _compat._scan_dirs(_env.extensions()), dlc_config=config)
    left_out = _registry.dropped_note(active)
    if left_out:
        # Disclosed (F139): a mod the engine leaves out is in no tree built here.
        print(f"          note: the owner lookup covers the active set; NOT in it: {left_out}")
    folder_to_path = {m["folder"]: Path(m["path"]) for m in active}
    trees: dict[str, object] = {}

    def tree_for(vpath: str):
        """The base tree x4compat's positional targets index into -- x4compat's OWN
        `base_tree_for`, so a nested vpath gets its owner mod exactly as in
        `_analyze_vpath` (FX-H8: a bare-Config tree was None for every nested vpath,
        9 of 50 mappable HARD rows UNRESOLVED). Built once per vpath."""
        if vpath not in trees:
            try:
                trees[vpath] = _compat.base_tree_for(vpath, folder_to_path, config)
            except Exception as exc:                    # counted unmapped, and said
                print(f"          (could not build {vpath}: {exc!r})")
                trees[vpath] = None
        return trees[vpath]

    # --- kinds where the winner supplies the live value ----------------------
    for kind, column in (("FULL-OVERRIDE", "entities"),
                         ("UNION-KEY", "entities"),
                         ("HARD", "attrs")):
        rows = by_kind.get(kind, [])
        if not rows:
            print(f"          {kind}: none on this install")
            continue
        checked = disagree = absent = 0
        why: dict[str, int] = {}                        # HARD: every miss, by reason
        for c in rows:
            # `live_value_owner()` is the single definition of "which mod's value
            # is live" — it returns None for the kinds where naming one would be a
            # confident wrong answer, so this loop cannot ask the question of a
            # kind that has no answer.
            owner = c.live_value_owner()
            if owner is None:
                absent += 1
                continue
            where = ""
            if kind == "HARD" and getattr(c, "removed_by", ""):
                # A HARD row decided by an EARLIER REMOVAL (AUDIT-2026-09-24 AN-5) has
                # no live value to own: the node is GONE, so there is no stored property
                # to scope to. The remover must appear among the store's REMOVALS for
                # the file. An empty set is NOT excused as absent: compat claims a
                # removal is live and the store recorded none -- that is a disagreement.
                if not _file_is_tracked(con, c.vpath):
                    # The store never merged this file, so it CANNOT hold the removal:
                    # explained, exactly as an ordinary HARD row on such a file is.
                    absent += 1
                    why[UNTRACKED_FILE] = why.get(UNTRACKED_FILE, 0) + 1
                    continue
                origins = _replayed_removers(con, c.vpath, tree_for(c.vpath), c.target)
                if origins is None:                     # target unresolved in the base
                    origins = _removal_sources(con, c.vpath, c.target)
                where = f" (removal of {c.target})"
                checked += 1
                if owner not in origins:
                    disagree += 1
                    if disagree <= 3:
                        print(f"          {c.vpath}{where}: compat says {owner!r} "
                              f"removed the node; store removals={sorted(origins)}")
                continue
            elif kind == "HARD":
                # The COLLIDED node's origin, never "any origin in the file": the
                # winner owning SOMETHING else in the document is not agreement
                # (AUDIT-2026-09-24 GT-5).
                scope = _hard_scope(con, c, tree_for)
                if scope == ENTITY_REMOVED:
                    # The entity is in the base tree and gone from the store: the merge
                    # REMOVED it, so compat's live owner must be the mod that removed it
                    # (release review 2026-09-26: these rows used to leave the
                    # denominator as "explained" with the owner never checked).
                    origins = _replayed_removers(con, c.vpath, tree_for(c.vpath), c.target)
                    where = f" (entity removed at {c.target})"
                    checked += 1
                    if owner not in (origins or set()):
                        disagree += 1
                        if disagree <= 3:
                            print(f"          {c.vpath}{where}: compat live owner={owner!r}; "
                                  f"store removals of it={sorted(origins or ())}")
                    continue
                if isinstance(scope, str):
                    absent += 1
                    why[scope] = why.get(scope, 0) + 1
                    continue
                form, entity, key, attr = scope
                origins = _scoped_origins(con, form, entity, key, attr)
                where = f" {entity}:{key or '*'}{('@' + attr) if attr else ''}"
                if not origins:
                    absent += 1
                    why[PROP_NOT_STORED] = why.get(PROP_NOT_STORED, 0) + 1
                    continue
            else:
                origins = _origins(con, c.vpath, column)
            if not origins:
                absent += 1          # nothing store-tracked at the collided scope
                continue
            checked += 1
            if owner not in origins:
                disagree += 1
                if disagree <= 3:
                    print(f"          {c.vpath}{where}: compat live owner={owner!r}, "
                          f"{column} origin={sorted(origins)}")
        detail = (f"{checked - disagree}/{checked} agree via {column}.origin "
                  f"({absent} of {len(rows)} hold no stored entity at the collided scope)")
        if kind == "HARD":
            # Every miss is printed BY REASON and the buckets sum to the population.
            for reason, n in sorted(why.items(), key=lambda kv: -kv[1]):
                tag = "explained" if reason in EXPLAINED else "CHECKER"
                print(f"          HARD not compared x{n} [{tag}]: {reason}")
            explained = sum(n for r, n in why.items() if r in EXPLAINED)
            unexplained = absent - explained
            mappable = len(rows) - explained
            print(f"          HARD coverage: checked {checked} of {mappable} mappable "
                  f"({len(rows)} total, {explained} explained out, {unexplained} unmapped "
                  f"by the checker; floor: unmapped <= {MAX_UNEXPLAINED_SHARE:.0%})")
            if mappable and unexplained > MAX_UNEXPLAINED_SHARE * mappable:
                # The checker could not place its own population -- a non-answer, and
                # a pass over the rest would read as covering them. But a disagreement
                # it DID find is a finding, and a finding outranks a refusal: record the
                # FAIL first, then the floor refusal beside it (release review 2026-09-26).
                if disagree:
                    note(False, "HARD: compat winner is the store's origin", detail)
                not_run("HARD: compat winner is the store's origin",
                        f"{detail}; {unexplained} of {mappable} mappable rows unmapped by the "
                        f"checker, over the {MAX_UNEXPLAINED_SHARE:.0%} floor")
                continue
        if not checked:
            # 0 of N is a non-answer: every row was unmappable, so nothing agreed.
            not_run(f"{kind}: compat winner is the store's origin", detail)
            continue
        note(disagree == 0, f"{kind}: compat winner is the store's origin", detail)

    # --- SUBTREE: the winner is the WIPER; the VICTIM must be gone -----------
    subs = by_kind.get("SUBTREE", [])
    if subs:
        ok = viol = absent = unmapped = 0
        for c in subs:
            mode, prop = _subtree_scope(c.target)
            if mode == "unmapped":
                unmapped += 1
                continue
            n = _victim_attrs(con, c.vpath, c.mods[0], prop)
            if n is None:
                absent += 1
                continue
            if n:
                viol += 1
                if viol <= 3:
                    print(f"          {c.vpath}: w0={c.target} victim={c.mods[0]!r} "
                          f"wiped_by={c.wiped_by!r} still owns {n} attr(s)")
            else:
                ok += 1
        # Every row is accounted for, and the residue prints even when it is 0.
        detail = (f"{ok}/{ok + viol} clean - {unmapped} unmapped w0 - {absent} no stored "
                  f"entity - accounted {ok + viol + unmapped + absent}/{len(subs)}")
        if ok + viol == 0:
            not_run("SUBTREE: the wiped mod owns nothing under the wiped node", detail)
        else:
            note(viol == 0, "SUBTREE: the wiped mod owns nothing under the wiped node",
                 detail)

    # --- NAME-CLASH: winner is deliberately empty ---------------------------
    clashes = by_kind.get("NAME-CLASH", [])
    if clashes:
        named = [c for c in clashes if c.winner]
        note(not named, "NAME-CLASH: winner left empty (index/macros.xml decides)",
             f"{len(clashes) - len(named)}/{len(clashes)} empty")

    soft = len(by_kind.get("SOFT", []))
    print(f"          (SOFT {soft} not cross-checked: benign coexistence, no winner claim)")
    con.close()


# ------------------------------------------------------ 3. builder idempotence

def check_builder_idempotence(tmp: Path) -> None:
    print("\n3. BUILDER IDEMPOTENCE — same inputs twice, identical output?")
    db1, db2 = tmp / "b1.sqlite", tmp / "b2.sqlite"
    for db in (db1, db2):
        rc, out = run("x4effective", "--db", str(db), "build", "--kinds", "job")
        if rc != 0:
            note(False, "x4effective build succeeded", f"exit {rc}")
            return

    def rows(db: Path) -> list:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        r = con.execute("SELECT kind, name, klass, vpath, origin FROM entities "
                        "ORDER BY kind, name, vpath").fetchall()
        a = con.execute("SELECT prop, value, origin FROM attrs "
                        "ORDER BY prop, value, origin").fetchall()
        con.close()
        return [r, a]

    r1, r2 = rows(db1), rows(db2)
    note(r1[0] == r2[0], "x4effective entities identical across two builds",
         f"{len(r1[0])} vs {len(r2[0])} rows")
    note(r1[1] == r2[1], "x4effective attrs identical across two builds",
         f"{len(r1[1])} vs {len(r2[1])} rows")

    t1, t2 = tmp / "x1.tsv", tmp / "x2.tsv"
    ok = True
    for t in (t1, t2):
        rc, out = run("x4xref", "build", "--out", str(t))
        if rc != 0 or not t.is_file():
            ok = False
            break
    if ok:
        note(t1.read_bytes() == t2.read_bytes(), "x4xref index byte-identical across builds",
             f"{t1.stat().st_size} vs {t2.stat().st_size} bytes")
    else:
        print("     note  x4xref build --out not available in this form; index idempotence skipped")


# ------------------------------------------------------ 4. read-only hardening

def check_sql_hardening() -> None:
    print("\n4. READ-ONLY HARDENING — `x4effective sql` beyond DELETE")
    payloads = [
        ("ATTACH", "ATTACH DATABASE 'evil.db' AS evil"),
        ("PRAGMA writable", "PRAGMA writable_schema=ON"),
        ("multi-statement", "SELECT 1; DROP TABLE entities"),
        ("UPDATE", "UPDATE entities SET origin='x'"),
        ("INSERT", "INSERT INTO entities VALUES(1,'a','b','c','d','e','f')"),
        ("CREATE", "CREATE TABLE zzz(a)"),
        ("comment-hidden write", "/* SELECT */ DELETE FROM attrs"),
    ]
    for label, sql in payloads:
        rc, out = run("x4effective", "sql", sql)
        # Must refuse: non-zero exit, and never an unhandled traceback.
        refused = rc != 0
        clean = "Traceback (most recent call last)" not in out
        note(refused and clean, f"rejects {label}",
             f"exit {rc}" + ("" if clean else "  UNHANDLED TRACEBACK"))

    # The SECOND layer, which the CLI checks above cannot see. The verb guard is
    # only a PREFIX test: `SELECT 1; DROP TABLE entities` passes it and is stopped
    # by sqlite refusing multi-statement execution (hence its exit 1, not 2). So
    # what actually makes this surface safe is the connection being opened
    # `mode=ro`. Pin it directly — a future refactor could drop `?mode=ro` and
    # every CLI-level check above would still pass.
    from x4validate import _effective
    try:
        con = _effective._connect(_env.effective_db())
    except (SystemExit, ValueError, sqlite3.Error) as exc:
        # v4.0.0 review R5-4: `_connect` reports a missing or incompatible store with
        # ValueError now, not SystemExit -- uncaught, it aborted the gate with a traceback.
        # v4.0.0 delta review: PRINTING "NOT RUN" was not enough -- the gate still exited 0
        # with a section that examined nothing. Count it, so the verdict is 2.
        not_run("connection itself is read-only (mode=ro)",
                f"no usable store ({type(exc).__name__}); the read-only check did not run")
        return
    blocked = 0
    probes = ["UPDATE entities SET origin='x'", "DROP TABLE attrs", "CREATE TABLE zzz(a)"]
    for sql in probes:
        try:
            con.execute(sql)
        except sqlite3.Error:
            blocked += 1
    con.close()
    note(blocked == len(probes), "connection itself is read-only (mode=ro)",
         f"{blocked}/{len(probes)} writes blocked at the sqlite layer")


def check_path_edges(tmp: Path) -> None:
    """Windows-specific surface nothing else touches: non-ASCII names and a path
    past MAX_PATH (260). Both are ordinary for real users — mods ship with
    accented or CJK titles, and a Steam library nested a few levels deep gets
    long fast — and both fail in ways that look like "the tool is broken"."""
    print("\n5. PATH / ENCODING EDGES")
    cases = {
        "non-ASCII mod name": tmp / "Ünïcödé Mod — 日本語 (v2)",
        "path past MAX_PATH": tmp.joinpath(*[f"nested_dir_level_{i:02d}" for i in range(1, 12)],
                                           "themod"),
    }
    for label, mod in cases.items():
        (mod / "libraries").mkdir(parents=True, exist_ok=True)
        (mod / "content.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<content id="edge_probe" name="Ünïcödé — 日本語" version="100"/>\n',
            encoding="utf-8")
        (mod / "libraries" / "wares.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<diff>\n'
            "<replace sel=\"//ware[@id='ore']/price/@average\">450</replace>\n</diff>\n",
            encoding="utf-8")
        rc, out = run("x4validate", str(mod), "--tier", "a")
        ok = rc == 0 and "Traceback" not in out and "no issues found" in out
        note(ok, label, f"exit {rc}, path len {len(str(mod))}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="x4gate_cross_"))
    try:
        print("=" * 88)
        print("CROSS-TOOL / SENSITIVITY / IDEMPOTENCE / HARDENING")
        print("=" * 88)
        check_sensitivity(tmp)
        check_cross_tool_agreement()
        check_builder_idempotence(tmp)
        check_sql_hardening()
        check_path_edges(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 88)
    if failures:
        print(f"VIOLATIONS: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        if cannot:
            print(f"(and {len(cannot)} section(s) could not run: {'; '.join(cannot)})")
        return 1
    if cannot:
        print(f"Every section that RAN holds, but {len(cannot)} could not run:")
        for c in cannot:
            print(f"  - {c}")
        return 2
    print("All cross-tool checks hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

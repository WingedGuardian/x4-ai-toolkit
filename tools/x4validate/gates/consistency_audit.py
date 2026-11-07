#!/usr/bin/env python
"""Consistency audit — three code paths, one truth.

The same effective value is reachable three ways:
  1. `_merge.build_effective(vpath)`      — the merge itself
  2. `x4effective dump <vpath>`           — the live re-merge CLI
  3. the sqlite store (`entities`/`attrs`) — the cached extraction

They must agree. A disagreement means one path has drifted, and since callers
pick whichever is convenient, the drift would surface as an inexplicable
contradiction rather than an error. Cross-checking independent paths finds what
no single-path test can.

Run:  uv run python gates/consistency_audit.py [--samples=N]
Exit: 0 all agree, 1 any disagreement.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402
from x4validate import _effective, _merge, _registry  # noqa: E402

_TOUCH = None
_FOLDER_TO_PATH: dict = {}


def _overlays_for(vpath: str):
    """The overlays that touch *vpath*, in load order — what the store used."""
    global _TOUCH
    if _TOUCH is None:
        active = _effective.active_mods(None)
        # Said once, when the cache is built: what the store's world leaves out (F139).
        left_out = _registry.dropped_note(active)
        if left_out:
            print(f"NOT in the recomputed tree (the engine does not load it): {left_out}",
                  file=sys.stderr)
        ordered = _effective.ordered_overlays(active)
        _FOLDER_TO_PATH.update({m["folder"]: p for m, p in ordered})
        _TOUCH = _effective.build_touch_map(ordered)
    return _effective.touchers_for(vpath, _TOUCH, _FOLDER_TO_PATH)

DB = _env.effective_db()
SAMPLES = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--samples=")), 40)


def store_rows():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    # Spread the sample across owners so it is not all base-game entities.
    cur.execute(
        "SELECT e.name, e.vpath, a.prop, a.value, a.origin "
        "FROM entities e JOIN attrs a ON a.entity_id = e.id "
        "WHERE e.klass IN ('shieldgenerator','missile','engine','bullet') "
        "  AND a.prop LIKE '%.%' AND a.origin != 'base' "
        "ORDER BY RANDOM() LIMIT ?", (SAMPLES,))
    rows = cur.fetchall()
    con.close()
    return rows


#: `tag[3]` (positional) or `tag[{20206,601}]` (keyed by a text reference).
_STEP = re.compile(r"^(?P<tag>[^\[]+)(?:\[(?P<key>[^\]]+)\])?$")


def _lookup(macro, prop: str) -> str | None:
    """Resolve a store prop path against a macro element, or return None.

    The store disambiguates repeated sibling elements two ways: a ZERO-based
    index (`boost[1].thrust`, ~10k attrs) and a text-reference key
    (`x[{20206,601}].y`). `Element.find` handles neither — it reads `[1]` as an
    XPath predicate, rejects 0 outright, and chokes on the brace form.

    **Never raises.** A resolver that throws makes this audit report its own
    limitation as a DISAGREEMENT — a checker accusing the thing it checks, which
    is exactly how this gate failed intermittently before. An unknown shape is
    "I cannot answer", not "they disagree".
    """
    try:
        tag, _, attr = prop.rpartition(".")
        if not tag:
            return macro.get(attr.lstrip("@"))
        node = macro
        for i, step in enumerate(tag.split(".")):
            m = _STEP.match(step)
            if not m:
                return None
            key = m.group("key")
            matches = (node.findall(f".//{m.group('tag')}") if i == 0
                       else node.findall(m.group("tag")))
            if not matches:
                return None
            if key is None:
                node = matches[0]
            elif key.isdigit():
                idx = int(key)
                if len(matches) <= idx:
                    return None
                node = matches[idx]
            else:                       # keyed: match any attribute equal to it
                node = next((e for e in matches if key in e.attrib.values()), None)
                if node is None:
                    return None
        return node.get(attr.lstrip("@"))
    except Exception:                   # silent-ok: see docstring — never accuse
        return None


def from_merge(vpath: str, name: str, prop: str) -> str | None:
    # Must use the SAME overlay set the store was built with. A default Config()
    # is Tier A (base+DLC only), so comparing against it just re-discovers that
    # mods change things — it compared vanilla to effective and called it a bug.
    res = _merge.build_effective(vpath, _merge.Config(),
                                 extra_overlays=_overlays_for(vpath))
    if res.tree is None:
        return None
    return _one_macro(res.tree, name, prop)


#: A dump that RAN and produced an incomplete tree. Distinct from None, which
#: means the dump could not be used at all.
_DEGRADED = object()

#: The file holds the macro name MORE THAN ONCE (issue #17: a nested full copy
#: merges, so both copies live in one document). Which copy the store row came from
#: and which one the engine uses cannot be told apart here, so the value is NOT
#: compared -- counted and named instead, never a coin toss that can flake the gate.
_DUPLICATE = object()


def _one_macro(tree, name: str, prop: str):
    """*prop* of the ONE macro named *name* in *tree*; `_DUPLICATE` when there are
    several, None when there is none."""
    hits = [m for m in tree.iter("macro") if m.get("name") == name]
    if len(hits) > 1:
        return _DUPLICATE
    return _lookup(hits[0], prop) if hits else None


def from_dump(vpath: str, name: str, prop: str) -> str | None:
    p = subprocess.run(["uv", "run", "x4effective", "dump", vpath], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    # rc 3 is DEGRADED, not failed: `dump` produced a tree but an overlay was
    # dropped, so the tree is real and INCOMPLETE. Comparing against it could
    # manufacture a disagreement caused by the missing layer, so it is not
    # comparable -- but it must be COUNTED, not silently discarded. Returning the
    # sentinel here and counting it at the call site keeps those apart.
    if p.returncode == 3:
        return _DEGRADED
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        root = etree.fromstring(p.stdout.encode())
    except Exception:
        return None  # silent-ok: dump unparseable; the cross-checked count drops visibly
    return _one_macro(root, name, prop)


def eq(a, b) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return a == b


def main() -> int:
    # A STALE store disagrees with today's merge for reasons that are not defects, and a
    # fresh-looking agreement over an old store proves nothing about today's engine
    # (AUDIT-2026-09-24 GT-6). Refuse first.
    refused = _env.stale_store_refusal(DB, "consistency_audit")
    if refused is not None:
        return refused
    rows = store_rows()
    print("=" * 96)
    print(f"CONSISTENCY AUDIT — store vs build_effective vs `dump`, {len(rows)} sampled values")
    print("=" * 96)
    bad = []
    checked = 0
    no_vpath = no_merged = 0
    duplicated: list[str] = []     # issue #17: name defined more than once in its file
    dump_checked = dump_degraded = dump_unusable = 0
    dump_cache: dict[tuple[str, str, str], str | None] = {}
    for name, vpath, prop, value, origin in rows:
        if not vpath:
            no_vpath += 1
            continue
        merged = from_merge(vpath, name, prop)
        if merged is _DUPLICATE:
            duplicated.append(f"{name} in {vpath}")
            continue
        if merged is None:
            no_merged += 1
            continue
        checked += 1
        if not eq(merged, value):
            bad.append(("store vs merge", name, prop, value, merged, origin, vpath))
            continue
        key = (vpath, name, prop)
        if key not in dump_cache:
            dump_cache[key] = from_dump(vpath, name, prop)
        dumped = dump_cache[key]
        if dumped is _DUPLICATE:
            duplicated.append(f"{name} in {vpath} (dump)")
        elif dumped is _DEGRADED:
            dump_degraded += 1
        elif dumped is None:
            dump_unusable += 1
        else:
            dump_checked += 1
            if not eq(dumped, value):
                bad.append(("store vs dump", name, prop, value, dumped, origin,
                            vpath))

    # A FLOOR, for the reason oracle.py gives: 0 values cross-checked and 0
    # disagreements is not agreement, it is a run that examined nothing --
    # reachable with --samples=0, or whenever every sampled value has no
    # merged counterpart (`if merged is None: continue`, which has no counter).
    print(f"  values cross-checked : {checked}"
          + (f"   (not checkable: {no_vpath} without a vpath, {no_merged} with no merged "
             f"counterpart -- of {len(rows)} sampled)" if (no_vpath or no_merged) else ""))
    if duplicated:
        # NAMED, not just counted: these were sampled and deliberately not compared.
        print(f"  not compared, name defined more than once in its file "
              f"(issue #17, which copy is used is UNMEASURED): {len(duplicated)} -- "
              + "; ".join(sorted(set(duplicated))[:10]))
    if not checked:
        print("REFUSING: 0 values were cross-checked, so there is nothing to "
              "agree about. A NON-ANSWER, not a clean run.", file=sys.stderr)
        return 2
    # THE THIRD CHANNEL NEEDS ITS OWN DENOMINATOR. `checked` counts store-vs-merge
    # and is incremented BEFORE the dump call, so it cannot drop when the dump
    # channel dies -- the old comment claiming "the cross-checked count drops
    # visibly" was false. With every dump failing, this header still said "store vs
    # build_effective vs `dump`" while only TWO channels ran.
    print(f"  dump cross-checked   : {dump_checked}"
          + (f"   (degraded, not comparable: {dump_degraded})" if dump_degraded else "")
          + (f"   (unusable: {dump_unusable})" if dump_unusable else ""))
    if not dump_checked:
        print("DEGRADED: this audit claims THREE channels and the `dump` channel "
              "contributed nothing, so what ran was a two-way comparison.",
              file=sys.stderr)
    # The disagreements are printed BEFORE the degraded verdict is returned: a
    # store-vs-merge disagreement is a finding, and a finding outranks a refusal
    # (release review 2026-09-26 -- the rc 3 used to return first and hide it).
    print(f"  DISAGREEMENTS        : {len(bad)}")
    for kind, name, prop, a, b, origin, vpath in bad[:25]:
        print(f"\n  {kind}  {name}  {prop}   (origin={origin})")
        print(f"     store={a!r}   other={b!r}")
        print(f"     {vpath}")
    if bad:
        return 1
    if not dump_checked:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

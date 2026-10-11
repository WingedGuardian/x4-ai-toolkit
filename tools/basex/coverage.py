r"""Reconcile what BaseX actually INDEXED against what exists on disk.

Why this is the load-bearing part
---------------------------------
`SET SKIPCORRUPT true` is required (one malformed file otherwise aborts the whole
build) but it drops files **silently** — BaseX never reports which, or how many.
That is a "no channel for work not done" gap: the index looking complete is not
evidence that it is. It is the same bug class that made x4validate print
`OK: no issues found` for packed mods it had never opened.

A bigger index is not proof. **Proof needs a denominator.** So:

    "0 hits"                      <- worthless, could mean anything
    "0 hits over 13,701 of 13,701 documents, 0 unreadable"   <- a finding

This writes `coverage.json` next to the DB so a query layer can refuse to render
a zero-result as a finding when coverage is short.

Usage:  cd tools/x4validate && uv run python ../basex/coverage.py --db x4raw \
            --stage ../basex/_stage --reference <ref> --extensions <ext>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASEX_DIR = HERE / "basex"

# Counting query: documents per root, so each ADD can be reconciled separately.
_COUNT_XQ = """
let $all := collection('{db}')
return string-join((
  'total=' || count($all),
  'base='  || count($all[starts-with(document-uri(root(.)), '/{db}/base/')]),
  'mods='  || count($all[starts-with(document-uri(root(.)), '/{db}/mods/')])
), '&#10;')
"""


def _basex_run():
    """`basex_run` by FILE: tests load this module by path from tools/x4validate, where
    tools/basex is not on sys.path (and `coverage` itself shadows a PyPI package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("basex_run_for_coverage", HERE / "basex_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def basex_query(db: str, xquery: str) -> str:
    # UTF-8 both ways, the query passed as a file: see basex_run (issue #7).
    rc, stdout, stderr = _basex_run().run_query(xquery, BASEX_DIR)
    if rc != 0:
        raise RuntimeError(f"BaseX query failed: {stderr.strip()[:400]}")
    return stdout


def count_disk_xml(root: Path) -> int:
    # reference-scope-ok: the packed half is counted separately, from stage-manifest
    # ("documents_staged_base"/"documents_staged_mods"), because staging is transient.
    return sum(1 for _ in root.rglob("*.xml")) if root.is_dir() else 0


def find_unparseable(roots: list[Path]) -> list[str]:
    """Every XML file under *roots* that lxml refuses — i.e. what SKIPCORRUPT ate.

    Run only when a deficit exists, so the common case pays nothing. This is what
    turns "12 documents are missing and we don't know why" (unusable) into "12
    documents are missing, here they are by name" (usable, and a human can judge
    whether they matter). An UNEXPLAINED deficit is the dangerous state: it means
    something is wrong with the build itself, not just with a mod's XML.
    """
    from lxml import etree
    bad = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.xml"):  # reference-scope-ok: packed ones came from the manifest
            try:
                etree.parse(str(p))
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{p}: {str(exc).splitlines()[0][:120]}")
    return bad


def _unparseable_by_root(manifest: dict, reference: Path, extensions: Path) -> dict[str, list[str]]:
    """Every malformed file, keyed by the BaseX root it would have been indexed under.

    base = the reference tree (loose) + packed DLC staged into /base/extensions;
    mods = the extensions tree (loose) + packed mods staged into /mods. A packed entry
    is placed by its SOURCE's recorded root in the stage manifest; one the manifest
    lists without a placeable source goes to `unattributed`, which the caller must
    treat as unexplained rather than guess a root for it.
    """
    out: dict[str, list[str]] = {"base": [], "mods": [], "unattributed": []}
    placed: list[str] = []
    for src in manifest.get("sources", []) or []:
        key = "mods" if src.get("root") == "/mods" else "base"
        for u in src.get("unparseable", []) or []:
            entry = f"{src.get('name')}/{u}"
            out[key].append(f"[packed] {entry}")
            placed.append(entry)
    remaining = list(placed)
    for entry in manifest.get("unparseable", []) or []:
        if entry in remaining:
            remaining.remove(entry)
        else:
            out["unattributed"].append(f"[packed] {entry}")
    out["base"] += [f"[loose]  {u}" for u in find_unparseable([reference])]
    out["mods"] += [f"[loose]  {u}" for u in find_unparseable([extensions])]
    return out


NEWLINE = chr(10)

#: The form `_modfiles.overlay_vpaths` writes for an unreadable catalog: an overlay FOLDER
#: name (no `/`, unlike every vpath entry), then the fixed words. test_coverage.py feeds this
#: the real function's output, so a change of wording there fails here.
_UNREADABLE_CATALOG = re.compile(r"[^/:]+: catalog unreadable \(")


def coverage_effective(db: str, eff_manifest: Path, out_path: Path) -> int:
    """Reconcile the x4eff DB against what build-effective.py said it produced.

    x4eff's denominator is different in kind from x4raw's: a vpath can be missing
    not because a FILE was unreadable but because no effective tree exists for it
    (e.g. a nested patch whose target mod is not installed — the engine also does
    nothing there). Those are legitimate absences, but they still have to be
    ENUMERATED, or "0 hits" over the effective tree means nothing.
    """
    man = json.loads(eff_manifest.read_text(encoding="utf-8"))
    counts = man.get("counts", {})
    expected_total = counts.get("documents_total", 0)
    try:
        raw = basex_query(db, f"count(collection('{db}'))")
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"error: could not query BaseX: {exc}", file=sys.stderr)
        return 2
    indexed_total = int(raw.strip() or 0)

    failures = man.get("failures", [])
    # AN UNREADABLE CATALOG IS NOT ONE MISSING VPATH (v3.3.0 release review). The build
    # records an overlay whose packed catalog could not be read as ONE `failures` entry
    # (`_modfiles.overlay_vpaths`: "<mod>: catalog unreadable (...)"), and this counted it
    # as one "vpath with no effective tree" -- a legitimate, licensed absence -- while every
    # packed member of that catalog, a number nobody has, was never enumerated. MEASURED:
    # status complete, supports_negative_claim true, "NEGATIVE CONFIRMED over 10000 of
    # 10000". It is F35's shape (a source the enumeration never reached), so F35's verdict.
    catalogs = [f for f in failures if _UNREADABLE_CATALOG.match(f)]
    unbuilt = [f for f in failures if f not in catalogs]
    merge_skips = man.get("merge_skips", [])
    deficit = expected_total - indexed_total
    status = "complete" if deficit == 0 else "unexplained"

    # --- F35: produced-vs-indexed cannot see a source that was never SCANNED ---
    # Both numbers above come from THIS build, so a vpath the enumeration never
    # reached is absent from the produced count, absent from the failure list,
    # and absent from the deficit alike. MEASURED 2026-08-22: 119 of 142 mini-DLC
    # documents were missing while this printed COMPLETE. Counting FAILED reads
    # was never going to catch that -- the denominator has to include what we
    # INTENDED to scan, not only what we managed to read. "base + 6 DLC" where 8
    # DLC exist is a defect anyone can see on sight; "0 unreadable" is not.
    enum = man.get("enumeration")
    if enum is None:
        status = "unexplained"
        enum_note = ("this manifest predates the scanned-source-set contract, so there is "
                     "no record of which sources the enumeration even attempted")
    elif enum.get("sources_contributing_nothing"):
        status = "unexplained"
        enum_note = ("source(s) contributed NOTHING: "
                     + ", ".join(enum["sources_contributing_nothing"]))
    else:
        enum_note = ""
    if catalogs:
        status = "unexplained"
        enum_note = ((enum_note + "; ") if enum_note else "") + (
            f"{len(catalogs)} overlay catalog(s) could not be read, so their packed "
            "members were never enumerated and no count of them exists: "
            + "; ".join(catalogs))

    print(f"  {'root':<8}  {'produced':>9}  {'indexed':>9}  {'delta':>7}")
    print(f"  {'x4eff':<8}  {expected_total:>9}  {indexed_total:>9}  {indexed_total-expected_total:>+7}"
          + ("" if deficit == 0 else "   <-- MISSING"))
    if enum:
        print()
        print(f"  enumerated from {enum['sources_configured']} configured source(s), "
              f"{enum['documents_enumerated']} base+DLC document(s):")
        for _name, _d in enum["sources"].items():
            _flag = "   <-- CONTRIBUTED NOTHING" if _d["count"] == 0 else ""
            print(f"    {_name:<24} {_d['count']:>6}  ({_d['read']}){_flag}")
    if enum_note:
        print()
        print(f"  ** {enum_note} **")
    print(f"\n  {len(unbuilt)} vpath(s) have NO effective tree and were never produced;")
    print(f"  {len(merge_skips)} overlay(s) were malformed and are absent from the trees.")
    print("  Both are enumerated in effective-manifest.json — a negative over x4eff is")
    print("  a claim about the tree MINUS those, so check them when the answer matters.")
    # A COMPLETE OVER ZERO IS NOT A LICENCE. main() grew two producing-side refusals
    # -- a non-directory root, and expected["total"] <= 0 -- on the stated grounds that
    # "refusing to PRODUCE it is the half that stops a false licence sitting on disk".
    # This function RETURNS BEFORE BOTH, so the x4eff path reached neither. MEASURED by
    # the v3.1.0 release reviewer: a manifest whose enumeration SUCCEEDED (so
    # sources_contributing_nothing is empty) while every merge failed
    # (documents_total: 0) printed "COVERAGE COMPLETE — 0 effective documents
    # indexed", wrote supports_negative_claim=true and exited 0 -- and
    # build-effective.sh then stamped a freshness fingerprint on it. An artifact that
    # reads FRESH and COMPLETE over an empty index.
    #
    # ask.py refuses such a store on the consuming side, so no false negative reached
    # a user. The harm is a false-success build exit and a bad artifact on disk, and
    # leaning on the consumer is the asymmetry those producing-side guards exist to
    # remove.
    if status == "complete" and expected_total <= 0:
        status = "empty"
        print(
            NEWLINE + "  ** REFUSING A LICENCE: the manifest reports 0 documents "
            "produced, so " + chr(39) + "complete" + chr(39) + " would mean complete "
            "over NOTHING. A zero denominator cannot support a negative. **")
    elif status == "complete":
        print(f"\n  COVERAGE COMPLETE — {indexed_total} effective documents indexed.")
    elif status != "empty":
        print(f"\n  ** UNEXPLAINED DEFICIT of {deficit} — do not trust a negative. **")

    out_path.write_text(json.dumps({
        "db": db,
        "expected": {"total": expected_total},
        "indexed": {"total": indexed_total},
        "deficit": deficit,
        "status": status,
        "unparseable": merge_skips[:50],
        "vpaths_without_effective_tree": len(unbuilt),
        # Named in full: a count of the members they hold does not exist.
        "unreadable_catalogs": catalogs,
        # Carried through so a caller can RENDER the caveat instead of reading a
        # bare boolean: a negative over x4eff is a claim about the tree MINUS
        # these. The human output always said so; nothing machine-readable did.
        "enumeration": enum,
        "negative_claim_excludes": {
            "vpaths_without_effective_tree": len(unbuilt),
            "unparseable_overlays": len(merge_skips),
        },
        "supports_negative_claim": status == "complete",
    }, indent=2), encoding="utf-8")
    print(f"  coverage: {out_path}")
    return 0 if status == "complete" else 4


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="x4raw")
    p.add_argument("--eff-manifest", default=None,
                   help="reconcile an EFFECTIVE-tree DB against this manifest instead")
    p.add_argument("--stage", required=False, default="")
    p.add_argument("--manifest", default=str(HERE / "stage-manifest.json"))
    p.add_argument("--reference", required=False, default="")
    p.add_argument("--extensions", required=False, default="")
    p.add_argument("--out", default=None, help="coverage.json path (default: alongside stage)")
    args = p.parse_args(argv)

    if args.eff_manifest:
        out = Path(args.out) if args.out else BASEX_DIR / f"coverage-{args.db}.json"
        return coverage_effective(args.db, Path(args.eff_manifest), out)

    # F46. These defaulted to "" — and `Path("")` is `Path(".")`, so a bare run
    # counted the XML in whatever directory you were standing in, called that the
    # expected total, and reported a DEFICIT. A denominator taken from the wrong
    # population is worse than no denominator, because it still gets printed.
    # This is the tool everything else asks for its denominator, so it is the last
    # place that may guess. Same rule as `_scan.CorpusScan.verdict`, which RAISES
    # rather than render a zero over a population it never looked at.
    missing = [n for n, v in (("--reference", args.reference),
                              ("--extensions", args.extensions)) if not v]
    if missing:
        print(f"error: {' and '.join(missing)} not supplied, so the expected "
              f"document count cannot be computed.", file=sys.stderr)
        print("       Refusing to guess: an empty root resolves to the CURRENT "
              "DIRECTORY, which would publish a denominator measured over the "
              "wrong population and report a deficit against it.", file=sys.stderr)
        return 2

    stage = Path(args.stage)
    reference = Path(args.reference)
    extensions = Path(args.extensions)

    # ...and they must EXIST. The check above tests for an empty STRING, so it
    # catches `--reference ""` and nothing else. A root that is merely WRONG -- a
    # typo, a stale config, an unmounted drive -- is a non-empty string, sails
    # through, and `count_disk_xml` returns 0 for it because `root.is_dir()` is
    # False. Expected then totals 0, indexed totals 0, the deficit is 0, and this
    # publishes {"status": "complete", "supports_negative_claim": true} over
    # NOTHING AT ALL -- the artifact ask.py renders as
    #     NEGATIVE CONFIRMED over 0 of 0 documents (complete).
    #
    # Same rule as the refusal above, one failure mode further on: a population
    # that was never looked at is not a population of zero.
    unreal = ["%s %s" % (n, v) for n, v in (("--reference", reference),
                                             ("--extensions", extensions))
              if not v.is_dir()]
    if unreal:
        print("error: not a directory: " + "; ".join(unreal), file=sys.stderr)
        print("       Refusing to measure: a root that does not exist yields ZERO"
              " documents, which would be published as a denominator of 0 and"
              " called complete coverage.", file=sys.stderr)
        return 2

    # --- expected, from disk + the staging manifest ---------------------------
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    # Read staged counts from the MANIFEST, not from the staging dir: staging is
    # transient, and a coverage check that only works mid-build is useless later.
    _t = manifest.get("totals", {})
    staged_mods = _t.get("documents_staged_mods", count_disk_xml(stage / "mods"))
    staged_base = _t.get("documents_staged_base", count_disk_xml(stage / "base_extensions"))

    expected = {
        "base": count_disk_xml(reference) + staged_base,
        "mods": count_disk_xml(extensions) + staged_mods,
    }
    expected["total"] = expected["base"] + expected["mods"]

    # A DENOMINATOR OF ZERO IS NOT A DENOMINATOR. Both roots can exist and still
    # hold no XML (an unpopulated reference/, a wrong-but-real path), and the
    # reconciliation below would then find 0 expected, 0 indexed, deficit 0, and
    # write supports_negative_claim: true. ask.py refuses that artifact on the
    # consuming side now; refusing to PRODUCE it is the half that stops a false
    # licence sitting on disk being quoted by anything else.
    if expected["total"] <= 0:
        print("error: 0 expected documents under %s and %s"
              % (reference, extensions), file=sys.stderr)
        print("       Refusing to publish coverage over an empty population: it"
              " would license every negative claim while proving none.",
              file=sys.stderr)
        return 2

    # --- actual, from BaseX ---------------------------------------------------
    try:
        raw = basex_query(args.db, _COUNT_XQ.format(db=args.db))
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"error: could not query BaseX: {exc}", file=sys.stderr)
        return 2
    indexed = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            try:
                indexed[k.strip()] = int(v.strip())
            except ValueError:
                pass

    # --- reconcile ------------------------------------------------------------
    rows = []
    complete = True
    for key in ("base", "mods", "total"):
        exp, got = expected.get(key, 0), indexed.get(key, 0)
        delta = got - exp
        if delta != 0:
            complete = False
        rows.append((key, exp, got, delta))

    extraction_failures = manifest.get("totals", {}).get("extraction_failures", 0)
    if extraction_failures:
        complete = False

    width = max(len(r[0]) for r in rows)
    print(f"  {'root':<{width}}  {'on disk':>9}  {'indexed':>9}  {'delta':>7}")
    for key, exp, got, delta in rows:
        flag = "" if delta == 0 else "   <-- MISSING" if delta < 0 else "   <-- EXTRA"
        print(f"  {key:<{width}}  {exp:>9}  {got:>9}  {delta:>+7}{flag}")

    if extraction_failures:
        print(f"\n  !! {extraction_failures} packed document(s) could not be extracted "
              "(see manifest.json)")

    deficit = expected["total"] - indexed.get("total", 0)
    unparseable: list[str] = []
    status = "complete"
    if complete:
        print("\n  COVERAGE COMPLETE — a zero-result from this index is a real negative.")
    else:
        print(f"\n  deficit of {deficit} document(s) — identifying which files "
              "SKIPCORRUPT dropped...")
        # Packed ones were recorded at staging time (staging itself is gone by now);
        # loose ones we scan for here. Each is attributed to the ROOT it would have
        # been indexed under, because the deficit is judged per root (below).
        by_root = _unparseable_by_root(manifest, reference, extensions)
        unparseable = by_root["base"] + by_root["mods"] + by_root["unattributed"]
        for u in unparseable:
            print(f"    - {u}")
        # PER ROOT, never on the aggregate (AUDIT-2026-09-24 BX-4). The total used
        # to be the only thing compared, so +5 EXTRA under /base and -5 MISSING
        # under /mods netted to a deficit of 0, matched 0 malformed files, and was
        # published as ACCOUNTED with supports_negative_claim=true -- a licence over
        # an index missing five mod documents. An aggregate hides exactly the item
        # it nets out, so every root must balance on its own: its deficit is its
        # own malformed files, never a surplus somewhere else. A packed malformed
        # file the manifest cannot place under a root cannot balance either.
        per_root_ok = (not by_root["unattributed"]
                       and indexed.get("total", 0) == sum(indexed.get(k, 0) for k in ("base", "mods"))
                       and all(expected[k] - indexed.get(k, 0) == len(by_root[k])
                               for k in ("base", "mods")))
        if per_root_ok and deficit == len(unparseable) and not extraction_failures:
            status = "accounted"
            usable = indexed.get("total", 0)
            print(f"\n  COVERAGE ACCOUNTED — every missing document is explained above.")
            print(f"  A zero-result is valid over {usable} of {expected['total']} documents;")
            print(f"  the {len(unparseable)} exclusions are malformed XML the ENGINE cannot")
            print("  read either, so they hold no live content. Judge them by name.")
        else:
            status = "unexplained"
            print(f"\n  ** UNEXPLAINED DEFICIT: {deficit} missing in total, "
                  f"{len(unparseable)} malformed files found; per root: "
                  + "; ".join(f"{k} {expected[k] - indexed.get(k, 0)} missing vs "
                              f"{len(by_root[k])} malformed" for k in ("base", "mods"))
                  + (f"; {len(by_root['unattributed'])} malformed packed file(s) with no "
                     "root" if by_root["unattributed"] else "")
                  + ". **")
            print("  Something is wrong with the BUILD, not just with a mod's XML.")
            print("  This index CANNOT support a negative claim until that is resolved.")

    out_path = Path(args.out) if args.out else BASEX_DIR / f"coverage-{args.db}.json"
    out_path.write_text(json.dumps({
        "db": args.db,
        "expected": expected,
        "indexed": indexed,
        "extraction_failures": extraction_failures,
        "deficit": deficit,
        "status": status,           # complete | accounted | unexplained
        "unparseable": unparseable,
        # Only these two states may back a negative claim.
        "supports_negative_claim": status in {"complete", "accounted"},
    }, indent=2), encoding="utf-8")
    print(f"  coverage: {out_path}")

    # 0 clean · 3 accounted (usable, with named exclusions) · 4 unexplained (do not trust)
    return {"complete": 0, "accounted": 3, "unexplained": 4}[status]


if __name__ == "__main__":
    raise SystemExit(main())

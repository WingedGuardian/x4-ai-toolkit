r"""Serialize X4's EFFECTIVE merged tree to disk, so BaseX can index what the
engine actually sees — not what somebody wrote.

The two databases answer different questions, and both are wanted
-----------------------------------------------------------------
    x4raw  every file AS WRITTEN, per mod   -> "who WROTE this, in which mod"
    x4eff  _merge.build_effective per vpath -> "what does the ENGINE SEE"

x4raw cannot answer the second: it has no diff application, no load order, no
conflict winner. Ask it "does anything set safepos/@radius to 8km" and it says
yes — from vanilla — while the engine has long since been handed 21km by whoever
loads last. Ask x4eff and you get the value that is actually in play.

Only contested vpaths need building
-----------------------------------
Measured on the live modlist: of 9,138 base+DLC vpaths, only **1,522 are overlaid
by any mod**; another 1,783 mod vpaths are new files. So 3,305 vpaths need a
build_effective call and the remaining 7,616 base documents are already their own
effective form and are hardlinked verbatim. At ~0.01 s/vpath that is about
a minute, not the hours a naive "merge everything" would cost.

Usage:  cd tools/x4validate && uv run python ../basex/build-effective.py --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from x4recycle import recycle_or_raise

from lxml import etree

from x4validate import _cat, _compat, _effective, _merge, _modfiles, _registry


def installed_in_load_order(config: "_merge.Config | None" = None) -> list[Path]:
    """Installed extension roots, in the order X4 applies them.

    Uses _compat.compute_load_order -- the engine's MEASURED order (case-insensitive
    folder order, dependencies in repeated passes), the same ordering x4validate's Tier B
    uses and gates/load_order_oracle.py re-checks against the engine's own log.
    """
    # ACTIVE: x4eff answers "what does the ENGINE SEE". Built from the on-disk
    # set it carried a disabled mod's 19 files as live content -- and unlike a
    # missing document, surplus content corrupts POSITIVE answers, which nothing
    # else guards. (x4raw is the other question and correctly uses "installed".)
    not_loaded: list[str] = []
    mods = _registry.mods("active", dropped=not_loaded, dlc_config=config)
    for msg in not_loaded:
        # Disclosed: absent from x4eff because the engine does not load it -- a
        # query that finds nothing of this mod must not read as "it adds nothing".
        print(f"NOT in x4eff (the engine does not load it): {msg}", file=sys.stderr)
    by_folder = {m["folder"]: m for m in mods}
    out = []
    for folder in _compat.compute_load_order(mods):
        m = by_folder.get(folder)
        if m and Path(m["path"]).is_dir():
            out.append(Path(m["path"]))
    # AFTER ordering: what the order and the set assume rides on `mods`.
    model = _registry.model_note(mods)
    if model:
        print(f"x4eff load-order model: {model}", file=sys.stderr)
    return out


def enumeration_report(config: _merge.Config, base: dict[str, str]) -> dict:
    """What the enumeration actually SCANNED, per source, and how it was read.

    F35: coverage used to reconcile "documents produced" against "documents
    indexed" -- both derived from THIS build. A vpath that was never enumerated
    cannot be produced, cannot fail, and therefore cannot show up as a deficit.
    Status came back COMPLETE over a population that had already lost 119
    documents.

    Counting the FAILED reads was never going to catch that. The missing channel
    is the SCANNED SOURCE SET: name every source the enumeration was supposed to
    cover and what each contributed, so a source that contributed nothing is
    self-evident on sight. "base + 6 DLC" where 8 DLC exist is a defect anyone
    can see; "0 unreadable files" is not.

    Derived from the enumeration's OWN output rather than re-walking, so it
    cannot drift from the set it describes.
    """
    packed = config.packed_dlc_names()
    per: dict[str, int] = {"reference": 0}
    for low in base:
        parts = low.split("/")
        src = parts[1] if len(parts) >= 3 and parts[0] == "extensions" else "reference"
        per[src] = per.get(src, 0) + 1

    configured = ["reference"] + [d.name for d in config.dlc_dirs()]
    sources = {}
    for name in configured:
        sources[name] = {
            "count": per.get(name.lower(), per.get(name, 0)),
            "read": "packed" if name.lower() in packed else "loose",
        }
    # A source the enumeration never reached is the failure this exists to name.
    empty = sorted(n for n, d in sources.items() if d["count"] == 0)
    return {
        "sources_configured": len(configured),
        "documents_enumerated": len(base),
        "sources": sources,
        "sources_contributing_nothing": empty,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="output tree (previous tree recycled and recreated)")
    p.add_argument("--reference", default=None)
    p.add_argument("--limit", type=int, default=0, help="build only N contested vpaths (smoke test)")
    args = p.parse_args(argv)

    config = _merge.Config(reference=Path(args.reference)) if args.reference else _merge.Config()
    # Issue #10 shape: BEFORE the rmtree below. A missing or empty reference used to
    # wipe the previous tree, write 0 documents plus a manifest, and exit 0 -- which
    # build-effective.sh then indexed into x4eff as a successful build. rc 2 is a
    # "died before writing a manifest" refusal to that script, so it stops.
    try:
        _effective.require_base_documents(config, "serialize the effective tree")
    except _effective.EmptyReference as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    overlays = installed_in_load_order(config)
    config = _merge.Config(reference=config.reference, overlays=tuple(overlays))

    out = Path(args.out)
    try:
        recycle_or_raise([out])
    except (OSError, RuntimeError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True)

    t0 = time.time()
    # Enumeration lives in the package (`_modfiles.overlay_vpaths`) so the engine
    # freshness axis watches it (AUDIT-2026-09-24 FR-2); an overlay whose catalog
    # cannot be read is now RECORDED in `failures` instead of dropped (BX-6).
    failures: list[str] = []
    contested, untouched, base = _modfiles.overlay_vpaths(config, overlays, failures)
    if args.limit:
        contested = set(sorted(contested)[:args.limit])
    print(f"vpaths: {len(contested)} contested (merge required), "
          f"{len(untouched)} base-only (hardlinked where loose)")

    written = 0
    merge_skips: list[str] = []
    for v in sorted(contested):
        try:
            res = _merge.build_effective(v, config)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{v}: build_effective raised {type(exc).__name__}: {exc}")
            continue
        merge_skips.extend(f"{v}: {s}" for s in res.skipped)
        if res.tree is None:
            failures.append(f"{v}: no effective tree (base absent and no overlay parsed)")
            continue
        target = out / v
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(etree.tostring(res.tree, encoding="utf-8", xml_declaration=True))
        except OSError as exc:
            failures.append(f"{v}: write failed ({exc})")
            continue
        written += 1
    merged_secs = time.time() - t0

    # A packed DLC has no file on disk, so `shutil.copyfile` cannot serve it.
    # Landing the enumeration fix WITHOUT this would have turned one silent
    # absence into 119 silent copy failures -- and, per F35, coverage would still
    # have printed COMPLETE, because it reconciles produced-vs-indexed and those
    # documents would simply never have been produced.
    packed_dlc = {d.name.lower(): d for d in config.dlc_dirs()
                  if d.name.lower() in config.packed_dlc_names()}

    def _packed_source(v: str) -> tuple[Path, str] | None:
        parts = v.split("/", 2)
        if len(parts) < 3 or parts[0].lower() != "extensions":
            return None
        d = packed_dlc.get(parts[1].lower())
        return None if d is None else (d, parts[2])

    copied = 0
    copied_packed = 0
    for v in sorted(untouched):
        src = config.reference / v
        target = out / v
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                # Immutable reference input; never duplicate a whole source tree.
                # A cross-volume/permission refusal is accounted, not a copy fallback.
                os.link(src, target)
            else:
                pk = _packed_source(v)
                data = _cat.read_path(pk[0], pk[1]) if pk else None
                if data is None:
                    failures.append(f"{v}: no loose file and no packed member")
                    continue
                target.write_bytes(data)
                copied_packed += 1
            copied += 1
        except OSError as exc:
            failures.append(f"{v}: staging failed ({exc})")

    total_secs = time.time() - t0
    enum = enumeration_report(config, base)
    manifest = {
        "out": str(out),
        "overlays_in_load_order": [d.name for d in overlays],
        # The SCANNED SOURCE SET -- see enumeration_report() and BLIND-SPOTS F35.
        "enumeration": enum,
        "counts": {
            "contested": len(contested),
            "merged_written": written,
            "untouched_hardlinked": copied-copied_packed,
            "untouched_materialized": copied_packed,
            "documents_total": written + copied,
            "failures": len(failures),
            "merge_skips": len(merge_skips),
        },
        "seconds": {"merge": round(merged_secs, 1), "total": round(total_secs, 1)},
        "failures": failures,
        # Overlays _merge itself could not parse — they are absent from the
        # effective tree, so any query over it is that much less complete.
        "merge_skips": merge_skips[:500],
    }
    (out.parent / "effective-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print("enumerated {} base+DLC documents from {} source(s): {}".format(
        enum["documents_enumerated"], enum["sources_configured"],
        ", ".join(f"{n}={d['count']}{'*' if d['read'] == 'packed' else ''}"
                  for n, d in enum["sources"].items())))
    if enum["sources_contributing_nothing"]:
        print("  !! source(s) that contributed NOTHING: "
              + ", ".join(enum["sources_contributing_nothing"]))
    print(f"merged  {written} vpaths in {merged_secs:.1f}s")
    print(f"staged  {copied} base-only documents ({copied-copied_packed} hardlinked, {copied_packed} materialized from catalogs)")
    print(f"TOTAL   {written + copied} documents in {total_secs:.1f}s")
    if merge_skips:
        print(f"  !! {len(merge_skips)} overlay(s) could not be parsed and are MISSING "
              "from the effective tree")
        for s in merge_skips[:10]:
            print(f"     {s}")
    if failures:
        print(f"  !! {len(failures)} vpath(s) FAILED — the effective index is incomplete:")
        for f in failures[:15]:
            print(f"     {f}")
        if len(failures) > 15:
            print(f"     ... and {len(failures) - 15} more")
    print(f"manifest: {out.parent / 'effective-manifest.json'}")
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())

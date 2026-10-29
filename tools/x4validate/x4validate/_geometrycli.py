"""Opt-in x4similar geometry path; legacy stats imports no numeric dependencies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time


def arguments(parser):
    parser.add_argument("--geometry", metavar="MACRO_VPATH",
                        help="experimental geometry candidate; bypass stats class/purpose filters")
    parser.add_argument("--geometry-against", action="append", default=[], metavar="MACRO_VPATH",
                        help="explicit comparison macro (repeat); otherwise scan the installed ship inventory")
    parser.add_argument("--geometry-overlay", action="append", default=[], metavar="DIR",
                        help="append isolated candidate root after active overlays; never installs it")
    parser.add_argument("--geometry-alias", action="append", default=[], metavar="NAME=DIR",
                        help="explicit hypothetical extension namespace for candidate assets")
    parser.add_argument("--geometry-json", action="store_true", help="versioned geometry report on stdout")
    parser.add_argument("--geometry-samples", type=int, default=4096, metavar="N",
                        help="area-weighted surface samples per hull, 128..16384 (default 4096)")
    parser.add_argument("--geometry-limit", type=int, default=32, metavar="N",
                        help="refuse inventories larger than N (default 32, maximum 256); never silently shortlist")


def run(args, reference, ext):
    started = time.perf_counter()
    try:
        import numpy
        import scipy
        import trimesh
        import rtree
        from ._geometry_assets import World
        from ._geometry_mesh import GeometryError, DECODER_VERSION
        from ._geometry_score import compare, SCORER_VERSION
    except ImportError as exc:
        print(f"geometry unavailable: {exc}; install with uv sync --extra geometry", file=sys.stderr)
        return 2
    from . import _compat, _effective, _merge, _registry, _similarity
    if not 128 <= args.geometry_samples <= 16384 or not 1 <= args.geometry_limit <= 256:
        print("error: geometry samples must be 128..16384, limit 1..256", file=sys.stderr)
        return 2
    try:
        aliases = {}
        for value in args.geometry_alias:
            name, path = value.split("=", 1)
            if not name or name.lower() in aliases or "/" in name or chr(92) in name:
                raise GeometryError("invalid/duplicate geometry alias")
            aliases[name.lower()] = Path(path)
        scan = _compat._scan_dirs(ext)
        active = _effective.active_mods(scan)
        ordered = _effective.ordered_overlays(active)
        overlays = [p for _, p in ordered]
        for value in args.geometry_overlay:
            p = Path(value)
            if not p.is_dir() or p in overlays:
                raise GeometryError(f"missing/duplicate candidate overlay {p}")
            overlays.append(p)
        if any(p not in overlays for p in aliases.values()):
            raise GeometryError("each geometry alias must name an explicit --geometry-overlay root")
        config = _merge.Config(reference=reference)
        world = World(config, overlays, aliases, _merge.GAME_ROOT if _merge.is_configured_reference(reference) else None)
        notes, unreadable = [], []
        candidates = [(v, v, None) for v in args.geometry_against]
        inventory_scope = "explicit macro paths"
        if not candidates:
            vectors = _similarity._collect_all(reference, ext, unreadable, notes=notes)
            candidates = [(v.vpath, f"{v.macro_name} [{v.source}]", v.source) for v in vectors]
            inventory_scope = "installed ship definitions; active resolution; disabled-only entries unresolved"
        if len(candidates) > args.geometry_limit:
            raise GeometryError(f"{len(candidates)} candidates exceed --geometry-limit {args.geometry_limit}; choose explicit --geometry-against paths or raise the bound (max 256)")
        subject = world.assemble(args.geometry)
        rows, assemblies, objects = [], [subject.report()], [subject]
        active_names = {m["folder"] for m, _ in ordered}
        for path, label, owner in candidates:
            if owner is not None and owner != "base" and not owner.startswith("dlc:") and owner not in active_names:
                rows.append(dict(candidate=label, geometry_score=None, status="unresolved",
                                 reason="disabled inventory source requires separate hypothetical resolution"))
                continue
            other = world.assemble(path, label)
            objects.append(other)
            assemblies.append(other.report())
            if subject.mesh is None or other.mesh is None:
                result = dict(geometry_score=None, status="unresolved", reason="extraction unavailable; see assembly manifest")
            else:
                try:
                    result = compare(subject.mesh, other.mesh, args.geometry_samples)
                except (GeometryError, ValueError, MemoryError) as exc:
                    result = dict(geometry_score=None, status="unresolved", reason=f"scoring refused: {exc}")
                if (subject.unresolved or other.unresolved) and result.get("geometry_score") is not None:
                    result["partial_geometry_score"] = result.get("geometry_score")
                    result.update(geometry_score=None, status="partial", verdict="unresolved",
                                  reason="incomplete/uncertain assembly; partial surfaces scored only")
            shared = {p["payload_sha256"] for p in subject.included} & {p["payload_sha256"] for p in other.included}
            rows.append(dict(candidate=label, shared_exact_mesh_payloads=sorted(shared),
                             shared_parts_scope="exact mesh bytes only; not assembled-hull or fuzzy part equivalence", **result))
        for obj in objects:
            world.verify(obj)
        assemblies = [a.report() for a in objects]
        if any(a.mesh is None and any("drift" in reason for reason in a.unresolved) for a in objects):
            for row in rows:
                row.pop("partial_geometry_score", None)
                row.update(geometry_score=None, status="unresolved", reason="run input drift; see assembly receipts")
        source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                         (Path(__file__), Path(__file__).with_name("_geometry_mesh.py"),
                          Path(__file__).with_name("_geometry_assets.py"), Path(__file__).with_name("_geometry_score.py"))}
        report = dict(schema="x4similar.geometry/1", candidate=args.geometry,
                      inventory_scope=inventory_scope, resolution_scope=world.scope,
                      decoder=DECODER_VERSION, scorer=SCORER_VERSION,
                      dependencies=dict(numpy=numpy.__version__, scipy=scipy.__version__,
                                        trimesh=trimesh.__version__, rtree=rtree.__version__),
                      implementation_sha256=source_hashes, notes=notes,
                      unreadable_inventory=[str(u) for u in unreadable],
                      coverage=dict(total=len(candidates), scored=sum(r.get("geometry_score") is not None for r in rows),
                                    partial=sum(r["status"] == "partial" for r in rows),
                                    unresolved=sum(r["status"] == "unresolved" for r in rows),
                                    shortlisted=len(candidates), excluded=0),
                      assemblies=assemblies, pairs=rows, cache_bytes=world.cache_bytes,
                      seconds=time.perf_counter()-started,
                      limitations=["EXPERIMENTAL; no qualified real-hull classification threshold",
                                   "Scores describe shape only, never mod redundancy or removal safety",
                                   "Explicit candidate overlays are hypothetical, not active installations",
                                   "Partial assemblies cannot support whole-hull negatives",
                                   "No persistent cache; no coarse shortlist or search recall claim"])
        if args.geometry_json:
            print(json.dumps(report, indent=2, allow_nan=False))
        else:
            print(f"EXPERIMENTAL geometry: {args.geometry}; {report['coverage']}")
            for row in rows:
                score = row.get("geometry_score")
                display = f"{score:.2f}/100" if score is not None else "null"
                print(f"  {row['candidate']}: {display} [{row['status']}] — verdict unresolved")
                if row.get("reason"):
                    print(f"    {row['reason']}")
                if "size_a" in row:
                    print(f"    size XYZ: {row['size_a']} -> {row['size_b']}; uniform scale {row['uniform_scale_a_to_b']:.5g}")
                    print(f"    proper rotation determinant {row['transform']['determinant']:.5g}; mirrored score {row['mirrored_score']:.2f}")
                    print("    directed overlap A->B/B->A: "+", ".join(
                        f"{r['tolerance']:g}: {r['a_to_b']:.3f}/{r['b_to_a']:.3f}" for r in row["overlap"]))
            for assembly in assemblies:
                print(f"  {assembly['label']}: {len(assembly['included'])} included / "
                      f"{len(assembly['excluded'])} excluded / {len(assembly['unresolved'])} unresolved")
                for reason in assembly["unresolved"]:
                    print(f"    {reason}")
                owners = sorted({r.get("owner") for r in assembly["receipts"] if r.get("owner")})
                print(f"    mesh owners: {', '.join(owners) or 'none'}; pose=rest; LODs={sorted({p['lod'] for p in assembly['included']})}")
            print("Use --geometry-json for source hashes, part inventory, LOD, transforms, dimensions and directed distances.")
        return 0
    except (GeometryError, OSError, ValueError) as exc:
        print(f"geometry unresolved: {exc}", file=sys.stderr)
        return 2

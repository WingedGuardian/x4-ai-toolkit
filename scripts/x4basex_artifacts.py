"""Dispose of verified BaseX build caches without backups or Recycle Bin copies.

Only known generated slots or producer-receipted workspaces qualify. Inputs must
be available now; unexpected files, links and invalid receipts retain the cache.
Database directories are deliberately absent: BaseX itself owns DROP DB/locking.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "tools" / "basex"
RECEIPT = ".x4-basex-disposable.json"


def plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"BaseX disposal refuses a link/reparse point: {part}")
    if path == Path(path.anchor) or path == Path.home().absolute():
        raise OSError(f"BaseX disposal refuses a root/home: {path}")
    return path


def receipt(path, kind):
    return {"v": 1, "kind": kind, "path": str(path),
            "producer": "x4-basex", "disposition": "rebuildable cache; permanent disposal"}


def _same_folder(a, b):
    """One folder, whatever the spelling: case, separators and 8.3 short names are not a different place."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _receipt_problem(declared, path, marker):
    """None when the receipt declares this folder as a stage/effective workspace, else the refusal to raise."""
    role = declared.get("kind")
    invalid = OSError(f"invalid BaseX disposition receipt: {marker}")
    if role not in {"stage", "effective"} or not isinstance(declared.get("path"), str):
        return invalid
    if {**declared, "path": str(path)} != receipt(path, role):
        return invalid
    if not _same_folder(declared["path"], path):
        return OSError(
            f"BaseX disposition receipt {marker} declares {declared['path']}, but this folder is {path}: it was moved or "
            "renamed, so the receipt does not cover it and it is kept. If it holds only generated XML, move it to the "
            "Recycle Bin yourself and run the build again; a rebuild writes a new workspace and receipt.")
    return None


def _inspect(path, artifact_root):
    path = plain(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    slots = {artifact_root / "_stage": "stage", artifact_root / "_eff/tree": "effective",
             artifact_root / "_eff/effective-manifest.json": "metadata",
             artifact_root / "basex/coverage-x4raw.json": "metadata",
             artifact_root / "basex/coverage-x4eff.json": "metadata"}
    kind = slots.get(path)
    if stat.S_ISREG(info.st_mode):
        if kind != "metadata" or path.stat().st_size > 16 * 1024 * 1024:
            raise OSError(f"not a recognized BaseX cache metadata file: {path}")
        if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
            raise OSError(f"invalid generated metadata: {path}")
        return path, path.stat().st_size
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"not an ordinary BaseX file/directory: {path}")
    marker = path / RECEIPT
    if marker.exists():
        plain(marker)
        if marker.stat().st_size > 4096:
            raise OSError(f"invalid BaseX disposition receipt: {marker}")
        try:
            declared = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise OSError(f"invalid BaseX disposition receipt: {marker}") from exc
        if not isinstance(declared, dict):
            raise OSError(f"invalid BaseX disposition receipt: {marker}")
        role = declared.get("kind")
        problem = _receipt_problem(declared, path, marker)
        if problem:
            raise problem
        if kind is not None and kind != role:
            raise OSError(f"BaseX disposition kind changed: {path}")
        kind = role
    elif kind not in {"stage", "effective"}:
        raise OSError(f"unreceipted custom workspace retained: {path}")
    # Legacy canonical slots are covered by the explicit BaseX-cache retirement
    # decision. Do not fabricate a creation receipt for their existing contents.
    size = 0
    def failed(exc):
        raise exc
    for dp, dirs, files in os.walk(path, onerror=failed):
        for name in dirs + files:
            p = Path(dp) / name
            info = p.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError(f"BaseX disposal refuses a link/reparse point: {p}")
            if name in files:
                if not stat.S_ISREG(info.st_mode) or (p != marker and p.suffix.lower() != ".xml"):
                    raise OSError(f"unexpected file in BaseX cache; retained: {p}")
                size += info.st_size
    return path, size


def dispose(paths, *, proof, artifact_root=ARTIFACT_ROOT, sources=()):
    """Validate the whole batch first; failure never falls back to recycling."""
    proof()
    root = plain(artifact_root)
    inspected = []
    for value in paths:
        p = plain(value)
        for source in sources:
            s = Path(source).absolute()
            if p == s or p in s.parents or s in p.parents:
                raise OSError(f"BaseX output overlaps an input source: {p}")
        item = _inspect(p, root)
        if item is not None and item[0] not in [q for q, _ in inspected]:
            inspected.append(item)
    done = []
    for p, size in inspected:
        try:
            if p.is_dir():
                shutil.rmtree(p)  # no chmod and no fallback; linked input attributes never change
            else:
                p.unlink()
            if os.path.lexists(p):
                raise OSError("still present after disposal")
        except OSError as exc:
            print(json.dumps({"permanently_deleted": done, "recycled": [],
                              "failed": [{"path": str(p), "reason": str(exc)}]}), file=sys.stderr)
            raise
        done.append({"path": str(p), "bytes": size})
    if done:
        print(json.dumps({"permanently_deleted": done, "recycled": [], "failed": []}))
    return done


def prepare_output(path, kind, *, proof, sources=()):
    if kind not in {"stage", "effective"}:
        raise ValueError("unknown BaseX workspace kind")
    path = plain(path)
    dispose([path], proof=proof, sources=sources)
    path.mkdir(parents=True)
    # Written while empty, before any generated XML. Never backfilled.
    (path / RECEIPT).write_text(json.dumps(receipt(path, kind)) + "\n", encoding="utf-8")


def database_target(artifact_root, db, sources=()):
    root = plain(artifact_root)
    # Share preflight's DBPATH resolver: the native engine honors .basex even
    # when its databases are stored somewhere other than beside the jar.
    spec = importlib.util.spec_from_file_location("_basex_disposal_preflight", ARTIFACT_ROOT / "preflight.py")
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    data = preflight._dbpath(root / "basex")
    if not data.is_absolute():
        data = root / "basex" / data
    target = plain(data / db)
    for source in sources:
        s = Path(source).absolute()
        if target == s or target in s.parents or s in target.parents:
            raise OSError(f"BaseX output overlaps an input source: {target}")
    return target


def declare_database(db, *, proof, artifact_root=ARTIFACT_ROOT, sources=()):
    """Declare before native CREATE DB, outside the database's private format."""
    if db not in {"x4raw", "x4eff"}:
        raise ValueError("not a toolkit-owned BaseX database")
    proof()
    root = plain(artifact_root)
    target = database_target(root, db, sources)
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    else:
        raise OSError(f"database still exists after DROP DB; refusing creation receipt: {target}")
    marker = plain(root / "basex" / f"disposition-{db}.json")
    value = receipt(target, "database")
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        old_path = previous.get("path") if isinstance(previous, dict) else None
        if not isinstance(old_path, str) or Path(old_path).name != db or previous != receipt(Path(old_path), "database"):
            raise OSError(f"unexpected database disposition metadata: {marker}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(value) + "\n", encoding="utf-8")
    print(json.dumps({"creation_receipt": str(marker), "database": db}))
    return marker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--declare-db", choices=("x4raw", "x4eff"))
    parser.add_argument("--artifact-root", default=str(ARTIFACT_ROOT))
    parser.add_argument("--reference")
    args = parser.parse_args(argv)
    if bool(args.paths) == bool(args.declare_db):
        parser.error("supply artifact paths or --declare-db, not both")
    try:
        from x4validate import _effective, _merge
        config = _merge.Config(reference=Path(args.reference)) if args.reference else _merge.Config()
        proof = lambda: _effective.require_base_documents(config, "manage BaseX generated caches")
        if args.declare_db:
            declare_database(args.declare_db, artifact_root=args.artifact_root, proof=proof,
                             sources=(config.reference,))
        else:
            root = plain(args.artifact_root)
            # This licence revocation is before native DROP DB in both builders.
            # Qualify the actual configured database path before permitting it.
            for db in ("x4raw", "x4eff"):
                if root / "basex" / f"coverage-{db}.json" in [plain(p) for p in args.paths]:
                    database_target(root, db, (config.reference,))
            dispose(args.paths, artifact_root=args.artifact_root, proof=proof,
                    sources=(config.reference,))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"REFUSING BaseX disposal: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

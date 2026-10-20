"""Restore a literal backup, preserving and verifying the current destination first."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def identity(path):
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def regular(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_file():
        raise ValueError(f"expected a plain file: {path}")


def restore(backup_dir, name, destination):
    backup_dir = Path(backup_dir).resolve()
    if Path(name).name != name or name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError("backup must be one literal filename in the backup directory")
    source, dest = backup_dir / name, Path(destination).absolute()
    regular(source)
    if not dest.parent.is_dir() or dest.parent.resolve() != dest.parent:
        raise ValueError("destination parent must exist without redirected components")
    if source.resolve() == dest.resolve():
        raise ValueError("source and destination must differ")
    before_source, expected = identity(source), digest(source)
    current, retained = None, None
    if os.path.lexists(dest):
        regular(dest)
        current = identity(dest), digest(dest)
        retained = backup_dir / ("before-restore__" + uuid.uuid4().hex + "__" + dest.name)
        try:
            with retained.open("xb") as stream, dest.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if digest(retained) != current[1]:
                raise OSError("current destination preservation failed; restore refused")
        except BaseException:
            retained.unlink(missing_ok=True)  # this invocation's unverified copy only
            raise
        if identity(dest) != current[0]:
            raise OSError("current destination changed; restore refused")
    fd, staged_name = tempfile.mkstemp(prefix=".x4-restore-", dir=dest.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as stream, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(staged) != expected or identity(source) != before_source:
            raise OSError("backup changed or staged copy failed verification")
        if current is not None:
            regular(dest)
            if identity(dest) != current[0] or digest(dest) != current[1] or digest(retained) != current[1]:
                raise OSError("current destination changed before restore publication")
            os.replace(staged, dest)
        else:
            # Publish without replacing a concurrently created destination.
            os.link(staged, dest)
        if digest(dest) != expected:
            raise OSError("restored destination failed readback")
    finally:
        staged.unlink(missing_ok=True)  # only this invocation's owned staging file
    return retained


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("usage: restore-from-backup.py <backup-dir> <list [pattern]|restore name destination>", file=sys.stderr)
        return 2
    directory, command, *args = argv
    try:
        if command == "restore" and len(args) == 2:
            retained = restore(directory, *args)
            if retained is not None:
                print(f"Current destination preserved and verified: {retained}")
            print(f"Restored and verified: {args[1]}")
        elif command == "list" and len(args) <= 1:
            needle = args[0].casefold() if args else ""
            paths = [p for p in Path(directory).iterdir() if p.is_file() and p.name != "AUDIT_LOG.txt"
                     and needle in p.name.casefold()]
            for p in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:40]:
                print(p.name)
        else:
            raise ValueError("expected list [pattern] or restore <literal backup name> <destination>")
    except (OSError, ValueError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

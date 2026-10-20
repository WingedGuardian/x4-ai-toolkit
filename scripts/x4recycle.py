"""Recoverable removal for prior-run artifacts. Refusal never falls back to deletion.

Callers decide which paths they own; this module checks the removal actually happened.
Tests inject a backend, or use the bounded pytest sandbox shared with subprocesses.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid


def _test_recycle(paths):
    root_text = os.environ.get("X4_RECYCLE_TEST_ROOT")
    bin_text = os.environ.get("X4_RECYCLE_TEST_BIN")
    if not root_text or not bin_text:
        raise AssertionError("tests must inject a fake recycler")
    root, dest = Path(root_text).resolve(), Path(bin_text).resolve()
    if root not in dest.parents:
        raise AssertionError("fake bin must be inside the pytest sandbox")
    result = {}
    for name in paths:
        path = Path(name)
        resolved = path.resolve()
        if root not in resolved.parents or dest == resolved or dest in resolved.parents:
            raise AssertionError("fake recycler target is outside its owned sandbox")
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / (uuid.uuid4().hex + "-" + path.name)
        path.rename(target)
        result[name] = None
    return result


def _native_recycle(paths):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return _test_recycle(paths)
    if os.name == "nt":
        # One Windows implementation, including the permanent-delete warning.
        from x4housekeep import recycle
        return recycle(paths)
    if sys.platform == "darwin":
        result = {}
        script = 'on run argv\ntell application "Finder" to delete (POSIX file (item 1 of argv))\nend run'
        for name in paths:
            run = subprocess.run(["osascript", "-e", script, "--", name], capture_output=True,
                                 text=True, timeout=120)
            result[name] = None if run.returncode == 0 else (run.stderr.strip() or "Finder Trash failed")
        return result
    gio = shutil.which("gio")
    if not gio:
        raise RuntimeError("gio trash is unavailable; originals retained")
    result = {}
    for name in paths:
        run = subprocess.run([gio, "trash", "--", name], capture_output=True,
                             text=True, timeout=120)
        result[name] = None if run.returncode == 0 else (run.stderr.strip() or "gio trash failed")
    return result


def recycle_or_raise(paths, *, recycler=None):
    """Recycle existing named paths, verifying every result; absent paths are harmless."""
    names = []
    for value in paths:
        path = Path(value).absolute()
        if not os.path.lexists(path):
            continue
        redirected = getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if path.is_symlink() or redirected:
            raise OSError(f"refusing to recycle a link: {path}")
        if path.parent == path or path == Path.home():
            raise OSError(f"refusing to recycle a root or home: {path}")
        if str(path) not in names:
            names.append(str(path))
    if not names:
        return
    results = (recycler or _native_recycle)(names)
    errors = []
    for name in names:
        reason = results.get(name, "recycler returned no result")
        if reason is None and os.path.lexists(name):
            reason = "still present after reported recycle"
        if reason is not None:
            errors.append(f"{name}: {reason}")
    if errors:
        raise OSError("Recycle Bin/Trash refusal: " + "; ".join(errors))


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print("usage: python scripts/x4recycle.py <path> [<path> ...]", file=sys.stderr)
        return 2
    try:
        recycle_or_raise(paths)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

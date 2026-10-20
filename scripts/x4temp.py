"""Ownership receipts for newly created X4 toolkit temporary directories."""
from __future__ import annotations

import json
import os
from pathlib import Path

MARKER = ".x4-temp-owner.json"


def identity(path):
    return os.path.normcase(os.path.realpath(path))


def mark_owned_temp(directory, toolkit):
    """Call only immediately after creating this directory, never to adopt old scratch."""
    directory = Path(directory)
    data = {"v": 1, "toolkit": identity(toolkit), "directory": identity(directory)}
    body = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
    with (directory / MARKER).open("xb") as stream:
        stream.write(body)
    return directory


def owns_temp(directory, toolkit):
    marker = Path(directory) / MARKER
    if not marker.exists():
        return False
    if marker.is_symlink() or marker.stat().st_size > 4096:
        return False
    data = json.loads(marker.read_text(encoding="utf-8"))
    return data == {"v": 1, "toolkit": identity(toolkit), "directory": identity(directory)}

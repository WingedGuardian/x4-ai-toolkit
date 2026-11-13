"""One rule for where an x4live journal may live.

A journal, capture or task root must not overlap (contain, or sit inside):

- the PERSONAL TREE: the parent of the profile folder (Documents/Egosoft/X4), which
  holds every profile, its saves and uidata.xml;
- the game install: the folder of the X4 executable, and the configured game root;
- the configured reference folder.

Comparison is first lexical on resolved paths (WindowsPath comparison is
case-insensitive), then by FILE IDENTITY: Path.resolve() keeps spellings such as
`//?/C:/...` (extended length) and `//localhost/C$/...` (administrative share), shown here
with forward slashes, as
written, so the lexical check alone was bypassed (MEASURED, delta review 2). Each folder
and each of its existing ancestors is compared by (st_dev, st_ino) with the other folder.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import _paths


def _identity(path):
    """(st_dev, st_ino) of an existing path; None when absent or not identifiable
    (an inode number of 0 does not identify anything)."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _chain(path):
    """Identities of `path` and each of its existing ancestors (the journal itself may
    not exist yet)."""
    p = Path(path)
    return {i for i in (_identity(q) for q in (p, *p.parents)) if i is not None}


def overlaps(a, b) -> bool:
    """True when one of the two folders is, or is inside, the other."""
    ra = Path(a).resolve()
    rb = Path(b).resolve()
    if ra.is_relative_to(rb) or rb.is_relative_to(ra):
        return True
    ia, ib = _identity(ra), _identity(rb)
    return (ib is not None and ib in _chain(ra)) or (ia is not None and ia in _chain(rb))


def personal_tree(profile) -> Path:
    """The folder that holds every profile: the parent of one profile folder."""
    return Path(profile).resolve().parent


def protected_roots(*, image=None, profile=None) -> dict[str, Path]:
    """The trees a journal must not overlap, from arguments and the toolkit's configuration.

    A relative `image` is resolved against the current folder. A tree that cannot be
    determined is left out here and named by unchecked_roots(), never silently.
    """
    roots: dict[str, Path] = {}
    if image is not None:
        roots["game install (the X4 executable's folder)"] = Path(image).resolve().parent
    for name, value in (("configured game root", _paths.game_root()),
                        ("reference folder", _paths.reference())):
        if value is not None:
            roots[name] = Path(value)
    prof = profile if profile is not None else _paths.profile()
    if prof is not None:
        roots["personal tree (every profile and its saves)"] = personal_tree(prof)
    return roots


def unchecked_roots(*, image=None, profile=None) -> list[str]:
    """Names of the protected trees protected_roots() could not determine."""
    out = []
    if image is None:
        out.append("game install")
    if _paths.game_root() is None:
        out.append("configured game root")
    if _paths.reference() is None:
        out.append("reference folder")
    if profile is None and _paths.profile() is None:
        out.append("personal tree")
    return out


def journal_refusal(root, *, image=None, profile=None) -> str | None:
    """The one journal rule: why `root` may not be used, or None when it may."""
    return overlap_reason(root, protected_roots(image=image, profile=profile))


def overlap_reason(root, roots: dict[str, Path]) -> str | None:
    """The first protected tree `root` overlaps, as a sentence; None when it overlaps none."""
    for name, tree in roots.items():
        if overlaps(root, tree):
            return f"journal {Path(root)} overlaps the {name} {tree}"
    return None

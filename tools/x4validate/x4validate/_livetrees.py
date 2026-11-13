"""One rule for where an x4live journal may live.

A journal, capture or task root must not overlap (contain, or sit inside):

- the PERSONAL TREE: the parent of the profile folder (Documents/Egosoft/X4), which
  holds every profile, its saves and uidata.xml;
- the game install: the folder of the X4 executable, and the configured game root;
- the configured reference folder.

Comparison is on resolved paths; WindowsPath comparison is case-insensitive.
"""
from __future__ import annotations

from pathlib import Path

from . import _paths


def overlaps(a, b) -> bool:
    """True when one of the two folders is, or is inside, the other."""
    a = Path(a).resolve()
    b = Path(b).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def personal_tree(profile) -> Path:
    """The folder that holds every profile: the parent of one profile folder."""
    return Path(profile).resolve().parent


def protected_roots(*, image=None, profile=None) -> dict[str, Path]:
    """The trees a journal must not overlap, from arguments and the toolkit's configuration."""
    roots: dict[str, Path] = {}
    if image is not None and Path(image).is_absolute():
        roots["game install (the X4 executable's folder)"] = Path(image).parent
    for name, value in (("configured game root", _paths.game_root()),
                        ("reference folder", _paths.reference())):
        if value is not None:
            roots[name] = Path(value)
    prof = profile if profile is not None else _paths.profile()
    if prof is not None:
        roots["personal tree (every profile and its saves)"] = personal_tree(prof)
    return roots


def overlap_reason(root, roots: dict[str, Path]) -> str | None:
    """The first protected tree `root` overlaps, as a sentence; None when it overlaps none."""
    for name, tree in roots.items():
        if overlaps(root, tree):
            return f"journal {Path(root)} overlaps the {name} {tree}"
    return None

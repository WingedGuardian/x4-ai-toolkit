#!/usr/bin/env python3
"""Deploy a mod this toolkit SHIPS into the game's extensions folder -- guarded, dry-run
first, no recursive delete.

    uv run python scripts/deploy-mod.py <mod> [<mod> ...]            # DRY RUN, writes nothing
    uv run python scripts/deploy-mod.py <mod> [<mod> ...] --apply    # writes

Run from `tools/x4validate`. The source is always this repo's `mods/<mod>`; the destination
is the game-root extensions folder from your toolkit config (`X4_EXTENSIONS`, or
`X4_GAME/extensions`).

WHY THIS EXISTS
    The obvious form -- `rm -rf "$EXT/$m" && cp -r "$SRC/$m" "$EXT/"` -- is one empty
    shell variable away from deleting the wrong tree inside the game installation, and it
    destroys the destination BEFORE the copy, so a failure part way leaves no mod at all.
    This script never removes a directory. It diffs the two trees, copies what is new or
    changed, and recycles orphans one named file at a time after printing them.

GUARDS, in order. Any failure raises `Refused` before a single byte is written, and with
several mods EVERY mod's guards run before ANY mod is written:
    1. the mod is one this repo SHIPS: a folder under `mods/` carrying a content.xml
    2. the extensions root is configured and EXISTS -- a typo must not create a folder the
       game never reads
    3. it is not inside the user PROFILE: neither inside the configured one, nor on ANY
       path shaped like `Egosoft/X4/<digits>/...` (an unconfigured X4_PROFILE is
       announced). Dependencies resolve only within one extensions root, so a profile
       deploy reads every dependency as MISSING
    4. its parent looks like a GAME ROOT (it carries 01.cat)
    5. both manifests are well-formed; the source has an id; if the destination exists,
       its manifest id EQUALS the source's. Folder name is not identity
    6. LINKS: the destination folder is not itself a link or junction; no file is written
       through a link, or to a path resolving outside the destination; every orphan
       scheduled for deletion is a plain file (not a link) under the destination

AFTERWARDS it re-reads the destination and requires the same file SET with every file
byte-identical by sha256. A deploy that reports success without re-reading is a claim.

A DEPLOYED FILE IS NOT A LOADED FILE. X4 keeps running the lua it read at load; after
deploying the helper, restart the game and check `x4live query probe` reports the new
`build=`.

Exit: 0 every deploy verified - 1 a deploy did not verify, or failed part way - 2 refused
or not configured.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: `<repo>/tools/x4validate/scripts/deploy-mod.py` -> `<repo>`. From __file__, never an
#: environment variable: the source must be the tree this script is standing in.
REPO = Path(__file__).resolve().parents[3]
MODS = REPO / "mods"
FLAGS = {"--apply", "-h", "--help"}
sys.path.insert(0, str(REPO / "scripts"))
from x4recycle import recycle_or_raise  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x4validate import _paths  # noqa: E402


class Refused(Exception):
    """A guard refused. Nothing was written."""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def files_of(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def manifest_id(mod: Path) -> str | None:
    man = mod / "content.xml"
    if not man.is_file():
        return None
    try:
        return ET.fromstring(man.read_bytes()).get("id")
    except ET.ParseError as exc:
        raise Refused(f"{man} is not well-formed XML ({exc})") from exc


def shipped(src_root: Path) -> list[str]:
    if not src_root.is_dir():
        return []
    return sorted(p.name for p in src_root.iterdir() if (p / "content.xml").is_file())


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _profile_shaped(path: Path) -> bool:
    """`.../Egosoft/X4/<digits>/...` -- the profile layout, recognised by shape."""
    parts = [p.lower() for p in path.resolve().parts]
    return any(parts[i] == "egosoft" and parts[i + 1] == "x4" and parts[i + 2].isdigit()
               for i in range(len(parts) - 2))


def deploy(name: str, apply: bool, src_root: Path = MODS, ext_root: Path | None = None,
           out=print) -> bool:
    """Deploy one shipped mod. Raises `Refused`; returns whether the result VERIFIED."""
    out("=" * 74)
    out(f"### {name}   ({'APPLY' if apply else 'DRY RUN'})")
    out(f"    from {src_root / name}")
    out("=" * 74)

    if name not in shipped(src_root):
        raise Refused(f"{name} is not a mod this repo ships (under {src_root}: "
                      f"{', '.join(shipped(src_root)) or 'none'}), so it does not ship")
    if ext_root is None:
        raise Refused("no game extensions folder is configured (X4_EXTENSIONS or X4_GAME)")
    if not ext_root.is_dir():
        raise Refused(f"the extensions folder {ext_root} does not exist -- check "
                      "X4_EXTENSIONS / X4_GAME; nothing is created for a path the game "
                      "never reads")
    profile = _paths.profile()
    if profile is None:
        out("  profile guard: X4_PROFILE is not configured, so only the path SHAPE "
            "(Egosoft/X4/<id>) was checked")
    why = None
    if profile is not None and _inside(ext_root, profile):
        why = f"is inside the configured user profile {profile}"
    elif _profile_shaped(ext_root):
        why = "is shaped like a user profile folder (Egosoft/X4/<id>)"
    if why:
        raise Refused(f"{ext_root} {why}. Deploy to the game-root extensions folder: "
                      "dependencies resolve only within one extensions root, so a profile "
                      "deploy reads every dependency as MISSING")
    if not (ext_root.parent / "01.cat").is_file():
        raise Refused(f"{ext_root.parent} does not look like the X4 game root (no 01.cat), "
                      "so its extensions folder is not one the game loads")

    src, dst = src_root / name, ext_root / name
    if dst.is_symlink() or dst.is_junction() or (
            dst.exists() and dst.resolve().parent != ext_root.resolve()):
        raise Refused(f"{dst} is a link to somewhere else -- deploying through it would "
                      "write into, and delete orphans from, another tree")
    want = manifest_id(src)
    if not want:
        raise Refused(f"source has no readable content.xml id: {src}")
    if dst.exists():
        got = manifest_id(dst)
        if got is None:
            raise Refused(f"{dst} has no content.xml -- not a mod folder this script wrote")
        if got != want:
            raise Refused(f"destination manifest id {got!r} != source {want!r} -- a different mod")
        out(f"  guard ok: source and destination are both manifest id {got!r}")
    else:
        out(f"  destination does not exist yet -- will be created (manifest id {want!r})")

    srcf = files_of(src)
    dstf = files_of(dst) if dst.exists() else set()
    new = sorted(srcf - dstf)
    orphan = sorted(dstf - srcf)
    changed = sorted(f for f in (srcf & dstf) if sha(src / f) != sha(dst / f))
    same = sorted(f for f in (srcf & dstf) if sha(src / f) == sha(dst / f))
    for label, items in (("NEW", new), ("CHANGED", changed),
                         ("UNCHANGED", same), ("ORPHAN (to delete)", orphan)):
        out(f"  {label:20s} {len(items)}")
        if label != "UNCHANGED":
            for f in items:
                out(f"      {f}")
    for f in new + changed:
        t = dst / f
        if t.is_symlink() or (dst.exists() and not _inside(t.parent, dst)) or (
                t.exists() and not _inside(t, dst)):
            raise Refused(f"refusing to write {t}: it is a link, or resolves outside {dst}")
    for f in orphan:
        t = dst / f
        if t.is_symlink() or not t.is_file() or not _inside(t, dst):
            raise Refused(f"refusing to delete {t}: not a plain file under {dst}")

    if not apply:
        out("  (dry run -- nothing written)\n")
        return True

    dst.mkdir(parents=True, exist_ok=True)
    for f in new + changed:
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, dst / f)
    for f in orphan:
        recycle_or_raise([dst / f])  # one named file; never a directory
        out(f"      recycled orphan: {f}")

    back = files_of(dst)
    bad = [f for f in sorted(srcf) if not (dst / f).is_file() or sha(src / f) != sha(dst / f)]
    ok = back == srcf and not bad
    out(f"  RESULT: same file set={back == srcf}  byte-diff={bad or 'NONE'}\n")
    return ok


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a.startswith("-") and a not in FLAGS]
    if unknown:
        print(f"REFUSING: unknown option(s) {' '.join(unknown)} -- the only option is "
              "--apply; a mistyped one would otherwise be a silent dry run", file=sys.stderr)
        return 2
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        print(f"mods this repo ships: {', '.join(shipped(MODS)) or 'none'}")
        return 0
    names = [a for a in argv if not a.startswith("-")]
    apply = "--apply" in argv
    if not names:
        print(__doc__)
        print(f"mods this repo ships: {', '.join(shipped(MODS)) or 'none'}")
        return 2
    ext = _paths.game_extensions()
    if ext is None:
        print("REFUSING: no game extensions folder is configured -- set X4_EXTENSIONS or "
              "X4_GAME (run the installer, or `uv run x4validate --paths`)", file=sys.stderr)
        return 2
    try:
        for n in names:                      # every guard, for every mod, first
            deploy(n, False, MODS, ext, out=lambda s: None)
    except (Refused, OSError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    try:
        ok = all([deploy(n, apply, MODS, ext) for n in names])
    except Refused as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"!! a deploy FAILED part way ({exc}); re-run the dry run to see what the "
              "destination now holds", file=sys.stderr)
        return 1
    if apply and not ok:
        print("!! a deploy did not verify clean", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

r"""Read X4 ``.cat``/``.dat`` mod archives as an in-memory virtual filesystem.

X4 mods ship either loose files or packed catalog pairs. To reason about a packed
mod (e.g. VRO, which is 217 MB packed and otherwise invisible to our tooling) we
must read its catalog directly. The format is simple and community-documented:

- A ``.cat`` is a UTF-8 text index, one line per member::

      <virtual/path> <size> <unix_mtime> <md5hex>

  Paths may contain spaces; the trailing three tokens are fixed, so we parse from
  the right (``rsplit(" ", 3)``).
- The matching ``.dat`` stores member payloads concatenated in ``.cat`` line order,
  so a member's byte offset is the running sum of preceding sizes.
- A mod folder may hold ``ext_NN.cat`` (extension-local additions/diffs) and/or
  ``subst_NN.cat`` (full-file base-game substitutions). Both are read and resolve by
  virtual path; later catalogs override earlier ones for the same path.
- ``*_sig.cat`` are signatures — skipped.
- Version/diff catalogs (``ext_vNNN.cat``, ``ext_NN_diff_vNNN.cat``) load ONLY when
  the game version equals NNN exactly (Egosoft workshop guide; engine-proven
  2026-08-02 via two independent debug.txt fingerprints). Mods DO ship them — a
  stale one is dead weight the engine ignores too, so skipping matches the engine
  for every non-matching version. A cat matching the CURRENT game version would be
  live; those are still skipped here, with a warning, until support is built.

This is an independent implementation from the documented format; cross-checked for
correctness against Egosoft's XRCatTool and meethune/x4cat (MIT) as format references.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
# Plain mod catalogs we read: ext_01.cat, subst_02.cat, ...
_PLAIN_CAT_RE = re.compile(r"^(ext|subst)_(\d+)\.cat$", re.IGNORECASE)
# Version/diff catalogs we deliberately skip. Mods DO ship them (see the module
# docstring); each skip is logged with its owning folder.
_VERSION_CAT_RE = re.compile(r"^(ext|subst)_.*v\d+\.cat$", re.IGNORECASE)
# Member extensions worth extracting (everything our analysis parses is XML/XSD).
_XML_SUFFIXES = (".xml", ".xsd")


@dataclass(frozen=True)
class CatMember:
    """One member of a ``.cat`` index, locatable in the sibling ``.dat``."""

    path: str          # normalized virtual path (forward slashes, no leading /)
    size: int
    mtime: int
    md5: str
    cat_path: Path     # the .cat this came from (.dat is the same stem)
    dat_offset: int


def _parse_line(line: str) -> tuple[str, int, int, str] | None:
    """Parse one index line into ``(path, size, mtime, md5)`` or ``None`` if malformed."""
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.rsplit(" ", 3)
    if len(parts) != 4:
        return None
    raw_path, size_s, mtime_s, md5 = parts
    try:
        size, mtime = int(size_s), int(mtime_s)
    except ValueError:
        # silent-ok: int() coercion on one catalog index line. The caller logs
        # "Malformed catalog line in %s: %r" for every None, so the failure IS
        # reported — one level up, where the file name is known.
        return None
    if size < 0 or mtime < 0 or not _MD5_RE.match(md5):
        return None
    normalized = raw_path.replace("\\", "/").lstrip("/")
    return normalized, size, mtime, md5


def _iter_mod_cats(mod_dir: Path) -> list[Path]:
    """Return a mod's readable catalogs (subst_* then ext_*, numeric order).

    subst_ (base substitutions) load before ext_ (extension-local) so that on the
    rare path defined by both, the extension-local copy wins — matching how a mod's
    own additions take precedence over its base overrides. Version/diff catalogs are
    skipped with a warning.
    """
    if not mod_dir.is_dir():
        return []
    plain: list[tuple[int, int, Path]] = []  # (prefix_rank, numeric_id, path)
    for p in sorted(mod_dir.iterdir()):
        if not p.is_file() or p.name.lower().endswith("_sig.cat"):
            continue
        if _VERSION_CAT_RE.match(p.name):
            # Name the OWNER. Two different mods each ship an ext_v800.cat, so
            # without the folder the two legitimate warnings are indistinguishable
            # from one emitted twice — which is exactly how it was misread.
            logger.warning(
                "Skipping version/diff catalog %s in '%s' (loads only when the game "
                "version matches its vNNN exactly; a non-matching one is ignored by "
                "the engine too)",
                p.name, mod_dir.name
            )
            continue
        m = _PLAIN_CAT_RE.match(p.name)
        if not m:
            continue
        prefix_rank = 0 if m.group(1).lower() == "subst" else 1
        plain.append((prefix_rank, int(m.group(2)), p))
    plain.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in plain]


def _read_index(cat_path: Path, xml_only: bool) -> list[CatMember]:
    """Parse a single ``.cat`` into members with correct cumulative ``.dat`` offsets.

    Offsets accumulate over EVERY member (even non-XML ones we don't return), because
    the ``.dat`` concatenates all payloads regardless of what we choose to expose.
    """
    members: list[CatMember] = []
    offset = 0
    with open(cat_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = _parse_line(line)
            if parsed is None:
                if line.strip():
                    logger.warning("Malformed catalog line in %s: %r", cat_path.name, line.strip())
                continue
            path, size, mtime, md5 = parsed
            if not xml_only or path.lower().endswith(_XML_SUFFIXES):
                members.append(CatMember(path, size, mtime, md5, cat_path, offset))
            offset += size
    return members


def build_mod_vfs(mod_dir: Path, xml_only: bool = True) -> dict[str, CatMember]:
    """Merge all of a mod's catalogs into ``{virtual_path: CatMember}``.

    Later catalogs override earlier ones for the same virtual path. Returns an empty
    dict for a loose-only (or nonexistent) mod. When *xml_only*, only ``.xml``/``.xsd``
    members are indexed (geometry/textures/audio are skipped).
    """
    vfs: dict[str, CatMember] = {}
    for cat_path in _iter_mod_cats(mod_dir):
        for member in _read_index(cat_path, xml_only):
            vfs[member.path] = member
    return vfs


@lru_cache(maxsize=256)
def _cached_vfs(mod_dir_str: str, xml_only: bool) -> dict[str, CatMember]:
    return build_mod_vfs(Path(mod_dir_str), xml_only)


#: Directories already warned about, so a loop over 115 mods cannot spam.
_warned_packed_only: set[str] = set()


def mod_vfs(mod_dir: Path, xml_only: bool = True,
            packed_only: bool = False) -> dict[str, CatMember]:
    """Memoized :func:`build_mod_vfs` — safe within a single CLI run (cats are static).

    ⚠ **This reads CATALOGS ONLY.** For a loose (unpacked) mod it returns ``{}``,
    which is the correct answer to "what is in this mod's archives" and the WRONG
    answer to "what XML does this mod own". Use `_scan.iter_mod_xml` /
    `iter_mod_xml_bytes` for the latter — they enumerate loose THEN packed with the
    engine's loose-shadows-packed rule.

    MEASURED 2026-08-13, the sixth instance of this defect shape: an ad-hoc corpus
    scan built on `mod_vfs` alone read **2,681** XML files across 115 mods and
    reported a name "NOT FOUND". Adding the loose half read **4,401** and found it
    immediately, in a loose file. Nothing warned; only the count looking too low did.

    So: when this is about to return ``{}`` for a directory that *does* contain
    loose XML, and the caller has not passed *packed_only*, say so on stderr. That
    is exactly the silent-narrowing case and nothing else — a packed mod, an empty
    mod, and an acknowledged is-it-packed test are all silent.

    Pass ``packed_only=True`` when "catalogs only" is genuinely what you mean (an
    is-this-mod-packed test, or a reader-edge assertion). It documents the intent
    at the call site as well as silencing the warning.
    """
    vfs = _cached_vfs(str(mod_dir), xml_only)
    if vfs or packed_only:
        return vfs
    key = str(mod_dir)
    if key in _warned_packed_only:
        return vfs
    # Recorded BEFORE the check, not after: a loose mod with no XML at all would
    # otherwise re-walk its whole tree on every call, since it never warns.
    _warned_packed_only.add(key)
    try:
        has_loose = next(mod_dir.rglob("*.xml"), None) is not None
    except OSError:
        # silent-ok: an unreadable dir is not the case this warning is about, and
        # the empty result is already the honest answer for it.
        return vfs
    if has_loose:
        logger.warning(
            "mod_vfs('%s') reads CATALOGS ONLY and returned 0 members, but this mod "
            "ships loose .xml. If you meant 'every XML this mod owns', use "
            "_scan.iter_mod_xml(); pass packed_only=True to acknowledge catalogs-only.",
            mod_dir.name
        )
    return vfs


@lru_cache(maxsize=256)
def _folded_vfs(mod_dir_str: str, xml_only: bool) -> dict[str, CatMember]:
    """Case-folded index of a mod's VFS, built once per archive.

    `_get_ci` previously answered a MISS by scanning every key and lowercasing
    it — and a miss is the common case, because most overlays do not contain the
    path being asked about. Cost was O(members) per lookup, per overlay, per
    file: validating a 116-file cross-mod patch against ~120 installed
    extensions ran past 900s. Folding once and caching makes the miss O(1).
    """
    return {k.lower(): v for k, v in _cached_vfs(mod_dir_str, xml_only).items()}


def read_member(member: CatMember, verify: bool = True) -> bytes:
    """Read a member's raw bytes from its ``.dat``; verify the MD5 by default."""
    dat_path = member.cat_path.with_suffix(".dat")
    with open(dat_path, "rb") as f:
        f.seek(member.dat_offset)
        data = f.read(member.size)
    if len(data) != member.size:
        raise OSError(
            f"Short read for {member.path} in {dat_path.name}: "
            f"expected {member.size}, got {len(data)}"
        )
    if verify:
        actual = hashlib.md5(data).hexdigest()
        if actual != member.md5:
            raise OSError(
                f"MD5 mismatch for {member.path} in {dat_path.name}: "
                f"index {member.md5}, actual {actual}"
            )
    return data


def _get_ci(vfs: dict[str, CatMember], vpath: str,
            folded: dict[str, CatMember] | None = None) -> CatMember | None:
    """Case-insensitive VFS lookup — X4 treats virtual paths case-insensitively
    (e.g. VRO ships ``t/0001-L007.xml`` where others use ``l007``).

    Pass *folded* (see :func:`_folded_vfs`) to make a miss O(1). Without it this
    falls back to a linear scan, which is correct but was the hot path: a miss is
    the NORMAL outcome, since most overlays simply do not contain the file being
    asked about.
    """
    member = vfs.get(vpath)
    if member is not None:
        return member
    low = vpath.lower()
    if folded is not None:
        return folded.get(low)
    for k, v in vfs.items():
        if k.lower() == low:
            return v
    return None


def read_path(mod_dir: Path, vpath: str, verify: bool = True) -> bytes | None:
    """Read one virtual path's bytes from a mod's catalogs, or ``None`` if absent.

    Any member type: an ``.xml``/``.xsd`` vpath is looked up in the XML-only index
    every XML reader shares, and anything else (``.lua``, ``.xmf``, ``.dds`` ...) in the
    full index. Both used to go through the XML-only one, so a packed non-XML member
    read as ``None`` -- "absent" -- while it was there (MEASURED 2026-11-08:
    ``ui/simple_menu/options_menu.lua`` in sn_mod_support_apis).
    """
    vpath = vpath.replace("\\", "/").lstrip("/")
    xml_only = vpath.lower().endswith(_XML_SUFFIXES)
    # packed-ok: this function IS the catalogs reader — its docstring says so, and every
    # caller pairs it with the loose half first (`_merge.overlay_root` checks
    # `loose.is_file()` and only then falls through to here). MEASURED 2026-08-22: without
    # this acknowledgement the warning fired ~15 times on an ordinary `x4validate` run,
    # once per loose mod, at a call site where it can never be right. A check that floods
    # is worse than no check — it trains you to skim past the run it IS right about.
    # The XML call is kept exactly as it was (positional dir + packed_only): callers and tests that
    # substitute mod_vfs rely on that signature. Only a non-XML vpath asks for the full index.
    vfs = mod_vfs(mod_dir, packed_only=True) if xml_only else mod_vfs(mod_dir, xml_only=False, packed_only=True)
    member = _get_ci(vfs, vpath, _folded_vfs(str(mod_dir), xml_only))
    return None if member is None else read_member(member, verify=verify)


def is_packed(mod_dir: Path) -> bool:
    """True if the mod ships any readable catalog (vs loose files only)."""
    return bool(_iter_mod_cats(mod_dir))

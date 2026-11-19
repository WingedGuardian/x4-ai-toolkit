r"""The INPUT SNAPSHOT every effective-store build is made from (toolkit 4.3, item 6).

WHY IT EXISTS. `gates/cross_tool.py` compares two readers of the same world: x4compat
(reading the mod files when the gate runs) and the effective store (reading them when
it was built). Read at two different moments, the comparison needed the modlist held
still for the length of a store build plus the check, and a stale store was refused
outright. User decision 2026-11-18: cross_tool must never need a modlist hold. So every
store build takes a snapshot of its inputs and is built FROM it, and cross_tool runs
x4compat against the same snapshot after PROVING it is the one the store was built from.
That also closes the mid-build-edit gap: the store can no longer read a mod file that
changed after the fingerprint was taken.

WHAT IS COPIED (MEASURED 2026-11-18 on the live install, 240 mod folders: the engine-
relevant files are 13.8 GB, almost all `.dat` textures; the XML a build reads is ~82 MB
-- 36 MB loose, 46 MB packed -- so a full copy, or hardlinks, are ruled out):

* every loose ``.xml`` / ``.xsd`` / ``.lua`` file, byte for byte;
* every other loose file as a 0-byte NAME STUB, so a directory listing (wildcard icon
  textures, `_merge`'s loose index) sees the same names;
* every ``.cat`` REWRITTEN with every member line kept: an XML/XSD/LUA member keeps its
  size and md5 and its bytes go into a new ``.dat`` beside it, in line order; any other
  member keeps its path, mtime and ORIGINAL md5 with size 0, so it takes no room in the
  ``.dat`` and every offset stays right. `_cat.read_member` works unchanged and still
  md5-checks: a kept member reads byte-identical to the live one, and reading a zeroed
  member FAILS LOUDLY (md5 of nothing != the recorded md5) instead of returning bytes
  that are not the file's;
* each ``ego_dlc_*`` folder's ``content.xml`` only -- the DLC is base game, read from
  the reference and from the live packed archives (`_merge.GAME_ROOT` stays live), and
  its live records are re-checked by `prove`;
* the profile ``content.xml``.

Each file's live mtime is carried over to its copy. `MANIFEST.json` lists every file
with its sha256 and size, plus the live fingerprint the store is stamped with;
`RECEIPT.json` is the creation-time disposal receipt ("copies only; regenerate with
x4effective build"). Then the live tree is walked AGAIN: if anything moved, the
snapshot is retaken, up to RETRIES times, and then the build is REFUSED naming the files.

Built in `<db>.inputs.<pid>.tmp`, installed at `<db>.inputs` only after the store itself
was installed. The previous snapshot is DELETED outright only when its receipt and every
hash still match (it is then proven to be nothing but rebuildable copies); otherwise it
goes to the Recycle Bin, and if that is impossible it is left in place and reported.

This module IS an engine source (`_freshness.ENGINE_SOURCES`): `world()` and the
catalog rewrite decide which bytes the merge reads, so an edit here can change the
store for byte-identical live inputs -- the definition of that axis. It therefore
prints nothing and parses no command line (tests/test_engine_sources_carry_no_cli.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# The one DLC-folder test (F284); `_merge` never imports this module, so no cycle.
from x4validate._merge import is_dlc_folder

#: The `_registry` module constants that name the install roots and the profile. Every
#: reader of a root reads one of these AT CALL TIME (tests/test_inputs_snapshot.py bans
#: an import-time copy), which is what lets `world()` point the whole toolkit at a snapshot.
ROOT_CONSTANTS = ("GAME_EXTENSIONS", "PROFILE_EXTENSIONS", "WORKSHOP_CONTENT", "PROFILE_CONTENT")
#: Loose files copied byte for byte, and catalog members whose bytes are kept.
COPY_SUFFIXES = (".xml", ".xsd", ".lua")
#: Retakes after the first attempt before a build is refused.
RETRIES = 3
SUFFIX = ".inputs"
MANIFEST = "MANIFEST.json"
RECEIPT = "RECEIPT.json"
FORMAT = 1
GENERATOR = "x4validate._inputs (x4effective build)"
DISPOSITION = "copies only; regenerate with x4effective build"
#: Listed per refusal; the rest is counted.
MAX_NAMED = 10

#: Test seam: called with the attempt number after the copy and before the re-walk.
_AFTER_COPY = None


class SnapshotRefused(RuntimeError):
    """The live inputs kept moving while they were copied, or could not be copied."""


def snapshot_path(db_path: Path) -> Path:
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + SUFFIX)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_dir(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (OSError, ValueError):
        return False  # silent-ok: an unspellable path is "not the same root", which maps it to None


@dataclass
class Snapshot:
    """A snapshot on disk: its folder and the parsed manifest."""

    path: Path
    manifest: dict
    manifest_sha: str

    def dirs(self) -> list[Path]:
        """The snapshot's copies of the build's roots, in the build's order."""
        return [self.path / r["snap"] for r in self.manifest["roots"]]

    def constant(self, name: str):
        rel = self.manifest["constants"].get(name)
        return None if rel is None else self.path / rel

    @property
    def stamp(self) -> dict:
        return self.manifest["stamp"]

    def kind_of(self, live_root: str) -> str:
        for r in self.manifest["roots"]:
            if _same_dir(r["live"], live_root):
                return r["kind"]
        return f"unknown-root:{live_root}"


# --------------------------------------------------------------------------- the live walk

def _listing(roots: list[Path]) -> dict[tuple[int, str], tuple | None]:
    """What a snapshot copies, keyed (root index, path under the root): (size, mtime_ns)
    for a file whose bytes are copied or rewritten, None for a name-only stub. A moved
    name, a moved copied file, or a moved catalog makes two listings differ."""
    out: dict[tuple[int, str], tuple | None] = {}
    for i, root in enumerate(roots):
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir():
                continue
            if is_dlc_folder(d.name):
                m = d / "content.xml"
                try:
                    st = m.stat()
                    out[(i, f"{d.name}/content.xml")] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass  # silent-ok: a DLC without a readable manifest has nothing to copy
                continue
            for dirpath, _dirs, files in os.walk(d):
                for name in files:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root).replace(os.sep, "/")
                    if os.path.splitext(name)[1].lower() in COPY_SUFFIXES + (".cat", ".dat"):
                        try:
                            st = os.stat(full)
                            out[(i, rel)] = (st.st_size, st.st_mtime_ns)
                        except OSError:
                            out[(i, rel)] = ("unreadable",)
                    else:
                        out[(i, rel)] = None
    return out


def _profile_state(profile) -> tuple | None:
    if profile is None:
        return None
    try:
        return ("file", _sha_bytes(Path(profile).read_bytes()))
    except OSError:
        return ("absent",)


# --------------------------------------------------------------------------- copying

class _Writer:
    """Writes snapshot files and records each one's sha256 and size for the manifest."""

    def __init__(self, base: Path):
        self.base = base
        self.files: dict[str, list] = {}
        self.bytes = 0

    def _dst(self, rel: str) -> Path:
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, rel: str, data: bytes, mtime_ns: int | None = None) -> None:
        dst = self._dst(rel)
        dst.write_bytes(data)
        if mtime_ns is not None:
            os.utime(dst, ns=(mtime_ns, mtime_ns))
        self.files[rel] = [_sha_bytes(data), len(data)]
        self.bytes += len(data)

    def put_cat(self, rel: str, src_cat: Path, src_mtime_ns: int) -> None:
        """Rewrite one catalog (see the module docstring) and build its `.dat`."""
        src_dat = src_cat.with_suffix(".dat")
        dat_rel = rel[: -len(".cat")] + ".dat"
        lines: list[str] = []
        dat_hash = hashlib.sha256()
        dat_len = 0
        have_dat = src_dat.is_file()
        dat_out = None
        dat_in = None
        try:
            if have_dat:
                dat_in = open(src_dat, "rb")
                dat_out = open(self._dst(dat_rel), "wb")
            offset = 0
            # Exactly how `_cat._read_index` reads it: text, utf-8 with replacement, so a
            # line this keeps parses to the same member there. newline="" keeps endings.
            from x4validate import _cat
            with open(src_cat, encoding="utf-8", errors="replace", newline="") as f:
                for line in f:
                    parsed = _cat._parse_line(line)
                    if parsed is None:
                        lines.append(line)   # malformed: the reader skips it either way
                        continue
                    path, size, _mtime, _md5 = parsed
                    if path.lower().endswith(COPY_SUFFIXES):
                        if dat_in is not None:
                            dat_in.seek(offset)
                            data = dat_in.read(size)
                            # A short live .dat is padded so every LATER offset stays
                            # right; this member then fails its md5 here as it fails a
                            # short read there -- loudly in both.
                            data = data + b"\0" * (size - len(data))
                            dat_out.write(data)
                            dat_hash.update(data)
                            dat_len += len(data)
                        lines.append(line)
                    else:
                        body = line.rstrip("\r\n")
                        ending = line[len(body):]
                        raw_path, _size_s, mtime_s, md5 = body.strip().rsplit(" ", 3)
                        lines.append(f"{raw_path} 0 {mtime_s} {md5}{ending}")
                    offset += size
        finally:
            if dat_in is not None:
                dat_in.close()
            if dat_out is not None:
                dat_out.close()
        if have_dat:
            st = src_dat.stat()
            os.utime(self.base / dat_rel, ns=(st.st_mtime_ns, st.st_mtime_ns))
            self.files[dat_rel] = [dat_hash.hexdigest(), dat_len]
            self.bytes += dat_len
        self.put(rel, "".join(lines).encode("utf-8"), src_mtime_ns)


def _copy_root(w: _Writer, root: Path, snap_rel: str) -> None:
    if not root.is_dir():
        return
    for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        if is_dlc_folder(d.name):
            m = d / "content.xml"
            if m.is_file():
                st = m.stat()
                w.put(f"{snap_rel}/{d.name}/content.xml", m.read_bytes(), st.st_mtime_ns)
            continue
        for dirpath, dirnames, files in os.walk(d):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            (w.base / snap_rel / rel_dir).mkdir(parents=True, exist_ok=True)
            cats = {os.path.splitext(n)[0].lower() for n in files if n.lower().endswith(".cat")}
            for name in sorted(files):
                full = Path(dirpath) / name
                rel = f"{snap_rel}/{rel_dir}/{name}"
                low = name.lower()
                ext = os.path.splitext(low)[1]
                st = full.stat()
                if ext in COPY_SUFFIXES:
                    w.put(rel, full.read_bytes(), st.st_mtime_ns)
                elif ext == ".cat":
                    w.put_cat(rel, full, st.st_mtime_ns)
                elif ext == ".dat" and os.path.splitext(low)[0] in cats:
                    continue                     # written by its catalog's rewrite
                else:
                    w.put(rel, b"", st.st_mtime_ns)


def _moved(stamp0: dict, stamp1: dict, list0: dict, list1: dict, prof0, prof1, roots) -> list[str]:
    """Every name that moved between two walks of the live inputs, or [] when still."""
    from x4validate import _freshness
    names: list[str] = []
    if stamp0.get("engine") != stamp1.get("engine"):
        names.append("the toolkit's engine sources (an edit during the snapshot)")
    if stamp0.get("reference") != stamp1.get("reference"):
        names.append("the reference tree")
    try:
        for ch in _freshness.diff_detail(stamp0.get("detail") or [], stamp1.get("detail") or []):
            files = list(ch.get("files_changed") or []) + list(ch.get("files_touched") or [])
            if files:
                names.extend(f"{ch['folder']}/{f}" for f in files)
            else:
                names.append(f"{ch['folder']} ({ch['kind']})")
    except _freshness.NoBaseline:
        pass  # silent-ok: [] detail means no extensions root; the listing below still compares
    for key in sorted(set(list0) | set(list1)):
        if list0.get(key, "<absent>") != list1.get(key, "<absent>"):
            i, rel = key
            names.append(f"{roots[i]}/{rel}" if i < len(roots) else rel)
    if prof0 != prof1:
        names.append("the profile content.xml")
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def take(db_path: Path, roots: list[Path], stamp_fn, progress=lambda s: None) -> Snapshot:
    """Snapshot *roots* (and the profile) beside *db_path*, in a temp folder.

    *stamp_fn* returns the live fingerprint (`_freshness.fingerprint` over the store's
    roots); it is taken before the copy and again after it, and the snapshot is kept
    only when both, and the full listing, agree -- so the stamp the store gets is PROVEN
    to describe the bytes it was built from. Raises SnapshotRefused naming what moved.
    """
    from x4validate import _registry
    db_path = Path(db_path)
    roots = [Path(r) for r in roots]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(f"{db_path.name}{SUFFIX}.{os.getpid()}.tmp")
    profile = _registry.PROFILE_CONTENT
    root_recs = [{"index": i, "kind": _registry._root_kind(r), "live": str(r), "snap": f"r{i}",
                  "exists": r.is_dir()} for i, r in enumerate(roots)]
    constants: dict[str, str | None] = {}
    for name in ROOT_CONSTANTS[:3]:
        live = getattr(_registry, name)
        constants[name] = next((rec["snap"] for rec in root_recs
                                if live is not None and _same_dir(rec["live"], live)), None)
    constants["PROFILE_CONTENT"] = None if profile is None else "p/content.xml"
    t0 = time.monotonic()
    moved: list[str] = []
    for attempt in range(1 + RETRIES):
        if tmp.exists() and attempt:
            shutil.rmtree(tmp)                   # this run's own temp, from the attempt before
        elif tmp.exists():
            # NOT this run's (a dead build with a reused pid, or anything else at the name):
            # the same proof as a replaced snapshot -- deleted outright only when its receipt
            # and every hash prove it rebuildable, else the Recycle Bin, never a silent
            # fallback to a permanent delete.
            progress(dispose(tmp, "a leftover input snapshot temp"))
            if tmp.exists():
                raise SnapshotRefused(
                    f"a leftover {tmp.name} could not be disposed of safely (see the line "
                    "above), so no snapshot was taken and the existing store is unchanged -- "
                    "move or delete that folder yourself, then rebuild")
        stamp0, list0, prof0 = stamp_fn(), _listing(roots), _profile_state(profile)
        w = _Writer(tmp)
        tmp.mkdir(parents=True)
        try:
            for rec, root in zip(root_recs, roots):
                if rec["exists"]:               # a missing root stays missing in the copy
                    (tmp / rec["snap"]).mkdir(parents=True, exist_ok=True)
                _copy_root(w, root, rec["snap"])
            if profile is not None and Path(profile).is_file():
                pst = Path(profile).stat()
                w.put("p/content.xml", Path(profile).read_bytes(), pst.st_mtime_ns)
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise SnapshotRefused(
                f"could not copy the build's inputs into {tmp}: {exc} -- the store was NOT "
                "rebuilt (a file vanished mid-copy, or a path is too long: retry)") from exc
        if _AFTER_COPY is not None:
            _AFTER_COPY(attempt)
        stamp1, list1, prof1 = stamp_fn(), _listing(roots), _profile_state(profile)
        moved = _moved(stamp0, stamp1, list0, list1, prof0, prof1, roots)
        if not moved:
            break
        progress(f"input snapshot attempt {attempt + 1}: {len(moved)} input(s) moved while "
                 f"they were copied (e.g. {moved[0]}); retaking it")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SnapshotRefused(
            f"the build's inputs kept moving while they were copied ({1 + RETRIES} attempts), so "
            "no store was built from them -- the existing store is unchanged. Still moving: "
            + "; ".join(moved[:MAX_NAMED])
            + (f"; and {len(moved) - MAX_NAMED} more" if len(moved) > MAX_NAMED else ""))
    manifest = {"format": FORMAT, "roots": root_recs, "constants": constants,
                "profile": {"live": None if profile is None else str(profile),
                            "copied": "p/content.xml" in w.files},
                "stamp": stamp0, "files": dict(sorted(w.files.items()))}
    body = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    (tmp / MANIFEST).write_bytes(body)
    msha = _sha_bytes(body)
    receipt = {"generator": GENERATOR, "store": db_path.name, "manifest_sha256": msha,
               "files": len(w.files), "bytes": w.bytes, "disposition": DISPOSITION,
               "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (tmp / RECEIPT).write_bytes(json.dumps(receipt, indent=1, sort_keys=True).encode("utf-8"))
    progress(f"input snapshot: {len(w.files)} file(s), {w.bytes / 1e6:.1f} MB, "
             f"{time.monotonic() - t0:.1f} s")
    return Snapshot(tmp, manifest, msha)


# --------------------------------------------------------------------------- the world swap

def _clear_caches() -> None:
    """Every path-keyed cache a build or x4compat fills, so no answer crosses worlds."""
    from x4validate import _cat, _merge
    _cat._cached_vfs.cache_clear()
    _cat._folded_vfs.cache_clear()
    _merge._folded_loose.cache_clear()
    _merge.EffectiveIndex.clear()


@contextmanager
def world(snap: Snapshot):
    """Point the four `_registry` root constants at *snap* for the block, then restore them.

    `_merge.GAME_ROOT` and the reference stay LIVE: the packed DLC and the unpacked
    reference are base game, not inputs the snapshot copies (`prove` re-checks both)."""
    from x4validate import _registry
    saved = {n: getattr(_registry, n) for n in ROOT_CONSTANTS}
    _clear_caches()
    try:
        for n in ROOT_CONSTANTS:
            setattr(_registry, n, snap.constant(n))
        yield snap
    finally:
        for n, v in saved.items():
            setattr(_registry, n, v)
        _clear_caches()


# --------------------------------------------------------------------------- load / verify

def load(db_path: Path) -> Snapshot | None:
    p = snapshot_path(db_path)
    try:
        body = (p / MANIFEST).read_bytes()
        return Snapshot(p, json.loads(body), _sha_bytes(body))
    except (OSError, ValueError):
        return None  # silent-ok: "no usable snapshot" -- every caller refuses on None


def _disk_files(base: Path) -> tuple[dict[str, Path], list[str]]:
    """Every file under *base* except the two records, plus any LINK found (a link is
    never a snapshot's own copy, so it can never be proven)."""
    out: dict[str, Path] = {}
    links: list[str] = []
    for dirpath, dirnames, files in os.walk(base):
        for n in list(dirnames):
            full = os.path.join(dirpath, n)
            if os.path.islink(full) or _is_junction(full):
                links.append(os.path.relpath(full, base).replace(os.sep, "/"))
                dirnames.remove(n)
        for n in files:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if rel in (MANIFEST, RECEIPT):
                continue
            if os.path.islink(full):
                links.append(rel)
            out[rel] = Path(full)
    return out, links


def _is_junction(p: str) -> bool:
    fn = getattr(os.path, "isjunction", None)
    return bool(fn and fn(p))


def verify_files(snap: Snapshot) -> list[str]:
    """[] when every file on disk is exactly the manifest's set and re-hashes to it."""
    problems: list[str] = []
    want = snap.manifest.get("files") or {}
    have, links = _disk_files(snap.path)
    problems += [f"a link in the snapshot: {r}" for r in links]
    problems += [f"missing from the snapshot: {r}" for r in sorted(set(want) - set(have))]
    problems += [f"not in the manifest: {r}" for r in sorted(set(have) - set(want))]
    for rel in sorted(set(want) & set(have)):
        try:
            data = have[rel].read_bytes()
        except OSError as exc:
            problems.append(f"unreadable: {rel} ({exc})")
            continue
        if [_sha_bytes(data), len(data)] != list(want[rel]):
            problems.append(f"changed since the build: {rel}")
    return problems


def _keyed(detail, snap: Snapshot) -> dict[tuple[str, str], dict]:
    out = {}
    for rec in detail or []:
        r = dict(rec)
        root = r.pop("root", "")
        out[(snap.kind_of(root), r["folder"].lower())] = r
    return out


def prove(db_path: Path, config=None) -> tuple[Snapshot | None, list[str]]:
    """Is `<db>.inputs` exactly what the store at *db_path* was built from, and is
    everything the snapshot does NOT copy unchanged since? ``(snapshot, [])`` when
    proven, else ``(snapshot-or-None, [every reason])``. A missing snapshot, an
    unreadable store and every partial answer are reasons: nothing here passes by default.

    Checked: the manifest's sha256 == the store's `input_snapshot`; every snapshot file
    re-hashes to the manifest, with nothing added or missing; the manifest's per-folder
    content detail == the store's `fingerprint_detail` (matched by root KIND and folder),
    and its content digest == the store's; the engine sources still hash to the store's
    engine axis; the reference digest is the store's; every live `ego_dlc_*` record
    equals the store's. A moved MOD is not a reason -- that is the point of the snapshot.
    """
    import sqlite3

    from x4validate import _freshness, _merge
    db_path = Path(db_path)
    problems: list[str] = []
    snap = load(db_path)
    if snap is None:
        return None, [f"no readable input snapshot at {snapshot_path(db_path)} (stores built "
                      "before toolkit 4.3 have none): rebuild with `x4effective build`"]
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("select key, value from meta").fetchall())
            stored = _freshness.read_sqlite(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return snap, [f"could not read the store's metadata: {exc}"]
    recorded = meta.get("input_snapshot")
    if not recorded:
        return snap, ["the store records no input snapshot (built before toolkit 4.3): rebuild it"]
    if recorded != snap.manifest_sha:
        problems.append(f"the snapshot is not the one this store was built from "
                        f"[store {recorded[:16]} != snapshot {snap.manifest_sha[:16]}]")
    rec = _receipt(snap.path)
    if rec is None or rec.get("manifest_sha256") != snap.manifest_sha:
        problems.append("the snapshot's RECEIPT.json is missing or does not match its manifest")
    problems += verify_files(snap)
    if stored is None or stored.get("content") is None:
        problems.append("the store's content axis is UNKNOWN, so its world cannot be matched")
        return snap, problems
    stamp = snap.stamp
    if stored.get("content") != stamp.get("content"):
        problems.append("the store's content digest is not the snapshot's")
    a, b = _keyed(stored.get("detail"), snap), _keyed(stamp.get("detail"), snap)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            problems.append(f"per-file detail differs for {key[1]} ({key[0]} root)")
    if stored.get("engine") != _freshness.hash_engine():
        problems.append(f"engine changed since the build: the merge code was edited "
                        f"[{stored.get('engine')} -> {_freshness.hash_engine()}]")
    config = config or _merge.Config()
    now_ref = _freshness.reference_digest(config.reference)
    if stored.get("reference") is None or stored.get("reference") != now_ref:
        problems.append(f"the reference tree moved since the build "
                        f"[{stored.get('reference')} -> {now_ref}]")
    # The DLC is read LIVE (base game): its records must not have moved. The profile
    # bit is taken from the snapshot's copy, so only the DLC's own files count.
    live_roots = [Path(r["live"]) for r in snap.manifest["roots"]]
    prof = snap.constant("PROFILE_CONTENT")
    live = _keyed([r for r in _freshness.content_detail(config.reference, live_roots,
                                                          profile=prof)
                   if is_dlc_folder(r["folder"])], snap)
    stored_dlc = {k: v for k, v in a.items() if is_dlc_folder(k[1])}
    for key in sorted(set(live) | set(stored_dlc)):
        if live.get(key) != stored_dlc.get(key):
            problems.append(f"DLC {key[1]} changed since the build")
    return snap, problems


def quick_problems(db_path: Path, meta: dict, stored: dict | None, config=None) -> list[str]:
    """The CHEAP part of `prove`, for a status line (`x4fresh status` runs at every session
    start): the snapshot is the store's, its receipt matches, and the engine code and the
    reference have not moved. It re-hashes no file and walks no DLC -- the full proof is
    cross_tool's own first step, so a status that says "cross_tool runs" can still meet a
    refusal there for a hand-edited snapshot file. [] = backed."""
    from x4validate import _freshness, _merge
    snap = load(db_path)
    if snap is None:
        return ["no input snapshot"]
    out = []
    if meta.get("input_snapshot") != snap.manifest_sha:
        out.append("the snapshot is not this store's")
    rec = _receipt(snap.path)
    if rec is None or rec.get("manifest_sha256") != snap.manifest_sha:
        out.append("the snapshot's receipt does not match")
    if stored is None or stored.get("engine") != _freshness.hash_engine():
        out.append("engine changed")
    ref = (config or _merge.Config()).reference
    if stored is None or stored.get("reference") != _freshness.reference_digest(ref):
        out.append("reference changed")
    return out


# --------------------------------------------------------------------------- install / dispose

def _receipt(base: Path) -> dict | None:
    try:
        return json.loads((base / RECEIPT).read_bytes())
    except (OSError, ValueError):
        return None  # silent-ok: an absent receipt makes the folder UNPROVEN, never deletable


def proven_rebuildable(base: Path) -> list[str]:
    """[] when *base* is provably nothing but a snapshot's own copies: its receipt names
    this generator and its manifest, and every file matches. Else the reasons."""
    rec = _receipt(base)
    if rec is None:
        return ["no readable RECEIPT.json"]
    if rec.get("generator") != GENERATOR or rec.get("disposition") != DISPOSITION:
        return ["the receipt was not written by the snapshot builder"]
    try:
        body = (base / MANIFEST).read_bytes()
        manifest = json.loads(body)
    except (OSError, ValueError):
        return ["no readable MANIFEST.json"]
    if rec.get("manifest_sha256") != _sha_bytes(body):
        return ["the receipt does not match the manifest"]
    return verify_files(Snapshot(base, manifest, _sha_bytes(body)))


def _recycle(path: Path) -> str | None:
    """Move *path* to the Recycle Bin. None on success, else why it is still there.
    In-process (no helper console). A network drive has no bin and is refused up front:
    the shell would otherwise destroy the folder without asking."""
    if os.name != "nt":
        return "no Recycle Bin on this platform"
    import ctypes
    from ctypes import wintypes
    full = str(Path(path).resolve())
    if full.startswith("\\\\"):
        return "a network path has no Recycle Bin"
    k32 = ctypes.windll.kernel32
    if k32.GetDriveTypeW(ctypes.c_wchar_p(full[:3])) == 4:   # DRIVE_REMOTE
        return "a network drive has no Recycle Bin"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    # FO_DELETE with FOF_ALLOWUNDO (the bin) | NOCONFIRMATION | SILENT | NOERRORUI |
    # NOCONFIRMMKDIR, and WANTNUKEWARNING so an item too big for the bin is the USER's call.
    op = SHFILEOPSTRUCTW(None, 3, full + "\0", None, 0x4000 | 0x40 | 0x10 | 0x4 | 0x400 | 0x200,
                         False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if op.fAnyOperationsAborted:
        return "declined: left in place"
    if rc != 0:
        return f"shell error 0x{rc:X}"
    return "still present after a reported recycle" if Path(full).exists() else None


def dispose(old: Path, what: str = "the previous input snapshot") -> str:
    """Remove a replaced snapshot (or a leftover temp: *what*). Returns one line saying
    what happened to it."""
    why = proven_rebuildable(old)
    if not why:
        try:
            shutil.rmtree(old)
            return f"deleted {what} (receipt and every hash matched): {old.name}"
        except OSError as exc:
            return f"WARNING: could not delete {what} {old}: {exc}"
    err = _recycle(old)
    if err is None:
        return (f"moved {what} to the Recycle Bin (unproven: {why[0]}"
                f"{f'; and {len(why) - 1} more' if len(why) > 1 else ''})")
    return f"WARNING: left {what} in place at {old} ({why[0]}; recycle: {err})"


def install(snap: Snapshot, db_path: Path, progress=lambda s: None) -> Snapshot:
    """Move a finished snapshot to `<db>.inputs`, disposing of the one it replaces."""
    final = snapshot_path(db_path)
    old = None
    if final.exists():
        old = final.with_name(f"{final.name}.old.{os.getpid()}.tmp")
        if old.exists():
            progress(dispose(old))
        os.replace(final, old)
    try:
        os.replace(snap.path, final)
    except OSError:
        if old is not None and not final.exists():
            os.replace(old, final)
        raise
    if old is not None:
        progress(dispose(old))
    return Snapshot(final, snap.manifest, snap.manifest_sha)


def discard(snap: Snapshot | None) -> None:
    """Remove this run's own unfinished temp snapshot (same-run scratch)."""
    if snap is not None and snap.path.name.endswith(".tmp") and snap.path.exists():
        shutil.rmtree(snap.path, ignore_errors=True)

"""`x4modlist downloads`: what is in my download folders, against what is installed.

Read-only. It opens each archive, reads every manifest INSIDE it and compares by manifest
id, because neither a filename nor the registry can answer the question: filenames do not
carry the id, and the registry only knows the mods it has seen. Nothing is extracted to
disk and no archive member is ever executed.

Every archive ends in exactly one of three states (read, no manifest, unreadable), and the
totals are printed so a dropped archive would show. An unreadable archive is listed with
its reason: it is a NON-ANSWER about that file, never "not a mod". Exit 3 when anything was unreadable or a
manifest unusable, because the list above is then not the whole folder; an archive with no manifest was read
and has an answer, so it does not count (--all names those).

A verdict here compares manifests only. NEWER is not a recommendation: this tool does not
know about holds, local edits beyond the registry's flag, or what the new version does.
When an id is installed in more than one folder, each copy is compared and named, and copies
that disagree read UNDETERMINED: this tool does not know which copy the engine uses.

WARNINGS cover only mods you might still install (NEWER, NOT-INSTALLED, the same-folder bucket and
UNDETERMINED) and each states what it read. A check that could not run says so and is not a clean result:
- profile-disabled: the profile has an entry turning this id off, so installing it will not load it.
- same-name: an installed mod has this name under a different id.
- test-profile: a profile given with --test-profile has no entry for this id, so it would load there.
- icons: the merged icon total after this mod passes what one install was measured to load.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from lxml import etree

from . import _merge, _registry, _scan

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
MAX_MANIFEST_BYTES = 1024 * 1024  # a content.xml is a few KB; anything huge is not one
MAX_MANIFEST_DEPTH = 3  # content.xml, mod/content.xml, wrapper/mod/content.xml
SEVENZIP_TIMEOUT_S = 120

ICON_LIST = "libraries/icons.xml"
MAX_ICON_LIST_BYTES = 32 * 1024 * 1024
# MEASURED on ONE install (2026-11-02): with a HUD mod registering 911 icons the load died with
# `Out of Handles! for 'class XGfx::Material'`; with 848 it loaded. These are those two runs in the units this
# file counts, distinct merged names: 1,656 base + DLC + 75 from the other mods + the HUD's 848 or 911.
# The 75 was counted on 2026-11-03, when those mods held 107 raw entries against about 110 at the test, so each
# figure may be off by a few. How names map to handles, and whether the limit moves with other content or
# hardware, is not measured, so these are reference points and never a proven limit.
ICONS_LOADED = 2579
ICONS_FAILED = 2642
_REMOVED_ICON = re.compile(r"""@name\s*=\s*['"]([^'"]+)['"]""")
_PACKED = re.compile(r"^(?:ext|subst)_\d+\.cat$", re.IGNORECASE)

NEWER, SAME, OLDER, NOT_INSTALLED = "NEWER", "SAME", "OLDER", "NOT-INSTALLED"
SAME_FOLDER = "DIFFERENT-ID-SAME-FOLDER"
UNDETERMINED = "UNDETERMINED"
BUCKET_ORDER = (NEWER, NOT_INSTALLED, SAME_FOLDER, UNDETERMINED, OLDER, SAME)
#: A mod you might still install. SAME and OLDER are not, so warning about them would be noise.
WOULD_INSTALL = (NEWER, NOT_INSTALLED, SAME_FOLDER, UNDETERMINED)

# Nexus manual-download names, in the two shapes it has used:
#   "Some Mod 2089 1.32 2026-10-30T18-12Z kslDldtFQ.zip"
#   "Some Mod-2089-1-32-1777659026.7z"
_NAME_NEW = re.compile(
    r"^(?P<name>.+?) (?P<id>\d{1,6}) (?P<ver>\S+) (?P<date>\d{4}-\d{2}-\d{2})T\d{2}-\d{2}Z"
    r" \w+(?:\(\d+\))?\.\w+$")
_NAME_OLD = re.compile(
    r"^(?P<name>.+?)-(?P<id>\d{1,6})-(?:(?P<ver>[\w-]+)-)?(?P<epoch>\d{9,11})(?:\(\d+\))?\.\w+$")


class ArchiveUnreadable(Exception):
    """This archive could not be read. The message is the reason shown to the user."""


def parse_nexus_name(filename: str) -> dict | None:
    """(nexus id, version, upload date) from a Nexus download name, or None.

    A name is a hint for the printed page link, never an identity: the comparison uses
    the manifest. None means only that this name is not in a known shape.
    """
    m = _NAME_NEW.match(filename)
    if m:
        return {"nexus_id": int(m["id"]), "version": m["ver"], "uploaded": m["date"]}
    m = _NAME_OLD.match(filename)
    if m:
        day = _dt.datetime.fromtimestamp(int(m["epoch"]), _dt.timezone.utc).date().isoformat()
        return {"nexus_id": int(m["id"]), "version": m["ver"] or "", "uploaded": day}
    return None


def _is_manifest_member(member: str) -> bool:
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    return bool(parts) and parts[-1].lower() == "content.xml" and len(parts) <= MAX_MANIFEST_DEPTH


def _find_7z() -> str | None:
    found = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if found:
        return found
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)  # env-ok: a Windows system variable, not toolkit configuration
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(Path(base) / "7-Zip" / "7z.exe")
    return None


def _run_7z(exe: str, args: list[str]) -> bytes:
    """Run 7-Zip and return stdout, or raise with the reason. `--` keeps a name from being a switch."""
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=SEVENZIP_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ArchiveUnreadable(f"7-Zip timed out after {SEVENZIP_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise ArchiveUnreadable(f"7-Zip could not run: {exc.__class__.__name__}") from exc
    if r.returncode != 0:
        raise ArchiveUnreadable(f"7-Zip exit {r.returncode}: not a readable archive")
    return r.stdout


def _read_with_7z(path: Path, exe: str | None) -> list[tuple[str, bytes]]:
    if exe is None:
        raise ArchiveUnreadable("needs 7-Zip, which was not found")
    listing = _run_7z(exe, ["l", "-ba", "-slt", "--", str(path)]).decode("utf-8", "replace")
    out = []
    for line in listing.splitlines():
        if not line.startswith("Path = "):
            continue
        member = line[len("Path = "):].strip()
        if _is_manifest_member(member):
            data = _run_7z(exe, ["e", "-so", "-spd", "--", str(path), member])
            if len(data) > MAX_MANIFEST_BYTES:
                raise ArchiveUnreadable(f"{member} is larger than a manifest should be")
            out.append((member, data))
    return out


def _read_zip(path: Path) -> list[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(path) as z:
            out = []
            for info in z.infolist():
                if not _is_manifest_member(info.filename):
                    continue
                if info.file_size > MAX_MANIFEST_BYTES:
                    raise ArchiveUnreadable(f"{info.filename} is larger than a manifest should be")
                out.append((info.filename, z.read(info)))
            return out
    except zipfile.BadZipFile as exc:
        raise ArchiveUnreadable("not a readable zip") from exc
    except RuntimeError as exc:  # zipfile raises RuntimeError for a password-protected member
        raise ArchiveUnreadable("password-protected") from exc
    except ArchiveUnreadable:
        raise
    except Exception as exc:  # noqa: BLE001 - a malformed name or stream (UnicodeDecodeError, zlib.error, ...)
        # is a reason about THIS archive; it must not abort the scan of the others.
        raise ArchiveUnreadable(f"zip read failed: {exc.__class__.__name__}") from exc


def read_manifests(path: Path, sevenzip: str | None) -> list[tuple[str, bytes]]:
    """Every (member, bytes) that is a manifest in this archive. Raises ArchiveUnreadable."""
    try:
        if path.stat().st_size == 0:
            raise ArchiveUnreadable("0 bytes: the download did not complete")
    except OSError as exc:
        raise ArchiveUnreadable(f"cannot stat: {exc.__class__.__name__}") from exc
    if path.suffix.lower() == ".zip":
        return _read_zip(path)
    return _read_with_7z(path, sevenzip)


def _icon_prefix(member: str) -> tuple[str, str] | None:
    """(mod folder inside the archive, "icons" or "packed") for a member that decides the icon count."""
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    if not parts:
        return None
    if len(parts) >= 2 and "/".join(parts[-2:]).lower() == ICON_LIST and len(parts) <= MAX_MANIFEST_DEPTH + 1:
        return "/".join(parts[:-2]).lower(), "icons"
    if _PACKED.match(parts[-1]) and len(parts) <= MAX_MANIFEST_DEPTH:
        return "/".join(parts[:-1]).lower(), "packed"
    return None


def read_icon_lists(path: Path, sevenzip: str | None) -> tuple[dict[str, bytes], set[str]]:
    """({mod folder: its icon list bytes}, {mod folders with a catalog}). Raises ArchiveUnreadable.

    A catalog is not opened, so a mod with one and no loose list has an UNKNOWN count, never zero.
    """
    lists: dict[str, bytes] = {}
    packed: set[str] = set()
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    hit = _icon_prefix(info.filename)
                    if hit is None:
                        continue
                    if hit[1] == "packed":
                        packed.add(hit[0])
                    elif info.file_size > MAX_ICON_LIST_BYTES:
                        raise ArchiveUnreadable(f"{info.filename} is larger than an icon list should be")
                    else:
                        lists[hit[0]] = z.read(info)
        except ArchiveUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001 - a reason about this archive, as in _read_zip
            raise ArchiveUnreadable(f"zip read failed: {exc.__class__.__name__}") from exc
        return lists, packed
    if sevenzip is None:
        raise ArchiveUnreadable("needs 7-Zip, which was not found")
    listing = _run_7z(sevenzip, ["l", "-ba", "-slt", "--", str(path)]).decode("utf-8", "replace")
    for line in listing.splitlines():
        if not line.startswith("Path = "):
            continue
        member = line[len("Path = "):].strip()
        hit = _icon_prefix(member)
        if hit is None:
            continue
        if hit[1] == "packed":
            packed.add(hit[0])
        else:
            data = _run_7z(sevenzip, ["e", "-so", "-spd", "--", str(path), member])
            if len(data) > MAX_ICON_LIST_BYTES:
                raise ArchiveUnreadable(f"{member} is larger than an icon list should be")
            lists[hit[0]] = data
    return lists, packed


def icon_ops(data: bytes) -> tuple[set[str], set[str]] | None:
    """(names added, names removed) by one icons.xml, whole-file or diff. None if it is not one.

    Names are what was measured to count: 176 duplicate names sharing one texture did not help.
    A `<remove>` selecting anything other than a named icon removes nothing here, so a count errs high.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:  # silent-ok: the caller reports a None as an unread icon list, by mod
        return None
    if root.tag == "icons":
        return {e.get("name") for e in root.iter("icon") if e.get("name")}, set()
    if root.tag != "diff":
        return None
    added: set[str] = set()
    removed: set[str] = set()
    for op in root:
        if op.tag == "add":
            added.update(e.get("name") for e in op.iter("icon") if e.get("name"))
        elif op.tag == "remove":
            removed.update(_REMOVED_ICON.findall(op.get("sel") or ""))
    return added, removed


def _layer_ops(root: Path, unread: list[str], label: str) -> tuple[set[str], set[str]]:
    """Every icon operation one mod or DLC root makes, loose then packed."""
    pick = _scan.all_of(_scan.under("libraries/"), lambda v: v.lower() == ICON_LIST)
    added: set[str] = set()
    removed: set[str] = set()
    failed: list = []
    for _vpath, data in _scan.iter_mod_xml_bytes(root, pick, failed):
        ops = icon_ops(data)
        if ops is None:
            unread.append(label)
            continue
        added |= ops[0]
        removed |= ops[1]
    if failed:
        unread.append(label)
    return added, removed


def icon_world(active: list[dict], config=None) -> dict:
    """The merged icon names of the running set, kept per layer so a mod can be swapped for its archive.

    Returns {"base", "layers", "unread"} or {"note": why it could not be counted}. A count over a missing
    base would read as a large margin, so an empty base is a refusal and not a small number.
    """
    try:
        cfg = config or _merge.Config()
        base: set[str] = set()
        unread: list[str] = []
        for root in [cfg.reference, *cfg.dlc_dirs()]:
            base |= _layer_ops(Path(root), unread, Path(root).name)[0]
        if not base:
            return {"note": "icon budget not checked: no base-game icon list was found"}
        layers = [(m["id"].lower(), *_layer_ops(Path(m["path"]), unread, m["folder"]))
                  for m in active if m.get("id") and m.get("path")]
    except Exception as exc:  # noqa: BLE001 - disclosed in the output as a check that did not run
        return {"note": f"icon budget not checked ({exc.__class__.__name__}): {exc}"}
    return {"base": base, "layers": layers, "unread": unread}


def icon_total(world: dict, swap_id: str | None = None, ops: tuple[set[str], set[str]] | None = None) -> int:
    """Merged names in load order. With *ops*, that mod's layer is replaced, or appended if not active."""
    names = set(world["base"])
    swapped = False
    for mod_id, added, removed in world["layers"]:
        if swap_id is not None and mod_id == swap_id:
            added, removed = ops
            swapped = True
        names |= added
        names -= removed
    if ops is not None and not swapped:
        names |= ops[0]
        names -= ops[1]
    return len(names)


def profile_entries(path: Path | None = None) -> dict[str, bool]:
    """{lower id: enabled} for one profile content.xml. The caller decides what a failure means."""
    return {mod_id.lower(): on for mod_id, on in _registry.ingest_content_xml(path)}


def _test_profile_file(given: str) -> Path:
    p = Path(given)
    return p / "content.xml" if p.is_dir() else p


def _warnings_for(row: dict, manifest: dict, by_name: dict, profile: dict | None,
                  test_profiles: dict[str, dict]) -> list[dict]:
    if row["bucket"] not in WOULD_INSTALL:
        return []
    key = row["id"].lower()
    out = []
    if profile is not None and profile.get(key) is False:
        out.append({"kind": "profile-disabled", "text": "the profile has an entry turning this id off, "
                    "so it will not load after you install it"})
    twins = [m for m in by_name.get(row["name"].strip().lower(), []) if (m.get("id") or "").lower() != key]
    if row["name"].strip() and twins:
        named = ", ".join(f"{m['folder']} [{m.get('id')}]" for m in twins)
        out.append({"kind": "same-name", "text": f"already installed under a different id: {named}"})
    if manifest.get("enabled", True):
        for label, entries in test_profiles.items():
            if key not in entries:
                out.append({"kind": "test-profile", "text": f"profile {label} has no entry for this id, "
                            "so it would load there"})
    return out


def _icon_warning(after: int) -> dict | None:
    if after >= ICONS_FAILED:
        return {"kind": "icons", "text": f"{after} merged icon names after this mod: one install measured "
                f"{ICONS_FAILED} failing to load (Out of Handles) and {ICONS_LOADED} loading"}
    if after > ICONS_LOADED:
        return {"kind": "icons", "text": f"{after} merged icon names after this mod: past the {ICONS_LOADED} one "
                f"install measured loading, short of the {ICONS_FAILED} it measured failing; between is unmeasured"}
    return None


def parse_manifest(data: bytes) -> dict | None:
    """id/name/version/date, and whether its own default is enabled, or None if it is not usable."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:  # silent-ok: scan() records a None as an unusable manifest, by file
        return None
    if not isinstance(root.tag, str) or root.tag != "content" or not root.get("id"):
        return None
    manifest = {k: root.get(k) or "" for k in ("id", "name", "version", "date")}
    manifest["enabled"] = _registry.profile_flag(root.get("enabled"))  # the same convention as a profile entry
    return manifest


def _version_int(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def compare(candidate: dict, installed: dict) -> str:
    """NEWER / SAME / OLDER by manifest version, then date. UNDETERMINED if neither can decide."""
    cv, iv = _version_int(candidate.get("version")), _version_int(installed.get("version"))
    cd, id_ = candidate.get("date") or "", installed.get("date") or ""
    if cv is not None and iv is not None and cv != iv:
        return NEWER if cv > iv else OLDER
    if not cd or not id_:
        return SAME if cv is not None and cv == iv else UNDETERMINED
    if cd != id_:
        return NEWER if cd > id_ else OLDER
    return SAME if cv is not None and cv == iv else UNDETERMINED


def _top_folder(member: str) -> str | None:
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    return parts[-2].lower() if len(parts) >= 2 else None


def classify(member: str, manifest: dict, by_id: dict, by_folder: dict) -> tuple[str, dict | None, list[dict]]:
    """The bucket for one manifest, the installed mod it was compared with, and, when that id is installed
    more than once, every copy with its own verdict. Copies that disagree are UNDETERMINED: comparing with
    whichever came last would state a verdict about one folder as if it were about the mod."""
    copies = by_id.get(manifest["id"].lower())
    if copies:
        verdicts = [{"folder": c["folder"], "version": c.get("version"), "date": c.get("date"),
                     "bucket": compare(manifest, c)} for c in copies]
        buckets = {v["bucket"] for v in verdicts}
        return (buckets.pop() if len(buckets) == 1 else UNDETERMINED), copies[0], verdicts if len(copies) > 1 else []
    inst = by_folder.get(_top_folder(member) or "")
    if inst is not None:
        return SAME_FOLDER, inst, []
    return NOT_INSTALLED, None, []


def _sort_key(row: dict) -> tuple:
    return (_version_int(row["version"]) or -1, row["date"], row.get("uploaded") or "")


def _registry_flags(registry_path) -> tuple[dict[str, bool], str | None]:
    """{mod id: custom_edited}, read-only. A registry that cannot be read costs the flag, not the run."""
    try:
        reg = _registry.load_registry(Path(registry_path) if registry_path else None)
    except Exception as exc:  # noqa: BLE001 - disclosed in the output as flags unavailable
        return {}, f"registry not read ({exc.__class__.__name__}): the [custom] flag is unavailable"
    flags = {}
    for entry in reg.get("mods") or []:
        if (entry.get("human") or {}).get("custom_edited"):
            flags[str(entry.get("id")).lower()] = True
    return flags, None


def scan(dirs: list[Path], installed: list[dict], active_ids: set[str], custom: dict,
         recursive: bool, sevenzip: str | None, profile: dict | None = None,
         test_profiles: dict[str, dict] | None = None, world: dict | None = None) -> dict:
    """The whole report as data. Reads only. *profile* None means the profile was not read."""
    test_profiles = test_profiles or {}
    counting = world is not None and "base" in world
    by_name: dict[str, list[dict]] = {}
    for m in installed:
        if (m.get("name") or "").strip():
            by_name.setdefault(m["name"].strip().lower(), []).append(m)
    by_id: dict[str, list[dict]] = {}
    for m in sorted(installed, key=lambda r: str(r.get("folder")).lower()):
        if m.get("id"):
            by_id.setdefault(m["id"].lower(), []).append(m)
    by_folder = {m["folder"].lower(): m for m in installed}
    archives, other_files = [], 0
    for d in dirs:
        walker = d.rglob("*") if recursive else d.iterdir()
        for p in sorted(walker):
            if not p.is_file():
                continue
            if p.suffix.lower() in ARCHIVE_SUFFIXES:
                archives.append(p)
            else:
                other_files += 1
    rows, unreadable, no_manifest, bad = [], [], [], []
    for path in archives:
        try:
            members = read_manifests(path, sevenzip)
        except ArchiveUnreadable as exc:
            unreadable.append({"file": path.name, "reason": str(exc)})
            continue
        if not members:
            no_manifest.append(path.name)
            continue
        hint = parse_nexus_name(path.name) or {}
        lists: dict[str, bytes] = {}
        packed: set[str] = set()
        list_error = None
        if counting:
            try:
                lists, packed = read_icon_lists(path, sevenzip)
            except ArchiveUnreadable as exc:
                list_error = str(exc)
        for member, data in members:
            manifest = parse_manifest(data)
            if manifest is None:
                bad.append({"file": path.name, "member": member})
                continue
            bucket, inst, copies = classify(member, manifest, by_id, by_folder)
            flags = []
            if copies:
                flags.append(f"installed {len(copies)} times")
            if inst is not None:
                key = (inst.get("id") or "").lower()
                if custom.get(key):
                    flags.append("custom")
                if key not in active_ids:
                    flags.append("inactive")
                if bucket != SAME_FOLDER and manifest["name"] and inst.get("name") and manifest["name"] != inst["name"]:
                    flags.append("renamed")
            rows.append({
                "bucket": bucket, "file": path.name, "member": member, "id": manifest["id"],
                "name": manifest["name"], "version": manifest["version"], "date": manifest["date"],
                "nexus_id": hint.get("nexus_id"), "uploaded": hint.get("uploaded"),
                "installed": None if inst is None else {
                    "folder": inst["folder"], "id": inst["id"], "name": inst.get("name"),
                    "version": inst.get("version"), "date": inst.get("date")},
                "installed_copies": copies,
                "flags": flags})
            row = rows[-1]
            row["warnings"] = _warnings_for(row, manifest, by_name, profile, test_profiles)
            row["icons"] = None
            if counting and bucket in WOULD_INSTALL:
                parts = [p for p in member.replace("\\", "/").split("/") if p]
                folder = "/".join(parts[:-1]).lower()
                ops = icon_ops(lists[folder]) if folder in lists else None
                if ops is not None or (folder not in lists and folder not in packed and list_error is None):
                    ops = ops or (set(), set())
                    after = icon_total(world, manifest["id"].lower(), ops)
                    row["icons"] = {"names": len(ops[0]), "total_after": after}
                    warning = _icon_warning(after) if ops[0] else None  # no icons: not this mod's doing
                    if warning:
                        row["warnings"].append(warning)
                else:
                    why = list_error or ("packed in a catalog" if folder in packed else "icon list will not parse")
                    row["warnings"].append({"kind": "icons", "text": f"icon count unknown: {why}"})
    newest: dict[str, dict] = {}
    for row in rows:
        key = row["id"].lower()
        if key not in newest or _sort_key(row) > _sort_key(newest[key]):
            newest[key] = row
    superseded = [r for r in rows if newest[r["id"].lower()] is not r]
    icons_now = icon_total(world) if counting else None
    return {"scanned": [str(d) for d in dirs], "recursive": recursive, "archives": len(archives),
            "other_files": other_files, "no_manifest": len(no_manifest), "no_manifest_files": no_manifest,
            "unreadable": unreadable,
            "bad_manifests": bad, "rows": sorted(newest.values(), key=lambda r: r["file"].lower()),
            "superseded": superseded, "sevenzip": sevenzip, "icons_now": icons_now,
            "icons_unread": world.get("unread", []) if counting else [],
            "test_profiles": sorted(test_profiles), "check_notes": []}


def _describe(row: dict) -> str:
    link = f"#{row['nexus_id']} " if row.get("nexus_id") else ""
    here = ""
    inst = row["installed"]
    if inst:
        here = f" | installed {inst['folder']} [{inst['id']}] v{inst.get('version')} {inst.get('date')}"
    if row["installed_copies"]:
        # Each copy with ITS verdict: the one `here` names is not the answer when they disagree.
        here = " | " + "; ".join(f"installed {c['folder']} v{c['version']} {c['date']}: {c['bucket']}"
                                 for c in row["installed_copies"])
    flags = "".join(f" [{f}]" for f in row["flags"])
    return f"{link}{row['name']} [{row['id']}] v{row['version']} {row['date']}{here}{flags}"


def _print_report(report: dict, show_all: bool, flag_note: str | None) -> None:
    rows = report["rows"]
    print("Manifests compared, nothing else: NEWER is not a recommendation, and holds or "
          "reasons for a difference are not known here.")
    warned = [r for r in rows if r["warnings"]]
    if warned:
        print(f"\nWARNINGS: {sum(len(r['warnings']) for r in warned)} on {len(warned)} mods")
        for row in warned:
            for w in row["warnings"]:
                print(f"  {row['name']} [{row['id']}] {w['kind']}: {w['text']}")
    for bucket in BUCKET_ORDER:
        group = [r for r in rows if r["bucket"] == bucket]
        if bucket == SAME and not show_all:
            print(f"\n{bucket}: {len(group)} (--all lists them)")
            continue
        print(f"\n{bucket}: {len(group)}")
        for row in group:
            print(f"  {_describe(row)}")
    if report["bad_manifests"]:
        print(f"\nUNUSABLE MANIFEST: {len(report['bad_manifests'])} (the rest of that archive may still have counted)")
        for b in report["bad_manifests"]:
            print(f"  {b['file']}: {b['member']}")
    print(f"\nUNREADABLE: {len(report['unreadable'])} (a non-answer about each file, not a verdict)")
    for u in report["unreadable"]:
        print(f"  {u['file']}: {u['reason']}")
    if show_all and report["no_manifest_files"]:
        print(f"\nNO MANIFEST: {report['no_manifest']} (read, and no content.xml within "
              f"{MAX_MANIFEST_DEPTH} folder levels: not a mod in a shape this tool looks for)")
        for name in report["no_manifest_files"]:
            print(f"  {name}")
    if show_all and report["superseded"]:
        print(f"\nOLDER COPIES of a mod listed above: {len(report['superseded'])}")
        for row in report["superseded"]:
            print(f"  {row['file']}: v{row['version']} {row['date']}")
    read = report["archives"] - report["no_manifest"] - len(report["unreadable"])
    print(f"\nScanned {', '.join(report['scanned'])}"
          f"{' recursively' if report['recursive'] else ' (top level only)'}: "
          f"{report['archives']} archives = {read} with a manifest + {report['no_manifest']} without "
          f"+ {len(report['unreadable'])} unreadable. {report['other_files']} other files not opened. "
          f"{len(rows)} mods, {len(report['superseded'])} older copies not listed.")
    if report["sevenzip"] is None:
        print("7-Zip was not found: .7z and .rar archives could not be read.")
    if flag_note:
        print(flag_note)
    for note in report["check_notes"]:
        print(note)
    if report["icons_now"] is not None:
        print(f"Merged icon names now: {report['icons_now']}. One install measured {ICONS_LOADED} loading and "
              f"{ICONS_FAILED} failing; that is a reference point, not a proven limit for this machine.")
    if report["icons_unread"]:
        print(f"Icon lists not read, so the count is short by theirs: {', '.join(report['icons_unread'])}")


def cmd_downloads(args) -> int:
    dirs = [Path(d) for d in args.dirs]
    missing = [str(d) for d in dirs if not d.is_dir()]
    if missing:
        print(f"error: not a folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    installed = list(_registry.mods("installed"))
    active = _registry.mods("active")
    active_ids = {m["id"].lower() for m in active if m.get("id")}
    left_out = _registry.dropped_note(active)
    if left_out:
        print(f"NOT counted as active (the engine does not load it): {left_out}", file=sys.stderr)
    if not installed:
        print("error: no installed mods were found, so nothing can be compared. "
              "Run `x4validate --paths` to see where the tool is looking.", file=sys.stderr)
        return 2
    custom, flag_note = _registry_flags(getattr(args, "registry", None))
    test_profiles: dict[str, dict] = {}
    for given in args.test_profile or []:
        file = _test_profile_file(given)
        try:
            test_profiles[file.parent.name or str(file)] = profile_entries(file)
        except (OSError, etree.XMLSyntaxError) as exc:
            # Asked for by name, so a skip would read as "no leak" about a profile never opened.
            print(f"error: --test-profile {given}: not readable ({exc.__class__.__name__})", file=sys.stderr)
            return 2
    notes = []
    try:
        profile = profile_entries()
    except Exception as exc:  # noqa: BLE001 - disclosed below as a check that did not run
        profile = None
        notes.append(f"profile-disabled not checked: the profile was not read ({exc.__class__.__name__})")
    world = icon_world(active)
    if "note" in world:
        notes.append(world["note"])
    report = scan(dirs, installed, active_ids, custom, args.recursive, _find_7z(),
                  profile=profile, test_profiles=test_profiles, world=world)
    report["check_notes"] = notes
    if args.json:
        report["custom_flag_note"] = flag_note
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        _print_report(report, args.all, flag_note)
    if report["archives"] == 0:
        print("Nothing was examined: no .zip, .7z or .rar in those folders.", file=sys.stderr)
        return 3
    if not report["rows"] and report["unreadable"]:
        print("Nothing was read: every archive with a possible manifest was unreadable.", file=sys.stderr)
        return 3
    if report["unreadable"] or report["bad_manifests"]:
        print(f"PARTIAL: {len(report['unreadable'])} unreadable archive(s) and {len(report['bad_manifests'])} "
              "unusable manifest(s): the comparison above does not cover the whole folder.", file=sys.stderr)
        return 3
    return 0


def register(sub) -> None:
    """Wire `downloads` into `x4modlist`'s parser."""
    p = sub.add_parser("downloads", help="read the manifests inside downloaded archives and "
                       "compare them with what is installed: newer, same, older, not installed. Read-only. "
                       "Exit 3 when anything was unreadable or a manifest unusable")
    p.add_argument("dirs", nargs="+", metavar="DIR", help="a folder of downloaded mod archives")
    p.add_argument("--recursive", action="store_true", help="also scan subfolders (default: top level only)")
    p.add_argument("--all", action="store_true", help="also list mods that match what is installed, older copies, "
                   "and archives with no manifest")
    p.add_argument("--test-profile", action="append", metavar="PATH",
                   help="a profile folder or content.xml to check for mods that would load there because it has "
                   "no entry for them; repeat for several. An unreadable one refuses")
    p.add_argument("--json", action="store_true", help="print the whole report as JSON")
    p.set_defaults(func=cmd_downloads)

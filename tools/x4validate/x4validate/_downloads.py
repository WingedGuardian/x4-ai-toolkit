"""`x4modlist downloads`: what is in my download folders, against what is installed.

Read-only. It opens each archive, reads every manifest INSIDE it and compares by manifest
id, because neither a filename nor the registry can answer the question: filenames do not
carry the id, and the registry only knows the mods it has seen. Nothing is extracted to
disk and no archive member is ever executed.

Every archive ends in exactly one of three states (read, no manifest, unreadable), and the
totals are printed so a dropped archive would show. An unreadable archive is listed with
its reason: it is a NON-ANSWER about that file, never "not a mod".

A verdict here compares manifests only. NEWER is not a recommendation: this tool does not
know about holds, local edits beyond the registry's flag, or what the new version does.
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

from . import _registry

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
MAX_MANIFEST_BYTES = 1024 * 1024  # a content.xml is a few KB; anything huge is not one
MAX_MANIFEST_DEPTH = 3  # content.xml, mod/content.xml, wrapper/mod/content.xml
SEVENZIP_TIMEOUT_S = 120

NEWER, SAME, OLDER, NOT_INSTALLED = "NEWER", "SAME", "OLDER", "NOT-INSTALLED"
SAME_FOLDER = "DIFFERENT-ID-SAME-FOLDER"
UNDETERMINED = "UNDETERMINED"
BUCKET_ORDER = (NEWER, NOT_INSTALLED, SAME_FOLDER, UNDETERMINED, OLDER, SAME)

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
    except (OSError, EOFError, NotImplementedError) as exc:
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


def parse_manifest(data: bytes) -> dict | None:
    """id/name/version/date from a manifest, or None if it is not a usable one."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:  # silent-ok: scan() records a None as an unusable manifest, by file
        return None
    if not isinstance(root.tag, str) or root.tag != "content" or not root.get("id"):
        return None
    return {k: root.get(k) or "" for k in ("id", "name", "version", "date")}


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


def classify(member: str, manifest: dict, by_id: dict, by_folder: dict) -> tuple[str, dict | None]:
    """The bucket for one manifest, and the installed mod it was compared with."""
    inst = by_id.get(manifest["id"].lower())
    if inst is not None:
        return compare(manifest, inst), inst
    inst = by_folder.get(_top_folder(member) or "")
    if inst is not None:
        return SAME_FOLDER, inst
    return NOT_INSTALLED, None


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
         recursive: bool, sevenzip: str | None) -> dict:
    """The whole report as data. Reads only."""
    by_id = {m["id"].lower(): m for m in installed}
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
    rows, unreadable, no_manifest, bad = [], [], 0, []
    for path in archives:
        try:
            members = read_manifests(path, sevenzip)
        except ArchiveUnreadable as exc:
            unreadable.append({"file": path.name, "reason": str(exc)})
            continue
        if not members:
            no_manifest += 1
            continue
        hint = parse_nexus_name(path.name) or {}
        for member, data in members:
            manifest = parse_manifest(data)
            if manifest is None:
                bad.append({"file": path.name, "member": member})
                continue
            bucket, inst = classify(member, manifest, by_id, by_folder)
            flags = []
            if inst is not None:
                key = inst["id"].lower()
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
                "flags": flags})
    newest: dict[str, dict] = {}
    for row in rows:
        key = row["id"].lower()
        if key not in newest or _sort_key(row) > _sort_key(newest[key]):
            newest[key] = row
    superseded = [r for r in rows if newest[r["id"].lower()] is not r]
    return {"scanned": [str(d) for d in dirs], "recursive": recursive, "archives": len(archives),
            "other_files": other_files, "no_manifest": no_manifest, "unreadable": unreadable,
            "bad_manifests": bad, "rows": sorted(newest.values(), key=lambda r: r["file"].lower()),
            "superseded": superseded, "sevenzip": sevenzip}


def _describe(row: dict) -> str:
    link = f"#{row['nexus_id']} " if row.get("nexus_id") else ""
    here = ""
    inst = row["installed"]
    if inst:
        here = f" | installed {inst['folder']} [{inst['id']}] v{inst.get('version')} {inst.get('date')}"
    flags = "".join(f" [{f}]" for f in row["flags"])
    return f"{link}{row['name']} [{row['id']}] v{row['version']} {row['date']}{here}{flags}"


def _print_report(report: dict, show_all: bool, flag_note: str | None) -> None:
    rows = report["rows"]
    print("Manifests compared, nothing else: NEWER is not a recommendation, and holds or "
          "reasons for a difference are not known here.")
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


def cmd_downloads(args) -> int:
    dirs = [Path(d) for d in args.dirs]
    missing = [str(d) for d in dirs if not d.is_dir()]
    if missing:
        print(f"error: not a folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    installed = list(_registry.mods("installed"))
    active_ids = {m["id"].lower() for m in _registry.mods("active")}
    if not installed:
        print("error: no installed mods were found, so nothing can be compared. "
              "Run `x4validate --paths` to see where the tool is looking.", file=sys.stderr)
        return 2
    custom, flag_note = _registry_flags(getattr(args, "registry", None))
    report = scan(dirs, installed, active_ids, custom, args.recursive, _find_7z())
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
    return 0


def register(sub) -> None:
    """Wire `downloads` into `x4modlist`'s parser."""
    p = sub.add_parser("downloads", help="read the manifests inside downloaded archives and "
                       "compare them with what is installed: newer, same, older, not installed. Read-only")
    p.add_argument("dirs", nargs="+", metavar="DIR", help="a folder of downloaded mod archives")
    p.add_argument("--recursive", action="store_true", help="also scan subfolders (default: top level only)")
    p.add_argument("--all", action="store_true", help="also list mods that match what is installed, and older copies")
    p.add_argument("--json", action="store_true", help="print the whole report as JSON")
    p.set_defaults(func=cmd_downloads)

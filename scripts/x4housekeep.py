#!/usr/bin/env python3
"""x4housekeep -- disk retention for agent output: report, plan, then apply exactly the plan.

Ported from the Skyrim toolkit's tools/housekeep.py: the same logic, with the X4 save rule and X4 paths. The
shared policy text is pinned in both repos by test_retention_policy.py; a logic fix in one belongs in both.

Agent work piles up bytes that carry no information: copies of game files made as test fixtures, build
intermediates, the same log copied into ten evidence folders, launch logs nobody prunes, snapshot folders
that repeat the one before. The policy this tool implements is RETENTION.md; read that first.

    python scripts/x4housekeep.py [report]            what is where, by class (changes nothing)
    python scripts/x4housekeep.py plan -o plan.json   every action, with the hash it was planned on
            [--only CLASS[,CLASS]]                   (skips the scans no listed class needs)
    python scripts/x4housekeep.py apply plan.json     re-check each action, then do exactly those
            [--only CLASS[,CLASS]]                   (recreatable, build, link, gzip, launch, snapshot, scratch)
    python scripts/x4housekeep.py sweep-temp          recycle the toolkit's own stale temp dirs (see "temp sweep")
    python scripts/x4housekeep.py session STATE.txt   sweep-temp, then write the session-start disk note to STATE.txt
    --config FILE   default <toolkit>/x4-housekeep.json (x4-housekeep.json.example ships)

Two passes, never one: `plan` decides, `apply` re-verifies. An action whose file changed since it was planned
(size, hash, a newer write in its task folder) is REFUSED, not re-decided -- run `plan` again.

What it never touches, whatever the config says: saves (save/*.xml.gz; anything under Documents/Egosoft/X4)
and anything under a folder whose name
contains "backup"; git-tracked files; every file a project doc links to; and any task folder written to within
`active_hours` (another session is probably still working there).

Removal goes to the Recycle Bin, never a permanent delete, with no exceptions (Windows: the shell's own delete with
FOF_ALLOWUNDO). An item too big for the bin gets Windows' own "delete permanently?" prompt; "No" leaves it in place.
The bin frees no disk space until it is emptied; that is the user's call. A file the bin refuses is reported
FAILED and left in place. Duplicates are not removed at all: they become NTFS hard links to one copy, marked
read-only so an in-place edit cannot silently change every path at once.

Accounting: `report` and `plan` print every scanned byte in exactly one row, the rows sum to the total, and the
row of bytes it leaves alone prints even at zero.

Exit: 0 ok · 1 an action was refused or failed · 2 bad usage or config.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4temp import owns_temp

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOLKIT_ROOT / "x4-housekeep.json"


def is_save(low: str) -> bool:
    """An X4 save, or anything in the user profile (Documents/Egosoft/X4/<id>/ holds saves, content.xml and UI
    data). `low` is the lower-cased path with forward slashes."""
    return "/egosoft/x4/" in low or ("/save/" in low and low.endswith((".xml.gz", ".xml")))


CLASSES = ("recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch")
DEFAULTS = {
    "agent_output": [],               # task-folder roots (relative to the toolkit root, or absolute)
    "nested_task_roots": [],          # roots one level down whose children are task folders of their own
    "live_roots": [],                 # a byte-identical copy of a file here is "recreatable" (the game folder)
    "state_exts": [".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".json", ".jsonl", ".ini", ".toml",
                   ".yaml", ".yml", ".xml", ".cfg", ".log", ".txt"],   # live files that change: never a reference
    "launch_logs": [],                # folders of launch-YYYYMMDD-HHMMSS/ log captures
    "legacy_snapshots": [],           # folders of YYYYMMDD_HHMMSS/ snapshot copies (the 3.x snapshot hook)
    "scratch": [],                    # whole folders to recycle, named one by one
    "evidence_docs": ["CLAUDE.md", "AGENTS.md", "KNOWLEDGEBASE.md", "KNOWLEDGEBASE.local.md"],
    "protected_globs": [],
    "build_dir_names": ["build", "build-sdk", "build-vcpkg", "native-bootstrap", "native-prebuilt",
                        "CMakeFiles", "vcpkg_installed", "buildtrees"],
    "build_exts": [".obj", ".pch", ".lib", ".ilk", ".iobj", ".ipdb", ".tlog", ".idb"],
    "active_hours": 48,
    "min_bytes": 1048576,             # recreatable / link candidates below this are not worth a hash
    "gzip_after_days": 7,
    "recycle_after_days": 90,
    "free_floor_gib": 0,              # report warns within 20 GiB of it; 0 = no floor
    # The toolkit's OWN temp dirs: immediate children of the system temp dir whose name starts with one of these.
    # tools/x4validate/tests/test_temp_hygiene.py fails when a prefix the code creates is missing here, and
    # test_x4housekeep.py when one here is generic (no "x4" in it) or has no creator in the repo. Never a folder
    # every project shares, such as pytest's pytest-of-<user> (RETENTION.md, "Toolkit temp").
    "temp_prefixes": ["x4qa_", "x4gate_", "x4reg_gate_", "x4reader_", "x4diff_truth_", "x4stress_",
                      "x4-tool-audit-pytest-", "x4-pytest-batch-", "x4-mutation-result-", "x4-fake-install-",
                      "x4hk5_", "x4hk6_", "x4psfe_", "x4split_", "x4lanei_", "x4-vht-case-",
                      "x4-acl-privilege-probe-", "x4f11kb_", "x4-pytest-session-"],
    "temp_after_hours": 24,           # nothing in the dir or its direct children newer than this
    "sweep_every_hours": 6,           # session start launches a sweep at most this often
    "codex_sessions_warn_gib": 5,     # session note when ~/.codex/sessions passes this
    "worktrees_warn": 10,             # session note when the toolkit repo has more worktrees than this
}
LAUNCH_RE = re.compile(r"^launch-(\d{8})-(\d{6})$")
STAMP_RE = re.compile(r"^(\d{8})_(\d{6})$")
GIB = 1024 ** 3


# --------------------------------------------------------------------------- config and paths

def load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(path) if path else DEFAULT_CONFIG
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        unknown = sorted(set(data) - set(DEFAULTS) - {"root", "_comment"})
        if unknown:
            raise ValueError("unknown config key(s) in {}: {}".format(p, ", ".join(unknown)))
        cfg.update(data)
    elif path:
        raise ValueError("config not found: {}".format(p))
    cfg["root"] = Path(cfg.get("root") or TOOLKIT_ROOT)
    return cfg


def _abs(cfg, p) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg["root"] / q


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def blake(path, chunk=1 << 20) -> str:
    d = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            d.update(b)
    return d.hexdigest()


def newest_mtime(folder: Path) -> float:
    newest = folder.stat().st_mtime
    for path in walk_files(folder, include_dirs=True):
        newest = max(newest, path.stat().st_mtime)
    return newest


def fingerprint(folder: Path):
    """(file count, total bytes, newest mtime) -- the cheap 'has this folder changed' check for whole-folder
    actions. Hashing 245k snapshot files twice would cost minutes for no extra safety on a folder nothing writes."""
    n = size = 0
    newest = 0.0
    for path in walk_files(folder):
        st = path.stat()
        n += 1
        size += st.st_size
        newest = max(newest, st.st_mtime)
    return [n, size, round(newest, 3)]


def tracked_files(root: Path) -> set:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("could not inspect tracked-file protection") from exc
    if out.returncode != 0:
        if b"not a git repository" in out.stderr:
            return set()  # silent-ok: an archive or synthetic non-Git root has no tracked files
        raise OSError("git tracked-file protection failed (exit {})".format(out.returncode))
    return {_key(root / p) for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def evidence_links(cfg) -> set:
    """Every agent-output path a project doc links to (written relative to the root, as docs link them). A linked
    file is never planned for anything."""
    links = set()
    reference_chars = 0
    prefixes = []
    for r in cfg["agent_output"]:
        try:
            rel = os.path.relpath(os.path.abspath(str(_abs(cfg, r))), os.path.abspath(str(cfg["root"])))
        except ValueError:
            continue  # silent-ok: cross-drive output has no relative evidence-link spelling
        if rel.split(os.sep)[0] != "..":
            prefixes.append(re.escape(rel.replace("\\", "/")))
    if not prefixes:
        return links
    start_re = re.compile("(?:{})/".format("|".join(prefixes)), re.I if os.name == "nt" else 0)
    for doc in cfg["evidence_docs"]:
        p = _abs(cfg, doc)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        # This is conservative reference discovery, not a Markdown parser. Keep
        # every plausible line-bounded destination, including spaces and balanced
        # punctuation. Stopping at the FIRST ')' or space silently lost evidence.
        # Both literal and URI/Markdown-escaped spellings can name real files.
        views = {text, unquote(text)}
        views |= {re.sub(r"\\([!\"#$%&'()*+,./:;<=>?@\[\]^_`{|}~\\-])", r"\1", s) for s in tuple(views)}
        for view in views:
            for line in view.splitlines():
                for start in start_re.finditer(line):
                    tail = line[start.start():]
                    if len(tail) > 32768:
                        raise OSError("evidence reference line exceeds the safe discovery bound; cleanup refused")
                    ends = [i for i, ch in enumerate(tail) if ch.isspace() or ch in ")]\"'`<>|.,;:*"]
                    for end in ends + [len(tail)]:
                        candidate = tail[:end]
                        if candidate:
                            reference_chars += len(candidate)
                            if reference_chars > 8 * 1024 * 1024:
                                raise OSError("evidence references exceed the safe discovery byte bound; cleanup refused")
                            links.add(_key(cfg["root"] / candidate))
                        if len(links) > 50000:
                            raise OSError("evidence references exceed the safe discovery bound; cleanup refused")
    return links


class Guard:
    """The Forever class and the per-run exclusions, in one place: plan and apply both ask it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracked = tracked_files(cfg["root"])
        self.links = evidence_links(cfg)
        self.globs = [g.lower() for g in cfg["protected_globs"]]

    def why_protected(self, path) -> str:
        p = str(path)
        low = p.replace("\\", "/").lower()
        if is_save(low):
            return "save"
        # Folder names BELOW the configured root only: a root that itself sits under ".../backups/..." must not
        # make every file look protected (or, read the other way, make the rule meaningless).
        try:
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(str(self.cfg["root"])))
        except ValueError:                       # another drive: no relative form, judge the whole path
            rel = p
        rel_parts = rel.replace("\\", "/").lower().split("/")
        if any(part.startswith("known-good-") for part in rel_parts):
            return "known-good baseline"
        if rel_parts[-1] in {"audit_log.txt", "guards-off.log"}:
            return "audit record"
        if not rel.startswith(".."):
            if any("backup" in part for part in rel_parts[:-1]):
                return "backup folder"
        elif any("backup" in part for part in low.split("/")[:-1]):
            return "backup folder"
        k = _key(p)
        if k in self.tracked:
            return "git-tracked"
        if k in self.links:
            return "linked evidence"
        if any(fnmatch.fnmatch(low, g) for g in self.globs):
            return "protected_globs"
        return ""


def protected_inside(guard: "Guard", target: Path) -> str:
    """The first Forever reason for a whole-folder (or single-file) action: the target itself, or any file under it.
    A launch, snapshot or scratch folder holding a save, a backup folder or a tracked file is never recycled whole."""
    target = Path(target)
    why = guard.why_protected(target / "_" if target.is_dir() else target)   # the folder's OWN name counts too
    if why:
        return why
    for f in walk_files(target) if target.is_dir() else ():
        why = guard.why_protected(f)
        if why:
            return why
    return ""


# --------------------------------------------------------------------------- task folders

def task_folders(cfg):
    """[(path, state)] under every agent_output root; state is "idle", "active" or "loose".

    A folder directly in a root is a task folder, judged active as a whole. A folder named in nested_task_roots
    is a root of its own (recursively). A LOOSE file -- one sitting directly in a root, not inside a task folder --
    is never a candidate: that is where shared live state lives (a service's events.jsonl, a status JSON a session
    rewrites). Making an append-only log read-only would break its next write, and a log appended once a week
    walks straight through the activity gate."""
    out = []
    cutoff = time.time() - cfg["active_hours"] * 3600
    nested = {_key(_abs(cfg, n)) for n in cfg["nested_task_roots"]}

    def visit(folder: Path):
        for e in sorted(os.scandir(folder), key=lambda e: e.name):
            p = Path(e.path)
            if e.is_dir(follow_symlinks=False) and _key(p) in nested:
                visit(p)
            elif e.is_dir(follow_symlinks=False):
                out.append((p, "active" if newest_mtime(p) > cutoff else "idle"))
            else:
                out.append((p, "loose"))

    for r in cfg["agent_output"]:
        root = _abs(cfg, r)
        if root.is_dir():
            visit(root)
    return out


def walk_files(target: Path, include_dirs=False):
    def failed(error):
        raise error
    def linked(path):
        info = path.lstat()
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if linked(target):
        raise OSError("symbolic link or reparse point is not a cleanup target: {}".format(target))
    if target.is_file():
        yield target
        return
    if include_dirs:
        yield target
    for dp, dirs, fn in os.walk(target, onerror=failed):
        for name in dirs + fn:
            if linked(Path(dp) / name):
                raise OSError("symbolic link or reparse point inside cleanup target: {}".format(Path(dp) / name))
        if include_dirs:
            for name in dirs:
                yield Path(dp) / name
        for f in fn:
            yield Path(dp) / f


# --------------------------------------------------------------------------- planning

def live_index(cfg):
    """{size: [paths]} of every CONTENT file under the live roots at or above min_bytes -- stat only, hashed lazily.

    A state file (a database, a config, a log) is never a reference copy: it equals its snapshot only until its
    owner next writes, and from then on the snapshot is the only record of that state."""
    idx = {}
    state = {e.lower() for e in cfg["state_exts"]}
    for r in cfg["live_roots"]:
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        for dp, _, fn in os.walk(root):
            for f in fn:
                if os.path.splitext(f)[1].lower() in state:
                    continue
                p = os.path.join(dp, f)
                try:
                    s = os.path.getsize(p)
                except OSError as exc:
                    raise OSError("live reference could not be inspected: {}".format(p)) from exc
                if s >= cfg["min_bytes"]:
                    idx.setdefault(s, []).append(p)
    return idx


def is_build(path: Path, cfg) -> bool:
    names = {n.lower() for n in cfg["build_dir_names"]}
    parts = [x.lower() for x in path.parts[:-1]]
    return path.suffix.lower() in {e.lower() for e in cfg["build_exts"]} and any(x in names for x in parts)


def plan(cfg, guard: Guard | None = None, now: float | None = None, only=None) -> dict:
    """Every action, with the hash it was planned on. `only` (a set of CLASSES) skips the scans no requested class
    needs -- the launch watcher plans gzip/launch without hashing every agent-output file -- and counts a planned
    action of an unrequested class as kept, so the rows still sum to what was scanned."""
    now = time.time() if now is None else now
    guard = guard or Guard(cfg)
    actions, rows = [], {}
    wants = (lambda *classes: True) if not only else (lambda *classes: bool(set(classes) & set(only)))

    def count(row, n):
        rows[row] = rows.get(row, 0) + n

    def act(action, row):
        if only and action["class"] not in only:
            count("kept: class not requested (--only)", action["bytes"])
        else:
            actions.append(action)
            count(row, action["bytes"])

    # ---- agent output: recreatable, build, link
    tasks = task_folders(cfg) if wants("recreatable", "build", "link") else []
    live = live_index(cfg) if tasks else {}
    live_hash = {}
    by_size = {}

    def queue_duplicate(f, task, size, digest=None):
        # chmod affects every name of an inode. Existing links may include live
        # files outside every configured root; their ownership is unqualified.
        if f.stat().st_nlink != 1:
            count("kept: pre-existing hard links (ownership unqualified)", size)
        else:
            by_size.setdefault(size, []).append((f, task, digest))

    for task, state in tasks:
        for f in walk_files(task):
            try:
                size = f.stat().st_size
            except OSError as exc:
                raise OSError("candidate could not be inspected: {}".format(f)) from exc
            if state == "loose":
                count("kept: loose file in a root (shared live state)", size)
                continue
            if state == "active":
                count("kept: task folder active (written within active_hours)", size)
                continue
            why = guard.why_protected(f)
            if why:
                count("kept: protected ({})".format(why), size)
                continue
            if is_build(f, cfg):
                act({"class": "build", "path": str(f), "bytes": size, "hash": blake(f),
                                "task": str(task)}, "plan: build output -> Recycle Bin")
                continue
            if size >= cfg["min_bytes"] and size in live:
                h = blake(f)
                match = None
                for lp in live[size]:
                    if lp not in live_hash:
                        live_hash[lp] = blake(lp)
                    if live_hash[lp] == h:
                        match = lp
                        break
                if match:
                    act({"class": "recreatable", "path": str(f), "bytes": size, "hash": h,
                                    "live": match, "task": str(task)}, "plan: copy of a live file -> Recycle Bin")
                    continue
                queue_duplicate(f, task, size, h)
                continue
            if size >= cfg["min_bytes"]:
                queue_duplicate(f, task, size)
                continue
            count("kept: below min_bytes / not duplicated", size)

    for size, group in sorted(by_size.items()):
        if len(group) < 2:
            count("kept: below min_bytes / not duplicated", size * len(group))
            continue
        hashed = {}
        for f, task, h in group:
            hashed.setdefault(h or blake(f), []).append((f, task))
        for h, files in hashed.items():
            files.sort(key=lambda x: str(x[0]).lower())
            canon = files[0][0]
            count("kept: canonical copy of a duplicate set" if len(files) > 1
                  else "kept: below min_bytes / not duplicated", size)
            for f, task in files[1:]:
                try:
                    same = os.stat(f).st_ino == os.stat(canon).st_ino and os.stat(f).st_ino != 0
                except OSError as exc:
                    raise OSError("duplicate identity could not be inspected") from exc
                if same:
                    count("kept: already hard-linked", size)
                    continue
                act({"class": "link", "path": str(f), "bytes": size, "hash": h,
                     "canonical": str(canon), "task": str(task)},
                    "plan: duplicate -> hard link (bytes freed)")

    # ---- launch logs
    for r in (cfg["launch_logs"] if wants("gzip", "launch") else []):
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        for e in sorted(os.scandir(root), key=lambda e: e.name):
            p = Path(e.path)
            size = sum(f.stat().st_size for f in walk_files(p)) if e.is_dir() else e.stat().st_size
            m = LAUNCH_RE.match(e.name) if e.is_dir() else None
            if not m:
                count("kept: launch log, labelled or not a launch folder (Forever)", size)
                continue
            if newest_mtime(p) > now - cfg["active_hours"] * 3600:
                count("kept: launch folder active", size)
                continue
            age = (now - time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))) / 86400
            why = protected_inside(guard, p) if age >= cfg["gzip_after_days"] else ""
            if why and age >= cfg["recycle_after_days"]:
                count("kept: protected ({})".format(why), size)
            elif age >= cfg["recycle_after_days"]:
                act({"class": "launch", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: launch folder past recycle_after_days -> Recycle Bin")
            elif age >= cfg["gzip_after_days"]:
                for f in walk_files(p):
                    s = f.stat().st_size
                    if f.suffix.lower() == ".gz" or s < 65536:
                        count("kept: launch log, recent or already compressed", s)
                        continue
                    fwhy = guard.why_protected(f)
                    if fwhy:
                        count("kept: protected ({})".format(fwhy), s)
                        continue
                    act({"class": "gzip", "path": str(f), "bytes": s, "hash": blake(f), "task": str(p)},
                        "plan: launch log -> gzip (original to Recycle Bin)")
            else:
                count("kept: launch log, recent or already compressed", size)

    # ---- legacy snapshot stamps: recycle a stamp whose content repeats the last one kept
    for r in (cfg["legacy_snapshots"] if wants("snapshot") else []):
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        stamps = sorted((e for e in os.scandir(root) if e.is_dir() and STAMP_RE.match(e.name)),
                        key=lambda e: e.name)
        prev = None
        for i, e in enumerate(stamps):
            p = Path(e.path)
            files = sorted(walk_files(p))
            sig = sorted((str(f.relative_to(p)).lower(), f.stat().st_size, blake(f)) for f in files)
            size = sum(s for _, s, _ in sig)
            if newest_mtime(p) > now - cfg["active_hours"] * 3600:
                count("kept: snapshot folder active", size)
                continue
            why = protected_inside(guard, p) if sig == prev and i != len(stamps) - 1 else ""
            if why:
                count("kept: protected ({})".format(why), size)
            elif sig == prev and i != len(stamps) - 1:
                act({"class": "snapshot", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: snapshot repeating the previous one -> Recycle Bin")
            else:
                count("kept: snapshot with distinct content (or the newest)", size)
                prev = sig

    # ---- named scratch folders
    for s in (cfg["scratch"] if wants("scratch") else []):
        p = _abs(cfg, s)                     # relative to the toolkit root, like every other configured path -- never the cwd
        if p.is_dir():
            fp = fingerprint(p)
            if newest_mtime(p) > now - cfg["active_hours"] * 3600:
                count("kept: scratch folder active", fp[1])
                continue
            why = protected_inside(guard, p)
            if why:
                count("kept: protected ({})".format(why), fp[1])
                continue
            act({"class": "scratch", "path": str(p), "bytes": fp[1], "fingerprint": fp},
                "plan: named scratch folder -> Recycle Bin")

    return {"created": now, "root": str(cfg["root"]), "actions": actions, "rows": rows}


def print_rows(rows: dict, title: str):
    total = sum(rows.values())
    print("{}: {:.2f} GiB scanned".format(title, total / GIB))
    for k in sorted(rows, key=lambda k: (not k.startswith("plan"), k)):
        print("  {:>9.2f} GiB  {}".format(rows[k] / GIB, k))
    kept = sum(v for k, v in rows.items() if not k.startswith("plan"))
    print("  {:>9.2f} GiB  (left alone, all 'kept' rows)".format(kept / GIB))
    planned = total - kept
    print("  {:>9.2f} GiB  (planned)   rows sum: {}".format(planned / GIB, "OK" if planned + kept == total
                                                              else "ACCOUNTING MISMATCH"))


# --------------------------------------------------------------------------- applying

def recycle(paths) -> dict:
    """{path: None | error} -- one PowerShell process for the whole batch. Recycle Bin only, never a delete."""
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise AssertionError("tests must inject a fake recycler")
    if os.name != "nt":
        raise RuntimeError("Recycle Bin removal is implemented for Windows only")
    # The shell's own delete, NOT Microsoft.VisualBasic's DeleteDirectory(..., 'OnlyErrorDialogs', ...): that UI option
    # suppresses confirmations, and the confirmation it suppresses is the one Windows shows when an item is too big for
    # the bin and would be destroyed instead. FOF_WANTNUKEWARNING (0x4000) brings exactly that prompt back while
    # FOF_NOCONFIRMATION (0x10) still skips "move to the Recycle Bin?". FOF_ALLOWUNDO (0x40) is the Recycle Bin.
    # The user's answer is the only way anything is ever permanently deleted; "No" leaves the item in place.
    script = (
        "Add-Type -TypeDefinition @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public static class HkBin {\n"
        " [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]\n"
        " struct Op { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;\n"
        "  public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }\n"
        " [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHFileOperationW(ref Op op);\n"
        " public static string Recycle(string p) {\n"
        "  var op = new Op { wFunc = 3, pFrom = p + \"\\0\\0\", fFlags = 0x4000 | 0x40 | 0x10 | 0x200 | 0x4 };\n"
        "  int rc = SHFileOperationW(ref op);\n"
        "  if (op.fAnyOperationsAborted) return \"declined: left in place\";\n"
        "  return rc == 0 ? \"\" : \"shell error 0x\" + rc.ToString(\"X\"); } }\n"
        "'@;"
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; [Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        "foreach ($p in [Console]::In.ReadToEnd().Split([char]10)) { $p = $p.TrimEnd([char]13); if ($p -eq '') { continue };"
        " $e = [HkBin]::Recycle($p);"
        " if ($e -eq '') { [Console]::Out.WriteLine('OK' + [char]9 + $p) }"
        " else { [Console]::Out.WriteLine('ERR' + [char]9 + $p + [char]9 + $e) } }")
    # The list goes on stdin: under -Command, trailing arguments are NOT $args -- PowerShell appends them to the
    # command text and runs them (measured: $args.Count == 0). A list file would be one more thing to delete.
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], input="\n".join(paths),
                         capture_output=True, text=True, encoding="utf-8", timeout=3600)
    silent = "no result reported" + (" (powershell: {})".format(out.stderr.strip()[:300]) if out.stderr.strip() else "")
    result = {p: silent for p in paths}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] in result:
            result[parts[1]] = None if parts[0] == "OK" else (parts[2] if len(parts) > 2 else "error")
    for p in paths:                     # the bin's word is not the proof: the path must actually be gone
        if result[p] is None and os.path.exists(p):
            result[p] = "still present after a reported recycle"
    return result


def _set_readonly(path):
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


class _Memo:
    """Per-apply caches: a live master is hashed once, not once per copy of it; a task folder is walked once."""

    def __init__(self):
        self.hashes, self.newest = {}, {}
        self.links = {}  # only canonical links successfully created by THIS apply

    def hash(self, path):
        st = os.stat(path)
        k = (_key(path), st.st_size, st.st_mtime_ns)
        if k not in self.hashes:
            self.hashes[k] = blake(path)
        return self.hashes[k]

    def task_newest(self, task):
        k = _key(task)
        if k not in self.newest:
            self.newest[k] = newest_mtime(Path(task))
        return self.newest[k]


def check(action, cfg, guard: Guard, now: float, memo: _Memo | None = None) -> str:
    """'' when the action may run; otherwise why it is refused. Everything plan() decided is re-proved here."""
    memo = memo or _Memo()
    p = Path(action["path"])
    if not p.exists():
        return "gone since the plan"
    if action["class"] in ("recreatable", "build", "link", "gzip"):
        why = guard.why_protected(p)
        if why:
            return "protected ({})".format(why)
        if p.stat().st_size != action["bytes"] or blake(p) != action["hash"]:
            return "changed since the plan"
    if action["class"] in ("recreatable", "build", "link", "gzip"):
        if not action.get("task"):
            return "task scope missing; create a new plan"
        if memo.task_newest(action["task"]) > now - cfg["active_hours"] * 3600:
            return "task folder written to since the plan (active)"
    if action["class"] == "recreatable":
        if not Path(action["live"]).is_file() or memo.hash(action["live"]) != action["hash"]:
            return "live file no longer identical"
    if action["class"] == "link":
        c = Path(action["canonical"])
        if not any(c.resolve().is_relative_to(_abs(cfg, base).resolve()) for base in cfg["agent_output"]):
            return "canonical copy is outside agent output"
        if not c.is_file() or memo.hash(c) != action["hash"]:
            return "canonical copy changed or gone"
        if guard.why_protected(c):
            return "canonical copy is protected"
        if p.stat().st_nlink != 1 or c.stat().st_nlink != 1 + memo.links.get(_key(c), 0):
            return "hard-link ownership changed or is unqualified"
    if action["class"] in ("launch", "snapshot", "scratch"):
        why = protected_inside(guard, p)
        if why:
            return "protected ({})".format(why)
        if fingerprint(p) != action["fingerprint"]:
            return "folder changed since the plan"
        if newest_mtime(p) > now - cfg["active_hours"] * 3600:
            return "folder is active"
    return ""


def link(action, expected_canonical_links=1):
    dup, canon = Path(action["path"]), Path(action["canonical"])
    tmp = dup.with_name(dup.name + ".housekeep-link")
    if tmp.exists():
        raise OSError("leftover {} -- inspect it by hand".format(tmp))
    if dup.stat().st_nlink != 1 or canon.stat().st_nlink != expected_canonical_links:
        raise OSError("hard-link ownership changed before linking")
    os.link(canon, tmp)
    if (os.stat(tmp).st_ino != os.stat(canon).st_ino or blake(tmp) != action["hash"]
            or dup.stat().st_nlink != 1 or canon.stat().st_nlink != expected_canonical_links + 1):
        recycle([tmp])                                           # our own just-made link -- still the bin, never a delete
        raise OSError("the new link's bytes or ownership changed")
    if not os.access(dup, os.W_OK):
        os.chmod(dup, stat.S_IWRITE | stat.S_IREAD)
    os.replace(tmp, dup)
    _set_readonly(canon)                                          # one attribute, shared by every link


def compress(action) -> Path:
    src = Path(action["path"])
    gz = src.with_name(src.name + ".gz")
    if gz.exists():
        raise OSError("{} already exists".format(gz.name))
    with open(src, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    d = hashlib.blake2b(digest_size=16)
    with gzip.open(gz, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            d.update(b)
    if d.hexdigest() != action["hash"]:
        recycle([gz])                                             # our own bad output -- still the bin, never a delete
        raise OSError("gzip round trip does not reproduce the original")
    st = src.stat()
    os.utime(gz, (st.st_atime, st.st_mtime))
    return gz


def apply(plan_doc: dict, cfg, only=None, recycler=None, now: float | None = None) -> int:
    # Resolved at CALL time: a def-time default would be the real recycle() even where a test replaced it.
    recycler = recycle if recycler is None else recycler
    now = time.time() if now is None else now
    guard = Guard(cfg)
    memo = _Memo()
    order = [c for c in CLASSES if not only or c in only]
    failed = 0
    totals = {}
    for cls in order:
        acts = [a for a in plan_doc["actions"] if a["class"] == cls]
        if not acts:
            continue
        to_recycle, done = [], 0
        for a in acts:
            try:
                why = check(a, cfg, guard, now, memo)
            except OSError as exc:
                why = "could not recheck candidate: {}".format(exc)
            if why:
                print("  REFUSED  {:<11} {}  ({})".format(cls, a["path"], why))
                failed += 1
                continue
            try:
                if cls == "link":
                    canonical_key = _key(a["canonical"])
                    link(a, 1 + memo.links.get(canonical_key, 0))
                    memo.links[canonical_key] = memo.links.get(canonical_key, 0) + 1
                    done += a["bytes"]
                elif cls == "gzip":
                    compress(a)
                    to_recycle.append(a)
                else:
                    to_recycle.append(a)
            except OSError as e:
                print("  FAILED   {:<11} {}  ({})".format(cls, a["path"], e))
                failed += 1
        if to_recycle:
            res = recycler([a["path"] for a in to_recycle])
            for a in to_recycle:
                err = res.get(a["path"], "no result reported")
                if err:
                    print("  FAILED   {:<11} {}  (Recycle Bin: {})".format(cls, a["path"], err))
                    failed += 1
                else:
                    done += a["bytes"]
        totals[cls] = done
        print("  {:<11} {:>4} action(s), {:.2f} GiB done".format(cls, len(acts), done / GIB))
    print("applied: {:.2f} GiB; {} refused/failed".format(sum(totals.values()) / GIB, failed))
    return 1 if failed else 0


# --------------------------------------------------------------------------- temp sweep
# The toolkit's own leftover temp dirs go to the Recycle Bin like everything else -- never a permanent delete (user
# ruling 2026-10-18: "There's never a good reason to just outright delete. Never."). Three gates stand between a dir
# and the bin: the name is one the toolkit creates, nothing in it or its direct children changed for
# temp_after_hours, and the dir can be renamed (Windows refuses that while any process holds a file open inside it).
# A dir the bin will not take stays where it is and is named in the session note. Same code as the Skyrim
# toolkit's tools/housekeep.py.

def _has_bin() -> bool:
    """recycle() is Windows-only. Off Windows the sweep KEEPS what it found (and says so) rather than raising: a raise
    in the detached session job meant the state file was never written and the sweep re-ran at every session start."""
    return os.name == "nt"


def _newest_shallow(p: Path) -> float:
    """Compatibility name: activity checks cover the complete candidate tree."""
    return newest_mtime(p)


def _in_use(p: Path) -> bool:
    probe = p.with_name(p.name + ".housekeep-probe")
    if probe.exists():
        raise OSError("existing probe path must be inspected: {}".format(probe))
    try:
        os.rename(p, probe)
    except OSError:
        return True
    try:
        os.rename(probe, p)
    except OSError as exc:
        raise OSError("probe restoration failed; retained at {} (restore to {}): {}".format(probe, p, exc)) from exc
    return False


def temp_candidates(cfg, temp: Path):
    """Potential names only. A name grants no ownership; sweep_temp verifies each receipt. Only the toolkit's own
    prefixes: never pytest's machine-wide pytest-of-<user>, whose runs belong to every project (RETENTION.md)."""
    prefixes = tuple(cfg["temp_prefixes"])
    entries = list(os.scandir(temp))
    for e in entries:
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
            # A junction is not a symlink to os.DirEntry on Python 3.10: skip every reparse point outright.
            if getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                continue
        except OSError as exc:
            raise OSError("could not inspect temporary candidate {}".format(e.path)) from exc
        if prefixes and e.name.startswith(prefixes):
            yield Path(e.path), "toolkit temp"


def sweep_temp(cfg, temp: Path | None = None, now: float | None = None, recycler=None) -> dict:
    """Recycle every candidate that passes the gates, in one batch. What the bin refuses stays where it is."""
    temp = Path(temp or tempfile.gettempdir())
    now = time.time() if now is None else now
    cutoff = now - cfg["temp_after_hours"] * 3600
    rows = {"recycled": 0, "kept: changed recently": 0, "kept: in use": 0,
            "kept: ownership unverified": 0, "kept: protected": 0, "kept: no Recycle Bin on this OS": 0,
            "failed": 0}
    ready = []
    failed = []
    try:
        candidates = list(temp_candidates(cfg, temp))
        guard = Guard(cfg)
    except OSError as exc:
        rows["failed"] = 1
        return {"rows": rows, "failed": [str(exc)], "temp": str(temp)}
    for p, _why in candidates:
        try:
            if not owns_temp(p, cfg["root"]):
                rows["kept: ownership unverified"] += 1
                continue
            if protected_inside(guard, p):
                rows["kept: protected"] += 1
                continue
            if _newest_shallow(p) > cutoff:
                rows["kept: changed recently"] += 1
                continue
            if _in_use(p):
                rows["kept: in use"] += 1
            else:
                ready.append(p)
        except (OSError, ValueError) as exc:
            rows["failed"] += 1
            failed.append("{} ({})".format(p, exc))
    if recycler is None and not _has_bin():
        rows["kept: no Recycle Bin on this OS"] = len(ready)
        ready = []
    for p, err in ((recycler or recycle)(ready) if ready else {}).items():
        if err:
            rows["failed"] += 1
            failed.append("{}  ({})".format(p, err))
        else:
            rows["recycled"] += 1
    return {"rows": rows, "failed": failed, "temp": str(temp)}


def worktree_count(root: Path) -> int | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("could not count worktrees") from exc
    if out.returncode:
        if "not a git repository" in out.stderr:
            return None
        raise OSError("git worktree inventory failed (exit {})".format(out.returncode))
    return max(0, sum(1 for line in out.stdout.splitlines() if line.startswith("worktree ")) - 1)


def session(cfg, state: Path) -> int:
    """The session-start job: sweep the toolkit's temp dirs, then write a one-line note for the next banner when
    something needs the user (free space near the floor, Codex sessions or worktrees past their threshold).
    Runs at most once per sweep_every_hours: the state file's age is the clock."""
    if os.environ.get("X4_HOUSEKEEP_SWEEP", "").lower() == "off":     # a test harness: never the real temp dir / bin
        return 0
    try:
        if time.time() - state.stat().st_mtime < cfg["sweep_every_hours"] * 3600:
            return 0
    except FileNotFoundError:
        pass  # silent-ok: the first session has no previous sweep timestamp
    res = sweep_temp(cfg)
    notes = []
    r = res["rows"]
    if r["recycled"] or r["failed"]:
        notes.append("moved {} leftover temp dir(s) to the Recycle Bin{}".format(
            r["recycled"], "; {} could not be recycled and were LEFT IN PLACE (ask the user before any permanent "
                           "delete)".format(r["failed"]) if r["failed"] else ""))
    if r["kept: no Recycle Bin on this OS"]:
        notes.append("{} leftover toolkit temp dir(s) kept: x4housekeep recycles on Windows only".format(
            r["kept: no Recycle Bin on this OS"]))
    notes.extend(res["failed"][:3])
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    try:
        codex = dir_size(home / ".codex" / "sessions") / GIB
        if codex > cfg["codex_sessions_warn_gib"]:
            notes.append("~/.codex/sessions is {:.1f} GiB and Codex does not prune it".format(codex))
    except OSError:
        notes.append("Codex session inventory unavailable")
        r["failed"] += 1
    try:
        wt = worktree_count(cfg["root"])
        if wt is not None and wt > cfg["worktrees_warn"]:
            notes.append("{} git worktrees (git worktree list)".format(wt))
    except OSError:
        notes.append("worktree inventory unavailable")
        r["failed"] += 1
    floor = cfg["free_floor_gib"]
    try:
        free = shutil.disk_usage(str(cfg["root"])).free / GIB
        if floor and free - floor < 20:
            notes.append("{:.0f} GiB free, floor {} GiB".format(free, floor))
    except OSError:
        notes.append("disk inventory unavailable")
        r["failed"] += 1
    state.parent.mkdir(parents=True, exist_ok=True)
    body = (("[x4 disk] " + "; ".join(notes) + " -- python scripts/x4housekeep.py report\n")
            if notes else "").encode("utf-8")
    state.write_bytes(body)
    return 1 if r["failed"] else 0


# --------------------------------------------------------------------------- report

def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in walk_files(p) if f.exists()) if p.exists() else 0


def user_level(cfg):
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    temp = Path(tempfile.gettempdir())
    rows = [("~/.claude/projects (transcripts)", home / ".claude" / "projects"),
            ("~/.codex/sessions", home / ".codex" / "sessions"),
            ("<temp>/claude (scratchpads)", temp / "claude")]
    print("user level (report only -- these are the agents' own stores):")
    for label, p in rows:
        print("  {:>9.2f} GiB  {}".format(dir_size(p) / GIB, label))
    for label, sp in (("user", home / ".claude" / "settings.json"),
                      ("project", cfg["root"] / ".claude" / "settings.json"),
                      ("project local", cfg["root"] / ".claude" / "settings.local.json")):
        try:
            v = json.loads(sp.read_text(encoding="utf-8")).get("cleanupPeriodDays")
        except FileNotFoundError:
            continue  # silent-ok: these settings files are optional
        except (OSError, ValueError) as exc:
            print("  could not inspect {} settings ({})".format(label, type(exc).__name__))
            continue
        if v is not None:
            note = "" if label == "user" else \
                "  <- WARNING: the sweep deletes EVERY project's transcripts at this value (RETENTION.md)"
            print("  cleanupPeriodDays = {} ({} settings){}".format(v, label, note))


def report(cfg) -> int:
    doc = plan(cfg)
    print_rows(doc["rows"], "agent output + rolling stores")
    by = {}
    for a in doc["actions"]:
        by[a["class"]] = by.get(a["class"], 0) + a["bytes"]
    print("planned by class: " + ", ".join("{} {:.2f} GiB".format(c, by.get(c, 0) / GIB) for c in CLASSES))
    user_level(cfg)
    floor = cfg["free_floor_gib"]
    free = shutil.disk_usage(str(cfg["root"])).free / GIB
    if floor:
        print("free space: {:.1f} GiB, floor {} GiB{}".format(
            free, floor, "  <- within 20 GiB of the floor" if free - floor < 20 else ""))
    return 0


def main(argv) -> int:
    args = list(argv)
    cfg_path = None
    if "--config" in args:
        i = args.index("--config")
        if i + 1 >= len(args):
            print("--config needs a file", file=sys.stderr)
            return 2
        cfg_path = args[i + 1]
        del args[i:i + 2]
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as e:
        print("housekeep: {}".format(e), file=sys.stderr)
        return 2
    cmd = args.pop(0) if args else "report"
    if cmd == "report" and not args:
        return report(cfg)
    if cmd == "sweep-temp" and not args:
        res = sweep_temp(cfg)
        rows = res["rows"]
        print("toolkit temp dirs under {}: {} considered".format(res["temp"], sum(rows.values())))
        for k, v in rows.items():                     # every row prints, zeros included; they sum to the total
            print("  {:>6}  {}".format(v, k))
        for p in res["failed"]:
            print("  FAILED   " + p)
        return 1 if res["failed"] else 0
    if cmd == "session" and len(args) == 1:
        return session(cfg, Path(args[0]))
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1].split(",")) if i + 1 < len(args) else set()
        del args[i:i + 2]
        if not only or only - set(CLASSES):
            print("--only takes: {}".format(",".join(CLASSES)), file=sys.stderr)
            return 2
    if cmd == "plan" and len(args) == 2 and args[0] == "-o":
        doc = plan(cfg, only=only)
        Path(args[1]).write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print_rows(doc["rows"], "plan")
        print("{} action(s) written to {}".format(len(doc["actions"]), args[1]))
        return 0
    if cmd == "apply" and args:
        if len(args) != 1:
            print("usage: housekeep.py apply PLAN.json [--only CLASS,...]", file=sys.stderr)
            return 2
        return apply(json.loads(Path(args[0]).read_text(encoding="utf-8")), cfg, only)
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

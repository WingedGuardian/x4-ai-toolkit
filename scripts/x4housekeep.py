#!/usr/bin/env python3
"""x4housekeep -- disk retention for agent output: report, plan, then apply exactly the plan.

Ported from the Skyrim toolkit's tools/housekeep.py: the same logic, with the X4 save rule and X4 paths. The
shared policy text is pinned in both repos by test_retention_policy.py; a logic fix in one belongs in both.

Agent work piles up bytes that carry no information: copies of game files made as test fixtures, build
intermediates, the same log copied into ten evidence folders, launch logs nobody prunes, snapshot folders
that repeat the one before. The policy this tool implements is RETENTION.md; read that first.

    python scripts/x4housekeep.py [report]            what is where, by class (changes nothing, read times included)
    python scripts/x4housekeep.py plan -o plan.json   every action, with the hash it was planned on
            [--only CLASS[,CLASS]]                   (skips the scans no listed class needs)
    python scripts/x4housekeep.py apply plan.json     re-check each action, then do exactly those
            [--only CLASS[,CLASS]]                   (recreatable, build, link, gzip, launch, snapshot, scratch, archive, expire)
    python scripts/x4housekeep.py sweep-temp          recycle the toolkit's own stale temp dirs (see "temp sweep")
    python scripts/x4housekeep.py session STATE.txt   sweep-temp, then write the session-start disk note to STATE.txt
    --config FILE   default <toolkit>/x4-housekeep.json (x4-housekeep.json.example ships)

Two passes, never one: `plan` decides, `apply` re-verifies. An action whose file changed since it was planned
(size, hash, a newer write in its task folder) is REFUSED, not re-decided -- run `plan` again.

What it never touches, whatever the config says: saves (save/*.xml.gz; anything under Documents/Egosoft/X4; a
copy anywhere, by the game's save names or a <savegame> root) and anything under a folder whose name
contains "backup"; git-tracked files; every file a project doc links to; and any task folder written to within
`active_hours` (another session is probably still working there).

Cross-run retirement uses the Recycle Bin (Windows: FOF_ALLOWUNDO). Unrecyclable items stay in place;
there is no automatic permanent fallback. Same-run owned staging may be cleaned by its creator.
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
import zlib
from pathlib import Path
import importlib.util
_BACKUP_SPEC = importlib.util.spec_from_file_location('housekeep_cold_helper', Path(__file__).with_name('housekeep_backups.py'))
certainty = importlib.util.module_from_spec(_BACKUP_SPEC)
_BACKUP_SPEC.loader.exec_module(certainty)
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4temp import owns_temp

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOLKIT_ROOT / "x4-housekeep.json"


#: The names the game gives a save. RETENTION.md's Forever class is "saves and every copy of them", and a copy is usually
#: not under a folder called save/. Exact stems only: "save_notes.xml" and "my_quicksave.xml.gz" are not saves by name.
SAVE_NAME = re.compile(r"^(?:save_\d+|quicksave|autosave_\d+)\.xml(?:\.gz)?$")
#: The ROOT element, not the word: optional BOM, XML declaration and comments, then <savegame. "<savegame-like/>" or a
#: mention deeper in some other document is not a save.
SAVE_ROOT = re.compile(rb"(?:\xef\xbb\xbf)?\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--.*?-->\s*)*<savegame[\s>/]", re.S)
SAVE_SNIFF_BYTES = 4096


def is_save(low: str) -> bool:
    """An X4 save by its path, or anything in the user profile (Documents/Egosoft/X4/<id>/ holds saves, content.xml and
    UI data). `low` is the lower-cased path with forward slashes. A renamed copy has no such name: _holds_savegame
    reads its first bytes."""
    return ("/egosoft/x4/" in low or ("/save/" in low and low.endswith((".xml.gz", ".xml")))
            or bool(SAVE_NAME.match(low.rsplit("/", 1)[-1])))


def _holds_savegame(path) -> bool:
    """Does this file open with X4's <savegame> root, plain or gzipped, whatever it is called? Protection only, so a false
    positive keeps a file. Bounded: at most two sniff sizes read, and one decompressed, from any file; a truncated gzip
    still answers. The read is not recorded (open_unrecorded) and writes nothing. Unreadable or corrupt content is
    ordinary content here: every removal path still has its own read of it."""
    try:
        with _archive_module().open_unrecorded(path) as raw:
            head = raw.read(SAVE_SNIFF_BYTES * 2)
        if head[:2] == b"\x1f\x8b":
            head = zlib.decompressobj(wbits=31).decompress(head, SAVE_SNIFF_BYTES)
    except (OSError, zlib.error):
        return False  # silent-ok: not readable as XML; an unreadable file is held by its removal path's own proof
    return bool(SAVE_ROOT.match(head[:SAVE_SNIFF_BYTES]))


CLASSES = ("recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch", "archive", "expire")
DEFAULTS = {
    # Optional task archiving: first-output/code approval precedes any archive action.
    'archive_enabled': False,
    'archive_root': '',
    'archive_retention_days': 90,
    'archive_review_days': 30,
    'archive_review_seconds': 15,
    'archive_roots': {},
    'archive_after_days': 14,
    'min_idle_days': 7,
    'read_veto_hours': 48,
    'budget_gib': 0,
    'pin_warn_gib': 1,
    'approved_sha256': '',
    "recycle_bin_warn_gib": 10,       # read-only Windows aggregate; 0 disables warning
    "obsolete_items": {},            # exact bytes + explicit reason/evidence; never age alone
    "backup_archive": {},            # opt-in cold backups; docs/BACKUP-ARCHIVE.md
    # Optional: a resource-job ledger ({"entries": [{"cwd": ...}]}) naming folders a running job works in. "" = none.
    # Only ever a hold on archiving: a ledger that cannot be read is announced and archiving goes on (RETENTION.md).
    "job_ledger": "",
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
DAY = 86400


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
    if not cfg["archive_root"]:
        cfg["archive_root"] = str(cfg["root"] / ".claude" / "archive")
    cfg["archive_root"] = str(_abs(cfg, cfg["archive_root"]))
    return cfg


def _abs(cfg, p) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg["root"] / q


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def blake(path, chunk=1 << 20) -> str:
    """A current content proof; never rewind a concurrent writer's timestamps."""
    return _archive_module()._retirement_hash(Path(path), float('inf'), chunk)


def newest_mtime(folder: Path, own=()) -> float:
    """The newest write in `folder`. `own` holds the keys of folders this run itself changed: recycling a file bumps its
    folder's mtime, which is this run's write and no one else's. Only that folder's OWN stamp is skipped, so a new file
    inside it, or any other folder, still counts."""
    newest = 0.0 if _key(folder) in own else folder.stat().st_mtime
    for path in walk_files(folder, include_dirs=True):
        st = path.stat()
        if stat.S_ISDIR(st.st_mode) and _key(path) in own:
            continue
        newest = max(newest, st.st_mtime)
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
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120,
                             **NO_WINDOW)
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
    roots = [_abs(cfg, r) for r in cfg["agent_output"]]
    if not roots:
        return links
    for doc in cfg["evidence_docs"]:
        p = _abs(cfg, doc)
        if not p.is_file():
            continue
        prefixes = []
        for root in roots:
            prefixes.append(re.escape(str(root).replace("\\", "/")))
            try:
                prefixes.append(re.escape(os.path.relpath(root, p.parent).replace("\\", "/")))
            except ValueError:
                pass  # silent-ok: cross-drive documents can still use the absolute spelling
        start_re = re.compile("(?:{})/".format("|".join(prefixes)), re.I if os.name == "nt" else 0)
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
                            q = Path(candidate)
                            links.add(_key(q if q.is_absolute() else p.parent / q))
                        if len(links) > 50000:
                            raise OSError("evidence references exceed the safe discovery bound; cleanup refused")
    return links


class Guard:
    """The Forever class and the per-run exclusions, in one place: plan and apply both ask it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracked = tracked_files(cfg["root"])
        # Output may live in a different repository; the toolkit's index says
        # nothing about its tracked evidence. Missing/link roots stay held by
        # inventory rather than being traversed for Git discovery.
        seen = {_key(cfg["root"])}
        roots = [*cfg["agent_output"], *cfg["launch_logs"], *cfg["legacy_snapshots"],
                 *cfg["scratch"], *cfg.get("archive_roots", {})]
        for value in roots:
            root = _abs(cfg, value)
            if _key(root) in seen or root.is_relative_to(cfg["root"]):
                continue
            seen.add(_key(root))
            try:
                info = root.lstat()
            except FileNotFoundError:
                continue  # silent-ok: a configured root that does not exist holds nothing to protect
            if root.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                continue
            if root.is_dir():
                self.tracked.update(tracked_files(root))
        self.links = evidence_links(cfg)
        self.globs = [g.lower() for g in cfg["protected_globs"]]

    def why_protected(self, path) -> str:
        p = str(path)
        low = p.replace("\\", "/").lower()
        if is_save(low) or _holds_savegame(p):
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
    if target.is_dir() and (target/'.housekeep-keep').exists():
        return 'pinned (.housekeep-keep)'
    why = guard.why_protected(target / "_" if target.is_dir() else target)   # the folder's OWN name counts too
    if why:
        return why
    for f in walk_files(target) if target.is_dir() else ():
        why = guard.why_protected(f)
        if why:
            return why
    return ""


# --------------------------------------------------------------------------- task folders

def task_folders(cfg, held=None):
    """[(path, state)] under every agent_output root; state is "idle", "active" or "loose".

    A folder directly in a root is a task folder, judged active as a whole. A folder named in nested_task_roots
    is a root of its own (recursively). A LOOSE file -- one sitting directly in a root, not inside a task folder --
    is never a candidate: that is where shared live state lives (a service's events.jsonl, a status JSON a session
    rewrites). Making an append-only log read-only would break its next write, and a log appended once a week
    walks straight through the activity gate."""
    out = []
    held = [] if held is None else held
    cutoff = time.time() - cfg["active_hours"] * 3600
    nested = {_key(_abs(cfg, n)) for n in cfg["nested_task_roots"]}

    def visit(folder: Path):
        for e in sorted(os.scandir(folder), key=lambda e: e.name):
            p = Path(e.path)
            try:
                info = p.lstat()
                if p.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OSError("symbolic link or reparse point is not a cleanup target: {}".format(p))
                if e.is_dir(follow_symlinks=False) and _key(p) in nested:
                    visit(p)
                elif e.is_dir(follow_symlinks=False):
                    out.append((p, "active" if newest_mtime(p) > cutoff else "idle"))
                else:
                    out.append((p, "loose"))
            except OSError as exc:
                held.append({"path": str(p), "reason": str(exc), "bytes": None})

    for r in cfg["agent_output"]:
        root = _abs(cfg, r)
        try:
            info = root.lstat()
            if root.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("configured root is a symbolic link or reparse point")
            if not root.is_dir():
                raise OSError("configured root is not a directory")
            why = landing_refusal(root)      # apply would refuse each of its actions anyway: say so once, here
            if why:
                raise OSError(why)
            visit(root)
        except OSError as exc:
            held.append({"path": str(root), "reason": str(exc), "bytes": None})
    return out


class Unaddressable(OSError):
    """A name the directory listing returned but its path cannot reach.

    Windows lists names ending in '.' or ' ' (e.g. 'y2.txt.'), but the normal Win32 path strips the trailing
    character, so lstat raises FileNotFoundError on a file that exists. It stays an OSError on purpose: every
    DELETE/MOVE path already fails closed on OSError (task held, sweep counts it failed). Only report-only
    callers catch it, and they must say what they could not measure (dir_size_counted)."""

    def __init__(self, path):
        super().__init__("name listed but not addressable by its path (trailing dot or space?): {}".format(path))
        self.path = path


def walk_files(target: Path, include_dirs=False):
    def failed(error):
        raise error
    def linked(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path is target:
                raise
            raise Unaddressable(path) from None
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



KEEP_PIN, CLOSED_MARK = ".housekeep-keep", ".housekeep-closed"
_READS_RECORDED = {}
_ARCHIVE_MODULE = None
NO_WINDOW = {"creationflags": 0x08000000} if os.name=="nt" else {}
RECYCLE_BATCH = 500
MAX_BIN_PATH = 259


def activity(folder: Path) -> tuple:
    """Newest write/read, measured over the strict source set."""
    w = r = 0.0
    for p in walk_files(folder):
        st = p.stat()
        w, r = max(w, st.st_mtime), max(r, st.st_atime)
    return w, r


def newest_activity(folder: Path) -> float:
    return max(activity(folder))


def reads_recorded(folder: Path) -> bool:
    """Measure using owned scratch on the same volume; never backdate source files."""
    return _archive_module().reads_recorded(folder, _READS_RECORDED)


class LiveStateUnknown(OSError):
    """A source of 'work is still live' (the instruction files) exists but cannot be read: unknown, not idle."""


def live_state_tokens(cfg) -> set:
    """Backticked names in the `## Live state` block of the instruction files: work a session is still holding."""
    toks = set()
    for doc in ("CLAUDE.md", "AGENTS.md"):
        try:
            text = (cfg["root"] / doc).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # silent-ok: a MISSING instruction file names no live work (an existing unreadable one raises below)
        except (OSError, ValueError) as exc:
            raise LiveStateUnknown("instruction file {} exists but cannot be read ({})".format(doc, exc)) from exc
        m =re.search(r"^## Live state.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
        if m:                                   # entry lines only: the block's own instructions name example paths
            entries = "\n".join(ln for ln in m.group(1).splitlines() if ln.lstrip().startswith(("- ", "* ")))
            toks |= {t.replace("\\", "/").rstrip("/").lower() for t in re.findall(r"`([^`\n]+)`", entries)}
    return toks


def job_cwds(cfg) -> tuple:
    """([folder keys], note) from the configured job ledger. An optional convenience of one machine's tooling, not the
    toolkit's record of what is live, so a ledger that cannot be read does not stop archiving: the note says the check
    did not run. Every other hold on a folder (read veto, pins, Live state, writes) is independent of it."""
    value = cfg.get("job_ledger") or ""
    if not value:
        return [], ""
    p = _abs(cfg, os.path.expanduser(str(value)))
    try:
        cwds = [e["cwd"] for e in json.loads(p.read_text(encoding="utf-8")).get("entries", []) if e.get("cwd")]
        if not all(isinstance(c, str) for c in cwds):
            raise TypeError("an entry's cwd is not a string")
        return [_key(c) for c in cwds], ""
    except FileNotFoundError:
        return [], ""  # silent-ok: no ledger written yet = no jobs known
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        return [], "job ledger {} could not be read ({}); running jobs were not checked".format(p, type(exc).__name__)


def is_done(task: Path, idle_days: float, tokens: set, cwds: list, guard) -> str:
    """'' when `task` is finished work; otherwise why it is still in use or must stay. idle_days is measured by the
    caller. Written-recently is the weakest signal (a read leaves no mtime), so every other gate is independent."""
    if (task / KEEP_PIN).exists():
        return "pinned (.housekeep-keep)"
    name = task.name.lower()
    path = str(task).replace("\\", "/").lower()
    if any(t == name or path.endswith("/" + t) or t.endswith("/" + name) or (len(t) > 8 and t in path)
           for t in tokens):
        return "named in the Live state block"
    k = _key(task)
    if any(c == k or c.startswith(k + os.sep) for c in cwds):
        return "a running job works here"
    # The Forever class, judged from the task down: a source root the operator configured may itself be called
    # "backups" (archive_roots), but a backup folder inside a task, or a task named for one, is never archived.
    if "backup" in name:
        return "holds backup folder"
    for dp, dirs, files in os.walk(task):
        if ".git" in dirs or ".git" in files:
            return "a git repository"
        if any("backup" in d.lower() for d in dirs):
            return "holds backup folder"
        for f in files:
            if fnmatch.fnmatch(f.lower(), 'owned-session*.json') or fnmatch.fnmatch(f.lower(), '*ownership*.json'):
                # Legacy receipts have no common release schema. A dead/reused PID, age or an
                # unqualified `released` field cannot prove that the owner's work is finished.
                return "holds an ownership receipt without qualified release proof"
            if is_save(str(Path(dp)/f).replace(chr(92),"/").lower()) or f.lower().endswith((".ess",".skse")):
                return "holds a save"
            why = guard.why_protected(os.path.join(dp, f))
            if why in {"save","known-good baseline","audit record","protected_globs"}:
                return "holds "+why
    return ""


def archive_facts(folder):
    """One bounded-memory metadata pass for fingerprint and write/read activity."""
    n = size = 0
    wrote = read = 0.0
    for p in walk_files(folder):
        st = p.stat()
        n += 1
        size += st.st_size
        wrote, read = max(wrote, st.st_mtime), max(read, st.st_atime)
    return [n, size, round(wrote, 3)], wrote, read


def _kept_extra(guard, folder) -> list:
    """Files under `folder` that archiving leaves in place wherever they sit: git-tracked, or linked from a project doc."""
    k = _key(folder) + os.sep
    return [x for x in guard.tracked | guard.links if x.startswith(k)]


def stub_state(folder: Path, guard) -> str:
    """'' when an archived folder holds only its stub and what archiving keeps (`_kept_extra` included, the set archiving
    itself used). Otherwise what else is there, in words. Such a folder is never archived again, since a second archive
    of it can only fail: what is left is a recycle that did not finish, or work done since, and which it is decides
    whether it is also in the zip. By name: the names are in the stub's record; hashing every leftover at every plan
    would cost more than a person comparing the one folder this names."""
    ha = _archive_module()
    kept = ha.keep_local(folder, _kept_extra(guard, folder))
    rest = [f for f in walk_files(folder) if os.path.normcase(str(f)) not in kept
            and f != folder / ha.STUB and f != folder / CLOSED_MARK]
    if not rest:
        return ""
    try:
        text = (folder / ha.STUB).read_text(encoding="utf-8")
        named = set(json.loads(text.rsplit("<!-- ", 1)[1].split(" -->", 1)[0])["source_hashes"])
    except (OSError, ValueError, IndexError, KeyError, TypeError):
        return "{}: has an {} but its record cannot be read, and {} other item(s) are here: nothing shows any of them " \
               "is in a zip; kept, not archived again".format(folder, ha.STUB, len(rest))
    new = [f for f in rest if f.relative_to(folder).as_posix() not in named]
    return ("{}: archived, but {} item(s) are still here: {} named in its zip (a recycle that did not finish, or edited "
            "since: compare before removing them), {} NOT in it (possibly the only copy{}); kept, not archived again"
            ).format(folder, len(rest), len(rest) - len(new), len(new),
                     ", e.g. {}".format(new[0].relative_to(folder).as_posix()) if new else "")


def archive_selection(cfg, guard, tasks, now, held=None, inventory=None) -> tuple:
    """([action], rows) for every done folder due: closed first, then by age; over budget, down to min_idle_days."""
    held = [] if held is None else held
    cwds, ledger_note = job_cwds(cfg)
    try:
        tokens, unknown = live_state_tokens(cfg), ""
    except LiveStateUnknown as exc:        # unknown is not idle: every folder stays this run, and the reason is reported
        tokens, unknown = set(), str(exc)
        held.append({"path": "(live-work state)", "reason": unknown, "bytes": None, "scope": "archive"})
    inventory = {} if inventory is None else inventory
    inventory.update(roots=[str(_abs(cfg, r)) for r in cfg["archive_roots"]], roots_held=0,
                     candidates_inspected=0, candidates_held=0, job_ledger_note=ledger_note, stub_notes=[])
    # from_root: the folder lives under archive_roots, which the file-level loop never scans, so its kept bytes are
    # counted here; agent_output folders that stay are counted by that loop instead (rows must sum to the scan).
    cands = [(t, cfg["archive_after_days"], False) for t, s in tasks if s != "loose"]
    for root, days in cfg["archive_roots"].items():
        r = _abs(cfg, root)
        try:
            st = r.lstat()
            if r.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                raise OSError("archive root is a symbolic link or reparse point")
            if not r.is_dir():
                raise OSError("archive root is not a directory")
            cands += [(Path(e.path), days, True) for e in os.scandir(r) if e.is_dir(follow_symlinks=False)]
        except OSError as exc:
            inventory["roots_held"] += 1
            held.append({"path": str(r), "reason": str(exc), "bytes": None, "scope": "archive"})
    inventory["candidates_requested"] = len(cands)
    info, total = [], 0
    actions, rows, pins = [], {}, []

    def keep(row, n, from_root):
        if from_root:
            rows[row] = rows.get(row, 0) + n

    for t, days, from_root in cands:
        try:
            fp, wrote, read = archive_facts(t)
        except OSError as exc:
            inventory["candidates_held"] += 1
            held.append({"path": str(t), "reason": str(exc), "bytes": None, "scope": "archive"})
            continue
        inventory["candidates_inspected"] += 1
        total += fp[1]
        # Before any other hold: the note is about what a pin costs, whatever else keeps the folder this run.
        if (t / KEEP_PIN).exists() and fp[1] >= cfg["pin_warn_gib"] * GIB:
            pins.append((t, fp[1]))
        if (t / "ARCHIVED.md").exists():
            try:
                state = stub_state(t, guard)
            except OSError as exc:
                inventory["candidates_held"] += 1
                held.append({"path": str(t), "reason": str(exc), "bytes": None, "scope": "archive"})
                continue
            if state:
                inventory["stub_notes"].append(state)
            keep("kept: archived, other items still here" if state else "kept: already archived (stub only)",
                 fp[1], from_root)
            continue
        idle = (now - wrote) / DAY if wrote else 0.0           # idleness: writes
        if read > now - cfg["read_veto_hours"] * 3600:         # but anything reading here right now blocks it
            keep("kept: archive folder read within read_veto_hours", fp[1], from_root)
            continue
        closed = (t / CLOSED_MARK).exists()
        if not closed and not reads_recorded(t.parent):        # no read signal on this drive: only explicit "done"
            keep("kept: archive needs .housekeep-closed (this drive does not record reads)", fp[1], from_root)
            continue
        info.append((not closed, -idle, t, days, fp, idle, closed, from_root))
    info.sort(key=lambda x: (x[0], x[1]))
    measured = total
    over = cfg["budget_gib"] and total > cfg["budget_gib"] * GIB
    for _, _, t, days, fp, idle, closed, from_root in info:
        # The budget lowers the bar for agent_output only; an archive_roots entry's own age is the user's number.
        due = closed or idle >= days or (over and not from_root and idle >= cfg["min_idle_days"])
        if not due or idle < cfg["min_idle_days"] and not closed:
            keep("kept: archive folder not due yet", fp[1], from_root)
            continue
        why = ("live-work state unknown: " + unknown) if unknown else is_done(t, idle, tokens, cwds, guard)
        if why:
            keep("kept: archive folder not done ({})".format(why), fp[1], from_root)
            continue
        label = re.sub(r"[^\w.-]+", "_", t.parent.name) or "root"
        action = {"class": "archive", "path": str(t), "bytes": fp[1], "fingerprint": list(fp),
                  "nas": str(Path(cfg["archive_root"]) / label), "idle_days": round(idle, 1),
                  "closed": closed}
        why = _archive_module().archive_scope(action, cfg)
        if why:
            keep("kept: archive scope ("+why+")",fp[1],from_root)
            continue
        actions.append(action)
        if over:
            total -= fp[1]
            over = total > cfg["budget_gib"] * GIB
    return actions, rows, pins, measured


def archive_protection(guard, path):
    """A source-group label 'backups' is not an eternal veto on an explicitly obsolete ZIP.
    Every independent save, baseline, tracked, linked and custom protection still applies."""
    why = guard.why_protected(path)
    if why != 'backup folder':
        return why
    k = _key(path)
    if k in guard.tracked:
        return 'git-tracked'
    if k in guard.links:
        return 'linked evidence'
    if any(fnmatch.fnmatch(str(path).replace('\\','/').lower(), g) for g in guard.globs):
        return 'protected_globs'
    return ''


def review_archives(cfg, guard=None):
    guard = Guard(cfg) if guard is None else guard
    root = Path(cfg['archive_root'] or Path(cfg['root'])/'.claude/archive')
    if not root.is_absolute():
        root = Path(cfg['root'])/root
    try:
        root.lstat()
        certainty.plain_path(root)
        if not root.is_relative_to(Path(cfg['root'])):
            guard.tracked.update(tracked_files(root))
    except FileNotFoundError as exc:
        if not str(root).startswith(('\\\\','//')):
            return dict(state='absent',root=str(root),rows=[],held=[])
        return dict(state='partial',root=str(root),rows=[],held=[dict(path=str(root),reason=str(exc))])
    except (OSError, ValueError) as exc:
        return dict(state='partial',root=str(root),rows=[],held=[dict(path=str(root),reason=str(exc))])  # silent-ok: reported as partial, with the reason
    return _archive_module().review_archives(dict(cfg,archive_root=str(root)),
                                             protect=lambda p:archive_protection(guard,p))


def _archive_module():
    """The shared archive helper, cached only for this process."""
    global _ARCHIVE_MODULE
    if _ARCHIVE_MODULE is None:
        spec = importlib.util.spec_from_file_location("housekeep_archive", Path(__file__).with_name("housekeep_archive.py"))
        _ARCHIVE_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_ARCHIVE_MODULE)
    return _ARCHIVE_MODULE


def _is_link(path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def landing_refusal(p, on_a_share="no Recycle Bin") -> str:
    """'' when `p` really lives where it is spelled; otherwise why it is left in place. `on_a_share` is why that matters
    to the caller.

    MEASURED 2026-10-31: the shell's delete destroys a file on a network share silently (rc 0, no dialog), and a SUBST
    drive or a volume with no letter has no bin either, so this refusal is the only protection there. A drive letter
    says nothing about where a path lands once a junction or symlink sits on the way, so every existing component is
    checked and realpath has the last word. Only a case or 8.3 short-name difference may remain (GitHub's RUNNER~1);
    realpath expands those. No answer means keep."""
    s = os.path.abspath(str(p))
    if s.startswith("\\\\"):
        return "left in place: network location ({})".format(on_a_share)
    part = s
    while True:
        try:
            if _is_link(part):
                return "left in place: {} is a link or reparse point; this path lands somewhere else".format(part)
        except (FileNotFoundError, NotADirectoryError):
            pass  # silent-ok: not there (yet); only what exists can redirect, and the shell reports a missing item
        except OSError as exc:
            return "left in place: where this path lands could not be established ({})".format(exc)
        parent = os.path.dirname(part)
        if parent == part:
            break
        part = parent
    try:
        real = os.path.realpath(s)
    except (OSError, ValueError) as exc:
        return "left in place: where this path lands could not be established ({})".format(exc)
    if real.startswith("\\\\"):
        return "left in place: this path really lands on a network location or a volume with no drive letter ({})".format(real)
    if os.path.normcase(os.path.splitdrive(real)[0]) != os.path.normcase(os.path.splitdrive(s)[0]):
        return "left in place: this path really lands on another volume ({})".format(real)
    if _drive_type(s) == 4 or _drive_type(real) == 4:                  # DRIVE_REMOTE: a share has no Recycle Bin
        return "left in place: network location ({})".format(on_a_share)
    return ""


def bin_refusal(p) -> str:
    """'' when the Recycle Bin can take `p` without asking; otherwise why it would prompt or destroy it (left alone)."""
    why = landing_refusal(p)
    if why:
        return why
    s = os.path.abspath(str(p))
    longest = len(s)
    size = 0
    try:
        if os.path.isdir(s):
            def failed(error):
                raise error
            for dp, dirs, fn in os.walk(s, onerror=failed):
                for name in dirs + fn:
                    q = os.path.join(dp, name)
                    longest = max(longest, len(q))
                    if _is_link(q):
                        return "left in place: {} inside it is a link or reparse point".format(q)
                for f in fn:
                    size += os.stat(os.path.join(dp, f)).st_size
        else:
            size = os.stat(s).st_size
    except FileNotFoundError:
        if os.path.lexists(s):      # something inside vanished or cannot be addressed: its size is not known
            return "left in place: its size could not be measured (a name inside cannot be reached)"
    except OSError as exc:
        return "left in place: its size could not be measured ({})".format(exc)
    if longest > MAX_BIN_PATH:
        return "left in place: a path is longer than {} characters (the bin would ask to delete it)".format(MAX_BIN_PATH)
    cap = bin_capacity(s)
    if cap is None:
        return "left in place: Recycle Bin capacity could not be verified"
    if cap <= 0:
        return "left in place: this volume disables recycling"
    if size > cap:
        return "left in place: {:.1f} GiB is bigger than this drive's Recycle Bin ({:.1f} GiB)".format(
            size / GIB, cap / GIB)
    return ""


def _drive_type(path: str) -> int:
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(path)[0] + "\\")
    except (OSError, AttributeError):
        return 0  # silent-ok: DRIVE_UNKNOWN: bin_capacity() then cannot verify and bin_refusal() refuses


def bin_capacity(path: str):
    """This volume's Recycle Bin size limit in bytes (0 if it deletes immediately); None when it cannot be read.

    Two things outrank the per-volume key. The NoRecycleFiles policy makes every delete on the machine permanent. And
    only a fixed volume has a bin to go to: Windows keeps BitBucket\\Volume\\{guid} for a USB stick it has seen, so the
    key on a removable drive is stale evidence, not proof."""
    DRIVE_FIXED = 3
    try:
        import ctypes
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer") as k:
                    if winreg.QueryValueEx(k, "NoRecycleFiles")[0]:
                        return 0
            except FileNotFoundError:
                continue  # silent-ok: no such policy key or value: this hive sets nothing
        if _drive_type(os.path.splitdrive(path)[0] + "\\") != DRIVE_FIXED:
            return None
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(os.path.splitdrive(path)[0] + "\\", buf, 64):
            return None
        guid = buf.value[buf.value.index("{"):buf.value.index("}") + 1]
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume" + "\\" + guid
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            return _unconfigured_capacity(os.path.splitdrive(path)[0] + "\\")
        with k:
            try:
                if winreg.QueryValueEx(k, "NukeOnDelete")[0]:
                    return 0
            except OSError:
                pass  # silent-ok: no NukeOnDelete value: fall through to MaxCapacity
            return int(winreg.QueryValueEx(k, "MaxCapacity")[0]) * 1024 * 1024
    except (OSError, ValueError, AttributeError, ImportError):
        return None  # silent-ok: None = capacity unverified, which bin_refusal() treats as a refusal


def _unconfigured_capacity(root: str):
    """A volume with no BitBucket registry key has never had its bin configured (a hosted CI runner's D:, a new drive).
    A fixed drive whose bin answers a query may recycle up to a deliberately small assumed limit, far below Windows'
    unpublished default; anything larger stays in place. Other drive types and a failed query stay unverified (None)."""
    DRIVE_FIXED = 3
    if _drive_type(root) != DRIVE_FIXED:
        return None
    try:
        import ctypes
        info_type = certainty._recycle_bin_info_type(ctypes.sizeof(ctypes.c_void_p))
        info = info_type()
        info.cbSize = ctypes.sizeof(info)
        query = ctypes.WinDLL("shell32").SHQueryRecycleBinW
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(info_type)]
        query.restype = ctypes.c_int32
        if query(root, ctypes.byref(info)) != 0:
            return None
        return min(GIB, shutil.disk_usage(root).total // 100)
    except (OSError, ValueError, AttributeError):
        return None  # silent-ok: None = capacity unverified, which bin_refusal() treats as a refusal


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
    cfg = dict(cfg, archive_root=str(_abs(cfg,cfg['archive_root'] or '.claude/archive')))
    now = time.time() if now is None else now
    guard = guard or Guard(cfg)
    actions, rows = [], {}
    wants = (lambda *classes: True) if not only else (lambda *classes: bool(set(classes) & set(only)))

    def count(row, n):
        rows[row] = rows.get(row, 0) + n

    def act(action, row):
        if action['class'] in {'build', 'launch', 'scratch', 'expire'}:
            why = certainty.obsolete_reason(action['path'], cfg, action.get('hash'))
            if why:
                count('kept: '+why, action['bytes'])
                return
        if only and action["class"] not in only:
            count("kept: class not requested (--only)", action["bytes"])
        else:
            actions.append(action)
            count(row, action["bytes"])

    # ---- agent output: recreatable, build, link
    held = []
    all_tasks = task_folders(cfg, held) if wants("recreatable", "build", "link", "archive") else []
    # ---- archive first: a folder going to the NAS whole is not also picked over file by file
    archived = set()
    plan_pins, agent_total = [], None
    archive_inventory = {}
    if cfg["archive_enabled"] and wants("archive"):
        arch, arch_rows, plan_pins, agent_total = archive_selection(cfg, guard, all_tasks, now,
                                                                 held=held, inventory=archive_inventory)
        for k, v in arch_rows.items():
            count(k, v)
        for a in arch:
            act(a, "plan: done folder -> zip to NAS, verify, payload to Recycle Bin")
            archived.add(_key(a["path"]))
    held_keys = [_key(item["path"]) for item in held]
    all_tasks = [(t, s) for t, s in all_tasks
                 if not any(_key(t) == k or _key(t).startswith(k + os.sep) for k in held_keys)]
    tasks = [(t, s) for t, s in all_tasks if _key(t) not in archived] if wants("recreatable", "build", "link") else []
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
                st = f.stat()
                size = st.st_size
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
            if st.st_nlink != 1:
                # Already shared bytes cannot be reclaimed by recycling this
                # name. Do not hash live game inputs or qualify their ownership.
                count("kept: pre-existing hard links (ownership unqualified)", size)
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
        prev_path = None
        for i, e in enumerate(stamps):
            p = Path(e.path)
            files = sorted(walk_files(p))
            why = next((certainty.rolling_protection(f, guard) for f in [p, *files] if certainty.rolling_protection(f, guard)), '')
            if why:
                count("kept: protected ({})".format(why), sum(f.stat().st_size for f in files))
                continue
            sig = certainty.snapshot_signature(p, blake)
            size = sum(s for _, s, _ in sig)
            if newest_mtime(p) > now - cfg["active_hours"] * 3600:
                count("kept: snapshot folder active", size)
                continue
            why = ""  # all rolling-store protections were checked before hashing
            if why:
                count("kept: protected ({})".format(why), size)
            elif sig == prev and i != len(stamps) - 1:
                act({"class": "snapshot", "path": str(p), "bytes": size, "fingerprint": fingerprint(p),
                     "duplicate_of": str(prev_path), "duplicate_signature": certainty.digest(sig)},
                    "plan: snapshot repeating the previous one -> Recycle Bin")
            else:
                count("kept: snapshot with distinct content (or the newest)", size)
                prev = sig
                prev_path = p

    # ---- ordinary archives are reviewed independently of age-based retention settings
    archive_review = review_archives(cfg, guard) if wants('expire') else {}
    for row in archive_review.get('rows', []):
        if row['state']=='review retirement' and archive_review['state']=='complete':
            act({'class':'expire','path':row['path'],'bytes':row['bytes'],'mtime':row['mtime'],
                 'hash':row['content_hash']}, 'plan: archive has exact obsolete decision -> Recycle Bin')
        else:
            count('kept: archive '+row['reason'], row['bytes'])

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

    return {"created": now, "root": str(cfg["root"]), "actions": actions, "rows": rows,
            "large_pins": [[str(p), b] for p,b in plan_pins], "agent_total": agent_total,
            "coverage": {"state": "accounted_with_holds" if held or archive_review.get("state")=="partial" else "complete",
                         "classes_requested": sorted(only or CLASSES),
                         "agent_output_requested": wants("recreatable", "build", "link", "archive"),
                         "agent_output_roots": [str(_abs(cfg, r)) for r in cfg["agent_output"]],
                         "targets_inspected": len(all_tasks), "targets_held": len(held),
                         "archive_inventory": archive_inventory,
                         "archive_review": archive_review,
                         "held": held, "unmeasured_held_bytes": bool(held or archive_review.get("state")=="partial")}}


def print_coverage(doc):
    coverage = doc.get("coverage")
    if coverage is None:
        return  # Legacy receipts do not establish an inventory denominator.
    if not coverage["agent_output_requested"]:
        print("Agent-output inspection was not requested (--only); this is a class-limited plan.")
    print("target inventory: {} inspected, {} held; {}".format(
        coverage["targets_inspected"], coverage["targets_held"], coverage["state"]))
    archive = coverage.get("archive_inventory")
    if archive:
        print("archive inventory: {} candidates inspected, {} candidates held, {} roots held".format(
            archive["candidates_inspected"], archive["candidates_held"], archive["roots_held"]))
        if archive.get("job_ledger_note"):
            print("  NOTE " + archive["job_ledger_note"])
        for note in archive.get("stub_notes", []):
            print("  NOTE " + note)
    for path, size in doc.get("large_pins", []):
        print("pinned with .housekeep-keep: {:.2f} GiB  {}".format(size / GIB, path))
    review = coverage.get("archive_review", {})
    if review:
        print("ordinary archive review: {}; {} ZIP(s) inspected; {} held branch(es); {}".format(
            review["state"],len(review["rows"]),len(review["held"]),review["root"]))
        for item in review["held"]:
            print("  HELD ARCHIVES {} ({})".format(item["path"],item["reason"]))
    if coverage["held"]:
        print("Byte totals exclude held targets; their sizes are unknown and they have no cleanup actions.")
        for item in coverage["held"]:
            print("  HELD {} ({})".format(item["path"], item["reason"]))


def print_rows(rows: dict, title: str):
    total = sum(rows.values())
    print("{}: {:.2f} GiB logical file bytes scanned (hard links included)".format(title, total / GIB))
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
    if os.name != "nt":
        raise RuntimeError("Recycle Bin removal is implemented for Windows only")
    # 2026-10-29: items the bin cannot take made Windows pop "delete permanently?" dialogs on the user's desktop during
    # an unattended run. Those items are never handed to the shell: they stay where they are and are reported.
    refused = {p: bin_refusal(p) for p in paths}
    refused = {p: why for p, why in refused.items() if why}
    paths = [p for p in paths if p not in refused]
    if not paths:
        return refused
    # The shell's own delete, NOT Microsoft.VisualBasic's DeleteDirectory(..., 'OnlyErrorDialogs', ...): that UI option
    # suppresses confirmations, and the confirmation it suppresses is the one Windows shows when an item is too big for
    # the bin and would be destroyed instead. FOF_WANTNUKEWARNING (0x4000) brings exactly that prompt back while
    # FOF_NOCONFIRMATION (0x10) still skips "move to the Recycle Bin?". FOF_ALLOWUNDO (0x40) is the Recycle Bin.
    # For a local item too big for its bin, the user's answer decides; "No" leaves the item in place.
    # NOERRORUI suppresses error dialogs. WANTNUKEWARNING is NOT a guarantee: MEASURED 2026-10-31, a file on a
    # network share was destroyed silently (rc 0, no dialog) with these flags AND without 0x400. bin_refusal() above
    # is the only thing keeping no-bin locations out of this call; never loosen it on the strength of this flag.
    # https://learn.microsoft.com/windows/win32/api/shellapi/ns-shellapi-shfileopstructw
    script = (
        "Add-Type -TypeDefinition @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public static class HkBin {\n"
        " [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]\n"
        " struct Op { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;\n"
        "  public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }\n"
        " [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHFileOperationW(ref Op op);\n"
        " public static string Recycle(string p) {\n"
        "  var op = new Op { wFunc = 3, pFrom = p + \"\\0\\0\", fFlags = 0x4000 | 0x40 | 0x10 | 0x200 | 0x4 | 0x400 };\n"
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
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], input="\n".join(paths),
                             capture_output=True, text=True, encoding="utf-8", timeout=3600, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        # Unknown outcome per path: report each one by what is on disk now, never as recycled.
        why = "recycle call failed ({}: {})".format(type(exc).__name__, exc)
        result = {p: (why if os.path.exists(p) else why + "; path is gone") for p in paths}
        result.update(refused)
        return result
    silent = "no result reported" + (" (powershell: {})".format(out.stderr.strip()[:300]) if out.stderr.strip() else "")
    result = {p: silent for p in paths}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] in result:
            result[parts[1]] = None if parts[0] == "OK" else (parts[2] if len(parts) > 2 else "error")
    for p in paths:                     # the bin's word is not the proof: the path must actually be gone
        if result[p] is None and os.path.exists(p):
            result[p] = "still present after a reported recycle"
    result.update(refused)
    return result


def _set_readonly(path):
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


class _Memo:
    """Per-apply caches: a live master is hashed once, not once per copy of it; a task folder is walked once."""

    def __init__(self, own=None):
        self.hashes, self.newest = {}, {}
        self.links = {}  # only canonical links successfully created by THIS apply
        self.own = set() if own is None else own  # folders THIS apply changed (newest_mtime skips their own stamp)

    def hash(self, path):
        st = os.stat(path)
        k = (_key(path), st.st_size, st.st_mtime_ns)
        if k not in self.hashes:
            self.hashes[k] = blake(path)
        return self.hashes[k]

    def task_newest(self, task):
        k = _key(task)
        if k not in self.newest:
            self.newest[k] = newest_mtime(Path(task), self.own)
        return self.newest[k]


# the config list each class is planned from; apply re-proves containment against the CURRENT config
ACTION_ROOTS = {"recreatable": "agent_output", "build": "agent_output", "link": "agent_output",
                "gzip": "launch_logs", "launch": "launch_logs", "snapshot": "legacy_snapshots", "scratch": "scratch"}


def _under(path, roots) -> bool:
    """True when `path` resolves inside one of `roots` (resolved Path ancestry, so "out-evil" is not under "out")."""
    q = Path(path).resolve()
    return any(q.is_relative_to(Path(r).resolve()) for r in roots)


def check(action, cfg, guard: Guard, now: float, memo: _Memo | None = None) -> str:
    """'' when the action may run; otherwise why it is refused. Everything plan() decided is re-proved here."""
    memo = memo or _Memo()
    p = Path(action["path"])
    if not p.exists():
        return "gone since the plan"
    # Before any work is done, and whichever recycler ends up holding it.
    why = landing_refusal(p, *(["hard links stay on local volumes"] if action["class"] == "link" else []))
    if why:
        return why
    scope = ACTION_ROOTS.get(action["class"])
    if scope and not _under(p, [_abs(cfg, r) for r in cfg[scope]]):
        return "path is outside the configured {}; create a new plan".format(scope)
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
        if not _under(action["live"], [_abs(cfg, r) for r in cfg["live_roots"]]):
            return "live file is outside the configured live roots; create a new plan"
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
    if action["class"] == "archive":
        why = _archive_module().archive_scope(action, cfg)
        if why:
            return why
        if list(fingerprint(p)) != list(action["fingerprint"]):
            return "folder changed since the plan"
        wrote, read = activity(p)
        if read > now - cfg["read_veto_hours"] * 3600:
            return "something read here since the plan"
        if (now - wrote) / DAY < cfg["min_idle_days"] and not (p/CLOSED_MARK).is_file():
            return "something wrote here since the plan"
        try:
            why = is_done(p, action["idle_days"], live_state_tokens(cfg), job_cwds(cfg)[0], guard)
        except LiveStateUnknown as exc:
            return "live-work state unknown, archiving blocked this run ({})".format(exc)
        if why:
            return "no longer done ({})".format(why)
        if not cfg["archive_enabled"]:
            return "archiving is off"
        return ""
    if action["class"] == "expire":
        root = Path(cfg['archive_root'] or Path(cfg['root'])/'.claude/archive')
        certainty.plain_path(p)
        if not p.resolve().is_relative_to(root.resolve()) or p.suffix.lower()!='.zip':
            return 'archive is outside configured archive root'
        if not root.is_relative_to(Path(cfg['root'])):
            guard.tracked.update(tracked_files(root))
        why = archive_protection(guard, p)
        if why:
            return 'protected ('+why+')'
        st = p.stat()
        if st.st_nlink!=1 or st.st_mtime != action["mtime"] or st.st_size != action["bytes"]:
            return "archive zip changed since the plan"
        actual = _archive_module()._retirement_hash(p,time.monotonic()+cfg['archive_review_seconds'])
        if actual!=action.get('hash'):
            return 'archive bytes changed since the plan; create a new plan'
        return certainty.obsolete_reason(p, cfg, actual)
    if action["class"] in ("launch", "snapshot", "scratch"):
        why = "" if action['class'] == 'snapshot' else protected_inside(guard, p)
        if why:
            return "protected ({})".format(why)
        if fingerprint(p) != action["fingerprint"]:
            return "folder changed since the plan"
        if newest_mtime(p) > now - cfg["active_hours"] * 3600:
            return "folder is active"
    if action['class'] == 'snapshot':
        try:
            return certainty.snapshot_refusal(action, cfg, guard, blake)
        except (OSError, ValueError) as exc:
            return 'snapshot retained-copy proof failed: '+str(exc)
    if action['class'] in {'build', 'launch', 'scratch', 'expire'}:
        return certainty.obsolete_reason(p, cfg, action.get('hash'))
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
    if d.hexdigest() != action["hash"] or blake(src) != action["hash"]:
        recycle([gz])                                             # our own bad output -- still the bin, never a delete
        raise OSError("gzip round trip does not reproduce the original")
    st = src.stat()
    os.utime(gz, (st.st_atime, st.st_mtime))
    return gz


def _expected_refusal(exc) -> bool:
    """A failure that means "leave it and say so" rather than a bug: the helper modules load under their own names, so
    their ArchiveError is matched by name (a test may hold a second copy of the module)."""
    return isinstance(exc, (OSError, ValueError)) or type(exc).__name__ == "ArchiveError"


def apply(plan_doc: dict, cfg, only=None, recycler=None, now: float | None = None, deadline: float | None = None,
          summary: bool = False):
    """Do exactly the plan, re-checking each action. `deadline` (epoch): no new action starts after it; the rest is
    counted as deferred for the next run. summary=True returns {"gib", "failed", "deferred"} instead of an exit code.
    Archive payloads go to the bin in batches at the end of the class, not one shell call per folder."""
    recycler = recycle if recycler is None else recycler
    if any(a['class'] in {'archive','expire'} and (not only or a['class'] in only) for a in plan_doc['actions']):
        if cfg.get('approved_sha256') != cleanup_code_sha256():
            print('REFUSED: archive cleanup version has not been approved after first-output review: '+cleanup_code_sha256())
            return {'gib':0,'failed':1,'deferred':0} if summary else 1
    now = time.time() if now is None else now
    guard = Guard(cfg)
    memo = _Memo()
    order = [c for c in CLASSES if not only or c in only]
    failed = deferred = 0
    totals = {}
    for cls in order:
        acts = [a for a in plan_doc["actions"] if a["class"] == cls]
        if not acts:
            continue
        to_recycle, done = [], 0
        archived = []                       # (action, record) awaiting the batched recycle of their payload
        if cls == "archive":                # read again now: the plan's note may be days old, and this run acts on it
            ledger_note = job_cwds(cfg)[1]
            if ledger_note:
                print("  NOTE " + ledger_note)
        for a in acts:
            if deadline is not None and time.time() > deadline:
                deferred += 1
                continue
            try:
                why = check(a, cfg, guard, now, memo)
            except (OSError, ValueError, TimeoutError) as exc:
                why = 'candidate could not be rechecked: '+str(exc)
            except Exception as exc:
                if type(exc).__name__ != 'ArchiveError':
                    raise
                why = 'archive proof failed: '+str(exc)
            if why:
                print("  REFUSED  {:<11} {}  ({})".format(cls, a["path"], why))
                failed += 1
                continue
            try:
                if cls == "link":
                    canonical_key = _key(a['canonical'])
                    link(a, 1 + memo.links.get(canonical_key, 0))
                    memo.links[canonical_key] = memo.links.get(canonical_key, 0) + 1
                    memo.own.add(_key(Path(a["path"]).parent))
                    done += a["bytes"]
                elif cls == "gzip":
                    compress(a)
                    memo.own.add(_key(Path(a["path"]).parent))
                    to_recycle.append(a)
                elif cls == "archive":
                    ha = _archive_module()
                    archived.append((a, ha.archive_folder(Path(a["path"]), Path(a["nas"]), None,
                                                          _kept_extra(guard, a["path"]))))
                else:
                    to_recycle.append(a)
            except Exception as e:          # OSError, or the archive module's ArchiveError: local folder untouched
                if not isinstance(e, OSError) and type(e).__name__ != "ArchiveError":
                    raise
                print("  FAILED   {:<11} {}  ({})".format(cls, a["path"], e))
                failed += 1
        if archived:
            ha = _archive_module()
            ready_archives = []
            for a, rec in archived:
                try:
                    ha.verify_source(Path(a['path']), rec)
                    ready_archives.append((a, rec))
                except (OSError, ValueError, ha.ArchiveError) as exc:
                    print('  REFUSED  archive {} ({})'.format(a['path'], exc))
                    failed += 1
            archived = ready_archives
            victims = [str(v) for _, rec in archived for v in rec["victims"]]
            res = {}
            for i in range(0, len(victims), RECYCLE_BATCH):
                res.update(recycler(victims[i:i + RECYCLE_BATCH]))
            for a, rec in archived:
                left = {v: res.get(str(v), "no result reported") for v in rec["victims"]}
                left = {str(v): e for v, e in left.items() if e}
                if left:
                    ha.note_leftovers(Path(a["path"]), left)
                    print("  PARTIAL  {:<11} {}  ({} item(s) left in place: {}; zip is complete: {})".format(
                        cls, a["path"], len(left), next(iter(left.values())), rec["zip"]))
                    failed += 1
                else:
                    done += a["bytes"]
        if to_recycle:
            ready = []
            for a in to_recycle:
                try:
                    if a['class'] == 'gzip':
                        d = hashlib.blake2b(digest_size=16)
                        with gzip.open(a['path']+'.gz', 'rb') as stream:
                            for block in iter(lambda:stream.read(1<<20), b''):
                                d.update(block)
                        why = '' if blake(Path(a['path'])) == a['hash'] == d.hexdigest() else 'gzip replacement/source changed'
                    else:
                        why = check(a, cfg, guard, time.time(), _Memo(memo.own))
                    if a['class'] == 'snapshot' and any(
                            other['path'] == a.get('duplicate_of') for other in plan_doc['actions']):
                        why = 'retained snapshot is also scheduled for removal'
                except Exception as exc:
                    if not _expected_refusal(exc):
                        raise
                    why = 'final replacement proof failed: '+str(exc)
                if why:
                    print('  REFUSED  {}  ({})'.format(a['path'], why))
                    failed += 1
                else:
                    ready.append(a)
            to_recycle = ready
            paths = [a["path"] for a in to_recycle]
            res = {}
            for i in range(0, len(paths), RECYCLE_BATCH):
                res.update(recycler(paths[i:i + RECYCLE_BATCH]))
            for a in to_recycle:
                err = res.get(a["path"], "no result reported")
                if err:
                    print("  FAILED   {:<11} {}  (Recycle Bin: {})".format(cls, a["path"], err))
                    failed += 1
                else:
                    memo.own.add(_key(Path(a["path"]).parent))
                    done += a["bytes"]
        totals[cls] = done
        print("  {:<11} {:>4} action(s), {:.2f} GiB done".format(cls, len(acts), done / GIB))
    print("applied: {:.2f} GiB; {} refused/failed; {} deferred (deadline)".format(
        sum(totals.values()) / GIB, failed, deferred))
    if summary:
        return {"gib": sum(totals.values()) / GIB, "failed": failed, "deferred": deferred}
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
            "kept: disposability unverified": 0,
            "kept: ownership unverified": 0, "kept: protected": 0, "kept: no Recycle Bin on this OS": 0,
            "failed": 0}
    ready = []
    failed = []
    try:
        candidates = list(temp_candidates(cfg, temp))
        guard = Guard(cfg)
    except OSError as exc:
        rows["failed"] = 1
        return {"rows": rows, "failed": [str(exc)], "temp": str(temp), "preflight_error": str(exc)}
    for p, _why in candidates:
        try:
            explicit = not certainty.obsolete_reason(p, cfg)
            if not owns_temp(p, cfg["root"]) and not explicit:
                rows["kept: ownership unverified"] += 1
                continue
            if not certainty.disposable_temp(p, cfg['root']) and not explicit:
                rows['kept: disposability unverified'] += 1
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
                             text=True, timeout=30, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("could not count worktrees") from exc
    if out.returncode:
        if "not a git repository" in out.stderr:
            return None
        raise OSError("git worktree inventory failed (exit {})".format(out.returncode))
    return max(0, sum(1 for line in out.stdout.splitlines() if line.startswith("worktree ")) - 1)


NOTE_MAX = 4000   # the harness files hook output past ~10,000 characters and shows the model only the head


def format_note(notes) -> str:
    """The session-start line: the instruction FIRST, then the findings, cut (and saying by how much) at NOTE_MAX."""
    if not notes:
        return ""
    head = "[x4 disk] run `python scripts/x4housekeep.py report` -- "
    body = "; ".join(notes)
    room = NOTE_MAX - len(head) - 1
    if len(body) > room:
        keep = room - 60
        body = body[:keep] + "... [{} more characters cut; the report has them]".format(len(body) - keep)
    return head + body + "\n"


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
    try:
        housekeep_backups = certainty
        note = housekeep_backups.periodic_note(cfg)
        if note:
            notes.append(note)
    except (OSError, ValueError, KeyError, TypeError) as exc:     # a malformed vault record is KeyError/TypeError
        notes.append("cold backup review unavailable: {}: {}".format(type(exc).__name__, exc))
    try:
        note = _archive_module().periodic_note(cfg, lambda:review_archives(cfg))
        if note:
            notes.append(note)
    except (OSError, ValueError) as exc:
        notes.append('task archive review unavailable: {}'.format(exc))
    r = res["rows"]
    if res.get("preflight_error"):
        notes.append("temp cleanup skipped; no recycling attempted")
    elif r["recycled"] or r["failed"]:
        notes.append("moved {} leftover temp dir(s) to the Recycle Bin{}".format(
            r["recycled"], "; {} check/recycling failure(s); affected items LEFT IN PLACE".format(
                r["failed"]) if r["failed"] else ""))
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
    except OSError as exc:
        notes.append("worktree inventory unavailable: {}".format(exc))
        r["failed"] += 1
    floor = cfg["free_floor_gib"]
    try:
        free = shutil.disk_usage(str(cfg["root"])).free / GIB
        if floor and free - floor < 20:
            notes.append("{:.0f} GiB free, floor {} GiB".format(free, floor))
    except OSError:
        notes.append("disk inventory unavailable")
        r["failed"] += 1
    bin_note = certainty.recycle_bin_note(cfg)
    if bin_note:
        notes.append(bin_note)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_bytes(format_note(notes).encode("utf-8"))
    return 1 if r["failed"] else 0


# --------------------------------------------------------------------------- report

def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in walk_files(p) if f.exists()) if p.exists() else 0


def dir_size_counted(p: Path):
    """REPORT-ONLY size: (bytes, unaddressable paths, links not followed). Never for anything that deletes.

    Links and reparse points are NOT descended (their bytes live elsewhere) and unaddressable names are
    skipped; both are RETURNED so the caller can say the total is a lower bound instead of the whole.

    The size comes from the directory listing, with no stat per entry: this walks every agent's scratchpad (MEASURED
    2026-11-03, 200,847 objects, warm cache: the whole report took 32.9 s with two stats per entry, 18.4 s without). On
    Windows that size can lag for a file open right now, which a report can bear."""
    if not p.exists():
        return 0, [], []
    total, skipped, links = 0, [], []
    stack = [p]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for e in entries:
                if os.name == "nt" and e.name.endswith((".", " ")):
                    skipped.append(Path(e.path))   # Win32 strips that character, so the path reaches another name
                    continue
                try:
                    info = e.stat(follow_symlinks=False)
                except FileNotFoundError:
                    skipped.append(Path(e.path))
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    links.append(Path(e.path))
                elif stat.S_ISDIR(info.st_mode):
                    stack.append(e.path)
                else:
                    total += info.st_size
    return total, skipped, links


def _size_line(p: Path):
    """'N.NN GiB' for a report row, or an UNMEASURED/lower-bound statement. Never a bare 0 on failure."""
    try:
        size, skipped, links = dir_size_counted(p)
    except OSError as exc:
        return "UNMEASURED ({})".format(exc)
    gaps = []
    if skipped:
        gaps.append("{} unaddressable name(s) not counted, e.g. {}".format(len(skipped), skipped[0]))
    if links:
        gaps.append("{} link(s) not followed, e.g. {}".format(len(links), links[0]))
    if gaps:
        return ">= {:.2f} GiB (lower bound: {})".format(size / GIB, "; ".join(gaps))
    return "{:>9.2f} GiB".format(size / GIB)


def user_level(cfg):
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    temp = Path(tempfile.gettempdir())
    rows = [("~/.claude/projects (transcripts)", home / ".claude" / "projects"),
            ("~/.codex/sessions", home / ".codex" / "sessions"),
            ("<temp>/claude (scratchpads)", temp / "claude")]
    print("user level (report only -- these are the agents' own stores):")
    for label, p in rows:
        print("  {}  {}".format(_size_line(p), label))
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
    print_coverage(doc)
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
    try:
        return _main(argv)
    except Exception as exc:
        if not _expected_refusal(exc):
            raise
        print("housekeep: REFUSED: {}. Nothing beyond what is printed above was done.".format(exc), file=sys.stderr)
        return 1


def _main(argv) -> int:
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
    if cmd == "version" and not args:
        print(cleanup_code_sha256())
        return 0
    if cmd == "backups":
        housekeep_backups = certainty
        guard = Guard(cfg)
        def protect_backup(path):
            # The opt-in vault preserves backup/baseline bytes before local retirement.
            # Every other protection remains, even when the backup rule matched first.
            k = _key(path)
            if k in guard.tracked or k in guard.links:
                return "tracked/linked evidence"
            if any(fnmatch.fnmatch(str(path).replace('\\', '/').lower(), g) for g in guard.globs):
                return "protected_globs"
            why = guard.why_protected(path)
            return "" if why in {"backup folder", "known-good baseline"} else why
        return housekeep_backups.main(args, cfg, recycle, protect_backup)
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
        print_coverage(doc)
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



def cleanup_code_sha256():
    """All code that decides ordinary housekeep archive/retirement behavior."""
    d = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in ("x4housekeep.py", "housekeep_archive.py", "housekeep_backups.py"):
        d.update(name.encode()+b"\0"+(here/name).read_bytes())
    return d.hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

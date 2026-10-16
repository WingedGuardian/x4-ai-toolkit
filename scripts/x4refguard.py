#!/usr/bin/env python3
r"""Layer 2 for `reference/`: OS-level accidental-write protection for ordinary
processes, with the privileged exceptions documented below. It reports each
filesystem state as itself, never as "protected" by default.

WHY. `reference/` is the unpacked base game + DLC: ~510,000 files, a 27 GB re-unpack to
replace. Until this tool its only protection was the `.unpacked-and-locked` sentinel
(which the toolkit's own scripts respect) and the Claude hooks (which see only commands
Claude types). An interpreter write -- `python -c "open(p, 'w')"` -- passed both.

WHAT IT APPLIES

  Windows   one inherited deny ACE on the root, for the CURRENT USER'S SID:
                icacls <root> /deny *<SID>:(OI)(CI)(DE,DC,WD,AD)
            = Delete + DeleteSubdirectoriesAndFiles + WriteData + AppendData
            = numeric mask 65606 (EXPECTED_MASK). Never W, S, WDAC, WO or F: `W` carries
            SYNCHRONIZE and denied READS; WDAC made an earlier deny unremovable (see
            x4lock.py, "WHY THERE IS NO ACL HERE").
            MEASURED 2026-10-02 on scratch trees (docs/superpowers/measurements):
            reads 12/12 still work; writes 20/20 and deletes/renames 15/15 blocked;
            removable by the same unelevated user 47/47 with 0 deny entries left;
            ~8 s per 100k files to apply or remove; mtime/size unchanged 100k/100k.
  Linux     BEST EFFORT, NOT DEVICE-TESTED. As root with `chattr`: `chattr -R +i`
            (immutable). Otherwise: `chmod a-w` on every directory and file -- a
            directory without write permission refuses unlink/rename/create inside it,
            a file without it refuses an overwrite.
  macOS     BEST EFFORT, NOT DEVICE-TESTED. `chflags -R uchg` (owner-settable
            immutable flag), else the same chmod fallback.
  other     exit 3 `unsupported` -- the ONLY case that exit 3 means.

`status` names the mechanism found, what it stops, and what it does NOT stop. It never
reports a layer it could not confirm by re-reading the filesystem.

WHAT IT DOES NOT STOP (the honest gaps)
  * Renaming the reference ROOT itself: the PARENT directory grants that (MEASURED on
    Windows; INFERRED on POSIX for chmod). `status` then reports the configured root
    missing, never "protected".
  * The same user deliberately lifting it -- `remove` below, or the raw commands in the
    escape hatch. This stops ACCIDENTS, not intent. Agent harnesses gate `remove` (a
    Claude `ask`, a Codex `.rules` prompt).
  * Windows: an Administrator; changing ACLs/attributes (WRITE_DAC is deliberately not
    denied, so the deny stays removable). POSIX chmod: root, and `chmod u+w` by the owner.
    MEASURED in hosted scratch diagnostic 37844985751: elevated Git Bash with
    SeBackupPrivilege/SeRestorePrivilege available overwrote or deleted the tree
    in three of three probes, even with those rights initially disabled. Removing
    both rights from a disposable child blocked all three; unprotected controls
    still worked. The ACL state does not certify safety against those privileges.

USAGE
    python scripts/x4refguard.py status [--json] [--full] [--toolkit DIR] [--reference DIR]
    python scripts/x4refguard.py apply  [--path P] [--yes] [--toolkit DIR] [--reference DIR]   P must be the configured root
    python scripts/x4refguard.py remove [--path P] [--yes] [--toolkit DIR] [--reference DIR]   the escape hatch, step 1/2

BEFORE CHANGING ANYTHING (B3, install red-team 2026-10-04: an apply ran >2 minutes on a
60 GB tree with no output and no question): apply and remove print the target root and
count the objects under it (a progress line every 50,000), then ASK. `--yes` answers for
you; without it and without a terminal to ask on, they refuse with exit 2. While the OS
call runs, a heartbeat line says it is still working.

WHICH TOOLKIT (B2, same red-team): this script acts for the toolkit it LIVES IN. If
$X4_TOOLKIT names a different one, one line says so, and apply/remove REFUSE (exit 2)
unless --toolkit DIR names the toolkit to act for explicitly. And an EXPORTED X4_REFERENCE
naming a different root from the acting toolkit's config makes apply/remove REFUSE (exit 2),
printing both roots, unless --reference DIR chooses one (FX-B2: `--toolkit B` alone still
acted on another copy's tree through an inherited X4_REFERENCE); status prints one line.

`status` samples: the root, the sentinel, and the first file found depth-first in each
top-level directory -- NOT a census, and it says so on every run. `--full` walks every
object (slow on Windows: one Get-Acl per object) and says how many it walked.

EXIT CODES (the toolkit's contract)
    0  protected, or the action succeeded AND was verified by re-reading
    1  absent or partial, or the action ran but verification disagrees
    2  unconfigured, refused, or the state could not be read
    3  unsupported platform (no mechanism exists here)

ESCAPE HATCH -- how a USER lifts it (never an agent on its own):
    1. python scripts/x4refguard.py remove
    2. if the configured root has changed since:  ... remove --path <old root>
       (accepted only if that path carries THIS tool's exact protection)
    3. if this tool is broken, Windows, from cmd.exe:
           icacls "<root>" /remove:d *<your SID>      (`whoami /user` prints the SID)
       Linux: sudo chattr -R -i "<root>"  /  chmod -R u+w "<root>"
       macOS: chflags -R nouchg "<root>"  /  chmod -R u+w "<root>"
    4. last resort, Windows, elevated:  icacls "<root>" /reset /T /C
       (discards every explicit ACE in the tree and restores inheritance)

TEST SAFETY. When `X4_REFGUARD_SANDBOX` is set, every mutating call (icacls, chattr,
chflags, chmod) first asserts its target is strictly under that directory and raises
SandboxViolation -- deliberately not an Exception subclass, so nothing swallows it --
otherwise. The test suites set it to their tmp dir.

Stdlib only; imports on Python 3.10.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent
sys.path.insert(0, str(_TOOLKIT / "tools" / "x4validate"))

try:
    from x4validate import _paths
except ImportError:                     # pragma: no cover - packaging accident
    _paths = None

#: Delete 0x10000 + DeleteSubdirectoriesAndFiles 0x40 + WriteData 0x2 + AppendData 0x4.
EXPECTED_MASK = 0x10000 | 0x40 | 0x2 | 0x4          # 65606, MEASURED as Get-Acl reports it
ICACLS_SPEC = "(OI)(CI)(DE,DC,WD,AD)"
#: ObjectInherit | ContainerInherit, as Get-Acl reports InheritanceFlags.
_INHERIT_BOTH = 3
SENTINEL = ".unpacked-and-locked"
SANDBOX_ENV = "X4_REFGUARD_SANDBOX"
SUPPORTED = ("windows", "linux", "darwin")

SAMPLE_SCOPE = ("root + sentinel + first file of each top-level dir "
                "(not a census; use --full)")

#: THIS script, as a command that works from ANY directory (FX-B2, delta review: the hints said
#: `python scripts/x4refguard.py`, relative to a cwd that is often the game folder or another
#: toolkit -- where it names a different script or none).
SELF_CMD = 'python "%s"' % Path(__file__).resolve()

ESCAPE_HATCH = """\
To lift it (a USER's step, never an agent's on its own):
  1. %(self)s remove        (shows the folder and a count, then asks; --yes skips)
  2. root moved since?  %(self)s remove --path <old root>
  3. tool broken? Windows, from cmd.exe: icacls "<root>" /remove:d *<your SID>  (whoami /user)
     Linux: sudo chattr -R -i "<root>" or chmod -R u+w "<root>"; macOS: chflags -R nouchg "<root>"
  4. last resort, Windows, elevated: icacls "<root>" /reset /T /C""" % {"self": SELF_CMD}


#: C6 (install red-team): "unconfigured / moved?" said WHAT, never how to fix it.
HOW_TO_CONFIGURE = ("To fix: unpack the game first (bash bin/unpack-reference.sh, which "
                    "writes the tree X4_REFERENCE names), or set X4_REFERENCE in "
                    "x4-paths.env at the toolkit root to where your unpacked tree is "
                    "(`x4validate --paths` shows what is resolved).")

def _bash_for_humans() -> str:
    """The Git Bash a human should type in PowerShell: the toolkit's own resolver's answer,
    else Git for Windows' default location. Never bare `bash` (the WSL stub there)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("x4refguard_gitbash", _HERE / "gitbash.py")
        gb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gb)
        found = gb.find_bash()
    except Exception:                       # noqa: BLE001 - a hint, never a verdict
        found = None
    # CI3 (ubuntu): off Windows PowerShell (pwsh) runs bash by its own path -- `& "/usr/bin/bash"`
    # is the correct line there; Git for Windows' default exists only on Windows.
    if found:
        return found
    return "C:\\Program Files\\Git\\bin\\bash.exe" if os.name == "nt" else "/bin/bash"


def no_sentinel_help(root: Path) -> str:
    """R2-a (second install red-team, 2026-10-04): the refusal named a file and stopped. It now
    says what the sentinel IS and gives both ways out, per shell."""
    native = str(root)
    posix = root.as_posix()
    mark = "Unpacked by hand on a date not recorded; the Steam build id is unknown."
    return (
        "%s has no %s sentinel, so it is not a finished unpack -- protecting it would lock a "
        "possibly broken tree.\n"
        "  WHAT IT IS: the LAST thing bin/unpack-reference.sh writes, after it has counted a "
        "complete unpack (every toolkit release since 1.0 writes it). A tree without it is "
        "unfinished, or was unpacked by hand.\n"
        "  TO RESOLVE, either:\n"
        "    1. unpack it with the toolkit (it writes the sentinel, then applies this protection):\n"
        "         Git Bash / Linux / macOS:  bash bin/unpack-reference.sh\n"
        "         PowerShell:                & \"%s\" bin/unpack-reference.sh\n"
        "    2. or, ONLY if this folder is a COMPLETE unpack you made yourself, mark it:\n"
        "         Git Bash / Linux / macOS:  printf '%%s\\n' '%s' > \"%s/%s\"\n"
        "         PowerShell:                Set-Content -LiteralPath \"%s%s%s\" -Value '%s'\n"
        "       then run:  %s apply"
        % (native, SENTINEL, _bash_for_humans(), mark, posix, SENTINEL,
           native, os.sep, SENTINEL, mark, SELF_CMD))


#: A progress line every this many objects (B3).
PROGRESS_EVERY = 50_000
#: A heartbeat line every this many seconds while one long OS call runs (B3).
HEARTBEAT_S = 10.0


class Refused(Exception):
    """The target is not one this tool may act on. Exit 2."""


class Unresolvable(RuntimeError):
    """The configuration could not be READ -- distinct from "nothing is configured"."""


class SandboxViolation(BaseException):
    """A mutating call aimed outside $X4_REFGUARD_SANDBOX. BaseException on purpose:
    an `except Exception` anywhere on the path must not turn it into a refusal."""


# ------------------------------------------------------------------- seams

def _platform() -> str:
    """'windows' | 'linux' | 'darwin' | anything else. The ONE platform seam: tests steer
    it here instead of patching `os.name`, which pathlib also dispatches on."""
    if os.name == "nt":
        return "windows"
    return sys.platform if sys.platform in ("linux", "darwin") else sys.platform


def _geteuid() -> int:
    fn = getattr(os, "geteuid", None)
    return fn() if fn else -1


def _which(name: str):
    return shutil.which(name)


def _configured_root():
    """The configured reference root, or None when NOTHING configures it.

    Raises Unresolvable when the configuration could not be read at all, exactly like
    x4lock: "could not look" must never be reported as "nothing to protect"."""
    if _paths is None:
        raise Unresolvable(
            "x4validate._paths could not be imported, so the configured reference root "
            "is unknown.")
    return _paths.reference()


def _norm(p) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(p)))


def _is_same_or_ancestor(a, b) -> bool:
    """True when `a` is `b` or contains it."""
    na, nb = _norm(a), _norm(b)
    return nb == na or nb.startswith(na.rstrip("\\/") + os.sep)


def _sandbox_check(target) -> None:
    box = os.environ.get(SANDBOX_ENV)
    if not box:
        return
    t, b = _norm(target), _norm(box)
    if t == b or not t.startswith(b.rstrip("\\/") + os.sep):
        raise SandboxViolation(
            "x4refguard: refusing a mutating call on %s -- it is not strictly under "
            "%s=%s" % (target, SANDBOX_ENV, box))


def _mutate_run(argv: list, target) -> subprocess.CompletedProcess:
    """THE choke point for every icacls/chattr/chflags call that changes anything."""
    _sandbox_check(target)
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          errors="replace")


def _isatty() -> bool:
    """Can we ASK? The seam tests steer (a pytest run has no terminal on stdin)."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _ask(prompt: str) -> str:
    return input(prompt)


def _count(root: Path) -> int:
    """Every directory and file under root (symlinks not followed), announcing progress."""
    n = 0
    for _ in _walk(root):
        n += 1
        if n % PROGRESS_EVERY == 0:
            print("  counting: %d object(s) so far ..." % n, file=sys.stderr, flush=True)
    return n


def _confirm(root: Path, action: str, yes: bool) -> int | None:
    """B3: say WHAT and HOW MUCH before changing anything, then ask. None = proceed; an int
    is the exit code to return (2: not confirmed -- nothing was changed)."""
    print("x4refguard %s -- target: %s" % (action, root), file=sys.stderr, flush=True)
    try:
        n = _count(root)
    except OSError as exc:
        print("REFUSED: could not enumerate the complete target: %s. Nothing was changed." % exc,
              file=sys.stderr)
        return 2
    verb = "protect" if action == "apply" else "lift the protection from"
    print("  %d object(s) (directories + files) under it. This will %s all of them; on "
          "Windows that takes about 8 s per 100,000 objects (MEASURED), longer on a slow "
          "disk." % (n, verb), file=sys.stderr, flush=True)
    if yes:
        return None
    if not _isatty():
        print("REFUSED: not confirmed. This is not an interactive terminal, so there is no one "
              "to ask -- re-run with --yes to confirm. Nothing was changed.", file=sys.stderr)
        return 2
    try:
        answer = _ask("%s %d object(s) under %s? [y/N] " % (verb.capitalize(), n, root))
    except EOFError:
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return None
    print("Not confirmed; nothing was changed.", file=sys.stderr)
    return 2


def _with_heartbeat(label: str, fn):
    """Run fn(), printing `still <label>: N s elapsed` every HEARTBEAT_S seconds, so a long
    OS call (one icacls propagating to ~500,000 objects) is never silent (B3)."""
    import threading
    import time
    done = threading.Event()
    start = time.monotonic()

    def beat():
        while not done.wait(HEARTBEAT_S):
            print("  still %s: %d s elapsed ..." % (label, time.monotonic() - start),
                  file=sys.stderr, flush=True)
    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        done.set()
        t.join(1.0)


def _mutate_chmod(path, mode: int) -> None:
    """THE choke point for every chmod."""
    _sandbox_check(path)
    os.chmod(path, mode)


# -------------------------------------------------------------- resolution

def _dangerous(root: Path):
    """A reason this root must never be protected, or None."""
    if _norm(root) == _norm(root.anchor):
        return "it is a drive/filesystem root"
    home = Path.home()
    if _is_same_or_ancestor(root, home):
        return "it is your home directory or contains it"
    game = None
    if _paths is not None:
        try:
            game = _paths.game_root()
        except Exception:                   # noqa: BLE001 - a broken config is not a pass
            game = None
    if game and _is_same_or_ancestor(root, game):
        return "it is the game root or contains it (%s)" % game
    toolkits = [_TOOLKIT]
    if os.environ.get("X4_TOOLKIT"):
        toolkits.append(Path(os.environ["X4_TOOLKIT"]))
    if _paths is not None and _paths.explicit_toolkit() is not None:
        toolkits.append(_paths.explicit_toolkit())
    for tk in toolkits:
        if _is_same_or_ancestor(root, tk):
            return "it is the toolkit root or contains it (%s)" % tk
    if (root / ".git").exists():
        return "it is a git checkout (.git at its root)"
    return None


def resolve_target(path, action: str) -> Path:
    """The directory `action` may act on, or raise Refused / Unresolvable.

    Order: unsupported platform is decided by the caller (exit 3); then unconfigured;
    then an explicit --path that is not the configured root (remove alone accepts one
    that carries this tool's exact protection); then, for apply, not a directory,
    dangerous roots, and a missing sentinel.
    """
    configured = _configured_root()
    if configured is None:
        raise Refused("no reference root is configured. " + HOW_TO_CONFIGURE)
    if path is not None and _norm(path) != _norm(configured):
        if action == "remove" and Path(path).is_dir() and _carries_our_protection(Path(path)):
            return Path(path).resolve()
        raise Refused("%s is not the configured reference root (%s)" % (path, configured))
    root = Path(configured)
    if not root.is_dir():
        raise Refused("the configured reference root %s is not an existing directory "
                      "(renamed or moved? status cannot vouch for it). %s"
                      % (root, HOW_TO_CONFIGURE))
    root = root.resolve()
    if action == "apply":
        why = _dangerous(root)
        if why:
            raise Refused("refusing to protect %s: %s" % (root, why))
        if not (root / SENTINEL).is_file():
            raise Refused(no_sentinel_help(root))
    return root


def _carries_our_protection(p: Path) -> bool:
    """Does `p` carry exactly what THIS tool applies? Used only to let `remove --path`
    lift a root that is no longer configured, never an arbitrary directory.

    Windows: exactly one explicit deny for the user, of EXPECTED_MASK, inheritable.
    POSIX: an immutable or write-less root that ALSO holds the sentinel -- a read-only
    directory alone is too common a shape to be this tool's signature."""
    try:
        if _platform() == "windows":
            item = _acl([p])[1][0]
            return (_explicit_denies(p, item) == [EXPECTED_MASK]
                    and _ours_in(item))
        if not (p / SENTINEL).is_file():
            return False
        return _posix_mark(p) is not None
    except AclError:
        return False


# ============================================================ Windows mechanism

class AclError(RuntimeError):
    """The ACL could not be READ. Becomes state `error`, never `absent`."""


#: One PowerShell process per batch. Paths arrive on STDIN (UTF-8), never interpolated
#: into the command, and never through an env var (a 32,767-character limit). Output is
#: numeric -- rights masks and SIDs -- so nothing depends on the display language.
_PS_ACL = r"""
$ErrorActionPreference='Stop'
$enc = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $enc
[Console]::InputEncoding = $enc
$raw = [Console]::In.ReadToEnd()
$sidT = [System.Security.Principal.SecurityIdentifier]
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$items = @()
foreach ($p in ($raw -split "`n")) {
  $p = $p.TrimEnd("`r")
  if (-not $p) { continue }
  try {
    $a = Get-Acl -LiteralPath $p
    $rules = @($a.GetAccessRules($true, $true, $sidT) | ForEach-Object {
      [ordered]@{sid=$_.IdentityReference.Value; type=[int]$_.AccessControlType;
        rights=[int64]$_.FileSystemRights; inherited=[bool]$_.IsInherited;
        inh=[int]$_.InheritanceFlags; prop=[int]$_.PropagationFlags} })
    $items += ,([ordered]@{path=$p; owner=$a.GetOwner($sidT).Value; rules=$rules; error=$null})
  } catch {
    $items += ,([ordered]@{path=$p; owner=$null; rules=@(); error=$_.Exception.Message})
  }
}
ConvertTo-Json -Depth 6 -Compress -InputObject ([ordered]@{user=$user; items=$items})
"""
_PS_BATCH = 2000
_USER_SID: str | None = None


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _ps51_env() -> dict:
    """The environment for the Windows PowerShell 5.1 child, WITHOUT the parent's PSModulePath.

    MEASURED 2026-10-03 (CI windows-latest and a local pwsh 7 parent): PowerShell 7 exports a
    PSModulePath that starts with its OWN module folders, and a powershell.exe child inherits
    it, finds PS7's Microsoft.PowerShell.Security first and cannot load it -- so Get-Acl fails
    and every status/apply refused. Unset, powershell.exe rebuilds its own default path
    (MEASURED: Get-Acl then works). Only built-in cmdlets run here, so nothing else is lost.
    A copy: the caller's environment is never modified."""
    env = dict(os.environ)
    for k in [k for k in env if k.upper() == "PSMODULEPATH"]:
        del env[k]
    return env


def _acl(paths) -> tuple[str, list[dict]]:
    """(user SID, one item per path, in order). Raises AclError on ANY doubt."""
    global _USER_SID
    paths = [str(p) for p in paths]
    user, out = None, []
    for i in range(0, max(len(paths), 1), _PS_BATCH):
        chunk = paths[i:i + _PS_BATCH]
        try:
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_ACL],
                input="\n".join(chunk).encode("utf-8"), capture_output=True, env=_ps51_env())
        except OSError as exc:
            raise AclError("powershell.exe could not be started: %s" % exc) from exc
        text = r.stdout.decode("utf-8", errors="replace").strip()
        if r.returncode != 0 or not text:
            raise AclError("Get-Acl failed (rc %d): %s" % (
                r.returncode, r.stderr.decode("utf-8", errors="replace")[:300]))
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AclError("Get-Acl output was not JSON: %r" % text[:200]) from exc
        items = _as_list(data.get("items"))
        if len(items) != len(chunk):
            raise AclError("Get-Acl returned %d item(s) for %d path(s)" % (len(items), len(chunk)))
        for it in items:
            it["rules"] = _as_list(it.get("rules"))
        user = data.get("user") or user
        out.extend(items)
    if not user or not str(user).startswith("S-1-"):
        raise AclError("could not determine the current user's SID (got %r)" % user)
    _USER_SID = user
    return user, out


def _user_sid() -> str:
    if _USER_SID is None:
        _acl([])
    return _USER_SID


def _item(p, item=None) -> dict:
    if item is not None:
        return item
    _, items = _acl([p])
    if items[0].get("error"):
        raise AclError("Get-Acl %s: %s" % (p, items[0]["error"]))
    return items[0]


def _deny_rules(item: dict, explicit_only: bool) -> list[dict]:
    sid = _user_sid()
    return [r for r in item["rules"] if r.get("sid") == sid and r.get("type") == 1
            and (not explicit_only or not r.get("inherited"))]


def _explicit_denies(p, item=None) -> list[int]:
    """Masks of the NON-inherited deny ACEs for the current user on `p`."""
    return [int(r["rights"]) for r in _deny_rules(_item(p, item), explicit_only=True)]


def _deny_masks(p, item=None) -> list[int]:
    """Masks of EVERY deny ACE for the current user on `p`, explicit and inherited."""
    return [int(r["rights"]) for r in _deny_rules(_item(p, item), explicit_only=False)]


def _ours_in(item: dict) -> bool:
    """The exact explicit ACE this tool writes: our mask, OI|CI, no propagation flags."""
    return any(int(r["rights"]) == EXPECTED_MASK and int(r.get("inh", 0)) == _INHERIT_BOTH
               and int(r.get("prop", 0)) == 0
               for r in _deny_rules(item, explicit_only=True))


def _owner_is_user(p) -> bool:
    if _platform() == "windows":
        return _item(p).get("owner") == _user_sid()
    try:
        euid = _geteuid()
        return euid == 0 or os.stat(p).st_uid == euid
    except OSError:
        return False


def _child_ok(item: dict) -> bool:
    """A non-root object is protected when a deny for the user covers the whole mask AND
    no explicit allow on the object itself overrides an inherited deny (canonical ACE
    order puts explicit ACEs first). Conservative: an explicit allow of ANY SID that
    grants a denied bit counts, since group membership is not evaluated here."""
    if item.get("error"):
        return False
    denies = _deny_rules(item, explicit_only=False)
    if not any(int(r["rights"]) & EXPECTED_MASK == EXPECTED_MASK for r in denies):
        return False
    if any(not r.get("inherited") for r in denies
           if int(r["rights"]) & EXPECTED_MASK == EXPECTED_MASK):
        return True
    return not any(r.get("type") == 0 and not r.get("inherited")
                   and int(r["rights"]) & EXPECTED_MASK for r in item["rules"])


_WIN_STOPS = ["delete or rename of any file or directory inside the root",
              "overwrite, truncate or append (WriteData, AppendData)",
              "creating new files or directories inside the root"]
_WIN_GAPS = ["renaming the root itself (its PARENT grants that)",
             "the same user lifting it (WRITE_DAC is deliberately not denied)",
             "an Administrator"]


def _win_report(root: Path, full: bool) -> dict:
    r = _blank("error", root)
    if full:
        r["sample_scope"] = "FULL walk"
    try:
        objs = _objects(root, full)
    except OSError as exc:
        r["detail"] = "could not enumerate reference tree; protection coverage is incomplete: %s" % exc
        return r
    r["enumeration_complete"] = True
    try:
        user, items = _acl(objs)
    except AclError as exc:
        r["detail"] = "could not read the ACL: %s" % exc
        return r
    head = items[0]
    if head.get("error"):
        r["detail"] = "could not read the root's ACL: %s" % head["error"]
        return r
    r["owner_is_user"] = head.get("owner") == user
    explicit = _explicit_denies(root, head)
    r["mask"] = explicit[0] if len(explicit) == 1 else (explicit or None)
    if not explicit:
        r.update(state="absent", sampled=len(items),
                 detail="LAYER 2 OFF: reference/ has no deny-delete")
        return r
    if explicit != [EXPECTED_MASK] or not _ours_in(head):
        r.update(state="foreign", sampled=len(items),
                 detail="a deny ACE for your SID exists on the root that this tool did "
                        "not write (masks %s, expected exactly [%d] with (OI)(CI)); it is "
                        "left alone" % (explicit, EXPECTED_MASK))
        return r
    ok = 1 + sum(1 for it in items[1:] if _child_ok(it))
    r.update(mechanism="icacls-deny %s" % ICACLS_SPEC, sampled=len(items), sample_ok=ok,
             stops=list(_WIN_STOPS), does_not_stop=list(_WIN_GAPS))
    if ok == len(items):
        # R2-d: "N of M sampled" counts the ROOT too, as status's own line does. This said
        # "3 of 3" (children only) where status said "4 of 4" for the same tree.
        r.update(state="protected", detail="deny ACE %d on the root, held by %d of %d "
                 "sampled object(s), root included" % (EXPECTED_MASK, ok, len(items)))
    else:
        bad = [it["path"] for it in items[1:] if not _child_ok(it)]
        r.update(state="partial", detail="the root carries the deny but %d of %d sampled "
                 "object(s) do not (inheritance cut, or an explicit allow), e.g. %s" % (
                     len(bad), len(items), bad[0]))
    return r


def _win_apply(root: Path, yes: bool = False) -> int:
    before = _win_report(root, False)
    if before["state"] in ("error", "foreign"):
        print("REFUSED: %s" % before["detail"], file=sys.stderr)
        return 2
    if not _owner_is_user(root):
        print("REFUSED: %s is not owned by you. A deny on a tree you do not own may not "
              "be removable unelevated (x4lock's lockout history); not applying." % root,
              file=sys.stderr)
        return 2
    if before["state"] == "protected":
        print("already protected: %s (%s)" % (root, before["detail"]))
        return 0
    rc = _confirm(root, "apply", yes)
    if rc is not None:
        return rc
    sid = _user_sid()
    res = _with_heartbeat("applying", lambda: _mutate_run(
        ["icacls", root, "/deny", "*%s:%s" % (sid, ICACLS_SPEC), "/C", "/Q"], root))
    after = _win_report(root, False)
    if after["state"] == "protected":
        print("Layer 2 applied: %s -- %s" % (root, after["detail"]))
        return 0
    print("FAILED VERIFICATION: icacls returned %d but the re-read says %s: %s. The tree "
          "may be PARTIALLY protected.\n  icacls: %s %s" % (
              res.returncode, after["state"], after["detail"], res.stdout.strip()[:300],
              res.stderr.strip()[:300]), file=sys.stderr)
    return 1


def _win_remove(root: Path, yes: bool = False) -> int:
    item = _item(root)
    explicit = _explicit_denies(root, item)
    if explicit and (explicit != [EXPECTED_MASK] or not _ours_in(item)):
        print("REFUSED: %s carries a deny for your SID that this tool did not write "
              "(masks %s). `icacls /remove:d` would remove it too; lift it yourself if "
              "you mean to." % (root, explicit), file=sys.stderr)
        return 2
    if not explicit:
        print("nothing to remove: %s carries no deny for your SID" % root)
        return 0
    rc = _confirm(root, "remove", yes)
    if rc is not None:
        return rc
    sid = _user_sid()
    res = _with_heartbeat("removing", lambda: _mutate_run(
        ["icacls", root, "/remove:d", "*%s" % sid, "/C", "/Q"], root))
    left = _explicit_denies(root)
    if not left:
        print("Layer 2 lifted: %s" % root)
        return 0
    print("FAILED VERIFICATION: icacls returned %d but the root still carries deny "
          "mask(s) %s.\n  icacls: %s %s" % (res.returncode, left, res.stdout.strip()[:300],
                                            res.stderr.strip()[:300]), file=sys.stderr)
    return 1


# ============================================================== POSIX mechanism
#
# BEST EFFORT, NOT DEVICE-TESTED (decision #15). The chmod fallback runs for real on the
# CI ubuntu leg; chattr and chflags are exercised only through fakes.

def _mode(p) -> int:
    return os.lstat(p).st_mode


def _flags(paths) -> dict:
    """{path: True/False/None} -- is the object immutable? None = could not tell."""
    out = {str(p): None for p in paths}
    if _platform() == "darwin":
        for p in paths:
            try:
                out[str(p)] = bool(getattr(os.lstat(p), "st_flags", 0) & stat.UF_IMMUTABLE)
            except OSError:
                pass
        return out
    lsattr = _which("lsattr")
    if not lsattr:
        return out
    paths = [str(p) for p in paths]
    for i in range(0, len(paths), 500):
        chunk = paths[i:i + 500]
        r = subprocess.run([lsattr, "-d", *chunk], capture_output=True, text=True,
                           errors="replace")
        for line in r.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1] in out:
                out[parts[1]] = "i" in parts[0]
    return out


def _readonly(p) -> bool:
    try:
        return (_mode(p) & 0o222) == 0
    except OSError:
        return False


def _posix_mark(root: Path):
    """Which mechanism the ROOT carries: 'immutable', 'readonly', or None."""
    if _flags([root]).get(str(root)):
        return "immutable"
    if _readonly(root):
        return "readonly"
    return None


def _posix_mechanism_name(mark: str) -> str:
    if mark == "immutable":
        return "chflags uchg" if _platform() == "darwin" else "chattr +i"
    return "chmod a-w (dirs and files)"


_POSIX_STOPS = {
    "immutable": ["delete, rename or create inside the root", "overwrite, truncate or append",
                  "a chmod of the protected objects"],
    "readonly": ["delete, rename or create inside the root (directory write bit)",
                 "overwrite or truncate of a file (file write bit)"],
}
_POSIX_GAPS = {
    "immutable": ["renaming the root itself (INFERRED: its parent is writable)",
                  "root (chattr -i / chflags nouchg)", "NOT device-tested (best effort)"],
    "readonly": ["root or any process with CAP_DAC_OVERRIDE",
                 "the owner running chmod u+w", "renaming the root itself (its parent is "
                 "writable)", "NOT device-tested (best effort)"],
}


def _posix_report(root: Path, full: bool) -> dict:
    r = _blank("absent", root)
    if full:
        r["sample_scope"] = "FULL walk"
    try:
        objs = _objects(root, full)
    except OSError as exc:
        r.update(state="error", detail="could not enumerate reference tree; protection coverage is incomplete: %s" % exc)
        return r
    r["enumeration_complete"] = True
    r["owner_is_user"] = _owner_is_user(root)
    mark = _posix_mark(root)
    r["sampled"] = len(objs)
    if mark is None:
        r["detail"] = "LAYER 2 OFF: reference/ is neither immutable nor write-protected"
        return r
    if mark == "immutable":
        flags = _flags(objs)
        ok = sum(1 for p in objs if flags.get(str(p)))
    else:
        ok = sum(1 for p in objs if _readonly(p))
    r.update(mechanism=_posix_mechanism_name(mark), sample_ok=ok,
             stops=list(_POSIX_STOPS[mark]), does_not_stop=list(_POSIX_GAPS[mark]))
    if ok == len(objs):
        r.update(state="protected", detail="%s on %d of %d sampled object(s)" % (
            r["mechanism"], ok, len(objs)))
    else:
        r.update(state="partial", detail="%s on the root but only %d of %d sampled "
                 "object(s)" % (r["mechanism"], ok, len(objs)))
    return r


def _chmod_tree(root: Path, writable: bool) -> int:
    """Clear (or restore the OWNER's) write bits on every dir and file. Symlinks are
    never followed. A nonzero result means failed changes or incomplete traversal."""
    failed = 0
    try:
        for n, p in enumerate(_walk(root), 1):
            if n % PROGRESS_EVERY == 0:
                print("  chmod: %d object(s) done ..." % n, file=sys.stderr, flush=True)
            try:
                m = stat.S_IMODE(_mode(p))
                _mutate_chmod(p, (m | stat.S_IWUSR) if writable else (m & ~0o222))
            except OSError:
                failed += 1
    except OSError as exc:
        print("chmod traversal incomplete; some objects remain unverified: %s" % exc, file=sys.stderr)
        failed += 1  # At least one unverified object; the omitted subtree's size is unknown.
    return failed


def _posix_apply(root: Path, yes: bool = False) -> int:
    before = _posix_report(root, False)
    if not _owner_is_user(root):
        print("REFUSED: %s is not owned by you; not applying." % root, file=sys.stderr)
        return 2
    if before["state"] == "protected":
        print("already protected: %s (%s)" % (root, before["detail"]))
        return 0
    rc = _confirm(root, "apply", yes)
    if rc is not None:
        return rc
    plat, note = _platform(), ""
    if plat == "linux" and _geteuid() == 0 and _which("chattr"):
        res = _with_heartbeat("applying", lambda: _mutate_run([_which("chattr"), "-R", "+i", root], root))
        note = "chattr rc %d %s" % (res.returncode, res.stderr.strip()[:200])
    elif plat == "darwin" and _which("chflags"):
        res = _with_heartbeat("applying", lambda: _mutate_run([_which("chflags"), "-R", "uchg", root], root))
        note = "chflags rc %d %s" % (res.returncode, res.stderr.strip()[:200])
    else:
        failed = _chmod_tree(root, writable=False)
        note = "chmod a-w: %d object(s) could not be changed" % failed
        if failed:
            print("FAILED: chmod was incomplete; at least %d object(s) failed or remain unverified. "
                  "The tree may be partially protected." % failed, file=sys.stderr)
            return 1
        if plat == "linux":
            note += " (chattr +i needs root; using the chmod fallback)"
    after = _posix_report(root, False)
    if after["state"] == "protected":
        print("Layer 2 applied (best effort, not device-tested): %s -- %s" % (
            root, after["detail"]))
        return 0
    print("FAILED VERIFICATION: the re-read says %s: %s. The tree may be PARTIALLY "
          "protected. (%s)" % (after["state"], after["detail"], note), file=sys.stderr)
    return 1


def _posix_remove(root: Path, yes: bool = False) -> int:
    mark = _posix_mark(root)
    if mark is None:
        print("nothing to remove: %s carries no protection this tool recognises" % root)
        return 0
    rc = _confirm(root, "remove", yes)
    if rc is not None:
        return rc
    plat = _platform()
    if mark == "immutable":
        if plat == "linux":
            if _geteuid() != 0 or not _which("chattr"):
                print("REFUSED: %s is immutable (chattr +i) and only root can clear that: "
                      "sudo chattr -R -i \"%s\"" % (root, root), file=sys.stderr)
                return 2
            _mutate_run([_which("chattr"), "-R", "-i", root], root)
        elif _which("chflags"):
            _mutate_run([_which("chflags"), "-R", "nouchg", root], root)
        else:
            print("REFUSED: %s is immutable and chflags is not available" % root,
                  file=sys.stderr)
            return 2
    if _readonly(root) or mark == "readonly":
        if _chmod_tree(root, writable=True):
            print("FAILED: chmod was incomplete; protection may remain on unverified objects.", file=sys.stderr)
            return 1
    left = _posix_mark(root)
    if left is None:
        print("Layer 2 lifted: %s (owner write restored; group/other write bits are not "
              "restored)" % root)
        return 0
    print("FAILED VERIFICATION: %s still reads as %s" % (root, left), file=sys.stderr)
    return 1


# =============================================================== shared walking

def _walk_error(error):
    raise error


def _walk(root: Path):
    """Every directory and file under root, root first, symlinks never followed."""
    yield root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_walk_error):
        dirnames.sort()
        for n in dirnames:
            p = Path(dirpath) / n
            if not p.is_symlink():
                yield p
        for n in sorted(filenames):
            p = Path(dirpath) / n
            if not p.is_symlink():
                yield p


def _first_file(d: Path):
    for dirpath, dirnames, filenames in os.walk(d, followlinks=False, onerror=_walk_error):
        dirnames.sort()
        for n in sorted(filenames):
            return Path(dirpath) / n
    return None


def _objects(root: Path, full: bool) -> list:
    """The SAMPLE (see SAMPLE_SCOPE), or every object with --full. Root first."""
    if full:
        return list(_walk(root))
    out = [root]
    if (root / SENTINEL).is_file():
        out.append(root / SENTINEL)
    with os.scandir(root) as entries:
        tops = [e for e in entries if e.is_dir(follow_symlinks=False)]
    for e in sorted(tops, key=lambda e: e.name):
        f = _first_file(Path(e.path))
        if f is not None:
            out.append(f)
    return out


# ------------------------------------------------------------------ report

def _blank(state: str, root=None, detail: str = "") -> dict:
    return {
        "state": state, "root": str(root) if root is not None else None,
        "platform": _platform(), "mechanism": None,
        "mask": None, "mask_expected": EXPECTED_MASK if _platform() == "windows" else None,
        "sentinel": bool(root is not None and (Path(root) / SENTINEL).is_file()),
        "owner_is_user": None, "sampled": 0, "sample_ok": 0,
        "enumeration_complete": False,
        "sample_scope": SAMPLE_SCOPE, "stops": [], "does_not_stop": [],
        "detail": detail,
    }


def report(full: bool = False, path=None) -> dict:
    """The status object (see `status --json`). Never raises for an expected state."""
    plat = _platform()
    if plat not in SUPPORTED:
        return _blank("unsupported", detail="no protection mechanism exists for platform "
                      "%r; reference/ is NOT OS-protected here (disclosed gap)" % plat)
    try:
        configured = _configured_root()
    except Unresolvable as exc:
        return _blank("error", detail=str(exc))
    if configured is None and path is None:
        return _blank("unconfigured", detail="no reference root is configured. "
                      + HOW_TO_CONFIGURE)
    root = Path(path) if path is not None else Path(configured)
    if not root.is_dir():
        return _blank("unconfigured", root, "the configured reference root %s does not "
                      "exist (not unpacked yet, or renamed or moved). %s"
                      % (root, HOW_TO_CONFIGURE))
    root = root.resolve()
    if plat == "windows":
        return _win_report(root, full)
    return _posix_report(root, full)


_EXIT = {"protected": 0, "absent": 1, "partial": 1, "foreign": 2, "unconfigured": 2,
         "error": 2, "unsupported": 3}


# ---------------------------------------------------------------- commands

#: The words a human reads for a state. The JSON keeps the state itself (a contract the
#: doctor and the installers parse); "absent" read like a missing FILE (R2 cosmetic).
_HUMAN_STATE = {"absent": "not applied"}

#: States with a protection in place that a user may want to lift (R2: the long lift block
#: was printed for "absent" too, where there is nothing to lift).
_LIFTABLE = ("protected", "partial", "foreign")

APPLY_HINT = ("To apply it: %s apply   (shows the folder and a count, "
              "then asks you; --yes answers for you, e.g. when an agent runs it after you agreed)"
              % SELF_CMD)


def _human(r: dict) -> str:
    return "reference deny-delete: %s -- %s" % (_HUMAN_STATE.get(r["state"], r["state"]),
                                                 r["detail"])


def cmd_status(args) -> int:
    r = report(full=args.full, path=getattr(args, "path", None))
    if args.json:
        print(json.dumps(r, sort_keys=True))
    else:
        print(_human(r))
        print("  folder: %s" % (r["root"] or "(none configured)"))
        print("  sample scope: %s; %d of %d sampled object(s) protected, root included" % (
            "FULL walk" if args.full else r["sample_scope"], r["sample_ok"], r["sampled"]))
        if r["state"] == "absent":
            print(APPLY_HINT)
        elif r["state"] in ("partial", "foreign"):
            print(ESCAPE_HATCH)
    return _EXIT.get(r["state"], 2)


def _act(args, action: str) -> int:
    if _platform() not in SUPPORTED:
        print("UNSUPPORTED: no protection mechanism exists for platform %r; nothing was "
              "%s." % (_platform(), "applied" if action == "apply" else "removed"),
              file=sys.stderr)
        return 3
    refusal = _paths.foreign_toolkit_refusal("x4refguard " + action) if _paths is not None else None
    # FX-B2: an INHERITED X4_REFERENCE outranks the acting toolkit's config, so `--toolkit B`
    # alone still protected (or lifted) another copy's tree. A difference refuses, naming
    # both roots and --reference, the flag that chooses one.
    if not refusal and _paths is not None:
        refusal = _paths.env_root_refusal("x4refguard " + action,
                                          {"reference": "--reference"}, "reference")
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    yes = bool(getattr(args, "yes", False))
    try:
        root = resolve_target(args.path, action)
    except (Refused, Unresolvable) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        if action == "remove":          # R2: the lift block answers a remove, not an apply
            print(ESCAPE_HATCH, file=sys.stderr)
        return 2
    try:
        if _platform() == "windows":
            return _win_apply(root, yes) if action == "apply" else _win_remove(root, yes)
        return _posix_apply(root, yes) if action == "apply" else _posix_remove(root, yes)
    except AclError as exc:
        print("ERROR: could not read the ACL, so nothing can be verified: %s" % exc,
              file=sys.stderr)
        return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="x4refguard",
        description="Layer-2 OS protection of the reference/ tree (see the module "
                    "docstring for what it stops, what it does not, and the escape hatch).",
        epilog=ESCAPE_HATCH, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    st = sub.add_parser("status", help="report the protection state")
    st.add_argument("--json", action="store_true")
    st.add_argument("--full", action="store_true", help="walk every object, not a sample")
    ap_ = sub.add_parser("apply", help="protect the configured reference root")
    ap_.add_argument("--path")
    rm = sub.add_parser("remove", help="lift the protection (the escape hatch)")
    rm.add_argument("--path")
    for p in (ap_, rm):
        p.add_argument("--yes", action="store_true",
                       help="confirm without being asked (required when not run in a terminal)")
    for p in (st, ap_, rm):
        p.add_argument("--reference", metavar="DIR",
                       help="act on this reference root, chosen explicitly. Needed for "
                            "apply/remove when an exported X4_REFERENCE and this toolkit's "
                            "config name different roots (both are printed)")
        p.add_argument("--toolkit", metavar="DIR",
                       help="act for this toolkit's configuration. Default: the toolkit this "
                            "script lives in; REQUIRED for apply/remove when $X4_TOOLKIT names "
                            "a different one")
    args = ap.parse_args(argv)
    if getattr(args, "toolkit", None):
        if _paths is None or not Path(args.toolkit).is_dir():
            print("REFUSED: --toolkit %s is not a directory (or the x4validate package could "
                  "not be imported)" % args.toolkit, file=sys.stderr)
            return 2
        _paths.use_toolkit(args.toolkit)
    elif _paths is not None:
        _paths.toolkit_notice()
    if getattr(args, "reference", None):
        if _paths is None:
            print("REFUSED: --reference needs the x4validate package, which could not be "
                  "imported", file=sys.stderr)
            return 2
        _paths.use_root("X4_REFERENCE", args.reference)
    if args.cmd == "status" and _paths is not None:
        _paths.env_root_notice("reference")
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd in ("apply", "remove"):
        return _act(args, args.cmd)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

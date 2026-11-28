"""Guarded update: stage a release for review, then (later) apply it only on the user's explicit go.

STAGE half: `stage_update` fetches the newest release through `release.fetch`, stages it into a review bundle exactly
the way `installation.handle` does for `upgrade-stage`, renders the review for the user and records what apply must
later check in `meta.json`. It changes nothing installed and authorizes nothing.

APPLY half: `apply_update` applies a staged review only with its SHA-256, `quiescent=True` and, when the review holds
guard changes, `accept_guard_changes=True`. It then runs the toolkit's setup and canary; any failure rolls the
operation back and runs the old canary on the restored state. When that fails too it fails closed: it writes
`update-failed.json`, and staging refuses until the user clears it.

Everything that decides what counts as guard code comes from the INSTALLED toolkit (`ReleaseProfile`,
`UpdatePolicy`), never from the downloaded release.

COMMAND LINE (last section): `add_arguments` / `handle` / `exit_code` / `session_note` are what a toolkit wires next to
`release.add_arguments`. Everything above is the library; the section at the end is only the thin surface over it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import installation, release, transport, update_review, upgrade

FAILED_MARKER = ".modding-agent-harness/update-failed.json"
#: Written before files are replaced and removed only once the outcome is final. Left behind (a kill, an interrupt, a
#: tool timeout during setup or canary), it means an unverified guard set may be live: fail closed, like the marker.
IN_FLIGHT = ".modding-agent-harness/update-in-flight.json"
META = "meta.json"
MAX_INVENTORY = 1024 * 1024


class UpdateRefused(ValueError):
    """The update was refused before it changed anything; the message says why."""


@dataclass(frozen=True)
class Staged:
    bundle: Path
    review_sha256: str
    requires_guard_accept: bool
    text: str
    from_version: str
    to_version: str


# --- stage half -------------------------------------------------------------------------------------------------


def _read_json(path):
    """A bounded JSON object, read the way `installation.handle` reads its candidate, previous and render files."""
    with Path(path).open("rb") as stream:
        return transport.load(stream.read(MAX_INVENTORY + 1))


def _present(path):
    return path.exists() or path.is_symlink()


def _read_record(root):
    """The in-flight record's content, or {} when it is absent or unreadable (callers treat unknown fields as such)."""
    try:
        data = _read_json(Path(root) / IN_FLIGHT)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _journals_for(root, review_sha256):
    """Operation ids under upgrades/ whose plan.json records this review and root (unreadable plans are skipped)."""
    found = []
    folder = Path(root) / ".modding-agent-harness" / "upgrades"
    if not isinstance(review_sha256, str) or not folder.is_dir():
        return found
    for path in sorted(folder.iterdir()):
        if re.fullmatch(r"[0-9a-f]{32}", path.name):
            try:
                plan = _read_json(path / "plan.json")
            except (OSError, ValueError):
                continue
            if plan.get("review_sha256") == review_sha256 and plan.get("root") == str(Path(root).resolve()):
                found.append(path.name)
    return found


def _describe_record(root):
    """'in progress or interrupted (pid N, started T)' plus what to roll back, for refusals and the session note."""
    record = _read_record(root)
    who = "pid {}, started {}".format(record.get("pid", "unknown"), record.get("started", "unknown"))
    operation = record.get("operation")
    if isinstance(operation, str) and re.fullmatch(r"[0-9a-f]{32}", operation):
        what = "its operation is {0} (upgrade-rollback {0} --quiescent)".format(operation)
    else:
        try:
            journals = _journals_for(root, record.get("review_sha256"))
        except OSError:
            journals = []
        what = ("it recorded no operation; journal(s) holding its review: {} (upgrade-rollback <that id> --quiescent)"
                .format(", ".join(journals)) if journals else
                "it recorded no operation and no journal holds its review, so I think nothing was published")
    return "in progress or interrupted ({}); {}".format(who, what)


def _refuse_in_flight(root):
    if _present(root / IN_FLIGHT):
        raise UpdateRefused(
            "an update is {} and left {}: an unverified guard set may be live. If that process has ended, read the "
            "file, run the toolkit's canary or roll back the operation, then clear it with update-clear-failed "
            "(--force when the recorded process cannot be checked); nothing was changed".format(
                _describe_record(root), IN_FLIGHT))


_EARLIER_BUNDLE = re.compile(r"bundle(-[0-9a-f]{12})?")   # the review folder names `release.fetch` has used


def _discard_earlier_review(work):
    """Remove every earlier review of this same version (our own rebuildable scratch beside the unpacked release: the
    legacy `bundle` and each `bundle-<12 hex>` a fetch named), so staging again is possible, earlier bundles do not
    pile up, and no meta.json can outlive the bundle it described. Only those exact names go; nothing else in the
    work folder is touched."""
    paths = [entry for entry in work.iterdir() if _EARLIER_BUNDLE.fullmatch(entry.name)]
    paths.append(work / META)
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


def _write_meta(path, meta):
    data = (json.dumps(meta, sort_keys=True, indent=1) + "\n").encode("utf-8")
    part = path.with_name(".{}.{}.part".format(path.name, uuid.uuid4().hex))
    try:
        with part.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def stage_update(root, profile, policy, *, notes_files=(), http=None, candidate_preparer=None, generated_paths=()):
    """Fetch the newest release and stage it for review. Returns `Staged`; installs and authorizes nothing.

    `candidate_preparer` and `generated_paths` are the toolkit's own renderer hooks, exactly as `installation.handle`
    takes them; with a preparer the render configuration `policy.render_config(root)` is read and handed to it.
    Every refusal is an `UpdateRefused`.
    """
    root = Path(root).resolve()
    if (root / FAILED_MARKER).exists() or (root / FAILED_MARKER).is_symlink():
        raise UpdateRefused("an earlier update failed and left {}; read it, fix the cause and clear it before "
                            "staging another update".format(FAILED_MARKER))
    _refuse_in_flight(root)
    try:
        notes = installation.notes_set(notes_files)
        fetched = release.fetch(root, profile, **({} if http is None else {"http": http}))
        work = Path(fetched["bundle"]).parent
        _discard_earlier_review(work)
        transform = None
        if candidate_preparer is not None:
            render_path = Path(policy.render_config(root))
            if not render_path.is_file():
                raise UpdateRefused("the toolkit's render configuration is missing: {}".format(render_path))
            config = _read_json(render_path)
            transform = lambda installed, staged, files: candidate_preparer(installed, staged, files, config)
        bundle = Path(fetched["bundle"])
        upgrade.stage_review_bundle(
            root, fetched["source"], bundle, _read_json(fetched["candidate"]),
            _read_json(fetched["previous"]) if fetched["previous"] else None,
            local_files=tuple(sorted(notes)), candidate_transform=transform,
            generated_paths=tuple(generated_paths) if transform is not None else ())
        review_sha256 = hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest()
        text, requires_accept = update_review.render_review(
            bundle=bundle, installed_root=root, source=fetched["source"], policy=policy, review_sha256=review_sha256)
        staged = Staged(bundle, review_sha256, requires_accept, text, str(fetched["installed"]),
                        str(fetched["version"]))
        _write_meta(bundle.parent / META, {"requires_guard_accept": requires_accept, "review_sha256": review_sha256,
                                           "from_version": staged.from_version, "to_version": staged.to_version})
        return staged
    except UpdateRefused:
        raise
    except ValueError as exc:
        raise UpdateRefused(str(exc)) from exc


# --- apply half -------------------------------------------------------------------------------------------------

TIMEOUT = 600          # seconds per setup or canary run
TAIL_LINES = 20        # lines of each captured stream kept in the report and the marker
SOURCE = "unpacked"    # `release.fetch` unpacks the release beside the bundle under this name
_SHA = re.compile(r"[0-9a-f]{64}")
_OPERATION = re.compile(r"[0-9a-f]{32}")
_JOURNALS = ".modding-agent-harness/upgrades"
_OWN_STATE = frozenset((".modding-agent-harness/upgrade.lock", FAILED_MARKER, IN_FLIGHT))   # the updater's own files


@dataclass(frozen=True)
class Applied:
    ok: bool
    operation: str
    rolled_back: bool
    report: str


_OUTPUT_TAIL = 64 * 1024


def _alive(pid):
    """True, False, or None when it cannot be told. Standard library only (ctypes on Windows, signal 0 elsewhere)."""
    try:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                error = ctypes.get_last_error()
                return False if error == 87 else True if error == 5 else None   # no such process / access denied
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return None
                return code.value == 259                                 # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def _kill_tree(process):
    """Kill the step and everything it started: taskkill /T on Windows, the process group elsewhere."""
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(process.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def _file_tail(stream):
    stream.flush()
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - _OUTPUT_TAIL))
    return stream.read()


def run(argv, cwd, timeout):
    """Run one setup or canary step: an argv list, never a shell, no console window, stdin closed. Output goes to
    temporary files, not pipes, so a grandchild that keeps them open cannot block the wait; on a timeout (or an
    interrupt) the whole process tree is killed."""
    argv = list(argv)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        options = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(argv, **options)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            raise subprocess.TimeoutExpired(argv, timeout, output=_file_tail(out), stderr=_file_tail(err)) from None
        except BaseException:
            _kill_tree(process)
            raise
        return subprocess.CompletedProcess(argv, code, _file_tail(out), _file_tail(err))


_default_run = run


def _refused(message):
    return UpdateRefused(message + "; nothing was changed")


def _argv(argv, what):
    if (not isinstance(argv, (tuple, list)) or not argv
            or any(not isinstance(part, str) or "\0" in part for part in argv) or not argv[0]):
        raise _refused("the toolkit's {} command is not a non-empty argv list of strings".format(what))
    return tuple(argv)


def _tail(data):
    if data is None:
        return []
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return text.splitlines()[-TAIL_LINES:]


def _step(label, argv, root, runner):
    """Run one step. A nonzero exit, a timeout and an executable that cannot start are all failures, never skips."""
    out = err = None
    try:
        done = runner(argv, root, TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        ok, status, out, err = False, "timed out after {} s".format(exc.timeout), exc.output, exc.stderr
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        ok, status = False, "could not start: {}: {}".format(type(exc).__name__, exc)
    else:
        ok, status, out, err = done.returncode == 0, "exit {}".format(done.returncode), done.stdout, done.stderr
    return {"label": label, "argv": list(argv), "ok": ok, "status": status, "stdout": _tail(out), "stderr": _tail(err)}


def _step_lines(step):
    lines = ["  {}: {} -- {}".format(step["label"], "ok" if step["ok"] else "FAILED", step["status"]),
             "    argv: " + json.dumps(step["argv"])]
    for stream in ("stdout", "stderr"):
        if step[stream]:
            lines.append("    {} (last {} lines at most):".format(stream, TAIL_LINES))
            lines += ["      " + line for line in step[stream]]
    return lines


def _output_tail(step):
    return "\n".join(["stdout:"] + step["stdout"] + ["stderr:"] + step["stderr"]) if step else ""


def _snapshot(root):
    """Size and modification time of every file under the install root except the upgrade journals, links not
    followed: what tells the report which files setup left behind that the rollback journal does not cover. Stat only,
    so a large install costs a directory walk, not a full read; the files rollback restored are verified by its own
    hashes and left out of the comparison (their times change)."""
    found = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        here = Path(folder)
        relative = here.relative_to(root).as_posix()
        if relative == _JOURNALS:
            dirs[:] = []
            continue
        for name in files:
            path = here / name
            key = path.relative_to(root).as_posix()
            if key in _OWN_STATE:
                continue
            try:
                info = path.lstat()
                found[key] = (info.st_mode, info.st_size, info.st_mtime_ns)
            except OSError as exc:
                found[key] = "unreadable: {}".format(type(exc).__name__)
    return found


def _differences(before, after, verified=()):
    names = sorted((set(before) | set(after)) - set(verified))
    return ["{} ({})".format(name, "added" if name not in before else "removed" if name not in after else "changed")
            for name in names if before.get(name) != after.get(name)]


def _journal(root, operation):
    return root / ".modding-agent-harness" / "upgrades" / operation


def _own_operation(root, exc, review_sha256):
    """The operation an `UpgradeFailed` from OUR `installation.apply` call names, proved twice: its message carries
    `journal=<path>:` and that journal's plan.json records our review SHA-256. None when either proof is missing; a
    folder merely being new is never proof (another updater may have created it while we waited for the lock)."""
    match = re.search(r"; journal=(.*?[\\/]upgrades[\\/]([0-9a-f]{32})): ", str(exc))
    if match is None:
        return None
    operation = match.group(2)
    journal = _journal(root, operation)
    if Path(match.group(1)) != journal:
        return None
    try:
        plan = _read_json(journal / "plan.json")
    except (OSError, ValueError):
        return None
    return operation if plan.get("review_sha256") == review_sha256 and plan.get("root") == str(root) else None


def _published_any(root, operation):
    """Did the operation record any publication intent? An unreadable journal counts as yes."""
    try:
        return any(_journal(root, operation).glob("*-intent.json"))
    except OSError:
        return True


def _create_record(root, data):
    """Create the in-flight record exclusively (O_EXCL): a record another updater made first is never overwritten."""
    path = root / IN_FLIGHT
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((json.dumps(data, sort_keys=True, indent=1) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def _ours(root, nonce):
    return _read_record(root).get("nonce") == nonce


def _record(root, data):
    """Rewrite the in-flight record (keeping its nonce) while it is still ours. Returns None, or why it was not."""
    if not _ours(root, data.get("nonce")):
        return "the record is no longer this apply's"
    return _write_marker(root, data, IN_FLIGHT)


def _settle(root, nonce):
    """The outcome is final: remove the in-flight record, but only while it still carries this apply's nonce. Never
    raises; anything unclear leaves the record in place (fail closed)."""
    try:
        if _ours(root, nonce):
            (root / IN_FLIGHT).unlink()
    except OSError:
        pass


def _read_review(bundle, review_sha256):
    with upgrade._entry(bundle, "complete.json").open("rb") as stream:
        raw = stream.read(update_review.MAX_REVIEW + 1)
    if len(raw) > update_review.MAX_REVIEW or hashlib.sha256(raw).hexdigest() != review_sha256:
        raise ValueError("review SHA does not match the bundle's complete.json")
    review = json.loads(raw)
    if not isinstance(review, dict) or not isinstance(review.get("plan"), dict):
        raise ValueError("unsupported upgrade review")
    return review


def _write_marker(root, marker, name=FAILED_MARKER):
    """Write the fail-closed marker (or another of the updater's own records). Returns None, or why it failed."""
    try:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_meta(path, marker)
        return None
    except (OSError, ValueError) as exc:
        return "{}: {}".format(type(exc).__name__, exc)


def clear_failed(root, force=False):
    """The user has read the failure and fixed its cause: set the failed marker and the in-flight record aside
    (renamed, kept as evidence, never deleted) so staging works again. Nothing to clear is not an error.

    The in-flight record may belong to an apply that is still running (in the background, or in another session):
    while its recorded process is alive, or when that cannot be told, this refuses unless `force=True`. The failed
    marker has no process to check."""
    root = Path(root).resolve()
    if not force and _present(root / IN_FLIGHT):
        record = _read_record(root)
        if _alive(record.get("pid")) is not False:
            raise UpdateRefused("the apply that wrote {} is still running, or its process cannot be checked ({}). "
                                "Wait for it, or clear with --force only when you are sure it has ended; nothing was "
                                "changed".format(IN_FLIGHT, _describe_record(root)))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for name in (FAILED_MARKER, IN_FLIGHT):
        path = root / name
        if _present(path):
            os.replace(path, path.with_name("{}.cleared-{}-{}.json".format(Path(name).stem, stamp, uuid.uuid4().hex[:8])))
    return None


def apply_update(root, policy, bundle, review_sha256, choices, *, accept_guard_changes=False, quiescent=False,
                 notes_files=(), run=None, new_policy=None, candidate_validator=None):
    """Apply a staged review, then run setup and canary; roll back on any failure and fail closed if that fails.

    Refusals (nothing changed) raise `UpdateRefused`. Once files may have been published the result is an `Applied`:
    `ok` only when every step passed. The guard decision is re-derived from the bundle's `complete.json` with the
    in-memory `policy`; `meta.json` beside the bundle sits in a user-writable folder, so it is only a cross-check.
    When the review changes the toolkit's policy files, `new_policy` (the candidate's policy, supplied by the toolkit)
    gives the new setup and canary, and the old canary (`policy.canary`) must pass as well.
    `candidate_validator` and `notes_files` are handed to `installation.apply` unchanged.
    """
    runner = _default_run if run is None else run
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    if quiescent is not True:
        raise _refused("stop host and editor writers first and pass quiescent=True (--quiescent); this flag does not "
                       "stop them")
    if (root / FAILED_MARKER).exists() or (root / FAILED_MARKER).is_symlink():
        raise _refused("an earlier update failed and left {}; read it, fix the cause and clear it first"
                       .format(FAILED_MARKER))
    _refuse_in_flight(root)
    if not isinstance(review_sha256, str) or _SHA.fullmatch(review_sha256) is None:
        raise _refused("the review SHA-256 must be 64 lowercase hex digits")
    try:
        notes_files = tuple(sorted(installation.notes_set(notes_files)))
        _, requires_accept = update_review.render_review(bundle=bundle, installed_root=root, source=bundle.parent / SOURCE,
                                                         policy=policy, review_sha256=review_sha256)
        review = _read_review(bundle, review_sha256)
    except (OSError, ValueError) as exc:
        raise _refused("the staged review cannot be applied: {}".format(exc)) from exc
    if requires_accept and accept_guard_changes is not True:
        raise _refused("the review changes guard code, policy files or hook settings: applying needs the user's "
                       "separate go for those changes (--accept-guard-changes)")
    try:
        meta = _read_json(bundle.parent / META)
    except (OSError, ValueError) as exc:
        raise _refused("meta.json beside the bundle is missing or unreadable; stage the update again") from exc
    if (meta.get("review_sha256") != review_sha256 or type(meta.get("requires_guard_accept")) is not bool
            or meta["requires_guard_accept"] != requires_accept
            or not all(isinstance(meta.get(key), str) for key in ("from_version", "to_version"))):
        raise _refused("meta.json beside the bundle disagrees with the review it describes; stage the update again")
    from_version, to_version = meta["from_version"], meta["to_version"]

    rows = review["plan"].get("files")
    if not isinstance(rows, list):
        raise _refused("the review has no file rows")
    if not isinstance(choices, dict):
        raise _refused("choices must map each collision and merged settings file to keep, candidate or merged_settings")
    guard, _ = update_review.classify(rows, policy)
    # A guard file the review exempted (the user's own edit, untouched upstream) still changes when the choice is not
    # `keep`: that choice needs the same separate go. Re-derived here from complete.json and the installed policy.
    chosen = [row for row in update_review.conditional_guard_rows(rows, policy)
              if choices.get(row["path"], "keep") != "keep"]
    if chosen and accept_guard_changes is not True:
        raise _refused("choosing other than keep for {} replaces guard code the user changed locally: applying needs "
                       "the user's separate go for that (--accept-guard-changes)".format(
                           ", ".join(sorted(row["path"] for row in chosen))))
    policy_names = {name.casefold() for name in policy.policy_files}
    policy_changed = sorted(row["path"] for row in (*guard, *chosen) if row["path"].casefold() in policy_names)
    if policy_changed and new_policy is None:
        raise _refused("the review changes the toolkit's policy files ({}): the toolkit must pass new_policy, the "
                       "candidate's setup and canary, so both old and new canary can be run"
                       .format(", ".join(policy_changed)))
    active = new_policy if policy_changed else policy
    planned = [("setup", _argv(active.setup, "setup")), ("canary", _argv(active.canary, "canary"))]
    old_canary = _argv(policy.canary, "installed canary")
    old_setup = _argv(policy.setup, "installed setup")
    installed = (old_setup, old_canary)
    if policy_changed:
        planned.append(("old canary", old_canary))

    paths = {row["path"] for row in rows}
    merged = review.get("merged_settings_hashes")
    needed = {row["path"] for row in rows if row.get("action") == "preserve_collision"}
    # A merged settings file that is not a review row cannot take a choice (apply would refuse it as unknown).
    needed |= {name for name in (merged if isinstance(merged, dict) else {}) if name in paths}
    missing = sorted(needed - set(choices))
    if missing:
        raise _refused("every collision and merged settings file needs the user's explicit choice; missing: "
                       + ", ".join(missing))

    before = _snapshot(root)
    nonce = uuid.uuid4().hex
    flight = {"v": 1, "operation": None, "review_sha256": review_sha256, "from_version": from_version,
              "to_version": to_version, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "pid": os.getpid(), "nonce": nonce}
    try:
        _create_record(root, flight)
    except FileExistsError as exc:
        raise _refused("another update created {} first: it is {}".format(IN_FLIGHT, _describe_record(root))) from exc
    except OSError as exc:
        raise _refused("could not write {} before applying: {}".format(IN_FLIGHT, exc)) from exc
    try:
        earlier = {path.name for path in (root / _JOURNALS).iterdir()} if (root / _JOURNALS).is_dir() else set()
    except OSError:
        earlier = None
    # From here until the outcome is final, the record stays on disk. It is removed only on a final outcome (never in
    # a `finally`), so a kill, an interrupt or a tool timeout leaves it behind and the next stage or apply refuses.
    try:
        try:
            result = installation.apply(root, bundle, review_sha256, choices, quiescent=True,
                                        candidate_validator=candidate_validator, notes_files=notes_files)
        except installation.UpgradeFailed as exc:
            # Raised only once OUR journal exists. Identify it positively; a new folder alone may be another updater's.
            operation = _own_operation(root, exc, review_sha256)
            if operation is None:
                return _unidentified(root, exc, review, choices, from_version, to_version, nonce)
            flight["operation"] = operation
            _record(root, flight)
            if not _published_any(root, operation):
                raise _refused("apply failed before publishing anything (journal {}): {}".format(operation, exc)) \
                    from exc
            # Files may already be replaced: recover exactly as after a failed step.
            failed = {"label": "apply", "argv": [], "ok": False, "status": "failed: {}".format(exc),
                      "stdout": [], "stderr": []}
            return _recover(root, installed, runner, notes_files, operation, [failed], failed, before,
                            review, choices, from_version, to_version, nonce)
        except TimeoutError as exc:
            # A bare TimeoutError: every error after the journal exists is wrapped in UpgradeFailed, so this is the
            # lock wait (or the deadline) before anything was written.
            raise _refused("the upgrade lock .modding-agent-harness/upgrade.lock is held by another updater, or apply "
                           "ran out of time before changing anything: {}".format(exc)) from exc
        except (OSError, ValueError) as exc:
            # Normally raised before our journal existed. But installation.apply's own cleanup can raise and replace
            # an UpgradeFailed (a scanner locking a temp file), so look for a journal of ours before calling it clean.
            found = _new_journals(root, earlier, review_sha256)
            if found is None or found[1]:
                return _unidentified(root, exc, review, choices, from_version, to_version, nonce)
            ours = found[0]
            if not ours:
                raise _refused("apply refused: {}".format(exc)) from exc
            published = [operation for operation in ours if _published_any(root, operation)]
            if len(ours) == 1 and published:
                flight["operation"] = ours[0]
                _record(root, flight)
                failed = {"label": "apply", "argv": [], "ok": False, "status": "failed: {}".format(exc),
                          "stdout": [], "stderr": []}
                return _recover(root, installed, runner, notes_files, ours[0], [failed], failed, before,
                                review, choices, from_version, to_version, nonce)
            if published:
                return _unidentified(root, exc, review, choices, from_version, to_version, nonce)
            # A journal of ours exists but records no publication: probably nothing changed, but not provably. The
            # in-flight record stays (fail closed) and names it.
            flight["operation"] = ours[0]
            _record(root, flight)
            return Applied(False, ours[0], False, "\n".join([
                "Update {} -> {}: FAILED at apply ({}); NOT rolled back.".format(from_version, to_version, exc),
                "A journal of this apply exists ({}) but records no publication; the failure replaced the upgrader's "
                "own error, so this cannot prove nothing changed.".format(", ".join(ours)),
                "Kept {}: staging and applying refuse until the user checks the install and clears it.".format(
                    IN_FLIGHT), ""]) + "\n")
        operation = result["operation"]
        flight["operation"] = operation
        _record(root, flight)
        steps = []
        for label, argv in planned:
            step = _step(label, argv, root, runner)
            steps.append(step)
            if not step["ok"]:
                return _recover(root, installed, runner, notes_files, operation, steps, step, before, review,
                                choices, from_version, to_version, nonce)
        _settle(root, nonce)
    except UpdateRefused:
        _settle(root, nonce)      # a clean refusal is a final outcome
        raise
    except BaseException as exc:
        # Outcome unknown (an interrupt, or an error nobody expected): keep the record, note what happened, fail closed.
        flight["interrupted"] = "{}: {}".format(type(exc).__name__, exc)
        _record(root, flight)
        raise
    lines = ["Update {} -> {}: APPLIED, setup and canary passed.".format(from_version, to_version),
             "operation: {} (undo with upgrade-rollback {} --quiescent)".format(operation, operation)]
    lines += _common(review, choices, steps, result.get("published", []))
    lines += ["Not restored by rollback:",
              "  whatever setup generated or deployed is outside the rollback journal; a later upgrade-rollback of this",
              "  operation restores the installed files only.", ""]
    return Applied(True, operation, False, "\n".join(lines) + "\n")


def _new_journals(root, earlier, review_sha256):
    """([ours], [unknown]) among the journal folders that appeared since `earlier`, or None when that cannot be told.
    Ours: plan.json records this review and root. Unknown: a new folder whose plan cannot be read."""
    if earlier is None:
        return None
    try:
        names = {path.name for path in (root / _JOURNALS).iterdir()} if (root / _JOURNALS).is_dir() else set()
    except OSError:
        return None
    ours, unknown = [], []
    for name in sorted(names - earlier):
        if not _OPERATION.fullmatch(name):
            continue
        try:
            plan = _read_json(_journal(root, name) / "plan.json")
        except (OSError, ValueError):
            unknown.append(name)
            continue
        if plan.get("review_sha256") == review_sha256 and plan.get("root") == str(root):
            ours.append(name)
    return ours, unknown


def _unidentified(root, exc, review, choices, from_version, to_version, nonce):
    """`installation.apply` failed after creating a journal, but the failure names no journal we can prove is ours.
    Some files may be replaced and nothing can be rolled back safely: fail closed."""
    marker = {"v": 1, "from_version": from_version, "to_version": to_version, "operation": None,
              "first_failure": "apply", "failed_step": "apply", "error": "{}: {}".format(type(exc).__name__, exc),
              "output_tail": "", "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    reason = _write_marker(root, marker)
    if reason is None:
        _settle(root, nonce)
    lines = ["Update {} -> {}: FAILED at apply ({}); NOT rolled back.".format(from_version, to_version, exc),
             "The failure names no upgrade journal that records this review, so this updater cannot tell which files",
             "it replaced and rolls nothing back. Some files may be replaced.", "",
             "FAIL CLOSED: the guards may be broken or missing."]
    lines.append("Wrote {}; staging refuses until the user reads it, fixes the cause and clears it.".format(
        FAILED_MARKER) if reason is None else
        "COULD NOT WRITE {} ({}): tell the user now; do not trust the guards.".format(FAILED_MARKER, reason))
    lines += ["", "Not restored by rollback:", "  unknown: look in .modding-agent-harness/upgrades/ for the journal.", ""]
    lines += _common(review, choices, [], [])
    return Applied(False, "", False, "\n".join(lines) + "\n")


def _common(review, choices, steps, published):
    rows = review["plan"].get("files", [])
    kept = sorted(row["path"] for row in rows if row.get("action") == "preserve_collision"
                  and choices.get(row["path"]) == "keep")
    local = review.get("observed_local_files") if isinstance(review.get("observed_local_files"), dict) else {}
    preserved = sorted({name for name, digest in local.items() if digest is not None} |
                       {row["path"] for row in rows if row.get("action") == "preserve_local"})
    lines = ["This apply assumed it was the only writer (quiescent). A running agent may keep the hooks it loaded",
             "before the update until it restarts.", "", "Files published ({}):".format(len(published))]
    lines += ["  " + name for name in published] + ["", "Steps:"]
    for step in steps:
        lines += _step_lines(step)
    lines += ["", "Collisions kept ({}):".format(len(kept))] + ["  " + name for name in kept]
    lines += ["", "Local files preserved ({}):".format(len(preserved))] + ["  " + name for name in preserved] + [""]
    return lines


def _recover(root, installed, runner, notes_files, operation, steps, failed, before, review, choices,
             from_version, to_version, nonce):
    """A step failed after files were (or may have been) published: roll back, re-run the INSTALLED setup on the
    restored files (so what it deploys outside the install, a live guard copy for example, comes from the restored
    toolkit again), then the old canary. Any of those failing writes the fail-closed marker."""
    old_setup, old_canary = installed
    marker = {"v": 1, "from_version": from_version, "to_version": to_version, "operation": operation,
              "first_failure": failed["label"], "first_failure_status": failed["status"],
              "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rolled_back, problem = False, None
    try:
        restored = installation.rollback(root, operation, quiescent=True, notes_files=notes_files)
        rolled_back = True
    except Exception as exc:
        restored = None
        marker.update(failed_step="rollback", error="{}: {}".format(type(exc).__name__, exc),
                      output_tail=_output_tail(failed))
        problem = "the rollback failed: {}: {}".format(type(exc).__name__, exc)
    redeployed = False
    if rolled_back:
        again = _step("setup after rollback", old_setup, root, runner)
        steps = [*steps, again]
        if not again["ok"]:
            marker.update(failed_step="setup after rollback", output_tail=_output_tail(again))
            problem = "the installed setup fails on the restored files, so what it deploys may still be the new version"
        else:
            redeployed = True
            check = _step("canary after rollback", old_canary, root, runner)
            steps = [*steps, check]
            if not check["ok"]:
                marker.update(failed_step="canary after rollback", output_tail=_output_tail(check))
                problem = "the installed canary fails on the restored state"
    head = ("Update {} -> {}: FAILED at {} ({}); {}.".format(
        from_version, to_version, failed["label"], failed["status"],
        "rolled back to the previous install" if rolled_back else "NOT rolled back"))
    lines = [head, "operation: {}".format(operation)]
    if restored is not None:
        lines.append("rollback restored {} file(s), set {} new file(s) aside in {}".format(
            len(restored.get("restored", [])), len(restored.get("retained_new_files", [])), restored.get("journal")))
    if redeployed:
        lines.append("setup re-ran from the restored toolkit, so the copies it deploys come from the restored version")
    if problem is None:
        _settle(root, nonce)      # rolled back cleanly and the old canary passes: final
    else:
        reason = _write_marker(root, marker)
        if reason is None:
            _settle(root, nonce)  # the marker now carries the fail-closed state
        lines += ["", "FAIL CLOSED: {}. The guards may be broken or missing.".format(problem)]
        lines.append("Wrote {}; staging refuses until the user reads it, fixes the cause and clears it.".format(
            FAILED_MARKER) if reason is None else
            "COULD NOT WRITE {} ({}): tell the user now; do not trust the guards.".format(FAILED_MARKER, reason))
    lines.append("")
    lines += _common(review, choices, steps, [])
    lines += ["Not restored by rollback:"]
    if rolled_back:
        verified = [*restored.get("restored", []), *restored.get("retained_new_files", [])]
        lines += (["  " + name for name in _differences(before, _snapshot(root), verified)]
                  or ["  (no file under the install root)"])
    else:
        lines.append("  unknown: the rollback did not complete; journal {}".format(
            root / _JOURNALS / operation))
    lines += ["  setup's effects outside the install root (deployed copies, for example) are not tracked here.", ""]
    return Applied(False, operation, rolled_back, "\n".join(lines) + "\n")


# --- command line ---------------------------------------------------------------------------------------------------

COMMANDS = frozenset(("update-stage", "update-apply", "update-clear-failed"))
_MAX_CHOICES = 1024 * 1024


def add_arguments(subparsers):
    """Register the three commands. `update-apply` takes exactly what the printed review's `next:` block tells the
    agent to run: `update-apply --bundle DIR --review-sha256 SHA --choices FILE [--accept-guard-changes] --quiescent`."""
    staging = subparsers.add_parser("update-stage", help="fetch the newest release and stage it for review; "
                                    "installs and authorizes nothing")
    applying = subparsers.add_parser("update-apply", help="apply a staged update after the user's go, then run setup "
                                     "and canary; rolls back on failure")
    applying.add_argument("--bundle", required=True, type=Path)
    applying.add_argument("--review-sha256", required=True)
    applying.add_argument("--choices", type=Path, help="JSON: keep/candidate/merged_settings for every collision")
    applying.add_argument("--accept-guard-changes", action="store_true",
                          help="the user's separate go for guard, hook, settings or policy-file changes")
    applying.add_argument("--quiescent", action="store_true", required=True,
                          help="host/editor writers have been stopped; this flag does not stop them")
    clearing = subparsers.add_parser("update-clear-failed", help="set the failed and in-flight markers aside after the "
                                     "user has read them and fixed the cause")
    clearing.add_argument("--force", action="store_true",
                          help="clear the in-flight record even though its process is alive or cannot be checked")
    for parser in (staging, applying, clearing):
        parser.add_argument("--installed-root", type=Path)


def _choices(path):
    if path is None:
        return {}
    try:
        with Path(path).open("rb") as stream:
            return transport.load(stream.read(_MAX_CHOICES + 1))
    except (OSError, ValueError) as exc:
        raise UpdateRefused("the choices file cannot be read: {}; nothing was changed".format(exc)) from exc


def handle(args, root, profile, policy, *, notes_files=(), candidate_preparer=None, candidate_validator=None,
           generated_paths=(), new_policy=None, http=None):
    """Run one parsed `update-*` command and return a JSON-able dict, or raise `UpdateRefused` / `OSError`.

    The toolkit supplies `profile` (`release.ReleaseProfile`), `policy` (`UpdatePolicy`) and its renderer hooks
    (`candidate_preparer`, `candidate_validator`, `generated_paths`, `new_policy`); none of it comes from the
    downloaded release. Turn the result into a process exit code with `exit_code`.
    """
    root = Path(args.installed_root or root)
    if args.command == "update-stage":
        staged = stage_update(root, profile, policy, notes_files=notes_files, http=http,
                              candidate_preparer=candidate_preparer, generated_paths=generated_paths)
        return {"bundle": str(staged.bundle), "review_sha256": staged.review_sha256,
                "requires_guard_accept": staged.requires_guard_accept, "from_version": staged.from_version,
                "to_version": staged.to_version, "application_authorized": False, "review": staged.text}
    if args.command == "update-apply":
        applied = apply_update(root, policy, args.bundle, args.review_sha256, _choices(args.choices),
                               accept_guard_changes=args.accept_guard_changes, quiescent=args.quiescent,
                               notes_files=notes_files, new_policy=new_policy, candidate_validator=candidate_validator)
        return {"ok": applied.ok, "operation": applied.operation, "rolled_back": applied.rolled_back,
                "report": applied.report}
    if args.command == "update-clear-failed":
        resolved = Path(root).resolve()
        present = [name for name in (FAILED_MARKER, IN_FLIGHT) if _present(resolved / name)]
        clear_failed(root, force=args.force)
        return {"cleared": present}
    raise ValueError("not a guarded update command")


def exit_code(result):
    """The process exit code for what `handle` returned or raised (the toolkit's main maps it with this):

    0  ok: a stage or clear result, or an apply whose setup and canary passed (`ok` true).
    1  the update did not succeed after work began: an apply with `ok` false (rolled back, or the fail-closed marker
       was written), or any unexpected error, whose outcome is unknown (read `update-in-flight.json`).
    2  a refusal before anything changed: `UpdateRefused`, `OSError`, `ValueError`.
    Anything that is neither a result nor an exception is a bug in the caller (TypeError).
    """
    if isinstance(result, (UpdateRefused, OSError, ValueError)):
        return 2
    if isinstance(result, BaseException):
        return 1
    if isinstance(result, Applied):
        return 0 if result.ok is True else 1
    if isinstance(result, dict):
        return 0 if result.get("ok", True) is True else 1     # a stage or clear result carries no `ok`
    raise TypeError("exit_code takes a handle result or an exception, not {}".format(type(result).__name__))


def session_note(root):
    """One loud line while the failed marker or the in-flight record exists, else None. Never raises: a state it
    cannot read is reported as unknown, never as fine. A toolkit's session-start hook prints it beside `release.note`."""
    try:
        found = []
        meaning = {FAILED_MARKER: "an update failed and could not be rolled back and verified: the guards may be "
                                  "broken or missing",
                   IN_FLIGHT: "an update is {}: an unverified guard set may be live".format(
                       _describe_record(root))}
        for name in (FAILED_MARKER, IN_FLIGHT):
            try:
                os.lstat(Path(root) / name)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                found.append("{} could not be read, so treat it as present".format(name))
                continue
            found.append("{} exists: {}".format(name, meaning[name]))
        if not found:
            return None
        return ("TOOLKIT UPDATE NEEDS THE USER: {}. Do not update or trust the hooks; tell the user now. They read the "
                "file, fix the cause, then clear it with update-clear-failed.".format("; ".join(found)))
    except Exception as exc:
        return ("TOOLKIT UPDATE STATE UNKNOWN: could not check for {} or {} ({}). Tell the user before updating."
                .format(FAILED_MARKER, IN_FLIGHT, type(exc).__name__))

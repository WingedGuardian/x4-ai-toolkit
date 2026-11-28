"""Find a toolkit's newer GitHub release and fetch it for the reviewed upgrader; never install anything.

A toolkit supplies a `ReleaseProfile` (its GitHub repository, its zip's name, how to read the installed version).
`check` asks GitHub at most once a day and caches the answer; `note` reads only that cache, so a session start
never waits on the network. `fetch` downloads the release zip, refuses it unless its SHA-256 matches the digest
GitHub publishes for the asset, unpacks it aside after checking every entry name, and refuses it unless its
`BUILD-INFO` names the release's version and the commit the tag points at. It then writes the candidate inventory the
existing `upgrade-preview` / `upgrade-stage` / `upgrade-apply` commands take. Applying stays with those commands, so
the user's own changes are shown before anything is replaced, and every applied update has a rollback journal.

Checking is on by default. `{"v": 1, "check": false}` in `.modding-agent-harness/updates.json`, or the environment
variable MODDING_HARNESS_UPDATE_CHECK=off, turns it off. (The setting is not in config.json: an older core refuses
unknown keys there, so a rollback past this version would break the save store.)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from typing import Callable

from . import upgrade

API = "https://api.github.com"
STATE = ".modding-agent-harness/update-check.json"
SETTINGS = ".modding-agent-harness/updates.json"
INTERVAL = 24 * 3600
MAX_META = 2 * 1024 * 1024
MAX_ZIP = 512 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
TIMEOUT = 20
COMMANDS = frozenset(("update-check", "update-fetch"))
_VERSION = re.compile(r"v?(\d+(?:\.\d+){0,3})")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ReleaseProfile:
    repo: str                                   # "owner/name" on GitHub
    asset: str                                  # the zip's name, with "{version}" for the version
    product: str                                # how the note names it, e.g. "the Skyrim toolkit"
    installed_version: Callable[[Path], "str | None"]


def parse_version(text):
    """`v4.2.0` / `4.2.0` as a tuple of ints, or None. Pre-release or build suffixes are not versions here."""
    match = _VERSION.fullmatch(str(text).strip()) if text is not None else None
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer(candidate, installed):
    a, b = parse_version(candidate), parse_version(installed)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def build_info(root):
    """The `key=value` lines of a release's BUILD-INFO, or {} when there is none."""
    path = Path(root) / "BUILD-INFO"
    try:
        with path.open("rb") as stream:
            raw = stream.read(4097)
    except OSError:
        return {}
    if len(raw) > 4096:
        return {}
    result = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result


_OFF_WORDS = ("off", "0", "false", "no")


def settings(root, environ=None):
    """(enabled, problem). Off by MODDING_HARNESS_UPDATE_CHECK=off or `"check": false` (also "false", "off", 0) in
    the settings file. The file is read in whatever encoding Windows wrote it (UTF-8 with or without a BOM, UTF-16
    from PowerShell 5's `>`). A settings file that exists but cannot be read is taken as OFF -- someone wrote it to
    say something, and asking GitHub against their wish is the worse mistake -- and `problem` says why."""
    environ = os.environ if environ is None else environ
    if environ.get("MODDING_HARNESS_UPDATE_CHECK", "").strip().lower() in _OFF_WORDS:
        return False, ""
    try:
        with (Path(root) / SETTINGS).open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return True, ""
    except OSError as exc:
        return False, "{} could not be read ({}); update checks are off until it is fixed".format(SETTINGS, type(exc).__name__)
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
        data = json.loads(text)
    except ValueError:
        return False, "{} is not valid JSON; update checks are off until it is fixed".format(SETTINGS)
    if not isinstance(data, dict):
        return False, "{} is not a JSON object; update checks are off until it is fixed".format(SETTINGS)
    value = data.get("check", True)
    if value is False or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0):
        return False, ""
    if isinstance(value, str) and value.strip().lower() in _OFF_WORDS:
        return False, ""
    return True, ""


def enabled(root, environ=None):
    return settings(root, environ)[0]


def updates_home(root, environ=None):
    """Where one install's update downloads, unpacked candidates and review bundles go: OUTSIDE the install, because
    the reviewed upgrader refuses a bundle or a source inside the root it upgrades. Per user, beside the save vault:
    %LOCALAPPDATA%/modding-agent-harness/updates/<install id> (Linux: $XDG_DATA_HOME or ~/.local/share), the id a
    hash of the install's resolved path so two installs never share a folder. MODDING_HARNESS_UPDATES_DIR overrides
    the base (tests)."""
    environ = os.environ if environ is None else environ
    base = environ.get("MODDING_HARNESS_UPDATES_DIR")
    if not base:
        if os.name == "nt":
            base = Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "modding-agent-harness/updates"
        else:
            base = Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "modding-agent-harness/updates"
    key = str(Path(root).resolve())
    key = key.casefold() if os.name == "nt" else key
    home = Path(base) / hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    installed, folder = Path(root).resolve(), home.resolve()
    if folder == installed or installed in folder.parents:
        raise ValueError("the update folder must be outside the install")
    return home


def http_get(url, limit=MAX_META, accept="application/vnd.github+json", stream_to=None):
    """GET with a timeout and a byte cap. With `stream_to`, the body is written there and its SHA-256 returned."""
    request = urllib.request.Request(url, headers={"User-Agent": "modding-agent-harness-update-check",
                                                   "Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:   # noqa: S310 (https only, checked by caller)
        if stream_to is None:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError("response exceeds its size limit")
            return body
        sha, total = hashlib.sha256(), 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                return sha.hexdigest(), total
            total += len(chunk)
            if total > limit:
                raise ValueError("download exceeds its size limit")
            sha.update(chunk)
            stream_to.write(chunk)


def _json(fetch, url):
    data = json.loads(fetch(url).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("unexpected GitHub response")
    return data


def latest(profile, fetch=http_get):
    """The newest published release: tag, version, and the profile's asset with its GitHub SHA-256 digest."""
    if not _REPO.fullmatch(profile.repo):
        raise ValueError("release repository must be owner/name")
    data = _json(fetch, "{}/repos/{}/releases/latest".format(API, profile.repo))
    if data.get("draft") or data.get("prerelease"):
        raise ValueError("the latest release is a draft or a prerelease")
    tag = data.get("tag_name")
    version = parse_version(tag)
    if version is None:
        raise ValueError("the latest release's tag is not a version")
    text = ".".join(map(str, version))
    name = profile.asset.format(version=text)
    asset = next((a for a in data.get("assets") or () if isinstance(a, dict) and a.get("name") == name), None)
    result = {"tag": tag, "version": text, "asset": None}
    if asset is not None:
        digest = str(asset.get("digest") or "")
        url = str(asset.get("browser_download_url") or "")
        result["asset"] = {"name": name, "url": url, "size": asset.get("size"),
                           "sha256": digest[7:] if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else None}
    return result


def _read_state(root):
    try:
        with (Path(root) / STATE).open("rb") as stream:
            data = json.loads(stream.read(65537).decode("utf-8"))
        return data if isinstance(data, dict) and data.get("v") == 1 else {"v": 1}
    except (OSError, ValueError):
        return {"v": 1}


def _write_state(root, data):
    path = Path(root) / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(".part-" + uuid.uuid4().hex)
    try:
        part.write_bytes((json.dumps(data, sort_keys=True) + "\n").encode("utf-8"))
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def check(root, profile, fetch=http_get, now=None, force=False, environ=None):
    """Ask GitHub at most once a day (always with `force`); cache and return the state. Never raises."""
    now = time.time() if now is None else now
    try:
        if not enabled(root, environ):
            return {"v": 1, "status": "off"}
        state = _read_state(root)
        if not force and isinstance(state.get("checked_at"), (int, float)) and 0 <= now - state["checked_at"] < INTERVAL:
            return state
        state["checked_at"] = now
        try:
            found = latest(profile, fetch)
            state.update(status="checked", latest=found["version"], tag=found["tag"], asset=found["asset"])
            state.pop("error", None)
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            state.update(status="unreachable", error=type(exc).__name__)   # offline is quiet, and retried tomorrow
        _write_state(root, state)
        return state
    except Exception as exc:                    # a session start must never fail on this
        return {"v": 1, "status": "failed", "error": type(exc).__name__}


def note(root, profile, environ=None, mark=True):
    """One line when the cache knows of a newer release, once per version (cache only, no network). Never raises."""
    try:
        if not enabled(root, environ):
            return ""
        state = _read_state(root)
        installed = profile.installed_version(Path(root))
        found = state.get("latest")
        if state.get("status") != "checked" or not newer(found, installed) or state.get("notified_for") == found:
            return ""
        if mark:
            state["notified_for"] = found
            _write_state(root, state)
        return ('{} {} is out (you have {}): say "update the toolkit" and the agent will show what changes '
                'before installing it.'.format(profile.product, found, installed))
    except Exception:
        return ""


def _safe_members(archive):
    """Every entry under ONE root folder, no links, no unsafe names, within the size budget; returns (root, files)."""
    files, roots, total = {}, set(), 0
    for info in archive.infolist():
        name = info.filename
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ValueError("the release zip contains a link: " + name)
        if "\\" in name or name.startswith("/") or re.match(r"[A-Za-z]:", name):
            raise ValueError("unsafe entry name in the release zip: " + name)
        parts = name.rstrip("/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("unsafe entry name in the release zip: " + name)
        roots.add(parts[0])
        if info.is_dir():
            continue
        if len(parts) < 2:
            raise ValueError("a file sits outside the release zip's one folder: " + name)
        total += info.file_size
        if total > MAX_UNPACKED:
            raise ValueError("the release zip unpacks past its size budget")
        files["/".join(parts[1:])] = info
    if len(roots) != 1:
        raise ValueError("the release zip must hold exactly one folder")
    upgrade.inventory({"v": 1, "files": dict.fromkeys(files, "0" * 64)})   # the upgrader's own name rules
    return roots.pop(), files


def _own_release_url(url, repo):
    """https://github.com/<repo>/releases/download/<tag>/<name>, with no dot segments (raw or %-encoded), no query,
    no fragment and no credentials."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment:
        return False
    segments = urllib.parse.unquote(parts.path).split("/")
    if any(seg in (".", "..") or "\\" in seg for seg in segments):
        return False
    prefix = "/{}/releases/download/".format(repo)
    return parts.path.startswith(prefix) and len(segments) == len(prefix.split("/")) + 1


def fetch(root, profile, http=http_get, environ=None):
    """Download, verify and unpack the latest release aside; write its candidate inventory. Changes nothing installed.

    Returns the paths the reviewed upgrader takes (`upgrade-preview --candidate`, `upgrade-stage --source`).
    """
    root = Path(root).resolve()
    installed = profile.installed_version(root)
    found = latest(profile, http)
    if not newer(found["version"], installed):
        raise ValueError("no newer release: {} is installed and the latest is {}".format(installed, found["version"]))
    asset = found["asset"]
    if asset is None:
        raise ValueError("release {} has no asset named {}".format(found["tag"], profile.asset.format(version=found["version"])))
    if not asset["sha256"]:
        raise ValueError("GitHub publishes no SHA-256 digest for {}; refusing an unverifiable download".format(asset["name"]))
    if not _own_release_url(asset["url"], profile.repo):
        raise ValueError("the asset's download address is not this repository's release")
    commit = _json(http, "{}/repos/{}/commits/{}".format(API, profile.repo, found["tag"])).get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("could not resolve the commit release {} points at".format(found["tag"]))

    work = updates_home(root, environ) / found["version"]
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / asset["name"]
    part = work / (asset["name"] + ".part")
    try:
        with part.open("wb") as stream:
            sha, size = http(asset["url"], limit=MAX_ZIP, accept="application/octet-stream", stream_to=stream)
        if sha != asset["sha256"]:
            raise ValueError("the download's SHA-256 does not match the digest GitHub publishes for it")
        if isinstance(asset["size"], int) and size != asset["size"]:
            raise ValueError("the download's size does not match the release asset")
        os.replace(part, zip_path)
    finally:
        part.unlink(missing_ok=True)

    source = work / "unpacked"
    candidate_path = work / "candidate.json"
    for stale in (source, candidate_path):      # our own earlier unpack of this same version (same-run scratch)
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()
    try:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                top, members = _safe_members(archive)
                for rel, info in members.items():
                    target = source.joinpath(*rel.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise ValueError("the release zip is damaged: {}".format(exc)) from None
        info = build_info(source)
        if parse_version(info.get("version")) != parse_version(found["version"]):
            raise ValueError("the zip's BUILD-INFO names version {}, not {}".format(info.get("version"), found["version"]))
        if info.get("commit") != commit:
            raise ValueError("the zip's BUILD-INFO names a different commit than tag {}".format(found["tag"]))
    except BaseException:
        if source.exists():
            shutil.rmtree(source, ignore_errors=True)   # a refused release leaves nothing unpacked behind
        zip_path.unlink(missing_ok=True)
        raise
    candidate = {"v": 1, "files": {}}
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        candidate["files"][path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    candidate_path.write_bytes((json.dumps(candidate, sort_keys=True) + "\n").encode("utf-8"))
    previous, why = _previous(profile, installed, http, work)
    # A fresh review bundle folder each fetch: the upgrader refuses an existing destination, so a second try at the
    # same version (the user said "not now", or a stale review was refused) would otherwise dead-end.
    bundle = work / ("bundle-" + uuid.uuid4().hex[:12])
    return {"version": found["version"], "installed": installed, "tag": found["tag"], "commit": commit,
            "zip": str(zip_path), "sha256": sha, "source": str(source), "candidate": str(candidate_path),
            "previous": previous, "previous_note": why, "bundle": str(bundle),
            "files": len(candidate["files"]), "installed_changed": False}


def _previous(profile, installed, http, work):
    """What the INSTALLED release shipped, as an inventory (`upgrade-preview --previous`): the installed version's
    own release zip, verified by its GitHub digest and hashed member by member in memory (never unpacked). Without
    it every shipped file the user never touched would be shown as a possible change of theirs. Optional: any
    problem returns (None, why) and the review simply shows more files to choose on."""
    try:
        data = _json(http, "{}/repos/{}/releases/tags/v{}".format(API, profile.repo, installed))
        name = profile.asset.format(version=".".join(map(str, parse_version(installed))))
        asset = next((a for a in data.get("assets") or () if isinstance(a, dict) and a.get("name") == name), None)
        digest = str((asset or {}).get("digest") or "")
        url = str((asset or {}).get("browser_download_url") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not url.startswith(
                "https://github.com/{}/releases/download/".format(profile.repo)):
            return None, "the installed release {} has no verifiable asset {}".format(installed, name)
        part = work / ("previous-" + name + ".part")
        try:
            with part.open("w+b") as stream:
                sha, _ = http(url, limit=MAX_ZIP, accept="application/octet-stream", stream_to=stream)
                if sha != digest[7:]:
                    return None, "the installed release's zip does not match its published digest"
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    _, members = _safe_members(archive)
                    files = {rel: hashlib.sha256(archive.read(info)).hexdigest() for rel, info in members.items()}
        finally:
            part.unlink(missing_ok=True)
        path = work / "previous.json"
        path.write_bytes((json.dumps({"v": 1, "files": files}, sort_keys=True) + "\n").encode("utf-8"))
        return str(path), ""
    except Exception as exc:                    # optional evidence: never the reason an update fails
        return None, "the installed release's inventory is unavailable ({})".format(type(exc).__name__)


def add_arguments(subparsers):
    checking = subparsers.add_parser("update-check", help="is a newer release out? (asks GitHub at most once a day)")
    checking.add_argument("--now", action="store_true", help="ask GitHub now, ignoring the once-a-day limit")
    fetching = subparsers.add_parser("update-fetch", help="download, verify and unpack the latest release aside")
    for parser in (checking, fetching):
        parser.add_argument("--installed-root", type=Path)


def handle(args, root, profile):
    root = Path(args.installed_root or root)
    if args.command == "update-check":
        state = check(root, profile, force=args.now)
        installed = profile.installed_version(root)
        problem = settings(root)[1]
        return {"installed": installed, "latest": state.get("latest"), "status": state.get("status"),
                "update_available": newer(state.get("latest"), installed), "checked_at": state.get("checked_at"),
                "error": state.get("error") or problem or None}
    if args.command == "update-fetch":
        return fetch(root, profile)
    raise ValueError("not a release command")

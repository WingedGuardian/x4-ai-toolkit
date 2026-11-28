"""What a staged update changes, rendered for the user: guard code as diffs, everything else as a list.

A toolkit supplies an `UpdatePolicy` in code (which paths are guard code, which files are policy, how to run its setup
and canary). `classify` splits the review's file rows into guard changes and the rest. `render_review` reads the staged
bundle's `complete.json` and returns the text to show the user plus whether applying needs the separate guard go
(`--accept-guard-changes`). Nothing here writes anything or runs anything.

A file is a guard change only when its bytes would actually change: a guard path the new release did not touch
(candidate equals the previous release's bytes) is "unchanged upstream", never a guard change by path alone. Files
that are added or removed by the release count as changed. Merged hook settings that differ from the installed
settings count too, because they rewire which hooks run.
"""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import upgrade

DIFF_LINES = 400
MAX_REVIEW = 16 * 1024 * 1024
SHORT = 12
_SHA = re.compile(r"[0-9a-f]{64}")
_QUIET = ("unchanged", "absent_retired")
KEEPS_INSTALLED = ("preserve_collision", "preserve_local")   # actions whose default outcome keeps the installed bytes


CONDITIONAL_HEADING = "Only if you choose `candidate`"
_DRIVE = re.compile(r"[A-Za-z]:")


def _posix_relative(value, what):
    """A posix-relative path or glob, or ValueError: a backslash, an absolute path, a drive or `..` would make a glob
    that silently matches nothing, so every hook change would stop counting as a guard change."""
    if (not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or _DRIVE.match(value)
            or ".." in value.split("/")):
        raise ValueError("{} must be a non-empty posix-relative path (no backslash, no leading /, no drive, no ..): "
                         "{!r}".format(what, value))


@dataclass(frozen=True)
class UpdatePolicy:
    guard_paths: tuple[str, ...]                # fnmatch globs, posix-relative, matched case-insensitively
    policy_files: tuple[str, ...]               # exact paths of policy files (setup/canary definitions and the like)
    setup: tuple[str, ...]                      # argv of the setup step
    canary: tuple[str, ...]                     # argv of the canary
    render_config: Callable[[Path], Path]       # the toolkit's render configuration for an install root

    def __post_init__(self):
        if not isinstance(self.guard_paths, (tuple, list)) or not self.guard_paths:
            raise ValueError("guard_paths must be a non-empty tuple of posix-relative globs")
        if not isinstance(self.policy_files, (tuple, list)):
            raise ValueError("policy_files must be a tuple of posix-relative paths")
        for glob in self.guard_paths:
            _posix_relative(glob, "a guard glob")
        for name in self.policy_files:
            _posix_relative(name, "a policy file")
        object.__setattr__(self, "guard_paths", tuple(self.guard_paths))
        object.__setattr__(self, "policy_files", tuple(self.policy_files))


def _is_guard_path(path, policy):
    folded = path.casefold()
    return (any(fnmatch.fnmatchcase(folded, glob.casefold()) for glob in policy.guard_paths)
            or folded in {name.casefold() for name in policy.policy_files})


def _changes_bytes(row):
    """Would applying the candidate change the file's bytes, and did the release itself change it?"""
    candidate, installed = row.get("candidate_sha256"), row.get("installed_sha256")
    if candidate == installed:
        return False
    # The new release did not touch it and the default outcome keeps the user's installed bytes: that is lossless, not
    # a guard change. A `create` row (file deleted locally; apply writes it back) is a change even when the release
    # did not touch the file.
    return not (row.get("action") in KEEPS_INSTALLED and candidate is not None
                and candidate == row.get("previous_sha256"))


def classify(rows, policy):
    """(guard rows, other rows). A row is a guard change when its path is guard code or a policy file AND the release
    changes its bytes (added, removed or different from the installed copy, and not merely unchanged upstream)."""
    guard, other = [], []
    for row in rows:
        (guard if _is_guard_path(row["path"], policy) and _changes_bytes(row) else other).append(row)
    return guard, other


def conditional_guard_rows(rows, policy):
    """Guard-path rows `classify` exempts only because their default outcome keeps the user's bytes (a local edit the
    release did not touch): choosing `candidate` on one would still replace guard code, so that choice needs the guard
    go. Rows whose candidate bytes equal the installed bytes change nothing either way."""
    return [row for row in rows if _is_guard_path(row["path"], policy) and not _changes_bytes(row)
            and row.get("candidate_sha256") is not None and row.get("candidate_sha256") != row.get("installed_sha256")]


def _validated_rows(review):
    plan = review.get("plan")
    rows = plan.get("files") if isinstance(plan, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("path"), str) for row in rows):
        raise ValueError("review has no plan.files rows")
    upgrade.inventory({"v": 1, "files": dict.fromkeys((row["path"] for row in rows), "0" * 64)})
    for row in rows:
        for key in ("installed_sha256", "previous_sha256", "candidate_sha256"):
            value = row.get(key)
            if value is not None and (not isinstance(value, str) or _SHA.fullmatch(value) is None):
                raise ValueError("review row has an invalid hash: " + row["path"])
        if not isinstance(row.get("action"), str):
            raise ValueError("review row has no action: " + row["path"])
    return rows


def _read(base, name, expected, what):
    """The file's bytes (b'' when it is absent and expected absent), refused unless they match the reviewed hash."""
    path = upgrade._entry(base, name)
    data = path.read_bytes() if path.exists() else None
    if (None if data is None else hashlib.sha256(data).hexdigest()) != expected:
        raise ValueError("{} bytes do not match the reviewed hash: {}".format(what, name))
    return b"" if data is None else data


def _digest(data):
    return hashlib.sha256(data).hexdigest() if data else "(absent)"


def _diff_lines(name, old, new, old_label, new_label):
    """Unified diff lines, or hash lines when the bytes are binary or are not valid UTF-8 text."""
    if b"\0" in old or b"\0" in new:
        return ["binary file, not shown as text:", "  {}: {}".format(old_label, _digest(old)),
                "  {}: {}".format(new_label, _digest(new))]
    hashes = []
    try:
        old.decode("utf-8")
        new.decode("utf-8")
    except UnicodeDecodeError:
        # Replacement characters can make two different byte strings look identical, so the hashes are shown too.
        hashes = ["not valid UTF-8; the diff below uses replacement characters and can hide byte changes:",
                  "  {}: {}".format(old_label, _digest(old)), "  {}: {}".format(new_label, _digest(new))]
    split = lambda data: data.decode("utf-8", errors="replace").splitlines(keepends=True)
    lines = [line.rstrip("\n").replace("\r", "\\r") for line in difflib.unified_diff(
        split(old), split(new), fromfile="{}/{}".format(old_label, name), tofile="{}/{}".format(new_label, name),
        lineterm="")]
    if len(lines) > DIFF_LINES:
        hidden = len(lines) - DIFF_LINES
        lines = lines[:DIFF_LINES] + ["... {} more lines not shown ({} diff lines in all); read the file in full "
                                      "before agreeing".format(hidden, len(lines))]
    return hashes + (lines or ["(no textual difference; bytes differ)"])


def _quote(argument):
    """One argument as a POSIX shell word (Git Bash, the agent's usual shell): bare when it is plainly safe, otherwise
    in single quotes, which keep backslashes, `$`, backticks and parentheses literal."""
    if argument and re.fullmatch(r"[A-Za-z0-9_./:=@%+,-]+", argument):
        return argument
    return "'" + argument.replace("'", "'\"'\"'") + "'"


def _next_block(bundle, review_sha256, requires_accept, conditional=()):
    bundle = Path(bundle)
    choices = (bundle.parent / "choices.json").as_posix()
    command = "update-apply --bundle {} --review-sha256 {} --choices {}{} --quiescent".format(
        _quote(bundle.as_posix()), review_sha256, _quote(choices), " --accept-guard-changes" if requires_accept else "")
    steps = ["next:", "  1. Show the user this review, with every guard diff above in full."]
    if requires_accept:
        steps.append("  2. The review changes guard code, policy or hook settings: the user must give a separate, "
                     "explicit go for those changes, not only for the update.")
    if conditional:
        steps.append("  {}. Choosing `candidate` for a file under \"{}\" replaces guard code too: it needs the same "
                     "separate go and --accept-guard-changes.".format(len(steps), CONDITIONAL_HEADING))
    steps += ["  {}. Before running the command, the agent writes the user's keep/candidate/merged_settings choice for "
              "every collision and merged settings file, as one JSON object, into this file: {}".format(
                  len(steps), choices),
              "  {}. Only after the user says go, run exactly:".format(len(steps) + 1),
              "       " + command,
              "     Do not run it until the user says go."]
    return steps


def render_review(*, bundle, installed_root, source, policy, review_sha256):
    """(text, requires_guard_accept) for the staged review in `bundle`; `review_sha256` must be its complete.json's hash."""
    bundle, installed_root, source = Path(bundle).resolve(), Path(installed_root).resolve(), Path(source).resolve()
    if not isinstance(review_sha256, str) or _SHA.fullmatch(review_sha256) is None:
        raise ValueError("review SHA-256 must be 64 lowercase hex digits")
    with upgrade._entry(bundle, "complete.json").open("rb") as stream:
        raw = stream.read(MAX_REVIEW + 1)
    if len(raw) > MAX_REVIEW:
        raise ValueError("upgrade review exceeds parser budget")
    if hashlib.sha256(raw).hexdigest() != review_sha256:
        raise ValueError("review SHA does not match the bundle's complete.json")
    review = json.loads(raw)
    if not isinstance(review, dict) or review.get("v") != 1:
        raise ValueError("unsupported upgrade review")
    rows = _validated_rows(review)
    staged = review.get("candidate_files") if isinstance(review.get("candidate_files"), dict) else {}
    guard, other = classify(rows, policy)

    def diff_block(row):
        name, candidate = row["path"], row.get("candidate_sha256")
        old = _read(installed_root, name, row.get("installed_sha256"), "installed")
        new = b""
        if candidate is not None:
            # What apply installs is the staged copy in the bundle (rendered for this install); the release's own
            # copy is the fallback for a bundle that holds none.
            copy = upgrade._entry(bundle, "candidate/" + name)
            new = (_read(bundle / "candidate", name, staged.get(name, candidate), "staged candidate") if copy.exists()
                   else _read(source, name, candidate, "release"))
        return (["  {}  [{}]".format(name, row["action"])] +
                ["    " + line for line in _diff_lines(name, old, new, "installed", "candidate")])

    sections = [diff_block(row) for row in guard]
    conditional = [diff_block(row) for row in conditional_guard_rows(rows, policy)]

    settings = []
    merged = review.get("merged_settings_hashes")
    for name, digest in sorted((merged if isinstance(merged, dict) else {}).items()):
        if name not in upgrade.PROJECT_SETTINGS or not isinstance(digest, str) or _SHA.fullmatch(digest) is None:
            raise ValueError("review has an unknown or invalid merged settings entry")
        installed_digest = next((row.get("installed_sha256") for row in rows if row["path"] == name), None)
        if installed_digest is None:
            current = upgrade._entry(installed_root, name)
            installed_digest = hashlib.sha256(current.read_bytes()).hexdigest() if current.exists() else None
        if digest == installed_digest:
            continue
        old = _read(installed_root, name, installed_digest, "installed")
        new = _read(bundle, upgrade.PROJECT_SETTINGS[name], digest, "merged settings")
        settings.append(["  {}  [merged hook settings]".format(name)] +
                        ["    " + line for line in _diff_lines(name, old, new, "installed", "merged")])

    requires_accept = bool(sections or settings)
    text = ["Update review (sha256 {})".format(review_sha256), "bundle: {}".format(bundle), ""]
    if requires_accept:
        text.append("GUARD CHANGES ({}): hooks, settings or policy files change. They need a separate go from the "
                    "user.".format(len(sections) + len(settings)))
        for block in sections + settings:
            text += block + [""]
    else:
        text += ["No guard, hook, settings or policy change.", ""]
    if conditional:
        text.append("{} ({}): guard files you changed yourself that the release did not touch. `keep` (lossless) needs "
                    "nothing; choosing `candidate` replaces your copy with the release's and needs the same separate go "
                    "(--accept-guard-changes).".format(CONDITIONAL_HEADING, len(conditional)))
        for block in conditional:
            text += block + [""]
    matched = [row for row in rows if any(fnmatch.fnmatchcase(row["path"].casefold(), glob.casefold())
                                          for glob in policy.guard_paths)]
    text.append("guard globs matched {} of {} paths".format(len(matched), len(rows)))
    for glob in policy.guard_paths:
        if not any(fnmatch.fnmatchcase(row["path"].casefold(), glob.casefold()) for row in rows):
            text.append("WARNING: guard glob {} matched no path in this release; check the toolkit's UpdatePolicy if "
                        "that is unexpected".format(glob))
    text.append("")
    quiet = [row for row in other if row["action"] in _QUIET]
    listed = [row for row in other if row["action"] not in _QUIET]
    text.append("Other changes ({}):".format(len(listed)))
    for row in listed:
        short = (row.get("candidate_sha256") or "(none)")[:SHORT]
        note = ""
        if row["action"] == "preserve_collision" and row.get("candidate_sha256") is not None \
                and row.get("candidate_sha256") == row.get("previous_sha256"):
            note = "  unchanged upstream, keep is lossless"
        text.append("  {}  {}  {}{}".format(row["path"], row["action"], short, note))
    if quiet:
        text.append("  {} unchanged or already absent file(s) not listed.".format(len(quiet)))
    text.append("")
    return "\n".join(text + _next_block(bundle, review_sha256, requires_accept, conditional)) + "\n", requires_accept

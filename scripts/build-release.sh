#!/usr/bin/env bash
# Build the release asset, and PROVE it is the tag.
#
# WHY THIS EXISTS. v3.0.0 shipped `X4.Foundations.Claude.Code.Toolkit.v3.0.0.zip`
# (6,508,551 bytes) and NOTHING IN THE REPO MADE IT. The bundle a user downloads was
# assembled by hand, so it could not be reproduced, could not be diffed against the
# tag, and had no check that its contents were the reviewed contents.
#
# That is not a hypothetical worry in this repo. A release port once ran while a
# MUTATING gate had `_merge.py` rewritten in place and carried `if len(targets) >
# 99999:` into the public bundle -- ambiguous-selector detection silently OFF in a
# shipped release, with the port reporting success and the tracked diff looking right
# (CLAUDE.md #27). A bundle built from the WORKING TREE is exactly where that recurs.
#
# So this builds from `git archive`, which reads the COMMITTED object store and cannot
# see a working-tree edit at all -- immune to that window by construction rather than
# by remembering.
#
# MEASURED 2026-09-07: `git archive --format=zip v3.0.0` reproduces the shipped v3.0.0
# asset BYTE FOR BYTE -- 6,508,551 bytes, sha256
# 273d242c168b4cff5d0679a9251c9e230506c540fd22ff5c39f66edd03fd4de6, all 258 members
# identical. So this script is not a new convention; it is the existing one, written
# down and checked. `--selftest` re-proves that claim, which is what makes the build
# method falsifiable instead of merely asserted.
#
#   scripts/build-release.sh <ref>     build dist/X4.Foundations.AI.Assistant.Toolkit-<ref>.zip
#   scripts/build-release.sh --selftest  rebuild v3.0.0 and assert its known sha256
#
# THE SHAPE OF A BUILD (4.3.1). The shared core's self-update (`modding_harness.release.fetch`) accepts a
# release zip only when it holds exactly ONE top folder and a BUILD-INFO naming the tag's version and the
# tag's commit. So every build here is `git archive --prefix=X4AI/` (the folder users extract) plus one
# VIRTUAL member `X4AI/BUILD-INFO`:
#     vX.Y.Z tag   version=X.Y.Z / commit=<full sha of REF^{commit}>
#     any other    version= (empty) / commit=<sha> / build=dry-run   -- no version, so `fetch` refuses it
# and `verify` proves the zip is the ref: every member under X4AI/, the folder stripped, EXACTLY ONE extra
# member (X4AI/BUILD-INFO, byte for byte what this script wrote), no tracked BUILD-INFO at the ref, and the
# existing member-set and byte checks. `--selftest` rebuilds v3.0.0, which has no folder and no BUILD-INFO,
# so it keeps the old shape ("legacy" mode): it controls that the METHOD still reproduces published bytes.
#
# `git archive --add-virtual-file` writes BUILD-INFO. The script asks git itself whether `archive` has the
# option (exit 2 "cannot build" if not) and does not state which release introduced it; the effective
# minimum is the git 2.40 this script already needs for `check-attr --source`. git-archive's own
# documentation says a virtual file's path is not affected by the --prefix option, so the virtual path is
# spelled in full (`X4AI/BUILD-INFO`); a git that did apply the prefix would write X4AI/X4AI/BUILD-INFO and
# `verify` would refuse the zip rather than ship it.
#
# Exit: 0 built and verified · 1 the archive does not match the ref · 2 cannot build (including a
# vX.Y.Z ref whose CHANGELOG top heading or pyproject version is not that version)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
export GIT_ATTR_NOSYSTEM=1
git() { command git -c core.attributesFile=/dev/null -c core.autocrlf=false -c core.eol=lf "$@"; }

NAME_PREFIX="X4.Foundations.AI.Assistant.Toolkit"
V300_SHA="273d242c168b4cff5d0679a9251c9e230506c540fd22ff5c39f66edd03fd4de6"
V300_SIZE=6508551

die() { echo "REFUSING: $*" >&2; exit 2; }

# THE INTERPRETER IS RESOLVED, NOT ASSUMED. Both helpers below hard-coded `python`,
# which does not exist on a great many Linux installs -- including `ubuntu-latest`,
# where this repo's own CI calls `python3`. The failure mode was the bad one: a
# missing interpreter made `verify()` return non-zero, which this script's own
# header defines as "the archive does not match the ref", so a maintainer on Linux
# was told the bundle was CORRUPT when the truth was that the checker never ran.
# "Could not look" rendered as "something is wrong" -- the conflation this toolkit
# refuses everywhere else, inverted into a FALSE FAILURE on the release gate.
#
# rc 2 is "cannot build", and that is what a missing interpreter is.
PY=""
for _c in python3 python py; do
  if command -v "$_c" >/dev/null 2>&1 && "$_c" -c "import sys" >/dev/null 2>&1; then
    PY="$_c"; break
  fi
done
[ -n "$PY" ] || die "no working python found (tried python3, python, py) -- the bundle cannot be verified, and an unverified bundle is not shippable"

# THE MEMBER SET MUST BE THE REF'S TRACKED SET, EXACTLY. This is the check the hand
# build never had: not "did a zip appear" but "is what is in it what the tag says".
# Both directions matter -- a missing file ships a broken toolkit, an extra file ships
# something nobody reviewed.
verify() {
  local zip="$1" ref="$2" mode="${3:-legacy}" version="${4:-}"
  "$PY" - "$zip" "$ref" "$mode" "$version" <<'PY'
import subprocess, sys, zipfile
import hashlib, os, re
os.environ["GIT_ATTR_NOSYSTEM"] = "1"
GIT = ["git", "-c", "core.attributesFile=" + os.devnull, "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
zip_path, ref = sys.argv[1], sys.argv[2]
# MODE. "legacy" = the v3.0.0 shape (no top folder, no BUILD-INFO): only `--selftest` uses it.
# "release" / "dry-run" = the shape the shared core's self-update fetch accepts: EVERY member under
# exactly one folder `X4AI/`, plus ONE extra member `X4AI/BUILD-INFO` holding exactly what this
# script says it wrote (the version, and the full commit of REF^{commit}, which is derived HERE from
# the ref, never taken from the caller). Any other extra member, a tracked BUILD-INFO and a member
# outside the folder are refused, like every other member the ref does not track.
mode = sys.argv[3] if len(sys.argv) > 3 else "legacy"
version = sys.argv[4] if len(sys.argv) > 4 else ""
TOP = "X4AI/"
if mode not in ("legacy", "release", "dry-run"):
    print("  REFUSING: unknown verify mode " + repr(mode), file=sys.stderr)
    sys.exit(1)
z = zipfile.ZipFile(zip_path)
all_entries = z.infolist()
entries = [i for i in all_entries if not i.is_dir()]
name_of = {}                      # zip member name -> the path it has once the top folder is stripped
for i in all_entries:
    if mode == "legacy":
        name_of[i.filename] = i.filename
    elif i.filename.startswith(TOP):
        name_of[i.filename] = i.filename[len(TOP):]
    else:
        print("  MEMBER OUTSIDE THE ONE TOP FOLDER " + TOP + ": " + i.filename, file=sys.stderr)
        sys.exit(1)
members = set()
for entry in entries:
    if entry.filename in members:
        print("  DUPLICATE MEMBER: " + entry.filename, file=sys.stderr)
        sys.exit(1)
    members.add(entry.filename)
members = {name_of[m] for m in members}
info = subprocess.check_output([*GIT, "rev-parse", "--git-path", "info/attributes"]).decode().strip()
if os.path.isfile(info) and os.path.getsize(info):
    print("  REFUSING: machine-local info/attributes can alter the archive", file=sys.stderr)
    sys.exit(1)
# -z: NUL-separated. `.split()` splits on WHITESPACE, so `docs/my notes.md`
# became two "missing" entries and the real path became an "extra" one -- a
# FALSE REFUSAL on a correct bundle. Fails safe (refuses rather than ships) and
# measured 0 of 270 paths affected today, so it is latent: one file with a space
# in its name makes a correct release unshippable. `-z` also stops git C-quoting
# non-ASCII, which `--name-only` alone does.
out = subprocess.run([*GIT, "ls-tree", "-r", "-z", "--name-only", ref],
                     capture_output=True, check=True).stdout
tracked = {p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p}

# BUILD-INFO belongs to the BUILD, not to the tree: a tracked one would be a tree vouching for itself
# (and a second member of the same name in the zip), so the ref must not carry one.
if mode != "legacy":
    if "BUILD-INFO" in tracked:
        print("  REFUSING: BUILD-INFO is tracked at " + ref + "; it is written by the build and a tree "
              "must not vouch for itself.", file=sys.stderr)
        sys.exit(1)
    commit = subprocess.run([*GIT, "rev-parse", "--verify", "-q", ref + "^{commit}"],
                            capture_output=True, text=True, check=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        print("  REFUSING: " + ref + " does not resolve to a full commit id", file=sys.stderr)
        sys.exit(1)
    if mode == "release":
        if not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version):
            print("  REFUSING: a release build needs an X.Y.Z version, got " + repr(version), file=sys.stderr)
            sys.exit(1)
        expected_info = ("version=%s\ncommit=%s\n" % (version, commit)).encode("ascii")
    else:
        # A dry run has NO version, so the core's parse_version gives None and its fetch refuses it.
        expected_info = ("version=\ncommit=%s\nbuild=dry-run\n" % commit).encode("ascii")
    if "BUILD-INFO" not in members:
        print("  MISSING FROM THE BUNDLE: " + TOP + "BUILD-INFO", file=sys.stderr)
        sys.exit(1)
    actual_info = z.read(TOP + "BUILD-INFO")
    if actual_info != expected_info:
        print("  BUILD-INFO IS NOT WHAT THIS BUILD WROTE: expected %r, found %r" % (expected_info, actual_info),
              file=sys.stderr)
        sys.exit(1)
    # Exactly ONE extra member is allowed, and it has just been checked byte for byte. From here the
    # member set is compared WITHOUT it, so any other extra member is still IN THE BUNDLE, NOT IN THE REF.
    members.discard("BUILD-INFO")
    entries = [e for e in entries if name_of[e.filename] != "BUILD-INFO"]

# EXPORT-IGNORE. `git archive` omits paths marked `export-ignore`, so a plain
# tracked-set comparison calls every one of them MISSING and refuses a CORRECT
# bundle -- which is exactly what happened the first time `release/ export-ignore`
# was added, on a release that was otherwise ready to ship.
#
# Comparing the zip against `git archive` output instead would be CIRCULAR: the zip
# IS git archive output, so the check would compare the tool to itself and could
# never go red. The tracked set stays the reference, minus what the REF omits.
#
# ASK THE RIGHT QUESTION. `git check-attr export-ignore -- <file>` answers
# "unspecified" for every file under `release/`: the pattern names the DIRECTORY and
# git archive prunes it before descending, so the per-file query is an adjacent
# question that would exclude nothing and refuse everything. Ancestors are queried
# too, in the trailing-slash form the directory pattern actually matches.
#
# `--source=<ref>` reads the attributes AS OF THE REF -- what git archive used. The
# working tree's .gitattributes may legitimately differ, and does whenever an older
# tag is verified (v3.0.0 predates this very rule). If git is too old for --source,
# REFUSE: guessing with the wrong attributes gives a false verdict in either
# direction, and an unverified bundle is not shippable.
probe = subprocess.run([*GIT, "check-attr", "--source", ref, "export-ignore", "--",
                        ".gitattributes"], capture_output=True)
if probe.returncode != 0:
    print("  REFUSING: this git cannot do `check-attr --source`, so which paths "
          "`git archive` omitted is undeterminable for %s (git 2.40+ needed)." % ref,
          file=sys.stderr)
    sys.exit(1)

def _ancestors(path):
    """The path itself, plus every parent directory in trailing-slash form."""
    yield path
    parts = path.split("/")[:-1]
    for k in range(1, len(parts) + 1):
        yield "/".join(parts[:k]) + "/"

ignored = set()
queries = sorted({q for t in tracked for q in _ancestors(t)})
if queries:
    payload = b"\x00".join(q.encode("utf-8", "surrogateescape") for q in queries)
    ca = subprocess.run([*GIT, "check-attr", "--source", ref, "-z", "--stdin",
                         "export-ignore"], input=payload,
                        capture_output=True, check=True).stdout
    f = ca.split(b"\x00")
    # -z output is a flat NUL stream of (path, attr, value) triples.
    setp = {f[k].decode("utf-8", "surrogateescape")
            for k in range(0, len(f) - 2, 3) if f[k + 2] == b"set"}
    if setp:
        ignored = {t for t in tracked if any(a in setp for a in _ancestors(t))}
        tracked = tracked - ignored

missing, extra = sorted(tracked - members), sorted(members - tracked)
print("  members=%d  tracked at %s=%d (export-ignore excluded %d)"
      % (len(members), ref, len(tracked), len(ignored)))
if missing or extra:
    for p in missing[:20]:
        print("    MISSING FROM THE BUNDLE: " + p, file=sys.stderr)
    for p in extra[:20]:
        print("    IN THE BUNDLE, NOT IN THE REF: " + p, file=sys.stderr)
    sys.exit(1)
if not members:
    print("  REFUSING: the bundle is empty", file=sys.stderr)
    sys.exit(1)
print("  member set matches the ref exactly")
# Compare against committed Git objects, independently of git archive. Text
# conversion is accepted only for paths declared text by the ref's attributes.
objects = {}
tree = subprocess.check_output([*GIT, "ls-tree", "-r", "-z", ref])
for row in tree.split(b"\0"):
    if row:
        metadata, name = row.split(b"\t", 1)
        objects[name.decode("utf-8", "surrogateescape")] = metadata.split()[2].decode("ascii")
raw = subprocess.run([*GIT, "check-attr", "--source", ref, "-z", "--stdin", "text", "eol"],
                     input=b"\0".join(p.encode("utf-8", "surrogateescape") for p in sorted(members)),
                     capture_output=True, check=True).stdout.split(b"\0")
attributes = {}
for index in range(0, len(raw) - 2, 3):
    attributes.setdefault(raw[index].decode("utf-8", "surrogateescape"), {})[raw[index + 1]] = raw[index + 2]
for entry in entries:
    data = z.read(entry)
    oid = objects[name_of[entry.filename]]
    algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    actual = algorithm(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if actual == oid:
        continue
    attrs = attributes.get(name_of[entry.filename], {})
    text = (attrs.get(b"text") != b"unset" and
            (attrs.get(b"text") in (b"set", b"auto") or attrs.get(b"eol") in (b"lf", b"crlf")))
    original = subprocess.check_output([*GIT, "cat-file", "blob", oid]) if text else None
    if (original is not None and b"\0" not in data and b"\0" not in original and
            data.replace(b"\r\n", b"\n") == original.replace(b"\r\n", b"\n")):
        continue
    print("  CONTENT DIFFERS FROM THE REF: " + name_of[entry.filename], file=sys.stderr)
    sys.exit(1)
print("  member bytes match committed objects (declared text line endings normalized)")
PY
}

sha_of() { "$PY" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"; }

if [ "${1:-}" = "--selftest" ]; then
  # A CONTROL THAT CAN GO RED. If the build method ever stops reproducing the one
  # asset whose bytes are publicly known, this says so before a release does.
  git rev-parse -q --verify "v3.0.0^{}" >/dev/null || die "tag v3.0.0 not present, so the control cannot run"
  tmp="$(mktemp -d)" || die "no temp dir"
  trap 'rm -rf "$tmp"' EXIT
  # The published asset encodes TWO facts about the machine that built it, so the control
  # pins both instead of inheriting the caller's (MEASURED 2026-10-04, CI run 37172347642):
  # a zip member's DOS timestamp is LOCAL time (built in US Eastern; TZ=UTC gives other
  # bytes at the same size), and `* text=auto` writes the NATIVE line ending (built on
  # Windows: CRLF in 37 members; an LF build is 1,887 B smaller). EST5EDT is a POSIX TZ
  # string both glibc and the Windows CRT read. The control overrides the normal LF
  # default to preserve that historical asset. Real builds honor committed eol rules,
  # default to LF, and still inherit TZ for the ZIP timestamps.
  TZ=EST5EDT git -c core.eol=crlf -c core.autocrlf=false archive --format=zip v3.0.0 -o "$tmp/x.zip" \
    || die "git archive failed"
  got_sha="$(sha_of "$tmp/x.zip")"; got_size="$(wc -c < "$tmp/x.zip" | tr -d ' ')"
  echo "selftest: rebuilt v3.0.0 -> $got_size bytes, sha256 $got_sha"
  verify "$tmp/x.zip" v3.0.0 || exit 1
  if [ "$got_sha" = "$V300_SHA" ] && [ "$got_size" = "$V300_SIZE" ]; then
    echo "SELFTEST PASSED — git archive still reproduces the published v3.0.0 asset byte for byte."
    exit 0
  fi
  echo "SELFTEST FAILED — the rebuild no longer matches the published asset." >&2
  echo "  expected $V300_SIZE bytes / $V300_SHA" >&2
  echo "  got      $got_size bytes / $got_sha" >&2
  echo "  Do NOT ship until this is explained: the build method has changed under us." >&2
  exit 1
fi

REF="${1:-}"
[ -n "$REF" ] || die "usage: scripts/build-release.sh <ref> | --selftest"
git rev-parse -q --verify "$REF^{}" >/dev/null || die "no such ref: $REF"

# A VERSION TAG MUST NAME ITSELF IN ITS NOTES AND ITS PACKAGE. The CI test only compares
# the first `## vX.Y.Z` heading with pyproject and allows `## Unreleased` above it, which
# is right for a working tree and wrong for a release: a tag whose CHANGELOG still opened
# with `## Unreleased`, its pyproject still at the previous version, built and verified
# (MEASURED 2026-10-25, probing the Skyrim toolkit's release-review finding). Read AT THE
# REF, like everything else here. A ref that is not vX.Y.Z is a dry run and says so.
# `tags/vX.Y.Z` and `refs/tags/vX.Y.Z` name the SAME tag: matching only the bare spelling
# let either one skip this check as a "dry run" (4.0.1 batch). The short name is also the
# bundle's name, so a slashed spelling no longer becomes a path git archive cannot create.
TAG="${REF#refs/tags/}"; TAG="${TAG#tags/}"
if printf '%s' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  _ver="${TAG#v}"
  _top="$(git show "$REF:CHANGELOG.md" 2>/dev/null | tr -d '\r' | grep -m1 '^## ')"
  case "$_top" in
    "## $TAG"|"## $TAG "*) ;;
    *) die "$REF: CHANGELOG.md at the ref opens with '${_top:-no ## heading}', not '## $TAG'. Rename the Unreleased section to the release heading, commit, and re-tag." ;;
  esac
  _pv="$(git show "$REF:tools/x4validate/pyproject.toml" 2>/dev/null | tr -d '\r' \
        | sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  [ "$_pv" = "$_ver" ] || die "$REF: tools/x4validate/pyproject.toml at the ref says version '${_pv:-none}', not '$_ver'."
else
  echo "  note: $REF is not a vX.Y.Z tag, so the CHANGELOG/pyproject version check did not run (a dry run)."
fi

# A DIRTY TREE IS NOT AN ERROR HERE -- git archive cannot see it -- but it IS a warning
# worth printing, because it means the thing you just tested is not the thing you are
# about to ship, and that gap is where a release defect hides.
if [ -n "$(git status --porcelain)" ]; then
  echo "  note: the working tree is dirty. This builds from the COMMITTED state of"
  echo "        $REF, so those edits are NOT in the bundle — which is correct, and"
  echo "        worth saying out loud in case you expected them to be."
fi

# The capability is asked of git itself (its own usage text), never inferred from a version number.
# (Captured first: `archive -h` exits 129 and the script runs under pipefail, so a pipe into grep -q would
# read as "missing" on every git.)
_archive_help="$(git archive -h 2>&1)"
case "$_archive_help" in
  *add-virtual-file*) ;;   # help prints it as `--[no-]add-virtual-file`
  *) die "this git's 'archive' has no --add-virtual-file, so BUILD-INFO cannot be written; update git (this script needs 2.40+ anyway)" ;;
esac
COMMIT="$(git rev-parse --verify -q "$REF^{commit}")" || die "$REF does not resolve to a commit"
if printf '%s' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  MODE=release; VERSION="${TAG#v}"
  BUILD_INFO="$(printf 'version=%s\ncommit=%s\n' "$VERSION" "$COMMIT")"
else
  MODE=dry-run; VERSION=""
  BUILD_INFO="$(printf 'version=\ncommit=%s\nbuild=dry-run\n' "$COMMIT")"
fi

mkdir -p dist || die "cannot create dist/"
OUT="dist/${NAME_PREFIX}-${TAG}.zip"
# $(...) drops the trailing newline, so the file's final "\n" is added back here.
git archive --format=zip --prefix=X4AI/ "--add-virtual-file=X4AI/BUILD-INFO:${BUILD_INFO}"$'\n' "$REF" -o "$OUT" \
  || die "git archive failed"
echo "built $OUT  ($MODE build, folder X4AI/, BUILD-INFO commit $COMMIT)"
verify "$OUT" "$REF" "$MODE" "$VERSION" || { echo "the bundle does not match $REF — not shippable." >&2; exit 1; }
echo "  size   $(wc -c < "$OUT" | tr -d ' ') bytes"
echo "  sha256 $(sha_of "$OUT")"
echo
echo "Attach with:  gh release create $REF \"$OUT\" --title ... --notes-file ..."

#!/usr/bin/env bash
# Build (or rebuild) the x4raw BaseX database from real X4 XML — base+DLC
# reference, every INSTALLED extension's loose XML, and (since 2026-07-27) every
# extension's PACKED XML plus any DLC not unpacked into reference\ (asked of
# Config -- none, when every DLC is unpacked), via stage.py. Internal dev tool,
# not part of the public toolkit (BaseX needs a separate JVM install).
#
# PACKED CONTENT IS 62% OF ALL MOD XML (vro alone is 1,613 files). Before staging
# was added, this index could not support ANY negative claim — "nothing
# references X" was a statement about the 38% that happened to be loose.
#
# STILL NOT THE EFFECTIVE TREE: this indexes files AS WRITTEN — no diff
# application, no load order, no conflict winner. That is x4eff's job (see
# build-effective.py). x4raw answers "who WROTE this"; x4eff answers "what does
# the engine SEE".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASEX_DIR="$HERE/basex"
. "$HERE/_x4v-tree.sh"
X4VALIDATE="$(x4v_resolve "$HERE")"
x4v_announce "$X4VALIDATE"
# Resolve, never guess. These used to fall back to one developer's absolute
# paths, so on any other machine the build ran happily over directories that do
# not exist -- and reported success. (It also shipped a username.)
# x4validate._paths is the ONE resolver: $VAR -> x4-paths.env -> default.
# Fail BEFORE the long work, not after it.
#
# MEASURED 2026-08-24: this script runs stage.py to completion (:49) and does not
# touch java until :54, so on a machine with no JVM the entire staging pass was
# spent before anything could fail -- and it then failed with "command not found"
# rather than "install Java 17". uv is checked here in bash because everything
# else, preflight included, is invoked THROUGH it.
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not on PATH. This script drives x4validate via 'uv run python'." >&2
  echo "       Install it from https://docs.astral.sh/uv/" >&2
  exit 2
fi
resolve_path() {  # $1 = env var name, $2 = _paths function
  local v="${!1:-}"
  if [ -z "$v" ]; then
    v=$(cd "$X4VALIDATE" && uv run python -c "from x4validate import _paths; p=_paths.$2(); print(p or '')" 2>/dev/null)
  fi
  if [ -z "$v" ]; then
    echo "ERROR: cannot resolve \$$1. Set it, or configure x4-paths.env." >&2
    echo "Refusing to guess: a corpus built over a path that does not exist indexes" >&2
    echo "ZERO documents and still exits 0." >&2
    exit 2
  fi
  printf '%s' "$v"
}
REFERENCE="$(resolve_path X4_REFERENCE reference)"
EXTENSIONS="$(resolve_path X4_EXTENSIONS game_extensions)"
DB=x4raw

# THE REFERENCE MUST HOLD THE GAME, checked BEFORE preflight (which runs java) and
# before the next generation is prepared below (issue #10). A missing reference used to be refused only by
# BaseX's "not found" AFTER x4raw had been dropped (the drop-first build, before 4.1.1); an EMPTY one beside installed mods
# would index the mods with no base game (READ from coverage.py, not run). rc 2 =
# configuration.
echo "== reference =="
REF_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/reference_check.py" --reference "$REFERENCE" \
    --what "build the $DB corpus") || REF_RC=$?
if [ "$REF_RC" -ne 0 ]; then
  echo "       Nothing was dropped, deleted or indexed; the previous $DB is untouched." >&2
  exit "$REF_RC"
fi

echo "== preflight =="
(cd "$X4VALIDATE" && uv run python "$HERE/preflight.py" --need java jar disk)

# BUILD-THEN-SWAP (4.1.1). This build writes ${DB}__next and dbswap.py swaps it in only once it is verified,
# so a build that fails or is killed leaves the previous $DB and its coverage in place: STALE, never MISSING.
# MEASURED 2026-11-05: eight killed builds in a row left x4raw and x4eff missing for hours under drop-first.
# `prepare` finishes or undoes an interrupted swap, refuses when the disk cannot hold two generations above
# the floor (there is no drop-first fallback), and drops a leftover receipted ${DB}__next. rc 2/7 = refused.
NEXT="${DB}__next"
echo "== preparing the next generation =="
# THE BUILD LOCK (review 2026-11-07): one build of $DB at a time, from here through the swap. A second builder's
# `prepare` could otherwise undo this one's swap, drop its $NEXT mid-build, or share its pending fingerprint. The
# owner is THIS shell's OS pid (Git Bash: its Windows pid), so a killed build leaves a lock whose owner is not
# running, which the next build takes over and reports. rc 7 = another live build holds it.
OWNER="$(cat /proc/$$/winpid 2>/dev/null || echo $$)"
(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" lock --db "$DB" --owner "$OWNER")
trap '(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" unlock --db "$DB" --owner "$OWNER") >/dev/null 2>&1 || true' EXIT
(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" prepare --db "$DB" --owner "$OWNER")

# Java is a native Windows process and does not understand Git Bash's /c/... form:
# passing it verbatim made BaseX look for "C:/c/Users/..." and report the resource
# missing — while the build still exited 0. Hand Java a real Windows path.
winpath() { cygpath -m "$1" 2>/dev/null || echo "$1"; }
STAGE="$HERE/_stage"
STAGE_WIN="$(winpath "$STAGE")"

echo "== staging packed content =="
# THE FINGERPRINT OF WHAT THIS BUILD READS, taken before it reads anything (T1, R2-7). Stamped at the end, a mod changed
# while the build ran read FRESH for ever over the old values. A failure here stops the build: set -e.
PENDING="$BASEX_DIR/pending-fingerprint-$DB.json"
(cd "$X4VALIDATE" && uv run python "$HERE/staleness.py" --snapshot "$PENDING" --db "$DB")

(cd "$X4VALIDATE" && uv run python "$HERE/stage.py" --out "$STAGE" --extensions "$EXTENSIONS")

echo
echo "== building $NEXT (the live $DB stays in place until the swap) =="
cd "$BASEX_DIR"
# coverage-$DB.json is NOT revoked here any more (it was, AUDIT-2026-09-24 BX-3, because the build used to drop
# $DB first and a dying build left that licence beside a partial database). The partial database is now $NEXT,
# which ask.py never reads, and coverage-$DB.json keeps describing the $DB that is still in place.
# The creation receipt is written before CREATE DB; `prepare` above already dropped any earlier $NEXT.
(cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4basex_artifacts.py" --artifact-root "$HERE" --reference "$REFERENCE" --declare-db "$NEXT")

# BaseX exits 0 even when an ADD names a path it cannot find, so the exit code is
# not a usable gate. Capture the output and fail loudly on "not found".
BUILD_LOG="$(mktemp)"
java -cp BaseX.jar org.basex.BaseX -c "
SET SKIPCORRUPT true
CREATE DB $NEXT
SET CREATEFILTER *.xml
ADD TO /base $REFERENCE
ADD TO /base/extensions $STAGE_WIN/base_extensions
ADD TO /mods $EXTENSIONS
ADD TO /mods $STAGE_WIN/mods
OPTIMIZE ALL
INFO DB
" 2>&1 | tee "$BUILD_LOG"

if grep -qi "not found\|Improper use\|Stopped at" "$BUILD_LOG"; then
  echo >&2
  echo "ERROR: BaseX rejected at least one input above — $NEXT is INCOMPLETE and is not swapped in." >&2
  echo "       The previous $DB is untouched." >&2
  echo "       (BaseX exits 0 on this, so it would otherwise pass silently.)" >&2
  rm -f "$BUILD_LOG"
  exit 1
fi
rm -f "$BUILD_LOG"

echo
echo "== reconciling coverage (SKIPCORRUPT drops files SILENTLY) =="
# Exit 3 = coverage incomplete. Report it, do not abort: the DB is built and
# usable, it simply cannot back a NEGATIVE claim until the delta is explained.
COVERAGE_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/coverage.py" --db "$NEXT" --stage "$STAGE" \
    --reference "$REFERENCE" --extensions "$EXTENSIONS") || COVERAGE_RC=$?

# Stamp WHEN this index was true, not just how much of it was indexed. Without
# this, ask.py cannot tell a current answer from one about a superseded world —
# x4eff served 858 wrong values for 11 days because nothing recorded it.
# F126: capture the stamp rc. This ran in a subshell whose failure was IGNORED, so a
# BUILT database could be left UNSTAMPED and every CLI would banner it as STALE forever,
# while the script exited on the coverage verdict alone -- success reported for work it
# did not do. MEASURED 2026-09-20: a staging `rm` lost a race with a BaseX file handle and
# this is the state it left behind. An unstamped index is a FAILURE even though the
# database itself is fine, because nothing downstream can tell the two apart.
# The stamp goes on $NEXT's report, for $DB's world (the snapshot is $DB's): see staleness.write(db=).
STAMP_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/staleness.py" --write --db "$DB" --coverage "$BASEX_DIR/coverage-$NEXT.json" --taken-before "$PENDING") || STAMP_RC=$?
if [ "$STAMP_RC" -ne 0 ]; then
  echo >&2
  echo "ERROR: $NEXT was BUILT but its freshness stamp was NOT written (rc $STAMP_RC)," >&2
  echo "       so it is not swapped in; the previous $DB is untouched. Re-run the stamp, then the swap:" >&2
  echo "         (cd \"$X4VALIDATE\" && uv run python \"$HERE/staleness.py\" --write --db $DB --coverage \"$BASEX_DIR/coverage-$NEXT.json\" --taken-before \"$PENDING\")" >&2
  echo "         (cd \"$X4VALIDATE\" && uv run python \"$HERE/dbswap.py\" swap --db $DB)" >&2
  exit 1
fi

echo
echo "== swapping $NEXT in as $DB =="
# Verify (stamped report naming $NEXT, document count equal to it), rename $DB aside, rename $NEXT into place,
# write coverage-$DB.json, drop the previous generation. rc 7: a reader holds $DB open -- nothing was swapped,
# retry the printed command when it finishes. rc 2: verification refused; the previous $DB is untouched.
SWAP_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" swap --db "$DB" --owner "$OWNER") || SWAP_RC=$?
if [ "$SWAP_RC" -ne 0 ]; then
  echo "ERROR: $NEXT was built and stamped but NOT swapped in (rc $SWAP_RC); see the reason above." >&2
  exit "$SWAP_RC"
fi

# Staging is transient by design — the index is the durable artifact. Keep it
# only if KEEP_STAGE=1 (useful when debugging an extraction failure).
if [ "${KEEP_STAGE:-0}" != "1" ]; then
  (cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4basex_artifacts.py" --artifact-root "$HERE" --reference "$REFERENCE" "$STAGE")
  echo "staging discarded (KEEP_STAGE=1 to keep it)"
fi

exit "$COVERAGE_RC"

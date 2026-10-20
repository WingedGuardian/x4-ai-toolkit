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
# before the DROP DB below (issue #10). A missing reference used to be refused only by
# BaseX's "not found" AFTER x4raw had been dropped; an EMPTY one beside installed mods
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

# Java is a native Windows process and does not understand Git Bash's /c/... form:
# passing it verbatim made BaseX look for "C:/c/Users/..." and report the resource
# missing — while the build still exited 0. Hand Java a real Windows path.
winpath() { cygpath -m "$1" 2>/dev/null || echo "$1"; }
STAGE="$HERE/_stage"
STAGE_WIN="$(winpath "$STAGE")"

echo "== staging packed content =="
(cd "$X4VALIDATE" && uv run python "$HERE/stage.py" --out "$STAGE" --extensions "$EXTENSIONS")

echo
echo "== building $DB =="
cd "$BASEX_DIR"
# Revoke the previous build's licence FIRST (AUDIT-2026-09-24 BX-3). coverage-$DB.json
# carries supports_negative_claim AND the freshness fingerprint of the database about to
# be dropped; left in place, a build that dies from here on (BaseX rejecting an input
# exits 1 below; an interrupt) leaves that licence beside a partial database, and ask.py
# certifies negatives over it. Absent coverage means no negative is ever certified.
(cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4recycle.py" "$BASEX_DIR/coverage-$DB.json")
java -cp BaseX.jar org.basex.BaseX -c "DROP DB $DB" >/dev/null 2>&1 || true

# BaseX exits 0 even when an ADD names a path it cannot find, so the exit code is
# not a usable gate. Capture the output and fail loudly on "not found".
BUILD_LOG="$(mktemp)"
java -cp BaseX.jar org.basex.BaseX -c "
SET SKIPCORRUPT true
CREATE DB $DB
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
  echo "ERROR: BaseX rejected at least one input above — the index is INCOMPLETE." >&2
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
(cd "$X4VALIDATE" && uv run python "$HERE/coverage.py" --db "$DB" --stage "$STAGE" \
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
STAMP_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/staleness.py" --write --db "$DB") || STAMP_RC=$?
if [ "$STAMP_RC" -ne 0 ]; then
  echo >&2
  echo "ERROR: $DB was BUILT but its freshness stamp was NOT written (rc $STAMP_RC)." >&2
  echo "       An unstamped index reads STALE forever and every CLI banners it, so this" >&2
  echo "       is a failure even though the database is fine. Re-run just the stamp:" >&2
  echo "         (cd \"$X4VALIDATE\" && uv run python \"$HERE/staleness.py\" --write --db $DB)" >&2
  exit 1
fi

# Staging is transient by design — the index is the durable artifact. Keep it
# only if KEEP_STAGE=1 (useful when debugging an extraction failure).
if [ "${KEEP_STAGE:-0}" != "1" ]; then
  (cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4recycle.py" "$STAGE")
  echo "staging discarded (KEEP_STAGE=1 to keep it)"
fi

exit "$COVERAGE_RC"

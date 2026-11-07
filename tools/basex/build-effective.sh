#!/usr/bin/env bash
# Build the x4eff BaseX database: X4's EFFECTIVE merged tree, not files as written.
#
#   x4raw  every file AS WRITTEN, per mod   -> "who WROTE this, in which mod"
#   x4eff  _merge.build_effective per vpath -> "what does the ENGINE SEE"
#
# Both are wanted; x4eff is the one a negative claim should be made against,
# because it applies diffs in load order and resolves conflict winners.
#
# ADVISORY LIMIT: inter-mod load order is the engine's MEASURED signature-check
# order (BLIND-SPOTS F128), not documented by Egosoft; that it is also the
# patch-apply order was MEASURED by the in-game load-order probe (2026-09-26,
# AUDIT LO-2). Other install roots are unobserved, so an x4eff answer that
# depends on WHICH mod won is advisory to that degree.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASEX_DIR="$HERE/basex"
. "$HERE/_x4v-tree.sh"
X4VALIDATE="$(x4v_resolve "$HERE")"
x4v_announce "$X4VALIDATE"
EFF="$HERE/_eff"
DB=x4eff
MANIFEST="$EFF/effective-manifest.json"

winpath() { cygpath -m "$1" 2>/dev/null || echo "$1"; }

# Fail BEFORE the long work, not after it. build-effective.py takes minutes and
# java is not touched until well after that, so a missing JVM used to surface as
# a class-not-found error at the end of a build that had already done its work.
# uv is checked here in bash because everything else, preflight included, is
# invoked THROUGH it.
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not on PATH. This script drives x4validate via 'uv run python'." >&2
  echo "       Install it from https://docs.astral.sh/uv/" >&2
  exit 2
fi
# THE REFERENCE MUST HOLD THE GAME, checked BEFORE preflight (which runs java) and
# before the `rm -rf` of the previous tree below (issue #10): build-effective.py builds
# from the CONFIGURED reference, which is what this checks. rc 2 = configuration.
echo "== reference =="
REF_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/reference_check.py" \
    --what "serialize the effective tree for $DB") || REF_RC=$?
if [ "$REF_RC" -ne 0 ]; then
  echo "       Nothing was deleted or indexed; the previous tree and $DB are untouched." >&2
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
(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" prepare --db "$DB")

# Delete the previous run's artifacts BEFORE building, not after.
#
# MEASURED 2026-08-24 by crash-injection on this exact script: with a stub that
# exited non-zero, the run continued, indexed a LEFTOVER tree from the previous
# build, reconciled the new DB against the PREVIOUS manifest, stamped it FRESH,
# and exited 0. Every downstream reader would have been told a crashed build
# succeeded. The manifest is written by build-effective.py itself (line ~242),
# so its ABSENCE is the honest signal that the builder never finished -- but
# only if a survivor cannot masquerade as it.
(cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4basex_artifacts.py" --artifact-root "$HERE" "$EFF/tree" "$MANIFEST")

echo "== serializing the effective tree =="
# THE FINGERPRINT OF WHAT THIS BUILD READS, taken before it reads anything (T1, R2-7). Stamped at the end, a mod changed
# while the build ran read FRESH for ever over the old values. A failure here stops the build: set -e.
PENDING="$BASEX_DIR/pending-fingerprint-$DB.json"
(cd "$X4VALIDATE" && uv run python "$HERE/staleness.py" --snapshot "$PENDING" --db "$DB")

BUILD_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/build-effective.py" --out "$EFF/tree") || BUILD_RC=$?

# rc 3 is NOT a crash. build-effective.py returns `0 if not failures else 3`
# AFTER writing the manifest: the tree is built and the manifest describes it
# accurately, some vpaths simply failed to merge. Continuing is correct, and
# coverage.py reconciles the shortfall. Any OTHER non-zero means it died before
# writing the manifest, so there is nothing to reconcile against and going on
# would build an index nobody can score.
if [ "$BUILD_RC" -eq 3 ]; then
  echo "  (build-effective reported per-file failures — see effective-manifest.json)"
elif [ "$BUILD_RC" -ne 0 ]; then
  echo >&2
  echo "ERROR: build-effective.py died with rc $BUILD_RC before writing a manifest." >&2
  echo "       Refusing to continue: the DB would be built from whatever tree" >&2
  echo "       happened to be on disk and scored against a manifest that does not" >&2
  echo "       describe it. Nothing has been indexed or stamped." >&2
  exit "$BUILD_RC"
fi

if [ ! -f "$MANIFEST" ]; then
  echo >&2
  echo "ERROR: build-effective.py exited $BUILD_RC but wrote no manifest at" >&2
  echo "       $MANIFEST -- so coverage has no denominator. Refusing to continue." >&2
  exit 1
fi

EFF_WIN="$(winpath "$EFF/tree")"
echo
echo "== building $NEXT (the live $DB stays in place until the swap) =="
cd "$BASEX_DIR"
# coverage-$DB.json is NOT revoked here any more (it was, AUDIT-2026-09-24 BX-3, because the build used to drop
# $DB first and a dying build left that licence beside a partial database). The partial database is now $NEXT,
# which ask.py never reads, and coverage-$DB.json keeps describing the $DB that is still in place.
# The creation receipt is written before CREATE DB; `prepare` above already dropped any earlier $NEXT.
(cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4basex_artifacts.py" --artifact-root "$HERE" --declare-db "$NEXT")

BUILD_LOG="$(mktemp)"
java -cp BaseX.jar org.basex.BaseX -c "
SET SKIPCORRUPT true
CREATE DB $NEXT
SET CREATEFILTER *.xml
ADD TO / $EFF_WIN
OPTIMIZE ALL
INFO DB
" 2>&1 | tee "$BUILD_LOG"

if grep -qi "not found\|Improper use\|Stopped at" "$BUILD_LOG"; then
  echo "ERROR: BaseX rejected an input above — $NEXT is INCOMPLETE and is not swapped in; $DB is untouched." >&2
  rm -f "$BUILD_LOG"; exit 1
fi
rm -f "$BUILD_LOG"

# The serialized tree is large and reproducible; the DB is the durable artifact.
if [ "${KEEP_EFF:-0}" != "1" ]; then
  (cd "$X4VALIDATE" && uv run python "$HERE/../../scripts/x4basex_artifacts.py" --artifact-root "$HERE" "$EFF/tree")
  echo "serialized tree discarded (KEEP_EFF=1 to keep it); manifest retained"
fi

echo
echo "== reconciling x4eff coverage =="
# Report the verdict, do not abort: the DB is built and usable, it simply cannot
# back a NEGATIVE claim until the delta is explained.
#
# Exit codes on THIS path (--eff-manifest) are 0 complete / 4 unexplained / 2
# refusal. Note it differs from the x4raw path, which has a third tier:
# {complete: 0, accounted: 3, unexplained: 4} (coverage.py:305). "accounted"
# means the whole deficit is explained by named unparseable files, and the eff
# path has no such tier -- do not copy build-corpus.sh's "exit 3" wording here,
# because this path never produces a 3.
#
# The verdict is PROPAGATED (see the exit at the end) -- it used to be swallowed
# by `|| true`, which is how a build with an unexplained shortfall exited 0 and
# read to every caller as a clean build. build-corpus.sh has always propagated
# it; the asymmetry meant the two halves of one corpus reported differently.
COVERAGE_RC=0
(cd "$X4VALIDATE" && uv run python "$HERE/coverage.py" --db "$NEXT" \
    --eff-manifest "$MANIFEST") || COVERAGE_RC=$?

# x4eff is ENGINE-dependent: it is a product of _merge, so a merge fix
# invalidates it even when not one input file changed. That is exactly how this
# DB went eleven days serving pre-fix values. Stamp both axes.
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
(cd "$X4VALIDATE" && uv run python "$HERE/dbswap.py" swap --db "$DB") || SWAP_RC=$?
if [ "$SWAP_RC" -ne 0 ]; then
  echo "ERROR: $NEXT was built and stamped but NOT swapped in (rc $SWAP_RC); see the reason above." >&2
  exit "$SWAP_RC"
fi

exit "$COVERAGE_RC"

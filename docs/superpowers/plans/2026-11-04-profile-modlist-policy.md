# Profile mod-list write policy

The user requested ordinary mod-file treatment for creating and editing root profile `content.xml`, including a new numeric profile that is not selected by `X4_PROFILE`.

## Acceptance

- Root mod-list creation/editing is advisory through file guards and complete Action v1 batches.
- Simple recognized shell data writes and a verified regular-file copy follow the same policy.
- Compound/opaque shell commands keep their previous profile confirmation; preflight must not infer future destination kinds or borrow another operation's exemption.
- Configuration/nested-file confirmation, destructive actions, save transactions, and game/reference blocks are not relaxed.
- Canonical targets, directories, symlinked copy sources and multiply-linked files cannot gain a mod-list exemption.
- Regenerate all adapters from `agent/`; update instructions, README and changelog.
- Independent review and a post-review scratch transaction precede integration. Game-root deployment additionally requires green CI on the exact commit. No game launch is part of this task.

## Implementation

The existing file policy only exempted the configured profile's exact mod-list. Its generic `Egosoft/X4` backstop asked for an otherwise identical file in another profile. Extend the exemption to the exact root filename of numeric profile IDs, using canonical paths. Existing directories and multiply-linked files retain confirmation.

Keep the shell's raw `writes_profile` fact truthful. A separate policy fact withholds extra confirmation only for verified mod-list write occurrences. A single recognized primitive is required; destination kind/link checks use case-preserving physical paths and the existing native-path bridge loaded from the same checkout. Copy sources must be a single regular non-symlink file. Rejected occurrences dominate approved occurrences sharing a target tuple.

This is a policy change about root mod-list writes, not a general repair to profile discovery. Preexisting shell handling of another profile's configuration when a selected `X4_PROFILE` excludes it is outside this change; no claim of universal file/shell parity follows.

## Verification record

The initial regression reproduced three failures: new and existing secondary-profile files, plus a complete three-file Action v1 request. Five protection controls passed.

Independent review identified physical-target, directory-kind, fallback-container, temporal-command, occurrence-collision and hard-link issues in intermediate candidates. Concrete fixture checks reproduced the affected cases before repairs. Intermediate successful test counts are historical, not evidence for the final revision. Broad checks interrupted for source changes are incomplete and must not be counted as passes.

Final focused candidate check: 31 passed, one Windows file-symlink privilege skip. Independent read-only reviewer reports no remaining critical/important source findings; this does not certify live host enforcement or CI.

Post-review checks: 254 regression tests passed with six Windows symlink-privilege skips; 912 parser tests passed; the corrected smoke run passed all 339 probes with zero failures/skips. The focused 31/1 result was rerun after making the fixtures portable to Linux (project-owned sandbox rather than shared /tmp) and replacing numeric examples with the standard placeholder. The privacy scan then read 911 candidate files, including both new untracked source/plan files, with zero findings. Private generated fixture trees were excluded only in the smoke process's Git configuration; no product source was excluded.

The expected smoke denominator is independently derived: old333 + two new file probes in each of two layouts + two new shell probes =339. The first complete run had338passing probes/one privacy failure and the old denominator; the privacy checker correctly rejected numeric profile examples. Neither intermediate run is recorded as green.

Assessed confidence:99% that the original three-file new-profile refusal is repaired in the tested source;95% preservation of the covered guard safeguards. Windows file-symlink cases and POSIX execution still require their CI legs. These estimates are not measurements or a claim of complete parser coverage.

The scratch E2E models the original three-file request, receives a fully evaluated guard allowance, creates the three files only inside its fixture, rereads exact bytes and verifies the old profile is unchanged. No actual user profile, mod installation, save or game process is touched.

## Integration and deployment

USER ruling 2026-11-04: the existing toolkit release session owns publication. The ROR session returns to vanilla ROR and will not start another RC3 publisher. Verified follow-up on local master is `7f4f4873`; its current status and the refused attempt are recorded below. Preserve the release session's concurrent work. Reusable capabilities remain toolkit-owned; ROR-specific code and receipts remain in the ROR workstream. This records ownership, not a claim that publication or deployment occurred.

2026-11-04 publication gate result: the authorized push of `a340b4a38687bd12eafbc979bdef3b89ff1e3ce4` was REFUSED locally; nothing was published or dispatched. The changed-file run finished with 2 failed, 3830 passed, 29 skipped, 2 deselected and 6 subtests passed in 3629.20 seconds; total pre-push gate time was 4266 seconds. Failures were the fuzzer census treating raw `writes_profile` as an unwired rule, and the new fixture prefix missing from housekeeping. Reviewed follow-up `7ce3f5af` (also landed on local master as `7f4f4873`) classifies the raw diagnostic while requiring the refined `writes_profile_other` ask rule, and reuses the registered hook-test prefix. Production guard bytes are unchanged. Post-review focused validation passed 168 tests with one Windows file-symlink privilege skip. A complete replacement pre-push/CI verdict is still required; the focused repair check is not a green publication gate.

A scoped timing check compiled immutable before/after Git blobs and ran six Bash-parser cases with 30 alternating repetitions per case. Largest observed median increase was 0.962 ms for a mod-list write; shell startup and installer time were not measured. The initial worktree-based timing control was INVALID because the other release checkout had advanced to the same code; it was discarded. A concurrent release checkout has merged the original fix and has further core/CI work in progress: preserve its working files and verify the current published batch before any retry. No ROR content, actual profile, save, deployment or game launch is part of this publication work.

The mutation tester's isolated snapshot initially omitted the new sibling native-path helper. Its consumer regression failed before adding save_policy.py to the snapshot manifest, then passed. The updated focused module passed 32 tests with one Windows file-symlink privilege skip; the actual isolated mutation baseline ran all 912 parser tests successfully. Independent follow-up review found no issues in this verification-only addition. Full mutation trials were not run here: five stale exact-text anchors were measured unchanged from the starting master and remain outside this fix. Do not describe that baseline as a green mutation gate or exact-commit CI.

Implemented source commit: `af36b24b14f1c0db6fcb2838ec4353cb5ed4748b`. The 26 tested source/generated/test/documentation paths were integrated to current master with exact-byte verification; four unrelated CLI-reference working files were preserved. A check-only replay of the original three-file request through that committed neutral harness returned evaluated allow, noninert, preservation not required; all three actual target files remained absent. This is policy-evaluation evidence, not execution of those writes or deployment acceptance.

A detached checkout isolates tests without inventing a branch. Verified explicit paths land on current `master`; preserve unrelated working changes. Do not push, tag or release without user direction.

No source can be installed to a game root until independent review and exact-commit CI are both satisfied. Until then, the ROR profile/diagnostic transaction remains held and the previous manual-copy proposal is superseded by this guard repair request. Never weaken or split the refused live operation to bypass its guard.

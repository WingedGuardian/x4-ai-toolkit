# Typed deployed-mod retirement policy

## Acceptance and scope

A dedicated normalized file delete of a deployed mod folder or ordinary XML requests confirmation. It must not be denied by the rule that prevents editing an overwritten deployment. A normal write remains denied when a separate source root is configured. Whole-install, whole-extensions, reference, archive, settings and save protections retain their existing checks. A mixed action takes its worst verdict. No operation executes merely because policy was evaluated.

This repair does not permit deleting archives directly, change explicit Action-v1 move policy, authorize retirement without confirmation, or establish host enforcement. A move represented as source delete plus destination write must retain both endpoint checks. Explicit move operations remain conservative. No game launch is part of this task.

## Root cause and occurrence inventory

READ: `_verdict` sent every non-shell operation to `protect-files.sh` as a Write, then additionally checked delete/move through the shell policy. The deployed-copy write denial dominated the shell retirement confirmation. `scripts/test-hooks.sh` already expects deployed-folder and XML removal to confirm.

Three transport shapes were inspected: single-file `_verdict` is affected; `write_verdicts` is deliberately write-only and must remain so; native file requests carry their actual top-level tool name and must not borrow a free-form operation field. Generic Action v1 passes operations through; Codex and OpenCode patch adapters normalize move sources as delete and destinations as write. Explicit generic moves stay unchanged.

MEASURED baseline: three reproduced delete-policy failures (legacy folder, legacy XML, neutral Action v1 folder); ten controls passed. An earlier test setup selected the WSL launcher and was corrected to use the toolkit's Git Bash resolver before this baseline.

## Implementation

Preserve normalized delete kind in `guard_payload` as internal `DeleteFile`, retaining content/edit/patch fields for earlier protected-file checks. All other file kinds retain Write. Pass actual kind from `_verdict`. Only the deployed-copy branch recognizes the top-level delete tool name; the write-only batch explicitly cannot use it. Earlier file rules and the synthetic recursive shell-delete check still run. No probe-name exemptions, guard disabling, skipped policy layer or fabricated allowance.

Only canonical guard sources are edited; generated trees are rebuilt with the generator. Adapter grammars and normalization are unchanged.

## Assumptions and fallbacks

- 99% confidence in diagnosed masking: directly reproduced by the three failing cases.
- 95% confidence in operation-specific repair before tests: normalized kind is already available; the file policy can recognize it without trusting arbitrary metadata.
- 95% confidence in protection preservation before tests: earlier branches and shell check are unchanged; negative controls must confirm it. Any control regression stops integration.
- Host interception and live retirement remain unverified until reviewed source has exact-commit green CI, is deployed normally and the original complete request is re-evaluated. Do not route around an earlier refusal.

## Verification and delivery

Implemented source commit: `a3f282986e6082338fd399f2d22baa1bb9cd0a3f`.

Independent read-only review found no blocking source findings, confirmed all eight generated guard files match their canonical sources, and approved the README follow-up. MEASURED final isolated Windows suite: 428 passed, eight symlink-privilege skips, zero failures across 436 cases:

| Module | Passed | Skipped |
|---|---:|---:|
| deployed retirement policy | 26 | 2 |
| x4guard check | 68 | 0 |
| file guard batch | 62 | 5 |
| profile mod-list policy | 32 | 1 |
| settings guard | 29 | 0 |
| generated agent trees | 123 | 0 |
| Codex adapter | 35 | 0 |
| OpenCode adapter | 53 | 0 |

MEASURED isolated native hook smoke: 339 passed, zero failed/skipped. Privacy check: 913 files read, zero findings, including two then-untracked additions. Four unrelated CLI-reference working edits were hashed before integration and remained unchanged. The fourteen implementation paths were copied from the reviewed candidate with byte-for-byte readback; no game-root guard changed.

The first broader attempt selected the WSL launcher in tests that use PATH and was stopped, not counted as qualification. The final process put Git Bash first on PATH. The first smoke run was 338 passed/one failed: a concurrent check-only candidate diagnostic created its telemetry directory after the caller-file baseline. It remains recorded as failed/contaminated; the isolated 339/0 rerun supersedes it. Neither issue justified a product-policy relaxation.

MEASURED original-target transaction: candidate neutral Action v1 returned evaluated ASK, noninert; all three installed target files retained their hashes. No deletion occurred. This is end-to-end policy evaluation only, not retirement, Recycle Bin, host-interception or deployed-guard proof. Assessed confidence is 99% for the reproduced operation-classification correction and 95% for preservation of covered safeguards; skipped alias cases still need the CI platforms. Passing local source checks are not CI or live deployment.

Integrate verified explicit paths on current master, preserving unrelated work. Do not push, tag or release without user direction. The existing toolkit release lane owns publication; inspect its current intent and jobs before any source-bound CI request. Changed game-root guards require independent review and green CI on the exact commit before deployment. Until then the original retirement remains held.


Release-owner handoff: integrate the canonical source delta into the current release lane, regenerate against that lane's sources, and requalify the actual resulting head. Do not overwrite a newer generated tree with this checkout's old baseline. Changed guard bytes require renewed review. The current release batch was inspected read-only through paginated actual jobs and its remote branch head; it is a different source head and cannot qualify this fix. No run was dispatched, retried or cancelled here. No push, tag, release or game launch was performed.

After exact-head green CI and normal reviewed guard deployment, re-evaluate the complete original retirement request through the active guards. Honor any confirmation/refusal. Only then retire the authorized temporary probe recoverably with X4 closed, verify production jobs/scripts and file hashes, and preserve the passed runtime evidence. Do not repeat the already-passing game test merely to clear this policy blocker.

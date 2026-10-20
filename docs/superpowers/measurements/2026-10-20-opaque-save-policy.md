# Opaque save-policy measurement - 2026-10-20

MEASURED against a stable canonical parser; historical payloads were parsed and
fed to preview hooks, never executed. No save backup was created by the replay.

- Full replay: 26,346 distinct historical Bash payloads; 0 dropped input lines.
- Candidate controls: 4/4 positive/negative controls before the final replay.
- Final matches: 34; technical non-answers: 0; new incomplete-analysis flags: 0.
- Classes: 18 save analysis/copy calls and 16 guard/probe harnesses.
- Per-item verdict comparison: 9 allow -> ask; 21 allow -> deny;
  3 deny -> deny; 1 ask -> ask. All 34 compared, no non-answers.
- Prior reader narrowing removed 7 of 38 candidates. Final glued-option coverage
  then added 3 and removed 0 relative to the 31-match parser.

The 30 newly stopped historical calls measure conservative review friction.
They are legitimate historical work, not detected destructive programs.

Parser SHA-256: `8c40039976adfc8c1f426c8e52b624a6dd9eeb92eefd7126b1ea4cad53f1e76f`.
Baseline preview hooks: `739b5e9`. The private command corpus is not shipped.

Limits: the extractor omits per-command cwd. Explicit roots and directories
established in command text are represented; unknown implementations and host
interception are unmeasured. The opaque game/reference residual is unchanged.

Regression controls: `test_shared_save_policy.py` and `test_host_save_batches.py`.

Frozen corpus SHA-256: `2a05e96b812ce3a6b93d567abd7ee0b3efd3b17f23dd0b7bf6e98b94afea18ee`.

All three final additions were independently read as legitimate guard-testing
activity. The third uses an external helper whose implementation was not inspected.
Opaque glued root variables retain conservative refusal despite an ordinary
assignment; use literal resolved arguments. Command-wide assignment narrowing
was withdrawn to avoid assuming execution order.

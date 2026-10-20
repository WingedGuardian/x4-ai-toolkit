# Opaque save-policy measurement - 2026-10-20

MEASURED against a stable canonical parser; historical payloads were parsed and
fed to preview hooks, never executed. No save backup was created by the replay.

- Full replay: 26,346 distinct historical Bash payloads; 0 dropped input lines.
- Candidate controls: 4/4 positive/negative controls before the final replay.
- Final matches: 31; parse/carrier non-answers: 0.
- Classes: 18 save analysis/copy calls and 13 guard/probe harnesses.
- Per-item verdict comparison: 9 allow -> ask; 19 allow -> deny;
  2 deny -> deny; 1 ask -> ask. All 31 compared, no non-answers.
- Narrowing known reader forms removed 7 of 38 matches and added none.

The 28 newly stopped historical calls measure conservative review friction.
They are legitimate historical work, not detected destructive programs.

Parser SHA-256: `ff4e8060bfa455a07696c7ae42916ce282d51e9b81664af0cc0e84586bc5e368`.
Baseline preview hooks: `739b5e9`. The private command corpus is not shipped.

Limits: the extractor omits per-command cwd. Explicit roots and directories
established in command text are represented; unknown implementations and host
interception are unmeasured. The opaque game/reference residual is unchanged.

Regression controls: `test_shared_save_policy.py` and `test_host_save_batches.py`.

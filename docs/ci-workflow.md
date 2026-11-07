# Development CI

CI uses the [core-owned policy](https://github.com/WingedGuardian/modding-agent-harness/blob/38bf2cb8da9d4add2fa3c71341d641fafd265368/CONTRIBUTING.md),
pinned independently of the runtime harness revision.

Develop in a draft PR. Draft updates run Python syntax, lock freshness, workflow
contracts and identifier scans. The merge qualification check deliberately stays
blocked while full testing is deferred; green fast checks are not full
qualification.

Mark the PR ready to request both existing full OS jobs. Further code updates
require fresh qualification. Default-branch pushes and manual dispatch retain
full testing. The historical `ci/**` push procedure is replaced by a draft/ready
PR or manual dispatch; do not dispatch a duplicate of the same PR run.

Each repository has an independent full-run queue that retains started requests.
X4 retains its two-job Windows/Linux matrix, including every original check and
platform refusal. Fast draft checks remain independent of the full-run queue.

Retry failed jobs on the same revision while retaining original failures and
passing legs. Reports bind the source head and tested merge candidate and show
queue time separately from execution time. Missing or skipped required jobs
cannot qualify a ready PR. Mutation gates, skip ceilings and failure thresholds
are preserved. CI does not certify the installed game-dependent gates.

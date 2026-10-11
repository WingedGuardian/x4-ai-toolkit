# Capped step-1 gates

The 2026-10-07 takeover replaces blanket occupancy limits with the user's shared
resource-budget headroom policy. The resource tool is external to this repository:
40% total managed CPU, 8 GiB total managed memory, below-normal priority, at most
eight workers. Leave 6 GiB available RAM, 8 GiB available commit and 150 GiB free
on C:. Other output drives keep max(20 GiB, 5%). Estimate overruns warn; sustained
pressure throttles and blocks new batches; emergency floors stop only the job tree.
The resource tool's `HEADROOM.md` specifies the sample windows and emergency floors.
`scripts/run-gates.sh --all` explicitly passes `--workers=8` to historical hook
replay, whose standalone default remains up to 14. Other gate arguments are unchanged.
The runner invocation is exercised by `tests/test_run_gates_logs.py`.

MEASURED installer-only run, 2026-10-07: eight installer batches hit a 4 GiB job
cap during startup. Four workers completed all 414 selected installer and deploy
parity tests with zero skips in 606.5 seconds, peaking at 1.63 GiB job memory.
Use four workers for this selection under a 4 GiB cap; a mixed full-suite peak
does not establish the peak for eight installer batches starting together.

MEASURED full-gate budgeting on this workstation, 2026-10-08: a 4 GiB cap
stopped `run-gates.sh --all` in QA. The resumed QA-through-XSD tail completed
under a 7 GiB cap, with a 6.40 GiB job peak and 7.50 GiB minimum available RAM.
Use `--cpu 40 --ram 7` for this full sweep on this corpus, subject to admission;
the 4 GiB pytest budget does not establish QA's memory requirement. The policy
still reserves 6 GiB available RAM and limits all managed jobs to 8 GiB total.
The completed tail passed all 13 remaining gates; prior baseline drift and an
outdated load-order log remained failures/non-answers in the combined receipts.

From the toolkit root, using the installed resource-budget path as RB:

```
python RB/rb.py run --name x4-step1 --cpu 40 --ram 4 --disk C:/=10 --wait-until-fits 60 -- uv run --directory tools/x4validate python -B -u ../../scripts/run-release-gates.py --workers 8 --out REPORT_DIRECTORY
```

Use an external, new report directory. Add `--resume` to the step-1 command to
reuse completed stages only when source bytes, SHA, interpreter, installed input
fingerprints, relevant private configuration hashes, resource policy and deployed
hook bytes still match. Evidence file hashes must also match. Secret values are
not persisted. This runs the ten preliminary gates, hook mutation coverage and
pytest, stopping on the first unexpected failure. It does not deploy or run steps
2–5. The single real deployment-parity failure can be recorded as **pending**;
it is never reported as a clean pass. The exit code is read from the checkpoint,
never from the invocation: **0** only when every stage is recorded passed, **1** a
stage failed, **3** a stage is pending or was not run (`--through vht`). Until
v4.0.0's last fix round a pending stage or an early `--through` stop exited 0, and
`--resume` reused a pending row the same way (reviewer M-1).

Hook verification defaults to serial. `verify-hook-tests.py --workers N --report FILE`
gives each mutation/predicate trial its own copy and full hook-suite execution.
Parallel dispatch requires the resource monitor heartbeat. `--sample N` is only a
benchmark: it always returns 3 and cannot pass a release gate. Reports name every
trial's return code, failed test set and test count, plus input byte hashes.

Pytest's `scripts/run-pytest-batches.py` keeps unknown modules exclusive in one
process. The reviewed manifest initially enables seven module batches and function
batches for `test_install_over_existing.py`. Exact module and shared-helper hashes
pin those reviews; changed inputs fall back to exclusive execution. Each batch has
normal system-temp scratch space, separate from its durable report directory:
guard fixtures under `.claude/backups` would inherit that path's policy exemption.
`--serial` runs the original monolithic suite. `--compare A/results.json B/results.json`
compares every collected node, phase outcome, xfail, skip reason and collection skip,
refusing changed source fingerprints, missing tests and duplicate tests.
Skip comparison canonicalizes only the random nonce of this runner's directories
under the current system temp root. It retains error codes, filenames and messages.
The full serial/parallel run exposed three Windows symlink-privilege skips whose
messages differed only in those nonces; all remained skips. The correction changes
comparison only, with positive and negative twins in `tests/test_parallel_runners.py`.

MEASURED sample, 2026-10-07: the same 16 of 261 hook trials took 238.5 / 122.2 /
71.5 / 55.6 seconds with 1 / 2 / 4 / 8 workers. All 16 trial outcomes matched.
Eight is the smallest tested count within 10% of the fastest sample. Peak job memory
was 700 MiB at eight workers. Full-suite equivalence and end-to-end speed are separate
acceptance checks; these sample timings do not prove either. The resource suite
passed all 99 tests, including bounded CPU and memory-cap probes.

## Takeover findings

READ git history: the stale verb-resolution hook mutant followed FX-G7 (`fc2d357`),
but the missing settings rule row originated earlier (`dbaaf7d`) and the adapter
deadline anchor changed with result-aware settings checks (`d3f53d5`). This is not
one FX-G7 defect. The nine register failures were stale/missing test citations;
their existing tests are now named, with no baseline expansion. F253 records the
Unicode source-line checker defect. No production X4 XML or profile was changed.

UNMEASURED: Codex host fail-open behavior was not re-probed in this takeover.
Resource-job one-second CPU accounting peaks remain unsuitable as evidence of
machine saturation; the watchdog uses independent machine CPU samples and now
records their peak separately. Historical host measurements and cost estimates
must retain those qualifications.

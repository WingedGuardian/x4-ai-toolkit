# Codex long-job scheduling context

User-authorized repair, 2026-10-25. Scope: the existing X4 guard adapter, not
Skyrim headless scheduling, resource floors, or gameplay files.

## Cause and acceptance criteria

READ: `agent/guards/adapters/codex.py` translated native shell calls without
host scheduling context. `x4guard.guard_payload` rebuilt a shell payload carrying
only command and working directory. `hook_facts.facts` consequently interpreted
the missing Claude `run_in_background` field as a foreground long job.

READ: this Codex session's `exec_command` returns a session ID for a running job;
it exposes `yield_time_ms`, not Claude's `run_in_background` argument. Native
hooks can label those calls Bash and omit the original execution options.

Acceptance: known long jobs pass through Codex's yielding shell contract without
fabricating a Claude flag; default/Claude foreground rejection and destructive
command protection remain effective. No game launch or live test is needed.

## Repair

The native adapter attaches `execution_host=codex` to shell guard calls. The
neutral Action v1 entry preserves its explicit host. The shared guard passes
that context separately from command text and tool input. Only the two
Claude-specific scheduling facts (foreground long job and Bash timeout cap)
exclude Codex; the other checks remain active. Unknown/default hosts retain
their previous scheduling rules.

Canonical changes are in four `agent/guards/` files. Generated trees were
regenerated, not edited directly. No second guard, settings change, or permanent
resource exception was introduced.

## Measurements

- RED: six new regression cases, 3 failed / 3 passed before the repair. Both
  native Codex long-job cases and the neutral Codex action incorrectly denied.
- GREEN: `test_codex_long_jobs.py`, `test_codex_adapter.py`, and
  `test_gen_codex_tree.py`: **56 passed, 0 skipped**, 89.85 seconds. Includes a
  destructive-command twin and default/Claude foreground-denial twins.
- Mandatory `scripts/test-hooks.sh`: **319 passed, 0 failed, 0 skipped**.
- `git diff --check`: clean.
- Deployment dry run named exactly five changed hook files: two Claude shared
  helpers and three Codex adapter/helpers. Apply confirmed **46/46 Claude** and
  **49/49 Codex** managed files at parity. `hooks.json` was unchanged.
- Deployed Windows wrapper, command strings submitted as data only: a BaseX
  long-job probe returned no denial; a PowerShell reference-tree deletion probe
  returned the explicit read-only-reference denial. Neither command executed.

Limits: these tests establish adapter and wrapper behavior, not a guarantee that
every future Codex version invokes hooks. Existing host-enforcement warnings and
manual Action v1 checks still apply. The separate BaseX build encountered a Java
memory cap and then Windows recycle failures; neither is this scheduling defect.

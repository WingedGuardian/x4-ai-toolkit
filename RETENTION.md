# Retention: what agent work keeps, and for how long

Backups and evidence are the point of this toolkit, and left alone they eat the disk. Measured on a sibling
install (the Skyrim VR toolkit's) after six weeks: 44 GiB of agent output, of which about 20 GiB were exact copies of
bytes already on disk. Very little of it was *old*; it was *repeated*. This page is the policy, shared with the Skyrim
toolkit; `scripts/x4housekeep.py` implements the parts a tool can.

<!-- shared-policy:begin -->
## Shared policy (identical in the Skyrim and X4 toolkits -- change both)

### Certainty before removal

Age, size, an idle timer, a filename and a completed-task marker trigger review; none proves that unique bytes are obsolete. Housekeep retires a payload only when it re-proves an identical retained copy or a verified lossless replacement, or the operator has explicitly declared those exact bytes obsolete with a reason and evidence. Unknown usefulness is kept indefinitely. Failed or stale proof leaves the source in place.

This applies to ordinary cleanup and both archive tiers. Older plans do not bypass the apply-time checks. A backup vault is optional and disabled by default; archive retirement needs a separate exact-plan approval and a working recycle route.

### Destination follows creation-time disposition and current proof

Permanent deletion is eligible only when the producer declared the item disposable
at creation and rebuildability is checked now. A receipt alone is not current
proof; a current rebuild test alone is not a creation-time disposition. Never
backfill receipts onto legacy material. Incomplete proof demotes an otherwise
authorized retirement to the Recycle Bin; unknown value remains kept.

Uncommitted work, unique logs/evidence, long-run outputs and handwritten material
can be the only copy and belong in the Recycle Bin when retirement is justified.
Worktree proof includes a clean tree plus a branch committed and pushed; this is
not permission to push just to make cleanup eligible. Same-run scratch may still
be deleted by its creator at exit. An item Windows cannot recycle needs the
user's exact-item decision, never a silent permanent fallback.

Housekeep currently uses recycling for cross-run retirements. It does not infer
permanent eligibility from a folder name, ownership marker or file extension.
NAS backup payloads also use the recycler after exact-plan approval; without a
working recycle route they stay in place. No automatic Bin purge is provided.

The Windows session-start report warns at `recycle_bin_warn_gib` (default 10 GiB;
0 disables the warning). It reads aggregate contents only and reports unavailable
queries separately. The size is logical contents, not guaranteed reclaimable
physical allocation. Report permanent deletions, recycling and failures separately.

### Classes

| Class | What | Rule |
|---|---|---|
| **Forever** | Saves and every copy of them; backup stores and their manifests (except explicitly configured rolling tool stores below); knowledgebase snapshots; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Evidence** | Findings, READMEs, receipts (`.md`, `.json`, `.jsonl`), small logs; every file a project doc links to | Kept. They are small, and docs point at them. |
| **Recreatable** | A byte-identical copy of a file that is still live (a fixture copy of a game master) | Removed as soon as it is idle: the live file is the copy. |
| **Build output** | Compiler intermediates inside build folders | Review candidates only. Removal requires an explicit content-bound obsolete decision. Build products and active work remain protected. |
| **Finished work** | A task folder that is done: no write for `archive_after_days` (default 14; down to `min_idle_days`, default 7, while agent output is over `budget_gib`), no read for `read_veto_hours` (default 48), not named in the instruction file's Live state block, not pinned with `.housekeep-keep`, no save, backup folder or git repository/worktree, no ownership receipt without qualified release proof. A session can mark it done early with `.housekeep-closed`; independent holds still apply. | Zipped to the archive folder, the zip read back and checked, a short `ARCHIVED.md` (with a restore command) left behind, then the bulk recycled. Top-level `README*`, `.md`, `.json`, `.jsonl` and `.log` files under 1 MiB stay local as well as in the zip; every other file then lives only in the zip. The archive is local by default; another drive or a NAS is optional, never assumed. Archive age triggers review only; unique archives stay until explicitly declared obsolete. |
| **Duplicate** | The same bytes at several agent-output paths | Stored once: hard links to one read-only copy. Every path keeps working. |
| **Rolling** | Per-launch logs, snapshot folders, named scratch beds | Compression requires a verified lossless replacement. Snapshots require an identical retained generation. Unique logs and scratch need explicit content-bound obsolete decisions; age alone never deletes them. |
| **Rolling tool store** | Configured rolling snapshots, including stores under backup folders | Retire only a byte-identical repeated generation after re-proving the retained copy. Newest and distinct generations stay. |
| **Toolkit temp** | Leftover workspaces explicitly declared disposable by their producer when newly created | Name, ownership and age alone are insufficient. Require a producer disposition receipt or explicit content-bound obsolete decision, then the ordinary protection, inactivity and in-use checks. Legacy unmarked workspaces stay. |

### The toolkit cleans up after itself

Measured on one machine on 2026-10-18: one day of local test runs left about 25 GiB of copies in pytest's
temp dir, and 14,018 small directories from earlier runs had built up since August. None of it was ever read
again. So:

1. **Code and tests remove what they create, in the same run.** A temp dir is made with a cleanup registered at
   the moment it is created, never "deleted at the end" where a crash skips it. Same-run scratch may bypass
   the Recycle Bin; cross-run permanent deletion needs the separate receipt and current-proof gates. A test enforces cleanup,
   and every prefix the code creates must be one the sweep knows.
2. **Session start sweeps the leftovers** of earlier runs (a crash, a killed job) to the Recycle Bin, in the
   background, at most every 6 hours.
3. **Session start says what it cannot fix**: free space near the floor, an agent's session store past its
   threshold, a pile of git worktrees. One line, only when there is something to say.
4. **Full test suites run in CI, not on the user's machine.** Locally: the tests for the files that changed.

### Rules every removal follows

1. **Report, plan, apply -- never one step.** The plan records each file's hash; apply re-checks it and refuses
   anything that changed.
2. **Recycle Bin (or Trash) by default.** Permanent eligibility requires both gates above, except same-run scratch.
   An item too big for the Bin, a network item without a recycling route, a path that really lands elsewhere (through
   a junction or symlink, or on a SUBST drive), a removable volume, a machine policy that disables recycling, or
   unknown/disabled Bin capacity stays in place and is reported in-session. Windows' shell destroys a file on a network
   share silently, so this refusal is the only protection there. Any exact-item permanent-deletion decision belongs to the user; cleanup
   never silently falls back to it. Recycling frees no disk space until the Bin is deliberately emptied.
3. **Leave active work alone.** Task archival checks whole-task write/read activity, pins, Live state, a configured
   job ledger (`job_ledger`; one that cannot be read is announced and does not hold archiving), ownership receipts and
   repository/save/backup-folder protections. Recent reads independently veto archival. If read recording cannot be proven with same-run owned scratch on the same volume, only an explicitly
   closed folder can qualify. A closure bypasses write-age only; it does not bypass the independent hold gates.
4. **Never touch a git-tracked file**, and never hard-link to a live game file.
5. **Accounting must sum.** Every scanned byte lands in exactly one row; the row of bytes left alone prints even
   at zero.
6. **A changed rule acts only after a person has seen its output.** Archive/retirement actions require the
   cleanup version the user approved. An updated version first produces a review; it does not approve itself.
   The optional session daily runner applies only its approved version. X4 task archiving is opt-in and manual.
7. **Never unattended, never a blindside.** Cleanup runs only from an open agent session, which announces it --
   what, which approved version, where the log is -- before it starts. No background service or scheduled task, no
   window or focus change, and no unattended permanent-deletion prompt: an item the bin cannot
   take stays where it is and is reported.

   **The one exception to rules 6 and 7 is the toolkit temp sweep** (above). Session start launches it detached, with no
   announcement and no approved version, and the next session start reports what it did. It touches only workspaces the
   toolkit's own producer declared disposable when it created them, after the protection, inactivity and in-use
   checks. It moves them to the Recycle Bin, except a workspace whose creation receipt the toolkit's checklist names as
   proof of disposability: that one is deleted permanently, since the Recycle Bin frees nothing on the same drive.
   Failure evidence inside a workspace is recycled, never deleted. Each toolkit's checklist names its receipts and
   how to turn the sweep off.

### Where agent output goes

One folder per task. Findings and receipts at the top level; bulk (copied game files, archives, build trees, raw
log copies) in a `payload/` subfolder. Docs link to the top level, so the bulk can be trimmed without breaking a
link.

Cleanup is the second line of defence; writing less is the first:

- **Reference, don't copy.** Evidence records a commit, a hash and a diff, never a full copy of a tree, bundle,
  database or game file per run. Stage unchanged inputs by reference where the tool allows, otherwise
  copy them; never hard-link them (a hard link is the same file, so a write to the staged copy changes
  the source).
- **One build folder per task.** Regenerated outputs overwrite, or keep the newest two.
- **Close the task.** Keep the README and receipts; retire bulk only after replacement or obsolescence proof, and write
  `.housekeep-closed`. A multi-GB folder that is genuinely needed later gets `.housekeep-keep` and one README line
  saying why; `report` and `plan` name every pinned folder over `pin_warn_gib` (default 1 GiB), as does the optional
  daily note.
- **Budget.** Agent output has a size budget (`budget_gib`); over it, finished work is archived oldest first, and
  the optional daily runner reports measured logical growth separately from physical drive usage.

### Ordinary archive review

Ordinary task archives and the optional cold-backup vault are separate inventories. Both are reviewed; one is
not coverage for the other. `archive_review_days` defaults to 30, with `archive_review_seconds` 15 per ordinary
archive review, from an open session only. A failed/partial review retries sooner and offers no retirement.
`archive_retention_days` is accepted for compatibility; age is never authority to remove an archive, and setting
it to zero does not disable certainty-based review. Unknown usefulness stays indefinitely.

Only an explicit `obsolete_items` entry bound to the current archive bytes, with
`basis: operator-confirmed-obsolete`, a reason and evidence can produce an ordinary archive retirement candidate.
Its `content_hash` is the current BLAKE2b digest with 16-byte output. Apply checks those bytes again, plus the
configured namespace and independent protections. Source group names such as `backups` do not hide tracked,
linked, save, baseline or custom protections. NAS retirement still needs a working recycler; failure leaves it.

The source check `check-housekeep-parity.py PEER_SOURCE_ROOT` compares both actual shared policies, their pins,
and both shared archive helpers. The local pin tests alone do not prove that the sibling repository agrees.

### Agent transcripts

- **Claude Code** deletes its own transcripts, file history, plans and session scratchpads after
  `cleanupPeriodDays` (default 30). Set it in **user** settings (`~/.claude/settings.json`) only.
  **Never ship it in a project's `.claude/settings.json`:** the sweep covers every project on the machine and
  uses the merged settings of whichever session starts it, so a toolkit-shipped value would delete the user's
  other projects' transcripts at the toolkit's number (code.claude.com/docs: settings-reference
  `cleanupPeriodDays`; monitoring-usage `retention_sweep`). Organisations that need one value everywhere set it
  in managed settings.
- **Codex** keeps sessions under `~/.codex/sessions/`; this toolkit knows of no automatic pruning there.
  `housekeep report` shows its size; removing old sessions is the user's call.
<!-- shared-policy:end -->

## X4 checklist

- **BaseX generated caches do not need backup generations or recycling.** Use the
  builders' native `DROP DB` for `x4raw`/`x4eff` and checked permanent disposal via
  `scripts/x4basex_artifacts.py` for generated staging/effective trees and stale
  cache licences. Inputs are checked now. New tree and database generations get
  disposition receipts before creation; database receipts stay outside BaseX's
  private data format. Recognized canonical XML-only legacy workspaces are
  covered by the explicit BaseX cache-disposal decision, without inventing old
  creation receipts. Custom unreceipted workspaces, links and unexpected files
  stay. Failure stops the build; it never falls back to the Bin or a backup copy.
  Keep hand-written findings and logs outside disposable cache directories.

- **Pytest has its own disposable directory per invocation.** Both the main and
  BaseX suites avoid pytest's shared numbered-directory cleanup. Legacy unmarked
  runs and other projects' runs are preserved. A caller's `--basetemp` must name
  a new directory; existing output is refused before pytest can wipe it.

- **Automatic Temp cleanup requires producer-declared disposability.** Only a receipt written when this
  toolkit creates an empty directory declares it disposable (version 2). A legacy version-1 ownership receipt alone is insufficient. Legacy unmarked directories,
  matching names alone and other projects' pytest runs are left alone. High-volume
  QA and pytest producers write receipts; other unmarked scratch remains manual.
  Receipts are checked against both the canonical toolkit and directory paths.
  Set `X4_HOUSEKEEP_SWEEP=off` to disable the session sweep.
  A workspace carrying the version-2 receipt (`.x4-temp-owner.json`, purpose "disposable temporary workspace") is
  deleted permanently. One that still holds a failed test's folder (the test suite marks it with
  `.x4-kept-failure`) is failure evidence and goes to the Recycle Bin instead. A directory with a file dated in the
  future, or with only a version-1 ownership receipt, is kept and named in the report.
  A protected file, unreadable tree, active descendant or failed probe keeps the
  candidate in place and is accounted for. A failed probe restore names its retained
  location. Tests use a fake recycler and cannot invoke the real Recycle Bin.

- **The user profile is Forever.** Everything under `Documents\Egosoft\X4\<profile-id>\` -- saves
  (`save\*.xml.gz`), `content.xml`, UI data -- is never planned. A save is also recognised wherever a copy sits: by the
  game's names (`save_NNN`, `quicksave`, `autosave_NN`) or by a `<savegame>` root, whatever it was renamed to. Other
  profile files are recognised only under that folder.
- **Per-edit backups** (`.claude/backups/<stamp>__<path>`, written by `backup-before-edit.sh`) have no age-out and
  are Forever under the shared policy: they are often the only copy of a file's earlier state. `report` shows
  their size; pruning them is the user's decision. `known-good-*` baselines, `AUDIT_LOG.txt` and `GUARDS-OFF.log`
  are Forever too.
- **The game folder is a live root.** Point `live_roots` at it and a fixture copy of a `.cat`/`.dat` pair becomes
  recreatable.
- **Agent output** has no fixed folder here (work goes to the session scratchpad). If you keep one, list it in
  `agent_output`.
  Deduplication keeps pre-existing hard links because their other names may be live
  files outside those roots; apply rechecks ownership before changing permissions.
- **Not covered by the tool:** git worktrees a session leaves behind (`git worktree list`; each is a full checkout)
  and the BaseX databases (rebuildable from the repo).

## Running it

Task archive workflow: configure `agent_output`/`archive_roots` and `archive_root`, enable `archive_enabled`,
then run `python scripts/x4housekeep.py version` and `python scripts/x4housekeep.py plan --only archive,expire -o archive-plan.json`.
Review that version and the full proposal before recording its digest in `approved_sha256`; then an announced
in-session `apply archive-plan.json --only archive,expire` rechecks it. X4 does not start an automatic task archive
run. An unreachable NAS, stale proof, active owner or unqualified Bin route leaves local material in place.
Archive inventories may include another drive/NAS; logical totals are not C: allocation or immediate recovery.


Optional backup preservation on secondary storage: the cold backup archive guide, `docs/BACKUP-ARCHIVE.md`.
That file is in the repository (https://github.com/WingedGuardian/x4-ai-toolkit) and the release zip; an
installed copy does not carry `docs/`.
It is opt-in, verifies every archived byte, retains unknown usefulness indefinitely,
and requires a separate evidence-backed, exactly approved plan for recycling obsolete archives.

```bash
cp x4-housekeep.json.example x4-housekeep.json      # then list your own folders in it
python scripts/x4housekeep.py                       # report: changes nothing
python scripts/x4housekeep.py plan -o plan.json     # every action, with the hash it was planned on
python scripts/x4housekeep.py apply plan.json [--only link,gzip]
```

`python scripts/x4housekeep.py sweep-temp` runs the temp sweep by hand. The session-start hook runs
`x4housekeep.py session <hooks>/../housekeep-session.txt` detached and shows that file's line at the next start.

`apply` runs the classes in a fixed order (recreatable, build, link, gzip, launch, snapshot, scratch, archive, expire)
and prints every refusal with its reason. A refusal is not an error to work around: re-run `plan`.
Set `job_ledger` to a resource-job ledger if your tooling keeps one (`{"entries": [{"cwd": ...}]}`); the default is none.

### Explicit obsolete decisions

Use top-level `obsolete_items` for unique ordinary cleanup candidates. Each key is an exact absolute path:

```json
"obsolete_items": {
  "/absolute/retired-item": {
    "basis": "operator-confirmed-obsolete",
    "reason": "Explain why this specific state is no longer needed",
    "evidence": "Reviewed receipt, replacement or operator decision",
    "content_hash": "<current housekeep content hash>"
  }
}
```

Files use BLAKE2b-128; directory generations use the SHA256 manifest digest returned by `housekeep_backups.content_hash(path)`. Record the decision only after review, never generate declarations merely to clear a report. Changed content invalidates it. Saves, tracked/linked evidence, pins and active work remain protected.

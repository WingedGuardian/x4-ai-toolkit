# X4 game driver: start the game, run checks, close it (design, draft for approval)

Status: APPROVED 2026-11-24 by the user ("you can do what you need to do ... ultimately the build is to get it to the point where you can run it all headlessly"). Nothing is built. Measured and read facts are tagged; anything else is a guess and says so.

End state (user, 2026-11-24): the session runs the whole cycle with no human step, including reaching a world. Until the load step is measured, the user loading the save by hand is an accepted temporary fallback, never the design target. Each launch still needs the approval described under Non-goals.

## Intent and why

The user's words (2026-11-24): the session starts the game itself (a new game, a new save or a loaded save, the model's choice), runs its checks by script or `x4live`, gets what it needs, and closes the game itself, without the user babysitting. Today nothing in the toolkit starts or closes X4: each launch needs the user to ask for it and load the save, and the game stays open until the user quits it. Skyrim's toolkit has such a driver (`tools/vr-headless.py`).

## Goals

1. One command runs the whole cycle: start X4 through Steam, reach a world, let the caller run checks, close the game, verify no `X4.exe` is left, report.
2. The driver never starts a console window the user sees (start everything with `CREATE_NO_WINDOW`, never detached; the 2026-11-24 pop-up incident is why).
3. It closes only the process it started, and it cannot be fooled by a recycled PID.
4. A failure in any step still ends in a verified closed game, or says plainly that it could not close it.

## Non-goals

- No launch approval in code: approval stays policy (a launch needs the user's approval of that specific run, or an explicit request to queue a described run, user ruling 2026-11-24). The driver records which approval it was given and refuses a start without one.
- No automatic retry of a launch. No launch inside a test script.
- No changes to the game, the profile or save files by the driver itself. Saves are only ever written by `x4live save` (empty slot, never overwrites).

## Facts

- READ: the user starts X4 through Steam with launch options the user has set in Steam; the driver never changes them.
- READ: the toolkit has `STEAM_APPID = "392160"` (`_paths.py:59`), process lookup through `tasklist` (`_livedump.py:110-160`, returns `None` when unknown), and no code that starts or stops X4.
- READ: `x4live` reaches a running game through the helper pipe; the helper arms only after a world loads; a minimised window stops the channel (x4-live skill, measured 2026-09-20). `x4live query player` reports whether the player ship exists. There is no quit or exit verb in the helper or the pipe.
- READ: a quit freeze was seen once with a third-party mod (cause unproven). A full crash dump takes about 5 minutes and must not be cut short.
- READ: Skyrim's driver is reusable in shape: process snapshot as (PID, name, path, start time), exit counted after 3 consecutive missed reads, a manifest for recovery, `finally` cleanup that refuses to touch an unowned process, hidden process reads, tests for PID reuse and failed reads.
- UNMEASURED: whether `steam.exe -applaunch 392160 [args]` appends arguments to the saved options; how to load a named save or start a new game from the command line (no documented option); how X4 reacts to a normal window-close request; autosave on exit; whether X4 renders on a private background desktop (Skyrim's driver uses one for VR).

## Approaches considered

1. **A driver module in the toolkit (`x4run`), modelled on Skyrim's, built in three steps.** Offline first with a fake process runner, then one approved launch that only measures, then a full run. Cost: new code and tests; gain: reuses a shape that already works for Skyrim and keeps every unmeasured part behind a measurement.
2. **Prompt-only:** write a procedure for the session to follow with `Start-Process` and `taskkill`. Cheap, but it is exactly the improvised launching that produced the pop-up incident, and nothing refuses a second `X4.exe` or a recycled PID.
3. **Reuse Skyrim's driver as is.** Rejected: it is built around the VR null driver, OpenComposite and DevBench, which X4 does not have.

Recommended: 1.

## Design

`x4run` (CLI in the toolkit, module `x4validate/_x4run.py`), subcommands `status`, `run`, `stop`.

- **Preflight, refuse otherwise:** no `X4.exe` already running (the user may be playing; check by name through a hidden process read), Steam running, an approval record supplied (who approved, which mode, which save or new game, which desktop), enough free disk per the machine policy.
- **Start:** `steam.exe -applaunch 392160` started with `CREATE_NO_WINDOW` (Steam applies the saved launch options; whether extra arguments are appended is measured in step 2). The driver then finds the new `X4.exe` (it did not exist before) and records PID, path and process start time in a state file under the toolkit's state folder. Only that recorded process is ever closed.
- **Get to a world:** decided by measurement in step 2. Candidates, in order: a measured command-line load of a named save; the existing start-menu adapter and load dispatcher in `x4live` (offline-qualified only, so each is proven in game before it is relied on); the user loading the save as today, as the fallback when nothing else works.
- **Ready:** `x4live query probe` answers and `x4live query player` reports a player ship, within a timeout. The helper's own load hook is not used as the signal (never seen to fire).
- **Run:** the driver hands control to a caller-supplied command (a script, `x4live` calls) and waits for it. The run has an overall time limit.
- **Close, a ladder with a wait at every rung:** a normal window-close request on the recorded process; wait; then, only if the process is still the recorded one and has not exited, a termination of that process (checked by PID and start time first, so a reused PID is never touched); wait; verify no `X4.exe` remains. A long limit is allowed while a crash dump is being written. Autosave behaviour on exit is measured in step 2 and reported.
- **Failure handling:** `finally` always runs the close ladder; if the game cannot be closed the report says so and keeps the state file as the recovery record. A second `run` refuses while a state file names a live recorded process.
- **After:** the report names the log files (`debug.txt`) and can run the toolkit's existing log triage on them.

## Build steps (each its own go)

1. **Offline.** The driver with an injectable process runner, and tests in the shape of Skyrim's: a missing read does not end the run, three consecutive misses do, a reused PID is never closed, a failed process read retries and never reads as "gone", every child is started hidden, a second `X4.exe` refuses the start. No launch.
2. **One approved launch, measuring only:** start through Steam, reach the main menu, check the pipe, close, verify nothing is left. Measures the argument handling, the close request, autosave on exit and the freeze risk. No save is loaded.
3. **A full run:** load a named test save, run `x4live` checks, close. Needs its own approval.

## Anti-goals (a failure even if it technically works)

- A console window the user sees, at any point.
- Closing, killing or touching an `X4.exe` the driver did not start.
- Leaving the game running with no report, or reporting "closed" without verifying it.
- Launching the game without the approval record.

## Left to the builder

Module and state-file names, the exact timeouts (to be set from step 2's measurements), how the approval record is stored, and whether `x4run` is also exposed as a toolkit skill.

# x4run: start X4, run a check, close X4

> **Status: built and tested offline. It has never been run against the real game: treat
> every result as unverified.** Every test drives a scripted stand-in for the process runner;
> nothing in the test suite starts Steam or X4. The first real `x4run run` is build step 2
> and needs its own approval. Until then, `x4run run` is a tool you can read about, not one
> with a track record.

`x4run` is a command line (`x4validate/_x4run.py` holds the logic, `_x4runcli.py` the command
line) that is meant to run one whole cycle without a person at the keyboard: ask Steam to
start X4, wait for the game process, let your command run, close the game, and verify that
no `X4.exe` is left. The design and its reasons are in
`docs/superpowers/specs/2026-11-24-x4-game-driver-design.md`.

## Commands

```text
x4run status                      the recorded run (if any) and the live X4.exe list; read-only
x4run run --approved-by WHO --mode MODE --target WHAT --desktop main [--no-ready-check] -- COMMAND...
x4run stop                        close the recorded game (the recovery path after a crash)
x4run forget                      remove the run record, only after a read shows no X4.exe running
```

## Exit codes

`run`:

* `0` the recorded game closed, the command ran, and the last read saw no `X4.exe` left.
* `2` refused BEFORE any launch call: nothing was started, so it is safe to retry once the
  cause is fixed (an incomplete approval, a `--desktop` other than `main`, an `X4.exe`
  already running, Steam not running, a failed preflight read, a run record in the way, a bad
  argument, the stubbed readiness probe, or a `steam.exe` that could not be started at all).
* `1` everything else, in particular: a launch call was made and no game was recorded (a game
  may still appear or be running: read the output and check by hand), a game not confirmed
  closed, an `X4.exe` still running after the recorded game closed (the preflight proved none
  ran when the run began, so it appeared during the run) or a last read that failed so this
  could not be checked, a game that never became ready, and a command that timed out or could
  not be started (the game is still closed first).

A nonzero exit code of YOUR command is reported but is not a driver error.

`stop`: `0` once the recorded game closed (even if other `X4.exe` are listed as still
running: those are not the driver's and it never touches them), `1` not confirmed closed,
`2` a record it cannot read. `stop` and `forget` with NO record make one live read: none
running is `0` ("nothing to stop/forget"), any `X4.exe` running is `1` ("nothing recorded
here; X4.exe is running: close it by hand, or use the checkout/host that started it"), and a
failed read prints `unknown` and is `2`.

`forget`: `0` removed (or nothing to forget), `2` refused (the record is younger than 270 s, a
read failed, or an `X4.exe` is running).

`status`: `0`, or `2` when the process read failed (`X4.exe: unknown`) or the run record could
not be read.

`--no-ready-check` is printed as `ready: yes (NOT checked: --no-ready-check)`: readiness was
assumed, not measured.

## The approval rule

**A launch needs the user's approval of that specific run, or an explicit request to queue a
described run.** `x4run` records which approval it was given (who, which mode, which save or
new game, which desktop, and when), prints it in the `run` report, and refuses to start
without all of it; it cannot grant approval and nothing in the code checks that the approval
is real. The record is only as good as the person or agent that fills in `--approved-by`. A
blank field, an unknown `--mode` (`menu-only`, `load-save`, `new-game`) or a missing flag is
refused before any process is read or started.

**The desktop line of an approval must say "main".** The driver cannot choose a desktop: the
game appears on the current desktop and takes focus. `--desktop` accepts only `main`; any
other value is refused (exit `2`) before any call.

No launch happens inside a test, and the driver never retries a launch.

## What the driver does, in order

1. **Preflight.** Refuses if an `X4.exe` is already running (the user may be playing), if
   Steam is not running (the driver never starts Steam itself), if a run record already
   exists, or if a process read fails. A failed read is a third state: it is never
   "nothing is running" and never "the game exited".
2. **Start.** Writes a run record (phase `starting`), makes ONE `steam.exe -applaunch` call,
   polls for the new `X4.exe` (up to 180 s after the call, which itself may take 30 s), and
   records its PID, path and process start time (phase `running`). A launch call that times
   out or exits nonzero is UNKNOWN, not failed (the forwarder's exit codes are unmeasured):
   the driver keeps polling. **Only `<game root>/X4.exe` is recorded**; a new `X4.exe` from
   any other folder, or several at once, is refused with the record kept.
3. **Ready.** Polls a readiness probe. **The real probe is a stub in this step**, so
   `x4run run` refuses (exit `2`, nothing started) unless `--no-ready-check` is given, which
   treats the game as ready at once. Step 3 wires the real probe.
4. **Run.** Starts your command (an argv list, never through a shell) with no input and its
   output redirected to `last-command.out` and `last-command.err` in the state folder
   (truncated at the start of each run). The report names both files and prints the last
   4 KB of each. A hidden console child's own output is otherwise lost.
5. **Close, always.** A window-close request on the RECORDED process, a wait, then, only if
   that process is still the recorded one on PID, name, path AND start time, a terminate
   request, a wait, and a last read. An `X4.exe` the driver did not start is never closed.
   A game is reported closed only when the exit was confirmed by three consecutive absent
   reads and the last read agrees.

Every child process is started with `CREATE_NO_WINDOW`: no console window may appear.

**Crash dumps.** The driver does not detect a crash dump. With the default `--hold 0` the
terminate comes 60 s after the window-close request, which cuts short a dump still being
written. Pass `--hold` (at least `600` when a dump writer such as procdump is attached) to
`run` or `stop` when a dump may be written.

**A game you start yourself during the start window will be closed.** The driver cannot tell
an `X4.exe` the user starts from the same game folder during its start window (up to about
3.5 minutes) from its own: it would record it, and close or terminate it at the end of the
run. Do not start X4 yourself while a run is starting.

## The run record: one per machine

The record lives in ONE per-user folder for the whole machine, whichever checkout or worktree
runs `x4run`: `%LOCALAPPDATA%\x4-toolkit\x4run\state.json` on Windows,
`~/.local/state/x4-toolkit/x4run/state.json` elsewhere, or under `$X4RUN_STATE_DIR` when that
is set (tests use it). A second `run` from ANY checkout refuses while a record exists, and
`status`, `stop` and `forget` see the same record from every checkout. The command output
files live beside it.

## Reading the result

* `closed: yes` with `remaining: unknown (the last process read failed)` means the recorded
  game is gone, but whether OTHER `X4.exe` processes exist could not be checked (`run` exits
  `1`).
* `closed: NO` means a game may still be running. The run record is kept. Check for a running
  `X4.exe` by hand, then `x4run stop`.
* A record in phase `starting` names no process, so `x4run stop` can only report it. Check
  for a running `X4.exe` by hand, then `x4run forget`.
* `x4run forget` removes the record only if its file is at least 270 s old (a younger record
  may belong to a run that is still starting, which has no `X4.exe` yet) and one successful
  read shows no `X4.exe`. Never delete the record file by hand.

## Before the first real launch (step 2)

* The game appears on the current (main) desktop and takes focus.
* Do not start X4 yourself while the run is starting (up to about 3.5 minutes).
* `--no-ready-check` is required: the real readiness probe is not wired yet. It means the
  command runs the moment `X4.exe` appears, while the game is still loading.
* `x4live` will not answer at the main menu (the helper arms only after a world loads), so a
  step-2 pipe check should expect no answer.
* Run `x4run run` in the background: an agent's foreground shell call is limited to 10
  minutes, while the driver's own budgets reach about 70 minutes. A killed driver leaves the
  game running with a record that `x4run stop` recovers.
* There is ONE run record per machine now: `status`, `stop` and `forget` work from any
  checkout.
* `Stop-Process` without `-Force` may prompt before stopping a process not owned by the
  current user, and under `-NonInteractive` that prompt becomes an error (unmeasured): the
  close then ends "unclosed", which is safe but needs a by-hand close.
* Any "launch call ... exited" or "timed out" message means "check for an `X4.exe` by hand":
  the forwarder's exit codes and timing are unmeasured.
* Pass `--hold` (at least 600) if a crash dump may be written.

## Measured and unmeasured

Taken from the design; the driver has not been run against the real game.

**Read (from files and earlier measurements, not from running this driver):**

* The user starts X4 through Steam with launch options the user has set in Steam; the driver
  never changes them.
* The toolkit has `STEAM_APPID = "392160"` and process lookup that returns "unknown" on a
  failed read.
* `x4live` reaches a running game through the helper pipe; the helper arms only after a world
  loads; a minimised window stops the channel. There is no quit verb in the helper or the
  pipe.
* A quit freeze was seen once with a third-party mod (cause unproven). A full crash dump takes
  about five minutes and must not be cut short (`x4run run`/`stop` have a `--hold` option for
  that wait; see "Crash dumps" above).

**Measured once, outside the driver's own runs:** the process READ (the PowerShell
`Win32_Process` query) was run for real against `explorer.exe`, `steam.exe` and `X4.exe` (none
running): the rows parsed and two reads gave identical identities. The close and terminate
calls have never been run.

**Unmeasured (every item is a guess until step 2):**

* whether `steam.exe -applaunch 392160 [args]` appends arguments to the saved launch options;
* what the `steam.exe -applaunch` forwarder's exit codes mean, and how long the call takes;
* how to load a named save or start a new game from the command line;
* how X4 reacts to a normal window-close request, and whether it autosaves on exit;
* whether X4 renders on a private background desktop;
* every timeout in the module (60 s close grace, 30 s terminate wait, 180 s start wait) is a
  default, not a measurement;
* the real behaviour of the close request and the terminate request on this machine.

## Non-goals

* No launch approval in code (see above); no automatic retry of a launch; no launch inside a
  test script.
* No change to the game, the profile or save files by the driver itself. Saves are only ever
  written by `x4live save`, into an empty slot.

## The three build steps

1. **Offline** (this step, built): the driver, its command line and its tests, with a scripted
   fake as the only process runner. No launch.
2. **One approved launch, measuring only:** the first real `x4run run`. Start through Steam,
   reach the main menu, check the pipe, close, verify nothing is left; measures the items
   above. No save is loaded. Needs its own approval.
3. **A full run:** load a named test save, run `x4live` checks, close; wires the real
   readiness probe. Needs its own approval.

# x4run: start X4, run a check, close X4

> **Status: built and tested offline. It has NOT been run against the real game.**
> Every test drives a scripted stand-in for the process runner; nothing in the test suite
> starts Steam or X4. Nothing launches X4 until build step 2 is approved and run. Until
> then, `x4run run` is a tool you can read about, not one with a track record.

`x4run` is a command line (`x4validate/_x4run.py` holds the logic, `_x4runcli.py` the command
line) that is meant to run one whole cycle without a person at the keyboard: ask Steam to
start X4, wait for the game process, let your command run, close the game, and verify that
no `X4.exe` is left. The design and its reasons are in
`docs/superpowers/specs/2026-11-24-x4-game-driver-design.md`.

## Commands

```text
x4run status                      the recorded run (if any) and the live X4.exe list; read-only
x4run run --approved-by WHO --mode MODE --target WHAT --desktop WHERE [--no-ready-check] -- COMMAND...
x4run stop                        close the recorded game (the recovery path after a crash)
x4run forget                      remove the run record, only after a read shows no X4.exe running
```

Exit codes: `0` success; `2` a refusal (nothing was done, or nothing more than the message
says); `1` the game is NOT confirmed closed, or any other failure. A nonzero exit code of
YOUR command is reported but is not a driver error: the exit code is `0` if the game was
closed. A run whose game never became ready, or whose command timed out or could not be
started, exits `1` (the game is still closed first).

## The approval rule

**A launch needs the user's approval of that specific run, or an explicit request to queue a
described run.** `x4run` records which approval it was given (who, which mode, which save or
new game, which desktop, and when) and refuses to start without all of it; it cannot grant
approval and nothing in the code checks that the approval is real. The record is only as
good as the person or agent that fills in `--approved-by`. A blank field, an unknown `--mode`
(`menu-only`, `load-save`, `new-game`) or a missing flag is refused before any process is
read or started.

No launch happens inside a test, and the driver never retries a launch.

## What the driver does, in order

1. **Preflight.** Refuses if an `X4.exe` is already running (the user may be playing), if
   Steam is not running (the driver never starts Steam itself), if a run record already
   exists, or if a process read fails. A failed read is a third state: it is never
   "nothing is running" and never "the game exited".
2. **Start.** Writes a run record (phase `starting`), makes ONE `steam.exe -applaunch` call,
   polls for the new `X4.exe`, and records its PID, path and process start time (phase
   `running`).
3. **Ready.** Polls a readiness probe. **The real probe is a stub in this step**, so
   `x4run run` refuses (exit `2`, nothing started) unless `--no-ready-check` is given, which
   treats the game as ready at once. Step 3 wires the real probe.
4. **Run.** Starts your command (an argv list, never through a shell) and waits for it.
5. **Close, always.** A window-close request on the RECORDED process, a wait, then, only if
   that process is still the recorded one on PID, name, path AND start time, a terminate
   request, a wait, and a last read. An `X4.exe` the driver did not start is never closed.
   A game is reported closed only when the exit was confirmed by three consecutive absent
   reads and the last read agrees.

Every child process is started with `CREATE_NO_WINDOW`: no console window may appear.

## Reading the result

* `closed: yes` with `remaining: unknown (the last process read failed)` means the recorded
  game is gone, but whether OTHER `X4.exe` processes exist could not be checked.
* `closed: NO` means a game may still be running. The run record is kept. Check for a running
  `X4.exe` by hand, then `x4run stop`.
* A record in phase `starting` names no process, so `x4run stop` can only report it. Check
  for a running `X4.exe` by hand, then `x4run forget`.
* `x4run forget` removes the record only after one successful read shows no `X4.exe`. Never
  delete the record file by hand.

## Measured and unmeasured

Taken from the design; nothing here has been run against the real game.

**Read (from files and earlier measurements, not from running this driver):**

* The toolkit has `STEAM_APPID = "392160"` and process lookup that returns "unknown" on a
  failed read.
* `x4live` reaches a running game through the helper pipe; the helper arms only after a world
  loads; a minimised window stops the channel. There is no quit verb in the helper or the
  pipe.
* A quit/new-game freeze was seen once (2026-11-03) with an old HUD mod; cause unproven. A
  full crash dump takes about five minutes and must not be cut short (`x4run run`/`stop` have
  a `--hold` option for that wait).

**Unmeasured (every item is a guess until step 2):**

* whether `steam.exe -applaunch 392160 [args]` appends arguments to the saved launch options;
* how to load a named save or start a new game from the command line;
* how X4 reacts to a normal window-close request, and whether it autosaves on exit;
* whether X4 renders on a private background desktop;
* every timeout in the module (60 s close grace, 30 s terminate wait, 180 s start wait) is a
  default, not a measurement;
* the real behaviour of the process read, the close request and the terminate request on this
  machine (the PowerShell calls were exercised only through the fake runner).

## Non-goals

* No launch approval in code (see above); no automatic retry of a launch; no launch inside a
  test script.
* No change to the game, the profile or save files by the driver itself. Saves are only ever
  written by `x4live save`, into an empty slot.

## The three build steps

1. **Offline** (this step, built): the driver, its command line and its tests, with a scripted
   fake as the only process runner. No launch.
2. **One approved launch, measuring only:** start through Steam, reach the main menu, check
   the pipe, close, verify nothing is left; measures the items above. No save is loaded.
   Needs its own approval.
3. **A full run:** load a named test save, run `x4live` checks, close; wires the real
   readiness probe. Needs its own approval.

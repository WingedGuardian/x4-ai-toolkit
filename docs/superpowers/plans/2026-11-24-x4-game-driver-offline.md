# X4 game driver, step 1 (offline build) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `x4run` driver (start X4 through Steam, track the process, run a caller command, close the game, verify nothing is left) and its tests with NO game launch: every process and Steam interaction goes through an injected runner that tests fake.

**Architecture:** One logic module `_x4run.py` (process snapshot, exit watch, state file, approval record, start, close ladder, run cycle) with every side effect passed in (`run`, `sleep`, `clock`), and a thin CLI module `_x4runcli.py`. Tests drive the logic with a scripted fake process table. Shapes follow Skyrim's `vr-headless.py` (snapshot tuple, 3-miss exit wait, `finally` cleanup that refuses an unowned process, hidden reads).

**Tech Stack:** Python 3.13 through `uv`, standard library only, pytest. Windows is the real target; logic is tested on both OSes through the fake runner.

**Spec:** `docs/superpowers/specs/2026-11-24-x4-game-driver-design.md` (APPROVED 2026-11-24). Read it first.

## Global Constraints

- No test, task or command in this plan may start Steam or X4. A launch is step 2 and needs the user's approval of that specific run. Never call `steam.exe`, `X4.exe`, `taskkill` or `Stop-Process` for real; the real runner is only exercised through the fake.
- Every child process the module starts uses `creationflags=subprocess.CREATE_NO_WINDOW` (0x08000000), never `DETACHED_PROCESS`; a test asserts the flag on every call the fake runner sees.
- A process read that FAILS is a third state, never "no X4 running" and never "the game exited": `read_processes` raises `ProcessReadError`; an empty list is a real answer.
- Only the recorded process (PID, name, path AND start time all equal) is ever closed or terminated.
- Stdlib only. No new dependency. LF line endings. No usernames or absolute personal paths in code or tests.
- Resource policy: run tests with `python ~/.claude/tools/resource-budget/rb.py run --name x4run-<n> --cpu 5 --ram 1 --wait-until-fits 60 -- uv run --frozen python -m pytest -q -p no:cacheprovider <files>` from `tools/x4validate`. Never run the full suite locally (CI does).
- Stage explicit paths only (never `git add -A` or `.`). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push.
- Work in the worktree `x4-toolkit-driver`, branch `session/x4-driver`.

## Review Focus

1. A recorded PID that reappears with a DIFFERENT start time is another process: never closed, counted as "recorded process gone". (Task 2, Task 5)
2. A failed or malformed process read must not read as "the game is gone" (exit wait) or "nothing is running" (preflight): preflight refuses on unknown. (Task 1, Task 2, Task 4)
3. A second `X4.exe` at start (the user may be playing) refuses the start and touches nothing. (Task 4)
4. Steam starts but no new `X4.exe` appears within the timeout: no retry, a report, no state file left claiming a game. (Task 4)
5. The window-close request is ignored forever (the 2026-11-03 quit freeze): the ladder escalates only after its grace period, terminates only the recorded process, and still verifies; a dump-in-progress allowance exists. (Task 5)
6. The driver itself dies mid-run: the next `run` refuses with the recovery instruction, and `stop` can still close the recorded game. (Task 3, Task 6)

## File structure

- Create `tools/x4validate/x4validate/_x4run.py`: all logic. One responsibility per section, each section a task.
- Create `tools/x4validate/x4validate/_x4runcli.py`: argument parsing, wiring the real runner, exit codes.
- Create `tools/x4validate/tests/test_x4run_process.py`, `test_x4run_state.py`, `test_x4run_start.py`, `test_x4run_close.py`, `test_x4run_cycle.py`, `test_x4run_cli.py`, and a shared fake in `tests/_x4run_fake.py`.
- Modify `tools/x4validate/pyproject.toml` (`[project.scripts]`: `x4run = "x4validate._x4runcli:main"`), `.gitignore` (the state folder), `CHANGELOG.md` (`## Unreleased`), and the generated CLI reference through its generator (Task 7).

### Task 1: Process snapshot and the fake runner

**Files:** Create `_x4run.py` (section "process"), `tests/_x4run_fake.py`, `tests/test_x4run_process.py`.

**Interfaces:**
- Produces:
  - `NO_WINDOW: int` (`getattr(subprocess, "CREATE_NO_WINDOW", 0)`).
  - `@dataclass(frozen=True) class ProcessInfo: pid: int; name: str; path: str; started: str` (`started` is a UTC ISO string, compared exactly).
  - `class ProcessReadError(RuntimeError)`.
  - `def read_processes(run=subprocess.run, *, name: str = "X4.exe", sleep=time.sleep, attempts: int = 3) -> list[ProcessInfo]`: one hidden PowerShell read (JSON out), retried up to `attempts` times on a transient failure, then raises `ProcessReadError`. `[]` means none running.
  - `tests/_x4run_fake.py`: `class FakeSystem` holding a scripted process table and a call log; `fake.run(argv, **kw)` answers the PowerShell process read from the table (single object or list JSON), the Steam launch, the window-close request and the terminate request, appends every call (argv + creationflags) to `fake.calls`, and can be scripted to fail a read N times, return malformed output, or time out.

- [ ] **Step 1: Write the failing tests** in `test_x4run_process.py`:
  - `test_read_returns_the_matching_processes` (two X4 rows parse to two `ProcessInfo`; a single-object JSON answer parses to one),
  - `test_empty_output_is_an_empty_list_not_an_error`,
  - `test_a_transient_failure_retries_then_succeeds` and `test_a_persistent_failure_raises_ProcessReadError_never_an_empty_list`,
  - `test_malformed_json_is_rejected` (raises, does not return `[]`),
  - `test_a_timeout_is_a_read_failure`,
  - `test_every_read_is_started_hidden` (every call in `fake.calls` has `creationflags & NO_WINDOW` and none has `0x8` set).
- [ ] **Step 2: Run them; expect FAIL** (module missing).
- [ ] **Step 3: Implement** `ProcessInfo`, `ProcessReadError`, `read_processes` and the fake. The PowerShell command selects `ProcessId`, `Name`, `ExecutablePath` and a UTC ISO start time through `Get-CimInstance Win32_Process` filtered by name; the timeout is 15 s; `sleep` is the injected one.
- [ ] **Step 4: Run; expect PASS.** Commit `x4run: process snapshot with hidden reads and a scripted fake`.

### Task 2: Exit watch

**Files:** `_x4run.py` (section "exit"), `tests/test_x4run_process.py`.

**Interfaces:**
- Consumes: `ProcessInfo`.
- Produces: `class ExitWatch: def __init__(self, recorded: ProcessInfo, misses: int = 3)`, `def observe(self, procs: list[ProcessInfo] | None) -> bool` (True once the recorded process has been absent for `misses` consecutive observations; `None` means the read failed and neither counts nor resets). "Present" means an entry equal to `recorded` on pid, name, path AND started.

- [ ] **Step 1: Failing tests:** `test_one_missing_read_does_not_end_the_run`, `test_three_consecutive_misses_end_it`, `test_a_reappearance_resets_the_count`, `test_a_failed_read_neither_counts_nor_resets` (None between two misses does not reach 3 early, and does not reset), `test_a_reused_pid_with_a_different_start_time_counts_as_a_miss` (Review Focus 1).
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run, PASS.** Commit `x4run: exit watch with the 3-miss rule and PID-reuse proof`.

### Task 3: Approval record and state file

**Files:** `_x4run.py` (section "state"), `tests/test_x4run_state.py`, `.gitignore`.

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Approval: approved_by: str; mode: str; target: str; desktop: str; recorded_at: str` and `def require_approval(a: Approval | None) -> Approval` (raises `UnapprovedLaunch(ValueError)` when missing or any field is blank/whitespace; `mode` must be one of `"menu-only"`, `"load-save"`, `"new-game"`).
  - `@dataclass class RunState: run_id: str; approval: Approval; phase: str; process: ProcessInfo | None; started_at: str` (`phase` in `"starting"`, `"running"`, `"closing"`).
  - `class UnfinishedRun(RuntimeError)`; `class StateStore: def __init__(self, folder: Path)`, `begin(state) -> None` (creates the state file with `O_EXCL`; raises `UnfinishedRun` naming the file and the recovery command `x4run stop` if it exists), `update(state) -> None` (atomic: temp file + `os.replace`, retrying about 10 s on `PermissionError` like Skyrim's `atomic`), `read() -> RunState | None`, `clear() -> None`.
  - The state folder is `<X4_TOOLKIT>/.x4run/`; add it to `.gitignore`.

- [ ] **Step 1: Failing tests:** `test_missing_or_blank_approval_refuses`, `test_unknown_mode_refuses`, `test_begin_creates_and_a_second_begin_refuses_with_the_recovery_command` (Review Focus 6), `test_update_is_atomic_and_round_trips`, `test_update_retries_a_transient_PermissionError_then_succeeds`, `test_read_of_a_corrupt_file_raises_not_none`, `test_clear_removes_it`.
- [ ] **Step 2-4:** implement, run, pass. Commit `x4run: approval record and recoverable state file`.

### Task 4: Start

**Files:** `_x4run.py` (section "start"), `tests/test_x4run_start.py`.

**Interfaces:**
- Consumes: `read_processes`, `ProcessInfo`, `Approval`, `StateStore`, `RunState`, the fake.
- Produces: `def steam_exe(game_root: Path) -> Path` (the Steam root is `game_root.parents[2]`; returns `<steam>/steam.exe`); `class StartRefused(RuntimeError)`; `def start(game_root: Path, approval: Approval, store: StateStore, *, run=subprocess.run, sleep=time.sleep, clock=time.monotonic, timeout_s: float = 180.0) -> RunState`.
- Behaviour: (1) `require_approval`; (2) `read_processes` once; a `ProcessReadError` or any running `X4.exe` raises `StartRefused` with nothing started and no state written; (3) `store.begin` with phase `"starting"`; (4) launch `[steam_exe, "-applaunch", STEAM_APPID]` hidden (`STEAM_APPID` from `_paths`), no retry; (5) poll for a NEW `X4.exe` until `timeout_s`; (6) on success record the process, set phase `"running"`, return the state; (7) on timeout clear the state file, raise `StartRefused` saying Steam was asked but no game appeared, and perform no second launch.

- [ ] **Step 1: Failing tests:** `test_a_running_X4_refuses_and_starts_nothing` (Review Focus 3: `fake.calls` has no launch), `test_a_failed_process_read_refuses_the_start` (Review Focus 2), `test_missing_approval_refuses_before_any_call`, `test_the_launch_is_steam_applaunch_with_the_app_id_and_hidden`, `test_the_new_process_is_recorded_with_pid_path_and_start_time`, `test_no_game_within_the_timeout_clears_state_and_does_not_retry` (Review Focus 4: exactly one launch call), `test_a_pre_existing_state_file_refuses`.
- [ ] **Step 2-4:** implement, run, pass. Commit `x4run: start through Steam with preflight, no retry`.

### Task 5: Close ladder

**Files:** `_x4run.py` (section "close"), `tests/test_x4run_close.py`.

**Interfaces:**
- Consumes: `RunState`, `ExitWatch`, `read_processes`, `StateStore`.
- Produces: `@dataclass(frozen=True) class CloseReport: closed: bool; method: str` (`"already-gone"`, `"window-close"`, `"terminate"`, `"unclosed"`); `steps: tuple[str, ...]`; `remaining: tuple[ProcessInfo, ...]`; `def close(state: RunState, store: StateStore, *, run=subprocess.run, sleep=time.sleep, clock=time.monotonic, grace_s: float = 60.0, kill_wait_s: float = 30.0, hold_s: float = 0.0) -> CloseReport`.
- Behaviour: set phase `"closing"`; if the recorded process is not present (equal on all four fields) report `"already-gone"` without any call; otherwise (1) ask the recorded process to close its main window (hidden PowerShell `CloseMainWindow` by PID, after re-reading and re-checking the start time); (2) watch with `ExitWatch` up to `grace_s` (plus `hold_s`, the allowance for a crash dump in progress); (3) only if still present, terminate that PID after one more start-time check; (4) watch up to `kill_wait_s`; (5) finally read all `X4.exe` processes and report any still running in `remaining`; `closed` is True only when the recorded process is confirmed gone by `ExitWatch`. A failed read never counts as gone. On `closed` the state file is cleared; otherwise it is kept.

- [ ] **Step 1: Failing tests:** `test_already_gone_makes_no_call`, `test_window_close_that_works_never_terminates`, `test_an_ignored_window_close_escalates_only_after_the_grace_period` (Review Focus 5: assert no terminate call before `grace_s` of fake time), `test_terminate_targets_only_the_recorded_pid_after_a_start_time_check`, `test_a_reused_pid_is_never_closed_or_terminated` (Review Focus 1: same PID, other start time: zero close and zero terminate calls, `method == "already-gone"`), `test_a_second_unrelated_X4_is_left_alone_and_listed_in_remaining`, `test_a_failed_read_during_the_wait_is_not_gone`, `test_unclosed_keeps_the_state_file_and_says_so`, `test_hold_s_extends_the_grace_period`, `test_every_call_is_hidden`.
- [ ] **Step 2-4:** implement, run, pass. Commit `x4run: close ladder that only touches the recorded process`.

### Task 6: Run cycle

**Files:** `_x4run.py` (section "cycle"), `tests/test_x4run_cycle.py`.

**Interfaces:**
- Consumes: `start`, `close`, `read_processes`, `StateStore`.
- Produces: `@dataclass class CycleReport: started: bool; ready: bool; command_rc: int | None; close: CloseReport | None; notes: tuple[str, ...]`; `def run_cycle(game_root, approval, store, command: list[str], *, ready_probe: Callable[[], bool], run=subprocess.run, sleep=time.sleep, clock=time.monotonic, ready_timeout_s: float = 300.0, command_timeout_s: float = 3600.0, **close_kw) -> CycleReport`.
- Behaviour: `start`; wait for `ready_probe()` up to `ready_timeout_s` (the probe is injected: step 3 supplies the real `x4live` check; this step only tests the seam); run `command` hidden with `command_timeout_s`; ALWAYS (a `finally`) call `close`; a `StartRefused` returns a report with `started=False` and nothing to close. `stop(game_root, store, ...)`: read the state file and close the recorded process (the recovery path for a driver that died).

- [ ] **Step 1: Failing tests:** `test_the_game_is_closed_when_the_command_fails`, `test_the_game_is_closed_when_the_command_times_out`, `test_the_game_is_closed_when_the_probe_never_becomes_ready`, `test_a_refused_start_closes_nothing`, `test_a_keyboard_interrupt_still_closes`, `test_stop_closes_the_recorded_process_from_the_state_file` (Review Focus 6), `test_stop_with_no_state_file_reports_nothing_to_stop`.
- [ ] **Step 2-4:** implement, run, pass. Commit `x4run: run cycle with finally-close and a stop recovery path`.

### Task 7: CLI, registration, docs

**Files:** Create `_x4runcli.py`, `tests/test_x4run_cli.py`; modify `pyproject.toml`, the generated CLI reference (find its generator: grep `x4-cli-reference` in `tools/x4validate/scripts/` and `agent/skills/`; regenerate, do not hand-edit generated files), `CHANGELOG.md`, `docs/` (a short `docs/x4run.md`).

**Interfaces:**
- Consumes: everything above. Produces: `def main(argv: list[str] | None = None) -> int`; subcommands `status` (prints the recorded run and the live `X4.exe` list), `run` (requires `--approved-by`, `--mode`, `--target`, `--desktop`, then `-- COMMAND...`), `stop`. Exit codes follow the toolkit: 0 success, 2 refusal (`StartRefused`, `UnapprovedLaunch`, `UnfinishedRun`, `ProcessReadError`), 1 any other failure or an unclosed game. Every flag has `--help` text. `run` without `--approved-by` refuses before any process read. The real `ready_probe` is a stub that reports "not wired yet: step 3" and makes `run` refuse unless `--no-ready-check` is given; the CLI says plainly that the driver has not been run against the real game.

- [ ] **Step 1: Failing tests:** `test_run_without_approval_exits_2_before_any_call`, `test_status_with_no_state_and_no_game`, `test_stop_with_nothing_recorded_exits_0_and_says_so`, `test_unclosed_exits_1`, `test_help_names_every_flag`.
- [ ] **Step 2-4:** implement; add the `[project.scripts]` entry; regenerate the CLI reference and run `tests/test_gen_agent_trees.py` plus any CLI-matrix/CLI-reference test that the repo has (find them with `git grep -l "x4save" tools/x4validate/tests`); write `docs/x4run.md` (what it does, what is measured vs not, the approval rule) and a CHANGELOG `## Unreleased` bullet saying the driver is built and tested offline, has not been run against the real game, and that nothing launches X4 until step 2. Commit `x4run: CLI, registration and docs`.

## Self-review notes

- Spec coverage: preflight (Task 4), hidden start and no retry (Task 4), recorded-process-only closing and the ladder (Task 5), `finally` and recovery (Task 6), approval record (Task 3), CLI/docs (Task 7). The spec's start-menu/load-save mechanics, the readiness probe against real `x4live`, the Steam argument handling and the autosave/quit measurements are steps 2 and 3, not here.
- Types are consistent across tasks (`ProcessInfo`, `Approval`, `RunState`, `StateStore`, `ExitWatch`, `CloseReport`, `CycleReport`).

# Bounded native camera adapter

This module is an isolated implementation proposal. It is not registered in the helper,
exposed through the pipe, or deployed. No game runtime result is claimed.

The module returns a factory accepting an explicit native function-wrapper table plus
resolve_issued(token), is_valid(id), epoch(), and now(). The resolver must use the current
session's issued-ID allowlist; validity must use the helper's existing native validation.
Epoch must change after every UI reload, and now must be a finite monotonic seconds clock.
Native wrappers preserve the function names and arguments documented in the source.

## Contract

- prepare(token) performs no writes and returns an opaque ticket. One operation can be active.
- execute(ticket) consumes it once, rechecks identity, native eligibility and known same-sector
  context, and refuses preparations older than five seconds. It checks SetSofttarget's result
  before requesting target view. Only exact false is a known native refusal; any nonboolean
  reply is UNCERTAIN. A partial/throwing write is UNCERTAIN and is never retried.
- observe(ticket) requires a later monotonic observation and the same issued, valid identity.
  Exact GetExternalViewRefObject equality within five seconds yields TARGET_VERIFIED. This
  verifies observed target state, not transition causation, pixels, orientation or visibility.
  A mismatch remains PENDING until the bound, then UNVERIFIED_TIMEOUT. A previously verified
  target changing yields DIVERGED and relinquishes restoration ownership.
  A failed identity/clock/getter observation after the request is UNCERTAIN, not an unacted
  refusal. Nil, boolean, malformed or nonfinite external references are unavailable evidence;
  a valid different identity is a distinct pending/mismatch observation.
- restore_cockpit(ticket) requires the still-current verified target and context. It invokes
  SetPlayerCameraCockpitView(true) once. RESTORE_REQUESTED_UNVERIFIED is intentionally not a
  completion claim: this proposal has no source-attested cockpit-mode getter.

Each result carries action_attempted and per-native-call attempts (softtarget, target_view,
cockpit). These facts survive uncertain observations and terminal outcomes. They describe
calls attempted, not confirmed engine effects. A refused repeat/foreign ticket does not
reconstruct an earlier result; the transport must retain the original attempt receipt.

The factory takes Lua functions, so integration must wrap native FFI functions explicitly.
Tickets are private table identities, not client-serializable authorization. A future pipe
adapter must retain them server-side and independently authorize each write. Native target
selection changes the soft target as well as the camera; callers must disclose both effects.
No saved camera preferences, save files, screenshot/input workaround or generic Lua execution
is included. Lost transport replies require observation, never automatic resubmission.

## Evidence and validation

READ: native X4 9.0 menu_map target-view/targeting callers and menu_followcamera external
reference getter establish the argument shapes; exact source locations are in the module.
The module intentionally refuses native map's unknown-sector fallback. Source-contract
confidence is 97%; runtime wiring confidence is 85% until an approved game run.

The behavioral test file executes this actual Lua module using the toolkit's existing lupa
development dependency. Thin native stubs check exact arguments, write ordering, stale IDs,
reloads, invalid context, delayed/mismatched observations, timeout boundaries, one-use tickets,
and uncertainty after partial writes.

Historical pre-review run, 2026-11-09: all 50 behavioral cases passed, zero skipped, in 0.18 seconds;
the resource-wrapped process exited zero. The command, from tools/x4validate, was:

    uv run --frozen python -B -m pytest -q -ra -p no:cacheprovider tests/test_livecamera.py

The owned child had UV_PROJECT_ENVIRONMENT, VIRTUAL_ENV and PYTHONPATH unset. This
qualification executes Lua with stub native functions; it does not verify native engine
exports, UI reload lifetime, real camera rendering or the eventual transport integration.
MEASURED successor run, 2026-11-09: 64 cases passed, zero skipped, in 0.18 seconds using
the same command and corrected environment. The resource wrapper admitted the run and
returned child exit zero. This fresh result covers post-write identity/clock/getter failures,
nil/boolean/malformed external references, nonboolean targeting replies, and retained attempt
facts in uncertain results. The historical 50-case result remains separate.

Before runtime integration, independently review the resolver/epoch/clock bindings and write
authorization. In an approved disposable session, verify load and trigger markers, exercise
one issued same-sector subject, check next-update identity, and request cockpit restoration.
Do not call camera-target readback visual proof. Screenshot/input host availability and an
independent rendered-image check are separate gates.

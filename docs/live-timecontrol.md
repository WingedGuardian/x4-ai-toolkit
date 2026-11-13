# Native SETA control: offline qualification

The timecontrol module copies the fixed `seta` StartPlayerActivity and
StopPlayerActivity route in native menu_map.lua:5384-5396. It requires an issued
occupied ship, reads native availability and activity, and consumes a short-lived
private ticket before dispatch. Stop is an explicit operator request; retirement
does not stop unrelated player activity or restore state automatically.

Independent source review found no blocker. Thirty synthetic-native controls
passed under resource-budget run 3d9e38d3def9. These cover eligibility, occupied
ship identity, changed session/state, clock rollback, duplicate dispatch,
retirement and exceptions after dispatch. No game was launched by these tests.

The native calls return void. DISPATCHED_UNVERIFIED proves only that the call was
attempted. State observations retain separate UI_ELAPSED and
NATIVE_GET_CURRENT_GAME_TIME clock domains and are explicitly non-atomic.
Acceleration factor and outcome invariance remain unqualified. A runtime control
must measure game-time advance against independent host monotonic intervals,
verify actual activation/deactivation, and compare dependent outcomes before a
campaign uses acceleration to close its cases. Unknown availability refuses.

FFI declarations are copied source evidence, not runtime ABI qualification.
No arbitrary acceleration factor, world-age mutation or automatic cleanup is
provided.

## Closed helper integration

The source-bound bundle exposes `time_state` with exactly one issued ship token,
and `x4-native-v1` actions `time.start` and `time.stop` with exactly `{ship}`.
Preparation is read-only; execution consumes the private ticket once. Status
retains NATIVE_SETA_STATE_ONLY and never reports acceleration completion.

Assembly passes the native `getElapsedTime` global specifically to timecontrol.
The common action clock supplied by live_query is GetCurrentGameTime and is not
an elapsed-time source. Independent review caught the initial alias; it was
corrected before integration acceptance. Regression controls use UI=50/game=123
and prove the ticket expires at UI=56 even when game time remains123.

Final combined module/provider/assembly/bundle/helper-composition verification:
302 passed, zero skipped, resource run 81bc5df39973. Independent wiring review
found no remaining blocker. This is synthetic/native-source qualification only;
actual runtime activation, ABI, factor and outcome controls remain outstanding.

## Clock-family follow-up

A bounded source audit inspected16 named assembly consumers: four explicitly
label UI elapsed clocks, one path reader has no clock, and 11 have general or
unlabeled clocks. The other three UI-labeled consumers were ticker, camera-state
and player-navigation. Their assembly wiring now also receives getElapsedTime.
Other consumers retain their existing game clock; their pause-sensitive deadlines
are not silently redefined as wall time. This audit excludes the menu module and
optional extensions and is not a whole-toolkit clock audit.

Combined affected-module and helper integration verification passed 433 controls,
zero skipped (resource run f766d5f690c9). Independent narrow source review is clear.
The wiring regression proves all four UI consumers receive 50 while the common
clock and options adapter receive 10. No runtime factor or timing claim follows.

# Session-bound automation routing — offline qualification

The new host BoundPipe and Lua live_automation factory wrap each query/action in an exact public session envelope. They are not yet loaded by the installed helper or exposed by the CLI.

Measured offline: 42 actual-Lua/Python controls pass, zero skips (resource receipt 8e5de28bb89c). They exercise the actual action core, scalar codec, routing code and durable journal together; native state is synthetic. Independent source review by the services lane found no remaining blocker after the corrections below. No engine behavior is established.

Before dispatch, the helper compares the caller's session, UI generation, provider revision, world token and helper build with its current authority. It rejects missing, nonfinite or backwards game age. Execution/status require the exact previously prepared packet, not merely an id and caller-supplied digest. It repeats identity/clock checks after the operation. A lost reply can mean an action occurred; the action journal prohibits resending and permits status-only reconciliation.

The envelope has exactly version, stamp, build, age, status, payload. The stamp is the public five-field contract. Host process identity comes from SessionLink, never fabricated in native output. Host connection failure poisons that BoundPipe instance. A new qualified connection may reconcile the preserved action journal.

Limits:
- The router and SDK registry are one lifetime and must be recreated together.
- Maximum 256 retained prepared packets; no eviction/replay.
- Read verbs require an explicit whitelist. Pause, eval, nested routing and unknown reads refuse.
- The inner payload is a JSON string bounded to 4096 bytes by the actual encoder; larger observations need scoped pages. No silent truncation.
- Existing legacy query replies remain unbound and cannot qualify campaign acceptance.
- Build identity must cover all loaded automation modules; the historical live_query-only fingerprint is insufficient.
- UI bootstrap probing plus a durable host UI-generation allocation is the next integration dependency. The isolated router does not itself establish that a supplied generation belongs to a particular UI instance.
- Native wrappers, full helper loading, CLI, deployment, reload controls and campaign execution remain unqualified.

Initial test harness incorrectly called ActionJournal.prepare and expected execute to return uncertainty rather than raise. It was corrected to the existing create/exception contract before the passing run; no production behavior was changed to satisfy that mistaken test.


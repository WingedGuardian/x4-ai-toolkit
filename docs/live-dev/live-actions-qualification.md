# Explicit live action lifecycle — implementation status

This is an offline-tested infrastructure library, not a deployed game-control capability.
`x4live automation` uses these modules on the host side. The helper in `mods/x4_toolkit_helper` does not load the bundle (its ui.xml lists only live_query.lua and engine_probe.lua); only a helper composed with `scripts/prepare_live_helper.py` does. No native transaction,
fixture action, camera movement, save or game launch was performed by this work.

## Interfaces

`_liveactions.ActionJournal` consumes an exact preparation request and receipt, saves them,
and reserves one durable dispatch marker before invoking a supplied transport. Reopening or
using a second client does not resend a preserved request. It validates the complete bounded
observation history, exact request/session/digest, and dispatch marker. Contradictory outcomes
are retained and block qualification. Unknown outcomes use read-only reconciliation; never a
second execution. The journal is not an authorization mechanism, cryptographic attestation,
proof of native effects, or portable power-loss guarantee.

Request version 1 fields: id (32 lowercase hex), session, provider, action, arguments (JSON
object), binding (opaque preparation token). Digest is SHA256 of canonical sorted compact ASCII
JSON. Arguments and request are bounded; nonfinite values are rejected. Receipts bind version,
id, session and digest, with status, observations and reason. An adapter must obtain actual
engine preparation and independently observe actual outcome; an invented dictionary is not
runtime evidence.

`ui/live_actions.lua` returns a factory for a closed registered-provider registry. Each trusted
installed provider registers id, source revision, fixed action IDs, session callback and
`invoke(phase, request, done)`. Phases are prepare/execute/status. Input to this engine core
also includes the host digest. Provider code may acknowledge asynchronously. The core copies
requests, rejects ID reuse with changed content, consumes before dispatch, invalidates changed
sessions, and preserves request records without eviction (256-request bound). A lost execution
ack can be reconciled through status without another execute. Providers must verify MD reply
correlation and real readiness; the registry does not infer MD state from callback delivery.

A provider's completion is not a whole-mod PASS. Fixture dispatch, action completion, cleanup
and functional result need separate observations. Registered callbacks are trusted installed
code, not a sandbox; source/hash review and explicit command entrypoints remain necessary.

## Integration still required

- Bind a real new-world/reload handshake; loaded_at alone is not a unique session identity.
- Connect strictly parsed, bounded pipe commands and dedicated write CLI entrypoints.
- Register source-bound private diagnostic adapters and native queue/camera adapters.
- Keep economic actions separate from simple cue signalling. Native purchase payment precedes
  task creation; repair and refit use distinct paths. No transaction implementation is yet claimed.
- Qualify game behavior after independent review and specifically approved deployment/run.

## Offline evidence and review

Initial journal 25 controls passed. Independent review found mutable transport request binding,
incomplete history/marker validation, and discarded contradiction evidence. Those were corrected;
a follow-up caught receipt-before-dispatch acceptance, also corrected. Regression controls cover
these paths, duplicate clients, interrupted dispatch, lost replies, malformed/stale receipts and
prepared-history corruption.

Actual-Lua registry controls use synthetic providers and exercise async/lost acknowledgements,
closed vocabulary, stale sessions, duplicate callbacks, conflicting request IDs and bounded
history. These tests prove library behavior, never native X4 execution.

Review corrections preserve competing receipts in unique contradiction files when observation
filenames race. Later reconciliation refuses any such marker instead of accepting only the
winning file. Protocol faults in the Lua registry are sticky; ordinary lost acknowledgements
remain reconcilable. Explicit read-only status requests supersede older status attempts and
can recover a missing preparation acknowledgement without repeating preparation or execution.
Before dispatch, status may report only prepared/refused/unknown; it cannot invent an execution.
Preparation timeout may likewise report unknown without poisoning the protocol. It cannot
enable execution until fresh read-only readiness confirms prepared. While readiness is pending,
execution is blocked; superseded callbacks cannot unblock it.
Superseded replies are ignored and counted. Each snapshot exposes the immutable registered
provider_revision separately from the opaque prepared-context binding.

MEASURED 2026-11-09: the final combined Python journal and actual-Lua registry suite passed
71 controls with zero skips in 0.46 seconds. The resource-wrapped process exited zero:

    uv run --frozen python -B -m pytest -q -ra -p no:cacheprovider tests/test_liveactions.py tests/test_liveactions_lua.py

This was run from tools/x4validate with UV_PROJECT_ENVIRONMENT, VIRTUAL_ENV and PYTHONPATH
unset in the owned child. It qualifies these libraries under synthetic providers only.

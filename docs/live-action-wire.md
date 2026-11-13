# Registered action wire (offline implementation)

This codec bridges the durable Python action journal and the Lua registered-provider core.
It does not yet install a helper, register a native action, expose a CLI command or grant permission.

The one ASCII argument uses eight pipe-delimited fields: version, request ID, session,
provider, action, preparation binding, host request digest, and comma-delimited scalar arguments.
Keys are identifiers; values are typed printable-ASCII strings encoded as lowercase hex, booleans,
or finite numbers within the exact Lua integer range. Null, nested and non-ASCII arguments refuse.
Use machine IDs rather than localized labels. The entire encoded argument is capped at1400bytes;
LivePipe.ask still independently enforces1900bytes for the complete frame.

The host digest correlates the immutable Python request; it is not authentication of another
process or a cryptographic server-side verification. Providers independently check their fixed
actions, current session, subject/readiness and prepared context. Installed provider code is
trusted code and must be source-qualified.

Replies are bounded JSON with exact request identity and separate preparing/prepared/running/
terminal/unknown states. Preparing never creates a durable prepared journal. Tables with sparse
or mixed keys, unsafe numbers, cycles or metatables refuse encoding; empty tables encode as
objects. Native64-bit object/task/money identities must use exact strings, never Lua doubles.
The encoder bounds depth/nodes/string lengths and uses a conservative cumulative byte budget.

PipeActionTransport makes exactly one pipe ask per invocation. It has no retries. Callers use
ActionJournal.execute for mutation, preserving the dispatch marker before transport. A failed
or lost acknowledgement may follow a completed game action; reconcile by status. Never repeat
execute or infer completion from a callback alone.

MEASURED offline:53controls passed with no skips. They exercise actual Lua decoding/encoding,
Python receipt binding, malformed/oversized/unsupported payloads and a cross-language synthetic
provider through the real durable journal. The last control loses an execution reply AFTER the
synthetic mutation and verifies status reconciliation without a second mutation. These are
synthetic transport/library controls, not native X4 qualification. Independent integration
review, actual helper/CLI wiring and specifically approved native controls remain required.


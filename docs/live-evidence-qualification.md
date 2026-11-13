# Raw bound read evidence

ReadEvidence collects one existing AutomationClient.query call under a closed
BoundPipe trace. It does not implement another query path, open a pipe, create a
journal or file, invoke an action, or launch X4. The shared query_capture function
is the previous header/page loop used by both the client and offline replay.

The trace permits only read_capture and read_page and refuses any other command
before dispatch. It retains at most 33 replies (one header and up to 32 pages),
each outer payload at most 65,536 UTF-8 bytes, with an aggregate raw budget.
Nested traces refuse. Failed or interrupted collection produces no local handle.

Each frame retains the exact outer JSON payload string delivered by Reply,
its sequence/status, original request verb/arguments, and the host identity
verified before and after that command. This is not a claim to retain the
original pipe framing bytes: sequence/status are stored separately. The source
capture nonce, verb, original source arguments, helper build, native public
stamp, game ages, and host process/load identity remain available together.
The recorded profile comes from the same connected ProfilePeer and is checked
again at collection close.

## Collection and replay

Construct ReadEvidence with a production AutomationClient.connect result.
A bare constructor is not measured native provenance: tests must explicitly
select synthetic=True, and their handles remain SYNTHETIC even when they use
actual classes and the real Lua protocol. No isinstance test alone upgrades
synthetic native values to an observed tier.

collect(verb, *args) returns an opaque in-process handle only after the actual
client result and a complete replay agree. receipt(handle) returns a copy.
transcript(handle) returns a separate copy of the raw transcript. Neither copy
can create a new handle or mutate its retained evidence. There are at most
16 retained captures and 32 MiB of serialized transcripts per collector.

replay(transcript) reuses BoundPipe's envelope validation,
SessionLink.verify_reply's exact native-stamp comparison, and query_capture /
ReadCapture for all page identities, order, finite times, counts, UTF-8 decoding,
and reconstructed-byte hashes. It refuses extra/missing/reordered frames,
changed source arguments, non-increasing reply sequences, different process
frames, changed reader-source pins, duplicate JSON fields, mixed native stamps,
wrong helper builds and source intervals extending past their reply.

The parser derives source status, capture interval, completeness and payload
from the retained raw envelopes/pages. It does not accept editable wrapper
claims of success. ABSENT and ERR remain those source statuses even when the
transport is complete; zero bytes can be a qualified capture but never prove
a global empty population. Atomic is always false, and world_lineage remains
UNKNOWN.

An exported transcript is unauthenticated historical replay. Its recorded tier
is metadata, not independently authenticated observation. Only the local opaque
handle carries LOCAL_TRUSTED_COLLECTION provenance from this collector. This
trusted-agent mechanism is not cryptographic provenance or a security boundary
against arbitrary same-user code modifying objects/source/transcripts.
A receipt is historical, not a new current-session or current-state query.
Critical reader source files are hashed before/after collection and replay;
this is a scoped reader pin set, not a claim to hash the entire Python runtime.

## Offline qualification

140 controls passed with zero skips across test_liveevidence.py,
test_livebound.py, test_livereadpages.py and test_liveautomation.py (2.92 seconds,
resource-budget child exit 0, 36 MB peak job memory). Tests use the actual Lua
router, BoundPipe, AutomationClient, paging implementation and replay, with
explicitly synthetic native data and no game. Implausible resource-wrapper CPU
peak telemetry is not compliance evidence.

Controls cover 0 through 32,000-byte payloads, exact outer-string preservation,
UTF-8, one source invocation, source error statuses, identity/profile drift,
counterfeit source pins, page tampering, failure before dispatch, incomplete
traces, capacity and provenance. Native Windows/game transport and a complete
campaign preflight remain separate runtime and integration qualifications.

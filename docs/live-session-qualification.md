# Public load-session foundation

This module is offline infrastructure. It is not wired into the helper, does not
implement a public MD provider, and does not qualify save/load behavior in X4.
Tests exercise synthetic transport responses and temporary host journals only.

Historical first unit, MEASURED 2026-11-09: `pytest -q -ra -p no:cacheprovider tests/test_livesession.py`
under the resource wrapper passed 50 controls with one skip, exit 0 (ledger
`0cd478b89024`). The skipped real directory-symlink control encountered Windows
privilege error 1314; no alternate creation route was attempted. Nine Windows
API controls use ctypes-shaped stubs, not a connected game process. The suite
covers fresh/resume separation, repeated old-save invalidation, stale challenges,
UI reload, process changes, nonce consumption after a lost acknowledgement,
malformed/partial journals, exact peer identity and reply binding. These results
qualify host logic only; they do not establish native event ordering.

The source-reviewed successor additionally refuses a present-session READY with
an empty world observation. Final combined host/actual-Lua run passed 102 controls
with the same one privilege skip, resource ledger `d02ddf48ec01`, exit 0:
`pytest -q -ra -p no:cacheprovider tests/test_livesession.py tests/test_livesession_lua.py`.
This is 51 host passes plus one skip and 51 Lua passes, not a native runtime test.

`SessionJournal(path)` exclusively records monotonically numbered fresh load
allocations before sending a fresh handshake. `SessionLink(journal, peer,
exchange, revision, ui_generation)` uses an opaque one-use probe, followed by an
explicit `fresh(probe)` or `resume(probe)`. There is no automatic fallback.
Timeout after fresh allocation consumes that nonce; recovery requires another
probe and explicit resume of the exact latest recorded session. UI reload uses a
new UI generation and the same load nonce. Authoritative MD invalidation after a
save load requires a new host allocation even when loading the same old save.

The public provider ID is `x4-live-session-v1`. Private ROR adapters consume its
nonce as a secondary binding; they cannot act as the public world authority.
Every public query reply must carry exactly the five native identity stamp fields
`provider`, `revision`, `session`, `generation`, `world_token` and pass
`verify_reply()`. That method rechecks the measured host peer and durable journal,
then returns the full host identity envelope; host PID/start/load-generation are
not represented as native echoed observations. An action provider must recheck the same public identity before
preparation, execution and observation. Identity acceptance is not native action
completion, source attestation, authentication, or permission.

Handshake request fields are exactly `mode`, `session`, `challenge`, `generation`.
Mode is probe/fresh/resume. Probe session is empty; the other modes use a 32-hex
load nonce. Challenge and UI generation are each 32 lowercase hex characters.
Reply fields are exactly `kind`, `provider`, `revision`, `mode`, `challenge`,
`generation`, `session`, `has_session`, `reason`, `age`, `world_token`. Probe
returns STATE; fresh/resume return READY with the requested nonce. REFUSED and
missing/malformed replies leave the host unavailable. Revision is 64 lowercase
hex; challenge and generation must echo the request. Age is finite nonnegative
observation only. `world_token` is a future lineage observation: the producer
initializes it before a session becomes present, so present-session replies
require a nonempty token; absent-session probes may return empty or retained
tokens. Its presence never upgrades `world_lineage="UNKNOWN"`.

`connected_pipe_peer(handle)` is a read-only Windows adapter for the existing
Python named-pipe **server**. It uses the connected **client** PID, opens that
process with QUERY_LIMITED_INFORMATION, reads its exact creation FILETIME and
full executable path, and rechecks the client PID. It refuses non-X4 images and
unavailable measurements. Keep the same connection through the handshake; the
PID/start tuple does not alone prove a world has not loaded.
The caller must separately bind the exact expected executable, profile and
deployed sources. An `X4.exe` basename is not campaign/profile proof; the adapter
retains the full measured image path for that external check.

Source evidence (READ): `_livepipe.py` creates/accepts the named-pipe server.
Microsoft documents [GetNamedPipeClientProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeclientprocessid)
and [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes).
The latter returns creation time as a 64-bit FILETIME; the implementation retains
integer text and never derives uniqueness from game age or rounded timestamps.
These native calls have not been exercised against X4 by this unit's tests.

The public MD implementation is a separately reviewed pure candidate generator,
`scripts/live_session_md.py`: initialize a
volatile absent session on new-world initialization; reset on `event_game_loaded`
before acknowledging requests; implement probe/fresh/resume as above; emit LOAD,
TRIGGER and invalidation markers. Native `md/notifications.xml:3841` demonstrates
the load event, but does not prove the new provider's wiring or event ordering.
The legacy helper manifest declares save=false and describes no MD/save-state writes.
That description does not cover the candidate session provider. Its composed
manifest/loader footprint needs updated disclosure and separate application review.
Stable persistent world UUID behavior has not been verified for this helper;
save=false is not treated as evidence either way. No MD XML is created here.

Limitations: preserve the host journal; deletion or rollback defeats monotonic
history. Exclusive allocation and fsync do not attest power-loss directory
durability. Capacity is 4096 loads, with explicit refusal rather than silent
pruning. A malformed/partial journal is retained and refused. No filesystem
sandbox or cryptographic authentication is claimed. Transport callbacks are
trusted integration code; arbitrary caller dictionaries are not independent
game evidence. The public provider must invalidate on every load and check its
current identity before every reply; the host cannot detect a load from PID
alone. Native event order, UI-only reload, repeated old-save loads, and actual
Windows peer identity still require source-reviewed wiring and one approved
disposable runtime campaign.

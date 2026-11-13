# Experimental agent automation CLI

Offline implementation, not deployed or runtime-qualified. No command here launches or restarts X4. Game runs still require their own immediate approval.

Use x4live automation with explicit --journal (an existing dedicated local directory), --image (expected exact executable), --profile-id, --build (full automation-bundle SHA256), --revision (public MD provider revision), --mode fresh|resume, and optional --pipe/--timeout. Then choose identity, query VERB [ARGS], prepare REQUEST.json, execute REQUEST_ID, or status REQUEST_ID. There is no fresh/resume fallback.

The exact connected pipe client supplies process ID, creation FILETIME and executable identity. The Windows process command line must contain one explicit -personalfolderid matching the requested profile, with no alternate personalfolder override. The first profile query is bracketed by process identity reads; every subsequent session identity check rechecks the connected peer. This does not prove the effective mod set. Missing or ambiguous native evidence refuses.

UI qualification first asks a read-only volatile bootstrap for a challenge-bound state. An unbound UI gets a new durable nonce before the first handshake. An existing UI must match the latest durable allocation for the same peer/build. The volatile proof is required by session_connect, so a UI reload between probing and connecting cannot accept an old proof. UI nonce allocations and game-load allocations use separate journal subdirectories. Their storage schema uses the historical session column as a nonce; public UI results call it generation. No clock-derived UI identity is used.

A preparation spec contains exactly id (32 lowercase hex), provider, action, arguments (object), binding. The client supplies the measured load session. The immutable request is reserved in requests before its first preparation dispatch. If preparation is asynchronous or its reply is lost, status reconciles it without resending prepare. Only a real prepared receipt creates the action journal. Execution uses its existing durable dispatch reservation; repeated execution refuses. Preserve journal directories across clients.

Exit codes: 0 means the requested read/identity operation completed, or an action receipt is prepared/completed; 4 means preparing/running; 2 means refused/unavailable; 3 means uncertain/degraded/failed. These are command outcomes, never ROR acceptance verdicts. The JSON includes the bound envelope and identity where qualified. After dispatch, even a reporting-time identity failure returns UNKNOWN with the returned receipt, request ID and journal location. No lost reply authorizes replay.

Legacy query blocks all automation transport verbs before opening a pipe. The automation query path also blocks action/session/pause routes. Only the router's closed read whitelist is callable. Large observations require scoped pages rather than silent truncation.

Offline evidence:
- UI/profile/router/client/CLI initial combined suite:101 passes, zero skips, resource ec4b12d3cf81.
- Post-dispatch reporting correction:25 CLI/client controls pass, zero skips, resource0f487502fde0.
- The Windows argv parser was exercised through the real OS API. Process/CIM, X4 pipe peer identity, MD lifecycle and helper loading remain native-test gates.
- Independent source reviews closed for UI/profile qualification, dispatch binding and durable CLI orchestration.
- The UI/world ticket protocol, native bootstrap, provider registration, loaded-module attestation and native actions require integrated controls before deployment. This document does not claim those are complete.


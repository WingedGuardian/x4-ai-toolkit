# Fixed generation capacity profiles

The toolkit's interactive limits remain the default. A trusted generated helper may select `campaign_v1`; wire requests cannot select a profile or supply numeric capacity overrides. A profile increases bounded capacity, not permission to act or proof a whole campaign fits.

The canonical own-code source is `x4validate._livecapacity`:

- `resolve(name=None)` returns a fresh flat dictionary with `name`, `revision`, `source_sha256` and fixed limits. Only `interactive` and `campaign_v1` are accepted.
- `lua_source()` returns the source for the equivalent copied-value Lua resolver. The generator binds its source/hash rather than adding another independently maintained capacity table.
- SDK construction is `factory(profile_name, trusted_resolver)`. Routing uses `env.capacity_profile` and `env.capacity_resolver`. Without a resolver only the legacy interactive fallback is available; it labels its source identity `legacy-interactive`.
- The generated bundle must bind the profile name/revision and source to its build identity. This unit does not itself add a wire quota endpoint or apply the generation plumbing.

A resolver is a trusted installed-code dependency, like the native adapters. It is not an authentication boundary against arbitrary local Lua. Constructors copy the scalar limits they consume and reject changed limit values, invalid source-identity shapes and unknown names.

| Limit | Interactive | Campaign |
|---|---:|---:|
| SDK retained request IDs |256|8192|
| Router retained action packets |256|8192|
| Router retained fresh read IDs |4096|65536|
| SDK logical retained-value budget |64 MiB|64 MiB|
| Full adapter-result charge reserved per active request |128 KiB|16 KiB|
| Maximum retained request charge |24 KiB|4 KiB|
| Per-record metadata allowance |1 KiB|1 KiB|
| Router logical packet/read-ID/payload budget |16 MiB|16 MiB|

The manifest additionally names the coordinated native-provider, orbit, capture and room-entry limits for their owners' generators. Their inclusion in the manifest does not claim those implementations have been updated or qualified by this unit.

## Reservation and terminal evidence

The SDK validates and copies a request, then charges its actual retained value plus the entire profile-valid result slot and metadata **before invoking provider preparation**. Preparation can have adapter-side effects, so reservation is earlier than execute. New work refuses before invoking an adapter when either request history or byte capacity is exhausted.

Prepared, running and unknown records retain the full result reservation. Once a valid completed/refused/failed result is accepted, the SDK releases only the unused part of that reservation: the request, actual result charge, metadata and consumed identity remain retained. Terminal data is sealed. Duplicate/contradictory late callbacks can mark the record unknown/faulted but cannot replace observations, refund twice, reclaim its ID or grow its released observation slot. Superseded generation callbacks cannot replace or charge current evidence.

The charge recursively includes value overhead, string bytes and table keys. It is explicitly a logical value bound, not allocator/RSS measurement; Lua object overhead, adapter histories, pending callbacks, native state, temporary clones and transport buffers are separate. The 64 MiB SDK and 16 MiB router ledgers are separate bounds, not a claim that the whole helper process uses at most 64 MiB.

Campaign results exceeding 16 KiB charge are malformed for that profile. They produce sticky unknown/protocol-fault evidence without retry or silent success. Interactive clone/value limits are preserved. For a result actually encodable within the bound wire's4096-byte inner string and 512-node limit, charge is conservatively bounded by 4096 string/key bytes plus 16×512 node/numeric-key overhead, below 16 KiB. The controls include a dense 500-value actual-codec result. This does not turn a result rejected by the wire into valid transport evidence.

The router retains every accepted packet/nonce. It checks capacity and charges packet bytes/identity overhead before invoking the SDK, and charges each fresh read ID before calling its source reader. One 32,000-byte read payload slot remains reserved; pages reuse the current capture. Status/execute with the same exact packet do not consume another packet identity. Reusing an ID with changed bytes refuses.

## Stage preflight interface

SDK and router expose trusted `capacity()` copied snapshots. Both include profile/source identity, used/limit request counts, logical retained charges and `eviction=false`. Router additionally includes used/limit read counts. SDK exposes result/request/metadata charge bounds.

SDK `preflight(new_requests)` and router `preflight(new_requests,new_reads)` report whether a conservative allocation fits current usage. They return `reserved=false`: this is an advisory source-bound snapshot, not a stage reservation or permission. Every actual preparation rechecks and reserves. Simultaneous clients can consume remaining capacity; no stage may assume an observed free quota remains reserved.

A stage manifest must account for present/absent captures, control images, camera start/stop, discoveries versus selected room entries, retries that are permitted only as new distinct observations, polling, UI pages, file membership and bytes. Full unknown/running reservations can exhaust the SDK before 8192 IDs. Small terminal records can retain all 8192. No UI reload, history eviction or replay is a quota workaround.

## Offline qualification

Combined actual Lua SDK/router/codec, existing action-core, read-page and wire controls: **153 passed, zero skipped**,9.46 seconds,68 MiB peak job memory, resource run `e38eaa9c9603`. This includes:

- all 8192 small terminal requests through the actual SDK/router, then exact boundary refusal and no replay of the first retained ID;
- all 65536 read IDs, with next-ID and old-ID refusal;
- pre-effect byte exhaustion, retained pending records, terminal unused-reservation release and subsequent valid allocation;
- duplicate/contradictory and superseded callbacks after terminal completion without quota underflow or evidence replacement;
- source-profile copying, numeric/unknown override refusals, dense actual-codec result, oversized-result quarantine and unchanged interactive boundaries.

An initial combined run had 146 passes and one synthetic fixture failure: Lua rejected a128-character formatting-width specifier before reaching the SDK. The fixture now builds the same bounded keys with repetition and a supported format. No runtime defect was inferred from that failure.

Final successor: the same four-file suite passed 155 controls, zero skips, in 8.46s (resource job f3a0bafb0797,68 MiB peak job memory). Two new controls reject explicit false profile names while preserving nil defaults. Their first run had 154 passes and one fixture failure because the router fixture omitted mandatory dependencies; the corrected fixture supplies inert dependencies and reaches the intended profile refusal. Independent source review closed on the final nil-only constructors and reservation/refund logic; no duplicate reviewer test run was counted.

Confidence by link: fixed profile arithmetic and constructor selection 99% offline; logical reservation/refund invariants 95% based on actual Lua controls and independent source review; compatibility of every generated provider result with the campaign bound remains dependent on the composed-provider controls; game/native capacity behavior is **not runtime verified**. No game, helper deployment, profile/save, capture or action execution occurred. Reported synthetic test adapter invocations are not engine calls.

The resource wrapper's implausible literal peak-CPU percentage is unqualified telemetry. Exit status, counts, elapsed time and job memory are separate observations.

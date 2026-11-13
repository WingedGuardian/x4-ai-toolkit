# Optional native room extension

Experimental own-code calling route for the qualified standalone room adapter. Runtime remains unqualified. Inclusion does not open a transporter menu, move the player, or authorize movement.

Pure `scripts/live_rooms_extension.py.prepare()` returns one candidate, `ui/live_rooms_extension.lua`, and exact source/output hashes. Load it through the fixed public extension catalog after the bundle and before bootstrap. No main helper, public session, core room module or shared assembly is changed by this extension.

The generated loader uses actual registered `Menus`, `ConvertIDTo64Bit`, `ConvertStringToLuaID`, `GetComponentData` and **getElapsedTime UI seconds**. The lock read follows native `GetComponentData(ConvertStringToLuaID(tostring(room)), "islocked")`; before the property read, the Lua ID must round-trip to the exact original uint64 room. Only native uint64 or a safe exact integer is accepted from that converter; no lossy numeric conversion is used. A null/misbound conversion or unreadable lock never means unlocked. No guessed engine menu parameters or transporter connection are accepted.

## Fixed provider

Provider `x4-rooms-v1` implements:
- `location {}`: execute captures sampled player/container/parent-room state.
- `targets {}`: execute captures the existing engine-opened TransporterMenu's native target list. The result is metadata with count, source, location, public capture ID and transport_pages_complete=false.
- `page {capture, offset}`: zero-based pagination of the current owned target capture. The immutable result carries capture, offset, next_offset, total, rows and end_of_retained. Join matching contiguous pages before claiming complete transport. Empty Lua rows may encode as an empty JSON object; only the explicit zero denominator qualifies that case.
- `transport {capture, index}`: one-based native target index. SDK prepare delegates to the actual room adapter's read-only preparation and privately retains its opaque native ticket. SDK execute consumes its dispatch record and calls the adapter once. No raw native ticket is accepted from a caller.

All requests bind the provider revision, public session/generation/world token and SDK request identity. The SDK retains immutable arguments. Source changes, superseded capture, malformed arguments and unavailable native observations refuse or return unknown.

The native room adapter independently rechecks menu/source/targets/lock/location and timing before dispatch. Its unresolved-transport interlock remains authoritative: no second transport until a stable native successor source slot and parent-room observation qualify its release. A timeout or caller assertion cannot clear it.

TRANSPORT_REQUESTED maps to SDK running, **never completed arrival**. A thrown/ambiguous native action stays unknown and is not replayed. Status is historical native request state plus the adapter's local unresolved flag; it performs no fresh arrival measurement. Successful location/target/page capture only means that observation was returned. A menu close or changed room alone is not causality proof.

Read actions send no native transport. No automatic native order, docking, walking, menu opener, save, load, launch or retry exists in this provider.

## Bounds and qualification

The unchanged room core bounds native targets at 128 and its own captures/tickets at 128. Its injected bounded encoder can refuse a smaller population because of node or byte limits; such a refusal remains unavailable, never an empty list. The provider retains at most 256 SDK records and one current target capture. Pages shrink against actual encoded payload size with reserved SDK/envelope overhead. One row that cannot fit is refused, not silently dropped.

Payloads are JSON strings in observations.payload_json to preserve SDK nesting limits. Raw counts and identity strings remain exact. A caller must not treat component IDs as newly issued general-query capabilities.

Offline tests use the actual generated loader, room core, SDK and bounded wire encoder with synthetic native/menu responses. They cover the exact Lua-ID wrapper, no query at construction/prepare-read, empty versus unavailable, immutable pages, lifecycle and lock changes, uncertain one-shot transport, unresolved interlock and successor-slot observation. They do not operate the game.

The core's player-room query is a documented source transfer rather than a copied player-room caller; it remains a runtime gate. The source slot still requires an already engine-opened menu. This unit therefore does not close automatic room entry or ordinary walking/docking coverage by itself. See live-rooms-qualification.md for native references and core assumptions.

## Helper composition preparation

The fixed helper catalog now accepts extensions=('rooms',), returning exactly
ui/live_rooms_extension.lua from the pinned factory. It shares the existing
pre/post source and duplicate-loader checks. Canonical order places rooms before
topology, conversation and persistence; none is included without request.
Independent composition source review is clear. Final combined composition/core/
SDK tests completed after resource admission recovered:63 passed, zero skipped,
resource run 8dc05ecb4b66, process handle 55466 exit 0. The earlier 1dc0b3a7 checkpoint
correctly recorded the then-pending run; this closes that offline composition
gate. Installation, automatic menu opening and runtime arrival remain unqualified.

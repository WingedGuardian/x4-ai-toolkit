# Native build-task read qualification

Experimental read-only factory, now wired into the isolated native assembly/bundle
candidate; not deployed or engine-qualified. It never submits, changes or cancels a
build task. It reports native observations, not delivery, construction success,
scheduler eligibility or economic balance.

## Native evidence

READ from game-owned 9.0 reference, not redistributed:

| Reference vpath | SHA-256 |
|---|---|
| ui/addons/ego_detailmonitor/menu_ship_configuration.lua | a18af705ec02eb1a0a0bef1f0e035eb26d418c8d2009514c578f03ec237cc707 |
| ui/addons/ego_detailmonitor/menu_map.lua | 6c56ba3881495ad8ee7db3a144c16079f2b2eaad56a3a1e5efe171f899fd6865 |

Ship configuration declares unsigned 64-bit BuildTaskID/UniverseID at 18–21, BuildTaskInfo at 43–53, GetBuildTaskInfo/GetBuildTasks at 501–502, and GetNumBuildTasks at 569. Its current/other build loops at 7948–7958 use container, buildmodule 0, both in-progress booleans and include-upgrade true. Map 8294–8323 retains the returned task/container/component/macro/faction/builder/price/resource/queue fields individually; its false include-upgrade value has a narrower scope. This reader deliberately uses the configuration scope with upgrades included.

Map 9415 and 9427 show native point lookup of a known task ID. The point return has no in-progress field; the module never fabricates one. Native price is retained as an exact signed integer with unit NATIVE_UNQUALIFIED; no credit conversion is assumed.

Source confidence: 95% for these call shapes and field transfer. The validity of native zero-count coverage and the runtime behaviour of returned IDs remain unqualified until observed with the real adapter.

## API

The factory accepts trusted ffi, C, globals.IsValidComponent, resolve_issued, canonical current, monotonic now and actual bounded wire encode. Native aliases, named BuildTaskInfo layout and field types are checked before use, so an already-declared native pointer type is reused.

- capture(issued_container, nonce32hex) resolves a live issued station/ship. Both native query branches use buildmodule 0 and include-upgrade true. Count/fill/count must agree, IDs must be nonzero and unique across both branches, and every row must name the requested container. A second full census must agree in IDs and fields. The canonical world and container are reread before acceptance.
- metadata(capture_id) returns copied historical scope/count/time metadata.
- page(capture_id, offset) returns exact task rows, total, next_offset and done. Native order within a branch is not claimed: output is deterministically sorted by task ID, while each native queue_position is preserved.
- task(capture_id, task_id) calls GetBuildTaskInfo only for an exact native ID retained from that capture. It verifies the returned task/container identity and rechecks container/session. Native zero ID reports NOT_OBSERVED with completion_inferred=false. Disappearance may have multiple causes and never proves completion. Updated fields are a new observation; the capture remains unchanged.
- dispose() clears the cache and retires the instance.

Component/builder IDs may be native zero and are retained as exact strings. They are observation handles, not automatically issued arguments for other helper queries. Empty native macro/faction strings remain empty; unreadable pointers refuse. No names are converted into identities.

A successful empty census is OBSERVED with complete=true and total=0. Invalid caller inputs, overflow, failed native reads or unstable observations return REFUSED/complete=false without an invented zero count. Completeness applies to this exact native container/filter census, not all tasks in the universe. Individual point reads do not refresh or extend the historical census.

## Bounds and lifecycle

At most 4,096 native rows per branch, 32 enumeration calls per capture, 128 capture nonces per instance, 300-second TTL and 1 MiB of accounted retained row/header JSON. Native buffers and the repeated census are also bounded by the two branch limits. The encoded-byte accounting is not a measurement of Lua allocator overhead.

A page has at most 16 rows and at most 3,000 bytes from the actual wire encoder, including its header. Rows are reduced until the full payload fits. An oversized header/single row or zero-progress page refuses; identities are never truncated. Metadata and point reads use the same payload limit. Consumers must collect all pages without gaps; capture completeness alone does not prove transport completion.

Repeated current nonce returns the same immutable capture. New eligible attempts supersede it, including failed attempts. Expired, superseded or canonical-session-changed captures refuse. Backward clocks refuse. All observations are explicitly non-atomic.

## Verification status

MEASURED offline: 54 actual LuaJIT controls passed, zero skipped (12.13 seconds; 68 MB peak resource-wrapper job memory). Controls cover native pointer/layout compatibility, exact 64-bit IDs and raw prices, complete-empty versus failed counts, duplicate/foreign tasks, count/fill and same-count identity/field drift, paginated identities through the actual nested wire envelope, cache bounds, lifecycle refusal and observed-ID-only point reads. The initial admission-only attempt did not launch tests and was stopped before correcting a synthetic nil-pointer stub. The subsequent admitted run completed with exit zero. This evidence is synthetic, not a game observation.

No native game calls, task mutations, deployment, profile changes or game launch were performed for this unit.

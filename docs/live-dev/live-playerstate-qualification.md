# Native player-state snapshot qualification

This read-only factory reports player credits, licence possession for one issued peer's native faction catalogue, and the native owned-blueprint ware census. It does not grant money, licences or blueprints, decide purchase eligibility, or claim an atomic engine snapshot. No engine qualification has been performed.

## Source and interface

READ: the native `ui/addons/ego_detailmonitor/menu_trader_blueprintsorlicences.lua` declares `UIBlueprint`, `GetNumBlueprints` and `GetBlueprints` at lines 10–25; resolves trader ownership at 127–130; reads player money at 134; gets the faction licence catalogue at 145; enumerates owned blueprint wares at 179–185; and checks each licence with `HasLicence("player", licence.type, traderfaction)` at 304–306. The confirmation path at 595–610 compares `GetPlayerMoney()` with the credit price; the separate `AddPlayerMoney` call multiplies by 100. This reader uses only the getter and never invents a raw cents getter.

The native reference file SHA-256 is `8715f253f12aecbbfab78fa82661d622ba8d3fd77c79ac78910ba1785d3e8f3b`. Game source is not distributed.

`new(d)` takes trusted `ffi`, native `C`, the five named global functions, `resolve_issued`, canonical `current`, monotonic `now`, and the actual bounded wire `encode`. The loader must supply the canonical public session reader, exact issued native component handles, and bind this module's source/build. Caller-supplied state or faction strings are not substitutes.

- `capture(peer_token, nonce32hex)`: observe an issued live peer's faction and capture the three state categories. Repeating the current nonce returns the same immutable capture. A new eligible attempt supersedes the old capture; failed attempts are not silently retried.
- `metadata(capture_id)`: return copied metadata while its session and TTL remain valid.
- `page(capture_id, "licences"|"blueprints", offset)`: return copied rows, exact total, next offset, completion/coverage, peer, canonical stamp and capture times.
- `dispose()`: invalidate the cache and permanently retire this instance.

All five canonical identity fields are checked before/after capture and before each subsequent read. A UI reload, save load, superseded capture, backward clock or expired capture refuses access. These checks depend on the injected authoritative public-session implementation; no independent world identity is inferred here.

## Evidence semantics

Money is a decimal string in credits only when the getter returns a finite safe integral Lua number or a native signed/unsigned 64-bit integer. Unsafe, fractional or unreadable values are UNKNOWN, without rounding or guessed zero. The native getter's actual runtime return type remains to be observed. Money is reread before capture completion; changes produce UNKNOWN.

Licence rows retain catalogue ID, type and the independently read boolean possession for the peer's exact faction. Catalogue membership is not possession. Two reads must agree in identities and possession; this is not a census of all factions, nor a licence seller or purchase-route qualification.

Blueprints retain ware identities and native occurrence counts. Count/fill/count and a second full identity census must agree. Duplicate native ware variants are aggregated explicitly; reported native rows and unique wares remain separate. A negative is meaningful only within a complete owned census, not as proof of ware validity or purchase eligibility.

A failed census reports UNKNOWN and `complete=false`; a successful empty census reports observed zero. Capture completion means all three requested categories were observed within this declared scope. It does not mean every page was transported, fitting or gameplay passed, or the observations were simultaneous. Consumers must collect all pages without gaps and respect each category's coverage.

## Bounds and transport

Native blueprint rows: 8,192; licence catalogue rows: 512; native calls per capture: 2,048; retained encoded capture bytes: 524,288; capture history: 256; TTL: 300 seconds. Native identifiers are at most 256 bytes. Native handles remain exact 64-bit values/decimal strings.

Each page carries at most 16 rows and is reduced using the actual encoder until its entire inner payload is at most 3,000 bytes. Metadata uses the same limit, below the bound transport's 4,096-byte string ceiling. An oversized header or single row explicitly refuses; no truncated identifier or zero-progress success is returned. The cache budget is computed from bounded encoded pieces, avoiding the wire encoder's per-invocation 512-node limit. Empty array/object spellings have equal encoded length; a control checks full JSON accounting for a small capture. The public wrapper still applies its own outer byte/node/depth limits.

MEASURED offline: 63 actual LuaJIT controls passed, zero skipped. Controls include retained native pointer type/layout, values beyond 2^53, unknown/empty distinctions, stable-count identity drift, owner/session changes, cache/TTL/history limits, 200 long blueprint identities and 40 long licence identities across exact wire pages, oversized escaped rows, metadata refusal, and the actual nested public response encoding. These use synthetic inputs, not the game.

Source-transfer confidence: 95% for the named native read call shapes and scope; 98% for tested bounded state/paging mechanics. Runtime confidence remains unqualified until the loader, actual getters and same-subject native comparison are observed in the approved disposable campaign.

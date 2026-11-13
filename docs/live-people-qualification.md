# Native crew/people observations

`live_people.lua` is a read-only factory with `ffi`, `C`, `globals.IsValidComponent`, `resolve_issued`, `current`, `now`, and `encode`. Call `capture(issued_container, nonce32hex)`, then `page(capture_id, offset)` until the exact total is joined; `metadata` rereads the capture identity and `dispose` retires it. The native assembly exposes fixed people_capture, people_page and people_metadata reads, requiring exactly two, two and one arguments respectively.

READ analogues: `ui/addons/ego_detailmonitor/menu_ship_configuration.lua:154–173,599,611–612,10983–11024` supplies PeopleInfo, RoleTierData, NPCSeed and the calls. `ui/addons/ego_detailmonitor/menu_map.lua:17112–17172` supplies the full role census, including non-hireable roles, arriving personnel, zero-tier skill-0 fallback, and transfer/arrival observations. GetPeopleCapacity uses the native empty macro and includepilot=false. Native menu labels and description strings remain native observations.

Scope is **the returned role/tier NPCSeed population**, not every NPC entity or a physical headcount aboard. NPCSeed decimal strings preserve all uint64 bits and are not issued component IDs. Each PERSON carries HasPersonArrived and IsPersonTransferScheduled independently; scheduled transfer can coexist with arrived=true. The UI's derived transferring count is not substituted for either observation. Capacity explicitly excludes pilot; no unsupported assertion equates that capacity to the returned role sum. A zero-tier role uses native fallback skill 0, marked implicit, not an invented named tier. The unassigned role is counted once.

GetNumAllRoles is an allocation capacity, not the number of returned roles. Returned role count must fit it. Every reported tier and person count must match its filled result, every tier amount must account for its role, and NPCSeed identities must be unique across the capture. No role is filtered by canhire. A complete empty census is distinguishable from REFUSED. Two full sorted censuses, capacity rechecks, exact issued-container recheck and canonical public stamp bracketing detect sampled changes. They do not make a native atomic transaction; a change and return between samples remains possible.

Bounds: 64 roles, 64 tiers per role, 8,192 people total, 40,000 counted census calls (class/validity checks are separate), 2 MiB retained row JSON plus one bounded header, one current immutable capture, 128 attempted nonces, 300-second TTL, at most 16 rows and 3,000 encoded bytes per page. The actual wire encoder also enforces its node limit. A single unrepresentable row or oversized header refuses; no truncated success. Empty rows may encode as an empty JSON object, and total=0 is the explicit denominator. Old captures are superseded, nonce replay cannot change the target, and every page rechecks the session and monotonic clock.

FFI checks verify uint64 aliases and native struct alignment, field offsets, pointer/bool types, signedness and numeric storage representation before any census call. Native declarations come from the named sources; synthetic callbacks do not prove the game binary's ABI. Offline LuaJIT controls cover these boundaries. Native runtime and integration are not yet qualified.

Offline integration successor: the source-bound bundle includes this module and
the native assembly supplies its checked component conversion, exact issued IDs,
canonical session and shared disposal lifetime.172 assembly/bundle/composition
controls passed, zero skips (resource 1701918c08c8), including actual empty-census
capture/page and failed-getter refusal through the real native wrapper. Independent
wiring review is clear. This supersedes the older lack of offline integration;
native runtime, hired-subject joins and final campaign acceptance remain open.

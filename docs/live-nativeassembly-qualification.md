# Native adapter assembly

Current successor includes the native_save_path zero-argument bound read, using
the actual paths observer and preserving its canonical stamp or UNAVAILABLE
payload. Its disposer is owned by the assembly; construction queries nothing.
The actual observer-through-assembly controls cover both a successful exact path
and an unavailable getter. Final affected135 controls passed, zero skips,
resourcea6631cd43e7f; unchanged31 native-path controls passed in the prior combined
run81fba399d320. Independent READ review is clear. No screenshot root is inferred
and no actual game path was queried.

Offline-qualified; runtime remains unverified. No native game call, deployment,
profile or save operation has been performed by this unit.

The module exports new(deps) and read_verbs(). The latter returns a fresh closed
vocabulary map without constructing anything; bootstrap can use it before its
public session exists. new accepts exactly the named module roles menu, orders,
camera, transactions, nativeproviders, menupages, playerstate and buildtasks
along with ffi, C, globals, Helper, public_current, resolve_issued, clock, encode
and build (full bundle SHA256). Unrelated bundle module roles may coexist but
are never dynamically selected.

The instance returns provider_spec, provider_revisions, read_verbs, read and
dispose. Bootstrap must permit x4-native-v1 at exactly this bundle SHA256 before
registering provider_spec. Construction performs FFI declaration/layout and
callable-symbol inspection, never native reads, opening menus, orders or camera
actions. Menus is read lazily from the actual native registry.

| Read | Exact string arguments |
|---|---|
| menu_capture | fresh 32hex nonce |
| menu_page | nonce, canonical zero-based decimal page index |
| orders | issued ship |
| transactions | issued station |
| state_capture | issued peer, fresh nonce |
| state_page | returned capture ID, licences or blueprints, decimal offset |
| buildtasks_capture | issued container, fresh nonce |
| buildtasks_page | returned capture ID, decimal offset |
| buildtasks_task | returned capture ID, task ID already observed in that capture |

read returns transport OK plus encoded JSON or ERR with a bounded reason.
Transport OK never promotes the payload's REFUSED/PARTIAL/UNKNOWN into acceptance.
Full menu/state/build-task data uses the existing bounded capture/page adapters.
Direct order/transaction reads fail explicitly if the encoder's node/depth or
32,000-byte output ceiling is exceeded. Generic host response paging cannot
repair a pre-encoding node overflow; the scoped read stays unavailable.

All read routes check the provider's current full public identity before and
after collection. Disposal retires provider and state/build-task caches; it
never restores a camera, cancels an order or changes a game object. Disposal
failure remains sticky and prevents successful retirement.

Native declarations added by assembly are copied from the reviewed camera
source: native menu_map.lua594/777/779/853/865/978–983 and
menu_followcamera.lua8. Their local source hashes and semantic caller lines
remain recorded in live-camera-qualification.md. Menu conversion is the
existing Helper.convertComponentIDs path; menu/orders/transactions/playerstate/
buildtasks retain their previously documented native dependencies and guards.
No Egosoft source is included. Callable metadata does not attest parameter ABI;
real registration/global availability and engine behavior remain unverified.

The prepared tests cover metadata isolation, all nine read routes, malformed
arguments, missing native bindings, disposal, overflow, refusal preservation,
and construction using all eight actual adapter modules with throwing native
stubs. The admitted combined run passed81/81 controls (33 assembly plus48 native
provider), zero skips, child exit0, rb5ef7c79db23b, pytest0.55s. Current source:
83f07c02259b7330f26c0bef28dbc3ea052ce0c71bfc750e5dbded83e4f8df29;
test:f2d039bf20e6b2d5391fe46c367ffe2368b6206d358830a53876404adea11d1b.
The root independently READ reviewed the constructor and integration contract.
Caught LuaJIT SEH0xe24c4a02 diagnostics remain visible during refusal controls;
the completed process exited0, not a diagnostic-free execution claim.

The command selected tests/test_livenativeproviders.py and
tests/test_livenativeassembly.py in tools/x4validate via the resource wrapper,
uv run --frozen python -B -m pytest -q -ra --tb=short -p no:cacheprovider.
Source confidence is96%; live integration remains limited by the untested native
boundary. No E2E claim.

## Native ID conversion successor

The earlier81-control result remains historical for the pre-conversion source.
Root review identified that raw UniverseID cdata does not belong in bare
GetComponentData: the existing helper/test records an in-game1539/1539 unclassified
failure from that path. This is READ historical evidence, not a new measurement.
Native map5168/5564/7522 converts via ConvertStringToLuaID(tostring(id)); map26779,
3521 and21260/21309→22317 show the corresponding order-parameter/docking boundaries.
Native ship_configuration9987 converts through ConvertStringTo64Bit for validity.

Assembly now supplies those native conversions around bare globals, preserving
variadic nil-bearing returns and leaving C.* calls on exact native cdata. Native
conversion must round-trip to the exact original ID. Bare ConvertIDTo64Bit output
is normalized to uint64 only when already uint64 or a finite nonnegative integer
strictly below2^53, matching the existing helper's precision safeguard. No tonumber
ID conversion or inferred approximate handle is allowed. Seven new controls cover
the conversion successor. All40 assembly controls passed, zero skips, childexit0,
rb5c7a08e66db5, pytest0.23s. Current source161b69a15316b8e88b139c052e8eceda1b3f11fd2fe9d1c7900a9d0f20de2899;
test6d35e9a2916f27955fbb890ad485ab322ec71444824b0687e76c0e9033f58a68.
Root independent READ review found no remaining conversion blocker. Existing48
provider controls were unchanged and not rerun. Native runtime remains unverified.

## Dialogue model observation successor

The assembly also constructs and retires the bundled read-only dialogue probe.
`dialog_probe` accepts exactly zero arguments and uses the same bound public
session before/after the read. It reports UNKNOWN actor, section and rendered
visibility, never conversation-route acceptance. Integrated140 controls passed
(31 probe,42 assembly,67 bundle), zero skips, resourcefed6415df34f. The original
40-control receipt above remains historical. No conversation selection was added.

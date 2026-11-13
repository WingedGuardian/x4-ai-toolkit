# Source-bound capacity composition

The helper generator selects one fixed profile, `interactive` (the default) or
`campaign_v1`. A caller cannot replace that choice or supply numeric limits through
the wire. Selection changes the generated bundle's BUILD identity.

The canonical source is `tools/x4validate/x4validate/_livecapacity.py`. Generation
records both its raw and LF-normalized SHA-256, the emitted Lua SHA-256, resolved
limits and revision. The resolver is a local in the bundle; the module inventory
remains 28. Composition checks source bytes before and after all factories run.

The same profile/resolver reaches SDK, router, native assembly and trusted
extension context. Extension context exposes the profile name as a string,
the revision as a 64-hex identity and the closed resolver. Camera, capture and
diagnostic-room factories receive the selected generation profile. Other fixed
extensions retain their separately documented bounds.

The fixed bound read `automation_capacity` takes no arguments and returns current
SDK, router and native-provider counters. It cannot be supplied by the legacy
read namespace. The usual session-bound read envelope and captured source identity
still apply. Nested counters are sequential samples, not an atomic reservation.
`reserved=false` and `future_fit_verified=false` are explicit. Reading capacity
itself consumes a read identity; stage planners must account for observation and
polling overhead. SDK/router retained-value charges are not process RSS.

A campaign profile raises specific audited history ceilings while retaining
consumed request identities. It does not establish that all future actions, image
bytes, menus, clip/audio budgets or an unspecified campaign will fit. Preflight
needs a source-bound stage inventory, actual remaining usage and declared bounded
costs; missing usage or future byte bounds stay UNKNOWN. Do not evict IDs, replay
uncertain actions, reload the UI or clear bookkeeping to manufacture headroom.

Offline qualification: 284 tests passed, zero skips. These cover bundle/bootstrap/composition, all nine fixed
extensions, profile identity/substitution, fixed-read routing and actual SDK/router
constructor forwarding, plus actual generated-bundle/router/codec through BoundPipe
and host ReadCapture. Review caught a table-versus-encoded-string return defect:
the added whole-chain control failed before the correction and
passes after it. The earlier 209-check constructor group did not
exercise that full read path. Native calls were synthetic. This receipt does not
include the separate provider/host capacity
suites or establish a combined full-campaign runtime result.

No installed mod, profile, save, UI focus or game state changed. Canonical MD
qualification belongs to the individual changed extension generators; a later
immutable combined freeze must pin their exact qualified source and outputs.

Additional constructor qualification passed all six real nine-loader
lifecycle controls under each selected profile (12 tests, zero skips). It exercises
actual shared LuaJIT factories, registration, cleanup and failure handling with
native calls forbidden before world readiness. Six interactive cases overlap the
284-check group; the six campaign variants are additional. This does not execute
gameplay or establish the future campaign fits.


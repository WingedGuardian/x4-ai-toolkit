# Public native bootstrap candidate

The bootstrap is an offline candidate, not deployed or wired into the current
helper. It consumes own-code module factories supplied by the generated bundle:
session, actions, wire and automation. No game data is embedded. Module loading
through extension-qualified require is a working ModSupportAPI analogue, but the
selected bundle avoids depending on its runtime module-resolution behavior.

MEASURED 2026-11-09: `pytest -q -ra --tb=short -p no:cacheprovider tests/test_livebootstrap.py`
passed 25 controls with zero skips, resource ledger `f1612f4bfdbe`, child exit 0.
Tests execute the actual bootstrap/session/core/wire in LuaJIT with native callback
stubs and a router-construction stub. They do not qualify the final combined router
or game engine. The run emitted Windows exception diagnostics 0xe24c4a02 during
the intentionally caught Lua refusal controls, then completed all controls; this
is not a claim of a diagnostic-free stream. Earlier failed runs exposed the
function-type inspection limits below; those claims were narrowed explicitly.
Independent review then added refusal of explicit-false disposers and known-null
converted player IDs. The final same-command run passed 34 controls, zero skips,
resource ledger `b1af8d0e987e`, child exit 0, with the same disclosed LuaJIT
exception diagnostics. No source behavior changed after that run.

Confidence: 97% in the source-copied getter declarations, 95% in the bounded
wrapper/lifecycle behavior under the measured controls, and 85% in untested
native integration. The last figure assumes the documented native blackboard
and event wrappers behave as their existing callers do; approved engine testing
must verify it. No offline result substitutes for that runtime gate.

`new(deps)` takes modules, source revision, full bundle build hash, native globals,
ffi/C, the main helper's closed read vocabulary/dispatcher and an explicit
provider revision allow-map. No providers are enabled by default. The API exposes
session, registry facade, router, ui_state, ui_authorize, register_provider,
register_cleanup and dispose. A replacement disposes the prior singleton before
subscribing new callbacks. Old callbacks cannot read/clear replacement queues.

`ui_state(challenge)` is a read-only volatile UI probe. It returns exactly kind
UI_STATE, echoed challenge, current bound generation (or empty), and bundle build.
The challenge is also a bounded volatile ticket. Only the most recent UI probe's
ticket remains active; a new probe supersedes the previous one.
`ui_authorize(ticket,generation)`
must run before session connect; it rejects tickets from an earlier instance,
stale unbound observations, or attempts to change this instance's generation.
The same ticket can authorize the correlated probe/fresh exchanges. It grants
no gameplay permission. The host must durably allocate a fresh UI nonce after
an unbound observation; reuse of a previously bound nonce is allowed only after
the current UI reports it and the host independently recognizes it. Recreating
the bootstrap invalidates tickets, so a reload between UI probe and session
connect cannot silently adopt an old host generation.

Native evidence (READ): installed sn_mod_support_apis/ui/simple_menu/interface.lua
SHA256 db52b195fea7dd7915c803f960c21000094324ea092171f4bd8a1702c3530d86,
lines 20–34 use extension-qualified require; 41–42 declare uint64_t UniverseID and
GetPlayerID; 103 registers the event; 109 converts the native ID through
ConvertStringTo64Bit; 143 reads the player blackboard; 151 clears it with nil.
The packed provider was read through the toolkit's verified member reader without
extracting or changing it. Native ego_detailmonitorhelper/helper.lua331 declares
double GetCurrentGameTime(void); 8733/8750 use it for elapsed game time. The local
timeout clock follows native getElapsedTime calls such as menu_docked.lua314.
AddUITriggeredEvent uses the fixed public session screen/control and the existing
native table payload bridge. Existing incompatible aliases, reported return
types and absent/null symbols cause refusal before native calls.
MEASURED in offline LuaJIT: pointer `ffi.istype` admits different function return
types, and independently parsed equivalent function ctypes compare unequal by
identity. `typeof` text also omits argument lists. The guard therefore compares
its reported return-type rendering and nonnull addresses, with wrong-return,
missing and null controls. It cannot inspect parameter ABI. An explicit control
demonstrates that a wrong-arity callback is not distinguished and is never called;
`abi_status()` reports SOURCE_DECLARED_PARAMETER_ABI_RUNTIME_UNVERIFIED, with
parameter_abi_attested=false. Native parameter signatures are source-declared,
not runtime-attested. No native function is invoked to guess its signature, and
no introspection hack or unsafe symbol probe is used.

Each blackboard read obtains the current native player ID and verifies it did
not change during the read. Clearing uses that exact read's player and refuses
if the player changed. Known null/boolean conversion results are refused before blackboard access,
including zero numeric and native uint64 forms. The exact engine userdata
representation remains unqualified; no arbitrary userdata ABI is assumed.
Game age is queried fresh through GetCurrentGameTime; it is not inferred from
the session handshake age. Its equality to MD player.age
has not been runtime-tested. No source/protocol result proves camera, purchase,
fixture completion or stable save lineage.

Provider registration requires the allow-map's exact immutable revision and wraps
both session and invoke in the public current-state check. The registry copies
the fixed action vocabulary and callbacks. An optional trusted disposer, or
register_cleanup(id,fn) after registration, retires the provider on replacement;
it does not authorize in-game cleanup. Disposal failure stops replacement and
remains sticky across later attempts. Successful/idempotent disposal returns true.
Explicit false from a disposer is failure; nil remains supported for the existing
legacy adapters whose successful dispose has no return value.

Integration requirements remain owned by the main helper: load the generated
bundle before live_query.lua, publish its masked full-source hash, install the
reviewed public MD script with updated manifest disclosure, bind the expected
profile/deployed bytes, route session UI probes and ticketed connects, and require
session-bound envelopes for all reads/actions. No loader, XML, profile or save is
written by this unit. Native event order and blackboard behavior remain runtime
gates for the approved disposable campaign.
# Player conversion successor (2026-11-09)

The native player ID must round-trip exactly before any blackboard read or clear. A bare native number must be a positive finite integer below 2^53; a uint64 must equal GetPlayerID exactly. Other types, different IDs and unsafe conversions refuse. This extends the earlier null-only check, which did not establish identity preservation.

MEASURED:41 actual-LuaJIT controls pass with zero skips (resource 73b889777b11), using synthetic native callbacks. Independent source review is clear. No X4 call or deployed helper was tested. The faulthandler plugin was disabled to avoid misleading diagnostics from intentionally caught LuaJIT exceptions; assertion and process-exit checks stayed active.


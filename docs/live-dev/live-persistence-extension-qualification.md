# Persistence extension and production host port

The pure live_persistence_extension.build(sources) factory embeds the three own
Lua sources (persistence, persistencegate, persistenceprovider) in a source-bound
extension loader. It declares x4-persistence-v1 through the existing public bundle
extension catalog. Load this generated file after the public bundle and before
first world-bootstrap construction. Its native signatures are copied from
gameoptions.lua:262,271,278. No mod XML is written by the factory.

The loader uses ctx.registry, ctx.public_current, ctx.clock, ctx.encode and the
native globals/Helper/C supplied by the bundle. Gate cleanup is registered
immediately. Provider cleanup is explicit, idempotent and returns true on success;
a failed retirement remains failed. No MD channel or generic native evaluation is
introduced. Actual native ABI/function availability remains a runtime boundary.

## Host route

Use connect_persistence with the actual connected AutomationClient/ProfilePeer,
configured profile, three distinct existing task journal directories (observer,
port and dispatcher), extension revision and a PersistenceAuthority backed by
the applicable trusted agent conversation decision. It returns the dispatcher,
port and SlotVerifier after a read-only native_save_path query and exact comparison
with the canonical configured profile/save directory. It records that native
observation and its read capture. It neither launches X4 nor creates profile files.

Missing, relative, linked, mismatched or unqualified native path evidence refuses
construction. Task journals, including the authority journal, cannot overlap the
profile. A profile basename alone is not discovery evidence. Native path
observation supplies a save directory, not a screenshot directory.

Call port.refresh() explicitly before querying the catalogue. A catalogue still
loading remains a refusal/non-answer; this library does not silently retry an
action. The agent may request another observation after the native readiness
condition is available.

PersistencePort uses actual AutomationClient preparation and ActionJournal
execution for every fixed operation. Each wire request and outer ticket mapping
is retained before dispatch. Only its opaque local access capability can pass
the reserved-provider check; generic CLI or AutomationClient prepare, execute
and status cannot adopt x4-persistence-v1 JSON. Direct execution additionally
requires a consumed, unexpired PersistenceAuthority record from this instance,
with unchanged issuance/consumption journals, exact named profile/current identity
and complete inventory hash. That proof is checked again after SDK preparation,
immediately before SDK execution. The native capability is claimed once before
the first round trip. A timeout never triggers an automatic resend.
The host also requires an unfaulted SDK action-core receipt and an outer status
consistent with the native result. Retained observations under an SDK protocol
fault cannot become a successful native preparation or execution result.

This is a trusted in-process policy boundary, not a security sandbox against
arbitrary same-user Python or a program bypassing the pipe owner. No CLI --yes,
caller boolean or serialized receipt mints approval. The specific native-effects
exception described in live-persistence-dispatch-qualification.md is not approved
by publishing this implementation.

## Catalogue completeness and transport limits

The native observer captures the full raw standard-slot rows once. The provider
checks the complete denominator and preflights every row against the actual
encoder before publishing a capture header. Each immutable page carries capture
ID, zero-based index and the total count. The host requires exactly all pages,
strict integer counts, exact request identity and the same public stamp. Empty
rows become an empty list only from a qualified zero-count capture.

A single oversized or unsupported native row refuses the capture; it is not
dropped. There are at most 64 rows, a 4 MiB retained page budget, a 30-second page
window and 512 provider requests per instance. Each result stays below the wire
budget; an unknown outcome after a mutation remains consumed. Native catalogue
snapshots are explicitly non-atomic.

SDK completed means this fixed call or observation was returned, not a save/load
or gameplay acceptance result. SAVE_REQUESTED, LOAD_SCHEDULED and LOAD_REQUESTED
remain running. All provider observations say native_effect_complete=false.
The separate host settlement and own-save transition join carries its exact,
limited evidence; generic world lineage remains UNKNOWN.

## Qualification scope

Controls exercise the actual generated extension, Lua SDK and wire codec,
AutomationClient/ActionJournal, PersistencePort, native persistence gate and
observer with synthetic native calls and temporary synthetic saves. A separate
control uses the actual generated public catalog/facade and cleanup, with its
bootstrap and native assembly explicitly doubled; it does not claim actual X4
loading. Generic CLI restrictions run before opening a pipe.

The final combined owner run passed 105 controls with zero skips across
test_livepersistport.py, test_livepersistdispatch.py and test_liveautomation.py
(60.89 seconds; resource-budget child exit 0, 62 MB peak job memory). These are
offline controls, including the actual producer/consumer chain with synthetic
native calls, not native save/load qualification. The resource wrapper's
implausible CPU-peak telemetry is not used as compliance evidence.
After independent review, all 35 port controls passed again (zero skips,
28.43 seconds, 59 MB), including four new outer-status/protocol-fault controls.
The combined qualified denominator is 109 distinct controls; unchanged dispatcher
and client results retain the prior combined run. No native execution is inferred.

Deployment, actual native path/ABI, actual file effects, specific operation
authorization and live save/load still require their own evidence. No module or
test launches X4 or changes the user's save/profile files.

The initialization successor removes the provider's eager canonical-session read.
Construction registers the provider without calling native operations or requiring
a completed world handshake. An unavailable or malformed initial identity returns
no session; the first valid canonical identity binds the provider. Once bound,
observed identity loss, drift or invalid clock permanently refuses further dispatch,
even if the original values subsequently return. Disposal remains independently
available and idempotent.

The successor passed all 41 port controls with zero skips (22.32 seconds, 60 MB, child exit 0), including six actual-SDK lifecycle
controls. This is offline synthetic-native evidence; the combined real-loader
constructor check is a separate integration qualification. The factory itself is
unchanged and derives a new provider revision from these consumed source bytes.

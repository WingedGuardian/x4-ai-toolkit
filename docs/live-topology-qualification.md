# Topology observation (offline proposal)

The optional `x4-topology-observer` extension observes native clusters, sectors, zones and gates. It changes no game topology, knowledge, ownership or orders. Its MD generator returns candidate bytes in memory; nothing in this unit deploys the extension or launches X4.

**Runtime remains unqualified.** Source review, synthetic native-transfer controls and schema compilation do not establish that the engine loaded or triggered the instrument. The MD emits unconditional LOAD and first-action TRIGGER markers for that later check.

## Source and scope

READ native analogues:
- `md/upkeep_management.xml:25`: default multiple cluster enumeration.
- `md/inituniverse.xml:33–37`: multiple sectors and zones within a sector.
- `extensions/ego_dlc_boron/md/setup_dlc_boron.xml:263–266,272–294,407,1482`: multiple gates, configured exit and exit sector, inactive gates and active state.
- `libraries/scriptproperties.xml:901–909`: gate exit, destination and accelerator distinction.
- `libraries/common.xsd` documents normal-cluster defaults and exclusion of highways from zone queries. This documentation narrows the copied native mechanisms; it is not the implementation analogue.

The native default scope excludes non-normal clusters. Zones exclude highways. Gate queries do not enumerate highway endpoint classes. There is no owner, knowledge or active filter. An inactive gate can have a configured exit: this does **not** establish traversability. Component IDs are observations only; this provider does not add them to the live-query issued-ID resolver.

Clusters/sectors use native default global finds. Zones/gates require an exact sector macro. The observer enumerates the default-scope sectors and accepts that scope only when exactly one existing sector matches, with at most 4096 scope candidates. Ambiguous or missing scope is UNAVAILABLE, never an empty gate census.

## Counts, identity and timing

The native found-list count is separate from retained rows (maximum 512), omitted rows and unreadable/dead objects. Completeness means every object in that particular native query was retained and readable. It does not mean all possible topology classes, persistent topology, player-visible map coverage or an atomic snapshot.

The observer binds the canonical public session, UI generation, world token and MD revision. It samples MD player age before/after, rejects backward values, and keeps that domain separate from the UI clock used for deadlines. Contexts and IDs are read sequentially; identity stability is sampled.

One capture may be pending; reply queue capacity is 64, request history 256, timeout 10 UI seconds, page retention 300 UI seconds. New captures invalidate old pages. Scope overflow, malformed rows, duplicate object identities, partial transport and stale public identity never become a complete census. Unknown rows retain their denominator.

## Optional calling route

`scripts/live_topology_extension.py.prepare()` returns exactly:
- `md/X4ToolkitTopology.xml`
- `ui/live_topology_extension.lua`

Load the generated loader after the public automation bundle, before world bootstrap. The loader uses the fixed extension catalog and `ctx.create_md_channel`; no arbitrary cue name, expression, screen or blackboard is accepted.

Provider `x4-topology-observer-v1` has two SDK actions:
- `capture`: arguments `{kind, sector_macro}`, with kind clusters/sectors/zones/gates. Empty sector macro is required for clusters/sectors.
- `page`: arguments `{capture, offset}`, referring only to this provider's own dispatched capture request.

Prepare sends nothing. Execute sends a capture once. Status polls the retained observer state without resending. SDK completed means metadata or one page was captured; it does not establish a complete universe or gameplay acceptance. PARTIAL metadata can be successfully captured. Metadata carries its own enumeration_complete flag. Join all sequential pages with matching capture, offsets and total_retained before claiming transport completion. SDK history is shared and bounded at 256 requests: exhaustion is an explicit refusal, never eviction or automatic replay.

Payloads are JSON strings in `observations.payload_json` to preserve wire nesting limits. Provider pages adapt to the doubly encoded budget with reserved SDK overhead. A row too large for one response is refused; no silent truncation occurs. The standalone observer retains structured rows; the SDK page uses this fixed lossless tuple grammar:

`[exists, id_or_empty, macro, active_or_NOT_APPLICABLE, cluster, sector, zone, exit, exit_sector]`

Each context is false when absent, otherwise `[id, macro]`. A missing/dead object has exists=false, empty id/macro, NOT_APPLICABLE and all five contexts false. Boolean false active is distinct from NOT_APPLICABLE. Page `row_encoding` must equal TOPOLOGY_TUPLE_V1. Empty rows can encode as an empty JSON object due to Lua's empty-table representation; qualify them only with total_retained=0 and matching offsets/end_of_retained.

The host must preserve exact uint64 strings and source IDs, reject mixed captures, and retain unknown/partial and scope labels. No host wrapper label may promote these observations into a runtime or route PASS.

## Qualification boundary

The tests consume the actual Lua observer, SDK, generated loader and bounded wire encoder with synthetic MD/native transfer. They cover ownerless inactive exits, scopes, exact IDs above 2^53, duplicate/foreign rows, counts, caps, death, time and identity drift, delayed response, disposal, immutable requests and complete retained-page transport. No game was queried.

The private task receipt records exact source/test/compiler hashes and the canonical one-tree schema result. Shared bundle composition and runtime validation remain separate. No game data is distributed by this unit.

## Optional helper composition

`prepare_live_helper.build(..., extensions=('topology',))` now composes the fixed
factory through the supported catalog. It preserves the two-output inventory and
rechecks own generator/MD/observer/provider bytes. Canonical order is topology,
conversation, persistence regardless of caller tuple order. Unrequested loaders
remain absent; an already-composed loader refuses rather than being duplicated.
The manifest disclosure identifies the bounded non-atomic observational scope.

Composition, actual extension and provider controls passed56/56, zero skips,
resource run107951109cbc. Independent narrow review found no blocker. This is
offline generation/transport qualification, separate from actual installed load
order, native scope measurements and engine runtime qualification. The original
one-tree MD schema receipt and89 standalone/glue controls are retained separately.

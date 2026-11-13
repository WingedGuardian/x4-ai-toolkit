# Native owned-ship order adapter

Experimental infrastructure, not runtime-qualified. Loading the module performs
no game action. The factory verifies declarations, then exposes bounded observe,
prepare and execute operations. No transport, provider registration, deployment,
player autopilot, teleport or queue-wide clearing is included.

## Source contract

READ evidence from game-owned 9.0 files (not redistributed):

| File | SHA-256 |
|---|---|
| ui/addons/ego_detailmonitor/menu_map.lua | 6c56ba3881495ad8ee7db3a144c16079f2b2eaad56a3a1e5efe171f899fd6865 |
| ui/addons/ego_interactmenu/menu_interactmenu.lua | b5f89a2567fdace3989d1c4ce2d7429a606055fc68de7e5486294920145a74de |
| aiscripts/order.move.wait.xml | cca2f07c1c61157f1db11549ec01ebdbbc8f6fcf51971a7666ff0e8b82da008a |
| aiscripts/order.dock.wait.xml | 904c76dfdf50e7671d8332bd33ddf483f83fef2e27f3e097ce94f2f56956d1f7 |
| aiscripts/order.fight.attack.object.xml | 4c1e57ad6677a55ca7cc42987b6e86f5db3720167427f2e0d83bec632c6e3a36 |

- **Eligibility, 97% source confidence:** interact menu 575–584, 728–746 and
  831–849 checks IsOrderSelectableFor and assignedpilot before Attack,
  DockAndWait and MoveWait. Docking also uses IsDockingPossible at 2096–2103.
  Attack's visible condition at 1220–1221 excludes player-owned targets and
  requires destructible. Attack's script parameter requires defensible as well.
  The adapter additionally refuses non-owned/nonoperational subjects and both
  the player-controlled and player-occupied ship. It never substitutes an NPC
  order for player autopilot.
- **Creation and parameter shape, 95%:** map 4759–4763 creates Attack through
  CreateOrder3 and sets parameter 1 to the converted target. Interact 1077–1083
  creates MoveWait, sets parameter 1 to {sector,{x,y,z}}, then enables. Its
  immediate flags are supplied to the native default/override/temp arguments;
  this adapter fixes all three false, preserving a normal non-default queue
  operation. DockAndWait's native caller uses keyed destination; its order XML
  6–9 establishes destination as parameter 1. The low-level index-based docking
  composition is source-derived and still requires runtime qualification.
- **Observation, 98%:** map 213–246 declares Order/Order3; 11083–11101 fills
  Order3 buffers. GetOrderID is declared 742 and used 11456. Default order is
  separate (8658 onward); it has no invented AIOrderID here. GetOrderParams is
  read at 11376, and parameter values/types are used at 10143/29564 onward.
  Current queue index is queried only when the queue is nonempty, as in the
  native map. A native zero index is retained without invented semantics.
- **Position readback, 97%:** map 394–401 declares the six-float UIPosRot;
  26778–26787 converts GetOrderParams position values through that structure.
  The adapter verifies its layout and float precision, accepts explicit complete
  indexed or named xyz values, and refuses missing or ambiguous coordinates.
  Preparation reports both requested coordinates and their native float32
  normalization; native readback is compared in that same representation.
- **Arrival, not qualified:** map 8690–8695 shows a wait icon when
  issyncpointreached is true for move/dock orders. That raw boolean is returned,
  never converted into an arrival PASS. Docking needs actual container identity/
  event evidence; movement needs the intended position/sector readback. Attack
  submission proves neither a hit nor damage attribution.

These percentages qualify source interpretation, not engine behavior.

## API and exact scope

The returned factory accepts ffi, C, globals, resolve_issued, epoch and now.
Globals are the actual native IsValidComponent, GetComponentData,
ConvertStringToLuaID, ConvertIDTo64Bit, SetOrderParam, GetOrderParams and
IsDockingPossible. resolve_issued must return a current exact uint64 component
for the requested ship/sector/object token. Epoch must identify the actual
world/UI generation; now is a finite nonnegative monotonic clock.

The constructor validates UniverseID, AIOrderID and size_t as unsigned 64-bit,
then verifies native Order and Order3 sizes, alignment, offsets and field types
against anonymous proof layouts before native allocation. Only native named
structs reach C. This currently qualifies the 64-bit ABI, not a 32-bit port.

- observe(ship_token): enumerates all queued rows, including disabled entries,
  plus the default order, with count/fill/count and identity rereads. It returns
  exact decimal AIOrderIDs, queue indices, definitions, states, enabled/temporary/
  override/priority flags and raw syncpoint booleans. Parameters are explicitly
  not a complete queue-wide parameter census. An invalid or overflowed read
  refuses; an empty queue is not inferred from a failed call.
- prepare(ship_token, action, target_token, policy, position): supports move,
  dock and attack. Policy must explicitly be require_empty. Any queued entry,
  including disabled or temporary, refuses. Default must be absent or Wait.
  Move takes exactly three finite coordinates in an issued sector, bounded to
  +/-10,000,000 meters by this instrument. Dock/attack take an issued object.
  Preparation records immutable evidence without native writes.
- execute(ticket): one use, consumed before CreateOrder3. Rechecks current
  identities, epoch, pilot, ownership, occupancy, eligibility and queue/default.
  Refuses after 30 seconds or if any recorded precondition changed. It binds the
  returned index to an exact AIOrderID and the sole disabled requested order,
  sets parameter 1, verifies actual GetOrderParams readback, enables, then reads
  back the same order and queue again. No arbitrary parameter/order name passes
  through the API.

NOT_CREATED means the native creator returned zero. PARTIAL_CREATED_NOT_ENABLED
means an actual created order remains without confirmed enablement. Exceptions
after an attempted native write are UNCERTAIN and retain any known index/ID.
ORDER_OBSERVED means the requested order was read back enabled; it does not mean
arrived, docked, finished, or successful combat. No outcome authorizes a retry.
No automatic cleanup removes a partially created order.

The caller must preserve durable request journals and correlate later events.
An AIOrderID is stable identity evidence; a queue index can change and must never
be used by itself for future cancellation. Raw observations may exceed the
action-wire observation budget; integration must use scoped summaries or bounded
pages, never truncate silently.

## Limits and remaining runtime qualification

Limits: 128 tickets, 64 queued orders, 128 parameter descriptors, 2048 bytes per
native text, exact unsigned IDs and finite bounded coordinates. Native reads are
non-atomic despite count and identity checks. Default-order state is compared
conservatively; engine-driven default-state transitions may cause a partial
refusal that requires attribution, not a guessed relaxation.

Native MoveWait and DockAndWait can remain queued waiting after arrival. This
unit deliberately cannot silently replace them. Chaining acceptance cases uses
the explicit exact-order cancellation below after arrival has been independently
qualified. RemoveAllOrders is not an acceptable fallback.

Runtime qualification must establish: no-pilot refusal; player occupancy refusal;
unchanged preexisting queue refusal; valid native control move/dock/attack
submission and readback; dock target refusal; state changes between prepare and
execute; and lost-reply reconciliation without a second order. Actual motion,
travel state, dock arrival, attacks and collateral consequences require separate
observations. Direct order execution does not prove the interact-menu route.

## Offline verification

Tests execute actual LuaJIT and native FFI buffers using synthetic functions.
They cover all three parameter paths, IDs above 2^53, active/disabled queue rows,
eligibility and queue refusal, stale identity/pilot/epoch, partial creation/
parameter/enable failures, no retry, reentrancy, immutable preparation evidence
and native ABI refusal. Children retain timeout/assertion/exit checks without
disabling global crash diagnostics. No game is contacted.

Qualified 56 of 56 targeted controls, zero skips, using actual LuaJIT on the
isolated implementation branch. Resource-budget admission was GO; child exited
0 in 33.88 seconds with 50 MB peak job memory. The wrapper's implausible CPU
peak was not used as compliance evidence. These are synthetic native controls,
not game runtime results. No deployment or live order was performed.

## Exact cancellation of an adapter-created order

READ source: menu_map.lua5507-5511 checks ownership and
RemoveOrder(ship,index,false,true) before calling the same function with
checkonly=false. The interact menu's explicit cancel button follows the same
eligibility/dispatch separation at6963 and2767-2772. Source confidence97%;
actual engine cancellation and immediate readback remain runtime-unqualified.

- prepare_cancel(submission_ticket, expected_order_id) requires the exact decimal
  AIOrderID recorded by a prior successful creation-binding check in this same
  adapter instance. An arbitrary observed order, a guessed ID, a new adapter after
  reload, or a creation that never established its sole-order identity refuses.
  Bound partial/disabled creations remain eligible for explicit cleanup.
- The ship must currently be owned, operational, piloted and neither controlled
  nor occupied by the player. The adapter finds the target by AIOrderID in the
  current queue, records its current index and complete observed queue/default,
  and uses native checkonly cancellation eligibility without removing anything.
- execute_cancel(cancel_ticket) consumes its ticket, rechecks the exact queue,
  pilot, lifecycle and target, then reserves cancellation on the original
  submission before the one native removal call. Other tickets and exceptions
  cannot repeat that submission's cancellation.
- ORDER_REMOVAL_OBSERVED requires a true native result, the exact target absent,
  count decreased by one, and every other observed row and default unchanged.
  Relative order is preserved; only queue-index shifts caused by removal are
  normalized. Parameters of other orders are not enumerated, so the preservation
  claim is explicitly limited to the observed rows/default.
- NOT_REMOVED means native false with the exact unchanged queue still present.
  Contradictory native results, loss/change of another order, changed default,
  exceptions or context changes remain UNCERTAIN. None authorizes automatic retry,
  queue-wide removal or corrective writes.

The source submission journal is in this adapter instance. It is not restored
from arbitrary host data after a UI reload. Root integration must keep its durable
request journal and session binding separately; unavailable provenance refuses.
The capability does not certify arrival before cancellation: the campaign must
first qualify its move/dock observation, then explicitly request this cleanup.
There is no RemoveAllOrders fallback.

The successor passed92/92 targeted LuaJIT controls, zero skips: the original56
plus36 cancellation controls. Coverage includes exact/foreign/missing/duplicate
IDs, other orders before/after the target, stale pilot/ownership/occupancy/epoch,
new queued work after preparation, native check refusal, uncertain/false removal,
foreign-order loss, reentrant/second-ticket attempts and bound partial creations.
Owner execution took18.38s,52MB under resource admission; no game call occurred.

## Exact subject docking observation

The additive read-only `observe(ship_token).docking` samples the exact resolved
ship's native `isdocked` property twice. When true, it reads and repeats
`GetContextByClass(ship, "container", false)`, retaining the exact unsigned64-bit
container identity. The native analogue is MapMenu:9611–9619. The production
GetComponentData wrapper must retain its established ID conversion/roundtrip;
no component is selected by macro, name, recorder-group membership or order type.

`docking.subject` equals the outer `ship` ID. A consumer must join that exact ID
to the prepared/submitted subject and, when checking the requested destination,
compare `docking.container` with the exact prepared target in the same bound
session. A different ship of the same macro cannot supply this observation.
The containing-object sample is not a DOCKED event and does not prove this order
caused docking; `order_caused_docking_verified` remains false, as does the outer
`arrival_verified`.

A missing/invalid/throwing boolean, missing or changing dock container, or
inconsistent repeated docking state produces `docking.status="UNKNOWN"`, with no
default `isdocked=false`. An observed undocked ship has
`container_scope="NOT_QUERIED_UNDOCKED"`; its false container placeholder is not a
native absence census. World/issued-subject drift still refuses the entire
observation. Queue evidence can remain OBSERVED while docking is UNKNOWN.
Every read is sampled/non-atomic and performs no game mutation.

The final successor passed106/106 LuaJIT controls with zero skips
(resource job f2c7e635b632,40.06s,61MB,child exit0). The14 additional controls
include exact IDs above2^53, a different same-macro ship docking, unknown/native
failure/changed-container cases and whole-observation identity refusal. Existing
92 order/cancellation controls also passed. Native runtime remains unqualified.

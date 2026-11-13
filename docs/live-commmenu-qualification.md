# Native rendered communication-opening adapter

Current V4 adds fixed map information navigation and explicit left/right actor
pane selection. The V3 results and limitations below remain historical. See
live-mapnavigation-qualification.md for current scope and the 121 passing
V4 provider/loader controls. The comm widget itself and MD XML are unchanged; radial
selection and runtime qualification remain outstanding.

Offline candidate, no runtime/deployment or game actions performed. This adapter
uses the actual built MapMenu communication button wrapper; it does not call a
raw conversation or purchase API and cannot select a radial dialogue option.

READ native map source:26123–26143 reads contextMenuData.component/entity/person
and obtains an instantiated person when applicable;26232–26237 selects a concrete
entity or native context/person tuple to avoid remote-entity despawn;26242–26244
creates rowdata info_actor_comm and its onClick closure. Row presence/active state
incorporates arrival, transfer and information-unlocked conditions. Native
helper 4894–4901 preserves rowdata, and map 6894/6938–6940 builds the context frame
on layer 2 in info_context mode. Source hashes match live-dialogprobe-qualification.md.

The adapter observes the built frame/row/widget and native enabled state. Prepare
requires an issued context object plus the exact observed typed ENTITY or PERSON
token. Native person tokens are exact identities from the UI model, not component
IDs and not MD template seeds. They are never passed to a component converter.
The current native widget callback retains the game's entity/template choice.

Execute consumes a private ticket, revalidates the same public stamp, registered
frame, context values, widget, handler and actual buttonScriptMap wrapper, and
invokes that wrapper once. Dispatcher return is DISPATCHED_UNVERIFIED. Callback
error or post-dispatch identity/clock failure retains action_attempted=true and
UNCERTAIN. No successful dialogue opening, actor, rendered pixels, selection or
purchase is inferred. Later native MD actor/history observation remains separate.

Actor model/closure binding is source-supported, but private closure upvalues are
not inspected or claimed as direct native identity attestation. Source-reviewed
native menu construction plus exact current widget/model binding is the supported
route. It requires an already-open real actor context; navigation to that context
is a separate capability.

The pure conversation extension generator attaches this adapter through the
source-bound `x4-comm-menu-v1` provider. `inspect` has empty arguments; `open`
requires the exact observed `context`, `kind` and `actor` strings. Both require
binding equal to the generated provider revision. Prepare never invokes the
wrapper. Execute consumes its private request before dispatch; status never
repeats dispatch. Inspection completion means only a built widget was observed.
Opening stays running/unverified; it cannot complete a conversation or purchase
case. Missing attempt evidence remains UNKNOWN, including exceptions after
adapter dispatch. Cleanup is immediate, idempotent and failure-sticky.

MEASURED:127/127 combined controls passed, zero skips, pytest 0.58s, resource job
2185524bf045 child 0:43 conversation observer,9 MD structure,15 snapshot provider,
4 generated loader,45 comm adapter and 11 comm provider controls. The actual
generated loader uses the actual SDK/providers and a synthetic native menu model;
it inspects the bound widget and invokes its exact wrapper once. These are offline
behavioral controls, not engine/UI qualification. Caught LuaJIT SEH diagnostics
remained visible despite the successful test exit. The unchanged one-tree MD
schema proof is reused by exact bytes; no schema rerun is claimed for Lua changes.
Independent READ review found no concrete source blocker. Final source receipt
separately records the subsequent metadata-only qualification-label correction.
That final four-control loader check was NOT_LAUNCHED by the resource wrapper
(exit 3: RAM/commit admission limits). No retry is queued. The 127-control result
describes the preceding generator metadata; the current exact source and newly
derived loader revision still await those four controls. No current full-pass
or deployment readiness is claimed.

The subsequent V3 source integrates actor-context navigation through two fixed
additional actions, documented in live-actorcontext-qualification.md. Its controls
were initially resource-held; current V3 now passes 175/175 controls, zero skips,
resource 81446daefd70 child 0, pytest 0.75s. The current extension no longer
claims actor-context navigation is wholly unimplemented: it supports only the
built right information-row route, with closed-map navigation still absent.
The earlier held attempt and 127-control receipt remain history. V3 source and
generated hashes are bound by the separate navigation qualification receipt.

# Read-only conversation identity observer proposal

Offline source proposal; not deployed, not runtime-qualified. The pure Python
factory emits one new MD path in memory. The extension factory supplies a real
SDK calling route once its loader is included in a reviewed helper composition.
The rendered selection route remains BLOCKED.

The fixed begin(request32hex) operation sends only observational snapshot
bookkeeping to the installed MD observer; poll(request) never repeats it. This
is not a gameplay action or a general MD execution interface. The installed SDK
provider exposes one fixed snapshot action; execute calls begin once, and status
only polls. Receipt paging must not reissue execute. One request is outstanding, at most256 request IDs
per adapter lifetime, native reply queue64, deadline10 UI seconds. Public session
or UI-generation change retires the adapter. Late timed-out/superseded responses
cannot acquire authority; duplicate completed responses fault it.

READ analogue: native md/conversations.xml6–31 and307–318 uses instantiated,
namespaced conversation events with component versus context/template actor
branches;1125–1132 handles conversation finish. Event fields are documented in
common.xsd12602–12720. scriptproperties2440–2442 exposes current conversation ID,
active flag and actor; the active flag can include non-interactive conversation.
npctemplateentry.seed at2046 is a persistent identifying largeint; it is formatted
in MD, never rounded through a Lua/Python float. Native source hashes are in
live-dialogprobe-qualification.md. Queue wiring follows the source-reviewed
public live_session_md.py and fixed live_mdchannel.lua, not a new host verb.

The observer retains one observed conversation instance, bounded event sequence,
actor, section event and event age. Every observed start advances the instance;
no inferred stack reconstructs nested conversations. Any finish, load, start or
session invalidation clears section qualification. Attaching after a conversation
started cannot reconstruct its section. A next/returned-section event qualifies
history only after a known start with the same conversation and actor. Current
player properties and public stamp are read before/after snapshot assembly.

ACTOR_AND_HISTORICAL_SECTION_OBSERVED is historical event evidence under that
observed instance, not authoritative rendered-section visibility. Actor identity
is either a concrete component or a context component plus exact template seed.
UNKNOWN is preserved when it cannot be read. The adapter never passes an arbitrary
MD table to a native component converter. MD's current actor property is documented
as entity: a template-event/current-entity mismatch stays unqualified rather than
inventing equivalence. Missing/changed actor, session, producer, chronology or
fields cannot become a conversation acceptance result. All outputs explicitly
retain rendered_route=BLOCKED and runtime_verified=false.

Confidence: source wiring is based on actual native handlers; the runtime ordering
of event callbacks, player-property updates and template representation is still
unqualified. Engine qualification must first prove LOAD and snapshot TRIGGER,
then bind a real active actor and event history. No dialogue option, purchase,
menu transition, spawn, save, profile or game launch is authorized by this unit.

MEASURED: 51/51 controls passed (42 actual-Lua controls and9 structural controls),
zero skips, pytest0.21s, resource job f9e7fb9ebc8c child0. Caught LuaJIT SEH
diagnostics remained visible. The canonical validator compiled md.xsd in174.9s
and checked the one emitted tree: zero findings, zero skips, resource job
fb33537753d4 child0. Candidate SHA256:
2256d7c25a921f511c5dbb7ae6063daadb117558170ce3b3889d60e9d7e61ccc.
Independent READ review found no concrete source blocker. Schema/runtime are
separate: no event timing, actual blackboard representation or UI route is
engine-qualified by these controls. No passing result is inherited from the
earlier dialogue model probe.

Additional conservative boundary: common.xsd7890 documents that customhandler
can suppress the generic conversation-start handler. Event coverage is therefore
not claimed universally; an unobserved start leaves historical section UNKNOWN.
Native userdata/component representations outside this adapter's supported
scalar conversion boundary remain unqualified rather than guessed. The packet
is source-ready for integration and a later explicitly approved runtime test.

## Calling-route successor

`scripts/live_conversation_extension.py` produces the unchanged MD candidate and
an own-code `ui/live_conversation_extension.lua`. Its loader registers exactly
`x4-conversation-observer-v1` in the frozen extension catalog, using the existing
`ctx.create_md_channel` boundary and immediate disposal ownership. Load it after
the public automation bundle and before first world construction. The generator
binds exact observer/provider/generator sources and MD revision, and rechecks
them after construction. It writes nothing by itself. Final helper composition
must include both generated paths and the loader entry; no main or bundle file
is changed by this unit.

The one SDK action is `snapshot`, empty arguments, binding equal to the generated
provider revision. Prepare sends nothing. Execute consumes its private request
before one observational send; status reconciles without retry. A completed
receipt means `CONVERSATION_SNAPSHOT_ONLY`: it can describe an inactive state or
an actor with an unknown section and does not accept a conversation/UI test.
Observation transport failure remains unknown. The observer's retirement is now
idempotent and failure-sticky, so layered ownership cannot turn a failed cleanup
into successful replacement.

MEASURED successor:70/70 controls passed (43 observer,9 MD structure,15 real-SDK
provider,3 loader/source-binding controls), zero skips, pytest0.37s, resource
94f6a66c6cb0 child0. The actual generated loader registers the actual provider and
observer against the actual SDK, sends zero snapshots during construction or
prepare, and one on execute. First run69 passed/1 failed because a test expected
a returned refusal for an unknown action; the SDK correctly rejected that action
before provider dispatch. The test now asserts that exact exception and zero
sends. No implementation behavior was relaxed. Caught LuaJIT diagnostics remain
disclosed. Independent source review is clear.

The prior one-tree compiled proof is reused only for byte-identical MD SHA2256d7c2;
the successor Lua cleanup change is qualified by the new controls, not the old
51-control receipt. The original schema receipt and source commit remain history.

## Built communication-widget successor

The current extension generator also registers `x4-comm-menu-v1` at the same
source-derived revision. Its `inspect` and `open` actions use the actual built
MapMenu communication-button wrapper. See live-commmenu-qualification.md for
the exact context/actor binding, tests and unverified dispatch scope. The prior
single-provider receipt remains historical. The current pure generator still
returns two paths: the byte-identical observer MD and the expanded loader.
At that V2 checkpoint, actor-context navigation was unimplemented; radial
selection and rendered runtime remained unqualified. No generic gameplay
execution interface or direct purchase call was introduced.

V3 source now includes the fixed built-right-information-row context route
described in live-actorcontext-qualification.md. Current V3 passes175/175
resource-admitted controls (zero skips,81446daefd70 child0). Closed-map navigation and radial
selection are still absent; dispatch and independently observed outcomes remain
separate. The old51/70/127-control evidence is historical. V3's separate receipt
binds15 source inputs and rechecks52 unchanged schema/source pins, retaining the
two previously documented nonschema cleanup changes. The MD bytes remain2256d7c2:
one prior compile reused, zero new compiles. No native runtime acceptance follows.

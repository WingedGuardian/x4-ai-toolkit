# Current-room actor entry

Offline own-code adapter and generated trader/text SDK integration. No game
execution or entry-chain qualification yet.

`live_actorentry.lua` observes the bounded native prioritized NPC list for the
current player room, returning up to eight opaque actor keys. This is not an
all-NPC census; other-room entries are counted and excluded. Pages are independent
observations. It deliberately does not apply TransporterMenu's crowd filter,
which would hide non-mission crowd actors such as recruitable marines.

Three explicitly selected, consumed-before-call primitives use those keys:
`focus` calls native SetSofttarget; `comm` performs only an active, displayed,
unique native component-slot action of type comm; `interact` performs instant
root action 1 only when CORE GetActionInfo and PerformAction are available and
the current message offers it. Missing CORE functions refuse. There is no fallback,
arbitrary action ID, conversation section, actor name matching, or economic API.

READ native analogues: TransporterMenu813–844 list and room-membership handling;
MapMenu5560–5567 SetSofttarget; InteractMenu116–127 ABI,6656–6788 action enumeration
and2646–2649 exact slot/action dispatch; targetsystem1441/3660/4357–4377 instant
action eligibility and dispatch. The asynchronous EnterInteractMenu path is not
used: targetsystem1141 explicitly describes its target-change race.

Copied mechanism confidence: 95%, assuming the pinned native ABI and truthful
current public identity. Runtime API availability, actor inclusion, rendering,
and the resulting conversation remain unqualified. Native action-model eligibility
is not proof of a rendered interaction menu. An independent conversation snapshot
must match the intended actor and root, then actual offered choices and built
trader/recruitment controls must be observed before any case outcome is assessed.

Both ticket and observation counts retain canonical native_history limits
(interactive128/campaign512). Failed preparations consume capacity, records are
never evicted, and stale/replayed tickets cannot dispatch. Post-binding identity or
clock loss is sticky; uncertain native execution retires the adapter. Constructor
performs no native reads or effects. The existing trader/text descriptor adds
`actor.observe` with a bounded start offset and `actor.focus`, `actor.comm`,
`actor.interact` with an opaque actor token. Existing `capacity_observe` includes
actual actor ticket/observation counts. All previous actions remain available.
Its existing immutable byte pages transport observations; no new provider, MD,
native assembly module or channel is introduced. The generated loader pins the
new source and registers its disposer immediately after construction.

The prioritized list's original native values and labels are not caller identity
authority. Native conversion is roundtrip checked, metadata is rechecked, and
each mutation revalidates membership, current message/connection, action model,
public identity and five-second ticket lifetime. Pages adapt to the actual codec
budget and report their returned/native/matched denominators separately.

Source transfer does not establish that NPCs on every platform expose comm or
that CORE functions are visible to the menu environment. Those branches retain
explicit unavailable results; no missing representative is spawned. No success
claim may precede independent actor/section and actual offered-menu readback.

Offline qualification: 172 controls passed, zero skips, across the actor adapter,
actual generated trader/text extension with SDK/codec, helper composition and
all-nine extension constructors. This includes malformed ABI, exact uint64,
dense action lists, adaptive page bounds, missing CORE functions, stale actors,
callback-driven target/clock drift, consumed tickets and retained capacity.
All native behavior in these tests is synthetic; no engine effect was invoked.

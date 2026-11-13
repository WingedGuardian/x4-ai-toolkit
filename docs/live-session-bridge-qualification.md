# Public Lua session bridge

`live_session.lua` returns a factory `new(env)`. It is wired into the isolated
bootstrap/bundle candidate, not a deployed provider or native save/load qualification. It depends on
the public `X4ToolkitSession` MD proposal and has no private ROR dependency.

Initial unit, MEASURED 2026-11-09: 50 actual-Lua behavioral controls passed, zero skips,
`pytest -q -ra -p no:cacheprovider tests/test_livesession_lua.py`, resource ledger
`fadb8b200118`, exit 0. They execute this Lua source through Lupa with deterministic
native-wrapper stubs. They qualify correlation and refusal behavior, not engine
event delivery, blackboard semantics, UI reload or real save-load completion.
Independent review then found and corrected acceptance of matching empty world
tokens in READY/current state. The final combined host/actual-Lua run passed
102 controls with one host-only Windows symlink privilege skip, resource ledger
`d02ddf48ec01`, exit 0 (51 host passes, 51 Lua passes). No game calls were made.

The caller supplies the exact reviewed revision and six fixed native wrappers:
`send(request)` sends only the fixed `X4ToolkitSession` / `handshake` event;
`subscribe(on_reply,on_invalidated)` subscribes only its two fixed events;
`read()` copies `$x4_toolkit_session_replies` from the current player entity;
`clear()` clears that copied native queue using the established nil-blackboard
pattern; `read_state()` reads `$x4_toolkit_session_state`; `now()` supplies a
finite nondecreasing local timeout clock. MD simulation age is not that clock.
Native transport analogues are ModSupportAPI simple_menu/interface.lua121–151
and md/simple_menu_api.xml455–466. Root integration owns the wrappers and loader.

`connect(request, callback)` accepts exactly mode/session/challenge/generation,
using the host module's strict probe/fresh/resume grammar. It returns pending,
not readiness. Callback receives either an exact source response or an explicit
UNAVAILABLE reason. The first handshake binds this Lua instance's UI generation.
New generation means disposal/recreation, not silent in-place rekeying.

`current()` reads the native current-state table on **every** call. It returns
the five-field provider/revision/session/generation/world_token stamp only when
it exactly matches an accepted READY and no handshake is pending. Missing or
changed state invalidates readiness even if no load event has arrived. Age in
the six-field MD state is checked for finite validity, then omitted from identity:
the MD currently updates it on handshakes/invalidation, so it is not a fresh
simulation-clock measurement for every query. World-token presence is not proof
of persistent save lineage. All bound queries/actions must call `current()`.
Present-session state and replies require a nonempty world token because the
producer initializes it before READY; this shape check is not a lineage claim.
The diagnostic `status()` also uses that readback; it never reports cached READY
as current readiness.

One active module owner controls the bounded queue. `dispose()` retires its
callbacks before replacement; retired/faulted callbacks never read or clear a new
owner's queue. A whole batch is validated before any callback, so duplicate
matching rows cannot briefly grant readiness. Late expired responses never
restore readiness. Single pending handshake, 64 queued rows, and 2048 unique
challenges per instance are explicit bounds. Tick after ten local seconds
reports timeout without fresh fallback or resending. A lost fresh response can
be recovered only through a new host probe and explicit resume of its durable
nonce. Load invalidation makes this instance unavailable; the host must allocate
a new nonce after the public MD proves absence.

No source attestation, permission, authentication, actor availability or native
effect completion follows from a READY response. The wrapper must still bind the
expected helper/source/profile and actual connected game process. Native event
ordering and blackboard transfer need the approved disposable runtime campaign.
No game query/action, XML, profile, save, loader or installed file is changed by
this unit's behavioral tests.

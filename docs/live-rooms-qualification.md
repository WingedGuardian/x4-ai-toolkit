# Native transporter room adapter

Experimental standalone factory: `mods/x4_toolkit_helper/ui/live_rooms.lua`.
No registration, routing, game launch, menu opening, or movement occurs on import/construction.
Runtime qualification is pending.

## Interface and evidence

The factory receives `ffi`, `C`, `current()`, `now()`, `encode(value)`,
`menus()`, `convert_id(value)`, and `component_data(room, key)`.
`current()` must return the canonical public five-field session stamp.
`menus()` must return the actual registered native menu list.
`convert_id` must preserve the exact uint64 identity through the existing native converter.
For the only property read, `component_data(room, "islocked")` must use the native
`GetComponentData(ConvertStringToLuaID(tostring(room)), "islocked")` call shape.
It must not pass raw uint64 cdata to a Lua-ID API or replace an unreadable lock value with false.

- `location()`: two equal native player/container/occupied-ship/parent-room observations, bracketed by the public identity. Room zero is UNKNOWN, not an empty or nonexistent room.
- `targets()`: the actual engine-opened TransporterMenu source component/connection, source room, two equal native target lists and counts, current player location, and an opaque local capture token. No target IDs are automatically issued to other APIs.
- `prepare(capture, index)`: selects only a captured native target. Requires the same menu/source/location/targets/session, capture age at most 30 UI seconds, a nonzero known room, explicit unlocked state, and a different non-zone target. Returns an opaque local ticket or a refusal reason; no native mutation.
- `execute(ticket)`: consumes the ticket before revalidation. Requires the prepared context and a lifetime at most 15 UI seconds, including final location/session/time checks after identity conversion. Calls exactly `TransportPlayerToTarget` once. This is the native transporter mechanism, **not** a rendered button invocation or proof of ordinary walking/docking.
- `status(ticket)`: historical request result and current local unresolved flag. No fresh arrival measurement.
- `dispose()`: permanently retires this instance.

The native action returns void. A return produces TRANSPORT_REQUESTED, never arrival success.
A thrown native call produces UNKNOWN and cannot be replayed. The native room-context read can
later supply an independent location observation, but the adapter does not infer causality.

An attempted transport retains an interlock even after an exception. Other prepared tickets
and new preparations cannot dispatch while it remains unresolved. There is no time-based or
caller-asserted unlock. A later `targets()` must observe the exact requested successor
source component/connection, matching nonzero source room and player parent-room, under the
same canonical session. Native targets may omit the current slot, so this check queries
`GetRoomForTransporter` on the engine-supplied source slot separately; it does not invent an
extra enumerated target. Retiring the adapter does not cancel or prove the native effect.

## Source basis

READ local native sources (not redistributed):

| Source | Relevant evidence |
| --- | --- |
| `ui/addons/ego_detailmonitor/menu_transporter.lua:12–35` | UIComponentSlot and exact count/fill/room/native transport declarations |
| `menu_transporter.lua:90–104` | Current target, lock, and separate zone/spacesuit confirmation guards; native transport action |
| `menu_transporter.lua:128–165` | Source slot comes from engine menu parameters 3/4; count/fill; current slot may be absent from native targets |
| `menu_transporter.lua:93` | Room property uses native Lua-ID conversion |
| `menu_transporter.lua:832` | Parent-room GetContextByClass query for an NPC |
| `ui/addons/ego_interactmenu/menu_interactmenu.lua:238–240,2189,2219` | Player/container/occupied-ship declarations and GetContextByClass(GetPlayerID(), "sector", false) |

Source SHA256: transporter
`5f4c2791efe461d6bd2333ce9449b3ee41f6939c50e38f2e0f555d78d7e6e178`;
interactmenu
`b5f89a2567fdace3989d1c4ce2d7429a606055fc68de7e5486294920145a74de`.

The player **room** query is a transfer of the native player-context and NPC-room call shapes,
not a copied player-room caller. Source-transfer confidence: 92%; runtime remains untested.
Target enumeration and native transporter call-shape confidence: 95%, also runtime untested.

## Bounds and remaining gates

Limits: 128 native targets; 256 bytes per native string; 30,000 encoded bytes per target
observation; 128 retained capture IDs and 128 prepared tickets. Oversized/unreadable/changed
populations refuse without returning a partial complete census. The actual injected encoder
can impose stricter limits. Observations are sampled and non-atomic.

The source slot is never guessed. Automatic menu opening is **not implemented**: the named
native Lua paths reviewed here provide engine-fed parameters, not a supported getter/opener
for the current transporter connection. A caller cannot supply an arbitrary source slot to
bypass this prerequisite. The adapter remains unavailable until the actual menu exists.

This unit therefore supports a bounded transporter mechanism once that prerequisite is met;
it does not close the entire automated room-entry acceptance case. It does not prove rendered
menu-row coverage, accessibility by walking, docking, a safe route to the transporter, ordinary
travel, room asset correctness, or any campaign gameplay result.

The LuaJIT controls use synthetic native/menu state and actual module/codec execution.
They cover exact uint64/ABI, count/fill/row drift, zero versus unreadable, captured-target
mutation, lock/zone/current-room refusals, public/clock/location drift, ticket consumption,
native exceptions, bounded retention, and unresolved successor-slot gates. They are not an
engine or UI test.

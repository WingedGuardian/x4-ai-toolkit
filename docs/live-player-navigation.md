# Player navigation primitives

This source-backed adapter controls the exact currently occupied and controlled
player ship. It exposes distinct guidance, soft-target, autopilot-start,
dock-request and native QuickDock primitives. It never treats dispatch, native
acceptance or an active autopilot as arrival or successful landing.

## Native provenance

READ X4 9.0 sources:
- menu_map.lua:29539-29554 supplies SetGuidance(object, zero UIPosRot);
  5361-5362 calls ToggleAutoPilot(false).
- menu_docked.lua:1230-1238 selects the QuickDock container from the soft target,
  otherwise the environment object, and uses RequestDockAt(target,true) as a
  check. Lines1503-1512 call the autopilot and QuickDock natives.
- menu_interactmenu.lua:2133-2141 issues RequestDockAt(container,false);
  5430-5459 supplies IsDockingPossible and check-only RequestDockAt.
- menu_docked.lua:286-295 reads occupied ship, isdocked and native parent container.
- menu_map.lua:6734 reads GetAutoPilotTarget;29546 uses
  GetActiveGuidanceMissionComponent. Core crosshair handling.lua:119,485-491
  declares and reads IsAutoPilotActive.
- UIPosRot's six float fields are in menu_map.lua:394-401;
  SofttargetDetails2 is in menu_docked.lua:39-43.

This is the native-mechanism route. It does not establish that the displayed
InteractMenu row was offered: that menu has additional deployable/unit conditions
and visual bindings which require their own UI test. No Egosoft source is shipped.

## Interface and sequencing

The public native provider exposes five closed actions, each with exactly
`ship` and `target` issued-token arguments:
`navigation.guidance`, `navigation.target`, `navigation.autopilot_start`,
`navigation.dock_request`, `navigation.quick_dock`.
The fixed `player_navigation` read takes those same two tokens.

A campaign can request guidance, independently read it back, then request
autopilot start. This is not automatic end-to-end travel. The campaign observes
the actual approach and must qualify the later docking phase. QuickDock requires
the exact requested container to be the native soft-target/environment-derived
dock target; it cannot be used to toggle an unrelated docking action.

Each action uses a private, bounded preparation ticket, re-reads every captured
precondition and consumes the ticket before a native call. Preparation expires
after five UI-clock seconds. Duplicate execution never repeats the call.
The provider wraps these tickets in the durable action protocol. Status only
observes; it never retries or claims completion. There is no automatic stop,
undock, restoration, new game, teleport or cleanup.

The adapter requires exact current controlled AND occupied ship equality, live
issued subjects and an actual ship/station destination. Existing docking,
docked or active-autopilot states refuse action preparation, avoiding toggles
of already active behavior. RequestDockAt(true) and ToggleAutoPilot(true) are
native check-only calls. QuickDock and dock requests also require
IsDockingPossible and native docking eligibility.

Snapshot fields include exact ship/target/container, docked/docking state,
guidance, autopilot target/active state and native QuickDock target. A nil
guidance/target global stays UNKNOWN, not measured zero.
`docked_at_requested_container` records the current docked flag plus native
container equality; it does not prove this action caused arrival. Both
`atomic` and `arrival_verified` remain false.

Raw uint64 identities remain exact; the assembly converts IDs for bare engine
globals through the existing checked native wrappers. Struct layout/type probes
check declaration conflicts, not the engine's parameter ABI. A native false
return is NATIVE_REFUSED; an exception, unknown boolean or post-action identity/
clock failure is UNCERTAIN with action_attempted preserved.

## Qualification

MEASURED offline:46 standalone controls passed (2cd418030af0).
The final combined module/provider/assembly/bundle/composition group passed306
with zero skips (0dc841115722). This includes each real primitive through the
actual SDK registry, unchanged once-only semantics, native-ID wrapper reads,
current-ship mismatch, wrong docking target, partial/unknown state, expired
preparations and post-write uncertainty.

The preceding combined run had304 passes and two failures because two older
synthetic fixtures omitted newly required dependencies. Those fixtures were
updated and the same group rerun; the failed run is not accepted evidence.

Independent native-source review found no blocker in these primitive shapes.
Runtime native ABI, guidance/autopilot behavior, QuickDock behavior, actual
arrival and causal task/event joins remain unqualified. No game operation was
performed while developing or testing this module.


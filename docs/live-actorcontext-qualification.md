# Native actor-context navigation proposal

Current V4 source supersedes the right-only limitation described in the historical
V3 record below. Explicit left/right panes and visible panel mode are supported;
the default remains right for existing callers. See live-mapnavigation-qualification.md
for the 92 passing module controls and the later 121 passing V4 provider/loader
qualification. No runtime capability is inferred from this source update.

Offline source and behavioral qualification; native runtime remains unqualified.
No native game action, UI input, deployment or profile/save change performed.

READ: native MapMenu actor rows carry exact role/entity-or-person/context values
(menu_map.lua15300/15373). The actual right-click handler 28477–28643 obtains the
same rowDataMap entry and calls prepareInfoContext 28648–28691. That native method
checks actual assigned crew/pilot/manager/shiptrader identity, controllable class
and communication eligibility before creating the context. Right info frame is
layer 5 (1053,7052,19624–19679). Source hash is recorded in the dialogue probe doc.

The adapter accepts only an already-visible, stable right object-information
frame, with no modal, panel, multiverse or special map mode. It binds the actual
row object, rowDataMap, registered frame/table, issued context and typed actor;
it invokes the existing onTableRightMouseClick method once. No row is fabricated,
no native eligibility logic is replaced, and no direct conversation is started.
Native mouse coordinates must be available because prepareInfoContext reads them
without its caller's gamepad fallback. The adapter refuses missing coordinates;
it does not inject input or set mouse position.

Dispatch remains DISPATCHED_UNVERIFIED. Separate comm-widget inspection must
prove the resulting context; independent MD conversation observation comes after
the later communication click. Exceptions after dispatch preserve attempted=true.
Exact native person tokens are not component IDs or MD template seeds.

The V3 conversation extension source integrates this through the existing
`x4-comm-menu-v1` provider's fixed `context_inspect` and `context_open` actions.
Inspection takes empty arguments; opening takes `context`, `role`, `actor` from
the observed real row. Binding is the generated provider revision. Preparation
never invokes the handler, execute consumes its private ticket, and status never
repeats dispatch. `context_handler_attempted` is separate from the later comm
button's `wrapper_attempted`; context creation and conversation opening remain
unverified. Immediate disposal ownership covers both adapters.

This unit does not yet navigate from a closed map to the information frame, nor
select radial dialogue options. No completed runtime capability is claimed. The generator's pure
source/output binding changes with this successor; older receipts stay historical.

MEASURED V3:175/175 combined controls passed, zero skips, pytest 0.75s child 0. This includes 44 actor-context cases,14 combined navigation
SDK cases and 5 actual-generated-loader cases, plus 112 unchanged observer/comm
controls. Independent READ review found no remaining source blocker. In-place
frame/table ID changes are explicitly refused even when their object references
are unchanged. Caught LuaJIT SEH diagnostics remained visible. These controls use
synthetic native state and do not qualify engine callback ordering or rendering.

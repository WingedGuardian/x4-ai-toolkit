# Native ship-configuration menu adapter

Experimental, offline-qualified infrastructure. No game execution or purchase is
established by synthetic tests. The module neither registers transport nor opens
a menu on import. Integration, authorization, game-session binding and durable
action journaling belong to its caller.

## Why use the actual menu

The native menu owns loadout generation, licensing, pricing, crew allocation,
resource warnings and cart validation. Recreating those rules risks a different
commercial path. This adapter resolves the actual registered ShipConfigurationMenu,
waits for its built info frame, reads native widget state, and invokes only the
wrapper installed for a fixed current widget.

A menu callback is semantic UI execution, not a physical click. It does not prove
rendered pixels, input focus, or the originating Docked/Interact menu entry.
Opening the configuration menu directly must not close a test about a disabled
originating entry. Gameplay API results cannot substitute for UI refusal tests.

## Read source and confidence

READ evidence from these game-owned files, not distributed copies of game code.
Line numbers describe the pinned 9.0 source.

| Source | SHA-256 |
|---|---|
| ui/addons/ego_detailmonitor/menu_ship_configuration.lua | a18af705ec02eb1a0a0bef1f0e035eb26d418c8d2009514c578f03ec237cc707 |
| ui/addons/ego_detailmonitorhelper/helper.lua | d24a08b8da9f2c972794b60acb48ae36f38cb026c991249dab9f1164272d4df2 |
| ui/addons/ego_detailmonitor/menu_docked.lua | 760a0e6edfea33b9579dae213b3691ca734f0169272f28bb1b076b1f52ce11fd |
| ui/widget/lua/widget_fullscreen.lua | 420afba33d925a7b55f2a82ab12773df04826ef588317010d209b249de7baed1 |
| ui/addons/ego_detailmonitor/menu_map.lua | 6c56ba3881495ad8ee7db3a144c16079f2b2eaad56a3a1e5efe171f899fd6865 |

- **Registration/opening, 98% source confidence:** ship configuration 900–904
  inserts its local menu into global Menus. Helper.registerMenu 1331–1452 obtains
  GetMenuParameters2 and calls onShowMenu. Its shown flag becomes true before
  initialization finishes, so shown alone is insufficient. Helper 1777–1792 opens
  the native menu with converted parameters; 1893–1903 converts UniverseID values.
  Docked 1453–1469 supplies purchase/upgrade parameters. The adapter never calls
  onShowMenu with invented internal state.
- **Built callback identity, 98%:** Helper 4115–4162 assigns actual frame/table/cell
  IDs and installs wrappers. Buttons 2663–2690 preserve the UI trigger event;
  dropdown wrappers 2731–2776 preserve native argument order. Ship configuration
  9295–9323 supplies class/ship/loadout handlers: ordinary class/ship controls use
  helpOverlayID rather than uiTriggerID; loadout has neither and is matched
  uniquely by the actual native handler.
- **Enabled state/options, 98%:** widget_fullscreen 2613–2622/12454–12480 uses
  GetNumDropDownOptions/GetDropDownOptions3, including native per-option active
  booleans. The menu omits active on many descriptors (9124–9146); omission is
  never guessed. Buttons use IsButtonActive (11982/15412). Selection sets
  SetDropDownCurOption before the confirmed callback (2954–2976). Panel mode has
  a private active-panel gate (17178–17191); this unit refuses panel-mode frames.
- **Native price/cart path, 97%:** ship configuration 7683 onward assembles errors
  and warnings; 8155–8170 builds Confirm; 8518–8522 builds Add and captures its
  hasupgrades/hasrepairs arguments in a closure. The adapter never invents those
  arguments. Missing resources/unsuitable modules are not converted into new
  blockers: actual observed native states are retained.
- **Economic completion, unqualified:** buttonConfirm 1800–1822 transfers money
  before repairandupgrade. AddBuildTask6 11452 and payment annotation 11453–11455
  are downstream. No rollback or atomic recheck is established by these callers.
  Menu closure or callback return proves neither charge nor task, delivery,
  repair or refit. Those require separately bound money/task/event/ship evidence.
- **Runtime availability, unverified:** actual registered menus and native globals
  must be accessible in the integration's Lua environment. Dependencies are
  explicit; absence refuses instead of synthesizing them.

These percentages describe source links, not a live run.

## Factory and integration contract

The module returns new(deps) and native_reader(ffi, C, globals).
Use the bundled native_reader for widget_state and set_dropdown_current.
It validates native Color/DropDownOption3 layout and field types before native
option buffers; anonymous proof types never reach C. Existing names alone are
not ABI proof. Buffers use the native named type.

| Dependency | Required actual binding |
|---|---|
| menus | Global Menus, never a fabricated partial menu |
| cell_id | GetCellContent(table, row, column) |
| convert_id | ConvertIDTo64Bit, including native Lua-ID parameters |
| epoch | Nonempty caller-qualified UI/world epoch, invalidated on reload |
| now | Finite nonnegative monotonic clock |
| resolve_issued | Current issued station/ship token; refuse stale/absent objects |
| open_menu | Native OpenMenu |
| convert_params | Helper.convertComponentIDs on the new parameter table |
| widget_state | Bundled reader with IsValidWidgetElement, IsButtonActive and C dropdown getters |
| set_dropdown_current | Bundled native SetDropDownCurOption wrapper |
| is_player_owned | Optional until view is requested; exact boolean from qualified GetComponentData(component, "isplayerowned") |

Methods:

- observe() reads the current built configuration model and each fixed widget.
  Missing/unknown widgets are UNAVAILABLE/UNKNOWN, never enabled. Scope is
  CURRENT_BUILT_CONFIGURATION_MODEL, not whole-UI or economic PASS. Native text,
  options, booleans, warnings, missing resources, quote/cart/loadout remain
  distinct. Dynamic text functions are labelled unresolved, not invoked to
  invent rendered text.
- prepare(action, option, expected) accepts class, ship, loadout, add, confirm,
  repair_tab, repair_select and close.
  Expected contains the exact mode and decimal container ID. Dropdown choices
  must currently be offered/enabled. Add uses the built closure. Confirm requires
  one cart entry of quantity one, with no group or editing. Preparation has no
  game effects.
- prepare_open(mode, station_token, ship_token) accepts purchase, upgrade or view.
  It refuses an already shown menu, obtains issued identities, and prepares
  {0,0,station,mode,ships}. It grants no docked/immediate behavior, selects no
  preset, clears no other cart, and does not prove the originating entry.
- execute(ticket) consumes before dispatch; rechecks epoch, 30-second age and
  menu/frame/cell/wrapper/handler/model identity and native option/enabled state.
  Pre-dispatch drift refuses. An exception after the first setter/callback attempt
  is UNCERTAIN. Normal return is DISPATCHED_UNVERIFIED, never COMMITTED or complete.
  Duplicate/reentrant execution returns the stored outcome without repeating.

No arbitrary menu name/function/expression/callback arguments are accepted.
Caller authorization must bind prepared evidence and preserve a durable journal
across transport failure; local one-use tickets do not replace that journal.

Limits: 128 tickets, 256 menus, 64 frame children, 512 rows/table, 32 cells/row,
4096 wrapper entries, 2048 options, 4096 bytes/string, and 131072 copied string
bytes per model/options read. Overflow, unsupported values, ambiguous cells,
unknown enablement, duplicate options, count drift and cycles refuse. Count/fill/
count checks do not make the engine snapshot atomic. Native C strings must be
valid for the synchronous call; limits apply after reading each native string.

## Capability boundaries and runtime controls

This prepares configuration UI observations/actions, not a complete direct quote
or purchase API. Required runtime controls are:

1. Open a native control yard and bind initialized frame/parameter IDs.
2. Observe an offered ship and a known unavailable ship; do not invoke absent or
   disabled choices. Use current widget evidence rather than an invented gate.
3. Select an explicitly authorized offered preset and observe the rebuilt menu.
   Old prepared tickets must refuse.
4. Observe insufficient funds through the actual Confirm state; do not invoke
   a disabled callback.
5. For one approved purchase, capture money and active/waiting tasks before
   dispatch; invoke once; correlate actual task/events and delivery. Ambiguous
   joins remain unresolved. Task zero after payment is not a retry invitation.
6. Change/close/reload menus between preparation and execution and verify refusal
   with no payment. A transport loss after dispatch must not repeat payment.
7. Qualify upgrade/repair separately: the native route varies when docked and may
   create a Repair order or signal repairs_initiate. Direct restore/signal APIs
   bypass the commercial route and are excluded.

Originating Docked/Interact entries remain outside this unit. The scoped view,
close and repair successor below supplies their own native routes; it does not
prove originating-entry availability, ordinary UI navigation or runtime success.

## Read-only view, owned close and repair successor

Source confidence is 95% for each transferred native route; runtime availability
and outcomes are unverified. MapMenu 13233/13266 checks isplayerowned and opens
ShipConfigurationMenu with `{0,0,nil,"upgrade",{tostring(ship)}}`. Configuration
2708 makes that native mode read-only. The public action `menu.view {ship}` maps
to this shape; "view" is an adapter action, not an invented native menu mode.
Only a current issued ship with an actual boolean ownership result is accepted.
Missing ownership support refuses at action time and makes no constructor calls.
Observe binds the current object back to that sole native parameter and labels
the returned model read_only. Commercial widgets remain non-actionable there.

`menu.close {opening}` requires the same provider's successful, outer-qualified
menu.open or menu.view request. It binds the actual menu, subject, info frame,
model, main frame and native close handlers. Configuration 3272 offers the standard
close button; Helper 1072's closeElement(frame,"close") reaches the native
onCloseElement 10390 route. Preparation requires that exact offered main-frame
close and no modal context. Dispatch returns DISPATCHED_UNVERIFIED. Only a later
same-session read of that same registered menu's shown=nil or false flag can report
completed, scoped OWNED_MENU_CLOSED_READBACK. It proves neither pixels nor any
transaction. The opening-to-view association is sampled; outside concurrent UI
manipulation is not independently excluded by these observations.

Native Helper cleanup (helper.lua1623) clears `menu.shown` to nil. The close
observer accepts exactly nil/false after its owned dispatch checks and reports
`shown=false, shown_normalized=true` with `native_shown_state=UNSET|FALSE`.
Other native values refuse. This corrects the earlier false-only readback;
the historical synthetic fixture used false and did not cover native clearing.

`menu.repair_tab {station,ship}` uses the actual repair sidebar wrapper
(configuration 3635, displayed in infoFrame 9363).
`menu.repair_select {station,ship}` uses the built shipconfig_repair button
(5789–5795), whose closure invokes buttonSelectRepair 1209. Both require the exact
issued station/ship, upgrade mode, nonempty observed damagedcomponents and no
group. Selection additionally requires the single matching repair slot and an
empty repair plan, since the native button toggles existing selections. Prepared
model identity includes all repair fields. The upgradeplan is compared across
either callback; a change is UNCERTAIN after the attempted call, never retried.
No callback is fabricated and no native repair signal/order is called directly.

The sequence is native upgrade opening, repair tab, repair selection, then the
existing menu.add/menu.confirm path. Each step needs fresh preparation after the
native menu rebuild. Add 8518 captures the actual hasupgrades/hasrepairs flags;
repairandupgrade 11386 may signal repairs_initiate when docked or create a Repair
order otherwise. This source evidence explains the route, not an observed repair.
Current fitted loadout, money, tasks/orders and actual repaired components need
independent before/after readbacks. Damaged-component membership alone does not
prove hull repair, charge, progress or completion.

The original `widgets` vocabulary remains exactly five entries for the existing
menu pager. Two additive `repair_widgets` observations are serialized as part of
the full result; the pager's legacy read_complete denominator still concerns its
original five widgets only. No paging protocol or capacity ceiling changes.
Every opening/close/repair preparation uses the existing 128-ticket lifetime
budget, including consumed refusals; there is no eviction or UI-reload workaround.

## Offline controls

Actual LuaJIT runs this module with synthetic menus/native FFI stubs. Controls
cover wrapper ordering/triggers, native booleans when descriptor active is
omitted, false/unknown states, unoffered options, stale frames/handlers/quotes,
bounds, FFI refusal, cart scope, exact issued IDs, replay/reentrancy and
post-attempt uncertainty. They do not prove engine, screen, prices or purchases.

The first run passed 54 controls. Windows LuaJIT protected pcall errors caused
pytest's fault handler to print misleading SEH banners despite exit 0. Controls
now run in bounded child processes retaining assertions/stderr/timeout/exit.
No global fault handler is disabled. The final run passed 68/68 controls,
zero skips, in 33.51 seconds under the resource gate (child exit 0, 57 MB peak).
Six added controls bind every additional Add-to-cart input: settings, paint,
custom name, loadout name, duration and object-group state.
The runner's implausible CPU peak is not evidence of CPU compliance. This result
qualifies these synthetic boundaries only; independent source review and live
qualification remain separate.

The additive view/close/repair successor passed 200 combined menu/provider
controls, zero skips, rb 6873fb7789ed, exit 0 (43.09s pytest, 67 MB peak). Tests run
the actual current Lua modules and SDK with synthetic native dependencies,
including unchanged menu paging, failed outer opening-readback adoption,
ownership, native parameter shape, offered close, disabled/changed repair UI,
loadout drift and once-only execution. The earlier 198-pass run preceded the
pager/outer-readback corrections. Final tested source hashes are:

- live_menu.lua: 89411838592e7e58a7321d8bf8ec78e088b6d2f08265cbe2820760bbd601e009
- live_nativeproviders.lua: 887fef6b80e4547ff0695c8a863392fb77f91f403ce60224c10bda2dc6e10eab
- test_livemenu.py: e997b02d32fc5806c3a1d6a0e451960556c6e836c2de2bedd0b0d5476a13da0d
- test_livenativeproviders.py: df21d498f4af06a289282a6ffbe08c7e059797be00dd0fe43ebdf70e8bb5f1d4

These tests do not establish native assembly ownership forwarding or a composed
helper installation. Those integration steps remain separate from this unit.

The native-cleared visibility successor passed 205/205 menu/provider controls,
zero skips, job a35c97999f9d (40.23 seconds, 66 MB, exit 0). It covers nil and
false close readbacks plus true/zero/string/table refusals without replay. The
provider source includes the separate Options successor. Caught LuaJIT runtime
error diagnostics appeared; stdout transport truncated their middle, so this
record is the visible test summary, not a full raw-log archive. The implausible
CPU peak is excluded. No engine call or whole-helper result is claimed.

Final independent review added a visibility preservation check after the last
lifecycle callback. A callback that reopens the menu while retaining the epoch
now refuses; metadata comes from the retained native observation. The final
menu/provider suite passed 206/206, zero skips, job 0e7ddef756a7 (41.93 seconds,
66 MB, exit 0), including that regression. This supersedes the 205-control
source snapshot above; the same stdout-truncation and CPU-peak caveats apply.

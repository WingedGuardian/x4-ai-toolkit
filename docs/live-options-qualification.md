# Built Extension Options route

## Callback-button and close successor (2026-11-10)

Source-contract confidence95%; game wiring, pixels and authored effects remain
unverified. The adapter now observes and dispatches built callback buttons and
closes the current in-world OptionsMenu. Neither operation accepts a user-supplied
function, MD cue or arbitrary menu name.

Native displayOption buttons use table rowdata with a callback. Actual ROR uses
SN API Make_Button: interface.lua259–278 passes boolean selectable rowdata,
459–466 attaches its widget callback, and606 onward signals the MD row/column
closure. Those are separately tested shapes. The latter exposes a built-only
identity `widget:<native-id>` and `identity_kind=BUILT_WIDGET_ONLY`, never an
invented authored ID. Static text comes from the built text descriptor retained
by Helper.button:setText5753. Dynamic text is not evaluated; unsupported button
cells are counted, with callback_button_scope_complete=false.

options.button requires exact page, row, col, widget, observed id and static text.
It revalidates native enabled state, selectable row, descriptor, registered
onClick wrapper and private reference identities before invoking that wrapper
once. Reserved native save/load/new/extensions and navigation pages are refused.
This generic callback route is not an author/side-effect sandbox: an operator
must authorize the intended setting/report effect, and verify that effect
separately. A returned handler never qualifies it.

options.close requires the exact current page and its bound in-world frame.
gameoptions.lua13671–13736 onCloseElement('close') uses closeMenu/cleanup rather
than history/back/quit navigation. Modal questions, remapping/direct input and
cutscene state are excluded; all three handler identities are retained. Native
cleanup3556–3561 may resume simulation, so native_pause_possible=true is disclosed.
Later completion means only that this same registered menu has shown=false after
our successful dispatch, at a later clock reading. Cleanup may clear page/table
fields; no unchanged-page, pixel or unpaused-simulation claim is made. Helper
cleanup1623 sets native shown=nil; the normalized shown=false predicate is
explicitly marked shown_normalized, with native_shown_state=UNSET or FALSE.

Every prepared Options action now checks its30-UI-second lifetime again after
native model validation, immediately before dispatch. A slow read that passes
the deadline consumes the ticket without invoking the handler.

MEASURED synthetic controls:224 passed, zero skips, rb a73067678f37, child exit0,
18.83s pytest/19.4s wrapper,62MB peak, selecting test_liveoptions.py,
test_liveoptions_button.py, test_liveoptions_button_sdk.py,
test_liveoptions_close.py and test_livenativeproviders.py. This includes MSA
boolean rows, built-ID versus invented-ID refusal, disabled/dynamic/drifting
models, one-shot close, native special-state exclusion, final lifetime expiry,
SDK closed schemas and later-only visibility completion. It is not a game test.
The preceding184-pass native-row-only run is historical, not ROR button proof.
The final normalized-visibility supplement passed23/23 close controls, zero
skips, rb0aef2b124ffb, explicitly covering both native false and nil. It overlaps
the224-control group; the results are not an additive unique denominator.

Offline source preparation. No game call, deployment or setting change executed.

Native gameoptions.lua3615 retains optionsFrame on layer4;4713 builds selectable
row data;13493–13580 dispatches actual row selection. The adapter uses this
handler only from main/Extension Options index, never save/load/extensions/new
game special branches. Native OpenMenu caller437 and onShow13056 support the
fixed main route; opening in-world OptionsMenu may pause simulation (12951–12955).
Return from a handler is not proof the next view opened.

Installed SN API packed sources: options_menu.lua SHA256
dd96b98baa776d8c76ec67e6caf731ea60be100f2843b5ddd73d49fa07360ccb;
interface.lua db52b195fea7dd7915c803f960c21000094324ea092171f4bd8a1702c3530d86.
They patch OptionsMenu and put Extension Options at the main level. Registered
custom-menu creation is delayed by one frame. Widget IDs supplied by MD are not
exposed as native UI IDs; a private caller must independently bind its authored
setting to the observed row/column and validate the actual setting afterward.

SN interface542–571 replaces option.id with its one-based index, preserving
option.value. Helper6168–6172 retains the options table. The adapter compares
native dropdown IDs/text/availability to the descriptor and exposes a typed
authored-value mapping. Selection requires the exact observed page, row, column,
widget, native option ID, text, value type and value. Missing/unknown value mapping
is refused. The native registered dropdown wrapper is called after the existing
SetDropDownCurOption mechanism; no MD setting cue is called directly.

Private tickets retain frame/table/cell/row/handler references, numeric IDs,
projected models and world/UI stamp. Every write is revalidated, consumed once
and never retried by status. Post-write failure preserves uncertainty.

Historical resource admission held the 50 planned actual-Lua
controls before launching pytest. The bounded request returned rb exit 3 with
28.75/31.84 GiB RAM used and the running-game 20% free-memory floor. No retry is
queued. After memory recovery, all 50 controls passed with zero skips under
resource job e637d286100d (17.51 s pytest, child exit 0). Synthetic controls cover native-index versus authored-value selection,
view/model/handler changes, special-page refusal, consumed tickets and uncertain
post-write failures; they do not establish game-runtime behavior. Independent
READ review checked the native gameoptions/Helper and installed packed Simple Menu
API sources and found no concrete source blocker. Production SDK integration uses
closed options.open (empty arguments), options.submenu (page/id/submenu), and
options.select (page/row/col/widget/option/text/value_type/value). Combined adapter
and provider controls passed 113/113 with zero skips under resource job
f66829c76c26 (17.97 s pytest, child exit 0). Existing provider controls emitted
caught LuaJIT Windows SEH diagnostics; this is a passing test result, not a claim
of diagnostic-free execution. Independent READ review found no new integration
blocker. Assembly now constructs this adapter using the shared native widget
reader/setter, current menu registry and canonical session. It exposes the fixed
options_view read with one page argument and supplies the actual adapter to the
native action provider. The bundle hashes its source and disposal retires it.
Status reads the expected page without repeating dispatch and never qualifies
the setting effect. The outer provider/policy must retain its explicitly approved setting
scope: this generic adapter can select any currently built, fully bound dropdown.

The factory takes menus/current/now, valid_widget/cell_id, existing
menu.native_reader widget_state/set_dropdown_current, and native OpenMenu wrappers.
It exposes observe(page), prepare_open(), prepare_submenu(page,id,submenu),
prepare_select(page,row,col,widget,option_id,text,value_type,value), execute(ticket)
and dispose(). Only the two named submenu entry pages dispatch a row. Open is
fixed to OptionsMenu/main; no arbitrary menu, save or load operation is exposed.
The private caller must wait for the actual next-page model and independently
verify the authored setting effect. This offline candidate is not the installed
toolkit and has not changed any setting.

Combined assembly/bundle/composition/main-helper controls passed 399 with zero
skips (resource bbfa60e25634). These include actual options observation through
the assembly and wrong-page refusal. Independent wiring review is clear.
An earlier invocation named a nonexistent test file and ran no tests; it is not
verification evidence. Native menu behavior and setting effects remain untested.

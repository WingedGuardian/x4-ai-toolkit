# Native provider registration bridge

Offline-qualified and runtime-unqualified. This module maps the reviewed native
menu, order, camera and transaction adapters into the public action SDK. It adds
no native API declarations, transport, arbitrary callback execution, automatic
purchases, orders or cleanup. Constructor/import performs no native calls.

The factory takes instantiated menu/orders/camera/transactions adapters,
public_current, resolve_issued, now, and revision (the full loaded bundle SHA256).
Its spec registers x4-native-v1. request.binding must exactly equal that revision.
The bootstrap still requires this provider/revision in its explicit allow-map.

| Action | Flat arguments | Evidence boundary |
|---|---|---|
| menu.open | mode, station; ship only for upgrade | Direct configuration entry, not originating-menu availability |
| menu.view | ship | Owned issued ship, native nil-container read-only upgrade shape |
| menu.close | opening request ID | Own outer-qualified opening; later same-menu shown=false readback only |
| menu.repair_tab / menu.repair_select | station, ship | Actual built repair controls; existing Add/Confirm still required |
| menu.select | mode, station, field=class/ship/loadout, option | Actual offered widget option; callback return unverified |
| menu.add / menu.confirm | mode, station | Existing native cart validation; no inferred economic completion |
| order.submit | ship, kind=move/dock/attack, target, policy=require_empty; xyz for move | Native order readback, never arrival |
| order.cancel | submission request ID, exact order_id | Only own created order; later absence after observed removal |
| camera.view | target | Later exact external-target readback, not pixels |
| camera.restore | owned verified view request ID | Cockpit setter request only; no attested mode getter |
| options.open | none | Fixed in-world OptionsMenu/main; may pause simulation |
| options.submenu | page, id, submenu | Actual main/Extension Options navigation row |
| options.select | page, row, col, widget, option, text, value_type, value | Built dropdown mapping; authored effect unverified |
| options.button | page, row, col, widget, id, text | Bound built onClick wrapper; MSA IDs are explicitly built-only |
| options.close | page | Bound current in-world menu; may resume simulation; later visibility flag only |

Preparation keeps native tickets private; receipts never serialize opaque camera
tickets or native menu models. SDK and adapter both consume execution before a
write; exceptions or lost replies never trigger retries. Status does not rerun
prepare. Native execute retains its own model/eligibility/identity checks. The
public five-field stamp is checked before and after calls; a changed identity
retires this provider. Disposal only retires callbacks and ownership, never
orders, ships or camera state.

Menu and transaction full observations are separate observe(kind,token) reads,
for caller-owned bounded encoding/paging. Transaction enumeration remains
non-atomic. Empty queues and vanished tasks do not establish construction,
delivery, repair or purchase completion. Commercial menu action status stays unqualified
even when a subsequent current model is available. Only menu.close and options.close may complete
from a later owned-menu visibility read, explicitly scoped to that flag. Large models are deliberately
excluded from the action core's 512-node receipt budget.

Native provenance is inherited unchanged from live-menu-qualification.md,
live-orders-qualification.md, live-camera-qualification.md and
live-transactions-qualification.md: the wrapper invokes only those reviewed
methods. Source-contract confidence is 97%; actual game wiring and native
semantics remain unverified. Tests execute actual Lua with synthetic native
inputs, not X4.

## Qualification status

The initial resource-admitted run (rb 43701cbcec06) executed 35 controls:
34 passed and one failed because its expected result overlooked the SDK's earlier
exception for nonfinite request values. The actual camera, menu and order adapter
integration controls passed. The test now expects that specific SDK rejection;
thirteen additional identity, cancellation, restoration, clock, tri-state and refusal
controls bring the prepared suite to 48. Current source changes expose cockpit-attempt
facts, preserve unknown/malformed attempt values rather than inventing false, and
require ordered dispatch/status timestamps before any scoped completion. The initial
35-control result does not qualify these changes.

The revised run was NOT launched: rb returned WAIT/exit3 with 27.90/31.84GiB RAM
used under the active-game floor and 52.87/53.91GiB commit used. No retry is queued.
The subsequent admitted combined run passed 81/81 controls (48 provider plus
33 assembly), zero skips, child exit0, rb5ef7c79db23b; pytest completed in0.55s.
It consumed current provider source7db5426a4756ef19139ebac47827c73e19e72c846fada578c923fe965ee537c9
and test9a2bd98d9aab26c5e8a2ec94ba250f16208c25f8159035ac8801c6d3853e7f1f.
Independent READ review cleared
the provider and the added clock checks at source SHA256
adc83db5a46fdad24da1a642f604bf6dd882a6cda843a21c42a2c073bd639a20.
The review did not rerun controls or infer live behavior. Root then identified the
attempt-fact tri-state issue and cleared its narrow correction at final source
7db5426a4756ef19139ebac47827c73e19e72c846fada578c923fe965ee537c9.
Windows LuaJIT emits caught-exception SEH diagnostics during refusal tests; these
are retained, not suppressed or treated as runtime game evidence.

The completed invocation used the normal resource wrapper in tools/x4validate,
the isolated frozen environment, python -B, --tb=short and pytest's cache provider
disabled, selecting tests/test_livenativeproviders.py and
tests/test_livenativeassembly.py. No deployment or game was touched. These are
synthetic native controls; real engine integration remains unverified.

## View/close/repair successor

The four additive menu actions preserve all older action schemas. View and close
reuse current issued/native menu identities; repair actions resolve both exact
station and ship before native preparation. Close adoption additionally requires
the original opening's dispatch_readback_qualified flag: a returned native value
followed by failed provider clock/identity validation cannot authorize it.
Close completion never promotes money, repairs, delivery or rendered pixels.
Native ownership forwarding belongs to the assembly integration; no helper
composition, loader or staged game file is changed in this unit.

Final targeted menu/provider run: 200 passed, zero skips, rb6873fb7789ed, exit0,
43.09 seconds pytest / 43.8 seconds wrapper, 67 MB peak. It includes the actual
menu→SDK close/repair controls and unchanged menu pager roundtrip. The earlier
198-pass run predates those two integration corrections and is historical.
This final invocation disabled only pytest's faulthandler plugin to avoid its
misleading Windows LuaJIT caught-SEH dump; actual process exit, assertions,
resource limits and bounded native-menu child tests remained active. No X4 was
launched or queried. See live-menu-qualification.md for the native source pins,
limits and required runtime readbacks.

The Options callback-button/close successor and its final pre-dispatch expiry
check passed224 synthetic controls with zero skips (rb a73067678f37). See
live-options-qualification.md for native/MSA row distinctions, special-state
refusals and the exact five-file denominator. Optional missing button/close
adapters return REFUSED without dispatch; they never manufacture completion.
Runtime effects remain unqualified.

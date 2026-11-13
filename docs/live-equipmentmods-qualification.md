# Native installed equipment modifications

`live_equipmentmods.lua` reads modification presence and native roll fields. It does not read fitted macro compatibility or alter, install, remove or reroll modifications. Its factory accepts `ffi`, `C`, `globals.IsValidComponent`, `resolve_issued`, `current`, `now`, and `encode`. `read(issued_ship, kind, issued_component_or_nil)` returns OBSERVED or REFUSED; `dispose()` retires it. Native assembly exposes equipment_mods with exactly two arguments for ship/engine, or three for component-target kinds.

READ source: `ui/addons/ego_detailmonitorhelper/helper.lua:126–148,162–189,242–259,341–345,9730–9774` defines the four native mod structs and Helper.getInstalledModInfo dispatch. `ui/addons/ego_detailmonitor/menu_ship_configuration.lua:7314–7350` resolves group identity through GetShieldGroup before shield/grouped-turret reads. The selected weapon class check has a native caller at 10712. `menu_map.lua:594,29086–29117` supplies GetContextByClass and native parent-container traversal semantics.

Kinds are ship, engine, weapon, turret, shield and grouped_turret. Ship/engine take no component token. Direct weapon/turret use the issued weapon component; shield/grouped_turret use the native returned context, group string and group component, with the exact issued ship as defensible. Shield scope is the resolved shield group associated with the selected component, not a claim that the selected component is itself a shield generator. Group context+name+component identity remains explicit. A successful native false result means no installed mod **on this exact resolved target**. An unresolved group, wrong class, stale component, unknown boolean, missing string or failed call is REFUSED, never empty.

The current conservative membership boundary requires the component's immediate native container to be the issued ship. It does not guess that a nested component belongs to a ship; a runtime refusal on a valid nested target is a capability gap to investigate against the actual hierarchy. Direct turret and weapon labels do not claim different native class hierarchies: both use the native weapon class and getter. No component or group is automatically issued for subsequent queries.

All native struct roll fields are retained, including integer additions and zero values. They are raw native factors/fields, not reconstructed effective damage, shielding or thrust. Name, RawName, Ware, PropertyType and Quality come from the native buffer. Two point reads must agree, including group identity. This is sampled stability, not atomicity or a whole-ship census. A caller seeking all modified targets must separately qualify its fitted-component/group coverage, then join each exact point result; one successful point does not prove every ship installation was checked.

FFI verification checks uint64 aliases, size/alignment/offsets, pointer types, unsigned storage and float representation before calling the native getters. Private comparison structs are never passed as native buffers. Strings are bounded to 512 bytes, result encoding to 3,000 bytes, and an instance to 4,096 attempted reads. Oversized/nonfinite/unavailable data refuses without partial OBSERVED output. Canonical session, exact issued identities and a finite monotonic UI clock bracket each read. There are no native writes, profile changes or filesystem actions.

Offline LuaJIT controls qualify dispatch, wire bounds and defensive checks only. Native engine ABI behavior, container nesting and integration remain runtime gates.

The fitted-equipment producer now issues validated SLOT component IDs when an
accepted page of a complete capture is published. Only that page's retained,
installed components are issued. Partial captures, failed native reads, empty
slots, group summaries and expired/reset captures issue none. The producer binds
the original UI identity table before native reads and checks it before publishing;
current validity remains checked again on later component queries. This is page
publication, not verified host delivery or proof the component still has that fit.

This producer change passed 94 offline equipment controls with zero skips
(resource 98abb3bd0ea6), including multi-page issuance, late native failure,
bounded omissions, UI reset and invalidation. Independent source review is clear.

Offline integration passed172 assembly/bundle/composition controls, zero skips
(resource1701918c08c8), including the actual module's absent-mod versus failed
getter through native ID conversion. Independent wiring review is clear. The
bundle hashes this source and shared disposal retires it. These tests do not
qualify native nesting, fitting coverage or a real modification roll.

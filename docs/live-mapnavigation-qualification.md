# Native map information navigation

Offline candidate only; no native game action or deployment. READ sources:
DockedMenu1494 opens MapMenu with the fixed infomode/info/subject structure;
MapMenu6330 retains a valid prior subtab. The adapter uses the same parameter
structure and the native Helper component-parameter conversion, only from a
closed map with no other shown menu. This does not claim the map opened.

MapMenu1124/10549 identifies the actual built object-info tab by its fixed
helpOverlayID. Selection invokes its registered Helper button wrapper, preserving
native buttonInfoSubMode3862 planned-order handling. The panel route binds actual
sideBar/rightBar info-row widgets from19853/19920 and their wrappers. Neither
route assigns menu state. Frame/table/cell/handler/subject IDs are revalidated
before a consumed-once dispatch. Post-dispatch failure remains uncertain.

Explicit left/right actor-context observation uses the actual info frame layers
4/5 and native onTableRightMouseClick dispatch. The historical caller without a
side retains its right-pane meaning; it does not automatically select a pane.
Panel mode requires the selected pane's actual visible-state flag.

MEASURED: 92/92 map-navigation and left/right actor-context controls passed with
zero skips, resource job84005554b651, pytest15.30s, child exit0. Caught LuaJIT SEH
diagnostics appeared in the existing actor-context tests. Independent READ review
found no source blocker in those modules or their subsequent V4 integration.

The initial V4 integration attempt was NOT_LAUNCHED at RAM27.14/31.84GiB under
the running-game20% free floor. After resources recovered, fresh admission
f8f81740f1c6 ran the exact four-file suite: 121/121 PASS, zero skips, pytest15.97s,
child16.6s and exit0. Files: test_livemapnavigation, test_liveactorcontext,
test_livecommmenu_provider and test_liveconversation_extension. Existing direct
Lua tests emitted caught SEH diagnostics; this is not diagnostic-free execution.
The earlier92 result remains a separate module-only measurement.

The generated conversation loader now embeds the map adapter and registers
map_inspect/map_open/map_panel/map_objectinfo on x4-comm-menu-v1. All take exact
context/side strings; map_open requires left. Context inspection/opening gains
an optional side, with historical right default. No request accepts an arbitrary
menu name or selector. Loader disposal owns the new adapter immediately. The
existing MD output remains unchanged and requires no new schema proof for these
Lua-only changes. The private navigation-v4-qualification.json binds the current
factory, generated loader, unchanged MD bytes and exact four-file test sources;
the historical V3 receipt remains preserved.

Native widget
shapes, actual display, conversation actor continuity and gameplay effects remain
runtime-unqualified. This is a fixed navigation capability, not general menu or
input execution. Radial option selection remains a separate unsupported route.

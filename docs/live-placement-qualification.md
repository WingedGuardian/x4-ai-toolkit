# Diagnostic player placement

The player action and its MD/Lua behavior described below remain unchanged. The
new owned-ship diagnostic successor adds a second provider and MD output through
the same optional descriptor; see [owned-ship placement](live-shipplacement-qualification.md).
The two-output factory/composer qualification below is historical. The successor
factory returns three outputs; aggregate helper composition is qualified separately.

This optional engineering extension requests native diagnostic player transport to one helper-issued, player-owned, non-spacesuit ship. It does not simulate walking, ordinary boarding, docking, or completing any campaign case. No game action has been run to qualify it.

The fixed provider `x4-diagnostic-placement-v1` exposes only `diagnostic_place_player`, with `{ship: issued_token}`. The source-bound SDK binding and canonical public session are mandatory. Preparation resolves the issued ship and asks MD to retain its actual object plus the player's current container, occupied ship and controlled object. Execution rechecks those identities and a ten-second preparation limit in each clock's own domain. MD consumes the request before calling the native action. Status observes the retained request without repeating transport.

Native source evidence (READ):
- `md/setup.xml:1774–1780`, `Debug_Setup_SkipperShip_WarpPlayer`, uses `teleport_player object="$SkipperShip" instant="true" force="true"`. The proposal copies the omitted control attribute. A schema default is not evidence that the player obtained a cockpit seat.
- `ui/addons/ego_detailmonitor/menu_map.lua:27118–27129` sends a converted native component ID in a UI event. `md/tutorial_map_advanced.xml:273–274` resolves `component.{event.param3}` and checks player ownership.
- `md/setup.xml:576–583` observes player container and controlled object separately.

The proposal retains uint64 cdata in the request target field; it never substitutes a decimal/ULL string or searches by object name. Scalar native-ID event transfer has a working analogue. Transfer of that typed ID nested in the metadata table is still a runtime gate. The reply must echo the identical resolved object through the native channel converter; missing or different targets refuse. The queued transport is not claimed atomic.

MD records requested transport separately from actual target occupation/control. SDK `running` alone is not evidence of either. A successful snapshot means an observation at that moment, not sustained control. An unresolved dispatch keeps the MD reservation; timeout does not release it or cause a retry. The reservation releases only after actual occupation/control of its target is observed, or world invalidation. UI-only recreation does not erase an unresolved reservation.

Bounds: 64 retained request records, 64 queued replies, 256 transport calls per Lua instance, one pending transport callback, ten-second UI response timeout, bounded wrapped replies. Full batches are validated before callbacks. Ambiguous send, timeout, duplicate batch, correlation or world drift faults the adapter rather than replaying.

Confidence: 95% for copying the native diagnostic action and preserving issued/native identity offline; 90% for the proposed nested typed-ID transport, pending engine observation. Explicit assumptions: the actual engine preserves nested uint64 transfer; the copied diagnostic action can reach the selected ship in the current scenario. Both are tested by target echo and actual location/control readback in the approved runtime campaign. A bounded scalar-event transfer would be a separately reviewed fallback, never an automatic retry after ambiguous dispatch.

Pure `scripts/live_placement_extension.py:prepare()` returns two candidate byte strings (one MD script and one optional loader), source pins and inventory. It writes nothing. Shared bundle/helper composition is not changed by this unit. Install/deploy/launch remain separate actions.

## Fixed optional helper composition

The helper composer accepts extensions=('placement',) with exactly the two
qualified candidate paths. The factory, provider and MD source pins are checked
before/after generation. Canonical order is placement, tradertext, rooms,
topology, conversation, persistence. The manifest discloses instant diagnostic
teleportation and research bypass, and separately describes native SETA control.
These descriptions do not claim successful ordinary boarding or piloting.

Combined composition and actual placement-provider controls passed93/93, zero
skips (e1bab7047eaf). Independent narrow review found no blocker. This is offline
generation/SDK qualification; the typed-ID engine transport, actual occupation
and control still require runtime observations. No files were installed.

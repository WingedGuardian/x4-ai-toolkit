# Diagnostic automatic room entry

This optional extension operates without an engine-opened transporter menu. It is a diagnostic player placement route, separate from the existing ordinary transporter provider. It does not qualify walking, docking, room access rules, locks, ship control, room geometry or a visual case.

The fixed provider `x4-diagnostic-roomentry-v1` exposes `diagnostic_enter_room`. Arguments are `container` (a current helper-issued ship or station identity), `selector` (`controlroom`, an exact room macro, or `dynamicrooms`), and optional `room_choice`. There are no caller expressions, coordinates, raw cue names, arbitrary native IDs or numeric room indices.

`dynamicrooms` is an observed-match preview for containers whose dynamic room macro is not known beforehand. It queries native navcontexts and their rooms. Even a single match requires a subsequent request with its opaque choice token; preview never automatically selects or transports. Returned macro IDs are deduplicated into `macro_ids`, and each candidate carries its macro index. This is the sampled native query population, not proof of every engine room or rendered interior. Private rooms are not excluded in this diagnostic query; ordinary access policy is not evaluated.

The preview bounds native navcontexts at 16 and each context's room list at 64. If either is unreadable or beyond that bound, `scope_complete=false` and `room_count=false` mean the total is unknown, not zero; no choice can dispatch from that response. A complete returned room set above 16 reports its full count and refuses retention/selection. No silent truncation or first-room fallback is used.

`room_query_observed` identifies preparation replies containing that preview. Execute/status snapshots omit enumeration counts instead of presenting unqueried zero counts as a fresh room census.

Preparation queries only that container and selector. It retains an exact native room and its observed `tag.npctransport` waypoint. Missing rooms and missing waypoints refuse. Multiple matching rooms return up to 16 observed identities with opaque choice tokens; a subsequent request can choose one of those retained handles. The producer reports the full matching count. Counts above 16 refuse rather than silently omit a room. A choice must belong to the same container and selector in this provider instance, and fresh native membership must still contain it exactly once before dispatch.

Execution consumes the prepared request before any effect. If needed, it requests native teleport to the waypoint's controllable container, waits for the native teleport-success event, leaves a control position when required, and waits for the native stopped-control event. Already-listening sibling cues then use the native room-placement and waypoint-offset sequence. The stage advances before each effect, preventing replay. Unresolved transport retains a single Busy reservation; neither timeout nor an action return clears it. Only a later exact-request observation of the selected current player room, after the placement actions were called and with no controlled object, clears that reservation. World invalidation retires all records.

Completion means a sampled matching player room after the diagnostic placement call. It is not a full case PASS, an achieved camera pose, safe geometry, ordinary boarding, continuous presence, or proof of every engine callback. Native action failure can leave an unresolved reservation requiring a new qualified world session. Use one player-placement controller at a time; this extension does not invent a cross-extension locking framework.

## Source transfer

READ: `extensions/ego_dlc_timelines/md/scenario_transport_refugees.xml:419–440` supplies `find_npc_waypoint`, teleport to its controllable component, the teleport-success listener, `add_actor_to_room`, and `create_position` using the observed slot offset and player race height. `extensions/ego_dlc_boron/md/gs_boron2.xml:252–280` supplies the leave-control action and the sibling cue listening for stopped control before adding the player to a room. `md/scenario_tutorials.xml:2704–2734` searches transporter waypoints within observed rooms. `md/conversations.xml:1220–1225` searches a controlroom waypoint. Named macro matching uses native `find_room` (for example `scenario_transport_refugees.xml:4071`).

The object transport reuses the existing typed native-ID MD channel and exact echo checks. Nested uint64 transport into the engine remains a runtime gate. The request's public world/session/UI-generation binding is checked on every transport and asynchronous continuation. Preparation makes no native player change.

READ: `md/npc_instantiation.xml:1641–1645` queries `class.navcontext` components on a station and iterates each interior's actual `.rooms`; the preview reuses this bounded component-query mechanism for its issued container. Applying that generic query to a ship remains a runtime transfer gate. `md/scenario_tutorials.xml:8678` reads an object's `.macro.id`. Neither property declaration nor static validation is claimed as runtime population proof.

Source-transfer confidence:95% for the cited action shapes and room/control-event sequence;90% for applying the observed transporter-waypoint positioning sequence across arbitrary authored rooms. Actual engine transfer, all requested room instances, geometry and event delivery still require runtime observation. Schema proof and synthetic endpoints cannot close those gates.

## Packaging and limits

`scripts/live_roomentry_extension.py:prepare()` returns only two candidate byte strings and source pins: `md/X4ToolkitRoomEntry.xml` and `ui/live_roomentry_extension.lua`. It installs nothing. The generated loader uses the supported extension catalogue and owns its MD-channel disposal. Records 64, reply rows 64, matched-room retention 16, transport calls 256, preparation 10 simulation seconds, pending response 10 UI seconds. Distinct clocks are not compared. Serialized replies enforce the existing wire bound; oversized evidence refuses.

The public code contains no campaign-specific hull or room names. The campaign owner must map every requested hull/room to its exact source-authored selector and preserve each missing, ambiguous, over-cap or unreachable outcome. No twelve-hull completion is asserted by this mechanism alone.

Continuations additionally require nonregressing simulation age within 30 seconds of dispatch. A late event cannot trigger another player effect; expiry retains the unresolved reservation and never proves absence or success.

The dynamic-room preview successor passed 62 synthetic controls, with zero skips, using the actual generated loader, SDK, wire and LuaJIT. A fresh canonical MD compile covered one tree with zero findings and no skipped validation. The earlier known-selector unit's 49-control and schema receipts remain historical evidence. No actual game call, deployment or XML application occurred. Runtime gates remain open.

The selected room macro is frozen with its native room identity and rechecked before dispatch and every asynchronous continuation. Oversized preview evidence settles the request as unknown; it does not leave an unresolved preparation callback or silently truncate choices. The maximum retained 16-room preview with long unique macro IDs is covered by an actual-wire refusal control.

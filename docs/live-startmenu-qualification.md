# Native start-menu automation qualification

These are offline-qualified adapter building blocks, not a deployed new-game
capability. No game was launched, no native menu was opened, and no world was
started during qualification. The host dispatcher and native UI bootstrap are
implemented below; installation, actual pre-world loading and native runtime
remain unqualified. A synthetic policy callback is only a test dependency, never
evidence of user approval.

## Native source route (READ)

The reference used for this work was X4 9.0. The following named source paths
are references to the user's own game data, not redistributed files.

- ui/addons/ego_gameoptions/gameoptions.lua SHA256
  cff978c89336297ce182ad0b0fb6937c994448396f53359be70efa94fc3ee58f:
  OptionsMenu registration 423–429; startup OpenMenu parameters 437; modal
  modified/security/first-game questions 440–451; createOptionsFrame 3615–3628;
  displayNewGame 9483 onwards; actual offered row data 9582; native Start button
  9883–9884; buttonStartGameActive 10258–10277; buttonStartGame 10279–10303;
  viewCreated optionTable binding 13073–13104; onRowChanged selection 13277;
  ordinary newGameCallback route 13360–13432.
- ui/addons/ego_detailmonitorhelper/helper.lua SHA256
  d24a08b8da9f2c972794b60acb48ae36f38cb026c991249dab9f1164272d4df2:
  native delayed one-time callbacks 940–953; rowChanged updates currentTableRow
  and calls the actual menu handler 959–975; button setText stores
  properties.text.text at 5753–5757.
- ui/widget/lua/widget_fullscreen.lua SHA256
  420afba33d925a7b55f2a82ab12773df04826ef588317010d209b249de7baed1:
  SelectRow public wrapper 11590–11605 forwards into native checked selection
  but intentionally discards its return; row-change dispatch 11741; global
  export 18832. A nil return is not failure or successful selection evidence.

live_startmenu.native wraps actual registered Menus, IsStartmenu,
GetCellContent, IsButtonActive, IsValidWidgetElement, SelectRow, OpenMenu,
ReadText, getElapsedTime and Helper.currentTableRow. It never constructs a menu,
assigns selectedOption or invokes NewGame/buttonStartGame directly.

## Menu behavior and refusal boundary

The module observes the actual visible registered OptionsMenu. Other shown menus,
native modal contexts, animation transitions, missing frames and unknown data
refuse. Ordinary starts come from the built optionTable and its exact rowDataMap;
the row object's authored module data must agree with that mapping. Counts are
bounded, and unsupported model values refuse rather than being omitted.

prepare(id32, action, option) supports only:
- open_new: native OpenMenu("OptionsMenu", "new", nil, true), without bypassing
  any existing modal. This is a native routing action, not proof of selecting the
  original top-level New Game entry.
- select: an offered ordinary module through actual SelectRow, followed by
  Helper current-row and menu selected-object readback. It never assigns the
  menu's local state directly.
- start: the currently selected ordinary module and current actual Start widget.
  Tutorials, Timelines hub/scenarios, custom/map editors, multiplayer pages,
  unavailable/locked starts and unmet requirement strings refuse.

Start has no uiTriggerID in this native source. Identification is therefore
explicitly structural: current new-game page, table tabOrder=4, first column,
colspan=3 and localized ReadText(1001,9902), with exactly one matching built cell.
The native cell ID, descriptor active boolean, IsButtonActive and the actual
buttonStartGameActive predicate must agree. Its exact buttonScriptMap wrapper,
handler, selected object/model, table and frame are retained and rechecked.
This structural contract must be requalified if the native menu implementation
changes. It is not a universal recognition rule for other menus.

execute consumes preparation before requesting authorization or invoking any UI
callback. Preparations expire after 30 seconds; UI binding, native start-menu
state, modal state, current page, frame, selected model and callbacks are checked
again. A deferred authorization reply repeats those checks. Repeated callbacks,
replayed execute requests, disposal and changed UI instances cannot repeat Start.

The host-supplied authorize_start(context, callback) is a trusted dependency.
Production must consult applicable explicit authorization for this campaign,
profile and desktop/focus effect, and bind it to the exact UI/process identity,
request and game-start ID. An approved campaign can cover creating new worlds in
its already-running disposable game. The toolkit never launches or restarts the
game process. Caller
JSON, a CLI flag alone or a constant true callback cannot implement the policy
boundary. The native menu module does not issue consent or perform guard evaluation.

DISPATCHED_UNVERIFIED means the native callback returned. UNKNOWN means effects
may already have happened. SELECTION_OBSERVED proves only the native selection
readback. No status asserts world-loaded, campaign completion, successful
initialization or game-launch approval. Status records are historical and require
a current outer transport binding when delivered.

## UI-only pipe, separate from a world

live_startpipe uses only the fixed x4live_startmenu pipe. The wire provider is
x4-ui-startmenu-v1 and every reply declares UI_ONLY_NO_WORLD and
world_session=false. Reusing the scalar codec's session field carries a UI
instance token in this namespace only; it must never be accepted as an MD public
session by SessionLink or a world-action provider.

A trusted bootstrap must supply a fresh UI-instance binding, qualified helper
build, actual process/profile connection and the authorization dependency.
It must not synthesize a world nonce before a world exists. After leaving the
start menu this pump stops and invalidates pending menu preparations. Any new
world must establish its own actual MD session through the existing world bridge.

READ of installed Mod Support APIs:
- ui/named_pipes/pipes.lua SHA256
  d47ebe15fc11ad8c21449ee9eadc66c9078d9c2255f0b30afdef9f2bb8f78095:
  per-pipe Read_Pipe 730–755, Write_Pipe 814–837; direct connection 344–378;
  public exports 855–858. These methods address one named pipe.
- ui/named_pipes/interface.lua SHA256
  8d3606bec82e2f18f12cc3c7377ad52ad070b8cf5cef89cb09c30c988f6ff1e3:
  deferred Init reads player/blackboard state. It is not used here.
- ui/lua_loader.lua SHA256
  fc57c5a1a11a2dc6c567244ed9552b2101c5f77666855390108ea3be99ec356c:
  Register_OnLoad_Init waits for the MD readiness exchange; it is not sufficient
  to bootstrap a channel before a world.
- ui/time/interface.lua SHA256
  1910066cb2126dccb789d1697635b0b320fc25baef5324fb12c168a1a62787d0:
  its global onUpdate wiring is deferred. This adapter does not call that Init.

The pump instead schedules bounded 0.1-second one-time callbacks through native
Helper.addDelayedOneTimeCallbackOnUpdate with blockinput=false. It never replaces
global onUpdate, initializes shared MSA Time, runs shared Poll_For_Reads/Writes,
drains world blackboards or reads a player object. One module instance owns this
dedicated pump; explicit disposal is required before replacement.
An adapter disposal exception or explicit false result is sticky: a later retry
cannot turn failed retirement into permission to replace the pump.

Only observe/prepare/execute/status envelopes are accepted. Complete raw packets
are consumed/cached before dispatch. A repeated identical call returns its cached
reply; changed bytes under that ID refuse. A pending authorization is reconciled
through a new status call, never another native Start. Bounds: 1,400 request
bytes, 4,096 response bytes, 128 retained calls and 30-minute pump lifetime.
Oversized model responses announce UNAVAILABLE/complete=false; they do not claim
an empty offered-start set. Paging such a model is not implemented here.

Native Read_Pipe/Write_Pipe have their own internal connection/error handling.
Write failure can follow a possible send, and the native implementation does not
attest full-write length. This pump adds no retry: failures invalidate the
instance. Exact framing, server-side pipe ownership, actual pre-world module
loading and end-to-end recovery must still be qualified. The host should listen
before this dedicated pump starts. Protected UI mode may prevent library loading;
the adapter never changes that setting.

## Qualification

The initial menu-only run passed 65 actual Lua controls, zero skips. Windows
LuaJIT's caught-error SEH mechanism produced misleading faulthandler banners with
exit zero. The final harness runs each control in a bounded child and requires its
assertions, exit code and terminal marker. The combined native-menu and actual
codec/per-pipe run passed 89 controls, zero skips. After the independent review's
sticky-retirement correction, all 12 selected lifecycle/disposal/write-failure
controls passed, including two added failure-return/exception controls. The final
inventory therefore has 91 distinct controls; the 79 unrelated cases were not
repeated after that narrow correction. These are synthetic native dependencies.

No native runtime, pre-world loading, actual user authorization or rendered UI evidence
is implied by synthetic qualification. The fallback for a missing actual menu or
unsupported state is a scoped refusal, not raw NewGame or fabricated local state.

## Host/bootstrap integration

live_startbootstrap assembles only the menu, dedicated pump and existing scalar
codec. Its UI_STATE challenge is volatile to that Lua instance. UI_BIND consumes
the latest unexpired challenge, binds one host-allocated generation, expected
process/profile scope hash and source-declared 64-hex bundle build. A bound UI
cannot change generation/process/profile. Every subsequent reply echoes all four
identities. Neither GetPlayerID nor a world blackboard is required.

_livestartmenu.UIStartPipe uses the existing LivePipe Windows named-pipe creation
and handle ownership, with the fixed x4live_startmenu name. Its exchange checks
the actual host WriteFile byte count and refuses incomplete/invalid/oversized
reads. Connection and reply timeouts make no claim about a world being loaded or
the game being hung. It never starts X4 or automatically reconnects/retries.

StartClient.connect requires that connected dedicated pipe, constructs actual
ProfilePeer against the approved executable and explicit personalfolderid, then
performs the UI handshake. SessionJournal's exclusive allocation mechanism is
reused in a separate ui-generations directory. Its internal session column is a
UI nonce here, never an MD session. Actual process creation identity and profile
are checked before/after requests; loaded-build and native UI-generation replies
must match. The build is a source-declared loaded identifier, not cryptographic
measurement of executing machine code; deployment/source qualification remains
necessary.

ConversationAuthority receives a trusted agent-side decision callback. The
callback must consult the actual conversation policy and return exact
AGENT_RECORDED_CONVERSATION source, reference, applicable running-disposable-
campaign scope and context digest. That declaration is explicitly not
cryptographic human attestation. No packet, JSON receipt, CLI --yes flag or
serialized object can reconstruct the opaque in-process capability. Issuance and
one-use consumption are written exclusively to the task journal before dispatch.

The complete context includes connected process/profile/build/UI generation,
exact prepared request/start, and the agent-declared desktop/focus impact. The
agent must supply real approval provenance; this library cannot verify the truth
of an external conversation. It does not add a required TTY or manual menu step.

StartClient.prepare preserves request evidence before sending preparation.
execute repeats its journal/identity checks, obtains and consumes an applicable
opaque authorization for Start, and writes a dispatch marker before execution.
The first native call leaves Start pending; a separately correlated authorize
packet consumes that exact pending native request and invokes its already-bound
button wrapper. No native world-completion claim is accepted. Authorization
nonces correlate trusted host evidence, not a native human-authentication system.
The single owning host and its policy callback are the trust boundary; this is
not a security sandbox against another same-user program rewriting or bypassing
the host dispatcher.

Lost prepare/execute/authorization replies leave durable evidence and poison that
client. There is no automatic resend. A newly qualified connection to the same UI
can issue status only for the prior request; its dispatch marker prevents another
execute. A UI reload or different process/generation cannot adopt that request.
Status evidence is retained with its exact envelope. Entering a new world requires
the separate actual world-session handshake; UI disconnection is not completion.

The production agent entrypoint is StartClient.connect with a real UIStartPipe,
approved image/profile/build, task journal, explicit desktop/focus context and
ConversationAuthority backed by the actual agent conversation decision. There is
no process-launch routine and no untrusted convenience approval flag. Bundle
loading must instantiate live_startbootstrap before the world-only Init path;
that integration is now present in the pure bundle composition and main-helper
candidate. Actual loading remains unqualified until the approved game run.

## Offline integration qualification

The combined native-menu/pump and Python-to-actual-Lua bootstrap chain passed
137 controls with zero skips. Final review added two controls and tightened only
the Python host: the immediate pre-send peer must equal its qualified identity,
and a nonzero ReadFile status is refused even if bytes were returned. All 48 host
controls then passed; the unchanged 91 menu/pump controls retain the combined-run
result, giving 139 distinct passing controls. The tests replace native UI and
process/pipe APIs with controlled doubles, while using the actual codec,
bootstrap, pump, menu adapter and host journals. They perform no game actions.

Actual pre-world module loading, Windows pipe connection to X4, native widget
shapes and new-world transition remain runtime qualification gates. No test here
establishes that a game was launched or a world started.

The main-helper integration subsequently passed 329 focused bundle/main/legacy/
composition controls, zero skips, resource 13e48a125db8, with independent source
review. It declares its own IsStartmenu signature before native lookup, uses
the direct per-pipe module before world Init, and retires its UI owner before
creating the world owner. This adds no process launch and no automatic retry.

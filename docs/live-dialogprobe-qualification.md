# Read-only dialogue capability/model probe

Offline candidate; not deployed or engine-qualified. Construction declares two
native boolean functions but performs no native query or action. Capture reports
fixed-name global visibility, conversation flags and at most six option rows,
only when the actual IsDialogActive return is boolean true. Missing or malformed
values remain UNKNOWN; failures retain row indices and explicit issues. No option
selection, conversation start, registration, input or arbitrary evaluation occurs.

READ native 9.0 evidence, from the user's reference (not redistributed):

- ui/core/lua/dialogmenu.lua:188–224 forward-declares clickDialogButton,
  onDialog*MouseClick and leaveDialogMenu as locals. Their later function bodies
  are not evidence that a menus-addon global can access them.
- Lines 290–303 register engine contract events to those local callbacks.
- Lines 626–634 require private.dialogMenuShown and selectable before proceeding.
- Lines 746–752 select the option and hide the dialogue together.
- Lines 850–858 read five GetDialogOption values for each of the six configured
  positions (configuration line 65); line 1124 uses IsDialogActive after UI reload.
- ui/addons/ego_detailmonitorhelper/helper.lua:400–401 declares the two native
  conversation booleans; line 1402 distinguishes active/cancelling conversation.
- ui/core/addon.xsd exposes the menus environment, not arbitrary core-presentation
  injection. This schema fact is not proof that no other supported route exists.

Global function visibility is not native identity attestation. A visible callback
does not prove it is the native rendered-button handler. The probe therefore
always reports actor, section and rendered visibility UNKNOWN, route_qualified
false, and atomic false. UNBOUND_MODEL_OBSERVED means six typed bounded model rows
were read under one public stamp; it does not mean the menu was rendered, the NPC
was identified, or a conversation case passed. Same-activation option transitions
cannot be ruled out by this read alone. Public stamp, clock, activation or symbol
drift makes the result UNQUALIFIED. Text over 1024 bytes, shortcut over 128 bytes or
wrong types produce UNKNOWN fields, never silent truncation.

Full C-UI-3 remains blocked pending a source-proven real presentation route and
independent actual actor/section binding. Direct SelectDialogOption is not a
substitute for the guarded click+hide route. The safe next native qualification
is an explicitly approved bound read in inactive and active dialogue states,
checking actual global visibility and all six native return types. No manual
transcription is required; no unsupported OS-input workaround is proposed.

The isolated native assembly exposes this as `dialog_probe` with exactly zero
arguments through the existing public bound read interface. The bundled module
is part of BUILD, not an untracked side loader. Native assembly retires it with
the other adapters. This adds no conversation action or actor identity shortcut.

MEASURED final integrated 140/140 controls passed (31 probe,42 assembly,67 bundle),
zero skips, childexit 0, pytest 0.47s. The actual 16-module
bundle constructs without native data/action calls. Read controls cover missing
globals, unreadable/oversized fields, inactive dialogue, per-row retirement and
activation, session/clock/symbol drift, and no mutation callbacks. Independent
READ review is clear. Caught LuaJIT SEH diagnostics remained visible while the
child completed successfully. Native parameter ABI, cross-presentation visibility
and real option values remain runtime-unverified.

Reference source hashes at review:

| File | SHA256 |
|---|---|
| ui/core/lua/dialogmenu.lua | 4031d5db3185e0f7739bffb0d8c600aad12fbff664e1dcc707ab80a30ba296aa |
| ui/core/addon.xsd | 67d318ee4a6892905198519df094b8abe357c84751166e3bc4af2ee19a826cca |
| ui/addons/ego_detailmonitorhelper/helper.lua | d24a08b8da9f2c972794b60acb48ae36f38cb026c991249dab9f1164272d4df2 |
| ui/addons/ego_detailmonitor/menu_map.lua | 6c56ba3881495ad8ee7db3a144c16079f2b2eaad56a3a1e5efe171f899fd6865 |

The Egosoft-hosted [UI/MD communication guide](https://wiki.egosoft.com/X4%20Foundations%20Wiki/Modding%20Support/UI%20Modding%20support/Talking%20with%20MD%20and%20AI%20scripts/?language=en)
describes menus-environment RegisterEvent/blackboard channels; it does not give
this candidate access to the core presentation's local click handler. Local
helper 1818–1829 and map 5586–5608 provide a real menu-to-conversation opening route,
but they do not provide independent readback of the current actor/section or
prove cross-presentation selection equivalence. These are bounded source findings,
not a universal claim that no route exists.

## Follow-up route and identity research

READ on 2026-11-09: `libraries/scriptproperties.xml:2440–2442` exposes
`player.conversation`, `player.isinconversation` and `player.conversationactor`.
The first is a conversation ID, not a documented current-section property;
`isinconversation` explicitly includes non-interactive conversations. Neither
should be relabelled rendered dialogue readiness.

Native `md/conversations.xml:6–31` handles start/return events and distinguishes
an actor component from a two-item context/template list. Its section handler
at 307–318 reads the actual section from `event.param` and likewise distinguishes
actor representations. `libraries/common.xsd:12602–12720` documents the matching
finished/next-section/returned-section/started event fields. A template actor
must retain its typed context/template pair; coercing every actor to one issued
entity ID would lose identity. A conversation-finished outcome is the last
chosen section or a non-interactive conversation name, not proof of a successful
purchase or another downstream gameplay result.

A bounded future identity observer can copy those event-cue patterns, retaining
actual conversation/section/actor, an event sequence, current public session and
game age. It must invalidate on finish/load and refuse a section it has not
observed, including attachment halfway through an existing conversation. An
explicit correlated snapshot can join the event record to current player
properties; those properties alone cannot reconstruct a missed section. This
is a source-backed design, not implemented or runtime-qualified. It introduces
no selection, purchase or generic MD execution action.

The remaining selection gate is `BLOCKED_RENDERED_ROUTE`: the inspected local
presentation registers `dialog1`–`dialog6` on its own `UIContract`; no supported
cross-presentation dispatch was established in the inspected core dialogue,
Helper, ViewHelper, native menus or official communication documentation. This
bounded search does not prove an engine-wide absence. Calling a same-named
function found in the menus environment would not establish native identity.
The older X Rebirth Lua overview is not current X4 ABI proof (it even documents
a different GetDialogOption return tuple); the local 9.0 caller remains the
source for the probe's five values.

The smallest discriminating runtime check is the existing read-only probe in
inactive and active dialogue states, under the same bound public session. It
can establish which fixed globals and model fields are actually readable, not
actor identity or rendered selection. If the local click handler remains
unavailable, preserve the blocked capability and require a source-qualified
presentation entrypoint before any selection adapter; do not fall back to
arbitrary evaluation, bytecode/upvalue access, backend purchase calls or OS
input. MapMenu.openCommWithActor is a separate opening path and must retain its
active-menu lifecycle if later adapted. No new game run is authorized here.

Additional source pins:

| File | SHA256 |
|---|---|
| libraries/scriptproperties.xml | 1b641b638c8892284bbf5943f9ed283e0adf79f696341a2f00a2361f16485a8f |
| libraries/common.xsd | 8eec878511d06dd2be88a7054eb54556b29e481bf0feba20fe2cf3c6eabd3452 |
| md/conversations.xml | 783985423dca953c0d41e993939eff68b10712401ae1525c90624607765db345 |

Confidence is high for the cited source behavior and the probe's deliberate
refusal scope; cross-presentation execution and a live identity-observer join
remain unqualified. This follow-up changes documentation only; the 140-control
receipt above qualifies the unchanged probe/integration source at caff9c55.

## Bounded native-input research follow-up

READ: libraries/inputmap.xml157-162 and
inputmap_defaults_keyboard_mouse_wasd.xml121-126 map dialogue actions 1-6 to
keyboard 1-6. Core dialogmenu 626 explicitly mentions shortcut keys and checks
private.dialogMenuShown/selectable before leaveDialogMenu performs selection and
hide. This supports investigating a genuine native-input route; default maps
do not prove the user's current bindings. gameoptions 4844 reads current
GetInputActionMap() using numeric action keys;4953 identifies source 1 as keyboard.
The scoped source reads did not establish the numeric dialogue action mapping
or a supported cross-presentation sender. A localized GetMappedInputName result
does not establish a machine keycode.

The separately developed Windows capture host reads the bound HWND/process and
pixels only. It contains no keyboard injection, focus, restore or window-message
capability. Capture permission is not input permission. No native input or game
query was performed in this research. Direct SelectDialogOption still does not
prove the rendered core-handler route. A future input implementation needs an
explicit current binding, current rendered choice/actor verification and an
approved exact X4 foreground/process-bound transport. No such transport is
claimed by this package; do not replace missing proof with a generic key/click.

Source pins for this follow-up:

| File | SHA256 |
|---|---|
| libraries/inputmap.xml | 31ac1db6e3d944f88ddf749cb6c8acfef5360db7265c023b2c68aa3ead7331e5 |
| libraries/inputmap_defaults_keyboard_mouse_wasd.xml | 6c2112c3ce943c9bc69e0187a7982135ff7bf968e5007c0b6eab850e77c19391 |
| ui/addons/ego_gameoptions/gameoptions.lua | cff978c89336297ce182ad0b0fb6937c994448396f53359be70efa94fc3ee58f |

## Foreground input feasibility: design only, 2026-11-09

The later assignment authorizes offline investigation of a supported host-input
route. It supersedes the research prohibition above, not the requirement to prove
the route or to obtain approval for an actual game action. No keyboard, focus,
window-message or game operation was executed. No input transport is implemented.

READ: `_liveclip.window_for_peer` independently checks the pipe peer's process
creation time and image, then requires exactly one visible, non-minimized,
size-bounded top-level HWND. It explicitly does not prove foreground or unobscured
visibility. Reusing that identity check is appropriate; reusing its capture
authorization as input authorization is not.

Microsoft's [SendInput contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
accepts an input array, with no destination HWND. Its return counts inserted
events, not an application response. Existing pressed keys can interfere, and
UIPI can block delivery without a distinctive error. A
[GetForegroundWindow check](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow)
reports the foreground HWND at the instant queried and may return null. INFERRED:
checking it before and after injection cannot exclude an intervening focus
change. Therefore a future adapter must not promise X4-only delivery merely
because both checks matched. Targeted window messages are not an established
replacement for the native input route.

The smallest proposed operation is one chosen dialogue shortcut, with a private
once-only ticket and these prerequisites:

1. Actual bound pipe/session/UI generation, expected profile and process
   incarnation, unchanged source inventory and exact eligible HWND.
2. Fresh current input binding for that dialogue action, including an established
   engine-keycode to host-keycode mapping. Defaults and localized labels are
   insufficient. Reject unknown, ambiguous, modifier-dependent or unsupported
   mappings; do not alter the user's bindings.
3. Fresh selectable choice, current actor/conversation context, and a qualified
   rendered-dialog observation. A six-row model snapshot and historical MD
   section alone do not satisfy this requirement. A caller boolean cannot fill
   the gap. Reject unrelated modal/menu state or unresolved identity.
4. Exact foreground HWND immediately before dispatch; required keys/modifiers
   observed released. Do not focus, restore or synthesize releases for the user.
5. Persist consumption before a single bounded down/up insertion. Any exception,
   partial insertion, focus/session drift or missing outcome is UNCERTAIN and
   stops further writes. Never retry an ambiguous choice automatically.
6. Independent bound post-action evidence: actual conversation transition and,
   separately, the case's expected effect. Event insertion, disappearance of the
   radial menu or a changed section alone is not purchase/service completion.

READ blockers remain concrete. `gameoptions.lua:4844` exposes current mappings
under numeric action keys, but the inspected source does not establish the
dialogue action numeric IDs or host scan-code conversion. `dialogmenu.lua:850`
returns a displayed shortcut string; `helper.lua:346` declares GetMappedInputName
as a string-returning API, not a machine-code API. The current probe does not
observe private.dialogMenuShown or prove that its actor/section is the rendered
dialogue instance. Official wiki search found the older X Rebirth Lua overview,
which is not current X4 numeric-enum/ABI proof. These are bounded research
limitations, not claims that the engine has no such facility.

Confidence: 98% for the cited Windows API limitations and native source reads;
95% for reusing the existing process/HWND identity checks as one prerequisite;
below 90% for a currently executable, correctly targeted dialogue-input route.
No dispatch implementation is justified yet. The next discriminating work is a
source-backed current-binding reader and a rendered-dialog/actor join. If those
cannot be established, retain BLOCKED_RENDERED_ROUTE and continue the qualified
native widget routes; do not substitute direct SelectDialogOption or require
manual transcription as if it closed the missing automation capability.

Offline tests for any later implementation must cover changed peer/profile/UI,
lost foreground, active modifiers, missing/changed bindings, stale choice/actor,
double execute, partial insertion and missing/contradictory outcome. Synthetic
tests can qualify refusal and journaling semantics only; real delivery and the
focus-race limitation remain explicit runtime gates.

## Current binding observation successor

The numeric-action unknown above is now RESOLVED by additional READ evidence,
not by guessing from the default map. `gameoptions.lua:4684` uses the numeric
action as the text ID on page 1005; the menus controls list includes actions 97–102.
Native `t/0001-L044.xml:4501–4506` labels those IDs as dialog choices 1–6. The
current-map call is GetInputActionMap() with no default-map argument.
`gameoptions.lua:5705` constructs source/code/signum/toggle tuples, and 5713–5730
documents the optional toggle flag. The text source SHA256 is
7e5fb67e683d3e17b49000d79c518d7679618fe2bb2292e6ead2d864870a8393.

The existing fixed `dialog_probe` read now adds `current_bindings`. It selects
only actions 97–102, with at most four bindings per action. Larger, sparse,
metatable-bearing or malformed selected arrays are UNAVAILABLE with a reason;
none is silently truncated. An absent action stays UNAVAILABLE; an actual empty
table is OBSERVED with count 0. Each input is a compact tuple whose field order
is declared in `input_fields`: source, code, signum, toggle. An absent toggle is
NOT_PRESENT, distinct from false. Raw engine codes are not Windows scan codes.
`host_key_translation` remains UNKNOWN.

TWO_EQUAL_PROJECTED_READS means precisely that both selected projections agreed
under an unchanged public stamp/global identity and valid clock. It is not an
atomic map census, input-delivery authorization, rendered-dialog proof or a
conversation result. Missing/changed second read is UNQUALIFIED; retirement or
session/symbol drift invalidates the binding claim as well as the parent result.
The original six option observations retain their separate scope.

MEASURED: the full changed module passed 56/56 controls with zero skips, pytest 0.18s, child 0.7s, exit 0. Existing direct-Lua tests
emitted caught SEH diagnostics. The actual wire encoder accepts the maximum
four-bindings-per-action example together with maximum-length ASCII option
strings below the assembly 32000-byte payload limit. Unusual escaped text or a
large world token can still hit the existing encoder/outer limits and refuse;
there is no truncation or claim every valid native model must fit. Initial tests
found a missing post-second-read retirement check and an oversized 16-binding
representation; both were corrected before this qualification. No native game
query, input, focus, deployment or profile/save operation was performed.

Additional research read the dependency Mod Support APIs (SNAPI) and some other
UI mods through the packed reader, without extraction. It is not a corpus-wide
absence proof. The other mods' dialogue code uses add_player_choice and
conversation events; packed code that is not plain Lua was not interpreted. SNAPI's
`ui/hotkey/interface.lua` registers directtextinput on its local Scene/UIContract
to suppress external hotkeys; its Process_Message forwards custom hotkeys to MD.
Neither inspected path establishes dispatch into the core dialog1 event.
SNAPI hotkey source SHA256:
ec8c3005effede0598e3e820d6879a7875af6bd5c95a63edffd34043f6d25368.

The core shortcut registration remains source-proven: dialogmenu 298–303 binds
dialog1–6 to local callbacks;376–406 ignores delayed events then invokes the
guarded click handler. A menus-addon sender into that specific presentation,
rendered actor/choice identity and host-key translation remain unqualified.
Do not confuse RegisterEvent/CallEventScripts menus callbacks with the separate
registerForEvent presentation contract merely because event names look alike.

Independent READ review of the binding successor found no concrete blocker;
no reviewer duplicated the 56-control run. Source frozen at 46fafe91.

Further READ evidence narrows, but does not close, host-input feasibility.
SNAPI's loose `python_send_keys.txt` is a keyboard-capture-to-MD bridge despite
its filename; it does not inject native dialogue keys. Lines 372–377 convert a
Windows low-level hook scanCode into the author's X4 code by adding 0x80 for an
extended key. Lines 723–749 describe keyboard source 1/sign0 and shift 0x100/
ctrl 0x200 modifiers. The same file warns about key aliases, English mappings and
NumLock/Pause differences. Its SHA256 is
89dcc8aa36e911551b68bfa527e6f463a32055c2c5a818f24ddd1a9ecd541284.
This is READ evidence from that author's implementation, not a measurement of
our native input delivery. A narrowly restricted unmodified top-row digit
translation is now a plausible candidate; general key translation is not proven.
The released probe still reports host_key_translation UNKNOWN because it does
not implement or qualify that conversion.

Core `subchannelbar.lua:125–130` independently registers startDialog after
NotifyOnStartDialog(contract), which supplies a source analogue for observing
notifications in another presentation. It supplies no dialog1 sender.
`dialogmenu.lua:1124` schedules display after IsDialogActive on reload, reinforcing
the distinction between model activation and displayed readiness. A scoped read
of 41 core Lua files for named dialogue/event symbols did not establish a public
sender into the guarded local handler. Compiled presentation assets and native
engine implementations were not interpreted. No universal absence is claimed.

Replacing/instrumenting core dialogue Lua is not proposed without a qualified
loading/override analogue. The current menus addon schema and inspected mods do
not establish that path. No Egosoft Lua was copied into the toolkit. The remaining
rendered-instance/sender gate is explicit, while the qualified built-widget and
current-binding observations remain available for the next approved runtime.

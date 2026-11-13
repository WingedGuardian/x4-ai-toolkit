# Restricted rendered-dialog input prototype

Design checkpoint, 2026-11-09. Offline engineering only: no input, focus, capture,
game query, launch or deployment has been performed for this unit. Runtime is
UNQUALIFIED. This is a proposed calling route through the actual rendered dialogue
shortcut, not a direct `SelectDialogOption` or MD purchase operation.

## Observable acceptance criteria

One explicitly prepared choice may insert exactly one unmodified top-row digit
keydown/keyup pair. Its evidence must bind the actual connected executable,
profile, process incarnation, HWND, public load/UI session, current typed actor,
conversation instance, six native option rows and current action97–102 binding.
An agent reviews an actual decoded captured frame, supplies literal visible
labels and bounded regions, and receives an opaque local review handle. A bare
`ready=true`, caller JSON receipt, option text alone or active model is insufficient.

Before dispatch, re-read the native evidence and foreground/window state. A fresh
review of a changed frame can accept harmless background/portrait animation when
the offered labels, actor and layout are still correct. Full-frame equality is
an optional shortcut, never the only execution path. Changed choice, actor,
overlay/modal, unreadable image, stale evidence, held keys, changed session or
unknown translation must prevent dispatch. A consumed request never replays,
including after a crash, partial insertion or lost response. Independent later
native and visual observations describe the result; insertion is never a
conversation-success or economic-effect verdict.

## Assumption chain and precommitted fallbacks

Confidence describes the evidence for each link, not runtime acceptance.

| Link | Confidence / evidence | Cheapest discriminating control | Fallback |
| --- | --- | --- | --- |
| Connected process/profile/session | 98%, READ existing `ProfilePeer`, `SessionLink`, bound envelopes and `window_for_peer` | process replacement, wrong profile, load/UI change refuse | no input; re-establish explicit connection |
| Current actor/instance | 95%, READ source-backed conversation observer's typed component/template and historical-instance joins | changed actor/instance, unknown context, stale snapshot refuse | obtain new fixed observer snapshot; never infer from text |
| Offered choice and binding | 97%, READ `live_dialogprobe` six rows/current actions97–102, two projected input-map reads | missing/changed/selectable-false choice and ambiguous bindings refuse | reread; unsupported binding stays unavailable |
| Top-row digit translation | 90%, READ SNAPI's native scan-code bridge plus Microsoft scan-code APIs; not measured against X4 | only source1, sign0, no toggle/modifier, scan2–7 and OS roundtrip to digit VK allowed | refuse other codes; no general key conversion guess |
| Rendered readiness | 85%, explicitly AGENT_REVIEWED decoded frame, not machine semantics | broken choice/actor/modal control must refuse; animated background with same reviewed labels may pass | fresh bounded frame and review; uncertainty means no input |
| Pixel origin | 90%, existing ClipPort's HWND/process and sampled host interval | foreign/modified clip, wrong HWND, decoder hash/source drift refuse | no input; new approved capture, not native still re-labelled as HWND proof |
| Native insertion | 95% API contract, runtime delivery UNKNOWN | fake SendInput records exactly two events; zero/partial/exception remain non-success | preserve consumed receipt; never resend or send compensating keys automatically |
| Foreground continuity | cannot prove atomicity; 90% for sampled checks only | changed foreground before prevents input; after makes outcome uncertain | preserve uncertainty; no focus change or automatic retry |
| Correct game outcome | UNKNOWN until independent observations | same-instance section/option changes and fresh reviewed result recorded separately | outcome UNKNOWN; do not replay input |

## Evidence and review boundary

Reuse local opaque ClipPort handles, pinned FFmpeg decoding and the existing
durable action-journal pattern. Exported receipts are correlation, not authority.
Native screenshot observation explicitly lacks proven pixel/window origin and
does not substitute for the HWND-bound clip path. Frame extraction is a bounded
read of the retained clip; it does not start capture. The reviewing agent's
transcription/regions, reviewer identity, exact frame hash and native context are
retained. This proves which image was reviewed, not that the judgment is correct
or cryptographically authenticated. No machine `rendered=true` slot is invented.
The native actor token/instance stays separate from the visually reviewed actor
label; a name match alone cannot manufacture an object identity.

Use a finite host monotonic budget, separate from MD/game/UI ages. Bound native
samples and review freshness are checked again immediately before insertion.
An in-game modal can evade Windows GUI-thread flags; the image review and native
option checks remain required. The global focus race remains possible even when
both snapshots agree. Current pressed-key observations can also fail to observe
state under desktop/UIPI restrictions; never describe all-zero observations as
a complete proof that no physical key is down.

## Source basis

READ: `reference/ui/core/lua/dialogmenu.lua`298–303 and376–406 register dialog1–6
and pass non-delayed events into the local guarded click handler;626–634 gates on
shown/selectable. The native binding observer's documented source IDs97–102 name
choices1–6. Inspected SNAPI `python_send_keys.txt`372–377 maps Windows hook scanCode
to X4 code, adding0x80 for extended keys;723–749 describes source1/sign0 and
modifier bits. This author example is a translation lead, not engine-wide proof.
Its SHA256 is89dcc8aa36e911551b68bfa527e6f463a32055c2c5a818f24ddd1a9ecd541284.

Microsoft's [SendInput contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
inserts into a global input stream, returns an inserted-event count, preserves
existing key state and is subject to UIPI. It has no target-HWND argument.
[KEYBDINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput)
defines scan-code and key-up flags. [MapVirtualKeyExW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mapvirtualkeyexw)
supports explicit-layout scan/VK roundtrips.
[GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow)
can return NULL. [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate)
can return zero on access failure, and its low bit is unreliable.
[GUITHREADINFO](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-guithreadinfo)
describes native window/menu state, not X4's internal rendered modal hierarchy.

The prototype must not request focus, install hooks, post private messages,
execute arbitrary Lua, open menus, synthesize modifiers, or automatically clean
up partial insertion. Runtime use requires explicit input/capture approval and
a throwaway test context. Offline tests inject fake Windows functions only.

## Partial insertion and recovery boundary

The initial prototype quarantines further input after zero/partial/unknown
insertion or a failed post-dispatch check. One inserted event may leave the
owned key down. An exception gives no trustworthy count. Neither is a routine
retry. The journal retains the exact scan code and result, with cleanup explicitly
UNRESOLVED. Native observations may continue; dependent input must not.

The implemented recovery action is a separately prepared/journaled KEYUP for only
that same digit from a known one-event insertion, with fresh exact process,
foreground, public identity and keyboard observations. It must not re-press,
release all keys, reset the journal, infer ownership of another key, or run as an
automatic compensation. The Windows global-stream race remains. Unknown counts
cannot silently become known owned-key cleanup. The new request must be within
five host seconds of the partial insertion; only the recorded digit may have a
down bit, with exact original foreground/window/layout/session. One reported KEYUP
plus unchanged post-state with no down bits can lift the input quarantine, but
`cleanup_verified` remains false: this is sampled Windows evidence, and a zero
key-state value can itself be an access failure. Failed release stays quarantined
and cannot repeat. No physical-key ownership is inferred from an unrelated user
press or from an unknown original insertion count.

## Implemented calling sequence

`DialogInput` binds the existing actual AutomationClient and ClipPort. Production
cannot inject fake backends or clocks. `begin_actor()` sends one fixed read-only
snapshot; `context(handle)` uses its native receipt and the existing bound
`dialog_probe`. The caller drives an already approved clip capture, then
`frame(clip_handle, context_handle)` decodes its last frame without capturing.
`image(frame_handle)` supplies those exact bytes for the agent's visual inspection.
`review(...)` records literal six-position transcription, visible actor label,
regions and overlays, with frame hash, clip file, native actor/instance and reviewer.
These records are append-only and checked again before input. They do not prove
that the reviewer judged correctly. Expected actor label is task intent; only the
separate typed native actor identity is machine-bound.

Each visible choice includes the agent's literal rendered shortcut transcription.
The selected visible digit must match the current source-backed scan-code binding.
Raw `GetDialogOption` shortcut text is retained for drift but is not assumed to be
literal digit text: native dialogmenu850–865 assigns it to a text widget, and its
encoding is not independently established here. No markup parser guesses what
the player sees. All six input-binding rows must be accounted before selection.

The caller obtains a fresh actor/context after review, calls `prepare`, then a
different fresh snapshot/context for `execute`. Native evidence must be at most
three host seconds old, the captured frame at most30 seconds, and review at most15
seconds. The local journal consumes dispatch before the sole SendInput call.
`outcome` retains independent later snapshot/dialog observations without inferring
selection, causality or economic effects. `prepare_release`/`execute_release` are
explicit separate requests for the known partial event only. No CLI command,
capture, focus or gameplay action is implicitly invoked by the module.

## Offline qualification, 2026-11-09

MEASURED: `tests/test_livedialoginput.py` passed **61/61 controls, zero skips**,
resource job `1b7810510ac6`, pytest1.66 seconds, child exit0. Windows function
objects were fake; the ctypes INPUT structures and code paths were actual.
No real Windows input, focus, window enumeration, game query, capture, launch or
deployment was performed. Native conversation/clip evidence uses synthetic
fixtures and is explicitly tagged SYNTHETIC in resulting records. This qualifies
offline orchestration/refusals, not delivered native keys or visual judgment.

Coverage includes constructor injection refusal, exact foreground/PID/GUI/modal/
focus/layout checks through fake native symbols, held-key refusal, scan-code
roundtrip, actual two-event/one-release structures, source actor/choice/instance
and session drift, malformed/unknown binding rows, changed/modal/wrong-actor
visual controls, harmless animated background with fresh review, raw shortcut
format independence, immutable review/owner journals, deadlines, foreign handles,
0/1/2/malformed/exception insertion, independent owned-keyup requests and failures,
no replay, and non-causal post-input native observations.

READ: independent services review found and closed two source defects before the
final run: arbitrary production backend/clock injection, and deletion of the
owner journal silently skipping validation. The final recovery/shortcut/outcome
review found no remaining concrete blocker. The reviewer did not repeat tests.

Qualified source SHA256:

- `_livedialoginput.py`: `90757f2f21b934c8f35e26cad5660983d7753fe255ca0442a19f08962f245336`
- `test_livedialoginput.py`: `5c3665b7a175ad485caea5ffaadb218203a00f0a64a1aef5870500644ea2f667`

Pillow remains optional. Image-dependent fixtures import/skip at the helper,
not collection time; native ABI/preflight tests and the explicit missing-decoder
refusal control run independently. The final61-control run had Pillow available.
It is not a claim of a separate full no-Pillow run.

Still required: an actual host calling/orchestration route with approved journals
and image-review delivery, installed source/build qualification, and an explicitly
approved throwaway runtime. Runtime must test broken rendered controls and real
unchanged/animated dialogue, actual input-map/shortcut representation, observed
actor correlation, key delivery and independent outcomes. A source/test pass is
not a guarantee of that result. No proposal here grants runtime input permission.

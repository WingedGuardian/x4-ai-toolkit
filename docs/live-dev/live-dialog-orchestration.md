# Explicit dialogue capture and input orchestration

This isolated offline candidate joins the existing production `AutomationClient`,
`ClipPort`, and `DialogInput` interfaces. It has not been exercised against X4.
It does not launch, focus, unpause, navigate to a conversation, or retry a choice.
Use requires separate runtime approval for the capture and input scope. The
required public session, conversation observer extension, and helper versions
must already be installed and qualified for that run.

`x4validate._livedialogport.connect_dialog` takes the already connected client,
the exact disposable profile, four existing nonnested task-owned journal roots
(`port`, `input`, `clip_port`, `clip_recorder`), a pinned FFmpeg executable/hash,
the observer provider revision, and the expected typed actor plus visible label.
Connection reads native profile context and creates evidence journals; it does
not record pixels or send input. A new port cannot resume an old input ticket.

The host retains one `DialogPort` instance throughout this sequence:

1. `capture(case)` sends one fixed read-only actor snapshot, polls its status,
   and explicitly records a five-second, five-fps HWND-bound clip. It decodes the
   last frame and exports one private PNG with exclusive creation/readback. The
   returned local token binds that PNG, clip, source hashes, native context and
   exact client session. No capture happens inside `choose` or `outcome`.
2. The agent actually views that PNG. `review(token, reviewer=..., transcript=...)`
   accepts the existing exact six-choice transcript described in
   [the input design](live-dialog-input-design.md). Actor label, visible labels,
   enabled state, shortcuts, regions and absence of obscuring overlays are agent
   judgments, not machine-verified booleans. A fresh animated frame may be
   reviewed without requiring equality to an older frame.
3. `choose(token)` obtains a fresh native snapshot, prepares the private ticket,
   obtains a second fresh snapshot, and invokes the existing once-only digit
   input adapter. Every stage is journaled before dispatch. A changed actor,
   choice, session, image, owner record or source refuses; failure consumes this
   route rather than automatically retrying. It never claims purchase success.
4. `outcome(token)` obtains a separate later native snapshot and read evidence.
   This reports sampled native observations, not causal or rendered selection
   acceptance. `status(token)` returns only retained local state.

Every successful return rechecks the port source/owner binding. If a post-input
check fails, the port quarantines the operation while retaining its input,
release and outcome receipts; an exception is not evidence that nothing acted.

`release_owned(token)` is a separate explicit recovery request, never automatic
compensation. The input engine admits it only for its own exactly-one-inserted
down event, within five seconds, with fresh matching peer/window/layout and
restricted keyboard state. It sends only that digit's KEYUP once. Unknown counts
or a foreign key do not confer ownership. No-down-bits observation can lift the
input quarantine, but neither OS access nor game cleanup is proven by that zero.

The Windows focus race remains: `SendInput` targets the global stream, not an
atomic HWND destination. Before/after observations and agent-reviewed pixels do
not remove that race. All result paths keep `selection_verified=false`; partial
insertion blocks further input. A slow review expires under the existing 30s
frame/15s review bounds and requires a new explicit capture/review. Native polls
are bounded to 48 attempts and 2.25 host seconds; they never resend preparation
or execution. Journals and clips remain preserved on failure.

MEASURED offline qualification: 27/27 dedicated controls passed, zero skips, pytest 1.36s/child exit 0. They exercise the
actual `DialogInput`, action journals and decoder path with fake client/clip/
Win32 dependencies. No native capture, input, focus or game calls occurred.
Optional Pillow is required only by image-based fixtures; this run had it.
The first run's test-local undefined `tmp` typo was corrected before this final
run. Four counterexamples change the port owner after work: they retain any
already-attempted input/release evidence and quarantine the operation.

Qualified source SHA256:

- `_livedialogport.py`: `755548965b13077f6324c5442ad788809ec3d26d590ef1c46991e72b477d140d`
- `test_livedialogport.py`: `7780dc678ee65939b9cbc4c82fe19bf72ace4641c0c094ca8fcc0bd122d51191`

Independent READ review by the services lane closed on those exact source bytes;
the reviewer ran no duplicate tests. That review is separate from owner-run controls. Neither
qualifies actual Windows delivery, X4 rendering, purchases, or campaign E2E.

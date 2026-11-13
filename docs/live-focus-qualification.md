# Explicit X4 restore and foreground action

Offline candidate only. No real restore, foreground change, capture, input, or
game launch has been performed to qualify it. It changes which window has focus,
so call it only deliberately. It is never called automatically by connect,
queries, capture or dialogue input.

The supported route is `FocusPort(existing_client, existing_task_journal)`,
`ticket = port.prepare()`, then one explicit `port.execute(ticket)`. Preparation
reads only OS/profile/session-journal state. It does not ask the minimized game
for an MD reply. `status(ticket)` exposes retained evidence without retrying;
`close()` retires this owner. The caller uses fresh, independent checks for every
subsequent game action; a focus receipt does not authorize any such action.

Observable acceptance and fallback, per link:

| Link | Evidence and confidence | Failure disposition |
|---|---|---|
| Exact game process/profile | Existing connected `ProfilePeer`, executable path and creation FILETIME; 99% source/host design, runtime untested here | Refuse before any native write; never select by title or PID alone |
| Unique eligible HWND | Shared `window_for_peer` independently checks process incarnation before/after enumeration; 95% conservative source design | Foreign/ambiguous/hidden/disabled windows refuse; no HWND guessing |
| Restore request | `ShowWindowAsync(hwnd, SW_RESTORE=9)` once; 99% documented API semantics | Boolean acceptance is not completion; poll actual state for at most two seconds, otherwise uncertain |
| Foreground request | `SetForegroundWindow` once if needed; 99% documented API, actual OS eligibility unknown | OS denial or focus race remains uncertain; no Alt keys, AttachThreadInput, lock changes or replay |
| Current world after restore | One supported `BoundPipe.ask('ping')` after observed foreground; exact helper build/native stamp/peer/load checks | A changed or unavailable native session refuses continuation; never silently reconnect or renew identity |
| End state | Same window, restored, foreground, independently observed bound ping; sampled/non-atomic | Completed means this observation only, never gameplay or rendered-choice success |

The shared resolver's default shape and capture refusals remain unchanged. Only
explicit `allow_minimized=True` returns `minimized` and
`scope=OS_WINDOW_METADATA_NOT_CAPTURE_QUALIFIED`. Zero dimensions are allowed
only while minimized in that mode. Such metadata cannot qualify a clip.

Preparation expires after five host seconds. Restore/foreground observation
loops are bounded; the entire successful attempt must finish within fifteen
host seconds, including the single bound read. Underlying pipe timeouts still
apply. Journals consume the ticket before native dispatch and preserve separate
attempt/return flags. Exceptions after an attempted restore or activation yield
unknown, with dependent actions blocked. This is not atomic HWND targeting:
Windows can destroy/reuse a window or the user can change focus between samples.
No automatic compensation touches other applications.

Official sources (READ):

- [ShowWindowAsync](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindowasync): posts a show-state request without waiting for completion.
- [ShowWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow): defines `SW_RESTORE` as 9.
- [SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow): foreground activation is restricted and can be denied.
- [GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow): samples the current foreground, which may be absent during transitions.

MEASURED offline qualification, with distinct scopes:

- 124 passed, one skipped, child exit 0. This comprised
  31 focus controls and 93 unchanged clip/clip-port controls. The only skip was
  the existing generated-pixel FFmpeg test lacking an explicit executable path.
- 37 focus controls plus that one generated-pixel
  encoder control passed (38/38, zero skips, pytest 2.02s, child exit 0). The six
  additions cover actual BoundPipe envelope acceptance/rejection and production
  backend/clock injection refusal. No unchanged 93 controls were rerun.
- final 38 focus controls passed, zero skips,
  pytest 0.97s/child exit 0. An added counterexample makes a successful window
  observation itself exceed two seconds; the final source rejects it before
  foreground dispatch. This closes a self-review deadline gap without changing
  the shared clip code or rerunning its unchanged controls.
- The union is 132 distinct passing controls. The first run remains historically
  124/1; its skip is resolved by the explicit later test, not silently relabeled.
- Encoder SHA256 `b90225987bdd042cca09a1efb5e34e9848f2d1dbf5fbcd388753a44145522997`
  matched before/after. The encoder generated a synthetic test pattern; it never
  captured a window, desktop or audio.

Final source SHA256:

- `_livefocus.py`: `c3654f9b17c8955997380a4280544860b636c805dfdd59b58dac1d22e4b15110`
- `test_livefocus.py`: `d48e707f9c7fc573672287fdb0ae5c0fe6c05183df4d8f2d754e159e329dcd70`
- `_liveclip.py`: `e6bc60cb777a9299d825fd292416d1cc2ab90f9cb6598337c6166d3d4ba72c49`
- `test_liveclip.py`: `c0625638a4a986eba2b4950e850af348f4f8d737d1aedce197cf73cf9d04bdc7`

Synthetic native-return controls do not prove OS focus eligibility or X4 wake-up.
Independent services-lane READ review closed on the final source and shared
resolver delta with no remaining concrete blocker; no duplicate tests were run.
The existing input and default capture contracts remain unchanged. A live port
created before the shared resolver's source change must refuse its stale source
pins; a newly constructed owner binds current sources. Runtime remains unqualified.

# Native save/load adapter qualification

Experimental, not integrated or runtime-qualified. Import and construction make
no native calls. No game saves, profile files, deployments or game runs were
performed for this unit. The trusted host reservation and approval bridge is
**not implemented** by this Lua module. Missing callbacks refuse construction;
a caller JSON receipt or boolean is not an authenticated host capability.

## Source evidence and confidence

READ from game-owned 9.0 reference; no game code is redistributed.

| Source | SHA-256 |
|---|---|
| ui/addons/ego_gameoptions/gameoptions.lua | cff978c89336297ce182ad0b0fb6937c994448396f53359be70efa94fc3ee58f |
| ui/addons/ego_detailmonitorhelper/helper.lua | d24a08b8da9f2c972794b60acb48ae36f38cb026c991249dab9f1164272d4df2 |

- Native save call, **98% source confidence**: gameoptions 9290–9314 calls
  SaveGame(slot, displayName). Empty slots use save_ plus their three digits.
  Existing slots take a different overwrite-confirmation path (3253–3280).
- Native standard filename set, **99% source confidence**: Helper 881–889
  supplies ten manual slots, three autosaves, quicksave and online_save.
  The adapter accepts only save_001 through save_010 for its own writes.
  A unique display label is not a new arbitrary filename.
- Catalogue refresh, **97% source confidence**: gameoptions 13143–13157 calls
  ReloadSaveList then waits for IsSaveListLoadingComplete before GetSaveList.
  The adapter exposes refresh/observe; it never copies the native busy loop.
- Saveability, **97% source confidence**: gameoptions 6241 uses
  IsSavingPossible(false). Online mode is refused; Helper 11196–11198 wraps
  OnlineIsOnlineModeActive. This avoids bypassing the online inventory-loss
  question in gameoptions 8023–8047.
- Deferred load, **97% source confidence**: gameoptions 2955–2964 checks
  IsSaveValid and schedules LoadGame after 0.1 seconds. The adapter uses the
  same Helper delayed callback. Helper 940–953 restores input after return;
  the adapter catches its own callback failures so it does not strand input.
- Native catalogue completeness and real callback availability remain
  **runtime unverified**. Synthetic controls do not increase these ratings
  into engine measurements. No rendered UI or originating-menu test is claimed.

## API and boundaries

Factory dependencies: actual globals, C, Helper; canonical current(), monotonic
now(); trusted reserve_save, authorize_save, settled_save, authorize_load and
verify_load. Native dependencies and host callbacks are immutable integration
inputs. No arbitrary evaluation, overwrite, import, automatic retry or automatic
save-then-load chain exists.

1. refresh() requests a native list refresh. observe() returns the entire bounded
   native standard catalogue only after loading completes. Each row's original
   fields are retained. Unknown/sparse/duplicate/foreign filenames, nonfinite
   numbers or overflows refuse; missing data never becomes an empty catalogue.
2. prepare_save(id32hex, slot) requires a new ticket, native saving enabled and
   the slot and unique x4live-ID label absent. It retains the complete catalogue,
   current five-field public stamp and a host-owned opaque reservation handle.
3. execute_save(ticket) rechecks catalogue, saveability, offline state, session,
   age and the host gate. It consumes the ticket before the gate/native call.
   SAVE_REQUESTED is not success. A possible native call followed by failure is
   UNKNOWN; the request cannot be resent.
4. Caller refreshes and polls poll_save(ticket). A matching valid native row
   means WAITING_HOST_SETTLEMENT until the trusted host verifies actual settled
   bytes. SAVE_COMPLETED stores historical evidence, including path/hash/size,
   exact slot/label/ticket/stamp and native row. It is not a promise those bytes
   remain current; load performs a separate fresh host verification.
5. prepare_load(newTicket, ownCompletedSaveTicket) accepts only this instance's
   completed save and unchanged native identity. execute_load requires separate
   fresh host authorization, consumes the ticket and schedules one callback.
   The delayed callback rechecks eligibility, session, catalogue and host bytes.
   LOAD_REQUESTED or LOAD_UNCERTAIN never claims the new world loaded.
   A new canonical session and independently correlated save identity are needed
   after the transition; connection loss is neither success nor failure.

Bounds: 64 lifetime tickets; 300-second preparation lifetime; catalogue 64 rows,
4096 values/keys, depth 8, 64 KiB aggregate strings and 8 KiB per string. Observations
are non-atomic. Full catalogue observations may need the existing bounded paging
transport; this module does not claim its full table fits one wire response.
Status retains load_completed=false. dispose invalidates pending callbacks.
Status is historical ticket state, not a fresh canonical-world observation; the
outer router must qualify its delivery against the current public session.

## Required host interface and unresolved integration work

These callbacks are injected trusted code, not values accepted from an action's
arguments. Tests use explicitly synthetic stubs. The production implementation
must bind canonical process, profile, session, request digest and exact slot.

- reserve_save(context): reserve a task-owned journal identity, not a placeholder
  file in the save folder. Resolve the actual profile/save directory and reject
  links/reparse points and aliases. Check both .xml and .xml.gz representations
  and every slot-related member; fail on unknown variants. Capture exact existing
  save membership and hashes. No overwrite is authorized even if the native
  catalogue missed a file.
- authorize_save(context, opaqueHandle): verify the reservation against current
  process/profile/session, repeat absence/preservation checks and obtain the real
  pre-operation guard result for the exact operation and complete path scope.
  Require actual user save approval bound by the host; never synthesize transcript
  approval or infer it from test-plan approval. A policy allow is not a lock or an
  exclusive filesystem reservation. Concurrent writes remain a disclosed risk;
  detected drift must stop the request.
- settled_save(context, handle, nativeRow): read-only, bounded polling after the
  request. Require exactly the expected new save representation, successful full
  parse/decompression and repeated stable identity/size/hash across a documented
  settlement interval, plus the unique native catalogue label. Recheck preexisting
  saves and unexpected members. An event_game_saved success can supplement this;
  this module does not subscribe to it and never fabricates event evidence.
  Return nil while genuinely pending, fail on corruption/drift. Return the bound
  receipt only after all checks; do not trust a caller-supplied hash.
- authorize_load(context, completedReceipt): separate fresh approval, current
  exact file hash and permanent preservation policy qualification where required.
  Return a new opaque capability; never reuse save authorization.
- verify_load(context, capability, receipt): repeat identity, file and approval
  checks at the delayed dispatch boundary. No automatic load after saving.

READ of the existing toolkit ADAPTING.md 208–268 and tools/x4_harness/save_policy.py
shows supported Action v1 pre-operation checks, permanent verified preservation
for recognized existing saves, and strict refusal for unresolved/opaque scope.
Those APIs do not implement an exclusive native-save reservation or authenticate
a remote Lua caller's JSON receipt. A path-only policy check cannot be described
as qualification of all engine side effects. The actual integration must use the
supported host action/guard route and retain the complete result and transactions;
no new guard policy or bypass is proposed here.

## Verification

Actual LuaJIT runs with synthetic native/host functions check successful phase
separation, catalogue/session/time drift, occupied slots, missing identity,
invalid host receipts, deferred refusal, consumed requests and post-dispatch
uncertainty. No production host gate or native engine call is exercised.

Initial run: 37 passed, 1 failed due to a test stub using Lua's
flag-and-false-or-table idiom; corrected to an explicit branch. The subsequent
38-control run passed with zero skips (18.76 seconds, 50 MB peak, resource wrapper).
Caught LuaJIT errors on Windows emit misleading fatal-exception banners under
pytest's faulthandler. Controls now execute in bounded child processes and require
all assertions, the completion marker and exit zero; exceptions are not ignored.
Final additional controls cover approval nonreuse, gate-induced stamp drift,
duplicate deferred callbacks, ambiguous scheduling and missing refresh.

Final run: **43 passed, zero skipped**, 22.60 seconds, 50 MB peak under the
resource wrapper (job ebf56ce8d9ee). The duplicate callback also preserves the
original possible-dispatch status. These are synthetic control-flow checks,
not engine, filesystem settlement or authenticated host-transport qualification.

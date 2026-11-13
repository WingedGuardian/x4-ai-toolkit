# Native transaction observation qualification

This is a read-only build-queue adapter, not a purchasing, repair or refit API.
Its native contract was read from a local X4 reference. Synthetic tests execute
the actual module in LuaJIT with FFI buffers and stub native functions. They do
not prove engine ABI compatibility, menu parity, queue completion or gameplay.

## Integration contract

Load `ui/live_transactions.lua` and call `new{ffi=ffi, C=C,
resolve_issued_station=resolver, clock=getElapsedTime}`. The resulting object
has only `capture(token)`. It performs no registration, transport or game writes.

The resolver MUST accept only an ID previously issued by the current helper
generation, verify it still denotes an existing station, and return its native
uint64 cdata; otherwise return nil. It is called again at the end. The caller
must keep this entire synchronous capture in one helper generation. A same-ID
reissue across a reload is not independently detectable by this factory.
Numeric Lua IDs are refused, including seemingly exact small doubles.

The injected native interface is limited to:
- `GetNumBuildTasks(station, 0, isinprogress, true)`;
- `GetBuildTasks(buffer, capacity, station, 0, isinprogress, true)`.

Both active and waiting phases include upgrades. Each phase allows at most 32
rows; overflow is reported before allocation/fetch. A complete return requires
all observed counts to agree before/fetch/after, valid fields, no duplicated task
identity within/across phases, a still-valid station binding and finite ordered
timestamps. Zero counts are read twice. A failed count is never an empty queue.

Returned `status` is `refused`, `partial` or `complete`.
`enumeration_complete` means these bounded native reads succeeded, not that a
global or atomic census was obtained. `atomic=false` is unconditional. Equal
counts can conceal membership changes; a task can move between phases or finish
during observation. A task absent later is NOT proof of completion or cancellation.

Rows carry phase, exact task/container/component/builder uint64 strings,
macro/faction strings, exact signed int64 price string, missing-resource boolean
and queue position. Zero component/builder handles are preserved, not promoted
to known ships. Zero task/container IDs, null/empty/unknown text or malformed
fields make the capture partial. Empty macro semantics for upgrades have not
been qualified in the engine; these currently produce an explicit unknown row,
not a fabricated macro. Text is bounded to 256 bytes and control characters are
refused. Up to 64 rows and two phase records are returned. The transport owner
must still apply its payload/escaping limits and fail explicitly on overflow;
this module does not serialize, paginate or issue the observed component IDs.

FFI declarations reuse native `UniverseID`, `BuildTaskID` and `BuildTaskInfo`
types if already present, rather than introducing incompatible anonymous aliases.
Existence alone is insufficient: before any native query or queue allocation, ID
aliases must be unsigned 64-bit; task size, alignment and every field offset must
match a private primitive-only comparison layout. Field cdata types, bool shape
and an unsigned queue-position boundary roundtrip reject same-sized signed or
floating substitutes. The private comparison type is NEVER passed to a native
function. A single probe of the existing type is allocated only after its size
and alignment match. Missing declarations are installed with the source-read layout. Unavailable
declarations/native functions are refusals or partial results. Native code must
honor the passed array capacity and supply valid NUL-terminated pointers; Lua
cannot safely recover from an engine memory violation. Synthetic stubs do not
prove that ABI boundary.

## Source provenance (READ)

Reference-relative paths, SHA256 and relevant lines:

| Source | SHA256 | Contract |
|---|---|---|
| `ui/addons/ego_detailmonitor/menu_ship_configuration.lua` | `a18af705ec02eb1a0a0bef1f0e035eb26d418c8d2009514c578f03ec237cc707` | BuildTaskInfo 43–53; GetBuildTasks 502; GetNumBuildTasks 569; active/waiting native loops 7947–7965 |
| `ui/addons/ego_detailmonitorhelper/helper.lua` | `d24a08b8da9f2c972794b60acb48ae36f38cb026c991249dab9f1164272d4df2` | Shared loadout buffers 9142–9196; conversion 9364–9622; temporary FFI ownership 8956–8970 |
| `aiscripts/build.shiptrader.xml` | `b99f017c77605f9fa0e4b121629343afa33ed49c4bd410235f86224754c03cbd` | Dock/parked repair eligibility 29–32; repairs 63–73; refit/payment settlement 678–737 |

This records three inspected sources, not a whole-corpus assertion. Reference
bytes are not distributed. Source-contract confidence is 98%; runtime integration
remains unverified until a qualified disposable-world observation.

## Deferred writes: preserve the full native transaction

The native menu's purchase entry contains a complete upgrade/crew plan,
settings, quantity, prices, warnings and name/paint selection (2235–2267).
`buttonConfirm` checks available funds and transfers payment FIRST (1800–1822).
`repairandupgrade` then calls `AddBuildTask6`, and records transferred money
only if the returned task is nonzero (11452–11455). The visible callback has no
local rollback for a zero result. Never expose AddBuildTask6 as an arbitrary
passthrough or retry a transaction after a lost reply.

A future narrow commit must bind a short-lived immutable quote to exact station,
ship/macro and loadout; recheck eligibility, funds and prices; submit only once;
retain exact task identity and payment readback. A partial/unknown result must
remain consumed until reconciled, not be reported as an unacted refusal. No
automatic refund, cancellation or retry is proposed here.

Native quote calculation includes hull, equipment additions/removals, repairs
and separate crew, followed by RoundTotalTradePrice (7579–7640). Software uses
discounts/container factors (11145–11153); repairs use GetRepairPrice and repair
discounts (11177–11183). Licence/research/limited-ware checks are at 7222–7311;
build-module/loadout gates at 7689–7707; existing Equip/Repair checks at 4625–4639;
mandatory equipment, crew and money gates at 7980–8039. The confirmation button
requires no critical errors (8155–8170). These checks live outside the submission
callback. Calling a callback with incomplete synthetic menu state is not parity.

Missing resources are a warning, not rejection: purchase resource pair
7830–7862; refit resource pair 7885–7923. Native Helper loadout functions can be
reused at runtime, but their temporary FFI storage is cleared after callbacks;
do not retain raw pointers as quotes. Named menu functions depend on extensive
local state and are not standalone transaction APIs.

Repairs have two native routes (11386–11415): a docked ship receives
`repairs_initiate`; an undocked ship gets a Repair order with destination, hull,
selected components, accepted cost and other parameters (1760–1786). A raw signal
would omit commercial checks/payment. Refit uses the build task and later native
settlement, including additional payment, crew failures, refunds or aborts.
Task disappearance, a successful signal or order creation alone cannot prove
completion.

Future writes must extend explicit helper AND CLI write vocabularies, preserve
first-row write advisories and the one-send/no-retry contract, and have dedicated
receipt validation. They must not be tunneled through the read-only query verb.
No purchase, refund, repair, refit, movement, pause or save write is implemented
by this module.

## Offline controls and remaining runtime work

`tools/x4validate/tests/test_livetransactions.py` executes the actual Lua module.
Controls include >2^53 and uint64-maximum IDs, signed price preservation, active
and waiting query flags, all-zero queues, cap overflow, invalid counts, native
failure, returned count outside allocation, count drift, null/empty/unknown text,
duplicates across phases, clock reversal, revoked IDs and native type reuse.
Assertions run in bounded child processes: LuaJIT's ordinary protected Windows
exceptions otherwise trigger pytest faulthandler banners despite successful
pcall handling. Child timeout, nonzero exit and assertion failures still fail.

Initial direct run: 37 passed, no skips; protected SEH banners were noisy but exit
was zero. Final child-process qualification: 37 passed, zero skips in 10.37 s;
resource-budget admission GO, child exit 0, peak job memory 49 MB. The wrapper's
implausible CPU peak is not treated as reliable compliance evidence. Command:
`python -B -m pytest -q -p no:cacheprovider tools/x4validate/tests/test_livetransactions.py`
(through the resource wrapper, with bytecode writes disabled).
These checks are synthetic; runtime confirmation still needs the installed
module, issued station resolution, an empty queue and a known populated queue,
with independent task identity/phase observations. No live command was run while
preparing this adapter.

Review correction qualification: 56 passed, zero skips in 13.32 s, resource
admission GO, child exit 0, peak job memory 50 MB. This includes 18 incompatible
predeclarations (ID aliases, truncated/expanded/packed structures, reordered
fields and incorrect field types) that must make zero native calls, plus an
exact existing-layout positive. An initial expanded run failed 18 controls in
the Python dispatcher before reaching Lua because it split a single string
parameter into characters. The dispatcher was corrected and all 56 rerun; those
initial failures are not evidence about native type rejection. Complete still
means a successful non-atomic observation, never proof that the queue remained
empty or that a missing task finished.

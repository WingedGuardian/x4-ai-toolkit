# Read-only provider usage samples

This offline successor adds actual retained MD counts to the existing CAM
`camera_observe` snapshot and fixed empty-argument `capacity_observe` actions to
the capture and room-entry providers. The actions use the existing SDK and MD
channels. They do not capture an image, start a camera, enter a room, allocate an
MD operation record, or clear a busy reservation. They do allocate their own
Lua/SDK request record and one transport call. Reply queue bookkeeping remains.

The source analogue is the existing `X4ToolkitOrbit.Snapshot` request/reply cue,
including its public-state and request correlation gate. Capture and room entry
use separate fixed sibling cues so their observation cannot fall through to
preparation, room enumeration, release or movement. Each has an unconditional
first-action TRIGGER marker. No general MD execution interface is introduced.

The MD sample contains used records, the source-bound limit, busy state, reply
queue occupancy before appending this reply, the queue limit, and the selected
canonical capacity name/revision. Capture reads its explicit `$Count`; orbit
and room entry read their retained `$Records.count`. The public session,
generation, world token, public revision, MD revision, request/digest and call
must agree before the Lua provider accepts the sample. Actual MD player age is
retained separately from UI and host monotonic time. Missing/malformed state is
unavailable, never a zero inferred from a fresh UI instance.

MD counts include records from previous UI generations in this loaded world;
the MD scripts reset them on game start/load. Lua history and call counts instead
belong to the current provider instance. A sampled busy flag is an observation,
not permission to release or cancel anything.

`CapacitySamples` uses an actual `AutomationClient` and its existing action
journals. `start(kind)` locally prepares a new request, `execute(handle)` sends
its one MD read, and `status(handle)` reconciles without resending. A completed
sample is retained through an exact journal path/hash; the collector does not
duplicate the payload. Its opaque handles are bound to its client, profile,
process/load identity, expected helper build and frozen provider/MD sources.
The selected SDK history limit also bounds collector records.

Terminal SDK status returns the original completed result without calling the
provider again. Consequently `native_observation_cached=false` in that original
payload is **not freshness proof**. The collector always labels completed data
`RETAINED_SAMPLE_NOT_FRESH_STATUS`, reports host sample age, and leaves
`current_counts_verified=false`. A new observation needs a new request/call.

After the latest three samples complete, `capture_global_usage(ReadEvidence)`
issues the fixed `automation_capacity` read on that same client. The known local
call sequence establishes that the global counters include the sampler's own
requests. Later collector operations or known-provider action-journal membership
changes invalidate that ordering. This is not an atomic snapshot, a foreign
writer lock, a reservation, or a security boundary against arbitrary same-user
code. MD/UI clock values are not used to invent cross-domain ordering.

The planner accepts only `(CapacitySamples, kind_to_opaque_handle)` for these
values. It can consume the collector's ordered global evidence or retain the
explicitly unordered legacy global-evidence input. Static limits remain
DECLARED; actual counts remain sampled MACHINE_OBSERVED or SYNTHETIC. It reports
busy providers and keeps `NOT_READY` for missing private-provider demands,
unknown byte budgets or unqualified concurrency. No whole-campaign fit follows
from a larger count ceiling.

Stage plans must explicitly declare `capture_observations` and
`room_observations`, alongside the existing `orbit_observations`. Each future
observation costs one SDK/Lua record and one transport call, with zero MD
operation records. Missing fields refuse rather than defaulting to zero. The
three already-completed pre-stage samples are included in measured usage and
are not added again as future demand. Additional refreshes must be declared.

Source confidence is 95% for the copied existing queue/SDK mechanism and its
source-derived count costs. This assumes the deployed source and native MD
event delivery match their bound identities; runtime delivery remains unproven.
These are counts and value charges, not RSS measurements. Engine
delivery remains runtime-unqualified. Offline controls use actual generated Lua,
SDK/router/codec, host client and journals with synthetic native data. A grouped
canonical schema check is required for both profiles of all three changed MD
scripts before this successor is frozen. No game/deployment is performed here.

Offline qualification completed: 332 targeted controls passed with zero skips;
six fresh canonical MD trees (three providers, two profiles) had zero findings
or skipped checks. Tests execute the generated Lua and real host journals with
synthetic native replies. This proves the scoped code/schema behavior, not
engine delivery, an installed combined bundle, or whole-campaign feasibility.

# Native adapter local usage

This offline candidate adds the fixed, zero-argument `native_adapter_capacity`
bound read. It reports 11 local budgets from ten actual adapters. It does not
reset, widen or evict any history, query the engine, create a ticket or capture,
or prove future campaign fit. Engine/runtime qualification remains pending.

| Adapter | Actual allocation | Unchanged limit |
|---|---|---:|
| Menu | Retained successful prepare tickets, shared by open/select/add/confirm | 128 |
| Options | Retained successful prepare tickets, shared by open/submenu/select | 128 |
| Orders | Submission tickets; separate cancellation tickets | 128 each |
| Time control | Retained prepare tickets | 128 |
| Player navigation | Retained prepare tickets | 256 |
| Menu pages | Eligible capture attempts, including failed model reads | 128 |
| Player state | Eligible capture attempts | 256 |
| Build tasks | Eligible capture attempts | 128 |
| People | Eligible capture attempts | 128 |
| Equipment modifications | Read attempts, charged before kind/subject validation | 4096 |

READ evidence is the lexical allocation gates in the ten `live_*.lua` modules,
with the same constants shared by each gate and its new copied getter. This is
not a corpus-wide statement about optional extensions or private providers.
The assembly now has 22 fixed read routes across its existing 18 module entries.
Its native wrapper request budget remains separate: wrapper preparation can
consume a record even when the inner adapter refuses before retaining a ticket.

Cached state/build-task/people metadata and pages do not allocate another capture;
repeating their currently retained capture nonce returns that cache. Menu capture
requires a fresh nonce. `buildtasks_task` is a separate current native point read.
Each new outer read capture still consumes its normal router read nonce and
retained charge. Generic transport pages do not create an adapter capture.
Screenshots, menu captures and native wrapper requests are different budgets.

The getters perform local reads only and return fresh tables. Four existing
retirement paths overwrite their counters with a refusal sentinel; telemetry
reports `available=false, used="UNKNOWN"` after retirement rather than treating
that sentinel as measured usage. Cached public stamps are copied and explicitly
historical. The menu pager retains an opaque model fingerprint, not a separately
qualified current public stamp. The owning assembly's existing before/after
session gates apply to the encoded read. Sampling is sequential, not atomic;
counts alone do not prove that the underlying UI or native world is usable.

No planner readiness promotion is included. A future stage compiler must declare
demands for these individual budgets and account for pending/concurrent work;
it must not infer every inner cost from a blanket `native_requests` count.

MEASURED qualification: 673 affected controls passed, zero skipped (child exit 0; 165.70 seconds in pytest). The 12 selected files cover
the ten adapter suites, assembly and 42 new capacity controls. The new controls
include actual ten-adapter construction/read, copied counters and cached stamps,
failed-attempt charging, separate order histories, retirement UNKNOWN, malformed
counter refusal, and dense encoding. The actual SDK/router/codec, BoundPipe and
AutomationClient paged chain consumes one router read nonce and 64 bytes of its
retained identity charge per collection, zero SDK action records, zero adapter
allocations and zero synthetic native calls. Generic page retrieval does not
increase that read count. These are fixture measurements, not native game data.

Independent READ review is clear. It caught an equipment getter `reads` versus
assembly `attempts` mismatch before the full passing run; the assembly now uses
the actual getter key. Source confidence is 98% for this mechanical local
telemetry, assuming the existing bound transport/source attestation and owning
assembly lifecycle. This is not a confidence claim about engine operation.
Tests use synthetic native functions, never a running game. The three MD
generators and canonical profile source are unchanged from the six-tree usage
qualification; this telemetry adds no MD source or new schema claim.

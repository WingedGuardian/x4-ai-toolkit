# Offline campaign capacity

The trusted `campaign_v1` constructor profile raises host capture records and
directory members from 512 to 2048, native-provider history from 128 to 512,
and diagnostic room-entry history/calls from 64/256 to 256/8192. Selection is a
fixed name from `_livecapacity.resolve`, never a caller-supplied numeric cap.
The host requires campaign capture receipts to carry the exact canonical profile,
revision, source hash and limits before accepting native preparation. Present
capacity metadata is validated in both profiles. Only legacy interactive receipts
may omit it or carry both explicit `UNBOUND_LEGACY_INTERACTIVE` source markers;
neither supplies canonical source binding. Mixed markers refuse, and count/limit
checks still apply when that legacy metadata is present.
Interactive defaults remain unchanged. Image retention stays at 8 GiB, raw
camera evidence at 16 MiB (128 KiB per sample), and clip retention at 128 records
and 2 GiB with a 512 MiB pre-effect reservation per clip.

The room loader binds the selected profile/revision before acquiring its channel.
Its MD selected-record count and Lua attempt history share the profile ceiling;
they are different denominators. Discovery attempts consume Lua/SDK records but
do not create selected-room MD records. Sixteen candidate rooms/navcontexts and
64 rooms per context remain bounded. Historical interactive loaders may omit the
profile/revision; campaign loaders require both exact values. Runtime wiring is
not qualified by these offline changes.

Released captures retain every consumed ID, record, receipt and raw journal. Only
after qualified native reservation release, the two full RAM directory frames
become immutable path/hash/size references to their existing before/SETTLED
journals. Historical access checks the owner, exact path, file bytes, request and
identity again. Active frames remain in memory. A 32 MiB logical JSON charge
refuses further unreleased before-frame retention before capture reservation;
this charge is not process RSS. Individual frame journals are bounded at 16 MiB.
Journals are neither deleted nor rewritten. Their disk growth remains quadratic;
it is separate from the image-byte quota and must fit the task output disk.

MEASURED controlled representation: 128/256/512 historical capture pairs retained
16,384/65,536/262,144 file rows. Full mappings increased process working set by
6,295,552 / 24,571,904 / 94,457,856 bytes. Actual reference objects instead retained
zero historical file rows and increased it by 253,952 / 995,328 / 2,326,528 bytes.
Historical serialized frame bytes remain 4,815,873 / 19,167,233 / 76,476,417 on
the modeled journal side. This measures frame representations, including allocator
effects; it is not the RSS of a full capture process, a screenshot benchmark, or
a guarantee that an entire campaign fits. Other records and camera evidence remain
bounded separately.

`_livecaptureplan.evaluate` reports per-quota count/byte headroom. It includes two
SDK requests per capture (capture plus release), separate room discoveries and
selected entries, transport-call minima, byte ceilings and the clip reservation
needed before the last planned clip. Future costs are **DECLARED**, not measured.
At least two camera read nonces per capture are required; this is not a complete
read budget. Additional native-save-path reads and polling need explicit allowance.
Missing current native counters and unspecified image/camera/clip byte bounds are
**UNKNOWN**, never zero. Exact frozen generator receipts can bind MD/Lua limits;
they cannot establish current native usage. The fixed `automation_capacity`
read can supply sampled SDK/router/native counters through an actual local
`ReadEvidence` handle, with explicit expected build/provider revision. Its
sequential observations are not atomic or future reservations. CAM/CAP/room MD
usage remains unknown from their static receipts, so the planner retains
`NOT_READY` while any required current usage is unavailable.
It never grants runtime authorization, reserves resources, or reports an overall
PASS. A count ceiling of 128 does not promise 128 clips under the byte budget.

Qualification receipts record exact sources, test counts and schema trees. All
tests use synthetic native plumbing/files; no game, screenshot, clip or room
transport is executed by this unit. Runtime approval and exact installed-source
qualification remain separate.

Offline qualification: 194 targeted controls passed with zero skips, including
the actual generated counter route through the SDK/router and host evidence
collector, with synthetic native values. Both room MD profile variants separately
passed canonical schema validation with zero findings/skips; the final host run
rechecked and reused that exact source-bound proof. This is not runtime or whole
campaign capacity acceptance.

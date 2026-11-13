# Native surface sample — offline qualified, runtime unqualified

Source-contract confidence 95%; native behavior unqualified. The optional provider
requests one engine find_object_surface ray for an issued owned, stationary ship
in visible attention, current player sector and ready physics. It copies the
object-relative shape from move.claim.xml50–52 and the physics/null handling from
boarding.pod.xml48–63. Target movement/world changes refuse a qualified sample.

surface_sample takes only ship and a fixed face: xplus/xminus/yplus/yminus/
zplus/zminus. Seed positions derive from the engine's macro bounding-box centre
plus/minus twice its actual width/height/length. Returned surface XYZ/rotation
basis are explicitly target-object-local; host position/basis are separately
sector-relative. A null hit is one unsuccessful ray, never proof of absent
collision geometry. No movement, hull-membership result or collision safety is
inferred. Surface presence is different from rendered mesh correctness.
Dimensions must be positive and at most 100km; macro centre coordinates are
bounded to 10000km. These are explicit query limits, not geometry-population
claims. The origin and face are request-derived, separately labelled from the
native returned point. Native Pirate story_criminal.xml2761–2769 supplies a
position-value surface caller and uses its result on the same component; the
reader explicitly sets input object/space to that target using the documented
common position-group contract 7273–7287. Exact in-engine seed interpretation
still needs a translated/rotated native control; source evidence is not that test.

Preparation is local bookkeeping. Execute consumes once before its sole MD
sample request; status returns cached evidence and never resends. The MD side
retains 128/512 request identities using the canonical interactive/campaign_v1
native_history profile, validates public stamp/revisions, and rejects duplicate
samples. Lua retains the same bounded history and 8x call budget. Capacity sampling
has its own action and does not allocate a native sample row. Queue 64 and 10UI-
second transport bounds remain unchanged. There is no automatic retry/eviction.

The shared channel's full id/digest/call/phase/world/revision header is checked
before omitting duplicate enclosing headers from the bounded SDK result. Max-
uint64 identities stay typed cdata. Numeric lists, basis vectors, unknown fields,
late replies, identity/target drift, duplicate batches and capacity exhaustion
must fail closed. Constructor attaches only instrumentation; no native query.
The outer SDK can cache a terminal response unchanged: its original local
native_observation_cached=false is historical metadata, not a fresh status
sample. Use native age/request identity and a new explicit request for new data.
Any observed post-binding identity loss permanently retires this adapter and
settles its outstanding request once, even if another request noticed the loss.

The generic native reader is useful for collision-path planning and camera/surface
placement on multiple mod hulls. It is not a whole-hull geometry decoder, an
inside-hull classifier, a camera-pose measurement or a successful collision test.
Actual engine frames, null controls, native versus disabled-collision controls,
trajectory sampling and visual review remain separate runtime qualifications.

MEASURED synthetic controls:49 passed, zero skips. Actual generated
loader, SDK, native-channel boundary, router and codec preserve max-uint64 target
identity and dense numeric samples, both 128/512 history limits, null-ray distinction,
world/target drift, malformed replies and cross-request fault settlement. A first
admitted run passed two controls then exposed a test expecting a Lua table from
the router; the actual router returns encoded JSON. The corrected test decodes
both envelopes. Production source did not change for that correction.

Composed controls:143 passed, zero skips. Ten real optional
descriptors register 12 extension providers plus the native core provider, with
no constructor game calls, both trusted profiles and retired callbacks. Source
identity and factory-byte equality are checked. Historical test function names
containing nine are retained; their actual expectations are now ten descriptors.
The preceding 78-pass/one-failure run caught the stale 9-descriptor assertion.
These groups overlap and must not be added as a unique test denominator.

Canonical MD qualification at source checkpoint 8e2b7e2 compiled both profiles,
six queries each, with zero findings/skips (; local receipt SHA256
d41022986ad80858af7e47e497fb23c5337fc053dfe96489bbdc9e65d3224562).
All source/compiler pins and regenerated MD bytes matched. Independent READ
review cleared the source and five boundary/dense test additions. Admission had
initially held the test job at the game RAM/commit floor; that historical attempt
launched no pytest. After memory recovered, fresh admitted checks produced the
results above. No retry remains queued. No game E2E or release CI is claimed.
No generated XML, installed file, profile, save or game action is applied here.

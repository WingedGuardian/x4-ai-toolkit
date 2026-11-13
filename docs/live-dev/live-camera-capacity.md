# Fixed camera and still-capture capacity profiles

Status: offline component qualification passed: 153 distinct controls and four freshly compiled canonical MD variants, with zero skips or schema findings. Native delivery and campaign capacity remain unqualified. No game action or generated game file is written by these pure factories.

The shared `_livecapacity.py` is the authority for `interactive` and `campaign_v1`. Python generators consume its fresh profile and emitted Lua resolver. Unknown names and numeric caller overrides refuse. Selection occurs in trusted generation/constructor code, never in an action packet. The generated provider and MD revisions include the exact capacity source and selected profile. This is an in-process source contract, not security against arbitrary same-user code.

`live_orbit_extension.prepare(capacity_profile=None)` and `live_capture_extension.prepare(reference=None, capacity_profile=None)` preserve interactive defaults. Capture `prepare_parts` takes the same optional profile. Campaign attachment requires the bundle context's exact `capacity_profile` and `capacity_revision` before allocating its MD channel. Legacy nil context fields are accepted only by an interactive loader. Provider construction reads no native identity or game state; first binding retains the existing lazy-handshake behavior.

| Retained boundary | Interactive | Campaign v1 |
|---|---:|---:|
| Orbit Lua request history / MD mutation history | 64 | 2,048 |
| Orbit transport calls | 256 | 16,384 |
| Capture Lua request history | 512 | 4,096 |
| Capture MD capture records | 512 | 2,048 |
| Capture transport calls | 2,048 | 16,384 |

A capture and its release consume two distinct Lua/SDK request records; preparation, execution and release each consume transport calls. Polling consumes additional calls. Camera observation/start/stop requests consume Lua/SDK records, while native mutation records have their own denominator. Reply queues retain their existing 64-row bounds. There is no eviction, counter reset, reload recovery, record reuse or automatic replay to extend a budget. Exhaustion refuses further work while retaining historical owned records.

These are component ceilings, not a claim that all maxima fit simultaneously. The shared SDK/router history and retained-value byte charges, host file/member/image/clip budgets, and compiled stage-cost preflight may refuse earlier. Stage scheduling must count the actual accumulated work and next-stage upper bound, including cleanup and status polling; an unknown bound does not become zero. No whole-campaign fit or runtime throughput claim follows from larger constants.

Receipts expose the full `capacity_profile`, exact `capacity_source_sha256`, and `provider_limits.records/calls`. Orbit retains `md_contract.limits.records`; capture retains `native.contract.max_records`. Consumers can join these values instead of copying guessed capacities. Profile changes alter source-bound revision identity; previously compiled receipts remain historical. Changed MD output requires fresh canonical schema qualification.

The capacity change adds no achieved-camera-pose, image-file completion, visual-quality or engine-delivery proof. Existing uncertainty, exact owned-handle interlocks, consumed-before-dispatch and host settlement requirements remain in force.

Provider results expose `observations.capacity`, and the local provider exposes `capacity()`. These copied counters explicitly say `LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS`. A terminal SDK receipt retains its historical counter snapshot; reading it does not refresh native state or local counters.

The metadata successor also exposes copied canonical `revision` and `source_sha256` values. Generated profiles require both 64-hex identities; only the documented resolver-free interactive fallback reports `UNBOUND_LEGACY_INTERACTIVE`. Host consumers can require the exact profile/revision/source before dispatch instead of accepting matching counts alone. Twenty-four targeted controls passed with zero skips, including malformed identities, both profiles and the actual nested wire envelope with these fields. Caught LuaJIT SEH diagnostic output was emitted and truncated by the transport; the terminal pass count and child exit zero were retained. All four generated MD hashes and their original compiler/schema/source pins remain identical, so this Lua metadata-only successor reuses that canonical schema evidence.

Qualification uses actual Lua providers, the SDK and wire encoder with synthetic native replies. Boundary controls exercise both profiles through the final retained record and transport call, refusal without resend, exact capture/release accounting, source-bound loader checks before channel allocation, copied counters, and the nested wire envelope including added capacity fields. Provider-boundary capture tests deliberately use the campaign SDK ceiling for both provider profiles; the integrated interactive SDK may refuse earlier. The final 20-control capacity run overlaps the earlier 152-control component run, giving 153 distinct controls, not their sum. Four actual generated MD byte sequences (two providers times two profiles) passed the canonical compiler. No runtime calls occurred.

The consumed shared capacity source was `207c9c392a1362d105caade6b092d57e622e3b70327aba1b22040cf886789bc0`. The tested shared SDK/router snapshot accepted a false profile name as its interactive fallback. The subsequent owner correction (`37b1e6ae` / `ab2f9c62`) rejects false and retains nil-only defaulting; inverse byte comparison reconstructs both original consumed hashes exactly, and the owner reports 155 controls passed with zero skips. These providers already reject false names, and their selected string-profile behavior is unchanged by that correction. This evidence does not qualify the full bundle's bound capacity-read route or replace its separate integration gate.

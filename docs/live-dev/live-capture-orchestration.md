# Native still capture orchestration

**Offline qualification, not release/runtime acceptance.** The dispatcher has 39 passing offline integration controls. Its dedicated test file has 35 controls, zero skips. No guard rules changed. Actual native runtime remains unqualified.

Image decoding is optional: the project's `capture` extra pins the Pillow 13 series in the lockfile. Default toolkit dependencies are unchanged. The observer imports Pillow only when constructing its default decoder or decoding an image; missing Pillow raises CaptureUnavailable rather than accepting signature-only evidence. Dedicated tests now cover the missing decoder, PNG/JPEG/BMP/TGA in both candidate roots, file stability and decode races, hard links, identity/receipt mismatches, chronology, timeout, quarantine and enumeration bounds. Narrow independent source review found no blocker; these controls use synthetic receipts and temporary files.

`CaptureDispatcher` combines the existing registered action client and exclusive action journals with a bounded file observer. It does not launch the game, fly a ship, select a camera, qualify visual content, or make native capture atomic. The capture provider must already be installed and registered with its exact adapter and native revisions.

Construct it with an existing connected `AutomationClient`, two separate existing task journal directories outside the personal game directory, and both revisions. The observer reads `native_save_path`; that native save-folder observation does not prove a screenshot directory. The observer's two candidate screenshot directories remain inferred until a new stable decoded image is observed.

The closed workflow is:

1. `reserve(view_binding)` returns an opaque local ticket and records the complete file frame before any native action.
2. `prepare(ticket)` dispatches once. `status(ticket)` can recover asynchronous preparation without repeating it.
3. `execute(ticket)` requires a matching native preparation observation within the native capture time window. It consumes the request before transport.
4. `status(ticket)` waits for a matching native capture observation. SDK `running` alone means dispatch began; the host stage stays `EXECUTE_SENT` until the native phase and chronology qualify.
5. `settle(ticket)` requires one new stable fully decoded image, preserved pre-existing files, unchanged identity and bounded directory membership.
6. `prepare_release(ticket)` and `release(ticket)` require the retained opaque settlement and fresh exact file/directory-frame checks. Release is consumed once. Its acknowledgement means only that the native reservation was released.

No caller-provided JSON, hash or boolean can substitute for the observer's local settlement. Unknown actions retain the reservation. A transport failure, protocol contradiction, identity change, timeout or changed settled frame quarantines this owner; it offers no automatic reset, journal reopening or effect replay. Read-only status may reconcile a still-valid asynchronous request. A late release acknowledgement still requires the exact settled frame before reuse.

`receipt(ticket)` returns historical action/file evidence, the host stage and reservation status. It always reports `visual_pass=false` and `runtime_accepted=false`. A decoded image is not proof of causal origin, camera pose, model appearance or motion. Video cases remain outside this capability.

Camera context uses the same actual client's `ReadEvidence` collector, not submitted receipt JSON. Exactly one zero-argument `camera_state` read occurs before execute dispatch, and another after the first matching native capture acknowledgement (including delayed status). Raw bound envelopes, replayed receipts, source pins, helper identity and capture id/digest are retained in exclusive journals. Status polling cannot replace these samples. A production connection is required by default; the explicit `synthetic=True` test option retains SYNTHETIC evidence and cannot produce MACHINE_OBSERVED camera provenance.

`camera_observations.status` is PENDING, SAME_SAMPLED_VALUES, CHANGED or UNAVAILABLE. Equality compares raw native rotation, offset settings, reference handle and external mode only. It does not prove stability between samples, achieved pose/distance, or the exact instant when pixels were acquired. Native angles retain UNKNOWN units/frame; MD target and angles are separate observations. Camera UI intervals are ordered only against each other. Public read envelopes use GetCurrentGameTime and retain their independently validated chronology; there is no numerical enclosure claim between that clock and the injected camera UI clock. MD simulation ages and host file-settlement time also remain separately labelled.

Valid native UNAVAILABLE remains missing context and does not prevent independent settled-file release. Malformed observations, source or identity drift quarantine the owner without replay. Each ticket has two samples, each at most 128 KiB serialized including its raw transcript; total retained encoded camera evidence is capped at 16 MiB. Capacity for both samples is checked before dispatch. Camera journals are rechecked on ticket access; raw evidence exports are replayable but not cryptographic attestations. Native camera runtime and the successor integration controls must be qualified separately.

Qualification is currently bounded integration evidence: actual SDK/codec/ActionJournal and file observer, with synthetic MD replies, synthetic native path responses and temporary image files. The dedicated observer suite is complete for its 35 controls; actual native runtime and visual-content qualification remain separate requirements.

Optional-install collection is now checked separately. With the capture extra,
the 35 observer controls plus 4 geometry/CI policy controls passed 39/39, zero skips. With Pillow imports deliberately unavailable, the independent
missing-decoder control passed and 34 image-dependent controls explicitly skipped; those skips are not passes. The isolated full-qualification
workflow requests both geometry and capture extras so image controls execute.
No shared workflow branch, remote CI run or release was changed by this fix.

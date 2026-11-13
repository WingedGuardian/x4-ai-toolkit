# Optional compiled macro geometry observation

ARCHIVED prototype: a 2026-11-09 decision dropped the third-party library it wraps. This unit
is not loaded by any helper (it is not in ui.xml or the automation bundle), though
its file still sits in mods/x4_toolkit_helper/ui and its tests still run.
The70-control source revision is preserved at bb4fb0d1; the documentation below
describes that historical standalone unit, not planned integration. No dependency
installation or calibration is scheduled. Keep source history; do not consume
this prototype as a qualified runtime capability.

This standalone factory uses an installed third-party geometry library's interface only. It does not bundle, execute on import, install or reproduce that library's pointer-reading implementation. It is not wired into the public helper or an SDK provider yet.

Construction takes trusted callbacks `current`, `game_build`, `dependency`, `external`, `encode` and a copied `expected` declaration. Construction and `availability()` perform no callback, engine or external API calls. Explicit `boxes(macro)` and `connections(macro, offset)` reads require a current canonical public stamp and matching game-build/dependency declaration before invoking the external getter. They never call `ExtraData.Available()`.

The dependency declaration contains extension ID, release version, interface version, archive SHA256, Lua SHA256, installed receipt SHA256 and qualified game build. Its trusted host callback must validate the installed bytes, runtime peer and current qualification; this Lua adapter cannot independently authenticate that callback. `UIGetExtensionVersion` alone would only establish a version, not byte identity. Interface version100 and release103 are distinct. A later release is usable only with an explicitly qualified matching declaration; equality is rechecked during every observation. No runtime installation/source binding has been implemented in this unit.

## Fixed interface and evidence scope

The source-reviewed interface is `ExtraData.GetMacroDataEx(macro, "boundingbox", "fullboundingbox")` with two ordered return values, or the single fixed key `"connections"`. No arbitrary key, tag suffix, component pointer or expression is accepted.

Successful data remains `EXTERNAL_API_OBSERVATION_UNCALIBRATED` with `numeric_accuracy_qualified=false`. OBSERVED means the repeated external interface returned these values, not that they are independently accurate. The library's macro/template box agreement compares two pointer-read values; it is not an independent native getter oracle. First runtime calibration may retain this unqualified observation. Authored connection names/tags/transforms and independent box/framing calibration are separate evidence. No input flag can promote numeric accuracy.

Boxes retain the external field names `max`, `centre` and `radius`; indexed coordinates become named x/y/z without changing numeric values. These are compiled macro-definition values in the macro's local frame, not fitted component geometry, achieved camera pose or an observed visible silhouette. Nil means UNKNOWN: the external getter conflates unavailable support, missing macro and no box. Both nil boxes yield aggregate UNKNOWN; one yields PARTIAL. No zero or absence is invented.

Connections preserve name, tags, position, direction, three rotation rows, optional attached macro, usage count and optional exclusion-zone fields. An omitted child macro is NOT_REPORTED, not an empty fitted slot. Native tags that are not representable as a dense readable list refuse; this wrapper cannot establish that the external library supplied every semantic tag.

Each call repeats the fixed getter and compares the complete normalized bounded result, including rows outside the returned page. It rechecks canonical identity, getter identity, source declaration and build after encoding. The two native reads are sampled stability evidence, not an atomic engine snapshot.

Connections accept at most256 source rows and256 KiB of normalized encoded row data, with at most32 tags per row. The reply initially retains at most8 rows and shrinks to3000 encoded bytes. A single oversized row refuses instead of returning a no-progress page. Denominator, offset, returned, omitted and next offset are explicit. Empty dense arrays can be observed with denominator zero; nil, sparse, malformed or over-budget lists cannot. Each page is a fresh observation; `cross_page_snapshot=false` means pages cannot be combined into an immutable census without an additional caller-side proof.

The external library can allocate and scan internally before returning. This wrapper bounds its own normalization, calls (two fixed getters per read), retained page and output, not the external library's internal memory or latency. The library's first getter may initialize scanners and write UI userdata/build/crash-sentinel state. Therefore the observation is not advertised as side-effect-free.

## Source contract and qualification

READ: that library's packed Lua (the reviewed version) uses interface version 100; source lines664–666 define box output,698–735 connection rows,1122–1141 nil box behavior,1221–1234 ordered multiple returns,1270–1274 initializing Available. The reviewed archive/Lua identities are recorded privately; no third-party source is shipped here. Named native menu callers provide size classes and station-plot dimensions, which do not establish an equivalent compiled-macro box interface. This is a scoped source comparison, not a negative corpus claim.

The public connection getter at1183–1190 strips internal tagIds before returning rows; this adapter correctly excludes that internal field. A repeat nil connection result retains its checked source/stamp metadata and uncalibrated tier without inventing a zero denominator.

Offline controls exercise this own-code adapter with synthetic dependency callbacks and the actual toolkit wire encoder. They do not execute Extra Data, scan game memory or establish its compatibility with the current engine. Missing dependency, wrong build/source/interface, nil, malformed data, repeat drift, output bounds and disposal must refuse or remain explicitly unknown. Production host attestation, SDK integration and actual engine observations remain open.

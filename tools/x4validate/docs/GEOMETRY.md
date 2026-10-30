# Experimental render-hull similarity

Geometry is opt-in. Ordinary `x4similar` retains its numeric scores, weights,
class/purpose filters, threshold and text output. The numeric effective database
and its geometry-subtree exclusion are unchanged.

From the tool directory:

```text
uv sync --extra geometry
uv run --extra geometry x4similar --geometry assets/units/size_l/macros/candidate_macro.xml --geometry-against assets/units/size_l/macros/other_macro.xml --geometry-json
uv run --extra geometry x4similar --geometry assets/units/size_xl/macros/candidate_macro.xml --geometry-audit --geometry-max-instances 2048 --geometry-json
```

Arguments are game virtual paths, not macro names. Repeat `--geometry-against`
for explicit comparisons. Without it, the stats inventory is used without its
class/purpose filters: installed `ship_*` macros that carry `<properties>`; other
macros are not candidates. More than `--geometry-limit` candidates
(default 32, maximum 256) refuse; there is no hidden shortlist. Disabled-only
inventory entries remain unresolved rather than resolving against an active
mod's same-named definition. Unreadable inventory files and mods the engine does
not load are named separately (stderr in text mode, `unreadable_inventory` and
`notes` in JSON). A run that scores no pair at all, not even partially, exits 3: a
non-answer, never an OK.

`--geometry-overlay DIR` appends an isolated candidate **last**. This is an
explicit hypothetical model, not its predicted eventual installation order.
`--geometry-alias NAME=DIR` declares an authored extension namespace when its
snapshot folder has a different name. Aliases must name explicit candidate roots.
No candidate installation or activation is needed. Stats filters cannot be mixed
with geometry; no real-hull geometry threshold is qualified yet.

## Output and evidence

Human output shows sizes, scale, proper/mirrored alignment, directed overlaps,
owners, LODs and unresolved parts. JSON schema `x4similar.geometry/2` also gives:

- Ordered resolution roots, inventory scope and explicit hypothetical aliases.
- Every included instance, its transform, LOD and triangle/vertex counts; each
  excluded effect and unresolved part has its own reason.
- Shared exact mesh payload hashes are an explanation only; they never replace
  assembled-surface comparison or imply fuzzy part equivalence.
- SHA-256 receipts for mesh bytes and merged XML, XML contributors, storage
  owners, implementation hashes and dependency versions.
- Actual sample counts, native XYZ dimensions, uniform scale ratio, rotation
  and translation, directed coverage and median/p95/maximum surface distance.
- Total, shortlisted, scored, partial, unresolved and excluded denominators.

Version 2 adds independent `stages` for inventory, assets, placement and
verification, structured `findings` alongside the existing `unresolved` strings,
the discovered graph, per-instance asset audit, budgets and I/O/cache metrics.
I/O counters cover binary resolver requests, binary storage reads/bytes and mesh
decodes; they do not measure file reads performed inside the XML merge engine.
Finding codes distinguish `asset_absent`, `decode_failed`, `parent_unresolved`,
`unsupported_placement`, `reference_unresolved`, `cycle`, `budget_reached`,
`unqualified_pose`, `lod_fallback` and `input_drift`. Exclusions remain separate.
Consumers must check the schema and stage states rather than treating an empty
pair list, a decoded asset, or an audited graph as a complete comparison.

`--geometry-audit` audits only the requested macro and never scans comparison
candidates. Comparison paths and nondefault sample/candidate limits are rejected.
Its exit code is 0 only for a complete inventory, successful payload audit and
successful input verification; otherwise 3. Placement qualification is independent:
an asset audit may pass while parent placement remains unresolved. The assembly
status is `audited` for a passed audit, and no geometry score is produced.

Discovery precedes placement and checks reachable structural dependencies even
when an ancestor transform cannot be evaluated. Every mesh-bearing part is
audited, including effect-material surfaces excluded from scoring. An absent
declared LOD is a failure even if the LOD0 surface can be scored. `lod_audit`
accounts for every declared LOD (default LOD0 when none is declared); scoring
continues to use LOD0 or the explicitly reported lowest declared fallback. An absent
effect-looking mesh remains a failure; no procedural exception is inferred from
its name or `nocollision`. Missing payloads include exact VFS resolution attempts,
provider roots and catalogs. Known pending roots and budget frontiers are reported;
`total_parts` is null if traversal is incomplete. Counts of unique decoded payloads
and referencing instances are separate. Equipment branches remain outside scope.

`geometry_score` is 0..100 for a complete supported assembly. A missing, failed
or incomplete assembly has `geometry_score: null` with reasons. A decoded subset
may have a separate `partial_geometry_score`; that cannot establish a whole-hull
negative. Classifications remain `verdict: unresolved` even for complete inputs:
an experimental score is not a probability or a mod-removal recommendation.

## Extraction contract and limits

XML uses the toolkit's effective merge. Binary reads resolve explicit namespaces,
nested overrides, case-insensitive paths, loose-over-packed precedence, later
catalogs, and configured base archive ranges. They never use basename fallback.
Configured DLC reference folders may contain XML only; binary lookup also checks
the matching installed DLC folder/catalogs, after the reference copy and before
lower-priority owners. This fallback never substitutes an installed mod for an
explicit candidate alias. Binary receipts name the physical `binary_root`.
Malformed/ambiguous catalogs refuse. Nonmatching version catalogs are named as
ignored. Matching or unknown-version catalogs are enumerated; a requested member
refuses because its payload semantics are unsupported. Unrelated members do not
block lower-priority binary providers.

The independent XUMF subset reader accepts v3 little-endian triangle lists,
float32 XYZ/XYZW or half-float XYZW positions, supported interleaved element
layouts, 16/32-bit indices, and raw/zlib buffers. It validates offsets, strides,
counts, payload overlap, decompression bounds, indices and finite positions.
Limits: 128 MiB input/expanded bytes per mesh, 4 million buffer items, 512 render
instances by default (`--geometry-max-instances` accepts 1..2048), 128 macro
instances and depth 16. Empty/all-degenerate geometry refuses. The legacy Python
concatenation API retains its 4 million assembled vertices/2 million triangles
ceilings; the CLI uses instanced surfaces instead of raising those ceilings.
Decoded-payload residency is capped at 64 MiB per scene; oversized individual
payloads are used transiently. One payload's spatial indexes are built at a time,
with a conservative 128 MiB admission estimate. This estimate is not an OS memory
limit: run large jobs through a resource wrapper with a process-tree cap.
Mixed zero-area faces are excluded from scoring with explicit counts.

Native XYZ, quaternion offsets, uniform scale and repeated transformed instances
are supported. Parent ambiguity, missing parents and cycles refuse. Pose is explicit;
an explicitly declared LOD fallback makes the assembly partial. Effect-only
material references are excluded; appearance and collision/JCS are not compared.

Recursive structural attachments use the parent connector frame multiplied by
the inverse child connector frame, including parent part offsets. Shared
`component.part` references inherit mesh data, not provider placement. Equipment
classes are excluded explicitly. Virtual leaves are excluded only after their
actual absence of render source, parts and structural children is established;
irrelevant virtual anchors are not validated or repaired. Missing/ambiguous exact
index entries, missing physical ports and cycles remain unresolved.

Frames and ancestry share one parent resolver. Repeated parent names remain
unresolved, with raw consumer/candidate groups and XML paths in the diagnostic.
Group-relative selection, whitespace normalization and cross-group fallback are
not independently qualified. The mesh importer's inspected connection loop does
not resolve parent/group relationships, so it cannot establish that rule.

`--geometry-pose strict` is the default: animation ancestry, pivots, shared-part
runtime state and non-half-turn Euler poses remain unresolved where applicable.
`--geometry-pose bind` explicitly compares declared XML offsets and mesh bind
geometry without evaluating animation tracks, deployment or runtime state.
Pivot reparameterization and consumer reference placement have independent
Blender/importer arithmetic controls. Roll-only Euler projection is supported;
yaw/pitch, mixed quaternion/Euler declarations and nonfinite values refuse.
Roll arithmetic matches equivalent Egosoft-exported quaternions in Blender;
the game's Euler parser has not been tested. Bind completeness describes this
declared model, not every runtime pose. Add `--geometry-pose bind` to the example
command above only when this projection answers the comparison question.

MEASURED privately: four selected ship assemblies resolve completely in bind
mode. Independent Blender hierarchies account for all 960 declared render frames:
682 included and 278 excluded, no missing/unexpected frames. Thirty-three physical
attachment anchors coincide within 1.12e-7 in Blender. This checks numeric XML-bind
transforms, not complete rendered images or engine animation behavior. Separate
authored controls exercise exporter quaternions, roll equivalents, child connector
inversion, pivots and references.

Private qualification matched the inspected Blender importer's parsing coordinates
and triangle indices on **52/52 selected real LOD0 meshes**, with zero coordinate
error. This qualifies that selected population, not every XMF variant or complete
world assembly. The upstream importer is GPL-3.0; its implementation is neither
bundled nor imported by this MIT toolkit. Public fixtures are authored synthetic
geometry; no game/mod meshes ship.

## Frozen experimental score

The legacy Mesh API retains `surface-overlap-experimental-1`. The CLI uses
`surface-overlap-instanced-2`: the same alignment, tolerances and directed coverage
model, with a separately versioned deterministic instance-weighted sample stream.
Do not compare version 1 and version 2 numeric scores as identical observations.
Each placed instance contributes its own surface area, including uniform scale;
shared payloads do not collapse repeated docks or attachments. Analytic surface
moments stream in bounded triangle batches. Distance queries invert each rigid,
uniformly scaled instance transform and reuse its source-mesh index; conservative
world-space boxes prune only instances that cannot improve the current distance.
Queries still target complete triangle surfaces. A score is sampled evidence,
not an exact whole-surface integral or a globally optimal alignment.

Both scorers use deterministic area-weighted barycentric
sampling (default 4096, allowed 128..16384). Analytic surface moments remove
translation and normalize RMS radius using uniform scale. PCA enumerates proper
axis permutations/signs plus native alignment; the four best sampled-distance
starts receive bounded trimmed rigid ICP. Initial and refined solutions are
evaluated. Reflections are scored separately and never replace proper alignment.
Global optimality is unproved, especially on symmetric or rearranged assemblies.

Final distances go from samples to the **other triangle surface**, not vertices
or its point samples. A nearest vertex bounds an R-tree query that enumerates
every triangle box intersecting that radius cube. Candidate triangles stream in
batches of at most 2048 across query points. A conservative Euclidean distance
lower bound to each triangle bounding box removes impossible candidates before
exact evaluation. Shared budgets of 100 million bounding-box candidates and
20 million exact triangle tests refuse the entire pair on exhaustion, never
publishing a truncated distance as a complete one.
Inverse orthogonal queries reuse one spatial index per hull across hypotheses.

At normalized tolerances 0.015, 0.03 and 0.06, measure both directed fractions
within tolerance and combine them with a harmonic mean (zero when both are zero).
The final weighted mean uses 0.25, 0.50, 0.25 and is multiplied by 100. Directed
fractions, tail errors, scale and reflection evidence remain visible.

Reproduce calibration/holdout evidence:

```text
uv run --extra geometry python scripts/qualify-geometry.py
uv run --extra geometry python scripts/qualify-geometry.py --instanced
```

Each split has six positive and five negative authored controls, plus another
sampling seed for repeatability. Controls cover rigid transforms, scale, seams,
face reordering, subdivision, mild deformation/additions, equal dimensions with
different shapes, nonuniform stretch, reflection, a contained partial hull and a
changed bow. The distinct hulls come from one parameterized family, not a labelled
real-hull population. The prespecified synthetic cutoff is 90/100; each score and
confusion count is reported. This cutoff is not a real-ship classification option.

## Costs, freshness and outstanding qualification

No game launch, full extraction or persistent geometry database is required. The
per-run binary and merged-XML caches are each bounded at 128 MiB; arrays and indexes are
additional memory. A candidate's mesh is freed once its pair is scored, so peak memory
is the subject plus one candidate, not the whole inventory. Mesh bytes, effective XML and catalog sets are rechecked after
extraction and before CLI publication. There is no persistent cache to become
silently stale, and no coarse search or retrieval-recall claim.

Use the workspace resource runner for large comparisons. Initial real mismatch
attempts exceeded a 0.75 GiB job cap; streamed candidates and spatial-index reuse
address the allocations, while work exhaustion remains unresolved. A killed job
is not a completed benchmark or a shape result.

MEASURED after bounded cross-query batching: six selected real component-surface
pairs at 512 samples per hull completed in 4.46–14.38 seconds per pair. The serial
decoder, synthetic, real-pair and stats-baseline qualification took 153.9 seconds;
the resource runner reported 400 MB peak job memory under a 0.75 GiB cap. Binary
cache use was 12,598,120 bytes. These Windows measurements are not a full-inventory
performance qualification, and per-hull extraction timings were not isolated.
Those component-only measurements predate recursive bind assembly. The later
four-hull bind run required a 1.5 GiB job cap; a 0.75 GiB cap is not qualified for
complete assemblies. Individual extraction and pair timings are retained in the
private qualification receipts.

Outstanding: genuine old/new render-export controls (including 7.x/9.x), complete
engine runtime animation/deployment and general Euler semantics, fuzzy shared-part
explanations, large-inventory recall/performance and a real-hull threshold. A low
experimental overlap cannot prove uniqueness. Equipment, jobs, behavior, visual
appearance and collision compatibility remain separate questions.

Dependencies are optional: NumPy/SciPy (BSD), Trimesh (MIT) and Rtree (MIT, with
its distributed libspatialindex dependency); versions/hashes are in uv.lock.
Primary references: [Trimesh proximity](https://trimesh.org/trimesh.proximity.html),
[sampling](https://trimesh.org/trimesh.sample.html),
[SciPy spatial](https://docs.scipy.org/doc/scipy/reference/spatial.html), and
[the Blender oracle upstream](https://github.com/Lc4Hunter/X4_Mesh_Importer).

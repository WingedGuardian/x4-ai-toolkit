# Experimental render-hull similarity

Geometry is opt-in. Ordinary `x4similar` retains its numeric scores, weights,
class/purpose filters, threshold and text output. The numeric effective database
and its geometry-subtree exclusion are unchanged.

From the tool directory:

```text
uv sync --extra geometry
uv run --extra geometry x4similar --geometry assets/units/size_l/macros/candidate_macro.xml --geometry-against assets/units/size_l/macros/other_macro.xml --geometry-json
```

Arguments are game virtual paths, not macro names. Repeat `--geometry-against`
for explicit comparisons. Without it, the installed ship-definition inventory is
used without stats class/purpose filters. More than `--geometry-limit` candidates
(default 32, maximum 256) refuse; there is no hidden shortlist. Disabled-only
inventory entries remain unresolved rather than resolving against an active
mod's same-named definition. Unreadable inventory files are named separately.

`--geometry-overlay DIR` appends an isolated candidate **last**. This is an
explicit hypothetical model, not its predicted eventual installation order.
`--geometry-alias NAME=DIR` declares an authored extension namespace when its
snapshot folder has a different name. Aliases must name explicit candidate roots.
No candidate installation or activation is needed. Stats filters cannot be mixed
with geometry; no real-hull geometry threshold is qualified yet.

## Output and evidence

Human output shows sizes, scale, proper/mirrored alignment, directed overlaps,
owners, LODs and unresolved parts. JSON schema `x4similar.geometry/1` also gives:

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

`geometry_score` is 0..100 for a complete supported assembly. A missing, failed
or incomplete assembly has `geometry_score: null` with reasons. A decoded subset
may have a separate `partial_geometry_score`; that cannot establish a whole-hull
negative. Classifications remain `verdict: unresolved` even for complete inputs:
an experimental score is not a probability or a mod-removal recommendation.

## Extraction contract and limits

XML uses the toolkit's effective merge. Binary reads resolve explicit namespaces,
nested overrides, case-insensitive paths, loose-over-packed precedence, later
catalogs, and configured base archive ranges. They never use basename fallback.
Malformed/ambiguous catalogs refuse. Nonmatching version catalogs are named as
ignored; matching or unknown-version catalogs are unsupported and refuse.

The independent XUMF subset reader accepts v3 little-endian triangle lists,
float32 XYZ/XYZW or half-float XYZW positions, supported interleaved element
layouts, 16/32-bit indices, and raw/zlib buffers. It validates offsets, strides,
counts, payload overlap, decompression bounds, indices and finite positions.
Limits: 128 MiB input/expanded bytes per mesh, 4 million buffer items, 128 part
instances, 2 million assembled vertices and 1 million assembled triangles. Empty/all-degenerate geometry refuses.
Mixed zero-area faces are excluded from scoring with explicit counts.

Native XYZ, quaternion offsets, uniform scale and repeated transformed instances
are supported. Parent ambiguity, missing parents and cycles refuse. Pose is rest;
an explicitly declared LOD fallback makes the assembly partial. Effect-only
material references are excluded; appearance and collision/JCS are not compared.

**Unqualified extraction:** macro-connected structural attachments are individually
inventoried as unresolved, not silently called equipment. Pivots, Euler `rotation`
offsets and animated structural ancestry are unqualified. Missing exact component
index entries, including wildcard-only/unindexed components, remain unresolved.
Many real ships therefore have component-surface evidence but no whole-hull score.

Private qualification matched the inspected Blender importer's parsing coordinates
and triangle indices on **52/52 selected real LOD0 meshes**, with zero coordinate
error. This qualifies that selected population, not every XMF variant or complete
world assembly. The upstream importer is GPL-3.0; its implementation is neither
bundled nor imported by this MIT toolkit. Public fixtures are authored synthetic
geometry; no game/mod meshes ship.

## Frozen experimental score

`surface-overlap-experimental-1` uses deterministic area-weighted barycentric
sampling (default 4096, allowed 128..16384). Analytic surface moments remove
translation and normalize RMS radius using uniform scale. PCA enumerates proper
axis permutations/signs plus native alignment; the four best sampled-distance
starts receive bounded trimmed rigid ICP. Initial and refined solutions are
evaluated. Reflections are scored separately and never replace proper alignment.
Global optimality is unproved, especially on symmetric or rearranged assemblies.

Final distances go from samples to the **other triangle surface**, not vertices
or its point samples. A nearest vertex bounds an R-tree query that enumerates
every triangle box intersecting that radius cube. Candidate triangles stream in
batches of at most 2048 across query points. A shared budget of 20 million triangle tests refuses the entire
pair on exhaustion, never publishing a truncated distance as a complete one.
Inverse orthogonal queries reuse one spatial index per hull across hypotheses.

At normalized tolerances 0.015, 0.03 and 0.06, measure both directed fractions
within tolerance and combine them with a harmonic mean (zero when both are zero).
The final weighted mean uses 0.25, 0.50, 0.25 and is multiplied by 100. Directed
fractions, tail errors, scale and reflection evidence remain visible.

Reproduce calibration/holdout evidence:

```text
uv run --extra geometry python scripts/qualify-geometry.py
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
per-run byte cache is bounded at 128 MiB; decoded arrays and spatial indexes are
additional memory. Mesh bytes, effective XML and catalog sets are rechecked after
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

Outstanding: genuine old/new render-export controls (including 7.x/9.x), complete
structural attachments and pivot/Euler/animation semantics, fuzzy shared-part
explanations, large-inventory recall/performance and a real-hull threshold. A low
experimental overlap cannot prove uniqueness. Equipment, jobs, behavior, visual
appearance and collision compatibility remain separate questions.

Dependencies are optional: NumPy/SciPy (BSD), Trimesh (MIT) and Rtree (MIT, with
its distributed libspatialindex dependency); versions/hashes are in uv.lock.
Primary references: [Trimesh proximity](https://trimesh.org/trimesh.proximity.html),
[sampling](https://trimesh.org/trimesh.sample.html),
[SciPy spatial](https://docs.scipy.org/doc/scipy/reference/spatial.html), and
[the Blender oracle upstream](https://github.com/Lc4Hunter/X4_Mesh_Importer).

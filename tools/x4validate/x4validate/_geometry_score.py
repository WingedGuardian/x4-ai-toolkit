"""Experimental deterministic bidirectional surface similarity; not a probability.

No class, purpose, stats or component-name filters. Alignment only permits proper
rigid rotations; size normalization is uniform. Reflections are tested separately.
"""
from __future__ import annotations

import itertools
import time

import numpy as np
from scipy.spatial import cKDTree
import trimesh

from ._geometry_mesh import GeometryError, Mesh, validate

SCORER_VERSION = "surface-overlap-experimental-1"
SAMPLES = 4096
TOLERANCES = (0.015, 0.03, 0.06)
WEIGHTS = (0.25, 0.5, 0.25)
MAX_TRIANGLE_TESTS = 20_000_000


def sample(mesh: Mesh, count=SAMPLES, seed=0):
    """Area-weighted barycentric sampling (not vertex-density weighting)."""
    validate(mesh)
    if not 128 <= count <= 16384:
        raise GeometryError("surface sample count must be 128..16384")
    t = mesh.triangles()
    areas = np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1)
    rng = np.random.default_rng(seed)
    chosen = t[rng.choice(len(t), count, p=areas/areas.sum())]
    u, v = rng.random((2, count))
    u = np.sqrt(u)
    return ((1-u)[:, None]*chosen[:, 0] +
            (u*(1-v))[:, None]*chosen[:, 1] + (u*v)[:, None]*chosen[:, 2])


def _prepare(mesh, count, seed):
    points = sample(mesh, count, seed)
    # Analytic area moments: subdivision/reordered triangles do not change the
    # normalization or PCA frame (unlike sample-dependent estimates).
    t = mesh.triangles()
    areas = np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1)
    weights = areas/areas.sum()
    origin = t[0, 0].copy()
    t -= origin
    center = (t.mean(axis=1)*weights[:, None]).sum(axis=0)
    t -= center
    sums = t.sum(axis=1)
    covariance = (np.einsum('n,nti,ntj->ij', weights, t, t)+
                  np.einsum('n,ni,nj->ij', weights, sums, sums))/12
    center += origin
    radius = np.sqrt(np.trace(covariance))
    if not np.isfinite(radius) or radius <= 0:
        raise GeometryError("zero/non-finite surface radius")
    points = (points-center)/radius
    _, axes = np.linalg.eigh(covariance)
    return points, center, radius, axes


def surface_distances(surface, points, work=None):
    """Exact point-to-triangle queries with bounded triangle intermediates.

    A nearest vertex gives an upper bound. Every closer surface triangle must
    intersect its radius cube, so the R-tree query cannot omit a closer face.
    Stream that complete candidate set in batches of 256 instead of concatenating
    every point's candidates (which can allocate gigabytes on a large mismatch).
    A work-budget refusal is UNRESOLVED, never a partial distance or a zero score.
    """
    work = [0] if work is None else work
    target = surface._cache["geometry_vertex_tree"]
    if target is None:
        # Only referenced vertices lie on the target surface. An unused vertex
        # near the query is NOT an admissible distance upper bound.
        target = cKDTree(surface.vertices[np.unique(surface.faces)])
        surface._cache["geometry_vertex_tree"] = target
    radii = target.query(points, workers=1)[0]
    result = []
    tree = surface.triangles_tree
    for point, radius in zip(points, radii):
        radius += 1e-10
        candidates = tree.intersection(np.concatenate([point-radius, point+radius]))
        best = float("inf")
        while True:
            ids = list(itertools.islice(candidates, 256))
            if not ids:
                break
            work[0] += len(ids)
            if work[0] > MAX_TRIANGLE_TESTS:
                raise GeometryError("exact surface-distance work budget exceeded")
            triangles = surface.vertices[surface.faces[ids]]
            closest = trimesh.triangles.closest_point(triangles, np.broadcast_to(point, (len(ids), 3)))
            distances = np.linalg.norm(closest-point, axis=1)
            if not np.isfinite(distances).all():
                raise GeometryError("non-finite point-to-triangle distance")
            best = min(best, float(distances.min()))
        if not np.isfinite(best):
            raise GeometryError("empty surface-distance candidate set")
        result.append(best)
    return np.asarray(result)


def _coverage(a, b, surface_a=None, surface_b=None, work=None, directed=None):
    def distances(points, target, surface):
        if surface is None:
            return cKDTree(target).query(points, workers=1)[0]
        return surface_distances(surface, points, work)
    ab, ba = directed if directed is not None else (distances(a, b, surface_b), distances(b, a, surface_a))
    rows = []
    for t, w in zip(TOLERANCES, WEIGHTS):
        x, y = float(np.mean(ab <= t)), float(np.mean(ba <= t))
        rows.append(dict(tolerance=t, weight=w, a_to_b=x, b_to_a=y,
                         harmonic=2*x*y/(x+y) if x+y else 0.0))
    return sum(r["weight"]*r["harmonic"] for r in rows)*100, rows, ab, ba


def _align(a, b, axes_a, axes_b, determinant):
    target = cKDTree(b)
    hypotheses = [np.eye(3)] if determinant == 1 else [np.diag([-1., 1., 1.])]
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            r = axes_a @ np.eye(3)[:, perm] @ np.diag(signs) @ axes_b.T
            if np.linalg.det(r)*determinant > 0:
                hypotheses.append(r)
    # All 24 PCA permutations/signs, plus native coordinates; don't pin unstable
    # axis ordering on symmetric shapes. Refine the best four hypotheses.
    ranked = sorted(hypotheses, key=lambda r: np.mean(target.query(a @ r, workers=1)[0]**2))
    candidates = []
    for initial in ranked[:4]:
        candidates.append((initial.copy(), np.zeros(3)))
        rotation, translation = initial.copy(), np.zeros(3)
        for _ in range(24):
            moved = a @ rotation + translation
            distance, nearest = target.query(moved, workers=1)
            keep = distance <= np.quantile(distance, .9)
            x, y = moved[keep], b[nearest[keep]]
            xc, yc = x.mean(axis=0), y.mean(axis=0)
            u, _, vt = np.linalg.svd((x-xc).T @ (y-yc))
            correction = np.eye(3)
            correction[-1, -1] = np.linalg.det(u @ vt)
            delta = u @ correction @ vt
            shift = yc-xc @ delta
            rotation, translation = rotation @ delta, translation @ delta+shift
            if np.linalg.norm(delta-np.eye(3)) < 1e-8 and np.linalg.norm(shift) < 1e-8:
                break
        candidates.append((rotation, translation))
    return candidates


def compare(a: Mesh, b: Mesh, count=SAMPLES, seed=0):
    started = time.perf_counter()
    def clean(mesh):
        validate(mesh)
        t = mesh.triangles()
        keep = np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1) > 0
        return Mesh(mesh.vertices, mesh.faces[keep]), int((~keep).sum())
    a, degenerate_a = clean(a)
    b, degenerate_b = clean(b)
    pa, ca, ra, aa = _prepare(a, count, seed)
    pb, cb, rb, ab = _prepare(b, count, seed)
    surface_b = trimesh.Trimesh(vertices=(b.vertices-cb)/rb, faces=b.faces, process=False)
    surface_a = trimesh.Trimesh(vertices=(a.vertices-ca)/ra, faces=a.faces, process=False)
    work = [0]
    def finish(determinant):
        best = None
        for rotation, translation in _align(pa, pb, aa, ab, determinant):
            # Orthogonal inverse queries reuse ONE tree per hull. Rebuilding a
            # trimesh tree for every hypothesis retains native indexes through
            # cyclic caches until GC, exceeding bounded job memory on real hulls.
            directed_a = surface_distances(surface_b, pa @ rotation+translation, work)
            directed_b = surface_distances(surface_a, (pb-translation) @ rotation.T, work)
            result = _coverage(pa @ rotation+translation, pb, directed=(directed_a, directed_b))
            if best is None or result[0] > best[0][0]:
                best = result, rotation, translation
        return best
    try:
        proper, rotation, translation = finish(1)
        mirrored, _, _ = finish(-1)
    finally:
        # Release native spatial indexes even when the distance work refuses.
        for surface in (surface_a, surface_b):
            tree = surface._cache.cache.get("triangles_tree")
            if tree is not None:
                tree.close()
            surface._cache.clear()
            # Trimesh's cache hash callback creates a reference cycle. Drop large
            # normalized arrays explicitly rather than wait for cyclic GC.
            surface.vertices = np.empty((0, 3))
            surface.faces = np.empty((0, 3), dtype=np.int64)
    score, rows, da, db = proper
    def distribution(d):
        return dict(median=float(np.median(d)), p95=float(np.quantile(d, .95)),
                    maximum=float(d.max()))
    return dict(status="experimental", geometry_score=score, scorer=SCORER_VERSION,
                calibration="unqualified: no real-hull variant threshold",
                verdict="unresolved", score_is_probability=False,
                samples_a=len(pa), samples_b=len(pb), seed=seed,
                discarded_zero_area_faces_a=degenerate_a, discarded_zero_area_faces_b=degenerate_b,
                triangle_distance_tests=work[0], triangle_distance_budget=MAX_TRIANGLE_TESTS,
                overlap=rows, distances_a_to_b=distribution(da), distances_b_to_a=distribution(db),
                size_a=np.ptp(a.vertices[np.unique(a.faces)], axis=0).tolist(),
                size_b=np.ptp(b.vertices[np.unique(b.faces)], axis=0).tolist(),
                uniform_scale_a_to_b=rb/ra,
                transform=dict(convention="row XYZ: ((p-center_a)/radius_a) @ rotation + translation; target normalized",
                               center_a=ca.tolist(), center_b=cb.tolist(), radius_a=ra, radius_b=rb,
                               rotation=rotation.tolist(), translation=translation.tolist(),
                               determinant=float(np.linalg.det(rotation))),
                mirrored_score=mirrored[0], mirrored_variant=mirrored[0] > score+5,
                alignment="bounded PCA permutations + trimmed ICP; global optimum unproved",
                seconds=time.perf_counter()-started)

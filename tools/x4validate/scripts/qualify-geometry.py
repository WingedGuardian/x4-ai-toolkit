"""Reproduce authored synthetic calibration/holdout evidence; JSON to stdout.

Run with the geometry extra installed. No game assets, caches or file writes.
The frozen synthetic cutoff is an experiment, NOT a qualified real-hull label.
"""
import json
import hashlib
from pathlib import Path

import numpy as np
import scipy
import trimesh
from scipy.spatial.transform import Rotation

from x4validate._geometry_mesh import Mesh
from x4validate._geometry_score import compare, SCORER_VERSION, TOLERANCES, WEIGHTS


def hull(seed):
    rng = np.random.default_rng(seed)
    # A scalene closed hull, varied independently in the holdout.
    vertices = np.array([[0., 0, 0], [4, 0, 0], [.3, 1.5, 0], [.2, .1, 2.5]])
    vertices += rng.uniform(-.1, .1, vertices.shape)
    return Mesh(vertices, np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]))


def cases(seed):
    a = hull(seed)
    rigid = a.vertices @ Rotation.from_euler("xyz", [19+seed, 83, 37], degrees=True).as_matrix()*3+[10, -7, 19]
    split_v, split_f = trimesh.remesh.subdivide(a.vertices, a.faces)
    mild = a.vertices.copy()
    mild[2] += [.018, -.012, .02]
    added = Mesh(np.concatenate([a.vertices, a.vertices*.12+[1, .5, .5]]),
                 np.concatenate([a.faces, a.faces+4]))
    sphere = trimesh.creation.icosphere(subdivisions=1)
    sphere.vertices *= np.ptp(a.vertices, axis=0)/np.ptp(sphere.vertices, axis=0)
    return a, [
        ("renamed_identity", True, Mesh(a.vertices.copy(), a.faces.copy())),
        ("proper_rotation_translation_uniform_scale", True, Mesh(rigid, a.faces)),
        ("vertex_order_seams_materials", True, Mesh(a.vertices[a.faces].reshape(-1, 3), np.arange(12).reshape(4, 3))),
        ("retessellation", True, Mesh(split_v, split_f)),
        ("mild_deformation", True, Mesh(mild, a.faces)),
        ("small_added_structure", True, added),
        ("equal_dimensions_different_shape", False, Mesh(sphere.vertices, sphere.faces)),
        ("nonuniform_stretch", False, Mesh(a.vertices*[1, 2.5, .5], a.faces)),
        ("mirrored_asymmetric", False, Mesh(a.vertices*[-1, 1, 1], a.faces)),
        ("partial_contained_in_larger_assembly", False,
         Mesh(np.concatenate([a.vertices, a.vertices+[7, 0, 0]]), np.concatenate([a.faces, a.faces+4]))),
        ("changed_bow", False, Mesh(a.vertices+np.array([[0, 0, 0], [0, 4, 2], [0, 0, 0], [0, 0, 0]]), a.faces)),
    ]


def main():
    rows = []
    # Parameters are fixed in source before running either split. Labels derive
    # from construction, not the resulting score; never relabel a failed case.
    cutoff = 90.
    for split, seed in (("calibration", 2), ("heldout", 17)):
        a, controls = cases(seed)
        for name, positive, b in controls:
            result = compare(a, b, count=1024, seed=0)
            repeat = compare(a, b, count=1024, seed=1)
            rows.append(dict(split=split, name=name, positive=positive,
                             predicted=result["geometry_score"] >= cutoff,
                             repeat_score=repeat["geometry_score"],
                             repeatability_delta=abs(result["geometry_score"]-repeat["geometry_score"]),
                             **result))
    confusion = {}
    for split in ("calibration", "heldout"):
        selected = [r for r in rows if r["split"] == split]
        confusion[split] = {key: sum(r["positive"] == truth and r["predicted"] == predicted for r in selected)
                            for key, truth, predicted in (("tp", True, True), ("fn", True, False),
                                                         ("fp", False, True), ("tn", False, False))}
    print(json.dumps(dict(schema="x4similar.synthetic-qualification/1", scorer=SCORER_VERSION,
                          implementation_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [Path(__file__), Path(__file__).parents[1]/"x4validate/_geometry_score.py",
                               Path(__file__).parents[1]/"x4validate/_geometry_mesh.py"]},
                          dependencies=dict(numpy=np.__version__, scipy=scipy.__version__, trimesh=trimesh.__version__),
                          tolerances=TOLERANCES, weights=WEIGHTS, samples=1024,
                          provisional_synthetic_cutoff=cutoff, confusion=confusion, pairs=rows,
                          limitation="Authored synthetic controls only; no real-hull threshold or historical-version claim"),
                     indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

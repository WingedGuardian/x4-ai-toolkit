"""Instanced surfaces with bounded payload residency and complete distance queries.

Only positive uniform scale and rigid frames are supported. No transformed
whole-ship triangle array is needed. Spatial indexes live for one payload group
and are explicitly closed, including on work-budget refusal.
"""
from __future__ import annotations

from collections import OrderedDict
import itertools
import numpy as np
import trimesh

from ._geometry_mesh import GeometryError, decode, digest, validate


class Scene:
    CACHE_BYTES = 64 * 1024 * 1024
    INDEX_BYTES = 128 * 1024 * 1024

    def __init__(self, world, instances):
        self.world, self.instances = world, instances
        self.cache, self.cache_bytes = OrderedDict(), 0
        self.peak_cache_bytes = 0
        self.reads = 0
        self.closed = False

    def counts(self):
        return tuple(sum(i[k] for i in self.instances) for k in ("vertices", "triangles"))

    @classmethod
    def from_mesh(cls, mesh):
        """In-memory adapter for authored controls and existing Mesh callers."""
        validate(mesh)
        size = mesh.vertices.nbytes+mesh.faces.nbytes
        if size > cls.CACHE_BYTES:
            raise GeometryError("in-memory mesh adapter cache budget exceeded")
        obj = cls(None, [dict(payload_sha256="in-memory", transform=np.eye(4).tolist(),
                              vertices=len(mesh.vertices), triangles=len(mesh.faces))])
        obj.cache["in-memory"] = mesh
        obj.cache_bytes = obj.peak_cache_bytes = size
        return obj

    def telemetry(self):
        return dict(decoded_cache_bytes=self.cache_bytes, peak_decoded_cache_bytes=self.peak_cache_bytes,
                    payload_reads=self.reads, decoded_cache_limit_bytes=self.CACHE_BYTES,
                    index_admission="conservative per-payload estimate; OS resource cap recommended")

    def release(self):
        self.cache.clear()
        self.cache_bytes = 0
        self.closed = True

    def mesh(self, instance):
        if self.closed:
            raise GeometryError("released scene cannot be scored")
        sha = instance["payload_sha256"]
        if sha in self.cache:
            self.cache.move_to_end(sha)
            return self.cache[sha]
        raw = self.world.binary(instance["mesh"], record=False)
        self.reads += 1
        if digest(raw) != sha:
            raise GeometryError("input drift during scene payload read")
        mesh = decode(raw)
        self.world.io["mesh_decodes"] += 1
        size = mesh.vertices.nbytes+mesh.faces.nbytes
        while self.cache and self.cache_bytes+size > self.CACHE_BYTES:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= old.vertices.nbytes+old.faces.nbytes
        if size <= self.CACHE_BYTES:
            self.cache[sha] = mesh
            self.cache_bytes += size
            self.peak_cache_bytes = max(self.peak_cache_bytes, self.cache_bytes)
        return mesh

    @staticmethod
    def frame(instance):
        matrix = np.asarray(instance["transform"], dtype=float)
        linear = matrix[:3, :3]
        scale = np.linalg.norm(linear[:, 0])
        if not np.isfinite(matrix).all() or scale <= 0 or not np.allclose((linear/scale).T @ (linear/scale), np.eye(3), rtol=1e-10, atol=1e-12) or np.linalg.det(linear/scale) <= 0:
            raise GeometryError("unsupported nonrigid/reflected instance frame")
        return linear, matrix[:3, 3], scale

    def prepare(self, count, seed):
        """Analytic moments in world coordinates; area-weighted instance sampling.

        Sampling consumes a fixed per-scene sequence, intentionally versioned
        separately from the historical concatenated-mesh sampler.
        """
        if not 128 <= count <= 16384:
            raise GeometryError("surface sample count must be 128..16384")
        rows, total, first, second = [], 0., np.zeros(3), np.zeros((3, 3))
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        discarded = 0
        # Accumulate about a nearby origin to avoid cancellation after large translations.
        origin = np.asarray(self.instances[0]["transform"])[:3, 3]
        for instance in self.instances:
            mesh = self.mesh(instance)
            linear, shift, scale = self.frame(instance)
            shift = shift-origin
            area_sum, local_first, local_second, dropped = 0., np.zeros(3), np.zeros((3, 3)), 0
            for start in range(0, len(mesh.faces), 2048):
                triangles = mesh.vertices[mesh.faces[start:start+2048]] @ linear.T+shift
                area = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)/2
                keep = area > 0
                dropped += int((~keep).sum())
                t, area = triangles[keep], area[keep]
                if not len(t):
                    continue
                lo = np.minimum(lo, t.min(axis=(0, 1))+origin)
                hi = np.maximum(hi, t.max(axis=(0, 1))+origin)
                sums = t.sum(axis=1)
                area_sum += area.sum()
                local_first += (t.mean(axis=1)*area[:, None]).sum(axis=0)
                local_second += (np.einsum('n,nti,ntj->ij', area, t, t)+np.einsum('n,ni,nj->ij', area, sums, sums))/12
            total += area_sum
            first += local_first
            second += local_second
            discarded += dropped
            rows.append(area_sum)
        if not np.isfinite(total) or total <= 0:
            raise GeometryError("zero/non-finite scene area")
        center = first/total
        covariance = second/total-np.outer(center, center)
        radius = np.sqrt(np.trace(covariance))
        if not np.isfinite(radius) or radius <= 0:
            raise GeometryError("zero/non-finite scene radius")
        center += origin
        rng = np.random.default_rng(seed)
        chosen = rng.choice(len(rows), count, p=np.asarray(rows)/total)
        points = np.empty((count, 3))
        for n, instance in enumerate(self.instances):
            ids = np.flatnonzero(chosen == n)
            if not len(ids):
                continue
            mesh = self.mesh(instance)
            # A bounded per-mesh area vector; never expands every triangle position.
            areas = np.empty(len(mesh.faces))
            for start in range(0, len(areas), 2048):
                t = mesh.vertices[mesh.faces[start:start+2048]]
                areas[start:start+len(t)] = np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1)
            face_ids = rng.choice(len(areas), len(ids), p=areas/areas.sum())
            t = mesh.vertices[mesh.faces[face_ids]]
            u, v = rng.random((2, len(ids)))
            u = np.sqrt(u)
            p = (1-u)[:, None]*t[:, 0]+(u*(1-v))[:, None]*t[:, 1]+(u*v)[:, None]*t[:, 2]
            linear, shift, _ = self.frame(instance)
            points[ids] = p @ linear.T+shift
        _, axes = np.linalg.eigh(covariance)
        return (points-center)/radius, center, radius, axes, hi-lo, discarded

    def distances(self, normalized_points, center, radius, work):
        from ._geometry_score import surface_distances
        points = normalized_points*radius+center
        result = np.full(len(points), np.inf)
        groups = {}
        for instance in self.instances:
            groups.setdefault(instance["payload_sha256"], []).append(instance)
        for instances in groups.values():
            mesh = self.mesh(instances[0])
            # Conservative admission estimate for vertices, triangle bounds and
            # native indexes. Runtime qualification additionally uses an OS cap.
            estimate = mesh.vertices.nbytes+mesh.faces.nbytes+len(mesh.faces)*256+len(mesh.vertices)*64
            if estimate > self.INDEX_BYTES:
                raise GeometryError("per-payload surface index memory budget exceeded")
            surface = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
            used = mesh.vertices[np.unique(mesh.faces)]
            local_lo, local_hi = used.min(axis=0), used.max(axis=0)
            corners = np.array(list(itertools.product(*zip(local_lo, local_hi))))
            try:
                for instance in instances:
                    linear, shift, scale = self.frame(instance)
                    bounds = corners @ linear.T+shift
                    delta = np.maximum(np.maximum(bounds.min(axis=0)-points, points-bounds.max(axis=0)), 0)
                    lower = np.linalg.norm(delta, axis=1)
                    selected = np.flatnonzero(lower <= result*(1+1e-12)+1e-9)
                    if not len(selected):
                        continue
                    local = (points[selected]-shift) @ linear/(scale*scale)
                    distances = surface_distances(surface, local, work)*scale
                    result[selected] = np.minimum(result[selected], distances)
            finally:
                tree = surface._cache.cache.get("triangles_tree")
                if tree is not None:
                    tree.close()
                surface._cache.clear()
                surface.vertices = np.empty((0, 3))
                surface.faces = np.empty((0, 3), dtype=np.int64)
        if not np.isfinite(result).all():
            raise GeometryError("incomplete scene distance query")
        return result/radius

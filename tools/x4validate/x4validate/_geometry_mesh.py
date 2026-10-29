"""Bounded XUMF triangle reader. No Blender code or game assets are distributed.

Only the explicitly supported wire subset is accepted. Coordinates remain in
X4's native XYZ frame; Blender's Y/Z display conversion is deliberately absent.
The format layout was inspected against the local Blender importer; extraction
qualification, including its limits, is documented in docs/GEOMETRY.md.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

import numpy as np

MAX_BYTES = 128 * 1024 * 1024
MAX_ITEMS = 4_000_000
DECODER_VERSION = "xumf-subset-1"
# Serialized vertex element sizes. Unknown elements refuse, even if not positions:
# their size is needed to locate later elements in an interleaved record.
ELEMENTS = {0: (4, "<f4", 1), 1: (8, "<f4", 2), 2: (12, "<f4", 3),
            3: (16, "<f4", 4), 4: (4, "u1", 4), 5: (4, "u1", 4),
            6: (4, "<i2", 2), 7: (8, "<i2", 4),
            15: (4, "<f2", 2), 16: (8, "<f2", 4)}


class GeometryError(ValueError):
    """Unresolved geometry, never evidence of dissimilarity."""


@dataclass
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray

    def triangles(self):
        return self.vertices[self.faces]


def validate(mesh: Mesh) -> Mesh:
    v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if (v.ndim != 2 or v.shape[1] != 3 or not len(v) or
            f.ndim != 2 or f.shape[1] != 3 or not len(f)):
        raise GeometryError("empty or invalid triangle mesh")
    # MAX_ITEMS bounds EACH serialized XMF buffer in decode(). An assembly can
    # legitimately combine more than one index buffer; retain a separate finite
    # in-memory mesh bound rather than reapply the wire-buffer limit to the sum.
    if len(v) > MAX_ITEMS or len(f) > MAX_ITEMS:
        raise GeometryError("mesh item budget exceeded")
    if not np.isfinite(v).all() or not np.issubdtype(f.dtype, np.integer):
        raise GeometryError("non-finite positions or noninteger indices")
    if f.min() < 0 or f.max() >= len(v):
        raise GeometryError("triangle index outside vertex buffer")
    t = mesh.triangles()
    with np.errstate(over="ignore", invalid="ignore"):
        areas = np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1)
    if not np.isfinite(areas).all():
        raise GeometryError("non-finite triangle area")
    if not np.any(areas > 0):
        raise GeometryError("all triangles degenerate")
    return mesh


def decode(data: bytes) -> Mesh:
    """Read one bounded little-endian v3 triangle-list XMF, or refuse.

    Header/table offsets, decompressed totals, stride, every referenced index and
    finite position are checked before the mesh can reach a numeric score.
    """
    def require(ok, why):
        if not ok:
            raise GeometryError(why)

    require(64 <= len(data) <= MAX_BYTES, "XMF byte budget or truncated header")
    magic, version, endian, table, _, count, stride, mats, mat_stride, _, primitive = \
        struct.unpack_from("<4sBBBBBBBB10sI", data)
    require(magic == b"XUMF", "not XUMF")
    require(version == 3 and endian == 0 and primitive == 4,
            f"unsupported XMF version/endian/primitive {version}/{endian}/{primitive}")
    require(table >= 64 and 0 < count <= 32 and stride >= 188,
            "invalid descriptor table")
    require(not mats or mat_stride == 136, "unsupported material record")
    base = table + count * stride + mats * mat_stride
    require(base <= len(data), "truncated descriptor/material table")
    positions, indices, expanded, ranges = [], [], 0, []
    for n in range(count):
        at = table + n * stride
        kind, usage_index, offset, compression, _, fmt, size, items, step, sections, _, elems = \
            struct.unpack_from("<IIII4sIIIII16sI", data, at)
        require(0 < items <= MAX_ITEMS and 0 < step <= 1024 and elems <= 16,
                "invalid buffer item/stride/element count")
        expected = items * step
        expanded += expected
        require(expanded <= MAX_BYTES, "expanded XMF byte budget exceeded")
        require(base + offset + size <= len(data), "buffer outside payload")
        require(compression in (0, 1), "unsupported compression")
        require(not any(offset < hi and offset+size > lo for lo, hi in ranges),
                "overlapping buffer payloads")
        ranges.append((offset, offset+size))
        payload = data[base+offset:base+offset+size]
        if compression:
            try:
                stream = zlib.decompressobj()
                payload = stream.decompress(payload, expected+1)
                require(stream.eof and not stream.unused_data and not stream.unconsumed_tail,
                        "truncated, trailing or oversized compressed stream")
            except zlib.error as exc:
                raise GeometryError(f"invalid zlib stream: {exc}") from exc
        require(len(payload) == expected, "buffer byte count differs from declaration")
        if kind == 30:
            require(step in (2, 4) and items % 3 == 0 and elems == 0,
                    "unsupported index format or nontriangle list")
            indices.append(np.frombuffer(payload, dtype=f"<u{step}").reshape(-1, 3).copy())
            continue
        layout = []
        if elems:
            for i in range(elems):
                typ, usage, channel, _ = struct.unpack_from("<IBB2s", data, at+60+i*8)
                layout.append((typ, usage, channel))
        else:
            layout.append((fmt, 0 if kind in (0, 1) else -1, usage_index))
        cursor = 0
        for typ, usage, channel in layout:
            require(typ in ELEMENTS, f"unsupported vertex element {typ}")
            width, dtype, components = ELEMENTS[typ]
            require(cursor+width <= step, "vertex layout exceeds stride")
            if usage == 0:
                require(typ in (2, 3, 16) and channel == 0,
                        "unsupported position encoding/channel")
                positions.append(np.ndarray((items, components), dtype=dtype,
                                            buffer=payload, offset=cursor,
                                            strides=(step, np.dtype(dtype).itemsize))[:, :3].astype(float))
            cursor += width
    require(len(positions) == 1 and len(indices) == 1,
            "requires exactly one position and one triangle index buffer")
    return validate(Mesh(positions[0], indices[0]))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

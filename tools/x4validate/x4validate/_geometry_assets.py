"""Read-only, explicit-scope render assembly. Never resolves by mesh basename.

XML merge semantics belong to _merge. Binary assets have their own loose/packed
resolver, bounded reads and byte receipts; the numeric effective DB is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re

import numpy as np
from lxml import etree
from scipy.spatial.transform import Rotation

from . import _cat, _merge
from ._geometry_mesh import MAX_BYTES, GeometryError, Mesh, decode, digest, validate

ASSEMBLY_VERSION = "rest-component-1"
MAX_PARTS = 128
MAX_TRIANGLES = 1_000_000
MAX_ASSEMBLED_VERTICES = 2_000_000


def safe_path(value):
    p = PurePosixPath(value.replace(chr(92), "/"))
    if p.is_absolute() or not p.parts or ".." in p.parts or ":" in str(p):
        raise GeometryError(f"unsafe asset path: {value!r}")
    return str(p)


def transform(node):
    """Native X4 column matrix for a position/quaternion offset; no display swap."""
    out = np.eye(4)
    if node is None:
        return out
    unknown = {c.tag for c in node if isinstance(c.tag, str)}-{"position", "quaternion", "scale"}
    if unknown:
        raise GeometryError(f"unsupported transform elements: {sorted(unknown)}")
    for tag in ("position", "quaternion", "scale"):
        if len(node.findall(tag)) > 1:
            raise GeometryError(f"duplicate {tag} transform")
    p = node.find("position")
    if p is not None:
        out[:3, 3] = [float(p.get(k, 0)) for k in "xyz"]
    q = node.find("quaternion")
    if q is not None:
        quat = np.array([float(q.get(k, 1 if k == "qw" else 0)) for k in ("qx", "qy", "qz", "qw")])
        if not np.isfinite(quat).all() or np.linalg.norm(quat) < 1e-12:
            raise GeometryError("invalid quaternion")
        out[:3, :3] = Rotation.from_quat(quat).as_matrix()
    scale = node.find("scale")
    if scale is not None:
        if set(scale.attrib)-{"value"}:
            raise GeometryError("unsupported nonuniform assembly scale")
        factor = float(scale.get("value", "1"))
        if factor <= 0:
            raise GeometryError("nonpositive assembly scale")
        out[:3, :3] *= factor
    if not np.isfinite(out).all():
        raise GeometryError("non-finite transform")
    return out


@dataclass
class Assembly:
    label: str
    mesh: Mesh | None = None
    included: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    receipts: list = field(default_factory=list)
    scope: dict = field(default_factory=dict)

    def report(self):
        return dict(label=self.label, status="partial" if self.mesh is not None and self.unresolved else
                    "unresolved" if self.mesh is None else "complete",
                    included=self.included, excluded=self.excluded, unresolved=self.unresolved,
                    receipts=self.receipts, scope=self.scope, assembly=ASSEMBLY_VERSION,
                    vertices=0 if self.mesh is None else len(self.mesh.vertices),
                    triangles=0 if self.mesh is None else len(self.mesh.faces))


class World:
    """One declared resolution tree. No global DB builds or candidate installation.

    overlays must already be in engine order. Aliases are explicit hypothetical
    namespaces, never guessed from asset names. Cache is per run, <=128 MiB; no
    persistent derived artifact can silently become stale.
    """
    def __init__(self, config, overlays=(), aliases=None, game=None):
        self.config, self.overlays = config, tuple(overlays)
        self.roots = [config.reference, *config.dlc_dirs(), *self.overlays]
        self.aliases = {k.lower(): Path(v) for k, v in (aliases or {}).items()}
        self.game = game
        self.game_version = None
        if game is not None and (game/"version.dat").is_file():
            version = (game/"version.dat").read_text(encoding="utf-8").strip()
            if version.isdigit():
                self.game_version = int(version)
        self.catalogs, self.receipts, self.cache = {}, [], {}
        self.catalog_stamps = {}
        self.cache_bytes = 0
        self.scope = dict(resolution="explicit ordered base+DLC+overlays",
                          sources=[str(p) for p in self.roots],
                          aliases={k: str(v) for k, v in self.aliases.items()},
                          pose="rest; animated structural assemblies unresolved",
                          appearance="materials/textures and collision compatibility not compared",
                          cache="per-run byte cache; 128 MiB; no persistent artifact")
        self.scope["game_version"] = self.game_version
        self.scope["ignored_version_catalogs"] = []

    def _catalog(self, root):
        if root not in self.catalogs:
            stamp = self.catalog_stamp(root)
            # Version-gated catalogs are unsupported by the shared reader. Refuse
            # rather than let its warning turn a partial asset set into a verdict.
            for p in self.catalog_paths(root):
                if _cat._VERSION_CAT_RE.match(p.name):
                    version = int(re.search(r"v(\d+)\.cat$", p.name, re.I).group(1))
                    if self.game_version is None or version == self.game_version:
                        raise GeometryError(f"matching/unknown-version catalog requires qualification: {root.name}/{p.name}")
                    self.scope["ignored_version_catalogs"].append(dict(owner=root.name, catalog=p.name,
                                                                       reason=f"v{version} does not match {self.game_version}"))
            cats = _cat.build_mod_vfs(root, xml_only=False)
            if root == self.game:
                for p in self.catalog_paths(root):
                    if re.fullmatch(r"\d+\.cat", p.name, re.I):
                        for m in _cat._read_index(p, False):
                            cats[m.path] = m
            folded = {}
            for k, v in cats.items():
                if k.lower() in folded and folded[k.lower()].path != k:
                    raise GeometryError(f"case-colliding packed assets: {k}")
                folded[k.lower()] = v
            self.catalogs[root] = folded
            if stamp != self.catalog_stamp(root):
                raise GeometryError(f"catalog set drifted while indexing {root.name}")
            self.catalog_stamps[root] = stamp
        return self.catalogs[root]

    @staticmethod
    def catalog_paths(root):
        return sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".cat"),
                      key=lambda p: p.name.lower()) if root.is_dir() else []

    @staticmethod
    def catalog_stamp(root):
        stamps = {}
        for p in World.catalog_paths(root):
            if p.stat().st_size > MAX_BYTES:
                raise GeometryError("catalog byte budget exceeded")
            data = p.read_bytes()
            if len(data) > MAX_BYTES:
                raise GeometryError("catalog grew beyond byte budget")
            if not p.name.lower().endswith("_sig.cat"):
                try:
                    lines = data.decode("utf-8-sig").splitlines()
                except UnicodeError as exc:
                    raise GeometryError(f"invalid catalog encoding: {p.name}") from exc
                if any(line.strip() and _cat._parse_line(line) is None for line in lines):
                    raise GeometryError(f"malformed catalog entry: {p.name}")
            stamps[p.name] = digest(data)
        return stamps

    def _raw(self, root, path):
        # Resolve each segment case-insensitively, detecting ambiguity on POSIX.
        p = root
        for segment in path.split("/"):
            if not p.is_dir():
                p = None
                break
            matches = [q for q in p.iterdir() if q.name.lower() == segment.lower()]
            if len(matches) > 1:
                raise GeometryError(f"ambiguous loose path {root.name}/{path}")
            if not matches:
                p = None
                break
            p = matches[0]
        if p is not None and p.is_file():
            if p.stat().st_size > MAX_BYTES:
                raise GeometryError("loose asset byte budget exceeded")
            with p.open("rb") as stream:
                data = stream.read(MAX_BYTES+1)
            if len(data) > MAX_BYTES:
                raise GeometryError("loose asset grew beyond byte budget")
            return data, dict(owner=root.name, path=path, storage="loose")
        member = self._catalog(root).get(path.lower())
        if member is None:
            return None
        if member.size > MAX_BYTES:
            raise GeometryError("packed asset byte budget exceeded")
        return _cat.read_member(member), dict(owner=root.name, path=member.path,
                                             storage=member.cat_path.name)

    def binary(self, value, record=True):
        value = safe_path(value)
        if value in self.cache and record:
            data, receipt = self.cache[value]
            self.receipts.append(receipt)
            return data
        parts = value.split("/")
        if len(parts) > 2 and parts[0].lower() == "extensions":
            folder, rel = parts[1].lower(), "/".join(parts[2:])
            targets = [p for p in self.roots[1:] if p.name.lower() == folder]
            if folder in self.aliases:
                targets = [self.aliases[folder]]
            if len(set(targets)) != 1:
                raise GeometryError(f"missing/ambiguous extension namespace {parts[1]}")
            owner = targets[0]
            # Nested binary replacements, then owner. Never fall back to another
            # owner's same-basename mesh.
            choices = [(p, value) for p in reversed(self.overlays) if p != owner]
            choices.append((owner, rel))
        else:
            choices = [(p, value) for p in reversed(self.roots)]
            if self.game is not None:
                choices.append((self.game, value))
        for root, path in choices:
            found = self._raw(root, path)
            if found is not None:
                data, receipt = found
                receipt = dict(receipt, sha256=digest(data), bytes=len(data), requested=value)
                if record:
                    self.receipts.append(receipt)
                    if self.cache_bytes+len(data) <= MAX_BYTES:
                        self.cache[value] = data, receipt
                        self.cache_bytes += len(data)
                return data
        raise GeometryError(f"missing asset {value}")

    def xml(self, vpath):
        vpath = safe_path(vpath)
        vpath = self.xml_namespace(vpath)
        result = _merge.build_effective(vpath, self.config, extra_overlays=list(self.overlays))
        if result.tree is None or result.skipped:
            raise GeometryError(f"incomplete XML merge {vpath}: {result.skipped}")
        # Merged bytes alone fingerprint effective semantics; drift checking also
        # rebuilds this tree after extraction so source changes cannot hide.
        raw = etree.tostring(result.tree)
        receipt = dict(path=vpath, storage="merged-xml", sha256=digest(raw),
                       contributors=result.sources)
        self.receipts.append(receipt)
        return result.tree

    def xml_namespace(self, vpath):
        parts = vpath.split("/")
        if len(parts) > 2 and parts[0].lower() == "extensions" and parts[1].lower() in self.aliases:
            parts[1] = self.aliases[parts[1].lower()].name
        return "/".join(parts)

    def component_path(self, name):
        # Use the effective index including diff overrides; no raw index reader.
        root = self.xml("index/components.xml")
        entries = [e for e in root.iter("entry") if e.get("name", "").lower() == name.lower()]
        if len(entries) != 1 or not entries[0].get("value"):
            raise GeometryError(f"component {name}: missing/ambiguous exact index entry; wildcard discovery unqualified")
        return safe_path(entries[0].get("value"))+".xml"

    def assemble(self, macro_path, label=None):
        assembly = Assembly(label or macro_path, scope=self.scope.copy())
        start = len(self.receipts)
        try:
            root = self.xml(macro_path)
            macros = root.findall("macro")
            if len(macros) != 1:
                raise GeometryError("expected exactly one macro")
            macro = macros[0]
            link = macro.find("component")
            if link is None or not link.get("ref"):
                raise GeometryError("macro lacks component reference")
            for c in macro.findall("connections/connection"):
                child = c.find("macro")
                if child is not None:
                    # Whole ship assembly needs macro-connected fixed modules.
                    # They are not silently classified as fitted equipment.
                    assembly.unresolved.append(f"macro-connected part role/assembly unqualified: {child.get('ref')}")
            path = self.component_path(link.get("ref"))
            tree = self.xml(path)
            components = [c for c in tree.findall("component") if c.get("name", "").lower() == link.get("ref").lower()]
            if len(components) != 1:
                raise GeometryError("indexed component absent/ambiguous in merged file")
            comp = components[0]
            source = comp.find("source")
            if source is None or not source.get("geometry"):
                raise GeometryError("component lacks geometry source")
            geometry = safe_path(source.get("geometry"))
            connections = comp.findall("connections/connection")
            parents, instances = {}, []
            for c in connections:
                for part in c.findall("parts/part"):
                    name = part.get("name")
                    if not name:
                        raise GeometryError("missing render part name")
                    parents.setdefault(name, []).append(c)
                    instances.append((name, c, part))
            def parent_connection(name):
                matches = list({id(c): c for c in parents.get(name, [])}.values())
                if len(matches) != 1:
                    raise GeometryError(f"missing/ambiguous render parent {name}")
                return matches[0]
            def matrix(c, seen=()):
                if id(c) in seen or len(seen) > MAX_PARTS:
                    raise GeometryError("cyclic/excessive render hierarchy")
                parent = c.get("parent")
                return (matrix(parent_connection(parent), (*seen, id(c))) if parent else np.eye(4)) @ transform(c.find("offset"))
            vertices, faces, offset, triangles = [], [], 0, 0
            if len(instances) > MAX_PARTS:
                raise GeometryError("assembly part budget exceeded")
            for name, c, part in instances:
                materials = [m.get("ref", "").lower() for m in part.findall("lods/lod/materials/material")]
                if materials and all("effects" in m for m in materials):
                    assembly.excluded.append(dict(part=name, reason="effect-only materials"))
                    continue
                try:
                    # Any animation along a structural part's ancestry makes the
                    # whole-hull verdict unresolved; rest geometry can still score.
                    ancestor, visited = c, set()
                    while ancestor is not None:
                        if id(ancestor) in visited:
                            raise GeometryError("cyclic render hierarchy")
                        visited.add(id(ancestor))
                        if ancestor.find("animations") is not None:
                            assembly.unresolved.append(f"animated structural ancestry: {name}")
                        ancestor = parent_connection(ancestor.get("parent")) if ancestor.get("parent") else None
                    if part.find("pivot") is not None:
                        raise GeometryError("pivot convention not independently qualified")
                    lods = [int(l.get("index")) for l in part.findall("lods/lod")]
                    if len(lods) != len(set(lods)) or any(lod < 0 or lod > 31 for lod in lods):
                        raise GeometryError("invalid/duplicate LOD index")
                    lod = min(lods) if lods else 0
                    if lod != 0:
                        assembly.unresolved.append(f"LOD0 absent for {name}; fallback LOD{lod}")
                    mesh_path = geometry+"/"+name+f"-lod{lod}.xmf"
                    mesh = decode(self.binary(mesh_path))
                    if offset+len(mesh.vertices) > MAX_ASSEMBLED_VERTICES:
                        raise GeometryError("assembled vertex budget exceeded")
                    mat = matrix(c) @ transform(part.find("offset"))
                    points = mesh.vertices @ mat[:3, :3].T+mat[:3, 3]
                    triangles += len(mesh.faces)
                    if triangles > MAX_TRIANGLES:
                        raise GeometryError("assembled triangle budget exceeded")
                    vertices.append(points)
                    # Serialized uint16 indices are local to each part. Assembly
                    # offsets must widen before addition (NumPy 2 refuses overflow).
                    faces.append(mesh.faces.astype(np.int64)+offset)
                    offset += len(points)
                    assembly.included.append(dict(part=name, lod=lod, mesh=mesh_path,
                                                  payload_sha256=self.receipts[-1]["sha256"],
                                                  owner=self.receipts[-1].get("owner"),
                                                  vertices=len(points), triangles=len(mesh.faces),
                                                  transform=mat.tolist(), role="component render part"))
                except (GeometryError, ValueError, OSError, etree.XMLSyntaxError) as exc:
                    assembly.unresolved.append(f"{name}: {exc}")
            if vertices:
                assembly.mesh = validate(Mesh(np.concatenate(vertices), np.concatenate(faces)))
            else:
                assembly.unresolved.append("no decoded structural surfaces")
        except (GeometryError, ValueError, OSError, etree.XMLSyntaxError) as exc:
            assembly.unresolved.append(str(exc))
        assembly.receipts = self.receipts[start:].copy()
        self.verify(assembly)
        return assembly

    def verify(self, assembly):
        """Recheck byte/catalog receipts, including after a long pair comparison."""
        for root, stamp in self.catalog_stamps.items():
            if stamp != self.catalog_stamp(root):
                assembly.unresolved.append(f"catalog input drift: {root.name}")
                assembly.mesh = None
        # Check actual bytes/effective trees again, bypassing the per-run cache.
        for receipt in assembly.receipts:
            try:
                if receipt["storage"] == "merged-xml":
                    result = _merge.build_effective(receipt["path"], self.config, extra_overlays=list(self.overlays))
                    if result.tree is None or result.skipped:
                        raise GeometryError("XML no longer resolves")
                    current = digest(etree.tostring(result.tree))
                else:
                    current = digest(self.binary(receipt["requested"], record=False))
                if current != receipt["sha256"]:
                    raise GeometryError("input bytes changed during extraction")
            except (GeometryError, OSError, ValueError, etree.XMLSyntaxError) as exc:
                assembly.unresolved.append(f"input drift/recheck failed: {exc}")
                assembly.mesh = None

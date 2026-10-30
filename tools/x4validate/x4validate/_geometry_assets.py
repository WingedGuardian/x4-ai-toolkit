"""Read-only, explicit-scope render assembly. Never resolves by mesh basename.

XML merge semantics belong to _merge. Binary assets have their own loose/packed
resolver, bounded reads and byte receipts; the numeric effective DB is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import copy
import re

import numpy as np
from lxml import etree
from scipy.spatial.transform import Rotation

from . import _cat, _merge
from ._geometry_mesh import MAX_BYTES, GeometryError, Mesh, decode, digest, validate

ASSEMBLY_VERSION = "recursive-bind-2"
MAX_PARTS = 128
MAX_TRIANGLES = 2_000_000
MAX_ASSEMBLED_VERTICES = 4_000_000


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
    unknown = {c.tag for c in node if isinstance(c.tag, str)}-{"position", "quaternion", "rotation", "scale"}
    if unknown:
        raise GeometryError(f"unsupported transform elements: {sorted(unknown)}")
    for tag in ("position", "quaternion", "rotation", "scale"):
        if len(node.findall(tag)) > 1:
            raise GeometryError(f"duplicate {tag} transform")
    p = node.find("position")
    if p is not None:
        out[:3, 3] = [float(p.get(k, 0)) for k in "xyz"]
    q = node.find("quaternion")
    e = node.find("rotation")
    if q is not None and e is not None:
        raise GeometryError("ambiguous quaternion and Euler transform")
    if e is not None:
        # Roll only: the native quaternion frame's forward Z axis. Its equivalent
        # Blender Y rotation has the opposite sign under Egosoft's Y/Z basis swap.
        # No mixed-axis Euler order is inferred from this single-axis mapping.
        if set(e.attrib)-{"yaw", "pitch", "roll"}:
            raise GeometryError("unsupported Euler attributes")
        yaw, pitch, roll = [float(e.get(k, "0")) for k in ("yaw", "pitch", "roll")]
        if not np.isfinite([yaw, pitch, roll]).all() or yaw != 0 or pitch != 0:
            raise GeometryError("unqualified mixed-axis Euler transform (roll-only supported)")
        out[:3, :3] = Rotation.from_rotvec([0., 0., np.deg2rad(roll % 360)]).as_matrix()
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
    attachments: list = field(default_factory=list)
    pose_notes: list = field(default_factory=list)
    seconds: float = 0.0
    #: (vertices, triangles) of a mesh freed by release(); None once verify() finds drift.
    released: tuple | None = None
    scene: object | None = None
    findings: list = field(default_factory=list)
    graph: list = field(default_factory=list)
    assets: list = field(default_factory=list)
    stages: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    assembly_version: str = ASSEMBLY_VERSION

    def finding(self, instance, code, message, stage, **details):
        self.findings.append(dict(instance=instance, code=code, message=str(message),
                                  stage=stage, **details))
        self.unresolved.append(f"{instance}: {message}")

    def release(self):
        """Free a scored candidate's mesh (each may hold ~144 MB of arrays; keeping every one
        until the end made memory grow with --geometry-limit). Its counts are kept for report()."""
        if self.mesh is not None:
            self.released = (len(self.mesh.vertices), len(self.mesh.faces))
            self.mesh = None
        if self.scene is not None:
            self.metrics["scene"] = self.scene.telemetry()
            self.released = self.scene.counts()
            self.scene.release()
            self.scene = None

    def report(self):
        if self.scene is not None:
            self.metrics["scene"] = self.scene.telemetry()
        has = self.mesh is not None or self.scene is not None or self.released is not None
        counts = (self.scene.counts() if self.scene is not None else
                  (len(self.mesh.vertices), len(self.mesh.faces)) if self.mesh is not None
                  else self.released or (0, 0))
        return dict(label=self.label, status="audited" if self.stages.get("audit") == "passed" and self.stages.get("verification") == "passed" else "partial" if has and self.unresolved else
                    "unresolved" if not has else "complete",
                    included=self.included, excluded=self.excluded, unresolved=self.unresolved,
                    attachments=self.attachments, pose_notes=self.pose_notes, seconds=self.seconds,
                    receipts=self.receipts, scope=self.scope, assembly=self.assembly_version,
                    vertices=counts[0], triangles=counts[1], findings=self.findings,
                    graph=self.graph, assets=self.assets, stages=self.stages, budgets=self.budgets, metrics=self.metrics)


class World:
    """One declared resolution tree. No global DB builds or candidate installation.

    overlays must already be in engine order. Aliases are explicit hypothetical
    namespaces, never guessed from asset names. Cache is per run, <=128 MiB; no
    persistent derived artifact can silently become stale.
    """
    def __init__(self, config, overlays=(), aliases=None, game=None, pose="strict"):
        if pose not in ("strict", "bind"):
            raise GeometryError("geometry pose must be strict or bind")
        self.pose = pose
        self.io = dict(binary_requests=0, storage_reads=0, storage_bytes=0, mesh_decodes=0)
        self.config, self.overlays = config, tuple(overlays)
        self.dlc_roots = tuple(config.dlc_dirs())
        self.roots = [config.reference, *self.dlc_roots, *self.overlays]
        self.aliases = {k.lower(): Path(v) for k, v in (aliases or {}).items()}
        self.game = game
        self.game_version = None
        if game is not None and (game/"version.dat").is_file():
            version = (game/"version.dat").read_text(encoding="utf-8").strip()
            if version.isdigit():
                self.game_version = int(version)
        self.catalogs, self.receipts, self.cache = {}, [], {}
        self.catalog_stamps = {}
        self.xml_cache = {}
        self.xml_cache_bytes = 0
        self.cache_bytes = 0
        self.scope = dict(resolution="explicit ordered base+DLC+overlays",
                          sources=[str(p) for p in self.roots],
                          aliases={k: str(v) for k, v in self.aliases.items()},
                          pose=pose,
                          pose_definition="XML offsets and mesh bind geometry; animation tracks not evaluated",
                          appearance="materials/textures and collision compatibility not compared",
                          cache="per-run byte cache; 128 MiB; no persistent artifact")
        self.scope["game_version"] = self.game_version
        self.scope["ignored_version_catalogs"] = []
        self.scope["binary_dlc_fallbacks"] = {str(p): str(self.game/"extensions"/p.name)
                                               for p in self.dlc_roots} if self.game is not None else {}

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
            self.io["storage_reads"] += 1
            self.io["storage_bytes"] += len(data)
            return data, dict(owner=root.name, path=path, storage="loose", binary_root=str(root))
        member = self._catalog(root).get(path.lower())
        if member is None:
            return None
        if member.size > MAX_BYTES:
            raise GeometryError("packed asset byte budget exceeded")
        data = _cat.read_member(member)
        self.io["storage_reads"] += 1
        self.io["storage_bytes"] += len(data)
        return data, dict(owner=root.name, path=member.path, binary_root=str(root),
                                             storage=member.cat_path.name)

    def binary_choices(self, value):
        """Ordered exact VFS candidates, shared by reads and refusal diagnostics."""
        value = safe_path(value)
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
            choices.extend((p, rel) for p in self.binary_roots(owner))
        else:
            choices = [(p, value) for root in reversed(self.roots) for p in self.binary_roots(root)]
            if self.game is not None:
                choices.append((self.game, value))
        return choices

    def binary_roots(self, root):
        """A configured DLC's XML reference may be text-only.

        Its matching installed DLC remains a binary provider. This fallback is
        restricted to configured DLC roots, never an arbitrary candidate alias.
        """
        yield root
        if self.game is None or root not in self.dlc_roots:
            return
        parent = self.game/"extensions"
        matches = [p for p in parent.iterdir() if p.is_dir() and p.name.lower() == root.name.lower()] if parent.is_dir() else []
        if len(matches) > 1:
            raise GeometryError(f"ambiguous installed DLC binary root {root.name}")
        if matches and matches[0].resolve() != root.resolve():
            yield matches[0]

    def trace_binary(self, value):
        """Name every considered loose path/catalog; never basename-fallback."""
        rows = []
        for root, path in self.binary_choices(value):
            row = dict(owner=root.name, root=str(root), requested=path,
                       catalogs=[p.name for p in self.catalog_paths(root)])
            try:
                found = self._raw(root, path)
                row["status"] = "absent" if found is None else "present"
                if found is not None:
                    row["provider"] = found[1]
            except (GeometryError, OSError, ValueError) as exc:
                row.update(status="unsupported", reason=str(exc))
            rows.append(row)
        return rows

    def binary(self, value, record=True):
        self.io["binary_requests"] += 1
        value = safe_path(value)
        if value in self.cache and record:
            data, receipt = self.cache[value]
            self.receipts.append(receipt)
            return data
        choices = self.binary_choices(value)
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
        if vpath in self.xml_cache:
            tree, receipt = self.xml_cache[vpath]
            self.receipts.append(receipt)
            return copy.deepcopy(tree)
        result = _merge.build_effective(vpath, self.config, extra_overlays=list(self.overlays))
        if result.tree is None or result.skipped:
            raise GeometryError(f"incomplete XML merge {vpath}: {result.skipped}")
        # Merged bytes alone fingerprint effective semantics; drift checking also
        # rebuilds this tree after extraction so source changes cannot hide.
        raw = etree.tostring(result.tree)
        receipt = dict(path=vpath, storage="merged-xml", sha256=digest(raw),
                       contributors=result.sources)
        self.receipts.append(receipt)
        if self.xml_cache_bytes+len(raw) <= MAX_BYTES:
            self.xml_cache[vpath] = copy.deepcopy(result.tree), receipt
            self.xml_cache_bytes += len(raw)
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

    def macro_path(self, name):
        root = self.xml("index/macros.xml")
        entries = [e for e in root.iter("entry") if e.get("name", "").lower() == name.lower()]
        if len(entries) != 1 or not entries[0].get("value"):
            raise GeometryError(f"macro {name}: missing/ambiguous exact index entry")
        return safe_path(entries[0].get("value"))+".xml"

    def assemble(self, macro_path, label=None, *, instanced=False, audit=False, max_instances=512):
        if instanced or audit:
            from ._geometry_graph import build
            return build(self, macro_path, label, audit=audit, max_instances=max_instances)
        from ._geometry_assembly import assemble
        return assemble(self, macro_path, label)

    def verify(self, assembly):
        """Recheck byte/catalog receipts, including after a long pair comparison."""
        for root, stamp in self.catalog_stamps.items():
            try:
                if stamp != self.catalog_stamp(root):
                    raise GeometryError(f"catalog input drift: {root.name}")
            except (GeometryError, OSError, ValueError) as exc:
                assembly.finding(str(root), "input_drift", f"catalog input drift/recheck failed: {exc}", "verification")
                assembly.mesh = assembly.released = None
                if assembly.scene is not None:
                    assembly.scene.release()
                    assembly.scene = None
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
                assembly.finding(receipt["path"], "input_drift", f"input drift/recheck failed: {exc}", "verification")
                assembly.mesh = assembly.released = None
                if assembly.scene is not None:
                    assembly.scene.release()
                    assembly.scene = None
        if any(f["code"] == "input_drift" for f in assembly.findings):
            assembly.stages["verification"] = "failed"
            assembly.stages["comparison"] = "unavailable"

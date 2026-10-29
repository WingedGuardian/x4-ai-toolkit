"""Bounded, recursive structural geometry in a declared XML bind pose.

Fitted equipment classes are excluded; virtual components are accounted for from
their actual lack of source/parts, never from their names. Animation semantics
are a separate evidence axis. Strict mode continues to refuse them.
"""
from __future__ import annotations

import time

import numpy as np
from lxml import etree

from ._geometry_assets import Assembly, MAX_ASSEMBLED_VERTICES, MAX_TRIANGLES, transform
from ._geometry_mesh import GeometryError, Mesh, decode, validate

MAX_INSTANCES = 512
MAX_MACROS = 128
MAX_DEPTH = 16
EQUIPMENT_CLASSES = frozenset({"engine", "thruster", "weapon", "turret", "shieldgenerator",
                               "missile", "bullet", "countermeasure"})
FAILURES = (GeometryError, ValueError, OSError, etree.XMLSyntaxError)


def referenced_part(world, part, geometry):
    """Resolve a shared mesh payload; its provider's placement is NOT instanced.

    The consumer supplies the connector/parent frame. Provider XML contributes
    the referenced mesh, LOD/material declarations and source-byte provenance.
    """
    chain = []
    current = part
    while current.get("ref"):
        ref = current.get("ref")
        if ref in chain or len(chain) >= MAX_DEPTH or "." not in ref:
            raise GeometryError("cyclic/excessive/invalid render-part reference")
        chain.append(ref)
        owner, name = ref.split(".", 1)
        path = world.component_path(owner)
        root = world.xml(path)
        components = [c for c in root.findall("component") if c.get("name", "").lower() == owner.lower()]
        if len(components) != 1:
            raise GeometryError(f"referenced component {owner} absent/ambiguous")
        component = components[0]
        matches = [p for p in component.findall("connections/connection/parts/part") if p.get("name", "").lower() == name.lower()]
        if len(matches) != 1:
            raise GeometryError(f"referenced part {ref} absent/ambiguous")
        current = matches[0]
        source = component.find("source")
        geometry = source.get("geometry") if source is not None else None
    if not geometry:
        raise GeometryError("referenced part lacks geometry source")
    return current, geometry, chain


class Frames:
    """Component-local connector/part frames, including parent part offsets."""
    def __init__(self, component):
        self.connections = component.findall("connections/connection")
        self.parts, self.cache = {}, {}
        for c in self.connections:
            for p in c.findall("parts/part"):
                if not p.get("name"):
                    raise GeometryError("missing render part name")
                self.parts.setdefault(p.get("name"), []).append((c, p))

    def frame(self, connection, seen=()):
        key = id(connection)
        if key in seen or len(seen) >= MAX_INSTANCES:
            raise GeometryError("cyclic/excessive render hierarchy")
        if key in self.cache:
            return self.cache[key]
        parent = connection.get("parent")
        matrix = np.eye(4)
        if parent:
            matches = self.parts.get(parent, [])
            if len(matches) != 1:
                raise GeometryError(f"missing/ambiguous render parent {parent}")
            c, p = matches[0]
            matrix = self.frame(c, (*seen, key)) @ transform(p.find("offset"))
        result = matrix @ transform(connection.find("offset"))
        self.cache[key] = result
        return result

    def port(self, name):
        found = [c for c in self.connections if c.get("name", "").lower() == name.lower()]
        if len(found) != 1:
            raise GeometryError(f"missing/ambiguous attachment port {name}")
        return self.frame(found[0])


def assemble(world, macro_path, label=None):
    started = time.perf_counter()
    result = Assembly(label or macro_path, scope=world.scope.copy())
    receipt_start = len(world.receipts)
    vertices, faces = [], []
    total_vertices = total_triangles = instances = macros = 0

    def failure(instance, exc):
        result.unresolved.append(f"{instance}: {exc}")

    def visit(path, instance, parent_world=None, attach=None, ancestry=()):
        nonlocal total_vertices, total_triangles, instances, macros
        macros += 1
        if macros > MAX_MACROS or len(ancestry) >= MAX_DEPTH:
            raise GeometryError("macro assembly depth/count budget exceeded")
        tree = world.xml(path)
        definitions = tree.findall("macro")
        if attach is not None:
            definitions = [m for m in definitions if m.get("name", "").lower() == attach[2].lower()]
        if len(definitions) != 1:
            raise GeometryError("expected one unambiguous macro definition")
        macro = definitions[0]
        name = macro.get("name", "")
        if name.lower() in ancestry:
            raise GeometryError(f"cyclic macro assembly at {name}")
        klass = macro.get("class", "")
        if parent_world is not None and klass in EQUIPMENT_CLASSES:
            result.excluded.append(dict(instance=instance, macro=name, reason=f"fitted equipment class {klass}"))
            return
        link = macro.find("component")
        if link is None or not link.get("ref"):
            raise GeometryError("macro lacks component reference")
        comp_path = world.component_path(link.get("ref"))
        component_tree = world.xml(comp_path)
        components = [c for c in component_tree.findall("component") if c.get("name", "").lower() == link.get("ref").lower()]
        if len(components) != 1:
            raise GeometryError("indexed component absent/ambiguous in merged file")
        component = components[0]
        for euler in component.findall('.//offset/rotation'):
            roll = float(euler.get('roll', '0'))
            if np.isfinite(roll) and abs(roll % 180) > 1e-10:
                note = f"{instance}: roll-only native-quaternion bind projection; runtime Euler parser not engine-tested"
                if world.pose == 'strict':
                    failure(instance, note)
                else:
                    result.pose_notes.append(note)
        frames = Frames(component)
        source = component.find("source")
        geometry = source.get("geometry") if source is not None else None
        part_count = sum(len(c.findall("parts/part")) for c in frames.connections)
        children = macro.findall("connections/connection")
        virtual_leaf = not geometry and not part_count and not any(c.find("macro") is not None for c in children)
        if virtual_leaf:
            result.attachments.append(dict(instance=instance, macro=name, macro_class=klass,
                                           component=link.get("ref"), component_path=comp_path,
                                           geometry=None, render_parts=0, transform=None,
                                           parent_port=None if attach is None else attach[3],
                                           child_port=None if attach is None else attach[1],
                                           placement_status="not evaluated: no render source, parts or structural descendants"))
            result.excluded.append(dict(instance=instance, macro=name, reason="resolved component has no render source or parts"))
            return
        # Attachment ports can animate even when they have no render part of
        # their own. Conservatively inventory all declared connection animation
        # here so descendants cannot bypass strict pose qualification.
        if any(c.find("animations") is not None for c in frames.connections):
            note = f"{instance}: component declares animation; XML bind geometry does not evaluate tracks"
            if world.pose == "strict":
                failure(instance, note)
            else:
                result.pose_notes.append(note)
        matrix = np.eye(4) if parent_world is None else parent_world @ attach[0].port(attach[3]) @ np.linalg.inv(frames.port(attach[1]))
        result.attachments.append(dict(instance=instance, macro=name, macro_class=klass,
                                       component=link.get("ref"), component_path=comp_path,
                                       geometry=geometry, render_parts=part_count, transform=matrix.tolist(),
                                       parent_port=None if attach is None else attach[3],
                                       child_port=None if attach is None else attach[1]))
        if not geometry and not part_count:
            result.excluded.append(dict(instance=instance, macro=name, reason="resolved component has no render source or parts"))
        elif not geometry:
            failure(instance, "render parts lack geometry source")
        else:
            from ._geometry_assets import safe_path
            geometry = safe_path(geometry)
            for c in frames.connections:
                for part in c.findall("parts/part"):
                    instances += 1
                    if instances > MAX_INSTANCES:
                        raise GeometryError("assembly part budget exceeded")
                    part_name = part.get("name")
                    part_id = instance+"/"+c.get("name", "?")+"/"+part_name
                    materials = [m.get("ref", "").lower() for m in part.findall("lods/lod/materials/material")]
                    if materials and all("effects" in m for m in materials):
                        result.excluded.append(dict(instance=part_id, part=part_name, reason="effect-only materials"))
                        continue
                    try:
                        provider, mesh_geometry, reference_chain = referenced_part(world, part, geometry)
                        if reference_chain:
                            note = f"{part_id}: shared mesh payload uses consumer XML frame, provider placement is not instanced"
                            result.pose_notes.append(note)
                            if world.pose == "strict":
                                failure(part_id, "referenced render-part animation state not qualified in strict pose")
                        if not materials:
                            inherited = [m.get("ref", "").lower() for m in provider.findall("lods/lod/materials/material")]
                            if inherited and all("effects" in m for m in inherited):
                                result.excluded.append(dict(instance=part_id, part=part_name,
                                                            reason="effect-only inherited materials", reference_chain=reference_chain))
                                continue
                        # Inventory the entire ancestry before treating a pose as
                        # strict. Bind mode evaluates XML frames only, visibly.
                        ancestor, seen = c, set()
                        while ancestor is not None:
                            if id(ancestor) in seen:
                                raise GeometryError("cyclic render hierarchy")
                            seen.add(id(ancestor))
                            if ancestor.find("animations") is not None:
                                message = f"animated structural ancestry: {part_id}"
                                if world.pose == "strict":
                                    failure(part_id, message)
                                else:
                                    result.pose_notes.append(message+"; XML bind frame, tracks not evaluated")
                            parent = ancestor.get("parent")
                            if not parent:
                                ancestor = None
                            else:
                                matches = frames.parts.get(parent, [])
                                if len(matches) != 1:
                                    raise GeometryError(f"missing/ambiguous render parent {parent}")
                                ancestor = matches[0][0]
                        pivot = part.find("pivot")
                        if pivot is not None:
                            if world.pose == "strict":
                                raise GeometryError("pivot convention not independently qualified in strict pose")
                            if set(p.tag for p in pivot if isinstance(p.tag, str))-{"offset"} or len(pivot.findall("offset")) != 1:
                                raise GeometryError("unsupported pivot grammar")
                            transform(pivot.find("offset"))
                            result.pose_notes.append(f"{part_id}: pivot reparameterizes bind geometry; animated pivot not evaluated")
                        declarations = part if part.find("lods") is not None else provider
                        lods = [int(l.get("index")) for l in declarations.findall("lods/lod")]
                        if len(lods) != len(set(lods)) or any(i < 0 or i > 31 for i in lods):
                            raise GeometryError("invalid/duplicate LOD index")
                        lod = min(lods) if lods else 0
                        if lod != 0:
                            failure(part_id, f"LOD0 absent; fallback LOD{lod}")
                        mesh_path = safe_path(mesh_geometry)+"/"+provider.get("name")+f"-lod{lod}.xmf"
                        mesh = decode(world.binary(mesh_path))
                        receipt = world.receipts[-1]
                        if total_vertices+len(mesh.vertices) > MAX_ASSEMBLED_VERTICES:
                            raise GeometryError("assembled vertex budget exceeded")
                        if total_triangles+len(mesh.faces) > MAX_TRIANGLES:
                            raise GeometryError("assembled triangle budget exceeded")
                        placement = matrix @ frames.frame(c) @ transform(part.find("offset"))
                        vertices.append(mesh.vertices @ placement[:3, :3].T+placement[:3, 3])
                        faces.append(mesh.faces.astype(np.int64)+total_vertices)
                        total_vertices += len(mesh.vertices)
                        total_triangles += len(mesh.faces)
                        result.included.append(dict(instance=part_id, macro=name, part=part_name, lod=lod,
                                                    mesh=mesh_path, payload_sha256=receipt["sha256"], owner=receipt.get("owner"),
                                                    vertices=len(mesh.vertices), triangles=len(mesh.faces),
                                                    transform=placement.tolist(), role="structural render part"))
                        result.included[-1]["reference_chain"] = reference_chain
                    except FAILURES as exc:
                        failure(part_id, exc)
        for number, connection in enumerate(macro.findall("connections/connection")):
            child = connection.find("macro")
            if child is None:
                continue
            child_id = instance+f"/{connection.get('ref', '?')}[{number}]"
            try:
                ref, parent_port, child_port = child.get("ref"), connection.get("ref"), child.get("connection")
                if not ref or not parent_port or not child_port or len(connection.findall("macro")) != 1:
                    raise GeometryError("incomplete/ambiguous structural connection")
                if connection.find("offset") is not None or child.find("offset") is not None:
                    raise GeometryError("macro connection offset not independently qualified")
                visit(world.macro_path(ref), child_id, matrix, (frames, child_port, ref, parent_port), (*ancestry, name.lower()))
            except FAILURES as exc:
                failure(child_id, exc)
    try:
        visit(macro_path, result.label)
        if vertices:
            result.mesh = validate(Mesh(np.concatenate(vertices), np.concatenate(faces)))
        else:
            failure(result.label, "no decoded structural surfaces")
    except FAILURES as exc:
        failure(result.label, exc)
    # References/instances can use the same merged index hundreds of times.
    # Preserve each actual input once per assembly; instance transforms remain
    # separately inventoried, and verify() always bypasses the XML cache.
    distinct = {}
    for receipt in world.receipts[receipt_start:]:
        key = (receipt['storage'], receipt.get('requested', receipt['path']), receipt['sha256'])
        distinct.setdefault(key, receipt)
    result.receipts = list(distinct.values())
    world.verify(result)
    result.seconds = time.perf_counter()-started
    return result

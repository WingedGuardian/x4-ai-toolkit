"""Bounded structural discovery, payload audit and placement qualification.

Discovery never evaluates a transform. Asset decoding precedes placement, so
an unsupported parent cannot conceal an absent payload. Runtime animation and
collision are deliberately outside this contract.
"""
from __future__ import annotations

import time
import numpy as np

from ._geometry_assembly import Frames, MAX_DEPTH, MAX_MACROS, EQUIPMENT_CLASSES, FAILURES, referenced_part
from ._geometry_assets import Assembly, safe_path, transform
from ._geometry_mesh import GeometryError, decode
from ._geometry_scene import Scene

MAX_GRAPH_INSTANCES = 2048


def reason(exc, stage):
    """Classify at the operation boundary; no failure becomes an exclusion."""
    text = str(exc)
    if "budget" in text or "excessive" in text:
        return "budget_reached"
    if "cyclic" in text:
        return "cycle"
    if stage == "placement":
        return "parent_unresolved" if "parent" in text else "unsupported_placement"
    if stage == "decode":
        return "decode_failed"
    return "reference_unresolved"


def build(world, macro_path, label=None, *, audit=False, max_instances=512):
    if not 1 <= max_instances <= MAX_GRAPH_INSTANCES:
        raise GeometryError("render instance limit must be 1..2048")
    started = time.perf_counter()
    result = Assembly(label or macro_path, scope=world.scope.copy(), assembly_version="structural-graph-3")
    receipt_start = len(world.receipts)
    io_start = world.io.copy()
    result.budgets = dict(render_instances=max_instances, macro_instances=MAX_MACROS,
                          depth=MAX_DEPTH, decoded_cache_bytes=Scene.CACHE_BYTES,
                          per_surface_index_estimate_bytes=Scene.INDEX_BYTES)
    nodes, parts, pending = [], [], [(macro_path, result.label, None, None, ())]
    frontier, exhaustive, attempted = [], True, 0

    def fail(instance, exc, stage, code=None):
        result.finding(instance, code or reason(exc, stage), exc, stage)

    # Ordered DFS gives stable instance identities without treating names as keys.
    while pending:
        path, identity, parent, link, ancestry = pending.pop()
        if attempted >= MAX_MACROS:
            frontier.append(dict(instance=identity, path=path, reason="macro count budget"))
            exhaustive = False
            break
        attempted += 1
        try:
            if len(ancestry) >= MAX_DEPTH:
                raise GeometryError("macro assembly depth budget exceeded")
            root = world.xml(path)
            definitions = root.findall("macro")
            if link is not None:
                definitions = [m for m in definitions if m.get("name", "").lower() == link[0].lower()]
            if len(definitions) != 1:
                raise GeometryError("expected one unambiguous macro definition")
            macro = definitions[0]
            name, klass = macro.get("name", ""), macro.get("class", "")
            if name.lower() in ancestry:
                raise GeometryError(f"cyclic macro assembly at {name}")
            record = dict(instance=identity, macro=name, macro_class=klass, path=path,
                          parent_instance=None if parent is None else parent["record"]["instance"])
            result.graph.append(record)
            if parent is not None and klass in EQUIPMENT_CLASSES:
                result.excluded.append(dict(instance=identity, macro=name, reason=f"fitted equipment class {klass}"))
                nodes.append(dict(record=record, excluded=True))
                continue
            component_link = macro.find("component")
            if component_link is None or not component_link.get("ref"):
                raise GeometryError("macro lacks component reference")
            component_path = world.component_path(component_link.get("ref"))
            components = [c for c in world.xml(component_path).findall("component")
                          if c.get("name", "").lower() == component_link.get("ref").lower()]
            if len(components) != 1:
                raise GeometryError("indexed component absent/ambiguous in merged file")
            component = components[0]
            source = component.find("source")
            geometry = source.get("geometry") if source is not None else None
            record.update(component=component_link.get("ref"), component_path=component_path)
            node = dict(record=record, component=component, parent=parent, link=link,
                        geometry=geometry, matrix=None, placed=False, frames=None)
            nodes.append(node)
            part_limit_hit = False
            for ci, connection in enumerate(component.findall("connections/connection")):
                for pi, part in enumerate(connection.findall("parts/part")):
                    if len(parts) >= max_instances:
                        frontier.append(dict(instance=identity, connection=ci, part=pi, reason="render part budget"))
                        exhaustive = False
                        part_limit_hit = True
                        break
                    part_id = f"{identity}/{connection.get('name', '?')}[{ci}]/{part.get('name', '?')}[{pi}]"
                    parts.append(dict(node=node, connection=connection, part=part, instance=part_id))
                if part_limit_hit:
                    break
            if part_limit_hit:
                break
            children = macro.findall("connections/connection")
            for number in reversed(range(len(children))):
                connection = children[number]
                child = connection.find("macro")
                if child is None:
                    continue
                child_id = identity+f"/{connection.get('ref', '?')}[{number}]"
                ref, parent_port, child_port = child.get("ref"), connection.get("ref"), child.get("connection")
                if not ref or not parent_port or not child_port or len(connection.findall("macro")) != 1:
                    fail(child_id, "incomplete/ambiguous structural connection", "discovery")
                    exhaustive = False
                    continue
                if len(pending) >= MAX_MACROS:
                    frontier.append(dict(instance=child_id, reason="pending macro budget", unexpanded_siblings=number+1))
                    exhaustive = False
                    break
                try:
                    pending.append((world.macro_path(ref), child_id, node,
                                    (ref, parent_port, child_port,
                                     connection.find("offset") is not None or child.find("offset") is not None),
                                    (*ancestry, name.lower())))
                except FAILURES as exc:
                    fail(child_id, exc, "discovery")
                    exhaustive = False
        except FAILURES as exc:
            fail(identity, exc, "discovery")
            exhaustive = False
    if pending or frontier:
        exhaustive = False
    frontier.extend(dict(instance=identity, path=path, reason="pending unexpanded macro root")
                    for path, identity, _, _, _ in pending)
    if frontier:
        fail(result.label, "structural graph budget reached; traversal frontier retained", "discovery", "budget_reached")
    result.stages["inventory"] = dict(status="complete" if exhaustive else "incomplete",
        macro_instances=len(result.graph), attempted_macros=attempted, discovered_parts=len(parts),
        total_parts=len(parts) if exhaustive else None,
        pending_macro_roots=len(pending), frontier=frontier)

    # Audit every mesh-bearing declaration, including surfaces excluded from scoring.
    # Mesh identities are byte hashes; occurrence identities remain distinct.
    decoded = {}
    for item in parts:
        instance, node, part = item["instance"], item["node"], item["part"]
        asset = dict(instance=instance, part=part.get("name"), status="unresolved")
        result.assets.append(asset)
        try:
            if not part.get("name"):
                raise GeometryError("missing render part name")
            provider, geometry, chain = referenced_part(world, part, node["geometry"])
            materials = [m.get("ref", "").lower() for m in part.findall("lods/lod/materials/material")]
            if not materials:
                materials = [m.get("ref", "").lower() for m in provider.findall("lods/lod/materials/material")]
            item["excluded"] = bool(materials and all("effects" in m for m in materials))
            if item["excluded"]:
                result.excluded.append(dict(instance=instance, part=part.get("name"),
                    reason="effect-only materials", scope="scoring only; mesh declaration still audited"))
            declarations = part if part.find("lods") is not None else provider
            lods = [int(l.get("index", "")) for l in declarations.findall("lods/lod")]
            if len(lods) != len(set(lods)) or any(i < 0 or i > 31 for i in lods):
                raise GeometryError("invalid/duplicate LOD index")
            lod = min(lods) if lods else 0
            mesh_path = safe_path(geometry)+"/"+provider.get("name")+f"-lod{lod}.xmf"
            asset.update(mesh=mesh_path, lod=lod, reference_chain=chain,
                         expected_from="component source geometry + render part + declared/default LOD")
            asset["lod_audit"] = []
            for audit_lod in sorted(lods or [0]):
                requested = safe_path(geometry)+"/"+provider.get("name")+f"-lod{audit_lod}.xmf"
                lod_result = dict(lod=audit_lod, mesh=requested, status="unresolved")
                asset["lod_audit"].append(lod_result)
                try:
                    raw = world.binary(requested)
                except FAILURES as exc:
                    lod_result["resolution_attempts"] = world.trace_binary(requested)
                    if audit_lod == lod:
                        asset["resolution_attempts"] = lod_result["resolution_attempts"]
                    fail(instance, exc, "assets", "asset_absent" if str(exc).startswith("missing asset ") else None)
                    continue
                receipt = world.receipts[-1]
                sha = receipt["sha256"]
                if sha not in decoded:
                    try:
                        mesh = decode(raw)
                        world.io["mesh_decodes"] += 1
                        decoded[sha] = (len(mesh.vertices), len(mesh.faces))
                        del mesh
                    except FAILURES as exc:
                        fail(instance, f"{requested}: {exc}", "decode")
                        continue
                lod_result.update(status="decoded", payload_sha256=sha, owner=receipt.get("owner"),
                                  vertices=decoded[sha][0], triangles=decoded[sha][1])
                if audit_lod == lod:
                    asset.update({k: v for k, v in lod_result.items() if k not in ("status",)})
                    item["asset"] = asset
            asset["status"] = "decoded" if all(r["status"] == "decoded" for r in asset["lod_audit"]) else "unresolved"
            if lod != 0:
                fail(instance, f"LOD0 absent; fallback LOD{lod}", "assets", "lod_fallback")
        except FAILURES as exc:
            fail(instance, exc, "assets")
    result.stages["assets"] = dict(status="passed" if exhaustive and all(a["status"] == "decoded" for a in result.assets) else "incomplete" if not exhaustive else "failed",
        visited_instances=len(result.assets), decoded_instances=sum(a["status"] == "decoded" for a in result.assets),
        decoded_unique_payloads=len(decoded), unresolved_instances=sum(a["status"] != "decoded" for a in result.assets),
        declared_lod_references=sum(len(a.get("lod_audit", [])) for a in result.assets),
        decoded_lod_references=sum(r["status"] == "decoded" for a in result.assets for r in a.get("lod_audit", [])))

    # Nodes are in parent-before-child order; an invalid ancestor affects only
    # placement, never discovery or the independent payload audit above.
    for node in nodes:
        if node.get("excluded"):
            continue
        record, component = node["record"], node["component"]
        identity = record["instance"]
        connection_count = sum(len(c.findall("parts/part")) for c in component.findall("connections/connection"))
        virtual = not node["geometry"] and not connection_count
        attachment = dict(**record, geometry=node["geometry"], render_parts=connection_count, transform=None,
                          parent_port=None if node["link"] is None else node["link"][1],
                          child_port=None if node["link"] is None else node["link"][2])
        result.attachments.append(attachment)
        try:
            node["frames"] = Frames(component, max_parts=MAX_GRAPH_INSTANCES)
            if virtual:
                result.excluded.append(dict(instance=identity, reason="resolved component has no render source or parts"))
                # Leaves do not require a fictitious anchor frame. Components
                # with structural children still need a frame to place them.
                if not any(n.get("parent") is node for n in nodes):
                    attachment["placement_status"] = "not evaluated: no render source, parts or structural descendants"
                    node["placed"] = True
                    continue
            if any(c.find("animations") is not None for c in node["frames"].connections):
                note = f"{identity}: component declares animation; XML bind geometry does not evaluate tracks"
                if world.pose == "strict":
                    fail(identity, note, "placement", "unqualified_pose")
                else:
                    result.pose_notes.append(note)
            for rotation in component.findall('.//offset/rotation'):
                roll = float(rotation.get("roll", "0"))
                if np.isfinite(roll) and abs(roll % 180) > 1e-10:
                    note = f"{identity}: roll-only bind projection; runtime Euler parser not engine-tested"
                    if world.pose == "strict":
                        fail(identity, note, "placement", "unqualified_pose")
                    else:
                        result.pose_notes.append(note)
            parent, link = node["parent"], node["link"]
            if parent is None:
                matrix = np.eye(4)
            else:
                if link[3]:
                    raise GeometryError("macro connection offset not independently qualified")
                if not parent["placed"] or parent["matrix"] is None:
                    raise GeometryError("structural ancestor placement unresolved")
                matrix = parent["matrix"] @ parent["frames"].port(link[1]) @ np.linalg.inv(node["frames"].port(link[2]))
            node.update(matrix=matrix, placed=True)
            attachment["transform"] = matrix.tolist()
        except FAILURES as exc:
            fail(identity, exc, "placement")

    included = []
    for item in parts:
        node, connection, part = item["node"], item["connection"], item["part"]
        if item.get("excluded"):
            continue
        try:
            if not node["placed"] or node["matrix"] is None:
                raise GeometryError("structural ancestor placement unresolved")
            frames = node["frames"]
            ancestor, seen = connection, set()
            while ancestor is not None:
                if id(ancestor) in seen:
                    raise GeometryError("cyclic render hierarchy")
                seen.add(id(ancestor))
                if ancestor.find("animations") is not None:
                    note = f"{item['instance']}: animated structural ancestry; XML bind frame, tracks not evaluated"
                    if world.pose == "strict":
                        fail(item["instance"], note, "placement", "unqualified_pose")
                    else:
                        result.pose_notes.append(note)
                parent = frames.parent(ancestor)
                ancestor = None if parent is None else parent[0]
            pivot = part.find("pivot")
            if pivot is not None:
                if world.pose == "strict":
                    raise GeometryError("pivot convention not independently qualified in strict pose")
                if {p.tag for p in pivot if isinstance(p.tag, str)}-{"offset"} or len(pivot.findall("offset")) != 1:
                    raise GeometryError("unsupported pivot grammar")
                transform(pivot.find("offset"))
                result.pose_notes.append(f"{item['instance']}: pivot reparameterizes bind geometry; animation not evaluated")
            asset = item.get("asset")
            if asset is None:
                continue
            if asset["reference_chain"] and world.pose == "strict":
                fail(item["instance"], "referenced render-part animation state not qualified in strict pose", "placement", "unqualified_pose")
            placement = node["matrix"] @ frames.frame(connection) @ transform(part.find("offset"))
            entry = dict(asset, macro=node["record"]["macro"], transform=placement.tolist(), role="structural render part")
            result.included.append(entry)
            included.append(entry)
        except FAILURES as exc:
            fail(item["instance"], exc, "placement")
    placement_failures = sum(f["stage"] == "placement" for f in result.findings)
    result.stages["placement"] = dict(status="qualified" if exhaustive and not placement_failures else "unresolved",
                                      unresolved_findings=placement_failures)
    if included and not audit:
        result.scene = Scene(world, included)
    result.stages["comparison"] = "not_requested" if audit else "available" if included else "unavailable"
    # Do not keep duplicated full receipt lists on each graph occurrence.
    result.receipts = list({(r['storage'], r.get('requested', r['path']), r['sha256']): r
                            for r in world.receipts[receipt_start:]}.values())
    result.stages["verification"] = "passed"
    world.verify(result)
    result.metrics["io_at_assembly_end"] = {k: world.io[k]-io_start[k] for k in io_start}
    if audit:
        result.stages["audit"] = "passed" if exhaustive and result.stages["assets"]["status"] == "passed" and result.stages["verification"] == "passed" else "failed"
    result.seconds = time.perf_counter()-started
    return result

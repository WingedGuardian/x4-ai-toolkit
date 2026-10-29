"""Authored geometry only: decoding, assembly refusals and held-out contrasts."""
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="optional geometry extra")
pytest.importorskip("scipy", reason="optional geometry extra")
trimesh = pytest.importorskip("trimesh", reason="optional geometry extra")
pytest.importorskip("rtree", reason="optional geometry extra")

from x4validate import _merge, _similarity
from x4validate._geometry_assets import World, safe_path, transform
from x4validate._geometry_mesh import GeometryError, Mesh, decode
from x4validate._geometry_score import compare, sample


def tetra():
    return Mesh(np.array([[0., 0, 0], [3, 0, 0], [.3, 1, 0], [.2, .1, 2]]),
                np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]))


def xmf(mesh=None, compressed=False, half=False, indices32=False):
    mesh = tetra() if mesh is None else mesh
    positions = mesh.vertices.astype("<f2" if half else "<f4")
    if half:
        positions = np.column_stack([positions, np.ones(len(positions), dtype="<f2")]).astype("<f2")
    indices = mesh.faces.astype("<u4" if indices32 else "<u2")
    payloads = [positions.tobytes(), indices.tobytes()]
    header = bytearray(64)
    struct.pack_into("<4sBBBBBBBB10sI", header, 0, b"XUMF", 3, 0, 64, 0, 2, 188, 0, 136, bytes(10), 4)
    descriptors, body = [], b""
    for i, raw in enumerate(payloads):
        data = zlib.compress(raw) if compressed else raw
        d = bytearray(188)
        struct.pack_into("<IIII4sIIIII16sI", d, 0, 0 if i == 0 else 30, 0, len(body),
                         int(compressed), bytes(4), 16 if half and i == 0 else 2,
                         len(data), len(mesh.vertices) if i == 0 else mesh.faces.size,
                         positions.dtype.itemsize*positions.shape[1] if i == 0 else indices.dtype.itemsize,
                         0, bytes(16), 0)
        descriptors.append(d)
        body += data
    return bytes(header)+b"".join(descriptors)+body


@pytest.mark.parametrize("compressed,half,wide", [(False, False, False), (True, False, True), (True, True, False)])
def test_decoder_coordinates_and_counts(compressed, half, wide):
    m = decode(xmf(compressed=compressed, half=half, indices32=wide))
    np.testing.assert_allclose(m.vertices, tetra().vertices, atol=.001)
    np.testing.assert_array_equal(m.faces, tetra().faces)


@pytest.mark.parametrize("offset,value", [(0, b"FAIL"), (4, b"\x09"), (5, b"\x01"),
                                         (22, struct.pack("<I", 5)),
                                         (64+20, struct.pack("<I", 99)),
                                         (64+32, struct.pack("<I", 1)),
                                         (64+56, struct.pack("<I", 17))])
def test_decoder_refuses_unsupported(offset, value):
    raw = bytearray(xmf())
    raw[offset:offset+len(value)] = value
    with pytest.raises(GeometryError):
        decode(bytes(raw))


@pytest.mark.parametrize("cut", [0, 63, 65, 440, -1])
def test_decoder_truncated(cut):
    with pytest.raises(GeometryError):
        decode(xmf()[:cut])


def test_decoder_invalid_geometry():
    m = tetra()
    m.vertices[0, 0] = np.nan
    with pytest.raises(GeometryError, match="non-finite"):
        decode(xmf(m))
    m = tetra()
    m.faces[0, 0] = 9
    with pytest.raises(GeometryError, match="outside vertex"):
        decode(xmf(m))


def fixture(tmp_path):
    def put(p, data):
        target = tmp_path/p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode() if isinstance(data, str) else data)
    put("index/components.xml", '<index><entry name="hull" value="assets/hull"/></index>')
    put("assets/ship_macro.xml", '<macros><macro name="ship" class="ship_s"><component ref="hull"/></macro></macros>')
    put("assets/hull.xml", '<components><component name="hull"><source geometry="assets/hull_data"/><connections><connection name="c"><offset><position x="10"/></offset><parts><part name="main"/></parts></connection></connections></component></components>')
    put("assets/hull_data/main-lod0.xmf", xmf())
    return World(_merge.Config(reference=tmp_path, include_packed_dlc=False)), put


def test_assembly_coordinate_oracle(tmp_path):
    world, _ = fixture(tmp_path)
    a = world.assemble("assets/ship_macro.xml")
    assert not a.unresolved, a.unresolved
    assert len(a.included) == 1
    assert a.report()["triangles"] == 4
    np.testing.assert_allclose(a.mesh.vertices, tetra().vertices+[10, 0, 0], atol=1e-6)
    assert len(a.receipts) == 4


@pytest.mark.parametrize("change,reason", [
    ('parent="missing"', "missing/ambiguous render parent"),
    ('parent="main"', "cyclic"),
])
def test_missing_and_cyclic_parent(tmp_path, change, reason):
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    put("assets/hull.xml", path.read_text().replace('name="c"', f'name="c" {change}'))
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is None and any(reason in s for s in a.unresolved)


def test_effect_exclusion_and_animation_refusal(tmp_path):
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    put("assets/hull.xml", path.read_text().replace('<parts>', '<animations/><parts>'))
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is not None and a.report()["status"] == "partial"
    assert any("animated" in reason for reason in a.unresolved)


def test_pivot_refuses(tmp_path):
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    put("assets/hull.xml", path.read_text().replace('<part name="main"/>', '<part name="main"><pivot/></part>'))
    assert "pivot" in str(world.assemble("assets/ship_macro.xml").unresolved)


def test_repeated_mesh_instances_keep_transforms(tmp_path):
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    text = path.read_text()
    extra = '<connection name="second"><offset><position x="-10"/></offset><parts><part name="main"/></parts></connection>'
    put("assets/hull.xml", text.replace('</connections>', extra+'</connections>'))
    a = world.assemble("assets/ship_macro.xml")
    assert not a.unresolved, a.unresolved
    assert len(a.included) == 2 and len(a.mesh.faces) == 8
    np.testing.assert_allclose(a.mesh.vertices[4:], tetra().vertices+[-10, 0, 0], atol=1e-6)


def test_assembly_widens_local_indices(tmp_path):
    world, put = fixture(tmp_path)
    m = tetra()
    m.vertices = np.tile(m.vertices, (16384, 1))
    put("assets/hull_data/main-lod0.xmf", xmf(m))
    path = tmp_path/"assets/hull.xml"
    extra = '<connection name="second"><parts><part name="main"/></parts></connection>'
    put("assets/hull.xml", path.read_text().replace('</connections>', extra+'</connections>'))
    a = world.assemble("assets/ship_macro.xml")
    assert not a.unresolved, a.unresolved
    assert int(a.mesh.faces[4:].min()) == 65536


def test_input_drift_invalidates_mesh(tmp_path, monkeypatch):
    world, _ = fixture(tmp_path)
    original = world.binary
    def changed(value, record=True):
        raw = original(value, record)
        return raw if record else raw+b"drift"
    monkeypatch.setattr(world, "binary", changed)
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is None and "drift" in str(a.unresolved)


def test_packed_and_loose_precedence(tmp_path):
    raw = xmf()
    (tmp_path/"ext_01.cat").write_text(f"assets/part.xmf {len(raw)} 0 {hashlib.md5(raw).hexdigest()}\n")
    (tmp_path/"ext_01.dat").write_bytes(raw)
    world = World(_merge.Config(reference=tmp_path, include_packed_dlc=False))
    assert world.binary("assets/part.xmf") == raw
    (tmp_path/"assets").mkdir()
    (tmp_path/"assets/part.xmf").write_bytes(b"override")
    assert world.binary("assets/part.xmf", record=False) == b"override"


def pack(root, number, payload, vpath="assets/part.xmf"):
    root.mkdir(parents=True, exist_ok=True)
    (root/f"ext_{number:02}.cat").write_text(f"{vpath} {len(payload)} 0 {hashlib.md5(payload).hexdigest()}\n")
    (root/f"ext_{number:02}.dat").write_bytes(payload)


def test_later_catalog_and_catalog_drift(tmp_path):
    pack(tmp_path, 1, b"first")
    pack(tmp_path, 2, b"second")
    world = World(_merge.Config(reference=tmp_path, include_packed_dlc=False))
    assert world.binary("assets/part.xmf") == b"second"
    pack(tmp_path, 3, b"third")
    from x4validate._geometry_assets import Assembly
    a = Assembly("probe")
    world.verify(a)
    assert "catalog input drift" in str(a.unresolved)


def test_nested_binary_override_and_namespace_isolation(tmp_path):
    reference, owner, patch = [tmp_path/n for n in ("ref", "owner", "patch")]
    reference.mkdir()
    pack(owner, 1, b"owner")
    pack(patch, 1, b"nested", "extensions/owner/assets/part.xmf")
    world = World(_merge.Config(reference=reference, include_packed_dlc=False), [owner, patch])
    assert world.binary("extensions/owner/assets/part.xmf") == b"nested"
    with pytest.raises(GeometryError, match="namespace"):
        world.binary("extensions/notowner/assets/part.xmf")


def test_alias_is_explicit_and_case_insensitive(tmp_path):
    reference, candidate = tmp_path/"ref", tmp_path/"snapshot"
    reference.mkdir()
    pack(candidate, 1, b"candidate")
    world = World(_merge.Config(reference=reference, include_packed_dlc=False), [candidate], {"NamedMod": candidate})
    assert world.binary("extensions/NAMEDMOD/ASSETS/PART.XMF") == b"candidate"
    assert world.xml_namespace("extensions/NamedMod/assets/component.xml") == "extensions/snapshot/assets/component.xml"


def test_effective_geometry_source_patch(tmp_path):
    world, put = fixture(tmp_path)
    overlay = tmp_path/"patch"
    put("patch/assets/hull.xml", '<diff><replace sel="/components/component/source/@geometry">missing/data</replace></diff>')
    world.overlays = (overlay,)
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is None and "missing/data/main-lod0.xmf" in str(a.unresolved)


def test_a_lod_without_an_index_is_unresolved_not_a_traceback(tmp_path):
    """4.0.1 review: int(None) raised TypeError, which no except clause catches."""
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    put("assets/hull.xml", path.read_text().replace('<part name="main"/>', '<part name="main"><lods><lod/></lods></part>'))
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is None and a.unresolved, a.unresolved


def test_a_released_mesh_keeps_its_report_until_drift(tmp_path, monkeypatch):
    """4.0.1 review: a scored candidate's mesh is freed (memory grew with --geometry-limit);
    its report keeps the counts and status, and later drift still nulls it."""
    world, _ = fixture(tmp_path)
    a = world.assemble("assets/ship_macro.xml")
    before = a.report()
    a.release()
    assert a.mesh is None and a.report() == before and before["status"] == "complete"
    original = world.binary
    monkeypatch.setattr(world, "binary", lambda value, record=True: original(value, record) + (b"" if record else b"drift"))
    world.verify(a)
    assert a.released is None and a.report()["status"] == "unresolved" and a.report()["vertices"] == 0


def test_cli_text_names_what_it_did_not_compare(tmp_path, monkeypatch, capsys):
    """4.0.1 review: the inventory's left-out mods and unparseable macros reached JSON only."""
    fixture(tmp_path)
    from x4validate import _effective
    monkeypatch.setattr(_effective, "active_mods", lambda scan: [])
    def collect(ref, ext, unreadable, notes=None):
        unreadable.append("broken/ship_macro.xml")
        notes.append("NOT layered onto any ship (the engine does not load it): somemod")
        return []
    monkeypatch.setattr(_similarity, "_collect_all", collect)
    rc = _similarity.main(["--reference", str(tmp_path), "--ext-dir", str(tmp_path), "--geometry", "assets/ship_macro.xml"])
    err = capsys.readouterr().err
    assert rc == 3                                # zero candidates scored
    assert "NOT COMPARED" in err and "broken/ship_macro.xml" in err and "somemod" in err, err


def test_missing_lod_labels_fallback(tmp_path):
    world, put = fixture(tmp_path)
    path = tmp_path/"assets/hull.xml"
    put("assets/hull.xml", path.read_text().replace('<part name="main"/>', '<part name="main"><lods><lod index="2"/></lods></part>'))
    put("assets/hull_data/main-lod2.xmf", xmf())
    a = world.assemble("assets/ship_macro.xml")
    assert a.mesh is not None and a.included[0]["lod"] == 2
    assert a.report()["status"] == "partial" and "LOD0 absent" in str(a.unresolved)


def test_compression_bomb_refuses():
    raw = bytearray(xmf(compressed=True))
    # Declare only one position while retaining the larger compressed stream.
    struct.pack_into("<I", raw, 64+28, 1)
    with pytest.raises(GeometryError, match="oversized|byte count"):
        decode(bytes(raw))


@pytest.mark.parametrize("version,known,refuses", [(800, 900, False), (900, 900, True), (800, None, True)])
def test_version_catalog_is_not_a_silent_gap(tmp_path, version, known, refuses):
    (tmp_path/f"ext_v{version}.cat").write_text("")
    world = World(_merge.Config(reference=tmp_path, include_packed_dlc=False))
    world.game_version = known
    if refuses:
        with pytest.raises(GeometryError, match="version"):
            world.binary("absent.xmf")
    else:
        with pytest.raises(GeometryError, match="missing asset"):
            world.binary("absent.xmf")
        assert len(world.scope["ignored_version_catalogs"]) == 1


def test_malformed_catalog_refuses(tmp_path):
    (tmp_path/"ext_01.cat").write_text("broken line\n")
    world = World(_merge.Config(reference=tmp_path, include_packed_dlc=False))
    with pytest.raises(GeometryError, match="malformed catalog"):
        world.binary("absent.xmf")


def test_nested_parent_quaternion_transform():
    from lxml import etree
    q = np.sqrt(.5)
    matrix = transform(etree.fromstring(f'<offset><position x="2"/><quaternion qz="{q}" qw="{q}"/></offset>'))
    np.testing.assert_allclose((matrix @ [1, 0, 0, 1])[:3], [2, 1, 0], atol=1e-10)


@pytest.mark.parametrize("path", ["../secret", "/assets/x", "C:/assets/x", "a/../../b"])
def test_paths_refuse_traversal(path):
    with pytest.raises(GeometryError):
        safe_path(path)


def test_surface_sampling_area_not_vertices():
    m = tetra()
    points = sample(m, 1024)
    assert points.shape == (1024, 3)
    np.testing.assert_array_equal(points, sample(m, 1024))


@pytest.mark.parametrize("mode", ["identity", "rigid_scale", "reorder", "subdivide"])
def test_positive_controls(mode):
    from scipy.spatial.transform import Rotation
    a, b = tetra(), tetra()
    if mode == "rigid_scale":
        b.vertices = b.vertices @ Rotation.from_euler("xyz", [37, 81, 19], degrees=True).as_matrix()*7+[50, 20, -10]
    elif mode == "reorder":
        b.faces = b.faces[::-1, ::-1]
    elif mode == "subdivide":
        b = Mesh(*trimesh.remesh.subdivide(b.vertices, b.faces))
    result = compare(a, b, 512)
    assert result["geometry_score"] > 99
    assert result["transform"]["determinant"] == pytest.approx(1)
    assert result["samples_a"] == 512
    if mode == "rigid_scale":
        assert result["uniform_scale_a_to_b"] == pytest.approx(7)


def test_mild_edit_ranks_above_contrasts():
    a, mild, stretch = tetra(), tetra(), tetra()
    mild.vertices[3] += [.02, -.01, .02]
    stretch.vertices *= [1, 2.5, .5]
    other = trimesh.creation.icosphere(subdivisions=1)
    scores = [compare(a, b, 512)["geometry_score"] for b in
              (mild, stretch, Mesh(other.vertices, other.faces))]
    assert scores[0] > 90 and scores[0] > max(scores[1:])+20


def test_contained_partial_not_identity():
    a = tetra()
    # Same original plus a large remote structure: directed overlap must expose it.
    b = Mesh(np.concatenate([a.vertices, a.vertices+[8, 0, 0]]),
             np.concatenate([a.faces, a.faces+4]))
    result = compare(a, b, 512)
    assert result["geometry_score"] < 80
    assert result["verdict"] == "unresolved"


@pytest.mark.parametrize("batch", [3, 2048])
def test_bounded_distance_matches_independent_bruteforce(monkeypatch, batch):
    from x4validate import _geometry_score
    monkeypatch.setattr(_geometry_score, "DISTANCE_BATCH", batch)
    m = tetra()
    mesh = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=False)
    points = np.random.default_rng(4).normal(size=(32, 3))*4
    expected = trimesh.proximity.closest_point_naive(mesh, points)[1]
    np.testing.assert_allclose(_geometry_score.surface_distances(mesh, points), expected, atol=1e-12)


def test_distance_budget_refuses_instead_of_truncating(monkeypatch):
    from x4validate import _geometry_score
    monkeypatch.setattr(_geometry_score, "MAX_TRIANGLE_TESTS", 0)
    with pytest.raises(GeometryError, match="work budget"):
        compare(tetra(), tetra(), 128)


def test_box_budget_refuses_independently(monkeypatch):
    from x4validate import _geometry_score
    monkeypatch.setattr(_geometry_score, "MAX_BOX_TESTS", 0)
    with pytest.raises(GeometryError, match="bounding-box work budget"):
        compare(tetra(), tetra(), 128)


def test_box_pruning_keeps_exact_distances_on_curved_mesh():
    from x4validate._geometry_score import surface_distances
    mesh = trimesh.creation.icosphere(subdivisions=2)
    points = np.random.default_rng(11).normal(size=(40, 3))*3
    expected = trimesh.proximity.closest_point_naive(mesh, points)[1]
    work = [0, 0]
    np.testing.assert_allclose(surface_distances(mesh, points, work), expected, atol=1e-12)
    assert work[0] < work[1]


def test_unreferenced_vertex_is_not_a_surface_bound():
    from x4validate._geometry_score import surface_distances
    m = tetra()
    points = np.array([[9., 9., 9.], [-4, -3, -2]])
    mesh = trimesh.Trimesh(vertices=np.concatenate([m.vertices, points]), faces=m.faces, process=False)
    expected = trimesh.proximity.closest_point_naive(mesh, points)[1]
    np.testing.assert_allclose(surface_distances(mesh, points), expected, atol=1e-12)


def test_zero_area_faces_are_accounted():
    a = tetra()
    b = Mesh(a.vertices, np.concatenate([a.faces, [[0, 0, 0]]]))
    result = compare(a, b, 128)
    assert result["geometry_score"] == 100
    assert result["discarded_zero_area_faces_b"] == 1


def test_cli_geometry_is_opt_in(capsys):
    with pytest.raises(SystemExit) as exc:
        _similarity.main(["--geometry-json"])
    assert exc.value.code == 2


def test_cli_null_extraction_report(tmp_path, monkeypatch, capsys):
    world, _ = fixture(tmp_path)
    from x4validate import _effective
    monkeypatch.setattr(_effective, "active_mods", lambda scan: [])
    args = ["--reference", str(tmp_path), "--ext-dir", str(tmp_path),
            "--geometry", "missing.xml", "--geometry-against", "assets/ship_macro.xml", "--geometry-json"]
    assert _similarity.main(args) == 3          # nothing scored is a non-answer (4.0.1 review)
    report = json.loads(capsys.readouterr().out)
    assert report["pairs"][0]["geometry_score"] is None
    assert report["coverage"] == dict(total=1, scored=0, partial=0, unresolved=1, shortlisted=1, excluded=0)


@pytest.mark.parametrize("partial", [False, True])
def test_cli_supported_and_partial_geometry_contract(tmp_path, monkeypatch, capsys, partial):
    world, put = fixture(tmp_path)
    from x4validate import _effective
    monkeypatch.setattr(_effective, "active_mods", lambda scan: [])
    if partial:
        p = tmp_path/"assets/hull.xml"
        put("assets/hull.xml", p.read_text().replace('<parts>', '<animations/><parts>'))
    args = ["--reference", str(tmp_path), "--ext-dir", str(tmp_path), "--geometry",
            "assets/ship_macro.xml", "--geometry-against", "assets/ship_macro.xml",
            "--geometry-samples", "128", "--geometry-json"]
    assert _similarity.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    row = report["pairs"][0]
    assert row["status"] == ("partial" if partial else "experimental")
    assert row["geometry_score"] == (None if partial else 100)
    if partial:
        assert row["partial_geometry_score"] == 100
    assert len(row["shared_exact_mesh_payloads"]) == 1
    assert row["verdict"] == "unresolved"
    assert set(report["dependencies"]) == {"numpy", "scipy", "trimesh", "rtree"}

"""Synthetic graph/audit and instanced-surface numerical/refusal controls."""
import json
import pytest
np = pytest.importorskip("numpy", reason="optional geometry extra")
pytest.importorskip("scipy", reason="optional geometry extra")
trimesh = pytest.importorskip("trimesh", reason="optional geometry extra")
pytest.importorskip("rtree", reason="optional geometry extra")
from lxml import etree
from test_geometry import fixture, tetra, xmf
from test_geometry_assembly import setup_child
from x4validate._geometry_assembly import Frames
from x4validate._geometry_mesh import GeometryError
from x4validate._geometry_score import compare_scenes, _prepare, surface_distances


def test_audit_is_independent_of_parent_placement(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('name="c"', 'name="c" parent="absent"'))
    a = world.assemble('assets/ship_macro.xml', audit=True)
    assert a.stages['inventory']['status'] == 'complete'
    assert a.stages['assets']['status'] == a.stages['audit'] == 'passed'
    assert a.stages['placement']['status'] == 'unresolved'
    assert a.scene is None
    assert a.assets[0]['status'] == 'decoded'


def test_absent_mesh_is_not_hidden_by_invalid_parent(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('name="c"', 'name="c" parent="absent"').replace('name="main"', 'name="missing"'))
    a = world.assemble('assets/ship_macro.xml', audit=True)
    assert a.stages['audit'] == 'failed'
    assert {f['code'] for f in a.findings} >= {'asset_absent', 'parent_unresolved'}
    assert a.assets[0]['resolution_attempts'][0]['status'] == 'absent'


def test_failed_child_does_not_stop_auditing_other_branches(tmp_path):
    world, put = setup_child(tmp_path)
    p = tmp_path/'assets/ship_macro.xml'
    put('assets/ship_macro.xml', p.read_text().replace('<connections>', '<connections><connection ref="socket"><macro ref="missing" connection="anchor"/></connection>'))
    a = world.assemble('assets/ship_macro.xml', audit=True)
    assert a.stages['inventory']['status'] == 'incomplete'
    assert len(a.assets) == 2 and all(v['status'] == 'decoded' for v in a.assets)


def test_repeated_group_names_refuse_with_candidate_locations():
    c = etree.fromstring('<component><connections><connection name="a" group=" pilot "><offset><position x="11"/></offset><parts><part name="seat"/></parts></connection><connection name="b" group="gunner"><offset><position x="-29"/></offset><parts><part name="seat"/></parts></connection><connection name="child" group="pilot" parent="seat"/></connections></component>')
    frames = Frames(c)
    with pytest.raises(GeometryError, match="group-relative lookup unqualified") as exc:
        frames.port('child')
    assert " pilot " in str(exc.value) and 'gunner' in str(exc.value) and '/part' in str(exc.value)


def test_unknown_inventory_denominator_on_budget(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('</connections>', '<connection name="second"><parts><part name="main"/></parts></connection></connections>'))
    a = world.assemble('assets/ship_macro.xml', instanced=True, max_instances=1)
    inventory = a.stages['inventory']
    assert inventory['status'] == 'incomplete' and inventory['total_parts'] is None
    assert inventory['frontier'] and a.scene is not None
    assert any(f['code'] == 'budget_reached' for f in a.findings)


def test_scene_moments_bounds_and_distances_match_concatenation(tmp_path):
    world, _ = setup_child(tmp_path)
    legacy = world.assemble('assets/ship_macro.xml')
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    assert not a.unresolved, a.unresolved
    assert len(a.included) == 2
    assert a.scene.counts() == (8, 8)
    p, center, radius, axes, size, discarded = a.scene.prepare(128, 7)
    _, lc, lr, _ = _prepare(legacy.mesh, 128, 7)
    np.testing.assert_allclose(center, lc, atol=1e-12)
    np.testing.assert_allclose(radius, lr, atol=1e-12)
    np.testing.assert_allclose(size, np.ptp(legacy.mesh.vertices, axis=0))
    surface = trimesh.Trimesh(vertices=(legacy.mesh.vertices-center)/radius, faces=legacy.mesh.faces, process=False)
    queries = np.random.default_rng(4).normal(size=(40, 3))
    expected = surface_distances(surface, queries)
    np.testing.assert_allclose(a.scene.distances(queries, center, radius, [0, 0]), expected, atol=1e-12)
    a.release()
    assert a.report()['triangles'] == 8 and a.scene is None


def test_repeated_payload_instances_contribute_separately(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('</connections>', '<connection name="second"><offset><position x="-10"/></offset><parts><part name="main"/></parts></connection></connections>'))
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    assert a.stages['assets']['decoded_unique_payloads'] == 1
    assert len(a.included) == 2 and a.scene.counts() == (8, 8)
    result = compare_scenes(a.scene, a.scene, 128)
    assert result['geometry_score'] == 100 and result['scorer'] == 'surface-overlap-instanced-2'


def test_scene_refuses_work_and_index_budgets(tmp_path, monkeypatch):
    from x4validate import _geometry_score
    world, _ = fixture(tmp_path)
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    monkeypatch.setattr(_geometry_score, 'MAX_TRIANGLE_TESTS', 0)
    with pytest.raises(GeometryError, match='work budget'):
        compare_scenes(a.scene, a.scene, 128)
    monkeypatch.setattr(a.scene, 'INDEX_BYTES', 1)
    with pytest.raises(GeometryError, match='index memory budget'):
        a.scene.distances(np.zeros((1, 3)), np.zeros(3), 1, [0, 0])


def test_drift_invalidates_released_scene(tmp_path):
    world, put = fixture(tmp_path)
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    a.release()
    put('assets/hull_data/main-lod0.xmf', xmf(tetra())+b'drift')
    world.verify(a)
    assert a.released is None and a.scene is None
    assert a.stages['verification'] == 'failed'


def test_catalog_recheck_error_is_a_structured_refusal(tmp_path, monkeypatch):
    world, _ = fixture(tmp_path)
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    world.catalog_stamps[tmp_path] = {}
    def broken(root):
        raise GeometryError('malformed catalog after assembly')
    monkeypatch.setattr(world, 'catalog_stamp', broken)
    world.verify(a)
    assert a.scene is None and a.stages['verification'] == 'failed'
    assert any(f['code'] == 'input_drift' for f in a.findings)


def test_partless_animated_ancestor_stays_unqualified(tmp_path):
    world, put = setup_child(tmp_path)
    p = tmp_path/'assets/hull.xml'
    text = p.read_text().replace('<connection name="socket">', '<connection name="socket"><animations/>')
    put('assets/hull.xml', text)
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    assert a.scene is not None and a.stages['placement']['status'] == 'unresolved'
    assert any(f['code'] == 'unqualified_pose' for f in a.findings)


def test_mesh_adapter_and_eviction(tmp_path, monkeypatch):
    from x4validate._geometry_scene import Scene
    s = Scene.from_mesh(tetra())
    assert compare_scenes(s, s, 128)['geometry_score'] == 100
    s.release()
    with pytest.raises(GeometryError, match='released'):
        s.prepare(128, 0)
    world, _ = fixture(tmp_path)
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    monkeypatch.setattr(a.scene, 'CACHE_BYTES', 1)
    first = a.scene.prepare(128, 0)
    second = a.scene.prepare(128, 0)
    np.testing.assert_array_equal(first[0], second[0])
    assert a.scene.cache_bytes == 0 and a.scene.reads > 1


@pytest.mark.parametrize('count', [0, 2049])
def test_graph_rejects_unvalidated_instance_limits(tmp_path, count):
    world, _ = fixture(tmp_path)
    with pytest.raises(GeometryError, match='1..2048'):
        world.assemble('assets/ship_macro.xml', audit=True, max_instances=count)


@pytest.mark.parametrize('count', [600, 2048])
def test_many_instances_reuse_payload_without_concatenation(tmp_path, count):
    world, put = fixture(tmp_path)
    connections = ''.join(f'<connection name="c{i}"><offset><position x="{i}"/></offset><parts><part name="main"/></parts></connection>' for i in range(count))
    put('assets/hull.xml', '<components><component name="hull"><source geometry="assets/hull_data"/><connections>'+connections+'</connections></component></components>')
    a = world.assemble('assets/ship_macro.xml', instanced=True, max_instances=2048)
    assert not a.unresolved and a.mesh is None and len(a.included) == count
    assert a.stages['inventory']['total_parts'] == count
    assert a.stages['assets']['decoded_unique_payloads'] == 1
    _, center, _, _, size, _ = a.scene.prepare(128, 0)
    assert count/2-1 < center[0] < count/2+1 and size[0] == count+2


def test_legacy_and_graph_assemblies_keep_distinct_version_provenance(tmp_path):
    world, _ = fixture(tmp_path)
    assert world.assemble('assets/ship_macro.xml').report()['assembly'] == 'recursive-bind-2'
    assert world.assemble('assets/ship_macro.xml', instanced=True).report()['assembly'] == 'structural-graph-3'


def test_excluded_material_does_not_hide_a_missing_payload(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('<part name="main"/>', '<part name="missing"><lods><lod index="0"><materials><material ref="effects.glow"/></materials></lod></lods></part>'))
    a = world.assemble('assets/ship_macro.xml', audit=True)
    assert a.excluded and a.stages['audit'] == 'failed'
    assert any(f['code'] == 'asset_absent' for f in a.findings)


def test_all_declared_lods_are_audited_even_when_lod0_exists(tmp_path):
    world, put = fixture(tmp_path)
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('<part name="main"/>', '<part name="main"><lods><lod index="0"/><lod index="2"/></lods></part>'))
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    assert a.scene is not None and a.stages['assets']['status'] == 'failed'
    assert a.stages['assets']['declared_lod_references'] == 2
    assert a.stages['assets']['decoded_lod_references'] == 1
    assert any('lod2.xmf' in f['message'] for f in a.findings)
    put('assets/hull_data/main-lod2.xmf', xmf())
    b = world.assemble('assets/ship_macro.xml', audit=True)
    assert b.stages['audit'] == 'passed' and b.stages['assets']['decoded_lod_references'] == 2


@pytest.mark.parametrize('failure', ['cycle', 'depth', 'macro_count', 'bad_port'])
def test_graph_structural_refusals_keep_assets_honest(tmp_path, monkeypatch, failure):
    from x4validate import _geometry_graph
    world, put = setup_child(tmp_path)
    if failure == 'depth':
        monkeypatch.setattr(_geometry_graph, 'MAX_DEPTH', 1)
    elif failure == 'macro_count':
        monkeypatch.setattr(_geometry_graph, 'MAX_MACROS', 1)
    elif failure == 'bad_port':
        p = tmp_path/'assets/child.xml'
        put('assets/child.xml', p.read_text().replace('name="anchor"', 'name="wrong"'))
    else:
        p = tmp_path/'assets/child_macro.xml'
        put('assets/child_macro.xml', p.read_text().replace('</macro>', '<connections><connection ref="anchor"><macro ref="child" connection="anchor"/></connection></connections></macro>'))
    a = world.assemble('assets/ship_macro.xml', instanced=True)
    assert a.unresolved
    assert a.stages['inventory']['status'] == ('complete' if failure == 'bad_port' else 'incomplete')
    assert a.stages['assets']['decoded_instances'] >= 1
    if failure == 'bad_port':
        assert len(a.assets) == 2 and a.stages['assets']['status'] == 'passed'


def test_rotated_scaled_instance_distances_are_in_world_units(tmp_path):
    from x4validate._geometry_scene import Scene
    from scipy.spatial.transform import Rotation
    mesh = tetra()
    s = Scene.from_mesh(mesh)
    linear = Rotation.from_euler('xyz', [13, 71, 29], degrees=True).as_matrix()*3.5
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = linear, [40, -31, 19]
    s.instances[0]['transform'] = matrix.tolist()
    vertices = mesh.vertices @ linear.T+matrix[:3, 3]
    surface = trimesh.Trimesh(vertices=vertices, faces=mesh.faces, process=False)
    points = np.random.default_rng(9).normal(size=(40, 3))*4+[40, -31, 19]
    expected = surface_distances(surface, points)
    np.testing.assert_allclose(s.distances(points, np.zeros(3), 1, [0, 0]), expected, atol=1e-12)
    s.instances[0]['transform'][0][0] *= 2
    with pytest.raises(GeometryError, match='nonrigid'):
        s.distances(points, np.zeros(3), 1, [0, 0])


@pytest.mark.parametrize('missing,code', [(False, 0), (True, 3)])
def test_cli_audit_has_no_candidate_inventory(tmp_path, monkeypatch, capsys, missing, code):
    from x4validate import _similarity, _effective
    fixture(tmp_path)
    monkeypatch.setattr(_effective, 'active_mods', lambda scan: [])
    monkeypatch.setattr(_similarity, '_collect_all', lambda *a, **k: pytest.fail('audit enumerated candidates'))
    args = ['--reference', str(tmp_path), '--ext-dir', str(tmp_path), '--geometry',
            'missing.xml' if missing else 'assets/ship_macro.xml', '--geometry-audit', '--geometry-json']
    assert _similarity.main(args) == code
    r = json.loads(capsys.readouterr().out)
    assert r['schema'] == 'x4similar.geometry/2' and r['mode'] == 'audit'
    assert r['pairs'] == []


@pytest.mark.parametrize('option', [['--geometry-samples', '4096'], ['--geometry-limit=32'], ['--geometry-against', 'other.xml']])
def test_audit_rejects_explicit_comparison_options_even_defaults(option):
    from x4validate import _similarity
    with pytest.raises(SystemExit) as exc:
        _similarity.main(['--geometry', 'ship.xml', '--geometry-audit', *option])
    assert exc.value.code == 2

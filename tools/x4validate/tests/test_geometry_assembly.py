"""Authored connector assemblies and independently calculable transform controls."""
import pytest
np = pytest.importorskip("numpy", reason="optional geometry extra")
pytest.importorskip("scipy", reason="optional geometry extra")
pytest.importorskip("trimesh", reason="optional geometry extra")
pytest.importorskip("rtree", reason="optional geometry extra")
from lxml import etree

from x4validate._geometry_assets import transform
from x4validate._geometry_mesh import GeometryError
from x4validate._geometry_assembly import Frames
from test_geometry import fixture, tetra, xmf


@pytest.mark.parametrize("roll", [0, 180, -180, 360, 540])
def test_half_turn_roll_exact(roll):
    mat = transform(etree.fromstring(f'<offset><rotation roll="{roll}"/></offset>'))
    expected = [(-1 if int(roll/180)%2 else 1)*2, (-1 if int(roll/180)%2 else 1)*3, 5]
    np.testing.assert_allclose((mat @ [2, 3, 5, 1])[:3], expected)


@pytest.mark.parametrize("xml", ['<rotation yaw="180"/>', '<rotation pitch="2"/>',
                                 '<rotation roll="nan"/>', '<rotation roll="0"/><quaternion/>'])
def test_unqualified_euler_is_refused(xml):
    with pytest.raises(GeometryError):
        transform(etree.fromstring('<offset>'+xml+'</offset>'))


def test_roll_only_maps_forward_axis_without_choosing_mixed_euler_order():
    matrix = transform(etree.fromstring('<offset><rotation roll="90"/></offset>'))
    np.testing.assert_allclose((matrix @ [1, 0, 0, 1])[:3], [0, 1, 0], atol=1e-12)


def setup_child(tmp_path, **kwargs):
    world, put = fixture(tmp_path)
    for k, v in kwargs.items():
        setattr(world, k, v)
    put('index/macros.xml', '<index><entry name="child" value="assets/child_macro"/></index>')
    put('index/components.xml', '<index><entry name="hull" value="assets/hull"/><entry name="child" value="assets/child"/></index>')
    put('assets/ship_macro.xml', '<macros><macro name="ship" class="ship_s"><component ref="hull"/><connections><connection ref="socket"><macro ref="child" connection="anchor"/></connection></connections></macro></macros>')
    parent = (tmp_path/'assets/hull.xml').read_text()
    put('assets/hull.xml', parent.replace('</connections>', '<connection name="socket"><offset><position x="30"/></offset></connection></connections>'))
    put('assets/child_macro.xml', '<macros><macro name="child" class="dockarea"><component ref="child"/></macro></macros>')
    put('assets/child.xml', '<components><component name="child" class="dockarea"><source geometry="assets/child_data"/><connections><connection name="anchor"><offset><position x="5"/></offset></connection><connection name="body"><offset><position x="2"/></offset><parts><part name="body"/></parts></connection></connections></component></components>')
    put('assets/child_data/body-lod0.xmf', xmf())
    return world, put


def test_recursive_nonidentity_child_anchor(tmp_path):
    world, _ = setup_child(tmp_path)
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved, a.unresolved
    assert len(a.attachments) == 2 and len(a.included) == 2
    # parent port30 - child port5 + render offset2 = world27
    np.testing.assert_allclose(a.mesh.vertices[4:], tetra().vertices+[27, 0, 0], atol=1e-6)
    assert a.seconds > 0


def test_virtual_component_is_accounted_from_definition(tmp_path):
    world, put = setup_child(tmp_path)
    put('assets/child.xml', '<components><component name="child" class="storage"><connections><connection name="anchor"/></connections></component></components>')
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved and len(a.attachments) == 2
    assert len(a.included) == 1
    assert any('no render source or parts' in p['reason'] for p in a.excluded)


def test_virtual_leaf_does_not_need_an_invented_placement(tmp_path):
    world, put = setup_child(tmp_path)
    put('assets/child.xml', '<components><component name="child" class="storage"/></components>')
    p = tmp_path/'assets/hull.xml'
    put('assets/hull.xml', p.read_text().replace('x="30"', 'x="349,9"'))
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved and len(a.included) == 1
    assert a.attachments[-1]['transform'] is None
    assert a.attachments[-1]['placement_status'].startswith('not evaluated')


def test_storage_with_geometry_is_not_assumed_virtual(tmp_path):
    world, put = setup_child(tmp_path)
    p = tmp_path/'assets/child_macro.xml'
    put('assets/child_macro.xml', p.read_text().replace('class="dockarea"', 'class="storage"'))
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved and len(a.included) == 2


def test_equipment_is_excluded_separately(tmp_path):
    world, put = setup_child(tmp_path)
    p = tmp_path/'assets/child_macro.xml'
    put('assets/child_macro.xml', p.read_text().replace('class="dockarea"', 'class="engine"'))
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved and len(a.included) == 1
    assert a.excluded[0]['reason'] == 'fitted equipment class engine'


@pytest.mark.parametrize("reason,edit", [('attachment port', lambda x: x.replace('name="anchor"', 'name="wrong"')),
                                       ('cyclic macro', lambda x: x)])
def test_structural_failures_are_not_clean_negatives(tmp_path, reason, edit):
    world, put = setup_child(tmp_path)
    if reason == 'attachment port':
        put('assets/child.xml', edit((tmp_path/'assets/child.xml').read_text()))
    else:
        p = tmp_path/'assets/child_macro.xml'
        put('assets/child_macro.xml', p.read_text().replace('</macro>', '<connections><connection ref="anchor"><macro ref="child" connection="anchor"/></connection></connections></macro>'))
    a = world.assemble('assets/ship_macro.xml')
    assert a.mesh is not None and a.unresolved
    assert any(reason in s for s in a.unresolved)


def test_parent_part_offset_contributes_to_child_frame():
    root = etree.fromstring('<component><connections><connection name="parent"><offset><position x="10"/></offset><parts><part name="p"><offset><position y="2"/></offset></part></parts></connection><connection name="child" parent="p"><offset><position z="3"/></offset></connection></connections></component>')
    np.testing.assert_allclose(Frames(root).port('child')[:3, 3], [10, 2, 3])


@pytest.mark.parametrize("pose,expected", [('strict', 'partial'), ('bind', 'complete')])
def test_bind_pose_is_explicit_and_animation_stays_visible(tmp_path, pose, expected):
    world, put = setup_child(tmp_path, pose=pose)
    world.scope['pose'] = pose
    p = tmp_path/'assets/child.xml'
    put('assets/child.xml', p.read_text().replace('<parts>', '<animations><animation name="opening" start="0" end="100"/></animations><parts>'))
    a = world.assemble('assets/ship_macro.xml')
    assert a.report()['status'] == expected
    assert bool(a.pose_notes) == (pose == 'bind')


def test_bind_pivot_is_validated_and_reported(tmp_path):
    world, put = setup_child(tmp_path, pose='bind')
    p = tmp_path/'assets/child.xml'
    put('assets/child.xml', p.read_text().replace('<part name="body"/>', '<part name="body"><pivot><offset><position x="3"/><quaternion qz="0.3" qw="0.9539392"/></offset></pivot></part>'))
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved and a.pose_notes
    np.testing.assert_allclose(a.mesh.vertices[4:], tetra().vertices+[27, 0, 0], atol=1e-6)


def test_referenced_mesh_uses_consumer_frame_not_provider_placement(tmp_path):
    world, put = setup_child(tmp_path, pose='bind')
    put('index/components.xml', '<index><entry name="hull" value="assets/hull"/><entry name="child" value="assets/child"/><entry name="provider" value="assets/provider"/></index>')
    put('assets/provider.xml', '<components><component name="provider"><source geometry="assets/provider_data"/><connections><connection name="old"><offset><position x="1000"/></offset><parts><part name="shared"/></parts></connection></connections></component></components>')
    put('assets/provider_data/shared-lod0.xmf', xmf())
    p = tmp_path/'assets/child.xml'
    put('assets/child.xml', p.read_text().replace('<part name="body"/>', '<part name="body" ref="provider.shared"/>'))
    a = world.assemble('assets/ship_macro.xml')
    assert not a.unresolved, a.unresolved
    np.testing.assert_allclose(a.mesh.vertices[4:], tetra().vertices+[27, 0, 0], atol=1e-6)
    assert a.included[-1]['reference_chain'] == ['provider.shared']
    assert a.included[-1]['mesh'] == 'assets/provider_data/shared-lod0.xmf'


def test_broken_render_reference_remains_unresolved(tmp_path):
    world, put = setup_child(tmp_path, pose='bind')
    p = tmp_path/'assets/child.xml'
    put('assets/child.xml', p.read_text().replace('<part name="body"/>', '<part name="body" ref="missing.shared"/>'))
    a = world.assemble('assets/ship_macro.xml')
    assert a.report()['status'] == 'partial'
    assert any('missing' in s for s in a.unresolved)


def test_cached_xml_cannot_be_poisoned_by_a_caller(tmp_path):
    world, _ = fixture(tmp_path)
    root = world.xml('assets/ship_macro.xml')
    root.find('macro').set('name', 'poisoned')
    assert world.xml('assets/ship_macro.xml').find('macro').get('name') == 'ship'


def test_cached_xml_still_refuses_source_drift(tmp_path, monkeypatch):
    world, put = fixture(tmp_path)
    original = world.verify
    def drift(assembly):
        put('assets/hull.xml', '<components/>')
        original(assembly)
    monkeypatch.setattr(world, 'verify', drift)
    a = world.assemble('assets/ship_macro.xml')
    assert a.mesh is None and 'drift' in str(a.unresolved)


@pytest.mark.parametrize('pose,expected', [('strict', 'partial'), ('bind', 'complete')])
def test_part_euler_pose_is_not_silently_strict(tmp_path, pose, expected):
    world, put = setup_child(tmp_path, pose=pose)
    p = tmp_path/'assets/child.xml'
    put('assets/child.xml', p.read_text().replace('<part name="body"/>', '<part name="body"><offset><rotation roll="90"/></offset></part>'))
    a = world.assemble('assets/ship_macro.xml')
    assert a.report()['status'] == expected
    assert 'runtime Euler parser' in str(a.unresolved if pose == 'strict' else a.pose_notes)


@pytest.mark.parametrize('pose,expected', [('strict', 'partial'), ('bind', 'complete')])
def test_animation_on_partless_attachment_port_is_visible(tmp_path, pose, expected):
    world, put = setup_child(tmp_path, pose=pose)
    p = tmp_path/'assets/hull.xml'
    text = p.read_text().replace('<connection name="socket">', '<connection name="socket"><animations><animation name="move"/></animations>')
    assert text != p.read_text()
    put('assets/hull.xml', text)
    a = world.assemble('assets/ship_macro.xml')
    assert a.report()['status'] == expected
    assert 'declares animation' in str(a.unresolved if pose == 'strict' else a.pose_notes)

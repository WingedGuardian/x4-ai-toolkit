"""Actual pure composition; no candidate files applied or deployed."""
import importlib.util
from pathlib import Path
from lxml import etree as E
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("helper_composition",ROOT/"scripts/prepare_live_helper.py")
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)


def inputs():
    mod=ROOT/"mods/x4_toolkit_helper"
    return (mod.joinpath("content.xml").read_bytes(),mod.joinpath("ui.xml").read_bytes(),
            {key:(ROOT/path).read_bytes() for key,path in P.BUNDLE.SOURCES.items()})


def test_exact_source_bound_bundle_and_md_are_composed_without_input_changes():
    a,b,sources=inputs();before=dict(sources)
    files,receipt=P.build(a,b,sources)
    assert sources==before
    assert set(files)=={"content.xml","ui.xml","ui/live_automation_bundle.lua","md/X4ToolkitSession.xml"}
    assert P.BUNDLE.verify(files["ui/live_automation_bundle.lua"])==receipt["bundle"]["build"]
    assert receipt["bundle"]["revision"]==receipt["session"]["revision"]
    assert files["md/X4ToolkitSession.xml"]==P.SESSION.prepare()[0]["md/X4ToolkitSession.xml"]
    assert receipt["runtime_verified"] is False
    assert all(P.sha(files[path])==digest for path,digest in receipt["outputs"].items())


def test_manifest_preserves_all_author_fields_except_disclosure():
    a,b,sources=inputs();files,_=P.build(a,b,sources)
    original=E.fromstring(a);candidate=E.fromstring(files["content.xml"])
    assert candidate.get("description")==P.DESCRIPTION
    candidate.set("description",original.get("description"))
    assert E.tostring(candidate)==E.tostring(original)


def test_loader_adds_bundle_immediately_before_query_preserving_other_nodes():
    a,b,sources=inputs();files,_=P.build(a,b,sources)
    root=E.fromstring(files["ui.xml"]);before=E.fromstring(b)
    names=[n.get("name") for n in root.findall("environment/file")]
    original=[n.get("name") for n in before.findall("environment/file")]
    assert names==["ui/live_automation_bundle.lua",*original]
    assert [E.tostring(n) for n in root.findall("environment/dependency")]==[E.tostring(n) for n in before.findall("environment/dependency")]
    assert [E.tostring(n) for n in root.findall("environment/savedvariable")]==[E.tostring(n) for n in before.findall("environment/savedvariable")]


@pytest.mark.parametrize("change",["wrong_manifest","wrong_addon","already_composed","missing_query","duplicate_query","other_environment"])
def test_wrong_or_ambiguous_input_refuses(change):
    a,b,sources=inputs()
    if change=="wrong_manifest":a=a.replace(b'id="x4_toolkit_helper"',b'id="other"')
    if change=="wrong_addon":b=b.replace(b'name="x4_toolkit_helper"',b'name="other"')
    if change=="already_composed":b=b.replace(b'<file name="ui/live_query.lua"/>',b'<file name="ui/live_automation_bundle.lua"/><file name="ui/live_query.lua"/>')
    if change=="missing_query":b=b.replace(b'<file name="ui/live_query.lua"/>',b'')
    if change=="duplicate_query":b=b.replace(b'<file name="ui/live_query.lua"/>',b'<file name="ui/live_query.lua"/><file name="ui/live_query.lua"/>')
    if change=="other_environment":b=b.replace(b'type="menus"',b'type="other"')
    with pytest.raises(ValueError):P.build(a,b,sources)


@pytest.mark.parametrize("extensions",[("persistence",),("conversation",),("conversation","persistence"),
    ("topology",),("topology","conversation","persistence"),("rooms",),
    ("rooms","topology","conversation","persistence"),("tradertext",),
    ("tradertext","rooms","topology","conversation","persistence"),("placement",),
    ("placement","tradertext","rooms","topology","conversation","persistence"),
    ("capture",),("orbit",),("roomentry",),tuple(P.EXTENSIONS)])
def test_fixed_extensions_load_before_query_with_exact_candidate_pins(extensions):
    a,b,sources=inputs();files,receipt=P.build(a,b,sources,extensions=extensions)
    names=[n.get("name") for n in E.fromstring(files["ui.xml"]).findall("environment/file")]
    loaders=[P.EXTENSIONS[name][1] for name in P.EXTENSIONS if name in extensions]
    original=[n.get("name") for n in E.fromstring(b).findall("environment/file")]
    assert names==["ui/live_automation_bundle.lua",*loaders,*original]
    assert set(receipt["extensions"])==set(extensions)
    expected={"content.xml","ui.xml","ui/live_automation_bundle.lua","md/X4ToolkitSession.xml"}
    for name in extensions:expected.update(P.EXTENSIONS[name][2])
    assert set(files)==expected
    assert receipt["outputs"]=={path:P.sha(data) for path,data in files.items()}
    assert receipt["runtime_verified"] is False
    assert "save access authorization" in receipt["excluded"]
    candidate=E.fromstring(files["content.xml"]);original=E.fromstring(a)
    assert "saves or loads the game" in candidate.get("description") and "any program running as this Windows user" in candidate.get("description") if "persistence" in extensions else True
    candidate.set("description",original.get("description"))
    assert E.tostring(candidate)==E.tostring(original)


def test_extension_order_is_canonical_and_does_not_contaminate_base_receipt():
    a,b,sources=inputs();before=P.build(a,b,sources)
    first=P.build(a,b,sources,extensions=("persistence","conversation"))
    second=P.build(a,b,sources,extensions=("conversation","persistence"))
    assert first==second
    assert P.build(a,b,sources)==before


def test_topology_three_extension_order_and_exact_source_pins():
    a,b,sources=inputs()
    forward=P.build(a,b,sources,extensions=('topology','conversation','persistence'))
    reverse=P.build(a,b,sources,extensions=('persistence','conversation','topology'))
    assert forward==reverse
    files,receipt=forward
    assert receipt['extensions']['topology']['runtime_verified'] is False
    assert receipt['extensions']['topology']['provider']=='x4-topology-observer-v1'
    assert b'topology extension observes bounded native' in files['content.xml']


def test_room_composition_preserves_exact_factory_outputs_and_order():
    a,b,sources=inputs()
    first=P.build(a,b,sources,extensions=('rooms','topology','conversation','persistence'))
    other=P.build(a,b,sources,extensions=('persistence','conversation','topology','rooms'))
    assert first==other
    files,receipt=first
    room=receipt['extensions']['rooms']
    assert room['runtime_verified'] is False and room['menu_opening_supported'] is False
    assert room['arrival_verified'] is False
    assert set(room['output_sha256'])=={'ui/live_rooms_extension.lua'}
    assert b'cannot open that menu or prove arrival' in files['content.xml']


def test_tradertext_five_extension_order_and_source_bound_outputs():
    a,b,sources=inputs()
    names=('tradertext','rooms','topology','conversation','persistence')
    first=P.build(a,b,sources,extensions=names)
    assert first==P.build(a,b,sources,extensions=tuple(reversed(names)))
    files,receipt=first
    observed=receipt['extensions']['tradertext']
    assert observed['provider']=='x4-trader-text-v1'
    assert not observed['runtime_verified'] and not observed['purchase_verified']
    assert observed['menu_opening_supported'] and not observed['rendered_pixels_verified']
    assert set(observed['output_sha256'])=={'ui/live_tradertext_extension.lua'}
    assert b'can purchase licences or blueprints' in files['content.xml']


def test_placement_six_extension_order_and_scope():
    a,b,sources=inputs()
    names=('placement','tradertext','rooms','topology','conversation','persistence')
    first=P.build(a,b,sources,extensions=names)
    assert first==P.build(a,b,sources,extensions=tuple(reversed(names)))
    files,receipt=first
    candidate=receipt['extensions']['placement']
    assert candidate['provider']=='x4-diagnostic-placement-v1'
    assert not candidate['runtime_verified']
    assert candidate['actions']==['diagnostic_place_player']
    assert set(candidate['output_sha256'])=={'md/X4ToolkitPlacement.xml','md/X4ToolkitShipPlacement.xml','ui/live_placement_extension.lua'}
    assert b'bypassing teleport research' in files['content.xml']


def test_all_nine_extensions_preserve_exact_outputs_and_canonical_order():
    a,b,sources=inputs();names=tuple(P.EXTENSIONS)
    assert len(names)==10
    first=P.build(a,b,sources,extensions=names)
    assert first==P.build(a,b,sources,extensions=tuple(reversed(names)))
    files,receipt=first
    capture=receipt['extensions']['capture'];orbit=receipt['extensions']['orbit']
    assert capture['provider']=='x4-screen-capture-v1' and not capture['runtime_verified']
    assert set(capture['output_sha256'])=={'md/X4ToolkitCapture.xml','ui/live_capture_extension.lua'}
    assert orbit['provider']=='x4-orbit-camera-v1' and not orbit['runtime_verified']
    assert not orbit['achieved_pose_verified'] and not orbit['target_distance_verified']
    assert set(orbit['output_sha256'])=={'md/X4ToolkitOrbit.xml','ui/live_orbit_extension.lua'}
    assert 'visual capture provider' not in receipt['excluded']
    assert b'Native capture writes screenshots' in files['content.xml']
    assert b'temporarily replace the player view' in files['content.xml']
    room=receipt['extensions']['roomentry']
    assert room['provider']=='x4-diagnostic-roomentry-v1' and not room['runtime_verified']
    assert set(room['output_sha256'])=={'md/X4ToolkitRoomEntry.xml','ui/live_roomentry_extension.lua'}
    assert len(files)==22 and b'including private rooms' in files['content.xml']


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_placement_trusted_profile_reaches_both_providers_and_exact_three_outputs(profile):
    files,receipt=P.build(*inputs(),extensions=('placement',),capacity_profile=profile)
    factory=P._load('live_placement_extension',{})
    direct,inventory=factory.prepare(capacity_profile=profile)
    assert all(files[path]==data for path,data in direct.items())
    candidate=receipt['extensions']['placement']
    assert candidate['capacity_profile']['name']==profile
    assert candidate['ship_md']['limits']['records']==(128 if profile=='interactive' else 512)
    assert candidate['providers']=={key:inventory['revision']for key in
        ('x4-diagnostic-placement-v1','x4-diagnostic-shipplacement-v1')}
    assert candidate['legacy_player_md_unchanged'] is True
    assert b'warp a helper-issued player-owned ship' in files['content.xml']
    assert b'no flight or collision-safety claim' in files['content.xml']


def test_trader_navigation_disclosure_keeps_trader_opener_and_observation_limits():
    files,unused=P.build(*inputs(),extensions=('tradertext',))
    description=E.fromstring(files['content.xml']).get('description')
    assert 'cannot open the trader menu or verify purchase completion' in description
    assert 'open and navigate native encyclopedia and owned-property views' in description
    assert 'do not verify rendered pixels or a complete owned-property census' in description


def test_new_ship_placement_dependency_drift_refuses(monkeypatch):
    original=P._load;selected=ROOT/'mods/x4_toolkit_helper/ui/live_shipplacement.lua'
    read=Path.read_bytes;changed=False
    def load(name,pins=None):
        module=original(name,pins)
        if name=='live_placement_extension':
            prepare=module.prepare
            def marked(**kwargs):
                nonlocal changed
                value=prepare(**kwargs);changed=True;return value
            module.prepare=marked
        return module
    def drifting(path):
        data=read(path)
        return data+b'\n' if path==selected and changed else data
    monkeypatch.setattr(P,'_load',load);monkeypatch.setattr(Path,'read_bytes',drifting)
    with pytest.raises(ValueError,match='extension source changed during composition'):
        P.build(*inputs(),extensions=('placement',))


@pytest.mark.parametrize("extensions",["persistence",["persistence"],("unknown",),("persistence","persistence"),(None,)])
def test_invalid_extension_requests_refuse(extensions):
    with pytest.raises(ValueError):P.build(*inputs(),extensions=extensions)


@pytest.mark.parametrize("name",list(P.EXTENSIONS))
def test_already_present_extension_loader_refuses_even_when_not_requested(name):
    a,b,sources=inputs();loader=P.EXTENSIONS[name][1]
    b=b.replace(b'<file name="ui/live_query.lua"/>',
                ('<file name="'+loader+'"/><file name="ui/live_query.lua"/>').encode())
    with pytest.raises(ValueError):P.build(a,b,sources)


def test_extension_factory_cannot_overwrite_base_files(monkeypatch):
    original=P._load
    def load(name,pins=None):
        module=original(name,pins)
        if name=="live_persistence_extension":
            module.build=lambda sources:({"content.xml":b"unexpected"},{})
        return module
    monkeypatch.setattr(P,"_load",load)
    with pytest.raises(ValueError,match="candidate set"):
        P.build(*inputs(),extensions=("persistence",))


@pytest.mark.parametrize("path",["scripts/prepare_live_helper.py",
    "mods/x4_toolkit_helper/ui/live_persistenceprovider.lua",
    "mods/x4_toolkit_helper/ui/live_topology.lua",
    "mods/x4_toolkit_helper/ui/live_rooms_provider.lua",
    "mods/x4_toolkit_helper/ui/live_tradertext_provider.lua",
    "mods/x4_toolkit_helper/ui/live_actorentry.lua",
    "mods/x4_toolkit_helper/ui/live_placement.lua",
    "mods/x4_toolkit_helper/ui/live_capture_adapter.lua",
    "mods/x4_toolkit_helper/ui/live_orbit.lua",
    "mods/x4_toolkit_helper/ui/live_roomentry.lua"])
def test_mid_composition_source_drift_refuses_without_mutating_files(monkeypatch,path):
    values=inputs();selected=ROOT/path;read=Path.read_bytes;seen=0
    def drifting(self):
        nonlocal seen
        data=read(self)
        if self==selected:
            seen+=1
            if seen>1:return data+b"\n"
        return data
    monkeypatch.setattr(Path,"read_bytes",drifting)
    with pytest.raises(ValueError,match="changed during composition"):
        P.build(*values,extensions=tuple(P.EXTENSIONS))


def test_omitted_module_and_changed_source_cannot_reuse_build_identity():
    a,b,sources=inputs();_,first=P.build(a,b,sources)
    key=next(iter(sources));sources[key]+=b"\n-- changed\n"
    _,second=P.build(a,b,sources)
    assert first["bundle"]["build"]!=second["bundle"]["build"]
    del sources[key]
    with pytest.raises(ValueError):P.build(a,b,sources)


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def test_surface_composition_uses_exact_factory_profile_and_read_only_disclosure(profile):
    a,b,sources=inputs()
    files,receipt=P.build(a,b,sources,extensions=('surface',),capacity_profile=profile)
    module=P._load('live_surface_extension',{})
    expected,source=module.prepare(profile)
    assert all(files[path]==raw for path,raw in expected.items())
    assert receipt['extensions']['surface']['revision']==source['revision']
    assert receipt['extensions']['surface']['capacity_profile']['name']==profile
    assert b'one bounded object-relative ray' in files['content.xml']
    assert b'neither inside-hull membership nor complete collision coverage' in files['content.xml']
    assert receipt['runtime_verified'] is False


def test_base_disclosure_names_attack_options_unauthenticated_pipe_and_session_binding():
    # Review r2-4, r2-5, r2-6: what the bundle can do that the manifest did not say.
    a,b,sources=inputs();files,_=P.build(a,b,sources)
    text=E.fromstring(files["content.xml"]).get("description")
    for phrase in ("attack order may target any object","change the game's settings",
                   "does not authenticate its client","bind to the first session"):
        assert phrase in text, phrase


def test_composition_script_is_pinned_at_import_and_refuses_later_drift(monkeypatch):
    # Review r3-6: build() hashed its own source at build time, so an edit between import
    # and build was pinned as if it were the running code.
    a,b,sources=inputs()
    files,receipt=P.build(a,b,sources)
    assert receipt["generators"]["prepare_live_helper.py"]==P.SELF_SHA256
    monkeypatch.setattr(P,"SELF_SHA256","0"*64)
    with pytest.raises(ValueError,match="changed since import"):
        P.build(a,b,sources)

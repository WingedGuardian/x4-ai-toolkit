"""Structural source controls only; no engine or schema qualification claim."""
from pathlib import Path
import importlib.util
import re
import pytest
from lxml import etree as E
HERE=Path(__file__).resolve().parents[3]/"scripts"
spec=importlib.util.spec_from_file_location("capture_proposal",HERE/"live_capture_md.py")
G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)

def tree():
    candidates,receipt=G.prepare()
    return E.fromstring(candidates["md/X4ToolkitCapture.xml"]),receipt

def test_fixed_single_capture_action_and_no_gameplay_writes():
    root,_=tree();calls=root.xpath("//capture_screen")
    assert len(calls)==1 and calls[0].get("directory")=="'x4_toolkit_capture'"
    assert not root.xpath("//warp|//destroy_object|//set_owner|//capture_movie_start|//capture_movie_stop|//save_game")
    assert "$R.$directory" not in E.tostring(root).decode()

def test_request_trigger_is_constant_first_action_before_any_reads():
    root,_=tree();actions=root.xpath("//cue[@name='Request']/actions")[0]
    assert actions[0].tag=="debug_text"
    assert actions[0].attrib=={"text":"'X4TKCAP TRIGGER'","chance":"100"}
    assert actions[1].get("name")=="$R" and actions[1].get("exact")=="event.param3"

def test_exact_canonical_and_queue_gates_enclose_action():
    root,_=tree();call=root.xpath("//capture_screen")[0]
    gates=[n.get("value","") for n in call.iterancestors() if n.tag=="do_if"]
    assert any("md.X4ToolkitSession.State.$Session" in v and "$Generation" in v for v in gates)
    assert any("datatype.list" in v for v in gates)
    assert any(".count lt 64" in v for v in gates)

def test_consumed_before_capture_and_no_automatic_replay():
    root,_=tree();call=root.xpath("//capture_screen")[0]
    execute=[p for p in call.iterancestors() if p.tag=="do_elseif" and "'execute'" in p.get("value","")][0]
    assert execute[0].get("name")=="$Record.$state" and execute[0].get("exact")=="'refused'"
    assert call.getprevious().tag=="debug_text"
    assert not root.xpath("//reset_cue|//signal_cue|//signal_cue_instantly")

def test_view_settle_is_bounded_not_world_age():
    root,_=tree();g=root.xpath("//capture_screen")[0].getparent().get("value")
    assert "prepared_age + 4s" in g and "prepared_age + 30s" in g
    assert "player.container ==" in g and "player.sector ==" in g
    assert "player.target ==" not in g

def test_capture_cannot_claim_file_or_visual_completion():
    root,_=tree();text=E.tostring(root).decode()
    assert "$file_confirmed=false" in text and "$visual_pass=false" in text
    assert "'completed'" not in text
    assert "$capture_age" in text and "CAPTURE_SCREEN_CALLED_NOT_FILE_CONFIRMED" in text

def test_records_capacity_covers_declared_still_campaign():
    root,receipt=tree()
    assert receipt["contract"]["max_records"]==512
    assert any("$Count lt 512" in x.get("value","") for x in root.iter())
    assert any("$Busy == ''" in x.get("value","") for x in root.iter())

def test_exact_seven_public_receipt_fields():
    root,_=tree();v=root.xpath("//set_value[@name='$Reply']")[0].get("exact")
    assert re.findall(r"\$(\w+)=",v)==["version","id","session","digest","status","observations","reason"]

def test_transport_correlation_and_source_revision_are_explicit():
    root,r=tree();v=root.xpath("//set_value[@name='$Observation']")[0].get("exact")
    for key in ("bridge_phase","bridge_call","bridge_generation","revision"):assert "$"+key+"=" in v
    assert re.fullmatch("[0-9a-f]{64}",r["revision"]) and r["revision"] in v
    assert r["runtime_verified"] is False and r["schema_qualified"] is False

def test_release_only_clears_reservation_not_file_evidence():
    root,_=tree();release=[x for x in root.iter("do_elseif") if "'release'" in x.get("value","")][0]
    assert "$Record.$state != 'prepared'" in release.get("value")
    assert not release.xpath(".//capture_screen")
    assert "NOT_FILE_CONFIRMATION" in E.tostring(release).decode()

def test_native_source_drift_refuses(monkeypatch,tmp_path):
    import hashlib
    for name in G.SOURCES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"synthetic-source")
        monkeypatch.setitem(G.SOURCES,name,hashlib.sha256(b"synthetic-source").hexdigest())
    _,receipt=G.prepare(tmp_path)
    assert receipt["native_source_verification"]=="CURRENT_EXACT_PINS"
    (tmp_path/"md/terraforming.xml").write_bytes(b"changed")
    with pytest.raises(ValueError,match="native source changed"):G.prepare(tmp_path)

def test_default_generation_does_not_claim_current_native_sources():
    _,receipt=G.prepare()
    assert receipt["native_source_verification"]=="DECLARED_PRIOR_SOURCE_PINS_NOT_RECHECKED"

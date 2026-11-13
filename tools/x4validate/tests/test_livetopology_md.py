"""Structural checks of actual emitted XML; no simulated engine execution."""
import importlib.util
from pathlib import Path
import re
from lxml import etree as E
SOURCE=Path(__file__).resolve().parents[3]/"scripts/live_topology_md.py"
spec=importlib.util.spec_from_file_location("topology_md",SOURCE);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
def tree():
    files,receipt=g.prepare()
    assert list(files)==["md/X4ToolkitTopology.xml"]
    return E.fromstring(next(iter(files.values()))),receipt
def test_only_read_actions_and_fixed_transport():
    root,_=tree()
    allowed={"mdscript","cues","cue","conditions","actions","event_ui_triggered","check_any","event_game_started","event_game_loaded",
        "debug_text","set_value","do_if","do_elseif","do_else","do_all","find_cluster","find_sector","find_zone","find_gate",
        "append_to_list","raise_lua_event"}
    assert {x.tag for x in root.iter()}<=allowed
    assert root.xpath("//event_ui_triggered/@control")==["'snapshot'"]
def test_native_find_has_no_owner_knowledge_or_active_filter():
    root,_=tree()
    for x in root.xpath("//find_cluster|//find_sector|//find_zone|//find_gate"):
        assert x.get("multiple")=="true"
        assert set(x.attrib)<= {"name","multiple","space"}
        assert len(x)==0
    assert all(x.get("space")=="$Scope" for x in root.xpath("//find_gate|//find_zone"))
    assert not root.xpath("//find_cluster/@space")
def test_scope_and_row_loops_bounded_before_processing():
    root,_=tree()
    loop=root.xpath("//do_all[@exact='$Sectors.count']")[0]
    assert loop.getparent().get("value")=="$Sectors.count le 4096"
    assert root.xpath("//set_value[@name='$Retained']/@exact")==["if $Total le 512 then $Total else 512"]
    assert root.xpath("//do_all[@exact='$Retained']")
def test_fresh_cue_does_not_default_failed_find_to_empty():
    root,_=tree()
    assert not root.xpath("//set_value[@name='$Found']")
    assert root.xpath("//do_if[@value='typeof @$Found == datatype.list']")
    assert root.xpath("//set_value[@name='$Total' and @exact='-1']")
def test_reply_and_row_fields_match_lua_exact_shapes():
    root,receipt=tree();lua=(SOURCE.parents[1]/"mods/x4_toolkit_helper/ui/live_topology.lua").read_text(encoding="utf-8")
    for label,key in (("fields","fields"),("row_fields","row_fields")):
        declared=lua.split("local "+label+"={",1)[1].split("}",1)[0]
        assert re.findall(r"'([a-z_]+)'",declared)==receipt[key]
    assert re.findall(r"(?:\[|,)\$(\w+)=",root.xpath("//append_to_list[@name='$Rows']/@exact")[0])==receipt["row_fields"]
def test_exit_observation_independent_of_active_route_claim():
    root,_=tree()
    exitguard=root.xpath("//do_if[@value='$Object.exit.exists']")[0]
    assert exitguard.getparent().get("value")=="$R.$kind == 'gates'"
    assert root.xpath("//set_value[@name='$r_active']/@exact")==["-1","$Object.isactive"]
def test_channel_and_native_age_boundaries():
    root,receipt=tree()
    assert root.xpath("//set_value[@name='$AgeBefore']/@exact")==["player.age"]
    assert root.xpath("//set_value[@name='$AgeAfter']/@exact")==["player.age"]
    assert "$After.$revision == $P.$revision" in root.xpath("//set_value[@name='$Stable']/@exact")[0]
    assert {x.tag for x in root.xpath("//cue[@name='Invalidate']/conditions/check_any/*")}=={"event_game_started","event_game_loaded"}
    assert receipt["runtime_verified"] is False and receipt["contract"]["zones_exclude_highways"] is True

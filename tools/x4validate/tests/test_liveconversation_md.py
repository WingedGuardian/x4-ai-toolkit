"""Structural controls of actual proposal bytes; not MD runtime simulation."""
import importlib.util
from pathlib import Path
import re
from lxml import etree as E

SOURCE=Path(__file__).resolve().parents[3]/"scripts/live_conversation_md.py"
spec=importlib.util.spec_from_file_location("conversation_md",SOURCE)
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


def tree():
    files,receipt=g.prepare()
    assert list(files)==["md/X4ToolkitConversation.xml"]
    assert receipt["runtime_verified"] is False and receipt["rendered_route"]=="BLOCKED"
    return E.fromstring(next(iter(files.values()))),receipt


def test_only_fixed_observation_state_and_transport_actions():
    root,_=tree()
    allowed={"mdscript","cues","cue","actions","conditions","check_any","event_game_loaded","event_game_started",
        "event_ui_triggered","event_conversation_started","event_conversation_next_section",
        "event_conversation_returned_to_section","event_conversation_finished","debug_text","set_value",
        "do_if","do_elseif","do_else","append_to_list","raise_lua_event"}
    assert {x.tag for x in root.iter()}<=allowed
    assert [x.get("control") for x in root.iter("event_ui_triggered")]==["'snapshot'"]


def test_all_four_native_events_are_instantiated_and_namespaced():
    root,_=tree()
    for name in ("Started","Section","Returned","Finished"):
        cue=root.xpath("//cue[@name=$name]",name=name)[0]
        assert cue.get("instantiate")=="true" and cue.get("namespace")=="this"
        assert len(cue.xpath("./conditions/*"))==1


def test_load_start_and_session_change_invalidate_history():
    root,_=tree();invalid=root.xpath("//cue[@name='Invalidate']")[0]
    assert {x.tag for x in invalid.xpath("./conditions/check_any/*")}=={"event_game_loaded","event_game_started"}
    assert invalid.xpath(".//set_value[@name=$v and @exact='false']",v=g.ROOT+".$Known")
    snapshot=root.xpath("//cue[@name='Snapshot']")[0]
    assert snapshot.xpath(".//do_if[@value=$v]",v=g.ROOT+".$Session != $R.$session")


def test_section_only_changes_under_actual_same_instance_guard():
    root,_=tree()
    for name in ("Section","Returned"):
        setter=root.xpath("//cue[@name=$name]//set_value[@name=$v and @exact='event.param']",name=name,v=g.ROOT+".$Section")[0]
        guard=setter.getparent().get("value")
        for required in (g.ROOT+".$Known","player.isinconversation","player.conversation", "event.object", ".{1}", ".{2}"):
            assert required in guard
    assert not root.xpath("//cue[@name='Started']//set_value[@name=$v and @exact='event.param']",v=g.ROOT+".$Section")


def test_finished_never_claims_outcome_as_current_section():
    root,_=tree();finish=root.xpath("//cue[@name='Finished']")[0]
    assert finish.xpath(".//set_value[@name=$v and @exact='false']",v=g.ROOT+".$Known")
    assert not finish.xpath(".//set_value[@exact='event.param']")


def test_scalar_template_seed_and_component_fields_are_explicit():
    root,_=tree()
    for p in ("live_","history_"):
        seed=root.xpath("//set_value[@name=$v]",v="$"+p+"seed")[-1]
        assert seed.get("exact").startswith("'%s'.[") and ".seed]" in seed.get("exact")
        assert root.xpath("//set_value[@name=$v and @exact=$value]",v="$"+p+"kind",value="'TEMPLATE'")


def test_reply_fields_and_queue_bound_match_real_lua_contract():
    root,receipt=tree();reply=root.xpath("//append_to_list")[0]
    assert re.findall(r"(?:\[|,)\$(\w+)=",reply.get("exact"))==receipt["fields"]
    assert any(p.get("value")=="player.entity."+g.QUEUE+".count lt 64" for p in reply.iterancestors())
    lua=(SOURCE.parents[1]/"mods/x4_toolkit_helper/ui/live_conversation.lua").read_text()
    declared=lua.split("local fields={",1)[1].split("}",1)[0]
    assert re.findall(r'"([a-z_]+)"',declared)==receipt["fields"]


def test_before_after_independent_properties_and_public_stamp_checked():
    root,_=tree();stable=root.xpath("//set_value[@name='$Stable']")[0].get("exact")
    for value in ("$ActiveBefore == $ActiveAfter","$ConversationBefore == $ConversationAfter","$ActorBefore","$ActorAfter",
                  "$PAfter.$session", "$PAfter.$generation", "$PAfter.$world_token", "$AgeAfter ge $AgeBefore"):
        assert value in stable


def test_every_event_advance_is_bounded():
    root,_=tree()
    updates=root.xpath("//set_value[@name=$v and contains(@exact,'+ 1')]",v=g.ROOT+".$Sequence")
    assert len(updates)==4
    for x in updates: assert x.getparent().get("value")==g.ROOT+".$Sequence lt 1000000"

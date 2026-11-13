"""Structural controls for the actual generated public proposal, not MD execution."""
import importlib.util
from pathlib import Path
import re
from lxml import etree as E

spec=importlib.util.spec_from_file_location("public_session_proposal",Path(__file__).resolve().parents[3]/"scripts/live_session_md.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


def tree():
    files,receipt=g.prepare()
    assert list(files)==["md/X4ToolkitSession.xml"]
    assert receipt["runtime_verified"] is False and receipt["world_lineage"]=="UNKNOWN"
    return E.fromstring(next(iter(files.values())))


def test_no_private_product_identifiers_or_gameplay_actions():
    root=tree()
    assert "ror" not in E.tostring(root).decode().lower()
    allowed={"mdscript","cues","cue","actions","conditions","check_any",
             "event_game_loaded","event_game_started","event_ui_triggered",
             "debug_text","set_value","do_if","do_elseif","do_else","append_to_list","raise_lua_event"}
    assert {n.tag for n in root.iter()} <= allowed


def test_native_load_and_start_invalidate_without_clearing_lineage():
    root=tree();cue=root.xpath("//cue[@name='Invalidate']")[0]
    assert len(cue.xpath("./conditions/check_any/event_game_loaded"))==1
    assert len(cue.xpath("./conditions/check_any/event_game_started"))==1
    setters={n.get("name"):n.get("exact") for n in cue.iter("set_value")}
    assert setters[g.ROOT+".$Session"]=="''"
    assert setters[g.ROOT+".$Generation"]=="''"
    assert g.ROOT+".$WorldToken" not in setters
    assert setters["player.entity."+g.QUEUE]=="[]"


def test_fresh_and_resume_are_separate_branches():
    root=tree()
    branches=root.xpath("//cue[@name='Handshake']//do_elseif")
    assert len(branches)==2
    fresh,resume=branches
    assert "$R.$mode == 'fresh'" in fresh.get("value")
    assert g.ROOT+".$Session == ''" in fresh.get("value")
    assert "$R.$mode == 'resume'" in resume.get("value")
    assert "$R.$session == "+g.ROOT+".$Session" in resume.get("value")
    assert len(fresh.xpath(".//set_value[@name=$name]",name=g.ROOT+".$WorldToken"))==1
    assert not resume.xpath(".//set_value[@name=$name]",name=g.ROOT+".$WorldToken")


def test_capacity_precedes_any_handshake_state_change():
    root=tree()
    changes=root.xpath("//cue[@name='Handshake']//set_value[starts-with(@name,$prefix)]",prefix=g.ROOT+".")
    assert len(changes)==4
    for setter in changes:
        assert any(a.get("value")=="player.entity."+g.QUEUE+".count lt 64" for a in setter.iterancestors())


def test_blackboard_recreated_after_lua_drains_it():
    root=tree()
    guard=root.xpath("//cue[@name='Handshake']//do_if[@value=$v]",v="not player.entity."+g.QUEUE+"?")[0]
    assert guard[0].get("name")=="player.entity."+g.QUEUE and guard[0].get("exact")=="[]"


def test_malformed_existing_queue_is_preserved_and_never_counted():
    root=tree()
    room=root.xpath("//do_if[@value=$v]",v="player.entity."+g.QUEUE+".count lt 64")[0]
    assert room.getparent().get("value")=="typeof player.entity."+g.QUEUE+" == datatype.list"
    rejected=room.getparent().getnext()
    assert rejected.tag=="do_else" and len(rejected)==1
    assert "REPLY_QUEUE_MALFORMED_NO_STATE_CHANGE" in rejected[0].get("text")


def test_reply_exact_fields_and_correlations():
    root=tree()
    reply=root.xpath("//append_to_list")[0].get("exact")
    fields=re.findall(r"(?:\[|,)\$(\w+)=",reply)
    assert fields==g.CONTRACT["fields"]
    for field in ("mode","challenge","generation"):
        assert "$"+field+"=$R.$"+field in reply


def test_current_state_exports_provider_and_revision():
    root=tree()
    setters=root.xpath("//set_value[@name=$name]",name="player.entity."+g.STATE)
    assert len(setters)==2
    for n in setters:
        assert "$provider='"+g.PROVIDER+"'" in n.get("exact")
        assert "$revision='"+g.REVISION+"'" in n.get("exact")


def test_unknown_mode_never_gets_ready_by_default():
    root=tree()
    kinds=root.xpath("//cue[@name='Handshake']//set_value[@name='$Kind']")
    assert [n.get("exact") for n in kinds]==["'REFUSED'","'STATE'","'READY'","'READY'"]
    assert kinds[2].getparent().tag=="do_elseif"
    assert kinds[3].getparent().tag=="do_elseif"


def test_schema_tree_differs_from_compiled_candidate_only_in_revision_literals():
    import hashlib
    files,receipt=g.prepare()
    old=b"336ed1c2d2bdd9779884332a32d54553f769bb88e2d8000376817424ad614686"
    data=files["md/X4ToolkitSession.xml"]
    assert data.count(receipt["revision"].encode())==4
    normalized=data.replace(receipt["revision"].encode(),old)
    assert hashlib.sha256(normalized).hexdigest()=="cfa9fdff826bf004886b83c5d43b543d49b50f2db65f8e03c0c21a686c9a3182"


def test_source_change_cannot_keep_the_old_revision(tmp_path):
    path=tmp_path/"changed_factory.py"
    path.write_bytes(Path(g.__file__).read_bytes()+b"\n# deliberate source mutation\n")
    changed_spec=importlib.util.spec_from_file_location("changed_session_factory",path)
    changed=importlib.util.module_from_spec(changed_spec);changed_spec.loader.exec_module(changed)
    assert changed.REVISION!=g.REVISION
    assert changed.prepare()[1]["sha256"]!=g.prepare()[1]["sha256"]


def test_line_endings_cannot_change_the_source_revision(tmp_path):
    source=Path(g.__file__).read_bytes().replace(b"\r\n",b"\n")
    for label,data in (("lf",source),("crlf",source.replace(b"\n",b"\r\n"))):
        path=tmp_path/(label+".py");path.write_bytes(data)
        spec=importlib.util.spec_from_file_location(label,path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        assert module.prepare()==g.prepare()


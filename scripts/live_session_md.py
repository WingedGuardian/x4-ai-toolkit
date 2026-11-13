"""Pure public-helper MD proposal; no file/application/game side effects.

Native analogues: sn_mod_support_apis md/lua_loader.xml Reload_Listener
(start/load invalidation), md/simple_menu_api.xml (UI table/blackboard bridge).
The emitted script contains no ROR identifier or scenario logic.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from lxml import etree as E

ROOT = "md.X4ToolkitSession.State"
PROVIDER = "x4-live-session-v1"
SCREEN = "X4ToolkitSession"
QUEUE = "$x4_toolkit_session_replies"
STATE = "$x4_toolkit_session_state"
CONTRACT = dict(version=1, provider=PROVIDER, screen=SCREEN, control="handshake",
                reply_event="X4ToolkitSession.Replies", invalidated_event="X4ToolkitSession.Invalidated",
                queue=QUEUE, state=STATE, modes=["probe","fresh","resume"], max_replies=64,
                fields=["kind","provider","revision","mode","challenge","generation","session",
                        "has_session","reason","age","world_token"])
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
REVISION = hashlib.sha256(json.dumps(dict(contract=CONTRACT,source_sha256=SOURCE_SHA256),
    sort_keys=True,separators=(",",":")).encode()).hexdigest()


def n(parent, tag, **attrs):
    return E.SubElement(parent,tag,**attrs)


def put(parent, name, value):
    return n(parent,"set_value",name=name,exact=value)


def prepare():
    doc=E.Element("mdscript",name="X4ToolkitSession")
    cues=n(doc,"cues")
    state=n(cues,"cue",name="State",namespace="this")
    actions=n(state,"actions")
    n(actions,"debug_text",text="'X4TKSESSION LOAD revision="+REVISION+"'",chance="100")
    for key,value in (("Binding","101"),("Session","''"),("Generation","''"),("WorldToken","''")):
        put(actions,"$"+key,value)

    invalid=n(cues,"cue",name="Invalidate",instantiate="true",namespace="this")
    conditions=n(invalid,"conditions"); anyevent=n(conditions,"check_any")
    n(anyevent,"event_game_loaded"); n(anyevent,"event_game_started")
    actions=n(invalid,"actions")
    n(actions,"debug_text",text="'X4TKSESSION INVALIDATED age=%s'.[player.age]",chance="100")
    put(actions,ROOT+".$Session","''"); put(actions,ROOT+".$Generation","''")
    entity=n(actions,"do_if",value="player.entity")
    put(entity,"player.entity."+QUEUE,"[]")
    put(entity,"player.entity."+STATE,
        "table[$provider='"+PROVIDER+"',$session='',$generation='',$world_token=@"+ROOT+".$WorldToken,$revision='"+REVISION+"',$age=player.age]")
    n(actions,"raise_lua_event",name="'X4ToolkitSession.Invalidated'")

    hello=n(cues,"cue",name="Handshake",instantiate="true",namespace="this")
    conditions=n(hello,"conditions")
    n(conditions,"event_ui_triggered",screen="'"+SCREEN+"'",control="'handshake'")
    actions=n(hello,"actions")
    put(actions,"$R","event.param3")
    valid=n(actions,"do_if",value="@"+ROOT+".$Binding == 101 and player.entity and player.galaxy and "
        "typeof @$R.$mode == datatype.string and typeof @$R.$session == datatype.string and "
        "typeof @$R.$challenge == datatype.string and typeof @$R.$generation == datatype.string")
    absent=n(valid,"do_if",value="not player.entity."+QUEUE+"?")
    put(absent,"player.entity."+QUEUE,"[]")
    typed=n(valid,"do_if",value="typeof player.entity."+QUEUE+" == datatype.list")
    room=n(typed,"do_if",value="player.entity."+QUEUE+".count lt 64")
    put(room,"$Kind","'REFUSED'");put(room,"$Reason","'MODE_OR_STATE_REFUSED'")
    put(room,"$Session","''");put(room,"$HasSession","false")
    probe=n(room,"do_if",value="$R.$mode == 'probe' and $R.$session == ''")
    put(probe,"$Kind","'STATE'");put(probe,"$Reason","'OBSERVED'")
    put(probe,"$Session",ROOT+".$Session");put(probe,"$HasSession",ROOT+".$Session != ''")
    fresh=n(room,"do_elseif",value="$R.$mode == 'fresh' and $R.$session != '' and "+ROOT+".$Session == ''")
    put(fresh,ROOT+".$Session","$R.$session")
    put(fresh,ROOT+".$Generation","$R.$generation")
    lineage=n(fresh,"do_if",value=ROOT+".$WorldToken == ''")
    put(lineage,ROOT+".$WorldToken","$R.$session")
    put(fresh,"$Kind","'READY'");put(fresh,"$Reason","'FRESH_ACK'")
    put(fresh,"$Session",ROOT+".$Session");put(fresh,"$HasSession","true")
    resume=n(room,"do_elseif",value="$R.$mode == 'resume' and $R.$session != '' and $R.$session == "+ROOT+".$Session")
    put(resume,ROOT+".$Generation","$R.$generation")
    put(resume,"$Kind","'READY'");put(resume,"$Reason","'RESUME_ACK'")
    put(resume,"$Session",ROOT+".$Session");put(resume,"$HasSession","true")
    # State is a separate current-value channel; replies echo the specific request.
    put(room,"player.entity."+STATE,"table[$provider='"+PROVIDER+"',$session="+ROOT+".$Session,$generation="+ROOT+
        ".$Generation,$world_token="+ROOT+".$WorldToken,$revision='"+REVISION+"',$age=player.age]")
    reply="table[$kind=$Kind,$provider='"+PROVIDER+"',$revision='"+REVISION+"',$mode=$R.$mode," \
          "$challenge=$R.$challenge,$generation=$R.$generation,$session=$Session,$has_session=$HasSession," \
          "$reason=$Reason,$age=player.age,$world_token="+ROOT+".$WorldToken]"
    n(room,"append_to_list",name="player.entity."+QUEUE,exact=reply)
    n(room,"raise_lua_event",name="'X4ToolkitSession.Replies'")
    n(room,"debug_text",text="'X4TKSESSION HANDSHAKE mode=%s kind=%s session=%s generation=%s challenge=%s age=%s'.[$R.$mode,$Kind,$Session,$R.$generation,$R.$challenge,player.age]",chance="100")
    full=n(typed,"do_else")
    n(full,"debug_text",text="'X4TKSESSION REFUSED reason=REPLY_QUEUE_FULL_NO_STATE_CHANGE'",chance="100")
    malformed=n(valid,"do_else")
    n(malformed,"debug_text",text="'X4TKSESSION REFUSED reason=REPLY_QUEUE_MALFORMED_NO_STATE_CHANGE'",chance="100")
    data=E.tostring(doc,encoding="utf-8",xml_declaration=True,pretty_print=True)
    return {"md/X4ToolkitSession.xml":data}, dict(contract=CONTRACT,revision=REVISION,source_sha256=SOURCE_SHA256,
        sha256=hashlib.sha256(data).hexdigest(),status="OFFLINE_PROPOSAL_NOT_APPLIED",
        world_lineage="UNKNOWN",runtime_verified=False,
        limitations=["Native invalidation/order/blackboard transfer unqualified",
                     "WorldToken is an MD-reported token, not proof of save persistence",
                     "Public manifest must disclose new MD variables and explicit action support",
                     "Lua boundary must validate exact keys/hex/length before sending"])


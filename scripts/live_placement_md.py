"""Pure diagnostic placement proposal; never writes XML or invokes the game.

READ: md/setup.xml:1774-1780 copies teleport_player with omitted control.
READ: menu_map.lua:27118-27129 -> tutorial_map_advanced.xml:273-274
carries a converted native ID and resolves component.{event.param3}.
Nested typed-ID transport is an explicit, unmeasured runtime gate.
"""
from pathlib import Path
import hashlib,json
from lxml import etree as E
ROOT="md.X4ToolkitPlacement.State"
PUBLIC="player.entity.$x4_toolkit_session_state"
QUEUE="$x4_toolkit_placement_replies"
PROVIDER="x4-diagnostic-placement-v1"
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
REVISION=hashlib.sha256(("PLACEMENT_V1:"+SOURCE_SHA256).encode()).hexdigest()
def n(p,t,**a):return E.SubElement(p,t,**a)
def put(p,k,v):return n(p,"set_value",name=k,exact=v)
def table(d):return "table["+",".join("$"+k+"="+v for k,v in d.items())+"]"
def prepare():
    doc=E.Element("mdscript",name="X4ToolkitPlacement")
    cues=n(doc,"cues");state=n(cues,"cue",name="State",namespace="this");a=n(state,"actions")
    n(a,"debug_text",text="'X4TKPLACEMENT LOAD revision="+REVISION+"'",chance="100")
    put(a,"$Binding","713");put(a,"$Records","[]");put(a,"$Busy","null")
    inv=n(cues,"cue",name="Invalidate",namespace="this",instantiate="true")
    ev=n(n(inv,"conditions"),"check_any");n(ev,"event_game_started");n(ev,"event_game_loaded")
    a=n(inv,"actions")
    put(a,ROOT+".$Records","[]");put(a,ROOT+".$Busy","null")
    yes=n(a,"do_if",value="player.entity");put(yes,"player.entity."+QUEUE,"[]")
    for phase in ("prepare","execute","status"):
        cue=n(cues,"cue",name=phase.title(),namespace="this",instantiate="true")
        n(n(cue,"conditions"),"event_ui_triggered",screen="'X4ToolkitPlacement'",control="'"+phase+"'")
        a=n(cue,"actions")
        n(a,"debug_text",text="'X4TKPLACEMENT TRIGGER phase="+phase+" age=%s'.[player.age]",chance="100")
        put(a,"$R","event.param3");put(a,"$P","@"+PUBLIC)
        valid=n(a,"do_if",value="@"+ROOT+".$Binding == 713 and player.entity and "
            "typeof @$R == datatype.table and typeof @$P == datatype.table and "
            "$P.$provider == 'x4-live-session-v1' and typeof @$P.$session == datatype.string and "
            "$P.$session != '' and @$R.$session == $P.$session and @$R.$generation == $P.$generation and "
            "@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and "
            "@$R.$revision == '"+REVISION+"' and typeof @$R.$id == datatype.string and "
            "typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string")
        q=n(valid,"do_if",value="not player.entity."+QUEUE+"?");put(q,"player.entity."+QUEUE,"[]")
        q=n(valid,"do_if",value="typeof player.entity."+QUEUE+" == datatype.list and player.entity."+QUEUE+".count lt 64")
        put(q,"$Target","component.{@$R.$target}")
        put(q,"$Record","null");put(q,"$Matches","0")
        each=n(q,"do_all",exact=ROOT+".$Records.count",counter="$i")
        match=n(each,"do_if",value=ROOT+".$Records.{$i}.$id == $R.$id")
        put(match,"$Record",ROOT+".$Records.{$i}");put(match,"$Matches","$Matches + 1")
        put(q,"$Result","'refused'");put(q,"$Reason","'request unavailable or refused'")
        eligible="$Target.exists and $Target.isclass.ship and not $Target.isclass.spacesuit and $Target.owner == faction.player"
        same=("$Matches == 1 and $Record.$digest == $R.$digest and $Record.$target == $Target and "
              "$Record.$session == $R.$session and $Record.$generation == $R.$generation and "
              "$Record.$world_token == $R.$world_token and $Record.$public_revision == $R.$public_revision")
        if phase=="prepare":
            yes=n(q,"do_if",value="$Matches == 0 and "+ROOT+".$Records.count lt 64 and not "+ROOT+".$Busy and "+eligible)
            put(yes,"$Record",table(dict(id="$R.$id",digest="$R.$digest",target="$Target",
                session="$R.$session",generation="$R.$generation",world_token="$R.$world_token",public_revision="$R.$public_revision",
                consumed="false",age="player.age",container="player.container",controlled="player.controlled",
                occupied="player.occupiedship")))
            n(yes,"append_to_list",name=ROOT+".$Records",exact="$Record")
            put(yes,"$Result","'prepared'");put(yes,"$Reason","'target and player context retained; no teleport'")
        elif phase=="execute":
            yes=n(q,"do_if",value=same+" and not $Record.$consumed")
            # Consume even when expired/drifted; there is no retry for this request.
            put(yes,"$Record.$consumed","true")
            ready=n(yes,"do_if",value="not "+ROOT+".$Busy and "+eligible+" and player.age ge $Record.$age and "
                "player.age le $Record.$age + 10s and player.container == $Record.$container and "
                "player.controlled == $Record.$controlled and player.occupiedship == $Record.$occupied")
            put(ready,ROOT+".$Busy","$Record")
            put(ready,"$Record.$dispatched","true");put(ready,"$Record.$dispatch_age","player.age")
            # Native setup.xml:1779; omitted control copied intentionally.
            n(ready,"teleport_player",object="$Target",instant="true",force="true")
            n(ready,"show_notification",text="'Diagnostic player transport requested.'")
            n(ready,"write_to_logbook",category="general",title="'Toolkit diagnostic placement'",text="'Transport requested; location and ship control require readback.'")
            put(ready,"$Result","'dispatched'");put(ready,"$Reason","'native teleport requested; outcome unverified'")
        else:
            yes=n(q,"do_if",value=same)
            put(yes,"$Result","if @$Record.$dispatched then 'dispatched' else (if $Record.$consumed then 'refused' else 'prepared')")
            put(yes,"$Reason","'current location observation; no action replay'")
        put(q,"$Occupied","$Target.exists and player.occupiedship == $Target")
        put(q,"$Controlled","$Target.exists and player.controlled == $Target")
        put(q,"$Container","$Target.exists and player.container == $Target")
        # Only observed target occupation/control releases the serial dispatch interlock.
        settled=n(q,"do_if",value=same+" and @$Record.$dispatched and "
            "("+ROOT+".$Busy == $Record) and ($Occupied or $Controlled)")
        put(settled,ROOT+".$Busy","null")
        fields=dict(kind="'PLACEMENT'",revision="'"+REVISION+"'",id="$R.$id",digest="$R.$digest",
            call="$R.$call",phase="'"+phase+"'",session="$R.$session",generation="$R.$generation",
            world_token="$R.$world_token",public_revision="$R.$public_revision",status="$Result",reason="$Reason",
            target="$Target",target_exists="$Target.exists",occupied="$Occupied",controlled="$Controlled",container="$Container",
            age="player.age",dispatched="@$Record.$dispatched == true",consumed="@$Record.$consumed == true")
        n(q,"append_to_list",name="player.entity."+QUEUE,exact=table(fields))
        n(q,"raise_lua_event",name="'X4ToolkitPlacement.Replies'")
    data=E.tostring(doc,encoding="utf-8",xml_declaration=True,pretty_print=True)
    return {"md/X4ToolkitPlacement.xml":data},dict(revision=REVISION,source_sha256=SOURCE_SHA256,
        fields=list(fields),output_sha256=hashlib.sha256(data).hexdigest(),runtime_verified=False,
        status="OFFLINE_PROPOSAL_NOT_APPLIED",scope="DIAGNOSTIC_TRANSPORT_NOT_WALKING_DOCKING_OR_SEAT_PROOF",
        runtime_gates=["nested uint64 UI-event transfer","actual target echo","teleport outcome and ship control"],
        limits=dict(records=64,replies=64,prepare_lifetime_sim_seconds=10),
        control_attribute="OMITTED_AS_NATIVE_SETUP_CALLER")

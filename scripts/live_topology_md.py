"""Pure topology snapshot proposal. Never applies XML or changes game topology.

Native analogues: upkeep_management.xml:25 clusters; inituniverse.xml:33-37
sectors/zones; setup_dlc_boron.xml:263-266,1482 gates/exit sector.
Find defaults cover normal clusters; find_zone excludes highways.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from lxml import etree as E
ROOT="md.X4ToolkitTopology.State"
PROVIDER="x4-topology-observer-v1"
SCREEN="X4ToolkitTopology"
QUEUE="$x4_toolkit_topology_replies"
PUBLIC="player.entity.$x4_toolkit_session_state"
LIMIT=512
CONTRACT=dict(version=1,provider=PROVIDER,screen=SCREEN,control="snapshot",
    event="X4ToolkitTopology.Replies",queue=QUEUE,kinds=["clusters","sectors","zones","gates"],
    max_rows=LIMIT,max_scope_sectors=4096,max_replies=64,
    scope="NORMAL_CLUSTER_NATIVE_FIND_DEFAULTS",zones_exclude_highways=True)
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
REVISION=hashlib.sha256(json.dumps(dict(contract=CONTRACT,source=SOURCE_SHA256),sort_keys=True,separators=(",",":")).encode()).hexdigest()
def n(parent,tag,**attrs): return E.SubElement(parent,tag,**attrs)
def put(parent,name,value): return n(parent,"set_value",name=name,exact=value)
def table(fields): return "table["+",".join("$"+k+"="+v for k,v in fields.items())+"]"
def prepare():
    doc=E.Element("mdscript",name="X4ToolkitTopology")
    cues=n(doc,"cues");state=n(cues,"cue",name="State",namespace="this");a=n(state,"actions")
    n(a,"debug_text",text="'X4TKTOPOLOGY LOAD revision="+REVISION+"'",chance="100")
    put(a,"$Binding","101")
    invalid=n(cues,"cue",name="Invalidate",instantiate="true",namespace="this")
    events=n(n(invalid,"conditions"),"check_any");n(events,"event_game_started");n(events,"event_game_loaded")
    a=n(invalid,"actions");yes=n(a,"do_if",value="player.entity");put(yes,"player.entity."+QUEUE,"[]")
    cue=n(cues,"cue",name="Snapshot",instantiate="true",namespace="this")
    n(n(cue,"conditions"),"event_ui_triggered",screen="'"+SCREEN+"'",control="'snapshot'")
    a=n(cue,"actions");n(a,"debug_text",text="'X4TKTOPOLOGY TRIGGER age=%s'.[player.age]",chance="100")
    put(a,"$R","event.param3");put(a,"$P","@"+PUBLIC)
    valid=n(a,"do_if",value="@"+ROOT+".$Binding == 101 and player.entity and player.galaxy and "
        "typeof @$P == datatype.table and @$P.$provider == 'x4-live-session-v1' and "
        "typeof @$P.$session == datatype.string and $P.$session != '' and "
        "typeof @$R.$request == datatype.string and @$R.$session == $P.$session and "
        "@$R.$generation == $P.$generation and @$R.$world_token == $P.$world_token and "
        "typeof @$R.$sector_macro == datatype.string and ($R.$kind == 'clusters' or $R.$kind == 'sectors' or "
        "$R.$kind == 'zones' or $R.$kind == 'gates')")
    absent=n(valid,"do_if",value="not player.entity."+QUEUE+"?");put(absent,"player.entity."+QUEUE,"[]")
    typed=n(valid,"do_if",value="typeof player.entity."+QUEUE+" == datatype.list")
    room=n(typed,"do_if",value="player.entity."+QUEUE+".count lt 64")
    for k,v in dict(AgeBefore="player.age",Rows="[]",Total="-1",ScopeTotal="-1",ScopeMatches="0",
        Status="'UNAVAILABLE'",Scope="null",Unknown="0").items(): put(room,"$"+k,v)
    clusters=n(room,"do_if",value="$R.$kind == 'clusters' and $R.$sector_macro == ''")
    n(clusters,"find_cluster",name="$Found",multiple="true")
    sectors=n(room,"do_elseif",value="$R.$kind == 'sectors' and $R.$sector_macro == ''")
    n(sectors,"find_sector",name="$Found",multiple="true")
    scoped=n(room,"do_elseif",value="($R.$kind == 'zones' or $R.$kind == 'gates') and $R.$sector_macro != ''")
    n(scoped,"find_sector",name="$Sectors",multiple="true")
    known=n(scoped,"do_if",value="typeof @$Sectors == datatype.list")
    put(known,"$ScopeTotal","$Sectors.count")
    bounded=n(known,"do_if",value="$Sectors.count le 4096")
    loop=n(bounded,"do_all",exact="$Sectors.count",counter="$i")
    live=n(loop,"do_if",value="$Sectors.{$i}.exists")
    match=n(live,"do_if",value="$Sectors.{$i}.macro.id == $R.$sector_macro")
    put(match,"$ScopeMatches","$ScopeMatches + 1");put(match,"$Scope","$Sectors.{$i}")
    dead=n(loop,"do_else");put(dead,"$Unknown","$Unknown + 1")
    one=n(bounded,"do_if",value="$Unknown == 0 and $ScopeMatches == 1")
    zone=n(one,"do_if",value="$R.$kind == 'zones'")
    n(zone,"find_zone",name="$Found",multiple="true",space="$Scope")
    gate=n(one,"do_else");n(gate,"find_gate",name="$Found",multiple="true",space="$Scope")
    missed=n(bounded,"do_else");put(missed,"$Status","'SCOPE_UNAVAILABLE'")
    excess=n(known,"do_else");put(excess,"$Status","'SCOPE_CAPACITY'")
    found=n(room,"do_if",value="typeof @$Found == datatype.list")
    put(found,"$Total","$Found.count");put(found,"$Status","'CAPTURED'")
    put(found,"$Retained","if $Total le "+str(LIMIT)+" then $Total else "+str(LIMIT))
    each=n(found,"do_all",exact="$Retained",counter="$i")
    put(each,"$Object","$Found.{$i}")
    fields=dict(object="0",exists="false",macro="''",cluster="0",cluster_macro="''",sector="0",sector_macro="''",
                zone="0",zone_macro="''",active="-1",exit="0",exit_macro="''",exit_sector="0",exit_sector_macro="''")
    for k,v in fields.items():put(each,"$r_"+k,v)
    exists=n(each,"do_if",value="$Object.exists")
    put(exists,"$r_object","$Object");put(exists,"$r_exists","true");put(exists,"$r_macro","$Object.macro.id")
    for key in ("cluster","sector","zone"):
        condition="$R.$kind == '"+key+"s'" if key!="sector" else "$R.$kind == 'sectors'"
        own=n(exists,"do_if",value=condition);put(own,"$r_"+key,"$Object");put(own,"$r_"+key+"_macro","$Object.macro.id")
        parent=n(exists,"do_elseif",value="$Object."+key+".exists")
        put(parent,"$r_"+key,"$Object."+key);put(parent,"$r_"+key+"_macro","$Object."+key+".macro.id")
    gate=n(exists,"do_if",value="$R.$kind == 'gates'")
    put(gate,"$r_active","$Object.isactive")
    exitgate=n(gate,"do_if",value="$Object.exit.exists")
    put(exitgate,"$r_exit","$Object.exit");put(exitgate,"$r_exit_macro","$Object.exit.macro.id")
    dest=n(exitgate,"do_if",value="$Object.exit.sector.exists")
    put(dest,"$r_exit_sector","$Object.exit.sector");put(dest,"$r_exit_sector_macro","$Object.exit.sector.macro.id")
    dead=n(each,"do_else");put(dead,"$Unknown","$Unknown + 1")
    n(each,"append_to_list",name="$Rows",exact=table({k:"$r_"+k for k in fields}))
    put(room,"$AgeAfter","player.age");put(room,"$After","@"+PUBLIC)
    put(room,"$Stable","typeof @$After == datatype.table and $After.$session == $R.$session and "
        "$After.$generation == $R.$generation and $After.$world_token == $R.$world_token and "
        "$After.$revision == $P.$revision and $AgeAfter ge $AgeBefore")
    reply=dict(kind="'SNAPSHOT'",provider="'"+PROVIDER+"'",revision="'"+REVISION+"'",request="$R.$request",
        session="$R.$session",generation="$R.$generation",world_token="$R.$world_token",query_kind="$R.$kind",
        sector_macro="$R.$sector_macro",age_before="$AgeBefore",age_after="$AgeAfter",stable="$Stable",
        status="$Status",total="$Total",scope_total="$ScopeTotal",scope_matches="$ScopeMatches",unknown="$Unknown",rows="$Rows")
    n(room,"append_to_list",name="player.entity."+QUEUE,exact=table(reply))
    n(room,"raise_lua_event",name="'"+CONTRACT["event"]+"'")
    data=E.tostring(doc,encoding="utf-8",xml_declaration=True,pretty_print=True)
    return {"md/X4ToolkitTopology.xml":data},dict(contract=CONTRACT,revision=REVISION,fields=list(reply),
        row_fields=list(fields),source_sha256=SOURCE_SHA256,sha256=hashlib.sha256(data).hexdigest(),
        runtime_verified=False,status="OFFLINE_PROPOSAL_NOT_APPLIED",
        limitations=["Normal clusters only; zones exclude highways; gates exclude highway endpoint classes",
        "Native enumeration counts are sampled; no atomic or persistent topology claim",
        "Configured gate exit does not establish active traversability or player knowledge"])

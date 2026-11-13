"""Pure fixed-directory native still-capture proposal. No output/game writes."""
from pathlib import Path
import hashlib
import json
from lxml import etree as E

SOURCES={
 "md/terraforming.xml":"fb76d17a88a847d1db337462a905b7de59c5163b9ef169ed3180ac017ceb615f",
 "libraries/md.xsd":"9959e42a07bb97225b35c16cede1043c8a55ad418fac22eeaf706642a1650756",
 "libraries/scriptproperties.xml":"1b641b638c8892284bbf5943f9ed283e0adf79f696341a2f00a2361f16485a8f",
}
PUBLIC="md.X4ToolkitSession.State"
ROOT="md.X4ToolkitCapture.State"
QUEUE="$x4_toolkit_capture_replies"
CAPACITY=Path(__file__).resolve().parents[1]/'tools/x4validate/x4validate/_livecapacity.py'
CONTRACT={"provider":"x4-screen-capture-v1","version":1,"screen":"X4ToolkitCapture","control":"request",
 "directory":"x4_toolkit_capture","operations":["prepare","execute","status","release","capacity"],
 "max_records":512,"max_replies":64,"settle_sim_seconds":4,"preparation_sim_ttl":30,
 "completion":"CAPTURE_ACTION_CALLED_NOT_FILE_OR_VISUAL_CONFIRMED"}
def sha(data): return hashlib.sha256(data).hexdigest()
def n(parent,tag,**attrs): return E.SubElement(parent,tag,**attrs)
def put(parent,name,value):return n(parent,"set_value",name=name,exact=value)
def capacity(name=None):
    raw=CAPACITY.read_bytes()
    scope={'__file__':str(CAPACITY),'__name__':'capture_capacity'}
    exec(compile(raw,str(CAPACITY),'exec'),scope)
    profile=scope['resolve'](name)
    if CAPACITY.read_bytes()!=raw:raise ValueError('capacity source drift')
    return profile,raw,scope['lua_source']()

def prepare(reference: Path | None = None, capacity_profile=None):
    profile,budget_source,_=capacity(capacity_profile)
    contract={**CONTRACT,'max_records':profile['capture_native_records']}
    # Native source hashes are inherited provenance, not a current-corpus claim.
    pins=dict(SOURCES)
    if reference is not None:
        reference=Path(reference)
        pins={k:sha((reference/k).read_bytes()) for k in SOURCES}
        if pins!=SOURCES: raise ValueError("reviewed native source changed")
    source_sha=sha(Path(__file__).read_bytes().replace(b"\r\n",b"\n"))
    revision=sha(json.dumps({"contract":contract,"source_sha256":source_sha,"native":pins,
        'capacity':profile,'capacity_source':sha(budget_source)},sort_keys=True,separators=(",",":")).encode())
    root=E.Element("mdscript",name="X4ToolkitCapture");cues=n(root,"cues")
    state=n(cues,"cue",name="State",namespace="this");a=n(state,"actions")
    for key,value in [("Binding","915"),("Records","table[]"),("Count","0"),("Busy","''")]:put(a,"$"+key,value)
    n(a,"debug_text",text="'X4TKCAP LOAD revision="+revision+"'",chance="100")
    invalid=n(cues,"cue",name="Invalidate",namespace="this",instantiate="true")
    condition=n(invalid,"conditions");events=n(condition,"check_any")
    n(events,"event_game_loaded");n(events,"event_game_started")
    a=n(invalid,"actions")
    for key,value in [("Records","table[]"),("Count","0"),("Busy","''")]:put(a,ROOT+".$"+key,value)
    present=n(a,"do_if",value="player.entity");put(present,"player.entity."+QUEUE,"[]")
    n(a,"raise_lua_event",name="'X4ToolkitCapture.Invalidated'")
    n(a,"debug_text",text="'X4TKCAP INVALIDATED age=%s'.[player.age]",chance="100")
    cue=n(cues,"cue",name="Request",namespace="this",instantiate="true")
    conditions=n(cue,"conditions");n(conditions,"event_ui_triggered",screen="'X4ToolkitCapture'",control="'request'")
    a=n(cue,"actions")
    n(a,"debug_text",text="'X4TKCAP TRIGGER'",chance="100")
    put(a,"$R","event.param3")
    valid=n(a,"do_if",value=f"@{ROOT}.$Binding == 915 and player.entity and player.galaxy and "
        f"typeof @$R.$id == datatype.string and typeof @$R.$digest == datatype.string and "
        f"typeof @$R.$call == datatype.string and typeof @$R.$phase == datatype.string and "
        f"typeof @$R.$binding == datatype.string and @$R.$session != '' and "
        f"@$R.$session == @{PUBLIC}.$Session and @$R.$generation == @{PUBLIC}.$Generation")
    absent=n(valid,"do_if",value="not player.entity."+QUEUE+"?");put(absent,"player.entity."+QUEUE,"[]")
    typed=n(valid,"do_if",value="typeof player.entity."+QUEUE+" == datatype.list")
    room=n(typed,"do_if",value="player.entity."+QUEUE+".count lt 64")
    put(room,"$Status","'refused'");put(room,"$Reason","'UNKNOWN_OR_UNQUALIFIED_REQUEST'")
    put(room,"$Key","$R.$id")
    put(room,"$Record",f"@{ROOT}.$Records.{{$Key}}")
    prep=n(room,"do_if",value=f"$R.$phase == 'prepare' and $Record == null and {ROOT}.$Busy == '' and {ROOT}.$Count lt {profile['capture_native_records']}")
    put(prep,"$Record","table[$id=$R.$id,$digest=$R.$digest,$binding=$R.$binding,$session=$R.$session,"
        "$generation=$R.$generation,$state='prepared',$prepared_age=player.age,$container=player.container,"
        "$sector=player.sector,$target=player.target,$external=player.camera.external,"
        "$fullscreen=player.isinfullscreenmenu,$conversation=player.isinconversation]")
    put(prep,f"{ROOT}.$Records.{{$Key}}","$Record");put(prep,ROOT+".$Busy","$R.$id")
    n(prep,"set_value",name=ROOT+".$Count",operation="add",exact="1")
    put(prep,"$Status","'prepared'");put(prep,"$Reason","'PREPARED_WAIT_4_SIM_SECONDS'")
    existing=n(room,"do_elseif",value="$Record != null and $Record.$digest == $R.$digest and $Record.$binding == $R.$binding "
        "and $Record.$session == $R.$session and $Record.$generation == $R.$generation")
    status=n(existing,"do_if",value="$R.$phase == 'status'")
    put(status,"$Status","$Record.$state");put(status,"$Reason","'RECORDED_STATE_NOT_FILE_CONFIRMATION'")
    execute=n(existing,"do_elseif",value=f"$R.$phase == 'execute' and $Record.$state == 'prepared' and {ROOT}.$Busy == $R.$id")
    # Consume before eligibility/action: no replay after ambiguous native capture.
    put(execute,"$Record.$state","'refused'")
    unchanged=n(execute,"do_if",value="player.age ge $Record.$prepared_age + 4s and player.age le $Record.$prepared_age + 30s "
        "and player.container == $Record.$container and player.sector == $Record.$sector "
        "and player.camera.external == $Record.$external "
        "and player.isinfullscreenmenu == $Record.$fullscreen and player.isinconversation == $Record.$conversation")
    put(unchanged,"$Record.$state","'running'");put(unchanged,"$Record.$capture_age","player.age")
    n(unchanged,"debug_text",text="'X4TKCAP TRIGGER id=%s session=%s generation=%s age=%s'.[$R.$id,$R.$session,$R.$generation,player.age]",chance="100")
    n(unchanged,"capture_screen",directory="'x4_toolkit_capture'")
    put(unchanged,"$Status","'running'");put(unchanged,"$Reason","'CAPTURE_SCREEN_CALLED_NOT_FILE_CONFIRMED'")
    failed=n(execute,"do_else");put(failed,"$Reason","'SETTLE_TIME_OR_PREPARED_CONTEXT_CHANGED_NO_CAPTURE'")
    # Release is explicitly bookkeeping, not evidence that a file exists.
    release=n(existing,"do_elseif",value=f"$R.$phase == 'release' and {ROOT}.$Busy == $R.$id and $Record.$state != 'prepared'")
    put(release,ROOT+".$Busy","''");put(release,"$Record.$released","true")
    put(release,"$Status","$Record.$state");put(release,"$Reason","'RESERVATION_RELEASED_NOT_FILE_CONFIRMATION'")
    put(room,"$Observation","table[$bridge_phase=$R.$phase,$bridge_call=$R.$call,$bridge_generation=$R.$generation,"
        "$revision='"+revision+"',$age=player.age,$directory='x4_toolkit_capture',$file_confirmed=false,"
        "$visual_pass=false,$prepared_age=@$Record.$prepared_age,$capture_age=@$Record.$capture_age,"
        "$reservation_released=@$Record.$released,$prepared_target=@$Record.$target,$capture_target=player.target,"
        "$camera_yaw=player.camera.yaw,$camera_pitch=player.camera.pitch,$camera_roll=player.camera.roll,"
        "$camera_zoom=player.camera.zoom,$camera_external=player.camera.external]")
    put(room,"$Reply","table[$version=1,$id=$R.$id,$session=$R.$session,$digest=$R.$digest,"
        "$status=$Status,$observations=$Observation,$reason=$Reason]")
    n(room,"append_to_list",name="player.entity."+QUEUE,exact="$Reply")
    n(room,"raise_lua_event",name="'X4ToolkitCapture.Replies'")
    n(room,"debug_text",text="'X4TKCAP RECEIPT id=%s phase=%s status=%s age=%s'.[$R.$id,$R.$phase,$Status,player.age]",chance="100")
    # Fixed read-only observation of retained world records; never reserve or release.
    cue=n(cues,'cue',name='Capacity',namespace='this',instantiate='true')
    n(n(cue,'conditions'),'event_ui_triggered',screen="'X4ToolkitCapture'",control="'capacity'")
    a=n(cue,'actions');n(a,'debug_text',text="'X4TKCAP TRIGGER capacity'",chance='100')
    put(a,'$R','event.param3');put(a,'$P','@player.entity.$x4_toolkit_session_state')
    valid=n(a,'do_if',value='@'+ROOT+'.$Binding == 915 and player.entity and '
        "typeof @$R == datatype.table and typeof @$P == datatype.table and $P.$provider == 'x4-live-session-v1' and "
        "typeof @$P.$session == datatype.string and $P.$session != '' and "
        '@$R.$session == $P.$session and @$R.$generation == $P.$generation and '
        '@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and '
        "@$R.$md_revision == '"+revision+"' and typeof @$R.$id == datatype.string and "
        'typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string and '
        'typeof @'+ROOT+'.$Count == datatype.integer and typeof @'+ROOT+'.$Busy == datatype.string')
    missing=n(valid,'do_if',value='not player.entity.'+QUEUE+'?');put(missing,'player.entity.'+QUEUE,'[]')
    q=n(valid,'do_if',value='typeof player.entity.'+QUEUE+' == datatype.list and player.entity.'+QUEUE+'.count lt 64')
    put(q,'$Observation',"table[$bridge_phase='capacity',$bridge_call=$R.$call,$bridge_generation=$R.$generation,"
        "$revision='"+revision+"',$age=player.age,$world_token=$P.$world_token,$public_revision=$P.$revision,"
        "$md_used="+ROOT+".$Count,$md_limit="+str(profile['capture_native_records'])+",$md_busy="+ROOT+".$Busy != '',"
        "$md_queued=player.entity."+QUEUE+".count,$md_queue_limit=64,$capacity_profile='"+profile['name']+"',"
        "$capacity_revision='"+profile['revision']+"']")
    put(q,'$Reply',"table[$version=1,$id=$R.$id,$session=$P.$session,$digest=$R.$digest,"
        "$status='observed',$observations=$Observation,$reason='READ_ONLY_CAPACITY_SAMPLE']")
    n(q,'append_to_list',name='player.entity.'+QUEUE,exact='$Reply')
    n(q,'raise_lua_event',name="'X4ToolkitCapture.Replies'")
    data=E.tostring(root,encoding="utf-8",xml_declaration=True,pretty_print=True)
    if CAPACITY.read_bytes()!=budget_source:raise ValueError('capacity source drift')
    return {"md/X4ToolkitCapture.xml":data},{"status":"OFFLINE_NATIVE_CAPTURE_PROPOSAL_NOT_APPLIED",
        "capacity_profile":profile,"capacity_source_sha256":sha(budget_source),
        "source_sha256":source_sha,"revision":revision,"contract":contract,"native_source_pins":pins,"native_source_verification":"CURRENT_EXACT_PINS" if reference is not None else "DECLARED_PRIOR_SOURCE_PINS_NOT_RECHECKED",
        "candidate_sha256":sha(data),"runtime_verified":False,"schema_qualified":False,
        "integration":"SDK_ADAPTER_AND_HOST_FILE_OBSERVER_BINDING_REQUIRED",
        "limitations":["Fixed subdirectory only; native filename/format/root not returned",
          "UI adapter must enforce exact fields/hex lengths and normalize native observations",
          "Current canonical public session/generation required before every dispatch and reply",
          "Prepared context is not exact camera pose or visual coverage",
          "Host must serialize until settled-file observation or quarantine; release is not evidence",
          "The native screenshot action may depend on runtime debug/UI state; no engine proof",
          "Load/start invalidates capture records and readiness; authoritative fresh public nonce required; no replay"]}

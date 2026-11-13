"""Pure diagnostic room-entry proposal, copied from native player-room sequences.

scenario_transport_refugees.xml:419-440 supplies waypoint, transport-success
and room-position actions. gs_boron2.xml:252-280 supplies leave-control and
the already-listening sibling stopped-control cue. No game data is embedded.
"""
from pathlib import Path
import hashlib,json
from lxml import etree as E

ROOT = 'md.X4ToolkitRoomEntry.State'
PUBLIC = 'player.entity.$x4_toolkit_session_state'
QUEUE = '$x4_toolkit_roomentry_replies'
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
CAPACITY=Path(__file__).resolve().parents[1]/'tools/x4validate/x4validate/_livecapacity.py'

def capacity(name=None):
    raw=CAPACITY.read_bytes();scope={'__file__':str(CAPACITY),'__name__':'roomentry_capacity'}
    exec(compile(raw,str(CAPACITY),'exec'),scope)
    profile=scope['resolve'](name);resolver=scope['lua_source']()
    if CAPACITY.read_bytes()!=raw:raise ValueError('capacity source drift')
    return profile,raw,resolver

def n(p, tag, **a): return E.SubElement(p, tag, **a)
def put(p, name, value): return n(p, 'set_value', name=name, exact=value)
def table(values): return 'table[' + ','.join('$'+k+'='+v for k,v in values.items()) + ']'

def current(record='$B'):
    return ('typeof @$P == datatype.table and '+record+' and '
            '$P.$provider == \'x4-live-session-v1\' and ' +
            ' and '.join('$P.$'+k+' == '+record+'.$'+k for k in ('session','generation','world_token','revision')))

def rooms(a, target, selector):
    put(a, '$Rooms', '[]')
    control=n(a, 'do_if', value=selector+" == 'controlroom'")
    exists=n(control, 'do_if', value=target+'.controlroom.exists')
    n(exists, 'append_to_list', name='$Rooms', exact=target+'.controlroom')
    dynamic=n(a,'do_elseif',value=selector+" == 'dynamicrooms'")
    # Native NPC_Instantiation:1641-1645 enumerates navcontexts then their rooms.
    n(dynamic,'find_object_component',name='$Interiors',object=target,**{'class':'class.navcontext'},multiple='true')
    put(dynamic,'$ScopeCount','$Interiors.count')
    bounded=n(dynamic,'do_if',value='$Interiors.count le 16')
    each=n(bounded,'do_all',exact='$Interiors.count',counter='$interior_index')
    live=n(each,'do_if',value='$Interiors.{$interior_index}.exists')
    put(live,'$InteriorRooms','$Interiors.{$interior_index}.rooms')
    roomgate=n(live,'do_if',value='typeof $InteriorRooms == datatype.list and $InteriorRooms.count le 64')
    loop=n(roomgate,'do_all',exact='$InteriorRooms.count',counter='$room_index')
    alive=n(loop,'do_if',value='$InteriorRooms.{$room_index}.exists')
    unique=n(alive,'do_if',value='$Rooms.indexof.{$InteriorRooms.{$room_index}} == 0')
    n(unique,'append_to_list',name='$Rooms',exact='$InteriorRooms.{$room_index}')
    invalid=n(loop,'do_else');put(invalid,'$ScopeComplete','false')
    invalid=n(live,'do_else');put(invalid,'$ScopeComplete','false')
    invalid=n(each,'do_else');put(invalid,'$ScopeComplete','false')
    invalid=n(dynamic,'do_else');put(invalid,'$ScopeComplete','false')
    other=n(a, 'do_else')
    # Macro string is validated by the closed Lua boundary; it is a lookup value, never an expression.
    n(other, 'find_room', name='$Rooms', object=target, multiple='true', walkable='true', macro='macro.{'+selector+'}')

def prepare(capacity_profile=None):
    profile,budget_source,_=capacity(capacity_profile)
    REVISION=hashlib.sha256(json.dumps(dict(source=SOURCE_SHA256,capacity=profile),
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    doc=E.Element('mdscript', name='X4ToolkitRoomEntry'); cues=n(doc,'cues')
    state=n(cues,'cue',name='State',namespace='this'); a=n(state,'actions')
    n(a,'debug_text',text="'X4TKROOM LOAD revision="+REVISION+"'",chance='100')
    put(a,'$Binding','714'); put(a,'$Records','[]'); put(a,'$Busy','null')
    inv=n(cues,'cue',name='Invalidate',namespace='this',instantiate='true')
    anyevent=n(n(inv,'conditions'),'check_any');n(anyevent,'event_game_started');n(anyevent,'event_game_loaded')
    a=n(inv,'actions');n(a,'debug_text',text="'X4TKROOM INVALIDATE'",chance='100')
    put(a,ROOT+'.$Records','[]');put(a,ROOT+'.$Busy','null')
    yes=n(a,'do_if',value='player.entity');put(yes,'player.entity.'+QUEUE,'[]')
    eligible="$Target.exists and ($Target.isclass.ship or $Target.isclass.station) and not $Target.isclass.spacesuit"
    same=("$Matches == 1 and $Record.$digest == $R.$digest and $Record.$target == $Target and "
          "$Record.$selector == $R.$selector and $Record.$choice == $Choice and " +
          " and ".join('$Record.$'+k+' == $R.$'+k for k in ('session','generation','world_token'))+
          ' and $Record.$revision == $R.$public_revision')
    for phase in ('prepare','execute','status'):
        cue=n(cues,'cue',name=phase.title(),namespace='this',instantiate='true')
        n(n(cue,'conditions'),'event_ui_triggered',screen="'X4ToolkitRoomEntry'",control="'"+phase+"'")
        a=n(cue,'actions');n(a,'debug_text',text="'X4TKROOM TRIGGER phase="+phase+"'",chance='100')
        put(a,'$R','event.param3');put(a,'$P','@'+PUBLIC)
        gate=n(a,'do_if',value='@'+ROOT+'.$Binding == 714 and player.entity and '
            'typeof @$R == datatype.table and typeof @$P == datatype.table and '
            "$P.$provider == 'x4-live-session-v1' and typeof @$P.$session == datatype.string and $P.$session != '' and "
            '@$R.$session == $P.$session and @$R.$generation == $P.$generation and '
            '@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and '
            "@$R.$revision == '"+REVISION+"' and typeof @$R.$id == datatype.string and "
            'typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string and '
            'typeof @$R.$selector == datatype.string')
        missing=n(gate,'do_if',value='not player.entity.'+QUEUE+'?');put(missing,'player.entity.'+QUEUE,'[]')
        q=n(gate,'do_if',value='typeof player.entity.'+QUEUE+' == datatype.list and player.entity.'+QUEUE+'.count lt 64')
        put(q,'$Target','component.{@$R.$target}');put(q,'$Choice','component.{@$R.$choice}')
        put(q,'$Record','null');put(q,'$Matches','0');put(q,'$Candidates','[]');put(q,'$CandidateMacros','[]');put(q,'$RoomCount','0')
        put(q,'$ScopeCount','0');put(q,'$ScopeComplete','true')
        loop=n(q,'do_all',exact=ROOT+'.$Records.count',counter='$i')
        yes=n(loop,'do_if',value=ROOT+'.$Records.{$i}.$id == $R.$id')
        put(yes,'$Record',ROOT+'.$Records.{$i}');put(yes,'$Matches','$Matches + 1')
        put(q,'$Result',"'refused'");put(q,'$Reason',"'request unavailable or refused'")
        if phase=='prepare':
            yes=n(q,'do_if',value='$Matches == 0 and '+ROOT+'.$Records.count lt '+str(profile['roomentry_records'])+' and not '+ROOT+'.$Busy and '+eligible)
            rooms(yes,'$Target','$R.$selector');put(yes,'$RoomCount','$Rooms.count')
            bounded=n(yes,'do_if',value='$ScopeComplete and $RoomCount le 16')
            put(bounded,'$Candidates','$Rooms');put(bounded,'$Selected','null');put(bounded,'$SelectedCount','0')
            loop=n(bounded,'do_all',exact='$Rooms.count',counter='$j')
            n(loop,'append_to_list',name='$CandidateMacros',exact='$Rooms.{$j}.macro.id')
            match=n(loop,'do_if',value="($Choice and $Rooms.{$j} == $Choice) or (not $Choice and $Rooms.count == 1 and $R.$selector != 'dynamicrooms')")
            put(match,'$Selected','$Rooms.{$j}');put(match,'$SelectedCount','$SelectedCount + 1')
            selected=n(bounded,'do_if',value='$SelectedCount == 1 and $Selected.exists')
            n(selected,'find_npc_waypoint',name='$Slot',object='$Selected',tags='tag.npctransport',excludeblocked='false')
            available=n(selected,'do_if',value='$Slot and $Slot.component.exists')
            put(available,'$Record',table(dict(id='$R.$id',digest='$R.$digest',target='$Target',selector='$R.$selector',choice='$Choice',
                session='$R.$session',generation='$R.$generation',world_token='$R.$world_token',revision='$R.$public_revision',
                room='$Selected',room_macro_id='$Selected.macro.id',slot='$Slot',consumed='false',dispatched='false',positioned='false',age='player.age',
                container='player.container',controlled='player.controlled',occupied='player.occupiedship',stage="'PREPARED'")))
            n(available,'append_to_list',name=ROOT+'.$Records',exact='$Record')
            put(available,'$Result',"'prepared'");put(available,'$Reason',"'exact room and native transporter waypoint retained; no transport'")
            no=n(selected,'do_else');put(no,'$Reason',"'matching room has no observed native transporter waypoint'")
            no=n(bounded,'do_else');put(no,'$Reason',"'room absent or ambiguous; exact retained choice required'")
            no=n(yes,'do_else');put(no,'$Reason',"'room or native context enumeration exceeds bounds or is unreadable'")
        elif phase=='execute':
            yes=n(q,'do_if',value=same+' and not $Record.$consumed')
            put(yes,'$Record.$consumed','true')
            ready=n(yes,'do_if',value='not '+ROOT+'.$Busy and '+eligible+' and player.age ge $Record.$age and '
                'player.age le $Record.$age + 10s and player.container == $Record.$container and '
                'player.controlled == $Record.$controlled and player.occupiedship == $Record.$occupied and '
                '$Record.$room.exists and $Record.$slot.component.exists')
            rooms(ready,'$Target','$R.$selector');put(ready,'$Present','0')
            each=n(ready,'do_all',exact='$Rooms.count',counter='$j')
            yes=n(each,'do_if',value='$Rooms.{$j} == $Record.$room');put(yes,'$Present','$Present + 1')
            present=n(ready,'do_if',value='$ScopeComplete and $Present == 1 and $Rooms.count le 16 and $Record.$room.macro.id == $Record.$room_macro_id')
            put(present,ROOT+'.$Busy','$Record');put(present,'$Record.$dispatched','true')
            put(present,'$Record.$dispatch_age','player.age')
            put(present,'$Result',"'dispatched'");put(present,'$Reason',"'native diagnostic room transport requested; actual room requires readback'")
            inside=n(present,'do_if',value='player.entity.hascontext.{$Target}')
            put(inside,'$Record.$stage',"'LEAVE_READY'");n(inside,'signal_cue',cue='LeaveControl')
            outside=n(present,'do_else');put(outside,'$Record.$stage',"'TELEPORTING'")
            n(outside,'teleport_player',object='$Record.$slot.component.controllable',instant='true',force='true')
            n(present,'show_notification',text="'Diagnostic room transport requested.'")
            n(present,'write_to_logbook',category='general',title="'Toolkit diagnostic room entry'",text="'Transport is diagnostic; ordinary walking, locks and docking are not tested.'")
        else:
            yes=n(q,'do_if',value=same)
            put(yes,'$Result',"if $Record.$dispatched then 'dispatched' else (if $Record.$consumed then 'refused' else 'prepared')")
            put(yes,'$Reason',"'actual player room observation; no action replay'")
        put(q,'$Room','if $Matches == 1 or $Result == \'prepared\' then @$Record.$room else null')
        put(q,'$Entered','$Room.exists and player.room == $Room and not player.controlled')
        settled=n(q,'do_if',value=same+' and $Record.$positioned and '+ROOT+'.$Busy == $Record and $Entered')
        put(settled,ROOT+'.$Busy','null')
        fields=dict(kind="'ROOM_ENTRY'",revision="'"+REVISION+"'",id='$R.$id',digest='$R.$digest',call='$R.$call',phase="'"+phase+"'",
            session='$R.$session',generation='$R.$generation',world_token='$R.$world_token',public_revision='$R.$public_revision',
            status='$Result',reason='$Reason',target='$Target',target_exists='$Target.exists',room='$Room',room_exists='$Room.exists',
            entered='$Entered',controlled='player.controlled != null',age='player.age',dispatched='@$Record.$dispatched == true',
            consumed='@$Record.$consumed == true',positioned='@$Record.$positioned == true',
            room_count='if $ScopeComplete then $RoomCount else false',candidates='$Candidates',candidate_macros='$CandidateMacros',
            room_macro="if $Room.exists then $Room.macro.id else ''",scope_count='$ScopeCount',scope_complete='$ScopeComplete',
            choice_required="$R.$selector == 'dynamicrooms' and not $Choice")
        n(q,'append_to_list',name='player.entity.'+QUEUE,exact=table(fields));n(q,'raise_lua_event',name="'X4ToolkitRoomEntry.Replies'")
    # This sibling observes retained counts only; it does not enumerate or enter rooms.
    cue=n(cues,'cue',name='Capacity',namespace='this',instantiate='true')
    n(n(cue,'conditions'),'event_ui_triggered',screen="'X4ToolkitRoomEntry'",control="'capacity'")
    a=n(cue,'actions');n(a,'debug_text',text="'X4TKROOM TRIGGER capacity'",chance='100')
    put(a,'$R','event.param3');put(a,'$P','@'+PUBLIC)
    valid=n(a,'do_if',value='@'+ROOT+'.$Binding == 714 and player.entity and '
        "typeof @$R == datatype.table and typeof @$P == datatype.table and $P.$provider == 'x4-live-session-v1' and "
        "typeof @$P.$session == datatype.string and $P.$session != '' and "
        '@$R.$session == $P.$session and @$R.$generation == $P.$generation and '
        '@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and '
        "@$R.$revision == '"+REVISION+"' and typeof @$R.$id == datatype.string and "
        'typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string and '
        'typeof @'+ROOT+'.$Records == datatype.list and '+ROOT+'.$Busy?')
    missing=n(valid,'do_if',value='not player.entity.'+QUEUE+'?');put(missing,'player.entity.'+QUEUE,'[]')
    q=n(valid,'do_if',value='typeof player.entity.'+QUEUE+' == datatype.list and player.entity.'+QUEUE+'.count lt 64')
    capacity_fields=dict(kind="'ROOM_CAPACITY'",revision="'"+REVISION+"'",id='$R.$id',digest='$R.$digest',call='$R.$call',
        phase="'capacity'",session='$P.$session',generation='$P.$generation',world_token='$P.$world_token',
        public_revision='$P.$revision',status="'observed'",reason="'READ_ONLY_CAPACITY_SAMPLE'",age='player.age',
        md_used=ROOT+'.$Records.count',md_limit=str(profile['roomentry_records']),md_busy='@'+ROOT+'.$Busy != null',
        md_queued='player.entity.'+QUEUE+'.count',md_queue_limit='64',capacity_profile="'"+profile['name']+"'",
        capacity_revision="'"+profile['revision']+"'")
    n(q,'append_to_list',name='player.entity.'+QUEUE,exact=table(capacity_fields))
    n(q,'raise_lua_event',name="'X4ToolkitRoomEntry.Replies'")
    # Sibling listeners are active before triggering mutations, as in the native Boron setup.
    for name,event in [('Teleported','event_player_teleport_successful'),('LeaveControl','event_cue_signalled'),('EnterRoom',None)]:
        cue=n(cues,'cue',name=name,namespace='this',instantiate='true');condition=n(cue,'conditions')
        if event:n(condition,event)
        else:
            anyevent=n(condition,'check_any');n(anyevent,'event_cue_signalled');n(anyevent,'event_player_stopped_control')
        a=n(cue,'actions');n(a,'debug_text',text="'X4TKROOM CONTINUE "+name+"'",chance='100')
        put(a,'$B','@'+ROOT+'.$Busy');put(a,'$P','@'+PUBLIC)
        valid=n(a,'do_if',value=current()+' and $B.$dispatched and player.age ge $B.$dispatch_age and '
            'player.age le $B.$dispatch_age + 30s and $B.$room.exists and $B.$room.macro.id == $B.$room_macro_id and '
            '$B.$slot.component.exists and player.entity.hascontext.{$B.$target}')
        if name=='Teleported':
            yes=n(valid,'do_if',value="$B.$stage == 'TELEPORTING'")
            put(yes,'$B.$stage',"'LEAVE_READY'");n(yes,'signal_cue',cue='LeaveControl')
        elif name=='LeaveControl':
            yes=n(valid,'do_if',value="$B.$stage == 'LEAVE_READY'")
            put(yes,'$B.$stage',"'LEAVING'")
            controlled=n(yes,'do_if',value='player.controlled');n(controlled,'leave_control_position')
            other=n(yes,'do_else');n(other,'signal_cue',cue='EnterRoom')
        else:
            yes=n(valid,'do_if',value="$B.$stage == 'LEAVING' and not player.controlled")
            put(yes,'$B.$stage',"'POSITION_CALLED'") # consumed before either placement action
            n(yes,'add_actor_to_room',actor='player.entity',object='$B.$slot.component')
            n(yes,'create_position',name='$Position',value='$B.$slot.offset',y='player.entity.race.height')
            pos=n(yes,'set_player_entity_position');n(pos,'position',value='$Position');n(pos,'rotation',value='rotation.[0,0,0]')
            put(yes,'$B.$positioned','true')
    data=E.tostring(doc,encoding='utf-8',xml_declaration=True,pretty_print=True)
    if CAPACITY.read_bytes()!=budget_source:raise ValueError('capacity source drift')
    return {'md/X4ToolkitRoomEntry.xml':data},dict(revision=REVISION,source_sha256=SOURCE_SHA256,
        capacity_profile=profile,capacity_source_sha256=hashlib.sha256(budget_source).hexdigest(),
        fields=list(fields),capacity_fields=list(capacity_fields),output_sha256=hashlib.sha256(data).hexdigest(),runtime_verified=False,
        status='OFFLINE_PROPOSAL_NOT_APPLIED',scope='DIAGNOSTIC_ROOM_PLACEMENT_NOT_WALKING_DOCKING_ACCESS_PROOF',
        limits=dict(records=profile['roomentry_records'],replies=64,room_candidates=16,preview_navcontexts=16,rooms_per_navcontext=64,
                    prepare_sim_seconds=10,continuation_sim_seconds=30),
        runtime_gates=['nested exact uint64 transfer','native room/waypoint geometry','event delivery','actual room readback'])

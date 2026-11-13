"""Pure diagnostic owned-ship warp proposal; no output writes or engine calls.

READ: terraforming.xml:453-479 supplies sector warp with position/rotation values;
98/106 supply create_position and rotation.[yaw,pitch,roll]. scriptproperties
55-60/1790-1815 define sector-relative position/rotation and native basis vectors.
The binding/queue/capacity protocol follows the existing own-code room instrument.
"""
from pathlib import Path
import hashlib,json
from lxml import etree as E

ROOT='md.X4ToolkitShipPlacement.State'
PUBLIC='player.entity.$x4_toolkit_session_state'
QUEUE='$x4_toolkit_shipplacement_replies'
CAPACITY=Path(__file__).resolve().parents[1]/'tools/x4validate/x4validate/_livecapacity.py'
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
PARAMS=('x_m','y_m','z_m','yaw_degrees','pitch_degrees','roll_degrees')

def n(p,t,**a):return E.SubElement(p,t,**a)
def put(p,k,v):return n(p,'set_value',name=k,exact=v)
def table(d):return 'table['+','.join('$'+k+'='+v for k,v in d.items())+']'
def capacity(name=None):
    raw=CAPACITY.read_bytes();scope={'__file__':str(CAPACITY),'__name__':'shipplacement_capacity'}
    exec(compile(raw,str(CAPACITY),'exec'),scope)
    profile=scope['resolve'](name);lua=scope['lua_source']()
    if not (CAPACITY.read_bytes()==raw): raise ValueError('capacity source drift')
    return profile,raw,lua

def pose(p,r):
    # Explicit metre normalization of position and the documented vector coordinates.
    return '['+','.join([p+'.'+a+'/1m' for a in ('x','y','z')]+
        [r+'.'+basis+'.'+a+'/1m' for basis in ('forward','right','up') for a in ('x','y','z')])+']'

def orientation_match(a,b):
    return ' and '.join(a+'.'+v+'.distanceto.{'+b+'.'+v+'} le 0.001m' for v in ('forward','right','up'))

def prepare(capacity_profile=None):
    profile,raw,_=capacity(capacity_profile)
    rev=hashlib.sha256(json.dumps(dict(source=SOURCE_SHA256,capacity=profile),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    limit=profile['native_history'];calls=limit*8
    doc=E.Element('mdscript',name='X4ToolkitShipPlacement');cues=n(doc,'cues')
    state=n(cues,'cue',name='State',namespace='this');a=n(state,'actions')
    n(a,'debug_text',text="'X4TKSHIPPLACE LOAD revision="+rev+"'",chance='100')
    put(a,'$Binding','716');put(a,'$Records','[]');put(a,'$Busy','null')
    inv=n(cues,'cue',name='Invalidate',namespace='this',instantiate='true')
    events=n(n(inv,'conditions'),'check_any');n(events,'event_game_started');n(events,'event_game_loaded')
    a=n(inv,'actions');n(a,'debug_text',text="'X4TKSHIPPLACE TRIGGER invalidated'",chance='100')
    put(a,ROOT+'.$Records','[]');put(a,ROOT+'.$Busy','null')
    yes=n(a,'do_if',value='player.entity');put(yes,'player.entity.'+QUEUE,'[]')
    for phase in ('prepare','execute','status','capacity'):
        cue=n(cues,'cue',name=phase.title(),namespace='this',instantiate='true')
        n(n(cue,'conditions'),'event_ui_triggered',screen="'X4ToolkitShipPlacement'",control="'"+phase+"'")
        a=n(cue,'actions');n(a,'debug_text',text="'X4TKSHIPPLACE TRIGGER "+phase+"'",chance='100')
        put(a,'$R','event.param3');put(a,'$P','@'+PUBLIC)
        valid=n(a,'do_if',value='@'+ROOT+'.$Binding == 716 and player.entity and '
            'typeof @$R == datatype.table and typeof @$P == datatype.table and '
            "$P.$provider == 'x4-live-session-v1' and typeof @$P.$session == datatype.string and $P.$session != '' and "
            '@$R.$session == $P.$session and @$R.$generation == $P.$generation and '
            '@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and '
            "@$R.$revision == '"+rev+"' and typeof @$R.$id == datatype.string and "
            'typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string and '
            'typeof @'+ROOT+'.$Records == datatype.list and '+ROOT+'.$Busy?')
        missing=n(valid,'do_if',value='not player.entity.'+QUEUE+'?');put(missing,'player.entity.'+QUEUE,'[]')
        q=n(valid,'do_if',value='typeof player.entity.'+QUEUE+' == datatype.list and player.entity.'+QUEUE+'.count lt 64')
        fields=dict(kind="'SHIP_PLACEMENT'",revision="'"+rev+"'",id='$R.$id',digest='$R.$digest',call='$R.$call',phase="'"+phase+"'",
            session='$P.$session',generation='$P.$generation',world_token='$P.$world_token',public_revision='$P.$revision',age='player.age')
        if phase=='capacity':
            fields.update(kind="'SHIP_PLACEMENT_CAPACITY'",status="'observed'",reason="'READ_ONLY_CAPACITY_SAMPLE'",
                md_used=ROOT+'.$Records.count',md_limit=str(limit),md_busy='@'+ROOT+'.$Busy != null',
                md_queued='player.entity.'+QUEUE+'.count',md_queue_limit='64',capacity_profile="'"+profile['name']+"'",
                capacity_revision="'"+profile['revision']+"'")
        else:
            put(q,'$Target','component.{@$R.$target}')
            put(q,'$Record','null');put(q,'$Matches','0')
            each=n(q,'do_all',exact=ROOT+'.$Records.count',counter='$i')
            match=n(each,'do_if',value=ROOT+'.$Records.{$i}.$id == $R.$id')
            put(match,'$Record',ROOT+'.$Records.{$i}');put(match,'$Matches','$Matches + 1')
            put(q,'$Result',"'refused'");put(q,'$Reason',"'unavailable, drifted or ineligible'")
            put(q,'$Pose','[]');put(q,'$Desired','[]');put(q,'$Sector','null');put(q,'$Speed','0');put(q,'$Eligible','false')
            put(q,'$PositionMatch','false');put(q,'$OrientationMatch','false');put(q,'$Observed','false')
            numbers=' and '.join('(typeof @$R.$'+k+' == datatype.integer or typeof @$R.$'+k+' == datatype.float or '
                'typeof @$R.$'+k+' == datatype.largeint or typeof @$R.$'+k+' == datatype.largefloat) and '
                '$R.$'+k+' ge -'+str(10000000 if k.endswith('_m') else 360)+' and $R.$'+k+' le '+
                str(10000000 if k.endswith('_m') else 360) for k in PARAMS)
            sane=n(q,'do_if',value=numbers)
            n(sane,'create_position',name='$RequestedPosition',x='$R.$x_m * 1m',y='$R.$y_m * 1m',z='$R.$z_m * 1m')
            put(sane,'$RequestedRotation','rotation.[$R.$yaw_degrees * 1deg,$R.$pitch_degrees * 1deg,$R.$roll_degrees * 1deg]')
            put(sane,'$Desired',pose('$RequestedPosition','$RequestedRotation'))
            present=n(sane,'do_if',value='$Target.exists and $Target.isclass.ship and not $Target.isclass.spacesuit and $Target.sector.exists')
            put(present,'$Sector','$Target.sector');put(present,'$Position','$Target.relativeposition.{$Sector}')
            put(present,'$Rotation','$Target.relativerotation.{$Sector}');put(present,'$Speed','$Target.speed / 1m')
            put(present,'$Observed','true');put(present,'$Pose',pose('$Position','$Rotation'))
            eligible=('$Target.isoperational and $Target.owner == faction.player and not $Target.dock and '
                'not $Target.assigneddock and not $Target.isinternallystored and $Sector == player.sector and '
                '$Target.speed ge 0m and $Target.speed le 1m')
            put(present,'$Eligible',eligible)
            same=('$Matches == 1 and $Record.$digest == $R.$digest and $Record.$target == $Target and '+
                ' and '.join('$Record.$'+k+' == $R.$'+k for k in ('session','generation','world_token','public_revision')+PARAMS))
            if phase=='prepare':
                good=n(present,'do_if',value='$Matches == 0 and '+ROOT+'.$Records.count lt '+str(limit)+' and not '+ROOT+'.$Busy and '
                    '$Eligible and $Position.distanceto.{$RequestedPosition} le 100km')
                values=dict(id='$R.$id',digest='$R.$digest',target='$Target',sector='$Sector',position='$Position',rotation='$Rotation',
                    wanted_position='$RequestedPosition',wanted_rotation='$RequestedRotation',age='player.age',consumed='false',dispatched='false',
                    container='player.container',controlled='player.controlled',occupied='player.occupiedship')
                values.update({k:'$R.$'+k for k in ('session','generation','world_token','public_revision')+PARAMS})
                put(good,'$Record',table(values));n(good,'append_to_list',name=ROOT+'.$Records',exact='$Record')
                put(good,'$Result',"'prepared'");put(good,'$Reason',"'sector pose and player context retained; no warp'")
            elif phase=='execute':
                exact=n(present,'do_if',value=same+' and not $Record.$consumed')
                put(exact,'$Record.$consumed','true')
                good=n(exact,'do_if',value='$Eligible and not '+ROOT+'.$Busy and $Sector == $Record.$sector and '
                    'player.age ge $Record.$age and player.age le $Record.$age + 10s and '
                    'player.container == $Record.$container and player.controlled == $Record.$controlled and player.occupiedship == $Record.$occupied and '
                    '$Position.distanceto.{$Record.$position} le 1m and '+orientation_match('$Rotation','$Record.$rotation')+
                    ' and $Position.distanceto.{$Record.$wanted_position} le 100km')
                put(good,ROOT+'.$Busy','$Record');put(good,'$Record.$dispatched','true');put(good,'$Record.$dispatch_age','player.age')
                # Working native sector-space warp: terraforming.xml:475-478.
                warp=n(good,'warp',object='$Target',sector='$Record.$sector')
                n(warp,'position',value='$Record.$wanted_position');n(warp,'rotation',value='$Record.$wanted_rotation')
                n(good,'show_notification',text="'Diagnostic ship placement requested.'")
                n(good,'write_to_logbook',category='general',title="'Toolkit ship placement'",text="'Warp requested; observed pose and collision safety remain separate.'")
                put(good,'$Result',"'dispatched'");put(good,'$Reason',"'warp requested; pre-dispatch pose in this reply'")
            else:
                exact=n(present,'do_if',value=same)
                put(exact,'$Result',"if $Record.$dispatched then 'dispatched' else (if $Record.$consumed then 'refused' else 'prepared')")
                put(exact,'$Reason',"'current sector pose sampled; no action replay'")
                good=n(exact,'do_if',value='$Record.$dispatched and $Eligible and $Sector == $Record.$sector and '
                    'player.age gt $Record.$dispatch_age and player.container == $Record.$container and '
                    'player.controlled == $Record.$controlled and player.occupiedship == $Record.$occupied')
                put(good,'$PositionMatch','$Position.distanceto.{$Record.$wanted_position} le 1m')
                put(good,'$OrientationMatch',orientation_match('$Rotation','$Record.$wanted_rotation'))
                settled=n(good,'do_if',value='$PositionMatch and $OrientationMatch and '+ROOT+'.$Busy == $Record')
                put(settled,ROOT+'.$Busy','null')
            fields.update(status='$Result',reason='$Reason',target='$Target',target_exists='$Target.exists',sector='$Sector',
                pose_observed='$Observed',eligible='$Eligible',pose='$Pose',desired='$Desired',speed_mps='$Speed',
                position_match='$PositionMatch',orientation_match='$OrientationMatch',dispatched='@$Record.$dispatched == true',
                consumed='@$Record.$consumed == true',busy='@'+ROOT+'.$Busy != null')
        n(q,'append_to_list',name='player.entity.'+QUEUE,exact=table(fields))
        n(q,'raise_lua_event',name="'X4ToolkitShipPlacement.Replies'")
    data=E.tostring(doc,encoding='utf-8',xml_declaration=True,pretty_print=True)
    if not (CAPACITY.read_bytes()==raw): raise ValueError('capacity source drift')
    return {'md/X4ToolkitShipPlacement.xml':data},dict(status='OFFLINE_PROPOSAL_NOT_APPLIED',runtime_verified=False,
        revision=rev,source_sha256=SOURCE_SHA256,capacity_profile=profile,capacity_source_sha256=hashlib.sha256(raw).hexdigest(),
        limits=dict(records=limit,calls=calls,replies=64,prepare_sim_seconds=10,max_displacement_m=100000,
            max_coordinate_m=10000000,max_speed_mps=1,position_tolerance_m=1,basis_vector_tolerance_m=0.001),
        pose_layout=['x_m','y_m','z_m','forward_x','forward_y','forward_z','right_x','right_y','right_z','up_x','up_y','up_z'],
        output_sha256=hashlib.sha256(data).hexdigest(),scope='DIAGNOSTIC_SECTOR_POSE_NOT_FLIGHT_OR_COLLISION_SAFETY',
        # Review r3-4: $Busy is cleared only when a status read sees the requested pose, or by a game
        # start/load (Invalidate cue). A warp that never reaches it keeps the provider locked until then.
        failed_dispatch_locks_provider_until_load=True,
        runtime_gates=['nested uint64 target transport','warp outcome and later pose readback',
                       'MD datatype of Lua numbers (integer, float, largeint or largefloat all accepted; which arrives is unmeasured)'])

"""Read-only native surface sampler; pure output, no file or game mutations.
Native move.claim.xml50-52 gives object-local surface/rotation; boarding.pod.xml48-63
checks physics readiness and handles null. No collision or containment inference.
"""
from pathlib import Path
import hashlib,json
from lxml import etree as E
ROOT="md.X4ToolkitSurface.State"
PUBLIC="player.entity.$x4_toolkit_session_state"
QUEUE="$x4_toolkit_surface_replies"
CAPACITY=Path(__file__).resolve().parents[1]/"tools/x4validate/x4validate/_livecapacity.py"
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
FACES=("xplus","xminus","yplus","yminus","zplus","zminus")
def n(p,t,**a):return E.SubElement(p,t,**a)
def put(p,k,v):return n(p,"set_value",name=k,exact=v)
def table(d):return "table["+",".join("$"+k+"="+v for k,v in d.items())+"]"
def vector(p):return "["+",".join(p+"."+a+"/1m" for a in "xyz")+"]"
def basis(r):return "["+",".join(r+"."+b+"."+a+"/1m" for b in ("forward","right","up") for a in "xyz")+"]"
def capacity(name=None):
 raw=CAPACITY.read_bytes();scope={"__file__":str(CAPACITY)}
 exec(compile(raw,str(CAPACITY),"exec"),scope)
 profile=scope["resolve"](name)
 assert CAPACITY.read_bytes()==raw,"capacity source drift"
 return profile,raw,scope["lua_source"]()
def prepare(capacity_profile=None):
 profile,raw,_=capacity(capacity_profile)
 rev=hashlib.sha256(json.dumps(dict(source=SOURCE_SHA256,capacity=profile),sort_keys=True,separators=(",",":")).encode()).hexdigest()
 limit=profile["native_history"]
 doc=E.Element("mdscript",name="X4ToolkitSurface");cues=n(doc,"cues")
 state=n(cues,"cue",name="State",namespace="this");a=n(state,"actions")
 n(a,"debug_text",text="'X4TKSURFACE LOAD revision="+rev+"'",chance="100")
 put(a,"$Binding","717");put(a,"$Records","[]")
 invalid=n(cues,"cue",name="Invalidate",namespace="this",instantiate="true")
 events=n(n(invalid,"conditions"),"check_any");n(events,"event_game_started");n(events,"event_game_loaded")
 a=n(invalid,"actions");n(a,"debug_text",text="'X4TKSURFACE TRIGGER invalidated'",chance="100")
 put(a,ROOT+".$Records","[]")
 yes=n(a,"do_if",value="player.entity");put(yes,"player.entity."+QUEUE,"[]")
 for phase in ("sample","capacity"):
  cue=n(cues,"cue",name=phase.title(),namespace="this",instantiate="true")
  n(n(cue,"conditions"),"event_ui_triggered",screen="'X4ToolkitSurface'",control="'"+phase+"'")
  a=n(cue,"actions");n(a,"debug_text",text="'X4TKSURFACE TRIGGER "+phase+"'",chance="100")
  put(a,"$R","event.param3");put(a,"$P","@"+PUBLIC)
  valid=n(a,"do_if",value="@"+ROOT+".$Binding == 717 and player.entity and typeof @$R == datatype.table and typeof @$P == datatype.table and "
   "$P.$provider == 'x4-live-session-v1' and typeof @$P.$session == datatype.string and $P.$session != '' and "
   "@$R.$session == $P.$session and @$R.$generation == $P.$generation and @$R.$world_token == $P.$world_token and "
   "@$R.$public_revision == $P.$revision and @$R.$revision == '"+rev+"' and typeof @$R.$id == datatype.string and "
   "typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string and typeof @"+ROOT+".$Records == datatype.list")
  missing=n(valid,"do_if",value="not player.entity."+QUEUE+"?");put(missing,"player.entity."+QUEUE,"[]")
  q=n(valid,"do_if",value="typeof player.entity."+QUEUE+" == datatype.list and player.entity."+QUEUE+".count lt 64")
  fields=dict(kind="'SURFACE_CAPACITY'" if phase=="capacity" else "'SURFACE'",revision="'"+rev+"'",id="$R.$id",digest="$R.$digest",call="$R.$call",
   phase="'"+phase+"'",session="$P.$session",generation="$P.$generation",world_token="$P.$world_token",public_revision="$P.$revision",age="player.age")
  if phase=="capacity":
   fields.update(status="'observed'",reason="'READ_ONLY_CAPACITY_SAMPLE'",md_used=ROOT+".$Records.count",md_limit=str(limit),
    md_queued="player.entity."+QUEUE+".count",md_queue_limit="64",capacity_profile="'"+profile["name"]+"'",capacity_revision="'"+profile["revision"]+"'")
  else:
   put(q,"$Result","'refused'");put(q,"$Reason","'ineligible or duplicate sample'")
   put(q,"$Matches","0")
   each=n(q,"do_all",exact=ROOT+".$Records.count",counter="$i")
   same=n(each,"do_if",value=ROOT+".$Records.{$i}.$id == $R.$id");put(same,"$Matches","$Matches + 1")
   for key,value in dict(Target="null",Sector="null",Exists="false",Ready="false",Observed="false",Found="false",Origin="[]",Hit="[]",Frame="[]",Pose="[]",Centre="[]",Sizes="[]").items():put(q,"$"+key,value)
   new=n(q,"do_if",value="$Matches == 0 and "+ROOT+".$Records.count lt "+str(limit))
   n(new,"append_to_list",name=ROOT+".$Records",exact=table(dict(id="$R.$id",digest="$R.$digest",call="$R.$call")))
   put(new,"$Target","component.{@$R.$target}")
   ready=n(new,"do_if",value="$Target.exists and $Target.isclass.ship and not $Target.isclass.spacesuit and "
    "$Target.isoperational and $Target.isplayerowned and $Target.sector == player.sector and $Target.isphysicsready and "
    "$Target.macro.boundingbox.exists and $Target.speed le 1m and $Target.attention ge attention.visible and "+
    " and ".join("$Target."+dim+" gt 0m and $Target."+dim+" le 100km" for dim in ("width","height","length"))+" and "+
    " and ".join("$Target.macro.boundingbox.center."+axis+" ge -10000km and $Target.macro.boundingbox.center."+axis+" le 10000km" for axis in "xyz"))
   put(ready,"$Exists","true");put(ready,"$Ready","true");put(ready,"$Sector","$Target.sector")
   put(ready,"$CentreValue","$Target.macro.boundingbox.center")
   put(ready,"$Centre",vector("$CentreValue"))
   put(ready,"$Sizes","[$Target.width/1m,$Target.height/1m,$Target.length/1m]")
   put(ready,"$HostPosition","$Target.relativeposition.{$Sector}");put(ready,"$HostRotation","$Target.relativerotation.{$Sector}")
   put(ready,"$Pose","["+",".join("$HostPosition."+axis+"/1m" for axis in "xyz")+","+
    ",".join("$HostRotation."+b+"."+axis+"/1m" for b in ("forward","right","up") for axis in "xyz")+"]")
   for index,face in enumerate(FACES):
    branch=n(ready,"do_if",value="$R.$face == '"+face+"'")
    coords=["$CentreValue."+axis for axis in "xyz"]
    coords[index//2]+=(" + " if index%2==0 else " - ")+"2 * $Target."+("width","height","length")[index//2]
    put(branch,"$OriginValue","position.["+",".join(coords)+"]")
    put(branch,"$Origin",vector("$OriginValue"))
    put(branch,"$HitValue","null");put(branch,"$SurfaceRotation","null")
    query=n(branch,"find_object_surface",object="$Target",space="$Target",posname="$HitValue",rotname="$SurfaceRotation",tolerateneighbour="false")
    n(query,"position",value="$OriginValue",object="$Target",space="$Target")
    hit=n(branch,"do_if",value="$HitValue != null and $SurfaceRotation != null")
    put(hit,"$Found","true");put(hit,"$Hit",vector("$HitValue"));put(hit,"$Frame",basis("$SurfaceRotation"))
    stable=n(branch,"do_if",value="$Target.exists and $Target.isoperational and $Target.isplayerowned and "
     "$Target.isphysicsready and $Target.sector == $Sector and player.sector == $Sector and $Target.speed le 1m and "
     "$Target.relativeposition.{$Sector}.distanceto.{$HostPosition} le 1m and "+
     " and ".join("$Target.relativerotation.{$Sector}."+b+".distanceto.{$HostRotation."+b+"} le 0.001m" for b in ("forward","right","up"))+
     " and @$R.$session == @"+PUBLIC+".$session and @$R.$generation == @"+PUBLIC+".$generation and "
     "@$R.$world_token == @"+PUBLIC+".$world_token and @$R.$public_revision == @"+PUBLIC+".$revision")
    put(stable,"$Observed","true");put(stable,"$Result","'observed'");put(stable,"$Reason","'native query result; no containment or complete collision claim'")
   fields.update(status="$Result",reason="$Reason",target="$Target",target_exists="$Exists",sector="$Sector",
    physics_ready="$Ready",sample_observed="$Observed",surface_found="$Found",face="$R.$face",origin="$Origin",surface_position="$Hit",
    surface_frame="$Frame",host_pose="$Pose",bbox_centre="$Centre",dimensions="$Sizes")
  n(q,"append_to_list",name="player.entity."+QUEUE,exact=table(fields))
  n(q,"raise_lua_event",name="'X4ToolkitSurface.Replies'")
 data=E.tostring(doc,encoding="utf-8",xml_declaration=True,pretty_print=True)
 assert CAPACITY.read_bytes()==raw,"capacity source drift"
 return {"md/X4ToolkitSurface.xml":data},dict(revision=rev,source_sha256=SOURCE_SHA256,capacity_profile=profile,
  output_sha256=hashlib.sha256(data).hexdigest(),runtime_verified=False,scope="NATIVE_READ_ONLY_OBJECT_LOCAL_SURFACE_SAMPLE")

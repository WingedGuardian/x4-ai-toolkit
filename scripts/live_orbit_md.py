"""Pure bounded native orbit proposal; no output writes or game calls.

Positive native sources: scenario_mining_1.xml:244-248,295-301 owns the
OrbitParameterized handle; story_diplomacy_intro.xml:9087-9096 delays its
foreign-scene check; scenario_tutorials.xml:3497-3517 reads camera angles/zoom.
"""
from pathlib import Path
import hashlib
import json
from lxml import etree as E

ROOT = "md.X4ToolkitOrbit.State"
PUBLIC = "player.entity.$x4_toolkit_session_state"
QUEUE = "$x4_toolkit_orbit_replies"
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
CAPACITY = Path(__file__).resolve().parents[1] / 'tools/x4validate/x4validate/_livecapacity.py'


def capacity(name=None):
    raw=CAPACITY.read_bytes()
    scope={'__file__':str(CAPACITY),'__name__':'orbit_capacity'}
    exec(compile(raw,str(CAPACITY),'exec'),scope)
    profile=scope['resolve'](name)
    if CAPACITY.read_bytes()!=raw:raise ValueError('capacity source drift')
    return profile,raw,scope['lua_source']()


def n(p, t, **a):
    return E.SubElement(p, t, **a)


def put(p, k, v):
    return n(p, "set_value", name=k, exact=v)


def table(d):
    return "table[" + ",".join("$" + k + "=" + v for k, v in d.items()) + "]"


def camera(p):
    for k, v in dict(yaw="player.camera.yaw", pitch="player.camera.pitch",
                     zoom="player.camera.zoom", external="player.camera.external",
                     fullscreen="player.isinfullscreencutscene").items():
        put(p, "$" + k, v)


def prepare(capacity_profile=None):
    profile,budget_source,_=capacity(capacity_profile)
    budget_sha=hashlib.sha256(budget_source).hexdigest()
    REVISION=hashlib.sha256(json.dumps(dict(source=SOURCE_SHA256,capacity=profile,
        capacity_source=budget_sha),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    doc = E.Element("mdscript", name="X4ToolkitOrbit")
    cues = n(doc, "cues")
    state = n(cues, "cue", name="State", namespace="this")
    a = n(state, "actions")
    n(a, "debug_text", text="'X4TKORBIT LOAD revision=" + REVISION + "'", chance="100")
    put(a, "$Binding", "109"); put(a, "$Records", "[]"); put(a, "$Busy", "null")
    inv = n(cues, "cue", name="Invalidate", namespace="this", instantiate="true")
    ev = n(n(inv, "conditions"), "check_any")
    n(ev, "event_game_started"); n(ev, "event_game_loaded")
    a = n(inv, "actions")
    put(a, ROOT + ".$Records", "[]"); put(a, ROOT + ".$Busy", "null")
    yes = n(a, "do_if", value="player.entity")
    put(yes, "player.entity." + QUEUE, "[]")

    # Global exact-ID observation; never infer stopped from fullscreen=false.
    stopped = n(cues, "cue", name="Stopped", namespace="this", instantiate="true")
    n(n(stopped, "conditions"), "event_cutscene_stopped")
    a = n(stopped, "actions")
    yes = n(a, "do_if", value="@" + ROOT + ".$Busy and @" + ROOT + ".$Busy.$scene and "
            "event.param2 == " + ROOT + ".$Busy.$scene")
    put(yes, ROOT + ".$Busy.$stopped", "true")
    put(yes, ROOT + ".$Busy.$stopped_age", "player.age")
    put(yes, ROOT + ".$Busy", "null")

    for phase in ("prepare", "execute", "status", "snapshot"):
        cue = n(cues, "cue", name=phase.title(), namespace="this", instantiate="true")
        n(n(cue, "conditions"), "event_ui_triggered", screen="'X4ToolkitOrbit'", control="'" + phase + "'")
        a = n(cue, "actions")
        n(a, "debug_text", text="'X4TKORBIT TRIGGER phase=" + phase + " age=%s'.[player.age]", chance="100")
        put(a, "$R", "event.param3"); put(a, "$P", "@" + PUBLIC)
        valid = n(a, "do_if", value="@" + ROOT + ".$Binding == 109 and player.entity and "
            "typeof @$R == datatype.table and typeof @$P == datatype.table and "
            "@$P.$provider == 'x4-live-session-v1' and typeof @$P.$session == datatype.string and "
            "$P.$session != '' and @$R.$session == $P.$session and @$R.$generation == $P.$generation and "
            "@$R.$world_token == $P.$world_token and @$R.$public_revision == $P.$revision and "
            "@$R.$revision == '" + REVISION + "' and typeof @$R.$id == datatype.string and "
            "typeof @$R.$digest == datatype.string and typeof @$R.$call == datatype.string")
        if phase=='snapshot':
            valid.set('value',valid.get('value')+' and typeof @'+ROOT+'.$Records == datatype.list and '+ROOT+'.$Busy?')
        missing = n(valid, "do_if", value="not player.entity." + QUEUE + "?")
        put(missing, "player.entity." + QUEUE, "[]")
        q = n(valid, "do_if", value="typeof player.entity." + QUEUE + " == datatype.list and player.entity." + QUEUE + ".count lt 64")
        put(q, "$Record", "null"); put(q, "$Matches", "0")
        loop = n(q, "do_all", exact=ROOT + ".$Records.count", counter="$i")
        match = n(loop, "do_if", value=ROOT + ".$Records.{$i}.$id == $R.$id")
        put(match, "$Record", ROOT + ".$Records.{$i}"); put(match, "$Matches", "$Matches + 1")
        put(q, "$Result", "'refused'"); put(q, "$Reason", "'request unavailable or refused'")
        same = ("$Matches == 1 and $Record.$digest == $R.$digest and $Record.$op == $R.$op and "
                "$Record.$session == $R.$session and $Record.$generation == $R.$generation and "
                "$Record.$world_token == $R.$world_token and $Record.$public_revision == $R.$public_revision")
        if phase == "snapshot":
            put(q, "$Result", "'observed'"); put(q, "$Reason", "'sampled player camera properties; pose and units unqualified'")
        elif phase == "prepare":
            new = n(q, "do_if", value="$Matches == 0 and " + ROOT + ".$Records.count lt " + str(profile['orbit_history']))
            start = n(new, "do_if", value="($R.$op == 'start' or $R.$op == 'follow') and not " + ROOT + ".$Busy and "
                "not player.isinfullscreencutscene and not player.isinconversation and not player.isinfullscreenmenu and "
                "(typeof @$R.$distance == datatype.integer or typeof @$R.$distance == datatype.float or typeof @$R.$distance == datatype.largeint or typeof @$R.$distance == datatype.largefloat) and $R.$distance ge 10 and $R.$distance le 100000 and "
                "(typeof @$R.$elevation == datatype.integer or typeof @$R.$elevation == datatype.float or typeof @$R.$elevation == datatype.largeint or typeof @$R.$elevation == datatype.largefloat) and $R.$elevation ge -100000 and $R.$elevation le 100000 and "
                "(typeof @$R.$yaw == datatype.integer or typeof @$R.$yaw == datatype.float or typeof @$R.$yaw == datatype.largeint or typeof @$R.$yaw == datatype.largefloat) and $R.$yaw ge -360 and $R.$yaw le 360 and "
                "(typeof @$R.$pitch == datatype.integer or typeof @$R.$pitch == datatype.float or typeof @$R.$pitch == datatype.largeint or typeof @$R.$pitch == datatype.largefloat) and $R.$pitch ge -90 and $R.$pitch le 90 and "
                "(typeof @$R.$duration == datatype.integer or typeof @$R.$duration == datatype.float or typeof @$R.$duration == datatype.largeint or typeof @$R.$duration == datatype.largefloat) and $R.$duration ge 1 and $R.$duration le 30")
            put(start, "$Target", "component.{@$R.$target}")
            eligible = n(start, "do_if", value="$Target.exists and $Target.isclass.ship and $Target.sector == player.sector")
            put(eligible, "$Record", table(dict(id="$R.$id", digest="$R.$digest", op="$R.$op",
                session="$R.$session", generation="$R.$generation", world_token="$R.$world_token", public_revision="$R.$public_revision",
                age="player.age", consumed="false", target="$Target", distance="$R.$distance", elevation="$R.$elevation",
                duration="$R.$duration", yaw="$R.$yaw", pitch="$R.$pitch", sector="player.sector", container="player.container", occupied="player.occupiedship")))
            stop = n(new, "do_elseif", value="$R.$op == 'stop' and @" + ROOT + ".$Busy and " +
                ROOT + ".$Busy.$id == @$R.$owner and " + ROOT + ".$Busy.$session == $R.$session and " +
                ROOT + ".$Busy.$generation == $R.$generation and " + ROOT + ".$Busy.$world_token == $R.$world_token and " +
                ROOT + ".$Busy.$public_revision == $R.$public_revision and @" + ROOT + ".$Busy.$scene and "
                "not @" + ROOT + ".$Busy.$stop_requested")
            put(stop, "$Record", table(dict(id="$R.$id", digest="$R.$digest", op="$R.$op",
                session="$R.$session", generation="$R.$generation", world_token="$R.$world_token", public_revision="$R.$public_revision",
                age="player.age", consumed="false", owner=ROOT + ".$Busy")))
            accepted = n(new, "do_if", value="$Record != null")
            n(accepted, "append_to_list", name=ROOT + ".$Records", exact="$Record")
            put(accepted, "$Result", "'prepared'"); put(accepted, "$Reason", "'retained native context; start requires 1.2 simulated seconds before execute'")
        elif phase == "execute":
            yes = n(q, "do_if", value=same + " and not $Record.$consumed")
            put(yes, "$Record.$consumed", "true")
            timely = n(yes, "do_if", value="player.age ge $Record.$age and player.age le $Record.$age + 10s")
            start = n(timely, "do_if", value="($Record.$op == 'start' or $Record.$op == 'follow') and player.age ge $Record.$age + 1.2s and "
                "not " + ROOT + ".$Busy and not player.isinfullscreencutscene and not player.isinconversation and "
                "not player.isinfullscreenmenu and $Record.$target.exists and $Record.$target.sector == player.sector and "
                "player.sector == $Record.$sector and player.container == $Record.$container and player.occupiedship == $Record.$occupied")
            put(start, ROOT + ".$Busy", "$Record"); put(start, "$Record.$dispatched", "true")
            put(start, "$Record.$dispatch_age", "player.age")
            orbit = n(start, "do_if", value="$Record.$op == 'start'")
            play = n(orbit, "play_cutscene", result="$Record.$scene", key="'OrbitParameterized'")
            n(play, "param", name="targetobject", object="$Record.$target")
            n(play, "param", name="orbitdist", number="$Record.$distance")
            n(play, "param", name="orbitelevation", number="$Record.$elevation")
            follow = n(start, "do_else")
            play = n(follow, "play_cutscene", result="$Record.$scene", key="'Cutscene_follow_and_rotate'")
            n(play, "param", name="target_object", object="$Record.$target")
            n(play, "param", name="duration", number="$Record.$duration * 1s")
            for parameter in ("distance", "yaw", "pitch"):
                for endpoint in ("start", "end"):
                    n(play, "param", name=parameter + "_" + endpoint, number="$Record.$" + parameter)
            put(start, "$Result", "'requested'"); put(start, "$Reason", "'native owned camera requested; not achieved pose'")
            stop = n(timely, "do_elseif", value="$Record.$op == 'stop' and $Record.$owner == " + ROOT + ".$Busy and "
                "@$Record.$owner.$scene and not @$Record.$owner.$stop_requested and not @$Record.$owner.$stopped")
            put(stop, "$Record.$dispatched", "true"); put(stop, "$Record.$owner.$stop_requested", "true")
            n(stop, "stop_cutscene", cutscene="$Record.$owner.$scene")
            put(stop, "$Result", "'stop_requested'"); put(stop, "$Reason", "'exact owned handle stop requested; await stopped event'")
        else:
            yes = n(q, "do_if", value=same)
            put(yes, "$Result", "if @$Record.$dispatched then 'requested' else (if $Record.$consumed then 'refused' else 'prepared')")
            put(yes, "$Reason", "'retained operation and current camera sample; no replay'")
        camera(q)
        put(q, "$Owner", "if @$Record.$op == 'stop' then @$Record.$owner else $Record")
        fields = dict(kind="'ORBIT'", revision="'" + REVISION + "'", id="$R.$id", digest="$R.$digest", call="$R.$call",
            phase="'" + phase + "'", session="$R.$session", generation="$R.$generation", world_token="$R.$world_token",
            public_revision="$R.$public_revision", status="$Result", reason="$Reason", age="player.age",
            consumed="@$Record.$consumed == true", dispatched="@$Record.$dispatched == true",
            handle="if @$Owner.$scene then '%s'.[$Owner.$scene] else ''", stopped="@$Owner.$stopped == true",
            stop_requested="@$Owner.$stop_requested == true", target="if @$Owner.$target.exists then $Owner.$target else 0", yaw="$yaw", pitch="$pitch",
            zoom="$zoom", external="$external", fullscreen="$fullscreen")
        if phase == 'snapshot':
            fields.update(md_used=ROOT+'.$Records.count',md_limit=str(profile['orbit_history']),
                md_busy='@'+ROOT+'.$Busy != null',md_queued='player.entity.'+QUEUE+'.count',md_queue_limit='64',
                capacity_profile="'"+profile['name']+"'",capacity_revision="'"+profile['revision']+"'")
        n(q, "append_to_list", name="player.entity." + QUEUE, exact=table(fields))
        n(q, "raise_lua_event", name="'X4ToolkitOrbit.Replies'")
        if phase == "execute":
            children = n(cue, "cues")
            timer = n(children, "cue", name="BoundedStop")
            # F370: the engine rejects a check_value-only <conditions> here ("event condition
            # required"); the gate lives in the delay (0 when false) and in the actions.
            gate = "(@parent.$Record.$op == 'start' or @parent.$Record.$op == 'follow') and @parent.$Record.$dispatched"
            n(timer, "delay", exact="(if " + gate + " and @parent.$Record.$duration then parent.$Record.$duration else 0) * 1s")
            a = n(timer, "actions")
            yes = n(a, "do_if", value=gate + " and parent.$Record == " + ROOT + ".$Busy and @parent.$Record.$scene and "
                "not @parent.$Record.$stop_requested and not @parent.$Record.$stopped")
            put(yes, "parent.$Record.$stop_requested", "true")
            n(yes, "stop_cutscene", cutscene="parent.$Record.$scene")
    data = E.tostring(doc, encoding="utf-8", xml_declaration=True, pretty_print=True)
    if CAPACITY.read_bytes()!=budget_source:raise ValueError('capacity source drift')
    return {"md/X4ToolkitOrbit.xml": data}, dict(revision=REVISION, source_sha256=SOURCE_SHA256,
        capacity_profile=profile,capacity_source_sha256=budget_sha,
        output_sha256=hashlib.sha256(data).hexdigest(), fields=list(fields), runtime_verified=False,
        scope="BOUNDED_NATIVE_ORBIT_FOLLOW_AND_RAW_CAMERA_PROPERTIES", achieved_pose_verified=False,
        angular_serialization_units="UNKNOWN", target_distance_verified=False,
        limits=dict(records=profile['orbit_history'], replies=64, duration_sim_seconds=30, prepare_lifetime_sim_seconds=10,
                    start_minimum_delay_sim_seconds=1.2),
        runtime_gates=["nested uint64 target transport", "cutscene native lifecycle", "raw camera property frame/units",
                       "MD datatype of Lua numbers (integer, float, largeint or largefloat all accepted; which arrives is unmeasured)"])

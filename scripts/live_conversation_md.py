"""Pure read-only conversation observer proposal; never applies XML.

Copies event wiring/typed actor branches from native md/conversations.xml:6–31,
307–318,1125–1132. Queue transport follows own live_session_md.py. See the
qualification document for the distinction between event history and current UI.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from lxml import etree as E

ROOT = "md.X4ToolkitConversation.State"
PROVIDER = "x4-conversation-observer-v1"
SCREEN = "X4ToolkitConversation"
QUEUE = "$x4_toolkit_conversation_replies"
PUBLIC = "player.entity.$x4_toolkit_session_state"
LIMIT = 1000000
CONTRACT = dict(version=1, provider=PROVIDER, screen=SCREEN, control="snapshot",
    event="X4ToolkitConversation.Replies", queue=QUEUE, sequence_limit=LIMIT,
    max_replies=64, scope="READ_ONLY_CONVERSATION_EVENT_HISTORY")
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
REVISION = hashlib.sha256(json.dumps(dict(contract=CONTRACT, source=SOURCE_SHA256),
    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def n(parent, tag, **attrs):
    return E.SubElement(parent, tag, **attrs)


def put(parent, name, value):
    return n(parent, "set_value", name=name, exact=value)


def clear(parent, counters=False):
    for key, value in (("Session", "''"), ("Conversation", "''"), ("Actor", "null"),
                       ("Section", "''"), ("Known", "false"), ("EventAge", "0s"),
                       ("EventKind", "'INVALIDATED'")):
        put(parent, ROOT + ".$" + key, value)
    if counters:
        put(parent, ROOT + ".$Sequence", "0")
        put(parent, ROOT + ".$Instance", "0")


def public_ok():
    return "typeof @$P == datatype.table and @$P.$provider == 'x4-live-session-v1' and " \
        "typeof @$P.$session == datatype.string and $P.$session != '' and " \
        "typeof @$P.$generation == datatype.string and $P.$generation != ''"


def same_actor(a, b):
    return "((typeof " + a + " == datatype.component and typeof " + b + " == datatype.component and " + a + " == " + b + ") or " \
        "(typeof " + a + " == datatype.list and typeof " + b + " == datatype.list and " \
        + a + ".count == 2 and " + b + ".count == 2 and " + a + ".{1} == " + b + ".{1} and " + a + ".{2} == " + b + ".{2}))"


def actor(parent, value, prefix):
    """Only a component crosses the Lua component converter; templates stay typed.

    npctemplateentry.seed is documented as a persistent identifying largeint in
    scriptproperties.xml:2046. Format it in MD, avoiding host float conversion.
    """
    for key, val in (("kind", "'UNKNOWN'"), ("component", "0"), ("context", "0"), ("seed", "'UNKNOWN'")):
        put(parent, "$" + prefix + key, val)
    component = n(parent, "do_if", value="typeof " + value + " == datatype.component and " + value + ".exists")
    put(component, "$" + prefix + "kind", "'COMPONENT'")
    put(component, "$" + prefix + "component", value)
    template = n(parent, "do_elseif", value="typeof " + value + " == datatype.list and " + value + ".count == 2 and "
        "typeof " + value + ".{1} == datatype.component and typeof " + value + ".{2} == datatype.npctemplate and "
        + value + ".{1}.people.{" + value + ".{2}}.exists")
    put(template, "$" + prefix + "kind", "'TEMPLATE'")
    put(template, "$" + prefix + "context", value + ".{1}")
    put(template, "$" + prefix + "seed", "'%s'.[" + value + ".{1}.people.{" + value + ".{2}}.seed]")


def prepare():
    root = E.Element("mdscript", name="X4ToolkitConversation")
    cues = n(root, "cues")
    state = n(cues, "cue", name="State", namespace="this")
    actions = n(state, "actions")
    n(actions, "debug_text", text="'X4TKCONV LOAD revision=" + REVISION + "'", chance="100")
    clear(actions, True)
    put(actions, ROOT + ".$Binding", "101")
    invalid = n(cues, "cue", name="Invalidate", instantiate="true", namespace="this")
    events = n(n(invalid, "conditions"), "check_any")
    n(events, "event_game_started"); n(events, "event_game_loaded")
    actions = n(invalid, "actions"); clear(actions, True)
    entity = n(actions, "do_if", value="player.entity")
    put(entity, "player.entity." + QUEUE, "[]")
    n(actions, "debug_text", text="'X4TKCONV INVALIDATED age=%s'.[player.age]", chance="100")

    for name, tag in (("Started", "event_conversation_started"),
                      ("Section", "event_conversation_next_section"),
                      ("Returned", "event_conversation_returned_to_section"),
                      ("Finished", "event_conversation_finished")):
        cue = n(cues, "cue", name=name, instantiate="true", namespace="this")
        n(n(cue, "conditions"), tag)
        actions = n(cue, "actions")
        put(actions, "$P", "@" + PUBLIC)
        valid = n(actions, "do_if", value="@" + ROOT + ".$Binding == 101 and " + public_ok())
        changed = n(valid, "do_if", value=ROOT + ".$Session != $P.$session")
        clear(changed, True); put(changed, ROOT + ".$Session", "$P.$session")
        room = n(valid, "do_if", value=ROOT + ".$Sequence lt " + str(LIMIT))
        put(room, ROOT + ".$Sequence", ROOT + ".$Sequence + 1")
        put(room, ROOT + ".$EventAge", "player.age")
        put(room, ROOT + ".$EventKind", "'" + name.upper() + "'")
        if name == "Started":
            put(room, ROOT + ".$Instance", ROOT + ".$Instance + 1")
            put(room, ROOT + ".$Conversation", "event.param")
            put(room, ROOT + ".$Actor", "event.object")
            put(room, ROOT + ".$Section", "''")
            put(room, ROOT + ".$Known", "true")
        elif name == "Finished":
            # Conservative: any finish invalidates; nested conversations are not
            # reconstructed from a guessed stack or the reported outcome.
            put(room, ROOT + ".$Known", "false"); put(room, ROOT + ".$Section", "''")
        else:
            matched = n(room, "do_if", value=ROOT + ".$Known and player.isinconversation and "
                "player.conversation == " + ROOT + ".$Conversation and " + same_actor("event.object", ROOT + ".$Actor"))
            put(matched, ROOT + ".$Section", "event.param")
            missed = n(room, "do_else")
            put(missed, ROOT + ".$Known", "false"); put(missed, ROOT + ".$Section", "''")
        full = n(valid, "do_else")
        put(full, ROOT + ".$Known", "false"); put(full, ROOT + ".$Section", "''")
        stale = n(actions, "do_else"); clear(stale)

    cue = n(cues, "cue", name="Snapshot", instantiate="true", namespace="this")
    n(n(cue, "conditions"), "event_ui_triggered", screen="'" + SCREEN + "'", control="'snapshot'")
    actions = n(cue, "actions")
    n(actions, "debug_text", text="'X4TKCONV TRIGGER age=%s'.[player.age]", chance="100")
    put(actions, "$R", "event.param3"); put(actions, "$P", "@" + PUBLIC)
    valid = n(actions, "do_if", value="@" + ROOT + ".$Binding == 101 and player.entity and " + public_ok() + " and "
        "typeof @$R.$request == datatype.string and @$R.$session == $P.$session and "
        "@$R.$generation == $P.$generation and @$R.$world_token == $P.$world_token")
    absent = n(valid, "do_if", value="not player.entity." + QUEUE + "?")
    put(absent, "player.entity." + QUEUE, "[]")
    typed = n(valid, "do_if", value="typeof player.entity." + QUEUE + " == datatype.list")
    room = n(typed, "do_if", value="player.entity." + QUEUE + ".count lt 64")
    changed = n(room, "do_if", value=ROOT + ".$Session != $R.$session")
    clear(changed, True); put(changed, ROOT + ".$Session", "$R.$session")
    for key, val in (("AgeBefore", "player.age"), ("ActiveBefore", "player.isinconversation"),
                     ("ConversationBefore", "if typeof player.conversation == datatype.string then player.conversation else ''"), ("ActorBefore", "player.conversationactor")):
        put(room, "$" + key, val)
    actor(room, "$ActorBefore", "live_"); actor(room, ROOT + ".$Actor", "history_")
    put(room, "$HistoryMatches", ROOT + ".$Known and " + ROOT + ".$Session == $R.$session and $ActiveBefore and "
        "$ConversationBefore == " + ROOT + ".$Conversation and " + same_actor("$ActorBefore", ROOT + ".$Actor"))
    for key, val in (("AgeAfter", "player.age"), ("ActiveAfter", "player.isinconversation"),
                     ("ConversationAfter", "if typeof player.conversation == datatype.string then player.conversation else ''"), ("ActorAfter", "player.conversationactor"),
                     ("PAfter", "@" + PUBLIC)):
        put(room, "$" + key, val)
    put(room, "$Stable", "$ActiveBefore == $ActiveAfter and $ConversationBefore == $ConversationAfter and "
        "(not $ActiveBefore or " + same_actor("$ActorBefore", "$ActorAfter") + ") and "
        "$PAfter.$session == $R.$session and $PAfter.$generation == $R.$generation and "
        "$PAfter.$world_token == $R.$world_token and $AgeAfter ge $AgeBefore")
    fields = dict(kind="'SNAPSHOT'", provider="'" + PROVIDER + "'", revision="'" + REVISION + "'",
        request="$R.$request", session="$R.$session", generation="$R.$generation", world_token="$R.$world_token",
        age_before="$AgeBefore", age_after="$AgeAfter", sequence=ROOT + ".$Sequence", instance=ROOT + ".$Instance",
        history_session=ROOT + ".$Session", history_conversation=ROOT + ".$Conversation", event_age=ROOT + ".$EventAge",
        event_kind=ROOT + ".$EventKind", section=ROOT + ".$Section", history_matches="$HistoryMatches", stable="$Stable",
        active_before="$ActiveBefore", active_after="$ActiveAfter", conversation_before="$ConversationBefore",
        conversation_after="$ConversationAfter")
    for prefix in ("live_", "history_"):
        for field in ("kind", "component", "context", "seed"):
            fields[prefix + field] = "$" + prefix + field
    n(room, "append_to_list", name="player.entity." + QUEUE,
        exact="table[" + ",".join("$" + key + "=" + val for key, val in fields.items()) + "]")
    n(room, "raise_lua_event", name="'" + CONTRACT["event"] + "'")
    data = E.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True)
    return {"md/X4ToolkitConversation.xml": data}, dict(contract=CONTRACT, revision=REVISION,
        source_sha256=SOURCE_SHA256, sha256=hashlib.sha256(data).hexdigest(), fields=list(fields),
        status="OFFLINE_PROPOSAL_NOT_APPLIED", runtime_verified=False,
        section_scope="HISTORICAL_EVENT_WITH_SAME_OBSERVED_INSTANCE_ONLY",
        rendered_route="BLOCKED", limitations=["No reconstructed nested-conversation stack",
        "Missed start or any finish leaves section unqualified until a new observed start",
        "MD event timing and player actor representation require runtime qualification"])

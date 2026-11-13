"""Optional diagnostic roomentry extension. Pure generation; no installation."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_roomentry_md.py",provider="mods/x4_toolkit_helper/ui/live_roomentry.lua",
    capacity="tools/x4validate/x4validate/_livecapacity.py")
def sha(b):return hashlib.sha256(b).hexdigest()
def prepare(capacity_profile=None):
    own=Path(__file__).read_bytes()
    raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    scope={"__file__":str(ROOT/PATHS["md"]),"__name__":"roomentry_md_embedded"}
    exec(compile(raw["md"],scope["__file__"],"exec"),scope)
    files,md=scope["prepare"](capacity_profile)
    profile,budget_source,resolver=scope['capacity'](capacity_profile)
    if budget_source!=raw['capacity'] or md['capacity_profile']!=profile:raise ValueError('capacity generation drift')
    pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
    if not (md["source_sha256"]==pins["md"]): raise ValueError("MD source changed")
    revision=sha(json.dumps(dict(sources=pins,generator=sha(own.replace(b"\r\n",b"\n")),
        contract="DIAGNOSTIC_ROOM_ENTRY_V1",capacity=profile),sort_keys=True,separators=(",",":")).encode())
    lua="local capacity_resolver=(function()\n"+resolver+"\nend)()\n"
    lua+="local provider=(function()\n"+raw["provider"].decode().replace("\r\n","\n")+"\nend)()\n"
    lua+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'automation bundle missing')
bundle.register_extension({id='x4-diagnostic-roomentry',revision='REVISION',
    providers={['x4-diagnostic-roomentry-v1']='REVISION'},attach=function(ctx)
        local selected=ctx.capacity_profile
        if selected==nil then selected='interactive' end
        assert(selected==CAPACITY_NAME,'roomentry capacity profile mismatch')
        assert((selected=='interactive' and ctx.capacity_revision==nil) or ctx.capacity_revision==CAPACITY_REVISION,
            'roomentry capacity revision mismatch')
        local c=ctx.create_md_channel({screen='X4ToolkitRoomEntry',event='X4ToolkitRoomEntry.Replies',
            queue='$x4_toolkit_roomentry_replies',controls={prepare=true,execute=true,status=true,capacity=true}})
        ctx.on_dispose(c.dispose)
        local api=provider({ffi=ctx.ffi,channel=c,resolve_issued=ctx.resolve_issued,encode=ctx.encode,
            revision='REVISION',md_revision='MDREVISION',capacity_profile=CAPACITY_NAME,capacity_resolver=capacity_resolver})
        ctx.on_dispose(api.dispose)
        ctx.registry.register(api.spec)
        return {dispose=api.dispose}
    end})
""".replace('CAPACITY_NAME',json.dumps(profile['name'])).replace('CAPACITY_REVISION',json.dumps(profile['revision'])).replace("MDREVISION",md["revision"]).replace("REVISION",revision)
    files["ui/live_roomentry_extension.lua"]=lua.encode()
    if not (Path(__file__).read_bytes()==own): raise ValueError("generator changed")
    if not (all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items())): raise ValueError("source drift")
    return files,dict(status="OFFLINE_PROPOSAL_NOT_APPLIED",runtime_verified=False,revision=revision,
        capacity_profile=profile,capacity_source_sha256=sha(budget_source),md_contract=md,
        provider_limits=dict(records=profile['roomentry_records'],calls=profile['roomentry_calls']),
        md_revision=md["revision"],source_sha256={PATHS[k]:sha(v) for k,v in raw.items()},
        generator_sha256=sha(own),output_sha256={k:sha(v) for k,v in files.items()},
        provider="x4-diagnostic-roomentry-v1",actions=["diagnostic_enter_room","capacity_observe"],
        scope="DIAGNOSTIC_TRANSPORT_NOT_ORDINARY_BOARDING_DOCKING_OR_WALKING",
        target_scope="HELPER_ISSUED_SHIP_OR_STATION_EXACT_ROOM_SELECTOR_OBSERVED_CHOICE",
        native_target_transport="UINT64_CDATA_IN_TABLE_RUNTIME_UNQUALIFIED",
        native_action="Native refugee waypoint/room positioning and Boron stopped-control sequence; no ordinary access proof",
        failed_dispatch="NO_RETRY_UNRESOLVED_RESERVATION_UNTIL_OBSERVED_EXACT_ROOM_AFTER_POSITION_OR_WORLD_INVALIDATION")

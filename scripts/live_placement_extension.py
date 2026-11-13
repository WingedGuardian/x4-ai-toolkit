"""Optional diagnostic placement extension. Pure generation; no installation."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_placement_md.py",provider="mods/x4_toolkit_helper/ui/live_placement.lua",
    ship_md="scripts/live_shipplacement_md.py",ship_provider="mods/x4_toolkit_helper/ui/live_shipplacement.lua",
    capacity="tools/x4validate/x4validate/_livecapacity.py")
def sha(b):return hashlib.sha256(b).hexdigest()
def prepare(capacity_profile=None):
    own=Path(__file__).read_bytes()
    raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    scope={"__file__":str(ROOT/PATHS["md"]),"__name__":"placement_md_embedded"}
    exec(compile(raw["md"],scope["__file__"],"exec"),scope)
    files,md=scope["prepare"]()
    ship_scope={"__file__":str(ROOT/PATHS['ship_md']),"__name__":"shipplacement_md_embedded"}
    exec(compile(raw['ship_md'],ship_scope['__file__'],'exec'),ship_scope)
    ship_files,ship_md=ship_scope['prepare'](capacity_profile)
    profile,budget_source,resolver=ship_scope['capacity'](capacity_profile)
    if not (budget_source==raw['capacity'] and ship_md['capacity_profile']==profile): raise ValueError('capacity generation drift')
    files.update(ship_files)
    pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
    if not (md["source_sha256"]==pins["md"]): raise ValueError("MD source changed")
    if not (ship_md['source_sha256']==pins['ship_md']): raise ValueError('ship MD source changed')
    revision=sha(json.dumps(dict(sources=pins,generator=sha(own.replace(b"\r\n",b"\n")),
        contract="DIAGNOSTIC_PLAYER_AND_SECTOR_SHIP_PLACEMENT_V2",capacity=profile),sort_keys=True,separators=(",",":")).encode())
    lua="local capacity_resolver=(function()\n"+resolver+"\nend)()\n"
    lua+="local provider=(function()\n"+raw["provider"].decode().replace("\r\n","\n")+"\nend)()\n"
    lua+="local ship_provider=(function()\n"+raw['ship_provider'].decode().replace('\r\n','\n')+"\nend)()\n"
    lua+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'automation bundle missing')
bundle.register_extension({id='x4-diagnostic-placement',revision='REVISION',
    providers={['x4-diagnostic-placement-v1']='REVISION',['x4-diagnostic-shipplacement-v1']='REVISION'},attach=function(ctx)
        local selected=ctx.capacity_profile;if selected==nil then selected='interactive' end
        assert(selected==CAPACITY_NAME,'placement capacity profile mismatch')
        assert((selected=='interactive' and ctx.capacity_revision==nil) or ctx.capacity_revision==CAPACITY_REVISION,
            'placement capacity revision mismatch')
        local c=ctx.create_md_channel({screen='X4ToolkitPlacement',event='X4ToolkitPlacement.Replies',
            queue='$x4_toolkit_placement_replies',controls={prepare=true,execute=true,status=true}})
        ctx.on_dispose(c.dispose)
        local api=provider({ffi=ctx.ffi,channel=c,resolve_issued=ctx.resolve_issued,encode=ctx.encode,
            revision='REVISION',md_revision='MDREVISION'})
        ctx.on_dispose(api.dispose)
        ctx.registry.register(api.spec)
        local sc=ctx.create_md_channel({screen='X4ToolkitShipPlacement',event='X4ToolkitShipPlacement.Replies',
            queue='$x4_toolkit_shipplacement_replies',controls={prepare=true,execute=true,status=true,capacity=true}})
        ctx.on_dispose(sc.dispose)
        local ship_api=ship_provider({ffi=ctx.ffi,channel=sc,resolve_issued=ctx.resolve_issued,encode=ctx.encode,
            revision='REVISION',md_revision='SHIPMDREVISION',capacity_profile=CAPACITY_NAME,capacity_resolver=capacity_resolver})
        ctx.on_dispose(ship_api.dispose);ctx.registry.register(ship_api.spec)
        local retired,failed=false,false
        local function dispose()
            if failed then return false end;if retired then return true end;retired=true
            for _,owner in ipairs({api,ship_api})do local ok,v=pcall(owner.dispose);if not ok or v~=true then failed=true end end
            return not failed
        end
        ctx.on_dispose(dispose)
        return {dispose=dispose}
    end})
""".replace('CAPACITY_NAME',json.dumps(profile['name'])).replace('CAPACITY_REVISION',json.dumps(profile['revision'])).replace('SHIPMDREVISION',ship_md['revision']).replace("MDREVISION",md["revision"]).replace("REVISION",revision)
    files["ui/live_placement_extension.lua"]=lua.encode()
    if not (Path(__file__).read_bytes()==own): raise ValueError("generator changed")
    if not (all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items())): raise ValueError("source drift")
    return files,dict(status="OFFLINE_PROPOSAL_NOT_APPLIED",runtime_verified=False,revision=revision,
        md_revision=md["revision"],source_sha256={PATHS[k]:sha(v) for k,v in raw.items()},
        generator_sha256=sha(own),output_sha256={k:sha(v) for k,v in files.items()},
        provider="x4-diagnostic-placement-v1",actions=["diagnostic_place_player"],
        ship_provider='x4-diagnostic-shipplacement-v1',ship_actions=['diagnostic_place_ship','capacity_observe'],
        capacity_profile=profile,ship_md=ship_md,legacy_player_md_unchanged=True,
        scope="DIAGNOSTIC_TRANSPORT_NOT_ORDINARY_BOARDING_DOCKING_OR_WALKING",
        target_scope="HELPER_ISSUED_PLAYER_OWNED_NON_SPACESUIT_SHIP",
        native_target_transport="UINT64_CDATA_IN_TABLE_RUNTIME_UNQUALIFIED",
        native_action="setup.xml:1779 teleport_player instant=true force=true; control omitted",
        failed_dispatch="NO_RETRY_UNRESOLVED_RESERVATION_UNTIL_OBSERVED_OCCUPATION_OR_WORLD_INVALIDATION")

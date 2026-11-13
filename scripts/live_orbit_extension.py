"""Pure optional bounded orbit/angle observer composition; no XML writes."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
PATHS = dict(md="scripts/live_orbit_md.py", provider="mods/x4_toolkit_helper/ui/live_orbit.lua",
    capacity="tools/x4validate/x4validate/_livecapacity.py")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def prepare(capacity_profile=None):
    own = Path(__file__).read_bytes()
    raw = {k: (ROOT / p).read_bytes() for k, p in PATHS.items()}
    scope = {"__file__": str(ROOT / PATHS["md"]), "__name__": "orbit_md_embedded"}
    exec(compile(raw["md"], scope["__file__"], "exec"), scope)
    files, md = scope["prepare"](capacity_profile)
    profile,budget_source,resolver=scope['capacity'](capacity_profile)
    if budget_source!=raw['capacity'] or md['capacity_profile']!=profile:raise ValueError('capacity generation drift')
    pins = {k: sha(v.replace(b"\r\n", b"\n")) for k, v in raw.items()}
    if not (md["source_sha256"] == pins["md"]): raise ValueError("MD source changed")
    revision = sha(json.dumps(dict(sources=pins, generator=sha(own.replace(b"\r\n", b"\n")),
        contract="BOUNDED_ORBIT_CAMERA_V1",capacity=profile), sort_keys=True, separators=(",", ":")).encode())
    lua = "local capacity_resolver=(function()\n"+resolver+"\nend)()\n"
    lua += "local provider=(function()\n" + raw["provider"].decode().replace("\r\n", "\n") + "\nend)()\n"
    lua += """
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'automation bundle missing')
bundle.register_extension({id='x4-orbit-camera',revision='REVISION',
    providers={['x4-orbit-camera-v1']='REVISION'},attach=function(ctx)
        local selected=ctx.capacity_profile
        if selected==nil then selected='interactive' end
        assert(selected==CAPACITY_NAME,'orbit capacity profile mismatch')
        assert((selected=='interactive' and ctx.capacity_revision==nil) or ctx.capacity_revision==CAPACITY_REVISION,
            'orbit capacity source mismatch')
        local c=ctx.create_md_channel({screen='X4ToolkitOrbit',event='X4ToolkitOrbit.Replies',
            queue='$x4_toolkit_orbit_replies',controls={prepare=true,execute=true,status=true,snapshot=true}})
        ctx.on_dispose(c.dispose)
        local api=provider({ffi=ctx.ffi,channel=c,resolve_issued=ctx.resolve_issued,encode=ctx.encode,
            revision='REVISION',md_revision='MDREVISION',capacity_profile=CAPACITY_NAME,capacity_resolver=capacity_resolver})
        ctx.on_dispose(api.dispose)
        ctx.registry.register(api.spec)
        return {dispose=api.dispose}
    end})
""".replace("CAPACITY_NAME",json.dumps(profile['name'])).replace("CAPACITY_REVISION",json.dumps(profile['revision'])).replace("MDREVISION", md["revision"]).replace("REVISION", revision)
    files["ui/live_orbit_extension.lua"] = lua.encode()
    if not (Path(__file__).read_bytes() == own): raise ValueError("generator drift")
    if not (all((ROOT / PATHS[k]).read_bytes() == v for k, v in raw.items())): raise ValueError("source drift")
    return files, dict(status="OFFLINE_PROPOSAL_NOT_APPLIED", runtime_verified=False,
        capacity_profile=profile,capacity_source_sha256=sha(budget_source),
        provider_limits=dict(records=profile['orbit_history'],calls=profile['orbit_calls']),
        revision=revision, md_revision=md["revision"], generator_sha256=sha(own),
        source_sha256={PATHS[k]: sha(v) for k, v in raw.items()},
        output_sha256={k: sha(v) for k, v in files.items()},
        provider="x4-orbit-camera-v1", actions=["camera_observe", "orbit_start", "follow_start", "orbit_stop"],
        scope=md["scope"], achieved_pose_verified=False, target_distance_verified=False,
        angle_serialization_units="UNKNOWN", md_contract=md,
        native_cutscene_dependency=["Base cutscenes/orbit parameterized.xml: OrbitParameterized",
            "Base cutscenes/cutscene_follow_and_rotate.xml: Cutscene_follow_and_rotate"],
        authored_cutscene_data_redistributed=False,
        runtime_gates=md["runtime_gates"])

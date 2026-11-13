"""Optional read-only native surface extension; pure generation, no installation."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_surface_md.py",provider="mods/x4_toolkit_helper/ui/live_surface.lua",
 capacity="tools/x4validate/x4validate/_livecapacity.py")
def sha(b):return hashlib.sha256(b).hexdigest()
def prepare(capacity_profile=None):
 own=Path(__file__).read_bytes();raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
 scope={"__file__":str(ROOT/PATHS["md"]),"__name__":"surface_md_embedded"}
 exec(compile(raw["md"],scope["__file__"],"exec"),scope)
 files,md=scope["prepare"](capacity_profile)
 profile,budget,resolver=scope["capacity"](capacity_profile)
 assert budget==raw["capacity"] and md["capacity_profile"]==profile,"capacity drift"
 pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
 assert md["source_sha256"]==pins["md"],"MD source drift"
 revision=sha(json.dumps(dict(sources=pins,generator=sha(own.replace(b"\r\n",b"\n")),
  contract="ONE_NATIVE_READ_ONLY_SURFACE_V1",capacity=profile),sort_keys=True,separators=(",",":")).encode())
 lua="local capacity_resolver=(function()\n"+resolver+"\nend)()\n"
 lua+="local provider=(function()\n"+raw["provider"].decode().replace("\r\n","\n")+"\nend)()\n"
 lua+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'automation bundle missing')
bundle.register_extension({id='x4-native-surface',revision='REVISION',
 providers={['x4-native-surface-v1']='REVISION'},attach=function(ctx)
    local selected=ctx.capacity_profile;if selected==nil then selected='interactive' end
    assert(selected==CAPACITY_NAME,'surface capacity profile mismatch')
    assert((selected=='interactive' and ctx.capacity_revision==nil) or ctx.capacity_revision==CAPACITY_REVISION,
        'surface capacity revision mismatch')
    local c=ctx.create_md_channel({screen='X4ToolkitSurface',event='X4ToolkitSurface.Replies',
        queue='$x4_toolkit_surface_replies',controls={sample=true,capacity=true}})
    ctx.on_dispose(c.dispose)
    local api=provider({ffi=ctx.ffi,channel=c,resolve_issued=ctx.resolve_issued,encode=ctx.encode,
        revision='REVISION',md_revision='MDREVISION',capacity_profile=CAPACITY_NAME,capacity_resolver=capacity_resolver})
    ctx.on_dispose(api.dispose);ctx.registry.register(api.spec)
    return {dispose=api.dispose}
 end})
""".replace("CAPACITY_NAME",json.dumps(profile["name"])).replace("CAPACITY_REVISION",json.dumps(profile["revision"])).replace("MDREVISION",md["revision"]).replace("REVISION",revision)
 files["ui/live_surface_extension.lua"]=lua.encode()
 assert Path(__file__).read_bytes()==own and all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items()),"source drift"
 return files,dict(status="OFFLINE_PROPOSAL_NOT_APPLIED",revision=revision,md_revision=md["revision"],
  source_sha256={str(ROOT/PATHS[k]):sha(v) for k,v in raw.items()},generator_sha256=sha(own),
  output_sha256={k:sha(v) for k,v in files.items()},capacity_profile=profile,
  provider="x4-native-surface-v1",actions=["surface_sample","capacity_observe"],
  runtime_verified=False,scope="READ_ONLY_OBJECT_LOCAL_SURFACE_SAMPLE_NOT_CONTAINMENT")

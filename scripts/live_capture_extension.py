"""Pure optional native still-capture extension. No game or filesystem writes.

The public factory consumes current own-code dependencies, never a private
worktree or historical git-show module. The native source hashes are declared
provenance unless an explicit reference root is supplied for rechecking.
"""
from pathlib import Path
import hashlib,json,types
ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_capture_md.py",
    adapter="mods/x4_toolkit_helper/ui/live_capture_adapter.lua",
    sdk="mods/x4_toolkit_helper/ui/live_actions.lua",
    channel="mods/x4_toolkit_helper/ui/live_mdchannel.lua",
    bundle="scripts/live_automation_bundle.py",capacity="tools/x4validate/x4validate/_livecapacity.py")
SDK=ROOT/PATHS["sdk"]
CHANNEL=ROOT/PATHS["channel"]
LOADER="-- Composition factory; trusted installed loader supplies the exact generated adapter.\nreturn function(bundle, adapter)\n    assert(type(bundle)==\"table\" and type(bundle.register_extension)==\"function\"\n        and type(adapter)==\"function\" and type(capacity_resolver)==\"function\",\"reviewed bundle/adapter/capacity required\")\n    bundle.register_extension({\n        id=\"x4-capture-extension-v1\",revision=\"@REVISION@\",\n        providers={[\"x4-screen-capture-v1\"]=\"@REVISION@\"},\n        attach=function(ctx)\n            local selected=ctx.capacity_profile\n            if selected==nil then selected=\"interactive\" end\n            assert(selected==@CAPACITY_NAME@,\"capture capacity profile mismatch\")\n            assert((selected==\"interactive\" and ctx.capacity_revision==nil) or ctx.capacity_revision==@CAPACITY_REVISION@,\n                \"capture capacity source mismatch\")\n            local channel=ctx.create_md_channel({screen=\"X4ToolkitCapture\",\n                event=\"X4ToolkitCapture.Replies\",queue=\"$x4_toolkit_capture_replies\",controls={request=true}})\n            -- Register ownership immediately, before any further fallible acquisition.\n            ctx.on_dispose(channel.dispose)\n            local api=adapter(ctx.registry,channel,{capacity_profile=@CAPACITY_NAME@,capacity_resolver=capacity_resolver})\n            ctx.on_dispose(api.dispose)\n            channel.arm_tick(api.state)\n            return {dispose=api.dispose,state=api.state}\n        end})\nend\n"
def sha(b):return hashlib.sha256(b).hexdigest()
def normalized(b):return b.replace(b"\r\n",b"\n")
def module(raw,path,name):
    m=types.ModuleType(name);m.__file__=str(path)
    exec(compile(raw,str(path),"exec"),m.__dict__);return m
def bundle_builder():
    path=ROOT/PATHS["bundle"];raw=path.read_bytes()
    m=module(raw,path,"capture_current_bundle")
    if path.read_bytes()!=raw:raise ValueError("bundle source drift")
    return m
def _produce(reference=None,capacity_profile=None):
    own=Path(__file__).read_bytes()
    raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    md=module(raw["md"],ROOT/PATHS["md"],"capture_current_md")
    candidates,native=md.prepare(reference,capacity_profile)
    profile,budget_source,resolver=md.capacity(capacity_profile)
    if budget_source!=raw["capacity"] or native["capacity_profile"]!=profile:raise ValueError("capacity generation drift")
    if native["source_sha256"]!=sha(normalized(raw["md"])):raise ValueError("MD source drift")
    relative_pins={PATHS[k]:sha(normalized(v)) for k,v in raw.items()}
    revision=sha(json.dumps(dict(sources=relative_pins,generator=sha(normalized(own)),
        md=sha(candidates["md/X4ToolkitCapture.xml"]),contract="PUBLIC_STILL_CAPTURE_V1",capacity=profile),
        sort_keys=True,separators=(",",":")).encode())
    adapter=normalized(raw["adapter"]).decode().replace("@REVISION@",revision).replace("@MD_REVISION@",native["revision"])
    registration="local capacity_resolver=(function()\n"+resolver+"\nend)()\n"+LOADER.replace("@REVISION@",revision).replace("@CAPACITY_NAME@",json.dumps(profile["name"])).replace("@CAPACITY_REVISION@",json.dumps(profile["revision"]))
    registration=registration.replace('controls={request=true}','controls={request=true,capacity=true}')
    parts={"ui/x4_capture_adapter.lua":adapter.encode(),"ui/x4_capture_extension.lua":registration.encode()}
    loader="-- Own-code optional native still-capture extension.\nlocal adapter=(function()\n"+adapter+"\nend)()\n"
    loader+="local register=(function()\n"+registration+"\nend)()\n"
    loader+="register(assert(rawget(_G,'X4ToolkitAutomationBundle'),'public automation bundle missing'),adapter)\n"
    candidates["ui/live_capture_extension.lua"]=loader.encode()
    if Path(__file__).read_bytes()!=own or any((ROOT/PATHS[k]).read_bytes()!=v for k,v in raw.items()):
        raise ValueError("capture source changed during generation")
    receipt=dict(status="OFFLINE_CAPTURE_EXTENSION_NOT_APPLIED",revision=revision,
        capacity_profile=profile,capacity_source_sha256=sha(budget_source),
        provider_limits=dict(records=profile["capture_history"],calls=profile["capture_calls"]),
        provider="x4-screen-capture-v1",actions=["capture","release","capacity_observe"],native_revision=native["revision"],
        source_sha256={PATHS[k]:sha(v) for k,v in raw.items()},generator_sha256=sha(own),
        output_sha256={p:sha(b) for p,b in candidates.items()},parts_sha256={p:sha(b) for p,b in parts.items()},
        md_sha256=sha(candidates["md/X4ToolkitCapture.xml"]),native=native,runtime_verified=False,
        release="BOOKKEEPING_ONLY_NOT_FILE_ATTESTATION",
        integration="optional loader after public bundle before world bootstrap",
        host_requirements=["Opaque observer settlement before release","Ambiguous output remains host-quarantined",
            "File observation is not visual content, achieved camera pose, causal origin or clip evidence"])
    return candidates,parts,receipt
def prepare(reference=None,capacity_profile=None):
    candidates,_,receipt=_produce(reference,capacity_profile)
    return candidates,receipt
def prepare_parts(reference=None,capacity_profile=None):
    """The same generated factory parts, for boundary qualification and composition."""
    _,parts,receipt=_produce(reference,capacity_profile)
    return parts,receipt

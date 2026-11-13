"""Pure optional topology extension; no application, native calls or shared bundle edits."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_topology_md.py",
    observer="mods/x4_toolkit_helper/ui/live_topology.lua",
    provider="mods/x4_toolkit_helper/ui/live_topology_provider.lua")

def sha(b): return hashlib.sha256(b).hexdigest()

def prepare():
    own=Path(__file__).read_bytes()
    raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    scope={"__file__":str(ROOT/PATHS["md"]),"__name__":"topology_md_embedded"}
    exec(compile(raw["md"],scope["__file__"],"exec"),scope)
    candidates,md=scope["prepare"]()
    assert scope["SOURCE_SHA256"]==sha(raw["md"].replace(b"\r\n",b"\n")),"MD source drift"
    pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
    revision=sha(json.dumps(dict(source=pins,generator=sha(own.replace(b"\r\n",b"\n")),
        md_revision=md["revision"],contract="TOPOLOGY_CAPTURE_AND_RETAINED_PAGES_V1"),
        sort_keys=True,separators=(",",":")).encode())
    source="-- Own-code topology extension. No constructor snapshot.\n"
    for name in ("observer","provider"):
        source+="local "+name+"=(function()\n"+raw[name].decode().replace("\r\n","\n")+"\nend)()\n"
    source+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'public automation bundle missing')
assert(type(bundle.register_extension)=='function','extension catalog missing')
bundle.register_extension({id='x4-topology-observer',revision='REVISION',
    providers={['x4-topology-observer-v1']='REVISION'},attach=function(ctx)
        assert(type(ctx.encode)=='function','bounded wire encoder missing')
        local channel=ctx.create_md_channel({screen='X4ToolkitTopology',event='X4ToolkitTopology.Replies',
            queue='$x4_toolkit_topology_replies',controls={snapshot=true}})
        local watch=observer({channel=channel,revision='MDREVISION',encode=ctx.encode})
        ctx.on_dispose(watch.dispose)
        local api=provider({observer=watch,revision='REVISION',public_current=ctx.public_current,encode=ctx.encode})
        ctx.on_dispose(api.dispose)
        ctx.registry.register(api.spec)
        return {dispose=api.dispose}
    end})
""".replace("MDREVISION",md["revision"]).replace("REVISION",revision)
    candidates["ui/live_topology_extension.lua"]=source.encode()
    assert Path(__file__).read_bytes()==own,"generator changed"
    assert all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items()),"extension source drift"
    return candidates,dict(status="OFFLINE_EXTENSION_PROPOSAL_NOT_APPLIED",runtime_verified=False,
        revision=revision,md_revision=md["revision"],provider="x4-topology-observer-v1",
        source_sha256={str(ROOT/PATHS[k]):sha(v) for k,v in raw.items()},generator_sha256=sha(own),
        output_sha256={p:sha(v) for p,v in candidates.items()},
        scope="NORMAL_CLUSTER_NATIVE_FIND_DEFAULTS_WITH_EXPLICIT_SECTOR_MACRO_SCOPE",
        integration="optional loader after public bundle, before world bootstrap",
        component_ids="OBSERVATION_ONLY_NOT_ISSUED",atomic=False,
        completion="metadata or one retained page, never complete universe or gameplay acceptance")

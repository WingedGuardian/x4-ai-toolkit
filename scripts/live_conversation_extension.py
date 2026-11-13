"""Pure installed-extension proposal for the fixed conversation observer.

Returns two candidate byte strings. The caller owns review/application and must
place the loader after the public automation bundle and before first construction.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATHS=dict(md="scripts/live_conversation_md.py",observer="mods/x4_toolkit_helper/ui/live_conversation.lua",
    provider="mods/x4_toolkit_helper/ui/live_conversation_provider.lua",
    commmenu="mods/x4_toolkit_helper/ui/live_commmenu.lua",commprovider="mods/x4_toolkit_helper/ui/live_commmenu_provider.lua",
    actorcontext="mods/x4_toolkit_helper/ui/live_actorcontext.lua",navigation="mods/x4_toolkit_helper/ui/live_mapnavigation.lua")

def sha(data): return hashlib.sha256(data).hexdigest()

def prepare():
    raw={name:(ROOT/path).read_bytes() for name,path in PATHS.items()}
    own=Path(__file__).read_bytes()
    scope={"__file__":str(ROOT/PATHS["md"]),"__name__":"conversation_md_embedded"}
    exec(compile(raw["md"],scope["__file__"],"exec"),scope)
    candidates,md=scope["prepare"]()
    assert scope["SOURCE_SHA256"]==sha(raw["md"].replace(b"\r\n",b"\n")),"MD source import drift"
    pins={name:sha(data.replace(b"\r\n",b"\n")) for name,data in raw.items()}
    revision=sha(json.dumps(dict(source=pins,generator=sha(own.replace(b"\r\n",b"\n")),
        md_revision=md["revision"],contract="CONVERSATION_SNAPSHOT_AND_NATIVE_COMM_NAVIGATION_V4"),sort_keys=True,separators=(",",":")).encode())
    source="-- Own-code observer extension; no game content and no constructor snapshot.\n"
    for name in ("observer","provider","commmenu","actorcontext","navigation","commprovider"):
        source+="local "+name+"=(function()\n"+raw[name].decode().replace("\r\n","\n")+"\nend)()\n"
    source+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'public automation bundle missing')
assert(type(bundle.register_extension)=='function','frozen extension catalog unavailable')
bundle.register_extension({id='x4-conversation-observer',revision='REVISION',
    providers={['x4-conversation-observer-v1']='REVISION',['x4-comm-menu-v1']='REVISION'},
    attach=function(ctx)
        local channel=ctx.create_md_channel({screen='X4ToolkitConversation',event='X4ToolkitConversation.Replies',
            queue='$x4_toolkit_conversation_replies',controls={snapshot=true}})
        local watch=observer({channel=channel,revision='MDREVISION'})
        ctx.on_dispose(watch.dispose)
        local api=provider({observer=watch,revision='REVISION',public_current=ctx.public_current})
        ctx.on_dispose(api.dispose)
        ctx.registry.register(api.spec)
        local g,ffi=ctx.globals,ctx.ffi
        assert(type(g)=='table' and ffi,'native comm bindings unavailable')
        local convert,cell,valid,active=g.ConvertIDTo64Bit,g.GetCellContent,g.IsValidWidgetElement,g.IsButtonActive
        for _,name in ipairs({'ConvertIDTo64Bit','GetCellContent','IsValidWidgetElement','IsButtonActive','GetLocalMousePosition'}) do
            assert(type(g[name])=='function','native comm function unavailable: '..name)
        end
        local comm=commmenu({menus=function() return g.Menus end,public_current=ctx.public_current,
            clock=ctx.clock,resolve_issued=ctx.resolve_issued,
            component_id=function(v)
                if type(v)=='cdata' and ffi.istype('uint64_t',v) then return v end
                return convert(v)
            end,cell_id=cell,widget_state=function(id)
                assert(valid(id)==true,'invalid native comm widget')
                local value=active(id);assert(type(value)=='boolean','native comm active state unavailable')
                return {active=value}
            end})
        ctx.on_dispose(comm.dispose)
        local context=actorcontext({menus=function() return g.Menus end,public_current=ctx.public_current,
            clock=ctx.clock,resolve_issued=ctx.resolve_issued,component_id=function(v)
                if type(v)=='cdata' and ffi.istype('uint64_t',v) then return v end
                return convert(v)
            end,mouse_position=g.GetLocalMousePosition,table_valid=valid})
        ctx.on_dispose(context.dispose)
        assert(type(g.OpenMenu)=='function' and type(ctx.Helper)=='table'
            and type(ctx.Helper.convertComponentIDs)=='function','native map route unavailable')
        local nav=navigation({menus=function() return g.Menus end,public_current=ctx.public_current,
            clock=ctx.clock,resolve_issued=ctx.resolve_issued,component_id=function(v)
                if type(v)=='cdata' and ffi.istype('uint64_t',v) then return v end
                return convert(v)
            end,valid_widget=valid,cell_id=cell,widget_state=function(id)
                assert(valid(id)==true,'invalid native map widget')
                local value=active(id);assert(type(value)=='boolean','native map active state unavailable')
                return {active=value}
            end,open_menu=g.OpenMenu,convert_parameters=ctx.Helper.convertComponentIDs})
        ctx.on_dispose(nav.dispose)
        local commapi=commprovider({adapter=comm,context_adapter=context,navigation_adapter=nav,revision='REVISION',public_current=ctx.public_current})
        ctx.on_dispose(commapi.dispose)
        ctx.registry.register(commapi.spec)
        local disposed,failed=false,false
        return {dispose=function()
            if failed then return false end
            if disposed then return true end
            disposed=true
            local ok1,v1=pcall(commapi.dispose);local ok2,v2=pcall(api.dispose)
            if not ok1 or v1~=true or not ok2 or v2~=true then failed=true;return false end
            return true
        end}
    end})
""".replace("MDREVISION",md["revision"]).replace("REVISION",revision)
    candidates["ui/live_conversation_extension.lua"]=source.encode()
    assert all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items()),"input drift during extension generation"
    assert Path(__file__).read_bytes()==own,"generator changed during extension generation"
    return candidates,dict(status="OFFLINE_EXTENSION_PROPOSAL_NOT_APPLIED",runtime_verified=False,
        provider="x4-conversation-observer-v1",revision=revision,md_revision=md["revision"],
        source_sha256={str(ROOT/PATHS[k]):sha(v) for k,v in raw.items()},generator_sha256=sha(own),
        output_sha256={p:sha(v) for p,v in candidates.items()},scope="SNAPSHOT_AND_BUILT_COMM_WIDGET_ONLY",
        providers={"x4-conversation-observer-v1":revision,"x4-comm-menu-v1":revision},
        integration="load after bundle, before first world bootstrap; no main or public session edits",
        comm_widget_route="SOURCE_SUPPORTED_OFFLINE_ONLY",actor_context_navigation="BUILT_LEFT_OR_RIGHT_INFO_ROWS_ONLY_OFFLINE",
        map_navigation="FIXED_OPEN_AND_BUILT_PANEL_OBJECTINFO_WRAPPERS_OFFLINE",
        radial_selection="BLOCKED",rendered_runtime="UNQUALIFIED")

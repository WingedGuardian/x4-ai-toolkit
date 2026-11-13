"""Pure optional native transporter extension. No menu opener or native action on build."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PATHS={"adapter":"mods/x4_toolkit_helper/ui/live_rooms.lua",
       "provider":"mods/x4_toolkit_helper/ui/live_rooms_provider.lua"}
def sha(b):return hashlib.sha256(b).hexdigest()
def prepare():
    own=Path(__file__).read_bytes();raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
    revision=sha(json.dumps(dict(source=pins,factory=sha(own.replace(b"\r\n",b"\n")),
        contract="ROOM_NATIVE_CAPTURE_PAGES_AND_EXACT_TRANSPORT_V1"),sort_keys=True,separators=(",",":")).encode())
    text="-- Own-code room extension; no engine-opened menu is fabricated.\n"
    for k,v in raw.items():text+="local "+k+"=(function()\n"+v.decode().replace("\r\n","\n")+"\nend)()\n"
    text+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'public bundle unavailable')
assert(type(bundle.register_extension)=='function','extension catalog unavailable')
bundle.register_extension({id='x4-rooms-extension',revision='REVISION',
    providers={['x4-rooms-v1']='REVISION'},attach=function(ctx)
        local g,ffi=ctx.globals,ctx.ffi
        assert(type(g)=='table' and ffi and ctx.C,'native room dependencies')
        local convert,luaid,data,clock=g.ConvertIDTo64Bit,g.ConvertStringToLuaID,g.GetComponentData,g.getElapsedTime
        assert(type(convert)=='function' and type(luaid)=='function' and type(data)=='function'
            and type(clock)=='function','native room functions')
        local native=adapter({ffi=ffi,C=ctx.C,current=ctx.public_current,now=clock,encode=ctx.encode,
            menus=function()return g.Menus end,
            convert_id=function(v)
                if type(v)=='cdata' and ffi.istype('uint64_t',v)then return v end
                return convert(v)
            end,
            component_data=function(room,key)
                assert(key=='islocked','closed room property')
                local id=luaid(tostring(room))
                local s=tostring(id)
                assert(id~=nil and type(id)~='boolean' and s~='0' and s~='0ULL' and s~='0LL',
                    'native room Lua ID unavailable')
                local back=convert(id)
                if type(back)=='number'then
                    assert(back==back and back>0 and back<=9007199254740991 and back==math.floor(back),
                        'inexact room roundtrip')
                    back=ffi.new('uint64_t',back)
                end
                assert(type(back)=='cdata' and ffi.istype('uint64_t',back) and back==room,
                    'room Lua ID roundtrip mismatch')
                return data(id,'islocked')
            end})
        ctx.on_dispose(native.dispose)
        local api=provider({adapter=native,revision='REVISION',public_current=ctx.public_current,encode=ctx.encode})
        ctx.on_dispose(api.dispose);ctx.registry.register(api.spec)
        return {dispose=api.dispose}
    end})
""".replace("REVISION",revision)
    out={"ui/live_rooms_extension.lua":text.encode()}
    assert Path(__file__).read_bytes()==own,"room factory changed"
    assert all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items()),"room source changed"
    return out,dict(status="OFFLINE_EXTENSION_PROPOSAL_NOT_APPLIED",revision=revision,
        provider="x4-rooms-v1",runtime_verified=False,menu_opening_supported=False,
        transport="NATIVE_MECHANISM_NOT_RENDERED_BUTTON",arrival_verified=False,
        source_sha256={PATHS[k]:sha(v) for k,v in raw.items()},generator_sha256=sha(own),
        output_sha256={p:sha(v) for p,v in out.items()},clock="getElapsedTime_UI_SECONDS")

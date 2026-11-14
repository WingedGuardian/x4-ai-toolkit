"""Pure optional built trader/encyclopedia extension. Returns own-code bytes only."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PATHS={"trader":"mods/x4_toolkit_helper/ui/live_tradermenu.lua",
       "text":"mods/x4_toolkit_helper/ui/live_encyclopedia.lua",
       "navigation":"mods/x4_toolkit_helper/ui/live_textnavigation.lua",
       "actor":"mods/x4_toolkit_helper/ui/live_actorentry.lua",
       "provider":"mods/x4_toolkit_helper/ui/live_tradertext_provider.lua"}

def sha(b):return hashlib.sha256(b).hexdigest()

def prepare():
    own=Path(__file__).read_bytes();raw={k:(ROOT/p).read_bytes() for k,p in PATHS.items()}
    pins={k:sha(v.replace(b"\r\n",b"\n")) for k,v in raw.items()}
    revision=sha(json.dumps(dict(source=pins,factory=sha(own.replace(b"\r\n",b"\n")),
        contract="BUILT_TRADER_TEXT_NAVIGATION_ACTOR_ENTRY_AND_IMMUTABLE_PAGES_V3"),sort_keys=True,separators=(",",":")).encode())
    source="-- Optional own-code built trader and text extension; no direct gameplay grants.\n"
    for k,v in raw.items():source+="local "+k+"=(function()\n"+v.decode().replace("\r\n","\n")+"\nend)()\n"
    source+="""
local bundle=assert(rawget(_G,'X4ToolkitAutomationBundle'),'public bundle unavailable')
assert(type(bundle.register_extension)=='function','extension catalog unavailable')
bundle.register_extension({id='x4-trader-text-extension',revision='REVISION',
    providers={['x4-trader-text-v1']='REVISION'},attach=function(ctx)
        local g,ffi,C=ctx.globals,ctx.ffi,ctx.C
        assert(type(g)=='table' and ffi and C,'native trader/text dependencies')
        assert(type(ctx.Helper)=='table' and type(ctx.Helper.convertComponentIDs)=='function','native menu conversion')
        local convert_parameters=ctx.Helper.convertComponentIDs
        if ctx.capacity_profile=='campaign_v1' then
            assert(type(ctx.capacity_resolver)=='function' and type(ctx.capacity_revision)=='string'
                and #ctx.capacity_revision==64 and not ctx.capacity_revision:find('[^0-9a-f]') and
                ctx.capacity_resolver(ctx.capacity_profile).revision==ctx.capacity_revision,'campaign profile mismatch')
        end
        local raw={}
        for _,k in ipairs({'ConvertIDTo64Bit','ConvertStringToLuaID','GetComponentData','GetPlayerMoney',
            'GetUIRelation','GetOwnLicences','HasLicence','GetWareData','getElapsedTime',
            'IsValidWidgetElement','GetCellContent','IsButtonActive','GetMacroData','OpenMenu'})do
            assert(type(g[k])=='function','missing native '..k);raw[k]=g[k]
        end
        if not pcall(ffi.typeof,'UniverseID')then ffi.cdef('typedef uint64_t UniverseID;')end
        assert(ffi.sizeof('UniverseID')==8 and ffi.istype('uint64_t',ffi.new('UniverseID')),'native ID ABI')
        ffi.cdef('UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);')
        ffi.cdef('bool IsComponentClass(UniverseID componentid,const char* classname); bool IsStoryFeatureUnlocked(const char* featureid);')
        -- Signature copied from vanilla menu_encyclopedia.lua:101; called as at menu_diplomacy.lua:1657.
        ffi.cdef('UniverseID GetFactionRepresentative(const char* factionid);')
        local context=C.GetContextByClass
        local isclass,unlocked,representative=C.IsComponentClass,C.IsStoryFeatureUnlocked,C.GetFactionRepresentative
        assert(type(context)=='function' or type(context)=='cdata','context native symbol unavailable')
        local function exact(v)
            if type(v)=='cdata' and ffi.istype('uint64_t',v)then assert(v~=0,'null component');return v end
            assert(type(v)=='number' and v==v and v>0 and v<=9007199254740991 and v==math.floor(v),'inexact native ID')
            return ffi.new('uint64_t',v)
        end
        local function component(v)
            if type(v)=='cdata' and ffi.istype('uint64_t',v)then return exact(v)end
            return exact(raw.ConvertIDTo64Bit(v))
        end
        local function lua_id(v)
            local n=component(v);local out=raw.ConvertStringToLuaID(tostring(n))
            local label=tostring(out)
            assert(out~=nil and type(out)~='boolean' and label~='0' and label~='0ULL' and label~='0LL','native Lua ID unavailable')
            assert(exact(raw.ConvertIDTo64Bit(out))==n,'native Lua ID roundtrip changed identity');return out
        end
        local globals={}
        for _,k in ipairs({'GetPlayerMoney','GetUIRelation','GetOwnLicences','HasLicence','GetWareData'})do globals[k]=raw[k]end
        globals.GetComponentData=function(v,key)
            assert(key=='owner','closed trader component property');return raw.GetComponentData(lua_id(v),key)
        end
        globals.GetContextByClass=function(v,class,include)
            assert(class=='container' and include==false,'closed trader context');return context(component(v),class,include)
        end
        local native=trader({ffi=ffi,C=C,globals=globals,current=ctx.public_current,now=raw.getElapsedTime,
            menus=function()return g.Menus end,resolve_issued=ctx.resolve_issued,component_id=component,
            valid_widget=raw.IsValidWidgetElement,cell_id=raw.GetCellContent,
            representative=function(faction)return representative(faction)end,
            convert_parameters=convert_parameters,open_menu=raw.OpenMenu,
            widget_state=function(id)
                assert(raw.IsValidWidgetElement(id)==true,'invalid trader widget')
                local active=raw.IsButtonActive(id);assert(type(active)=='boolean','native button state unavailable');return {active=active}
            end})
        ctx.on_dispose(native.dispose)
        local observer=text({menus=function()return g.Menus end,current=ctx.public_current,now=raw.getElapsedTime,
            valid_widget=raw.IsValidWidgetElement,cell_id=raw.GetCellContent,encode=ctx.encode})
        ctx.on_dispose(observer.dispose)
        local navigator=navigation({menus=function()return g.Menus end,current=ctx.public_current,now=raw.getElapsedTime,
            encode=ctx.encode,resolve_issued=ctx.resolve_issued,component_id=component,
            entry_for_component=function(token)
                local value=component(ctx.resolve_issued(token,'object'))
                local macro,ismodule=raw.GetComponentData(lua_id(value),'macro','ismodule')
                assert(type(ismodule)=='boolean' and (ismodule or isclass(value,'ship')==true),'ship/module entry required')
                assert(type(macro)=='string' and #macro>0,'component macro unavailable')
                return {component=value,mode=ismodule and 'Stations' or 'Ships',
                    library=raw.GetMacroData(macro,'infolibrary'),id=macro}
            end,
            unlocked=function(name)return unlocked(name=='MapMenu' and 'x4ep1_map' or 'x4ep1_encyclopedia')end,
            open_menu=raw.OpenMenu,convert_parameters=convert_parameters,valid_widget=raw.IsValidWidgetElement,
            cell_id=raw.GetCellContent,widget_state=function(id)return {active=raw.IsButtonActive(id)}end,
            capacity_profile=ctx.capacity_profile,capacity_resolver=ctx.capacity_resolver})
        ctx.on_dispose(navigator.dispose)
        local actorentry=actor({ffi=ffi,C=C,globals=g,current=ctx.public_current,now=raw.getElapsedTime,
            encode=ctx.encode,component_id=component,actor_info=function(v)
                local name,typestring,rolename,owner=raw.GetComponentData(lua_id(v),'name','typestring','rolename','owner')
                return {name=name,typestring=typestring,rolename=rolename,owner=owner}
            end,capacity_profile=ctx.capacity_profile,capacity_resolver=ctx.capacity_resolver})
        ctx.on_dispose(actorentry.dispose)
        local api=provider({trader=native,text=observer,navigation=navigator,actor=actorentry,current=ctx.public_current,now=raw.getElapsedTime,
            encode=ctx.encode,revision='REVISION',capacity_profile=ctx.capacity_profile,capacity_resolver=ctx.capacity_resolver})
        ctx.on_dispose(api.dispose);ctx.registry.register(api.spec)
        return {dispose=api.dispose}
    end})
""".replace("REVISION",revision)
    out={"ui/live_tradertext_extension.lua":source.encode()}
    if not (Path(__file__).read_bytes()==own): raise ValueError("trader/text factory changed")
    if not (all((ROOT/PATHS[k]).read_bytes()==v for k,v in raw.items())): raise ValueError("trader/text source changed")
    return out,dict(status="OFFLINE_EXTENSION_PROPOSAL_NOT_APPLIED",revision=revision,
        provider="x4-trader-text-v1",runtime_verified=False,menu_opening_supported=True,
        menu_opening_scope="FIXED_NATIVE_ROUTES_NOT_RUNTIME_QUALIFIED",
        purchase_verified=False,rendered_pixels_verified=False,
        source_sha256={PATHS[k]:sha(v) for k,v in raw.items()},generator_sha256=sha(own),
        output_sha256={p:sha(v) for p,v in out.items()},clock="getElapsedTime_UI_SECONDS")

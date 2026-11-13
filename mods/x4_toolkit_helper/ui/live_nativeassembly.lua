-- Assemble only bundled own-code adapters around documented native dependencies.
-- No constructor-time native calls. FFI declarations/layout checks are not runtime ABI proof.
local READS={menu_capture=true,menu_page=true,orders=true,transactions=true,
    state_capture=true,state_page=true,buildtasks_capture=true,buildtasks_page=true,buildtasks_task=true,
    dialog_probe=true,native_save_path=true,logbook_window=true,camera_state=true,
    people_capture=true,people_page=true,people_metadata=true,equipment_mods=true,
    ticker_window=true,options_view=true,player_navigation=true,time_state=true,native_adapter_capacity=true}
local function vocabulary()
    local out={};for k in pairs(READS) do out[k]=true end;return out
end
local function new(d)
    assert(type(d)=="table" and type(d.modules)=="table","fixed bundle modules required")
    local m=d.modules
    for _,name in ipairs({"orders","camera","nativeproviders","menupages","playerstate","buildtasks","dialogprobe","paths","logbook","camerastate","people","equipmentmods","ticker","options","playernavigation","timecontrol"}) do
        assert(type(m[name])=="function","missing module factory: "..name)
    end
    assert(type(m.menu)=="table" and type(m.menu.new)=="function"
        and type(m.menu.native_reader)=="function","native menu module required")
    assert(type(m.transactions)=="table" and type(m.transactions.new)=="function","native transaction module required")
    assert(type(d.build)=="string" and #d.build==64 and not d.build:find("[^0-9a-f]"),"bundle identity required")
    for _,name in ipairs({"public_current","resolve_issued","clock","encode"}) do
        assert(type(d[name])=="function","missing assembly dependency: "..name)
    end
    local ffi,C,g,H=d.ffi,d.C,d.globals,d.Helper
    assert(ffi and C and type(g)=="table" and type(H)=="table","native bindings required")
    local globals={}
    for _,name in ipairs({"GetCellContent","OpenMenu","ConvertIDTo64Bit","IsValidWidgetElement",
        "IsButtonActive","IsValidComponent","GetComponentData","ConvertStringToLuaID","SetOrderParam",
        "GetOrderParams","IsDockingPossible","GetPlayerMoney","GetOwnLicences","HasLicence",
        "ConvertStringTo64Bit","GetActiveGuidanceMissionComponent","GetAutoPilotTarget","GetPlayerActivity","getElapsedTime"}) do
        assert(type(g[name])=="function","missing native global: "..name);globals[name]=g[name]
    end
    assert(type(H.convertComponentIDs)=="function","native Helper conversion required")
    local convert=H.convertComponentIDs
    local current,resolve,clock,encode,build=d.public_current,d.resolve_issued,d.clock,d.encode,d.build
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.sizeof("UniverseID")==8 and ffi.istype("uint64_t",ffi.new("UniverseID")),"native ID alias mismatch")
    -- Bare globals consume engine Lua IDs/numbers, not raw FFI UniverseID.
    -- Native map5168/5564 (component data),26779 (order params),3521 (set),
    -- 21260/21309 ->22317 (dock); ship_configuration9987 (validity).
    -- Preserve exact identities through native conversion, never tonumber(uint64).
    local raw={};for k,v in pairs(globals) do raw[k]=v end
    local function exact64(value)
        if type(value)=="cdata" and ffi.istype("uint64_t",value) then return value end
        assert(type(value)=="number" and value==value and value>=0 and value<9007199254740992
            and value%1==0,"bare native ID is not an exact safe integer")
        return ffi.new("uint64_t",value)
    end
    globals.ConvertIDTo64Bit=function(value)
        if type(value)=="cdata" and ffi.istype("uint64_t",value) then return value end
        return exact64(raw.ConvertIDTo64Bit(value))
    end
    local function lua_id(value)
        assert(type(value)=="cdata" and ffi.istype("uint64_t",value) and value~=0,"exact native component required")
        local converted=raw.ConvertStringToLuaID(tostring(value))
        assert(converted~=nil and type(converted)~="boolean","native Lua ID unavailable")
        assert(exact64(raw.ConvertIDTo64Bit(converted))==value,"native Lua ID conversion changed identity")
        return converted
    end
    for _,name in ipairs({"GetComponentData","GetOrderParams","SetOrderParam"}) do
        local fn=raw[name]
        globals[name]=function(component,...) return fn(lua_id(component),...) end
    end
    globals.IsDockingPossible=function(ship,target,...)
        return raw.IsDockingPossible(lua_id(ship),lua_id(target),...)
    end
    globals.IsValidComponent=function(component)
        assert(type(component)=="cdata" and ffi.istype("uint64_t",component),"exact validity subject required")
        if component==0 then return false end
        local converted=raw.ConvertStringTo64Bit(tostring(component))
        assert(exact64(converted)==component,"native validity conversion changed identity")
        return raw.IsValidComponent(converted)
    end
    -- Native menu_map.lua594/777/779/853/865/978-983; menu_followcamera.lua8.
    -- Declarations/available symbols are source evidence, not parameter-ABI attestation.
    ffi.cdef[[
        UniverseID GetPlayerID(void);
        UniverseID GetPlayerControlledShipID(void);
        UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);
        bool IsExternalViewDisabled(void);
        bool IsPlayerCameraTargetViewPossible(UniverseID targetid,bool force);
        bool SetSofttarget(UniverseID componentid,const char*const connectionname);
        void SetPlayerCameraTargetView(UniverseID targetid,bool force);
        UniverseID GetExternalViewRefObject(void);
        void SetPlayerCameraCockpitView(bool force);
    ]]
    local disposed,dispose_failure=false,nil
    local function live() assert(not disposed,"native assembly disposed") end
    local function epoch()
        live();local s=current()
        assert(type(s)=="table" and type(s.session)=="string" and type(s.generation)=="string",
            "public lifecycle unavailable")
        return s.session..":"..s.generation
    end
    local widget_state,set_dropdown=m.menu.native_reader(ffi,C,globals)
    local options=m.options{
        menus=function() live();assert(type(g.Menus)=="table","native Menus unavailable");return g.Menus end,
        current=current,now=clock,valid_widget=globals.IsValidWidgetElement,
        cell_id=globals.GetCellContent,widget_state=widget_state,
        set_dropdown_current=set_dropdown,open_menu=globals.OpenMenu}
    local menu=m.menu.new{
        menus=function() live();assert(type(g.Menus)=="table","native Menus unavailable");return g.Menus end,
        cell_id=globals.GetCellContent,epoch=epoch,now=clock,resolve_issued=resolve,
        convert_id=globals.ConvertIDTo64Bit,open_menu=globals.OpenMenu,
        convert_params=function(params) return convert(params) end,
        -- Native menu_map.lua13233/13266 gates this read-only ship view on
        -- GetComponentData(...,'isplayerowned'). Preserve exact Lua-ID conversion.
        is_player_owned=function(component)
            live()
            local owned=globals.GetComponentData(component,'isplayerowned')
            assert(type(owned)=='boolean','native ownership unavailable')
            return owned
        end,
        widget_state=widget_state,set_dropdown_current=set_dropdown}
    local orders=m.orders{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,epoch=epoch,now=clock}
    local transactions=m.transactions.new{ffi=ffi,C=C,
        resolve_issued_station=function(token) return resolve(token,"station") end,clock=clock}
    local state=m.playerstate{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,current=current,
        now=clock,encode=encode}
    local tasks=m.buildtasks{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,current=current,
        now=clock,encode=encode}
    local dialog=m.dialogprobe{ffi=ffi,C=C,globals=g,public_current=current,clock=clock}
    local paths=m.paths{ffi=ffi,C=C,current=current}
    local logbook=m.logbook{globals=g,current=current,now=clock,encode=encode}
    local ticker=m.ticker{ffi=ffi,C=C,current=current,now=globals.getElapsedTime,encode=encode}
    local navigation=m.playernavigation{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,current=current,now=globals.getElapsedTime}
    local timecontrol=m.timecontrol{ffi=ffi,C=C,activity=globals.GetPlayerActivity,
        resolve_issued=resolve,current=current,now=globals.getElapsedTime}
    local camerastate=m.camerastate{ffi=ffi,C=C,current=current,now=globals.getElapsedTime}
    local people=m.people{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,current=current,now=clock,encode=encode}
    local equipmentmods=m.equipmentmods{ffi=ffi,C=C,globals=globals,resolve_issued=resolve,current=current,now=clock,encode=encode}
    -- Probe all required symbols before exposing a provider, without invoking any.
    -- callable shape/nonnull does NOT reveal or prove parameter arity.
    local symbols={}
    for _,name in ipairs({"GetPlayerID","GetPlayerControlledShipID","GetContextByClass",
        "IsExternalViewDisabled","IsPlayerCameraTargetViewPossible","SetSofttarget",
        "SetPlayerCameraTargetView","GetExternalViewRefObject","SetPlayerCameraCockpitView",
        "GetNumDropDownOptions","GetDropDownOptions3","IsDropDownActive","SetDropDownCurOption",
        "GetNumOrders","GetOrders3","GetDefaultOrder","GetOrderID","GetOrderQueueCurrentIdx",
        "CreateOrder3","EnableOrder","RemoveOrder","IsOrderSelectableFor","IsComponentClass",
        "IsComponentOperational","GetPlayerOccupiedShipID","GetNumBuildTasks","GetBuildTasks",
        "GetNumBlueprints","GetBlueprints","GetBuildTaskInfo"}) do
        local fn=C[name]
        assert(type(fn)=="function" or (type(fn)=="cdata" and tostring(ffi.typeof(fn)):find("%(")
            and ffi.cast("void *",fn)~=ffi.NULL),"native callable unavailable: "..name)
        symbols[name]=fn
    end
    local camera_native={}
    for _,name in ipairs({"GetPlayerID","GetPlayerControlledShipID","GetContextByClass",
        "IsExternalViewDisabled","IsPlayerCameraTargetViewPossible","SetSofttarget",
        "SetPlayerCameraTargetView","GetExternalViewRefObject","SetPlayerCameraCockpitView"}) do
        local fn=symbols[name];camera_native[name]=function(...) return fn(...) end
    end
    local camera=m.camera{native=camera_native,
        resolve_issued=function(token) return resolve(token,"object") end,
        is_valid=globals.IsValidComponent,epoch=epoch,now=clock}
    local provider=m.nativeproviders{revision=build,public_current=current,resolve_issued=resolve,
        capacity_profile=d.capacity_profile,capacity_resolver=d.capacity_resolver,
        now=clock,menu=menu,orders=orders,camera=camera,transactions=transactions,options=options,navigation=navigation,timecontrol=timecontrol}
    local pages=m.menupages{observe=function() return provider.observe("menu") end}
    local counter_reads={}
    for name,adapter in pairs({menu=menu,options=options,orders=orders,time=timecontrol,navigation=navigation,
        menu_captures=pages,state_captures=state,buildtask_captures=tasks,people_captures=people,equipment_mods=equipmentmods})do
        assert(type(adapter.capacity)=='function','missing local counter getter: '..name)
        counter_reads[name]=adapter.capacity -- immutable getter identity; no constructor call
    end
    local budget_names={menu={tickets=true},options={tickets=true},orders={submissions=true,cancellations=true},
        time={tickets=true},navigation={tickets=true},menu_captures={attempts=true},state_captures={attempts=true},
        buildtask_captures={attempts=true},people_captures={attempts=true},equipment_mods={reads=true}}
    local function adapter_capacity()
        local values={}
        for name,get in pairs(counter_reads)do
            local value=get()
            assert(type(value)=='table' and getmetatable(value)==nil and type(value.available)=='boolean'
                and type(value.budgets)=='table' and getmetatable(value.budgets)==nil,'invalid local counter result')
            for key in pairs(budget_names[name])do assert(value.budgets[key]~=nil,'missing local counter budget')end
            for key,b in pairs(value.budgets)do
                assert(budget_names[name][key],'unexpected local counter budget')
                assert(type(b)=='table' and type(b.limit)=='number' and b.limit>0 and b.limit<=65536
                    and b.limit%1==0,'invalid local counter limit')
                assert((not value.available and b.used=='UNKNOWN') or (type(b.used)=='number'
                    and b.used>=0 and b.used<=b.limit and b.used%1==0),'invalid local counter usage')
            end
            values[name]=value
        end
        return {scope='LOCAL_ADAPTER_INSTANCE_COUNTERS_NOT_NATIVE_WORLD_CENSUS',adapters=values,adapter_count=10,budget_count=11,
            cached_contexts='HISTORICAL_NOT_CURRENT_READBACK',atomic=false,reserved=false,future_fit_verified=false}
    end
    local api={read_verbs=vocabulary(),provider_revisions={["x4-native-v1"]=build},
        abi_status="SOURCE_DECLARED_PARAMETER_ABI_RUNTIME_UNVERIFIED"}
    local function args(values,n)
        assert(type(values)=="table" and getmetatable(values)==nil and #values==n,"exact read arguments required")
        local found=0
        for k,v in pairs(values) do
            assert(type(k)=="number" and k%1==0 and k>=1 and k<=n and type(v)=="string"
                and #v>0 and #v<=128 and not v:find("[%c]"),"invalid scalar read argument")
            found=found+1
        end
        assert(found==n,"sparse read arguments")
    end
    local function nonce(s) assert(#s==32 and not s:find("[^0-9a-f]"),"capture nonce required");return s end
    local function offset(s)
        assert(s=="0" or s:match("^[1-9]%d*$"),"canonical nonnegative page offset required")
        local n=tonumber(s);assert(n and n<=1048576,"page offset overflow");return n
    end
    local function read(verb,a)
        live();assert(READS[verb],"closed native read vocabulary")
        assert(provider.session(),"public session unavailable")
        local result
        if verb=="native_adapter_capacity" then args(a,0);result=adapter_capacity()
        elseif verb=="menu_capture" then args(a,1);result=pages.capture(nonce(a[1]))
        elseif verb=="menu_page" then args(a,2);result=pages.page(nonce(a[1]),offset(a[2]))
        elseif verb=="orders" then args(a,1);result=provider.observe("orders",a[1])
        elseif verb=="transactions" then args(a,1);result=provider.observe("transactions",a[1])
        elseif verb=="state_capture" then args(a,2);result=state.capture(a[1],nonce(a[2]))
        elseif verb=="state_page" then
            args(a,3);assert(a[2]=="licences" or a[2]=="blueprints","closed state page kind")
            result=state.page(a[1],a[2],offset(a[3]))
        elseif verb=="buildtasks_capture" then args(a,2);result=tasks.capture(a[1],nonce(a[2]))
        elseif verb=="buildtasks_page" then args(a,2);result=tasks.page(a[1],offset(a[2]))
        elseif verb=="dialog_probe" then args(a,0);result=dialog.capture()
        elseif verb=="native_save_path" then args(a,0);result=paths.read()
        elseif verb=="logbook_window" then args(a,2);result=logbook.read(offset(a[1]),offset(a[2]))
        elseif verb=="ticker_window" then args(a,2);result=ticker.read(offset(a[1]),offset(a[2]))
        elseif verb=="options_view" then args(a,1);result=options.observe(a[1])
        elseif verb=="time_state" then args(a,1);result=timecontrol.observe(a[1])
        elseif verb=="player_navigation" then args(a,2);result=navigation.observe(a[1],a[2])
        elseif verb=="camera_state" then args(a,0);result=camerastate.read()
        elseif verb=="people_capture" then args(a,2);result=people.capture(a[1],nonce(a[2]))
        elseif verb=="people_page" then args(a,2);result=people.page(a[1],offset(a[2]))
        elseif verb=="people_metadata" then args(a,1);result=people.metadata(a[1])
        elseif verb=="equipment_mods" then
            args(a,(a[2]=="ship" or a[2]=="engine") and 2 or 3)
            result=equipmentmods.read(a[1],a[2],a[3])
        else args(a,2);result=tasks.task(a[1],a[2]) end
        assert(provider.session(),"public session changed during read")
        local payload=encode(result)
        assert(type(payload)=="string" and #payload<=32000,"native read exceeds wire limit; use bounded capture/page")
        return payload
    end
    function api.read(verb,a)
        local ok,value=pcall(read,verb,a)
        if not ok then return "ERR",tostring(value):sub(1,512) end
        return "OK",value -- transport only: payload retains REFUSED/PARTIAL/UNKNOWN.
    end
    function api.dispose()
        if dispose_failure then error(dispose_failure) end
        if disposed then return true end
        disposed=true
        local first
        for _,item in ipairs({provider,state,tasks,dialog,paths,logbook,camerastate,people,equipmentmods,ticker,options,navigation,timecontrol}) do
            local ok,result=pcall(item.dispose)
            if not ok or result==false then first=first or tostring(result) end
        end
        if first then dispose_failure="native assembly retirement failed: "..first;error(dispose_failure) end
        return true
    end
    local spec=provider.spec
    function api.order_binding(request_id) live();return provider.order_binding(request_id) end
    function api.capacity() live();return provider.capacity() end
    api.provider_spec={id=spec.id,revision=spec.revision,actions=spec.actions,
        session=spec.session,invoke=spec.invoke,dispose=api.dispose}
    return api
end
return {read_verbs=vocabulary,new=new}

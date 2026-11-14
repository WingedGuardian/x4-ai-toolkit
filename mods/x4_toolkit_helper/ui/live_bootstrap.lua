-- Fixed public bootstrap. Bundle generation injects these own-code factories;
-- there is no dynamic module name or arbitrary native/script dispatch.
-- Native wrapper evidence is listed in its live-bootstrap-qualification development record (in the toolkit's git repository, not in release downloads).
local singleton
return function(deps)
    local function hex(v,n)
        return type(v)=="string" and #v==n and not v:find("[^0-9a-f]")
    end
    assert(type(deps)=="table" and hex(deps.revision,64) and hex(deps.build,64),"bound source identities required")
    assert(type(deps.modules)=="table","bundle module factories required")
    for _,name in ipairs({"session","actions","wire","automation"}) do
        assert(type(deps.modules[name])=="function","missing module factory: "..name)
    end
    assert(type(deps.read)=="function" and type(deps.read_verbs)=="table","closed read interface required")
    local bundle_build, read = deps.build, deps.read
    local ffi=deps.ffi or require("ffi")
    local C=deps.C or ffi.C
    local native=deps.native or {
        RegisterEvent=RegisterEvent, GetNPCBlackboard=GetNPCBlackboard,
        SetNPCBlackboard=SetNPCBlackboard, AddUITriggeredEvent=AddUITriggeredEvent,
        ConvertStringTo64Bit=ConvertStringTo64Bit, getElapsedTime=getElapsedTime}
    for _,name in ipairs({"RegisterEvent","GetNPCBlackboard","SetNPCBlackboard",
            "AddUITriggeredEvent","ConvertStringTo64Bit","getElapsedTime"}) do
        assert(type(native[name])=="function","missing native global: "..name)
    end
    -- ModSupportAPI interface.lua41-42 and native Helper331/371. Existing aliases
    -- and symbol return types must agree; a failed declaration is never ignored.
    -- LuaJIT does not expose parameter lists through these supported inspectors.
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.sizeof("UniverseID")==8 and ffi.istype("uint64_t",ffi.new("UniverseID")),"incompatible UniverseID")
    ffi.cdef[[UniverseID GetPlayerID(void); double GetCurrentGameTime(void);]]
    local function function_type(fn,pointer,func)
        assert(type(fn)=="cdata","native function signature unavailable")
        -- LuaJIT independently parsed function ctypes need not be identity-equal;
        -- istype applies permissive pointer compatibility. Compare its canonical
        -- return-type rendering instead. It omits parameter lists: this is NOT
        -- parameter-ABI attestation. No native call is made to guess a signature.
        local actual=tostring(ffi.typeof(fn))
        assert((actual==tostring(ffi.typeof(pointer)) or actual==tostring(ffi.typeof(func))) and
            ffi.cast("void *",fn)~=ffi.NULL,"native function signature unavailable")
        return fn
    end
    local get_player=function_type(C.GetPlayerID,"uint64_t (*)(void)","uint64_t (void)")
    local get_age=function_type(C.GetCurrentGameTime,"double (*)(void)","double (void)")
    local allow={}
    for id,revision in pairs(deps.provider_revisions or {}) do
        assert(type(id)=="string" and #id>0 and #id<=128 and not id:find("[^%w_.:-]") and hex(revision,64),
            "invalid allowed provider source")
        allow[id]=revision
    end
    local reads={}
    for verb,enabled in pairs(deps.read_verbs) do
        assert(type(verb)=="string" and enabled==true,"invalid read vocabulary")
        reads[verb]=true
    end
    -- Validate all dependencies first; replacement then retires the old owner
    -- before subscribing any new callback. Failed disposal must stop replacement.
    if singleton then singleton.dispose() end
    local api, disposed, cleanup, registered = {}, false, {}, {}
    function api.abi_status()
        return {status="SOURCE_DECLARED_PARAMETER_ABI_RUNTIME_UNVERIFIED",parameter_abi_attested=false}
    end
    local dispose_failure
    local session, registry, router
    local tickets, ticket_count, bound_generation = {}, 0, nil
    local active_proof
    local queue_player
    local function live() assert(not disposed,"bootstrap disposed") end
    function api.capacity()
        live()
        assert(registry and router,'capacity observers unavailable')
        return {sdk=registry.capacity(),router=router.capacity(),
            scope='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'}
    end
    local function player()
        live()
        local raw=get_player()
        assert(type(raw)=="cdata" and ffi.istype("uint64_t",raw),"player ID is not native uint64")
        local text=tostring(raw)
        assert(text:match("^[1-9]%d*ULL$"),"player is unavailable")
        local converted=native.ConvertStringTo64Bit(text)
        local exact=converted
        if type(exact)=="number" then
            assert(exact==exact and exact>0 and exact<9007199254740992 and exact%1==0,
                "player conversion is not an exact safe integer")
            exact=ffi.new("uint64_t",exact)
        end
        assert(type(exact)=="cdata" and ffi.istype("uint64_t",exact) and exact==raw,
            "player conversion unavailable or changed identity")
        return converted,text
    end
    local function read_blackboard(name)
        local id,before=player()
        local value=native.GetNPCBlackboard(id,name)
        local _,after=player()
        assert(before==after,"player changed during blackboard read")
        return value,before,id
    end
    function api.ui_state(challenge)
        live();assert(hex(challenge,32),"UI probe challenge required")
        assert(tickets[challenge]==nil and ticket_count<2048,"UI probe reused or capacity exhausted")
        ticket_count=ticket_count+1
        tickets[challenge]={observed=bound_generation or ""}
        active_proof=challenge
        return {kind="UI_STATE",challenge=challenge,generation=bound_generation or "",build=bundle_build}
    end
    function api.ui_authorize(proof,generation)
        live();assert(hex(proof,32) and hex(generation,32),"UI proof required")
        local ticket=tickets[proof]
        assert(ticket,"UI probe ticket is not from this instance")
        assert(proof==active_proof,"UI probe ticket was superseded")
        if ticket.authorized then
            assert(ticket.authorized==generation and bound_generation==generation,"UI ticket rebound")
        elseif ticket.observed~="" then
            assert(ticket.observed==generation and bound_generation==generation,"UI generation changed")
            ticket.authorized=generation
        else
            assert(bound_generation==nil,"unbound UI probe became stale")
            bound_generation=generation;ticket.authorized=generation
        end
        return true
    end
    function api.dispose()
        assert(dispose_failure==nil,dispose_failure)
        if disposed then return true end
        disposed=true;tickets={};active_proof=nil
        local errors={}
        if session then
            local ok,why=pcall(session.dispose)
            if not ok or why==false then errors[#errors+1]="public session: "..tostring(why) end
        end
        for id,fn in pairs(cleanup) do
            local ok,why=pcall(fn)
            if not ok or why==false then errors[#errors+1]=id..": "..tostring(why) end
        end
        if #errors>0 then
            dispose_failure="provider disposal failed: "..table.concat(errors,"; ")
            error(dispose_failure)
        end
        if singleton==api then singleton=nil end
        return true
    end
    local ok,why=pcall(function()
        session=deps.modules.session{
            revision=deps.revision,
            now=function() live();return native.getElapsedTime() end,
            send=function(request)
                live();assert(bound_generation==request.generation,"UI ticket must authorize generation first")
                native.AddUITriggeredEvent("X4ToolkitSession","handshake",request)
            end,
            subscribe=function(on_reply,on_invalidated)
                native.RegisterEvent("X4ToolkitSession.Replies",function()
                    if not disposed then on_reply() end
                end)
                native.RegisterEvent("X4ToolkitSession.Invalidated",function()
                    if not disposed then on_invalidated() end
                end)
            end,
            read=function()
                local value,text,id=read_blackboard("$x4_toolkit_session_replies")
                queue_player={text=text,id=id};return value
            end,
            clear=function()
                assert(queue_player,"no owned queue read")
                local _,text=player()
                assert(text==queue_player.text,"player changed before queue clear")
                local id=queue_player.id;queue_player=nil
                return native.SetNPCBlackboard(id,"$x4_toolkit_session_replies",nil)
            end,
            read_state=function() return read_blackboard("$x4_toolkit_session_state") end}
        registry=deps.modules.actions(deps.capacity_profile,deps.capacity_resolver)
        function api.register_provider(spec)
            live()
            assert(type(spec)=="table" and allow[spec.id] and allow[spec.id]==spec.revision,
                "provider revision is not explicitly allowed")
            assert(not registered[spec.id],"provider already registered")
            assert(type(spec.session)=="function" and type(spec.invoke)=="function","provider callbacks required")
            assert(spec.dispose==nil or type(spec.dispose)=="function","invalid provider disposer")
            local current,invoke=spec.session,spec.invoke
            local id=spec.id
            registry.register{id=id,revision=spec.revision,actions=spec.actions,
                session=function()
                    live();local stamp=session.current()
                    if not stamp then return nil end
                    local value=current()
                    if value~=stamp.session then return nil end
                    return value
                end,
                invoke=function(phase,request,done)
                    live();local stamp=session.current()
                    assert(stamp and stamp.session==request.session and current()==stamp.session,
                        "provider public session unavailable")
                    return invoke(phase,request,done)
                end}
            registered[id]=spec.revision
            if spec.dispose then cleanup[id]=spec.dispose end
        end
        function api.register_cleanup(id,fn)
            live();assert(registered[id] and cleanup[id]==nil and type(fn)=="function","invalid cleanup registration")
            cleanup[id]=fn
        end
        api.registry={register=api.register_provider,prepare=function(...)
            live();return registry.prepare(...) end,
            execute=function(...) live();return registry.execute(...) end,
            status=function(...) live();return registry.status(...) end}
        api.session=session
        local wire=deps.modules.wire()
        api.encode=wire.encode
        router=deps.modules.automation{session=session,registry=api.registry,wire=wire,
            capacity_profile=deps.capacity_profile,capacity_resolver=deps.capacity_resolver,
            read=function(...) live();return read(...) end,read_verbs=reads,
            age=function() live();return get_age() end,build=bundle_build,
            ui_authorize=api.ui_authorize,ui_state=api.ui_state}
        api.router=router
    end)
    if not ok then api.dispose();error("bootstrap unavailable: "..tostring(why)) end
    singleton=api
    return api
end

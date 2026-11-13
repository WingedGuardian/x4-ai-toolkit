-- SDK bridge to the actual rendered MapMenu comm button. Opening remains
-- unverified until a separate actor/conversation observation; no selection API.
return function(d)
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    assert(type(d)=="table" and hex(d.revision,64) and type(d.public_current)=="function"
        and type(d.adapter)=="table" and type(d.context_adapter)=="table" and type(d.navigation_adapter)=="table","comm provider dependencies required")
    local revision,public=d.revision,d.public_current
    local comm_adapter,context_adapter,navigation_adapter=d.adapter,d.context_adapter,d.navigation_adapter
    for _,adapter in ipairs({comm_adapter,context_adapter,navigation_adapter}) do
        for _,k in ipairs({"observe","prepare","execute","dispose"}) do assert(type(adapter[k])=="function","missing comm adapter method") end
    end
    local api,records,count,stamp,retired={}, {},0,nil,false
    local navigation_actions={map_inspect=true,map_open=true,map_panel=true,map_objectinfo=true}
    local function identity()
        assert(not retired,"comm provider retired")
        local s=public();assert(type(s)=="table" and s.provider=="x4-live-session-v1" and hex(s.session,32)
            and hex(s.generation,32) and hex(s.revision,64) and type(s.world_token)=="string" and #s.world_token>0,"public session unavailable")
        local nextstamp={}
        for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
            if stamp then assert(s[k]==stamp[k],"comm public identity changed") end;nextstamp[k]=s[k]
        end
        stamp=nextstamp;return s.session
    end
    local function result(state,reason,native,attempted)
        if type(attempted)~="boolean" then attempted="UNKNOWN" end
        return {status=state,reason=reason,observations={scope="BUILT_MAP_COMM_WIDGET_ONLY",native=native or {},
            wrapper_attempted=attempted,conversation_open_verified=false,conversation_actor_verified=false,
            rendered_dialogue_verified=false,purchase_verified=false}}
    end
    local function invoke(phase,req,done)
        assert(type(done)=="function","SDK callback required")
        local r=type(req)=="table" and records[req.id] or nil
        local context=type(req)=="table" and (req.action=="context_inspect" or req.action=="context_open")
        local navigation=type(req)=="table" and navigation_actions[req.action]
        local opening=type(req)=="table" and (req.action=="open" or req.action=="context_open" or navigation and req.action~="map_inspect")
        local adapter=navigation and navigation_adapter or context and context_adapter or comm_adapter
        local function answer(...)
            local out=result(...)
            if navigation then
                out.observations.scope="FIXED_MAP_INFO_NAVIGATION_ONLY"
                out.observations.map_handler_attempted=out.observations.wrapper_attempted
                out.observations.wrapper_attempted=false
                out.observations.info_open_verified=false
            elseif context then
                out.observations.scope="BUILT_MAP_ACTOR_CONTEXT_NAVIGATION_ONLY"
                out.observations.context_handler_attempted=out.observations.wrapper_attempted
                out.observations.wrapper_attempted=false
                out.observations.context_open_verified=false
            end
            return out
        end
        local ok,out=pcall(function()
            assert(type(req)=="table" and req.provider=="x4-comm-menu-v1"
                and (req.action=="inspect" or req.action=="open" or req.action=="context_inspect" or req.action=="context_open" or navigation)
                and req.binding==revision and hex(req.id,32) and hex(req.digest,64) and req.session==identity(),"unbound comm request")
            local a=req.arguments
            assert(type(a)=="table" and getmetatable(a)==nil,"plain comm arguments")
            local n=0
            for k,v in pairs(a) do
                local allowed=navigation and (k=="context" or k=="side") or context and k=="side"
                    or opening and (k=="context" or k==(context and "role" or "kind") or k=="actor")
                assert(allowed and type(v)=="string"
                    and #v>0 and #v<=128,"closed comm arguments");n=n+1
            end
            if navigation then
                assert(n==2 and a.context and (a.side=="left" or a.side=="right"),"explicit map context/pane")
                assert(req.action~="map_open" or a.side=="left","native map open uses left pane")
            else
                assert(not opening and n==(a.side and 1 or 0) or opening and n==(a.side and 4 or 3),"comm argument count")
                assert(a.side==nil or context and (a.side=="left" or a.side=="right"),"explicit actor pane")
            end
            if r then
                assert(r.digest==req.digest and r.session==req.session and r.action==req.action,"changed comm request")
                for k,v in pairs(r.args) do assert(a[k]==v,"changed prepared comm argument") end
            end
            if phase=="prepare" then
                assert(not r and count<128,"comm preparation duplicate/capacity")
                local args={};for k,v in pairs(a) do args[k]=v end
                r={digest=req.digest,session=req.session,action=req.action,args=args,dispatched=false,attempted=false}
                records[req.id]=r;count=count+1
                if opening then
                    if navigation then r.prepared=adapter.prepare(a.context,a.side,req.action:sub(5))
                    elseif context then r.prepared=adapter.prepare(a.context,a.role,a.actor,a.side)
                    else r.prepared=adapter.prepare(a.context,a.kind,a.actor) end
                    identity()
                    assert(type(r.prepared)=="table","malformed comm preparation")
                    if r.prepared.status~="PREPARED" then return answer("refused","native menu preparation refused",r.prepared,false) end
                    assert(r.prepared.ticket~=nil,"missing private comm ticket")
                end
                r.ready=true
                return answer("prepared","fixed menu operation ready; no handler called",r.prepared,false)
            end
            assert(r and r.ready,"no private comm preparation")
            if phase=="execute" then
                assert(not r.dispatched,"comm operation already consumed");r.dispatched=true
                if not opening then
                    if navigation then r.native=adapter.observe(a.context,a.side)
                    elseif context then r.native=adapter.observe(a.side)
                    else r.native=adapter.observe() end
                else
                    r.attempted="UNKNOWN"
                    r.native=adapter.execute(r.prepared.ticket)
                    if type(r.native)=="table" and type(r.native.action_attempted)=="boolean" then r.attempted=r.native.action_attempted end
                end
                identity()
            else assert(phase=="status","unknown comm phase") end
            if not r.dispatched then return answer("prepared","private comm preparation retained",r.prepared,false) end
            assert(type(r.native)=="table" and type(r.native.status)=="string","comm outcome unavailable")
            if not opening then
                return answer(r.native.status=="OBSERVED" and "completed" or "unknown","built menu observation only",r.native,false)
            end
            local state=r.native.status=="REFUSED" and r.native.action_attempted==false and "refused"
                or r.native.status=="DISPATCHED_UNVERIFIED" and "running" or "unknown"
            return answer(state,"native handler observation; inspect next UI stage independently",r.native,r.attempted)
        end)
        if not ok then
            out=answer(r and "unknown" or "refused",tostring(out),r and r.native,r and r.attempted)
        end
        done(out)
    end
    function api.session() local ok,v=pcall(identity);return ok and v or nil end
    local disposal_failed=false
    function api.dispose()
        if disposal_failed then return false end
        if retired then return true end
        retired=true;local ok,value=pcall(comm_adapter.dispose);local ok2,value2=pcall(context_adapter.dispose)
        local ok3,value3=pcall(navigation_adapter.dispose)
        if not ok or value~=true or not ok2 or value2~=true or not ok3 or value3~=true then disposal_failed=true;return false end
        return true
    end
    api.spec={id="x4-comm-menu-v1",revision=revision,actions={inspect=true,open=true,context_inspect=true,context_open=true,
        map_inspect=true,map_open=true,map_panel=true,map_objectinfo=true},session=api.session,invoke=invoke,dispose=api.dispose}
    return api
end

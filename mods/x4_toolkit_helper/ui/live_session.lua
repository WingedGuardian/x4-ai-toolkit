-- Public MD session bridge factory. No ROR/scenario dependency or native effects
-- until constructed. Root supplies fixed-screen native wrappers; no eval/MD exec.
-- Transport analogue: ModSupportAPI simple_menu/interface.lua121-151 copies and
-- clears a blackboard queue; md/simple_menu_api.xml455-466 appends/raises its event.
local owner
return function(env)
    assert(owner == nil, "public session bridge already has an active owner")
    local function hex(v,n)
        return type(v)=="string" and #v==n and not v:find("[^0-9a-f]")
    end
    local function finite(v)
        return type(v)=="number" and v==v and v>=0 and v<math.huge
    end
    assert(type(env)=="table" and hex(env.revision,64),"source-bound revision required")
    for _,k in ipairs({"send","subscribe","read","clear","read_state","now"}) do
        assert(type(env[k])=="function","missing public session dependency: "..k)
    end
    local PROVIDER, REVISION = "x4-live-session-v1", env.revision
    local token, api = {}, {}
    local pending, ready, fault, generation, last_clock
    local seen, count, ignored = {}, 0, 0
    local function keys(t, allowed, count_expected)
        assert(type(t)=="table" and getmetatable(t)==nil,"plain table required")
        local n=0
        for k in pairs(t) do assert(allowed[k],"unexpected field");n=n+1 end
        assert(n==count_expected,"missing field")
    end
    local function copy(t)
        local r={};for k,v in pairs(t) do r[k]=v end;return r
    end
    local function equal(a,b)
        for k,v in pairs(a) do if b[k]~=v then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function clock()
        local now=env.now()
        assert(finite(now) and (last_clock==nil or now>=last_clock),"unknown or backward local clock")
        last_clock=now;return now
    end
    local function notify(p,value)
        local ok=pcall(p.done,value)
        if not ok then fault="HANDSHAKE_CALLBACK_FAILED";ready=nil end
    end
    local function fail(reason)
        fault=fault or reason;ready=nil
        if pending then
            local old=pending;pending=nil;seen[old.request.challenge]="expired"
            notify(old,{kind="UNAVAILABLE",reason=fault})
        end
    end
    local function active()
        return owner==token and fault==nil
    end
    local function tick()
        if not active() then return end
        local now=clock()
        if pending and now-pending.started>10 then
            local old=pending;pending=nil;ready=nil;seen[old.request.challenge]="expired"
            notify(old,{kind="UNAVAILABLE",reason="HANDSHAKE_TIMEOUT_NO_FALLBACK"})
        end
    end
    local function stamp()
        local state=env.read_state()
        keys(state,{provider=true,revision=true,session=true,generation=true,world_token=true,age=true},6)
        assert(state.provider==PROVIDER and state.revision==REVISION and finite(state.age),"unbound MD state")
        assert(type(state.world_token)=="string" and #state.world_token<=128,"invalid world observation")
        assert((state.session=="" and state.generation=="") or
            (hex(state.session,32) and hex(state.generation,32)),"invalid current MD identity")
        assert(state.session=="" or state.world_token~="","present session lacks world observation")
        return {provider=state.provider,revision=state.revision,session=state.session,
            generation=state.generation,world_token=state.world_token}
    end
    local function validate_reply(row)
        keys(row,{kind=true,provider=true,revision=true,mode=true,challenge=true,generation=true,
            session=true,has_session=true,reason=true,age=true,world_token=true},11)
        assert(row.provider==PROVIDER and row.revision==REVISION,"unbound public provider")
        assert(row.kind=="STATE" or row.kind=="READY" or row.kind=="REFUSED","invalid handshake kind")
        assert(row.mode=="probe" or row.mode=="fresh" or row.mode=="resume","invalid handshake mode")
        assert(hex(row.challenge,32) and hex(row.generation,32),"invalid reply correlation")
        -- MD has no boolean datatype: an MD true/false crosses the blackboard as the number 1/0 (vanilla
        -- ego_detailmonitorhelper/helper.lua:8824 tests a blackboard flag with ~= 0). Accept exactly 0/1;
        -- any other value still refuses. Run-03: the boolean-only test faulted the first reply.
        if row.has_session==1 then row.has_session=true elseif row.has_session==0 then row.has_session=false end
        assert(type(row.has_session)=="boolean" and
            ((row.has_session and hex(row.session,32)) or (not row.has_session and row.session=="")),
            "invalid reply session")
        assert(type(row.reason)=="string" and #row.reason<=4096 and finite(row.age),"invalid reply metadata")
        assert(type(row.world_token)=="string" and #row.world_token<=128,"invalid world observation")
        assert(not row.has_session or row.world_token~="","present session lacks world observation")
        return copy(row)
    end
    local function drain()
        if not active() then return end -- retired owners must never consume replacement queues
        local ok,why=pcall(function()
            tick()
            if not active() then return end
            local rows=env.read()
            if rows==nil then return end
            assert(type(rows)=="table" and getmetatable(rows)==nil and #rows<=64,"reply queue overflow")
            local n=0
            for k in pairs(rows) do
                assert(type(k)=="number" and k%1==0 and k>=1 and k<=#rows,"sparse reply queue")
                n=n+1
            end
            assert(n==#rows,"sparse reply queue")
            local matched
            -- Validate the whole bounded batch before clearing or delivering any
            -- callback. A duplicate matching row cannot briefly confer readiness.
            for _,raw in ipairs(rows) do
                local row=validate_reply(raw)
                assert(seen[row.challenge]~="delivered","duplicate delivered handshake")
                if pending and row.challenge==pending.request.challenge then
                    assert(matched==nil,"duplicate pending handshake")
                    assert(row.generation==pending.request.generation and row.mode==pending.request.mode,
                        "handshake correlation rebound")
                    matched=row
                else ignored=ignored+1 end
            end
            assert(env.clear()~=false,"blackboard clear failed")
            if not matched then return end
            local p=pending
            local state=stamp()
            if matched.kind=="READY" then
                assert(p.request.mode~="probe" and matched.has_session and
                    matched.session==p.request.session,"unexpected READY")
                local expected={provider=PROVIDER,revision=REVISION,session=matched.session,
                    generation=matched.generation,world_token=matched.world_token}
                assert(equal(state,expected),"READY differs from current MD identity")
                ready=expected
            elseif matched.kind=="STATE" then
                assert(p.request.mode=="probe" and matched.session==state.session and
                    matched.world_token==state.world_token,"STATE differs from current MD identity")
            end
            pending=nil;seen[p.request.challenge]="delivered"
            notify(p,matched)
        end)
        if not ok then fail("SESSION_PROTOCOL_FAULT: "..tostring(why)) end
    end
    function api.connect(request,done)
        assert(active(),"public session bridge unavailable")
        assert(type(done)=="function","handshake callback required")
        keys(request,{mode=true,session=true,challenge=true,generation=true},4)
        assert(request.mode=="probe" or request.mode=="fresh" or request.mode=="resume","invalid mode")
        assert(hex(request.challenge,32) and hex(request.generation,32),"invalid request correlation")
        assert((request.mode=="probe" and request.session=="") or
            (request.mode~="probe" and hex(request.session,32)),"invalid requested session")
        assert(generation==nil or request.generation==generation,"UI generation changed; recreate bridge")
        assert(pending==nil,"handshake already pending")
        assert(seen[request.challenge]==nil and count<2048,"challenge reused or capacity exhausted")
        local now=clock()
        generation=request.generation;ready=nil;count=count+1
        pending={request=copy(request),done=done,started=now}
        seen[request.challenge]="pending" -- reserve before send, never resend on timeout
        local ok,why=pcall(env.send,copy(request))
        if not ok then fail("HANDSHAKE_SEND_UNCERTAIN: "..tostring(why)) end
        return {status="pending",challenge=request.challenge}
    end
    function api.current()
        if not active() then return nil,fault or "SESSION_OWNER_RETIRED" end
        local ok,result=pcall(function()
            tick()
            if not active() or pending or ready==nil then return nil end
            local actual=stamp()
            assert(equal(actual,ready),"current MD identity changed")
            return copy(actual)
        end)
        if not ok then fail("SESSION_READBACK_UNAVAILABLE: "..tostring(result));return nil,fault end
        if result==nil then return nil,"SESSION_NOT_READY" end
        return result
    end
    function api.tick()
        local ok,why=pcall(tick)
        if not ok then fail("SESSION_CLOCK_UNAVAILABLE: "..tostring(why)) end
    end
    function api.status()
        local current=api.current()
        return {pending=pending~=nil,ready=current~=nil,fault=fault,
            ignored=ignored,requests=count,generation=generation}
    end
    function api.dispose()
        fail("SESSION_BRIDGE_DISPOSED")
        if owner==token then owner=nil end
    end
    owner=token
    local ok,why=pcall(env.subscribe,drain,function()
        if active() then fail("MD_LOAD_INVALIDATED_REQUIRES_NEW_HOST_NONCE") end
    end)
    if not ok then api.dispose();error("session subscription failed: "..tostring(why)) end
    return api
end

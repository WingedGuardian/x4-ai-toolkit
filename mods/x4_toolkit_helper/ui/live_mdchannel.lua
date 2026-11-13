-- Fixed installed-provider MD channel. Host requests cannot choose native symbols,
-- blackboards or event names. Copies the public bootstrap's native queue mechanism.
return function(d)
    assert(type(d)=="table" and type(d.config)=="table","fixed channel configuration required")
    local cfg=d.config
    local function label(v) return type(v)=="string" and #v>0 and #v<=128 and not v:find("[^%w_.$:-]") end
    assert(label(cfg.screen) and label(cfg.event) and label(cfg.queue) and cfg.queue:sub(1,1)=="$",
        "fixed native channel names required")
    assert(type(cfg.controls)=="table" and type(d.public_current)=="function","channel controls/session required")
    local controls={};local count=0
    for name,value in pairs(cfg.controls) do
        assert(label(name) and value==true,"invalid fixed control");controls[name]=true;count=count+1
    end
    assert(count>0 and count<=8,"control vocabulary bound")
    local screen,event,queue=cfg.screen,cfg.event,cfg.queue
    local ffi,C,g,H=d.ffi,d.C,d.globals,d.Helper
    assert(ffi and C and type(g)=="table" and type(H)=="table","native dependencies required")
    local native={}
    for _,name in ipairs({"RegisterEvent","GetNPCBlackboard","SetNPCBlackboard","AddUITriggeredEvent",
        "ConvertStringTo64Bit","ConvertIDTo64Bit","getElapsedTime"}) do
        assert(type(g[name])=="function","missing native channel function: "..name);native[name]=g[name]
    end
    local schedule=H.addDelayedOneTimeCallbackOnUpdate
    assert(type(schedule)=="function","native deadline scheduler required")
    local public=d.public_current
    local disposed,fault,stamp,last_age,read_owner,listener,tick=false,nil,nil,nil,nil,nil,nil
    local timers=0
    local timer_limit,timer_interval=64,10.25
    local api={}
    function api.capacity()
        -- A local observation only: completed replies do not retire deadlines.
        -- No native call, clock advance, reset, callback or session binding.
        return {scope='LOCAL_CHANNEL_DEADLINE_CALLBACKS_NOT_PENDING_REQUESTS',
            scheduled_callbacks_used=timers,scheduled_callbacks_limit=timer_limit,
            deadline_interval_ui_clock_units=timer_interval,
            disposed=disposed,faulted=fault~=nil,stamp_bound=stamp~=nil,
            lifecycle_open=not disposed and fault==nil,tick_armed=tick~=nil,
            future_reservation=false}
    end
    local function live() assert(not disposed and not fault,"MD channel retired or faulted") end
    local function now()
        live();local value=native.getElapsedTime()
        assert(type(value)=="number" and value==value and value>=0 and value<math.huge
            and (last_age==nil or value>=last_age),"invalid or backward UI clock")
        last_age=value;return value
    end
    local function current()
        live();local s=public()
        assert(type(s)=="table" and s.provider=="x4-live-session-v1","canonical public session missing")
        for _,name in ipairs({"revision","session","generation","world_token"}) do
            assert(type(s[name])=="string" and #s[name]>0,"incomplete public stamp")
        end
        if stamp then
            for key,value in pairs(stamp) do assert(s[key]==value,"public session changed") end
        else
            stamp={provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,world_token=s.world_token}
        end
        return s
    end
    local function exact(value)
        if type(value)=="number" then
            assert(value==value and value>0 and value<9007199254740992 and value%1==0,"unsafe native identity")
            value=ffi.new("uint64_t",value)
        end
        assert(type(value)=="cdata" and ffi.istype("uint64_t",value) and value~=0,"exact native identity required")
        return value
    end
    local function player()
        current()
        local raw=C.GetPlayerID();assert(type(raw)=="cdata","native player identity required")
        exact(raw)
        local converted=native.ConvertStringTo64Bit(tostring(raw))
        assert(exact(converted)==raw,"native player conversion changed identity")
        return converted,tostring(raw)
    end
    local function protected(fn,...)
        local results={pcall(fn,...)}
        if not results[1] then fault=tostring(results[2]);error(fault) end
        return results[2]
    end
    function api.arm_tick(fn)
        live();assert(tick==nil and type(fn)=="function","one deadline callback required");tick=fn
    end
    function api.subscribe(fn)
        live();assert(listener==nil and type(fn)=="function","one queue subscriber required")
        listener=fn
        protected(native.RegisterEvent,event,function()
            if disposed or fault or not stamp then return end
            protected(function() current();listener() end)
        end)
    end
    function api.send(control,body)
        return protected(function()
            current();assert(controls[control] and type(body)=="table","unlisted MD control")
            assert(tick and timers<timer_limit,"deadline callback unavailable or capacity exhausted")
            local deadline=now()+timer_interval
            timers=timers+1
            local fired=false
            schedule(function()
                if fired then return end;fired=true
                timers=timers-1
                if disposed or fault then return end
                protected(function() current();tick() end)
            end,false,deadline)
            current();native.AddUITriggeredEvent(screen,control,body)
        end)
    end
    function api.read()
        return protected(function()
            local id,before=player()
            local value=native.GetNPCBlackboard(id,queue)
            local _,after=player();assert(before==after,"player changed during queue read")
            read_owner={id=id,raw=before}
            return value
        end)
    end
    function api.clear()
        return protected(function()
            assert(read_owner,"queue must be read before clearing")
            local _,before=player();assert(before==read_owner.raw,"queue owner changed before clear")
            local owner=read_owner;read_owner=nil
            native.SetNPCBlackboard(owner.id,queue,nil)
            local _,after=player();assert(before==after,"queue owner changed during clear")
        end)
    end
    function api.component_id(value)
        protected(current)
        local id=type(value)=="cdata" and ffi.istype("uint64_t",value) and value or native.ConvertIDTo64Bit(value)
        -- An unreadable observation is local UNKNOWN, not a broken queue protocol.
        -- Canonical-session failure above still retires the channel.
        return tostring(exact(id)) -- never inserts into helper-issued IDs
    end
    function api.dispose() disposed=true;read_owner=nil;listener=nil;tick=nil;return true end
    api.now=now;api.public_current=current
    return api
end

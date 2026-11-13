-- Native SETA control and clock samples; no speed/outcome invariance claim.
-- READ: menu_map.lua5384-5396 Start/StopPlayerActivity("seta"),
-- menu_docked.lua62/152-153, Helper409/10103 IsSetaActive/GetCurrentGameTime,
-- core crosshair handling.lua118/132/1099 HasSeta/IsSetaActive.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C and type(d.activity)=="function"
        and type(d.current)=="function" and type(d.now)=="function"
        and type(d.resolve_issued)=="function","time-control dependencies")
    local ffi,C=d.ffi,d.C
    if not pcall(ffi.typeof,"UniverseID")then ffi.cdef("typedef uint64_t UniverseID;")end
    assert(ffi.istype("uint64_t",ffi.new("UniverseID")),"native identity ABI")
    ffi.cdef[[
        UniverseID GetPlayerOccupiedShipID(void);
        bool HasSeta(void);
        bool CanActivateSeta(bool);
        bool IsSetaActive(void);
        double GetCurrentGameTime(void);
        void StartPlayerActivity(const char*);
        void StopPlayerActivity(const char*);
    ]]
    local api,records,serial,disposed={}, {},0,false
    local MAX_TICKETS=128
    local function finite(v)return type(v)=="number" and v==v and v>=0 and v<math.huge end
    local function bool(v)assert(type(v)=="boolean","native boolean unavailable");return v end
    local function id(v)assert(type(v)=="cdata" and ffi.istype("uint64_t",v) and v~=0,"exact occupied ship required");return v end
    local function copy(v)
        if type(v)~="table"then return v end
        local t={};for k,x in pairs(v)do t[k]=copy(x)end;return t
    end
    local function same(a,b)
        if type(a)~=type(b)then return false end
        if type(a)~="table"then return a==b end
        for k,v in pairs(a)do if not same(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end
        return true
    end
    local function stamp()
        assert(not disposed,"time control retired")
        local s=d.current();assert(type(s)=="table" and getmetatable(s)==nil,"session unavailable")
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        local n=0
        for k,v in pairs(s)do assert(keys[k] and type(v)=="string" and #v>0 and #v<=4096,"session field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1","session shape")
        for _,k in ipairs({"revision","session","generation"})do
            assert(#s[k]==(k=="revision" and 64 or 32) and not s[k]:find("[^0-9a-f]"),"session token")
        end
        return copy(s)
    end
    local function sample(token)
        local before=stamp();local began=d.now();assert(finite(began),"UI clock unavailable")
        local ship=id(d.resolve_issued(token,"ship"))
        assert(id(C.GetPlayerOccupiedShipID())==ship,"occupied ship mismatch")
        local game=C.GetCurrentGameTime();assert(finite(game),"native game clock unavailable")
        local activity=d.activity()
        assert(type(activity)=="string" and #activity<=128 and not activity:find("[%c]"),"activity unavailable")
        local out={status="OBSERVED",scope="NATIVE_SETA_STATE_AND_CLOCK",stamp=before,
            ship=tostring(ship),activity=activity,has_seta=bool(C.HasSeta()),
            can_activate=bool(C.CanActivateSeta(false)),active=bool(C.IsSetaActive()),
            game_time=game,game_clock="NATIVE_GET_CURRENT_GAME_TIME",
            ui_clock="UI_ELAPSED",ui_begin=began,atomic=false,
            acceleration_factor_qualified=false,outcome_invariance_qualified=false}
        assert(id(C.GetPlayerOccupiedShipID())==ship,"occupied ship changed")
        local after=stamp();local ended=d.now()
        assert(same(before,after) and finite(ended) and ended>=began,"sample session/clock changed")
        out.ui_end=ended;return out
    end
    local function fail(reason,attempted)
        return {status=attempted and "UNCERTAIN" or "REFUSED",reason=tostring(reason):sub(1,1024),
            action_attempted=attempted==true,acceleration_factor_qualified=false,outcome_invariance_qualified=false}
    end
    function api.observe(ship)
        local ok,value=pcall(sample,ship)
        return ok and value or fail(value,false)
    end
    local function eligible(mode,s)
        if mode=="start"then
            assert(s.has_seta and s.can_activate and not s.active and s.activity~="seta","SETA unavailable/already active")
        else assert(s.active and s.activity=="seta","SETA not active")end
    end
    function api.prepare(mode,ship)
        local ok,value=pcall(function()
            assert(mode=="start" or mode=="stop","closed SETA mode")
            assert(type(ship)=="string" and serial<MAX_TICKETS,"issued ship/capacity")
            local s=sample(ship);eligible(mode,s)
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={mode=mode,ship=ship,state=s,used=false}
            return {status="PREPARED",ticket=ticket,before=copy(s),action_attempted=false}
        end)
        return ok and value or fail(value,false)
    end
    function api.execute(ticket)
        local r=records[ticket]
        if not r or r.used or disposed then return fail("unknown, consumed or retired ticket",false)end
        r.used=true
        local attempted=false
        local ok,value=pcall(function()
            local s=sample(r.ship);eligible(r.mode,s)
            assert(same(s.stamp,r.state.stamp) and s.ui_begin>=r.state.ui_end
                and s.ui_end-r.state.ui_end<=5 and s.game_time>=r.state.game_time,"preparation clock/session drift")
            for _,k in ipairs({"ship","activity","has_seta","can_activate","active"})do
                assert(s[k]==r.state[k],"SETA precondition changed: "..k)
            end
            attempted=true
            if r.mode=="start"then C.StartPlayerActivity("seta")else C.StopPlayerActivity("seta")end
            local after=stamp();local ended=d.now()
            assert(same(after,s.stamp) and finite(ended) and ended>=s.ui_end,"post-dispatch session/clock changed")
            return {status="DISPATCHED_UNVERIFIED",mode=r.mode,action_attempted=true,
                stamp=after,before=s,ui_end=ended,acceleration_factor_qualified=false,outcome_invariance_qualified=false}
        end)
        return ok and value or fail(value,attempted)
    end
    function api.dispose()disposed=true;records={};return true end
    function api.capacity()
        return {available=not disposed,budgets={tickets={used=serial,limit=MAX_TICKETS}},
            cached_context='PER_REQUEST_NOT_EXPOSED'}
    end
    return api
end


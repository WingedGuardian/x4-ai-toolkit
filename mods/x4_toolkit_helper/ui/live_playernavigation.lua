-- Native player navigation primitives; no automatic travel, retries or cleanup.
-- READ: menu_map.lua:29539-29554 guidance,5361-5362 autopilot toggle;
-- menu_docked.lua:1230-1238 target eligibility,1503-1512 toggle/QuickDock;
-- menu_interactmenu.lua:2133-2141 RequestDockAt,5430-5459 eligibility.
-- Each dispatch is distinct. Native acceptance is NOT arrival or docking proof.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C and type(d.globals)=="table","navigation bindings")
    for _,k in ipairs({"resolve_issued","current","now"}) do assert(type(d[k])=="function","missing "..k) end
    local ffi,C,g=d.ffi,d.C,d.globals
    for _,k in ipairs({"GetComponentData","IsValidComponent","IsDockingPossible",
        "GetActiveGuidanceMissionComponent","GetAutoPilotTarget","ConvertIDTo64Bit"}) do
        assert(type(g[k])=="function","missing navigation global "..k)
    end
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.istype("uint64_t",ffi.new("UniverseID")),"identity ABI")
    if not pcall(ffi.typeof,"UIPosRot") then ffi.cdef("typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;") end
    local proof=ffi.typeof("struct {float x,y,z,yaw,pitch,roll;}")
    assert(ffi.sizeof("UIPosRot")==ffi.sizeof(proof) and ffi.alignof("UIPosRot")==ffi.alignof(proof),"position ABI")
    local actual,expected=ffi.new("UIPosRot"),ffi.new(proof)
    for _,k in ipairs({"x","y","z","yaw","pitch","roll"}) do
        assert(ffi.offsetof("UIPosRot",k)==ffi.offsetof(proof,k),"position field ABI")
        actual[k]=1.25;expected[k]=1.25
    end
    assert(ffi.string(ffi.cast("char*",actual),ffi.sizeof(actual))==
        ffi.string(ffi.cast("char*",expected),ffi.sizeof(expected)),"position scalar ABI")
    if not pcall(ffi.typeof,"SofttargetDetails2") then
        ffi.cdef("typedef struct {UniverseID softtargetID;const char* softtargetConnectionName;uint32_t messageID;} SofttargetDetails2;")
    end
    local targetproof=ffi.typeof("struct {uint64_t softtargetID;const char* softtargetConnectionName;uint32_t messageID;}")
    assert(ffi.sizeof("SofttargetDetails2")==ffi.sizeof(targetproof) and
        ffi.alignof("SofttargetDetails2")==ffi.alignof(targetproof),"softtarget ABI")
    for _,k in ipairs({"softtargetID","softtargetConnectionName","messageID"}) do
        assert(ffi.offsetof("SofttargetDetails2",k)==ffi.offsetof(targetproof,k),"softtarget field ABI")
    end
    assert(ffi.istype("uint64_t",ffi.new("SofttargetDetails2").softtargetID),"softtarget identity ABI")
    ffi.cdef[[
        UniverseID GetPlayerControlledShipID(void);
        UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetContextByClass(UniverseID,const char*,bool);
        bool IsComponentClass(UniverseID,const char*);
        bool IsAutoPilotActive(void);
        bool ToggleAutoPilot(bool);
        void SetGuidance(UniverseID,UIPosRot);
        bool RequestDockAt(UniverseID,bool);
        bool QuickDock(void);
        SofttargetDetails2 GetSofttarget2(void);
        UniverseID GetEnvironmentObject(void);
        bool SetSofttarget(UniverseID,const char*);
    ]]
    local api,records,serial,disposed={}, {},0,false
    local MAX_TICKETS=256
    local actions={guidance=true,autopilot_start=true,dock_request=true,quick_dock=true,target=true}
    local function finite(v)return type(v)=="number" and v==v and math.abs(v)<math.huge end
    local function id(v,zero)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v) and (zero or v~=0),"exact native identity required")
        return v
    end
    local function wire(v)return tostring(id(v,true)) end
    local function bool(v)assert(type(v)=="boolean","native boolean unavailable");return v end
    local function same(a,b)
        if type(a)~=type(b)then return false end
        if type(a)~="table"then return a==b end
        for k,v in pairs(a)do if not same(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end
        return true
    end
    local function clone(v)
        if type(v)~="table"then return v end
        local out={};for k,x in pairs(v)do out[k]=clone(x)end;return out
    end
    local function stamp()
        assert(not disposed,"navigation disposed")
        local s=d.current();assert(type(s)=="table" and getmetatable(s)==nil,"session unavailable")
        local out,count={},0
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        for k,v in pairs(s)do assert(keys[k] and type(v)=="string" and #v>0 and #v<=4096,"session field");out[k]=v;count=count+1 end
        assert(count==5 and out.provider=="x4-live-session-v1","session shape")
        for _,k in ipairs({"revision","session","generation"})do
            assert(#out[k]==(k=="revision" and 64 or 32) and not out[k]:find("[^0-9a-f]"),"session token")
        end
        local age=d.now();assert(finite(age) and age>=0,"clock unavailable")
        return out,age
    end
    local function global_id(fn)
        local raw=fn()
        if raw==nil then return "UNKNOWN" end -- never turn an unavailable global into zero
        return wire(g.ConvertIDTo64Bit(raw))
    end
    local function snapshot(shiptoken,targettoken)
        local before,age=stamp()
        local ship=id(d.resolve_issued(shiptoken,"ship"))
        local target=id(d.resolve_issued(targettoken,"object"))
        assert(ship~=target and bool(g.IsValidComponent(ship)) and bool(g.IsValidComponent(target)),"subject unavailable")
        assert(C.IsComponentClass(target,"ship")==true or C.IsComponentClass(target,"station")==true,"dock container required")
        local controlled=id(C.GetPlayerControlledShipID(),true)
        local occupied=id(C.GetPlayerOccupiedShipID(),true)
        assert(controlled==ship and occupied==ship,"player must control the exact occupied ship")
        local docked=bool(g.GetComponentData(ship,"isdocked"))
        local docking=bool(g.GetComponentData(ship,"isdocking"))
        local parent=id(C.GetContextByClass(ship,"container",false),true)
        local active=bool(C.IsAutoPilotActive())
        local soft=id(C.GetSofttarget2().softtargetID,true)
        local environment=id(C.GetEnvironmentObject(),true)
        local docktarget=ffi.new("uint64_t",0)
        if soft~=0 then docktarget=id(C.GetContextByClass(soft,"container",true),true)
        elseif environment~=0 then docktarget=id(C.GetContextByClass(environment,"container",true),true) end
        local out={status="OBSERVED",scope="PLAYER_NAVIGATION_STATE",stamp=before,
            ship=wire(ship),target=wire(target),controlled=wire(controlled),occupied=wire(occupied),
            docked=docked,docking=docking,container=wire(parent),autopilot_active=active,
            guidance=global_id(g.GetActiveGuidanceMissionComponent),
            autopilot_target=global_id(g.GetAutoPilotTarget),softtarget=wire(soft),
            environment=wire(environment),quick_dock_target=wire(docktarget),
            dock_possible=bool(g.IsDockingPossible(ship,target)),
            dock_request_eligible=bool(C.RequestDockAt(target,true)),
            autopilot_toggle_eligible=bool(C.ToggleAutoPilot(true)),
            docked_at_requested_container=docked and parent==target,
            atomic=false,arrival_verified=false,observer_clock="UI_ELAPSED"}
        local after,ended=stamp()
        assert(same(before,after) and ended>=age,"read session/clock changed")
        out.start_time=age;out.end_time=ended
        return out,ship,target
    end
    local function refusal(reason,attempted)
        return {status=attempted and "UNCERTAIN" or "REFUSED",reason=tostring(reason):sub(1,1024),
            action_attempted=attempted==true,arrival_verified=false}
    end
    local function eligible(action,s)
        assert(not s.docked and not s.docking,"existing docking state must not be toggled")
        assert(not s.autopilot_active,"existing autopilot must not be toggled")
        if action=="autopilot_start"then
            assert(s.guidance==s.target and s.autopilot_toggle_eligible,"guidance/eligibility mismatch")
        elseif action=="dock_request"or action=="quick_dock"then
            assert(s.dock_possible and s.dock_request_eligible,"native docking refused")
            if action=="quick_dock"then assert(s.quick_dock_target==s.target,"QuickDock would target another container")end
        end
    end
    function api.observe(ship,target)
        local ok,out=pcall(snapshot,ship,target)
        return ok and out or refusal(out,false)
    end
    function api.prepare(action,ship,target)
        local ok,out=pcall(function()
            assert(actions[action] and type(ship)=="string" and type(target)=="string","fixed action/issued tokens required")
            assert(serial<MAX_TICKETS,"navigation capacity")
            local s=snapshot(ship,target);eligible(action,s)
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={action=action,ship=ship,target=target,state=s,used=false}
            return {status="PREPARED",ticket=ticket,action=action,before=clone(s),action_attempted=false}
        end)
        return ok and out or refusal(out,false)
    end
    function api.execute(ticket)
        local r=records[ticket]
        if not r or r.used or disposed then return refusal("unknown, retired or consumed ticket",false)end
        r.used=true
        local attempted=false
        local ok,out=pcall(function()
            local s,ship,target=snapshot(r.ship,r.target);eligible(r.action,s)
            assert(same(s.stamp,r.state.stamp) and s.start_time>=r.state.end_time and
                s.end_time-r.state.end_time<=5,"stale preparation")
            for _,k in ipairs({"ship","target","controlled","occupied","docked","docking","container",
                "autopilot_active","guidance","autopilot_target","softtarget","environment",
                "quick_dock_target","dock_possible","dock_request_eligible","autopilot_toggle_eligible"})do
                assert(s[k]==r.state[k],"navigation precondition changed: "..k)
            end
            local verdict
            attempted=true
            if r.action=="guidance"then C.SetGuidance(target,ffi.new("UIPosRot",0))
            elseif r.action=="target"then verdict=bool(C.SetSofttarget(target,""))
            elseif r.action=="autopilot_start"then verdict=bool(C.ToggleAutoPilot(false))
            elseif r.action=="dock_request"then verdict=bool(C.RequestDockAt(target,false))
            else verdict=bool(C.QuickDock()) end
            local after,ended=stamp()
            assert(same(after,s.stamp) and ended>=s.end_time,"post-dispatch session/clock changed")
            return {status=verdict==false and "NATIVE_REFUSED" or "DISPATCHED_UNVERIFIED",
                action=r.action,action_attempted=true,native_return=verdict,
                before=s,stamp=after,end_time=ended,arrival_verified=false}
        end)
        return ok and out or refusal(out,attempted)
    end
    function api.dispose()disposed=true;records={};return true end
    function api.capacity()
        return {available=not disposed,budgets={tickets={used=serial,limit=MAX_TICKETS}},
            cached_context='PER_REQUEST_NOT_EXPOSED'}
    end
    return api
end


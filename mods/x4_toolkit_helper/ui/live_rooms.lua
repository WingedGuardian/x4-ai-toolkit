-- Native transporter targets sourced only from the engine-opened TransporterMenu.
-- No guessed connection, menu opener, walking, docking, or arrival guarantee.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C,"room native dependencies")
    for _,k in ipairs({"current","now","encode","menus","convert_id","component_data"}) do
        assert(type(d[k])=="function","room dependency: "..k)
    end
    local ffi,C=d.ffi,d.C
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.istype("uint64_t",ffi.new("UniverseID")),"room identity ABI")
    if not pcall(ffi.typeof,"UIComponentSlot") then
        ffi.cdef("typedef struct {UniverseID component;const char* connection;} UIComponentSlot;")
    end
    local proof=ffi.typeof("struct {uint64_t component;const char* connection;}")
    local item=ffi.new("UIComponentSlot")
    assert(ffi.sizeof(item)==ffi.sizeof(proof) and ffi.alignof("UIComponentSlot")==ffi.alignof(proof)
        and ffi.offsetof("UIComponentSlot","component")==ffi.offsetof(proof,"component")
        and ffi.offsetof("UIComponentSlot","connection")==ffi.offsetof(proof,"connection")
        and ffi.istype("uint64_t",item.component) and ffi.istype("const char*",item.connection),"room slot ABI")
    ffi.cdef[[
        UniverseID GetPlayerID(void);
        UniverseID GetPlayerContainerID(void);
        UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);
        bool IsComponentClass(UniverseID componentid,const char* classname);
        uint32_t GetNumValidTransporterTargets2(UniverseID componentid,UIComponentSlot currentlocation);
        uint32_t GetValidTransporterTargets2(UIComponentSlot* result,uint32_t resultlen,UniverseID componentid,UIComponentSlot currentlocation);
        UniverseID GetRoomForTransporter(UIComponentSlot transporter);
        const char* GetTransporterLocationName(UIComponentSlot transporter);
        void TransportPlayerToTarget(UIComponentSlot target);
    ]]
    local api,disposed,lastclock,latest,serial,tickets,records={},false,nil,nil,0,0,{}
    local unresolved
    local function finite(v)return type(v)=="number" and v==v and math.abs(v)<math.huge end
    local function integer(v,n)return finite(v) and v>=0 and v%1==0 and v<=n end
    local function hex(v,n)return type(v)=="string" and #v==n and not v:find("[^0-9a-f]")end
    local function exact(v)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"native identity unavailable")
        local s=tostring(v):match("^(%d+)ULL$");assert(s,"native identity format");return s
    end
    local function text(v)
        if type(v)=="string" then assert(#v<=256 and not v:find("%z"),"room string bound");return v end
        assert(type(v)=="cdata" and ffi.istype("const char*",v) and v~=ffi.NULL,"native room string unavailable")
        local n=0;while n<=256 and v[n]~=0 do n=n+1 end
        assert(n<=256,"native room string overflow");return ffi.string(v,n)
    end
    local function equal(a,b)
        if type(a)~=type(b)then return false end
        if type(a)~="table"then return a==b end
        for k,v in pairs(a)do if not equal(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end
        return true
    end
    local function copy(v)
        if type(v)~="table"then return v end
        local out={};for k,x in pairs(v)do out[k]=copy(x)end;return out
    end
    local function clock()
        local n=d.now();assert(finite(n) and n>=0 and (not lastclock or n>=lastclock),"room clock unavailable/backward")
        lastclock=n;return n
    end
    local function stamp()
        assert(not disposed,"room adapter disposed")
        local s=d.current();assert(type(s)=="table" and getmetatable(s)==nil,"canonical identity unavailable")
        local fields={provider=true,revision=true,session=true,generation=true,world_token=true};local n=0
        for k in pairs(s)do assert(fields[k],"unknown stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and hex(s.session,32)
            and hex(s.generation,32) and type(s.world_token)=="string" and #s.world_token>0
            and #s.world_token<=4096,"canonical identity malformed")
        return copy(s)
    end
    local function location()
        local player=C.GetPlayerID();assert(exact(player)~="0","player unavailable")
        local room=C.GetContextByClass(player,"room",false)
        local container=C.GetPlayerContainerID();local ship=C.GetPlayerOccupiedShipID()
        local rid=exact(room)
        assert(rid=="0" or C.IsComponentClass(room,"room")==true,"player room class mismatch")
        assert(exact(C.GetPlayerID())==exact(player),"player changed")
        return {player=exact(player),container=exact(container),occupied_ship=exact(ship),
            room=rid,room_status=rid=="0" and "UNKNOWN" or "OBSERVED_PARENT_ROOM_CONTEXT",
            room_source="GetContextByClass(GetPlayerID(),room,false)",walking_verified=false,docking_verified=false}
    end
    local function menu()
        local list=d.menus();assert(type(list)=="table" and #list<=256,"menu registry unavailable")
        local found
        for k,v in pairs(list)do
            assert(integer(k,#list) and k>0 and type(v)=="table","menu registry malformed")
            if v.name=="TransporterMenu" then assert(not found,"ambiguous transporter menu");found=v end
            assert(v.name=="TransporterMenu" or v.shown~=true,"another menu is open")
        end
        assert(found and found.shown==true and found.minimized~=true,"engine-opened transporter menu required")
        assert(type(found.param)=="table" and type(found.buttonGoTo)=="function","native menu incomplete")
        local component=d.convert_id(found.param[3]);local connection=text(found.param[4])
        assert(exact(component)~="0" and exact(found.transportercomponent)==exact(component)
            and text(found.transporterconnection)==connection,"native source slot drift")
        local slot=ffi.new("UIComponentSlot");slot.component=component;slot.connection=connection
        return found,slot,connection
    end
    local function rows(source)
        local n=C.GetNumValidTransporterTargets2(source.component,source)
        assert(integer(n,128),"native target count unavailable/overflow")
        local out,seen={},{}
        if n>0 then
            local buf=ffi.new("UIComponentSlot[?]",n)
            assert(C.GetValidTransporterTargets2(buf,n,source.component,source)==n,"native target count/fill mismatch")
            for i=0,n-1 do
                local s=buf[i];local id,connection=exact(s.component),text(s.connection)
                assert(id~="0","zero target component")
                local key=id..":"..#connection..":"..connection
                assert(not seen[key],"duplicate native target");seen[key]=true
                local room=C.GetRoomForTransporter(s);local rid=exact(room)
                assert(rid=="0" or C.IsComponentClass(room,"room")==true,"target room class mismatch")
                local locked=false;if rid~="0" then locked=d.component_data(room,"islocked") end
                assert(type(locked)=="boolean","room lock state unknown")
                local zone=C.IsComponentClass(s.component,"zone");assert(type(zone)=="boolean","target class unknown")
                out[#out+1]={index=i+1,component=id,connection=connection,room=rid,locked=locked,
                    zone=zone,name=text(C.GetTransporterLocationName(s)),
                    is_current=(id==exact(source.component) and connection==text(source.connection))}
            end
        end
        assert(C.GetNumValidTransporterTargets2(source.component,source)==n,"target count changed")
        return out,n
    end
    local function census()
        local before=stamp();local began=clock();local loc=location()
        local m,source,connection=menu();local source_room=C.GetRoomForTransporter(source)
        local source_room_id=exact(source_room)
        assert(source_room_id=="0" or C.IsComponentClass(source_room,"room")==true,"source room class mismatch")
        local first,n=rows(source);local second,n2=rows(source)
        local m2,source2,connection2=menu();local loc2=location()
        assert(m==m2 and connection==connection2 and exact(source.component)==exact(source2.component)
            and exact(C.GetRoomForTransporter(source2))==source_room_id
            and n==n2 and equal(first,second) and equal(loc,loc2),"transporter context changed")
        local after=stamp();local ended=clock();assert(equal(before,after),"public identity changed")
        return {status="OBSERVED",scope="ENGINE_OPENED_NATIVE_TRANSPORTER_TARGETS",stamp=before,
            started=began,finished=ended,source={component=exact(source.component),connection=connection,room=source_room_id},
            location=loc,targets=first,count=n,complete_native_target_list=true,
            menu_opening_supported=false,rendered_row_coverage=false,atomic=false},m
    end
    local function unavailable(err)return {status="UNAVAILABLE",reason=tostring(err):sub(1,1024),action_attempted=false}end
    function api.location()
        local ok,out=pcall(function()
            local s=stamp();local began=clock();local a=location();local b=location()
            assert(equal(a,b) and equal(s,stamp()),"player location changed")
            return {status="OBSERVED",stamp=s,started=began,finished=clock(),location=a,
                atomic=false,scope="NATIVE_PLAYER_CONTAINER_AND_PARENT_ROOM_CONTEXT"}
        end)
        return ok and out or unavailable(out)
    end
    function api.targets()
        local ok,out=pcall(function()
            local v,m=census()
            if unresolved then
                assert(equal(v.stamp,unresolved.stamp),"unresolved transport belongs to retired session")
                local target=unresolved.target
                if v.source.component==target.component and v.source.connection==target.connection
                    and v.source.room==target.room and v.location.room==target.room and v.location.room~="0" then
                    unresolved=nil
                end
            end
            local raw=d.encode(v);assert(type(raw)=="string" and #raw<=30000,"target payload overflow")
            assert(serial<128,"room capture budget exhausted");serial=serial+1
            v.capture=tostring(serial);latest={value=copy(v),menu=m}
            local returned=copy(v);returned.unresolved_transport=unresolved~=nil;return returned
        end)
        return ok and out or unavailable(out)
    end
    function api.prepare(capture,index)
        local ok,out=pcall(function()
            assert(not unresolved,"earlier transport remains unresolved")
            assert(type(capture)=="string" and latest and capture==latest.value.capture,"stale target capture")
            assert(integer(index,#latest.value.targets) and index>0,"target index outside captured set")
            local fresh,m=census();local prior=copy(latest.value);prior.capture=nil
            -- Capture clocks describe separate observations; identity/model must agree.
            fresh.started=prior.started;fresh.finished=prior.finished
            assert(m==latest.menu and equal(fresh,prior),"target capture changed")
            assert(clock()-latest.value.finished<=30,"target capture expired")
            local target=latest.value.targets[index]
            assert(not target.zone,"zone/spacesuit confirmation route unsupported")
            assert(not target.is_current and not target.locked and target.room~="0","target room not actionable")
            assert(tickets<128,"room ticket budget exhausted");tickets=tickets+1
            local token={};records[token]={capture=capture,index=index,stamp=copy(prior.stamp),
                value=copy(latest.value),menu=m,state="PREPARED",prepared=clock()}
            return token
        end)
        return ok and out or nil,not ok and tostring(out) or nil
    end
    function api.execute(token)
        local r=records[token]
        if not r or r.state~="PREPARED"then return {status="REFUSED",reason="unknown/consumed room ticket",action_attempted=false}end
        r.state="CONSUMED"
        local called=false
        local ok,out=pcall(function()
            assert(not unresolved,"earlier transport remains unresolved")
            assert(equal(r.stamp,stamp()) and clock()-r.prepared<=15,"room ticket lifecycle expired")
            local fresh,m=census();local prior=copy(r.value);prior.capture=nil
            fresh.started=prior.started;fresh.finished=prior.finished
            assert(m==r.menu and equal(fresh,prior),"prepared transporter context changed")
            local t=fresh.targets[r.index];assert(t and not t.zone and not t.locked and not t.is_current and t.room~="0","room target not eligible")
            local native=ffi.new("UIComponentSlot");native.component=d.convert_id(t.component);native.connection=t.connection
            assert(exact(native.component)==t.component,"target conversion mismatch")
            assert(equal(location(),fresh.location) and equal(r.stamp,stamp())
                and clock()-r.prepared<=15,"final room dispatch context changed")
            unresolved={stamp=copy(r.stamp),target=copy(t)}
            called=true;C.TransportPlayerToTarget(native)
            return {status="TRANSPORT_REQUESTED",action_attempted=true,target=copy(t),stamp=copy(r.stamp),
                mechanism="NATIVE_TRANSPORTER_API_NOT_RENDERED_BUTTON",arrival_verified=false,
                walking_verified=false,docking_verified=false}
        end)
        r.result=ok and out or {status=called and "UNKNOWN" or "REFUSED",reason=tostring(out):sub(1,1024),action_attempted=called}
        return copy(r.result)
    end
    function api.status(token)
        local r=records[token];if not r then return {status="REFUSED",reason="unknown room ticket"}end
        return {status=r.state,result=copy(r.result),historical=true,arrival_verified=false,
            unresolved_transport=unresolved~=nil}
    end
    function api.dispose()disposed=true;latest=nil;return true end
    return api
end

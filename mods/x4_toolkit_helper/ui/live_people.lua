-- Read-only role/skill-tier NPCSeed census; not a component/person-object census.
-- Native analogue: MapMenu.prepareInfoFrame crew handling; see qualification doc.
return function(d)
    local ffi,C,g=d.ffi,d.C,d.globals
    assert(ffi and C and type(g)=="table","native dependencies required")
    for _,k in ipairs({"resolve_issued","current","now","encode"}) do assert(type(d[k])=="function","missing "..k) end
    assert(type(g.IsValidComponent)=="function","native validity required")
    for _,name in ipairs({"UniverseID","NPCSeed"}) do
        if not pcall(ffi.typeof,name) then ffi.cdef("typedef uint64_t "..name..";") end
        assert(ffi.istype("uint64_t",ffi.new(name)),"unsigned native identity required")
    end
    local layouts={
        PeopleInfo={fields="const char *id,*name,*desc;uint32_t amount,numtiers;bool canhire;",
            text={"id","name","desc"},uint={"amount","numtiers"},bool={"canhire"}},
        RoleTierData={fields="const char *name;int32_t skilllevel;uint32_t amount;",
            text={"name"},uint={"amount"},int={"skilllevel"}}
    }
    for name,layout in pairs(layouts) do
        if not pcall(ffi.typeof,name) then ffi.cdef("typedef struct {"..layout.fields.."} "..name..";") end
        local proof=ffi.typeof("struct {"..layout.fields.."}")
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),"crew ABI size")
        local actual,expected=ffi.new(name),ffi.new(proof)
        for kind,fields in pairs(layout) do
            if kind~="fields" then for _,field in ipairs(fields) do
                assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field),"crew ABI offset")
                if kind=="text" then assert(ffi.istype("const char *",actual[field]),"crew ABI text")
                elseif kind=="bool" then assert(type(actual[field])=="boolean","crew ABI bool");actual[field]=true;expected[field]=true
                elseif kind=="uint" then actual[field]=4294967295;expected[field]=4294967295;assert(actual[field]==4294967295,"crew ABI unsigned")
                else actual[field]=-123;expected[field]=-123;assert(actual[field]==-123,"crew ABI signed") end
            end end
        end
        assert(ffi.string(ffi.cast("char*",actual),ffi.sizeof(actual))==ffi.string(ffi.cast("char*",expected),ffi.sizeof(expected)),"crew ABI representation")
    end
    ffi.cdef[[
        uint32_t GetNumAllRoles(void);
        uint32_t GetPeople2(PeopleInfo* result,uint32_t resultlen,UniverseID controllableid,bool includearriving);
        uint32_t GetRoleTiers(RoleTierData* result,uint32_t resultlen,UniverseID controllableid,const char* role);
        uint32_t GetRoleTierNPCs(NPCSeed* result,uint32_t resultlen,UniverseID controllableid,const char* role,int32_t skilllevel);
        uint32_t GetPeopleCapacity(UniverseID controllableid,const char* macroname,bool includepilot);
        bool HasPersonArrived(UniverseID controllableid,NPCSeed person);
        bool IsPersonTransferScheduled(UniverseID controllableid,NPCSeed person);
        bool IsComponentClass(UniverseID componentid,const char* classname);
    ]]
    local api,cache,seen,attempts,lastclock={},nil,{},0,nil
    local MAX_CAPTURES,capacity_retired=128,false
    local function finite(v) return type(v)=="number" and v==v and math.abs(v)<=9007199254740991 end
    local function clock()
        local t=d.now();assert(finite(t) and t>=0 and (not lastclock or t>=lastclock),"clock unavailable/backward")
        lastclock=t;return t
    end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function copy(t)
        if type(t)~="table" then return t end
        local o={};for k,v in pairs(t) do o[k]=copy(v) end;return o
    end
    local function equal(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end;return true
    end
    local function stamp()
        local s=d.current();local n=0
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical session unavailable")
        for k in pairs(s) do assert(keys[k],"unexpected canonical field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and hex(s.session,32) and
            hex(s.generation,32) and type(s.world_token)=="string" and #s.world_token>0 and #s.world_token<=4096,"canonical session malformed")
        return copy(s)
    end
    local function ident(v,zero)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"exact native identity required")
        local s=tostring(v):match("^(%d+)ULL$")
        assert(s and (zero or s~="0"),"native identity absent");return s
    end
    local function string_field(v)
        assert(v~=nil and v~=ffi.NULL,"native string unreadable")
        local n=0;while n<=512 and v[n]~=0 do n=n+1 end;assert(n<=512,"native text unterminated/over bound");local s=ffi.string(v,n)
        assert(#s<=512 and not s:find("[%z\1-\31]"),"native text exceeds bound")
        return s -- Empty native macro/faction is retained, never replaced with a guessed label.
    end
    local function count(v)
        assert(finite(v) and v>=0 and v%1==0 and v<=8192,"native count unavailable/over bound")
        return v
    end
    local function encode(v,limit)
        local s=d.encode(v);assert(type(s)=="string" and #s<=limit,"encoded byte bound");return #s
    end
    local function safe(fn)
        local ok,v=pcall(fn)
        return ok and v or {status="REFUSED",complete=false,reason=tostring(v):sub(1,512)}
    end
    local function container(token)
        assert(type(token)=="string" and #token>0 and #token<=128,"issued container required")
        local obj=d.resolve_issued(token,"object");local id=ident(obj)
        assert(g.IsValidComponent(obj)==true,"issued container invalid")
        assert(C.IsComponentClass(obj,"station")==true or C.IsComponentClass(obj,"ship")==true,"station/ship container required")
        return obj,id
    end

    local function metadata(c)
        local out={status="OBSERVED",complete=true,capture_id=c.id,stamp=copy(c.stamp),container_id=c.container_id,
            start_time=c.start_time,end_time=c.end_time,atomic=false,total=#c.rows,counts=copy(c.counts),
            scope="NATIVE_ROLE_TIER_NPCSEED_CENSUS",include_arriving=true,capacity_excludes_pilot=true,
            native_calls=c.calls,retained_json_bytes=c.bytes,ttl_seconds=300,max_page_rows=16,max_payload_bytes=3000}
        encode(out,3000);return out
    end
    local function current(id)
        assert(cache and cache.id==id,"unknown/superseded capture")
        assert(equal(stamp(),cache.stamp),"canonical session changed")
        assert(clock()-cache.end_time<=300,"capture expired")
        return cache
    end
    function api.capture(token,nonce)
        return safe(function()
            assert(hex(nonce,32),"caller nonce required")
            if cache and cache.nonce==nonce then assert(token==cache.token,"nonce target changed");return metadata(current(cache.id)) end
            assert(not seen[nonce] and attempts<MAX_CAPTURES,"capture nonce/history refused")
            cache=nil;seen[nonce]=true;attempts=attempts+1
            local c={nonce=nonce,id="people:"..nonce,stamp=stamp(),start_time=clock(),calls=0,token=token}
            c.object,c.container_id=container(token)
            local function call(fn,...)
                c.calls=c.calls+1;assert(c.calls<=40000,"native crew call budget");return fn(...)
            end
            local function census()
                local rows,roles,seeds={}, {}, {}
                local cap=count(call(C.GetPeopleCapacity,c.object,"",false))
                local n=count(call(C.GetNumAllRoles));assert(n<=64,"role capacity overflow")
                local p=ffi.new("PeopleInfo[?]",math.max(1,n))
                local actual=count(call(C.GetPeople2,p,n,c.object,true));assert(actual<=n,"role count/fill overflow")
                local counts={role_capacity=n,roles=actual,tiers=0,people=0,arrived=0,not_arrived=0,transfer_scheduled=0,crew_capacity=cap}
                for i=0,actual-1 do
                    local role=string_field(p[i].id);assert(#role>0 and not roles[role],"empty/duplicate role");roles[role]=true
                    local amount=count(p[i].amount);local tiers=count(p[i].numtiers);assert(tiers<=64,"tier capacity overflow")
                    counts.people=counts.people+amount;assert(counts.people<=8192,"people total overflow")
                    rows[#rows+1]={record="ROLE",role=role,name=string_field(p[i].name),description=string_field(p[i].desc),
                        amount=amount,tier_count=tiers,can_hire=p[i].canhire}
                    local tb=ffi.new("RoleTierData[?]",math.max(1,tiers))
                    assert(count(call(C.GetRoleTiers,tb,tiers,c.object,role))==tiers,"tier count/fill mismatch")
                    local total,skills=0,{}
                    for j=0,math.max(1,tiers)-1 do
                        local skill=tiers==0 and 0 or tonumber(tb[j].skilllevel)
                        assert(finite(skill) and skill%1==0 and skill>=-2147483648 and skill<=2147483647 and not skills[skill],"invalid/duplicate skill tier")
                        skills[skill]=true
                        local num=tiers==0 and amount or count(tb[j].amount);total=total+num
                        counts.tiers=counts.tiers+1
                        rows[#rows+1]={record="TIER",role=role,skill_level=skill,amount=num,
                            name=tiers==0 and "" or string_field(tb[j].name),implicit_zero_tier=tiers==0}
                        local nb=ffi.new("NPCSeed[?]",math.max(1,num))
                        assert(count(call(C.GetRoleTierNPCs,nb,num,c.object,role,skill))==num,"NPC count/fill mismatch")
                        for k=0,num-1 do
                            local seed=ident(nb[k]);assert(not seeds[seed],"duplicate NPCSeed across roles/tiers");seeds[seed]=true
                            local scheduled=call(C.IsPersonTransferScheduled,c.object,nb[k])
                            local arrived=call(C.HasPersonArrived,c.object,nb[k])
                            assert(type(arrived)=="boolean" and type(scheduled)=="boolean","arrival/transfer unavailable")
                            counts[arrived and "arrived" or "not_arrived"]=counts[arrived and "arrived" or "not_arrived"]+1
                            if scheduled then counts.transfer_scheduled=counts.transfer_scheduled+1 end
                            rows[#rows+1]={record="PERSON",role=role,skill_level=skill,npc_seed=seed,
                                has_arrived=arrived,transfer_scheduled=scheduled,identity_kind="NPCSeed_NOT_COMPONENT"}
                        end
                    end
                    assert(total==amount,"tier amounts do not account for role")
                end
                assert(count(call(C.GetNumAllRoles))==n,"role allocation capacity changed")
                table.sort(rows,function(a,b)
                    local ak=a.role.."|"..a.record.."|"..tostring(a.skill_level or "").."|"..(a.npc_seed or "")
                    local bk=b.role.."|"..b.record.."|"..tostring(b.skill_level or "").."|"..(b.npc_seed or "")
                    return ak<bk
                end)
                return rows,counts
            end
            c.rows,c.counts=census()
            local second,counts=census()
            assert(equal(c.rows,second) and equal(c.counts,counts),"native crew census changed")
            local obj,id_now=container(token)
            assert(obj==c.object and id_now==c.container_id and equal(stamp(),c.stamp),"container/session changed")
            c.end_time=clock();c.bytes=0
            for _,r in ipairs(c.rows) do c.bytes=c.bytes+encode(r,2500)+1 end
            assert(c.bytes<=2097152,"retained row JSON byte bound")
            cache=c;return metadata(c)
        end)
    end
    function api.metadata(id) return safe(function()return metadata(current(id)) end) end
    function api.page(id,offset)
        return safe(function()
            local c=current(id);assert(finite(offset) and offset>=0 and offset%1==0 and offset<=#c.rows,"invalid offset")
            local finish=math.min(#c.rows,offset+16);local rows={}
            for i=offset+1,finish do rows[#rows+1]=copy(c.rows[i]) end
            local out={status="OBSERVED",complete=true,capture_id=id,container_id=c.container_id,stamp=copy(c.stamp),
                offset=offset,next_offset=finish,total=#c.rows,done=finish==#c.rows,rows=rows,
                start_time=c.start_time,end_time=c.end_time,atomic=false}
            while true do
                if pcall(encode,out,3000) then assert(finish>offset or offset==#c.rows,"page cannot progress");return out end
                assert(#rows>0,"page header oversized");rows[#rows]=nil;finish=finish-1
                out.next_offset=finish;out.done=finish==#c.rows
            end
        end)
    end
    function api.capacity()
        return {available=not capacity_retired,budgets={attempts={used=capacity_retired and 'UNKNOWN' or attempts,limit=MAX_CAPTURES}},
            cache_present=cache~=nil,cached_stamp=cache and copy(cache.stamp) or 'UNKNOWN',
            cached_context='LAST_SUCCESSFUL_CAPTURE_NOT_CURRENT_READBACK'}
    end
    function api.dispose() cache=nil;attempts=MAX_CAPTURES;capacity_retired=true;return true end
    return api
end

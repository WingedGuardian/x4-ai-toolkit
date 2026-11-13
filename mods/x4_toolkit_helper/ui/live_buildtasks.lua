-- Read-only native build-task census. No submission, cancellation or queue edits.
-- See its live-buildtasks-qualification development record (in the toolkit's git repository, not in release downloads) for scope and runtime limits.
return function(d)
    local ffi,C,g=d.ffi,d.C,d.globals
    assert(ffi and C and type(g)=="table","native dependencies required")
    for _,k in ipairs({"resolve_issued","current","now","encode"}) do assert(type(d[k])=="function","missing "..k) end
    assert(type(g.IsValidComponent)=="function","native validity required")
    for _,name in ipairs({"UniverseID","BuildTaskID"}) do
        if not pcall(ffi.typeof,name) then ffi.cdef("typedef uint64_t "..name..";") end
        assert(ffi.istype("uint64_t",ffi.new(name)),"unsigned native identity required")
    end
    local fields=[[BuildTaskID id; UniverseID buildingcontainer,component;
        const char *macro,*factionid; UniverseID buildercomponent;
        int64_t price; bool ismissingresources; uint32_t queueposition;]]
    if not pcall(ffi.typeof,"BuildTaskInfo") then ffi.cdef("typedef struct {"..fields.."} BuildTaskInfo;") end
    local proof=ffi.typeof("struct {"..fields.."}")
    assert(ffi.sizeof("BuildTaskInfo")==ffi.sizeof(proof) and ffi.alignof("BuildTaskInfo")==ffi.alignof(proof),"build-task ABI size")
    local test=ffi.new("BuildTaskInfo")
    for _,k in ipairs({"id","buildingcontainer","component","macro","factionid","buildercomponent","price","ismissingresources","queueposition"}) do
        assert(ffi.offsetof("BuildTaskInfo",k)==ffi.offsetof(proof,k),"build-task ABI offset")
        if k=="macro" or k=="factionid" then assert(ffi.istype("const char *",test[k]),"build-task ABI text")
        elseif k=="price" then assert(ffi.istype("int64_t",test[k]),"build-task ABI price")
        elseif k=="ismissingresources" then assert(type(test[k])=="boolean","build-task ABI bool")
        elseif k=="queueposition" then test[k]=4294967295;assert(test[k]==4294967295,"build-task ABI uint32")
        else assert(ffi.istype("uint64_t",test[k]),"build-task ABI identity") end
    end
    ffi.cdef[[
        uint32_t GetNumBuildTasks(UniverseID containerid,UniverseID buildmoduleid,bool isinprogress,bool includeupgrade);
        uint32_t GetBuildTasks(BuildTaskInfo* result,uint32_t resultlen,UniverseID containerid,UniverseID buildmoduleid,bool isinprogress,bool includeupgrade);
        BuildTaskInfo GetBuildTaskInfo(BuildTaskID id);
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
        local s=ffi.string(v)
        assert(#s<=256 and not s:find("[%z\1-\31]"),"native text exceeds bound")
        return s -- Empty native macro/faction is retained, never replaced with a guessed label.
    end
    local function count(v)
        assert(finite(v) and v>=0 and v%1==0 and v<=4096,"native count unavailable/over bound")
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
    local function row(v,container_id,inprogress)
        local out={task_id=ident(v.id),container_id=ident(v.buildingcontainer),component_id=ident(v.component,true),
            macro=string_field(v.macro),faction=string_field(v.factionid),builder_component_id=ident(v.buildercomponent,true),
            price_native_integer=tostring(v.price):match("^(-?%d+)LL$"),price_unit="NATIVE_UNQUALIFIED",
            missing_resources=v.ismissingresources,queue_position=tonumber(v.queueposition)}
        assert(out.container_id==container_id,"foreign build container")
        assert(out.price_native_integer and type(out.missing_resources)=="boolean" and finite(out.queue_position) and
            out.queue_position>=0 and out.queue_position%1==0 and out.queue_position<=4294967295,"native task fields unreadable")
        if inprogress~=nil then out.in_progress_query=inprogress end
        return out
    end
    local function metadata(c)
        local out={status="OBSERVED",complete=true,capture_id=c.id,stamp=copy(c.stamp),container_id=c.container_id,
            start_time=c.start_time,end_time=c.end_time,atomic=false,total=#c.rows,counts=copy(c.counts),
            scope="CONTAINER_BUILD_TASKS_INCLUDE_UPGRADES",buildmodule_filter="0",include_upgrade=true,
            enumeration_calls=c.calls,retained_json_bytes=c.bytes,ttl_seconds=300,max_page_rows=16,max_payload_bytes=3000}
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
            if cache and cache.nonce==nonce then return metadata(current(cache.id)) end
            assert(not seen[nonce] and attempts<MAX_CAPTURES,"capture nonce/history refused")
            cache=nil;seen[nonce]=true;attempts=attempts+1
            local c={nonce=nonce,id="buildtasks:"..nonce,stamp=stamp(),start_time=clock(),calls=0,token=token}
            c.object,c.container_id=container(token)
            local function call(fn,...)
                c.calls=c.calls+1;assert(c.calls<=32,"native enumeration call budget");return fn(...)
            end
            local function census()
                local rows,ids,handles,counts={},{},{},{}
                for _,mode in ipairs({true,false}) do
                    local n=count(call(C.GetNumBuildTasks,c.object,0,mode,true))
                    counts[mode and "in_progress" or "other"]=n
                    if n>0 then
                        local buf=ffi.new("BuildTaskInfo[?]",n)
                        assert(count(call(C.GetBuildTasks,buf,n,c.object,0,mode,true))==n,"native count/fill mismatch")
                        for i=0,n-1 do
                            local r=row(buf[i],c.container_id,mode)
                            assert(not ids[r.task_id],"duplicate task across census");ids[r.task_id]=true
                            rows[#rows+1]=r;handles[r.task_id]=ffi.new("BuildTaskID",buf[i].id)
                        end
                    end
                    assert(count(call(C.GetNumBuildTasks,c.object,0,mode,true))==n,"native census count changed")
                end
                table.sort(rows,function(a,b)return a.task_id<b.task_id end)
                return rows,handles,counts
            end
            c.rows,c.handles,c.counts=census()
            local second,_,counts=census()
            assert(equal(c.rows,second) and equal(c.counts,counts),"native task census changed")
            local obj,ident_now=container(token)
            assert(obj==c.object and ident_now==c.container_id and equal(stamp(),c.stamp),"container/session changed")
            c.end_time=clock()
            c.bytes=0
            for _,r in ipairs(c.rows) do c.bytes=c.bytes+encode(r,4096)+1 end
            c.bytes=c.bytes+encode({stamp=c.stamp,container_id=c.container_id,counts=c.counts,
                id=c.id,nonce=c.nonce,token=c.token,start_time=c.start_time,end_time=c.end_time},8192)
            assert(c.bytes<=1048576,"retained JSON byte bound")
            -- Payload byte accounting covers retained row/header JSON, not Lua allocator overhead.
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
                if pcall(encode,out,3000) then
                    assert(finish>offset or offset==#c.rows,"page cannot make progress");return out
                end
                assert(#rows>0,"page header oversized");rows[#rows]=nil;finish=finish-1
                out.next_offset=finish;out.done=finish==#c.rows
            end
        end)
    end
    function api.task(id,task_id)
        return safe(function()
            local c=current(id)
            assert(type(task_id)=="string" and c.handles[task_id],"task not observed in this capture")
            local before=stamp();local obj,ident_now=container(c.token)
            assert(obj==c.object and ident_now==c.container_id,"container changed")
            local start=clock();local native=C.GetBuildTaskInfo(c.handles[task_id])
            local out={capture_id=id,task_id=task_id,container_id=c.container_id,stamp=before,
                start_time=start,atomic=false,completion_inferred=false}
            if ident(native.id,true)=="0" then
                out.status="NOT_OBSERVED";out.reason="NATIVE_ID_ABSENT_NOT_COMPLETION"
            else
                local actual=row(native,c.container_id)
                assert(actual.task_id==task_id,"native task identity changed")
                out.status="OBSERVED";out.row=actual
                -- The native point getter has no in-progress field; no query-mode guess.
            end
            local after_obj=container(c.token)
            assert(after_obj==c.object and equal(stamp(),before),"container/session changed during task read")
            out.end_time=clock();encode(out,3000);return out
        end)
    end
    function api.capacity()
        return {available=not capacity_retired,budgets={attempts={used=capacity_retired and 'UNKNOWN' or attempts,limit=MAX_CAPTURES}},
            cache_present=cache~=nil,cached_stamp=cache and copy(cache.stamp) or 'UNKNOWN',
            cached_context='LAST_SUCCESSFUL_CAPTURE_NOT_CURRENT_READBACK'}
    end
    function api.dispose() cache=nil;attempts=MAX_CAPTURES;capacity_retired=true end
    return api
end

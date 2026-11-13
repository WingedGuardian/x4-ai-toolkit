-- Read-only player state from native trader APIs; no grant/payment/learn calls.
-- Source and limits: docs/live-playerstate-qualification.md.
return function(d)
    local ffi,C,g=d.ffi,d.C,d.globals
    assert(ffi and C and type(g)=="table","native state dependencies")
    for _,k in ipairs({"resolve_issued","current","now","encode"}) do assert(type(d[k])=="function","missing "..k) end
    for _,k in ipairs({"IsValidComponent","GetComponentData","GetPlayerMoney","GetOwnLicences","HasLicence"}) do
        assert(type(g[k])=="function","missing "..k)
    end
    if not pcall(ffi.typeof,"UIBlueprint") then
        ffi.cdef("typedef struct {const char *macro,*ware,*productionmethodid;} UIBlueprint;")
    end
    local proof=ffi.typeof("struct {const char *macro,*ware,*productionmethodid;}")
    assert(ffi.sizeof("UIBlueprint")==ffi.sizeof(proof) and ffi.alignof("UIBlueprint")==ffi.alignof(proof),"blueprint ABI size")
    local probe=ffi.new("UIBlueprint")
    for _,k in ipairs({"macro","ware","productionmethodid"}) do
        assert(ffi.offsetof("UIBlueprint",k)==ffi.offsetof(proof,k) and ffi.istype("const char *",probe[k]),"blueprint ABI field")
    end
    ffi.cdef[[
        uint32_t GetNumBlueprints(const char* set,const char* category,const char* macroname);
        uint32_t GetBlueprints(UIBlueprint* result,uint32_t resultlen,const char* set,const char* category,const char* macroname);
    ]]
    local api,cache,seen,captures,lastclock={ },nil,{},0,nil
    local MAX_CAPTURES,capacity_retired=256,false
    local MAXBLUE,MAXLIC,PAGE,TTL=8192,512,16,300
    local function finite(n) return type(n)=="number" and n==n and math.abs(n)<math.huge end
    local function clock()
        local n=d.now()
        assert(finite(n) and n>=0 and (not lastclock or n>=lastclock),"clock unavailable or backward")
        lastclock=n;return n
    end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function text(v)
        assert(type(v)=="string" and #v>0 and #v<=256 and not v:find("[%z\1-\31]"),"invalid native identifier")
        return v
    end
    local function stamp()
        local s=d.current()
        local fields={provider=true,revision=true,session=true,generation=true,world_token=true};local count=0
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical state unavailable")
        for k in pairs(s) do assert(fields[k],"unexpected canonical field");count=count+1 end
        assert(count==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and
            hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string" and
            #s.world_token>0 and #s.world_token<=4096,"canonical state malformed")
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,world_token=s.world_token}
    end
    local function copy(v)
        if type(v)~="table" then return v end
        local out={};for k,x in pairs(v) do out[k]=copy(x) end;return out
    end
    local function same(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not same(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function count(n)
        assert(finite(n) and n>=0 and n%1==0 and n<=4294967295,"native count unreadable")
        return n
    end
    local function sequence(t,max)
        assert(type(t)=="table" and getmetatable(t)==nil,"native list unreadable")
        local n=#t;assert(n<=max,"native list overflow")
        for k in pairs(t) do assert(type(k)=="number" and k>=1 and k<=n and k%1==0,"native list sparse") end
        for i=1,n do assert(t[i]~=nil,"native list sparse") end
        return n
    end
    local function exact_id(v)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"exact component required")
        local s=tostring(v):match("^(%d+)ULL$")
        assert(s and s~="0","component absent");return s
    end
    local function error_text(e) return tostring(e):sub(1,512) end
    local function refused(e) return {status="REFUSED",reason=error_text(e),complete=false} end
    local function encoded(v,cap)
        local raw=d.encode(v)
        assert(type(raw)=="string" and #raw<=cap,"encoded snapshot/page byte bound")
        return #raw
    end
    local function cache_bytes(c)
        -- Encode bounded pieces: the wire encoder limits nodes per invocation.
        -- Empty object/array spellings both occupy two bytes.
        local header={}
        for k,v in pairs(c) do
            if k~="licences" and k~="blueprints" then header[k]=copy(v) end
        end
        local extra=0
        for _,kind in ipairs({"licences","blueprints"}) do
            header[kind]={info=copy(c[kind].info),rows={}}
            for _,row in ipairs(c[kind].rows) do extra=extra+encoded(row,4096) end
            extra=extra+math.max(0,#c[kind].rows-1)
        end
        local n=encoded(header,524288)+extra
        assert(n<=524288,"retained snapshot byte bound")
        return n
    end
    local function money_value(v)
        if type(v)=="number" then
            if not finite(v) or math.abs(v)>9007199254740991 or v%1~=0 then
                return {status="UNKNOWN",reason="UNSAFE_OR_FRACTIONAL_LUA_NUMBER_NO_INTEGER_GUESS",unit="credits"}
            end
            return {status="OBSERVED",credits=(v==0 and "0" or string.format("%.0f",v)),unit="credits",representation="EXACT_SAFE_LUA_INTEGER"}
        end
        if type(v)=="cdata" and (ffi.istype("int64_t",v) or ffi.istype("uint64_t",v)) then
            local s=tostring(v):match("^(-?%d+)ULL$") or tostring(v):match("^(-?%d+)LL$")
            assert(s,"native money integer unreadable")
            return {status="OBSERVED",credits=s,unit="credits",representation="NATIVE_64BIT_INTEGER"}
        end
        return {status="UNKNOWN",reason="UNQUALIFIED_MONEY_TYPE",unit="credits"}
    end
    local function valid_cache(id)
        assert(cache and cache.id==id,"unknown/superseded capture")
        assert(same(stamp(),cache.stamp),"canonical session changed")
        assert(clock()-cache.end_time<=TTL,"capture expired")
        return cache
    end
    local function metadata(c)
        local out={status=c.complete and "OBSERVED" or "PARTIAL",capture_id=c.id,peer_id=c.peer_id,
            faction=c.faction,stamp=copy(c.stamp),start_time=c.start_time,end_time=c.end_time,
            money=copy(c.money),licences=copy(c.licences.info),blueprints=copy(c.blueprints.info),
            complete=c.complete,atomic=false,native_calls=c.calls,retained_json_bytes=c.retained_bytes,
            scope="PLAYER_MONEY_PEER_FACTION_LICENCE_CATALOG_AND_OWNED_BLUEPRINT_WARES",
            pagination={max_page_rows=PAGE,max_payload_bytes=3000,ttl_seconds=TTL}}
        encoded(out,3000)
        return out
    end
    function api.capture(peer_token,nonce)
        local ok,result=pcall(function()
            assert(hex(nonce,32),"caller capture nonce required")
            if cache and cache.nonce==nonce then return metadata(valid_cache(cache.id)) end
            assert(not seen[nonce] and captures<MAX_CAPTURES,"reused capture nonce/history full")
            cache=nil -- new eligible attempt supersedes old capture, including partial failures
            captures=captures+1;seen[nonce]=true
            local c={nonce=nonce,id="playerstate:"..nonce,stamp=stamp(),start_time=clock(),calls=0}
            local function call(fn,...)
                c.calls=c.calls+1;assert(c.calls<=2048,"native call budget")
                return fn(...)
            end
            assert(type(peer_token)=="string" and #peer_token>0 and #peer_token<=128,"issued peer required")
            local peer=call(d.resolve_issued,peer_token,"object")
            c.peer_id=exact_id(peer)
            assert(call(g.IsValidComponent,peer)==true,"issued peer invalid")
            c.faction=text(call(g.GetComponentData,peer,"owner"))
            local mok,m=pcall(function() return money_value(call(g.GetPlayerMoney)) end)
            c.money=mok and m or {status="UNKNOWN",reason=error_text(m),unit="credits"}
            local function licence_rows()
                local raw=call(g.GetOwnLicences,c.faction)
                local n=sequence(raw,MAXLIC)
                local rows,ids={},{}
                for i=1,n do
                    local row=raw[i]
                    assert(type(row)=="table","licence row unreadable")
                    local ident,typ=text(row.id),text(row.type)
                    assert(not ids[ident],"duplicate licence catalogue identity");ids[ident]=true
                    local held=call(g.HasLicence,"player",typ,c.faction)
                    assert(type(held)=="boolean","licence possession unknown")
                    rows[#rows+1]={id=ident,type=typ,held=held}
                end
                table.sort(rows,function(a,b)return a.id<b.id end)
                return rows
            end
            c.licences={rows={},info={status="UNKNOWN",complete=false,scope="PEER_FACTION_NATIVE_CATALOG"}}
            local lok,lerr=pcall(function()
                local rows=licence_rows()
                c.licences.info.first_count=#rows
                assert(same(rows,licence_rows()),"licence catalogue/possession changed")
                c.licences.rows=rows
                c.licences.info={status="OBSERVED",complete=true,count=#rows,scope="PEER_FACTION_NATIVE_CATALOG"}
            end)
            if not lok then c.licences.info.reason=error_text(lerr) end
            c.blueprints={rows={},info={status="UNKNOWN",complete=false,scope="NATIVE_OWNED_BLUEPRINT_WARES"}}
            local function blueprints()
                local n=count(call(C.GetNumBlueprints,"","",""))
                c.blueprints.info.reported_native_rows=n
                assert(n<=MAXBLUE,"blueprint native count exceeds bound")
                local map={}
                if n>0 then
                    local buf=ffi.new("UIBlueprint[?]",n)
                    local got=count(call(C.GetBlueprints,buf,n,"","",""))
                    assert(got==n,"blueprint count/fill mismatch")
                    for i=0,n-1 do
                        assert(buf[i].ware~=nil and buf[i].ware~=ffi.NULL,"blueprint ware unreadable")
                        local ware=text(ffi.string(buf[i].ware))
                        map[ware]=(map[ware] or 0)+1
                    end
                end
                assert(count(call(C.GetNumBlueprints,"","",""))==n,"blueprint count changed")
                local rows={}
                for ware,occurrences in pairs(map) do rows[#rows+1]={ware=ware,native_occurrences=occurrences} end
                table.sort(rows,function(a,b)return a.ware<b.ware end)
                return rows,n
            end
            local bok,berr=pcall(function()
                local rows,n=blueprints();local second,n2=blueprints()
                assert(n==n2 and same(rows,second),"blueprint identities changed at stable count")
                c.blueprints.rows=rows
                c.blueprints.info={status="OBSERVED",complete=true,reported_native_rows=n,
                    unique_wares=#rows,count=#rows,scope="NATIVE_OWNED_BLUEPRINT_WARES",
                    negative_scope="NOT_IN_COMPLETE_OWNED_CENSUS; NOT_WARE_VALIDITY_OR_PURCHASE_ELIGIBILITY"}
            end)
            if not bok then c.blueprints.info.reason=error_text(berr) end
            if c.money.status=="OBSERVED" then
                local lastok,last=pcall(function() return money_value(call(g.GetPlayerMoney)) end)
                if not lastok or not same(last,c.money) then
                    c.money={status="UNKNOWN",reason="MONEY_CHANGED_OR_UNREADABLE_DURING_CAPTURE",unit="credits"}
                end
            end
            assert(call(d.resolve_issued,peer_token,"object")==peer and call(g.IsValidComponent,peer)==true and
                call(g.GetComponentData,peer,"owner")==c.faction,"peer identity/faction changed")
            assert(same(stamp(),c.stamp),"canonical session changed during capture")
            c.end_time=clock()
            c.complete=c.money.status=="OBSERVED" and c.licences.info.complete and c.blueprints.info.complete
            c.retained_bytes=0
            for _=1,3 do c.retained_bytes=cache_bytes(c) end
            assert(cache_bytes(c)==c.retained_bytes,"retained byte accounting did not converge")
            cache=c
            return metadata(c)
        end)
        return ok and result or refused(result)
    end
    function api.metadata(id)
        local ok,result=pcall(function()return metadata(valid_cache(id)) end)
        return ok and result or refused(result)
    end
    function api.page(id,kind,offset)
        local ok,result=pcall(function()
            local c=valid_cache(id)
            assert(kind=="licences" or kind=="blueprints","unknown census kind")
            assert(type(offset)=="number" and offset>=0 and offset%1==0,"invalid page offset")
            local part=c[kind]
            assert(offset<=#part.rows,"offset beyond captured rows")
            local rows={};local finish=math.min(#part.rows,offset+PAGE)
            for i=offset+1,finish do rows[#rows+1]=copy(part.rows[i]) end
            local out={status=part.info.status,capture_id=id,kind=kind,offset=offset,next_offset=finish,
                total=#part.rows,done=finish==#part.rows,rows=rows,complete=part.info.complete,
                coverage=copy(part.info),stamp=copy(c.stamp),peer_id=c.peer_id,faction=c.faction,
                start_time=c.start_time,end_time=c.end_time,atomic=false}
            while true do
                local fits=pcall(encoded,out,3000)
                if fits then
                    assert(finish>offset or offset==#part.rows,"page made no progress")
                    return out
                end
                assert(#rows>0,"page header exceeds wire budget")
                rows[#rows]=nil;finish=finish-1
                out.next_offset=finish;out.done=finish==#part.rows
            end
        end)
        return ok and result or refused(result)
    end
    function api.capacity()
        return {available=not capacity_retired,budgets={attempts={used=capacity_retired and 'UNKNOWN' or captures,limit=MAX_CAPTURES}},
            cache_present=cache~=nil,cached_stamp=cache and copy(cache.stamp) or 'UNKNOWN',
            cached_context='LAST_SUCCESSFUL_CAPTURE_NOT_CURRENT_READBACK'}
    end
    function api.dispose() cache=nil;captures=MAX_CAPTURES;capacity_retired=true end
    return api
end

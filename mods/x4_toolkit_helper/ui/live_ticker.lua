-- Read-only native ticker cache window; no clearing or acknowledgement.
-- Source: menu_playerinfo.lua12,114-120,220,248,2828-2906.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C and type(d.current)=="function"
        and type(d.now)=="function" and type(d.encode)=="function","ticker dependencies")
    local ffi,C=d.ffi,d.C
    if not pcall(ffi.typeof,"TickerCacheID") then ffi.cdef("typedef uint64_t TickerCacheID;") end
    assert(ffi.istype("uint64_t",ffi.new("TickerCacheID")),"ticker unsigned identity ABI")
    local fields="TickerCacheID id;double time;const char *category,*title,*text;"
    if not pcall(ffi.typeof,"TickerCacheEntry") then ffi.cdef("typedef struct {"..fields.."} TickerCacheEntry;") end
    local proof=ffi.typeof("struct {"..fields.."}");local item,expected=ffi.new("TickerCacheEntry"),ffi.new(proof)
    assert(ffi.sizeof("TickerCacheEntry")==ffi.sizeof(proof) and ffi.alignof("TickerCacheEntry")==ffi.alignof(proof),"ticker ABI size")
    for _,field in ipairs({"id","time","category","title","text"}) do
        assert(ffi.offsetof("TickerCacheEntry",field)==ffi.offsetof(proof,field),"ticker ABI offset")
        if field=="id" then assert(ffi.istype("uint64_t",item.id),"ticker ID ABI")
        elseif field=="time" then item.time=16777217.125;expected.time=16777217.125
        else assert(ffi.istype("const char *",item[field]),"ticker text ABI") end
    end
    assert(ffi.string(ffi.cast("char*",item),ffi.sizeof(item))==ffi.string(ffi.cast("char*",expected),ffi.sizeof(expected)),"ticker time representation ABI")
    ffi.cdef[[
        uint32_t GetNumTickerCache(const char* categoryname);
        uint32_t GetTickerCache(TickerCacheEntry* result,uint32_t resultlen,size_t start,size_t count,const char* categoryname);
    ]]
    local api,disposed,lastclock={},false,nil
    local function hex(v,n)return type(v)=="string" and #v==n and not v:find("[^0-9a-f]")end
    local function finite(v)return type(v)=="number" and v==v and math.abs(v)<math.huge end
    local function integer(v,max)return finite(v) and v>=0 and v%1==0 and v<=max end
    local function clock()
        local v=d.now();assert(finite(v) and v>=0 and (not lastclock or v>=lastclock),"ticker observer clock unavailable/backward")
        lastclock=v;return v
    end
    local function stamp()
        assert(not disposed,"ticker observer disposed")
        local s=d.current();assert(type(s)=="table" and getmetatable(s)==nil,"canonical stamp unavailable")
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true};local n=0
        for k in pairs(s)do assert(keys[k],"unexpected stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and hex(s.session,32)
            and hex(s.generation,32) and type(s.world_token)=="string" and #s.world_token>0
            and #s.world_token<=4096,"incomplete public stamp")
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,world_token=s.world_token}
    end
    local function equal(a,b)
        if type(a)~=type(b)then return false end
        if type(a)~="table"then return a==b end
        for k,v in pairs(a)do if not equal(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end
        return true
    end
    local function text(v,cap)
        assert(v~=nil and v~=ffi.NULL,"ticker native string unavailable")
        local n=0;while n<=cap and v[n]~=0 do n=n+1 end
        assert(n<=cap,"ticker native string over bound/unterminated")
        return ffi.string(v,n)
    end
    local function rows(n,start)
        local buf=ffi.new("TickerCacheEntry[?]",n)
        local written=C.GetTickerCache(buf,n,start,n,"")
        assert(integer(written,16) and written==n,"ticker native count/fill mismatch")
        local out={}
        for i=0,n-1 do
            local row=buf[i]
            assert(ffi.istype("uint64_t",row.id),"ticker ID unavailable")
            local id=tostring(row.id):match("^(%d+)ULL$");assert(id,"ticker ID not exact")
            local time=tonumber(row.time);assert(finite(time) and time>=0,"ticker timestamp unavailable")
            out[i+1]={index=start+i,ticker_cache_id=id,time=time,category=text(row.category,256),
                title=text(row.title,4096),text=text(row.text,16384)}
        end
        return out
    end
    local function observe(start,limit)
        assert(integer(start,1048577) and start>=1 and integer(limit,16) and limit>=1,"bounded one-based ticker window required")
        local before=stamp();local began=clock()
        local total=C.GetNumTickerCache("")
        assert(integer(total,1048576),"ticker native total unavailable/overflow")
        assert(start<=total+1,"window begins beyond ticker cache")
        local n=math.min(limit,total-start+1)
        local first=n>0 and rows(n,start) or {}
        local second=n>0 and rows(n,start) or {}
        local total_after=C.GetNumTickerCache("")
        assert(integer(total_after,1048576) and total_after==total and equal(first,second),"native ticker window changed")
        local after=stamp();local ended=clock();assert(equal(before,after),"public identity changed")
        local out={status="OBSERVED",scope="NATIVE_TICKER_CACHE_WINDOW",stamp=before,
            start_time=began,end_time=ended,observer_clock="INJECTED_UI_CLOCK",entry_time_domain="NATIVE_UNQUALIFIED",
            atomic=false,category_filter="",total_before=total,total_after=total_after,
            start_index=start,requested_count=limit,returned_count=n,rows=first,
            covers_total=(start==1 and n==total),complete_window=true,
            next_index=(start+n<=total and start+n or 0),
            stability="TWO_EQUAL_NATIVE_ROWS_WITH_EQUAL_TOTALS",
            observed_fields={"id","time","category","title","text"},
            outside_cache_coverage=false,rendered_notification_verified=false}
        local raw=d.encode(out);assert(type(raw)=="string" and #raw<=30000,"ticker window exceeds encoded bound; request fewer rows")
        return out
    end
    function api.read(start,limit)
        local ok,out=pcall(observe,start,limit)
        if ok then return out end
        return {status="UNAVAILABLE",scope="NATIVE_TICKER_CACHE_WINDOW",complete_window=false,reason=tostring(out):sub(1,1024)}
    end
    function api.dispose()disposed=true;return true end
    return api
end

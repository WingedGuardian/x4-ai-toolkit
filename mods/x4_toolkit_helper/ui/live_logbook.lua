-- Read-only native logbook window. No menu opening, clearing or acknowledgement.
-- Analogue: menu_playerinfo.lua2830/2856 and menu_map.lua16802-16816.
return function(d)
    assert(type(d)=="table" and type(d.globals)=="table"
        and type(d.current)=="function" and type(d.now)=="function"
        and type(d.encode)=="function","logbook dependencies")
    local api,disposed={},false
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function finite(v) return type(v)=="number" and v==v and math.abs(v)<math.huge end
    local function integer(v,max) return finite(v) and v>=0 and v%1==0 and v<=max end
    local function stamp()
        assert(not disposed,"logbook observer disposed")
        local s=d.current()
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical stamp unavailable")
        local expected={provider=true,revision=true,session=true,generation=true,world_token=true}
        local n=0;for k in pairs(s) do assert(expected[k],"unexpected stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string"
            and #s.world_token>0 and #s.world_token<=4096,"incomplete public stamp")
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,
            world_token=s.world_token}
    end
    local function text(v,limit)
        assert(type(v)=="string" and #v<=limit and not v:find("%z"),"logbook text unavailable or too large")
        return v
    end
    local function rows(raw,n,start)
        assert(type(raw)=="table" and getmetatable(raw)==nil,"native logbook is not a list")
        local keys=0
        for k in pairs(raw) do
            assert(integer(k,n) and k>=1,"native logbook list sparse/extra");keys=keys+1
        end
        assert(keys==n,"native logbook count mismatch")
        local out={}
        for i=1,n do
            local item=raw[i]
            assert(type(item)=="table" and getmetatable(item)==nil,"native logbook row unavailable")
            assert(finite(item.time) and item.time>=0,"native logbook time unavailable")
            out[i]={index=start+i-1,time=item.time,title=text(item.title,4096),
                text=text(item.text,16384),category=text(item.category,256),
                entityname=text(item.entityname,2048),factionname=text(item.factionname,2048)}
        end
        return out
    end
    local function equal(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function observe(start,limit)
        assert(integer(start,1048576) and start>=1 and integer(limit,16) and limit>=1,"bounded one-based logbook window required")
        local before=stamp();local began=d.now()
        assert(finite(began) and began>=0,"game time unavailable")
        local g=d.globals
        assert(type(g.GetNumLogbook)=="function" and type(g.GetLogbook)=="function","native logbook API unavailable")
        local total=g.GetNumLogbook("all")
        assert(integer(total,1048576),"native logbook total unavailable/overflow")
        assert(start<=total+1,"window begins beyond logbook")
        local n=math.min(limit,total-start+1)
        -- Never coalesce nil to empty. Zero is supported by two native totals.
        local first=n>0 and rows(g.GetLogbook(start,n,"all"),n,start) or {}
        local second=n>0 and rows(g.GetLogbook(start,n,"all"),n,start) or {}
        local total_after=g.GetNumLogbook("all")
        assert(total_after==total and equal(first,second),"native logbook changed during capture")
        local after=stamp();local ended=d.now()
        assert(equal(before,after),"public identity changed during logbook observation")
        assert(finite(ended) and ended>=began,"game time reversed/unavailable")
        local result={status="OBSERVED",scope="NATIVE_ALL_LOGBOOK_WINDOW",stamp=before,
            start_time=began,end_time=ended,atomic=false,category="all",total_before=total,total_after=total_after,
            start_index=start,requested_count=limit,returned_count=n,rows=first,
            covers_total=(start==1 and n==total),complete_window=true,
            next_index=(start+n<=total and start+n or 0),stability="TWO_EQUAL_PROJECTED_READS_WITH_EQUAL_TOTALS",
            observed_fields={"time","title","text","category","entityname","factionname"},
            omitted_fields={"money","bonus"},notifications_included=false}
        local encoded=d.encode(result)
        assert(type(encoded)=="string" and #encoded<=30000,"logbook window exceeds wire budget; request fewer rows")
        return result
    end
    function api.read(start,limit)
        local ok,result=pcall(observe,start,limit)
        if ok then return result end
        return {status="UNAVAILABLE",scope="NATIVE_ALL_LOGBOOK_WINDOW",complete_window=false,
            reason=tostring(result):sub(1,1024)}
    end
    function api.dispose() disposed=true;return true end
    return api
end

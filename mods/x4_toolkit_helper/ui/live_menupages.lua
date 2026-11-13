-- Read-only immutable pages of the unchanged live_menu.observe() result.
-- Table encoding preserves string/integer keys and empty tables; no guessed arrays.
return function(env)
    assert(type(env)=="table" and type(env.observe)=="function","menu observer required")
    local api, active, seen, captures = {},nil,{},0
    local MAX_CAPTURES=128
    local REVISION="menu-observation-pages-v1"
    local CHUNK,MAX_BYTES,MAX_NODES,MAX_STRINGS=1000,1048576,12000,131072
    local names={"class","ship","loadout","add","confirm"}
    local function finite(v)
        return type(v)=="number" and v==v and math.abs(v)<math.huge
    end
    local function integer(v,max)
        return finite(v) and v>=0 and v%1==0 and v<=max
    end
    local function nonce(s)
        return type(s)=="string" and #s==32 and not s:find("[^0-9a-f]")
    end
    local function quote(s)
        return '"'..s:gsub('[%z\1-\31\\"]',function(c)
            if c=='"' then return '\\"' end
            if c=='\\' then return '\\\\' end
            return string.format("\\u%04x",c:byte())
        end)..'"'
    end
    local function canonical(value)
        local budget={nodes=0,strings=0};local ancestors={}
        local function visit(v)
            budget.nodes=budget.nodes+1
            assert(budget.nodes<=MAX_NODES,"menu node limit")
            local kind=type(v)
            if kind=="string" then
                assert(#v<=4096,"menu string limit")
                budget.strings=budget.strings+#v
                assert(budget.strings<=MAX_STRINGS,"menu string byte limit")
                return quote(v)
            elseif kind=="boolean" then return v and "true" or "false"
            elseif kind=="number" then
                assert(finite(v) and math.abs(v)<=9007199254740991,"inexact menu number")
                return string.format("%.17g",v)
            end
            assert(kind=="table" and getmetatable(v)==nil and not ancestors[v],"unsupported menu value")
            ancestors[v]=true
            local keys={}
            for k in pairs(v) do
                assert(type(k)=="string" or
                    (finite(k) and k%1==0 and math.abs(k)<=9007199254740991),"invalid menu key")
                keys[#keys+1]=k
            end
            table.sort(keys,function(a,b)
                if type(a)~=type(b) then return type(a)=="number" end
                return a<b
            end)
            local entries={}
            for _,k in ipairs(keys) do
                local encodedkey=visit(k)
                entries[#entries+1]='['..quote(type(k)=="number" and "n" or "s")..','..encodedkey..','..visit(v[k])..']'
            end
            ancestors[v]=nil
            return '{"$table":['..table.concat(entries,",")..']}'
        end
        -- Native model depth is independently bounded; this outer observation adds wrappers.
        local depthnodes=0
        local function depth(v,n,path)
            depthnodes=depthnodes+1
            assert(depthnodes<=MAX_NODES,"menu node limit")
            assert(n<=20,"menu depth limit")
            if type(v)~="table" then return end
            assert(not path[v],"cyclic menu value");path[v]=true
            for _,x in pairs(v) do depth(x,n+1,path) end
            path[v]=nil
        end
        depth(value,0,{})
        local result=visit(value)
        assert(#result<=MAX_BYTES,"encoded menu byte limit")
        return result,budget.nodes,budget.strings
    end
    local function counts(o)
        assert(type(o)=="table" and o.status=="OBSERVED" and
            type(o.model)=="table" and type(o.widgets)=="table" and
            type(o.epoch)=="string" and o.epoch~="" and finite(o.age) and o.age>=0,
            "menu observation unavailable")
        local total=0
        for k in pairs(o.widgets) do
            local known=false
            for _,name in ipairs(names) do if k==name then known=true end end
            assert(known,"unknown widget observation");total=total+1
        end
        assert(total==5,"missing widget observation")
        local complete,options=true,{}
        for i,name in ipairs(names) do
            local w=o.widgets[name]
            assert(type(w)=="table" and
                (w.status=="OBSERVED" or w.status=="UNKNOWN" or w.status=="UNAVAILABLE"),
                "invalid widget observation")
            if w.status=="OBSERVED" then assert(type(w.model)=="table","missing widget model")
            else complete=false end
            if i<=3 then
                if w.status=="OBSERVED" then
                    local rows=w.model.options
                    assert(type(rows)=="table" and #rows<=2048,"missing option observation")
                    local n=0
                    for k in pairs(rows) do assert(integer(k,#rows) and k>=1,"sparse option observation");n=n+1 end
                    assert(n==#rows,"sparse option observation")
                    options[name]=n
                else options[name]="UNKNOWN" end
            end
        end
        return complete,options
    end
    local function read()
        local observation=env.observe()
        local complete,options=counts(observation)
        local full,nodes,strings=canonical(observation)
        local age=observation.age
        local stable={}
        for k,v in pairs(observation) do if k~="age" then stable[k]=v end end
        local identity=canonical(stable)
        return {bytes=full,nodes=nodes,strings=strings,identity=identity,
            age=age,complete=complete,options=options}
    end
    local function check(r)
        local now=read()
        assert(now.identity==r.identity and now.age>=r.last_age,"menu model or clock changed")
        r.last_age=now.age
    end
    local function common(r,status)
        return {version=1,revision=REVISION,status=status,capture=r.capture,
            total_bytes=#r.bytes,total_nodes=r.nodes,total_string_bytes=r.strings,
            pages=math.ceil(#r.bytes/CHUNK),chunk_bytes=CHUNK,captured_age=r.age,
            read_complete=r.complete,
            option_counts={class=r.options.class,ship=r.options.ship,loadout=r.options.loadout}}
    end
    local function refused(reason)
        return {status="REFUSED",reason=tostring(reason),capture_complete=false}
    end
    function api.capture(id)
        local ok,result=pcall(function()
            assert(nonce(id) and not seen[id],"fresh capture nonce required")
            assert(captures<MAX_CAPTURES,"capture history full")
            captures=captures+1;seen[id]=true;active=nil
            local r=read()
            r.capture=id;r.last_age=r.age
            check(r)
            active=r
            return common(r,"CAPTURED")
        end)
        if not ok then return refused(result) end
        return result
    end
    function api.page(id,index)
        local ok,result=pcall(function()
            assert(active and active.capture==id,"capture absent or superseded")
            local r=active
            local pages=math.ceil(#r.bytes/CHUNK)
            assert(integer(index,pages-1),"page index outside capture")
            check(r)
            local offset=index*CHUNK
            local bytes=r.bytes:sub(offset+1,offset+CHUNK)
            local hex=bytes:gsub(".",function(c) return string.format("%02x",c:byte()) end)
            local out=common(r,"PAGE")
            out.index=index;out.offset=offset;out.length=#bytes;out.hex=hex
            check(r)
            return out
        end)
        if not ok then active=nil;return refused(result) end
        return result
    end
    function api.capacity()
        return {available=true,budgets={attempts={used=captures,limit=MAX_CAPTURES}},
            cache_present=active~=nil,cached_context='OPAQUE_MODEL_FINGERPRINT_NOT_PUBLIC_STAMP',
            lifecycle_gate='OWNING_ASSEMBLY'}
    end
    return api
end

-- Fixed native persistence gate. Only the qualified host dispatcher may expose it.
-- Correlation records are not cryptographic proof of a human or filesystem state.
return function(d)
    assert(type(d)=="table" and type(d.native)=="function" and type(d.current)=="function","trusted dependencies required")
    local api,reservations,grants,settlements,loads={},{},{},{},{}
    local native,disposed=false,false
    local function hex(s,n) return type(s)=="string" and #s==n and not s:find("[^0-9a-f]") end
    local function same(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not same(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function exact(a,keys)
        assert(type(a)=="table" and not getmetatable(a),"argument object required")
        local n,w=0,0
        for k in pairs(a) do assert(keys[k],"unexpected field");n=n+1 end
        for k in pairs(keys) do assert(a[k]~=nil,"missing field");w=w+1 end
        assert(n==w,"field count")
    end
    local function current()
        assert(not disposed,"gate disposed")
        return d.current()
    end
    local function checkcontext(c,r)
        assert(r and c.ticket==r.ticket and c.slot==r.slot and c.label==r.label and same(c.stamp,r.stamp),
            "host/native context mismatch")
    end
    local function authorization(c,mode)
        local r=grants[c.ticket]
        assert(r and not r.used and r.operation==mode,"missing/consumed host authorization")
        assert(same(c.stamp,r.stamp),"authorization session changed")
        r.used=true
        return r
    end
    local deps={}
    for k,v in pairs(d) do deps[k]=v end
    deps.current=current
    deps.reserve_save=function(c)
        local r=reservations[c.ticket];checkcontext(c,r)
        assert(not r.claimed,"reservation already claimed");r.claimed=true
        return r.token
    end
    deps.authorize_save=function(c,token)
        local r=reservations[c.ticket];checkcontext(c,r);assert(token==r.token,"foreign reservation")
        authorization(c,"SAVE");return true
    end
    deps.settled_save=function(c,token,row)
        local r=reservations[c.ticket];checkcontext(c,r);assert(token==r.token,"foreign reservation")
        local s=settlements[c.ticket]
        if not s then return nil end
        assert(row.filename==s.slot and row.name==s.label and same(s.stamp,c.stamp),"native settled identity differs")
        return s
    end
    deps.authorize_load=function(c,receipt)
        local r=authorization(c,"LOAD")
        assert(receipt.ticket==loads[c.ticket] and receipt.slot==c.slot and receipt.label==c.label and
            same(receipt.stamp,c.stamp),"load receipt mismatch")
        r.token={};r.receipt=receipt;return r.token
    end
    deps.verify_load=function(c,token,receipt)
        local r=grants[c.ticket]
        assert(r and r.operation=="LOAD" and r.used and not r.verified and r.token==token and
            same(r.receipt,receipt) and same(c.stamp,r.stamp),"load grant mismatch/replay")
        r.verified=true;return true
    end
    native=d.native(deps)
    function api.invoke(operation,a)
        local stamp=current()
        if operation=="refresh" then
            exact(a,{})
            return native.refresh()
        elseif operation=="observe" then
            exact(a,{})
            return native.observe()
        elseif operation=="prepare_save" then
            exact(a,{ticket=true,slot=true,reservation_sha256=true})
            assert(hex(a.ticket,32) and hex(a.reservation_sha256,64) and
                type(a.slot)=="string" and a.slot:match("^save_%d%d%d$") and not reservations[a.ticket],
                "invalid/repeated host reservation")
            local n=0;for _ in pairs(reservations) do n=n+1 end;assert(n<64,"reservation capacity")
            reservations[a.ticket]={ticket=a.ticket,slot=a.slot,label="x4live-"..a.ticket,stamp=stamp,
                source=a.reservation_sha256,token={}}
            return native.prepare_save(a.ticket,a.slot)
        elseif operation=="execute_save" or operation=="execute_load" then
            exact(a,{ticket=true,capability=true,context_sha256=true,mode=true,guard_qualified=true})
            assert(hex(a.ticket,32) and hex(a.capability,32) and hex(a.context_sha256,64) and not grants[a.ticket],
                "invalid/replayed grant")
            assert((a.mode=="CANONICAL_GUARD" and a.guard_qualified==true) or
                (a.mode=="EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION" and a.guard_qualified==false),
                "authority mode mismatch")
            for _,r in pairs(grants) do assert(r.capability~=a.capability,"capability reused") end
            local n=0;for _ in pairs(grants) do n=n+1 end;assert(n<128,"grant capacity")
            grants[a.ticket]={stamp=stamp,operation=operation=="execute_save" and "SAVE" or "LOAD",
                capability=a.capability,context_sha256=a.context_sha256,mode=a.mode,used=false}
            if operation=="execute_save" then return native.execute_save(a.ticket) end
            return native.execute_load(a.ticket)
        elseif operation=="confirm_settlement" then
            exact(a,{ticket=true,slot=true,label=true,path=true,sha256=true,size=true,stamp=true})
            local r=reservations[a.ticket];checkcontext(a,r)
            assert(not settlements[a.ticket] and same(stamp,a.stamp) and hex(a.sha256,64) and
                type(a.path)=="string" and #a.path>0 and #a.path<=4096 and type(a.size)=="number" and
                a.size>0 and a.size<=9007199254740991 and a.size%1==0,"settlement unavailable/repeated")
            local s={};for k,v in pairs(a) do s[k]=v end
            settlements[a.ticket]=s
            return native.poll_save(a.ticket)
        elseif operation=="prepare_load" then
            exact(a,{ticket=true,save_ticket=true})
            assert(hex(a.ticket,32) and hex(a.save_ticket,32) and not loads[a.ticket] and settlements[a.save_ticket],
                "unknown/repeated own load")
            loads[a.ticket]=a.save_ticket
            return native.prepare_load(a.ticket,a.save_ticket)
        elseif operation=="status" then
            exact(a,{ticket=true});assert(hex(a.ticket,32),"ticket required")
            return native.status(a.ticket)
        end
        error("unsupported persistence operation")
    end
    function api.dispose()
        disposed=true
        return native.dispose()
    end
    return api
end

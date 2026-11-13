-- Experimental native save/load boundary; no native calls at import.
-- Host gate functions are trusted integration capabilities, NOT caller JSON.
return function(d)
    local g,C,H=d.globals,d.C,d.Helper
    assert(type(g)=="table" and C and type(H)=="table","native dependencies required")
    for _,k in ipairs({"GetSaveList","IsSavingPossible","OnlineIsOnlineModeActive","SaveGame","LoadGame","getElapsedTime"}) do
        assert(type(g[k])=="function","missing native "..k)
    end
    for _,k in ipairs({"current","now","reserve_save","authorize_save","settled_save","authorize_load","verify_load"}) do
        assert(type(d[k])=="function","missing trusted dependency "..k)
    end
    assert(type(H.addDelayedOneTimeCallbackOnUpdate)=="function","native delayed callback required")
    local allowed={online_save=true,quicksave=true}
    for i=1,3 do allowed[string.format("autosave_%02d",i)]=true end
    for i=1,10 do allowed[string.format("save_%03d",i)]=true end
    local api,tickets,seen,count={}, {},{},0
    local disposed,lastage,refresh_epoch=false,nil,nil
    local function finite(x) return type(x)=="number" and x==x and math.abs(x)<=9007199254740991 end
    local function hex(x,n) return type(x)=="string" and #x==n and not x:find("[^0-9a-f]") end
    local function clock()
        assert(not disposed,"disposed")
        local n=d.now();assert(finite(n) and n>=0 and (not lastage or n>=lastage),"invalid/backward clock")
        lastage=n;return n
    end
    local function clone(v,depth,budget)
        depth=depth or 0;budget=budget or {n=0,s=0}
        budget.n=budget.n+1;assert(depth<=8 and budget.n<=4096,"catalogue structure overflow")
        if type(v)=="string" then
            budget.s=budget.s+#v;assert(#v<=8192 and budget.s<=65536,"catalogue string overflow");return v
        elseif type(v)=="number" then assert(finite(v),"invalid catalogue number");return v
        elseif type(v)=="boolean" or v==nil then return v
        end
        assert(type(v)=="table" and getmetatable(v)==nil,"unreadable catalogue value")
        local out={}
        for k,x in pairs(v) do
            assert(type(k)=="string" or (finite(k) and k%1==0),"invalid catalogue key")
            out[clone(k,depth+1,budget)]=clone(x,depth+1,budget)
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
    local function stamp()
        clock()
        local s=d.current();assert(type(s)=="table" and getmetatable(s)==nil,"canonical session unavailable")
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true};local n=0
        for k in pairs(s) do assert(keys[k],"unknown stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and hex(s.session,32) and
            hex(s.generation,32) and type(s.world_token)=="string" and #s.world_token>0 and #s.world_token<=4096,
            "invalid canonical stamp")
        return clone(s)
    end
    local function offline()
        assert(g.OnlineIsOnlineModeActive()==false,"online or unknown game mode refused")
    end
    local function snapshot()
        local s=stamp();offline()
        assert(refresh_epoch and equal(refresh_epoch,s),"explicit catalogue refresh required")
        assert(C.IsSaveListLoadingComplete()==true,"catalogue loading/unknown")
        local rows=clone(g.GetSaveList(clone(allowed)))
        local n=#rows;assert(n<=64,"catalogue row overflow")
        for k in pairs(rows) do assert(finite(k) and k%1==0 and k>=1 and k<=n,"sparse catalogue") end
        local names={}
        for i=1,n do
            local r=rows[i]
            assert(type(r)=="table" and type(r.filename)=="string" and allowed[r.filename] and not names[r.filename],
                "unknown/duplicate save filename")
            assert(type(r.name)=="string" and finite(r.rawtime) and r.rawtime>=0,"save identity unavailable")
            names[r.filename]=true
        end
        table.sort(rows,function(a,b)return a.filename<b.filename end)
        local saveable=g.IsSavingPossible(false);assert(type(saveable)=="boolean","saveability unknown")
        assert(C.IsSaveListLoadingComplete()==true and equal(stamp(),s),"catalogue/session changed")
        return {stamp=s,rows=rows,count=n,saveable=saveable,age=clock(),atomic=false,scope="NATIVE_STANDARD_SAVE_CATALOGUE"}
    end
    local function safe(fn)
        local ok,v=pcall(fn)
        if ok then return v end
        return {status="REFUSED",reason=tostring(v):sub(1,1024),completed=false}
    end
    local function context(r)
        return {ticket=r.id,slot=r.slot,label=r.label,stamp=clone(r.stamp),prepared_age=r.age}
    end
    local function find(rows,slot)
        for _,v in ipairs(rows) do if v.filename==slot then return v end end
    end
    local function validrow(row,label)
        return row and row.name==label and not row.isonline and not row.error and not row.invalidgameid
            and not row.invalidversion and not row.invalidpatches
    end
    local function current(r)
        assert(equal(stamp(),r.stamp),"session changed")
        assert(clock()-r.age<=300,"preparation expired")
        offline()
    end
    function api.refresh()
        return safe(function()
            local s=stamp();offline();refresh_epoch=nil
            C.ReloadSaveList()
            assert(equal(stamp(),s),"session changed during refresh")
            refresh_epoch=s;return {status="REFRESH_REQUESTED",complete=false}
        end)
    end
    function api.observe() return safe(function()local v=snapshot();v.status="OBSERVED";return v end) end
    function api.prepare_save(id,slot)
        return safe(function()
            assert(hex(id,32) and not seen[id] and count<64,"reused/invalid ticket or history full")
            assert(type(slot)=="string" and slot:match("^save_%d%d%d$") and allowed[slot],"standard slot required")
            seen[id]=true;count=count+1
            local snap=snapshot();assert(snap.saveable,"native saving refused")
            assert(not find(snap.rows,slot),"native slot occupied")
            local label="x4live-"..id
            for _,row in ipairs(snap.rows) do assert(row.name~=label,"label already exists") end
            local r={id=id,slot=slot,label=label,stamp=snap.stamp,age=clock(),catalogue=snap.rows,state="PREPARING"}
            local token=d.reserve_save(context(r))
            assert(type(token)=="table" or type(token)=="userdata","host reservation unavailable")
            r.token=token;r.state="PREPARED";tickets[id]=r
            return {status="PREPARED",ticket=id,slot=slot,label=label,stamp=clone(r.stamp),
                host_reservation_required=true,native_completion=false}
        end)
    end
    function api.execute_save(id)
        local r=tickets[id];local called=false
        local ok,value=pcall(function()
            assert(r and r.state=="PREPARED","save ticket not executable")
            current(r);local snap=snapshot()
            assert(snap.saveable and equal(snap.rows,r.catalogue) and not find(snap.rows,r.slot),"save context changed")
            r.state="CONSUMED" -- host approval may itself be one-use
            assert(d.authorize_save(context(r),r.token)==true,"host save gate refused")
            current(r)
            assert(g.IsSavingPossible(false)==true,"native saveability changed")
            assert(not find(snapshot().rows,r.slot),"slot appeared")
            called=true;r.state="SAVE_REQUESTED"
            g.SaveGame(r.slot,r.label)
            return {status="SAVE_REQUESTED",ticket=id,slot=r.slot,label=r.label,completed=false,may_have_acted=true}
        end)
        if ok then return value end
        if r and called then r.state="SAVE_UNCERTAIN" end
        return {status=called and "UNKNOWN" or "REFUSED",reason=tostring(value):sub(1,1024),
            completed=false,may_have_acted=called,ticket=id}
    end
    function api.poll_save(id)
        return safe(function()
            local r=tickets[id]
            assert(r and (r.state=="SAVE_REQUESTED" or r.state=="SAVE_UNCERTAIN" or r.state=="SAVE_COMPLETED"),
                "no submitted save")
            assert(equal(stamp(),r.stamp),"save session changed");offline()
            if r.state=="SAVE_COMPLETED" then
                return {status="SAVE_COMPLETED",ticket=id,receipt=clone(r.receipt),native=clone(r.savedrow)}
            end
            local snap=snapshot();local row=find(snap.rows,r.slot)
            if not validrow(row,r.label) then return {status="WAITING_NATIVE_CATALOGUE",ticket=id,completed=false} end
            -- This callback must verify fresh settled bytes, not accept caller-provided proof.
            local receipt=d.settled_save(context(r),r.token,clone(row))
            if receipt==nil then return {status="WAITING_HOST_SETTLEMENT",ticket=id,completed=false,native_observed=true} end
            assert(type(receipt)=="table" and receipt.ticket==id and receipt.slot==r.slot and receipt.label==r.label and
                type(receipt.path)=="string" and #receipt.path>0 and hex(receipt.sha256,64) and
                finite(receipt.size) and receipt.size>0 and receipt.size%1==0 and
                equal(receipt.stamp,r.stamp),"unqualified host settlement")
            assert(equal(stamp(),r.stamp),"session changed during settlement")
            r.receipt=clone(receipt);r.savedrow=clone(row);r.state="SAVE_COMPLETED"
            return {status="SAVE_COMPLETED",ticket=id,receipt=clone(receipt),native=clone(row)}
        end)
    end
    function api.prepare_load(id,save_id)
        return safe(function()
            assert(hex(id,32) and not seen[id] and count<64,"reused/invalid load ticket")
            seen[id]=true;count=count+1
            local saved=tickets[save_id];assert(saved and saved.state=="SAVE_COMPLETED","own completed save required")
            local snap=snapshot();assert(equal(snap.stamp,saved.stamp),"session changed")
            assert(equal(find(snap.rows,saved.slot),saved.savedrow),"saved catalogue identity changed")
            assert(C.IsSaveValid(saved.slot)==true,"native save invalid")
            local r={id=id,slot=saved.slot,label=saved.label,stamp=snap.stamp,age=clock(),state="LOAD_PREPARED",
                receipt=clone(saved.receipt),savedrow=clone(saved.savedrow)}
            tickets[id]=r
            return {status="LOAD_PREPARED",ticket=id,slot=r.slot,source_save_ticket=save_id,
                requires_separate_fresh_approval=true,completed=false}
        end)
    end
    function api.execute_load(id)
        local r=tickets[id];local scheduled=false
        local ok,result=pcall(function()
            assert(r and r.state=="LOAD_PREPARED","load ticket not executable")
            current(r)
            assert(equal(find(snapshot().rows,r.slot),r.savedrow) and C.IsSaveValid(r.slot)==true,"load source changed")
            r.state="LOAD_CONSUMED"
            local token=d.authorize_load(context(r),clone(r.receipt))
            assert(type(token)=="table" or type(token)=="userdata","fresh host load authorization required")
            r.load_token=token
            local due=g.getElapsedTime();assert(finite(due) and due>=0,"native clock unavailable")
            r.state="LOAD_SCHEDULED";scheduled=true
            H.addDelayedOneTimeCallbackOnUpdate(function()
                -- Never throw through Helper: it must restore native input state.
                -- A duplicate callback cannot overwrite a prior possible dispatch.
                if r.state~="LOAD_SCHEDULED" then return end
                local called=false
                local good,why=pcall(function()
                    assert(r.state=="LOAD_SCHEDULED","load callback already consumed")
                    r.state="LOAD_CALLBACK_CONSUMED"
                    current(r)
                    assert(equal(find(snapshot().rows,r.slot),r.savedrow) and C.IsSaveValid(r.slot)==true,
                        "load source changed before callback")
                    assert(d.verify_load(context(r),r.load_token,clone(r.receipt))==true,"host load recheck refused")
                    current(r);called=true;r.state="LOAD_REQUESTED";g.LoadGame(r.slot)
                end)
                if not good then r.state=called and "LOAD_UNCERTAIN" or "LOAD_REFUSED";r.reason=tostring(why):sub(1,1024) end
            end,true,due+0.1)
            return {status="LOAD_SCHEDULED",ticket=id,completed=false,may_have_acted=true}
        end)
        if ok then return result end
        if r and scheduled then r.state="LOAD_UNCERTAIN" end
        return {status=scheduled and "UNKNOWN" or "REFUSED",ticket=id,completed=false,
            reason=tostring(result):sub(1,1024),may_have_acted=scheduled}
    end
    function api.status(id)
        return safe(function()
            clock();local r=tickets[id];assert(r,"unknown ticket")
            return {status=r.state,ticket=id,slot=r.slot,reason=r.reason,load_completed=false}
        end)
    end
    function api.dispose() disposed=true;refresh_epoch=nil;return true end
    return api
end

-- Fixed SDK provider for the trusted persistence host; no import-time native calls.
return function(d)
    local ID="x4-persistence-v1"
    assert(type(d)=="table" and type(d.gate)=="table" and type(d.gate.invoke)=="function"
        and type(d.gate.dispose)=="function" and type(d.register)=="function" and
        type(d.current)=="function" and type(d.now)=="function" and type(d.encode)=="function","provider dependencies")
    local function hex(v,n)return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    assert(hex(d.revision,64),"provider revision")
    local records,captures,count,bytes={},{},0,0
    local bound,last,retired,retire_failed,faulted=nil,nil,false,false,false
    local actions={refresh=true,observe=true,catalogue_page=true,prepare_save=true,execute_save=true,
        confirm_settlement=true,prepare_load=true,execute_load=true,status=true}
    local fields={
        refresh={},observe={},catalogue_page={capture=true,index=true},
        prepare_save={ticket=true,slot=true,reservation_sha256=true},
        execute_save={ticket=true,capability=true,context_sha256=true,mode=true,guard_qualified=true},
        execute_load={ticket=true,capability=true,context_sha256=true,mode=true,guard_qualified=true},
        confirm_settlement={ticket=true,slot=true,label=true,path=true,sha256=true,size=true,
            source_provider=true,source_revision=true,source_session=true,source_generation=true,source_world_token=true},
        prepare_load={ticket=true,save_ticket=true},status={ticket=true}}
    local function clone(t)
        if type(t)~="table" then return t end
        local r={};for k,v in pairs(t) do r[k]=clone(v) end;return r
    end
    local function read_current()
        assert(not retired and not faulted,"persistence provider retired/faulted")
        local s=d.current()
        assert(type(s)=="table" and s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string"
            and #s.world_token>0 and #s.world_token<=128,"public identity unavailable")
        local n=0;for k,v in pairs(s) do
            assert(k=="provider" or k=="revision" or k=="session" or k=="generation" or k=="world_token","unknown stamp field")
            if bound then assert(bound[k]==v,"public identity changed") end;n=n+1
        end
        assert(n==5,"public identity incomplete")
        if not bound then bound=clone(s) end
        local age=d.now();assert(type(age)=="number" and age==age and age>=0 and age<=9007199254740991
            and (not last or age>=last),"invalid/backward provider clock");last=age
        return s,age
    end
    local function current()
        local ok,s,age=pcall(read_current)
        if not ok then
            -- Before the first identity, unavailable is normal startup. Once bound,
            -- an observed loss/drift is permanent even if the old stamp returns.
            if bound then faulted=true end
            error(s)
        end
        return s,age
    end
    local function validate(q)
        local s=current()
        assert(q.provider==ID and q.binding==d.revision and q.session==s.session and
            hex(q.id,32) and hex(q.digest,64) and actions[q.action],"unbound persistence request")
        local a=q.arguments;assert(type(a)=="table" and not getmetatable(a),"plain arguments")
        local n,w=0,0
        for k,v in pairs(a) do
            assert(fields[q.action][k] and (type(v)=="string" or type(v)=="boolean" or type(v)=="number"),"unexpected scalar argument")
            if type(v)=="string" then assert(#v<=4096,"argument string bound")
            elseif type(v)=="number" then assert(v==v and math.abs(v)<=9007199254740991,"argument number bound") end
            n=n+1
        end
        for k in pairs(fields[q.action]) do assert(a[k]~=nil,"missing argument");w=w+1 end
        assert(n==w,"argument count")
    end
    local function pack(native)
        local out={status="completed",reason="SCOPED_NATIVE_CALL_RESULT_NOT_GAMEPLAY_PASS",
            observations={native_result=native,provider_revision=d.revision,native_effect_complete=false}}
        if native.status=="REFUSED" then out.status="refused"
        elseif native.status=="UNKNOWN" then out.status="unknown"
        elseif native.status=="SAVE_REQUESTED" or native.status=="LOAD_SCHEDULED" or native.status=="LOAD_REQUESTED" then
            out.status="running"
        end
        assert(#d.encode(out)<=3000,"native result exceeds qualified wire budget")
        return out
    end
    local function call(q)
        if q.action=="catalogue_page" then
            local c=captures[q.arguments.capture]
            local i=q.arguments.index
            assert(c and last-c.age<=30 and type(i)=="number" and i%1==0 and i>=0 and i<c.header.count,
                "catalogue page unknown/expired/out of range")
            return {status="CATALOGUE_PAGE",capture=q.arguments.capture,index=i,count=c.header.count,
                row=clone(c.rows[i+1])}
        end
        local a=clone(q.arguments)
        if q.action=="confirm_settlement" then
            a.stamp={}
            for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
                a.stamp[k]=a["source_"..k];a["source_"..k]=nil
            end
        end
        local result=d.gate.invoke(q.action,a)
        if q.action~="observe" or result.status~="OBSERVED" then return result end
        assert(type(result.rows)=="table" and type(result.count)=="number" and result.count%1==0
            and result.count>=0 and result.count<=64 and #result.rows==result.count,"catalogue denominator")
        local n=0;for k in pairs(result.rows) do
            assert(type(k)=="number" and k%1==0 and k>=1 and k<=result.count,"catalogue row key");n=n+1
        end
        assert(n==result.count,"sparse catalogue")
        local header=clone(result);header.rows=nil
        local size=#d.encode(header)
        for i,row in ipairs(result.rows) do
            local payload={status="CATALOGUE_PAGE",capture=q.id,index=i-1,count=result.count,row=row}
            local encoded=d.encode(pack(payload))
            assert(#encoded<=3000,"one native catalogue row too large")
            size=size+#encoded
        end
        assert(bytes+size<=4194304,"catalogue cache capacity")
        bytes=bytes+size;captures[q.id]={header=header,rows=clone(result.rows),age=last}
        return {status="CATALOGUE_CAPTURED",capture=q.id,header=header,pages=result.count}
    end
    local api={}
    function api.dispose()
        if retire_failed then return false end
        if retired then return true end
        retired=true
        local ok,v=pcall(d.gate.dispose)
        if not ok or v==false then retire_failed=true;return false end
        return true
    end
    local function invoke(phase,q,done)
        validate(q)
        local r=records[q.id]
        if r then assert(r.digest==q.digest and r.action==q.action,"request identity changed") end
        if phase=="prepare" then
            assert(not r and count<512,"provider request capacity/replay")
            count=count+1;r={digest=q.digest,action=q.action,age=last,consumed=false}
            records[q.id]=r
            done({status="prepared",reason="FIXED_CALL_PREPARED_NOT_NATIVE_EFFECT",observations={native_effect_complete=false}})
        elseif phase=="execute" then
            assert(r and not r.consumed and last-r.age<=30,"request not executable")
            r.consumed=true
            local ok,out=pcall(function()local result=call(q);current();return pack(result) end)
            if ok then r.result=out else r.result={status="unknown",reason=tostring(out):sub(1,512),
                observations={native_effect_complete=false,may_have_acted=true}} end
            done(clone(r.result))
        elseif phase=="status" then
            assert(r,"unknown provider request")
            done(r.result and clone(r.result) or {status="prepared",reason="NO_NATIVE_DISPATCH",
                observations={native_effect_complete=false}})
        else error("unknown provider phase") end
    end
    local ok,why=pcall(function()
        assert(d.register{id=ID,revision=d.revision,actions=actions,dispose=api.dispose,
            session=function()local ok,s=pcall(current);return ok and s.session or nil end,invoke=invoke}~=false,
            "registration refused")
    end)
    if not ok then
        local clean=api.dispose()
        error("persistence provider constructor failed: "..tostring(why)..(clean and "" or "; retirement failed"))
    end
    return api
end
